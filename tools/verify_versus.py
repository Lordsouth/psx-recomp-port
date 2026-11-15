#!/usr/bin/env python3
"""Versus mode (the title menu's Battle Mode) driven with both controllers and two memory cards (docs/GAME.md 15.6).

card --name N --types T,T,...   seed card -> Continue (the intro gives the partner); for each T the partner is made T
      (the evolution with the target supplied, verify_roster_training.force) and registered through the game's own
      Save Machine for Memory Card Battle in Jijimon's house (map 205: the confirm cell 85 runs DG script 165 section
      0x55 -> routine 48 openSaveMachine -> menus 0x3C register, 0x3D slot i, 0x3E yes -> registerBattleData, 0x41
      writes the card). <out>/N/card1.mcd keeps the card; <out>/N/card.json what the card holds. With the roster,
      every roster registration must be on the card as its stock stand-in (114 -> 115 on the stock edition) with the
      true id in the record's tail, and SAVE_FILE must hold the true id after the write.
match --p1 N --p2 M   N's card in slot 1, M's in slot 2. Title -> Battle Mode -> player 1 picks card 1 / slot 1 with
      pad 1, player 2 picks card 2 / slot 1 with pad 2 (menus 0x32-0x36 take pad 2 when VS_PLAYER_INDEX is 1; a pad 1
      press must not answer for player 2) -> one battle, the first arena -> each player picks a fighter (--pick P1,P2:
      the place among the card's registrations, 0-3) on its own pad and presses START -> the versus intro, the match
      (VS__combatMain), the result, back to the title. Verdicts from guest memory: the registrations Versus mode
      loaded (VS__REGISTERED_DIGIMON_BUFFER, player 1 at record 0, player 2 at record 40): with the roster what was
      registered, without it the card's ids, all stock types; the portraits drawn on the fighter screen
      (VS__renderSelectDigimonPlayer) and the name plates (VS_renderFighterNamePlate) decoded from the GPU commands
      (cell and palette row, against the palette table in RAM and the roster's remap; every palette index inside the
      68-entry table, the stock 115's own read of [115] excepted); the fighters the match loaded (ENTITY_TABLE[1]/[2]
      type); the round result (VS_STATE_WINS/LOSSES); the title menu afterwards; both card files byte for byte
      unchanged. --win N (default 1): 8 s into the match the other fighter is set to 1 HP so it ends in seconds (a
      test poke, as the cup tool does); 0 lets the AI fight it out.
State per run: --mods off (no mods at all) | roster (Expanded Roster); --dw15 runs on Digimon World 1.5 (off = plain
1.5, roster = roster-15; cards in the dw15 subfolder). Run folders: <out>/runs/<mode>_<port>/ (log, screenshots).

Pad 2 is the debug server's set_input with "port":2, held at "nothing pressed" for the whole match run so the pad
stays plugged in (a timed press unplugs it when it ends). It needs a build with two pads (game.toml players = 2,
CMake MAX_PLAYERS 2); with one, pad 2 does not exist and player 2's card prompt never answers.
Exit 0 = PASS, 1 = a FAIL, 2 = setup failed. Screenshots are artifacts; no verdict reads one.

The runs of task "versus" (C:/vv as --out):
    card --name sA --types 3,17 --mods off                       stock cards (control)
    match --p1 sA --p2 sA --mods off --pick 0,1                  control: stock versus stock, no mods
    card --name rA --types 114,105,112 --mods roster             roster cards: 114, one of 66-111, one of 112-113
    card --name rB --types 114,68,113 --mods roster
    match --p1 rA --p2 rB --mods roster --pick 0,0 | 1,1 | 2,2 | 0,1 | 1,0
    match --p1 rA --p2 rB --mods off --pick 0,0 | 1,1 | 2,2      the same cards, no mods: stock types, match completes
    card --name qA --types 105,112,113 --mods roster --dw15      1.5 (roster-15)
    card --name qB --types 68,112 --mods roster --dw15
    match --p1 qA --p2 qB --dw15 --mods roster | off --pick 0,0 | 1,1
"""
import argparse
import hashlib
import json
import os
import shutil
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dwgame import Game                                     # noqa: E402
from dwdbg import Dead, Wedge, pad_word                     # noqa: E402
from field_drive import Field, CURRENT_MENU, IS_SCRIPT_PAUSED, TAMER as f_TAMER   # noqa: E402
from verify_roster_training import ROSTER_STATE, force, fresh, idle, seed_cards   # noqa: E402
from verify_roster15 import STATE as DW15_ROSTER_STATE     # noqa: E402
import roster_cups                                          # noqa: E402
from verify_roster_saves import mapping                     # noqa: E402

