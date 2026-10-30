#!/usr/bin/env python3
"""Release gate: prove every shipped disc_user patch's expected bytes match
the real disc image.

WHY THIS EXISTS. main_exe patches are guarded when the mod plan is applied, so
a bad one fails loudly at boot. disc_user patches are not: the runtime checks
their expected bytes lazily, the first time the containing sector is actually
read (mod_runtime.cpp, mod_runtime_patch_disc_sector). A wrong offset therefore
survives startup, survives the menus, and only fails when the player reaches
the content -- at which point the runtime prints one stderr line and sets
disc_guard_failed, which turns off *every* disc patch in the plan rather than
just the bad one. That is a silent, late, total failure; this makes it an
early, loud, local one.

The physical mapping mirrors the runtime exactly (mod_runtime.cpp:388):
a 2352-byte/sector image stores each 2048-byte user payload at +24, so
  physical = (offset // 2048) * 2352 + 24 + (offset % 2048)
A 2048-byte/sector image is addressed directly.

  py -3.12 tools/verify_disc_patches.py                  # auto-locate disc
  py -3.12 tools/verify_disc_patches.py path/to/game.bin
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
PKG_DIR = os.path.join(ROOT, "mods", "preloaded", "packages")

# Packages that exist in the tree but are never shipped to players.
NOT_SHIPPED = {"dw.vanilla.vice"}

CANDIDATES = [
    os.path.join(ROOT, "disc", "Digimon World (USA).bin"),
    r"C:\Users\boura\Downloads\DigimonWorldVice_Installer_Windows\Windows"
    r"\x86_64\Digimon World (USA).bin",
]


def find_disc(argv):
    if len(argv) > 1:
        return argv[1]
    cfg = os.path.join(ROOT, "build-debug", "disc.cfg")
    if os.path.exists(cfg):
        p = open(cfg, encoding="utf-8").read().strip()
        if p and os.path.exists(p):
            return p
    for p in CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def parse_patches(path):
    """-> [(feature, offset, expected_bytes, line_no)] for disc_user patches."""
    out = []
    cur = {}
    for n, line in enumerate(open(path, encoding="utf-8"), 1):
        line = line.strip()
        if line == "[[patch]]":
            cur = {"line": n}
            continue
        m = re.match(r'^(\w+)\s*=\s*(.+)$', line)
        if not m:
            continue
        k, v = m.group(1), m.group(2).strip()
        if v.startswith('"') and v.endswith('"'):
            v = v[1:-1]
        cur[k] = v
        if k in ("expected", "replace") and "offset" in cur and \
                cur.get("target") == "disc_user" and "expected" in cur and \
                "replace" in cur:
            out.append((cur.get("feature", "?"), int(cur["offset"]),
                        bytes.fromhex(cur["expected"].replace(" ", "")),
                        cur["line"]))
            cur = {}
    return out


def main():
    disc = find_disc(sys.argv)
    if not disc:
        print("no disc image found; pass one as an argument")
        return 2
    size = os.path.getsize(disc)
    raw = (size % 2352) == 0
    if not raw and (size % 2048) != 0:
        print("disc image is neither 2352- nor 2048-byte sectored: %s" % disc)
        return 2
    print("disc: %s\n  %d bytes, %s sectors"
          % (disc, size, "2352-byte raw" if raw else "2048-byte user"))

    def physical(off):
        if not raw:
            return off
        return (off // 2048) * 2352 + 24 + (off % 2048)

    total = bad = 0
    with open(disc, "rb") as f:
        for pkg in sorted(os.listdir(PKG_DIR)):
            if pkg in NOT_SHIPPED:
                continue
            for ver in sorted(os.listdir(os.path.join(PKG_DIR, pkg))):
                man = os.path.join(PKG_DIR, pkg, ver, "manifest.toml")
                if not os.path.exists(man):
                    continue
                patches = parse_patches(man)
                if not patches:
                    continue
                nbad = 0
                for feature, off, expected, line in patches:
                    total += 1
                    # A patch may not straddle a sector boundary: the runtime
                    # applies it within one sector's buffer.
                    if off // 2048 != (off + len(expected) - 1) // 2048:
                        print("  FAIL %s:%d offset %d crosses a sector "
                              "boundary" % (pkg, line, off))
                        nbad += 1
                        continue
                    f.seek(physical(off))
                    got = f.read(len(expected))
                    if got != expected:
                        print("  FAIL %s:%d feature=%s offset=%d\n"
                              "       expected %s\n       on disc  %s"
                              % (pkg, line, feature, off, expected.hex(" "),
                                 got.hex(" ")))
                        nbad += 1
                bad += nbad
                print("  %s %-28s %3d disc_user patch(es)"
                      % ("OK  " if nbad == 0 else "FAIL", pkg, len(patches)))

    print("\nchecked %d disc_user patch(es); %d bad" % (total, bad))
    if bad:
        print("A mismatch here disables EVERY disc patch at runtime, not just "
              "the bad one.")
        return 1
    print("OK: every disc_user patch's guard will pass.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
