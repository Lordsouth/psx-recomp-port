#!/usr/bin/env python3
"""Stage overlay_toolchain/ beside a dev build, the way the setup wizard's rebuild
does for players, so overlays compile to native code instead of being interpreted.

  python tools/stage_overlay_toolchain.py build-release
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "psxrecomp"))
import psxrecomp_cli  # noqa: E402


class Progress:
    def log(self, msg):
        print(msg)


if len(sys.argv) != 2:
    sys.exit(__doc__)
sys.exit(0 if psxrecomp_cli.stage_overlay_toolchain_for_product(
    ROOT, (ROOT / sys.argv[1]).resolve(), Progress()) else 1)
