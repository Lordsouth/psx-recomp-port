#!/usr/bin/env python3
"""Cup eligibility for the Expanded Roster: its species join the arena cups' entry and penalty lists.

The lists are in DG.SCN, in the arena library script 213 (File City, maps 208/223) and in script 102 (the yearly cup
on map 106): section 0xA is a jump table of 23 cups, five lists each (getCupDataJumpTable, src/main/tournament.c
146-166; docs/GAME.md 15.3). Each list is reached through a u16 offset from the script's start, so the lists can move
anywhere in the script. They are packed against each other and the script's slot has no room after them, so this
re-lays out the whole list area of the script: every list rebuilt (stock lists byte-identical, roster lists with the
additions), identical lists stored once, packed from the area's start, and every u16 rewritten. The stock bytes of a
slot's tail after the lists are unreferenced; the new layout must fit in the slot.

Rules, each read from the stock lists and checked against them for the stock species (check() asserts it):
  grade cups 0-5      every partner (level 3 or more) may enter; cups 0 and 1 list the Ultimates as a penalty
  version cup 10      every partner with an id of 57 or more (the version cups 6-9 are id ranges below that)
  element cups 11-17  the species' first special element: Fire, Battle, Air, Ice, Earth, Mech, Filth
  cups 18-22          hand-picked by the developers (Dino, Wing, Animal, Human, Insect): a roster species joins
                      exactly the cups its stock look-alike in CUP_ANALOGUE is in
A species already on a cup's stock entry list keeps its stock place there (63-66 are on some lists already).
Pools (who you fight) and unlocks are not touched.

    py -3.12 tools/roster_cups.py [--dw15]   # print the before/after diff of every cup on your disc
"""
import struct

DG_SECTION_LISTS, N_CUPS = 0xA, 23
CUP_SCRIPTS = (213, 102)
GRADE, PENALTY_CUPS, VERSION0 = range(0, 6), (0, 1), 10
ELEMENT = {11: 0, 12: 1, 13: 2, 14: 4, 15: 3, 16: 5, 17: 6}          # cup -> DIGIMON_DATA.special[0]
HANDPICKED = range(18, 23)
# Stock look-alike for the hand-picked cups, chosen by body shape (the raise donors are gameplay donors, not shapes).
CUP_ANALOGUE = {
    63: 48, 64: 54, 65: 42, 66: 6, 67: 51, 68: 23, 69: 3, 70: 14, 71: 3, 72: 34, 73: 39, 74: 32, 75: 33,
    76: 41, 77: 25, 78: 52, 79: 38, 80: 34, 81: 23, 82: 17, 83: 4, 84: 3, 85: 20, 86: 46, 87: 11, 88: 47,
    89: 34, 90: 58, 91: 35, 92: 40, 93: 45, 94: 23, 95: 50, 96: 31, 97: 58, 98: 3, 99: 25, 100: 6, 101: 3,
    102: 51, 103: 34, 104: 9, 105: 22, 106: 21, 107: 37, 108: 23, 109: 4, 110: 4, 111: 19, 112: 10, 113: 23,
    114: 54,
}


def _sections(sc):
    out, p = {}, 2
    while True:
        k, off = struct.unpack_from("<HH", sc, p)
        if k == 0xFFFF:
            return out
        out[k] = off
        p += 4


def _list(sc, p):
    q = p
    while sc[q] < 0xFE:
        q += 1
    return bytes(sc[p:q + 1])


def decode(sc):
    """-> (refs, cups): refs[(cup, k)] = (u16 position, data offset); cups[cup][k] = list bytes (k 1..4)."""
    a = _sections(sc)[DG_SECTION_LISTS]
    refs, cups = {}, []
    for cup in range(N_CUPS):
        sub = struct.unpack_from("<H", sc, a + cup * 4 + 2)[0]
        row = {}
        for k in range(1, 5):
            pos = sub + k * 4 + 2
            off = struct.unpack_from("<H", sc, pos)[0] + 2
            refs[(cup, k)] = (pos, off)
            row[k] = bytes(sc[off:off + 3]) if k == 1 else _list(sc, off)
        cups.append(row)
    return refs, cups


def members(blob):
    return [b for b in blob if b < 0xFE]


def additions(cups, info, roster):
    """-> {(cup, k): [species to add]} from the rules. info(t) -> (level, special0)."""
    add = {}
    for cup in range(N_CUPS):
        entry = set(members(cups[cup][3]))
        new = []
        for t in roster:
            if t in entry:
                continue
            lvl, el = info(t)
            if cup in GRADE:
                ok = lvl >= 3
            elif cup == VERSION0:
                ok = lvl >= 3 and t >= 57
            elif cup in ELEMENT:
                ok = lvl >= 3 and el == ELEMENT[cup]
            elif cup in HANDPICKED:
                ok = lvl >= 3 and CUP_ANALOGUE[t] in entry
            else:
                ok = False
            if ok:
                new.append(t)
        add[(cup, 3)] = new
        add[(cup, 2)] = [t for t in new if cup in PENALTY_CUPS and info(t)[0] == 5]
    return add


