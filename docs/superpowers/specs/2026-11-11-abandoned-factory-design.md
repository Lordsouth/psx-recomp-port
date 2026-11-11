# The Abandoned Factory: design

A second new-maps feature for the Digimon World port, after the Hidden Glade
(`docs/GAME.md` 3, 5, 6; `docs/INTERNALS.md` 3.1-3.2; `tools/gen_newmap.py`).

## Intent

A big, optional new area: an abandoned factory where the machines still run with
nobody tending them. It should feel forgotten and slightly eerie, the mood of Digimon
World's Factorial Town, but it is our own place: our own layout, our own art, no copy
of any stock map. The player explores it, fights machine Digimon on the way, and beats
a boss at the end for very rare items. It is the port's first multi-screen area, and the
groundwork it needs (more disc space, more file names, more script slots) is what every
later map will reuse.

## Goals

- **Four connected screens**, walked in order: an entry hall, two production floors,
  a boss chamber. The player can walk back out the way they came.
- **The entrance** is a new gate on a Gear Savanna screen, found the way the glade's
  gap in the bushes is found in Native Forest.
- **Machine Digimon to fight**, aimed at a fully raised partner with strong healing
  items. Fights start when the player walks up and presses confirm (autotalk 0), and the
  player can run (trigger 1 clear). Three tiers:
  - **Regular enemies:** strong but fair, beatable several in a row in one trip.
    Optional: they stand aside from the path, and they come back on every visit.
  - **Floor guardians:** one per production floor, near the Hidden Glade boss's
    strength. A guardian blocks the way on, and once beaten it stays beaten for good
    (a trigger hides it).
  - **The boss:** Machinedramon in the last screen, harder than the Hidden Glade boss.
- **The layouts** make the guardians unavoidable: each production floor has a chokepoint
  the guardian holds, with the regular enemies on open ground to the sides.
- **The reward for beating the boss:** Metalbanana and all six stat chips (Offense,
  Defense, Brain, Speed, HP and MP chips), given once, on the first win.
- **Harmless workers:** Hagurumon and Tinmon, which have models but no battle
  animations, may wander the halls as ambience.
- **Works on Digimon World 1.5 too**, through a 1.5 edition, as the glade does
  (`glade-15`, `tools/gen_newmap_dw15.py`).
- **Saves never depend on it:** a save made inside resumes at the Gear Savanna gate,
  as `dw_newmaps.c` does for the glade; turning the mod off is always safe.

## Non-goals

- New Digimon, new models, new moves or new items. The cast and rewards are species
  and items already on the disc.
- New music or sound. The screens use existing banks and tracks.
- Random encounters (Digimon World has none; every fight is a placed Digimon).
- Anything the player must have: the factory is optional content behind a mod toggle.

## Anti-goals (a failure even if it technically works)

- Looking or feeling like a recreation of Factorial Town or any stock map.
- Shipping any game data: the art, collision and sprites are this project's own, like
  the glade's; `tools/verify_setup_package.py` must keep passing.
- A memory card that breaks, or a story flag consumed, when the mod is turned off or a
  save is made inside.
- Screens that crash, hang or run out of memory: every screen is tested in the game
  before it ships.
- A plan rejected because of a collision with another mod (`tools/check_mod_collisions.py`).

## Constraints (measured, 2026-11-11)

- **No spare map file or script slot is left.** MGEN17 was the only unused map file
  and DG slot 118 the only empty script; the glade uses both. Each factory screen
  needs, on both discs:
  - **Disc space:** about 247 sectors (MAP plus TFS). `MMM.DAT` (LBA 148668) is 13,500
    zero sectors nothing reads: room for about 54 screens. Never proven in game
    (`docs/GAME.md` 2.3).
  - **A file name the game can look up:** the file table (`FILE_OFFSET_TABLE`; 1.5 copy
    at `0x801F1000`) has no free entries.
  - **A DG script:** new table rows 223+ with their data placed past DG.SCN, in
    `MMM.DAT`. Documented, never proven (`docs/GAME.md` 2.3, 4.1).
- **Map rows:** 240-246 and 250-252 are free in both stock and 1.5.
- **MAPHEAD tail:** 312 bytes shared with the glade (20 used); 1.5 fills it to
  `0x60DE`, leaving about 200. A screen's row section is about 20-45 bytes.
