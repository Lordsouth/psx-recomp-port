#!/usr/bin/env python3
"""Generate the "Expanded Roster" mod: manifest + plugin data include.

WHAT IT DOES. Makes type 114 -- the game's single free Digimon slot -- a
real, raisable, evolvable partner (Machinedramon), by:

  1. RELOCATING the three partner-line tables that are indexed by type and
     too small (EVO_REQ_DATA 63, RAISE_DATA 66, EVO_GAINS_DATA 66 entries;
     plus ITEM_TAKE_DISTANCE 67) into a 16 KB enhancement arena at
     0x9F0000xx grown to 180 entries. Every lui/lo16 pair in the EXE that
     materializes a table address is found by the same scanner the reference
     census used, and rewritten by guarded patches. dw_roster.c fills the
     arena at boot.
  2. COMPLETING slot 114 in the per-type tables that already have 180
     entries but hold dev-stub values: model pointer (tAKA -> MUGE),
     skeleton pointer (NULL -> Machinedramon's), ORIGINAL_TYPE (battle
     auras), and the DIGIMON_DATA record itself.
  3. WIRING an evolution path to 114 and widening the raised-flag guard
     (type < 63) that would otherwise never record it.

Everything is guarded equal-length byte patches; switching the feature off
restores stock behavior exactly (the arena is then never read).

  py -3.12 tools/gen_roster_mod.py            # write manifest + data include
  py -3.12 tools/gen_roster_mod.py --check    # verify sites only
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
EXE = os.path.normpath(os.path.join(ROOT, "..", "game-vanilla", "extracted",
                                    "SLUS_010.32"))
LOAD_ADDR = 0x80090800
HDR = 0x800
CODE_END = 0x80113A60   # code subsegments end here (dw_decomp main.yaml)

PKG_ID = "dw.vanilla.roster"
PKG_VER = "1.0.0"
GAME_ID = "SLUS-01032"

# ---- arena layout (mirrored in dw_roster.c) --------------------------------
ARENA = 0x9F000000
OFF_EVO_REQ, OFF_RAISE, OFF_EVO_GAINS, OFF_ITEM_TAKE = (
    0x0000, 0x1400, 0x2800, 0x3200)
# phase 2: the last four type-indexed tables. The two TRN* ones live in
# overlay images rather than the EXE -- see OVL_RELOC.
OFF_EVO_PATHS, OFF_EVOCHART, OFF_TRAIN_ANIM, OFF_TRN2_ANIM = (
    0x3400, 0x3C00, 0x4200, 0x4400)
N_TYPES = 180
# 32 KB. The high-water mark is TRN2_ANIM's end; the lui/lo16 retarget can only
# encode a positive lo16, so every offset must also stay below 0x8000.
ARENA_SIZE = 0x8000
_ARENA_END = OFF_TRN2_ANIM + N_TYPES * 2
assert _ARENA_END <= ARENA_SIZE and _ARENA_END < 0x8000, _ARENA_END

# ---- tables being relocated, main EXE --------------------------------------
RELOC = {
    "EVO_REQ_DATA":       (0x8012ABEC, 63 * 28, OFF_EVO_REQ),
    "RAISE_DATA":         (0x801225BC, 66 * 28, OFF_RAISE),
    "EVO_GAINS_DATA":     (0x8012B2D0, 66 * 14, OFF_EVO_GAINS),
    "ITEM_TAKE_DISTANCE": (0x80122D24, 67, OFF_ITEM_TAKE),
    "EVO_PATHS_DATA":     (0x8012B66C, 62 * 11, OFF_EVO_PATHS),
    "EVOCHART_ICONS":     (0x80124544, 61 * 8, OFF_EVOCHART),
}

# ---- tables being relocated, overlay images --------------------------------
# Both are s8[68][2] indexed [type-1] inside an overlay, so every type above 68
# reads past the end into the overlay's string pool. (0.2.0 shipped slot 114 by
# patching the two string bytes index 114 happens to land on: correct for one
# slot, unworkable for 51.) Relocating them retires that hack.
#
# Overlays load verbatim at OVL_BASE, so disc offset = lba*2048 + (vaddr -
# OVL_BASE). These patches are code, and the runtime keys compiled overlay
# entries by a content hash of their code ranges (overlay_loader.c, "Inc3"), so
# a patched overlay misses its cache and is rebuilt -- executing stale native
# code for a patched range is impossible by construction.
OVL_BASE = 0x80088800
OVL_DIR = os.path.dirname(EXE)
OVL_RELOC = {
    "TRAINING_ANIM_IDS": ("TRN_REL.BIN", 148248, 0x8008F050, 68 * 2,
                          OFF_TRAIN_ANIM),
    "TRN2_D_8008DA20":   ("TRN2_REL.BIN", 148262, 0x8008DA20, 68 * 2,
                          OFF_TRN2_ANIM),
}

# EVERY overlay's load address. Relocating a table means retargeting every
# reference to it ANYWHERE, and overlays reference the EXE's tables too -- the
# first version of this generator censused only the EXE plus TRN/TRN2, which
# left EVL_REL.BIN (the evolution overlay) reading the STOCK 66-entry
# EVO_GAINS_DATA. Every type above 65 then read out of bounds and evolution
# produced a garbage partner type. Do not trim this list.
#
# Note these do NOT all share a base: assuming 0x80088800 for all of them (true
# only for TRN/TRN2) yields correct table hits but wrong site addresses.
OVL_BASES = {
    "BTL_REL.BIN": 0x80052AE0, "DGET_REL.BIN": 0x80080800,
    "DOO2_REL.BIN": 0x80070000, "DOOA_REL.BIN": 0x80080000,
    "EAB_REL.BIN": 0x80060000, "ENDI_REL.BIN": 0x80060000,
    "EVL_REL.BIN": 0x80060000, "FISH_REL.BIN": 0x80070000,
    "KAR_REL.BIN": 0x80053800, "MOV_REL.BIN": 0x80010000,
    "MURD_REL.BIN": 0x8007C000, "SHOP_REL.BIN": 0x80080800,
    "STD_REL.BIN": 0x80052AE0, "TRN2_REL.BIN": 0x80088800,
    "TRN_REL.BIN": 0x80088800, "VS_REL.BIN": 0x80052AE0,
}
# Start LBA on disc, verified by matching each overlay's first 2048 bytes
# against the disc's user data. Only needed for overlays that actually
# reference a relocated table; the generator asserts no other overlay does.
OVL_LBAS = {
    "EVL_REL.BIN": 147824,
    "TRN_REL.BIN": 148248,
    "TRN2_REL.BIN": 148262,
}

# ---- game tables read during generation -------------------------------------
DIGIMON_DATA_VA, DIGIMON_STRIDE = 0x8012CEB4, 52
EVO_PATHS_VA, EVO_PATHS_STRIDE = 0x8012B66C, 11
EVO_REQ_VA, EVO_REQ_STRIDE = 0x8012ABEC, 28
RAISE_VA, RAISE_STRIDE = 0x801225BC, 28
EVO_GAINS_VA, EVO_GAINS_STRIDE = 0x8012B2D0, 14
ITEM_TAKE_VA = 0x80122D24

# ---- newly raisable species --------------------------------------------------
# Only a CHAMPION may be an evolution source. The dispatch at 0x800A9150 is a
# level chain -- Fresh, In-Training, Rookie, Champion -- and it simply ends;
# there is no Ultimate branch, so an Ultimate partner never evolves again.
# (This is why the 0.2.0 wiring was dead: it hung Machinedramon off Megadramon
# and MetalGreymon, both level 5.)
#
# Requirement scoring, from calculateRequirementScore (evolution.c:303): four
# possible points, three needed -- care mistakes, a hard +/-5 weight window,
# all six stats at once, and ONE bonus from digimon/discipline/happiness/
# battles/techs. flags bit4 (0x10) makes care a maximum; cleared, it is a
# minimum, which is the "neglect" path the dark variants use below and no stock
# Ultimate uses. Weight is the game's own steering mechanic, so every new
# species gets a window distinct from the others its source already offers.
#
# req = (digimon, hp/10, mp/10, off, def, spd, brn, care, weight,
#        discipline, happiness, battles, techs, flags)
NEW_SPECIES = [
    dict(type=64, name="Gigadramon", src=[("Airdramon", 7)],
         raise_donor=54, gains_donor=54, anim_donor=54,
         req=(-1, 400, 500, 600, 400, 400, 400, 10, 45, -1, -1, 50, 35, 0x10)),
    dict(type=65, name="MetalEtemon", src=[("Sukamon", 39)],
         raise_donor=42, gains_donor=42, anim_donor=42,
         req=(-1, 400, 400, 600, 500, 400, 300, 5, 50, -1, -1, 60, 40, 0x10)),
    dict(type=66, name="Myotismon", src=[("Devimon", 6)],
         raise_donor=26, gains_donor=26, anim_donor=26,
         req=(-1, 400, 600, 500, 300, 400, 500, 15, 20, -1, -1, -1, 40, 0x00)),
    dict(type=70, name="WaruMonzaemon", src=[("Numemon", 11)],
         raise_donor=14, gains_donor=14, anim_donor=14,
         req=(-1, 400, 300, 500, 400, 300, 300, 12, 25, -1, -1, 60, -1, 0x00)),
    dict(type=90, name="Tekkamon", src=[("Kokatorimon", 50)],
         raise_donor=41, gains_donor=41, anim_donor=41,
         req=(-1, 400, 400, 500, 600, 400, 300, 8, 35, -1, -1, 40, 35, 0x10)),
    dict(type=112, name="WaruSeadramon", src=[("Seadramon", 10)],
         raise_donor=61, gains_donor=61, anim_donor=61,
         req=(-1, 500, 400, 500, 400, 400, 300, 12, 40, -1, -1, 50, -1, 0x00)),
    # Panjyamon is a CHAMPION and deliberately has no evolution path: the game
    # ships an item that targets it (Noble Mane, 0x7E -> type 63), which is the
    # only way in by design. It still needs the partner-line rows, or evolving
    # into it via that item lands on empty tables.
    dict(type=63, name="Panjyamon", src=[],
         raise_donor=9, gains_donor=9, anim_donor=9,
         req=(-1, 300, 300, 400, 300, 300, 300, 8, 30, -1, -1, 30, 20, 0x10)),
]
# Machinedramon keeps its authored requirement row; only its sources move, to
# the two Champions that already lead to the Megadramon / MetalGreymon line the
# feature was always described in terms of. Tyrannomon reaches both.
MACHINEDRAMON_SRC = [("Tyrannomon", 8), ("Greymon", 5)]

# ---- authored constants (from research/roster-* + the data workflow) -------
# Filled by fill_authored() below -- kept in one place on purpose.
AUTHORED = {}


def fill_authored():
    """Slot-114 data, evolution wiring, guard patches. Every 'expected' is a
    byte string read out of the EXE here, so a drifted constant refuses to
    apply rather than corrupting."""
    data = open(EXE, "rb").read()

    def rd(va, n):
        return data[va - LOAD_ADDR + HDR:va - LOAD_ADDR + HDR + n]

    extra = []
    # 1. DIGIMON_DATA[114] := the game's own Machinedramon record (from 115)
    dd = 0x8012CEB4
    stub = rd(dd + 114 * 52, 52)
    donor = rd(dd + 115 * 52, 52)
    assert stub[:20] == bytes(20), "slot 114 no longer a dev stub"
    assert donor[:13] == b"Machinedramon"
    donor = donor[:28] + bytes([3]) + donor[29:]  # attribute: Virus
    extra += bytes_patch(dd + 114 * 52, stub, donor)
    # 2. model pointer: tAKA -> MUGE pool string
    pt = 0x8011D19C + 114 * 4
    assert rd(pt, 4) == struct.pack("<I", 0x80133ED4)
    extra += bytes_patch(pt, struct.pack("<I", 0x80133ED4),
                         struct.pack("<I", 0x80133EDC))
    # 3. skeleton pointer: NULL -> Machinedramon's skeleton
    sk = 0x8011CE60 + 114 * 4
    assert rd(sk, 4) == bytes(4)
    sk115 = rd(0x8011CE60 + 115 * 4, 4)
    assert struct.unpack("<I", sk115)[0] == 0x8011CBC0
    extra += bytes_patch(sk, bytes(4), sk115)
    # 4. ORIGINAL_TYPE[114] = 115 (battle aura family)
    ot = 0x8012A84C + 114 * 2
    assert rd(ot, 2) == bytes([0xFF, 0xFF])
    extra += bytes_patch(ot, bytes([0xFF, 0xFF]), struct.pack("<h", 115))

    extra += authored_research(rd)

    AUTHORED["extra_patches"] = extra
    AUTHORED["hook_vaddr"] = 0x800EEBDC  # initializeHeap


def build_species_rows(rd, name_of, level_of):
    """Assemble the arena rows for every newly raisable species.

    Everything except the requirement row is donor-copied from a species the
    game already ships partner data for -- chosen for family and body plan, so
    the animation, camera pull-back and appetite all suit the model. The
    requirement row is authored (see NEW_SPECIES).

    INDEXING IS NOT UNIFORM and each row below states which it uses:
      EVO_REQ / RAISE / EVO_GAINS / TRAINING_ANIM / TRN2_ANIM -> [type]
      ITEM_TAKE / EVO_PATHS / EVOCHART                        -> [type - 1]
    """
    trn = open(os.path.join(OVL_DIR, "TRN_REL.BIN"), "rb").read()
    trn2 = open(os.path.join(OVL_DIR, "TRN2_REL.BIN"), "rb").read()

    def anim(img, table_va, t):
        o = table_va - OVL_BASE + t * 2
        return img[o:o + 2]

    rows = []
    for spec in NEW_SPECIES:
        t, d = spec["type"], spec["raise_donor"]
        # Donor and target must share a level: raise curves, evolution gains and
        # the training animation are all level-shaped.
        assert level_of(t) == level_of(d), (
            "%s is level %d but donor %s is level %d"
            % (spec["name"], level_of(t), name_of(d), level_of(d)))
        assert name_of(t) == spec["name"], (
            "type %d is %r, not %r" % (t, name_of(t), spec["name"]))
        # donors must themselves be inside the stock partner-line tables
        assert d < 66 and spec["gains_donor"] < 66 and spec["anim_donor"] < 68

        gains = bytearray(rd(EVO_GAINS_VA + spec["gains_donor"]
                             * EVO_GAINS_STRIDE, EVO_GAINS_STRIDE))
        gains[12:14] = struct.pack("<h", t)   # .targetDigimon is the species
        rows.append(dict(
            type=t, name=spec["name"],
            req=struct.pack("<13hBB", *(spec["req"] + (0,))),
            raise_=rd(RAISE_VA + d * RAISE_STRIDE, RAISE_STRIDE),
            gains=bytes(gains),
            item_take=rd(ITEM_TAKE_VA + (spec["raise_donor"] - 1), 1)[0],
            train=anim(trn, 0x8008F050, spec["anim_donor"]),
            trn2=anim(trn2, 0x8008DA20, spec["anim_donor"]),
        ))
    for r in rows:
        assert len(r["req"]) == 28 and len(r["raise_"]) == 28
        assert len(r["gains"]) == 14
        assert len(r["train"]) == 2 and len(r["trn2"]) == 2
    AUTHORED["species"] = rows


def authored_research(rd):
    """Constants sourced from the data-authoring research (research/roster-*):
    evolution wiring, guard widening, disc fixes, and the 114 entries."""
    extra = []
    # 5. raised-flag guard: sltiu $at,$a0,0x3f -> 0xb4 so setDigimonRaised
    #    records types up to 179 (hasDigimonRaised is already unbounded).
    g = 0x800FF7FC
    assert rd(g, 4) == struct.pack("<I", 0x2C81003F)
    extra += word_patch(g, 0x2C81003F, 0x2C8100B4)
    # 6. evolution wiring. to[] slots skip -1 entries, so any free byte works
    #    (evolution.c:73) -- but the SOURCE must be a Champion, since the level
    #    dispatch has no Ultimate branch (see NEW_SPECIES).
    def level_of(t):
        return rd(DIGIMON_DATA_VA + t * DIGIMON_STRIDE + 29, 1)[0]

    def name_of(t):
        return rd(DIGIMON_DATA_VA + t * DIGIMON_STRIDE, 20).split(
            b"\0")[0].decode("ascii", "replace")

    used_slots = {}

    def wire(src_name, src_type, new_type):
        """Write new_type into a free to[] byte of src_type's path entry."""
        assert level_of(src_type) == 4, (
            "%s (type %d) is level %d, not Champion -- an Ultimate source is "
            "dead wiring" % (src_name, src_type, level_of(src_type)))
        assert name_of(src_type) == src_name, (
            "type %d is %r, not %r" % (src_type, name_of(src_type), src_name))
        base = EVO_PATHS_VA + (src_type - 1) * EVO_PATHS_STRIDE + 5
        for slot in range(6):
            va = base + slot
            if va in used_slots:
                continue
            if rd(va, 1) == b"\xFF":
                used_slots[va] = new_type
                return bytes_patch(va, b"\xFF", bytes([new_type]))
        raise AssertionError("%s has no free to[] slot" % src_name)

    for spec in NEW_SPECIES:
        for src_name, src_type in spec["src"]:
            extra += wire(src_name, src_type, spec["type"])
    for src_name, src_type in MACHINEDRAMON_SRC:
        extra += wire(src_name, src_type, 114)

    # 8. The game's own three evolution items, un-gated.
    #
    # Items 125 "Giga Hand", 126 "Noble Mane" and 127 "Metalbanana" are
    # finished: named, described ("Digivolve to Gigadramon!"), priced, with
    # icons and handlers. handleEvoItems (0x800C3864) routes exactly 0x7D-0x7F
    # down a straight-line path that writes EVOLUTION_TARGET (64 / 63 / 65) and
    # falls through to the kick -- no level check anywhere on it.
    #
    # They were unusable for two reasons, and this project has now removed
    # both. Their targets 63/64/65 had no partner-line table rows (fixed: all
    # three are authored above), and partnerWillRefuseItem gates them on the
    # partner's level -- item 126 demands level 3, items 125/127 demand level 4.
    #
    # Two instructions form a range check: everything below 0x7D leaves at
    # 0x800A736C and everything from 0x80 up leaves at 0x800A7378, so the
    # fall-through into the level tests is reached by exactly those three ids.
    # Turning that second branch unconditional sends them to the same exit as
    # every other item, skipping only the level tests. Just the rs field
    # changes, $at -> $zero: one byte of the four.
    g2 = 0x800A7378
    assert rd(g2, 4) == struct.pack("<I", 0x1020004F), "patch site drifted"
    extra += word_patch(g2, 0x1020004F, 0x1000004F)

    build_species_rows(rd, name_of, level_of)
    # NOTE the ordering these two patches depend on: they edit EVO_PATHS_DATA
    # in the EXE image, and that table is now relocated. Manifest patches are
    # applied to guest RAM before initializeHeap runs, and the plugin's arena
    # copy is taken at that hook, so the copy inherits them. Keeping them as
    # guarded EXE patches (rather than authoring bytes in the plugin) keeps
    # the drift check: a changed EXE refuses to apply instead of corrupting.

    # 7. Offense/MP training animation ids. Both tables are s8[68][2] in an
    #    overlay indexed by raw type, so type 114 read 92 bytes past the end
    #    (TRN: the string "Give it all you ", giving invalid anim 101; TRN2:
    #    zeros, and threshold 0 soft-locks MP training). 0.2.0 shipped this as
    #    two disc patches that rewrote those string bytes in place. Both
    #    tables are now relocated into the arena instead, so the entry is
    #    authored data and the string is left intact. Anim 46 is the row 60 of
    #    66 partners share, proven present in the MUGE skeleton.
    #    Byte [1] is the frame the impact lands on: a per-frame counter is
    #    compared with == before the hit FX and sound fire (trn_off.c:145).
    #    0.2.0's in-place patch rewrote only byte [0] of "e ", so it inherited
    #    the string's space (0x20 = 32 frames) as the impact frame -- three
    #    times the 0x0A every other user of animation 46 uses, and outside the
    #    0x08..0x1E range of the whole stock table. Authored properly here.
    AUTHORED["train_anim_114"] = bytes([0x2E, 0x0A])
    AUTHORED["trn2_anim_114"] = bytes([0x2E, 0x0A])

    # authored table entries (filled into the arena by dw_roster.c)
    AUTHORED["evo_req_114"] = struct.pack(
        "<13hBB",
        -1,     # digimon: any current form
        500,    # hp/10   -> 5000
        500,    # mp/10   -> 5000
        500,    # offense
        500,    # defense
        400,    # speed
        400,    # brains
        5,      # care mistakes (max, flags bit4)
        50,     # weight +/-5 (Megadramon-profile default weight)
        100,    # discipline
        -1,     # happiness: unchecked
        50,     # battles (min)
        35,     # techniques learned
        0x10, 0)
    # RAISE_DATA donor: Megadramon -- same body plan, biggest appetite tier,
    # deepest camera pull-back (fits the 400x600 model)
    AUTHORED["raise_114"] = bytes.fromhex(
        "02060a0e1216ffff502d0b002400b0040e30040300320000cc0154fc")
    AUTHORED["evo_gains_114"] = struct.pack(
        "<7h", 6000, 6000, 600, 500, 600, 500, 114)
    AUTHORED["item_take_114"] = 0x0F
    AUTHORED["loadmmd_vaddr"] = 0x800A1F68
    AUTHORED["loadmmdasync_vaddr"] = 0x800A2810
    return extra


