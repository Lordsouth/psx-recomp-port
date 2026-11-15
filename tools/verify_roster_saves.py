#!/usr/bin/env python3
"""Memory-card saves with the Expanded Roster on and off, in both directions (dw_roster_saves.c).

save --types 114,68,...  roster on (+ Save Anywhere): for each type the partner evolves to it (the evolution sequence
      with the target supplied, as verify_roster_training.force does) and is saved; the card, the live partner
      record and the card payload are kept in <out>/T/.
off --type T  no mods at all: Continue from T's card. The partner must be the mapped stock species, real and
      raisable (its partner-line rows are stock rows), with its moves usable; then play: walk, a won battle, the
      field menu, and a stock save (Sleep -> "Save and go to sleep"). The re-saved card is kept as <out>/T_off/.
      As the control (before dw_roster_saves.c) this mode records what goes wrong instead.
on --type T [--card T|T_off]  roster on: Continue from the card. T: the partner record (type, Stats, learned moves,
      name, lives, PartnerPara, triggers, pstats) must equal the one saved. T_off: the stock partner stays.
      A type written s<T> (save --types s68) is caught by the virus first: the partner is Sukamon with pstat 5 = T.
mkstock  no mods: the seed is a fresh file, so Continue plays the intro; that partner is saved through Sleep
      (<out>/stock/), a card only the stock game ever wrote.
stock  roster on, that card: Continue changes nothing (case 3); then Save Anywhere with that stock partner, and every
      field the save hook could touch equals the live game, the spare bytes as before (case 4).
The control is save + off on a build without dw_roster_saves.c.

Verdicts are guest-memory and memory-card reads. Exit 0 = PASS, 1 = a FAIL, 2 = setup failed.

    $PY tools/verify_roster_saves.py save --types 114,68,105 --port 8201
    $PY tools/verify_roster_saves.py off --type 114 --port 8202
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
from field_drive import (Field, BATTLE, IS_SCRIPT_PAUSED, NPC_BASE, NPC_SIZE, PARTNER_MAX_HP, PSTAT_BASE,
                         TRIGGER_BASE, HOUR, TRI_STATE, MENU_CURSOR, CURRENT_MENU, YES_NO, UP, DOWN, LEFT, RIGHT)  # noqa: E402
from verify_roster_training import ROSTER_STATE, force, fresh, idle, seed_cards   # noqa: E402

PARTNER = 0x801557A8
SAVE_ANYWHERE = '\n[[package]]\nid = "dw.vanilla.qol"\nversion = "1.0.0"\n\n' \
                '[[feature]]\npackage_id = "dw.vanilla.qol"\nid = "save-anywhere"\nenabled = true\n'
NO_MODS = "format_version = 2\n"
RAISE_VA, EVO_PATHS_VA, DIGIMON_DATA_VA = 0x801225BC, 0x8012B66C, 0x8012CEB4
SAVE_FILE_S0 = 0x80131B2C
SPARE = (0x4E1, 0x4FC)          # SavegamePayload.unknown4E1: no stock reader or writer
WILD_ROOMS = (166, 105)         # rooms with wild Digimon (verify_roster_battles.py)
SUKAMON = 39
TICKING = (0x20, 0x21, 0x34, 0x35, 0x3E, 0x3F)   # PartnerPara bytes the field ticks: poopingTimer, areaEffectTimer, food


def mapping(root):
    """{roster type: stock stand-in} as gen_roster_mod.py wrote it into dw_roster_saves.inc."""
    text = open(os.path.join(root, "dw_roster_saves.inc")).read()
    return {int(a): int(b) for a, b in re.findall(r"\{\s*(\d+)\s*,\s*(\d+)\s*\}", text)}


def record(g):
    """The partner as the game holds it: type, Stats (0x20), learned moves (16), name (20), lives, PartnerPara,
    triggers[100], pstats 0-34 (the saved ones)."""
    p = g.d.read(PARTNER, 0x80)
    return dict(type=struct.unpack_from("<i", p)[0], stats=p[0x38:0x58].hex(), learned=p[0x58:0x68].hex(),
                name=p[0x68:0x7C].split(b"\0")[0].decode("ascii", "backslashreplace"), lives=p[0x7C],
                para=g.d.read(PARA, 0x84).hex(), triggers=g.d.read(TRIGGER_BASE, 100).hex(),
                pstats=g.d.read(PSTAT_BASE, 35).hex())


def payload_fields(s0):
    st = s0[0x270:0x290]
    return dict(type=struct.unpack_from("<i", s0, 0x1B8)[0], moves=list(st[12:16]), prio=list(st[8:12]),
                pstat5=s0[0x290 + 212 + 33 + 100 + 5], spare=s0[SPARE[0]:SPARE[1]].hex(),
                regs=[s0[0x500 + i * 0x40 + 0x1C] for i in range(40)])


def card_payload(card_dir):
    card = open(os.path.join(card_dir, "card1.mcd"), "rb").read()
    blk = next(i for i in range(1, 16) if card[i * 128 + 10:i * 128 + 26] == b"BASLUS-01032DMR0")
    return card[blk * 0x2000 + 0x200:blk * 0x2000 + 0x200 + 0xF00]


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("save", "off", "on", "mkstock", "stock"))
    ap.add_argument("--types", default="114,68,105")
    ap.add_argument("--type", type=int)
    ap.add_argument("--card", default=None, help="on: T (default) or T_off")
    ap.add_argument("--port", type=int, default=8201)
    ap.add_argument("--build", default=os.path.join(root, "build-debug"))
    ap.add_argument("--seed", default="/c/Users/boura/hdb-rewind" if os.name != "nt" else r"C:\Users\boura\hdb-rewind")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    out = os.path.abspath(a.out or os.path.join(a.build, "rsaves"))
    os.makedirs(out, exist_ok=True)
    cards = os.path.join(out, "c%d" % a.port)
    card_name = a.card or (str(a.type) if a.type else "stock" if a.mode == "stock" else None)
    if a.mode in ("save", "mkstock"):
        if not seed_cards(a.seed, cards):
            print("No memory card to seed from at %s" % a.seed, file=sys.stderr)
            return 2
    else:
        shutil.rmtree(cards, ignore_errors=True)
        shutil.copytree(os.path.join(out, card_name, "cards"), cards)
    state = NO_MODS if a.mode in ("off", "mkstock") else ROSTER_STATE + SAVE_ANYWHERE
    with open(os.path.join(a.build, "mods", "state.toml"), "w") as fh:
        fh.write(state)
    log = os.path.join(out, "%s_%s.log" % (a.mode, card_name or "x"))
    fails = []

    def check(cond, msg):
        print(("PASS " if cond else "FAIL ") + msg, flush=True)
        if not cond:
            fails.append(msg)
        return cond

    g = Game.launch(exe=os.path.join(a.build, "Digimon_World_Recompiled.exe"), port=a.port, memcard_dir=cards,
                    extra_args=["--headless-opengl"], log_path=log,
                    env={"PSX_OVERLAY_CAPTURES": os.path.join(out, "cap%d.json" % a.port)})
    f = Field(g, check)
    i32 = f.i32

    def alive():
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
            return open(log, "rb").read()[-2000:].decode("utf-8", "replace")
        except OSError:
            return ""

    def save_anywhere(tag):
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
        rec = record(g)                     # game time is stopped while the menu is open: the state just saved
        g.route("6:triangle,40:none")
        f.wait_for(lambda: g.tamer_state == 0, 10)
        return card_payload(cards), rec

    def keep(name, rec, s0):
        d = os.path.join(out, name)
        shutil.rmtree(d, ignore_errors=True)
        shutil.copytree(cards, os.path.join(d, "cards"))
        json.dump(dict(record=rec, payload=payload_fields(s0)), open(os.path.join(d, "saved.json"), "w"), indent=1)

    def para_check(rec, want_hex):
        """PartnerPara as saved, but for the timers the field ticks between Continue and this read (GAME.md 8.2:
        +20 poopingTimer, +34 areaEffectTimer, +3E foodLevel); the stock card run shows the same three move."""
        diff = [i // 2 for i in range(0, 0x108, 2) if rec["para"][i:i + 2] != want_hex[i:i + 2]]
        print("PartnerPara bytes that differ from the save: %s" % ["+%02X" % o for o in diff], flush=True)
        check(set(diff) <= set(TICKING), "PartnerPara as saved apart from the ticking timers (differ: %s)" % diff)

    def moves_ok(t, st):
        """Each equipped move slot names a move the species' own list holds (DIGIMON_DATA.moves[slot - 0x2E])."""
        dd = g.d.read(DIGIMON_DATA_VA + t * 52 + 35, 16)
        bad = [m for m in st[12:15] if m != 0xFF and not (0x2E <= m < 0x3E and dd[m - 0x2E] != 0xFF)]
        fin = st[15]
        if fin != 0xFF and not (0x2E <= fin < 0x3E and 0x3A <= dd[fin - 0x2E] < 0x71):
            bad.append(fin)
        return not bad and st[12] != 0xFF, bad

    def won_battle():
        """A wild room: the first Digimon that walks into the tamer, or the nearest one walked at; it falls at 1 HP."""
        for row in WILD_ROOMS:
            try:
                g.warp(112)                 # as verify_roster_battles.py: from there the room loads with its wild Digimon
                g.warp(row)
            except Wedge as e:
                print("warp to %d: %s" % (row, e), flush=True)
                continue
            fresh(g)
            if not f.wait_for(lambda: f.gstate() in BATTLE, 60):
                npcs = [n for n in map(f.npc, range(8)) if n and n["on_map"] and n["pos"]]
                if npcs:
                    e = npcs[0]["pos"]
                    f.go((e[0], e[2]), lambda: f.gstate() in BATTLE, limit=40)
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
        before = open(os.path.join(cards, "card1.mcd"), "rb").read()
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
            if open(os.path.join(cards, "card1.mcd"), "rb").read() != before and idle(g):
                break
            g.route("6:cross,40:none")
        f.wait_for(lambda: idle(g), 60)
        changed = open(os.path.join(cards, "card1.mcd"), "rb").read() != before
        check(changed, "the stock sleep save wrote the card (menus seen %s)" % sorted(seen))
        return card_payload(cards) if changed else None

    rc = 0
    try:
        f.enter()
        g.settle(timeout_frames=1800)
        if a.mode == "save":
            for spec in a.types.split(","):
                suka, t = spec.startswith("s"), int(spec.lstrip("s"))
                ok = force(g, t)
                if not check(ok, "partner evolved to %d %r" % (t, g.digimon_record(t)["name"])):
                    continue
                if suka:
                    # "s<T>": the virus path, as verify_roster_battles.py suka: virusBar 16, the game itself evolves
                    # the partner to Sukamon (39) and keeps T in pstat 5 to return to (docs/GAME.md 8.9)
                    fresh(g)
                    g.set("evolution_target", -1)
                    g.d.write_bytes(struct.pack("<h", 16), PARA + 0x1E)
                    f.wait_for(lambda: g.partner_type == SUKAMON and idle(g), 120)
                    for _ in range(30):
                        if idle(g):
                            break
                        g.route("4:cross,30:none")
                    if not check(g.partner_type == SUKAMON and f.pstat(5) == t,
                                 "the virus made it Sukamon with pstat 5 = %d (type %d, pstat 5 %d)"
                                 % (t, g.partner_type, f.pstat(5))):
                        continue
                fresh(g)
                g.advance(60)
                s0, rec = save_anywhere(t)
                keep(spec, rec, s0)
                pf = payload_fields(s0)
                print("SAVED %s: card type %d moves %s pstat5 %d spare %s" % (spec, pf["type"], pf["moves"], pf["pstat5"],
                                                                         pf["spare"]), flush=True)
        elif a.mode == "off":
            dead = alive()
            saved = json.load(open(os.path.join(out, card_name, "saved.json")))
            if not check(dead is None, "the game runs after Continue with no mods (%s)" % dead):
                print(log_tail())
                return 1
            rec = record(g)
            base = card_name.split("_")[0]
            t, want = rec["type"], mapping(root).get(int(base.lstrip("s")))
            if base.startswith("s"):
                check(t == SUKAMON and f.pstat(5) == want,
                      "Sukamon, with pstat 5 the stand-in %s it returns to (pstat 5 %d)" % (want, f.pstat(5)))
                want = SUKAMON
            print("LOADED with no mods: partner type %d %r, saved as %d" % (t, g.digimon_record(t)["name"],
                                                                          saved["record"]["type"]), flush=True)
            check(want is not None and t == want, "the partner is the mapped stock species %s (is %d)" % (want, t))
            check(1 <= t <= 62, "a stock partner type (1..62), so every partner-line table has its row")
            raise_row = g.d.read(RAISE_VA + t * 28, 28)
            check(any(raise_row[:8]) and raise_row[8] > 0, "RAISE_DATA[%d] is a real row: %s" % (t, raise_row.hex()))
            st = bytes.fromhex(rec["stats"])
            ok, bad = moves_ok(t, st)
            check(ok, "every equipped move is on %d's own list (moves %s, bad %s)" % (t, list(st[12:16]), bad))
            check(rec["stats"][:16] == saved["record"]["stats"][:16] and rec["name"] == saved["record"]["name"],
                  "stats and name are the ones saved")
            p0 = f.pos()
            f.walk(UP, 1)
            f.walk(RIGHT, 1)
            check(f.pos() != p0 and alive() is None, "walks: %s -> %s" % (p0, f.pos()))
            won, row, bits = won_battle()
            check(won and alive() is None, "a won battle in room %s (bits +%d)" % (row, bits))
            g.route("6:triangle,40:none")
            check(f.wait_for(lambda: i32(TRI_STATE) == -1, 10), "the field menu opens")
            g.route("6:triangle,40:none")
            f.wait_for(lambda: idle(g), 10)
            s0 = stock_save()
            if s0 is not None:
                pf = payload_fields(s0)
                check(pf["type"] == t, "the stock save holds the stock partner %d" % pf["type"])
                keep(card_name + "_off", record(g), s0)
            check(alive() is None, "still running")
        elif a.mode == "on":
            saved = json.load(open(os.path.join(out, card_name, "saved.json")))
            rec = record(g)
            print("LOADED with the roster: partner type %d" % rec["type"], flush=True)
            if card_name.endswith("_off"):
                check(rec["type"] == saved["record"]["type"],
                      "re-saved on stock: the stock partner %d stays (the roster one is not brought back)"
                      % saved["record"]["type"])
                check(rec["stats"] == saved["record"]["stats"], "its Stats are the stock save's")
            else:
                for k in ("type", "stats", "learned", "lives", "triggers", "pstats"):
                    check(rec[k] == saved["record"][k], "%s restored exactly (%s)" % (k, rec[k] if k != "triggers" else "100 bytes"))
                card = card_payload(os.path.join(out, card_name, "cards"))
                check(g.d.read(PARTNER + 0x68, 20) == card[0x47B:0x48F], "name as on the card (the player's, never remapped)")
                para_check(rec, saved["record"]["para"])
        elif a.mode == "mkstock":
            # The seed is a fresh file (mainState 0): Continue plays the intro, which gives the partner. Save it on
            # the stock game, so case 3 starts from a card the stock game wrote. The intro's script settles first.
            g.warp(1)
            fresh(g)
            g.advance(300)
            s0 = stock_save()
            if s0 is not None:
                keep("stock", record(g), s0)
        elif a.mode == "stock":
            s_card = card_payload(cards)
            rec = record(g)
            pf = payload_fields(s_card)
            check(rec["type"] == pf["type"] and 1 <= rec["type"] <= 62,
                  "a stock card loads its stock partner %d unchanged with the roster on" % rec["type"])
            check(bytes.fromhex(rec["stats"])[12:16] == bytes(pf["moves"]), "moves as on the card")
            check(rec["stats"] == s_card[0x270:0x290].hex(), "Stats as on the card")
            para_check(rec, s_card[0x1E0:0x264].hex())
            fresh(g)
            s0, rec = save_anywhere("stock")
            pf2 = payload_fields(s0)
            st = bytes.fromhex(rec["stats"])
            check(pf2["type"] == rec["type"] and pf2["moves"] == list(st[12:16]) and pf2["prio"] == list(st[8:12]),
                  "the stock partner is saved as the game holds it (type %d, moves %s)" % (pf2["type"], pf2["moves"]))
            check(s0[0x290:0x290 + 0x17C] == g.d.read(TRIGGER_BASE - 245, 0x17C), "script state as the game holds it")
            check(pf2["spare"] == pf["spare"], "the spare bytes are as the card had them: %s" % pf2["spare"])
    finally:
        g.shutdown()
    print("RESULT roster saves %s %s: %s" % (a.mode, card_name or a.types, "FAIL %s" % fails if fails else "PASS"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
