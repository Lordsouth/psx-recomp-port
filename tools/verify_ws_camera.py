#!/usr/bin/env python3
"""Does a scripted camera move finish in widescreen when its target lies past the widened clamp?

Agumon's recruit in Native Forest (map 109, MAYO00; DG script 105 section 6) ends with
"Agumon joins the city!", then MOVE_CAMERA_TO_ENTITY 253 10 and WAIT_FOR_ENTITY 200 (the camera
slot), then the tamer's "Alright, it pays to try." (docs/GAME.md 3.4). The box stays up until that
next box replaces it, so a camera move that never finishes looks like a box that will not close.

    hang     fresh sandbox card, widescreen 16:9: warp to map 109, let Agumon catch the tamer, win the
             scripted fight (HP poked), talk to "Agumon joins the city!". There the tamer is moved so the
             camera's target lies 4 px past the widened left clamp, the camera parked on it and 30 px off
             vertically (the case docs/GAME.md 3.4 describes). Press X: the script must reach the next box.
             Before dw_ws_camera.c's dw_move_parked_at_bound it never did. --off: widescreen off, the control.
    natural  no camera or tamer pokes: arrive on map 109 by each --exits stock exit (1 west, 2 east, 0 north;
             savestate between), optionally walk a --routes pad route, let Agumon catch the tamer, talk to the box,
             press X. Prints each meeting's camera move and stops at the first that never finishes.

    cmake --build build-debug --target psx-runtime
    py -3.12 -I tools/verify_ws_camera.py hang --seed <card dir> [--port N] [--off]
    py -3.12 -I tools/verify_ws_camera.py natural --seed <card dir> [--port N] [--exits 1,2,0] [--routes "300:left;300:right"]

The seed is any card dir whose slot 1 holds a Digimon World file with Agumon not yet recruited (a never-saved
New Game file plays Jijimon's intro first, which this drives through). It writes build/mods/state.toml.
Verdicts are guest-memory reads; screenshots are for a human.
"""
import argparse
import os
import shutil
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dwgame import Game  # noqa: E402
from field_drive import Field, TAMER, IS_SCRIPT_PAUSED, NPC_BASE, NPC_SIZE, BATTLE, CAMERA  # noqa: E402

ROW, SCRIPT, AGUMON_SCRIPT_ID, RECRUITED = 109, 105, 6, 203
BOX_END, NEXT_BOX_END = 0x3F8, 0x468          # script offsets just past the two texts (dis_script.py 105 6)
WALK_OFF = 0x38C                              # after FADEOUT_HUD: Agumon walks off, no box until BOX_END
ACTIVE_SCRIPT, SCRIPT_POINTER, ACTIVE_INSTRUCTION = 0x80134FD8, 0x80134FDC, 0x80134FE8
WAIT_FOR_ENTITY_ID, CURRENT_SCRIPT_ID = 0x80134FA4, 0x80134FC0
CAMERA_SLOT_TYPE = 0x801BE6B4 + 10 * 12       # SCRIPT_MOVEMENT[10].type
CAMERA_REACHED, CAMERA_HAS_TARGET = 0x80134DAC, 0x80134DAD
LIMITS = 0x80134DB6                           # DRAW_OFFSET_LIMIT_X_MAX, X_MIN, Y_MAX, Y_MIN (i16)
DIFF = 0x80134DD4                             # CAMERA_MOVE_DIFF_X, _Y (i16)
DRAWING_OFFSET = 0x80134EC0                   # X, Y (i32)
TRIGGER_BASE = 0x801BDFCD
OP_SHOW_TEXTBOX, OP_WAIT_FOR_ENTITY = 0x1A, 0x4A
WIDE = ('format_version = 2\n\n[[package]]\nid = "psx.enhancement.widescreen"\nversion = "1.0.0"\n\n'
        '[[feature]]\npackage_id = "psx.enhancement.widescreen"\nid = "widescreen"\nenabled = true\n'
        '[feature.values]\naspect = "16:9"\n')
