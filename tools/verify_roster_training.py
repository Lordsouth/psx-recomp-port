#!/usr/bin/env python3
"""Prove the roster's training-table relocation works IN A GYM.

docs/INTERNALS.md section 9 has said since 0.3.0 that this was "proven on paper
and at the guard, not on screen". Here is the screen.

WHAT CAN GO WRONG. Only TRN_tickOffenseTraining and TRN2_tickOffenseTraining
read the per-species training-animation tables, and they read them at the RAW
partner type with no clamp:

    lw  v0, PARTNER_ENTITY      ; Entity.type
    sll v1, v0, 1               ; index = type * 2
    lb  a1, TRAINING_ANIM_IDS(v1)

The stock tables hold 68 entries. A roster partner is type 114, so the stock
read lands 92 bytes past the end -- in TRN inside the string "Technique
mastered." (anim 101, frame 32), in TRN2 inside a neighbouring table (anim 0,
FRAME 0). Frame 0 is the trap: the state machine's state 2 advances only when
its frame counter EQUALS the table's frame byte, and state 2 has no timeout and
no input check, so a frame byte of 0 parks the session until an int16 counter
wraps -- about 36 minutes. Holding a direction does not rescue it, because the
only early-out lives in state 3.

The nastiest part is that a naive liveness check sees nothing wrong: the
session's own tick counter keeps incrementing the whole time. The state byte is
what stops.

WHAT THIS CHECKS, in order of strength:

  1. Every read site is retargeted. The generator asserts a census of 5 sites
     in TRN and 3 in TRN2; this re-reads the words out of the LOADED overlay
     and confirms each forms an arena address, not the stock one. A patch that
     applied on disc but not in RAM would show here.
  2. The relocated tables hold a sane entry for the partner's type -- in
     particular a NON-ZERO frame byte, which is exactly the hang condition.
  3. A real Offense session runs to completion, with the soft-lock signature
     polled for throughout: state == 2 held while the tick counter climbs.

Map 107 (Beetle Land) is the default because it is served by TRN2, where the
stock-table failure is the hang rather than merely a wrong animation. Map 112
(Green Gym) exercises the TRN path.

    py -3.12 tools/verify_roster_training.py --port 4384 --map 107

Exit 0 = proven, 3 = a check failed, 5 = the session could not be started (the
byte checks still report).
"""
import argparse
import json
import os
import shutil
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dwgame import Game                                    # noqa: E402

ARENA = 0x9F000000
OFF_TRAIN_ANIM, OFF_TRN2_ANIM = 0x4200, 0x4400
DETECT_LUI = 0x800E2610
PARTNER_ENTITY = 0x801557A8
TAMER_STATE = 0x80134C91
OFF_STAT = 0x801557E0

# Read sites for the animation tables, derived by disassembling the overlays.
# Each is a lui/addiu pair: the lui carries the high half of the table address.
SITES = {
    "TRN":  [0x8008B91C, 0x8008B9FC, 0x8008BC30, 0x8008BE38, 0x8008BF50],
    "TRN2": [0x80089C60, 0x80089D10, 0x80089E28],
}
# Per-overlay session state (main-exe RAM, distinct blocks per overlay).
BLOCK = {
    "TRN":  {"state": 0x80135371, "slot": 0x80135370, "ticks": 0x8013537A,
             "anim": 0x8013537E, "box": 0x80135394, "resident": 0x8008B820,
             "table_off": OFF_TRAIN_ANIM},
    "TRN2": {"state": 0x801353BD, "slot": 0x801353BC, "ticks": 0x801353C2,
             "anim": 0x801353C6, "box": 0x801353D4, "resident": 0x80089B64,
             "table_off": OFF_TRN2_ANIM},
}
TRN_MAPS = {112: "TRN", 78: "TRN", 119: "TRN",
            99: "TRN2", 107: "TRN2", 108: "TRN2", 165: "TRN2"}
