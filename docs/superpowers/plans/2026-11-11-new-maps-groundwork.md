# New-Maps Groundwork Implementation Plan (Abandoned Factory, plan 1 of 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove, on the stock disc, that a new map row can load map files stored in `MMM.DAT` under a new file name, and run a DG script stored at a new script-table row, so the factory's four screens have somewhere to live.

**Architecture:** A replace hook on the game's `lookupFileTable` (`0x800A31F8`) answers for new file names from a small generated table of `{name, lba, size}` and falls through to the stock body for every other name. New DG scripts get table rows 223+, whose offsets reach past DG.SCN into `MMM.DAT`. A local-only proof package (row 240, `FACT0`) carries the Hidden Glade's own files and script into `MMM.DAT` so the routes are tested with known-good content. The widescreen sliver, which reads a map's TFS by ISO path, learns the same names through a new framework call that reads sectors by LBA.

**Tech Stack:** C (port plugins, psxrecomp runtime), Python 3.12 tools, TOML manifests, the in-game debug server (`tools/dwgame.py`).

**Spec:** `docs/superpowers/specs/2026-11-11-abandoned-factory-design.md` (this plan covers its "prove the engine groundwork" call; plan 2 builds the four screens on stock, plan 3 the 1.5 edition).

## Global Constraints

- No `fprintf` in psxrecomp source (`psxrecomp/CLAUDE.md` rule 3); diagnostics go through the debug server.
- No game data in the repo or the setup package: the proof package is local-only, written to `build-debug/mods/installed/` like `tools/gen_newmap_spike.py`'s, never committed; `tools/verify_setup_package.py` must keep passing.
- `MMM.DAT`: LBA 148668, 13,500 sectors, all zero user bytes; DG.SCN at LBA 142589 (stock). A DG row offset is `(lba - 142589) * 2048 + byte`, in whole sectors from DG.SCN's start (`docs/GAME.md` 2.3, 4.1).
- Never touch DG `table[222]` (DG.SCN's end and script 221's size); `table[223]` onward holds stale ASCII that a guard must expect.
- Free map rows: 240-246, 250-252. The proof uses row 240.
- MAPHEAD tail `0x6070`-`0x61A8` (312 bytes): the glade's section occupies `0x6070`-`0x6083`; the proof's goes after it.
- A replace hook never coexists with a code patch on the same function (`mod_plugins.h`): `lookupFileTable` must stay byte-identical.
- Every new `game.toml` hook needs an owner in `HOOK_OWNER` in `tools/check_dw15_compat.py`.
- Gates that must pass at every commit: `check_mod_collisions`, `verify_disc_patches`, `check_dw15_compat`, `gen_newmap_dw15 --check`, `gen_roster_dw15 --check`, and the glade's `verify_newmap run1` and `fight`.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; commit locally, push only on the user's word.
- **The gate:** if the proof (Task 5) fails on either route, stop and report; do not start plan 2.

## Review Focus

- A stock file name (any of the 818) asked while the new-maps plugin is active must still resolve through the stock table: Task 2's test loads `MAYO03`, the glade and a DG script with the hook live.
- The mod off with nothing ticked: row 240 stays empty and no lookup is answered for `FACT0.*`: Task 5 boots with an empty state and asserts both.
- A savestate taken on row 240 must load back onto row 240 with its files resident: Task 5 saves and loads there.
- At 21:9 the sliver must find the proof's TFS (no black edges): Task 5 runs the proof with widescreen at 21:9 and asserts the sliver's read succeeded.
- A wrong guard on DG `table[223..224]` (stale ASCII) must refuse the plan before launch, never corrupt a script: Task 3's generator reads those bytes from the disc for `expected`, and `verify_disc_patches` checks them.

---

### Task 1: Framework: read disc sectors by LBA

**Files:**
- Modify: `psxrecomp/runtime/include/mod_plugins.h` (next to `psx_mod_read_disc_file`, line ~240)
- Modify: the file that implements `psx_mod_read_disc_file` (find it with `grep -rn "int psx_mod_read_disc_file" psxrecomp/runtime/src`)
- Modify: `psxrecomp/docs/MOD_PACKAGES.md` (the paragraph documenting `psx_mod_read_disc_file`)
- Test: `psxrecomp/runtime/tests/test_mod_runtime.cpp` (beside the `host_file(...)` checks in the disc-delta test, ~line 950)

