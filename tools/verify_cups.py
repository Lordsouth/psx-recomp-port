#!/usr/bin/env python3
"""A real arena cup in the game: registration at File City's arena, the tournament (overlay STD), and the walk
back to the field (docs/GAME.md 15).

    py -3.12 tools/verify_cups.py --seed <card dir> [--port N]                  stock disc, no mods: a win, then a loss
    py -3.12 tools/verify_cups.py --seed <card dir> --roster 112 [--port N]      Expanded Roster, partner made type 112
    py -3.12 tools/verify_cups.py --seed <card dir> --roster 64 --revert-fix     the bracket fix written back to stock
    py -3.12 tools/verify_cups.py --seed <card dir> --roster 64 --dw15           Digimon World 1.5, roster-15
    py -3.12 tools/verify_cups.py --seed <card dir> --wide | --smooth60          one cup with that option on
    py -3.12 tools/verify_cups.py --seed <card dir> --partner 64                 no mods, the partner made type 64
    py -3.12 tools/verify_cups.py --seed <card dir> --roster 113 --savestate     a state saved and loaded inside the cup

Route: warp to the Arena Lobby (208), talk to Greymon at the desk, pick Grade D (cup 0) for today on the schedule and
enter, then walk up into the waiting room once it is open, which starts the cup (DG script 213 section 0x36). The
seed card is an early save, so three things are poked and nothing else: the recruit flags of the Grade D pool,
read from your disc (triggers 200 + species: a cup draws its opponents from them and its count of them), the hour (9:00 to register for
today, which closes at 10, then 12:00 to enter), and the partner's HP in fights (its foe's to 1 to win, its own to 1
to lose). A partner no cup lists cannot register (the schedule refuses it), so it is entered by writing what the
schedule writes: trigger 37, pstats 2 (day), 3 (cup) and 4 (species); the run says so.

Verdicts are guest-memory reads: tournament counters (TOURNAMENTS_WON, TOURNAMENT_WINS, TOURNAMENTS_LOST), the cup-won
trigger (15 + cup), pstat 255 (wins), the bits and the prize, the map and control after it. The bracket is judged from
the GPU commands the game sent (debug server gpu_frame_dump): every 16x16 portrait quad on the bracket sheets
(texture page 0x380/0x3C0, palette x 0x120/0x130) decoded to (sheet, cell, palette row), and the partner's quad must be
the one the fix draws: sheet (type-1)/64, cell (type-1)%64, row from the palette table at min(type, 111). With
--revert-fix the stock code and table pointers are written back into the loaded overlay first, and the partner's
quad must then be the stock one (as with no mods) (sheet type/64; for 112 and up the row read past the table). Screenshots are
artifacts only. Writes build-debug/mods/state.toml.
"""
import argparse
import json
import os
import shutil
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dwgame import Game  # noqa: E402
from field_drive import Field, IS_SCRIPT_PAUSED, MONEY  # noqa: E402
from gen_roster_mod import ARENA, OFF_STD_ICONS, STD_BRACKET, STD_ICON_DONORS  # noqa: E402
import roster_cups  # noqa: E402
from dw_tables import DEFAULT_BIN, Disc  # noqa: E402

LOBBY, CUP = 208, 0                                  # Arena Lobby; Grade D
DESK, STAIRS = (155, 445), (-815, 1150)              # in front of Greymon; below the waiting-room door
HOUR, DAY = 0x80134EBC, 0x80134F04
COUNTERS = 0x80134FCC                                # TOURNAMENTS_WON, TOURNAMENT_WINS, TOURNAMENTS_LOST (i16)
SCRIPT_STATE_PTR = 0x80134FB8                        # triggers at +0xF5, pstats at +0x159 (stock and 1.5)
PARTICIPANTS, PLAYER_SLOT, OPPONENT = 0x80135150, 0x8013514D, 0x801350E8
DGET_BUFFER, SCHED_COL = 0x801353A8, 0x801353AC      # the schedule's buffer; its column, then row
SPRITE_CLUT, PROJ_ICONS = 0x8007A49C, 0x8007A50C     # STD's 112-entry tables, resident during a cup
ICON_REFS = [(0x8005E250, 0x8005E254), (0x8005E29C, 0x8005E2A0), (0x8005E3B8, 0x8005E3BC), (0x8005E404, 0x8005E408)]
OFF = ('format_version = 2\n\n[[package]]\nid = "dw.vanilla.qol"\nversion = "1.0.0"\n\n'
       '[[feature]]\npackage_id = "dw.vanilla.qol"\nid = "end-box-fix"\nenabled = false\n')
