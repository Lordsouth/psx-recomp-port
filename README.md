# Digimon World — Recompiled

A native PC build of **Digimon World** (SLUS-01032, NTSC-U) produced by static
recompilation with [psxrecomp](https://github.com/mstan/psxrecomp): the game's
MIPS code is translated to C ahead of time and compiled into a real executable.
It is not an emulator front-end.

This is the **unmodified** game. The Vice-modded build lives in
`../DigimonWorldRecomp` and is a separate project with its own mods and saves.

## What you need

Your own legal disc image. Nothing copyrighted is stored here — the build
reads the disc at the path in `game.toml` and extracts what it needs locally.

Verified against:

| | |
|---|---|
| executable | `SLUS_010.32`, sha1 `31ec666b987d5b007dec40eecc4092d27ed9085b` |
| disc image | 381,771,936 bytes, sha1 `5611645da66183e30d11fb6c11a2784ff47e1fdb` |

That executable hash matches the one [dw_decomp](https://github.com/jype0/dw_decomp)
targets, which is why its symbol map applies directly to this build.

## Playing

Double-click **`Play Digimon World.bat`**. It sets the working directory so
`game.toml`, `saves\` and the disc path resolve, puts MinGW on PATH so overlay
code can be compiled to native DLLs in the background, and starts the launcher.

**Controller:** leave the launcher's Player 1 toggle on **D-Pad**. On *Analog*
the runtime presents a DualShock, and this game only reads the digital pad
response — every button goes dead. `game.toml` sets `default_mode = "digital"`,
but the launcher's own saved setting overrides it, so check there first if a
pad ever stops responding.

**Keyboard:** Enter = Start, X = Cross, S = Circle, A = Triangle, Z = Square,
arrows = D-Pad. Rebind in the launcher's Controls page or `keybinds.ini`.

**Saving** works exactly as on the original: only by sleeping. Your partner
needs the ZZZ bubble over its head, then open the menu and choose Sleep — the
option is greyed out otherwise.

## Mods

Toggle these in the launcher's **Mods** panel. All are off by default and each
is a guarded byte patch, so switching one off restores the original bytes
exactly.

| package | what it does |
|---|---|
| `dw.vanilla.qol` | Longer Life (lifespan slider), World Clock Speed |
| `dw.vanilla.gameplay` | Tech Mastery (learn odds), Evolution Unbound (stat walls) |
| `dw.vanilla.trainer` | immortality, no pooping, stat-gain sliders, and more |

Every guard byte is read from *this* project's own extracted executable by the
scripts in `tools/`, never copied from the Vice build — the two binaries differ
and their tables are not interchangeable. Regenerate with
`py -3.12 tools/gen_qol_mods.py` and friends rather than hand-editing manifests.

`cheats.txt` holds GameShark-style codes; flip `[ ]` to `[x]` and restart.
Those are per-frame RAM writes, applied on top of whatever the Mods panel does.

## Rebuilding

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
