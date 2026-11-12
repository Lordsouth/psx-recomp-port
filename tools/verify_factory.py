#!/usr/bin/env python3
"""Does the Abandoned Factory (tools/gen_factory.py) play as four connected screens?

    walk   fresh sandbox card: warp to the Gear Savanna host and walk east through the new gate. On each of
           AFAC1..AFAC4: the row is CURRENT_SCREEN, the game runs on that screen's served MAP and TFS (the
           files at the LBA and of the size its dw_newmaps_files.inc rows give), the tamer arrived at spawn 0; walk
           to the screen's three floor spots, then take the forward strip (on the production floors through the
           gap). From AFAC4 walk back through every screen to the host, landing at each previous screen's spawn 2
           and at the host's return spawn.
           --wide does it at 21:9 and checks each screen's edges against its backdrop (Field.slivers).

    py -3.12 tools/gen_factory.py
    cmake --build build-debug --target psx-runtime      (stages the bundled package)
    py -3.12 -I tools/verify_factory.py walk --seed <card dir> [--port N] [--wide]

The seed is any card dir whose slot 1 holds a Digimon World file. It writes build/mods/state.toml. Verdicts are
guest-memory reads (and at 21:9 the screenshot's edges against our own backdrop).
"""
import argparse
import json
import os
import re
import shutil
import sys
import time
import tomllib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dwgame import Game  # noqa: E402
from field_drive import Field, TAMER, RIGHT, LEFT  # noqa: E402
from newmap_engine import tile_of  # noqa: E402

PACKAGE, FEATURE = "dw.newmaps.factory", "factory"
STATE = ('format_version = 2\n\n[[package]]\nid = "%s"\nversion = "1.0.0"\n\n'
         '[[feature]]\npackage_id = "%s"\nid = "%s"\nenabled = true\n' % (PACKAGE, PACKAGE, FEATURE))
