#!/usr/bin/env python3
"""Generate the "Save Anywhere" feature (fold into dw.vanilla.qol).

Stock Digimon World saves only through sleep: partnerTickSleep (0x800DDA08)
case 5 runs sleepRegen + handleSleeping -- an advanceToTime() skip to the wake
hour, evoTimer += hours slept, remainingLifetime -= hours slept, food/poop/
sickness timers reset -- and only then opens the main-menu object on
TARGET_MENU 0x28 ("Go to sleep" / "Save and go to sleep"). Case 7 also rolls
handleSpecialEvolutions(2). None of that can run for a save that must leave the
raise sim alone.

The game already has a save flow with no sleep in it: openSaveMachine
(0x8011381C, script op 0x30) arms the main menu (CHECKED_MEMORY_CARD, MAIN_STATE
= 1 so the save loads as a normal Continue, CURRENT_MENU = -2) and picks
TARGET_MENU 0x44 when no card was chosen at New Game; tickSaveMachine
(0x801138B0) ticks + renders it and returns 1 once CURRENT_MENU == -1. Its
menu 0x45 ("Do you want to save?") -> 0x3F/0x40/0x41 is the same
writeSavegame + MemCardWriteFile path the sleep save (0x29/0x2A/0x2B) and
gameClearSave use, with the same card checks and error/retry screens. That is
the whole write path here; nothing new touches the card.

Four guarded main_exe writes route the field menu into it:

 1 handleGameMenuSelection case 6 "Sleep" (0x800BA2A4). Stock returns when
   MENU_OPTIONS[6].disabled & 1 -- the game's own "not sleepy" gate. Now any
   nonzero value (1, or 2 from write 4) takes that branch, which
   calls openSaveMachine, swaps its 0x3C/0x46 (VS registration) target for 0x45
   unless it chose 0x44 (no card), and sets TRIANGLE_MENU_STATE = 6. When the
   partner IS sleepy the stock sleep code runs unchanged (same calls, same
   order); it now shares case 5's identical "IS_IN_MENU = 0; startGameTime();
   return" tail at 0x800BA330 to make room.
 2 tickTriangleMenu state 6 (0x800B7FB4): close UI box 0 (the menu) the way
   states 2/3/5 do, then tick tickSaveMachine each frame; when it reports done,
   state 0 reopens the menu. Game time stays stopped throughout because the
   menu is still open -- the player leaves with Triangle as usual.
 3 tickPlayerMenu's exit (0x800B95A4) stored state 6, whose stock code is
   instruction-for-instruction state 4's. It now stores 4, which frees state 6.
   The only TRIANGLE_MENU_STATE writers in the EXE, 1.5's new code and all 16
   overlays were enumerated; nothing else writes 6.
 4 addGameMenu (0x800B7CB8) marks Sleep "not sleepy" with 2 instead of 1. The
   only readers of MENU_OPTIONS[6].disabled are renderGameMenu (greys the icon
   when == 1), tickGameMenu (disabled buzz playSound(0,4) when & 1, else the
   select sound 3) and write 1's gate, so Sleep now draws and sounds enabled
   while still meaning "not sleepy". Sleepy still stores 0. The other writers
   (setSleepDisabled from tickSleepMechanics, gated on game time, and from
   handleSleeping, in the sleep state) never run while the menu is open, and
   the tamer code that calls addGameMenu stops game time in the same call, so
   2 holds for the life of the menu. No overlay or 1.5 code touches the byte.

Digimon World 1.5: all four ranges are byte-identical in 1.5's executable and
1.5 calls the same functions at the same addresses (its openSaveMachine only
relocates the DIGIMON_DATA level read). Every run checks it against the 1.5 EXE.

  python tools/gen_save_anywhere.py                 # verify only
  python tools/gen_save_anywhere.py --out DIR       # verify + write a standalone
                                                    # dw.wip.save manifest into DIR
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WORKSPACE = os.path.normpath(os.path.join(HERE, "..", ".."))
EXE_CANDIDATES = [os.path.join(WORKSPACE, "game-vanilla", "extracted", "SLUS_010.32"),
                  os.path.join(WORKSPACE, "dw15-release", "SLUS_010.32.vanilla")]
DW15_EXE = os.path.join(WORKSPACE, "dw15-release", "SLUS_010.32.dw15")
LOAD_ADDR = 0x80090800
HDR = 0x800

WIP_PKG = "dw.wip.save"

FEATURE_ID = "save-anywhere"
FEATURE = {
    "id": FEATURE_ID,
    "name": "Save Anywhere",
    "description": (
        "Save from the field menu at any time. Pick Sleep while your partner is "
        "not sleepy (no ZZZ bubble) and you get the game's own 'Do you want to "
        "save?' prompt instead of nothing; Sleep no longer looks greyed out. "
        "Nothing sleeps: no time skip, no tiredness, lifespan or care changes, "
        "and the clock stays stopped while the menu is open. When your partner "
        "is sleepy, Sleep works exactly as before."),
    "group": "Saving",
    "default_enabled": False,
}

# --- MIPS encoders (only the forms used below) ------------------------------
ZERO, AT, V0, A0, GP = 0, 1, 2, 4, 28


def _i(op, rs, rt, imm):
    return (op << 26) | (rs << 21) | (rt << 16) | (imm & 0xFFFF)


def jal(t): return (3 << 26) | ((t >> 2) & 0x3FFFFFF)
def lui(rt, imm): return _i(0x0F, 0, rt, imm)
def lb(rt, off, rs): return _i(0x20, rs, rt, off)
def lh(rt, off, rs): return _i(0x21, rs, rt, off)
def lw(rt, off, rs): return _i(0x23, rs, rt, off)
def sw(rt, off, rs): return _i(0x2B, rs, rt, off)
def andi(rt, rs, imm): return _i(0x0C, rs, rt, imm)
def addiu(rt, rs, imm): return _i(0x09, rs, rt, imm)
def beq(rs, rt, pc, t): return _i(0x04, rs, rt, (t - pc - 4) >> 2)
def bne(rs, rt, pc, t): return _i(0x05, rs, rt, (t - pc - 4) >> 2)


NOP = 0
MOVE_A0_ZERO = 0x00002021  # addu $a0, $zero, $zero

# --- game addresses (dw_decomp config/us/symbols.txt) -----------------------
GP_VALUE = 0x8013BB2C        # TRIANGLE_MENU_STATE 0x80134D30 is -0x6DFC($gp)
TRIANGLE_MENU_STATE = 0x80134D30
TARGET_MENU = 0x80135068
SLEEP_DISABLED = 0x80123ED6  # MENU_OPTIONS[6].disabled (setSleepDisabled's byte)
UI_BOX0_FRAME = 0x8013D3A0   # UI_BOX_DATA[0].frame
OPEN_SAVE_MACHINE = 0x8011381C
TICK_SAVE_MACHINE = 0x801138B0
CLOSE_TRIANGLE_MENU = 0x800B7FFC
CLOSE_UI_BOX_IF_OPEN = 0x800B8038
PARTNER_SET_STATE = 0x800DF4D0
MENU_SEL_RETURN = 0x800BA3FC  # handleGameMenuSelection epilogue
MENU_SEL_TAIL = 0x800BA330    # case 5: IS_IN_MENU = 0; startGameTime(); return
TRI_RETURN = 0x800B7FEC       # tickTriangleMenu epilogue
MENU_SAVE_PROMPT, MENU_NO_CARD = 0x45, 0x44


def gp(addr):
    return addr - GP_VALUE


def _case6():
    a = 0x800BA2A4
    sleep = a + 0x38
    return [
        lui(V0, SLEEP_DISABLED >> 16),
        lb(V0, SLEEP_DISABLED & 0xFFFF, V0),
        NOP,
        andi(V0, V0, 3),                        # 1 stock / 2 addGameMenu: not sleepy
        beq(V0, ZERO, a + 0x10, sleep),         # sleepy -> stock sleep path
        addiu(V0, ZERO, 6),
        jal(OPEN_SAVE_MACHINE),
        sw(V0, gp(TRIANGLE_MENU_STATE), GP),    # (delay) TRIANGLE_MENU_STATE = 6
        lw(V0, gp(TARGET_MENU), GP),
        addiu(AT, ZERO, MENU_NO_CARD),
        beq(V0, AT, a + 0x28, MENU_SEL_RETURN),  # no card chosen: keep 0x44
        addiu(V0, ZERO, MENU_SAVE_PROMPT),
        beq(ZERO, ZERO, a + 0x30, MENU_SEL_RETURN),
        sw(V0, gp(TARGET_MENU), GP),            # (delay) TARGET_MENU = 0x45
        jal(CLOSE_TRIANGLE_MENU),               # sleep: stock code, moved up
        NOP,
        jal(PARTNER_SET_STATE),
        addiu(A0, ZERO, 3),
        beq(ZERO, ZERO, a + 0x48, MENU_SEL_TAIL),
        NOP,
    ]


def _state6():
    a = 0x800B7FB4
    return [
        jal(CLOSE_UI_BOX_IF_OPEN),
        MOVE_A0_ZERO,
        lui(AT, (UI_BOX0_FRAME + 0x8000) >> 16),
        lh(V0, UI_BOX0_FRAME & 0xFFFF, AT),
        NOP,
        bne(V0, ZERO, a + 0x14, TRI_RETURN),    # menu box still closing
        NOP,
        jal(TICK_SAVE_MACHINE),
        NOP,
        beq(V0, ZERO, a + 0x24, TRI_RETURN),    # save UI still running
        NOP,
        NOP,
        NOP,
        # 0x800B7FE8 stays: sw $zero, TRIANGLE_MENU_STATE -> state 0 reopens the menu
    ]


# (address, stock words as disassembled, new words, comment). The stock words
# are asserted against the EXE so a different executable fails loudly.
SITES = [
    (0x800BA2A4,
     "40100400 20104400 80180200 1280023c 8e3e4224 21104300 00004280 00000000 "
     "01004230 4c004014 00000000 ffdf020c 00000000 347d030c 03000424 c49380af "
     "e875030c 00000000 44000010 1000bf8f",
     _case6(),
     "handleGameMenuSelection case 6: not sleepy -> openSaveMachine, TARGET_MENU 0x45, "
     "TRIANGLE_MENU_STATE 6; sleepy -> stock sleep code"),
    (0x800B7FB4,
     "01000424 1580013c a15724a0 21108000 1580013c dd5722a0 0ee0020c 00000000 "
     "1480013c c4d32284 00000000 02004014 00000000",
     _state6(),
     "tickTriangleMenu state 6: close the menu box, tick tickSaveMachine, reopen when done"),
    (0x800B95A4,
     "06000224",
     [addiu(V0, ZERO, 4)],
     "tickPlayerMenu exit: TRIANGLE_MENU_STATE 6 -> 4 (stock state 4 is the same code)"),
    (0x800B7CB8,
     "01000224",
     [addiu(V0, ZERO, 2)],
     "addGameMenu: not sleepy -> MENU_OPTIONS[6].disabled = 2 (draws and sounds enabled)"),
]


def _bytes(words):
    return b"".join(w.to_bytes(4, "little") for w in words)


def _off(vaddr):
    return vaddr - LOAD_ADDR + HDR


def hexs(b):
    return " ".join("%02x" % x for x in b)


def feature(exe_bytes):
    """Feature + options + patches as data; raises if the EXE is not stock."""
    patches = []
    for addr, stock, words, comment in SITES:
        stock_b = bytes.fromhex(stock.replace(" ", ""))
        new_b = _bytes(words)
        cur = exe_bytes[_off(addr):_off(addr) + len(stock_b)]
        if cur != stock_b or len(new_b) != len(stock_b):
            raise ValueError("0x%08X: EXE has %s, expected stock %s" % (addr, cur.hex(), stock_b.hex()))
        patches.append({"feature": FEATURE_ID, "target": "main_exe", "address": addr,
                        "expected": hexs(cur), "replace": hexs(new_b), "comment": comment})
    return {"feature": dict(FEATURE), "options": [], "patches": patches}


def toml_lines(data):
    f = data["feature"]
    out = ["[[feature]]", 'id = "%s"' % f["id"], 'name = "%s"' % f["name"],
           'description = "%s"' % f["description"], 'group = "%s"' % f["group"],
           "default_enabled = %s" % str(f["default_enabled"]).lower(), ""]
    for p in data["patches"]:
        out += ["# " + p["comment"], "[[patch]]", 'feature = "%s"' % p["feature"],
                'target = "%s"' % p["target"], "address = 0x%08X" % p["address"],
                'expected = "%s"' % p["expected"], 'replace = "%s"' % p["replace"], ""]
    return out


def dw15_report(data):
    """Per patch: does 1.5's EXE carry the same expected bytes?"""
    if not os.path.exists(DW15_EXE):
        return None
    d15 = open(DW15_EXE, "rb").read()
    out = []
    for p in data["patches"]:
        want = bytes.fromhex(p["expected"].replace(" ", ""))
        out.append(d15[_off(p["address"]):_off(p["address"]) + len(want)] == want)
    return out


