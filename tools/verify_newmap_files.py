#!/usr/bin/env python3
"""Does the file-by-name hook (dw_newmaps_files.c on lookupFileTable) leave stock behaviour alone?

Regression half: boot the stock game with the glade ticked (so the hook is live) and
check that map 2 (MAYO03) loads and plays, that row 239 loads the glade (MGEN17, its
TFS resident), and that talking to the glade boss starts the battle (DG.SCN read
through the hooked lookup). Run it before the hook exists and after: both must pass.

    py -3.12 tools/verify_newmap_files.py --seed <card dir> [--port N] [--build DIR]

Writes build/mods/state.toml. Verdicts are guest-memory reads.
"""
import argparse
import json
import os
import shutil
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dwgame import Game  # noqa: E402

CURRENT_SCREEN, GAME_STATE, IS_SCRIPT_PAUSED = 0x80134DA8, 0x80134F0A, 0x80134FF4
GENERAL_BUFFER = 0x80010000
TAMER = 0x8015576C
BATTLE = (1, 2, 3)
UP, RIGHT, DOWN, LEFT = 0x0010, 0x0020, 0x0040, 0x0080
STATE = ('format_version = 2\n\n[[package]]\nid = "dw.newmaps"\nversion = "1.0.0"\n\n'
         '[[feature]]\npackage_id = "dw.newmaps"\nid = "glade"\nenabled = true\n')


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", default=os.path.join(root, "build-debug"))
    ap.add_argument("--port", type=int, default=4440)
    ap.add_argument("--seed", default=os.path.join(root, "saves"))
    a = ap.parse_args()
    out = os.path.join(a.build, "newmap_files")
    cards = os.path.join(out, "cards")
    os.makedirs(out, exist_ok=True)
    spec = json.load(open(os.path.join(a.build, "newmap", "spec.json")))
    shutil.rmtree(cards, ignore_errors=True)
    os.makedirs(cards)
    for name in ("card1.mcd", "card2.mcd"):
        if os.path.exists(os.path.join(a.seed, name)):
            shutil.copy(os.path.join(a.seed, name), cards)
    if not os.path.exists(os.path.join(cards, "card1.mcd")):
        print("No memory card to seed from at %s" % a.seed, file=sys.stderr)
        return 2
    with open(os.path.join(a.build, "mods", "state.toml"), "w") as fh:
        fh.write(STATE)

    fails = []

    def check(cond, msg):
        print(("PASS " if cond else "FAIL ") + msg, flush=True)
        if not cond:
            fails.append(msg)

    g = Game.launch(exe=os.path.join(a.build, "Digimon_World_Recompiled.exe"), port=a.port,
                    memcard_dir=cards, extra_args=["--headless-opengl"],
                    log_path=os.path.join(out, "run.log"))
    i32 = lambda addr: struct.unpack("<i", g.d.read(addr, 4))[0]
    gstate = lambda: struct.unpack("<b", g.d.read(GAME_STATE, 1))[0]
    screen = lambda: g.d.read(CURRENT_SCREEN, 1)[0]

    def wait_for(pred, timeout=30.0):
        end = time.time() + timeout
        while time.time() < end:
            if pred():
                return True
            time.sleep(0.2)
        return False

    def pos(entity):
        ptr = struct.unpack("<I", g.d.read(entity + 4, 4))[0]
        if not 0x80000000 <= ptr < 0x80200000:
            return None
        return struct.unpack("<3i", g.d.read(ptr + 120, 12))

    def enter():                                       # verify_newmap.py's enter(): title -> CONTINUE
        free = lambda: (g.partner_type and g.tamer_state == 0 and gstate() == 0
                        and screen() != 238 and i32(IS_SCRIPT_PAUSED) == 1)
        for _ in range(12):
            g.route("6:start,150:none")
        g.advance(120)
        g.route("6:down,90:none")
        for i in range(600):
            g.route("6:cross,30:none")
            if free() and i % 5 == 0:
                g.advance(120)
                if free():
                    return
        raise RuntimeError("never reached the world from CONTINUE GAME")

    def plays(row, what):
        f0 = g.d.frame()
        time.sleep(2)
        check(screen() == row and gstate() == 0 and g.d.frame() > f0 + 60, "%s: map %d loaded and plays" % (what, row))

    # walking and talking, as in verify_newmap.py
    def pad(mask):
        g.d.cmd("set_input", layer="host", buttons="0x%04X" % (0xFFFF & ~mask))

    def walk(mask, seconds):
        g.route("%d:%s,4:none" % (seconds * 60, {UP: "up", RIGHT: "right", DOWN: "down", LEFT: "left"}[mask]), poll_s=0.02)

    def calibrate():
        vec = {}
        for d in (RIGHT, LEFT, UP, DOWN):
            p = pos(TAMER); walk(d, 0.35); time.sleep(0.15); q = pos(TAMER)
            vec[d] = (q[0] - p[0], q[2] - p[2])
        return vec

    def near(x, z, r=120):
        p = pos(TAMER)
        return p is not None and abs(p[0] - x) < r and abs(p[2] - z) < r

    def go(target, until, vec, limit=90):
        for _ in range(limit):
            if until():
                return True
            p = pos(TAMER)
            if p is None or g.tamer_state != 0 or gstate() != 0:
                g.route("4:cross,20:none")
                time.sleep(0.3)
                continue
            want = (target[0] - p[0], target[1] - p[2])
            order = sorted(vec, key=lambda d: -(vec[d][0] * want[0] + vec[d][1] * want[1]))[:3]
            for d in order:
                need = 0.35 * (vec[d][0] * want[0] + vec[d][1] * want[1]) / max(1, vec[d][0] ** 2 + vec[d][1] ** 2)
                walk(d, min(0.3, max(0.1, need)) if d == order[0] else 0.3)
                q = pos(TAMER)
                if q is None or until() or abs(q[0] - p[0]) + abs(q[2] - p[2]) > 20:
                    break
        return until()

    def talk_to_boss(vec):
        x, z = spec["boss"]["at"][0], spec["boss"]["at"][2]
        centre = tuple(spec["meadow"][1])
        go(centre, lambda: near(*centre), vec, limit=60)
        check(go((x, z - 700), lambda: near(x, z - 700, r=200), vec, limit=40), "up the path, below the boss %s" % (pos(TAMER),))
        north = max(vec, key=lambda d: vec[d][1])
        pad(north)
        last = None
        for _ in range(30):
            time.sleep(0.15)
            if pos(TAMER) == last:
                break
            last = pos(TAMER)
        pad(0)
        time.sleep(3)
        g.route("6:cross,20:none")
        time.sleep(1)
        check(gstate() == 0 and i32(IS_SCRIPT_PAUSED) != 1, "confirm opens the boss's dialogue")
        end = time.time() + 30
        while time.time() < end and gstate() not in BATTLE:
            g.route("4:cross,20:none")
        check(gstate() in BATTLE, "the battle starts")
        check(wait_for(lambda: gstate() == 2, 30) and wait_for(lambda: gstate() == 1, 30), "the fight is on")

    try:
        enter()
        g.settle(timeout_frames=1800)
        check(g.map_record(239)["file"] == "MGEN17", "row 239 holds MGEN17 (glade ticked)")
        g.warp(2, 0)
        check(g.map_record(2)["file"] == "MAYO03", "row 2 is MAYO03")
        plays(2, "stock map")
        g.warp(239, 0)
        plays(239, "glade")
        tfs = open(os.path.join(a.build, "mods", "bundled", "dw.newmaps", "1.0.0", "assets", "glade.tfs"), "rb").read()
        check(g.d.read(GENERAL_BUFFER, 4096) == tfs[:4096], "the glade's TFS is resident")
        talk_to_boss(calibrate())
    finally:
        g.shutdown()
    print("RESULT newmap_files: %s" % ("FAIL %s" % fails if fails else "PASS"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
