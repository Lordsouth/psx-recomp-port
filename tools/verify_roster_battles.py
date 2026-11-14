#!/usr/bin/env python3
"""Try to reproduce the reported Machinedramon crashes with the roster's partner Machinedramon (type 114).

The claims (docs/GAME.md 12.3, from Vicen's reports, never checked here): a partner Machinedramon can crash when a
battle pulls in all the Digimon of a room (Beetle Land; the first room of Mt. Infinity), All Range Beam can crash
three-enemy battles, and Machinedramon evolving to Sukamon crashes.

beam: the partner evolves to 114 (setup, the evolution sequence with the target supplied). Its three normal move
    slots all hold All Range Beam (tech 25, found in the live DIGIMON_DATA[114].moves), slot 3 its finisher, with
    MP to spare, so Your Call can only pick All Range Beam. Each battle: away to row 112 and back, so the room
    reloads with all its wild Digimon; they walk into the tamer and handleBattleStart pulls in every one on screen
    up to its cap of 3 enemies (src/main/btl.c:256-300). The enemies are kept alive until the partner has cast the
    beam --casts times, then left on 1 HP to fall. A battle passes when it ends with the player in control on the
    room. Rooms: 166 (MGEN01, Mt. Infinity's first room: 5 wild, 3 Digimon 88 and 2 Digimon 108) and 105 (BETL01,
    Beetle Land: 4 wild) -- MAPHEAD rows 166 and 105 (`tools/dis_script.py --maphead 166`).

suka: the partner evolves to 114 (setup), then virusBar (PARTNER_PARA +0x1E) is set to 16, the input the game reads:
    when it is 16 or more and the partner is not Sukamon, the game itself writes EVOLUTION_TARGET = 39 and starts
    the evolution (src/main/partner_impl.c:2127-2148). The partner type is never written by this tool. Passes when
    the guest wrote type 39 and the player has control again.

A crash is the process exiting or the debug server going silent; a hang is the guest frame counter stopping, or a
battle or evolution not finishing in its budget. On any of them the tail of the game log (the runtime's freeze
heartbeat prints there) is kept in the result.

    py -3.12 tools/verify_roster_battles.py beam --rows 166,105 --battles 5 --seed <card dir> --port 7410
    py -3.12 tools/verify_roster_battles.py suka --count 3 --seed <card dir> --port 7411

Exit 0 = every attempt completed, 3 = a crash, hang or failed check, 2 = setup failed.
"""
import argparse
import json
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dwgame import Game, DIGIMON_DATA, DIGIMON_STRIDE      # noqa: E402
from dwdbg import Dead, Wedge                              # noqa: E402
from field_drive import (Field, BATTLE, IS_SCRIPT_PAUSED, NPC_BASE, NPC_SIZE, PARTNER_STATS, PARTNER_MAX_HP,
                         PARTNER_ANIM)                     # noqa: E402
from verify_roster_training import ROSTER_STATE, DETECT_LUI, force, fresh, idle, seed_cards   # noqa: E402

