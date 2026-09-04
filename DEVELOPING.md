# Developing

Building this from source, and producing the setup package players install.
If you only want to play, see [README.md](README.md).
For how the enhancements actually work — every mod, cheat and patch, with
the addresses and the reasoning — see [docs/INTERNALS.md](docs/INTERNALS.md).

## 1. Get the framework

This repo does not vendor the framework. Fetch it first:

```
bash tools/bootstrap.sh
```

That clones two things next to this repo:

* [ethan4love/psxrecomp](https://gitlab.com/ethan4love/psxrecomp),
  branch `fix/bss-memfill-overlap`
* [mstan/recomp-ui](https://github.com/mstan/recomp-ui)

The psxrecomp checkout is a **fork, and it has to be**. Upstream cannot build
this game at all: it rejects the executable outright, because the header's BSS
memfill overlaps the sector-rounded code tail. The fork carries that parser fix
plus ten others found while getting this port working — the SDL joystick-hint
ordering that keeps controllers alive through the launcher, a Windows `os.utime`
crash that killed every rebuild, a `Path.write_text` call that broke setup on
Python 3.8/3.9, the dllexport-on-redeclaration fix without which no overlay
compiles under clang, a frame-pacing guard for monitors where vsync does not
actually throttle, the GameShark cheat engine, and the widescreen plugin. The fork also pins the downloaded build toolchain to a fixed release tag and SHA-256 and no longer edits the user's PATH.

## 2. Build

```
cmake -S psxrecomp/recompiler -B psxrecomp/recompiler/build -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build psxrecomp/recompiler/build
py -3.12 psxrecomp/psxrecomp_cli.py generate --config game.toml --project-root . --disc "<path to your .cue>"
cmake -S . -B build-release -G Ninja -DCMAKE_BUILD_TYPE=Release -DPSX_RECOMP_UI=ON -DRECOMP_UI_ENABLE_MODS=ON
cmake --build build-release --target psx-runtime
```

`generate` reads your disc and emits ~1.8M lines of C (47 shards); the build
then compiles them. Expect 10–30 minutes the first time.

Python 3.10 or newer. Some tools want `tomllib` (3.11+); without it they fall
back to `pip install tomli`.

## 3. Producing the setup package

This is what players install: a host exe **without** the generated game C, so it
carries no game code, plus the framework and tools. The player's own disc
produces the game on their machine.

```
cmake -S . -B build-setup -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DPSX_RECOMP_UI=ON -DRECOMP_UI_ENABLE_MODS=ON \
  -DPSXRECOMP_FORCE_SETUP_HOST=ON -DPSX_SETUP_WIZARD=ON
cmake --build build-setup --target psx-runtime

psxrecomp/tools/package_setup_host.sh \
  --build-dir build-setup --artifact win-x64 \
  --zip-prefix digimon-world \
  --exe-name Digimon_World_Recompiled \
  --display-name "Digimon World Recompiled" \
  --recompiler-build psxrecomp/recompiler/build \
  --runtime-bin /c/msys64/mingw64/bin \
  --project-file CMakeLists.txt --project-file game.toml --project-file VERSION \
  --project-file codegen_setup.c --project-file codegen_setup.h \
  --project-file dw_ws_backdrop.c --project-file dw_ws_camera.c \
  --project-file dw_ws_sliver.c --project-file dw_roster_flags.c \
  --project-file dw_roster.c --project-file dw_roster_data.inc \
  --project-file dw_evolution.c \
  --project-file dw_cheats.c --project-file dw_cheats_data.inc \
  --project-file README.md --project-file DISC.md --project-file cheats.txt \
  --project-file DEVELOPING.md \
  --project-file CHANGELOG.md \
  --project-dir seeds --project-dir mods --project-dir tools --project-dir docs
```

`PSXRECOMP_FORCE_SETUP_HOST` is what keeps game code out: the host links without
the generated C (~17 MB, against ~29 MB for the full product).

The packager uses the `zip` binary when there is one and falls back to Python
otherwise, so stock Git Bash without the `zip` package is fine. It says which it
used. That fallback exists because a hand-built archive once shipped without
directory records and a player could not extract it.

### Check the staged tree before archiving

`--project-dir mods` sweeps in **everything** under `mods/packages`, including
generated manifests that embed another project's bytes and must never be
redistributed. The framework working tree can also carry stray `.mcd` memory
cards holding real saves. Both were present the first time this was packaged.

Before archiving, confirm the staged tree has no game executable, no disc image,
no `generated/*.c` and no BIOS dump. The only `.bin` should be
`psxrecomp/bios/openbios.bin`, which is open source.

## Notes on the configuration

`text_size` is forced to `0xA4000` in `game.toml`. The header's own value runs
past the start of the BSS memfill, and bounding the sweep below it keeps the
analysis clean.

Widescreen ships in **native-wide** (`native_wide = true`), not squash. Squash
compresses a wider field of view into the 320-wide framebuffer and presents it
stretched, which is what made menus and the HUD look wrong through 0.3.1.
Native-wide renders into a genuinely wider framebuffer (320 -> 426 at 16:9) and
presents 1:1, so nothing is stretched.

The defects that had to be fixed live in `dw_ws_backdrop.c` (fade plate, parked
HUD, the clock and the field gauges as single widgets, the battle bars' slide
table), `dw_ws_camera.c` (both camera clamps, the tile-ladder shift, the
load-time nudge) and `dw_ws_sliver.c` (the fifth backdrop column, uploaded to a
VRAM sliver when the four-column ring falls short) as hooks on the game's own
functions; none of them is a code patch. Do NOT enable `nw_hud_corners` -- it
was tested here and shifts world polygons.

They are measured and recorded in `research/widescreen-survey/README.md`. **That
tree sits outside this repository and is not packaged**, and every `research/...`
path in these docs is the same: cited so the reasoning can be traced by someone
with the working tree, not because the file ships to anyone.

**Only 16:9 and 16:10 are offered**, via a game-owned copy of the framework's
widescreen manifest under `mods/packages/psx.enhancement.widescreen`. 21:9 asks
for 120 px of reveal per side and the tile ring tops out near 96 even with the
sliver, so it degraded rather than widened. If the framework's builtin manifest
version is ever bumped past 1.0.0 that override stops applying silently, since
the loader takes the highest semver -- check the staged manifest, not the source
one.

**Two traps when adding a hook here**, both of which have cost a release:

* A `mod_function_entry_funcs` address must be a function START as the
  recompiler discovered it. `renderGameClock`'s symbol is 0x800DD2A4 but its
  recompiled body begins at 0x800DD2A8, because a gp-relative load was hoisted
  above the prologue. A hook on the symbol is emitted nowhere and never fires,
  with no warning. Check with
  `grep "func_<addr>(CPUState" generated/*.c` before relying on one.
* **Nothing below 0x80090800 is statically recompiled**, so no overlay address
  can carry a hook at all. Reach overlay behaviour through data it reads (the
  battle bars' slide table) or through a main-executable function it calls.

To re-measure coverage after any change here, use a walking sweep, never
arrival frames:

```bash
py -3.12 tools/ws_sweep.py --port 4390 --maps 1,2,5,12 --steps 30
```

It is aspect-agnostic: it reads the reveal from the running game and the
geometry from the frames, so the same command verifies 16:10.

`geometry_correction` stays off: it is framework-withdrawn and measured at 0.2%
coverage here, so it buys nothing.

`[controller] default_mode = "digital"` matters more than it looks. This game
only reads the digital pad response; in analog mode every button is dead.

Overlays are captured and background-compiled to native DLLs via
`[runtime] overlay_autocompile_cmd`, pinned to `py -3.12` because a bare `py -3`
may resolve to a Python without `tomllib`. Without a compiler on PATH the game
still runs correctly — overlay code just stays in the interpreter.

## Mod manifests

`tools/gen_*.py` regenerate the manifests under `mods/packages/`, reading every
guard byte from **this project's own extracted executable**. Regenerate rather
than hand-editing, and never copy a manifest between builds of different
executables — the tables are not interchangeable.

Two failure modes worth knowing, both of which cost hours here:

* A `[[package]]` block in `mods/state.toml` needs an explicit `enabled = true`.
  It defaults to **false**, and a package without it is silently ignored.
* An invalid manifest is dropped **silently** — the package simply never
  applies, with nothing logged. Unescaped quotes in a description are enough to
  do it. `python -c "import tomllib; tomllib.load(open(path,'rb'))"` catches it.

### Trigger ids: run this whenever a guard bound changes

```bash
py -3.12 tools/scan_trigger_ids.py <DG.SCN> <MAPHEAD.SCN>
```

Trigger flags live in `ScriptState.triggers[100]` and are memcpy'd to the
memory card, so an id a mod writes that a disc script also uses is a
save-persistent bug in both directions. The roster shipped one from 0.3.0 to
0.3.3: widening `setDigimonRaised`'s bound put the new species' flags straight
onto the "Jijimon announced X joined the city" family, so raising a new Digimon
ate a story scene and watching that scene biased the evolution chooser.

The tool decodes the shipped bytecode rather than pattern-matching, and reports
every id the scripts touch against the bands a guard change opens up. Extract
the two script files with
`tools/extract_disc_file.py --path /SCN/DG.SCN`. Never widen a
trigger-writing bound without running it: the id space directly above a vanilla
range is exactly where the game put the next family.

### Run all four gates before every release

```bash
py -3.12 tools/check_mod_collisions.py
```

```bash
py -3.12 tools/verify_disc_patches.py
```

```bash
py -3.12 tools/verify_setup_package.py dist/digimon-world-<version>-win-x64.zip
```

```bash
py -3.12 tools/gen_cheats_mod.py --check
```

The first catches two co-enablable features writing the same byte differently —
a single collision makes the runtime discard the **entire** mod plan, so one
missed overlap costs every mod including widescreen.

The second checks something the runtime will not tell you until it is too late:
`disc_user` expected-bytes are verified lazily, when the containing sector is
first read. A bad offset boots fine, menus fine, and only fails when a player
reaches the content — at which point *every* disc patch in the plan is disabled,
not just the bad one. See `docs/INTERNALS.md` §7.3.

The third answers the question nothing else in this process asked: **can the
package a player downloads actually be compiled?** It resolves every quoted
`#include` in the staged framework against the package's own contents, and with
`--build` it configures and compiles the recompiler from the staged tree.

That check exists because **0.1.6, 0.2.0 and 0.3.0 all shipped unbuildable**.
The packager staged the framework with `--exclude 'generated'`; a bare name
matches at every depth, so besides dropping `psxrecomp/generated/` (recompiled
OpenBIOS C, correctly excluded) it also dropped
`recompiler/lib/rabbitizer/include/generated/` — 21 headers that are
checked-in *source*. Every player's build died on the very first file with
`fatal error: 'generated/InstrId_enum.h' file not found`, and the launcher
showed only "psxrecomp generate failed (exit 1)". Three releases went out
before a player reported it, because the release process looked at the package
as a file list and never as a compiler sees it. The exclusion is now anchored
(`--exclude '/generated'`) and this gate is what keeps it that way; it passes
on 0.1.5 and fails on 0.1.6, 0.2.0 and 0.3.0.

The fourth keeps the cheat mod honest. `dw.vanilla.cheats` turns every cheat
in `cheats.txt` into a launcher toggle, and its manifest is **generated** from
that file by `tools/gen_cheats_mod.py` -- deliberately carrying names and no
codes, so it cannot disagree with the file about an address. What it can do is
go stale: rename or delete a cheat and the option still exists, pointing at a
name nothing matches. `--check` fails on exactly that. Regenerate with the same
tool and no flag, then commit both `mods/packages/dw.vanilla.cheats/` and
`dw_cheats_data.inc`.

The first two are also worth running after regenerating any manifest, not only
at release time — they are seconds each. So is the header half of the third;
only `--build` is slow.

## Licence

The psxrecomp framework is PolyForm Noncommercial. This project ships no game
content — only configuration, tools and mod manifests.