OFF = 'format_version = 2\n'
M = 53                                        # the 16:9 reveal per side: the widened clamp is [M, scroll - M]
PAST, OFF_Y = 4, 30                          # target past the clamp (1..8 hangs, docs/GAME.md 4.6); camera Y offset


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("hang", "natural"))
    ap.add_argument("--off", action="store_true", help="widescreen off: the control")
    ap.add_argument("--exits", default="1,2,0", help="natural: map 109's stock exits to arrive by (1 west, 2 east, 0 north)")
    ap.add_argument("--routes", default="", help="natural: pad routes after each arrival, ';'-separated (frames:button,...)")
    ap.add_argument("--build", default=os.path.join(root, "build-debug"))
    ap.add_argument("--port", type=int, default=4460)
    ap.add_argument("--seed", default=os.path.join(root, "saves"))
    a = ap.parse_args()
    out = os.path.join(a.build, "ws_camera")
    cards = os.path.join(out, "cards")
    tag = "%s_%s" % (a.mode, "off" if a.off else "wide")
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(a.build, "mods", "state.toml"), "w") as fh:
        fh.write(OFF if a.off else WIDE)
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
                    memcard_dir=cards, extra_args=["--headless-opengl"], log_path=os.path.join(out, tag + ".log"))
    shot = lambda n: g.artifact_shot(os.path.join(out, "%s_%s.png" % (tag, n)))
    f = Field(g, check, shot)
    rd = lambda addr, fmt: struct.unpack("<" + fmt, g.d.read(addr, struct.calcsize("<" + fmt)))
    trigger = lambda n: bool(g.d.read(TRIGGER_BASE + n // 8, 1)[0] >> (n % 8) & 1)

    def offset():
        base, ptr = rd(ACTIVE_SCRIPT, "2I")
        return ptr - base

    def state():
        cam = rd(CAMERA, "2h")
        return dict(op=g.d.read(ACTIVE_INSTRUCTION, 1)[0], at=offset(), script=rd(CURRENT_SCRIPT_ID, "h")[0],
                    wait=g.d.read(WAIT_FOR_ENTITY_ID, 1)[0], slot10=g.d.read(CAMERA_SLOT_TYPE, 1)[0],
                    reached=rd(CAMERA_REACHED, "b")[0], has_target=g.d.read(CAMERA_HAS_TARGET, 1)[0],
                    cam=cam, dox=rd(DRAWING_OFFSET, "2i"), diff=rd(DIFF, "2h"), start=rd(DIFF - 4, "2h"), limits=rd(LIMITS, "4h"),
                    t203=trigger(RECRUITED), paused=f.i32(IS_SCRIPT_PAUSED))

    def agumon():
        for s in range(8):
            n = f.npc(s)
            if n and n["script"] == AGUMON_SCRIPT_ID:
                return s, n
        return None, None

    def at_box(end):
        return g.d.read(ACTIVE_INSTRUCTION, 1)[0] == OP_SHOW_TEXTBOX and offset() == end

    def to_the_box():
        """Wait for Agumon's contact, win the fight on 1 HP, talk up to "Agumon joins the city!"."""
        slot, _ = agumon()
        end = time.time() + 120
        while time.time() < end and f.i32(IS_SCRIPT_PAUSED) == 1:
            time.sleep(0.3)                    # followMode 16: he chases the tamer anywhere on the screen
        check(f.i32(IS_SCRIPT_PAUSED) != 1, "Agumon reached the tamer and his talk section started")
        f.partner(999, 9999)
        end = time.time() + 300
        while time.time() < end and not at_box(BOX_END):
            if f.gstate() in BATTLE and slot is not None:
                hp = rd(NPC_BASE + NPC_SIZE * slot + 0x4C, "h")[0]
                if hp > 1:
                    g.d.write_bytes(struct.pack("<h", 1), NPC_BASE + NPC_SIZE * slot + 0x4C)
            if rd(CURRENT_SCRIPT_ID, "h")[0] == SCRIPT and WALK_OFF <= offset() < BOX_END:
                time.sleep(0.2)                # Agumon walks off, then the box: no input needed, and a press could land on it
                continue
            g.route("4:cross,20:none")
        check(at_box(BOX_END), "\"Agumon joins the city!\" is up, waiting for X (%s)" % state())

    def press_and_watch(frames=600):
        g.route("4:cross,20:none")
        f0 = g.d.frame()
        while g.d.frame() < f0 + frames:
            if at_box(NEXT_BOX_END) or f.i32(IS_SCRIPT_PAUSED) == 1:
                return True
            time.sleep(0.1)
        return False

    def tamer_x(x):
        """The animation re-derives the location from anim.locX (<<15) every frame (src/main/anim.c:495): set both."""
        g.d.write_bytes(struct.pack("<i", x << 15), TAMER + 0x10)
        g.d.write_bytes(struct.pack("<i", x), rd(TAMER + 4, "I")[0] + 0x78)

    def target_for(x):
        """The camera X the move aims at with the tamer at world x: X on the box, read the target the first tick
        computed (CAMERA_MOVE_DIFF_X; camera + drawing offset = LIMIT_X_MAX), then back to the box."""
        g.savestate("load", 1)
        tamer_x(x)
        g.route("4:cross,30:none")
        return rd(LIMITS, "h")[0] - rd(DIFF, "h")[0]

    def force(s0):
        """Tamer moved so the camera's target is PAST px beyond the widened left clamp lo = M (vanilla-reachable,
        so the move's own target clamp keeps it), the camera parked on lo and OFF_Y below the top clamp."""
        g.savestate("save", 1)
        kx, ky = rd(LIMITS, "h")[0], rd(LIMITS + 4, "h")[0]
        x0 = f.pos(TAMER)[0]
        pts = [(x, target_for(x)) for x in (x0, x0 - 300)]
        (xa, ca), (xb, cb) = pts
        want = M - PAST
        slope = (cb - ca) / (xb - xa)          # camera px per world unit
        x = round(xa + (want - ca) / slope)
        for _ in range(6):                     # the projection is not linear: walk in on the exact value
            c = target_for(x)
            if c == want:
                break
            step = (want - c) / slope
            x += round(step) if abs(step) >= 1 else (1 if step > 0 else -1)
        print("INFO calibration %s; tamer x %d aims the camera at %d (want %d)" % (pts, x, c, want), flush=True)
        check(0 < M - c <= 8, "the target is %d px past the widened clamp at %d" % (M - c, M))
        g.savestate("load", 1)
        tamer_x(x)
        g.d.write_bytes(struct.pack("<hh", M, OFF_Y), CAMERA)
        g.d.write_bytes(struct.pack("<ii", kx - M, ky - OFF_Y), DRAWING_OFFSET)
        print("INFO forced: %s tamer %s" % (state(), f.pos(TAMER)), flush=True)
        shot("forced")

    def natural():
        """No camera or tamer pokes (Agumon's HP still is, to end the fight): from File City, saved in slot 2,
        arrive on map 109 through each stock exit (warp, the game's own door code), optionally walk a pad route,
        let Agumon catch the tamer, talk to the box, press X. Reports each meeting's camera move and stops at the
        first that never finishes."""
        g.savestate("save", 2)
        runs = [(int(e), r) for e in a.exits.split(",") for r in a.routes.split(";")]
        for i, (e, r) in enumerate(runs):
            g.savestate("load", 2)
            g.warp(ROW, e)
            if r:
                g.route(r + ",4:none", poll_s=0.02)
            to_the_box()
            s0 = state()
            ok = press_and_watch()
            s1 = state()
            kx = s1["limits"][0]
            t = kx - s1["diff"][0]                      # the move's target CAMERA_X
            print("INFO natural %d/%d exit %d route %r: tamer %s, camera at the box %s, target X %d "
                  "(widened clamp %d..%d), dy %d, finished %s" % (i + 1, len(runs), e, r, f.pos(TAMER), s0["cam"], t,
                                                                   M, 2 * kx - 320 - M, s1["diff"][1] - s1["start"][1],
                                                                   ok), flush=True)
            shot("natural_exit%d" % e)
            if not ok:
                check(False, "a natural meeting (exit %d, route %r) left the camera move unfinished: %s" % (e, r, s1))
                return
        print("INFO no natural meeting of %d hung" % len(runs), flush=True)

    try:
        f.enter()
        g.settle(timeout_frames=1800)
        check(not trigger(RECRUITED), "Agumon is not recruited on this card (trigger %d clear)" % RECRUITED)
        if a.mode == "natural":
            natural()
            raise SystemExit
        g.warp(ROW, 0)
        check(f.screen() == ROW and g.map_record(ROW)["file"] == "MAYO00", "on map %d (MAYO00)" % ROW)
        slot, n = agumon()
        check(slot is not None, "Agumon is on the map in NPC slot %s at %s" % (slot, n and n["pos"]))
        if fails:
            raise SystemExit
        to_the_box()
        s0 = state()
        print("INFO at the box: %s tamer %s" % (s0, f.pos(TAMER)), flush=True)
        shot("box")
        if a.mode == "hang":
            force(s0)
        ok = press_and_watch()
        s1 = state()
        shot("after")
        print("INFO after X: %s" % s1, flush=True)
        check(s1["t203"], "X closed the page: trigger %d is set" % RECRUITED)
        check(ok, "the camera move finished and the script reached the tamer's next box (%s)" % s1)
    except SystemExit:
        pass
    finally:
        g.shutdown()
    print("RESULT ws_camera %s: %s" % (tag, "FAIL %s" % fails if fails else "PASS"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
