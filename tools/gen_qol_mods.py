#!/usr/bin/env python3
"""Generate the "Digimon World - Quality of Life" .psxmod manifest.

These are difficulty knobs, not cheats. The trainer package next door turns
mechanics OFF; this one lets you tune them and keep playing the game as
designed. Both features were chosen from what players actually complain about
rather than from what is easy to patch:

  Longer Life
    The single most common complaint about Digimon World is that a partner
    dies of old age after a couple of hours and you start over. The game gives
    every Digimon 360 in-game hours, assigned at four sites that all store
    straight to PARTNER_PARA.remainingLifetime (+0x48). Each site is
    `addiu $v0, $zero, 360`, so the lifespan is one 16-bit immediate and can
    become a slider. Note the immediate is sign-extended, so the ceiling is
    kept well under 0x8000 -- a value above that would load a NEGATIVE
    lifespan and kill the Digimon instantly.

  World Clock Speed
    Digimon World already has a clock-speed control: tickGameClock reads
    PSTAT_TIME_SPEED and branches on 0 = normal, 1 = double, 2 = half,
    3 = paused. The value lands in $a0 via `andi $a0, $v0, 0xFF`, so replacing
    that one instruction with an immediate pins the speed regardless of the
    stat. Half speed doubles the real time you get per in-game hour, which
    slows hunger, tiredness, pooping, evolution timers and aging together --
    the whole raising treadmill relaxes at once, and no ratio between them
    changes. 3 (paused) is deliberately NOT offered: the game resets it to 0
    while time is running, so it would fight itself.

Three more features live in their own generators, which check their guards
against the same executable; this one folds them in and owns their
player-facing descriptions. All three run on Digimon World 1.5 too
(tools/check_dw15_compat.py):

  Save Anywhere  gen_save_anywhere.py -- Sleep while not sleepy opens the
                 game's own save prompt (openSaveMachine / menu 0x45).
  Text Speed     gen_text_speed.py -- a whole page per frame (Fast), and the
                 dialogue box opens without its zoom (Instant).
  Run Speed      gen_walk_speed.py -- extra whole frames of tamer movement
                 while running (1.5x / 2x).

One more is a plain bug fix, built here:

  Battle End Freeze Fix
    A win whose Brains gain crosses 700, 800, 900 or 999 adds the MP bonus message
    to the end-of-battle box, and the box never finishes (docs/GAME.md 12.1). Four
    small writes to BTL_REL.BIN and one to the executable. The only feature that is
    on by default. 1.5 owns those sectors but keeps these bytes, so
    gen_dw15_extras.py gives the dw15 feature the same writes.

Four more bug fixes follow the same pattern, each its own default-on feature (BUGFIXES,
docs/GAME.md 12.2): Jukebox Freeze Fix, Lost Techniques Fix, Sleep Healing Fix and
Evolution Item Fix. The last patches EVL_REL.BIN, so its guard is read from the disc image.

Guards are read from our own extracted executable, never copied from
dw_decomp -- Vice patches the binary and constants do move.

  python tools/gen_qol_mods.py            # write the manifest
  python tools/gen_qol_mods.py --check    # verify only
"""
import os
import struct
import sys

import gen_save_anywhere
import gen_text_speed
import gen_walk_speed

HERE = os.path.dirname(os.path.abspath(__file__))
# The stock executable: extracted by setup, else the copy beside the 1.5 work.
EXE = next((p for p in (
    os.path.normpath(os.path.join(HERE, "..", "..", "game-vanilla", "extracted", "SLUS_010.32")),
    os.path.normpath(os.path.join(HERE, "..", "..", "dw15-release", "SLUS_010.32.vanilla")))
    if os.path.exists(p)), None)
LOAD_ADDR = 0x80090800
HDR = 0x800

PKG_ID = "dw.vanilla.qol"
PKG_VER = "1.0.0"
GAME_ID = "SLUS-01032"

# `addiu $v0, $zero, 360` immediately followed by a store to
# PARTNER_PARA.remainingLifetime. Verified by disassembly, not guessed.
LIFESPAN_SITES = [0x800A45AC, 0x800A537C, 0x800DE81C, 0x800DEC84]
LIFESPAN_STOCK = 360
LIFESPAN_MAX = 9999          # far below the 0x8000 sign-extension cliff

