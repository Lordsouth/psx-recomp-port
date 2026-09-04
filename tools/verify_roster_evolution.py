#!/usr/bin/env python3
"""Prove the expanded roster two ways the project had never proved it.

Both open claims in docs/INTERNALS.md section 9 were about things nobody had
seen happen, only reasoned about.

(c) REQUIREMENT-DRIVEN SELECTION. Every roster test until now wrote
    EVOLUTION_TARGET itself, so all any of them proved was that a forced
    evolution completes. This never writes it. It shapes the partner's stats so
    the game's OWN chooser must pick a roster species, then reads back what the
    guest wrote.

    The dispatcher (disassembled at 0x800A9150..0x800A9254) runs while
    getTamerState() == 0 and PARTNER_STATE == 1, and for a level-4 partner with
    evoTimer >= 144 calls getChampionEvolutionTarget (0x800E2F08), storing its
    result in EVOLUTION_TARGET (0x80134E50). That chooser scores each of the six
    EVO_PATHS to[] candidates with calculateRequirementScore -- 3 points
    qualifies -- then keeps the qualifier with the highest mean of its REQUIRED
    stats (dw_decomp src/main/evolution.c:164-229 and :301-395).

    Greymon is the default source because the roster wires its to[0] to 114:
    to[] = [114, -1, 12, 26, -1, -1]. Agumon, the fixture partner, has no roster
    target at all -- which is worth knowing on its own, and is why this starts
    with a forced Agumon -> Greymon step. That step is SETUP, an ordinary stock
    evolution. The measurement is the one after it.

(a) MODEL RESIDENCY. dw_roster.c remaps loadMMD's type argument 114 -> 115 so
    slot 114 streams Machinedramon's model. Whether that actually happens had
    never been checked. This searches guest RAM for byte probes taken from the
    model file, with a CONTROL taken while the partner is still Greymon: the
    probe means nothing unless it is absent before and present after.

    Extract the probe file from YOUR OWN vanilla disc image, not from any other
    dump: /CHDAT/MMD3/MUGE.MMD, lba 5844, 78588 bytes. A copy taken from a
    modified disc measured 79774 bytes here and shares no prefix with the
    vanilla one, so probing with it would have failed for a reason that has
    nothing to do with the roster.

The chooser model below is a literal transcription of the guest's, INCLUDING
the quirk that statTotal/statCount are reset only when a candidate wins, so a
qualifying-but-losing candidate leaks into the next one's mean. Agreement
between the model's prediction and the guest's write is part of the evidence:
it means the requirement arithmetic is understood, not merely that something
happened.

    py -3.12 tools/verify_roster_evolution.py --port 4382 --mmd MUGE.MMD

Exit 0 = both proven. 3 = the game chose something else. 4 = the control was
already hit, so the model probe proves nothing and the run is inconclusive.
"""
import argparse
import json
import os
import shutil
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dwgame import Game                                    # noqa: E402

