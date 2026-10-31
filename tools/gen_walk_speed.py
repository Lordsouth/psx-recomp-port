#!/usr/bin/env python3
"""Generate the "Run Speed" feature (id "walk-speed"): the tamer runs 1.5x or 2x.

How the tamer moves (dw_decomp, US):
  There is no speed variable. Field movement is animation root motion:
  tamerTickOverworld (0x800A9CAC) calls tamerTickWalkingState (0x800A9DF8),
  then tickAnimation (0x800C1C64), whose tickMomentum / applyRootMomentum
  (0x800C1FBC / 0x800C25C0) add the root bone's momentum to the position.
  The game already runs by default: a held direction plays anim 3 (run, 50
  units per 20 Hz logic frame); holding R1 plays anim 2 (walk, 20 units) --
  the Health Shoe's "walk and HP/MP go up" checks animId == 2. Collision
  (entityCheckCollision 0x800D45EC) probes one momentum step ahead, and map
  exits / talk triggers are checked once per tamerTickWalkingState
  (checkMapInteraction). The camera follows in tickCameraFollowPlayer
  (0x800D6F64, called from renderMap) by the screen delta between
  STORED_TAMER_POS, saved at the end of tamerTickWalkingState, and the
  current position.

What this does:
  While the player has control and is running, give the tamer extra frames of
  movement per game frame: tickAnimation + tickCameraFollowPlayer +
  tamerTickWalkingState again. Each extra step is a whole vanilla frame of
  tamer movement, so collision is probed before every step, map exits / NPC
  talk / chests / item pickups are checked after every step, and the camera
  moves with every step. 2x does one extra step every frame; 1.5x does one on
  every other frame (odd libetc VSync count, 0x80116B30: logic frames are 3
  vblanks apart). No extra step unless TAMER_STATE == 0, IS_SCRIPT_PAUSED == 1
  (no script/dialogue running) and the tamer's anim is 3 (run), so walking
  with R1, idling, menus, cutscenes and scripted walks (tickEntityWalkTo,
  which stops on reaching a tile and could overshoot) are untouched.

  The code lives inside tamerTickOverworld itself. Its 21-way switch is a jump
  table (0x80114334) of 4-instruction case blocks `jal f; delay; b end; nop`.
  The table is rewritten to hold the callees themselves and the dispatch
  becomes `jalr`, with 2-instruction tail-call stubs for the two cases whose
  delay slot sets an argument (state 1: tamerSetState(6); states 9/13:
  tamerTickPraiseScold(state)). a0/a1 reach every callee exactly as before.
  That frees enough of the function for the extra-step loop. Both the function
  and the table are referenced only by the function itself.

  ponytail: logic-frame granularity -- 1.5x alternates 1 and 2 steps per frame,
  and drifts toward 1x/2x on frames that lag past 3 vblanks. A smoother 1.5x
  needs a per-entity momentum scale in native code.

  Digimon World 1.5: tamerTickOverworld and its jump table are byte-identical
  in 1.5's executable, so the same patches apply with or without dw.1_5/dw15.

  python tools/gen_walk_speed.py                        # verify only
  python tools/gen_walk_speed.py --out <manifest.toml>  # also write a standalone WIP manifest
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REL = os.path.normpath(os.path.join(HERE, "..", "..", "dw15-release"))
EXE = os.path.join(REL, "SLUS_010.32.vanilla")
EXE_DW15 = os.path.join(REL, "SLUS_010.32.dw15")
LOAD_ADDR = 0x80090800
HDR = 0x800

FN, FN_END = 0x800A9CAC, 0x800A9DF8      # tamerTickOverworld
TABLE, CASES = 0x80114334, 21            # its switch jump table
END = 0x800A9DDC                         # tickAnimation(&TAMER_ENTITY) + epilogue
WALKING = 0x800A9DF8                     # tamerTickWalkingState
TICK_ANIM = 0x800C1C64                   # tickAnimation
CAMERA = 0x800D6F64                      # tickCameraFollowPlayer
VCOUNT = 0x80116B30                      # libetc VSync counter (VSync(-1) returns it)
TAMER_ENTITY = 0x8015576C
GP_TAMER_STATE = -0x6E9B                 # TAMER_STATE 0x80134C91
GP_SCRIPT_PAUSED = -0x6B38               # IS_SCRIPT_PAUSED 0x80134FF4

ZERO, AT, V0, V1, A0, A1, S0, GP, SP, RA = 0, 1, 2, 3, 4, 5, 16, 28, 29, 31
NOP = 0


def i_(op, rs, rt, imm):
    return (op << 26) | (rs << 21) | (rt << 16) | (imm & 0xFFFF)


def br(op, rs, rt, pc, target):
    return i_(op, rs, rt, (target - pc - 4) >> 2)


def jal(t):
    return 0x0C000000 | ((t >> 2) & 0x3FFFFFF)


def j(t):
    return 0x08000000 | ((t >> 2) & 0x3FFFFFF)


def addiu(rt, rs, imm):
    return i_(9, rs, rt, imm)


def lui(rt, imm):
    return i_(0xF, 0, rt, imm)


def lo16(a):
    return a & 0xFFFF


def hi16(a):                              # paired with a sign-extending lo16
    return ((a + 0x8000) >> 16) & 0xFFFF


JR_RA = (RA << 21) | 8
JALR_V0 = (V0 << 21) | (RA << 11) | 9

# Original dispatch prologue (0x800A9CAC..0x800A9CE0) and end block (END..FN_END),
# verified word for word before anything is rewritten.
HEAD = [0x83829165, 0x27BDFFE8, 0xAFBF0010, 0x2C410015, 0x10200047, 0x00402821,
        0x3C038011, 0x24634334, 0x00021080, 0x00431021, 0x8C420000, 0x00000000,
        0x00400008, 0x00000000]
TAIL = [0x3C048015, 0x0C030719, 0x2484576C, 0x8FBF0010, 0x00000000, 0x03E00008,
        0x27BD0018]

SPEEDS = [("1.5x", "1.5x", True), ("2x", "2x", False)]   # (value, label, alternate frames)


def words(data, addr, n):
    off = addr - LOAD_ADDR + HDR
    return list(struct.unpack("<%dI" % n, data[off:off + 4 * n]))


def pack(ws):
    return struct.pack("<%dI" % len(ws), *ws)


def hexs(b):
    return " ".join("%02x" % x for x in b)


def build(data):
    """(stock fn bytes, {speed: new fn bytes}, stock table bytes, new table bytes)."""
    n_fn = (FN_END - FN) // 4
    fn = words(data, FN, n_fn)
    table = words(data, TABLE, CASES)
    if fn[:len(HEAD)] != HEAD or words(data, END, len(TAIL)) != TAIL:
        raise SystemExit("tamerTickOverworld at 0x%08X is not the stock dispatch" % FN)
    # Each table entry: END, or a case block `jal f; delay; b END; nop`.
    cases = []
    for e in table:
        if e == END:
            cases.append(None)
            continue
        k = (e - FN) // 4
        jw, delay, bw, tail = fn[k:k + 4]
        falls_through = e + 8 == END                  # last block (state 20) runs into END
        if (jw >> 26) != 3 or not falls_through and (bw != br(4, 0, 0, e + 8, END) or tail != NOP):
            raise SystemExit("case block at 0x%08X is not jal/delay/b end/nop" % e)
        cases.append((0x80000000 | ((jw & 0x3FFFFFF) << 2), delay))
    if cases[0] != (WALKING, NOP):
        raise SystemExit("state 0 does not call tamerTickWalkingState")

    new_end = FN + 4 * len(HEAD)
    pre = HEAD[:]
    pre[4] = br(4, AT, ZERO, FN + 16, new_end)       # beqz $at, end (moved)
    pre[12] = JALR_V0                                 # jr $v0 -> jalr $v0
    pre += TAIL
    pc = FN + 4 * len(pre)
    stubs = {}                                        # (callee, delay) -> stub address
    for c in cases:
        if c and c[1] != NOP and c not in stubs:
            stubs[c] = pc
            pre += [j(c[0]), c[1]]                    # tail call, argument in the delay slot
            pc += 8
    ret = pc
    pre += [JR_RA, NOP]
    cave = pc + 8
    new_table = [cave if i == 0 else ret if c is None else stubs.get(c, c[0])
                 for i, c in enumerate(cases)]

    out = {}
    for value, _label, alternate in SPEEDS:
        c = cave
        loop, done = c + 0x10, c + 0x74
        code = [
            addiu(SP, SP, -0x20), i_(0x2B, SP, RA, 0x18), i_(0x2B, SP, S0, 0x14),
            addiu(S0, ZERO, 1),                                   # one extra step at most
            jal(WALKING), NOP,                                    # loop:
            br(4, S0, ZERO, c + 0x18, done), addiu(S0, S0, -1),
            i_(0x20, GP, V0, GP_TAMER_STATE),                     # lb  TAMER_STATE
            i_(0x23, GP, V1, GP_SCRIPT_PAUSED),                   # lw  IS_SCRIPT_PAUSED
            br(5, V0, ZERO, c + 0x28, done), addiu(V1, V1, -1),   # state != 0 -> done
            br(5, V1, ZERO, c + 0x30, done), lui(AT, hi16(TAMER_ENTITY + 0x2E)),
            i_(0x24, AT, V0, lo16(TAMER_ENTITY + 0x2E)),          # lbu animId
            addiu(V1, ZERO, 3),
            br(5, V0, V1, c + 0x40, done), lui(AT, hi16(VCOUNT)),  # not running -> done
            i_(0x23, AT, V0, lo16(VCOUNT)), NOP,
            i_(0xC, V0, V0, 1) if alternate else i_(0xD, ZERO, V0, 1),  # andi 1 / ori 1
            br(4, V0, ZERO, c + 0x54, done), lui(A0, hi16(TAMER_ENTITY)),
            jal(TICK_ANIM), addiu(A0, A0, lo16(TAMER_ENTITY)),
            jal(CAMERA), NOP,
            br(4, ZERO, ZERO, c + 0x6C, loop), NOP,                # b loop
            i_(0x23, SP, RA, 0x18), i_(0x23, SP, S0, 0x14),        # done: (ra not used in its load delay)
            JR_RA, addiu(SP, SP, 0x20),
        ]
        assert c + 4 * len(code) == done + 0x10
        body = pre + code
        assert FN + 4 * len(body) <= FN_END
        out[value] = pack(body)
    stock = pack(fn[:len(next(iter(out.values()))) // 4])
    return stock, out, pack(table), pack(new_table)


def walk_speed_lines(exe=EXE):
    """[[feature]] + [[option]] + [[patch]] TOML lines for "walk-speed"."""
    with open(exe, "rb") as f:
        stock, new, table, new_table = build(f.read())
    out = [
        "[[feature]]",
        'id = "walk-speed"',
        'name = "Run Speed"',
        'description = "Your tamer runs faster in the field. Collisions, map '
        'exits, doors and talking to Digimon work as normal, and walking '
        '(hold R1), menus and cutscenes stay at normal speed. Your partner '
        'keeps its own pace, so on a long run it falls well behind and takes '
        'a few seconds to catch up after you stop. A map exit puts it back '
        'beside you."',
        'group = "Field"',
        "default_enabled = false",
        "",
        "[[option]]",
        'feature = "walk-speed"',
        'id = "speed"',
        'label = "Run speed"',
        'description = "1.5x adds a step every other frame, 2x every frame."',
        'group = "Field"',
        'type = "choice"',
        'default = "1.5x"',
        "",
    ]
    for value, label, _alt in SPEEDS:
        out += ["[[option.choice]]", 'value = "%s"' % value, 'label = "%s"' % label, ""]
    out += [
        "# tamerTickOverworld's jump table: case labels -> the callees themselves",
        "# (and tail-call stubs for states 1, 9, 13), for the jalr dispatch below.",
        "[[patch]]",
        'feature = "walk-speed"',
        'target = "main_exe"',
        "address = 0x%08X" % TABLE,
        'expected = "%s"' % hexs(table),
        'replace = "%s"' % hexs(new_table),
        "",
    ]
    for value, _label, _alt in SPEEDS:
        out += [
            "# tamerTickOverworld: jalr dispatch + extra run step (%s)." % value,
            "[[patch]]",
            'feature = "walk-speed"',
            'target = "main_exe"',
            "address = 0x%08X" % FN,
            'expected = "%s"' % hexs(stock),
            'replace = "%s"' % hexs(new[value]),
            'when = { speed = "%s" }' % value,
            "",
        ]
    return out


def main():
    with open(EXE, "rb") as f:
        van = f.read()
    stock, new, table, new_table = build(van)
    # check: the rewritten table sends every state where the stock one did
    t = struct.unpack("<%dI" % CASES, new_table)
    assert t[5] == 0x800AA194 and t[11] == 0x800E4E18 and t[12] == 0x800545C8
    assert t[2] == t[3] == t[4] == t[10] and t[9] == t[13] and len(set(t)) == 17
    lines = walk_speed_lines()
    if os.path.exists(EXE_DW15):
        with open(EXE_DW15, "rb") as f:
            dw = f.read()
        o = lambda a: a - LOAD_ADDR + HDR
        same = all(van[o(a):o(a) + n] == dw[o(a):o(a) + n] for a, n in (
            (FN, FN_END - FN), (TABLE, 4 * CASES), (0x80091CA8, 0x6C)))
        print("Digimon World 1.5 exe (tamerTickOverworld, jump table, VSync): %s"
              % ("identical (compatible)" if same else "DIFFERENT"))
    args = sys.argv[1:]
    if "--out" not in args or args.index("--out") + 1 >= len(args):
        print("verified; nothing written (pass --out <manifest.toml> to write)")
        return 0
    dest = args[args.index("--out") + 1]
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    head = [
        "# WIP walk-speed package. Generated by tools/gen_walk_speed.py.",
        "",
        "format_version = 5",
        'id = "dw.wip.walk"',
        'version = "1.0.0"',
        'name = "Run Speed (WIP)"',
        'author = "DigimonWorldRecomp"',
        'description = "Faster running in the field."',
        'resolver = "declarative"',
        'save_compatibility = "shared"',
        "",
        "[[target]]",
        'game_id = "SLUS-01032"',
        "",
    ]
    with open(dest, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(head + lines))
    print("wrote %s" % os.path.normpath(dest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