# tickGameClock: `andi $a0, $v0, 0xFF` capturing PSTAT_TIME_SPEED.
CLOCK_SITE = 0x800DD008
CLOCK_STOCK_WORD = 0x304400FF
# addiu $a0, $zero, N
CLOCK_SPEEDS = [
    ("half",   2, "Half speed (relaxed)",
     "A day takes twice as long in real time. Hunger, tiredness, pooping, "
     "evolution timers and ageing all slow together, so nothing is "
     "rebalanced -- you simply get more of your life per in-game hour."),
    ("double", 1, "Double speed",
     "A day passes twice as fast. Useful when you are waiting out a timer "
     "rather than playing."),
]


# Player-facing text for the folded-in features, worded from what was measured.
SAVE_DESC = (
    "Save from the field menu at any time. Pick Sleep while your partner is "
    "not sleepy (no ZZZ bubble) and the game's own 'Do you want to save?' "
    "prompt opens; Sleep no longer looks greyed out. Nothing sleeps: no time skip, no "
    "tiredness, lifespan or care changes, and the clock stays stopped while "
    "the menu is open. When your partner is sleepy, Sleep works as before.")
TEXT_DESC = (
    "Dialogue pages appear without the wait. The stock game draws a page one "
    "line per frame and only then zooms the box out of the speaker; Fast draws "
    "the whole page at once, Instant also opens the box without the zoom. "
    "Scripted pauses are unchanged, but text that does not wait for a button "
    "moves on a moment sooner, and with Instant a mashed X can skip a page.")
RUN_DESC = (
    "Your tamer runs faster in the field. Collisions, map exits, doors and "
    "talking to Digimon work as normal, and walking (hold R1), menus and "
    "cutscenes stay at normal speed. Your partner keeps its own pace, so on a "
    "long run it falls well behind and takes a few seconds to catch up after "
    "you stop. A map exit puts it back beside you.")


# BTL_REL.BIN. A disc patch may not cross a sector, which end_box_lines asserts.
BTL_LBA, BTL_BASE = 147703, 0x80052AE0
END_BOX = [
    (0x80062D58, "the MP bonus text has 2 rows, not 4: addi $v0,$v0,2",
     bytes.fromhex("04004220"), bytes.fromhex("02004220")),
    (0x80062EC8, "the reader stops at the end of the text, not on the digit 0: addiu $at,$zero,0",
     bytes.fromhex("30000124"), bytes.fromhex("00000124")),
    # The stock line runs past the box's 156 pixels, and the number follows it with no break.
    (0x80072FC2, "a first line that fits the box, and a row break",
     b"Consumption Bonus!\0", b"Cost Bonus!#R".ljust(19, b"\0")),
    (0x80072FE2, "a space before the number", b"\0", b" "),
]
# The USA font has 79 glyphs and no %: it draws a triangle. The string has room to spell it.
END_BOX_EXE = (0x8013475C, b"%!#W\0\0\0\0", b" pct!#W\0")
END_BOX_DESC = (
    "Fixes a bug in the original game. A battle freezes on its results when the win "
    "lifts your partner's Brains past 700, 800, 900 or 999, and everything since "
    "your last save is lost. With this on, the game shows its MP bonus message and "
    "carries on.")


