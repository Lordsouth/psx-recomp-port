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
    fight (glade) fresh sandbox card: the secret fight, won, fled and lost for real.
          HP is poked so the outcome is the one under test: the numbers are not
          judged here. --wide does it at 21:9 with the Expanded Roster on.
          --brains N checks Battle End Freeze Fix on the win (docs/GAME.md 12.1)
    --dw15 runs a mode on Digimon World 1.5, where the glade runs its 1.5 edition
    measure (glade) a real fight: the partner evolved and set to --stats, then nothing
          poked but --heal. Presses only the finisher button. Prints how it went, judges nothing

    py -3.12 tools/gen_newmap.py            (or gen_newmap_spike.py)
    cmake --build build-debug --target psx-runtime     (glade: stages the bundled package)
    py -3.12 tools/verify_newmap.py run1 --map glade --seed <card dir>
    py -3.12 tools/verify_newmap.py run2 --map glade
    py -3.12 tools/verify_newmap.py off --map glade --card glade
    py -3.12 tools/verify_newmap.py off --map glade --card host
    py -3.12 tools/verify_newmap.py fight --map glade --seed <card dir> [--wide]

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
from field_drive import (  # noqa: E402
    Field, UP, TAMER, NPC0, CAMERA, IS_SCRIPT_PAUSED, PREVIOUS_SCREEN, CURRENT_EXIT, NPC_ANIM, NPC_ON_MAP,
    NPC_STATS, PARTNER_STATS, PARTNER_MAX_HP, PARTNER_ANIM, HOUR, CURRENT_FRAME)


ROW_122 = 122                                     # the other map bound to DG script 118 (TUNN07_2)
HOST_EXIT = (1858, -1142)                         # Coela Point's east-exit spawn
STATE = ('format_version = 2\n\n[[package]]\nid = "%s"\nversion = "1.0.0"\n\n'
         '[[package]]\nid = "dw.vanilla.qol"\nversion = "1.0.0"\n\n'
         '[[feature]]\npackage_id = "%s"\nid = "%s"\nenabled = true\n\n'
         '[[feature]]\npackage_id = "dw.vanilla.qol"\nid = "save-anywhere"\nenabled = true\n')
WIDE = ('\n[[package]]\nid = "psx.enhancement.widescreen"\nversion = "1.0.0"\n\n'
        '[[package]]\nid = "dw.vanilla.roster"\nversion = "1.0.0"\n\n'
        '[[feature]]\npackage_id = "psx.enhancement.widescreen"\nid = "widescreen"\nenabled = true\n'
        '[feature.values]\naspect = "21:9"\n\n'
        '[[feature]]\npackage_id = "dw.vanilla.roster"\nid = "roster"\nenabled = true\n')
NO_END_FIX = '\n[[feature]]\npackage_id = "dw.vanilla.qol"\nid = "end-box-fix"\nenabled = false\n'
DROPS = ('\n[[package]]\nid = "dw.vanilla.trainer"\nversion = "1.0.0"\n\n'
         '[[feature]]\npackage_id = "dw.vanilla.trainer"\nid = "guaranteed-drops"\nenabled = true\n')
DW15 = ('\n[[package]]\nid = "dw.1_5"\nversion = "1.0.0"\n\n'
        '[[feature]]\npackage_id = "dw.1_5"\nid = "dw15"\nenabled = true\n')
