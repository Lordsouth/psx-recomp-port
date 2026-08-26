#!/usr/bin/env python3
"""Generate the Digimon World Trainer .psxmod manifest.

Two kinds of cheat are emitted:

* Function neutering -- replace a function's first two instructions with
  `jr $ra` + `nop` so it returns immediately. Every target is void in
  dw_decomp, so no caller reads a return value and returning early is safe.
* Byte tunables -- bounded integer options written straight into EXE data,
  with each option defaulting to the CURRENT stock byte so enabling the
  feature changes nothing until the player moves a slider.

Function names and addresses come from dw_decomp's config/symbols.txt
(vanilla SLUS-01032), but every `expected` guard is re-read from our own
extracted EXE, so the mod loader refuses to apply anything if a byte ever
moves. That matters: the Vice hack ships different STAT_GAIN_FACTORS values
than vanilla, and copying the decomp's constants would have been wrong.

  python tools/gen_trainer.py            # write the manifest
  python tools/gen_trainer.py --check    # verify only, no write
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EXE = os.path.normpath(os.path.join(HERE, "..", "..", "game-vanilla", "extracted",
                                    "SLUS_010.32"))
LOAD_ADDR = 0x80090800
HDR = 0x800

# jr $ra ; nop  (little-endian)
JR_RA_NOP = bytes([0x08, 0x00, 0xE0, 0x03, 0x00, 0x00, 0x00, 0x00])

PKG_ID = "dw.vanilla.trainer"
PKG_VER = "1.0.0"
GAME_ID = "SLUS-01032"

# (feature_id, label, group, address, dw_decomp symbol, description)
CHEATS = [
    ("no-hunger", "No Hunger", "Care",
     0x800A75DC, "tickHungerMechanics",
     "Your Digimon never gets hungry, so the food gauge stops draining and it "
     "never starves. Feeding still works normally."),
    ("no-tiredness", "No Tiredness", "Care",
     0x800A70E4, "tickTirednessMechanics",
     "The tiredness gauge stops rising, so your Digimon never needs to rest "
     "and never collapses from exhaustion."),
    ("never-sick", "Never Get Sick", "Care",
     0x800A856C, "tickSicknessMechanics",
     "Illness never develops, so your Digimon cannot catch the sickness that "
     "normally follows poor care or bad food."),
    ("never-unhappy", "Never Unhappy", "Care",
     0x800A5EA0, "tickUnhappinessMechanics",
     "Happiness stops decaying, so your Digimon will not sulk, disobey, or "
     "wander off from unhappiness."),
    ("no-death", "No Death (Immortal)", "Lifespan",
     0x800A8A3C, "tickDeathCondition",
     "The death check never runs, so your Digimon will not die of old age, "
     "neglect, or anything else. The single biggest source of lost runs."),
    ("no-lifespan-sickness", "No Lifespan Loss from Sickness", "Lifespan",
     0x800AAD28, "tickSicknessLostLife",
     "Being sick no longer burns days off your Digimon's lifespan."),
    ("no-lifespan-battle", "No Lifespan Loss from Battles", "Lifespan",
     0x800AAE44, "tickBattleLostLife",
     "Losing battles no longer burns days off your Digimon's lifespan."),
    ("no-battle-fatigue", "No Post-Battle Tiredness", "Battle",
     0x800A8E78, "handlePostBattleTiredness",
     "Fighting no longer adds tiredness, so you can battle back to back "
     "without resting."),
    ("no-pooping", "No Pooping", "Housekeeping",
     0x800A7D68, "tickPartnerPoopingMechanic",
     "Your Digimon never needs to poop, so no mess accumulates and no care "
     "mistakes are logged for leaving it."),
    ("no-poop-weight-loss", "No Weight Loss from Pooping", "Housekeeping",
     0x800A8100, "handlePoopWeightLoss",
     "Pooping no longer reduces weight, making it much easier to hold a "
     "weight target for evolution requirements."),
]

# STAT_GAIN_FACTORS is int8_t[4] indexed by (enemy count - 1); battle_ui.c
# scales the stats earned from a win by it. Vanilla dw_decomp has
# {10, 12, 16, 0}; this disc's Vice hack ships {10, 20, 30, 0}.
STAT_GAIN_ADDR = 0x801344D0
STAT_GAIN_OPTS = [
    ("gain-1", "Stat gain vs 1 enemy", 0),
    ("gain-2", "Stat gain vs 2 enemies", 1),
    ("gain-3", "Stat gain vs 3 enemies", 2),
]


def file_off(vaddr):
    return vaddr - LOAD_ADDR + HDR


def hexs(b):
    return " ".join("%02x" % x for x in b)


def collect(data):
    rows, problems = [], []
    for fid, label, group, addr, sym, desc in CHEATS:
        off = file_off(addr)
        if off < 0 or off + 8 > len(data):
            problems.append("%s: address 0x%08X outside EXE" % (fid, addr))
            continue
        cur = data[off:off + 8]
        if cur == JR_RA_NOP:
            problems.append("%s: 0x%08X is ALREADY jr $ra/nop" % (fid, addr))
            continue
        w0 = struct.unpack_from("<I", cur, 0)[0]
        # Sanity-check we are at a real function head: either a stack-frame
        # prologue (addiu $sp,$sp,-N) or a leaf starting with a load/lui.
        op = w0 >> 26
        is_prologue = (w0 & 0xFFFF0000) == 0x27BD0000
        is_leaf_head = op in (0x0F, 0x21, 0x23, 0x24, 0x25, 0x08, 0x09)
        if not (is_prologue or is_leaf_head):
            problems.append(
                "%s: 0x%08X first word 0x%08X does not look like a function "
                "entry" % (fid, addr, w0))
            continue
        rows.append((fid, label, group, addr, sym, desc, cur))

    off = file_off(STAT_GAIN_ADDR)
    stock = data[off:off + 4]
    for _oid, _label, idx in STAT_GAIN_OPTS:
        if not 1 <= stock[idx] <= 127:
            problems.append(
                "STAT_GAIN_FACTORS[%d] is 0x%02X, not a plausible int8 factor"
                % (idx, stock[idx]))
    return rows, problems, stock


def render(rows, stock):
    out = [
        "# Digimon World Trainer -- generated by tools/gen_trainer.py.",
        "# Do not hand-edit: regenerate so every `expected` guard stays",
        "# byte-exact against game/extracted/SLUS_010.32.",
        "#",
        "# Toggle features in the launcher's Mods panel.",
        "",
        "format_version = 5",
        'id = "%s"' % PKG_ID,
        'version = "%s"' % PKG_VER,
        'name = "Digimon World Trainer"',
        'author = "DigimonWorldRecomp"',
        'description = "Turn off the raise-sim pressures that end runs: '
        'hunger, tiredness, illness, lifespan drain and death, plus a battle '
        'stat-gain tuner. Every toggle is independent and off by default."',
        'resolver = "declarative"',
        'save_compatibility = "shared"',
        "",
        "[[target]]",
        'game_id = "%s"' % GAME_ID,
        "",
    ]

    for fid, label, group, _addr, _sym, desc, _cur in rows:
        out += [
            "[[feature]]",
            'id = "%s"' % fid,
            'name = "%s"' % label,
            'description = "%s"' % desc,
            'group = "%s"' % group,
            "default_enabled = false",
            "",
        ]

    out += [
        "[[feature]]",
        'id = "battle-stat-gain"',
        'name = "Battle Stat Gain"',
        'description = "Scales the stats your Digimon earns from winning a '
        'battle. Each slider applies when fighting that many enemies at once. '
        "Defaults are this disc's stock values, so enabling the feature "
        'changes nothing until you raise one."',
        'group = "Training"',
        "default_enabled = false",
        "",
    ]
    for oid, label, idx in STAT_GAIN_OPTS:
        out += [
            "[[option]]",
            'feature = "battle-stat-gain"',
            'id = "%s"' % oid,
            'label = "%s"' % label,
            'description = "Stock value on this disc is %d. Higher means '
            'faster growth; 127 is the largest value the game field holds."'
            % stock[idx],
            'group = "Training"',
            'type = "integer"',
            "min = 1",
            "max = 127",
            "step = 1",
            "default = %d" % stock[idx],
            "",
        ]

    # --- patches -----------------------------------------------------------
    for fid, _label, _group, addr, sym, _desc, cur in rows:
        out += [
            "# %s() @ 0x%08X -> jr $ra ; nop (return immediately)"
            % (sym, addr),
            "[[patch]]",
            'feature = "%s"' % fid,
            'target = "main_exe"',
            "address = 0x%08X" % addr,
            'expected = "%s"' % hexs(cur),
            'replace = "%s"' % hexs(JR_RA_NOP),
            "",
        ]
    for oid, _label, idx in STAT_GAIN_OPTS:
        out += [
            "# STAT_GAIN_FACTORS[%d] @ 0x%08X (stock %d)"
            % (idx, STAT_GAIN_ADDR + idx, stock[idx]),
            "[[patch]]",
            'feature = "battle-stat-gain"',
            'target = "main_exe"',
            "address = 0x%08X" % (STAT_GAIN_ADDR + idx),
            'expected = "%02x"' % stock[idx],
            'replace_from = { option = "%s", encoding = "u8", offset = 0 }'
            % oid,
            "",
        ]
    return "\n".join(out) + "\n"


def main():
    check_only = "--check" in sys.argv
    with open(EXE, "rb") as f:
        data = f.read()

    rows, problems, stock = collect(data)
    if problems:
        print("PROBLEMS:")
        for p in problems:
            print("  " + p)
        return 1

    print("verified %d function cheats against %s" % (len(rows), EXE))
    for _fid, label, _g, addr, sym, _d, cur in rows:
        print("  %-34s 0x%08X %-28s expected=%s"
              % (label, addr, sym, hexs(cur)))
    print("  %-34s 0x%08X %-28s stock=%s"
          % ("Battle Stat Gain (3 sliders)", STAT_GAIN_ADDR,
             "STAT_GAIN_FACTORS", " ".join(str(b) for b in stock)))

    if check_only:
        print("--check: nothing written")
        return 0

    dest_dir = os.path.normpath(os.path.join(
        HERE, "..", "mods", "packages", PKG_ID, PKG_VER))
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, "manifest.toml")
    with open(dest, "w", encoding="utf-8", newline="\n") as f:
        f.write(render(rows, stock))
    print("wrote %s" % dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