def check(cups, info, stock_types):
    """The rules must reproduce the stock lists for the stock partners (types below 63)."""
    allp = [t for t in stock_types if info(t)[0] >= 3]
    for cup in range(N_CUPS):
        got = set(t for t in members(cups[cup][3]) if t < 63)
        if cup in GRADE:
            want = set(allp)
        elif cup == VERSION0:
            want = set(t for t in allp if t >= 57)
        elif cup in ELEMENT:
            want = set(t for t in allp if info(t)[1] == ELEMENT[cup])
        else:
            continue
        assert got == want, "cup %d: the rule does not reproduce the stock list (%s vs %s)" % (
            cup, sorted(got ^ want), cup)
        if cup in PENALTY_CUPS:
            pen = set(t for t in members(cups[cup][2]) if t < 63)
            assert pen == set(t for t in allp if info(t)[0] == 5), "cup %d penalty list is not the Ultimates" % cup
    for t, a in CUP_ANALOGUE.items():
        assert a < 63 and info(a)[0] >= 3, "analogue %d of %d is not a stock partner" % (a, t)


def relayout(sc, add):
    """-> new script bytes with every list rebuilt; the list area starts at the lowest list offset."""
    refs, cups = decode(sc)
    start = min(off for _, off in refs.values())
    blobs = {}
    for (cup, k) in refs:
        b = cups[cup][k]
        extra = bytes(add.get((cup, k), []))
        if extra:
            b = b[:-1] + extra + b[-1:]
        blobs[(cup, k)] = b
    out = bytearray(sc)
    at, placed = start, {}
    for key in sorted(blobs):
        b = blobs[key]
        if b not in placed:
            placed[b] = at
            out[at:at + len(b)] = b
            at += len(b)
    assert at <= len(sc), "the cup lists need %d bytes past the slot" % (at - len(sc))
    for key, (pos, _) in refs.items():
        struct.pack_into("<H", out, pos, placed[blobs[key]] - 2)
    return bytes(out), at


def diff(before, after):
    """Lines describing every cup's entry and penalty lists before and after; asserts stock entries survive."""
    _, b = decode(before)
    _, a = decode(after)
    lines = []
    for cup in range(N_CUPS):
        for k, name in ((3, "entry"), (2, "penalty"), (4, "pool"), (1, "requirement")):
            old, new = (members(b[cup][k]), members(a[cup][k])) if k != 1 else (list(b[cup][k]), list(a[cup][k]))
            if k in (1, 4):
                assert old == new, "cup %d %s changed" % (cup, name)
                continue
            assert old == new[:len(old)], "cup %d %s: stock entries changed" % (cup, name)
            lines.append("cup %2d %-7s stock %3d, added %s" % (cup, name, len(old), new[len(old):]))
    return lines


def patches(dg, lba, info, roster, feature, disc_bytes_patch, stock_types=range(1, 63)):
    """Guarded disc_user patches rewriting the cup lists of CUP_SCRIPTS in this DG.SCN image."""
    out, report = [], []
    for sn in CUP_SCRIPTS:
        lo, hi = struct.unpack_from("<2I", dg, sn * 4)
        sc = dg[lo:hi]
        _, cups = decode(sc)
        check(cups, info, stock_types)
        new, end = relayout(sc, additions(cups, info, roster))
        report.append("script %d: lists end at 0x%X of 0x%X" % (sn, end, len(sc)))
        report += ["  " + x for x in diff(sc, new)]
        i = 0
        while i < len(sc):                       # one patch per changed run, split at sector starts
            if new[i] == sc[i]:
                i += 1
                continue
            j = i + 1                            # runs closer than 16 bytes merge; a patch stays in one sector
            while j < len(sc) and (lba * 2048 + lo + j) % 2048 and any(
                    new[x] != sc[x] for x in range(j, min(j + 16, len(sc)))):
                j += 1
            while new[j - 1] == sc[j - 1]:
                j -= 1
            out += disc_bytes_patch(lba * 2048 + lo + i, sc[i:j], new[i:j],
                                    "DG script %d +0x%X: cup lists" % (sn, i), feature)
            i = j
    return out, report


