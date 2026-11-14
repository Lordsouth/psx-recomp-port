#!/usr/bin/env python3
"""Run real Offense training sessions with stock and roster partners, to completion.

WHAT CAN GO WRONG. Only TRN_tickOffenseTraining and TRN2_tickOffenseTraining
read the per-species training-animation tables (dw_decomp src/trn/trn_off.c,
src/trn2/trn2_off.c; no other training reads them), and they read them at the
RAW partner type with no clamp. The stock tables hold 68 entries, so a roster
partner such as type 114 reads past the end -- in TRN into the string
"Technique mastered." (anim 101, frame 32), in TRN2 into a neighbouring table
(anim 0, FRAME 0). Frame 0 is the trap: state 2 (state 7 for the map-78
machine) advances only when its counter EQUALS the table's frame byte, and has
no timeout and no input escape (the CANCEL early-out lives in states 3 and 8),
so a frame byte of 0 parks the session until an int16 counter wraps. The roster
relocates both tables into the arena; this proves the relocation carries a real
session to its end.

WHERE THE MACHINES ARE. Map 112 (Green Gym) runs DG script 108 (MAPHEAD row
112: SET_SCRIPT 108). Its sections 0x51..0x56 are the six signs; each sets
pstat 0xFE (the stat) and calls routine 7, which starts the session
(src/main/script_interp.c:1682-1723). Offense is pstat 0xFE = 1, section 0x53
("Tree Striking") on map 112, section 0x53 ("Striking", script 103) on map 107
(TRN2) and section 0x53 ("Striking Special", script 75) on map 78.
`py -3.12 tools/dis_script.py 108 0x53` shows it. A section 0x50..0x6F is a
TILE trigger that needs CONFIRM while standing on a cell whose collision byte
is the section number (src/main/tamer.c:707-711), so the test reads the live
collision grid for cells of value 0x53, walks onto the nearest and presses
CROSS: Begin, then Normal (both first in their menus). Only if the greedy walk
cannot reach the cell does it place the tamer on it (reported as "placed").

THE SESSION. state 0 -> 1 (partner walks to the machine) -> 2/3 (or 7/8)
repeating until 1200 ticks -> 4 (results box; CROSS applies the gains at once,
src/trn/trn_hud.c) -> 5 -> 6 -> TRAINING_COMPLETE = 1 and the player has
control. The soft-lock signature is checked exactly: in state 2 or 7 the
counter can only legitimately reach the frame byte (an s8, at most 127), so a
counter past 127 is a parked session.

HP training (the button-mash boulder) is out of scope: it does not read either
table, so a roster species cannot change it.

    py -3.12 tools/verify_roster_training.py all --port 7400 --seed <card dir>
    py -3.12 tools/verify_roster_training.py control --no-roster --seed <card dir>
    py -3.12 tools/verify_roster_training.py plan --plan 112:114,107:114

Modes: control = the card's own (stock) partner on maps 112, 107 and 78;
roster = types 114, 68, 90 on 112, 114 and 68 on 107 (TRN2), 114 on 78;
all = control then roster in one game; plan = --plan MAP:TYPE[,...] (type 0 =
keep the current partner). Exit 0 = every session completed with a gain, 3 = a
check failed, 2 = setup failed.
"""
import argparse
import json
import os
import shutil
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dwgame import Game                                    # noqa: E402
from field_drive import Field, TAMER, HOUR, MAP_COLLISION, IS_SCRIPT_PAUSED  # noqa: E402

ARENA = 0x9F000000
OFF_TRAIN_ANIM, OFF_TRN2_ANIM = 0x4200, 0x4400
DETECT_LUI = 0x800E2610             # roster retarget site; imm reads 0x9F00 when on
OFF_STAT = 0x801557E0
TAMER_STATE = 0x80134C91
OFFENSE_SECTION = 0x53

