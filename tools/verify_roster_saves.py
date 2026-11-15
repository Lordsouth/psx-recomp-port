#!/usr/bin/env python3
"""Memory-card saves with the Expanded Roster on and off, in both directions (dw_roster_saves.c).

save --types SPEC,...  roster on (+ Save Anywhere): for each SPEC the partner is set up and saved; the card, the live
      partner record and the card payload are kept in <out>/SPEC/. A SPEC is
        T     the partner evolves to T (the evolution sequence with the target supplied, as
              verify_roster_training.force does);
        sT    T, then caught by the virus: Sukamon with pstat 5 = T (the game evolves it, docs/GAME.md 8.9);
        TvP   P catches the virus and is cured (routine 2's two writes), then evolves to T: pstat 5 still holds P;
      --register first writes a Battle Mode registration of the live partner into slot 0 of SAVE_FILE.saves[0], as
      registerBattleData does (main_menu.c:5230-5245).
off --card SPEC  no mods at all (or plain 1.5 with --dw15): Continue from SPEC's card. The partner must be the mapped
      stock species, real and raisable, its moves usable, pstat 4/5 stock; a registration of 114 reads 115. Then play:
      walk, a won battle, the field menu, Sukamon's revert where pstat 5 is set (routine 2's writes:
      EVOLUTION_TARGET = pstat 5, partner state 13), and a stock save (Sleep -> "Save and go to sleep"), kept as
      <out>/SPEC_off/. As the control (before dw_roster_saves.c) this mode records what goes wrong instead.
on --card SPEC|SPEC_off  roster on: Continue. SPEC: the partner record (type, Stats, learned moves, lives,
      PartnerPara, triggers, pstats) equals the one saved, and a registration of 114 reads 114 again in both copies.
      SPEC_off: the card still holds the stale block, the load hook counted it stale, and the stock partner stays.
mkstock  no mods: the seed is a fresh file, so Continue plays the intro; that partner is saved through Sleep
      (<out>/stock/), a card only the stock game ever wrote.
stock  roster on, that card: Continue changes nothing (case 3); then Save Anywhere with that stock partner, and every
      field the save hook could touch equals the live game, the spare bytes as before (case 4).
--dw15 runs any mode on Digimon World 1.5 (roster-15 for "on", plain 1.5 for "off"); cards live in cards/dw15.
The control is save + off on a build without dw_roster_saves.c (or, on 1.5, without its roster-15 plugin).

Verdicts are guest-memory and memory-card reads. Exit 0 = PASS, 1 = a FAIL, 2 = setup failed.

    $PY tools/verify_roster_saves.py save --types 114,68,105 --port 8201
    $PY tools/verify_roster_saves.py off --card 114 --port 8202
"""
import argparse
import json
import os
import re
import shutil
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dwgame import Game, PARA                              # noqa: E402
from dwdbg import Dead, Wedge                              # noqa: E402
from field_drive import (Field, BATTLE, IS_SCRIPT_PAUSED, NPC_BASE, NPC_SIZE, PARTNER_MAX_HP, HOUR, TRI_STATE,
                         MENU_CURSOR, CURRENT_MENU, YES_NO, UP, RIGHT)  # noqa: E402
from verify_roster_training import ROSTER_STATE, force, fresh, idle, seed_cards   # noqa: E402
from verify_roster15 import STATE as DW15_ROSTER_STATE    # noqa: E402

PARTNER = 0x801557A8
SAVE_ANYWHERE = '\n[[package]]\nid = "dw.vanilla.qol"\nversion = "1.0.0"\n\n' \
                '[[feature]]\npackage_id = "dw.vanilla.qol"\nid = "save-anywhere"\nenabled = true\n'
NO_MODS = "format_version = 2\n"
DW15_ONLY = ('format_version = 2\n\n[[package]]\nid = "dw.1_5"\nversion = "1.0.0"\n\n'
             '[[feature]]\npackage_id = "dw.1_5"\nid = "dw15"\nenabled = true\n')