# ---- versus mode (a roster partner registered on the save screen reaches it) -------------------------
# The versus portraits sheet (\STDDAT\16TAISEN.TIM) holds types 3..65 in cells 0..62 and Machinedramon in
# cell 63, drawn as type 66 or by the stock type-115 special case. Both renderers take the cell from type-3
# and the palette from a 68-entry table, so 66 and up wrap onto other portraits and read past the table.
# With the roster on, both first map the type through a 180-byte table in the arena: stock types with a
# portrait unchanged, Machinedramon (114, 115) to cell 63, every other roster species to its CUP_ANALOGUE
# look-alike, anything else to cell 63. The fighter's name is still drawn from its own record.
VS_SITES = {
    "select": 0x800F9124,      # VS__renderSelectDigimonPlayer, main EXE, type in s1, DIGIMON_SPRITE_CLUT
    "plate": 0x800574A0,       # VS_renderFighterNamePlate, VS_REL.BIN, type in s2, VS_DIGIMON_SPRITE_CLUT
}
VS_STOCK = {
    "select": [0x3C028013, 0x2442F528, 0x00511021, 0x90420000, 0x24040120, 0x0C024AAF, 0x204501E0,
               0x24010073, 0x16210007, 0xA642000E, 0x92620030, 0x240600E0, 0x00021100],
    "plate": [0x3C028007, 0x2442FA28, 0x00521021, 0x90420000, 0x24040120, 0x0C024AAF, 0x204501E0,
              0x24010073, 0x16410004, 0xA602000E, 0x240600E0, 0x10000010, 0x00C02821],
}


def vs_remap(roster):
    return bytes(t if t <= 65 else 66 if t in (114, 115) or t not in roster else CUP_ANALOGUE[t]
                 for t in range(180))


def vs_words(site, arena_off):
    """The 13 new words: type = remap[type], then the stock palette read, without the 115 special case
    (the remap never yields 115, and cell 63 drawn as 66 is the same quad)."""
    r = {"select": 17, "plate": 18}[site]                       # s1 / s2
    assert arena_off < 0x8000
    lui_at = 0x3C000000 | (1 << 16) | 0x9F00                    # lui at, 0x9F00
    addu_at = (1 << 21) | (r << 16) | (1 << 11) | 0x21          # addu at, at, rN
    lbu_r = (0x24 << 26) | (1 << 21) | (r << 16) | arena_off    # lbu rN, off(at)
    st = VS_STOCK[site]
    tail = [st[9], 0x10000004 if site == "select" else 0x10000001, 0]   # sh v0,14(..); b past the 115 case; nop
    return [lui_at, addu_at, lbu_r] + st[0:2] + [(st[2] & ~(31 << 16)) | (r << 16)] + st[3:7] + tail


# ---- 1.5: the projectile icon of 112/113 (no arena on 1.5) --------------------------------------------
# A six-word helper in the padding after roster-15's own setDigimonRaised, called from the four icon reads
# in STD_renderBracketProjectile: v0 = 0x80080000 + min(type, 111), then lbu v0,-0x5AF4(v0) reads the
# stock table at a clamped index. v1 holds the type at each site and is dead after the read.
DW15_ICON_HELPER = 0x801F0D84
DW15_ICON_HELPER_WORDS = [0x2C610070,   # sltiu at,v1,112
                          0x14200002,   # bne   at,zero,+2
                          0x3C028008,   # lui   v0,0x8008      (delay)
                          0x2403006F,   # addiu v1,zero,111    (type >= 112)
                          0x03E00008,   # jr    ra
                          0x00431021]   # addu  v0,v0,v1       (delay)
DW15_ICON_SITES = [0x8005E250, 0x8005E29C, 0x8005E3B8, 0x8005E404]
DW15_ICON_SITE_STOCK = [0x3C028008, 0x2442A50C, 0x00431021, 0x90420000]
DW15_ICON_SITE_NEW = [0x0C000000 | ((DW15_ICON_HELPER >> 2) & 0x3FFFFFF), 0, 0, 0x9042A50C]


if __name__ == "__main__":
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from dw_tables import DEFAULT_BIN, Disc
    from gen_roster_mod import NEW_SPECIES
    roster = sorted(sp["type"] for sp in NEW_SPECIES)
    if "--dw15" in sys.argv:                 # 1.5's own species table and DG.SCN, roster-15 (no 114)
        from check_dw15_compat import RAM_BASE, RELOCATED, Disc as Disc15, Exe
        from gen_roster_dw15 import DIGIMON_DATA, DD_STRIDE
        d15 = Disc15(os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..",
                                                    "dw15-release", "patched", "digimon_world_1_5_test1.bin")))
        ram = Exe(d15.file("/SLUS_010.32"), RELOCATED).ram
        info = lambda t: tuple(ram[DIGIMON_DATA + t * DD_STRIDE + 29 - RAM_BASE:][:2])
        lba, size = d15.files["/SCN/DG.SCN"] if "/SCN/DG.SCN" in d15.files else d15.files["/DG.SCN"]
        dg = d15.user(lba * 2048, size)
    else:
        d = Disc(DEFAULT_BIN)
        sp = lambda t: d.species(115 if t == 114 else t)
        info = lambda t: (sp(t)["level"], sp(t)["special"][0])
        roster += [114]
        dg, lba = d.read("DG.SCN"), d.files["DG.SCN"][0]
    _, rep = patches(dg, lba, info, roster, "roster", lambda *a: [])
    print("\n".join(rep))
