#!/usr/bin/env python3
"""Does a new map on free row 239 behave like a real map?

    --map spike   gen_newmap_spike.py: the unused Mt. Infinity floor MGEN17 + a Piddomon,
                  through Coela Point's east exit (dev only: it takes that route away)
    --map glade   gen_newmap.py: the shipped glade (bundled package), through its own
                  new exit in Native Forest

    run1  fresh sandbox card: walk through the entrance onto 239, run the map's own
          checks (spike: win the Piddomon battle; glade: prove our files are what the
          game loaded, walk the meadow, try to wade into the pond, stand behind a
          bush), save with Save Anywhere, walk back out
    run2  relaunch on run1's card. Spike: Continue lands on 239 at the saved spot.
          Glade: a save made inside resumes just outside, so Continue lands on the
          host; walk back in and out, then save next door, where previousScreen and
          the exit would name the new map
    off   (glade) the mod OFF, on the stock game: Continue from run1's card
          (--card glade) or run2's (--card host) loads and plays. No card may ever
          depend on the mod

    py -3.12 tools/gen_newmap.py            (or gen_newmap_spike.py)
    cmake --build build-debug --target psx-runtime     (glade: stages the bundled package)
    py -3.12 tools/verify_newmap.py run1 --map glade --seed <card dir>
    py -3.12 tools/verify_newmap.py run2 --map glade
    py -3.12 tools/verify_newmap.py off --map glade --card glade
    py -3.12 tools/verify_newmap.py off --map glade --card host

The seed is any card dir whose slot 1 holds a Digimon World file; a never-saved
New Game file plays Jijimon's intro first, which this drives through. It writes
build/mods/state.toml. Verdicts are guest-memory, VRAM and memory-card reads.
The glade's screenshot comparison is printed as a MEASURE line for a human:
screenshots are evidence here, never the verdict.
"""
import argparse
import json
import os
import shutil
import struct
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dwgame import Game  # noqa: E402

UP, RIGHT, DOWN, LEFT = 0x0010, 0x0020, 0x0040, 0x0080
TAMER, NPC0 = 0x8015576C, 0x8012F344 + 8          # ENTITY_TABLE[2]: first map NPC
CURRENT_SCREEN, GAME_STATE, IS_SCRIPT_PAUSED = 0x80134DA8, 0x80134F0A, 0x80134FF4
TRI_STATE, MENU_CURSOR, CURRENT_MENU, YES_NO = 0x80134D30, 0x80123E8C, 0x8013504C, 0x801317D8
GENERAL_BUFFER, MAP_TILE_DATA, MAP_COLLISION = 0x80010000, 0x8014F084, 0x801AF398
GS_VIEWPOINT, VIEWPORT_DISTANCE, HOUR, CURRENT_FRAME = 0x80185BA8, 0x80134ED4, 0x80134EBC, 0x80134F08
CAMERA = MAP_TILE_DATA + 7092                     # cameraX, cameraY (i16), width, height (i8)
MAP_OBJECT_INSTANCE_COUNT = 0x80134D1C            # map objects (foreground sprites) loadMapObjects set up
PREVIOUS_SCREEN, CURRENT_EXIT = 0x80134DA9, 0x80134DAA
HOST_EXIT = (1858, -1142)                         # Coela Point's east-exit spawn
STATE = ('format_version = 2\n\n[[package]]\nid = "%s"\nversion = "1.0.0"\n\n'
         '[[package]]\nid = "dw.vanilla.qol"\nversion = "1.0.0"\n\n'
         '[[feature]]\npackage_id = "%s"\nid = "%s"\nenabled = true\n\n'
         '[[feature]]\npackage_id = "dw.vanilla.qol"\nid = "save-anywhere"\nenabled = true\n')