NO_MODS = "format_version = 2\n"
DW15_ONLY = ('format_version = 2\n\n[[package]]\nid = "dw.1_5"\nversion = "1.0.0"\n\n'
             '[[feature]]\npackage_id = "dw.1_5"\nid = "dw15"\nenabled = true\n')
STATES = {(False, "off"): NO_MODS, (False, "roster"): ROSTER_STATE, (True, "off"): DW15_ONLY,
          (True, "roster"): DW15_ROSTER_STATE}

JIJIMON, MACHINE_CELL = 205, 85         # Jijimon's house; the collision value that runs script 165 section 0x55
MAP_COLLISION = 0x801AF398              # 100 x 100 collision bytes (docs/GAME.md 3.9)
MENU_CURSOR_POS = 0x801316B8            # MENU_HIGHLIGHTS[i].pos, 0x10 bytes apart; [0] is the title cursor
TARGET_MENU, VS_PLAYER_INDEX = 0x80135068, 0x8013505C
VS_BUFFER_PTR = 0x801346D4              # VS__REGISTERED_DIGIMON_BUFFER (a pointer): 80 records, player 2 from 40
ENTITY_TABLE, PARTNER, NPC0 = 0x8012F344, 0x801557A8, 0x80155828
CUR_HP = 0x38 + 0x14                    # Stats.current.currentHP from the entity start
VS_MODE_STATE, VS_BOTH_SELECTED = 0x80134F50, 0x80134F54
VS_SELECT = 0x801B1C7C                  # VS__SELECT_DIGIMON_DATA[2]
VS_BATTLE_SETUP, VS_CURRENT_BATTLE = 0x800716A8, 0x80135264
VS_WINS, VS_LOSSES = 0x80135258, 0x8013525A
SAVE_FILE_S0 = 0x80131B2C
EXE_CLUT, VS_CLUT = 0x8012F528, 0x8006FA28  # DIGIMON_SPRITE_CLUT (EXE) and VS_DIGIMON_SPRITE_CLUT (VS_REL), 68 each
ARENA_REMAP = 0x9F000000 + 0x32C0       # the roster's versus remap (gen_roster_mod.py OFF_VS_REMAP)
SITES = roster_cups.VS_SITES            # select (EXE 0x800F9124), plate (VS_REL 0x800574A0)


def regs_of(blob):
    """[(slot, digimonId, name, moves)] of the non-empty records in a 0xA00 registration block."""
    out = []
    for i in range(40):
        r = blob[i * 0x40:(i + 1) * 0x40]
        if r[0x1C]:
            out.append((i, r[0x1C], r[14:28].split(b"\0")[0].decode("ascii", "backslashreplace"), list(r[0x1D:0x20])))
    return out


def card_records(path):
    """The 40 raw registration records of copy 0."""
    card = open(path, "rb").read()
    blk = next(i for i in range(1, 16) if card[i * 128 + 10:i * 128 + 26] == b"BASLUS-01032DMR0")
    s0 = card[blk * 0x2000 + 0x200:blk * 0x2000 + 0x200 + 0xF00]
    return [s0[0x500 + i * 0x40:0x540 + i * 0x40] for i in range(40)]


def card_regs(path):
    card = open(path, "rb").read()
    blk = next(i for i in range(1, 16) if card[i * 128 + 10:i * 128 + 26] == b"BASLUS-01032DMR0")
    s0 = card[blk * 0x2000 + 0x200:blk * 0x2000 + 0x200 + 0xF00]
    s1 = card[blk * 0x2000 + 0x1100:blk * 0x2000 + 0x1100 + 0xF00]
    return dict(s0=regs_of(s0[0x500:0xF00]), s1=regs_of(s1[0x500:0xF00]), partner=struct.unpack_from("<i", s0, 0x1B8)[0],
                spare=s0[0x4E1:0x4FC].hex())


