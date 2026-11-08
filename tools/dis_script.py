#!/usr/bin/env python3
"""Disassemble a DG.SCN script or a MAPHEAD.SCN section from your own disc. Prints only.

    py -3.12 tools/dis_script.py 118              # every section of DG script 118
    py -3.12 tools/dis_script.py 182 0x33 0xFE    # just these sections
    py -3.12 tools/dis_script.py --maphead 2      # a MAPHEAD row, or a special such as 0x4DE
    py -3.12 tools/dis_script.py --maphead        # every MAPHEAD section

Each section is decoded by following its flow from the entry, as scan_trigger_ids.py
does, so the junk stock DG slots hold is never read as code; lines print in offset
order, with offsets from the script's start. A path that leaves the section's own
bytes is still shown. `???` marks a byte no handler takes: the game ends the script
there. Op names and operands are dw_decomp's include/dw/script.h (MIT); the formats
are in docs/GAME.md section 4.

The opcode table below is copied from dw_decomp (https://github.com/jype0/dw_decomp):

    MIT License. Copyright (c) 2026 jype

    Permission is hereby granted, free of charge, to any person obtaining a copy of this
    software and associated documentation files (the "Software"), to deal in the Software
    without restriction, including without limitation the rights to use, copy, modify,
    merge, publish, distribute, sublicense, and/or sell copies of the Software, and to
    permit persons to whom the Software is furnished to do so, subject to the following
    conditions:

    The above copyright notice and this permission notice shall be included in all copies
    or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
    INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A
    PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
    HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF
    CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE
    OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
"""
import argparse
import os
import struct
import sys
import unicodedata
from itertools import takewhile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scan_trigger_ids as S  # noqa: E402
from dw_tables import DEFAULT_BIN, Disc  # noqa: E402

# op, name, operands as a struct format ("x" is a pad byte). No format: decoded by decode() below.
OPS = {int(line.split()[0], 16): (line.split() + [""])[1:3] for line in """
10 SET_SELECTION
11 UNUSED_11
12 UNUSED_12
13 JUMP_AND_LINK xH
14 JUMP_TO_FILE_AND_LINK xHH
15 JUMP_RETURN x
16 JUMP_TO xH
17 JUMP_TO_FILE xHH
18 SWITCH
19 IF
1A SHOW_TEXTBOX
1B SET_DIALOG_OWNER B
1C SET_TRIGGER xH
1D UNSET_TRIGGER xH
1E SET_PSTAT xBB
1F ADD_TO_PSTAT xBB
20 REDUCE_PSTAT xBB
21 STORE_MAP_ID B
22 STORE_DIGIMON_TYPE B
23 SET_INVENTORY_SIZE B
24 STORE_RANDOM xBB
25 STORE_DATE B
26 SET_TEXTBOX_SIZE BBB
27 FADEOUT_HUD B
28 GIVE_ITEM xBB
29 REMOVE_ITEM xBB
2A ADD_MONEY xi
2B REDUCE_MONEY xi
2C COMPARE_DATE BHBBBBBx
2D LEARN_MOVE B
2E UNUSED_2E B
2F GIVE_CARD B
30 TAKE_CARD B
31 SET_MERIT xH
32 ADD_MERIT xH
33 REDUCE_MERIT xH
34 SET_STAT BH
35 ADD_STAT BH
36 REDUCE_STAT BH
37 ADVANCE_TO_DATE_AT B
38 ADD_MINUTES_TO_DATE_AT Bi
39 ADD_MINUTES_TO_DATE_AT_2 Bi
3A UNUSED_3A xBB
3B UNUSED_3B xBB
3C UNUSED_3C xBB
3D UNUSED_3D B
3E UNUSED_3E B
3F STORE_DIGIMON_VALUE xBB
46 LOAD_DIGIMON B
47 SET_DIGIMON BBB
48 UNLOAD_ENTITY B
49 CALL_DIGIMON_ROUTINE B
4A WAIT_FOR_ENTITY B
4B WARP_TO BBB
4C ENTITY_LOOK_AT_ENTITY xBB
4D ENTITY_SET_ROTATION Bh
4E ENTITY_WALK_TO BhhBB
4F MOVE_CAMERA_TO Bhh
50 MOVE_CAMERA_TO_ENTITY xBB
51 ENTITY_WALK_TO_ENTITY BBB
52 ENTITY_WALK_TO_WITH_CAMERA BhhBB
53 ENTITY_WALK_TO_ENTITY_WITH_CAMERA BBB
54 RESET_ENTITY_ORIGIN B
55 SET_TEXTBOX_ORIGIN xhhh
56 PLAY_ANIMATION xBB
57 SET_OBJ_VISIBILITY xBB
58 TELEPORT B
5A PLAY_SOUND xBB
5B UNUSED_5B B
5C UNUSED_5C B
5D SET_BGM B
5E STOP_BGM B
5F UNUSED_5F B
64 CALL_ROUTINE B
65 REMOVE_CONDITION B
66 START_BATTLE B
67 DELAY xH
68 SET_TEXTBOX_MODE xBB
69 DEAL_DAMAGE B
6A SET_AUTOTALK xBB
6B DATA
6C ENTITY_MOVE_TO BhhBB
6D ENTITY_MOVE_TO_ENTITY BBB
6E ENTITY_MOVE_TO_WITH_CAMERA BhhBB
6F ENTITY_MOVE_TO_ENTITY_WITH_CAMERA BBB
70 ROTATE_3D_OBJECT BBB
71 MOVE_OBJECT_TO BBBbxhh
72 ENTITY_MOVE_TO_AXIS BhBB
73 ENTITY_MOVE_TO_AXIS_WITH_CAMERA BhBB
74 SPAWN_ITEM Bhh
75 SPAWN_CHEST BhhhhH
76 SPAWN_BOULDER B
77 MOVE_BOULDER Bhh
78 DESPAWN_BOULDER B
79 UNLOAD_DIGIMON B
7A COPY_PSTAT xBB
7B SECTION_ON_EXIT B
7C SET_RECT_IMPASSABLE BhhBB
7D SPAWN_SPRITE_AT_LOCATION Bhhhh
7E SPAWN_SPRITE_AT_ENTITY BBB
FB SET_SCRIPT xHH
FC UNUSED_FC
FD UNUSED_FD
FE END_SECTION
FF END_SCRIPT
""".strip().splitlines()}
assert all(struct.calcsize("<" + f) == S.SIMPLE[op] for op, (_, f) in OPS.items() if f and op in S.SIMPLE)