ARENA, OFF_REQ, OFF_PATHS = 0x9F000000, 0x0000, 0x3400
SREQ, SPATH = 28, 11
DETECT_LUI = 0x800E2610          # roster retarget site; imm reads 0x9F00 when on
STOCK_MAX = 62                   # ids above this are roster-authored
REQF = ["digimon", "hp", "mp", "offense", "defense", "speed", "brain", "care",
        "weight", "discipline", "happiness", "battles", "techs"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=4382)
    ap.add_argument("--mmd", default="MUGE.MMD",
                    help="Machinedramon's model, extracted from YOUR vanilla disc")
    ap.add_argument("--setup-type", type=int, default=5,
                    help="stock species to start from; its to[] must hold a roster id")
    ap.add_argument("--out", default=None)
    ap.add_argument("--cards", default=None,
                    help="memory-card dir with a save in slot 1 "
                         "(default: a sandbox seeded from saves/)")
    a = ap.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = a.out or os.path.join(root, "build-debug", "roster_evo_%d" % a.port)
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

    blob = open(a.mmd, "rb").read()
    probes = [("header", blob[:32]), ("interior+0x1000", blob[0x1000:0x1040]),
              ("interior+0x4000", blob[0x4000:0x4040])]

    g = Game.launch(port=a.port, memcard_dir=cards,
                    log_path=os.path.join(out, "game.log"),
                    env={"PSX_OVERLAY_CAPTURES": os.path.join(out, "captures.json")})

    def req(t):
        v = struct.unpack("<13hBB", g.d.read(ARENA + OFF_REQ + t * SREQ, SREQ))
        d = dict(zip(REQF, v[:13]))
        d["flags"] = v[13]
        return d

    def paths(t):
        v = struct.unpack("<11b", g.d.read(ARENA + OFF_PATHS + (t - 1) * SPATH, SPATH))
        return {"from": list(v[:5]), "to": list(v[5:])}

    def nm(t):
        return g.digimon_record(t)["name"]

    def lv(t):
        return g.digimon_record(t)["level"]

    def score(cur, t, r, st, pa):
        """calculateRequirementScore (evolution.c:301-395). getNumMasteredMoves
        is not modelled, so the `techs` bonus is treated as unfulfillable --
        conservative: it can only make the model UNDER-count a candidate."""
        is_max_cm = (r["flags"] & 0x10) >> 4
        is_max_bt = r["flags"] & 1
        p = 0
        if is_max_cm == 0:
            if pa["care_mistakes"] >= r["care"]:
                p += 1
        elif pa["care_mistakes"] <= r["care"]:
            p += 1
        if r["weight"] - 5 <= pa["weight"] <= r["weight"] + 5:
            p += 1
        arr = [st["hp"] // 10, st["mp"] // 10, st["off"], st["def"],
               st["speed"], st["brain"]]
        rq = [r["hp"], r["mp"], r["offense"], r["defense"], r["speed"], r["brain"]]
        if lv(t) == 3:
            hi = None
            for i in range(6):
                if all(arr[i] >= arr[j] for j in range(6)):
                    hi = i
            if hi is not None and rq[hi] == 1:
                p += 1
        elif all(arr[i] >= rq[i] for i in range(6)):
            p += 1
        b = 0
        if r["digimon"] != -1 and cur == r["digimon"]:
            b = 1
        if r["discipline"] != -1 and r["discipline"] <= pa["discipline"]:
            b = 1
        if r["happiness"] != -1 and r["happiness"] <= pa["happiness"]:
            b = 1
        if r["battles"] != -1:
            if is_max_bt == 0:
                if r["battles"] <= pa["battles"]:
                    b = 1
            elif r["battles"] >= pa["battles"]:
                b = 1
        return p + b

    def choose(cur, to, st, pa):
        """getChampionEvolutionTarget (evolution.c:164-229), transcribed."""
        best, best_score = -1, 0
        total = count = 0
        trace = []
        for t in to:
            if t == -1:
                continue
            r = req(t)
            s = score(cur, t, r, st, pa)
            if s < 3:
                trace.append((t, nm(t), s, None))
                continue
            for key, val in (("hp", st["hp"] // 10), ("mp", st["mp"] // 10),
                             ("offense", st["off"]), ("defense", st["def"]),
                             ("speed", st["speed"]), ("brain", st["brain"])):
                if r[key] != -1:
                    total += val
                    count += 1
            total = total // count if count else 0
            trace.append((t, nm(t), s, total))
            if best_score < total:
                best, best_score = t, total
                total = count = 0          # reset ONLY on a win: the guest quirk
        return best, trace

    def ram():
        chunk, addr, buf = 0x40000, 0x80000000, bytearray()
        while addr < 0x80200000:
            n = min(chunk, 0x80200000 - addr)
            buf += g.d.read(addr, n)
            addr += n
        return bytes(buf)

    def probe(tag):
        mem = ram()
        res = {}
        for name, sig in probes:
            hits, i = [], mem.find(sig)
            while i >= 0 and len(hits) < 4:
                hits.append(0x80000000 + i)
                i = mem.find(sig, i + 1)
            res[name] = hits
            print("  %-7s %-16s %s" % (tag, name,
                  ", ".join("0x%08X" % h for h in hits) if hits else "not present"),
                  flush=True)
        return res

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

    rc, result = 0, {}
    try:
        print("partner", g.enter_game(), flush=True)
        lui = struct.unpack("<H", g.d.read(DETECT_LUI, 2))[0]
        print("roster active:", lui == 0x9F00, flush=True)
        if lui != 0x9F00:
            print("ABORT: the roster mod is not in the active plan")
            return 2

        print("\n[setup] forcing a stock evolution into %d %r"
              % (a.setup_type, nm(a.setup_type)), flush=True)
        if not force(a.setup_type):
            print("setup failed; partner is", g.partner_type)
            return 2
        g.advance(180)
        for _ in range(60):
            if g.tamer_state == 0 and g.partner_state == 1:
                break
            g.route("4:cross,30:none")
        g.set("evolution_target", -1)
        cur = g.partner_type
        print("partner now %d %r level %d" % (cur, nm(cur), lv(cur)), flush=True)

        pth = paths(cur)
        to = [t for t in pth["to"] if t != -1]
        print("EVO_PATHS[%d].to = %s" % (cur, pth["to"]), flush=True)
        for t in to:
            print("   cand %3d %-14s lv%d %s" % (t, nm(t), lv(t),
                  {k: v for k, v in req(t).items() if v != -1 and k != "flags"}),
                  flush=True)
        new = [t for t in to if t > STOCK_MAX]
        print("roster-authored candidates:", [(t, nm(t)) for t in new], flush=True)
        if not new:
            print("ABORT: no roster species is reachable from %d; pick another "
                  "--setup-type" % cur)
            return 2
        target = new[0]
        rt = req(target)

        # Meet the target's requirements; hold everything else low so the
        # mean-of-required tie-break cannot favour a stock candidate.
        st = {"hp": (rt["hp"] if rt["hp"] != -1 else 10) * 10,
              "mp": (rt["mp"] if rt["mp"] != -1 else 10) * 10,
              "off": rt["offense"] if rt["offense"] != -1 else 5,
              "def": rt["defense"] if rt["defense"] != -1 else 5,
              "speed": rt["speed"] if rt["speed"] != -1 else 5,
              "brain": rt["brain"] if rt["brain"] != -1 else 5}
        pa = {"care_mistakes": max(rt["care"], 0), "weight": max(rt["weight"], 0),
              "discipline": 100, "happiness": 100,
              "battles": max(rt["battles"], 0) if rt["battles"] != -1 else 0}
        pred, trace = choose(cur, pth["to"], st, pa)
        print("\nmodel trace with the shaped stats:", flush=True)
        for t, name, s, mean in trace:
            print("   %3d %-14s pts=%d mean=%s" % (t, name, s, mean), flush=True)
        print("MODEL PREDICTS:", pred, nm(pred) if pred != -1 else "", flush=True)

        for k, v in st.items():
            g.set(k, v)
        for k, v in pa.items():
            g.set(k, v)
        g.set("remaining_lifetime", 9999)
        g.set("poop_level", 0)
        g.set("tiredness", 0)

        print("\nCONTROL for the model probe (partner is still the setup species):",
              flush=True)
        before = probe("before")
        g.d.cmd("screenshot", path=os.path.join(out, "1_setup.png"))
        if any(before.values()):
            print("CONTROL HIT: the model's bytes are already resident, so this "
                  "probe cannot prove streaming. Reporting inconclusive rather "
                  "than a false positive.", flush=True)
            rc = 4

        print("\nevolution_target before:", g.evolution_target, flush=True)
        g.set("evo_timer", 200)          # Champion threshold is >= 144
        chosen = None
        for _ in range(120):
            g.advance(20)
            if g.evolution_target != -1:
                chosen = g.evolution_target
                break
        print("GAME WROTE evolution_target =", chosen,
              nm(chosen) if chosen not in (None, -1) else "", flush=True)
        ok_c = chosen == pred and chosen is not None and chosen > STOCK_MAX
        print("VERDICT (c) requirement-driven selection: predicted %s, game chose "
              "%s -> %s" % (pred, chosen, "MATCH" if chosen == pred else "MISMATCH"),
              flush=True)
        if not ok_c and rc == 0:
            rc = 3

        after = {}
        if chosen not in (None, -1):
            for _ in range(200):
                g.advance(30)
                if g.partner_type == chosen:
                    break
            print("partner_type after the sequence: %d %r"
                  % (g.partner_type, nm(g.partner_type)), flush=True)
            g.advance(180)
            g.d.cmd("screenshot", path=os.path.join(out, "2_after.png"))
            print("\nTEST for the model probe:", flush=True)
            after = probe("after")
            print("VERDICT (a) model residency: %s%s"
                  % ("bytes ARE resident" if all(after.values())
                     else "bytes are NOT resident",
                      "" if not any(before.values()) else " (INCONCLUSIVE: control was hit)"),
                  flush=True)
            if not all(after.values()) and rc == 0:
                rc = 3
            g.route("90:down,30:none")
            g.d.cmd("screenshot", path=os.path.join(out, "3_walking.png"))

        result = {"setup": a.setup_type, "source": cur, "to": pth["to"],
                  "target": target, "predicted": pred, "chosen": chosen,
                  "final_type": g.partner_type,
                  "before": {k: [hex(x) for x in v] for k, v in before.items()},
                  "after": {k: [hex(x) for x in v] for k, v in after.items()}}
    finally:
        g.shutdown()
    if result:
        json.dump(result, open(os.path.join(out, "result.json"), "w"), indent=1)
        print("\nresult -> %s" % os.path.join(out, "result.json"), flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