# Four more fixes for bugs of the original game (docs/GAME.md 12.2), each its own feature in the
# pattern of end-box-fix. Each write is (where, address, stock word, new word, note): `where` is
# "exe" or an overlay name in OVERLAYS. The stock words are instruction encodings the guards are
# checked against; the guards written to the manifest are the bytes read from the owner's exe or disc.
OVERLAYS = {"EVL_REL.BIN": (147824, 0x80060000)}   # name -> (LBA, load address)
BUGFIXES = [
    ("jukebox-fix", "Jukebox Freeze Fix",
     "Fixes a bug in the original game where the jukebox menu could freeze on a song with a long title.",
     # padWithSpaces loops while its count is not 0; a title too long for the row makes it negative.
     [("exe", 0x800FF54C, 0x14C0FFF6, 0x1CC0FFF6, "padWithSpaces: bne a2,zero -> bgtz a2, stop at 0 or below")]),
    ("tech-wipe-fix", "Lost Techniques Fix",
     "Fixes a bug in the original game where forgetting a technique after losing all lives also erased up to 16 "
     "other techniques your partner had learned.",
     # unlearnMove builds a 16-bit mask, so the AND clears bits 16-31 of the word. The new code
     # keeps the bits to clear in a0 (both of a pair: 44/48 are bits 12 and 16, 55/57 bits 23 and
     # 25 of word 1) and ANDs with its complement.
     [("exe", 0x800E674C, 0x00000000, 0x3C040001, "unlearnMove: nop -> lui a0,1 (move 48's bit, delay slot)"),
      ("exe", 0x800E6760, 0x24041000, 0x34841000, "addiu a0,zero,0x1000 -> ori a0,a0,0x1000: 44/48 both"),
      ("exe", 0x800E6780, 0x00002021, 0x3C040280, "addu a0,zero,zero -> lui a0,0x0280: 55/57 both"),
      ("exe", 0x800E67A0, 0x3044FFFF, 0x00402021, "andi a0,v0,0xffff -> addu a0,v0,zero: the whole bit"),
      ("exe", 0x800E67A4, 0x3882FFFF, 0x00802027, "xori v0,a0,0xffff -> nor a0,a0,zero: a 32-bit mask"),
      ("exe", 0x800E67A8, 0x3044FFFF, 0x00000000, "andi a0,v0,0xffff -> nop")]),
    ("sleep-heal-fix", "Sleep Healing Fix",
     "Fixes a bug in the original game where a long sleep could leave a partner with very high max HP or MP "
     "on a negative value instead of healing it to full.",
     # The clamp re-reads the 16-bit sum sign-extended, so a sum past 32767 passes `>= max`.
     # Read unsigned: two non-negative int16 values always sum below 65536.
     [("exe", 0x800A6FEC, 0x842257F4, 0x942257F4, "sleepRegen HP clamp: lh -> lhu"),
      ("exe", 0x800A700C, 0x842257F6, 0x942257F6, "sleepRegen MP clamp: lh -> lhu")]),
    ("evo-item-fix", "Evolution Item Fix",
     "Fixes a bug in the original game where using an evolution item stopped every later evolution from "
     "raising your partner's stats and lifespan until the game was reset.",
     # HAS_USED_EVOITEM (0x80134E52) is cleared only at boot. Clear it as EVL_applyEvolution
     # returns: after every check of it, so the item evolution itself still skips its gains. The
     # function reads the flag as lb -27866(gp); the store uses the same address.
     [("EVL_REL.BIN", 0x80063AF0, 0x00000000, 0xA3809326,
       "EVL_applyEvolution, the last branch's delay slot: nop -> sb zero,-27866(gp), HAS_USED_EVOITEM = 0")]),
]


def overlay_offset(name, vaddr):
    lba, base = OVERLAYS[name]
    return lba * 2048 + vaddr - base


def fix_patches(writes, feature, exe_data, disc_user):
    """[[patch]] lines for one fix's writes. disc_user(offset, n) reads a disc as 2048-byte sectors.
    Raises ValueError on a guard that does not match."""
    out = []
    for where, vaddr, stock, new, note in writes:
        if where == "exe":
            cur = exe_data[file_off(vaddr):file_off(vaddr) + 4]
            loc = ['target = "main_exe"', "address = 0x%08X" % vaddr]
        else:
            offset = overlay_offset(where, vaddr)
            assert offset % 2048 <= 2044, hex(vaddr)       # a disc patch may not cross a sector
            cur = disc_user(offset, 4)
            loc = ['target = "disc_user"', "offset = %d" % offset]
        if cur != struct.pack("<I", stock):
            raise ValueError("%s 0x%08X is %s, expected %08X" % (feature, vaddr, hexs(cur), stock))
        out += ["# %s 0x%08X: %s" % ("EXE" if where == "exe" else where, vaddr, note),
                "[[patch]]", 'feature = "%s"' % feature] + loc + [
                'expected = "%s"' % hexs(cur), 'replace = "%s"' % hexs(struct.pack("<I", new)), ""]
    return out


def bugfix_lines(exe_data, disc_user):
    """The four [[feature]] blocks."""
    out = []
    for fid, name, desc, writes in BUGFIXES:
        out += ["[[feature]]", 'id = "%s"' % fid, 'name = "%s"' % name, 'description = "%s"' % desc,
                'group = "Fixes"', "default_enabled = true", ""] + fix_patches(writes, fid, exe_data, disc_user)
    return out


def disc_reader():
    """-> disc_user(offset, n) over the owner's stock disc image, or None."""
    from verify_disc_patches import find_disc
    path = find_disc([])
    if not path:
        return None
    f = open(path, "rb")

    def read(offset, n):
        sec, off = divmod(offset, 2048)
        f.seek(sec * 2352 + 24 + off)
        return f.read(n)
    return read


