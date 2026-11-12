#!/usr/bin/env python3
"""Does the file-by-name hook (dw_newmaps_files.c on lookupFileTable) leave stock behaviour alone?

Regression half: boot the stock game with the glade ticked (the file hook is not in that
plan: the glade serves no new names) and check that map 2 (MAYO03) loads and plays, that row 239 loads the glade (MGEN17, its
TFS resident), and that talking to the glade boss starts the battle (DG.SCN is read by
sector through CdSearchFile, not through the hooked lookup, so this guards the table rows
and the glade's own plan, not the hook). Run it before the hook exists and after: both must pass.

    py -3.12 tools/verify_newmap_files.py --seed <card dir> [--port N] [--build DIR]

New-name half (--proof): with tools/gen_newmap_proof.py's package installed (local only) and its feature
ticked, row 240 names FACT0, its files come from MMM.DAT and its script from DG row 223: the TFS and
collision are resident after a warp, a savestate saved on 240 and loaded elsewhere returns there with the
TFS resident, standing by the boss and pressing confirm starts a battle (DG row 223 ran), and with an
empty state.toml row 240 is empty. At 21:9 (widescreen ticked) the arrival on row 240 must match the glade's on
row 239 (same art) in both slivers: the sliver reads a served TFS by sector (dw_ws_sliver.c), so black or missing
edges fail it. With the glade AND the proof ticked together the plan must be accepted (each map package
has its own plugin id; the framework refuses two features claiming one) and both rows must exist. The savestate check proves a state taken on 240 restores onto 240, not a re-serve by name. Needs a game build that includes the two FACT0 rows of dw_newmaps_files.inc.

    py -3.12 tools/verify_newmap_files.py --seed <card dir> --proof [--port N]

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


PROOF_STATE = ('format_version = 2\n\n[[package]]\nid = "dw.newmaps.proof"\nversion = "1.0.0"\n\n'
               '[[feature]]\npackage_id = "dw.newmaps.proof"\nid = "proof"\nenabled = true\n')
PROOF_BODY = ('\n[[package]]\nid = "dw.newmaps.proof"\nversion = "1.0.0"\n\n'
              '[[feature]]\npackage_id = "dw.newmaps.proof"\nid = "proof"\nenabled = true\n')
MAP_COLLISION, SLOT = 0x801AF398, 7
WIDE = ('\n[[package]]\nid = "psx.enhancement.widescreen"\nversion = "1.0.0"\n\n'
        '[[feature]]\npackage_id = "psx.enhancement.widescreen"\nid = "widescreen"\nenabled = true\n'
        '[feature.values]\naspect = "21:9"\n')
SLIVER_NCC = 0.9      # measured 0.991 left, 1.000 right between the two rows; a black or missing edge is far below


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", default=os.path.join(root, "build-debug"))
    ap.add_argument("--port", type=int, default=4440)
    ap.add_argument("--seed", default=os.path.join(root, "saves"))
    ap.add_argument("--proof", action="store_true", help="the new-name half: row 240 FACT0 from MMM.DAT")
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
        fh.write(PROOF_STATE if a.proof else STATE)

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

    def proof_body():
        pkg = os.path.join(a.build, "mods", "installed", "dw.newmaps.proof", "1.0.0", "assets")
        tfs = open(os.path.join(pkg, "glade.tfs"), "rb").read()
        mapf = open(os.path.join(pkg, "glade.map"), "rb").read()
        cam = spec["camera"]
        used = 8 + 512 * struct.unpack_from("<i", tfs, 4)[0] + 16388 * cam["gw"] * cam["gh"]
        resident = lambda: b"".join(g.d.read(GENERAL_BUFFER + o, min(0x4000, used - o)) for o in range(0, used, 0x4000))
        enter()
        g.settle(timeout_frames=1800)
        check(g.map_record(240)["file"] == "FACT0", "row 240 names FACT0 (proof ticked)")
        g.warp(240, 0)
        plays(240, "FACT0 from MMM.DAT")
        check(resident() == tfs[:used], "FACT0.TFS from MMM.DAT is the TFS resident in GENERAL_BUFFER (%d bytes)" % used)
        nw = struct.unpack_from("<i", mapf, 0)[0] // 4
        coll = struct.unpack_from("<i", mapf, 4 * (nw - 1))[0]
        check(g.d.read(MAP_COLLISION, 10000) == mapf[coll:coll + 10000], "FACT0.MAP's collision grid is live")
        g.savestate("save", SLOT)
        g.warp(2, 0)
        plays(2, "stock map after the warp away")
        g.savestate("load", SLOT)
        g.settle(timeout_frames=1800)
        check(screen() == 240, "the savestate saved on 240 returns to row 240 (screen %d)" % screen())
        check(resident() == tfs[:used], "the TFS is resident after the savestate load")
        g.advance(60)
        talk_to_boss(calibrate())

    try:
        if a.proof:
            proof_body()
            raise StopIteration
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
    except StopIteration:
        pass
    finally:
        g.shutdown()
    if a.proof:                                        # an empty state.toml: the package is not in the plan
        with open(os.path.join(a.build, "mods", "state.toml"), "w") as fh:
            fh.write("format_version = 2\n")
        g = Game.launch(exe=os.path.join(a.build, "Digimon_World_Recompiled.exe"), port=a.port + 1,
                        memcard_dir=cards, extra_args=["--headless-opengl"],
                        log_path=os.path.join(out, "run_empty.log"))
        try:
            enter()
            g.settle(timeout_frames=1800)
            check(g.map_record(240)["file"] == "", "with an empty state.toml row 240 is empty")
        finally:
            g.shutdown()
    if a.proof:                                        # glade + proof: one plan, two map plugins
        with open(os.path.join(a.build, "mods", "state.toml"), "w") as fh:
            fh.write(STATE + PROOF_BODY)
        g = Game.launch(exe=os.path.join(a.build, "Digimon_World_Recompiled.exe"), port=a.port + 4,
                        memcard_dir=cards, extra_args=["--headless-opengl"],
                        log_path=os.path.join(out, "run_both.log"))
        try:
            enter()
            g.settle(timeout_frames=1800)
            check(g.map_record(239)["file"] == "MGEN17", "glade + proof ticked together: row 239 is MGEN17")
            check(g.map_record(240)["file"] == "FACT0", "glade + proof ticked together: row 240 is FACT0")
            g.warp(240, 0)
            g.settle(timeout_frames=600)
            plays(240, "FACT0 beside the glade")
        finally:
            g.shutdown()
    if a.proof:                                        # 21:9: row 240 (files from MMM.DAT) vs row 239 (stock path), same art
        import numpy as np
        from PIL import Image
        shots = {}
        for k, (state, row) in enumerate(((PROOF_STATE, 240), (STATE, 239))):
            with open(os.path.join(a.build, "mods", "state.toml"), "w") as fh:
                fh.write(state + WIDE)
            g = Game.launch(exe=os.path.join(a.build, "Digimon_World_Recompiled.exe"), port=a.port + 2 + k,
                            memcard_dir=cards, extra_args=["--headless-opengl"],
                            log_path=os.path.join(out, "run_wide%d.log" % row))
            try:
                enter()
                g.settle(timeout_frames=1800)
                g.warp(row, 0)
                plays(row, "21:9")
                g.advance(120)
                path = os.path.join(out, "wide_%d.png" % row)
                g.artifact_shot(path)
                shots[row] = np.asarray(Image.open(path).convert("L"), float)
            finally:
                g.shutdown()
        a240, a239 = shots[240], shots[239]
        check(a240.shape == a239.shape and a240.shape[1] * 3 >= a240.shape[0] * 7,
              "both arrivals are 21:9 screenshots (%s)" % (a240.shape,))
        w = a240.shape[1]
        for name, sl in (("left", slice(0, w // 5)), ("right", slice(w * 4 // 5, w))):
            x, y = a240[:, sl], a239[:, sl]
            lit = x.mean() > 20 and y.mean() > 20
            xx, yy = x - x.mean(), y - y.mean()
            ncc = (xx * yy).sum() / np.sqrt((xx * xx).sum() * (yy * yy).sum() or 1)
            check(lit and ncc >= SLIVER_NCC, "21:9 %s sliver on FACT0 matches the glade's: NCC %.3f, brightness %.0f/%.0f"
                  % (name, ncc, x.mean(), y.mean()))
    print("RESULT newmap_files: %s" % ("FAIL %s" % fails if fails else "PASS"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