def main():
    exe = next((p for p in EXE_CANDIDATES if os.path.exists(p)), None)
    if exe is None:
        print("no vanilla EXE at any of: %s" % EXE_CANDIDATES)
        return 1
    try:
        data = feature(open(exe, "rb").read())
    except ValueError as e:
        print("PROBLEM: %s" % e)
        return 1
    print("verified %d sites against %s" % (len(data["patches"]), exe))
    rep = dw15_report(data)
    for p, ok in zip(data["patches"], rep or [None] * len(data["patches"])):
        print("  0x%08X  %3d bytes  1.5: %s" % (p["address"], len(p["replace"].split()),
                                               {True: "same", False: "DIFFERS", None: "1.5 EXE absent"}[ok]))
    if rep is not None and not all(rep):
        print("PROBLEM: 1.5 changed a guarded byte -- needs conflicts or a 1.5 variant")
        return 1
    if "--out" not in sys.argv:
        print("nothing written (pass --out DIR to write a standalone test manifest)")
        return 0
    i = sys.argv.index("--out")
    if i + 1 >= len(sys.argv):
        print("--out needs a directory")
        return 1
    out_dir = sys.argv[i + 1]
    out = [
        "# WIP: Save Anywhere, standalone for testing. Generated by tools/gen_save_anywhere.py.",
        "format_version = 5",
        'id = "%s"' % WIP_PKG,
        'version = "1.0.0"',
        'name = "Save Anywhere (WIP)"',
        'author = "DigimonWorldRecomp"',
        'description = "Work-in-progress test package; the feature belongs in dw.vanilla.qol."',
        'resolver = "declarative"',
        'save_compatibility = "shared"',
        "",
        "[[target]]",
        'game_id = "SLUS-01032"',
        "",
    ] + toml_lines(data)
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, "manifest.toml")
    with open(dest, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(out))
    print("wrote %s" % dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