def end_box_lines():
    return [
        "[[feature]]",
        'id = "end-box-fix"',
        'name = "Battle End Freeze Fix"',
        'description = "%s"' % END_BOX_DESC,
        'group = "Fixes"',
        "default_enabled = true",
        "",
    ] + end_box_patches("end-box-fix")


def end_box_offset(vaddr):
    return BTL_LBA * 2048 + vaddr - BTL_BASE


def end_box_patches(feature):
    out = []
    for vaddr, note, stock, new in END_BOX:
        assert len(stock) == len(new)
        offset = end_box_offset(vaddr)
        assert offset % 2048 + len(stock) <= 2048, hex(vaddr)
        out += [
            "# BTL_REL.BIN 0x%08X: %s" % (vaddr, note),
            "[[patch]]",
            'feature = "%s"' % feature,
            'target = "disc_user"',
            "offset = %d" % offset,
            'expected = "%s"' % hexs(stock),
            'replace = "%s"' % hexs(new),
            "",
        ]
    addr, stock, new = END_BOX_EXE
    return out + [
        "# BTL_STR_MP_BONUS_PERCENT: the font has no %, so spell it",
        "[[patch]]",
        'feature = "%s"' % feature,
        'target = "main_exe"',
        "address = 0x%08X" % addr,
        'expected = "%s"' % hexs(stock),
        'replace = "%s"' % hexs(new),
        "",
    ]


def describe(lines, text):
    """Replace the [[feature]] description (the first one) in a sub-generator's lines."""
    i = next(i for i, l in enumerate(lines) if l.startswith("description = "))
    return lines[:i] + ['description = "%s"' % text] + lines[i + 1:]


def file_off(vaddr):
    return vaddr - LOAD_ADDR + HDR


def hexs(b):
    return " ".join("%02x" % x for x in b)


