#!/usr/bin/env python3
"""The four stock bug fixes in dw.vanilla.qol, in the game (docs/GAME.md 12.2).

Each mode sets the bug up by poking RAM, then lets the game's own code run the buggy path and
reads the result from guest memory. --off runs the same setup with that fix unticked: the
control, where the stock bug must show.

  evoitem   Evolution Item Fix. HAS_USED_EVOITEM is set as an evolution item sets it, then the
            partner evolves twice. Fixed: the flag is 0 after the first, and the second gains
            stats. Stock: the flag stays 1 and the second gains nothing.
  sleep     Sleep Healing Fix. Full HP and MP at 9999, a Rest Pillow in the bag and a 24-hour
            sleep: sleepRegen's sum passes 32767. Fixed: HP and MP end at 9999. Stock: negative.
  techwipe  Lost Techniques Fix. A known set of learned moves, one life left, Tamer level 0, then
            a lost battle: lostAllLives makes the partner forget one move. Fixed: exactly that
            move (both bits of a learned pair) is gone. Stock: others go with it.
            --set word: moves 1, 2, 3 and 16-19. --set pairs: 44, 48, 50, 55, 57. --set late: 50, 55, 57
            (a pick is random: late picks the 55/57 pair 2 times in 3).
  jukebox   Jukebox Freeze Fix. padWithSpaces with a count below 0, as a long jukebox title gives
            it. See the mode's docstring for how it gets there.

  --dw15    on Digimon World 1.5 (cards live in dw15/). There Evolution Item Fix gives way and dw15
            makes its write itself, so --off does not apply to evoitem.
  --roster  Expanded Roster ticked too (its own clear of HAS_USED_EVOITEM runs beside the fix).

Every run first reads each main_exe fix's instruction back from guest memory; evoitem also reads
the EVL_REL.BIN one while the overlay is resident during the evolution.

  cmake --build build-debug --target psx-runtime
  py -3.12 tools/verify_bugfixes.py evoitem [--off] [--dw15] [--roster] --seed <card dir> --port N
"""
import argparse
import os
import shutil
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dwgame import Game  # noqa: E402
from field_drive import (Field, IS_SCRIPT_PAUSED, PARTNER_MAX_HP, PARTNER_STATS, PARTNER_LIVES,  # noqa: E402
                         BATTLE)
from gen_qol_mods import BUGFIXES  # noqa: E402  the writes, to read back

FIXES = {"evoitem": "evo-item-fix", "sleep": "sleep-heal-fix", "techwipe": "tech-wipe-fix",
         "jukebox": "jukebox-fix"}
STATE = 'format_version = 2\n\n[[package]]\nid = "dw.vanilla.qol"\nversion = "1.0.0"\n\n'
FEATURE = '[[feature]]\npackage_id = "dw.vanilla.qol"\nid = "%s"\nenabled = %s\n\n'
DW15 = ('[[package]]\nid = "dw.1_5"\nversion = "1.0.0"\n\n'
        '[[feature]]\npackage_id = "dw.1_5"\nid = "dw15"\nenabled = true\n\n')
ROSTER = ('[[package]]\nid = "dw.vanilla.roster"\nversion = "1.0.0"\n\n'
          '[[feature]]\npackage_id = "dw.vanilla.roster"\nid = "roster"\nenabled = true\n\n')
