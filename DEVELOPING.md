# Developing

Building this from source. **Players do not need any of this** -- see [README.md](README.md) to install the ready-made package.

## Building from source

This repo does not vendor the framework. Get it first:

```
bash tools/bootstrap.sh
```

That clones two things: [ethan4love/psxrecomp](https://gitlab.com/ethan4love/psxrecomp)
(branch `fix/bss-memfill-overlap`) and [mstan/recomp-ui](https://github.com/mstan/recomp-ui).

The psxrecomp checkout is a **fork**, and it has to be. Upstream will not build
this project: it rejects this game's executable outright, because the header's
BSS memfill overlaps the sector-rounded code tail. The fork also carries the SDL
joystick-hint ordering fix that keeps controllers working through the launcher,
a cap on freeze-dump size, the GameShark cheat engine, and the widescreen
activation plugin. Six commits, all on that branch.

Then:

```
cmake -S psxrecomp/recompiler -B psxrecomp/recompiler/build -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build psxrecomp/recompiler/build
py -3.12 psxrecomp/psxrecomp_cli.py generate --config game.toml --project-root .
cmake -S . -B build-release -G Ninja -DCMAKE_BUILD_TYPE=Release -DPSX_RECOMP_UI=ON -DRECOMP_UI_ENABLE_MODS=ON
cmake --build build-release --target psx-runtime
```

## Notes on the configuration

`text_size` is forced to `0xA4000` in `game.toml`. The header's own value runs
past the start of the BSS memfill, and bounding the sweep below it keeps the
analysis clean — the same adjustment the Vice build needs.

Widescreen runs in squash mode (`native_wide = false`). Native-wide rendering
on this title leaves stale pixels down the side, a void at area boundaries and
torn menus, all of which squash avoids. `geometry_correction` stays off: it is
framework-withdrawn and measured at 0.2% coverage here, so it buys nothing.

Overlays are captured and background-compiled to native DLLs via
`[runtime] overlay_autocompile_cmd`, pinned to `py -3.12` because the `py -3`
on this machine is 3.8 and has no `tomllib`. Without gcc on PATH the game still
runs correctly; overlay code just stays in the interpreter.

## Licence

The psxrecomp framework is PolyForm Noncommercial. This project ships no game
content — only configuration, tools and mod manifests.