SAVE_FILE = 0x8013192C
S0, S1 = SAVE_FILE + 0x200, SAVE_FILE + 0x200 + 0xF00
SCRIPT_STATE_PTR, TRIGGERS_OFFSET = 0x80134FB8, 0xF5       # triggers[100], then the saved pstats 0-34
EVOLUTION_TARGET = 0x80134E50
VS_BUFFER_PTR, VS_PLAYER_INDEX = 0x801346D4, 0x8013505C     # VS__REGISTERED_DIGIMON_BUFFER (a pointer): player 1 first
SPARE = (0x4E1, 0x4FC)          # SavegamePayload.unknown4E1: no stock reader or writer
P_PSTAT4, P_PSTAT5, P_REGS = 0x290 + 345 + 4, 0x290 + 345 + 5, 0x500
WILD_ROOMS = (166, 105)         # rooms with wild Digimon (verify_roster_battles.py)
SUKAMON = 39
TICKING = (0x20, 0x21, 0x34, 0x35, 0x3E, 0x3F)   # PartnerPara bytes the field ticks: poopingTimer, areaEffectTimer, food
ED = {False: dict(dd=0x8012CEB4, raise_=0x801225BC, on=ROSTER_STATE + SAVE_ANYWHERE, off=NO_MODS, sub=""),
      True: dict(dd=0x801F4300, raise_=0x801FA01C, on=DW15_ROSTER_STATE + SAVE_ANYWHERE, off=DW15_ONLY,
                 sub="dw15")}


def mapping(root, dw15):
    """{roster type: stock stand-in} as gen_roster_mod.py wrote it into dw_roster_saves.inc (no 114 on 1.5: Veemon)."""
    text = open(os.path.join(root, "dw_roster_saves.inc")).read()
    m = {int(a): int(b) for a, b in re.findall(r"\{\s*(\d+)\s*,\s*(\d+)\s*\}", text)}
    if dw15:
        m.pop(114, None)
    return m


def spec_parts(spec):
    """'68' -> (68, None, False); 's68' -> (68, None, True); '73v68' -> (73, 68, False)."""
    suka = spec.startswith("s")
    t, _, p = spec.lstrip("s").rstrip("t").partition("v")
    return int(t), int(p) if p else None, suka


def payload_fields(s0):
    st = s0[0x270:0x290]
    return dict(type=struct.unpack_from("<i", s0, 0x1B8)[0], moves=list(st[12:16]), prio=list(st[8:12]),
                pstat4=s0[P_PSTAT4], pstat5=s0[P_PSTAT5], spare=s0[SPARE[0]:SPARE[1]].hex(),
                regs=[s0[P_REGS + i * 0x40 + 0x1C] for i in range(40)])


