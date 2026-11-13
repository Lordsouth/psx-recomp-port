# Abandoned Factory: the four screens on stock (plan 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Abandoned Factory on the stock disc: four connected screens behind a new gate on Gear Savanna, with machine Digimon, two floor guardians, Machinedramon and a one-time reward, all tested in the game.

**Architecture:** First the glade's generator is split into a shared engine (`tools/newmap_engine.py`) with a byte-identical `--check`. `tools/gen_factory.py` then builds a separate package, `dw.newmaps.factory`. Its four MAP/TFS pairs and four DG scripts live in MMM.DAT, served by the plan-1 filter hook under new names. It has four MAPHEAD sections after the glade's and a new exit on a Gear Savanna screen. `dw_newmaps.c` keeps any save made inside off the card. The in-game helpers move out of `verify_newmap.py` into `tools/field_drive.py`, so that `tools/verify_factory.py` can share them.

**Tech Stack:** Python 3.12 (numpy, Pillow), C plugin code in the port, local ComfyUI for the art, and the port's TCP debug harness (`tools/dwgame.py`).

**Spec:** `docs/superpowers/specs/2026-11-11-abandoned-factory-design.md`. Plan 1 (groundwork) is `docs/superpowers/plans/2026-11-11-new-maps-groundwork.md`; its last section lists the items carried here.

## Global Constraints