ON_15 = ("jukebox-fix", "tech-wipe-fix", "sleep-heal-fix")   # evo-item-fix gives way to dw15


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=sorted(FIXES))
    ap.add_argument("--off", action="store_true", help="the fix unticked: the stock bug must show")
    ap.add_argument("--set", choices=sorted(MOVE_SETS), default="word", help="techwipe: which learned moves")
    ap.add_argument("--build", default=os.path.join(root, "build-debug"))
    ap.add_argument("--port", type=int, default=7000)
    ap.add_argument("--seed", default=os.path.join(root, "saves"))
    ap.add_argument("--dw15", action="store_true", help="on Digimon World 1.5")
    ap.add_argument("--roster", action="store_true", help="Expanded Roster ticked too")
    a = ap.parse_args()
    if a.dw15 and a.off and a.mode == "evoitem":
        ap.error("on 1.5 dw15 makes the evo-item write itself: there is no off case")

    tags = "".join("_" + t for t in ("off", "dw15", "roster") if getattr(a, t))
    out = os.path.join(a.build, "bf_%s%s" % (a.mode, tags))
    cards = os.path.join(out, "cards")
    card_dir = os.path.join(cards, "dw15") if a.dw15 else cards     # 1.5 keeps its own cards (dw_dw15.c)
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(card_dir)
    for name in ("card1.mcd", "card2.mcd"):
        if a.mode != "jukebox" and os.path.exists(os.path.join(a.seed, name)):   # jukebox: blank cards, NEW GAME
            shutil.copy(os.path.join(a.seed, name), card_dir)
    if a.mode != "jukebox" and not os.path.exists(os.path.join(card_dir, "card1.mcd")):
        print("No memory card to seed from at %s" % a.seed, file=sys.stderr)
        return 2
    # Every fix named, so the run does not depend on defaults; only the one under test may be off.
    # On 1.5 the fix that gives way to dw15 is left to its default, as verify_endfix15.py leaves end-box-fix.
    with open(os.path.join(a.build, "mods", "state.toml"), "w") as fh:
        fh.write(STATE + "".join(FEATURE % (fid, "false" if a.off and m == a.mode else "true")
                                 for m, fid in sorted(FIXES.items()) if not a.dw15 or fid in ON_15)
                 + (DW15 if a.dw15 else "") + (ROSTER if a.roster else ""))

    fails = []

    def check(cond, msg):
        print(("PASS " if cond else "FAIL ") + msg, flush=True)
        if not cond:
            fails.append(msg)

    g = Game.launch(exe=os.path.join(a.build, "Digimon_World_Recompiled.exe"), port=a.port, memcard_dir=cards,
                    extra_args=["--headless-opengl"], log_path=os.path.join(out, "game.log"))
    shot = lambda n: g.artifact_shot(os.path.join(out, n + ".png"))
    f = Field(g, check, shot, a.dw15)
    try:
        if a.mode != "jukebox":                       # jukebox starts a NEW GAME instead
            f.enter()
            check(True, "in the world on map %d" % f.screen())
            readback(g, check, a)
        {"evoitem": evoitem, "sleep": sleep, "techwipe": techwipe, "jukebox": jukebox}[a.mode](g, f, check, a)
    except Exception as e:  # a hang or a dead game is a result too
        check(False, "%s: %s" % (type(e).__name__, e))
    finally:
        shot("end")
        g.shutdown()
        print("RESULT %s%s: %s" % (a.mode, tags.replace("_", " --"), "FAIL %s" % fails if fails else "PASS"),
              flush=True)
    return 1 if fails else 0


GAME_PROBE = (0x800ED2A0, 0x3C028013, 0x3C02801F)    # lui v0,0x8013 stock, lui v0,0x801F 1.5 (its moved table)


def readback(g, check, a):
    """Which executable runs, then each main_exe fix's instruction as guest memory holds it: the new
    word when ticked, stock when not."""
    addr, stock_word, w15 = GAME_PROBE
    got = struct.unpack("<I", g.d.read(addr, 4))[0]
    check(got == (w15 if a.dw15 else stock_word), "the game running is %s: 0x%08X holds %08X"
          % ("1.5" if a.dw15 else "stock", addr, got))
    for mode, fid in sorted(FIXES.items()):
        on = not (a.off and mode == a.mode)
        for where, vaddr, stock, new, _note in next(w for i, _n, _d, w in BUGFIXES if i == fid):
            if where != "exe":
                continue
            got = struct.unpack("<I", g.d.read(vaddr, 4))[0]
            check(got == (new if on else stock),
                  "%s 0x%08X holds %08X (%s)" % (fid, vaddr, got, "fixed" if on else "stock"))


EVL_SITE = next(w for i, _n, _d, w in BUGFIXES if i == "evo-item-fix")[0]
EVL_BRANCH = 0x10430002                               # beq v0,v1,+2 just before the site: the overlay is resident