def main():
    check_only = "--check" in sys.argv
    if EXE is None:
        print("no stock SLUS_010.32 (game-vanilla/extracted or dw15-release)")
        return 1
    with open(EXE, "rb") as f:
        data = f.read()

    problems = []

    life = []
    for addr in LIFESPAN_SITES:
        cur = data[file_off(addr):file_off(addr) + 4]
        w = struct.unpack("<I", cur)[0]
        # addiu rt, $zero, 360  with rs == 0
        if (w & 0xFFE0FFFF) != 0x24000168:
            problems.append("lifespan site 0x%08X is %08X, not addiu rt,$zero,360"
                            % (addr, w))
            continue
        life.append((addr, cur))

    clock_cur = data[file_off(CLOCK_SITE):file_off(CLOCK_SITE) + 4]
    if struct.unpack("<I", clock_cur)[0] != CLOCK_STOCK_WORD:
        problems.append("clock site 0x%08X is %08X, expected %08X (andi $a0,$v0,0xFF)"
                        % (CLOCK_SITE, struct.unpack("<I", clock_cur)[0],
                           CLOCK_STOCK_WORD))

    addr, stock, _ = END_BOX_EXE
    if data[file_off(addr):file_off(addr) + len(stock)] != stock:
        problems.append("end box string 0x%08X differs" % addr)

    print("Longer Life      : %d/%d sites verified (stock %d hours)"
          % (len(life), len(LIFESPAN_SITES), LIFESPAN_STOCK))
    for addr, cur in life:
        print("    0x%08X  %s" % (addr, hexs(cur)))
    print("World Clock Speed: site 0x%08X  %s" % (CLOCK_SITE, hexs(clock_cur)))

    # Each raises on a guard that does not match this executable.
    extra = (describe(gen_save_anywhere.toml_lines(gen_save_anywhere.feature(data)), SAVE_DESC)
             + describe(gen_text_speed.feature_toml(data), TEXT_DESC)
             + describe(gen_walk_speed.walk_speed_lines(EXE), RUN_DESC))
    print("Save Anywhere, Text Speed, Run Speed: %d patches verified"
          % extra.count("[[patch]]"))

    disc = disc_reader()
    fixes = []
    if disc is None:
        problems.append("no stock disc image (disc/, or build-debug/disc.cfg) for the EVL_REL.BIN guards")
    else:
        try:
            fixes = bugfix_lines(data, disc)
            print("Bug fixes: %d patches verified" % fixes.count("[[patch]]"))
        except ValueError as e:
            problems.append(str(e))

    if problems:
        print("\nPROBLEMS:")
        for p in problems:
            print("  " + p)
        return 1
    if check_only:
        print("\n--check: nothing written")
        return 0

    out = [
        "# Digimon World - Quality of Life. Generated by tools/gen_qol_mods.py;",
        "# regenerate rather than hand-editing so every guard stays byte-exact.",
        "",
        "format_version = 5",
        'id = "%s"' % PKG_ID,
        'version = "%s"' % PKG_VER,
        'name = "Quality of Life"',
        'author = "DigimonWorldRecomp"',
        'description = "Comfort and pacing: how long your partner lives, how '
        'fast the world clock runs, saving from the field menu, faster '
        'dialogue and faster running, and fixes for bugs in the original '
        'game. Only the fixes are on by default, and none turns a mechanic off."',
        'resolver = "declarative"',
        'save_compatibility = "shared"',
        "",
        "[[target]]",
        'game_id = "%s"' % GAME_ID,
        "",
        "[[feature]]",
        'id = "longer-life"',
        'name = "Longer Life"',
        'description = "Sets how many in-game hours a Digimon lives. Stock is '
        '360 hours (15 in-game days), which is why partners so often die of '
        'old age mid-playthrough. Raising this does not touch anything else: '
        'illness, care mistakes and battle losses still cost lifespan exactly '
        'as before."',
        'group = "Raising"',
        "default_enabled = false",
        "",
        "[[option]]",
        'feature = "longer-life"',
        'id = "hours"',
        'label = "Lifespan (in-game hours)"',
        'description = "360 is stock. 720 doubles it, 1800 is five times. The '
        'ceiling is %d because the game stores lifespan in a signed 16-bit '
        'field and loads it with a sign-extending instruction."' % LIFESPAN_MAX,
        'group = "Raising"',
        'type = "integer"',
        "min = 1",
        "max = %d" % LIFESPAN_MAX,
        "step = 1",
        "default = %d" % LIFESPAN_STOCK,
        "",
        "[[feature]]",
        'id = "clock-speed"',
        'name = "World Clock Speed"',
        'description = "Pins the game clock to a speed the game itself '
        'supports. Half speed is the relaxed setting: a day takes twice as '
        'long in real time, so hunger, tiredness, pooping, evolution timers '
        'and ageing all slow together and no ratio between them changes."',
        'group = "Raising"',
        "default_enabled = false",
        "",
        "[[option]]",
        'feature = "clock-speed"',
        'id = "speed"',
        'label = "Clock speed"',
        'description = "Pause is not offered: the game resets that state while '
        'time is running, so it would fight itself."',
        'group = "Raising"',
        'type = "choice"',
        'default = "half"',
        "",
    ]
    for value, _imm, label, _desc in CLOCK_SPEEDS:
        out += ["[[option.choice]]", 'value = "%s"' % value,
                'label = "%s"' % label, ""]

    for addr, cur in life:
        out += [
            "# addiu $v0, $zero, 360 -> lifespan immediate; stores to",
            "# PARTNER_PARA.remainingLifetime (0x801384A8) on the next instruction.",
            "[[patch]]",
            'feature = "longer-life"',
            'target = "main_exe"',
            "address = 0x%08X" % addr,
            'expected = "%s"' % hexs(cur),
            'replace_from = { option = "hours", encoding = "u16le", offset = 0 }',
            "",
        ]

    for value, imm, label, _desc in CLOCK_SPEEDS:
        word = 0x24040000 | imm          # addiu $a0, $zero, imm
        rep = struct.pack("<I", word)
        out += [
            "# tickGameClock: andi $a0,$v0,0xFF -> addiu $a0,$zero,%d (%s)"
            % (imm, label),
            "[[patch]]",
            'feature = "clock-speed"',
            'target = "main_exe"',
            "address = 0x%08X" % CLOCK_SITE,
            'expected = "%s"' % hexs(clock_cur),
            'replace = "%s"' % hexs(rep),
            'when = { speed = "%s" }' % value,
            "",
        ]

    out += extra
    out += end_box_lines()
    out += fixes

    dest_dir = os.path.normpath(os.path.join(
        HERE, "..", "mods", "preloaded", "packages", PKG_ID, PKG_VER))
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, "manifest.toml")
    with open(dest, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out) + "\n")
    print("\nwrote %s" % dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