- No game data in the repo or in a package: art, collision, sprites, scripts and records are this project's own. `tools/verify_setup_package.py` must pass, with `MOD_PAYLOADS` updated for the new package.
- Fact sources: `docs/GAME.md` (sections 2-6, 9) and `dw_decomp` (read-only). Every new game fact you learn goes into `docs/GAME.md` with its tag (`[run 2026-11-..]`, `[inferred]`).
- Stock disc only. The factory declares `conflicts = ["dw.1_5/dw15"]` until plan 3 builds its 1.5 edition. Nothing here is released, and README and the release notes stay unchanged until plan 3.
- Guarded writes only: every patch's `expected` bytes are read from the disc by the generator, and `disc_sha256` matches the glade's.
- **Rows** 240, 241, 242, 243. **File names** `AFAC1`..`AFAC4` (`.MAP`, `.TFS`); the generator asserts these names appear in neither `FILE_OFFSET_TABLE` nor the ISO directory. **DG rows** 223, 224, 225, 226 (table[223..227]).
- **MMM.DAT** (LBA 148668) from sector 0, sector-aligned, in this order: AFAC1.MAP, AFAC1.TFS, ... AFAC4.TFS, then DG scripts 223..226 back to back. Each DG row's end is the next row's start.
- **MAPHEAD:** the factory's four sections start at the end of the glade's section, read from the glade manifest's `MAPHEAD section 239 body` patch and rounded up to even (today 0x6084). They total at most 180 bytes, so the same sections fit 1.5's tail in plan 3.
- **Triggers:** 627 = floor-1 guardian beaten, 628 = floor-2 guardian beaten, 629 = reward paid. Record them in GAME.md 4.8.
- **Per screen:** at most 8 records and 4 species. The heap estimate is Σ MMD bytes rounded up to sectors, plus bones × (0x88 + 0x52) per placed Digimon (bones from the MMD header, `model.c:2829`), plus 0x4800. It must be ≤ 152,000 B. Each MAP and TFS must be ≤ 0x78800 B (241 sectors). The grid is at most 30 tiles, W ≤ 11, H ≤ 8.
- **Fights:** every fight starts on a fresh confirm (autotalk 0) with trigger 1 clear (fleeable), and the entry hall (AFAC1) has none, so a flee always lands on a factory screen. Guardian and regular fights are predefined battles (pstat 0xFA = 1, fighters' script ids in 0xFB..0xFD, 0xFF for none), so nothing else on screen can join. Each fight screen's arrival section (DG section 0xFE) writes 0xFA = 0 first, because a flee never returns to the talk section.
- **Workers** (Hagurumon 122, Tinmon 123: no battle animations) take autotalk 2, so they never fire, and are never named as fighters.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. No `fprintf` in psxrecomp. Never push. Environment, ports and the seed card: see plan 1's env notes (the venv python, `--seed /c/Users/boura/hdb-rewind`, a fresh `--port` per run, restoring `build-debug/mods/state.toml` afterwards).

## Review Focus

1. **A flee from a predefined fight** leaves pstat 0xFA at 1, and the next stock fight anywhere would run predefined with stale fighters. Expected: after landing from a flee, 0xFA reads 0 (Task 6 test `flee_clears_predefined`).
2. **A worker drawn into a battle** (no battle animations) would hang the fight. Expected: no fight's enemy list holds species 122 or 123 (Task 6 test `no_worker_fights`).
3. **The bag is full when the boss pays.** Expected: nothing is lost or duplicated, trigger 629 stays clear, and the next win pays all seven (Task 7 test `reward_bag_full`).
4. **A card saved on any factory screen** is loaded with the mod off. Expected: it resumes at the Gear Savanna gate and plays (Task 4 tests `saves` and `off`).
5. **The busiest screen runs out of heap** (NULL model, or a hang in a two-enemy fight with effects). Expected: every placed NPC's model is non-null, and a two-enemy fight on each fight screen is won (Task 6 test `models_loaded` plus the group win).

---

### Task 1: Shared map engine, with a byte-identical glade

**Files:**
- Create: `tools/newmap_engine.py`
- Modify: `tools/gen_newmap.py` (keeps the glade's scene, constants, BOSS and main; everything reusable moves out)
- Modify: `tools/fight_sim.py` only if its `from gen_newmap import BOSS` breaks (it must not)

**Interfaces:**
- Produces, in `newmap_engine.py`:
  - `@dataclass Screen` with fields `name: str`, `row: int`, `vp: np.ndarray`, `vr: np.ndarray`, `h: int`, `grid: tuple[int, int]` (GW, GH), `paint: tuple[int, int]`, `lights: list`, `labels: tuple[str, ...]`, `ground: tuple[int, ...]`, `mats: dict`, `flat: dict`, `fg_mats: tuple[str, ...]`, `painted_props: tuple[str, ...]`, `floor: Callable[[x, z], bool-array]` (walkable before props), `strips: dict[int, Callable[[x, z], bool-array]]` (collision value → cells), `warps: np.ndarray` (6 × 10 int16, the MapWarps block), `records: list[bytes]` (each `struct.pack("<45h", ...)` or longer with waypoints), `regions: tuple` (8 ints, A0..A3, B0..B3).
  - Functions of one screen: `project(scr, P, frame=None)`, `depth(scr, P)`, `trace(scr, prims, scale, frame=None)`, `guide(scr, prims, scale=2, frame=None)`, `labels(scr, prims, frame=None)`, `classify(scr, rgb, lab_map)`, `collision(scr, prims, painted=None, fg=None) -> (grid, order)`, `foreground_pixels(scr, prims, painted)`, `build_sprites(scr, fg, order, idx, prims)`, `order_errors(...)`, `quantise(rgb, painted=None)`, `tint(clut, k)`, `build_tfs(scr, idx, cluts) -> bytes`, `build_map(scr, grid, tims=(), objects=b"") -> bytes`, `build_tim`, `build_objects`, `iso_record(f, rr, path)`, `sectors(blob)`, `cell_centre(tx, tz)`, `tile_of(x, z) -> (tx, tz)`.
  - A new primitive, `dict(kind="box", c=(x, y, z), r=(rx, ry, rz), mat=...)`: an axis-aligned box centred at c with half-extents r, handled by `hit` (distance and face normal), `occluded`, the shadow map, `labels` and `collision` (its footprint grown by the character radius, as ellipsoids are).
- Consumes: nothing new.

- [ ] **Step 1: Write the glade check.** Add `--check` to `gen_newmap.py`: build everything in memory and compare with the committed package. That means each asset's bytes, plus the manifest text above the `# ---- generated by tools/gen_newmap_dw15.py below this line` marker. Exit 0 when identical; otherwise exit 1 and name the first differing file. Run `$PY tools/gen_newmap.py --check` on the current code. Expected: PASS (exit 0), so the check reflects what is committed.
- [ ] **Step 2: Write the box self-check.** In `newmap_engine.py`, add a `__main__` self-check: a ray from (0, -100, -1000) along +z hits `box c=(0,-100,0) r=(50,50,50)` at distance 950 with normal (0,0,-1); a ray along +x from (-1000, -100, 0) hits at 950 with normal (-1,0,0); a ray that passes above misses (inf). Run `$PY -I tools/newmap_engine.py`. Expected: it fails, because the module is not written yet.
- [ ] **Step 3: Move the engine.** Move the reusable code into `newmap_engine.py`, turning module globals (VP, VR, H_PROJ, GW/GH, PAINT, LIGHTS, LABELS, GROUND, ...) into `Screen` fields. Then add the box primitive. `gen_newmap.py` builds `GLADE = Screen(...)` from its existing constants and scene, and passes its clearing/path/pond floor test and its warp strip as `floor` and `strips[110]`. `BOSS` stays a module-level name in `gen_newmap.py`.
- [ ] **Step 4: Verify.** `$PY -I tools/newmap_engine.py` passes. `$PY tools/gen_newmap.py --check` passes, so the glade's assets and manifest are byte-identical. `$PY tools/fight_sim.py --check` passes. The five plan-1 gates pass (env notes). Delete `tools/__pycache__`.
- [ ] **Step 5: Commit.** `git add tools/newmap_engine.py tools/gen_newmap.py` → "New maps: shared engine (newmap_engine.py), box primitive, gen_newmap --check".

### Task 2: In-game helpers as a module

**Files:**
- Create: `tools/field_drive.py`
- Modify: `tools/verify_newmap.py` (its closures inside `main()` move out; its modes and verdicts stay as they are)

**Interfaces:**
- Produces: `class Field` in `field_drive.py`, built as `Field(g: Game, check: Callable[[bool, str], None])`, with the methods that are closures in `verify_newmap.main()` today: `pad`, `pos`, `walk`, `wait_for`, `calibrate`, `go`, `near`, `battle`, `loaded_files`, `talk`, `partner`, `win`, `flee`, `lose`, `save_here`, `keep_card`. Same behaviour, with map-specific values passed as arguments rather than read from the glade's spec. Also two new helpers: `pstat(n) -> int` (u8 at 0x801BE031 + n) and `trigger(n) -> bool` (bit n of 0x801BDFCD).
- Consumes: `tools/dwgame.Game`.

- [ ] **Step 1: Run the regression before the change.** `$PY -I tools/verify_newmap.py run1 --map glade --port N --seed /c/Users/boura/hdb-rewind`, then `fight --map glade` on a new port. Expected: both PASS. They are the baseline.
- [ ] **Step 2: Move the helpers.** Lift the helpers into `Field`, change `verify_newmap.py` to use them, and add `pstat` and `trigger`.
- [ ] **Step 3: Rerun the same two runs.** Expected: both PASS with the same verdict lines. Restore state.toml.
- [ ] **Step 4: Commit.** `git add tools/field_drive.py tools/verify_newmap.py` → "Tests: in-game field helpers move to field_drive.py".

### Task 3: The route: four flat screens, a gate on Gear Savanna, files from MMM.DAT

**Files:**
- Create: `tools/gen_factory.py`, `tools/verify_factory.py`
- Create (generated): `mods/preloaded/packages/dw.newmaps.factory/1.0.0/manifest.toml` + `assets/`
- Modify (generated): `dw_newmaps_files.inc` (AFAC rows replace FACT0's)
- Modify: `dw_newmaps.c` (`DW_FILE_PLUGINS = { "dw.newmaps.factory" }`), `tools/check_dw15_compat.py` (`HOOK_OWNER[0x800A31F8] = "dw.newmaps.factory/factory"`), `tools/verify_setup_package.py` (`MOD_PAYLOADS`)
- Delete: `tools/gen_newmap_proof.py`, the `--proof` half of `tools/verify_newmap_files.py`, `MAPS["proof"]` in `tools/verify_newmap.py`. The proof's job passes to the factory, and the local proof package under `build-debug/mods/installed/` is removed.
- Docs: `docs/GAME.md` 2.3 (the factory replaces the proof as the route's evidence), 3.1 (rows 240-243 taken), 4.1 (DG rows 223-226 taken); `docs/INTERNALS.md` 3.3 (new subsection 3.4, "The Abandoned Factory").

**Interfaces:**
- Consumes: `newmap_engine.Screen` and its build functions (Task 1); `field_drive.Field` (Task 2); the filter hook and `dw_newmaps_file()` (plan 1).
- Produces:
  - In `gen_factory.py`: `SCREENS: list[Screen]` in row order 240..243; `HOST = dict(file, row, warp, cells, spawn, save_exit)`; `layout() -> list[(name, sector, bytes)]` for the MMM.DAT order in Global Constraints; `mh_sections() -> bytes`; `dg_script(i) -> bytes`. CLI: `$PY tools/gen_factory.py [--bin] [--build] [--preview] [--check]`, where `--check` regenerates in memory and compares the manifest, the assets and `dw_newmaps_files.inc`.
  - The build writes `build-debug/newmap/factory/spec.json`, which verify_factory reads: for each screen `row`, `spawn[0]`, `spawn[2]`, `strip_back`, `strip_fwd`, `walk` (three floor spots) and `camera`; plus `host`.
  - **Warp slots on every screen:** slot 0 is the arrival walking forward, and cell 110 leads back (to the previous screen's exit 2, or for AFAC1 to the host's new slot). Slot 1 is where a save resumes, the host gate's spawn (no cell 111). Slot 2 is the arrival walking back, and cell 112 leads forward to the next screen's exit 0. AFAC4 has no slot 2. Arrivals stay 2 cells off their own exit strip.
  - **Row entry:** flags 0xC8 (sound bank 8, no time of day, map-Digimon records), so each TFS has one CLUT. `loadingName` is Gear Savanna's (read from row 69 byte 15). Regions are A0 = 3 (machines), the rest -1. No doors or toilet. BGM: the `5D` operand and pstat-0xF5 value of row 152's MAPHEAD section, read from the disc and asserted to be one value.

- [ ] **Step 1: Pick the host.** Pick it from Gear Savanna rows 69-78 and 131 (`GIAS*`; all have free warp slots). Render each candidate's backdrop with its collision overlay into `build-debug/newmap/factory/hosts/` (local only), and choose an edge where the stock art already shows a way on: an opening, a path or a structure running off-frame, which no stock warp uses. Its two-row warp strip must be wall then floor, as the glade's MAYO03 entrance is, and the return spawn and the partner's spot must be floor. Write the choice into `HOST` and into GAME.md 3.1 as the entrance's row, slot and cells, with one line on why that edge.
- [ ] **Step 2: Write the failing test.** In `verify_factory.py`, mode `walk`, on a fresh sandbox card: warp to the host and walk through the gate. On each of AFAC1..AFAC4, check that `CURRENT_SCREEN` is the row and that `Field.loaded_files()` shows the screen's served `AFAC<n>` MAP/TFS (LBA and size from `dw_newmaps_files.inc`). Walk to the three `walk` spots, then take the forward strip. On AFAC4, walk back through every screen to the host, landing at each previous screen's spawn 2 and at the host's return spawn. `--wide` repeats the screen checks at 21:9 with the sliver NCC check from plan 1's `verify_newmap_files.py`, moved into `field_drive` (threshold 0.9, lit > 20). Run it. Expected: FAIL, because the package does not exist.
- [ ] **Step 3: Write the scenes.** Write four scenes in `gen_factory.py` with boxes, cylinders and ellipsoids, in the mood of the spec (an abandoned factory whose machines still run), resembling no stock map. Each screen uses the glade's elevated camera convention: entry at the bottom (-z), the way on at the top (+z), and a 6×4 grid unless a layout needs more (≤ 30 tiles).
  - **AFAC1, entry hall:** open floor and idle machinery, no chokepoint.
  - **AFAC2 and AFAC3, production floors:** a line of machinery across the middle-back with one gap exactly 3 cells wide. That gap is the only way to the forward strip, so a guardian standing on its middle column blocks it (GAME.md 3.9: the tamer's centre passes only the middle column). Open floor on both sides, in front, for the regulars.
  - **AFAC4, boss chamber:** sparse, with a large open floor for Machinedramon (radius 400) farther than its tracking range + 300 from spawn 0.
  - Labels: `floor`, `grate` (ground), `machine`, `pipe`, `wall`, `crate`. `fg_mats = ("machine", "pipe")`.
- [ ] **Step 4: Write the generator.** It uses the flat guides as art, and the art arrives in Task 5. Its asserts:
  - the names are unique (Global Constraints) and the MMM.DAT sectors are zero on the disc;
  - each MAP and TFS is ≤ 0x78800;
  - each DG script is ≤ 0x4000 and the rows are consecutive;
  - table[222] equals DG.SCN's size;
  - rows 240-243 are zeros and their MAPHEAD rows hold the stock filler (`fb 00 b4 00`);
  - the sections total ≤ 180 bytes and the MAPHEAD tail past EOF is zero;
  - the host slot is free and the stock save exit is a real one;
  - every spawn is floor and every strip is reachable.

  Each DG script is a section table with one arrival section 0xFE, which in this task is just `FE 00`. Each MAPHEAD section is `FB 00 <script> <row>`, `1E 00 F5 <pstat>`, `5D <bgm>`, `FE 00`. Manifest: package `dw.newmaps.factory`, feature `factory` (name "Abandoned Factory", group "New maps", `default_enabled = false`, `conflicts = ["dw.1_5/dw15"]`), with `[[plugin]] id = "dw.newmaps.factory"`. Overlays are the MMM.DAT files. Patches: the four MAP_ENTRIES rows, one 20-byte table[223..227] patch, one patch for the section bodies, four section pointers, and the host's warp slot fields and collision rows. Write `dw_newmaps_files.inc` with the eight AFAC rows. Make the code, compat and payload edits listed under Files, and do the deletions.
- [ ] **Step 5: Build and verify.** Run `$PY tools/gen_factory.py`, then `cmake --build build-debug --target psx-runtime`. Then `$PY -I tools/verify_factory.py walk --port N --seed /c/Users/boura/hdb-rewind` and `walk --wide` on a new port. Expected: PASS. The five gates pass, and `check_dw15_compat` lists `dw.newmaps.factory/factory` as conflicting and declared. `verify_setup_package.py` passes. `gen_factory.py --check` and `gen_newmap.py --check` pass. The glade's `verify_newmap.py run1 --map glade` still passes, and so does `verify_newmap_files.py`, which keeps its regression half. Restore state.toml.
- [ ] **Step 6: Commit** all files listed above → "Abandoned Factory: four flat screens through a Gear Savanna gate, files and scripts from MMM.DAT".

### Task 4: Saves made inside never name the factory

**Files:**
- Modify: `dw_newmaps.c`, `tools/gen_newmap.py` (its check on `dw_newmaps.c`'s defines), `tools/gen_factory.py` (the same check for its rows), `tools/verify_factory.py`
- Docs: GAME.md 9.3

**Interfaces:**
- Produces, in `dw_newmaps.c`: `typedef struct { const char* plugin; uint8_t first_row, last_row, host_row, host_exit, stock_exit; } DwMap;` and `static const DwMap k_maps[] = { {"dw.newmaps", 239, 239, 2, 4, 0}, {"dw.newmaps.factory", 240, 243, <HOST row>, <HOST warp>, <HOST save_exit>} };`. One thin entry callback per map on `createSavegameChecksum` (0x80112D34) calls `remap(&k_maps[i])`, which is today's logic with `== DW_ROW` widened to `first_row..last_row`. Each callback is registered under that map's plugin id. The generators assert their row in `k_maps`, rather than in the old `#define`s.
- Consumes: warp slot 1 on every factory screen (Task 3).

- [ ] **Step 1: Write the failing tests.** In `verify_factory.py`:
  - **`saves`:** on a fresh card, walk in and Save Anywhere on each of AFAC1..AFAC4. After each save, read the card: `currentScreen` is the host row, the exit is `save_exit`, and the position is the gate's return spawn. Then walk out to the host and save there; the card's `previousScreen` names no row 240-243. Keep the last card as `factory-inside`.
  - **`off`:** with the factory feature off, Continue from `factory-inside`. It lands on the host and the player walks.

  Run both. Expected: FAIL, because the card names row 24x.
- [ ] **Step 2: Implement** `DwMap`, `remap` and the registrations, and update both generators' asserts. Rebuild.
- [ ] **Step 3: Verify.** `saves` and `off` pass. The glade's `run2 --map glade`, `off --map glade --card glade` and `off --map glade --card host` still pass. `gen_newmap.py --check` and `gen_factory.py --check` pass. Restore state.toml.
- [ ] **Step 4: Commit** → "New maps: the save remap covers every map package (factory rows 240-243)".

### Task 5: Painted art for the four screens

**Files:**
- Create: `tools/paint_factory.py`, `art/newmaps/factory_1.png` .. `factory_4.png` (+ `.json` with seeds and settings, as `glade.json`)
- Modify: `tools/gen_factory.py` (reads the art: classify, ground IoU, foreground sprites, collision fitted to the art), the regenerated package, `tools/verify_setup_package.py`
- Docs: INTERNALS 3.4 (the painting recipe and why it differs from the glade's)

**Interfaces:**
- Consumes: the ComfyUI client and the Qwen-Image-Edit and Z-Image passes 1-2 from `tools/paint_newmap.py`, imported rather than copied; `gen_factory.py --preview` writes `build-debug/newmap/factory/guide_<n>.png` and the label maps.
- Produces: `$PY tools/paint_factory.py [--screen N]` writes `art/newmaps/factory_<n>.png` at the paint frame size of each `Screen`.

- [ ] **Step 1: Write the art checks** in `gen_factory.py`, so a missing or mismatched painting fails loudly:
  - each screen's art matches its paint frame size;
  - its ground IoU against the scene is ≥ 0.75;
  - every record position, spawn and strip cell is floor after the art-fitted collision;
  - the guardian's 3-cell gap still has exactly one passable column.

  With no art present the flat guide is still used, so Task 3's package stays buildable.
- [ ] **Step 2: Write the painter.** It runs passes 1-2 with factory prompts: pre-rendered 1999 PlayStation field-map CG of an abandoned factory interior, machines still running, dust and dim work lights, no people. It has no foliage pass. Ground detail comes from concrete and steel-plate textures, generated once with Z-Image like the glade's grass. The colour statistics are pulled half way toward the mean and standard deviation of the stock interior field maps (the rows with flag 0x40, measured as numbers, as `FOREST_LAB` was), not toward Factorial Town's. Seeds are fixed per screen. Paint the four screens, pick each by the ground IoU and a look at it, and commit the PNG and JSON.
- [ ] **Step 3: Regenerate and retest.** Run `gen_factory.py`, then rebuild. `verify_factory.py walk` and `walk --wide` pass on the painted screens. Look at the four `backdrop_day.png` previews and the in-game screenshots; they are MEASURE evidence, not verdicts. `verify_setup_package.py` passes, and `gen_factory.py --check` passes.
- [ ] **Step 4: Commit** → "Abandoned Factory: painted art for the four screens".

### Task 6: Regular enemies, workers and the two floor guardians

**Files:**
- Modify: `tools/gen_factory.py` (records, MAPHEAD `LOAD_DIGIMON`/`SET_DIGIMON` with the guardian IFs, DG talk and arrival sections), `tools/verify_factory.py`, `tools/fight_sim.py` (`--factory`: the same win-rate table for every factory record, through `enemy_from`)
- Docs: GAME.md 4.8 (triggers 627, 628), 6 (predefined fleeable fights, the 0xFA reset on arrival), 3.11 (the measured heap estimate per screen)

**Interfaces:**
- Consumes: `SCREENS`, `mh_sections`, `dg_script` (Task 3); `Field.win/flee/lose/talk/battle/pstat/trigger` (Task 2).
- Produces: `CAST: dict[int, list[Rec]]` in `gen_factory.py` keyed by row, with `Rec = (species, slot, script_id, role, xyz, words)`, where role is one of "worker", "regular", "guardian" or "boss", and a group id for regulars who fight together.

**Cast.** Species come from the spec's fighter list. The smaller-model variants 136, 137, 161 and 172 are allowed when they pass the checks below. Within ≤ 4 species and the heap budget, the preferred cast is:
- **AFAC1:** two Hagurumon and one Tinmon.
- **AFAC2:** a Guardromon pair and a Tankmon pair, plus the guardian (Giromon, or variant 161 if 41 breaks the budget).
- **AFAC3:** Mamemon (137) and one further Champion or Ultimate in pairs, plus the guardian (Megadramon 172).

The generator asserts, for every fighter:
- it has field and battle anims (`missing_anims` empty in `tools/dw_tables.py`'s species data);
- each move exists in its `DIGIMON_DATA.moves`;
- each move's EFE list holds `getOriginalType(type)` (GAME.md 6 step 2);
- the finisher is only in slot 3;
- currentHP = HP, currentMP = MP, brain ≥ 297;
- MP covers at least 30 picks at `mpCost*3`;
- it stands farther than trackingRange + 300 from every arrival.

Regulars use followMode 2 or 12, stand on open floor beside the path, and come back every visit. Guardians use followMode 2, range 600, and stand on the middle column of their screen's gap. Workers walk short waypoint loops (anim 2: their MMDs have it), with autotalk 2.

**Difficulty**, from `fight_sim.py --factory`, against the "Ultimate partner, 3 × 5000 heals" row:
- regulars win ≥ 90 %;
- guardians fall within ± 10 points of the glade boss's current rate;
- record the numbers in the generator's comments.

**Scripts:**
- **Arrival (each fight screen):** `1E 00 FA 00`, then `FE 00`.
- **Regular talk section:** `1B id`, `1A` one line, `27 00 67 00 01 00`, then `1E 00 FA 01` and `1E 00 FB/FC/FD <script ids of its group, 0xFF padded>`, then `66 00`. The win path is `1E 00 FA 00`, `FE 00`.
- **Guardian:** the same, with its win path `1C 00 <627|628>` before `1E 00 FA 00`.
- **Row section:** wraps the guardian's `46`/`47` in GAME.md 4.3's IF on its trigger (set → skip). Lines are ≤ 28 characters of `[A-Za-z0-9 .!?]`.

- [ ] **Step 1: Write the failing tests** in `verify_factory.py`, mode `fights`, on a fresh card, with HP poked as `verify_newmap fight` does:
  - **`models_loaded`:** on each of AFAC1..AFAC3, every placed record is `isOnMap` and its NPC model slot is non-null.
  - **`no_worker_fights`:** for each fight, the battle's enemy entity list holds exactly the group's script ids, never species 122 or 123.
  - **Regular group (AFAC2):** standing beside it starts nothing; confirm starts a 2-enemy battle with Run offered. Win: bits are paid and both leave. Reload the screen: both are back. Flee: you land on AFAC1 spawn 2. Lose: you end on row 205 or 218.
  - **Guardian (AFAC2):** holding toward the forward strip from in front of the gap for 10 s never reaches it. Flee (`flee_clears_predefined`): you land on AFAC1 spawn 2, and pstat 0xFA reads 0. Lose: you end on row 205 or 218. Win: trigger 627 is set and the guardian runs off. Walk through the gap to AFAC3. Come back to AFAC2: the guardian is absent and the gap is open.
  - **The same guardian checks on AFAC3** with trigger 628.

  Run it. Expected: FAIL, because there are no records yet.
- [ ] **Step 2: Implement** the cast, scripts, row sections and the `fight_sim --factory` table. Regenerate and rebuild.
- [ ] **Step 3: Verify.** `fights` passes, and so does `walk`, now with Digimon on the screens. Run `fights --wide` once. The gates and both `--check`s pass. Restore state.toml.
- [ ] **Step 4: Commit** → "Abandoned Factory: machine Digimon, workers and the floor guardians".

### Task 7: Machinedramon and the reward

**Files:**
- Modify: `tools/gen_factory.py` (the boss record, AFAC4's row and DG sections, the reward), `tools/verify_factory.py`, `tools/fight_sim.py` (the boss in `--factory`)
- Docs: GAME.md 4.8 (trigger 629), 6 (the all-or-nothing `GIVE_ITEM` pattern: trigger 0 on a failed give, rolled back with `REMOVE_ITEM`)

**Interfaces:**
- Consumes: `CAST`, the script builders (Task 6).
- Produces: `REWARD = (Metalbanana, Offense Chip, Defense Chip, Brain Chip, Quick Chip, HP Chip, MP Chip)` as item ids from `dw_decomp/src/main/item.c`'s item table. The generator asserts each name through the `ITEM_PARA_NAME` strings (`STR_ITEM_NAME_METALBANANA`, `_OFF_CHIP`, `_DEF_CHIP`, `_BRAIN_CHIP`, `_QUICK_CHIP`, `_HP_CHIP`, `_MP_CHIP`).

**The boss:** Machinedramon (115), alone on AFAC4, followMode 2, range 1200, autotalk 0. Its fight is not predefined (nothing can join on an empty screen), so it plays its own battle music. On `fight_sim --factory` it is harder than the glade boss: its win rate is at least 10 points below the glade boss's and above 0. MP covers at least 60 picks.

**Win path:**
1. IF trigger 629 is set, end: a later win pays bits only.
2. Otherwise, for each reward item in order: `28 00 <item> 01`, then IF trigger 0 is set, jump to the rollback for that position.
3. All given: `1C 00 75 02` (629), then text: two lines saying the machines fall silent and that you found a Metalbanana and six chips. End.
4. Rollback k: `29 00 <item_j> 01` for each item j < k already given this run, then text that the bag is full and to make room, then end with 629 clear.

- [ ] **Step 1: Write the failing tests**, mode `boss`, with HP poked:
  - **Boss:** confirm opens the dialogue and a battle with Machinedramon alone; Run is offered.
  - **First win:** the bag gains exactly one of each of the seven items, trigger 629 is set, and the bits are paid.
  - **Second win:** the bag is unchanged, the bits are paid, and 629 stays set.
  - **`reward_bag_full`:** on a fresh card whose bag has one free slot, a win leaves the bag exactly as before and 629 clear. Free the slots and win: all seven are paid and 629 is set.
  - **Flee and lose:** a flee lands on AFAC3 spawn 2; a loss ends on row 205 or 218.

  Run it. Expected: FAIL, because there is no boss.
- [ ] **Step 2: Implement.** Regenerate and rebuild.
- [ ] **Step 3: Verify.** `boss` passes. `fights`, `walk` and `saves` still pass. Run `boss --wide` once. Run one unpoked `measure`-style fight per tier (guardian and boss), and log it as MEASURE beside the glade's. The gates and both `--check`s pass. Restore state.toml.
- [ ] **Step 4: Commit** → "Abandoned Factory: Machinedramon and the one-time reward".

### Task 8: Docs and the full sweep

**Files:**
- Modify: `CHANGELOG.md` (an Unreleased entry: the Abandoned Factory on the stock disc; the 1.5 edition to follow), `docs/INTERNALS.md` 3.4 (complete), `docs/GAME.md` (anything still tagged as planned rather than measured)

- [ ] **Step 1: Run the whole suite, one run at a time on new ports.**
  - Factory: `walk`, `walk --wide`, `saves`, `off`, `fights`, `boss`.
  - Glade: `run1`, `run2`, both `off` modes, `fight`, `fight --wide`.
  - `verify_newmap_files.py`, `verify_dw15_states.py` and `verify_roster15.py`, which show 1.5 is unharmed.
  - The five gates, `verify_setup_package.py`, `verify_disc_patches.py`, and both `--check`s.

  Expected: all PASS. Record the run list with ports in the report.
- [ ] **Step 2: Update the docs**, so that every factory fact in GAME.md carries a `[run 2026-11-..]` tag from these runs.
- [ ] **Step 3: Commit** → "Abandoned Factory: docs and test sweep (stock disc)".

## Carried from plan 1

| Item | Where |
|---|---|
| One owner of `dw_newmaps_files.inc`, the MMM.DAT allocator, separate proof and factory ranges | Task 3: `gen_factory.py` owns the `.inc` and lays out MMM.DAT; the proof is retired |
| A check that the `.inc` matches the package | Task 3: `gen_factory.py --check` |
| Sliver fallback not gated, lookup order | No change needed: Task 3 asserts that AFAC names exist neither in the stock table nor in the ISO directory, so no stock name is ever served and the order cannot matter |
| DG script size, consecutive rows | Task 3's generator asserts |
| Hard-coded MAPHEAD end 0x6084 | Task 3 reads it from the glade's manifest (Global Constraints) |
| Break the 21:9 sliver check once on purpose | Task 3 Step 5: in a scratch build, make `dw_tfs_read_served` in `dw_ws_sliver.c` return 0, show `walk --wide` fails its sliver check, restore it, and note it in the report |
| `HOOK_OWNER[0x800A31F8]` | Task 3: `dw.newmaps.factory/factory` |
| `PROOF_BODY` duplication, StopIteration flow, empty-state launch | Moot: Task 3 deletes the `--proof` half |
| Compat no longer checks entry-hooked functions (`HOOK_OWNER[0x80112D34]` unused) | Plan 3: the save remap's addresses and return addresses must be checked against 1.5 there |
| 1.5 facts (DG rows from 227, no room after 1.5's DG, a tail per edition) | Plan 3 |

## Carried to plan 3 and polish (from execution)

| Item | Where |
|---|---|
| Factory MAPHEAD sections overlap `glade-15`'s 1.5 bytes; `check_mod_collisions` exempts only that never-together pair | Plan 3: give the factory's 1.5 edition its own bytes, then drop the exemption |
| `HOOK_OWNER[0x80112D34]` holds one owner while two plugins hook it (final review minor 3) | Plan 3, when factory-15 registers the same hook |
| Heap forced a narrower cast (AFAC2 Guardromon pairs + Giromon; AFAC3 Mamemon 137 pairs + Megadramon 172; workers only on AFAC1) | Polish: more species variety if the heap allows |
| Pair fights are not simulated (fight_sim is per fighter); unpoked measure fights lose on the reference card, as the glade boss does (GAME.md 7.18) | Polish: balance pass |
| Painted crates overlap at their lower edges (7-of-9 foot rule); art is clean for "abandoned" and repeats orange crates | Polish: art pass |
| `worker_confirm` is a smoke test only (workers' autotalk 2 never fires); low-HP pair fight variant not written | Polish |