MAPS = {
    # spawn 0, then the legs from anywhere inside to the map's warp strip
    "spike": dict(package="dw.newmaps.spike", feature="spike", file="MGEN17",
                  spawn=(1474, -446), out=[(450, -350), (450, -1450)]),
    "glade": dict(package="dw.newmaps", feature="glade", file="MGEN17"),
}


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("run1", "run2", "off"))
    ap.add_argument("--card", choices=("glade", "host"), default="glade", help="off: which saved card to load")
    ap.add_argument("--map", choices=sorted(MAPS), default="glade")
    ap.add_argument("--build", default=os.path.join(root, "build-debug"))
    ap.add_argument("--port", type=int, default=4430)
    ap.add_argument("--seed", default=os.path.join(root, "saves"))
    a = ap.parse_args()
    mp = dict(MAPS[a.map])
    spec = None
    if a.map == "glade":
        spec = json.load(open(os.path.join(a.build, "newmap", "spec.json")))
        mp["spawn"] = (spec["spawn"][0], spec["spawn"][2])
        # via the middle of the meadow: from the east side a straight line to the spawn snags on the log
        mp["out"] = [tuple(spec["meadow"][1]), mp["spawn"], (spec["path"][1][0], spec["strip_z"] - 400)]
        mp["host_row"], mp["host_at"] = spec["host"]["row"], (spec["host"]["spawn"][0], spec["host"]["spawn"][2])
    else:
        mp["host_row"], mp["host_at"] = 1, HOST_EXIT
    pkg_dir = os.path.join(a.build, "mods", "bundled" if a.map == "glade" else "installed", mp["package"], "1.0.0")
    if a.map == "glade":                               # the exe serves the staged copy: test what was generated
        src = os.path.join(root, "mods", "preloaded", "packages", mp["package"], "1.0.0", "assets")
        for name in os.listdir(src):
            if open(os.path.join(src, name), "rb").read() != open(os.path.join(pkg_dir, "assets", name), "rb").read():
                print("%s in %s is stale: rebuild to stage what gen_newmap.py wrote" % (name, pkg_dir), file=sys.stderr)
                return 2
    out = os.path.join(a.build, "newmap_%s" % a.map)
    cards = os.path.join(out, "cards")
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(a.build, "mods", "state.toml"), "w") as fh:
        fh.write("format_version = 2\n" if a.mode == "off" else STATE % (mp["package"], mp["package"], mp["feature"]))
    if a.mode == "off":                                # a copy of the card that save was left on
        shutil.rmtree(cards, ignore_errors=True)
        shutil.copytree(os.path.join(out, "cards_" + a.card), cards)
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
    shot = lambda n: (g.artifact_shot(os.path.join(out, "%s_%s.png" % (a.mode, n))),
                      os.path.join(out, "%s_%s.png" % (a.mode, n)))[1]

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

    def go(target, until, limit=90, vec=None):
        """Greedy walk toward world (x, z); tries the next-best direction at a wall."""
        vec = vec or calibrate()
        for _ in range(limit):
            if until():
                return True
            p = pos(TAMER)
            if p is None or g.tamer_state != 0 or gstate() != 0:
                g.route("4:cross,20:none")        # talk or fight through whatever stopped us
                time.sleep(0.3)                   # (the spike's Piddomon respawns on entry)
                continue
            want = (target[0] - p[0], target[1] - p[2])
            for d in sorted(vec, key=lambda d: -(vec[d][0] * want[0] + vec[d][1] * want[1]))[:3]:
                walk(d, 0.3)
                q = pos(TAMER)
                if q is None or until() or abs(q[0] - p[0]) + abs(q[2] - p[2]) > 20:
                    break
        return until()

    def near(x, z, r=120):
        p = pos(TAMER)
        return p is not None and abs(p[0] - x) < r and abs(p[2] - z) < r

    def enter():
        """Title -> CONTINUE. DOWN waits for the menu to take input: an early DOWN
        is dropped and the crosses then start a NEW GAME. The intro gives a partner
        while still on the opening screen (238) with its script running, so "in
        control" also needs the script idle (the field menu's own test) for 2 s."""
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

    def walk_out():
        for leg in mp["out"][:-1]:
            go(leg, lambda: near(*leg) or screen() == mp["host_row"], limit=60)
        ok = go(mp["out"][-1], lambda: screen() == mp["host_row"] or g.tamer_state == 5)
        check(ok and wait_for(lambda: screen() == mp["host_row"] and g.tamer_state == 0),
              "walked onto the warp strip back to map %d" % mp["host_row"])
        p = pos(TAMER)
        shot("out")
        check(p is not None and abs(p[0] - mp["host_at"][0]) < 400 and abs(p[2] - mp["host_at"][1]) < 400,
              "arrived at the entrance's spawn %s" % (p,))

    def walk_in():
        """From the host's side of the entrance, north through the new exit."""
        pad(UP)                                        # straight on: the walker's calibration steps
        ok = wait_for(lambda: screen() == 239 or g.tamer_state == 5, 10)   # would leave this narrow notch
        pad(0)
        check(ok and wait_for(lambda: screen() == 239 and g.tamer_state == 0),
              "walked north through the new exit onto map 239 (no cutscene took over)")
        g.advance(20)
        p = pos(TAMER)
        check(p is not None and (p[0], p[2]) == tuple(mp["spawn"]), "arrived at spawn 0 %s" % (p,))

    def battle():
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

    def loaded_files():
        """Memory and VRAM say the game runs on OUR map and tile files."""
        pkg = os.path.join(pkg_dir, "assets")
        tfs = open(os.path.join(pkg, "glade.tfs"), "rb").read()
        mapf = open(os.path.join(pkg, "glade.map"), "rb").read()
        used = 8 + 512 * struct.unpack_from("<i", tfs, 4)[0] + 16388 * spec["camera"]["gw"] * spec["camera"]["gh"]
        ram = b"".join(g.d.read(GENERAL_BUFFER + o, min(0x4000, used - o)) for o in range(0, used, 0x4000))
        check(ram == tfs[:used], "our TFS is the one resident in GENERAL_BUFFER (%d bytes)" % used)
        nw = struct.unpack_from("<i", mapf, 0)[0] // 4
        coll = struct.unpack_from("<i", mapf, 4 * (nw - 1))[0]        # the last header word
        check(g.d.read(MAP_COLLISION, 10000) == mapf[coll:coll + 10000], "our collision grid is live")
        if "fg" in spec:                                       # foreground sprites: objects and their image
            n = struct.unpack("<h", g.d.read(MAP_OBJECT_INSTANCE_COUNT, 2))[0]
            check(n == spec["fg"]["count"], "%d foreground sprites loaded as map objects" % n)
            tim = struct.unpack_from("<i", mapf, 4)[0]          # first image: TIM, clut block, pixel block
            pix = tim + 8 + struct.unpack_from("<I", mapf, tim + 8)[0]
            px, py, pw, ph = struct.unpack_from("<4H", mapf, pix + 4)
            row = max(range(ph), key=lambda r: sum(mapf[pix + 12 + 256 * r:pix + 12 + 256 * (r + 1)]))
            hexrow = g.d.cmd("vram_peek", x=px, y=py + row, w=pw, h=1)["hex"]      # one halfword per 4 digits
            live = b"".join(bytes.fromhex(hexrow[i:i + 4])[::-1] for i in range(0, len(hexrow), 4))
            check(live == mapf[pix + 12 + 256 * row:pix + 12 + 256 * (row + 1)],
                  "our sprite texels are in VRAM at (%d,%d)" % (px, py + row))
        vp = struct.unpack("<6i", g.d.read(GS_VIEWPOINT, 24))
        cam = spec["camera"]
        check(list(vp) == [int(v) // 2 * 2 for v in cam["vp"] + cam["vr"]]
              and i32(VIEWPORT_DISTANCE) == cam["h"], "our camera is live (GS_VIEWPOINT %s)" % (vp,))
        w, h = g.d.read(CAMERA + 4, 2)
        check((w, h) == (cam["gw"], cam["gh"]), "tile grid %dx%d" % (w, h))
        hexrow = "".join(g.d.cmd("vram_peek", x=x, y=480, w=128, h=1)["hex"] for x in (0, 128))
        live = [int(hexrow[i:i + 4], 16) for i in range(0, 1024, 4)]
        clut0 = list(struct.unpack_from("<256H", tfs, 8))
        check(live == clut0, "our day palette is the live CLUT (VRAM row 480)")

    def measure(tag):
        """Screenshot vs our own backdrop under the live camera (evidence, not verdict)."""
        from PIL import Image
        path = shot(tag)
        cx, cy = struct.unpack("<2h", g.d.read(CAMERA, 4))
        img = np.asarray(Image.open(path).convert("L"), float)
        ref = np.asarray(Image.open(spec["backdrop"]).convert("L"), float)
        if img.shape != (240, 320):
            print("MEASURE %s: screenshot is %s, skipped" % (tag, img.shape), flush=True)
            return
        best = None
        win = img[90:200, 40:280]                  # clear of the clock and the HP bars
        for dy in range(-3, 4):
            for dx in range(-3, 4):
                r = ref[cy + 90 + dy:cy + 200 + dy, cx + 40 + dx:cx + 280 + dx]
                if r.shape != win.shape:
                    continue
                aa, bb = win - win.mean(), r - r.mean()
                ncc = (aa * bb).sum() / np.sqrt((aa * aa).sum() * (bb * bb).sum())
                if best is None or ncc > best[0]:
                    best = (ncc, dx, dy)
        print("MEASURE %s: camera (%d,%d), backdrop match NCC %.3f at offset (%d,%d)"
              % (tag, cx, cy, best[0], best[1], best[2]), flush=True)

    def glade():
        loaded_files()
        measure("arrival")
        vec = calibrate()
        meadow = [tuple(t) for t in spec["meadow"]]           # deep floor cells the generator picked
        for i, t in enumerate(meadow):
            # from the middle of the meadow: straight lines from elsewhere brush the rock or the stump,
            # and the greedy walker can slide off them
            if i != 1:
                go(meadow[1], lambda: near(*meadow[1]), limit=60, vec=vec)
            ok = go(t, lambda: near(*t), limit=60, vec=vec)
            p = pos(TAMER)
            check(ok and screen() == 239, "walked the meadow to %s (at %s)" % (t, p))
            measure("meadow%d" % i)
        # the pond is solid: stand on its south shore, walk straight at it, stay ashore
        lobes, south = spec["pond"]["lobes"], tuple(spec["pond"]["shore"])
        wet_at = lambda x, z: min(((x - cx) / rx) ** 2 + ((z - cz) / rz) ** 2 for cx, cz, rx, rz in lobes) < 1
        check(go(south, lambda: near(*south, r=100), limit=60, vec=vec), "on the pond's south shore %s" % (pos(TAMER),))
        north = max(vec, key=lambda d: vec[d][1])              # the pad direction for +z
        start, wet = pos(TAMER), []
        pad(north)
        for _ in range(30):                                    # 3 s pushing at the water
            time.sleep(0.1)
            p = pos(TAMER)
            if wet_at(p[0], p[2]):
                wet.append(p)
        pad(0)
        edge = next(z for z in range(p[2], p[2] + 3000, 10) if wet_at(p[0], z))
        gap = edge - p[2]                                      # where the push ended, to the water's edge
        check(0 < gap < 300 and not wet,
              "pushing into the pond for 3 s stops %d units short of the water "
              "(%s -> %s, %d samples in the water)" % (gap, start, p, len(wet)))
        shot("pond")
        if "fg" in spec:
            behind_bush(vec)

    def to_backdrop(p):
        """World point -> backdrop pixel, with the engine's projection (INTERNALS.md 3.2)."""
        cam = spec["camera"]
        vp, vr = np.array(cam["vp"], float), np.array(cam["vr"], float)
        f = (vr - vp) / np.linalg.norm(vr - vp)
        r = np.array([f[2], 0, -f[0]]) / np.hypot(f[0], f[2])
        d = np.cross(f, r)
        q = np.array(p, float) - vp
        return 64 * cam["gw"] + cam["h"] * (q @ r) / (q @ f), 64 * cam["gh"] + cam["h"] * (q @ d) / (q @ f)

    def behind_bush(vec):
        """Stand where the bush island's sprite should hide most of the tamer, and read the screen:
        under the sprite the screen must show the backdrop (the bush is drawn over the tamer), and
        just above it the tamer must still show."""
        from PIL import Image
        via, spot = tuple(spec["fg"]["via"]), tuple(spec["fg"]["behind"])
        check(go(via, lambda: near(*via), limit=60, vec=vec), "north of the bush island %s" % (pos(TAMER),))
        above = (spot[0], via[1])                              # then straight south: no sliding along the bush
        go(above, lambda: near(*above, r=80), limit=30, vec=vec)
        check(go(spot, lambda: near(*spot, r=100), limit=60, vec=vec), "behind the bush island %s" % (pos(TAMER),))
        time.sleep(1.5)                                        # let the camera settle
        cam0 = struct.unpack("<2h", g.d.read(CAMERA, 4))
        p = pos(TAMER)
        img = np.asarray(Image.open(shot("behind")).convert("RGB"), int)
        cam1 = struct.unpack("<2h", g.d.read(CAMERA, 4))
        check(cam0 == cam1 and img.shape == (240, 320, 3), "steady camera for the screenshot %s" % (cam0,))
        # the backdrop as indices (from our TFS), recoloured with the live palette: right at any hour
        cam = spec["camera"]
        tfs = open(os.path.join(pkg_dir, "assets", "glade.tfs"), "rb").read()
        at = 8 + 512 * struct.unpack_from("<i", tfs, 4)[0]
        idx = np.zeros((cam["gh"] * 128, cam["gw"] * 128), np.uint8)
        for k in range(cam["gw"] * cam["gh"]):
            ty, tx = divmod(k, cam["gw"])
            tile = np.frombuffer(tfs, np.uint8, 16384, at + 4 + 16388 * k).reshape(128, 128)
            idx[ty * 128:(ty + 1) * 128, tx * 128:(tx + 1) * 128] = tile
        hexrow = "".join(g.d.cmd("vram_peek", x=x, y=480, w=128, h=1)["hex"] for x in (0, 128))
        clut = np.array([int(hexrow[i:i + 4], 16) for i in range(0, 1024, 4)])
        rgb = np.stack([(clut & 31) << 3, ((clut >> 5) & 31) << 3, ((clut >> 10) & 31) << 3], -1)
        ref = rgb[idx].astype(int)
        mask = np.asarray(Image.open(spec["fg"]["mask"])) > 0
        cx, cy = cam1
        u, v = to_backdrop((p[0], 0, p[2]))
        box = (slice(int(v) - 48, int(v) + 4), slice(int(u) - 16, int(u) + 17))   # the tamer, feet to head
        scr = np.zeros(ref.shape, int)
        scr[cy:cy + 240, cx:cx + 320] = img
        diff = np.min([np.abs(np.roll(scr, dy, 0) - ref).max(-1) for dy in (-1, 0, 1)], 0)[box] > 24
        under, over = mask[box], ~mask[box]
        hidden = diff[under].mean() if under.any() else 1.0
        check(under.sum() >= 80 and hidden < 0.05,
              "the bush is drawn over the tamer: %d px of sprite in the tamer's box, %.1f%% of them show the tamer"
              % (under.sum(), 100 * hidden))
        check(diff[over].sum() >= 30, "the tamer still shows above the bush (%d px differ from the backdrop)"
              % diff[over].sum())

    def time_of_day():
        """Our dusk and night palettes: renderMap starts the fade when HOUR reads 16,
        20 or 6 on a frame divisible by 1200, and the fade ends by loading the
        target CLUT whole (tickDaytimeTransition), so VRAM row 480 must equal it."""
        tfs = open(os.path.join(pkg_dir, "assets", "glade.tfs"), "rb").read()
        live = lambda: [int(h[i:i + 4], 16) for h in ["".join(g.d.cmd("vram_peek", x=x, y=480, w=128, h=1)["hex"]
                                                              for x in (0, 128))] for i in range(0, 1024, 4)]
        for hour, k, name in ((16, 1, "dusk"), (20, 2, "night"), (6, 0, "day")):
            prev = (hour - 1) % 24                             # roll 15:58 -> 16:00 the clock's own way
            g.d.write_bytes(struct.pack("<H", prev * 1200 + 58 * 20 + 5), CURRENT_FRAME)
            g.d.write_bytes(struct.pack("<hh", prev, 58), HOUR)  # HOUR, MINUTE are adjacent
            want = list(struct.unpack_from("<256H", tfs, 8 + 512 * k))
            check(wait_for(lambda: live() == want, 45), "%s palette goes live at %02d:00" % (name, hour))
            shot(name)

    def keep_card(name):
        """A copy of the card as this save left it, for the mod-off runs."""
        shutil.rmtree(os.path.join(out, "cards_" + name), ignore_errors=True)
        shutil.copytree(cards, os.path.join(out, "cards_" + name))
        shutil.copy(os.path.join(out, "saved_pos.json"), os.path.join(out, "saved_pos_%s.json" % name))

    def save_here(on_new_map=True):
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
        if a.map == "glade":                           # no card names the new map, or the new exit
            host = spec["host"]
            check(main_state == 1 and s0[0x49A] == host["row"] and s0[0x49C] == host["save_exit"]
                  and s0[0x49B] != 239 and not (s0[0x49B] == host["row"] and s0[0x49D] == host["exit"]),
                  "card save: map %d exit %d (before: map %d exit %d) at %s"
                  % (s0[0x49A], s0[0x49C], s0[0x49B], s0[0x49D], (px, py, pz)))
            if on_new_map:
                check((px, py, pz) == tuple(host["spawn"]) and screen() == 239,
                      "a save made on 239 resumes at the entrance, and play stays on 239")
        else:
            check(main_state == 1 and s0[0x49A] == 239 and s0[0x49C] == 0,
                  "card save: map %d exit %d at %s" % (s0[0x49A], s0[0x49C], (px, py, pz)))
        with open(os.path.join(out, "saved_pos.json"), "w") as fh:
            json.dump([px, py, pz], fh)

    try:
        enter()
        g.settle(timeout_frames=1800)
        if a.mode == "off":
            saved = json.load(open(os.path.join(out, "saved_pos_%s.json" % a.card)))
            p = pos(TAMER)
            shot("off_" + a.card)
            check(g.map_record(239)["file"] == "", "the mod is off: row 239 is empty")
            check(screen() == spec["host"]["row"], "Continue lands on the stock map %d" % spec["host"]["row"])
            check(p is not None and abs(p[0] - saved[0]) < 60 and abs(p[2] - saved[2]) < 60,
                  "at the saved position %s (now %s)" % (saved, p))
            walk(UP, 3)                                # the same walk that enters the glade with the mod on
            check(screen() == spec["host"]["row"] and gstate() == 0, "the stock map has no exit there, and plays on")
            f0 = g.d.frame()
            time.sleep(2)
            check(g.d.frame() > f0 + 60, "game running")
            print("RESULT newmap off %s: %s" % (a.card, "FAIL %s" % fails if fails else "PASS"))
            return 1 if fails else 0
        check(g.map_record(239)["file"] == mp["file"], "row 239 holds %s (is %s enabled?)" % (mp["file"], mp["package"]))
        if a.mode == "run1":
            if a.map == "glade":
                g.warp(mp["host_row"], spec["host"]["exit"])
                check(screen() == mp["host_row"], "standing in the host's notch %s" % (pos(TAMER),))
                walk_in()
            else:
                g.warp(1, 2)
                check(screen() == 1, "standing at Coela Point's east exit")
                go((2150, -1150), lambda: screen() == 239 or g.tamer_state == 5)
                check(wait_for(lambda: screen() == 239 and g.tamer_state == 0),
                      "walked through the east exit onto map 239 (no cutscene took over)")
                g.advance(20)
                p = pos(TAMER)
                check(p is not None and (p[0], p[2]) == tuple(mp["spawn"]), "arrived at spawn 0 %s" % (p,))
            shot("in")
            battle() if a.map == "spike" else glade()
            save_here()
            if a.map == "glade":
                keep_card("glade")
            walk_out()
        elif a.map == "glade":
            saved = json.load(open(os.path.join(out, "saved_pos.json")))
            p = pos(TAMER)
            shot("continued")
            check(screen() == mp["host_row"], "Continue from a save made on the glade lands on the host")
            check(p is not None and abs(p[0] - saved[0]) < 60 and abs(p[2] - saved[2]) < 60,
                  "just outside the entrance %s (now %s)" % (saved, p))
            f0 = g.d.frame()
            time.sleep(2)
            check(g.d.frame() > f0 + 60, "game running on the host after the load")
            walk_in()
            loaded_files()
            walk_out()
            wait_for(lambda: i32(IS_SCRIPT_PAUSED) == 1, 20)
            g.advance(120)                 # the field menu takes input once the arrival has settled
            live = g.d.read(PREVIOUS_SCREEN, 1)[0], g.d.read(CURRENT_EXIT, 1)[0]
            check(live == (239, spec["host"]["exit"]),
                  "next door, the live game names the new map: previous screen %d, exit %d" % live)
            save_here(on_new_map=False)
            keep_card("host")
            g.warp(239, 0)                 # last: the clock jumps wake the partner's needs
            time_of_day()
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
    print("RESULT newmap %s %s: %s" % (a.map, a.mode, "FAIL %s" % fails if fails else "PASS"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