MACHINEDRAMON, SUKAMON, ALL_RANGE_BEAM = 114, 39, 25
DD_MOVES = 35                       # DIGIMON_DATA.moves[16]
HOME = 112                          # where each battle starts from, so the room reloads
VIRUS_BAR = 0x80138460 + 0x1E
SCRATCH_TOP = 0x8013520C            # EVL's shard write pointer (MAIN_D_8013520C, src/evl/evl.c:1031,1114)


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("beam", "suka"))
    ap.add_argument("--rows", default="166,105", help="beam: rooms to fight in")
    ap.add_argument("--battles", type=int, default=5, help="beam: battles per room")
    ap.add_argument("--casts", type=int, default=3, help="beam: All Range Beam casts per battle before the kill")
    ap.add_argument("--count", type=int, default=3, help="suka: evolutions")
    ap.add_argument("--from-type", type=int, default=MACHINEDRAMON, help="suka: the partner that catches the virus")
    ap.add_argument("--port", type=int, default=7410)
    ap.add_argument("--build", default=os.path.join(root, "build-debug"))
    ap.add_argument("--seed", default=os.path.join(root, "saves"))
    a = ap.parse_args()
    out = os.path.join(a.build, "roster_battles_" + a.mode)
    cards = os.path.join(out, "cards")
    if not seed_cards(a.seed, cards):
        print("No memory card to seed from at %s" % a.seed, file=sys.stderr)
        return 2
    with open(os.path.join(a.build, "mods", "state.toml"), "w") as fh:
        fh.write(ROSTER_STATE)
    log = os.path.join(out, "game.log")
    fails, attempts = [], []

    def check(cond, msg):
        print(("PASS " if cond else "FAIL ") + msg, flush=True)
        if not cond:
            fails.append(msg)
        return cond

    g = Game.launch(exe=os.path.join(a.build, "Digimon_World_Recompiled.exe"), port=a.port, memcard_dir=cards,
                    extra_args=["--headless-opengl"], log_path=log,
                    env={"PSX_OVERLAY_CAPTURES": os.path.join(out, "captures.json")})
    f = Field(g, check)
    h16 = lambda addr: struct.unpack("<h", g.d.read(addr, 2))[0]
    npc_hp = lambda s: NPC_BASE + NPC_SIZE * s + 0x4C

    def alive():
        """None while the guest runs; else what killed it, with the log's tail."""
        if g.proc.poll() is not None:
            return "process exited, code %s" % g.proc.returncode
        try:
            f0 = g.d.frame()
            time.sleep(1.0)
            if g.d.frame() == f0:
                return "guest frame counter stopped at %d" % f0
        except (Dead, Wedge) as e:
            return "debug server: %s" % e
        return None

    def log_tail():
        try:
            return open(log, "rb").read()[-3000:].decode("utf-8", "replace")
        except OSError:
            return ""

    def to_machinedramon(t=MACHINEDRAMON):
        if g.partner_type != t and not force(g, t):
            raise RuntimeError("could not set up partner type %d (is %d)" % (t, g.partner_type))

    def beam_battle(row, beam_anim, fin_anim):
        g.warp(HOME)
        to_machinedramon()
        fresh(g)
        # off/def/speed/brain, pick weights, moves (animation ids), max HP/MP; current HP/MP follow
        g.d.write_bytes(struct.pack("<4h4B4B2h2h", 400, 400, 400, 500, 100, 100, 100, 0,
                                    beam_anim, beam_anim, beam_anim, fin_anim, 9999, 9999, 9999, 9999), PARTNER_STATS)
        g.warp(row)
        rec = dict(row=row)
        if not f.wait_for(lambda: f.gstate() in BATTLE, 90):
            # nobody came: walk at the nearest wild Digimon on the map
            npcs = [n for n in map(f.npc, range(8)) if n and n["on_map"] and n["pos"]]
            if npcs:
                e = npcs[0]["pos"]
                f.go((e[0], e[2]), lambda: f.gstate() in BATTLE, limit=40)
        rec["started"] = f.wait_for(lambda: f.gstate() == 1, 60)
        if not rec["started"]:
            rec["dead"] = alive()
            return rec
        enemies = f.enemies()
        rec["enemies"] = [f.npc(s)["type"] for s in enemies]
        casts, last, t0 = 0, None, time.time()
        while time.time() - t0 < 300 and f.gstate() in BATTLE:
            anim = g.d.read(PARTNER_ANIM, 1)[0]
            if anim == beam_anim and last != beam_anim:
                casts += 1
            last = anim
            g.d.write_bytes(struct.pack("<hh", 9999, 9999), PARTNER_MAX_HP + 4)
            for s in enemies:
                if casts < a.casts:
                    g.d.write_bytes(struct.pack("<h", 9000), npc_hp(s))
                elif h16(npc_hp(s)) > 1:
                    g.d.write_bytes(struct.pack("<h", 1), npc_hp(s))
            time.sleep(0.05)
            if g.proc.poll() is not None:
                break
        rec["casts"] = casts
        end = time.time() + 120
        while time.time() < end and g.proc.poll() is None and not (
                f.gstate() == 0 and g.tamer_state == 0 and f.i32(IS_SCRIPT_PAUSED) == 1):
            g.route("4:cross,20:none")
        rec["dead"] = alive()
        rec["over"] = rec["dead"] is None and f.gstate() == 0 and g.tamer_state == 0
        rec["screen"] = f.screen() if rec["dead"] is None else None
        return rec

    def suka_once():
        if g.current_screen != HOME:
            g.warp(HOME)
        to_machinedramon(a.from_type)
        fresh(g)
        g.set("evolution_target", -1)
        top0 = struct.unpack("<I", g.d.read(SCRATCH_TOP, 4))[0]
        tops = set()
        rec = dict(before=g.partner_type)
        g.d.write_bytes(struct.pack("<h", 16), VIRUS_BAR)
        target_seen = None
        end = time.time() + 180
        while time.time() < end and g.proc.poll() is None:
            t = g.evolution_target
            top = struct.unpack("<I", g.d.read(SCRATCH_TOP, 4))[0]
            if top != top0:
                tops.add(top)
            if t != -1 and target_seen is None:
                target_seen = t
            if g.partner_type == SUKAMON and idle(g):
                break
            if g.partner_type == SUKAMON:
                g.route("4:cross,30:none")
            else:
                time.sleep(0.05)
        rec["target_written"] = target_seen
        rec["scratch_tops"] = ["0x%08X" % v for v in sorted(tops)][-3:]  # the shard write pointer's last values
        rec["dead"] = alive()
        rec["after"] = g.partner_type if rec["dead"] is None else None
        rec["virus_bar"] = h16(VIRUS_BAR) if rec["dead"] is None else None
        rec["over"] = rec["dead"] is None and rec["after"] == SUKAMON and idle(g)
        return rec

    try:
        f.enter()
        if not check(struct.unpack("<H", g.d.read(DETECT_LUI, 2))[0] == 0x9F00, "roster on in the active plan"):
            return 2
        to_machinedramon(MACHINEDRAMON if a.mode == "beam" else a.from_type)
        moves = list(g.d.read(DIGIMON_DATA + g.partner_type * DIGIMON_STRIDE + DD_MOVES, 16))
        print("partner type %d %s, moves %s" % (g.partner_type, g.digimon_record(g.partner_type)["name"], moves),
              flush=True)
        if a.mode == "beam":
            if not check(ALL_RANGE_BEAM in moves, "All Range Beam (tech %d) is in DIGIMON_DATA[%d].moves"
                         % (ALL_RANGE_BEAM, MACHINEDRAMON)):
                return 2
            beam = 0x2E + moves.index(ALL_RANGE_BEAM)
            fin = 0x2E + max(i for i, m in enumerate(moves) if 0x3A <= m <= 0x70)
            for row in (int(r) for r in a.rows.split(",")):
                for n in range(a.battles):
                    rec = beam_battle(row, beam, fin)
                    rec["n"] = n + 1
                    attempts.append(rec)
                    ok = rec.get("over") and rec.get("screen") == row and rec.get("casts", 0) >= a.casts
                    check(ok, "room %d battle %d: %s enemies %s, All Range Beam cast %s times, %s"
                          % (row, n + 1, len(rec.get("enemies", [])), rec.get("enemies"), rec.get("casts"),
                             "completed, player in control on the room" if ok else "dead: %s" % rec.get("dead")))
                    if rec.get("dead"):
                        rec["log_tail"] = log_tail()
                        return 3
        else:
            for n in range(a.count):
                rec = suka_once()
                rec["n"] = n + 1
                attempts.append(rec)
                check(rec["over"], "evolution %d: type %d -> %s, EVOLUTION_TARGET written by the game %s, virusBar "
                      "now %s, shard pointer reached %s; %s" % (n + 1, rec["before"], rec["after"], rec["target_written"],
                                      rec["virus_bar"], rec["scratch_tops"][-1:],
                                      "completed, player in control" if rec["over"] else "dead: %s" % rec["dead"]))
                if rec.get("dead"):
                    rec["log_tail"] = log_tail()
                    return 3
        return 3 if fails else 0
    finally:
        g.shutdown()
        json.dump(dict(mode=a.mode, attempts=attempts, fails=fails), open(os.path.join(out, "result.json"), "w"),
                  indent=1)
        print("\n%d attempts, %d failed checks -> %s" % (len(attempts), len(fails), os.path.join(out, "result.json")))


if __name__ == "__main__":
    sys.exit(main())
