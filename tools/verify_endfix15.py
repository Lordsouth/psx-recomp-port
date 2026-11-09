#!/usr/bin/env python3
"""Battle End Freeze Fix on Digimon World 1.5, in the game (docs/GAME.md 12.1).

On 1.5 the fix is part of dw15, so there is no off case here: verify_newmap.py
fight --brains N --no-end-fix shows the stock freeze. This wins a real fight
against a wild Digimon on DGHA01 with the partner's Brains at N, so the win's
gain crosses a threshold, and checks the end box finishes on its MP bonus
message and hands control back.

    cmake --build build-debug --target psx-runtime
    py -3.12 tools/verify_endfix15.py 799 --seed <card dir>

The seed is a stock save; it is copied to the card folder 1.5 reads.
"""
import argparse, os, shutil, struct, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dwgame import Game

GAME_STATE, CURRENT_SCREEN = 0x80134F0A, 0x80134DA8
TAMER, NPC_TABLE = 0x8015576C, 0x8012F344 + 8
NPCS, STRIDE = 0x80155828, 0x68
PARTNER_STATS, PARTNER_MAX = 0x801557E0, 0x801557F0
START_BRAINS, END_BUFFER, END_LINES = 0x8013D610 + 10, 0x80073EA0, 0x801350B0
MONEY = 0x80134EB8
BATTLE = (1, 2, 3)                                   # 3 loading, 2 intro, 1 fighting
WANT = {700: 5, 800: 10, 900: 15, 999: 20}           # the threshold crossed -> the MP cost cut, in percent
STATE = ('format_version = 2\n\n[[package]]\nid = "dw.1_5"\nversion = "1.0.0"\n\n'
         '[[feature]]\npackage_id = "dw.1_5"\nid = "dw15"\nenabled = true\n')


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("brains", type=int, choices=sorted(t - 1 for t in WANT), help="the partner's Brains going in")
    ap.add_argument("--build", default=os.path.join(root, "build-debug"))
    ap.add_argument("--port", type=int, default=4431)
    ap.add_argument("--seed", default=os.path.join(root, "saves"))
    a = ap.parse_args()
    out = os.path.join(a.build, "endfix15_%d" % a.brains)
    cards = os.path.join(out, "cards")
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(os.path.join(cards, "dw15"))
    for name in ("card1.mcd", "card2.mcd"):
        if os.path.exists(os.path.join(a.seed, name)):
            shutil.copy(os.path.join(a.seed, name), os.path.join(cards, "dw15", name))
    if not os.path.exists(os.path.join(cards, "dw15", "card1.mcd")):
        print("No memory card to seed from at %s" % a.seed, file=sys.stderr)
        return 2
    with open(os.path.join(a.build, "mods", "state.toml"), "w") as fh:
        fh.write(STATE)

    fails = []

    def check(cond, msg):
        print(("PASS " if cond else "FAIL ") + msg, flush=True)
        if not cond:
            fails.append(msg)

    g = Game.launch(exe=os.path.join(a.build, "Digimon_World_Recompiled.exe"), port=a.port, memcard_dir=cards,
                    extra_args=["--headless-opengl"], log_path=os.path.join(out, "game.log"))
    gstate = lambda: struct.unpack("<b", g.d.read(GAME_STATE, 1))[0]
    h = lambda addr: struct.unpack("<h", g.d.read(addr, 2))[0]
    i32 = lambda addr: struct.unpack("<i", g.d.read(addr, 4))[0]
    shot = lambda n: g.artifact_shot(os.path.join(out, n + ".png"))

    def wait_for(cond, secs):
        end = time.time() + secs
        while time.time() < end:
            if cond():
                return True
            g.route("6:none")
        return cond()

    def location(entity):
        ptr = struct.unpack("<I", g.d.read(entity + 4, 4))[0]
        return ptr + 120 if 0x80000000 <= ptr < 0x80200000 else None

    def on_map():
        found = []
        for i in range(8):
            if g.d.read(NPCS + i * STRIDE + 0x34, 1)[0] == 1:
                loc = location(struct.unpack("<I", g.d.read(NPC_TABLE + i * 4, 4))[0])
                if loc:
                    found.append((i, struct.unpack("<3i", g.d.read(loc, 12))))
        return found

    try:
        free = lambda: (g.partner_type and g.tamer_state == 0 and gstate() == 0
                        and g.d.read(CURRENT_SCREEN, 1)[0] != 238 and i32(0x80134FF4) == 1)
        for _ in range(12):
            g.route("6:start,150:none")
        g.advance(120)
        g.route("6:down,90:none")
        for i in range(600):                           # CONTINUE, then through Jijimon's intro
            g.route("6:cross,30:none")
            if free() and i % 5 == 0:
                g.advance(120)
                if free():
                    break
        check(bool(free()), "in the world")
        row = next(r for r in range(256) if g.map_record(r)["file"] == "DGHA01")
        g.warp(row, 0)
        g.advance(120)
        npcs = on_map()
        check(bool(npcs), "a wild Digimon is on DGHA01 (row %d)" % row)
        if fails:
            return 1
        g.d.write_bytes(struct.pack("<4h", 999, 999, 999, a.brains), PARTNER_STATS)
        g.d.write_bytes(struct.pack("<4h", 9999, 9999, 9999, 9999), PARTNER_MAX)
        for i, _ in npcs:
            g.d.write_bytes(struct.pack("<h", 999), NPCS + i * STRIDE + 0x38 + 6)       # a sure Brains gain
        target = npcs[0][1]
        end = time.time() + 60
        while time.time() < end and gstate() not in BATTLE:
            loc = location(TAMER)
            if loc:
                g.d.write_bytes(struct.pack("<3i", target[0] + 100, target[1], target[2] + 100), loc)
            g.route("6:cross,20:none")
            target = (on_map() or npcs)[0][1]
        # A brief 1 can show before the loading begins, and the last battle's stats stay in memory.
        check(wait_for(lambda: gstate() == 2, 30) and wait_for(lambda: gstate() == 1, 30), "the fight is on")
        check(h(START_BRAINS) == a.brains, "the battle started on Brains %d" % h(START_BRAINS))
        shot("battle")
        bits, box, in_battle = i32(MONEY), None, True
        end = time.time() + 240
        while time.time() < end and not (not in_battle and gstate() == 0 and g.tamer_state == 0):
            in_battle = gstate() in BATTLE
            if gstate() == 1:
                for i in range(8):                     # only down to 1: a write after the killing hit would revive it
                    if h(NPCS + i * STRIDE + 0x38 + 0x14) > 1:
                        g.d.write_bytes(struct.pack("<h", 1), NPCS + i * STRIDE + 0x38 + 0x14)
                g.d.write_bytes(struct.pack("<h", 9999), PARTNER_MAX + 4)
            lines, rows = struct.unpack("<hh", g.d.read(END_LINES, 4))
            if box is None and lines and rows == lines:
                box = g.d.read(END_BUFFER, 200).split(b"\0")[0]
                shot("end_box")
                print("MEASURE end box, %d rows: %r" % (rows, box), flush=True)
            g.route("4:square,20:none" if gstate() == 1 else "4:cross,20:none")
        shot("after")
        check(not in_battle and gstate() == 0 and g.tamer_state == 0, "the battle ended and the player has control")
        check(i32(MONEY) > bits, "the bits were paid")
        text = b"#C1MP Cost Bonus!#Rreduced by %d pct!#W" % WANT[a.brains + 1]
        check(box is not None and text in box, "the end box finished on its MP bonus message")
    finally:
        g.shutdown()
        print("RESULT endfix15 %d: %s" % (a.brains, "FAIL %s" % fails if fails else "PASS"), flush=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