PKG = '\n[[package]]\nid = "%s"\nversion = "1.0.0"\n\n[[feature]]\npackage_id = "%s"\nid = "%s"\nenabled = true\n'
WIDE = PKG % ("psx.enhancement.widescreen", "psx.enhancement.widescreen", "widescreen") + '[feature.values]\naspect = "16:9"\n'
SMOOTH = PKG % ("dw.smooth60", "dw.smooth60", "smooth60")
ROSTER = PKG % ("dw.vanilla.roster", "dw.vanilla.roster", "roster")
DW15 = PKG % ("dw.1_5", "dw.1_5", "dw15")
DW15_BIN = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "dw15-release",
                                        "patched", "digimon_world_1_5_test1.bin"))


def cup_pool(bin_path, cup):
    """A cup's opponent list from the owner's disc: DG script 213 section 0xA, entry `cup`, list 4
    (getCupDataJumpTable, src/main/tournament.c:146-166). Lists end at a byte of 0xFE or more."""
    dg = Disc(bin_path).read("DG.SCN")
    start, end = struct.unpack_from("<2I", dg, 213 * 4)
    sc = dg[start:end]
    p = 2
    while struct.unpack_from("<H", sc, p)[0] != 0xA:          # {u16 section, u16 offset} pairs
        assert struct.unpack_from("<H", sc, p)[0] != 0xFFFF, "script 213 has no section 0xA"
        p += 4
    sec = struct.unpack_from("<H", sc, p + 2)[0]
    sub = struct.unpack_from("<H", sc, sec + cup * 4 + 2)[0]
    q = struct.unpack_from("<H", sc, sub + 4 * 4 + 2)[0] + 2
    out = []
    while sc[q] < 0xFE:
        out.append(sc[q])
        q += 1
    return out


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", required=True)
    ap.add_argument("--port", type=int, default=7300)
    ap.add_argument("--build", default=os.path.join(root, "build-debug"))
    ap.add_argument("--out", help="folder for cards, log and screenshots (default build/cups_<mode>)")
    ap.add_argument("--roster", type=int, help="Expanded Roster on; the partner is made this species")
    ap.add_argument("--partner", type=int, help="no mods; the partner is made this species")
    ap.add_argument("--revert-fix", action="store_true", help="write the stock bracket code back (the control)")
    ap.add_argument("--dw15", action="store_true", help="Digimon World 1.5 under it (roster-15)")
    ap.add_argument("--wide", action="store_true", help="Widescreen 16:9 on")
    ap.add_argument("--smooth60", action="store_true", help="Smooth 60 FPS on")
    ap.add_argument("--savestate", action="store_true",
                    help="inside the cup: save a state at the bracket, wipe the arena's icons, load the state after "
                         "the first match; the icons must come back and the cup finish")
    ap.add_argument("--cup", type=int, default=0, help="the cup to enter (default 0, Grade D); must be on day 0")
    ap.add_argument("--refuse", action="store_true", help="the schedule must refuse this partner for --cup")
    ap.add_argument("--desk-state", action="store_true",
                    help="save a state at the desk right after registering, load it, then enter the cup")
    ap.add_argument("--script102", action="store_true", help="also decode script 102's lists on map 106 at the end")
    ap.add_argument("--bin", help="the disc image to read the cup's pool from (default: stock, or 1.5 with --dw15)")
    a = ap.parse_args()
    species = a.roster or a.partner
    mode = ("roster%d" % a.roster if a.roster else "partner%d" % a.partner if a.partner else "stock") + \
        ("_dw15" if a.dw15 else "") + ("_revert" if a.revert_fix else "") + ("_wide" if a.wide else "") + \
        ("_s60" if a.smooth60 else "") + ("_state" if a.savestate else "") + \
        ("_cup%d" % a.cup if a.cup else "") + ("_refuse" if a.refuse else "") + ("_desk" if a.desk_state else "")
    out = a.out or os.path.join(a.build, "cups_" + mode)
    cards = os.path.join(out, "cards", "dw15") if a.dw15 else os.path.join(out, "cards")
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(cards)
    for n in ("card1.mcd", "card2.mcd"):
        if os.path.exists(os.path.join(a.seed, n)):
            shutil.copy(os.path.join(a.seed, n), os.path.join(cards, n))
    with open(os.path.join(a.build, "mods", "state.toml"), "w") as fh:
        fh.write(OFF + (DW15 if a.dw15 else "") + (ROSTER if a.roster else "") + (WIDE if a.wide else "")
                 + (SMOOTH if a.smooth60 else ""))

    fails = []

    def check(cond, msg):
        print(("PASS " if cond else "FAIL ") + msg, flush=True)
        if not cond:
            fails.append(msg)

    g = Game.launch(exe=os.path.join(a.build, "Digimon_World_Recompiled.exe"), port=a.port,
                    memcard_dir=os.path.join(out, "cards"), extra_args=["--headless-opengl"],
                    log_path=os.path.join(out, "game.log"))
    shot = lambda name: g.artifact_shot(os.path.join(out, name + ".png"))
    f = Field(g, check, shot=shot, dw15=a.dw15)
    rd = g.d.read
    i16s = lambda addr, n: struct.unpack("<%dh" % n, rd(addr, 2 * n))
    word = lambda addr: struct.unpack("<I", rd(addr, 4))[0]
    state = lambda: word(SCRIPT_STATE_PTR)
    trig = lambda n: bool(rd(state() + 0xF5 + n // 8, 1)[0] >> (n % 8) & 1)
    pstat = lambda n: rd(state() + 0x159 + n, 1)[0]
    free = lambda: g.tamer_state == 0 and f.gstate() == 0 and f.i32(IS_SCRIPT_PAUSED) == 1
    vec = {0x20: (300, 0), 0x80: (-300, 0), 0x10: (0, 300), 0x40: (0, -300)}   # this room's camera: pad -> world

    def set_trig(n):
        b = rd(state() + 0xF5 + n // 8, 1)[0]
        g.d.write_bytes(bytes([b | 1 << (n % 8)]), state() + 0xF5 + n // 8)

    def clear_text():
        for _ in range(40):
            if free():
                return True
            g.route("6:cross,30:none")
        return free()

    def judge_lists(sn, when="after the load"):
        """The cup lists as the game holds them now (script `sn` in the buffer), against the disc's stock lists:
        stock entries identical, additions only roster ids (stock run: nothing added)."""
        active = struct.unpack("<H", rd(0x80134FC2, 2))[0]   # ACTIVE_MAP_SCRIPT
        check(active == sn, "script %d is the active one (%d)" % (sn, active))
        base = word(0x80134FB4)
        guest = b"".join(rd(base + o, 0x400) for o in range(0, 0x3000, 0x400))
        dg = Disc(a.bin or (DW15_BIN if a.dw15 else DEFAULT_BIN)).read("DG.SCN")
        lo, hi = struct.unpack_from("<2I", dg, sn * 4)
        _, stock = roster_cups.decode(dg[lo:hi])
        _, now = roster_cups.decode(guest)
        added, ok = [], True
        for cup in range(roster_cups.N_CUPS):
            for k in (1, 4):
                ok &= now[cup][k] == stock[cup][k]
            for k in (2, 3):
                o, n = roster_cups.members(stock[cup][k]), roster_cups.members(now[cup][k])
                ok &= n[:len(o)] == o and now[cup][k][-1] == stock[cup][k][-1]
                added += n[len(o):]
        lo_id = min(added) if added else None
        check(ok and (added == [] if not a.roster else (added and lo_id >= 63 and (114 not in added or not a.dw15))),
              "script %d's cup lists in guest RAM %s: stock entries, pools and requirements identical, %d roster "
              "ids added (lowest %s)" % (sn, when, len(added), lo_id))

    def bag():
        b = f.bag()
        return {b[i]: b[30 + i] for i in range(30) if b[i] != 0xFF}

    sched, judged = {}, []

    def make_partner(t):
        """The game's own evolution into t, as Field.measure and verify_roster15 force one."""
        if g.partner_type == t:                      # a stock partner kept as it is (--roster 3)
            return True
        for field, value in (("remaining_lifetime", 9999), ("poop_level", 0), ("tiredness", 0), ("script_sync_bit", 1),
                             ("evolution_target", t)):
            g.set(field, value)
        g.set_atomic(("tamer_state", 6), ("tamer_substate", 0))
        g.set_atomic(("partner_state", 13), ("partner_sub_state", 0))
        end = time.time() + 240
        while time.time() < end and g.partner_type != t:
            time.sleep(0.5)
        g.advance(180)
        for _ in range(60):
            if g.tamer_state == 0 and g.partner_state == 1:
                break
            g.route("4:cross,30:none")
        g.set("evolution_target", -1)
        return g.partner_type == t

    def register(cup, row=None):
        """Greymon, Yes, the schedule; today's column, the row holding `cup`; enter. -> how it registered."""
        g.d.write_bytes(struct.pack("<hh", 9, 0), HOUR)
        f.go(DESK, lambda: f.near(*DESK, r=80), limit=40, vec=vec)
        f.walk(0x10, 0.3)
        if not trig(91):                             # Greymon's first-visit speech
            g.route("6:cross,40:none")
            clear_text()
        for pause in (1.0, 1.0, 1.5):                # talk, the Yes/No, Yes: the schedule opens
            g.route("6:cross,40:none")
            time.sleep(pause)
        buf = rd(word(DGET_BUFFER), 30)
        print("schedule (5 days x 6, bit 0x80 open to the partner, 0x40 open with a penalty):", buf.hex(), flush=True)
        shot("schedule")
        if not judged:                               # the schedule has read the lists: script 213 is in the buffer
            judged.append(1)
            judge_lists(213)
            if a.desk_state:                         # a state taken here, at the desk, then loaded
                g.savestate("save", 1)
                g.advance(60)
                g.savestate("load", 1)
                g.advance(120)
                judge_lists(213, "after loading a state taken at the desk")
                buf = rd(word(DGET_BUFFER), 30)
        col0 = [b for b in buf[:6]]
        if row is None:
            row = next((i for i, b in enumerate(col0) if b != 0xFF and b & 0x3F == cup), None)
            check(row is not None, "cup %d is on today's schedule (column %s)" % (cup, buf[:6].hex()))
            row = row or 0
        sched[cup] = col0[row]
        for _ in range(row):
            g.route("6:down,30:none")
        check(rd(SCHED_COL, 2) == bytes([0, row]), "schedule cursor on today, row %d" % row)
        for _ in range(8):
            g.route("6:cross,40:none")
            time.sleep(0.7)
            if trig(37):
                break
        how = "ui"
        if not trig(37):                             # a red cup: the schedule refuses; leave it the player's way
            g.route("6:triangle,40:none")
            how = "poke"
        clear_text()
        if a.refuse:
            return how
        if how == "poke":                            # pstats first: a tick that sees trigger 37 with another
            p = state() + 0x159                      # species in pstat 4 drops the entry (tournament.c:52-56)
            g.d.write_bytes(bytes([i16s(DAY, 1)[0] & 0x7F, cup, g.partner_type]), p + 2)
            for n in (37, 38, 39):
                set_trig(n)
        return how

    def bracket_quads(frame):
        """The 16x16 portraits of one frame's GP0 commands: [(sheet, cell, row, (x, y))]."""
        r = g.d.cmd("gpu_frame_dump", frame=frame, count=20000)
        quads = []
        for e in r.get("entries", []):
            op = int(e["op"], 16)
            w = [int(x, 16) for x in e.get("w", [])]
            if not 0x2C <= op <= 0x2F or len(w) < 9:
                continue
            clut, tpage = w[2] >> 16, w[4] >> 16
            tx, cx, cy = (tpage & 0xF) * 64, (clut & 0x3F) * 16, clut >> 6
            us = [w[k] & 0xFF for k in (2, 4, 6, 8)]
            vs = [(w[k] >> 8) & 0xFF for k in (2, 4, 6, 8)]
            if tx not in (0x380, 0x3C0) or cx not in (0x120, 0x130) or not 0x1E0 <= cy < 0x1F0 \
                    or (tpage >> 7) & 3 != 0 or max(us) - min(us) not in (14, 15, 16):
                continue
            sheet = (tx - 0x380) // 64
            quads.append((sheet, (min(vs) // 32) * 8 + (min(us) + 1) // 32, cy - 0x1E0,
                          (w[1] & 0xFFFF, w[1] >> 16), cx == 0x120 + 16 * sheet))
        return quads

    def run_cup(win, tag):
        """Into the waiting room and through the cup. -> (game states seen, matches, bracket record)"""
        g.d.write_bytes(struct.pack("<hh", 12, 0), HOUR)
        f.go(STAIRS, lambda: f.near(*STAIRS, r=100), limit=60, vec=vec)
        for _ in range(4):                           # the 12:00 reminder can come first; then the door
            for _ in range(10):
                f.walk(0x10, 0.3)
                if not free():
                    break
            end = time.time() + 6
            while time.time() < end and f.gstate() != 5:
                time.sleep(0.2)
            if f.gstate() == 5:
                break
            for _ in range(40):                      # the reminder, or the penalty box (trigger 40): stop
                if f.gstate() == 5 or free():        # pressing the moment the cup starts
                    break
                g.route("6:cross,30:none")
        check(f.gstate() == 5, "%s: the cup started (GAME_STATE 5)" % tag)
        seen, matches, last_s, rec = set(), 0, None, {}
        best, reverted, k, saved, loaded = [], False, 0, False, False
        dumps = 0
        t0 = time.time()
        while time.time() - t0 < 300:
            s = f.gstate()
            seen.add(s)
            parts = list(rd(PARTICIPANTS, 8))
            slot = rd(PLAYER_SLOT, 1)[0]
            if a.revert_fix and not reverted and word(STD_BRACKET[0][0]) == STD_BRACKET[0][2]:
                for va, old, _new in STD_BRACKET:                   # the overlay is in: put the stock words back
                    g.d.write_bytes(struct.pack("<I", old), va)
                for lui, lo in ICON_REFS:
                    g.d.write_bytes(struct.pack("<I", 0x3C028008), lui)
                    g.d.write_bytes(struct.pack("<I", 0x2442A50C), lo)
                reverted = all(word(va) == old for va, old, _ in STD_BRACKET)
                print("control: stock bracket code back in the overlay: %s" % reverted, flush=True)
            if s == 5 and slot < 8 and parts[slot] == g.partner_type and matches == 0 and len(best) < 8 \
                    and time.time() - t0 < 30:      # the first bracket; a slow dump must not run into a match
                dumps += 1
                q = bracket_quads(g.d.frame() - 1)
                if len(q) > len(best):
                    best = q
                    rec = dict(participants=parts, slot=slot, quads=q,
                               clut_table=list(rd(SPRITE_CLUT, 112 + 3)),
                               code=["0x%08X" % word(va) for va, _, _ in STD_BRACKET],
                               icon_ref=["0x%08X" % word(x) for x in ICON_REFS[0]],
                               icons=list(rd(ARENA + OFF_STD_ICONS, 115)) if a.roster and not a.dw15 else None,
                               icons_stock=list(rd(PROJ_ICONS, 112)),
                               dw15_icon=[word(va + 4 * i) for va in roster_cups.DW15_ICON_SITES for i in range(4)]
                               + [word(roster_cups.DW15_ICON_HELPER + 4 * i) for i in range(6)] if a.dw15 else None)
                    if len(q) >= 8:
                        shot("%s_bracket" % tag)
                        if a.savestate and not saved:
                            g.savestate("save", 1)
                            g.d.write_bytes(bytes(115), ARENA + OFF_STD_ICONS)
                            g.advance(10)
                            rec["icons_after_wipe"] = list(rd(ARENA + OFF_STD_ICONS, 115))
                            saved = True
            if a.savestate and saved and not loaded and matches == 1 and s == 5:
                g.d.write_bytes(bytes(115), ARENA + OFF_STD_ICONS)
                g.savestate("load", 1)
                g.advance(30)
                rec["icons_after_load"] = list(rd(ARENA + OFF_STD_ICONS, 115))
                loaded, last_s = True, 5
                print("savestate loaded inside the cup (back at the first bracket)", flush=True)
            opp = word(OPPONENT)
            if s == 4:
                if last_s != 4:                     # a match is a stretch of GAME_STATE 4
                    matches += 1
                    shot("%s_match%d" % (tag, matches))
                if win:
                    if 0x80000000 <= opp < 0x80200000 and struct.unpack("<h", rd(opp + 0x4C, 2))[0] > 1:
                        g.d.write_bytes(struct.pack("<h", 1), opp + 0x4C)
                    g.set("current_hp", max(g.hp, 1))
                elif g.current_hp > 1:
                    g.set("current_hp", 1)
            if s == 0 and 5 in seen and time.time() - t0 > 10:
                break
            last_s = s
            if k % 20 == 0 and s == 5 and matches == 3:
                shot("%s_end%d" % (tag, k))
            k += 1
            time.sleep(0.25)
        return seen, matches, rec

    def judge_bracket(rec, t):
        if not rec:
            check(False, "a bracket frame with the partner in it was captured")
            return
        quads, table = rec["quads"], rec["clut_table"]
        print("bracket: participants %s, player slot %d, code %s, icon table ref %s"
              % (rec["participants"], rec["slot"], rec["code"], rec["icon_ref"]), flush=True)
        print("bracket quads (sheet, cell, palette row, xy, palette on its own sheet): %s" % quads, flush=True)
        check(len(quads) >= 8, "%d bracket portraits decoded from the frame's GPU commands" % len(quads))
        stock_code = a.revert_fix or not a.roster
        if stock_code:
            want = (t // 64, (t - 1) % 64, table[t] if t < 112 + 3 else None)
            what = "the stock read: sheet type/64, cell (type-1)%%64, row from table[%d]" % t
        else:
            want = (((t - 1) // 64), (t - 1) % 64, table[min(t, 111)])
            what = "the fixed read: sheet (type-1)/64, cell (type-1)%%64, row from table[%d]" % min(t, 111)
        got = [q for q in quads if q[:3] == want]
        check(bool(got), "partner %d's portrait is %s = %s" % (t, what, want))
        if not stock_code:
            for p in rec["participants"]:
                ok = any(q[:3] == ((p - 1) // 64, (p - 1) % 64, table[min(p, 111)]) for q in quads)
                check(ok, "participant %d drawn from its own cell" % p)

    try:
        f.enter()
        check(True, "in the world; partner %d" % g.partner_type)
        if a.roster:
            text = open(os.path.join(out, "game.log"), errors="replace").read()
            check(("roster-15 activated" if a.dw15 else "dw_roster: arena") in text,
                  "the %s edition of the roster runs" % ("1.5" if a.dw15 else "stock"))
        g.warp(LOBBY)
        check(f.screen() == LOBBY, "warped to the Arena Lobby (%d)" % LOBBY)
        if species:
            check(make_partner(species), "the partner evolved into %d" % species)
            for _ in range(3):                       # the evolution's last box can still hold the tamer
                clear_text()
                try:
                    g.warp(LOBBY)
                    break
                except Exception as e:  # noqa: BLE001  (dwdbg.Wedge)
                    print("warp retry: %s" % e, flush=True)
        CUP = a.cup
        pool = cup_pool(a.bin or (DW15_BIN if a.dw15 else DEFAULT_BIN), 0)
        if CUP:                                      # its own pool too: a cup opens on recruits from its pool
            pool += cup_pool(a.bin or (DW15_BIN if a.dw15 else DEFAULT_BIN), CUP)
        print("pool flags from the disc: %d species" % len(pool), flush=True)
        check(len(pool) >= 7, "the cup's pool was read from the disc")
        for t in pool:
            set_trig(200 + t)
        clear_text()
        before = dict(counters=i16s(COUNTERS, 3), money=f.i32(MONEY), bag=bag())
        print("before: counters (won, wins, lost) %s, bits %d, bag %s" % (before["counters"], before["money"],
                                                                          before["bag"]), flush=True)

        how = register(CUP)
        flag = sched.get(CUP, 0xFF)
        if a.refuse:
            check(how == "poke" and not trig(37) and flag & 0xC0 == 0,
                  "the schedule refuses partner %d for cup %d: its byte 0x%02X has neither 0x80 nor 0x40, trigger 37 "
                  "stays clear" % (g.partner_type, CUP, flag))
            return 1 if fails else 0
        check(trig(37) and pstat(3) == CUP and pstat(4) == g.partner_type,
              "registered for cup %d by %s: schedule byte 0x%02X, trigger 37, pstat 3 = %d, pstat 4 = %d"
              % (CUP, "the schedule" if how == "ui" else "poke (the schedule refused this species)", flag,
                 pstat(3), pstat(4)))
        if a.roster:
            check(how == "ui", "the roster species registered through the schedule itself")

        seen, matches, rec = run_cup(True, "win")
        after = i16s(COUNTERS, 3)
        check(matches == 3 + (1 if a.savestate else 0), "three matches fought (%d, one replayed after a load)" % matches
              if a.savestate else "three matches fought (%d)" % matches)
        check(after[0] == before["counters"][0] + 1 and after[1] == before["counters"][1] + 3 and trig(15 + CUP)
              and pstat(255) == 3, "cup won: counters %s -> %s, trigger %d set, pstat 255 = %d"
              % (before["counters"], after, 15 + CUP, pstat(255)))
        clear_text()
        b = bag()
        won = [t for t in b if b[t] > before["bag"].get(t, 0)]
        check(f.i32(MONEY) > before["money"] and won, "prize paid: bits %d -> %d, new item %s in the bag"
              % (before["money"], f.i32(MONEY), won))
        check(free() and f.screen() == LOBBY, "back in the Arena Lobby in control (map %d)" % f.screen())
        print("hour after the cup: %s" % (i16s(HOUR, 2),), flush=True)
        shot("win_after")
        judge_bracket(rec, g.partner_type)
        if not (species or a.wide or a.smooth60):        # a loss, on the plain stock run only
            how = register(CUP + 1, 1)
            check(trig(37) and pstat(3) == CUP + 1, "registered for Grade C by %s" % how)
            mid = i16s(COUNTERS, 3)
            seen, matches, _ = run_cup(False, "lose")
            after = i16s(COUNTERS, 3)
            check(after[2] == mid[2] + 1 and after[0] == mid[0] and pstat(255) == 0 and not trig(16),
                  "cup lost in round one: counters %s -> %s, pstat 255 = %d, trigger 16 clear" % (mid, after, pstat(255)))
            pre = b
            clear_text()
            got = [t for t, n in bag().items() if n > pre.get(t, 0)]
            check(bool(got), "a consolation item in the bag: %s" % got)
            check(free() and f.screen() == LOBBY, "back in the Arena Lobby in control (map %d)" % f.screen())
            shot("lose_after")
        if a.roster and not a.dw15:
            icons = list(rd(ARENA + OFF_STD_ICONS + 112, 3))
            check(rec.get("icon_ref") and int(rec["icon_ref"][0], 16) & 0xFFFF == (ARENA >> 16)
                  if not a.revert_fix else True, "the projectile icon reads point at the arena: %s" % rec.get("icon_ref"))
            print("arena projectile icons for 112..114: %s" % icons, flush=True)
            if not a.revert_fix and rec.get("icons"):
                want = rec["icons_stock"] + [rec["icons_stock"][STD_ICON_DONORS[t]] for t in (112, 113, 114)]
                check(rec["icons"] == want, "at the first bracket the arena holds STD's own icon table plus donor "
                      "rows %s (copied at run time)" % want[112:])
            if a.savestate:
                check(rec.get("icons_after_wipe") == want, "icons rewritten within 10 frames of a wipe")
                check(rec.get("icons_after_load") == want, "icons right after a savestate load inside the cup")
        if a.dw15 and a.roster and rec.get("dw15_icon"):
            want = roster_cups.DW15_ICON_SITE_NEW * 4 + roster_cups.DW15_ICON_HELPER_WORDS
            check(rec["dw15_icon"] == want, "1.5: the four icon reads call the clamp helper at 0x%08X, which is in RAM"
                  % roster_cups.DW15_ICON_HELPER)
        if a.script102:
            g.warp(106)
            g.advance(180)
            judge_lists(102)
        if a.smooth60:
            check("temporal blending" in open(os.path.join(out, "game.log"), errors="replace").read(),
                  "Smooth 60 FPS is active (the runtime's temporal-blending line is in the log)")
        if a.wide:
            from PIL import Image
            w, h = Image.open(os.path.join(out, "win_after.png")).size
            check(w * 9 >= h * 16 - 9, "Widescreen is active: the screenshot is %dx%d" % (w, h))
        try:
            cnt = {c["name"]: c.get("value", c.get("count")) for c in g.d.cmd("mod_counters").get("counters", [])
                   if str(c.get("name", "")).startswith("roster.cups")}
            print("counters: %s" % cnt, flush=True)
            if a.roster:
                check(not cnt.get("roster.cups.unknown"), "no script was left stock for not matching (%s)" % cnt)
        except Exception as e:  # noqa: BLE001  (an older runtime without mod_counters)
            print("counters unavailable: %s" % e, flush=True)
        f0 = g.d.frame()
        time.sleep(2)
        check(g.d.frame() > f0 + 30, "the game runs on")
        with open(os.path.join(out, "bracket.json"), "w") as fh:
            json.dump(rec, fh)
    except Exception as e:
        fails.append("exception: %r" % e)
        raise
    finally:
        g.shutdown()
        print("RESULT cups %s: %s" % (mode, "FAIL %s" % fails if fails else "PASS"), flush=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
