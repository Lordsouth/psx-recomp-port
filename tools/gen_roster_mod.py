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

# ---- tables being relocated ------------------------------------------------
RELOC = {
    "EVO_REQ_DATA":       (0x8012ABEC, 63 * 28, OFF_EVO_REQ),
    "RAISE_DATA":         (0x801225BC, 66 * 28, OFF_RAISE),
    "EVO_GAINS_DATA":     (0x8012B2D0, 66 * 14, OFF_EVO_GAINS),
    "ITEM_TAKE_DISTANCE": (0x80122D24, 67, OFF_ITEM_TAKE),
}

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


def authored_research(rd):
    """Constants sourced from the data-authoring research (research/roster-*):
    evolution wiring, guard widening, disc fixes, and the 114 entries."""
    extra = []
    # 5. raised-flag guard: sltiu $at,$a0,0x3f -> 0xb4 so setDigimonRaised
    #    records types up to 179 (hasDigimonRaised is already unbounded).
    g = 0x800FF7FC
    assert rd(g, 4) == struct.pack("<I", 0x2C81003F)
    extra += word_patch(g, 0x2C81003F, 0x2C8100B4)
    # 6. evolution wiring: Machinedramon (114) reachable from the two
    #    machine-line Ultimates. to[] slots skip -1 entries, any free byte
    #    works (evolution.c:73).
    for src_name, va in (("Megadramon", 0x8012B8B8),
                         ("MetalGreymon", 0x8012B6EA)):
        assert rd(va, 1) == bytes([0xFF]), src_name
        extra += bytes_patch(va, bytes([0xFF]), bytes([114]))
    # 7. offense/MP training anim tables live in TRN/TRN2 overlays on disc,
    #    indexed by RAW type: slot 114 reads string bytes (TRN: invalid anim
    #    101) and zeros (TRN2: threshold 0 soft-locks MP training). Anim 46 is
    #    the row 60 of 66 partners share, proven present in the MUGE skeleton.
    def disc_patch(offset, old_b, new_b, note):
        return [
            "# %s" % note,
            "[[patch]]",
            'feature = "roster"',
            'target = "disc_user"',
            "offset = %d" % offset,
            'expected = "%s"' % hexs(old_b),
            'replace = "%s"' % hexs(new_b),
            "",
        ]
    extra += disc_patch(303638836, bytes([0x65, 0x20]), bytes([0x2E, 0x20]),
                        "TRN_REL.BIN offense-training anim for type 114")
    extra += disc_patch(303661828, bytes([0x00, 0x00]), bytes([0x2E, 0x0A]),
                        "TRN2_REL.BIN MP-training anim for type 114 "
                        "(threshold 0 would soft-lock)")

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


def scan_sites(data):
    """[(table, lui_vaddr, lui_word, lo_vaddr, lo_word, target)]"""
    text = data[HDR:HDR + (CODE_END - LOAD_ADDR)]
    words = struct.unpack("<%dI" % (len(text) // 4), text)
    live = {}   # reg -> (hi16, lui_vaddr, lui_word)
    out = []
    for i, w in enumerate(words):
        va = LOAD_ADDR + i * 4
        op = w >> 26
        rs = (w >> 21) & 31
        rt = (w >> 16) & 31
        if op == 0x0F:                       # lui
            live[rt] = (w & 0xFFFF, va, w)
            continue
        if (op == 0x09 or op in LOADS or op in STORES) and rs in live:
            hi, lui_va, lui_w = live[rs]
            tgt = ((hi << 16) + sext16(w & 0xFFFF)) & 0xFFFFFFFF
            for name, (base, size, _aoff) in RELOC.items():
                if base <= tgt < base + size or tgt == base:
                    out.append((name, lui_va, lui_w, va, w, tgt))
        d = dest_reg(w)
        if d is not None and d in live:
            # addiu rt,rs,imm with rt==rs keeps the pair usable for chained
            # materializations only when rt is a fresh reg; conservative:
            # invalidate on any write, except the lui itself handled above.
            del live[d]
    return out


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
        lo_patches.append((lo_va, lo_w, new_lo))
    return lui_patches, lo_patches


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


def main():
    check_only = "--check" in sys.argv
    data = open(EXE, "rb").read()

    sites = scan_sites(data)
    from collections import Counter
    c = Counter(s[0] for s in sites)
    print("reference sites:", dict(c))
    lui_patches, lo_patches = retarget(sites)
    print("patches: %d lui (deduped) + %d lo16" %
          (len(lui_patches), len(lo_patches)))

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

    out = [
        "# Digimon World - Expanded Roster. Generated by",
        "# tools/gen_roster_mod.py; regenerate rather than hand-editing.",
        "",
        "format_version = 5",
        'id = "%s"' % PKG_ID,
        'version = "%s"' % PKG_VER,
        'name = "Expanded Roster"',
        'author = "DigimonWorldVanilla"',
        'description = "Grows the partner-line tables beyond their stock '
        'sizes and completes the game one unused Digimon slot: Machinedramon '
        'becomes a legitimate, raisable partner with a real evolution '
        'requirement. Powered by table relocation into enhancement memory."',
        'resolver = "declarative"',
        'save_compatibility = "isolated"',
        "",
        "[[target]]",
        'game_id = "%s"' % GAME_ID,
        "",
        "[[feature]]",
        'id = "roster"',
        'name = "Machinedramon (new raisable Digimon)"',
        'description = "Relocates and grows the evolution-requirement, raise '
        'and evolution-gain tables, then wires slot 114 as Machinedramon: '
        'model, skeleton, battle auras, stats and an evolution path. The '
        'touched functions run interpreted until the background compiler '
        'rebuilds them natively."',
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
    ]
    inc_path = os.path.join(ROOT, "dw_roster_data.inc")
    with open(inc_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(inc))
    print("wrote %s" % inc_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
