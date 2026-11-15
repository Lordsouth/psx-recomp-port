#!/usr/bin/env python3
"""A warp over an open award box, and the battle-end halt it led to (docs/INTERNALS.md section 9).

A medal earned in the field opens UI box 1 in tamer state 20 (tamerTickAwardSomething). Forcing tamer
state 5 over it (the harness's warp) leaves the box open and the award pending, the award runs again on
the next map, and isUIBoxAvailable(1) answers yes for an open box, so a second world object (0x1A4, 1)
is added. Each close removes one, so one survives: it renders box 1 with its last render callback for
good. After a win that callback is renderBitBox, which calls BTL_renderBattleEndText in the battle
overlay; once the field reloads over the overlay the call lands in map data (FAIL-FAST 0x8006306C).

    py -3.12 tools/verify_warp_award.py --seed <card dir> [--trigger stats|money] [--raw] [--no-fight]

--trigger: stats (default) writes every partner stat to its cap (medal 7), money writes 999999 bits
(medal 13). The write is made in the field just before the warp, as the halting test did.
--raw: warp by flipping tamer state 5 directly, as Game.warp did before it learned to dismiss the box.
This is the control: expect an orphaned box object and, with stats and the fight, the halt. With money
the orphan was there but a later box 1 had set its callback back before the field reloaded: no halt.
Passes when no box object is orphaned and the win hands control back. The seed card must not hold
the medal already.
"""
import argparse, os, re, shutil, struct, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dwdbg
from dwgame import Game
from field_drive import Field, IS_SCRIPT_PAUSED, PARTNER_MAX_HP, PARTNER_STATS, TAMER, MONEY, NPC_BASE, NPC_SIZE

