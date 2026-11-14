#!/usr/bin/env python3
"""Does the roster's Tankmon fix land, and what does the game then start the partner on? (docs/GAME.md 12.2)

    py -3.12 tools/verify_roster_moves.py --seed <card dir> [--port N] [--control]
    py -3.12 tools/verify_roster_moves.py --seed <card dir> --dw15 --port N

Expanded Roster on, stock disc: reads the guest's DIGIMON_DATA[76].moves[0] (1 with the fix), evolves the partner to
Tankmon (76) with the game's own evolution (the way Field.measure forces one), reads back the starting move the
evolution chose (animation 0x2F = Red Inferno with the fix), then fights the wild pair on DGHA01 as
verify_endfix15.py reaches it, partner topped up, and prints the foes' HP and the partner's animation at each change.
--control writes the old byte (2, Spit Fire) into the guest first, which is the fix reverted: the evolution then
chooses animation 0x2E.
The foes' HP is printed, not judged: it falls with the old byte, and with a partner whose moves are all empty
(--anim 255), because the two foes and the partner's other techs hurt them too.
--anim sets the partner's first move to that animation id before the battle loads its effects (46 + its slot in
DIGIMON_DATA.moves, e.g. 53 for ToyAgumon's moves[7] with --species 84).
--dw15 is Digimon World 1.5 with the roster's 1.5 edition: it reads the byte at 1.5's table and stops.

Verdicts are guest-memory reads. It writes build-debug/mods/state.toml.
"""
import argparse
import os
import shutil
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dwgame import Game  # noqa: E402
from dwdbg import Wedge  # noqa: E402
from field_drive import (  # noqa: E402
    Field, IS_SCRIPT_PAUSED, NPC_BASE, NPC_SIZE, PARTNER_ANIM, PARTNER_MAX_HP, PARTNER_STATS, SPECIES, SPECIES_15, TAMER)

STATE = ('format_version = 2\n\n[[package]]\nid = "dw.vanilla.roster"\nversion = "1.0.0"\n\n'
         '[[feature]]\npackage_id = "dw.vanilla.roster"\nid = "roster"\nenabled = true\n')
DW15 = ('\n[[package]]\nid = "dw.1_5"\nversion = "1.0.0"\n\n'
        '[[feature]]\npackage_id = "dw.1_5"\nid = "dw15"\nenabled = true\n')