def evolve(g, f, check, target, evl=None, item=False):
    """The game's own evolution sequence to `target`, started the way verify_newmap.py measure starts it.
    `evl`, a list, gets the EVL_REL.BIN site's word whenever the overlay is resident during the sequence.
    `item`: HAS_USED_EVOITEM set once the sequence has begun, as handleEvoItems leaves it
    (src/main/item.c:2211). Set while the partner idles, the roster's own clear would wipe it first."""
    for field, value in (("remaining_lifetime", 9999), ("poop_level", 9999), ("tiredness", 0),
                         ("script_sync_bit", 1), ("evolution_target", target)):
        g.set(field, value)
    g.set_atomic(("tamer_state", 6), ("tamer_substate", 0))
    g.set_atomic(("partner_state", 13), ("partner_sub_state", 0))
    if item:
        g.set("has_used_evoitem", 1)

    def done():
        if evl is not None:
            b = struct.unpack("<2I", g.d.read(EVL_SITE[1] - 4, 8))
            if b[0] == EVL_BRANCH:
                evl.append(b[1])
        return g.partner_type == target and g.evolution_target == -1 and (g.partner_state == 1 or g.tamer_state == 20)
    ok = f.wait_for(done, 240)
    for _ in range(12):
        if g.tamer_state == 0 and g.partner_state == 1:
            break
        g.route("4:cross,45:none")
    g.settle()
    f.wait_for(lambda: g.tamer_state == 0 and f.i32(IS_SCRIPT_PAUSED) == 1, 30)
    g.advance(120)
    check(ok, "the partner evolved to %d" % target)


def evoitem(g, f, check, a):
    stats = lambda: struct.unpack("<4h", g.d.read(PARTNER_STATS, 8)) + struct.unpack("<2h", g.d.read(PARTNER_MAX_HP, 4))
    # Below MetalGreymon's gains, so a natural evolution raises them all.
    g.d.write_bytes(struct.pack("<4h", 100, 100, 100, 100), PARTNER_STATS)
    g.d.write_bytes(struct.pack("<4h", 1000, 1000, 1000, 1000), PARTNER_MAX_HP)
    before = stats()
    evl = []
    evolve(g, f, check, 12, evl, item=True)  # MetalGreymon
    _w, vaddr, stock, new, _n = EVL_SITE
    on = not a.off
    print("MEASURE EVL_REL.BIN 0x%08X while resident: %s" % (vaddr, sorted({"%08X" % w for w in evl})), flush=True)
    check(bool(evl) and all(w == (new if on else stock) for w in evl),
          "EVL_REL.BIN 0x%08X held the %s word while the overlay was resident" % (vaddr, "fixed" if on else "stock"))
    flag, first = g.has_used_evoitem, stats()
    print("MEASURE first evolution: flag 1 -> %d, stats %s -> %s" % (flag, before, first), flush=True)
    check(first == before, "the evolution right after the item skipped its gains, as stock does")
    evolve(g, f, check, 12)
    second = stats()
    print("MEASURE second evolution: flag %d, stats %s -> %s" % (g.has_used_evoitem, first, second), flush=True)
    if a.off:
        check(flag == 1, "stock: the flag survived the evolution (%d)" % flag)
        check(second == first, "stock: the later evolution gained nothing")
    else:
        check(flag == 0, "fixed: the evolution cleared the flag (%d)" % flag)
        check(all(s > t for s, t in zip(second, first)), "fixed: the later evolution raised every stat")


PARA = 0x80138460                                     # PARTNER_PARA: sleepyHour +4, wakeupHour +8, hoursAsleepDefault +0xE
HOUR, MINUTE = 0x80134EBC, 0x80134EBE
REST_PILLOW = 0x22
AREA_RESPONSE = 0x8014F084 + 7098                    # MAP_TILE_DATA.partnerAreaResponse