- **Per screen:** at most 8 placed Digimon and 4 distinct species (model slot 4 overlaps
  `SBOY.TIM`); fights of up to 3 enemies; art of at most 30 tiles (241 TFS sectors,
  `docs/GAME.md` 3.11); the heap is 197,520 bytes, and which limit fails first on a busy
  screen has never been measured. Machinedramon's model is 78,588 bytes, Andromon's
  89,952, so the boss chamber must be sparse.
- **Fighters on the disc** (complete animations): Guardromon, Tankmon (Champions);
  Andromon, Giromon, Mamemon, MetalMamemon, Megadramon, Gigadramon, MetalGreymon,
  MetalEtemon (Ultimates); Machinedramon.
- **Fight authoring traps** are in `docs/GAME.md` 6 (finisher only in slot 3, EFE lists,
  MP payment in fleeable fights, run-off hangs, flag ranges that pollute cups).
- **Rewards:** `GIVE_ITEM` (op `0x28`) in the boss's win path, guarded by a trigger in a
  safe range (`docs/GAME.md` 6, step 7: 627..639 or 641..649) so it pays once.

## Decided

- Four screens, in a line: entry hall, two production floors, boss chamber.
- Entrance on a Gear Savanna screen.
- Machine Digimon fights; Machinedramon is the boss.
- Difficulty in three tiers: regular enemies strong but fair, floor guardians near the
  glade boss, Machinedramon harder than the glade boss.
- One guardian per production floor blocks the way and stays beaten once won; regular
  enemies are optional and come back.
- Reward: Metalbanana plus all six stat chips.
- Exploring and fighting are the point; the mood is an abandoned factory with the
  machines still running.

## My calls (the user has not weighed in on these)

- The reward is given once, on the first win; later wins pay bits only.
- It also runs on 1.5, as an edition, and does not ship until it does. 1.5 moved DG.SCN
  to LBA 162388, past `MMM.DAT` (which ends at 162167), and a DG row is an unsigned offset
  from DG.SCN's start, so on 1.5 the scripts (and, if 1.5's patch claims `MMM.DAT`, the
  map files too) need a different home: after 1.5's DG.SCN, on sectors the 1.5 delta owns,
  written as guarded patches through a `[[requirement]]` on `dw15`, as `glade-15` does.
  The 1.5 BPS has to be walked for `MMM.DAT` first. Order: prove the route on stock, build
  the four screens on stock, then build and prove the 1.5 route as its own phase.
- Flags: the reward and the two guardians take three triggers from the safe ranges
  (627..639, 641..649), shared with every later map; record which ones in `docs/GAME.md`.
  Hiding a beaten guardian is an IF around its `LOAD_DIGIMON`/`SET_DIGIMON` in the row
  section, about 12 bytes of the MAPHEAD tail each.
- The engine groundwork is proven in the game, with a throwaway test screen, before any
  art is painted: map files served from `MMM.DAT` under new names, and a DG script at a
  new table row. If either route fails, stop and rethink before building screens.
- The file-name problem is solved with a small hook in the new-maps plugin that serves
  the new screens' files by name from `MMM.DAT`, rather than renaming file-table entries,
  unless a cleaner route turns up while proving the groundwork.
- Early screens use Champions and one or two Ultimates; deeper screens use Ultimates;
  Hagurumon and Tinmon are workers that never fight.
- The art follows the glade's pipeline (painted with local models, collision and
  foreground sprites from the scene, `tools/gen_newmap.py`).

## Left to the builder

- Which Gear Savanna screen and which free exit slot, and where the gate sits on it.
- Each screen's layout, props and art, within the mood above and the 30-tile limit.
- Which machine Digimon go on which screen, their stats, moves and placement (following
  `docs/GAME.md` 6), within 4 species and the heap per screen.
- Item ids for the reward (`dw_decomp/src/main/item.c`), the reward trigger id, and the
  text of the boss's lines.
- Whether the four screens share one DG script with a section per screen or use one each.
- The test tooling, extending `tools/verify_newmap.py` (walk in and out of every screen,
  every fight won, fled and lost, the reward paid once, saves inside resume at the gate,
  the mod off with a card saved inside, the same on 1.5).
