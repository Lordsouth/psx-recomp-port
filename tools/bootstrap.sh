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

PSXRECOMP_URL="https://gitlab.com/ethan4love/psxrecomp.git"
PSXRECOMP_REF="fix/bss-memfill-overlap"
RECOMP_UI_URL="https://github.com/mstan/recomp-ui.git"

if [[ -e psxrecomp ]]; then
  echo "psxrecomp/ already present -- leaving it alone"
else
  echo "cloning psxrecomp (${PSXRECOMP_REF})"
  git clone --branch "${PSXRECOMP_REF}" "${PSXRECOMP_URL}" psxrecomp
fi

if [[ -e recomp-ui ]]; then
  echo "recomp-ui/ already present -- leaving it alone"
else
  echo "cloning recomp-ui"
  git clone "${RECOMP_UI_URL}" recomp-ui
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
