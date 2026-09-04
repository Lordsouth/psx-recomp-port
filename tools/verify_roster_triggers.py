#!/usr/bin/env python3
"""Prove the roster's raised-flags no longer collide with the game's story flags.

Background in dw_roster_flags.c. In short: the roster used to record a new
species by setting trigger id `species + 0x200`, and 88 of the 117 ids that
widening reached are used by the shipped disc scripts -- mostly the "Jijimon
announced that X joined the city" family. The flags go to the memory card, so
raising a new species silently consumed a story scene, and seeing an
announcement made hasDigimonRaised() true for a species the player never owned,
which evolution.c uses to score evolution targets.

This drives the guest and reads the actual bits.

  1. Note every trigger bit before anything happens.
  2. Force an evolution into a roster species, which calls setDigimonRaised.
  3. Assert the OLD id (species + 0x200) did not change, and the NEW banked id
     (0x2CD + species - 63) went from 0 to 1.
  4. Assert nothing else in the story bands moved, so the re-bank did not just
     move the collision somewhere new. Every changed bit is listed either way.

The bit address is the guest's own arithmetic, transcribed from the helper at
0x80106CA8 that both setTrigger and isTriggerSet call:
    byte = *SCRIPT_STATE_PTR + 0xF5 + (id >> 3),  mask = 1 << (id & 7)

    py -3.12 tools/verify_roster_triggers.py --port 4392

Exit 0 = re-banked cleanly, 3 = a story flag moved or the new bit did not set.
"""
import argparse
import json
import os
import shutil
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dwgame import Game                                    # noqa: E402

SCRIPT_STATE_PTR = 0x80134FB8
TRIGGERS_OFFSET = 0xF5
TRIGGER_MAX = 799
STOCK_SPECIES, STOCK_BASE, BANK_BASE = 63, 0x200, 0x2CD
DETECT_LUI = 0x800E2610


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=4392)
    ap.add_argument("--species", type=int, default=114)
    ap.add_argument("--setup-type", type=int, default=5)
    ap.add_argument("--out", default=None)
    ap.add_argument("--cards", default=None)
    a = ap.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = a.out or os.path.join(root, "build-debug", "roster_trig_%d" % a.port)
    os.makedirs(out, exist_ok=True)
    cards = a.cards or os.path.join(out, "cards")
    os.makedirs(cards, exist_ok=True)
    if not os.path.exists(os.path.join(cards, "card1.mcd")):
        src = os.path.join(root, "saves")
        got = False
        for f in ("card1.mcd", "card2.mcd"):
            if os.path.exists(os.path.join(src, f)):
                shutil.copy(os.path.join(src, f), os.path.join(cards, f))
                got = True
        if not got:
            print("No memory card to seed from at %s" % src, file=sys.stderr)
            return 2

    g = Game.launch(port=a.port, memcard_dir=cards,
                    log_path=os.path.join(out, "game.log"),
                    env={"PSX_OVERLAY_CAPTURES": os.path.join(out, "captures.json")})

    def triggers():
        state = struct.unpack("<I", g.d.read(SCRIPT_STATE_PTR, 4))[0]
        raw = g.d.read(state + TRIGGERS_OFFSET, (TRIGGER_MAX // 8) + 1)
        return [(raw[i >> 3] >> (i & 7)) & 1 for i in range(TRIGGER_MAX + 1)]

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

    rc = 0
    try:
        print("partner", g.enter_game(), flush=True)
        if struct.unpack("<H", g.d.read(DETECT_LUI, 2))[0] != 0x9F00:
            print("ABORT: the roster mod is not in the active plan")
            return 2
        old_id = STOCK_BASE + a.species
        new_id = BANK_BASE + (a.species - STOCK_SPECIES)
        print("species %d: old id 0x%03X, re-banked id 0x%03X"
              % (a.species, old_id, new_id), flush=True)

        before = triggers()
        print("before: %d bits set; old id=%d new id=%d"
              % (sum(before), before[old_id], before[new_id]), flush=True)

        if not force(a.setup_type):
            print("setup evolution failed")
            return 2
        g.advance(150)
        for _ in range(60):
            if g.tamer_state == 0 and g.partner_state == 1:
                break
            g.route("4:cross,30:none")
        g.set("evolution_target", -1)
        mid = triggers()
        if not force(a.species):
            print("could not evolve into %d" % a.species)
            return 2
        g.advance(240)
        after = triggers()

        changed = [i for i in range(TRIGGER_MAX + 1) if after[i] != before[i]]
        print("after: %d bits set; %d bits changed: %s"
              % (sum(after), len(changed), ["0x%03X" % c for c in changed]), flush=True)

        ok_new = after[new_id] == 1 and before[new_id] == 0
        ok_old = after[old_id] == before[old_id]
        print("CHECK re-banked bit 0x%03X went 0 -> 1: %s"
              % (new_id, "PASS" if ok_new else "FAIL"), flush=True)
        print("CHECK story-band bit 0x%03X untouched: %s"
              % (old_id, "PASS" if ok_old else "FAIL -- still colliding"), flush=True)

        # nothing else in the two families the audit named may have moved
        story = [c for c in changed if 0x23F <= c <= 0x2CC]
        print("CHECK no other id in 0x23F..0x2CC moved: %s%s"
              % ("PASS" if not story else "FAIL",
                 "" if not story else " -- %s" % ["0x%03X" % c for c in story]),
              flush=True)
        if not (ok_new and ok_old and not story):
            rc = 3
        json.dump({"species": a.species, "old_id": old_id, "new_id": new_id,
                   "changed": ["0x%03X" % c for c in changed],
                   "mid_set": sum(mid), "before_set": sum(before),
                   "after_set": sum(after), "pass": rc == 0},
                  open(os.path.join(out, "result.json"), "w"), indent=1)
    finally:
        g.shutdown()
    return rc


if __name__ == "__main__":
    sys.exit(main())