# Read sites for the animation tables, derived by disassembling the overlays.
# Each is a lui/addiu pair: the lui carries the high half of the table address.
SITES = {
    "TRN":  [0x8008B91C, 0x8008B9FC, 0x8008BC30, 0x8008BE38, 0x8008BF50],
    "TRN2": [0x80089C60, 0x80089D10, 0x80089E28],
}
# Per-overlay session state (main-exe RAM, distinct blocks per overlay).
BLOCK = {
    "TRN":  {"state": 0x80135371, "ticks": 0x8013537A, "anim": 0x8013537E,
             "resident": 0x8008B820, "table_off": OFF_TRAIN_ANIM},
    "TRN2": {"state": 0x801353BD, "ticks": 0x801353C2, "anim": 0x801353C6,
             "resident": 0x80089B64, "table_off": OFF_TRN2_ANIM},
}
# Offense machines: map -> overlay. 112 and 107 tick states 2/3, 78 ticks 7/8.
OFFENSE_MAPS = {112: "TRN", 78: "TRN", 107: "TRN2"}
PLANS = {
    "control": [(112, 0), (107, 0), (78, 0)],
    "roster": [(112, 114), (112, 68), (112, 90), (107, 114), (107, 68), (78, 114)],
}
PLANS["all"] = PLANS["control"] + PLANS["roster"]
ROSTER_STATE = ('format_version = 2\n\n[[package]]\nid = "dw.vanilla.roster"\nversion = "1.0.0"\n\n'
                '[[feature]]\npackage_id = "dw.vanilla.roster"\nid = "roster"\nenabled = true\n')


def tile_trigger(x, z):
    """The collision cell index getTileTrigger reads for world (x, z) (src/main/map_collision.c:32-45)."""
    tx = int(x / 100) + 50 - (x < 0)
    tz = 50 - int(z / 100) - (z > 0)
    return tx + tz * 100


def idle(g):
    return g.tamer_state == 0 and g.partner_state == 1


def fresh(g):
    """Nothing in the raise sim may interrupt: no poop, no tiredness, no old age, daytime."""
    g.set("remaining_lifetime", 9999)
    g.set("poop_level", 0)
    g.set("tiredness", 0)
    g.d.write_bytes(struct.pack("<hh", 12, 0), HOUR)


def force(g, target):
    """Setup only: the evolution sequence with the target supplied (as tools/dwtest.py does)."""
    fresh(g)
    if g.script_sync_bit == 0:
        g.set("script_sync_bit", 1)
    g.set("evolution_target", target)
    g.set_atomic(("tamer_state", 6), ("tamer_substate", 0))
    g.set_atomic(("partner_state", 13), ("partner_sub_state", 0))
    for _ in range(200):
        g.advance(30)
        if g.partner_type == target:
            break
    for _ in range(60):
        if idle(g):
            break
        g.route("4:cross,30:none")
    g.advance(120)
    return g.partner_type == target and idle(g)


