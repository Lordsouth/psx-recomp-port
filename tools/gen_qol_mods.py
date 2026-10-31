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
        'dialogue and faster running. All are off by default and none turns '
        'a mechanic off."',
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
