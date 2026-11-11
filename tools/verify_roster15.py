#!/usr/bin/env python3
"""Expanded Roster on Digimon World 1.5 (roster-15, tools/gen_roster_dw15.py), in the game.

With 1.5 and the plain Expanded Roster ticked, the session must run roster-15.
Then a requirement-driven evolution, as in verify_roster_evolution.py: the partner
is made a Champion source, its stats are shaped so only the roster species
qualifies, and 1.5's OWN chooser must pick it -- this never writes the target.
The evolution must finish with the partner as that species, and the raised flag
must land in the roster bank (0x296 + species), not on 1.5's 0x262 + species,
which for these species is a treasure-chest id.

    cmake --build build-debug --target psx-runtime
    py -3.12 tools/verify_roster15.py --seed <card dir> [--source 7 --target 64]
"""
import argparse
import os
import shutil
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dwgame import Game  # noqa: E402

DIGIMON_DATA, DD_STRIDE = 0x801F4300, 0x34                # 1.5's live table
EVO_REQ, EVO_PATHS = 0x80089020, 0x8008CF20               # MODTBL, resident in the world
RAISE, TRN_ANIM = 0x801FA01C, 0x8012ED14
SCRIPT_STATE_PTR, TRIGGERS_OFFSET = 0x80134FB8, 0xF5
GAME_STATE, CURRENT_SCREEN = 0x80134F0A, 0x80134DA8
REQF = ["digimon", "hp", "mp", "offense", "defense", "speed", "brain", "care",
        "weight", "discipline", "happiness", "battles", "techs"]