**Interfaces:**
- Produces: `int psx_mod_read_disc_sectors(uint32_t lba, uint32_t count, void* buffer);` returns 1 when all `count` 2048-byte user sectors were read into `buffer`, 0 otherwise. Same thread rule and the same view as `psx_mod_read_disc_file`: active sector mods and the disc delta apply, guest CD state and timing untouched.

- [ ] **Step 1: Write the failing test** in the disc-delta block, after the `host_file("STOCK.BIN", ...)` check:

```cpp
std::vector<uint8_t> raw(2 * 2048);
check(psx_mod_read_disc_sectors(18, 2, raw.data()) &&
          std::memcmp(raw.data(), effective.data() + 18 * kRaw + 24, 2048) == 0 &&
          std::memcmp(raw.data() + 2048, effective.data() + 19 * kRaw + 24, 2048) == 0,
      "sector reads must serve the delta and its writes, past the stock end too");
check(!psx_mod_read_disc_sectors(0xFFFFFF, 1, raw.data()),
      "a sector past the image must fail, not read garbage");
```

- [ ] **Step 2: Build and run; expect a link error for `psx_mod_read_disc_sectors`**

Run: `cmake --build psxrecomp/runtime/build-tests-verify --target mod_runtime_test`

- [ ] **Step 3: Implement `psx_mod_read_disc_sectors`** beside `psx_mod_read_disc_file`, through the same sector reader it uses (so overlays, patches and the delta apply). No `fprintf`.

- [ ] **Step 4: Run the tests**

Run: `psxrecomp/runtime/build-tests-verify/mod_runtime_test.exe`
Expected: `mod runtime tests passed`

- [ ] **Step 5: Document it in `MOD_PACKAGES.md` and commit in `psxrecomp`**

```bash
git -C psxrecomp add runtime/include/mod_plugins.h runtime/src runtime/tests/test_mod_runtime.cpp docs/MOD_PACKAGES.md
git -C psxrecomp commit -m "mod plugins: psx_mod_read_disc_sectors"
```

### Task 2: Port: serve new map files by name

**Files:**
- Create: `dw_newmaps_files.h`, `dw_newmaps_files.c` (the name table and lookup; one responsibility)
- Create (generated, committed empty): `dw_newmaps_files.inc` (table rows; Task 3 fills it for the proof locally)
- Modify: `dw_newmaps.c` (register the replace hook in its activation, gated on the plugin being live)
- Modify: `game.toml` (`mod_function_replace_funcs` gains `"0x800A31F8"`, with a comment block like the others)
- Modify: `CMakeLists.txt` (compile `dw_newmaps_files.c`; follow how `dw_newmaps.c` is listed)
- Modify: `tools/check_dw15_compat.py` (`HOOK_OWNER[0x800A31F8] = "dw.newmaps/glade"`)
- Test: `tools/verify_newmap_files.py` (in-game, below)

