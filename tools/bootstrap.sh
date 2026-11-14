#!/usr/bin/env bash
# Fetch the two framework checkouts this project builds against.
#
# They are NOT submodules: on the machine this was developed on they are
# junctions into shared working trees, and committing gitlinks would have
# broken that. This script gets a fresh clone to the same layout.
#
#   bash tools/bootstrap.sh
#
# psxrecomp is a fork carrying fixes this project depends on -- the BSS-overlap
# parser fix that lets this game's executable load at all, the SDL joystick hint
# ordering that keeps controllers alive through the launcher, and others.
# Upstream mstan/psxrecomp will NOT build this project as-is.
set -euo pipefail
cd "$(dirname "$0")/.."

PSXRECOMP_URL="https://github.com/Lordsouth/psxrecomp.git"
PSXRECOMP_REF="feat/dw15-mod"
# The exact framework commit this tree is tested against (on PSXRECOMP_REF).
PSXRECOMP_COMMIT="36f4ad56"
# Fork of mstan/recomp-ui: the Mods page shows what a toggle turned off.
RECOMP_UI_URL="https://github.com/Lordsouth/recomp-ui.git"
RECOMP_UI_REF="feat/dw15-mod"

if [[ -e psxrecomp ]]; then
  echo "psxrecomp/ already present -- leaving it alone"
else
  echo "cloning psxrecomp (${PSXRECOMP_REF} at ${PSXRECOMP_COMMIT})"
  git clone --branch "${PSXRECOMP_REF}" "${PSXRECOMP_URL}" psxrecomp
  git -C psxrecomp checkout --detach "${PSXRECOMP_COMMIT}"
fi
# Rewind's snap ring is a psxrecomp submodule. Without it the game still
# builds, with Rewind silently left out (0.4.0 shipped that way). Only fetch
# it into a real checkout that lacks it; never reset one that has it.
if [[ ! -e psxrecomp/lib/retcomm-rbengine/src/snap/rbe_snap_ring.c ]]; then
  if [[ -e psxrecomp/.git ]]; then
    git -C psxrecomp submodule update --init lib/retcomm-rbengine ||
      echo "warning: could not fetch psxrecomp/lib/retcomm-rbengine -- the build will leave Rewind out"
  else
    echo "warning: psxrecomp/ is not a git checkout, so lib/retcomm-rbengine is missing -- the build will leave Rewind out"
  fi
fi

if [[ -e recomp-ui ]]; then
  echo "recomp-ui/ already present -- leaving it alone"
else
  echo "cloning recomp-ui (${RECOMP_UI_REF})"
  git clone --branch "${RECOMP_UI_REF}" "${RECOMP_UI_URL}" recomp-ui
fi

echo
echo "Done. Next:"
echo "  cmake -S psxrecomp/recompiler -B psxrecomp/recompiler/build -G Ninja -DCMAKE_BUILD_TYPE=Release"
echo "  cmake --build psxrecomp/recompiler/build"
echo "  py -3.12 psxrecomp/psxrecomp_cli.py generate --config game.toml --project-root ."
echo "  cmake -S . -B build-release -G Ninja -DCMAKE_BUILD_TYPE=Release -DPSX_RECOMP_UI=ON -DRECOMP_UI_ENABLE_MODS=ON"
echo "  cmake --build build-release --target psx-runtime"
echo
echo "Most people do not want any of this -- grab the setup package from"
echo "Releases instead."
