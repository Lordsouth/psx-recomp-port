#!/usr/bin/env python3
"""Savestates and Rewind on Digimon World 1.5 with every mod ticked, in the game.

All five 1.5 editions run (roster-15, glade-15 and the three in dw.1_5). Standing
in the Hidden Glade, this saves a state, changes things, and loads it back; then
it lets Rewind collect snapshots, changes things again, opens Rewind with
Select + R3 and jumps back. After each, the edition-made data must still be
there (the glade on row 239, 1.5's drop table at 100, the roster's rows and
evolution wiring) and the game must run on.

    py -3.12 tools/verify_dw15_states.py --seed <card dir>
"""
import argparse
import os
import shutil
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dwgame import Game  # noqa: E402
from dwdbg import pad_word  # noqa: E402

GAME_STATE, CURRENT_SCREEN, MONEY = 0x80134F0A, 0x80134DA8, 0x80134EB8
DROP_3 = 0x801F4300 + 3 * 0x34 + 0x22                    # Agumon's drop chance, 1.5's table
RAISE, EVO_PATHS = 0x801FA01C, 0x8008CF20
FEATURES = [("dw.1_5", "dw15", ""), ("dw.vanilla.roster", "roster", ""), ("dw.newmaps", "glade", ""),
            ("dw.vanilla.trainer", "guaranteed-drops", ""), ("dw.vanilla.gameplay", "evolution-unbound", 'values = { walls = "open" }'),
            ("dw.vanilla.evolution", "native-evolution", ""), ("dw.vanilla.qol", "save-anywhere", ""),
            ("psx.feature.rewind", "rewind", "")]


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", default=os.path.join(root, "build-debug"))
    ap.add_argument("--port", type=int, default=4434)
    ap.add_argument("--seed", default=os.path.join(root, "saves"))
    a = ap.parse_args()
    out = os.path.join(a.build, "dw15_states")
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(os.path.join(out, "cards", "dw15"))
    for n in ("card1.mcd", "card2.mcd"):
        if os.path.exists(os.path.join(a.seed, n)):
            shutil.copy(os.path.join(a.seed, n), os.path.join(out, "cards", "dw15", n))
    with open(os.path.join(a.build, "mods", "state.toml"), "w") as fh:
        fh.write("format_version = 2\n\n" + "".join(
            '[[feature]]\npackage_id = "%s"\nid = "%s"\nenabled = true\n%s\n' % f for f in FEATURES))

    fails = []

    def check(cond, msg):
        print(("PASS " if cond else "FAIL ") + msg, flush=True)
        if not cond:
            fails.append(msg)

    g = Game.launch(exe=os.path.join(a.build, "Digimon_World_Recompiled.exe"), port=a.port,
                    memcard_dir=os.path.join(out, "cards"), extra_args=["--headless-opengl"],
                    log_path=os.path.join(out, "game.log"))
    i32 = lambda addr: struct.unpack("<i", g.d.read(addr, 4))[0]
    gstate = lambda: struct.unpack("<b", g.d.read(GAME_STATE, 1))[0]
    screen = lambda: g.d.read(CURRENT_SCREEN, 1)[0]

    def editions_intact(when):
        check(g.map_record(239)["file"] == "MGEN17", "%s: the glade is on row 239" % when)
        check(g.d.read(DROP_3, 1)[0] == 100, "%s: 1.5's drop table reads 100" % when)
        check(g.d.read(RAISE + 111 * 28, 28) == g.d.read(RAISE + 32 * 28, 28), "%s: the roster's raise rows" % when)
        to = struct.unpack("<7h", g.d.read(EVO_PATHS + 6 * 24 + 10, 14))
        check(64 in to, "%s: Airdramon still evolves into Gigadramon %s" % (when, to))
        f0 = g.d.frame()
        time.sleep(2)
        check(g.d.frame() > f0 + 60, "%s: the game runs" % when)

    try:
        free = lambda: (g.partner_type and g.tamer_state == 0 and gstate() == 0 and screen() != 238
                        and i32(0x80134FF4) == 1)
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
        g.warp(239, 0)                                 # into the Hidden Glade, on 1.5
        g.settle()
        check(screen() == 239, "in the Hidden Glade")
        editions_intact("before")
        # Evolution Unbound, ticked plainly with Stat requirements on Open, runs its 1.5 edition:
        # Flamedramon's walls of 100 (docs/INTERNALS.md) read 50 in 1.5's live table.
        flame = next(t for t in range(384) if g.d.read(0x801F4300 + t * 0x34, 12).split(b"\0")[0] == b"Flamedramon")
        walls = struct.unpack("<6h", g.d.read(0x80089020 + flame * 28 + 2, 12))
        check(set(w for w in walls if w > 0) == {50}, "Evolution Unbound on Open: Flamedramon's walls read %s" % (walls,))

        money = i32(MONEY)
        g.savestate("save", 1)
        g.d.write_bytes(struct.pack("<i", money + 1234), MONEY)
        g.warp(2, 0)
        g.settle()
        g.savestate("load", 1)
        g.settle()
        check(i32(MONEY) == money and screen() == 239,
              "savestate: back in the glade with %d bits (now %d, map %d)" % (money, i32(MONEY), screen()))
        editions_intact("after the savestate")

        # Headless, Rewind captures and reads its hotkey and controls only through the
        # debug host pad layer (main.cpp's frame loop), the stand-in for a physical pad.
        def host(*buttons, frames=6):
            g.d.cmd("set_input", layer="host", buttons="0x%04X" % pad_word(*buttons))
            g.advance(frames) if not g.d.cmd("rewind_status").get("open") else time.sleep(frames / 60)
            g.d.cmd("set_input", layer="host", buttons="0xFFFF")
            g.advance(4) if not g.d.cmd("rewind_status").get("open") else time.sleep(0.1)

        g.d.cmd("set_input", layer="host", buttons="0xFFFF")
        end = time.time() + 40
        while time.time() < end and g.d.cmd("rewind_status").get("snaps", 0) < 20:
            g.advance(60)
        st = g.d.cmd("rewind_status")
        check(st.get("enabled") and st.get("snaps", 0) >= 20, "Rewind collected %s snapshots" % st.get("snaps"))
        loads = st.get("loads", 0)
        g.d.write_bytes(struct.pack("<i", money + 4321), MONEY)
        g.advance(30)
        host("select", "r3")
        check(g.d.cmd("rewind_status").get("open"), "Rewind opened with Select + R3")
        for _ in range(10):                            # 10 snapshots, 150 frames: before the change
            host("left")
        host("cross")
        g.d.cmd("clear_input")
        g.settle()
        st = g.d.cmd("rewind_status")
        check(st.get("loads", 0) == loads + 1 and st.get("load_fails", 0) == 0 and not st.get("open"),
              "Rewind jumped back (loads %d -> %d, fails %d)" % (loads, st.get("loads", 0), st.get("load_fails", 0)))
        check(i32(MONEY) == money, "Rewind: the bits are back to %d (now %d)" % (money, i32(MONEY)))
        editions_intact("after Rewind")
    finally:
        g.shutdown()
        print("RESULT dw15 states: %s" % ("FAIL %s" % fails if fails else "PASS"), flush=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