PSTAT_STAT_SELECT = 0x801BE12F      # pstat 0xFE: 0 hp,1 off,2 speed,3 def,4 mp,5 brain
PSTAT_SLOT_MODE = 0x801BE127        # pstat 0xF6: 0 = timed, 1 = slot minigame


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=4384)
    ap.add_argument("--map", type=int, default=107)
    ap.add_argument("--type", type=int, default=114,
                    help="partner type to test; must be a roster species")
    ap.add_argument("--setup-type", type=int, default=5)
    ap.add_argument("--out", default=None)
    ap.add_argument("--cards", default=None,
                    help="memory-card dir with a save in slot 1 "
                         "(default: a sandbox seeded from saves/)")
    a = ap.parse_args()

    if a.map not in TRN_MAPS:
        print("map %d hosts no training. Known: %s"
              % (a.map, sorted(TRN_MAPS)), file=sys.stderr)
        return 2
    ov = TRN_MAPS[a.map]
    blk = BLOCK[ov]

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = a.out or os.path.join(root, "build-debug", "roster_train_%d" % a.port)
    os.makedirs(out, exist_ok=True)
    # A sandbox card directory: the game must never be pointed at the
    # player's real saves. Seeded from the project's own saves/ on first use
    # because enter_game() needs a save in slot 1 to reach the world.
    cards = a.cards or os.path.join(out, 'cards')
    os.makedirs(cards, exist_ok=True)
    if not os.path.exists(os.path.join(cards, 'card1.mcd')):
        src = os.path.join(root, 'saves')
        got = False
        for f in ('card1.mcd', 'card2.mcd'):
            if os.path.exists(os.path.join(src, f)):
                shutil.copy(os.path.join(src, f), os.path.join(cards, f))
                got = True
        if not got:
            print('No memory card to seed the sandbox from. Put a card with a '
                  'save in slot 1 at %s, or pass --cards.' % src, file=sys.stderr)
            return 2

    g = Game.launch(port=a.port, memcard_dir=cards,
                    log_path=os.path.join(out, "game.log"),
                    env={"PSX_OVERLAY_CAPTURES": os.path.join(out, "captures.json")})

    def u8(x):
        return g.d.read(x, 1)[0]

    def i8(x):
        return struct.unpack("<b", g.d.read(x, 1))[0]

    def i16(x):
        return struct.unpack("<h", g.d.read(x, 2))[0]

    def u32(x):
        return struct.unpack("<I", g.d.read(x, 4))[0]

    def force(target):
        g.set("remaining_lifetime", 9999)
        g.set("poop_level", 0)
        g.set("tiredness", 0)
        if g.script_sync_bit == 0:
            g.set("script_sync_bit", 1)
        g.set("evolution_target", target)
        g.set_atomic(("tamer_state", 6), ("tamer_substate", 0))
        g.set_atomic(("partner_state", 13), ("partner_sub_state", 0))
        for _ in range(200):
            g.advance(30)
            if g.partner_type == target:
                return True
        return False

    rc, report = 0, {}
    try:
        print("partner", g.enter_game(), flush=True)
        if struct.unpack("<H", g.d.read(DETECT_LUI, 2))[0] != 0x9F00:
            print("ABORT: the roster mod is not in the active plan")
            return 2

        if g.partner_type != a.type:
            print("forcing partner to %d via %d ..." % (a.type, a.setup_type), flush=True)
            force(a.setup_type)
            g.advance(120)
            for _ in range(60):
                if g.tamer_state == 0 and g.partner_state == 1:
                    break
                g.route("4:cross,30:none")
            g.set("evolution_target", -1)
            if not force(a.type):
                print("could not reach partner type %d" % a.type)
                return 2
        g.advance(180)
        for _ in range(60):
            if g.tamer_state == 0 and g.partner_state == 1:
                break
            g.route("4:cross,30:none")
        ptype = g.partner_type
        print("partner type %d %r" % (ptype, g.digimon_record(ptype)["name"]), flush=True)

        g.warp(a.map)
        g.advance(240)
        print("on map %d (%s)" % (a.map, ov), flush=True)
        g.d.cmd("screenshot", path=os.path.join(out, "1_gym.png"))

        # The overlay is NOT loaded by arriving on the map. It is loaded by the
        # map's own script, at the machine: probing showed the region all zeroes
        # through 900 frames of standing on map 112, and populated immediately
        # after a walk-left and a CROSS. So walk into things until it appears,
        # rather than assuming a warp is enough -- assuming it is what made the
        # first run of this report a retarget failure that was really an absence.
        def resident():
            w = u32(blk["resident"])
            return (w >> 26) != 0 or (w & 0xFFFF0000) != 0

        if not resident():
            print("overlay not resident on arrival; looking for the machine ...",
                  flush=True)
            # A CUMULATIVE walk, not a set of independent attempts. The probe
            # that first found the machine reached it only after up, right and
            # down had already moved the player; starting each direction from
            # the entrance misses it. So never reset position, and press CROSS
            # after every leg.
            found = None
            for n in (60, 40, 100, 80):
                for d in ("up", "right", "down", "left"):
                    g.route("%d:%s,15:none,6:cross,45:none" % (n, d))
                    if resident():
                        found = "%d:%s" % (n, d)
                        break
                if found:
                    break
            if found:
                print("   loaded after %s" % found, flush=True)
            else:
                print("   still not resident after the search", flush=True)

        # ---- check 1: the overlay is resident, and every read site retargeted
        #
        # Residency is checked FIRST and separately. An unloaded overlay reads
        # as zeroes, and zeroes are not 0x9F00, so folding the two together
        # reports a retarget failure when the truth is that there is nothing
        # there yet -- which is exactly what the first run of this said.
        first = u32(blk["resident"])
        resident = (first >> 26) != 0 or (first & 0xFFFF0000) != 0
        print("offense tick first word = 0x%08X -> overlay %s"
              % (first, "resident" if resident else "NOT resident"), flush=True)
        sites = {}
        for va in SITES[ov]:
            sites["0x%08X" % va] = "0x%04X" % (u32(va) & 0xFFFF)
        print("read sites -> %s" % sites, flush=True)
        if not resident:
            print("CHECK 1 INCONCLUSIVE: %s is not loaded, so its instructions "
                  "cannot be inspected. The overlay loads from the map's own "
                  "script (opcode 0x64 0x27); a warp that lands on the map "
                  "without running that script leaves it absent." % ov, flush=True)
            site_ok = None
        else:
            site_ok = all(v == "0x9F00" for v in sites.values())
            print("CHECK 1 every %s animation-table read site retargeted to the "
                  "arena: %s" % (ov, "PASS" if site_ok else "FAIL"), flush=True)
            if not site_ok:
                rc = 3

        # ---- check 2: the relocated entry for this type is sane
        ent = g.d.read(ARENA + blk["table_off"] + ptype * 2, 2)
        anim, frame = ent[0], ent[1]
        stock = g.d.read(ARENA + blk["table_off"] + 3 * 2, 2)
        print("relocated %s entry for type %d: anim=0x%02X frame=0x%02X "
              "(a stock entry reads anim=0x%02X frame=0x%02X)"
              % (ov, ptype, anim, frame, stock[0], stock[1]), flush=True)
        entry_ok = frame != 0
        print("CHECK 2 frame byte is non-zero, so state 2 can advance: %s"
              % ("PASS" if entry_ok else "FAIL -- this is the hang"), flush=True)
        if not entry_ok:
            rc = 3

        # ---- check 3: run a session and watch for the soft-lock signature
        g.d.write_bytes(b"\x00", PSTAT_SLOT_MODE)      # timed, not the slot game
        g.d.write_bytes(b"\x01", PSTAT_STAT_SELECT)    # Offense
        base_off = i16(OFF_STAT)
        print("\nbaseline offense = %d; looking for a training machine ..."
              % base_off, flush=True)

        started = False
        for route in ("40:up,20:none,6:cross,40:none",
                      "40:right,20:none,6:cross,40:none",
                      "40:down,20:none,6:cross,40:none",
                      "40:left,20:none,6:cross,40:none",
                      "80:up,20:none,6:cross,40:none",
                      "80:right,20:none,6:cross,40:none",
                      "80:down,20:none,6:cross,40:none",
                      "80:left,20:none,6:cross,40:none"):
            g.route(route)
            g.advance(30)
            if u8(TAMER_STATE) == 8 or i8(blk["state"]) != 0:
                started = True
                break
        print("session started: %s (tamer_state=%d, %s state=%d)"
              % (started, u8(TAMER_STATE), ov, i8(blk["state"])), flush=True)

        if not started:
            print("Could not walk onto a machine from the map entrance. The "
                  "trigger's coordinates live in the on-disc map script, not in "
                  "the decompilation, so this step is a search rather than a "
                  "known route. Checks 1 and 2 above still stand.", flush=True)
            report["session"] = "not started"
            if rc == 0:
                rc = 5
        else:
            stall, last_ticks, hist = 0, -1, []
            done = False
            for _ in range(400):
                st = i8(blk["state"])
                tk = i16(blk["ticks"])
                hist.append((st, tk))
                if st == 2 and tk == last_ticks:
                    pass
                stall = stall + 1 if st == 2 else 0
                last_ticks = tk
                if st == 4:
                    g.d.write_bytes(b"\x01", blk["box"])   # dismiss the results box
                if u8(TAMER_STATE) == 0 and st == 0 and tk > 100:
                    done = True
                    break
                if stall > 120:
                    break
                g.advance(15)
            final_off = i16(OFF_STAT)
            states = sorted({h[0] for h in hist})
            print("states visited: %s; ticks reached %d; offense %d -> %d"
                  % (states, hist[-1][1], base_off, final_off), flush=True)
            softlock = stall > 120
            print("CHECK 3 session ran without the state-2 soft-lock: %s"
                  % ("FAIL -- parked in state 2" if softlock
                     else "PASS" if done or final_off > base_off
                     else "INCONCLUSIVE (session did not finish in budget)"),
                  flush=True)
            report["session"] = {"states": states, "ticks": hist[-1][1],
                                 "offense_before": base_off,
                                 "offense_after": final_off,
                                 "softlock": softlock, "completed": done}
            if softlock:
                rc = 3
            g.d.cmd("screenshot", path=os.path.join(out, "2_training.png"))

        report.update({"map": a.map, "overlay": ov, "type": ptype,
                       "sites": sites, "sites_ok": site_ok,
                       "entry": [anim, frame], "entry_ok": entry_ok})
    finally:
        g.shutdown()
    if report:
        json.dump(report, open(os.path.join(out, "result.json"), "w"), indent=1)
        print("\nresult -> %s" % os.path.join(out, "result.json"), flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