CMP = ("==", "!=", ">=", "<=", ">", "<")
GLYPHS = {b"\x81\x42": ".", b"\x81\x75": "'", b"\x81\x76": '"'}    # where the US font's glyph is not Shift-JIS's


def u16(d, p):
    return struct.unpack_from("<H", d, p)[0]


def text(d, p):
    """Text at p as ASCII, and the offset past it. Pairs of bytes; {xx yy} is a control code, <xxyy> no ASCII glyph."""
    s = ""
    while True:
        pair = d[p:p + 2]
        c = pair[0]
        if c == 3:
            s += "{03 %s}" % d[p + 1:p + 4].hex()
            p += 4
        elif c == 13:
            p += 2
            if d[p] == 0:
                return s, p + 2
            s += "/"
        elif c < 0x20:
            s += "{%02x %02x}" % (c, pair[1])
            p += 2
        else:
            try:
                g = GLYPHS.get(pair) or unicodedata.normalize("NFKC", pair.decode("cp932"))
            except UnicodeDecodeError:
                g = ""
            s += g if g.isascii() and g else "<%s>" % pair.hex()
            p += 2


def if_block(d, p):
    """The entries of an IF, and the offset past it, and the offsets it can jump to."""
    parts, targets = [], []
    while True:
        c = d[p]
        p += 2
        if c == 0x19:
            return " ".join(parts), p, targets
        kind, low = c & 0x38, c & 7
        glue = "and " if c & 0x80 else "or " if c & 0x40 else ""
        if kind == 0:
            parts.append("%strigger %d %s" % (glue, u16(d, p), "clear" if low else "set"))
            p += 2
        elif kind == 0x08:
            parts.append("%spstat %d %s %d" % (glue, d[p], CMP[low], d[p + 1]))
            p += 2
        elif kind in (0x10, 0x18):
            targets.append(u16(d, p))
            parts.append("%s->%04x" % ("then" if kind == 0x10 else "else", targets[-1]))
            p += 2
        elif kind == 0x20 and low in (0, 1, 4):
            parts.append("%s%s %d %s %d" % (glue, {0: "stat", 1: "card", 4: "item"}[low], d[p], CMP[d[p + 1] & 7], u16(d, p + 2)))
            p += 4
        elif kind == 0x20 and low in (2, 3):
            parts.append("%s%s%s %d" % (glue, "not " if d[p + 1] else "", {2: "move", 3: "condition"}[low], d[p]))
            p += 2
        elif kind == 0x20 and low == 5:
            parts.append("%smoney %s %d" % (glue, CMP[d[p + 1] & 7], struct.unpack_from("<i", d, p + 2)[0]))
            p += 6
        else:
            raise ValueError("IF entry %02x" % c)


