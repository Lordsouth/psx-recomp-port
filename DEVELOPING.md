# Developing

Building this from source, and producing the setup package players install.
If you only want to play, see [README.md](README.md).

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
  --project-file codegen_setup.c --project-file codegen_setup.h \n  --project-file dw_ws_backdrop.c \
  --project-file README.md --project-file DISC.md --project-file cheats.txt \
  --project-dir seeds --project-dir mods --project-dir tools
```

`PSXRECOMP_FORCE_SETUP_HOST` is what keeps game code out: the host links without
the generated C (~17 MB, against ~29 MB for the full product).

The packager needs the `zip` binary. Without it, staging still completes and you
can archive `dist/stage-setup-<artifact>/` yourself.

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

Widescreen runs in squash mode (`native_wide = false`). Native-wide rendering on
this title leaves stale pixels down the side, a void at area boundaries and torn
menus, all of which squash avoids. `geometry_correction` stays off: it is
framework-withdrawn and measured at 0.2% coverage here, so it buys nothing.

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

## Licence

The psxrecomp framework is PolyForm Noncommercial. This project ships no game
content — only configuration, tools and mod manifests.