WORLD_OBJECTS, UI_BOX_DATA = 0x80137418, 0x8013D390      # 128+1 x {i16 id, i16 instance, tick, render}; 6 x 0x24
BOX_OBJECT = 0x1A4                                       # createAnimatedUIBox / createStaticUIBox (src/main/ui.c)


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", default=os.path.join(root, "build-debug"))
    ap.add_argument("--port", type=int, default=4433)
    ap.add_argument("--seed", default=os.path.join(root, "saves"))
    ap.add_argument("--out", default=None)
    ap.add_argument("--trigger", choices=("stats", "money"), default="stats")
    ap.add_argument("--raw", action="store_true")
    ap.add_argument("--no-fight", action="store_true")
    a = ap.parse_args()
    out = a.out or os.path.join(a.build, "warp_award")
    cards = os.path.join(out, "c")
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(cards)
    for name in ("card1.mcd", "card2.mcd"):
        if os.path.exists(os.path.join(a.seed, name)):
            shutil.copy(os.path.join(a.seed, name), cards)
    with open(os.path.join(a.build, "mods", "state.toml"), "w") as fh:
        fh.write("format_version = 2\n")                  # nothing ticked: the fault is stock code plus the warp
    fails = []

    def check(cond, msg):
        print(("PASS " if cond else "FAIL ") + msg, flush=True)
        if not cond:
            fails.append(msg)
        return cond

    log = os.path.join(out, "game.log")
    g = Game.launch(exe=os.path.join(a.build, "Digimon_World_Recompiled.exe"), port=a.port, memcard_dir=cards,
                    log_path=log, env={"PSX_OVERLAY_CAPTURES": os.path.join(out, "caps.json")})
    fatal = lambda: re.search(r"FATAL[^\n]*", open(log, errors="replace").read())

    def wait(frames):
        """Game.advance, but it gives up on a halted game, whose frame counter never moves again."""
        target, end = g.d.frame() + frames, time.time() + 30 + frames / 20
        while g.d.frame() < target and time.time() < end and not fatal():
            time.sleep(0.05)

    def press(spec):
        g.route(spec, wait=False)
        wait(sum(int(x.split(":")[0]) for x in spec.split(",")))

    def orphans():
        """Box world objects whose UIBoxData says closed: renderUIBoxStatic draws them, render callback and all."""
        objs, boxes = g.d.read(WORLD_OBJECTS, 128 * 12), g.d.read(UI_BOX_DATA, 6 * 0x24)
        found = []
        for i in range(128):
            oid, inst = struct.unpack_from("<hh", objs, i * 12)
            if oid == BOX_OBJECT and 0 <= inst < 6 and struct.unpack_from("<h", boxes, inst * 0x24 + 18)[0] == 0:
                found.append((inst, "render 0x%08X" % struct.unpack_from("<I", boxes, inst * 0x24 + 0x20)[0]))
        return found

    try:
        f = Field(g, check)
        f.enter()
        g.settle(timeout_frames=1800)
        if a.trigger == "stats":
            g.d.write_bytes(struct.pack("<4h", 999, 999, 999, 999), PARTNER_STATS)
            g.d.write_bytes(struct.pack("<4h", 9999, 9999, 9999, 9999), PARTNER_MAX_HP)
        else:
            g.d.write_bytes(struct.pack("<i", 999999), MONEY)
        row = next(r for r in range(256) if g.map_record(r)["file"] == "DGHA01")
        g.advance(40)                                     # the award box is up 5 game ticks (about 15 frames) after the medal
        check(g.tamer_state == 20, "the medal's award box is up before the warp (tamer state %d)" % g.tamer_state)
        if a.raw:
            g.set("target_map", row)
            g.set("current_exit", 0)
            g.set_atomic(("tamer_state", 5), ("tamer_substate", 0))
        else:
            g.warp(row, 0)
        for _ in range(40):                               # the award, run again on DGHA01 when it was cut off
            if g.current_screen == row and g.tamer_state == 0 and f.i32(IS_SCRIPT_PAUSED) == 1:
                break
            g.route("4:cross,20:none")
        g.advance(60)
        o = orphans()
        print("MEASURE orphaned box objects after the warp: %s" % o, flush=True)
        check(not o, "no box world object outlives its box after the warp")
        if not a.no_fight:
            wild = [i for i in range(8) if f.npc(i) and f.npc(i)["on_map"] == 1 and f.npc(i)["pos"]]
            check(bool(wild), "a wild Digimon on DGHA01: %s" % wild)
            tamer_at = struct.unpack("<I", g.d.read(TAMER + 4, 4))[0] + 120
            end = time.time() + 90
            while time.time() < end and f.gstate() == 0 and wild:
                x, _, z = f.npc(wild[0])["pos"]
                g.d.write_bytes(struct.pack("<3i", x + 100, 0, z + 100), tamer_at)
                press("6:cross,20:none")
            seen, end = set(), time.time() + 400
            while time.time() < end and not fatal():
                s = f.gstate()
                seen.add(s)
                if s == 0 and 1 in seen and g.tamer_state == 0:
                    break
                if s == 1:                                # down to 1 only, the partner's own hit kills
                    for i in range(8):
                        hp = NPC_BASE + i * NPC_SIZE + 0x38 + 0x14
                        if struct.unpack("<h", g.d.read(hp, 2))[0] > 1:
                            g.d.write_bytes(struct.pack("<h", 1), hp)
                    wait(20)
                else:
                    press("4:cross,20:none")
            wait(240)                            # the field reloads over the battle overlay in this time
            m = fatal()
            check(not m, "no halt after the win%s" % (": " + m.group(0)[:120] if m else ""))
            if not m:
                check(1 in seen and f.gstate() == 0 and g.tamer_state == 0, "the win hands control back")
                o = orphans()
                print("MEASURE orphaned box objects after the win: %s" % o, flush=True)
                check(not o, "no box world object outlives its box after the win")
    except (dwdbg.Dead, dwdbg.Wedge) as e:
        m = fatal()
        check(False, "game stopped answering: %s%s" % (e, " / " + m.group(0)[:120] if m else ""))
    finally:
        g.shutdown()
        tag = "%s%s" % (a.trigger, " raw" if a.raw else "")
        print("RESULT warp_award %s: %s" % (tag, "FAIL %s" % fails if fails else "PASS"), flush=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
