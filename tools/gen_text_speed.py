#!/usr/bin/env python3
"""Generate the "Text Speed" feature (id text-speed).

What is actually slow about Digimon World's dialogue. There is no typewriter
effect to speed up: a page is rendered off screen into the back half of a
double-buffered VRAM text area and shown all at once when the box flips pages
(flipIdleTextboxPage / advanceTextbox). What the player waits on is:

  1. the render throttle. tickTextboxHandling calls drawTextboxStrings
     (0x800FFA4C) once per game tick (3 VBlanks), and that draws ONE line
     for a full-width box (vramMode 0 -- every dialogue box) or two for a
     half-width one (modes 1/2). A page is 2-4 lines (speaker name plus
     text), so 2-4 ticks, and X is ignored while it renders. Pages the
     script shows one at a time -- the whole NEW GAME interview, the last
     page of any script message -- are rendered only after the previous X
     press, so each of them costs the player those ticks.
  2. the zoom-in. A dialogue box created with flag 0x80 opens through
     createAnimatedUIBox: five ticks of a growing outline (plus the
     box-open sound) that only start once the first page has finished
     rendering. Its box tick, which flips the first page in, does not run
     until the animation is over.

Measured from guest memory (write trace on TEXTBOX_DATA.box[0], VBlanks,
vanilla and 1.5 alike): a new message box goes from the script showing it to
fully on screen in 21-27 stock, 18 Fast, 3 Instant; an interview page after
X in 9-12 stock, 6-9 Fast/Instant. Page text in VRAM is byte-identical.

So the options are:

  Fast     every full-width text box (vramMode 0: dialogue, the naming
           screen, shop lists) draws its whole page in one tick -- the
           lines-per-tick immediate in drawTextboxStrings becomes 16. The
           dialogue box still zooms out of the speaker with its sound.
           The half-width immediate (2 lines, vramMode 1/2: only the item
           keeper's two panes and the tournament schedule) is left stock:
           no dialogue uses it and neither box was reachable to test.
  Instant  Fast, plus the dialogue box opens without the zoom (the 0x80
           "animated" flag is dropped where the three dialogue-box setups
           build it), so a page is on screen the tick after the script
           shows it. The zoom's pop sound goes with it.

Left alone, on purpose: SCRIPT_OP_DELAY / DELAY_FRAMES and the
TEXTBOX_CLOSE_MODE 2 AUTOCLOSE_FRAMES countdown (cutscene timing -- both count
from the script op, not from the page appearing, so a faster render only
leaves auto-closing text on screen longer), the X-release gate
(isXPressedAfterDialogue), selection boxes (they block on the same render
check, so they simply become answerable sooner), and every menu box created
with flags 0xE1/0xF1: those close through removeAnimatedUIBox, which only an
animated box can finish, so the zoom flag is dropped only at the three
dialogue-box sites (0x21/0x81 -- they close through removeStaticUIBox
already). One timing does move: non-blocking text (TEXTBOX_CLOSE_MODE 1)
hands control back to the script one tick after its page is up, so a script
continues that much sooner -- up to ~18 VBlanks with Instant.

The 16-line cap is a bound, not a target: drawTextboxStrings stops at the
page's terminator, and no box is taller than the four-row dialogue box plus a
handful of rows. The stock one-line throttle exists for real hardware --
drawGlyph rings 50 glyph buffers through asynchronous LoadImage -- and this
feature is for the recompiled port, where the text VRAM after a one-tick
render was compared against the stock render (see build-mt-text/work).

Digimon World 1.5 does not touch any of the four words (checked against
1.5's executable on every run), so the feature runs on both.

  python tools/gen_text_speed.py                       # verify only
  python tools/gen_text_speed.py --out <manifest.toml> # also write the WIP manifest (dw.wip.text)
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WORKSPACE = os.path.normpath(os.path.join(HERE, "..", ".."))
VANILLA_EXE = os.path.join(WORKSPACE, "dw15-release", "SLUS_010.32.vanilla")
DW15_EXE = os.path.join(WORKSPACE, "dw15-release", "SLUS_010.32.dw15")
LOAD_ADDR = 0x80090800
HDR = 0x800

FEATURE_ID = "text-speed"
OPTION_ID = "speed"
WIP_PKG_ID = "dw.wip.text"
WIP_PKG_VER = "1.0.0"

LINES_PER_TICK = 16
BOTH = ("fast", "instant")

# (address, stock word, new word, choices, what it is). Stock words were read
# from the vanilla executable and disassembled; the generator re-reads them.
SITES = [
    (0x800FFB10, 0x24020001, 0x24000000 | (2 << 16) | LINES_PER_TICK, BOTH,
     "drawTextboxStrings: addiu $v0,$zero,1 -- lines drawn per tick for a "
     "full-width box (vramMode 0, every dialogue box)"),
    (0x80100FFC, 0x36020080, 0x36020000, ("instant",),
     "setupDialogueBox: ori $v0,$s0,0x80 -> ori $v0,$s0,0 -- script dialogue "
     "box opens static instead of zooming from the speaker"),
    (0x80101E20, 0x36020080, 0x36020000, ("instant",),
     "scriptSetTextboxSize: ori $v0,$s0,0x80 -> ori $v0,$s0,0 -- same, for "
     "script-sized boxes"),
    (0x80109C98, 0x24050081, 0x24050001, ("instant",),
     "setupNewGameDialogueBox: addiu $a1,$zero,0x81 -> 0x01 -- same, for "
     "Jijimon's NEW GAME interview"),
]

CHOICES = [("fast", "Fast"), ("instant", "Instant")]


def file_off(vaddr):
    return vaddr - LOAD_ADDR + HDR


def hexs(b):
    return " ".join("%02x" % x for x in b)


def word_at(data, addr):
    return struct.unpack_from("<I", data, file_off(addr))[0]


def verify(vanilla, dw15=None):
    """Problems with any guard, in either executable. Empty list = good."""
    problems = []
    for addr, stock, _new, _when, what in SITES:
        w = word_at(vanilla, addr)
        if w != stock:
            problems.append("vanilla 0x%08X is %08X, expected %08X (%s)"
                            % (addr, w, stock, what))
        if dw15 is not None and word_at(dw15, addr) != stock:
            problems.append("1.5 changed 0x%08X: %08X != stock %08X -- the "
                            "feature would need a 1.5 variant or a conflict"
                            % (addr, word_at(dw15, addr), stock))
    return problems


def feature_toml(vanilla):
    """TOML lines for the text-speed [[feature]], its [[option]] and every
    [[patch]], ready to splice into any SLUS-01032 manifest. Guards come from
    `vanilla` (the stock executable's bytes); raises if one does not match."""
    problems = verify(vanilla)
    if problems:
        raise ValueError("; ".join(problems))
    out = [
        "[[feature]]",
        'id = "%s"' % FEATURE_ID,
        'name = "Text Speed"',
        'description = "Dialogue pages appear without the wait. The stock '
        'game draws a page off screen one line per frame and only then '
        'zooms the box out of the speaker; Fast draws the whole page at '
        'once, Instant also opens the box without the zoom. Cutscene '
        'timing, auto-closing text and Yes/No questions are untouched."',
        'group = "Quality of Life"',
        "default_enabled = false",
        "",
        "[[option]]",
        'feature = "%s"' % FEATURE_ID,
        'id = "%s"' % OPTION_ID,
        'label = "Text speed"',
        'description = "Fast draws each page in one frame instead of one '
        'line per frame; the box still zooms out of the speaker. Instant also '
        'opens dialogue boxes without the zoom (or its sound). Turn the '
        'feature off for the stock speed."',
        'group = "Quality of Life"',
        'type = "choice"',
        'default = "instant"',
        "",
    ]
    for value, label in CHOICES:
        out += ["[[option.choice]]", 'value = "%s"' % value,
                'label = "%s"' % label, ""]
    for addr, stock, new, when, what in SITES:
        out += [
            "# " + what,
            "[[patch]]",
            'feature = "%s"' % FEATURE_ID,
            'target = "main_exe"',
            "address = 0x%08X" % addr,
            'expected = "%s"' % hexs(vanilla[file_off(addr):file_off(addr) + 4]),
            'replace = "%s"' % hexs(struct.pack("<I", new)),
        ]
        if when != BOTH:
            out.append('when = { %s = "%s" }' % (OPTION_ID, when[0]))
        out.append("")
    return out


def wip_manifest(vanilla):
    return [
        "# WIP: Text Speed. Generated by tools/gen_text_speed.py; regenerate",
        "# rather than hand-editing so every guard stays byte-exact.",
        "",
        "format_version = 5",
        'id = "%s"' % WIP_PKG_ID,
        'version = "%s"' % WIP_PKG_VER,
        'name = "Text Speed (WIP)"',
        'author = "DigimonWorldRecomp"',
        'description = "Faster dialogue boxes."',
        'resolver = "declarative"',
        'save_compatibility = "shared"',
        "",
        "[[target]]",
        'game_id = "SLUS-01032"',
        "",
    ] + feature_toml(vanilla)


def main():
    args = sys.argv[1:]
    with open(VANILLA_EXE, "rb") as f:
        vanilla = f.read()
    dw15 = None
    if os.path.exists(DW15_EXE):
        with open(DW15_EXE, "rb") as f:
            dw15 = f.read()
    problems = verify(vanilla, dw15)
    for addr, stock, new, when, what in SITES:
        print("0x%08X  %08X -> %08X  [%s]" % (addr, stock, new, "/".join(when)))
    print("1.5 executable: %s" % ("not found, NOT checked" if dw15 is None else
                                  "all %d words identical to stock" % len(SITES)
                                  if not problems else "DIFFERS"))
    if problems:
        print("\nPROBLEMS:\n  " + "\n  ".join(problems))
        return 1
    if "--out" not in args or args.index("--out") + 1 >= len(args):
        print("verified; nothing written (pass --out <manifest.toml> to write)")
        return 0
    dest = args[args.index("--out") + 1]
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    with open(dest, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(wip_manifest(vanilla)) + "\n")
    print("wrote %s" % dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
