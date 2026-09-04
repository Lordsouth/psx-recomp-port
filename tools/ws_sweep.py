#!/usr/bin/env python3
"""Walk the camera and measure the widescreen reveals at every stop.

WHY THIS EXISTS. Widescreen coverage was once claimed at "20 of 20 maps, mean
void 0.7%" on the strength of one screenshot per map, taken on ARRIVAL. Every
arrival frame is clean, because the load-time alignment nudge makes it so; the
backdrop only falls short once the player MOVES. The claim shipped, and players
walked into a black band covering most of the right reveal. A coverage claim
needs a sweep, not a sample.

So this drives the camera in four directions, stopping every few frames, and at
each stop records the camera, the tile-ring origin, the arithmetic deficit and
the measured void in each reveal. It also checks the VRAM sliver slots, so a
stop where the sliver SHOULD have fired but did not is visible in the numbers
rather than only in the picture.

Aspect-agnostic: the reveal width is read from the running game (ws_nw), and
the frame geometry from the screenshots (wsmeasure.geometry), so the same
command verifies 16:10 and 16:9 without edits.

    py -3.12 tools/ws_sweep.py --port 4390 --maps 1,2,5,12 --steps 30

Exit code 0 if every stop measured, 1 if any map could not be reached.
"""
import argparse
import json
import os
import shutil
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dwgame import Game                                   # noqa: E402
import wsmeasure                                          # noqa: E402