# ---- MIPS scanning (per-register lui tracking, as in the census) -----------
LOADS = {0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26}
STORES = {0x28, 0x29, 0x2A, 0x2B, 0x2E}


def sext16(v):
    return v - 0x10000 if v & 0x8000 else v


def dest_reg(word):
    op = word >> 26
    rt = (word >> 16) & 31
    rd = (word >> 11) & 31
    if op == 0:
        funct = word & 0x3F
        if funct in (0x08, 0x0C, 0x0D, 0x18, 0x19, 0x1A, 0x1B, 0x11, 0x13):
            return None
        if funct == 0x09:
            return rd
        return rd if rd else None
    if op == 1:
        return 31 if rt in (0x10, 0x11) else None
    if op == 3:
        return 31
    if op in (0x02, 0x04, 0x05, 0x06, 0x07):
        return None
    if 0x08 <= op <= 0x0F:
        return rt if rt else None
    if op in LOADS:
        return rt if rt else None
    if op == 0x10 or op == 0x12:
        return rt if ((word >> 21) & 31) in (0x00, 0x02) else None
    return None


def scan_image(text, start_va, tables):
    """Generic lui/lo16 materialization census over one code image.

    tables: name -> (base, size). Returns
    [(table, lui_vaddr, lui_word, lo_vaddr, lo_word, target)].
    """
    words = struct.unpack("<%dI" % (len(text) // 4), text)
    live = {}   # reg -> (hi16, lui_vaddr, lui_word)
    out = []
    for i, w in enumerate(words):
        va = start_va + i * 4
        op = w >> 26
        rs = (w >> 21) & 31
        rt = (w >> 16) & 31
        if op == 0x0F:                       # lui
            live[rt] = (w & 0xFFFF, va, w)
            continue
        if (op == 0x09 or op in LOADS or op in STORES) and rs in live:
            hi, lui_va, lui_w = live[rs]
            tgt = ((hi << 16) + sext16(w & 0xFFFF)) & 0xFFFFFFFF
            for name, (base, size) in tables.items():
                if base <= tgt < base + size or tgt == base:
                    out.append((name, lui_va, lui_w, va, w, tgt))
        d = dest_reg(w)
        if d is not None and d in live:
            # addiu rt,rs,imm with rt==rs keeps the pair usable for chained
            # materializations only when rt is a fresh reg; conservative:
            # invalidate on any write, except the lui itself handled above.
            del live[d]
    return out


def scan_sites(data):
    """EXE census, RELOC tables."""
    return scan_image(data[HDR:HDR + (CODE_END - LOAD_ADDR)], LOAD_ADDR,
                      {n: (b, s) for n, (b, s, _a) in RELOC.items()})


def arena_offset_of(name):
    """Arena offset for any relocated table, EXE-resident or overlay-resident."""
    if name in RELOC:
        return RELOC[name][2]
    return OVL_RELOC[name][4]


def table_base_of(name):
    if name in RELOC:
        return RELOC[name][0]
    return OVL_RELOC[name][2]


def scan_overlay_sites():
    """Census EVERY overlay for references to EVERY relocated table.

    Two scopes, deliberately different:

      - The six EXE-resident tables live at 0x8012xxxx, which no overlay maps,
        so a hit is unambiguous and every overlay is scanned for them.
      - The two overlay-resident tables live inside an overlay's own address
        window, which several overlays share (TRN and TRN2 both load at
        0x80088800). Scanning TRN2 for TRN's table would match TRN2's own
        unrelated data, so each is scanned ONLY in the overlay that owns it.

    -> (sites, images); sites are (file, table, lui_va, lui_w, lo_va, lo_w, tgt)
    """
    exe_tables = {n: (b, s) for n, (b, s, _a) in RELOC.items()}
    sites, images = [], {}
    for fn in sorted(OVL_BASES):
        path = os.path.join(OVL_DIR, fn)
        if not os.path.exists(path):
            continue
        img = open(path, "rb").read()
        images[fn] = img
        base = OVL_BASES[fn]
        tables = dict(exe_tables)
        for name, (owner, _lba, tb, size, _aoff) in OVL_RELOC.items():
            if owner == fn:
                tables[name] = (tb, size)
        n = (len(img) // 4) * 4
        for s in scan_image(img[:n], base, tables):
            sites.append((fn,) + s)

    # Any overlay with a hit must have a known LBA, or its patch cannot be
    # addressed on disc -- fail loudly rather than silently skipping it.
    touched = sorted({s[0] for s in sites})
    missing = [fn for fn in touched if fn not in OVL_LBAS]
    assert not missing, (
        "these overlays reference a relocated table but have no LBA: %s"
        % missing)
    return sites, images


def disc_offset(lba, vaddr, base=OVL_BASE):
    """Disc user-data offset of an overlay vaddr. Verified against the
    0.2.0 TRN patch (LBA 148248, base 0x80088800, type-114 slot -> 303638836).
    `base` is the overlay's own load address -- they are not all the same."""
    return lba * 2048 + (vaddr - base)


def retarget(sites):
    """-> (lui_patches {vaddr: (old, new)}, lo_patches [(vaddr, old, new)])"""
    lui_patches = {}
    lo_patches = []
    for name, lui_va, lui_w, lo_va, lo_w, tgt in sites:
        base, size, aoff = RELOC[name]
        new_tgt = ARENA + aoff + (tgt - base)
        lo = new_tgt & 0xFFFF
        hi = (new_tgt >> 16) + (1 if lo & 0x8000 else 0)
        assert not (lo & 0x8000), (
            "arena layout must keep lo16 positive: %s 0x%X" % (name, new_tgt))
        new_lui = (lui_w & 0xFFFF0000) | (hi & 0xFFFF)
        prev = lui_patches.get(lui_va)
        if prev is not None:
            assert prev == (lui_w, new_lui), (
                "shared lui at 0x%08X retargets inconsistently" % lui_va)
        lui_patches[lui_va] = (lui_w, new_lui)
        new_lo = (lo_w & 0xFFFF0000) | lo
        check_pair(new_lui, new_lo, new_tgt, "%s @0x%08X" % (name, lo_va))
        lo_patches.append((lo_va, lo_w, new_lo))
    return lui_patches, lo_patches


def check_pair(new_lui, new_lo, want, where):
    """Decode a rewritten lui/lo16 pair the way the CPU does and assert it
    materializes the intended address. The encoding is the one thing here that
    fails silently: a wrong hi-adjust still assembles, still passes every
    expected-byte guard, and simply points the game at the wrong memory."""
    got = ((new_lui & 0xFFFF) << 16) + sext16(new_lo & 0xFFFF)
    assert got & 0xFFFFFFFF == want, (
        "%s: pair decodes to 0x%08X, wanted 0x%08X" % (where, got, want))


def hexs(bs):
    return " ".join("%02x" % x for x in bs)


def word_patch(vaddr, old, new, feature="roster"):
    return [
        "[[patch]]",
        'feature = "%s"' % feature,
        'target = "main_exe"',
        "address = 0x%08X" % vaddr,
        'expected = "%s"' % hexs(struct.pack("<I", old)),
        'replace = "%s"' % hexs(struct.pack("<I", new)),
        "",
    ]


def bytes_patch(vaddr, old, new, feature="roster"):
    assert len(old) == len(new)
    return [
        "[[patch]]",
        'feature = "%s"' % feature,
        'target = "main_exe"',
        "address = 0x%08X" % vaddr,
        'expected = "%s"' % hexs(old),
        'replace = "%s"' % hexs(new),
        "",
    ]


def disc_bytes_patch(offset, old, new, note, feature="roster"):
    assert len(old) == len(new)
    # A patch may not straddle a 2048-byte sector boundary.
    assert offset // 2048 == (offset + len(old) - 1) // 2048, (
        "patch at %d crosses a sector boundary" % offset)
    return [
        "# %s" % note,
        "[[patch]]",
        'feature = "%s"' % feature,
        'target = "disc_user"',
        "offset = %d" % offset,
        'expected = "%s"' % hexs(old),
        'replace = "%s"' % hexs(new),
        "",
    ]


def overlay_retarget(sites, images):
    """Emit disc_user word patches retargeting overlay table references into
    the arena. Mirrors retarget() but keyed by (file, vaddr)."""
    lui_patches, lo_patches = {}, []
    for fn, name, lui_va, lui_w, lo_va, lo_w, tgt in sites:
        lba = OVL_LBAS[fn]
        base = table_base_of(name)
        aoff = arena_offset_of(name)
        new_tgt = ARENA + aoff + (tgt - base)
        lo = new_tgt & 0xFFFF
        hi = (new_tgt >> 16) + (1 if lo & 0x8000 else 0)
        assert not (lo & 0x8000), (
            "arena layout must keep lo16 positive: %s 0x%X" % (name, new_tgt))
        new_lui = (lui_w & 0xFFFF0000) | (hi & 0xFFFF)
        key = (fn, lui_va)
        prev = lui_patches.get(key)
        if prev is not None:
            assert prev == (lui_w, new_lui, lba), (
                "shared lui at %s 0x%08X retargets inconsistently"
                % (fn, lui_va))
        lui_patches[key] = (lui_w, new_lui, lba)
        new_lo = (lo_w & 0xFFFF0000) | lo
        check_pair(new_lui, new_lo, new_tgt, "%s %s @0x%08X"
                   % (fn, name, lo_va))
        lo_patches.append((fn, lo_va, lo_w, new_lo, lba))

    out = []
    for (fn, va), (old, new, lba) in sorted(lui_patches.items()):
        out += _ovl_word_patch(images, fn, va, old, new, lba)
    for fn, va, old, new, lba in sorted(lo_patches):
        out += _ovl_word_patch(images, fn, va, old, new, lba)
    return out


def _ovl_word_patch(images, fn, va, old, new, lba):
    """One overlay word patch, addressed through that overlay's OWN load base."""
    base = OVL_BASES[fn]
    off = va - base
    got = struct.unpack("<I", images[fn][off:off + 4])[0]
    assert got == old, ("%s 0x%08X: image has 0x%08X, expected 0x%08X"
                        % (fn, va, got, old))
    return disc_bytes_patch(
        disc_offset(lba, va, base), struct.pack("<I", old),
        struct.pack("<I", new), "%s 0x%08X: table ref -> arena" % (fn, va))


def main():
    check_only = "--check" in sys.argv
    data = open(EXE, "rb").read()

    sites = scan_sites(data)
    from collections import Counter
    c = Counter(s[0] for s in sites)
    print("EXE reference sites:", dict(c))
    lui_patches, lo_patches = retarget(sites)
    print("EXE patches: %d lui (deduped) + %d lo16" %
          (len(lui_patches), len(lo_patches)))

    ovl_sites, ovl_images = scan_overlay_sites()
    oc = Counter(s[1] for s in ovl_sites)
    print("overlay reference sites:", dict(oc))
    # Census counts are the contract: a different count means the scanner or
    # the image changed, and silently patching a different set of sites is how
    # you ship a half-relocated table.
    for name, want in (("EVO_REQ_DATA", 16), ("RAISE_DATA", 44),
                       ("EVO_GAINS_DATA", 4), ("ITEM_TAKE_DISTANCE", 3),
                       ("EVO_PATHS_DATA", 9), ("EVOCHART_ICONS", 7)):
        assert c[name] == want, "%s: %d sites, expected %d" % (
            name, c[name], want)
    # Per (overlay, table) rather than per table: the first version counted
    # only the two TRN tables and so never noticed that EVL_REL.BIN reads
    # EVO_GAINS_DATA. Every entry here is a site that MUST be retargeted, and a
    # new one appearing is a hard failure rather than a silent omission.
    per_file = Counter((s[0], s[1]) for s in ovl_sites)
    want_ovl = {
        ("TRN_REL.BIN", "TRAINING_ANIM_IDS"): 5,
        ("TRN2_REL.BIN", "TRN2_D_8008DA20"): 3,
        # These are the ones that made every type above 65 evolve into garbage:
        # EVL is the evolution overlay and was reading the stock 66-entry table.
        ("EVL_REL.BIN", "EVO_GAINS_DATA"): 1,
        ("EVL_REL.BIN", "RAISE_DATA"): 1,
        ("TRN_REL.BIN", "RAISE_DATA"): 3,
        ("TRN2_REL.BIN", "RAISE_DATA"): 3,
    }
    assert dict(per_file) == want_ovl, (
        "overlay site census changed.\n  got:    %s\n  wanted: %s"
        % (dict(per_file), want_ovl))

    funcs = set()
    for _n, lui_va, _lw, _lv, _low, _t in sites:
        funcs.add(lui_va & ~0x3F)   # coarse; report only
    print("~%d functions touched (drop to interpreter while feature is on)"
          % len(funcs))

    if check_only:
        print("--check: nothing written")
        return 0

    fill_authored()

    # detection lui for the plugin gate: lowest lui vaddr among EVO_REQ sites
    detect = min(lv for n, lv, _lw, _lo, _low, _t in sites
                 if n == "EVO_REQ_DATA")

    # stock overlay table contents, baked for the plugin (see the .inc note)
    def ovl_stock(name):
        fn, _lba, base, size, _aoff = OVL_RELOC[name]
        off = base - OVL_BASE
        return ovl_images[fn][off:off + size]
    stock_train = ovl_stock("TRAINING_ANIM_IDS")
    stock_trn2 = ovl_stock("TRN2_D_8008DA20")
    assert len(stock_train) == 136 and len(stock_trn2) == 136

    out = [
        "# Digimon World - Expanded Roster. Generated by",
        "# tools/gen_roster_mod.py; regenerate rather than hand-editing.",
        "",
        "format_version = 5",
        'id = "%s"' % PKG_ID,
        'version = "%s"' % PKG_VER,
        'name = "Expanded Roster"',
        'author = "DigimonWorldVanilla"',
        'description = "Grows every type-indexed partner table beyond its '
        'stock size, then makes seven Digimon raisable: Machinedramon in the '
        'game\'s one unused slot, plus Gigadramon, Myotismon, WaruSeadramon, '
        'WaruMonzaemon, MetalEtemon and Tekkamon -- finished Digimon that '
        'shipped on the disc as enemies only. Powered by table relocation '
        'into enhancement memory."',
        'resolver = "declarative"',
        'save_compatibility = "isolated"',
        "",
        "[[target]]",
        'game_id = "%s"' % GAME_ID,
        "",
        "[[feature]]",
        'id = "roster"',
        'name = "Seven new raisable Digimon"',
        'description = "Relocates and grows all eight type-indexed partner '
        'tables, then authors seven species into the headroom: model, '
        'skeleton, battle auras, stats, training animations and an evolution '
        'path from a Champion for each. The touched functions run interpreted '
        'until the background compiler rebuilds them natively."',
        'group = "Roster"',
        "default_enabled = false",
        "",
        "# The native side: dw_roster.c's activation reserves the arena and its",
        "# boot hook fills it. Activation only fires for manifest-selected ids.",
        "[[plugin]]",
        'feature = "roster"',
        'id = "dw.roster"',
        "",
    ]
    for lui_va in sorted(lui_patches):
        old, new = lui_patches[lui_va]
        out += word_patch(lui_va, old, new)
    for lo_va, old, new in sorted(lo_patches):
        out += word_patch(lo_va, old, new)
    out += overlay_retarget(ovl_sites, ovl_images)
    out += AUTHORED["extra_patches"]

    dest_dir = os.path.join(ROOT, "mods", "packages", PKG_ID, PKG_VER)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, "manifest.toml")
    with open(dest, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out) + "\n")
    print("wrote %s" % dest)

    inc = [
        "/* Generated by tools/gen_roster_mod.py -- regenerate, don't edit. */",
        "#define ROSTER_DETECT_LUI_VADDR 0x%08Xu" % detect,
        "#define ROSTER_HOOK_VADDR 0x%08Xu" % AUTHORED["hook_vaddr"],
        "#define ROSTER_LOADMMD_VADDR 0x%08Xu" % AUTHORED["loadmmd_vaddr"],
        "#define ROSTER_LOADMMDASYNC_VADDR 0x%08Xu"
        % AUTHORED["loadmmdasync_vaddr"],
        "",
        "static const unsigned char k_evo_req_114[28] = { %s };" %
        ", ".join("0x%02X" % b for b in AUTHORED["evo_req_114"]),
        "static const unsigned char k_raise_114[28] = { %s };" %
        ", ".join("0x%02X" % b for b in AUTHORED["raise_114"]),
        "static const unsigned char k_evo_gains_114[14] = { %s };" %
        ", ".join("0x%02X" % b for b in AUTHORED["evo_gains_114"]),
        "static const unsigned char k_item_take_114 = 0x%02X;" %
        AUTHORED["item_take_114"],
        "",
        "/* Arena offsets are emitted here rather than duplicated in the",
        " * plugin: the manifest's retargeted addresses are computed from",
        " * these, so a drift between the two would silently point the game at",
        " * the wrong table. */",
        "#define ROSTER_ARENA_SIZE 0x%Xu" % ARENA_SIZE,
        "#define ROSTER_OFF_EVO_REQ 0x%04Xu" % OFF_EVO_REQ,
        "#define ROSTER_OFF_RAISE 0x%04Xu" % OFF_RAISE,
        "#define ROSTER_OFF_EVO_GAINS 0x%04Xu" % OFF_EVO_GAINS,
        "#define ROSTER_OFF_ITEM_TAKE 0x%04Xu" % OFF_ITEM_TAKE,
        "#define ROSTER_OFF_EVO_PATHS 0x%04Xu" % OFF_EVO_PATHS,
        "#define ROSTER_OFF_EVOCHART 0x%04Xu" % OFF_EVOCHART,
        "#define ROSTER_OFF_TRAIN_ANIM 0x%04Xu" % OFF_TRAIN_ANIM,
        "#define ROSTER_OFF_TRN2_ANIM 0x%04Xu" % OFF_TRN2_ANIM,
        "",
        "/* Stock contents of the two overlay tables. Unlike the EXE tables,",
        " * these cannot be copied out of guest RAM at the fill hook: TRN and",
        " * TRN2 are not resident until the player enters a gym, long after",
        " * initializeHeap. Baked from the overlay images at generation time",
        " * and asserted equal-length. */",
        "static const unsigned char k_train_anim_stock[%d] = { %s };"
        % (len(stock_train), ", ".join("0x%02X" % b for b in stock_train)),
        "static const unsigned char k_trn2_anim_stock[%d] = { %s };"
        % (len(stock_trn2), ", ".join("0x%02X" % b for b in stock_trn2)),
        "static const unsigned char k_train_anim_114[2] = { %s };" %
        ", ".join("0x%02X" % b for b in AUTHORED["train_anim_114"]),
        "static const unsigned char k_trn2_anim_114[2] = { %s };" %
        ", ".join("0x%02X" % b for b in AUTHORED["trn2_anim_114"]),
        "",
        "/* Newly raisable species. One row each; the plugin walks this table",
        " * so adding a species is a generator edit, not a plugin edit.",
        " * NOTE the mixed indexing -- item_take is written at [type-1], the",
        " * rest at [type]. */",
        "typedef struct {",
        "    unsigned char type;",
        "    unsigned char req[28];",
        "    unsigned char raise_[28];",
        "    unsigned char gains[14];",
        "    unsigned char item_take;",
        "    unsigned char train[2];",
        "    unsigned char trn2[2];",
        "} RosterSpecies;",
        "",
    ]
    body = []
    for r in AUTHORED["species"]:
        j = lambda bs: ", ".join("0x%02X" % b for b in bs)
        body.append("    { /* %s */ %d," % (r["name"], r["type"]))
        body.append("      { %s }," % j(r["req"]))
        body.append("      { %s }," % j(r["raise_"]))
        body.append("      { %s }," % j(r["gains"]))
        body.append("      0x%02X, { %s }, { %s } },"
                    % (r["item_take"], j(r["train"]), j(r["trn2"])))
    inc += ["static const RosterSpecies k_species[%d] = {"
            % len(AUTHORED["species"])] + body + ["};", ""]
    inc_path = os.path.join(ROOT, "dw_roster_data.inc")
    with open(inc_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(inc))
    print("wrote %s" % inc_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