WIDE = ('\n[[package]]\nid = "psx.enhancement.widescreen"\nversion = "1.0.0"\n\n'
        '[[feature]]\npackage_id = "psx.enhancement.widescreen"\nid = "widescreen"\nenabled = true\n'
        '[feature.values]\naspect = "21:9"\n')


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("walk",))
    ap.add_argument("--wide", action="store_true", help="at 21:9, with each screen's edges checked")
    ap.add_argument("--build", default=os.path.join(root, "build-debug"))
    ap.add_argument("--port", type=int, default=4450)
    ap.add_argument("--seed", default=os.path.join(root, "saves"))
    a = ap.parse_args()
    out = os.path.join(a.build, "newmap_factory")
    cards = os.path.join(out, "cards")
    os.makedirs(out, exist_ok=True)
    spec = json.load(open(os.path.join(a.build, "newmap", "factory", "spec.json")))
    host, screens = spec["host"], spec["screens"]
    src = os.path.join(root, "mods", "preloaded", "packages", PACKAGE, "1.0.0")
    pkg = os.path.join(a.build, "mods", "bundled", PACKAGE, "1.0.0")
    fails = []

    def check(cond, msg):
        print(("PASS " if cond else "FAIL ") + msg, flush=True)
        if not cond:
            fails.append(msg)

    staged = os.path.exists(os.path.join(pkg, "manifest.toml"))
    check(staged, "the factory package is staged in %s" % pkg)
    if staged:                                         # the exe serves the staged copy: test what was generated
        for name in ["manifest.toml"] + [os.path.join("assets", n) for n in os.listdir(os.path.join(src, "assets"))]:
            if not os.path.exists(os.path.join(pkg, name)) or \
                    open(os.path.join(src, name), "rb").read() != open(os.path.join(pkg, name), "rb").read():
                print("%s in %s is stale: rebuild to stage what gen_factory.py wrote" % (name, pkg), file=sys.stderr)
                return 2
    inc = {(n, ext): (int(lba), int(size)) for n, ext, lba, size in
           re.findall(r'\{"(\w+)\.(MAP|TFS)", (\d+), (\d+)\}', open(os.path.join(root, "dw_newmaps_files.inc")).read())}
    overlays = {o["file"]: o["offset"] for o in tomllib.load(open(os.path.join(src, "manifest.toml"), "rb"))["overlay"]} \
        if os.path.exists(os.path.join(src, "manifest.toml")) else {}
    shutil.rmtree(cards, ignore_errors=True)
    os.makedirs(cards)
    for name in ("card1.mcd", "card2.mcd"):
        if os.path.exists(os.path.join(a.seed, name)):
            shutil.copy(os.path.join(a.seed, name), cards)
    if not os.path.exists(os.path.join(cards, "card1.mcd")):
        print("No memory card to seed from at %s" % a.seed, file=sys.stderr)
        return 2
    with open(os.path.join(a.build, "mods", "state.toml"), "w") as fh:
        fh.write(STATE + (WIDE if a.wide else ""))

    g = Game.launch(exe=os.path.join(a.build, "Digimon_World_Recompiled.exe"), port=a.port,
                    memcard_dir=cards, extra_args=["--headless-opengl"], log_path=os.path.join(out, a.mode + ".log"))
    shot = lambda n: (g.artifact_shot(os.path.join(out, "%s_%s.png" % (a.mode, n))),
                      os.path.join(out, "%s_%s.png" % (a.mode, n)))[1]
    f = Field(g, check, shot)
    screen, pos, near, go, wait_for = f.screen, f.pos, f.near, f.go, f.wait_for
    vec = {}

    def arrived(row, at, what):
        ok = wait_for(lambda: screen() == row and g.tamer_state == 0, 120)   # 21:9 runs at about a third of speed here
        g.advance(20)
        p = pos(TAMER)
        check(ok and p is not None and (p[0], p[2]) == (at[0], at[2]), "%s: on map %d at %s (now %s)" % (what, row, at, p))

    def files(sc):
        """The game runs on this screen's served files: the .inc rows match the package, and memory holds them."""
        for ext in ("MAP", "TFS"):
            name = "assets/%s.%s" % (sc["name"].lower(), ext.lower())
            lba, size = inc.get((sc["name"], ext), (None, None))
            check(lba is not None and overlays.get(name) == lba * 2048 and os.path.getsize(os.path.join(src, name)) == size,
                  "%s.%s is served from LBA %s, %s bytes, as the package lays it out" % (sc["name"], ext, lba, size))
        f.loaded_files(sc, os.path.join(src, "assets"), sc["name"].lower())   # the staged copy is checked equal

    def align(x):
        """The gap passes the tamer's centre in its middle column only (docs/GAME.md 3.9): line up on it by taps."""
        per = {d: abs(vec[d][0]) / 21 for d in (RIGHT, LEFT)}            # units per frame (calibrate walks 21)
        col = lambda: tile_of(pos(TAMER)[0], 0)[0]
        for _ in range(12):
            if col() == tile_of(x, 0)[0]:
                break
            dx = x - pos(TAMER)[0]
            d = RIGHT if (dx > 0) == (vec[RIGHT][0] > 0) else LEFT
            f.walk(d, max(2, int(abs(dx) / max(per[d], 1))) / 60)
            time.sleep(0.1)
        check(col() == tile_of(x, 0)[0], "lined up on the gap's middle column, x %d (at %s)" % (x, pos(TAMER)))

    def leave(target, row_to, what):
        ok = go(tuple(target), lambda: screen() == row_to or g.tamer_state == 5, vec=vec)
        check(ok and wait_for(lambda: screen() == row_to and g.tamer_state == 0, 120), "%s: walked onto map %d" % (what, row_to))

    try:
        f.enter()
        g.settle(timeout_frames=1800)
        for sc in screens:
            check(g.map_record(sc["row"])["file"] == sc["name"], "row %d holds %s (is %s enabled?)" % (sc["row"], sc["name"], PACKAGE))
        g.warp(host["row"], host["exit"])
        arrived(host["row"], host["spawn"], "the gate's spawn on %s" % host["file"])
        f.pad(RIGHT)                                   # straight east: the walker's calibration would take the gate
        ok = wait_for(lambda: screen() == screens[0]["row"] or g.tamer_state == 5, 10)
        f.pad(0)
        check(ok, "walked east through the new gate on %s" % host["file"])
        for i, sc in enumerate(screens):
            arrived(sc["row"], sc["spawn"][0], "%s, walking in" % sc["name"])
            files(sc)
            if a.wide:
                g.advance(60)
                f.slivers(shot("wide_" + sc["name"]), sc["backdrop"], sc["name"])
            else:
                shot(sc["name"])
            if not vec:                                # one camera for all four screens
                vec.update(f.calibrate())
            for t in sc["walk"]:
                check(go(tuple(t), lambda: near(*t), limit=60, vec=vec) and screen() == sc["row"],
                      "%s: walked to %s (at %s)" % (sc["name"], t, pos(TAMER)))
            if sc["strip_fwd"] is None:
                continue
            if sc["via"]:
                go(tuple(sc["via"][0]), lambda: near(*sc["via"][0]), limit=60, vec=vec)
                align(sc["via"][0][0])
                check(go(tuple(sc["via"][1]), lambda: near(*sc["via"][1]), limit=30, vec=vec),
                      "%s: through the gap to %s (at %s)" % (sc["name"], sc["via"][1], pos(TAMER)))
            leave(sc["strip_fwd"], screens[i + 1]["row"], "%s, the forward strip" % sc["name"])
        for i in range(3, -1, -1):                     # and all the way back
            sc = screens[i]
            for v in sc["via"][::-1]:
                go(tuple(v), lambda: near(*v), limit=30, vec=vec)
            if sc["via"]:
                check(near(*sc["via"][0]), "%s: back through the gap (at %s)" % (sc["name"], pos(TAMER)))
            go((sc["spawn"][0][0], sc["spawn"][0][2]), lambda: near(sc["spawn"][0][0], sc["spawn"][0][2]), limit=60, vec=vec)
            row_to = screens[i - 1]["row"] if i else host["row"]
            leave(sc["strip_back"], row_to, "%s, the strip back" % sc["name"])
            if i:
                arrived(row_to, screens[i - 1]["spawn"][2], "%s, walking back" % screens[i - 1]["name"])
            else:
                arrived(row_to, host["spawn"], "out at the gate on %s" % host["file"])
        shot("out")
    finally:
        g.shutdown()
    print("RESULT factory %s%s: %s" % (a.mode, " --wide" if a.wide else "", "FAIL %s" % fails if fails else "PASS"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