**Interfaces:**
- Produces: `int dw_newmaps_file(const char* name, uint32_t* lba, uint32_t* size);` 1 when `name` (the part after the last `\`, compared case-sensitively as the game does) is in the table. Table row type: `typedef struct { const char* name; uint32_t lba; uint32_t size; } DwNewmapsFile;` with `static const DwNewmapsFile k_newmaps_files[] = { #include "dw_newmaps_files.inc" };`.
- Produces: the replace callback for `lookupFileTable(FileLookup* a0, char* a1)`: on a hit, writes `FileLookup.pos` (CdlLOC: BCD minute, second, sector of `lba + 150`, track 0) and `FileLookup.size` (offset 4), sets `v0 = 1`, `pc = ra`, returns 1; otherwise returns 0 (stock body runs).

- [ ] **Step 1: Write the regression half of** `tools/verify_newmap_files.py`: boot stock with the glade ticked (so the hook is live) and the proof package absent; `g.warp(2, 0)` and assert map 2 (`MAYO03`) loads and plays (`screen() == 2`, frames advance); `g.warp(239, 0)` and assert the glade loads (row 239 is `MGEN17`, its TFS resident); talk to the glade boss and assert the battle starts (DG.SCN read through the hooked lookup). Task 5 adds the new-name half.

- [ ] **Step 2: Run it before the hook exists; expect PASS** (it pins stock behaviour so Task 2 cannot break it)

Run: `py -3.12 tools/verify_newmap_files.py --seed <card dir>`

- [ ] **Step 3: Implement the table, lookup and hook.** CdlLOC from an LBA: `l = lba + 150; minute = l / 4500; second = (l / 75) % 60; sector = l % 75;` each stored as BCD (`(v / 10) << 4 | v % 10`), track byte 0. Read the path with `psx_mod_read_byte` up to a NUL (cap 64).

- [ ] **Step 4: Regenerate and rebuild** (a new replace hook is compiled into the generated code)

Run: `py -3.12 psxrecomp/psxrecomp_cli.py generate --config game.toml --project-root . --disc "disc/Digimon World (USA).cue"` then `cmake --build build-debug --target psx-runtime`

- [ ] **Step 5: Rerun the test and the gates**

Run: `py -3.12 tools/verify_newmap_files.py --seed <card dir>`, `verify_newmap run1 --map glade`, `check_dw15_compat`
Expected: all PASS; `check_dw15_compat` reports the new hook under `dw.newmaps/glade` and the glade still `edition`.

- [ ] **Step 6: Commit**

```bash
git add dw_newmaps_files.h dw_newmaps_files.c dw_newmaps_files.inc dw_newmaps.c game.toml CMakeLists.txt tools/check_dw15_compat.py tools/verify_newmap_files.py
git commit -m "New maps: serve new map files by name (lookupFileTable hook)"
```

### Task 3: Proof package generator (local only)

**Files:**
- Create: `tools/gen_newmap_proof.py`

**Interfaces:**
- Consumes: the glade's committed assets `mods/preloaded/packages/dw.newmaps/1.0.0/assets/glade.map`, `glade.tfs`, `glade.dg`; the glade manifest's row-239 record and MAPHEAD section (read from its patches).
- Produces: `build-debug/mods/installed/dw.newmaps.proof/1.0.0/manifest.toml` (format 13, feature `proof`, `[[plugin]] id = "dw.newmaps"`) and the local `dw_newmaps_files.inc` rows `{"FACT0.MAP", lba, size}`, `{"FACT0.TFS", lba, size}`. Writes:
  - `[[overlay]]` `disc_user` of `glade.map` then `glade.tfs` at `MMM.DAT` sector 0 (LBA 148668) and following, sector-aligned;
  - `[[overlay]]` `disc_user` of `glade.dg` at the next free `MMM.DAT` sector;
  - `[[patch]]` `disc_user` DG `table[223]` and `table[224]` (u32 each at DG.SCN offset `223*4`): the script's offset from DG.SCN's start and its end; `expected` read from the disc;
  - `[[patch]]` `main_exe` `MAP_ENTRIES[240]` (`0x801292D4 + 240*16`): the glade's row-239 record with the name `FACT0`;
  - `[[patch]]` `disc_user` a MAPHEAD section for row 240 at the first even offset after `0x6084`: the glade's section with `SET_SCRIPT 223, row 240`; and row 240's table pointer.

- [ ] **Step 1: Write the generator with asserts:** `MMM.DAT` sectors used are all zero on the disc; DG `table[222]` is the file's end; the MAPHEAD section fits below `0x61A8`; row 240's record and section are the stock filler before writing.
- [ ] **Step 2: Run it**

Run: `py -3.12 tools/gen_newmap_proof.py --build build-debug`
Expected: prints the manifest path and the two `.inc` rows; `verify_disc_patches` reports `dw.newmaps.proof` OK.

- [ ] **Step 3: No commit of the package or the filled `.inc`** (`git status` shows only `tools/gen_newmap_proof.py`); commit the tool:

```bash
git add tools/gen_newmap_proof.py
git commit -m "New maps: local proof package for the groundwork (row 240 from MMM.DAT)"
```

### Task 4: Widescreen sliver reads served files

**Files:**
- Modify: `dw_ws_sliver.c` (`dw_tfs_read`, ~line 193)

**Interfaces:**
- Consumes: `dw_newmaps_file` (Task 2), `psx_mod_read_disc_sectors` (Task 1).

- [ ] **Step 1:** In `dw_tfs_read`, when `psx_mod_read_disc_file(path, ...)` fails, look up `<name>.TFS` with `dw_newmaps_file`; if found and it fits `cap`, read `ceil(size / 2048)` sectors with `psx_mod_read_disc_sectors` into `buf` and set `*size`.
- [ ] **Step 2: Rebuild** `cmake --build build-debug --target psx-runtime` (no regenerate needed: no hook change).
- [ ] **Step 3: Commit** after Task 5's 21:9 check passes.

### Task 5: The proof in the game, and the gate

**Files:**
- Modify: `tools/verify_newmap.py` (`MAPS["proof"] = dict(package="dw.newmaps.proof", feature="proof", file="FACT0")`, reusing the glade's spawn and checks; the row is 240)
- Modify: `tools/verify_newmap_files.py` (the new-name half)
- Modify: `docs/GAME.md` 2.3 and 4.1, `docs/INTERNALS.md` 3.x (record what was proven, with `[run]`)

- [ ] **Step 1: Write the new-name checks** in `verify_newmap_files.py`, run with the proof package installed and ticked: `g.map_record(240)["file"] == "FACT0"`; after `g.warp(240, 0)`, the resident TFS in `GENERAL_BUFFER` equals `glade.tfs`'s first bytes (as `verify_newmap`'s `loaded_files` does); collision is live; standing by the boss and pressing confirm starts a battle (proves DG row 223 ran); a savestate saved and loaded on row 240 returns there with the TFS resident; with an empty `state.toml`, row 240 is empty.
- [ ] **Step 2: Run them, then `verify_newmap run1 --map proof --wide`** (21:9: the sliver's backdrop match must be as good as the glade's, NCC above 0.7)

Run: `py -3.12 tools/verify_newmap_files.py --seed <card dir> --proof` and `py -3.12 tools/verify_newmap.py run1 --map proof --wide --seed <card dir>`
Expected: all PASS. **If anything fails on either route, stop: report which route failed and why; plan 2 waits.**

- [ ] **Step 3: Rerun the glade's `run1` and `fight`, and the gates**, with the proof package removed from `build-debug/mods/installed/`.
- [ ] **Step 4: Record the result in the docs** (`MMM.DAT` and DG rows 223+ move from "not run" to `[run]`, with the date and the test) and commit:

```bash
git add tools/verify_newmap.py tools/verify_newmap_files.py dw_ws_sliver.c docs/GAME.md docs/INTERNALS.md
git commit -m "New maps: MMM.DAT files and DG rows 223+ proven in game"
```

## Carried to plan 2 (from execution, 2026-11-12)

Plan 1 finished at c44ef2e. These items were deferred, and plan 2 owns them:
- The factory generator becomes the single owner of dw_newmaps_files.inc and the tail allocator. It retires or relocates the proof package, and it separates proof and factory resource ranges. Add a check that the .inc matches the package.
- Make the sliver fallback gated, with the same lookup order as the hook. Check the DG script size and that the new rows are consecutive.
- Stop gen_newmap_proof hard-coding the glade's MAPHEAD end (0x6084). Break the 21:9 sliver check once on purpose to see it fail.
- check_dw15_compat no longer looks at entry-hooked functions (HOOK_OWNER[0x80112D34] is unused), and 0x800A31F8 is owned by the proof until a map ships. Hand it to the factory.
- In verify_newmap_files, PROOF_BODY duplicates PROOF_STATE. The StopIteration control flow and the empty-state launch after a failed first phase also need tidying.
- 1.5: DG rows cannot point backwards, new rows start at 227, there is no room right after 1.5's DG, MMM.DAT is unchanged on 1.5, and each edition has its own tail.