def decode(d, p):
    """One instruction: (text, next offset or None if flow stops here, local jump targets)."""
    op = d[p]
    if op not in OPS:
        raise ValueError("%02x" % op)
    name, fmt = OPS[op]
    q = p + 1
    if op == 0x10:      # n offsets, 1A 00, text, and if cancel is allowed 12 00 and the code it lands on
        n = d[q]
        targets = list(struct.unpack_from("<%dH" % n, d, q + 1))
        s, q = text(d, q + 1 + 2 * n + 2)
        cancel = q + 2 if d[q:q + 2] == b"\x12\x00" else None
        return ('%s %s cancel %s "%s"' % (name, " ".join("->%04x" % t for t in targets),
                                          "no filler" if cancel is None else "->%04x" % cancel, s), cancel, targets)
    if op == 0x18:
        targets = list(struct.unpack_from("<%dH" % u16(d, q + 1), d, q + 3))
        return "%s pstat %d %s" % (name, d[q], " ".join("->%04x" % t for t in targets)), None, targets
    if op == 0x19:
        s, q, targets = if_block(d, q + 1)
        return "%s %s" % (name, s), q, targets
    if op == 0x1A:
        s, q = text(d, q + 1)
        return '%s "%s"' % (name, s), q, []
    args = struct.unpack_from("<" + fmt, d, q)
    q += struct.calcsize("<" + fmt)
    if op in (0x13, 0x16):
        return "%s ->%04x" % (name, args[0]), None if op == 0x16 else q, [args[0]]
    if op in (0x14, 0x17):
        return "%s script %d section 0x%x" % (name, args[0], args[1]), None if op == 0x17 else q, []
    return " ".join([name] + [str(a) for a in args]), None if op in (0x15, 0xFE, 0xFF) else q, []


def disassemble(d, entry):
    """Every instruction reachable from entry, as printable lines in offset order."""
    lines, work = {}, [entry]
    while work:
        p = work.pop()
        while p is not None and p not in lines:
            try:
                s, nxt, targets = decode(d, p)
            except (IndexError, ValueError, struct.error) as exc:
                lines[p] = "%04x ??? %s" % (p, exc)
                break
            lines[p] = "%04x %s" % (p, s)
            work += targets
            p = nxt
    return [lines[p] for p in sorted(lines)]


def show(d, sections, want):
    known = dict(sections)
    for sid in want or [s for s, _ in sections]:
        if sid not in known:
            sys.exit("no section 0x%x; these exist: %s" % (sid, " ".join("0x%x" % s for s, _ in sections)))
        print("--- section 0x%x at +0x%x" % (sid, known[sid]))
        if sid == S.DATA_SECTION_DGET and not S.decodes_as_code(d, 0, len(d), known[sid]):
            print("species ids, not code:", " ".join(str(b) for b in takewhile(lambda b: b < 0xFE, d[known[sid] + 2:])))
            continue
        print("\n".join(disassemble(d, known[sid])))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", default=DEFAULT_BIN)
    ap.add_argument("--maphead", action="store_true", help="the ids are MAPHEAD sections, not a DG script then sections")
    ap.add_argument("ids", nargs="*", type=lambda s: int(s, 0))
    a = ap.parse_args()
    if a.maphead:
        d = Disc(a.bin).read("MAPHEAD.SCN")
        show(d, S.parse_sections(d, 0, len(d))[0], a.ids)
    elif a.ids:
        dg = Disc(a.bin).read("DG.SCN")
        blobs = {int(b[0][6:]): b for b in S.blob_list("DG.SCN", dg)}
        if a.ids[0] not in blobs:
            sys.exit("no script %d: DG.SCN has 0..%d" % (a.ids[0], max(blobs)))
        _, s, e, _, _ = blobs[a.ids[0]]
        d = dg[s:e]
        sections = S.parse_sections(d, 0, len(d))[0]
        print("DG script %d: offset 0x%x, 0x%x bytes, sections %s"
              % (a.ids[0], s, e - s, " ".join("0x%x" % x for x, _ in sections)))
        show(d, sections, a.ids[1:])
    else:
        ap.error("give a DG script id, or --maphead")


if __name__ == "__main__":
    main()