CAMERA_X, CAMERA_Y = 0x80150C38, 0x80150C3A
MAP_WIDTH, MAP_HEIGHT = 0x80150C3C, 0x80150C3D
MAP_TILE_X, MAP_TILE_Y = 0x80134DC2, 0x80134DC4
CURRENT_SCREEN = 0x80134DA8
RING_COLS, TILE_PX, SCREEN_W, SCREEN_H = 4, 128, 320, 240
# dw_ws_sliver.c: three strips stacked in x 651..703, rows 256..495
SLIVER_X, SLIVER_Y, SLIVER_HW = 651, 256, 53


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=4390)
    ap.add_argument("--maps", default="1,2,5,12")
    ap.add_argument("--steps", type=int, default=30)
    ap.add_argument("--out", default=None)
    ap.add_argument("--exe", default=None)
    a = ap.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = a.out or os.path.join(root, "build-debug", "ws_sweep_%d" % a.port)
    os.makedirs(out, exist_ok=True)
    cards = os.path.join(out, "cards")
    if not os.path.isdir(cards):
        os.makedirs(cards)

    kw = {"port": a.port, "memcard_dir": cards,
          "log_path": os.path.join(out, "game.log"),
          "env": {"PSX_OVERLAY_CAPTURES": os.path.join(out, "captures.json")}}
    if a.exe:
        kw["exe"] = a.exe
    g = Game.launch(**kw)

    def i8(x):
        return struct.unpack("<b", g.d.read(x, 1))[0]

    def i16(x):
        return struct.unpack("<h", g.d.read(x, 2))[0]

    def peek(x, y, w, h):
        hx = g.d.cmd("vram_peek", x=x, y=y, w=w, h=h)["hex"]
        v = [int(hx[i:i + 4], 16) for i in range(0, len(hx), 4)]
        return sum(1 for q in v if q) / float(len(v))

    rows, unreached = [], []
    try:
        g.enter_game()
        nw = g.d.cmd("ws_nw")
        m = int(nw.get("nw_extra", 0)) // 2
        print("ws_nw %s -> per-side reveal %d px" % (nw, m), flush=True)
        if m <= 0:
            print("native-wide is not presenting; nothing to sweep")
            return 1

        def state():
            c, cy = i16(CAMERA_X), i16(CAMERA_Y)
            w, t = i8(MAP_WIDTH), i8(MAP_TILE_X)
            return {"c": c, "cy": cy, "w": w, "h": i8(MAP_HEIGHT), "t": t,
                    "ty": i8(MAP_TILE_Y), "scr": g.d.read(CURRENT_SCREEN, 1)[0],
                    "deficit": (c + SCREEN_W + m) - (t + RING_COLS) * TILE_PX}

        for mid in [int(x) for x in a.maps.split(",")]:
            try:
                g.warp(mid)
            except Exception as exc:                        # noqa: BLE001
                print("map %d unreachable: %s" % (mid, exc), flush=True)
                unreached.append(mid)
                continue
            g.advance(120)
            if state()["w"] <= RING_COLS:
                print("map %d: ring holds the whole map, skipped" % mid, flush=True)
                continue
            k = 0
            for d in ("right", "left", "down", "up"):
                still, last = 0, None
                for _ in range(a.steps):
                    g.route("12:%s,10:none" % d)
                    g.advance(8)
                    st = state()
                    if st["scr"] != mid:
                        try:
                            g.warp(mid)
                            g.advance(120)
                        except Exception:                   # noqa: BLE001
                            unreached.append(mid)
                        break
                    key = (st["c"], st["cy"])
                    still = still + 1 if key == last else 0
                    last = key
                    if still >= 2:
                        break
                    p = os.path.join(out, "m%d_k%03d.png" % (mid, k))
                    g.d.cmd("screenshot", path=p)
                    meas = wsmeasure.measure(p)
                    sl = peek(SLIVER_X, SLIVER_Y, SLIVER_HW, 128)
                    # A frame whose CENTRE is dark too is a fade or a screen
                    # transition, not a coverage failure: the reveals are black
                    # because the whole frame is. Recording centre is what lets
                    # the summary tell those apart -- without it a single
                    # transition frame reports as a 100% void and buries every
                    # real number under it.
                    faded = (meas["centre"] or 0) > 40
                    rows.append({"map": mid, "dir": d, "k": k, **st,
                                 "left": meas["left"], "right": meas["right"],
                                 "centre": meas["centre"], "faded": faded,
                                 "wide_w": meas.get("wide_w"),
                                 "reveal": meas.get("reveal"), "sliver": sl})
                    print("%4d k%03d %-5s C=%4d T=%d def=%4d  L=%5s R=%5s  "
                          "frame=%s reveal=%s sliver=%.2f"
                          % (mid, k, d, st["c"], st["t"], st["deficit"],
                             meas["left"], meas["right"], meas.get("wide_w"),
                             meas.get("reveal"), sl), flush=True)
                    k += 1
    finally:
        g.shutdown()

    faded = [r for r in rows if r.get("faded")]
    wide = [r for r in rows if r["right"] is not None and not r.get("faded")]
    act = [r for r in wide if r["deficit"] > 0]
    print("\n%d stops, %d measured wide, %d faded/transition (excluded), "
          "%d with the ring short on the right"
          % (len(rows), len(wide), len(faded), len(act)), flush=True)
    if wide:
        wr = max(wide, key=lambda r: r["right"])
        wl = max(wide, key=lambda r: r["left"])
        print("worst right void %.1f%% (map %d C=%d def=%d)"
              % (wr["right"], wr["map"], wr["c"], wr["deficit"]), flush=True)
        print("worst left  void %.1f%% (map %d C=%d def=%d)"
              % (wl["left"], wl["map"], wl["c"], wl["deficit"]), flush=True)
        if act:
            print("worst right void WHERE THE SLIVER MUST COVER: %.1f%%"
                  % max(r["right"] for r in act), flush=True)
    if unreached:
        print("UNREACHED MAPS: %s" % sorted(set(unreached)), flush=True)
    json.dump(rows, open(os.path.join(out, "sweep.json"), "w"), indent=1)
    print("rows -> %s" % os.path.join(out, "sweep.json"), flush=True)
    return 1 if unreached else 0


if __name__ == "__main__":
    sys.exit(main())