def sleep(g, f, check, a):
    h = lambda addr: struct.unpack("<h", g.d.read(addr, 2))[0]
    wake = h(PARA + 8)
    # 24 hours asleep: the clock on the wake hour, minute 0 (src/main/partner_impl.c:1341-1347). A sleeper
    # of 8 hours, so the factor is 2400 / 8 * 1.2 with the pillow = 360 and the heal about 26000-28400.
    g.d.write_bytes(struct.pack("<h", 8), PARA + 0xE)
    g.d.write_bytes(struct.pack("<2h", wake, 0), HOUR)
    f.set_bag([(REST_PILLOW, 1)])
    area = struct.unpack("<b", g.d.read(AREA_RESPONSE, 1))[0]
    g.d.write_bytes(bytes([0]), AREA_RESPONSE)      # neither liked (x1.2) nor disliked (x0.8) here
    g.d.write_bytes(struct.pack("<4h", 9999, 9999, 9999, 9999), PARTNER_MAX_HP)
    g.set_atomic(("partner_state", 3), ("partner_sub_state", 0))      # partnerTickSleep from its start
    # sleepRegen runs as the fade to black ends, in sub-state 5, which then moves to 6 (src/main/partner.c:324-338)
    slept = f.wait_for(lambda: g.partner_sub_state >= 6 or g.partner_state != 3, 60)
    hp, mp = h(PARTNER_MAX_HP + 4), h(PARTNER_MAX_HP + 6)
    print("MEASURE after sleepRegen: wake hour %d, area response was %d, HP %d / 9999, MP %d / 9999 (partner state %d.%d)"
          % (wake, area, hp, mp, g.partner_state, g.partner_sub_state), flush=True)
    check(slept and g.partner_state == 3, "the partner slept (sleepRegen ran)")
    if a.off:
        check(hp < 0 and mp < 0, "stock: HP and MP wrapped negative (%d, %d)" % (hp, mp))
    else:
        check(hp == 9999 and mp == 9999, "fixed: HP and MP healed to full (%d, %d)" % (hp, mp))
    for _ in range(40):                                  # through the save prompt, back to the field
        if g.partner_state == 1 and g.tamer_state == 0:
            break
        g.route("4:cross,30:none")             # "Go to sleep"
    check(g.partner_state == 1 and g.tamer_state == 0, "awake again, player in control")


TAMER_LEVEL = 0x8015576C + 0x38                       # TAMER_ENTITY.tamerLevel
LEARNED = 0x801557A8 + 0x58                           # PARTNER_ENTITY.learnedMoves[2]
NPCS, NPC_STRIDE, NPC_TABLE, TAMER = 0x80155828, 0x68, 0x8012F344 + 8, 0x8015576C
PAIRS = {44: {44, 48}, 48: {44, 48}, 55: {55, 57}, 57: {55, 57}}
MOVE_SETS = {"word": [1, 2, 3, 16, 17, 18, 19], "pairs": [44, 48, 50, 55, 57], "late": [50, 55, 57]}


def wild_fight(g, f, check):
    """Into a fight with a wild Digimon on DGHA01, the way verify_endfix15.py starts one."""
    def location(entity):
        ptr = struct.unpack("<I", g.d.read(entity + 4, 4))[0]
        return ptr + 120 if 0x80000000 <= ptr < 0x80200000 else None

    def on_map():
        found = []
        for i in range(8):
            if g.d.read(NPCS + i * NPC_STRIDE + 0x34, 1)[0] == 1:
                loc = location(struct.unpack("<I", g.d.read(NPC_TABLE + i * 4, 4))[0])
                if loc:
                    found.append(struct.unpack("<3i", g.d.read(loc, 12)))
        return found
    row = next(r for r in range(256) if g.map_record(r)["file"] == "DGHA01")
    g.warp(row, 0)
    g.advance(120)
    npcs = on_map()
    check(bool(npcs), "a wild Digimon is on DGHA01 (row %d)" % row)
    end = time.time() + 60
    while npcs and time.time() < end and f.gstate() not in BATTLE:
        loc, target = location(TAMER), (on_map() or npcs)[0]
        if loc:
            g.d.write_bytes(struct.pack("<3i", target[0] + 100, target[1], target[2] + 100), loc)
        g.route("6:cross,20:none")
    check(f.gstate() in BATTLE, "the fight is on")
    return row