MAPS = {
    # spawn 0, then the legs from anywhere inside to the map's warp strip
    "spike": dict(package="dw.newmaps.spike", feature="spike", file="MGEN17",
                  spawn=(1474, -446), out=[(450, -350), (450, -1450)]),
    "glade": dict(package="dw.newmaps", feature="glade", file="MGEN17"),
}
ROWS = {"spike": 239, "glade": 239}


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("run1", "run2", "off", "fight", "measure"))
    ap.add_argument("--stats", default="5500,5000,600,600,450,450", help="measure: partner HP, MP, off, def, speed, brains")
    ap.add_argument("--heal", type=int, default=0, help="measure: stand-in for N recovery items, +5000 HP each below 2500")
    ap.add_argument("--brains", type=int, help="fight: the partner's Brains going in, so the win's gain crosses a threshold")
    ap.add_argument("--no-end-fix", action="store_true", help="fight: with Battle End Freeze Fix off, so --brains freezes as stock does")
    ap.add_argument("--drops", action="store_true", help="fight: with Guaranteed Drops on, so the box has a drop message first")
    ap.add_argument("--wide", action="store_true", help="fight: at 21:9, with the Expanded Roster on")
    ap.add_argument("--dw15", action="store_true",
                    help="on Digimon World 1.5: the map runs its 1.5 edition, glade-15; cards live in dw15/")
    ap.add_argument("--card", choices=("glade", "host"), default="glade", help="off: which saved card to load")
    ap.add_argument("--map", choices=sorted(MAPS), default="glade")
    ap.add_argument("--build", default=os.path.join(root, "build-debug"))
    ap.add_argument("--port", type=int, default=4430)
    ap.add_argument("--seed", default=os.path.join(root, "saves"))
    a = ap.parse_args()
    mp = dict(MAPS[a.map])
    row = ROWS[a.map]
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
        for name in ["manifest.toml"] + [os.path.join("assets", n) for n in os.listdir(src)]:
            if open(os.path.join(src, "..", name), "rb").read() != open(os.path.join(pkg_dir, name), "rb").read():
                print("%s in %s is stale: rebuild to stage what gen_newmap.py wrote" % (name, pkg_dir), file=sys.stderr)
                return 2
    out = os.path.join(a.build, "newmap_%s" % a.map)
    cards = os.path.join(out, "cards_fight" if a.mode in ("fight", "measure") else "cards")    # run2 needs run1's
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(a.build, "mods", "state.toml"), "w") as fh:
        fh.write("format_version = 2\n" if a.mode == "off" else STATE % (mp["package"], mp["package"], mp["feature"])
                 + (WIDE if a.wide else "") + (NO_END_FIX if a.no_end_fix else "") + (DROPS if a.drops else "")
                 + (DW15 if a.dw15 else ""))
    card_dir = os.path.join(cards, "dw15") if a.dw15 else cards     # 1.5 keeps its own cards (dw_dw15.c)
    if a.mode == "off":                                # a copy of the card that save was left on
        shutil.rmtree(cards, ignore_errors=True)
        shutil.copytree(os.path.join(out, "cards_" + a.card), cards)
    if a.mode in ("run1", "fight", "measure"):
        shutil.rmtree(cards, ignore_errors=True)
        os.makedirs(card_dir)
        for name in ("card1.mcd", "card2.mcd"):
            if os.path.exists(os.path.join(a.seed, name)):
                shutil.copy(os.path.join(a.seed, name), card_dir)
        if not os.path.exists(os.path.join(card_dir, "card1.mcd")):
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
    shot = lambda n: (g.artifact_shot(os.path.join(out, "%s_%s.png" % (a.mode, n))),
                      os.path.join(out, "%s_%s.png" % (a.mode, n)))[1]
    f = Field(g, check, shot)
    i32, gstate, screen, pad, pos = f.i32, f.gstate, f.screen, f.pad, f.pos
    walk, wait_for, calibrate, go, near = f.walk, f.wait_for, f.calibrate, f.go, f.near
    partner = f.partner

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
        ok = wait_for(lambda: screen() == row or g.tamer_state == 5, 10)   # would leave this narrow notch
        pad(0)
        check(ok and wait_for(lambda: screen() == row and g.tamer_state == 0),
              "walked north through the new exit onto map %d" % row + " (no cutscene took over)")
        g.advance(20)
        p = pos(TAMER)
        check(p is not None and (p[0], p[2]) == tuple(mp["spawn"]), "arrived at spawn 0 %s" % (p,))

    def battle():
        f.battle(row)

    def loaded_files():
        f.loaded_files(spec, os.path.join(pkg_dir, "assets"))

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
            check(ok and screen() == row, "walked the meadow to %s (at %s)" % (t, p))
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
        check(0 < gap <= 300 and not wet,
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
        check(go(spot, lambda: near(*spot, r=150), limit=60, vec=vec), "behind the bush island %s" % (pos(TAMER),))
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
        f.keep_card(name, cards, out)

    def boss_fresh(when):
        """The map's record is what the game spawned, at full strength."""
        rec, npc = spec["boss"]["record"], struct.unpack("<I", g.d.read(NPC0, 4))[0]
        st = g.d.read(NPC_STATS, 0x18)
        p = pos(npc) if 0x80000000 <= npc < 0x80200000 else None
        hp = list(struct.unpack_from("<4h", st, 0x10))
        check(p is not None and (p[0], p[2]) == (spec["boss"]["at"][0], spec["boss"]["at"][2])
              and hp == rec[11:15] and list(struct.unpack_from("<4h", st)) == rec[15:19]
              and list(st[8:16]) == rec[26:30] + rec[22:26] and g.d.read(NPC_ON_MAP, 1)[0] == 1,
              "%s: the boss stands at %s, HP %d/%d, moves %s" % (when, p, hp[2], hp[0], list(st[12:16])))

    def talk(vec):
        f.talk(vec, spec["boss"]["at"], tuple(spec["meadow"][1]))

    def win():
        f.win(spec["boss"]["record"], row, a.brains, not a.no_end_fix)

    def flee():
        f.flee(mp["host_row"], mp["host_at"])

    def lose():
        f.lose(row)

    def measure_fight():
        hp, mp_, off, de, spd, brain = (int(v) for v in a.stats.split(","))
        g.warp(mp["host_row"], spec["host"]["exit"])
        for field, value in (("remaining_lifetime", 9999), ("poop_level", 9999), ("tiredness", 0), ("script_sync_bit", 1),
                             ("evolution_target", 12)):      # MetalGreymon
            g.set(field, value)
        g.set_atomic(("tamer_state", 6), ("tamer_substate", 0))
        g.set_atomic(("partner_state", 13), ("partner_sub_state", 0))
        check(wait_for(lambda: g.partner_type == 12 and g.evolution_target == -1
                       and (g.partner_state == 1 or g.tamer_state == 20), 240), "the partner evolved")
        for _ in range(12):
            if g.tamer_state == 0 and g.partner_state == 1:
                break
            g.route("4:cross,45:none")
        g.settle()
        wait_for(lambda: g.tamer_state == 0 and i32(IS_SCRIPT_PAUSED) == 1, 30)
        g.advance(120)                                 # the evolution's last box has closed
        g.d.write_bytes(struct.pack("<4h", off, de, spd, brain), PARTNER_STATS)
        # Buster Dive, Pulse Laser, Metal Sprinter, and its finisher: animation 46 + the slot in its move list
        g.d.write_bytes(bytes([34, 33, 33, 0, 50, 54, 56, 60]), PARTNER_STATS + 8)
        g.d.write_bytes(struct.pack("<4h", hp, mp_, hp, mp_), PARTNER_MAX_HP)
        g.warp(row, 0)                                 # coming back from the glade a big partner lands in the entrance's
        vec = calibrate()                              # lane, as it would at any stock exit: not what this measures
        talk(vec)
        rec = spec["boss"]["record"]
        h = lambda addr: struct.unpack("<h", g.d.read(addr, 2))[0]
        picks, ours, last, last_p, dry, printed, boss_hp, partner_hp = {}, 0, 0, 0, None, 0, rec[11], hp
        heals = 0
        t0 = time.time()
        while time.time() - t0 < 600 and gstate() == 1:
            anim, t = g.d.read(NPC_ANIM, 1)[0], time.time() - t0
            if anim in rec[22:26] and anim != last:
                picks[anim] = picks.get(anim, 0) + 1
            last = anim
            mine = g.d.read(PARTNER_ANIM, 1)[0]
            ours += mine == 60 and last_p != 60          # our finisher
            last_p = mine
            now = h(NPC_STATS + 0x14), h(PARTNER_MAX_HP + 4)
            if gstate() == 1:                            # still fighting: the game restores them at the end
                boss_hp, partner_hp = now
                if heals < a.heal and 0 < partner_hp < 2500:
                    g.d.write_bytes(struct.pack("<h", min(hp, partner_hp + 5000)), PARTNER_MAX_HP + 4)
                    heals += 1
            if dry is None and h(NPC_STATS + 0x16) < 96:
                dry = round(t)
            if t - printed >= 15:
                printed = t
                print("  %3ds: boss HP %d MP %d, partner HP %d MP %d, picks %s"
                      % (t, h(NPC_STATS + 0x14), h(NPC_STATS + 0x16), h(PARTNER_MAX_HP + 4), h(PARTNER_MAX_HP + 6), picks), flush=True)
            g.route("2:square,2:none", poll_s=0.02)   # the finisher, whenever its meter is full
        secs = round(time.time() - t0)
        shot("measure_end")
        print("MEASURE fight: partner %s: %ds, boss HP left %d, partner HP left %d, heals %d, boss picks %s, our finishers %d, MP ran out at %s"
              % (a.stats, secs, boss_hp, partner_hp, heals, picks, ours, dry), flush=True)
        end = time.time() + 300
        while time.time() < end and not (gstate() == 0 and g.tamer_state == 0 and i32(IS_SCRIPT_PAUSED) == 1):
            g.route("4:cross,20:none")
        over = gstate() == 0 and g.tamer_state == 0
        print("MEASURE outcome: %s (map %d)" % ("unfinished" if not over else "won" if screen() == row else "lost", screen()), flush=True)

    def fight():
        def stage(*steps):                             # each step runs on what the last left: stop at a failure
            for step in steps:
                if fails:
                    raise SystemExit("RESULT newmap glade fight: FAIL %s" % fails)
                step()

        vec = {}
        head = [lambda: g.warp(mp["host_row"], spec["host"]["exit"]), walk_in, loaded_files, lambda: shot("arrival"),
                lambda: boss_fresh("on arrival"), lambda: vec.update(calibrate()),
              lambda: a.brains and (g.d.write_bytes(struct.pack("<h", a.brains), PARTNER_STATS + 6),
                                    g.d.write_bytes(struct.pack("<h", 999), NPC_STATS + 6)),   # a sure gain
                lambda: talk(vec), win,
                loaded_files,                          # the battle left the map's art and collision as they were
                lambda: measure("after_win"), save_here]
        stage(*head, walk_out,
              lambda: wait_for(lambda: i32(IS_SCRIPT_PAUSED) == 1, 20), lambda: g.advance(60),
              walk_in, lambda: boss_fresh("back on the next visit"), lambda: talk(vec), flee,
              walk_in, lambda: boss_fresh("after fleeing"), lambda: talk(vec), lose,
              lambda: save_here(on_new_map=False, anywhere=True),
              lambda: g.warp(mp["host_row"], spec["host"]["exit"]), walk_in, lambda: boss_fresh("after losing"))
        g.warp(ROW_122, 0)                             # the other map on DG script 118
        g.advance(120)
        f0 = g.d.frame()
        time.sleep(2)
        check(screen() == ROW_122 and gstate() == 0 and g.d.frame() > f0 + 60, "map %d still plays" % ROW_122)
        shot("row122")

    def save_here(on_new_map=True, anywhere=False):
        def judge(s0, main_state, at):
            px, py, pz = at
            if a.map == "glade":
                check(239 not in s0[0x24:0x24 + 0x190:4], "no poop pile on the card names map 239")
            if anywhere:
                check(main_state == 1 and s0[0x49A] != 239 and s0[0x49B] != 239,
                      "card save: map %d (before: map %d) at %s" % (s0[0x49A], s0[0x49B], (px, py, pz)))
            elif a.map == "glade":                         # no card names the new map, or the new exit
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

        # a pile dropped here, in the table's last slot
        f.save_here(card_dir, out, judge, bytes([239, 50, 50, 5]) if on_new_map and a.map == "glade" else None)


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
        check(g.map_record(row)["file"] == mp["file"], "row %d holds %s (is %s enabled?)" % (row, mp["file"], mp["package"]))
        if a.mode == "measure":
            measure_fight()
        elif a.mode == "fight":
            fight()
        elif a.mode == "run1":
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