def card_payload(card_dir):
    card = open(os.path.join(card_dir, "card1.mcd"), "rb").read()
    blk = next(i for i in range(1, 16) if card[i * 128 + 10:i * 128 + 26] == b"BASLUS-01032DMR0")
    return card[blk * 0x2000 + 0x200:blk * 0x2000 + 0x200 + 0xF00]


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("save", "off", "on", "mkstock", "stock", "versus"))
    ap.add_argument("--types", default="114,68,105")
    ap.add_argument("--card", default=None, help="off/on: SPEC or SPEC_off")
    ap.add_argument("--register", action="store_true", help="save: a Battle Mode registration of the partner first")
    ap.add_argument("--dw15", action="store_true")
    ap.add_argument("--off", action="store_true", help="versus: without the roster")
    ap.add_argument("--port", type=int, default=8201)
    ap.add_argument("--build", default=os.path.join(root, "build-debug"))
    ap.add_argument("--seed", default=r"C:\Users\boura\hdb-rewind")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    e = ED[a.dw15]
    out = os.path.abspath(a.out or os.path.join(a.build, "rsaves15" if a.dw15 else "rsaves"))
    os.makedirs(out, exist_ok=True)
    cards = os.path.join(out, "c%d" % a.port)
    card_dir = os.path.join(cards, e["sub"]) if e["sub"] else cards
    card_name = a.card or ("stock" if a.mode == "stock" else None)
    if a.mode in ("save", "mkstock"):
        if not seed_cards(a.seed, card_dir):
            print("No memory card to seed from at %s" % a.seed, file=sys.stderr)
            return 2
    else:
        shutil.rmtree(cards, ignore_errors=True)
        shutil.copytree(os.path.join(out, card_name, "cards"), cards)
    with open(os.path.join(a.build, "mods", "state.toml"), "w") as fh:
        fh.write(e["off"] if a.mode in ("off", "mkstock") or a.mode == "versus" and a.off else e["on"])
    log = os.path.join(out, "%s_%s.log" % (a.mode, card_name or "x"))
    fails = []
    stand = mapping(root, a.dw15)

    def check(cond, msg):
        print(("PASS " if cond else "FAIL ") + msg, flush=True)
        if not cond:
            fails.append(msg)
        return cond

    g = Game.launch(exe=os.path.join(a.build, "Digimon_World_Recompiled.exe"), port=a.port, memcard_dir=cards,
                    extra_args=["--headless-opengl"], log_path=log,
                    env={"PSX_OVERLAY_CAPTURES": os.path.join(out, "cap%d.json" % a.port)})
    f = Field(g, check, dw15=a.dw15)
    i32 = f.i32
    name = lambda t: g.d.read(e["dd"] + t * 52, 20).split(b"\0")[0].decode("ascii", "backslashreplace")

    def script_state():
        return struct.unpack("<I", g.d.read(SCRIPT_STATE_PTR, 4))[0] + TRIGGERS_OFFSET

    def triggers():
        return g.d.read(script_state(), 100)

    def pstat(n):
        return g.d.read(script_state() + 100 + n, 1)[0]

    def record():
        """The partner as the game holds it: type, Stats (0x20), learned moves (16), name, lives, PartnerPara,
        triggers[100], pstats 0-34 (the saved ones)."""
        p = g.d.read(PARTNER, 0x80)
        return dict(type=struct.unpack_from("<i", p)[0], stats=p[0x38:0x58].hex(), learned=p[0x58:0x68].hex(),
                    name=p[0x68:0x7C].hex(), lives=p[0x7C], para=g.d.read(PARA, 0x84).hex(),
                    triggers=triggers().hex(), pstats=g.d.read(script_state() + 100, 35).hex())

    def regs(base):
        return [g.d.read(base + P_REGS + i * 0x40 + 0x1C, 1)[0] for i in range(40)]

    def counters():
        return {c["name"]: c["count"] for c in g.d.cmd("mod_counters").get("counters", [])
                if c["name"].startswith("dw.roster")}

    def alive():
        if g.proc.poll() is not None:
            return "process exited, code %s" % g.proc.returncode
        try:
            f0 = g.d.frame()
            time.sleep(1.0)
            if g.d.frame() == f0:
                return "guest frame counter stopped at %d" % f0
        except (Dead, Wedge) as ex:
            return "debug server: %s" % ex
        return None

    def log_tail():
        try:
            return open(log, "rb").read()[-2000:].decode("utf-8", "replace")
        except OSError:
            return ""

    def save_anywhere():
        """Field.save_here's menu walk, at an hour this species is awake (Machinedramon sleeps through noon): an hour
        past its wake hour (PartnerPara +08)."""
        wake = struct.unpack("<h", g.d.read(PARA + 8, 2))[0]
        for _ in range(30):
            g.d.write_bytes(struct.pack("<hh", (wake + 1) % 24, 0), HOUR)
            g.advance(60)
            if not g.para_condition & 1 and idle(g):
                break
        check(not g.para_condition & 1, "partner awake at hour %d, so Save Anywhere is offered" % ((wake + 1) % 24))
        g.route("6:triangle,40:none")
        check(f.wait_for(lambda: i32(TRI_STATE) == -1, 10), "field menu open")
        g.route("6:left,20:none")
        check(struct.unpack("<h", g.d.read(MENU_CURSOR, 2))[0] == 6, "cursor on Save")
        g.route("6:cross,30:none")
        check(f.wait_for(lambda: i32(CURRENT_MENU) == 0x45, 10), "save prompt open")
        if g.d.read(YES_NO, 1)[0] != 0:
            g.route("6:up,20:none")
        g.route("6:cross,30:none")
        f.wait_for(lambda: i32(CURRENT_MENU) in (-1, 3, 0x42, 0x46), 20)
        check(i32(CURRENT_MENU) == -1, "save finished without a card error")
        f.wait_for(lambda: i32(TRI_STATE) == -1, 10)
        rec = record()                      # game time is stopped while the menu is open: the state just saved
        g.route("6:triangle,40:none")
        f.wait_for(lambda: g.tamer_state == 0, 10)
        return card_payload(card_dir), rec

    def keep(nm, rec, s0):
        d = os.path.join(out, nm)
        shutil.rmtree(d, ignore_errors=True)
        shutil.copytree(cards, os.path.join(d, "cards"))
        json.dump(dict(record=rec, payload=payload_fields(s0), registered=a.register), open(os.path.join(d, "saved.json"), "w"), indent=1)

    def para_check(rec, want_hex):
        """PartnerPara as saved, but for the timers the field ticks between Continue and this read (GAME.md 8.2:
        +20 poopingTimer, +34 areaEffectTimer, +3E foodLevel); the stock card run shows the same ones move."""
        diff = [i // 2 for i in range(0, 0x108, 2) if rec["para"][i:i + 2] != want_hex[i:i + 2]]
        check(set(diff) <= set(TICKING), "PartnerPara as saved apart from the ticking timers (differ: %s)"
              % ["+%02X" % o for o in diff])

    def moves_ok(t, st):
        """Each equipped move slot names a move the species' own list holds (DIGIMON_DATA.moves[slot - 0x2E])."""
        dd = g.d.read(e["dd"] + t * 52 + 35, 16)
        bad = [m for m in st[12:15] if m != 0xFF and not (0x2E <= m < 0x3E and dd[m - 0x2E] != 0xFF)]
        fin = st[15]
        if fin != 0xFF and not (0x2E <= fin < 0x3E and 0x3A <= dd[fin - 0x2E] < 0x71):
            bad.append(fin)
        return not bad and st[12] != 0xFF, bad

    def stock_partner(t, what):
        check(1 <= t <= 61 and t not in stand, "%s: %d %r is a stock partner type" % (what, t, name(t)))
        row = g.d.read(e["raise_"] + t * 28, 28)
        check(any(row[:8]) and row[8] > 0, "%s: RAISE_DATA[%d] is a real row: %s" % (what, t, row.hex()))

    def evolve_to(t):
        """Wait out an evolution whose target the game or a routine set, and take control back."""
        g.set_atomic(("tamer_state", 6), ("tamer_substate", 0))
        g.set_atomic(("partner_state", 13), ("partner_sub_state", 0))
        for _ in range(200):
            g.advance(30)
            if g.partner_type == t and g.evolution_target == -1:
                break
        for _ in range(60):
            if idle(g):
                break
            g.route("4:cross,30:none")
        g.advance(120)
        return g.partner_type == t and idle(g)

    def catch_virus(t):
        """virusBar 16: the game itself evolves the partner to Sukamon and keeps its type in pstat 5."""
        fresh(g)
        g.set("evolution_target", -1)
        g.d.write_bytes(struct.pack("<h", 16), PARA + 0x1E)
        f.wait_for(lambda: g.partner_type == SUKAMON and idle(g), 120)
        for _ in range(30):
            if idle(g):
                break
            g.route("4:cross,30:none")
        return check(g.partner_type == SUKAMON and pstat(5) == t,
                     "the virus made it Sukamon with pstat 5 = %d (type %d, pstat 5 %d)" % (t, g.partner_type, pstat(5)))

    def revert():
        """Sukamon's cure, as partner routine 2 does it (partner.c:1153-1157): EVOLUTION_TARGET = pstat 5, state 13."""
        target = pstat(5)
        if g.script_sync_bit == 0:
            g.set("script_sync_bit", 1)
        fresh(g)
        g.d.write_bytes(struct.pack("<h", target), EVOLUTION_TARGET)
        return target, evolve_to(target)

    def register(base):
        """registerBattleData's record, slot 0, from the live partner (main_menu.c:5230-5245)."""
        p = g.d.read(PARTNER, 0x80)
        st = p[0x38:0x58]
        hp, mp = struct.unpack_from("<hh", st, 0x10)
        off, de, spd, brn = struct.unpack_from("<4h", st, 0)
        disc = struct.unpack("<h", g.d.read(PARA + 0x28, 2))[0]
        rec = struct.pack("<7h", hp, mp, off, de, spd, brn, disc) + p[0x68:0x76] + bytes([p[0] & 0xFF]) + st[12:15]
        g.d.write_bytes(rec, base + P_REGS)

    def won_battle():
        """A wild room: the first Digimon that walks into the tamer, or the nearest one walked at; it falls at 1 HP."""
        for row in WILD_ROOMS * 2:
            try:                            # as verify_roster_battles.py: from 112 the room loads with its wild Digimon
                g.warp(112)
            except Wedge as ex:             # from some maps this first warp lands on 218 (not chased); go on from there
                print("warp to 112: %s" % ex, flush=True)
            try:
                g.warp(row)
            except Wedge as ex:
                print("warp to %d: %s" % (row, ex), flush=True)
                continue
            fresh(g)
            if not f.wait_for(lambda: f.gstate() in BATTLE, 60):
                npcs = [n for n in map(f.npc, range(8)) if n and n["on_map"] and n["pos"]]
                if npcs:
                    p = npcs[0]["pos"]
                    f.go((p[0], p[2]), lambda: f.gstate() in BATTLE, limit=40)
            if not (f.wait_for(lambda: f.gstate() in (2, 3), 60) and f.wait_for(lambda: f.gstate() == 1, 60)):
                continue
            enemies = f.enemies()
            money = i32(0x80134EB8)
            end = time.time() + 240
            while time.time() < end and not (f.gstate() == 0 and g.tamer_state == 0 and i32(IS_SCRIPT_PAUSED) == 1):
                if f.gstate() in BATTLE:
                    g.d.write_bytes(struct.pack("<hh", 9999, 9999), PARTNER_MAX_HP + 4)
                    for s in enemies:
                        if struct.unpack("<h", g.d.read(NPC_BASE + NPC_SIZE * s + 0x4C, 2))[0] > 1:
                            g.d.write_bytes(struct.pack("<h", 1), NPC_BASE + NPC_SIZE * s + 0x4C)
                g.route("4:cross,20:none")
            return (f.gstate() == 0 and g.tamer_state == 0 and f.screen() == row, row, i32(0x80134EB8) - money)
        return (False, None, 0)

    def stock_save():
        """The stock game's only save: sleep. The partner is made sleepy (HOUR at its sleepy hour), Sleep is
        chosen from the field menu, and "Save and go to sleep" writes the card (menu 0x2B)."""
        sleepy_h = struct.unpack("<h", g.d.read(PARA + 4, 2))[0]
        g.d.write_bytes(struct.pack("<hh", sleepy_h, 0), HOUR)
        f.wait_for(lambda: g.para_condition & 1, 10)
        if not check(g.para_condition & 1, "partner sleepy at hour %d, so Sleep is offered" % sleepy_h):
            return None
        before = open(os.path.join(card_dir, "card1.mcd"), "rb").read()
        g.route("6:triangle,40:none")
        check(f.wait_for(lambda: i32(TRI_STATE) == -1, 10), "field menu open")
        g.route("6:left,20:none")
        check(struct.unpack("<h", g.d.read(MENU_CURSOR, 2))[0] == 6, "cursor on Sleep")
        g.route("6:cross,30:none")
        end = time.time() + 120
        seen = set()
        while time.time() < end:
            m = i32(CURRENT_MENU)
            seen.add(m)
            if m == 0x28 and g.d.read(YES_NO, 1)[0] != 1:
                g.route("6:down,20:none")       # "Save and go to sleep" is the second option
            if open(os.path.join(card_dir, "card1.mcd"), "rb").read() != before and idle(g):
                break
            g.route("6:cross,40:none")
        f.wait_for(lambda: idle(g), 60)
        changed = open(os.path.join(card_dir, "card1.mcd"), "rb").read() != before
        check(changed, "the stock sleep save wrote the card (menus seen %s)" % sorted(seen))
        return card_payload(card_dir) if changed else None

    try:
        if a.mode == "versus":
            # Title -> Battle Mode (cursor 3, main_menu.c:3378-3382) -> player 1's card, slot, confirm -> menu 0x36
            # copies the card's registrations into VS__REGISTERED_DIGIMON_BUFFER and counts them (:4147-4174). The
            # harness drives pad 1 only, so it stops where player 2 picks a card (menu 0x32 with VS_PLAYER_INDEX 1).
            for _ in range(12):
                g.route("6:start,150:none")
            for _ in range(3):
                g.route("6:down,40:none")
            check(g.d.read(0x801316B8, 1)[0] == 3, "title cursor on Battle Mode")
            for _ in range(40):
                if i32(CURRENT_MENU) & 0xFFFF == 0x32 and g.d.read(VS_PLAYER_INDEX, 1)[0] == 1:
                    break
                g.route("6:cross,60:none")
            got = i32(CURRENT_MENU) & 0xFFFF == 0x32 and g.d.read(VS_PLAYER_INDEX, 1)[0] == 1
            check(got, "player 1's card was read: Versus mode now asks player 2 for a card (menu 0x%X)" % i32(CURRENT_MENU))
            vsb = struct.unpack("<I", g.d.read(VS_BUFFER_PTR, 4))[0]
            vsb = vsb if 0x80000000 <= vsb < 0x80200000 else VS_BUFFER_PTR     # a pointer, or the array itself
            vs = [g.d.read(vsb + i * 0x40 + 0x1C, 1)[0] for i in range(40)]
            want = 115 if a.off else 114
            print("VERSUS buffer slot 0: digimonId %d, name %r; counters %s" % (
                vs[0], g.d.read(vsb + 14, 14).split(bytes(1))[0], counters()), flush=True)
            check(vs[0] == want, "the registration Versus mode loaded reads %d (%s)" % (
                want, "without the roster: the stock Machinedramon" if a.off else "with the roster: Machinedramon back"))
            check(alive() is None, "still running")
            return 1 if fails else 0
        f.enter()
        g.settle(timeout_frames=1800)
        if a.mode == "save":
            for spec in a.types.split(","):
                t, p, suka = spec_parts(spec)
                if p is not None:
                    if not (check(force(g, p), "partner evolved to %d %r" % (p, name(p))) and catch_virus(p)):
                        continue
                    target, ok = revert()
                    if not check(ok and target == p, "routine 2 cured it back to %d (pstat 5 still %d)" % (p, pstat(5))):
                        continue
                if not check(force(g, t), "partner evolved to %d %r" % (t, name(t))):
                    continue
                if suka and not catch_virus(t):
                    continue
                fresh(g)
                g.advance(60)
                if a.register:
                    register(S0)
                if spec.endswith("t"):
                    # "<SPEC>t": entered in a tournament, as script_interp.c:1811 records it (pstats 2, 3, 4 = the
                    # partner's species); the script state is copied into the save as it stands
                    g.d.write_bytes(bytes([0x95, 0x16, g.partner_type & 0xFF]), script_state() + 100 + 2)
                s0, rec = save_anywhere()
                keep(spec, rec, s0)
                pf = payload_fields(s0)
                print("SAVED %s: card type %d moves %s pstat4 %d pstat5 %d reg0 %d spare %s; live pstat5 %d" % (
                    spec, pf["type"], pf["moves"], pf["pstat4"], pf["pstat5"], pf["regs"][0], pf["spare"], pstat(5)),
                    flush=True)
                if a.register and t == 114 and not a.dw15:
                    check(pf["regs"][0] == 115 and regs(S0)[0] == 114 and regs(S1)[0] == 114,
                          "registration of 114: the card says %d, SAVE_FILE still 114/114 (read %d/%d)"
                          % (pf["regs"][0], regs(S0)[0], regs(S1)[0]))
        elif a.mode == "off":
            dead = alive()
            saved = json.load(open(os.path.join(out, card_name, "saved.json")))
            if not check(dead is None, "the game runs after Continue (%s)" % dead):
                print(log_tail())
                return 1
            rec = record()
            t0, p0, suka = spec_parts(card_name)
            t, want = rec["type"], SUKAMON if suka else stand.get(t0)
            print("LOADED without the roster: partner type %d %r, saved as %d; pstat 4 %d, pstat 5 %d, reg0 %d" % (
                t, name(t), saved["record"]["type"], pstat(4), pstat(5), regs(S0)[0]), flush=True)
            check(want is not None and t == want, "the partner is the mapped stock species %s (is %d)" % (want, t))
            stock_partner(t, "partner")
            for n in (4, 5):
                check(pstat(n) not in stand, "pstat %d (%d) names no roster species" % (n, pstat(n)))
            if card_name.split("_")[0].endswith("t"):
                check(pstat(4) == t, "the tournament entry's species (pstat 4) is the partner's, %d, so it stays" % pstat(4))
            if saved.get("registered") and saved["record"]["type"] == 114 and not a.dw15:
                check(regs(S0)[0] == 115 and regs(S1)[0] == 115,
                      "the registration of 114 reads 115, the stock Machinedramon (%d/%d)" % (regs(S0)[0], regs(S1)[0]))
            st = bytes.fromhex(rec["stats"])
            ok, bad = moves_ok(t, st)
            dd = g.d.read(e["dd"] + t * 52 + 35, 16)
            print("equipped move ids: %s" % [dd[m - 0x2E] if 0x2E <= m < 0x3E else None for m in st[12:16]], flush=True)
            check(ok, "every equipped move is on %d's own list (moves %s, bad %s)" % (t, list(st[12:16]), bad))
            sv = saved["record"]["stats"]
            check(rec["stats"][:24] == sv[:24] and rec["stats"][32:] == sv[32:] and rec["name"] == saved["record"]["name"],
                  "Stats (all but the move slots) and name are the ones saved")
            p = f.pos()
            f.walk(UP, 1)
            f.walk(RIGHT, 1)
            check(f.pos() != p and alive() is None, "walks: %s -> %s" % (p, f.pos()))
            won, row, bits = won_battle()
            check(won and alive() is None, "a won battle in room %s (bits +%d)" % (row, bits))
            g.route("6:triangle,40:none")
            check(f.wait_for(lambda: i32(TRI_STATE) == -1, 10), "the field menu opens")
            g.route("6:triangle,40:none")
            f.wait_for(lambda: idle(g), 10)
            if pstat(5):
                before = triggers()
                target, ok = revert()
                after = triggers()
                newly = [i * 8 + b for i in range(100) for b in range(8) if after[i] >> b & 1 and not before[i] >> b & 1]
                print("REVERT: routine 2 target %d -> partner %d %r; triggers newly set %s" % (
                    target, g.partner_type, name(g.partner_type), [hex(x) for x in newly]), flush=True)
                check(ok and alive() is None, "routine 2's evolution to %d finished with control back" % target)
                stock_partner(g.partner_type, "after the revert")
                check(not [x for x in newly if 0x262 + 63 <= x <= 0x262 + 113],
                      "no trigger in 1.5's roster-aliased bank 0x2A1..0x2D3 (treasure chests) was set")
            s0 = stock_save()
            if s0 is not None:
                pf = payload_fields(s0)
                check(pf["type"] == g.partner_type, "the stock save holds the stock partner %d" % pf["type"])
                keep(card_name + "_off", record(), s0)
            check(alive() is None, "still running")
        elif a.mode == "on":
            saved = json.load(open(os.path.join(out, card_name, "saved.json")))
            rec = record()
            cnt = counters()
            print("LOADED with the roster: partner type %d; counters %s" % (rec["type"], cnt), flush=True)
            if card_name.endswith("_off"):
                card = card_payload(os.path.join(out, card_name, "cards", e["sub"]))
                check(card[SPARE[0]:SPARE[0] + 2] == b"RS", "the stock re-save still carries the roster block")
                check(cnt.get("dw.roster.saves.stale", 0) >= 1 and not cnt.get("dw.roster.saves.restored"),
                      "the load hook ran and found the block stale (counters %s)" % cnt)
                check(rec["type"] == saved["record"]["type"],
                      "re-saved on stock: the stock partner %d stays (the roster one is not brought back)"
                      % saved["record"]["type"])
                check(rec["stats"][:40] == card[0x270:0x284].hex(), "its base Stats and moves are the stock save's (%s; card %s)"
                      % (rec["stats"][:40], card[0x270:0x284].hex()))
            else:
                check(cnt.get("dw.roster.saves.restored", 0) >= 1, "the load hook restored the block (counters %s)" % cnt)
                for k in ("type", "stats", "learned", "name", "lives", "triggers", "pstats"):
                    check(rec[k] == saved["record"][k], "%s restored exactly (%s)" % (k, rec[k] if k != "triggers" else "100 bytes"))
                para_check(rec, saved["record"]["para"])
                if saved.get("registered") and saved["record"]["type"] == 114 and not a.dw15:
                    check(regs(S0)[0] == 114 and regs(S1)[0] == 114,
                          "the registration reads 114 again in both copies (%d/%d)" % (regs(S0)[0], regs(S1)[0]))
        elif a.mode == "mkstock":
            # The seed is a fresh file (mainState 0): Continue plays the intro, which gives the partner. Save it on
            # the stock game, so case 3 starts from a card the stock game wrote. The intro's script settles first.
            g.warp(1)
            fresh(g)
            g.advance(300)
            s0 = stock_save()
            if s0 is not None:
                keep("stock", record(), s0)
        elif a.mode == "stock":
            s_card = card_payload(card_dir)
            rec = record()
            pf = payload_fields(s_card)
            check(rec["type"] == pf["type"] and 1 <= rec["type"] <= 61,
                  "a stock card loads its stock partner %d unchanged with the roster on" % rec["type"])
            check(rec["stats"] == s_card[0x270:0x290].hex(), "Stats as on the card")
            para_check(rec, s_card[0x1E0:0x264].hex())
            fresh(g)
            s0, rec = save_anywhere()
            pf2 = payload_fields(s0)
            st = bytes.fromhex(rec["stats"])
            check(pf2["type"] == rec["type"] and pf2["moves"] == list(st[12:16]) and pf2["prio"] == list(st[8:12]),
                  "the stock partner is saved as the game holds it (type %d, moves %s)" % (pf2["type"], pf2["moves"]))
            check(s0[0x290:0x290 + 0x17C] == g.d.read(script_state() - 245, 0x17C), "script state as the game holds it")
            check(pf2["spare"] == pf["spare"], "the spare bytes are as the card had them: %s" % pf2["spare"])
    finally:
        g.shutdown()
    print("RESULT roster saves%s %s %s: %s" % (" 1.5" if a.dw15 else "", a.mode, card_name or a.types,
                                              "FAIL %s" % fails if fails else "PASS"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