def techwipe(g, f, check, a):
    known = MOVE_SETS[a.set]
    words = [0, 0]
    for m in known:
        words[m // 32] |= 1 << (m % 32)
    moves = lambda: {i for i in range(64) if struct.unpack("<2I", g.d.read(LEARNED, 8))[i // 32] >> (i % 32) & 1}
    row = wild_fight(g, f, check)
    # One life left and Tamer level 0: lostAllLives forgets round(20 % of the known moves) = 1 here
    # (src/main/script_common.c:2485-2494). pstat 0xF3 gets the move it picked (:2515).
    g.d.write_bytes(struct.pack("<2I", *words), LEARNED)
    g.d.write_bytes(bytes([1]), PARTNER_LIVES)
    g.d.write_bytes(bytes([0]), TAMER_LEVEL)
    g.d.write_bytes(bytes([0xFF]), 0x801BE031 + 0xF3)
    f.partner(1, 1)
    picked, end = 0xFF, time.time() + 300
    while time.time() < end:
        if f.gstate() in BATTLE and struct.unpack("<h", g.d.read(PARTNER_MAX_HP + 4, 2))[0] > 1:
            g.d.write_bytes(struct.pack("<h", 1), PARTNER_MAX_HP + 4)
        picked = f.pstat(0xF3)
        if picked != 0xFF:
            break
        g.route("4:cross,20:none")
    after = moves()
    lost = set(known) - after
    print("MEASURE lostAllLives: known %s, picked %d, lost %s, left %s, words %08X %08X"
          % (known, picked, sorted(lost), sorted(after), *struct.unpack("<2I", g.d.read(LEARNED, 8))), flush=True)
    check(picked in known, "the battle was lost on the last life and lostAllLives forgot a move (%d)" % picked)
    want = PAIRS.get(picked, {picked})
    if a.off:
        check(lost > want, "stock: more than the picked move went (%s)" % sorted(lost - want))
    else:
        check(lost == want and after == set(known) - want, "fixed: exactly %s went" % sorted(want))
    f.shot("forgot")


NAMING_BUFFER, NAMING_CURRENT_LETTER, TEXTBOX_LINES_PTR = 0x801B1D1C, 0x80134F82, 0x80134FBC


def jukebox(g, f, check, a):
    """The jukebox menu itself sits behind a script routine of a late map, out of reach here. Its
    bug is padWithSpaces(out, 12, len) with a title too long for the row, so the count starts below 0
    (src/main/script_common.c:2919-2940). The naming screen of a NEW GAME calls the same function as
    padWithSpaces(out, 6, len) on the name (updateNamingPreview, src/shop/script_menu.c:3010-3013), and
    the name is in RAM: a 14-byte name with the letter cursor past its end makes the next letter press
    write a 16-byte name, so the count is 6 - 8 = -2. Stock: the loop writes spaces until the counter
    wraps, and the game stops. Fixed: the name is padded with nothing and the game goes on."""
    for _ in range(12):
        g.route("6:start,150:none")
    g.advance(120)
    g.d.write_bytes(bytes([0x7F]), NAMING_CURRENT_LETTER)   # setupNameDisplayBox overwrites it (:2759)
    up = False
    for _ in range(400):                                   # NEW GAME, through the intro to the tamer's name
        g.route("6:cross,30:none")
        if g.d.read(NAMING_CURRENT_LETTER, 1)[0] != 0x7F:
            up = True
            break
    check(up, "the naming screen is up")
    if not up:
        return
    g.advance(120)
    f.shot("naming")
    name = bytes([0x82, 0x60]) * 7                          # seven full-width letters, the screen's own code
    g.d.write_bytes(name + b"\0", NAMING_BUFFER)
    g.d.write_bytes(bytes([7]), NAMING_CURRENT_LETTER)     # the next letter goes at byte 14
    # No waiting on the route: when the guest stops, its frame counter stops and a waiting route never returns.
    g.route("6:cross,30:none", wait=False)                 # a letter: tickNamingBox -> updateNamingPreview
    alive, typed, line = True, b"", b""
    try:
        time.sleep(5)
        frame = g.d.frame()
        time.sleep(5)
        moved = g.d.frame() - frame
        alive = moved > 60                                 # 5 s is about 300 frames
        print("MEASURE guest frames in 5 s after the letter: %d" % moved, flush=True)
        typed = g.d.read(NAMING_BUFFER, 20).split(b"\0")[0]
        line = g.d.read(struct.unpack("<I", g.d.read(TEXTBOX_LINES_PTR, 4))[0], 64)
        print("MEASURE after the letter: name %d bytes %s, preview %s"
              % (len(typed), typed.hex(" "), line.hex(" ")), flush=True)
    except Exception as e:                                # the debug server goes with the game
        alive = False
        print("MEASURE after the letter: no answer from the game (%s: %s)" % (type(e).__name__, e), flush=True)
    if a.off:
        check(not alive, "stock: the game stopped once the 16-byte name reached padWithSpaces")
        check(typed + b"\x81\x40" * 4 in line, "stock: spaces run on past the name")
    else:
        check(alive, "fixed: the game is still running")
        check(len(typed) == 16, "fixed: the letter went in, a 16-byte name (%d)" % len(typed))
        check(typed + b"\x01\x01\0\0" in line, "fixed: the preview ends right after the name, no spaces")


if __name__ == "__main__":
    sys.exit(main())