ARENA_ROW, HOST_ROW = 105, 1                       # BETL01: two Tentomon, autotalk into a fight; Coela Point
MOVES_AT = 35                                      # DIGIMON_DATA.moves in a 52-byte record


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", required=True)
    ap.add_argument("--port", type=int, default=7100)
    ap.add_argument("--build", default=os.path.join(root, "build-debug"))
    ap.add_argument("--species", type=int, default=76)
    ap.add_argument("--anim", type=int, help="the partner's first move, as an animation id")
    ap.add_argument("--control", action="store_true", help="write the old move byte first (the fix reverted)")
    ap.add_argument("--dw15", action="store_true")
    ap.add_argument("--seconds", type=int, default=40)
    a = ap.parse_args()
    out = os.path.join(a.build, "moves")
    cards = os.path.join(out, "c")
    card_dir = os.path.join(cards, "dw15") if a.dw15 else cards
    shutil.rmtree(cards, ignore_errors=True)
    os.makedirs(card_dir)
    for name in ("card1.mcd", "card2.mcd"):
        if os.path.exists(os.path.join(a.seed, name)):
            shutil.copy(os.path.join(a.seed, name), card_dir)
    with open(os.path.join(a.build, "mods", "state.toml"), "w") as fh:
        fh.write(STATE + (DW15 if a.dw15 else ""))
    fails = []

    def check(cond, msg):
        print(("PASS " if cond else "FAIL ") + msg, flush=True)
        if not cond:
            fails.append(msg)

    g = Game.launch(exe=os.path.join(a.build, "Digimon_World_Recompiled.exe"), port=a.port, memcard_dir=cards,
                    extra_args=["--headless-opengl"], log_path=os.path.join(out, "run.log"))
    f = Field(g, check, None, a.dw15)
    byte_at = (SPECIES_15 if a.dw15 else SPECIES) + a.species * 52 + MOVES_AT
    try:
        f.enter()
        g.settle(timeout_frames=1800)
        have = g.d.read(byte_at, 1)[0]
        print("MEASURE DIGIMON_DATA[%d].moves[0] at 0x%08X reads %d" % (a.species, byte_at, have), flush=True)
        if a.dw15:
            check(have == 1, "1.5: the roster's fix is in the table (moves[0] = %d)" % have)
            return 1 if fails else 0
        if a.control:
            g.d.write_bytes(bytes([2]), byte_at)
        check(g.d.read(byte_at, 1)[0] == (2 if a.control else have), "moves[0] is %d for this run" % g.d.read(byte_at, 1)[0])
        if not a.control:
            check(have == 1, "the roster's patch is on: moves[0] reads 1, not stock 2")

        # the game's own evolution into the species, as Field.measure does it
        g.warp(HOST_ROW, 0)
        for field, value in (("remaining_lifetime", 9999), ("poop_level", 9999), ("tiredness", 0), ("script_sync_bit", 1),
                             ("evolution_target", a.species)):
            g.set(field, value)
        g.set_atomic(("tamer_state", 6), ("tamer_substate", 0))
        g.set_atomic(("partner_state", 13), ("partner_sub_state", 0))
        check(f.wait_for(lambda: g.partner_type == a.species and g.evolution_target == -1
                         and (g.partner_state == 1 or g.tamer_state == 20), 240), "the partner evolved into %d" % a.species)
        for _ in range(12):
            if g.tamer_state == 0 and g.partner_state == 1:
                break
            g.route("4:cross,45:none")
        g.settle()
        f.wait_for(lambda: g.tamer_state == 0 and f.i32(IS_SCRIPT_PAUSED) == 1, 30)
        g.advance(120)
        moves = list(g.d.read(PARTNER_STATS + 12, 4))
        print("MEASURE the evolution's starting moves (animation ids): %s" % [hex(m) for m in moves], flush=True)
        if a.anim is not None:
            moves[0] = a.anim
            g.d.write_bytes(bytes(moves), PARTNER_STATS + 12)
        first = moves[0]
        g.d.write_bytes(struct.pack("<4h", 999, 999, 999, 999), PARTNER_STATS)
        g.d.write_bytes(struct.pack("<4h", 9999, 9999, 9999, 9999), PARTNER_MAX_HP)

        # into the fight, as verify_endfix15.py does it: a wild Digimon on DGHA01, the tamer put beside it, confirm pressed
        row = next(r for r in range(256) if g.map_record(r)["file"] == "DGHA01")
        try:
            g.warp(row, 0)
        except Wedge:                                   # an arrival script (tamer state 20) holds the first frames
            pass
        for _ in range(40):
            if g.tamer_state == 0 and f.i32(IS_SCRIPT_PAUSED) == 1:
                break
            g.route("4:cross,20:none")
        g.advance(120)
        wild = [i for i in range(8) if f.npc(i) and f.npc(i)["on_map"] == 1 and f.npc(i)["pos"]]
        check(bool(wild), "a wild Digimon is on DGHA01 (row %d): slots %s" % (row, wild))
        tamer_at = struct.unpack("<I", g.d.read(TAMER + 4, 4))[0] + 120
        end = time.time() + 60
        while time.time() < end and f.gstate() == 0 and wild:
            x, _, z = f.npc(wild[0])["pos"]
            g.d.write_bytes(struct.pack("<3i", x + 100, 0, z + 100), tamer_at)
            g.route("6:cross,20:none")
        check(f.gstate() != 0, "a battle began (game state %d)" % f.gstate())
        check(f.wait_for(lambda: f.gstate() == 2, 60) and f.wait_for(lambda: f.gstate() == 1, 60), "the fight is on")
        # the partner's moves as the battle loaded them
        loaded = list(g.d.read(PARTNER_STATS + 12, 4))
        h = lambda addr: struct.unpack("<h", g.d.read(addr, 2))[0]
        foes = f.enemies()
        hp = lambda: sum(h(NPC_BASE + NPC_SIZE * s + 0x4C) for s in foes)
        hp0, low, anims, t0 = hp(), None, 0, time.time()
        low, last, prev, started = hp0, 0, hp0, t0
        print("MEASURE fight: enemies in slots %s, HP %d, partner's moves %s" % (foes, hp0, [hex(m) for m in loaded]), flush=True)
        while time.time() - t0 < a.seconds and f.gstate() == 1:
            cur = g.d.read(PARTNER_ANIM, 1)[0]
            if cur == first and last != first:
                anims += 1
                started = time.time()
            last = cur
            g.d.write_bytes(struct.pack("<hh", 9999, 9999), PARTNER_MAX_HP + 4)
            now = hp()
            if now != prev:                                 # each change of the foes' HP, and what the partner was doing
                print("  t=%5.1fs foes' HP %d -> %d, partner animation 0x%X, %.1fs after its move %d began"
                      % (time.time() - t0, prev, now, cur, time.time() - started, anims), flush=True)
                prev = now
            low = min(low, now)
            time.sleep(0.05)
        print("MEASURE result: enemy HP %d -> %d, partner played animation 0x%X %d time(s), in %ds"
              % (hp0, low, first, anims, time.time() - t0), flush=True)
        check(a.anim is not None or anims >= 1, "the partner played its starting move (animation 0x%X)" % first)
        # NOT judged: whether the foes' HP fell. It falls with the old byte too, and with a partner whose moves are all
        # empty (--anim 255): the foes and the partner's other techs hurt them, so HP cannot tell a move that hits from
        # one that does not (GAME.md 12.2).
        want = 0x2E if a.control else 0x2F
        check(a.anim is not None or moves[0] == want, "the evolution started the partner on animation 0x%X (read 0x%X)"
              % (want, moves[0]))
    finally:
        g.shutdown()
    print("RESULT roster moves%s: %s" % (" --control" if a.control else "", "FAIL %s" % fails if fails else "PASS"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