STATE = ('format_version = 2\n\n[[package]]\nid = "dw.1_5"\nversion = "1.0.0"\n\n'
         '[[package]]\nid = "dw.vanilla.roster"\nversion = "1.0.0"\n\n'
         '[[feature]]\npackage_id = "dw.1_5"\nid = "dw15"\nenabled = true\n\n'
         '[[feature]]\npackage_id = "dw.vanilla.roster"\nid = "roster"\nenabled = true\n')


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=int, default=7, help="the Champion to evolve from (7 Airdramon)")
    ap.add_argument("--target", type=int, default=64, help="the roster species it must become (64 Gigadramon)")
    ap.add_argument("--build", default=os.path.join(root, "build-debug"))
    ap.add_argument("--port", type=int, default=4433)
    ap.add_argument("--seed", default=os.path.join(root, "saves"))
    a = ap.parse_args()
    out = os.path.join(a.build, "roster15_%d" % a.target)
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(os.path.join(out, "cards", "dw15"))
    for n in ("card1.mcd", "card2.mcd"):
        if os.path.exists(os.path.join(a.seed, n)):
            shutil.copy(os.path.join(a.seed, n), os.path.join(out, "cards", "dw15", n))
    if not os.path.exists(os.path.join(out, "cards", "dw15", "card1.mcd")):
        print("No memory card to seed from at %s" % a.seed, file=sys.stderr)
        return 2
    with open(os.path.join(a.build, "mods", "state.toml"), "w") as fh:
        fh.write(STATE)

    fails = []

    def check(cond, msg):
        print(("PASS " if cond else "FAIL ") + msg, flush=True)
        if not cond:
            fails.append(msg)

    log = os.path.join(out, "game.log")
    g = Game.launch(exe=os.path.join(a.build, "Digimon_World_Recompiled.exe"), port=a.port,
                    memcard_dir=os.path.join(out, "cards"), extra_args=["--headless-opengl"], log_path=log)
    rd = lambda addr, n: g.d.read(addr, n)
    i32 = lambda addr: struct.unpack("<i", rd(addr, 4))[0]
    gstate = lambda: struct.unpack("<b", rd(GAME_STATE, 1))[0]
    name = lambda t: rd(DIGIMON_DATA + t * DD_STRIDE, 20).split(b"\0")[0].decode("ascii", "replace")
    level = lambda t: rd(DIGIMON_DATA + t * DD_STRIDE + 29, 1)[0]

    def req(t):
        v = struct.unpack("<13hBB", rd(EVO_REQ + t * 28, 28))
        r = dict(zip(REQF, v[:13]))
        r["flags"] = v[13]
        return r

    def to(t):
        return [x for x in struct.unpack("<7h", rd(EVO_PATHS + (t - 1) * 24 + 10, 14)) if x != -1]

    def triggers():
        state = struct.unpack("<I", rd(SCRIPT_STATE_PTR, 4))[0]
        raw = rd(state + TRIGGERS_OFFSET, 100)
        return lambda i: (raw[i >> 3] >> (i & 7)) & 1

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

    def settle():
        g.advance(180)
        for _ in range(60):
            if g.tamer_state == 0 and g.partner_state == 1:
                break
            g.route("4:cross,30:none")

    try:
        free = lambda: (g.partner_type and g.tamer_state == 0 and gstate() == 0
                        and rd(CURRENT_SCREEN, 1)[0] != 238 and i32(0x80134FF4) == 1)
        for _ in range(12):
            g.route("6:start,150:none")
        g.advance(120)
        g.route("6:down,90:none")
        for i in range(600):                           # CONTINUE, then through Jijimon's intro
            g.route("6:cross,30:none")
            if free() and i % 5 == 0:
                g.advance(120)
                if free():
                    break
        check(bool(free()), "in the world")
        text = open(log, errors="replace").read()
        check("dw.vanilla.roster/roster-15 activated implicitly" in text,
              "the session runs roster-15 in place of the Expanded Roster")

        t, s = a.target, a.source
        check(a.target in to(s), "%s's evolutions include %s: %s" % (name(s), name(t), [name(x) for x in to(s)]))
        check(rd(RAISE + 111 * 28, 28) == rd(RAISE + 32 * 28, 28), "Tentomon's raise data is Patamon's (its donor)")
        check(rd(TRN_ANIM + 111 * 2, 2) == rd(TRN_ANIM + 31 * 2, 2), "Tentomon trains with Patamon's animation")

        check(force(s), "setup: the partner is %s" % name(s))
        settle()
        g.set("evolution_target", -1)
        rt = req(t)
        print("target %d %s req %s" % (t, name(t), {k: v for k, v in rt.items() if v != -1}), flush=True)
        for c in to(s):
            print("   candidate %3d %-14s lv%d %s" % (c, name(c), level(c),
                  {k: v for k, v in req(c).items() if v != -1 and k != "flags"}), flush=True)
        # Meet the target's stats, care and weight (3 of the 4 points), and deny every
        # bonus point -- no battles, low discipline and happiness -- so a candidate
        # with another weight window cannot reach 3 (calculateRequirementScore).
        st = {"hp": max(rt["hp"], 1) * 10, "mp": max(rt["mp"], 1) * 10, "off": max(rt["offense"], 5),
              "def": max(rt["defense"], 5), "speed": max(rt["speed"], 5), "brain": max(rt["brain"], 5)}
        if level(t) == 3:      # a Rookie: its point is the stat marked 1 being the partner's highest
            keys = [("hp", "hp", 10), ("mp", "mp", 10), ("off", "offense", 1), ("def", "defense", 1),
                    ("speed", "speed", 1), ("brain", "brain", 1)]
            st = {k: (100 if rt[f] == 1 else 10) * m for k, f, m in keys}
        pa = {"care_mistakes": max(rt["care"], 0), "weight": max(rt["weight"], 0), "discipline": 0,
              "happiness": 0, "battles": 0}
        for k, v in list(st.items()) + list(pa.items()):
            g.set(k, v)
        g.set("remaining_lifetime", 9999)
        g.set("poop_level", 0)
        g.set("tiredness", 0)
        flag = triggers()
        before = (flag(0x296 + t), flag(0x262 + t))
        g.set("evo_timer", 200)                        # a Champion evolves at >= 144
        chosen = None
        for _ in range(120):
            g.advance(20)
            if g.evolution_target != -1:
                chosen = g.evolution_target
                break
        check(chosen == t, "1.5's chooser picked %s (wrote %s)" % (name(t), chosen))
        if chosen == t:
            end = time.time() + 120
            while time.time() < end and g.partner_type != t:
                g.advance(30)
            check(g.partner_type == t, "the evolution finished: the partner is %s" % name(g.partner_type))
            settle()
            g.artifact_shot(os.path.join(out, "after.png"))
            check(g.tamer_state == 0 and g.partner_state == 1 and gstate() == 0, "control is back after it")
            flag = triggers()
            print("raised flags before (bank, 1.5's) %s, after %s" % (before, (flag(0x296 + t), flag(0x262 + t))))
            check(flag(0x296 + t) == 1, "raised flag 0x%X (the roster bank) is set" % (0x296 + t))
            check(flag(0x262 + t) == before[1], "1.5's id 0x%X, a treasure chest, is untouched" % (0x262 + t))
            f0 = g.d.frame()
            time.sleep(2)
            check(g.d.frame() > f0 + 60, "the game runs on")
    finally:
        g.shutdown()
        print("RESULT roster15 %d: %s" % (a.target, "FAIL %s" % fails if fails else "PASS"), flush=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