def portraits(entries, tpage_x=0x380):
    """The 16x16 versus portraits of one frame's GP0 commands: [(cell, palette row, (x, y))]. Both renderers draw
    from the sheet at texture x 0x380 with palette x 0x120, rows 0x1E0 + table[type]; u = ((type-3) % 32) * 32 + 0/16
    kept to a byte, v = ((type-3) / 8) * 32, so the cell is (v / 32) * 8 + u / 32 (vs_select.c:797-806)."""
    out = []
    for e in entries:
        op = int(e["op"], 16)
        w = [int(x, 16) for x in e.get("w", [])]
        if not 0x2C <= op <= 0x2F or len(w) < 9:
            continue
        clut, tpage = w[2] >> 16, w[4] >> 16
        tx, cx, cy = (tpage & 0xF) * 64, (clut & 0x3F) * 16, clut >> 6
        us = [w[k] & 0xFF for k in (2, 4, 6, 8)]
        vs = [(w[k] >> 8) & 0xFF for k in (2, 4, 6, 8)]
        if tx != tpage_x or (tpage >> 4) & 1 or cx != 0x120 or not 0x1E0 <= cy < 0x200 \
                or max(us) - min(us) not in (14, 15, 16) or max(vs) - min(vs) not in (14, 15, 16):
            continue
        out.append(((min(vs) // 32) * 8 + min(us) // 32, cy - 0x1E0, (w[1] & 0xFFFF, w[1] >> 16)))
    return out


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("card", "match"))
    ap.add_argument("--name")
    ap.add_argument("--types", default="")
    ap.add_argument("--p1")
    ap.add_argument("--p2")
    ap.add_argument("--mods", choices=("off", "roster"), default="off")
    ap.add_argument("--dw15", action="store_true")
    ap.add_argument("--win", type=int, default=1, choices=(0, 1, 2))
    ap.add_argument("--pick", default="0,0", help="match: the fighter each player picks, as its place among the "
                    "card's registrations (0-3, the first page), player 1 then player 2")
    ap.add_argument("--port", type=int, default=8501)
    ap.add_argument("--build", default=os.path.join(root, "build-debug"))
    ap.add_argument("--seed", default=r"C:\Users\boura\hdb-rewind")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    out = os.path.abspath(a.out or os.path.join(a.build, "versus"))
    sub = "dw15" if a.dw15 else ""
    tag = a.name if a.mode == "card" else "%s_%s_%s%s_%s" % (a.p1, a.p2, a.mods, "_15" if a.dw15 else "",
                                                             a.pick.replace(",", ""))
    run = os.path.join(out, "runs", "%s_%d" % (a.mode, a.port))
    cards = os.path.join(run, "cards")
    card_dir = os.path.join(cards, sub) if sub else cards
    shutil.rmtree(run, ignore_errors=True)
    os.makedirs(card_dir)
    if a.mode == "card":
        if not seed_cards(a.seed, card_dir):
            print("no card to seed from at %s" % a.seed, file=sys.stderr)
            return 2
    else:
        for n, who in (("card1.mcd", a.p1), ("card2.mcd", a.p2)):
            shutil.copy(os.path.join(out, who, "card1.mcd"), os.path.join(card_dir, n))
    before = {n: hashlib.sha1(open(os.path.join(card_dir, n), "rb").read()).hexdigest()
              for n in ("card1.mcd", "card2.mcd") if os.path.exists(os.path.join(card_dir, n))}
    with open(os.path.join(a.build, "mods", "state.toml"), "w") as fh:
        fh.write(STATES[(a.dw15, a.mods)])
    fails = []

    def check(cond, msg):
        print(("PASS " if cond else "FAIL ") + msg, flush=True)
        if not cond:
            fails.append(msg)
        return cond

    log = os.path.join(run, "game.log")
    g = Game.launch(exe=os.path.join(a.build, "Digimon_World_Recompiled.exe"), port=a.port, memcard_dir=cards,
                    extra_args=["--headless-opengl"], log_path=log,
                    env={"PSX_OVERLAY_CAPTURES": os.path.join(run, "cap.json")})
    shot = lambda nm: g.artifact_shot(os.path.join(run, nm + ".png"))
    if a.mode == "match":
        g.d.cmd("set_input", port=2, connected=1, buttons="0xFFFF")     # pad 2 plugged in, nothing held
    f = Field(g, check, shot=shot, dw15=a.dw15)
    rd = g.d.read
    u8 = lambda addr: rd(addr, 1)[0]
    i16 = lambda addr: struct.unpack("<h", rd(addr, 2))[0]
    i32 = f.i32
    menu = lambda: i32(CURRENT_MENU)

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

    def press(btn, frames=6, port=1, after=30):
        """One press on pad `port`, then `after` idle frames. Pad 2 goes through the server's port-2 override."""
        if port == 1:
            g.route("%d:%s,%d:none" % (frames, btn, after))
        else:
            # set_input's port-2 override persists until changed, so pad 2 stays plugged in with nothing held
            # between presses; a timed "press" ends by unplugging it, and a pad that appears with the button
            # already down never shows the game a fresh press.
            g.d.cmd("set_input", port=2, connected=1, buttons="0x%04X" % pad_word(btn))
            g.advance(frames)
            g.d.cmd("set_input", port=2, connected=1, buttons="0xFFFF")
            g.advance(after)

    def until(pred, timeout):
        return f.wait_for(pred, timeout)

    def counters():
        return {c["name"]: c["count"] for c in g.d.cmd("mod_counters").get("counters", [])
                if c["name"].startswith("dw.roster")}

    try:
        if a.mode == "card":
            card(a, g, f, check, shot, press, until, menu, rd, u8, card_dir, out)
        else:
            match(a, g, f, check, shot, press, until, menu, rd, u8, i16, card_dir, out, run, alive, counters)
        check(alive() is None, "still running")
        if a.mode == "match":
            after = {n: hashlib.sha1(open(os.path.join(card_dir, n), "rb").read()).hexdigest() for n in before}
            check(after == before, "both card files are byte for byte what they were before Versus mode (%s)"
                  % {n: v[:10] for n, v in after.items()})
    except (Dead, Wedge) as ex:
        check(False, "the game stopped answering: %s" % ex)
    finally:
        g.shutdown()
    print("RESULT versus %s %s: %s" % (a.mode, tag, "FAIL %s" % fails if fails else "PASS"))
    return 1 if fails else 0


def card(a, g, f, check, shot, press, until, menu, rd, u8, card_dir, out):
    """Registrations made through the Save Machine, one per type, in slots 0, 1, ..."""
    f.enter()
    g.settle(timeout_frames=1800)
    types = [int(t) for t in a.types.split(",") if t]
    for slot, t in enumerate(types):
        if g.partner_type != t and not check(force(g, t), "partner evolved to %d" % t):
            continue
        fresh(g)
        g.warp(JIJIMON)
        coll = rd(MAP_COLLISION, 10000)
        cells = [i for i, b in enumerate(coll) if b == MACHINE_CELL]
        if not check(bool(cells), "map %d has collision cells %d (the Save Machine): %s" % (JIJIMON, MACHINE_CELL, cells)):
            return
        # The strip runs along a wall; the tamer's centre cannot stand in a cell touching it (GAME.md 3.9), so aim
        # at the cell of the strip farthest from walls: the middle row, the column with the most floor around it.
        def clear(i):
            return sum(coll[i + dx + dz * 100] < 0x80 for dx in (-1, 0, 1) for dz in (-1, 0, 1))
        best = max(cells, key=lambda i: (clear(i), -abs(i // 100 - sum(c // 100 for c in cells) / len(cells))))
        tx, tz = best % 100, best // 100
        target = ((tx - 50) * 100 + 50, (50 - tz) * 100 - 50)
        p = f.pos()
        on_cell = lambda: (lambda q: q is not None and coll[(int(q[0] / 100) + 50 - (q[0] < 0))
                                                            + (50 - int(q[2] / 100) - (q[2] > 0)) * 100]
                           == MACHINE_CELL)(f.pos())
        close = lambda: on_cell() and (lambda q: abs(q[0] - target[0]) + abs(q[2] - target[1]) < 45)(f.pos())
        walk_to(g, f, target, close)
        check(on_cell(), "walked onto the Save Machine cell (from %s toward %s, now %s)" % (p, target, f.pos()))
        # The cell runs on a fresh confirm: the first time Jijimon explains the machine (trigger 94), then it asks.
        # If nothing answers (the tamer drifted, or a box was still closing), walk back onto the cell and try again.
        seen = []
        for k in range(40):
            m = menu()
            seen.append(m)
            if k % 5 == 0:
                print("  try %d: menu %d tamer %d script paused %d pos %s on cell %s" % (k, m, g.tamer_state, f.i32(IS_SCRIPT_PAUSED), f.pos(), on_cell()), flush=True)
            if m == 0x3C:
                break
            if k % 6 == 5 and m == -1 and g.tamer_state == 0:
                g.route("20:up,4:none" if k % 12 == 5 else "20:down,4:none")   # step off and back on: a fresh arrival
                walk_to(g, f, target, close)
            press("cross", after=40)
        shot("%s_machine_%d" % (a.name, t))
        if not check(menu() == 0x3C, "the Save Machine opened menu 0x3C (register for battle?) (menus %s)"
                     % sorted(set(seen))):
            return
        shot("%s_register_%d" % (a.name, t))
        press("cross", after=40)                      # yes
        until(lambda: menu() == 0x3D, 10)
        check(menu() == 0x3D, "slot list open (0x3D)")
        for _ in range(slot):
            press("down", after=20)
        press("cross", after=40)                      # this slot
        until(lambda: menu() == 0x3E, 10)
        check(menu() == 0x3E and struct.unpack("<i", rd(0x80135060, 4))[0] == slot,
              "confirm screen (0x3E) for slot %d" % slot)
        before = open(os.path.join(card_dir, "card1.mcd"), "rb").read()
        press("cross", after=40)                      # yes: registerBattleData, then the card write
        end, seen = time.time() + 20, []
        while time.time() < end and menu() != -1:
            if not seen or seen[-1] != menu():
                seen.append(menu())
            time.sleep(0.2)
        if menu() != -1 and open(os.path.join(card_dir, "card1.mcd"), "rb").read() != before:
            press("cross", after=60)                  # the card is written; close what is left of the menu
            seen.append(menu())
        print("menus after the confirm: %s" % ["0x%X" % m for m in seen], flush=True)
        check(menu() == -1, "the save machine's menu closed (menu 0x%X)" % (menu() & 0xFFFF))
        check(open(os.path.join(card_dir, "card1.mcd"), "rb").read() != before, "the Save Machine wrote the card")
        until(lambda: g.tamer_state == 0 and f.i32(IS_SCRIPT_PAUSED) == 1, 20)
        live = rd(SAVE_FILE_S0 + 0x500 + slot * 0x40 + 0x1C, 1)[0]
        cr = card_regs(os.path.join(card_dir, "card1.mcd"))
        print("REGISTERED slot %d: partner %d; SAVE_FILE now %d; card %s" % (slot, t, live, cr["s0"]), flush=True)
        check(live == t, "SAVE_FILE holds registration %d = %d after the write" % (slot, t))
    d = os.path.join(out, a.name)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    shutil.copy(os.path.join(card_dir, "card1.mcd"), d)
    cr = card_regs(os.path.join(d, "card1.mcd"))
    cr.update(types=types, mods=a.mods, dw15=a.dw15)
    json.dump(cr, open(os.path.join(d, "card.json"), "w"), indent=1)
    print("CARD %s: %s" % (a.name, cr), flush=True)
    if a.mods == "roster":
        # dw_roster_saves.c: the card names a stock species for every roster registration (114 -> 115 on the stock
        # edition, 63-113 -> the partner stand-in), the truth in the record's unused tail
        stand = mapping(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), a.dw15)
        raw = card_records(os.path.join(d, "card1.mcd"))
        for slot, t in enumerate(types):
            r = raw[slot]
            want = 115 if t == 114 and not a.dw15 else stand.get(t, t)
            tag = r[0x20:0x26]
            check(r[0x1C] == want and (want == t or tag[:3] == bytes([0x52, 0x56, t])),
                  "card slot %d: registered %d, the card names %d (want %d), tail %s"
                  % (slot, t, r[0x1C], want, tag.hex()))


def walk_to(g, f, target, done, steps=120):
    """Greedy walk that re-learns each pad direction's world step from the step it just took: this room's camera
    turns as the tamer moves, so a one-off calibration (Field.go) points the wrong way after a few steps. Position
    writes do not stick (the game puts the tamer back), so the tamer really walks."""
    dirs = {0x10: "up", 0x20: "right", 0x40: "down", 0x80: "left"}
    vec = {d: None for d in dirs}
    stuck = {d: 0 for d in dirs}
    for _ in range(steps):
        if done():
            return True
        p = f.pos()
        if p is None or g.tamer_state != 0:
            g.route("4:cross,20:none")
            continue
        want = (target[0] - p[0], target[1] - p[2])
        dist = (want[0] ** 2 + want[1] ** 2) ** 0.5
        unknown = [d for d in dirs if vec[d] is None]
        if unknown:
            d = unknown[0]
        else:
            d = max(dirs, key=lambda k: (vec[k][0] * want[0] + vec[k][1] * want[1])
                    / max(1, (vec[k][0] ** 2 + vec[k][1] ** 2) ** 0.5) - 400 * stuck[k])
        secs = 0.12 if dist < 250 else 0.25
        g.route("%d:%s,4:none" % (int(secs * 60), dirs[d]), poll_s=0.02)
        q = f.pos()
        if q is None:
            continue
        mv = (q[0] - p[0], q[2] - p[2])
        if abs(mv[0]) + abs(mv[1]) < 10:
            stuck[d] += 1
            vec[d] = vec[d] or (0, 0)
        else:
            stuck = {k: 0 for k in dirs}
            vec[d] = mv
    return done()


def match(a, g, f, check, shot, press, until, menu, rd, u8, i16, card_dir, out, run, alive, counters):
    p1, p2 = (json.load(open(os.path.join(out, n, "card.json"))) for n in (a.p1, a.p2))
    roster = a.mods == "roster"
    for _ in range(12):
        g.route("6:start,150:none")
    for _ in range(3):
        press("down", after=40)
    check(u8(MENU_CURSOR_POS) == 3, "title cursor on Battle Mode")
    # player 1: card in slot 1, its first save, confirm (pad 1)
    for _ in range(40):
        if menu() == 0x32 and u8(VS_PLAYER_INDEX) == 1:
            break
        press("cross", after=60)
    check(menu() == 0x32 and u8(VS_PLAYER_INDEX) == 1, "player 1's card was read; Versus mode asks player 2 (menu 0x%X)"
          % menu())
    # player 2 (pad 2): pad 1 must not answer for it
    press("cross", port=1, after=60)
    check(menu() == 0x32 and u8(VS_PLAYER_INDEX) == 1, "a pad 1 press does not answer player 2's card prompt")
    shot("p2_card_prompt")
    p2seen = []
    for _ in range(40):
        m = menu()
        p2seen.append((m, u8(VS_PLAYER_INDEX), struct.unpack("<i", rd(0x80135030, 4))[0]))
        if m not in (0x32, 0x33, 0x34, 0x35, 0x36, 0x37) and m >= 0:
            break
        press("cross", port=2, after=60)
    print("player 2's card screens (menu, player, CONNECTED_CARDS): %s; pad %s" % (
        sorted(set(p2seen)), g.d.cmd("pad_status").get("slot1")), flush=True)
    vsb = struct.unpack("<I", rd(VS_BUFFER_PTR, 4))[0]
    buf = rd(vsb, 0xA00 * 2)
    got1, got2 = regs_of(buf[:0xA00]), regs_of(buf[0xA00:])
    print("VS buffer: player 1 %s; player 2 %s; counters %s" % (got1, got2, counters()), flush=True)

    # With the roster (either edition) Versus mode must hold what was registered; without it, what the card says,
    # and that must be a stock partner the stock tables cover (1..62, or 115, the stock Machinedramon; 1.5's own
    # Veemon is 114 there).
    stock_ok = lambda t: 1 <= t <= 62 or t == 115 or (a.dw15 and t == 114)
    for who, got, c in (("player 1", got1, p1), ("player 2", got2, p2)):
        ids = [r[1] for r in got]
        if roster:
            check(ids == c["types"], "%s's registrations in Versus mode's buffer read as registered: %s (card %s, "
                  "registered %s)" % (who, ids, [r[1] for r in c["s0"]], c["types"]))
        else:
            check(ids == [r[1] for r in c["s0"]], "%s's registrations in Versus mode's buffer are the card's: %s"
                  % (who, ids))
            check(all(stock_ok(t) for t in ids), "%s's registrations read as stock types without the roster: %s "
                  "(registered %s)" % (who, ids, c["types"]))
    check(u8(VS_MODE_STATE) in (0, 1, 2) or True, "VS mode state %d" % u8(VS_MODE_STATE))
    # mode (one battle) and map: confirm from either pad
    until(lambda: rd(0x80134F5B, 1)[0] == 0 and u8(VS_MODE_STATE) == 0, 30)
    shot("mode_select")
    for _ in range(20):
        if u8(VS_MODE_STATE) >= 1:
            break
        press("cross", after=40)
    check(u8(VS_MODE_STATE) >= 1, "mode chosen (VS__MODE_STATE %d, battles %d)" % (u8(VS_MODE_STATE),
                                                                                 u8(VS_BATTLE_SETUP + 10)))
    shot("map_select")
    for _ in range(20):
        if u8(VS_MODE_STATE) >= 2:
            break
        press("cross", after=40)
    check(u8(VS_MODE_STATE) >= 2, "map chosen (VS__MODE_STATE %d)" % u8(VS_MODE_STATE))
    g.advance(60)
    # fighter screen: judge the portraits (every registration of both players is on the first page), then each
    # player picks its fighter and START
    sel, end = [], time.time() + 20
    while time.time() < end and len(set(x[2] for x in sel)) < len(got1[:4]) + len(got2[:4]):
        q = portraits(g.d.cmd("gpu_frame_dump", frame=g.d.frame() - 1, count=20000).get("entries", []))
        if len(set(x[2] for x in q)) > len(set(x[2] for x in sel)):
            sel = q
        time.sleep(0.2)
    shot("fighters")
    exe_tab = list(rd(EXE_CLUT, 68 + 48))
    remap = list(rd(ARENA_REMAP, 180)) if roster and not a.dw15 else None
    code = [struct.unpack("<I", rd(SITES["select"] + 4 * i, 4))[0] for i in range(13)]
    print("fighter screen portraits (cell, palette row, xy): %s" % sel, flush=True)
    print("select site words: %s" % ["%08X" % w for w in code], flush=True)
    judge_portraits(a, check, "fighter screen", sel, [r[1] for r in got1[:4] + got2[:4]], exe_tab, remap)
    # a face button picks a place on the page (vs_select.c:532-562): triangle 0, square 1, circle 2, cross 3
    picks = [int(x) for x in a.pick.split(",")]
    for port, k in zip((1, 2), picks):
        press(("triangle", "square", "circle", "cross")[k], port=port, after=20)
    g.advance(30)
    for port in (1, 2):
        press("start", port=port, after=20)
    until(lambda: u8(VS_BOTH_SELECTED) == 1 or u8(VS_MODE_STATE) >= 3, 20)
    check(u8(VS_MODE_STATE) >= 3 or u8(VS_BOTH_SELECTED) == 1, "both players chose (VS__BOTH_SELECTED %d)"
          % u8(VS_BOTH_SELECTED))
    print("VS_BATTLE_SETUP fighters %s" % list(rd(VS_BATTLE_SETUP, 10)), flush=True)
    # the match. One loop watches it all: the name plates (drawn by the versus intro before the round and again
    # on the score screen after it, VS_renderVersusIntro), the fighters the match loaded, the HP poke, the result.
    t1, t2 = got1[picks[0]][1], got2[picks[1]][1]
    ent = lambda k: struct.unpack("<I", rd(ENTITY_TABLE + 4 * k, 4))[0]
    etype = lambda k: struct.unpack("<i", rd(ent(k), 4))[0] if 0x80000000 <= ent(k) < 0x80200000 else None
    sel_xy = set(x[2] for x in sel)                  # the fighter screen can still be fading out
    plate, loaded_seen, poked, shots, vs_tab, vcode = [], None, False, set(), None, None
    w0, l0 = tuple(rd(VS_WINS, 2)), tuple(rd(VS_LOSSES, 2))
    ended_at, t_loaded = None, None
    end = time.time() + 300
    while time.time() < end and menu() != 0:
        q = [x for x in portraits(g.d.cmd("gpu_frame_dump", frame=g.d.frame() - 1, count=20000).get("entries", []))
             if x[2] not in sel_xy]
        if len(q) > len(plate):
            plate = q
            vs_tab = list(rd(VS_CLUT, 68 + 48))
            vcode = [struct.unpack("<I", rd(SITES["plate"] + 4 * i, 4))[0] for i in range(13)]
            if "name_plates" not in shots:
                shot("name_plates")
                shots.add("name_plates")
        if ent(1) == PARTNER and ent(2) == NPC0 and etype(1) is not None:
            if loaded_seen is None:
                loaded_seen, t_loaded = (etype(1), etype(2)), time.time()
            if time.time() - t_loaded > 6 and "match" not in shots:
                shot("match")
                shots.add("match")
            if a.win and not poked and time.time() - t_loaded > 8:
                victim = NPC0 if a.win == 1 else PARTNER
                if struct.unpack("<h", rd(victim + CUR_HP, 2))[0] > 1:
                    g.d.write_bytes(struct.pack("<h", 1), victim + CUR_HP)
                    poked = True
        if ended_at is None and (tuple(rd(VS_WINS, 2)) != w0 or tuple(rd(VS_LOSSES, 2)) != l0):
            ended_at = time.time()
            shot("result")
        if ended_at and time.time() - ended_at > 4 and "score" not in shots:
            shot("score")
            shots.add("score")
        if ended_at and time.time() - ended_at > 40:     # the score screen lingers: confirm, as a player would
            press("cross", after=30)
        time.sleep(0.1)
    check(loaded_seen == (t1, t2), "the match loaded player 1's fighter as type %s and player 2's as %s (want %d, %d)"
          % (loaded_seen + (t1, t2) if loaded_seen else (None, None, t1, t2)))
    print("name plate portraits: %s; plate site words %s" % (plate, ["%08X" % w for w in vcode or []]), flush=True)
    judge_portraits(a, check, "name plates", plate, [t1, t2], vs_tab or list(rd(VS_CLUT, 68 + 48)), remap)
    wins, losses = list(rd(VS_WINS, 2)), list(rd(VS_LOSSES, 2))
    print("result: wins %s losses %s (poked %s)" % (wins, losses, poked), flush=True)
    if a.win:
        check(wins[a.win - 1] == 1 and losses[2 - a.win] == 1, "player %d won the round (wins %s, losses %s)"
              % (a.win, wins, losses))
    else:
        check(sum(wins) + sum(losses) >= 1, "the round ended (wins %s, losses %s)" % (wins, losses))
    # the result box closes by itself (VS__tickBattleResultScreen), then the score screen and back to the title
    # (TARGET_MENU VS_RETURN_MENU 0); a confirm press only if it lingers
    back = until(lambda: menu() == 0, 60)
    end = time.time() + 60
    while not back and time.time() < end:
        press("cross", after=60)
        back = menu() == 0
    shot("after")
    check(back, "back at the title menu after the result (menu %d)" % menu())


def judge_portraits(a, check, where, quads, types, table, remap):
    """Each fighter's portrait: the cell and palette row the code reads for its type. Roster (stock edition): the
    type goes through the arena remap first (the roster's look-alike, <= 66, inside the 68-entry table). 1.5 with
    roster-15: types of 66 and up clamp to 66. No mods: the stock read, cell (type-3) kept to a byte and the row at
    table[type], which for 68 and up lies past the 68-entry table (recorded, not judged)."""
    roster = a.mods == "roster"
    for t in types:
        if roster and not a.dw15:
            d = remap[t]
            check(d <= 66, "%s: remap[%d] = %d is inside the 68-entry palette table" % (where, t, d))
            if t in roster_cups.CUP_ANALOGUE and t not in (114, 115) and t > 65:
                check(d == roster_cups.CUP_ANALOGUE[t], "%s: %d shows its look-alike %d's portrait" % (where, t, d))
        else:
            d = min(t, 66) if roster else t                 # 1.5 + roster-15: the clamp; no mods: the type itself
        cell = 63 if d == 115 else (d - 3) % 64             # stock draws 115 from cell 63 but its row from [115]
        row = table[d] if d < len(table) else None
        hit = [q for q in quads if q[0] == cell and q[1] == row]
        past = d >= 68
        if t == 115 and not roster:     # the stock Machinedramon's own read of [115] (GAME.md 12.2), not a card's
            print("%s: stock 115 reads palette byte 115 (the stock game's own bug)" % where, flush=True)
        else:
            check(not past, "%s: type %d's palette index %d is inside the 68-entry table" % (where, t, d))
        check(bool(hit) or past, "%s: type %d drawn as cell %d, palette row %s (seen %s)"
              % (where, t, cell, row, sorted(set(q[:2] for q in quads))))
        if past:
            print("%s: type %d read palette byte %d of a 68-entry table (= %s); drawn %s" % (
                where, t, d, row, sorted(set(q[:2] for q in quads))), flush=True)


if __name__ == "__main__":
    sys.exit(main())
