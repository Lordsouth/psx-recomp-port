#!/usr/bin/env python3
"""Does a new map on free row 239 behave like a real map? (gen_newmap_spike.py)

    run1  fresh sandbox card: walk through Coela Point's east exit onto 239,
          fight the Piddomon there, save on 239 (Save Anywhere), walk back out
    run2  relaunch on run1's card: Continue lands on 239 at the saved spot,
          and the exit still works

    py -3.12 tools/gen_newmap_spike.py
    py -3.12 tools/verify_newmap_spike.py run1 --seed <card dir>
    py -3.12 tools/verify_newmap_spike.py run2

The seed is any card dir whose slot 1 holds a Digimon World file; a never-saved
New Game file plays Jijimon's intro first, which this drives through. It writes
build/mods/state.toml (spike + dw.vanilla.qol Save Anywhere). Verdicts are guest
memory and memory-card reads; screenshots are artifacts only.
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

UP, RIGHT, DOWN, LEFT = 0x0010, 0x0020, 0x0040, 0x0080
TAMER, NPC0 = 0x8015576C, 0x8012F344 + 8          # ENTITY_TABLE[2]: first map NPC
CURRENT_SCREEN, GAME_STATE, IS_SCRIPT_PAUSED = 0x80134DA8, 0x80134F0A, 0x80134FF4
TRI_STATE, MENU_CURSOR, CURRENT_MENU, YES_NO = 0x80134D30, 0x80123E8C, 0x8013504C, 0x801317D8
STATE = ('format_version = 2\n\n[[package]]\nid = "dw.newmaps.spike"\nversion = "1.0.0"\n\n'
         '[[package]]\nid = "dw.vanilla.qol"\nversion = "1.0.0"\n\n'
         '[[feature]]\npackage_id = "dw.newmaps.spike"\nid = "spike"\nenabled = true\n\n'
         '[[feature]]\npackage_id = "dw.vanilla.qol"\nid = "save-anywhere"\nenabled = true\n')


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("run1", "run2"))
    ap.add_argument("--build", default=os.path.join(root, "build-debug"))
    ap.add_argument("--port", type=int, default=4430)
    ap.add_argument("--seed", default=os.path.join(root, "saves"))
    a = ap.parse_args()
    out = os.path.join(a.build, "newmap_spike")
    cards = os.path.join(out, "cards")
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(a.build, "mods", "state.toml"), "w") as fh:
        fh.write(STATE)
    if a.mode == "run1":
        shutil.rmtree(cards, ignore_errors=True)
        os.makedirs(cards)
        for name in ("card1.mcd", "card2.mcd"):
            if os.path.exists(os.path.join(a.seed, name)):
                shutil.copy(os.path.join(a.seed, name), cards)
        if not os.path.exists(os.path.join(cards, "card1.mcd")):
            print("No memory card to seed from at %s" % a.seed, file=sys.stderr)
            return 2

    fails = []

    def check(cond, msg):
        print(("PASS " if cond else "FAIL ") + msg, flush=True)
        if not cond:
            fails.append(msg)

    g = Game.launch(exe=os.path.join(a.build, "Digimon_World_Recompiled.exe"), port=a.port,
                    memcard_dir=cards, extra_args=["--headless-opengl"],
                    log_path=os.path.join(out, a.mode + ".log"))
    i32 = lambda addr: struct.unpack("<i", g.d.read(addr, 4))[0]
    gstate = lambda: struct.unpack("<b", g.d.read(GAME_STATE, 1))[0]
    screen = lambda: g.d.read(CURRENT_SCREEN, 1)[0]
    shot = lambda n: g.artifact_shot(os.path.join(out, "%s_%s.png" % (a.mode, n)))

    def pad(mask):
        g.d.cmd("set_input", layer="host", buttons="0x%04X" % (0xFFFF & ~mask))

    def pos(entity):
        ptr = struct.unpack("<I", g.d.read(entity + 4, 4))[0]
        if not 0x80000000 <= ptr < 0x80200000:
            return None
        return struct.unpack("<3i", g.d.read(ptr + 120, 12))

    def walk(mask, seconds):
        pad(mask)
        time.sleep(seconds)
        pad(0)

    def wait_for(pred, timeout=30.0):
        end = time.time() + timeout
        while time.time() < end:
            if pred():
                return True
            time.sleep(0.2)
        return False

    def calibrate():
        """Pad direction -> world (dx, dz) under this screen's camera."""
        vec = {}
        for d in (RIGHT, LEFT, UP, DOWN):
            p = pos(TAMER); walk(d, 0.35); time.sleep(0.15); q = pos(TAMER)
            vec[d] = (q[0] - p[0], q[2] - p[2])
        return vec

    def go(target, until, limit=90):
        """Greedy walk toward world (x, z); tries the next-best direction at a wall."""
        vec = calibrate()
        for _ in range(limit):
            if until():
                return True
            p = pos(TAMER)
            if p is None or g.tamer_state != 0 or gstate() != 0:
                time.sleep(0.3)
                continue
            want = (target[0] - p[0], target[1] - p[2])
            for d in sorted(vec, key=lambda d: -(vec[d][0] * want[0] + vec[d][1] * want[1]))[:3]:
                walk(d, 0.3)
                q = pos(TAMER)
                if q is None or until() or abs(q[0] - p[0]) + abs(q[2] - p[2]) > 20:
                    break
        return until()

    def enter():
        """Title -> CONTINUE. DOWN waits for the menu to take input: an early DOWN
        is dropped and the crosses then start a NEW GAME."""
        for _ in range(12):
            g.route("6:start,150:none")
        g.advance(120)
        g.route("6:down,90:none")
        # The intro gives a partner while still on the opening screen (238) with
        # its script running, so "in control" also needs the script idle there
        # (IS_SCRIPT_PAUSED == 1, the field menu's own test) for 2 s straight.
        free = lambda: (g.partner_type and g.tamer_state == 0 and gstate() == 0
                        and screen() != 238 and i32(IS_SCRIPT_PAUSED) == 1)
        for i in range(600):
            g.route("6:cross,30:none")
            if free() and i % 5 == 0:
                g.advance(120)
                if free():
                    return
        raise RuntimeError("never reached the world from CONTINUE GAME")

    def near(x, z, r=80):
        p = pos(TAMER)
        return p is not None and abs(p[0] - x) < r and abs(p[2] - z) < r

    def walk_out():
        # Two straight legs (greedy steering corners badly under the 45-degree
        # camera): across the spawn room to tile (54,53), then down the dead end.
        go((450, -350), lambda: near(450, -350) or screen() == 1, limit=40)
        ok = go((450, -1450), lambda: screen() == 1 or g.tamer_state == 5)
        check(ok and wait_for(lambda: screen() == 1 and g.tamer_state == 0),
              "walked onto MGEN17's trigger strip back to Coela Point")
        p = pos(TAMER)
        shot("out")
        check(p is not None and abs(p[0] - 1858) < 400 and abs(p[2] + 1142) < 400,
              "arrived at Coela Point's east exit spawn %s" % (p,))

    try:
        enter()
        g.settle(timeout_frames=1800)
        check(g.map_record(239)["file"] == "MGEN17", "row 239 holds MGEN17 (is the spike enabled?)")
        if a.mode == "run1":
            g.warp(1, 2)
            check(screen() == 1, "standing at Coela Point's east exit")
            go((2150, -1150), lambda: screen() == 239 or g.tamer_state == 5)
            check(wait_for(lambda: screen() == 239 and g.tamer_state == 0),
                  "walked through the east exit onto map 239 (no cutscene took over)")
            g.advance(20)
            p = pos(TAMER)
            check(p is not None and (p[0], p[2]) == (1474, -446), "arrived at MGEN17 spawn 0")
            shot("in")

            npc = struct.unpack("<I", g.d.read(NPC0, 4))[0]
            check(0x80000000 <= npc < 0x80200000, "Piddomon spawned in NPC slot 0")
            e = pos(npc)
            go((e[0], e[2]), lambda: g.tamer_state != 0 or gstate() != 0, limit=30)
            seen, end = set(), time.time() + 120
            while time.time() < end:
                s = gstate()
                if s == 3 and 3 not in seen:
                    shot("battle")
                seen.add(s)
                if s == 0 and 3 in seen and g.tamer_state == 0:
                    break
                g.route("4:cross,20:none")
                time.sleep(0.3)
            check(3 in seen, "battle started on map 239 (game states %s)" % sorted(seen))
            check(gstate() == 0 and g.tamer_state == 0 and screen() == 239,
                  "battle over, player in control, still on map 239")

            check((g.para_condition & 1) == 0, "partner not sleepy, so Save Anywhere is offered")
            g.route("6:triangle,40:none")
            check(wait_for(lambda: i32(TRI_STATE) == -1, 10), "field menu open")
            g.route("6:left,20:none")
            check(struct.unpack("<h", g.d.read(MENU_CURSOR, 2))[0] == 6, "cursor on Save")
            g.route("6:cross,30:none")
            check(wait_for(lambda: i32(CURRENT_MENU) == 0x45, 10), "save prompt open")
            if g.d.read(YES_NO, 1)[0] != 0:
                g.route("6:up,20:none")
            g.route("6:cross,30:none")
            wait_for(lambda: i32(CURRENT_MENU) in (-1, 3, 0x42, 0x46), 20)
            check(i32(CURRENT_MENU) == -1, "save finished without a card error")
            wait_for(lambda: i32(TRI_STATE) == -1, 10)
            g.route("6:triangle,40:none")
            wait_for(lambda: g.tamer_state == 0, 10)
            card = open(os.path.join(cards, "card1.mcd"), "rb").read()
            blk = next(i for i in range(1, 16) if card[i * 128 + 10:i * 128 + 26] == b"BASLUS-01032DMR0")
            s0 = card[blk * 0x2000 + 0x200:]
            main_state, px, py, pz = struct.unpack_from("<4i", s0)
            check(main_state == 1 and s0[0x49A] == 239 and s0[0x49C] == 0,
                  "card save: map %d exit %d at %s" % (s0[0x49A], s0[0x49C], (px, py, pz)))
            with open(os.path.join(out, "saved_pos.json"), "w") as fh:
                json.dump([px, py, pz], fh)
            walk_out()
        else:
            saved = json.load(open(os.path.join(out, "saved_pos.json")))
            p = pos(TAMER)
            shot("continued")
            check(screen() == 239, "Continue lands on map 239")
            check(p is not None and abs(p[0] - saved[0]) < 60 and abs(p[2] - saved[2]) < 60,
                  "at the saved position %s (now %s)" % (saved, p))
            f0 = g.d.frame()
            time.sleep(2)
            check(g.d.frame() > f0 + 60, "game running on 239 after the load")
            walk_out()
    finally:
        g.shutdown()
    print("RESULT newmap spike %s: %s" % (a.mode, "FAIL %s" % fails if fails else "PASS"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