def seed_cards(seed, cards):
    """A fresh sandbox card dir from the seed's card1/card2; False when the seed has no card1."""
    shutil.rmtree(cards, ignore_errors=True)
    os.makedirs(cards)
    for f in ("card1.mcd", "card2.mcd"):
        if os.path.exists(os.path.join(seed, f)):
            shutil.copy(os.path.join(seed, f), cards)
    return os.path.exists(os.path.join(cards, "card1.mcd"))


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("control", "roster", "all", "plan"))
    ap.add_argument("--plan", default="", help="plan mode: MAP:TYPE,... (type 0 keeps the partner)")
    ap.add_argument("--no-roster", action="store_true", help="roster off (a stock control only)")
    ap.add_argument("--port", type=int, default=7400)
    ap.add_argument("--build", default=os.path.join(root, "build-debug"))
    ap.add_argument("--seed", default=os.path.join(root, "saves"))
    a = ap.parse_args()
    plan = PLANS.get(a.mode) or [tuple(int(v) for v in p.split(":")) for p in a.plan.split(",") if p]
    if not plan or any(m not in OFFENSE_MAPS for m, _ in plan):
        print("bad plan %r; offense machines are on maps %s" % (plan, sorted(OFFENSE_MAPS)), file=sys.stderr)
        return 2
    if a.no_roster and any(t > 67 for _, t in plan):
        print("a roster type needs the roster on", file=sys.stderr)
        return 2

    out = os.path.join(a.build, "roster_train_%s%s" % (a.mode, "_off" if a.no_roster else ""))
    cards = os.path.join(out, "cards")
    if not seed_cards(a.seed, cards):
        print("No memory card to seed from at %s" % a.seed, file=sys.stderr)
        return 2
    with open(os.path.join(a.build, "mods", "state.toml"), "w") as fh:
        fh.write("format_version = 2\n" if a.no_roster else ROSTER_STATE)

    fails, results = [], []

    def check(cond, msg):
        print(("PASS " if cond else "FAIL ") + msg, flush=True)
        if not cond:
            fails.append(msg)
        return cond

    g = Game.launch(exe=os.path.join(a.build, "Digimon_World_Recompiled.exe"), port=a.port,
                    memcard_dir=cards, extra_args=["--headless-opengl"],
                    log_path=os.path.join(out, "game.log"),
                    env={"PSX_OVERLAY_CAPTURES": os.path.join(out, "captures.json")})
    f = Field(g, check)
    u8 = lambda x: g.d.read(x, 1)[0]
    i8 = lambda x: struct.unpack("<b", g.d.read(x, 1))[0]
    i16 = lambda x: struct.unpack("<h", g.d.read(x, 2))[0]
    u32 = lambda x: struct.unpack("<I", g.d.read(x, 4))[0]
    shot = lambda n: g.artifact_shot(os.path.join(out, n + ".png"))

    def session(row, want):
        ov, blk = OFFENSE_MAPS[row], BLOCK[OFFENSE_MAPS[row]]
        if want and g.partner_type != want and not force(g, want):
            check(False, "map %d: could not set up partner type %d (is %d)" % (row, want, g.partner_type))
            return
        ptype = g.partner_type
        name = g.digimon_record(ptype)["name"]
        tag = "map %d %s, %s (type %d)" % (row, ov, name, ptype)
        if g.current_screen != row:
            g.warp(row)
        g.advance(120)
        fresh(g)
        coll = g.d.read(MAP_COLLISION, 10000)
        cells = [i for i, v in enumerate(coll) if v == OFFENSE_SECTION]
        p = f.pos(TAMER)
        centre = lambda i: ((i % 100 - 50) * 100 + 50, (50 - i // 100) * 100 - 50)
        target = min(cells, key=lambda i: (centre(i)[0] - p[0]) ** 2 + (centre(i)[1] - p[2]) ** 2)
        on = lambda: (lambda q: q is not None and coll[tile_trigger(q[0], q[2])] == OFFENSE_SECTION)(f.pos(TAMER))
        how = "walked" if f.go(centre(target), on, limit=60) else None
        tried = set()
        while how is None and len(tried) < 6:          # an edge cell can sit against the machine: try its neighbours
            q = f.pos(TAMER)
            nxt = min((i for i in cells if i not in tried),
                      key=lambda i: (centre(i)[0] - q[0]) ** 2 + (centre(i)[1] - q[2]) ** 2)
            tried.add(nxt)
            how = "walked" if f.go(centre(nxt), on, limit=20) else None
        if how is None:                                # the walk could not reach it: stand the tamer on the cell
            ptr = u32(TAMER + 4)
            x, z = centre(target)
            g.d.write_bytes(struct.pack("<i", x), ptr + 120)
            g.d.write_bytes(struct.pack("<i", z), ptr + 128)
            g.advance(10)
            how = "placed" if on() else None
        if not check(how is not None, "%s: tamer on an offense trigger cell (%d cells of 0x53, nearest at %s; "
                     "tamer at %s)" % (tag, len(cells), centre(target), f.pos(TAMER))):
            return
        g.advance(20)
        off0, ticks_seen = i16(OFF_STAT), 0
        shot("map%d_type%d_sign" % (row, ptype))
        trace = []                                     # what each press did, for a session that never starts
        for _ in range(30):
            g.route("6:cross,40:none")
            q = f.pos(TAMER)
            trace.append((u8(TAMER_STATE), f.i32(IS_SCRIPT_PAUSED), coll[tile_trigger(q[0], q[2])] if q else None))
            if u8(TAMER_STATE) == 8:
                break
        if u8(TAMER_STATE) != 8:
            print("   presses (tamer_state, IS_SCRIPT_PAUSED, trigger under the tamer): %s" % trace[:12], flush=True)
            shot("map%d_type%d_nostart" % (row, ptype))
        if not check(u8(TAMER_STATE) == 8, "%s: CROSS at the sign started a session (%s there, tamer_state %d)"
                     % (tag, how, u8(TAMER_STATE))):
            return
        resident_word = u32(blk["resident"])
        sites = {"0x%08X" % va: "0x%04X" % (u32(va) & 0xFFFF) for va in SITES[ov]}
        anim, frame = struct.unpack("<Bb", g.d.read(ARENA + blk["table_off"] + ptype * 2, 2)) if not a.no_roster \
            else (None, None)
        if not a.no_roster:
            check(resident_word != 0 and all(v == "0x9F00" for v in sites.values()),
                  "%s: %s resident (first word 0x%08X) and every table read site retargeted to the arena %s"
                  % (tag, ov, resident_word, sites))
            check(frame > 0, "%s: relocated entry anim 0x%02X frame %d (non-zero, so the hit state can advance)"
                  % (tag, anim, frame))
        seen, parked, done, end = [], None, False, g.d.frame() + 9000
        while g.d.frame() < end:
            st, tk, cnt = i8(blk["state"]), i16(blk["ticks"]), i16(blk["anim"])
            if not seen or seen[-1] != st:
                seen.append(st)
            ticks_seen = max(ticks_seen, tk)
            if st in (2, 7) and cnt > 127:              # past any s8 frame byte: parked
                parked = (st, cnt, tk)
                break
            if 6 in seen and st == 0 and u8(TAMER_STATE) == 0:    # state 6 hands control back and resets to 0
                done = True
                break
            if st == 4:
                g.route("4:cross,8:none")              # the results box: CROSS applies the gains at once
            else:
                g.advance(6)
        off1 = i16(OFF_STAT)
        shot("map%d_type%d" % (row, ptype))
        check(parked is None, "%s: never parked in a hit state (states %s)%s"
              % (tag, seen, "" if parked is None else ": PARKED in state %d, counter %d, ticks %d" % parked))
        check(done and ticks_seen >= 1200, "%s: session ran its full 1200 ticks (reached %d) and handed control back"
              % (tag, ticks_seen))
        check(off1 > off0, "%s: Offense %d -> %d (+%d)" % (tag, off0, off1, off1 - off0))
        results.append(dict(map=row, overlay=ov, type=ptype, name=name, reached=how, entry=[anim, frame],
                            states=seen, ticks=ticks_seen, offense=[off0, off1], completed=done,
                            parked=parked, sites=sites))
        g.advance(60)

    rc = 0
    try:
        f.enter()
        on = struct.unpack("<H", g.d.read(DETECT_LUI, 2))[0] == 0x9F00
        if not check(on != a.no_roster, "roster %s in the active plan (retarget site reads 0x%04X)"
                     % ("off" if a.no_roster else "on", struct.unpack("<H", g.d.read(DETECT_LUI, 2))[0])):
            return 2
        print("card partner: type %d %s" % (g.partner_type, g.digimon_record(g.partner_type)["name"]), flush=True)
        for row, t in plan:
            session(row, t)
        rc = 3 if fails else 0
    finally:
        g.shutdown()
        json.dump(dict(mode=a.mode, roster=not a.no_roster, sessions=results, fails=fails),
                  open(os.path.join(out, "result.json"), "w"), indent=1)
        print("\n%d sessions, %d failed checks -> %s" % (len(results), len(fails), os.path.join(out, "result.json")))
    return rc


if __name__ == "__main__":
    sys.exit(main())
