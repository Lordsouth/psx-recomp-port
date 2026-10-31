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

A package that ships a [[disc_delta]] (dw.1_5) swaps the disc beneath its own
features: the delta's feature, and every feature reaching it through
in-package requires_feature constraints (the runtime's requires_delta), are
guarded against the patched disc -- their writes may sit past the stock end.
Those are checked against the patched image (hash-checked against the
manifest's target_sha256), every other patch against the stock disc.

  py -3.12 tools/verify_disc_patches.py                  # auto-locate discs
  py -3.12 tools/verify_disc_patches.py path/to/game.bin [path/to/patched.bin]
"""
import hashlib
import os
import re
import sys
import tomllib

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
# The dw.1_5 delta's target (Flips / Rom Patcher JS on the stock disc).
PATCHED = os.path.normpath(os.path.join(
    ROOT, "..", "dw15-release", "patched", "digimon_world_1_5_test1.bin"))


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


def delta_features(path):
    """-> (target_sha256, {feature ids guarded after the delta}) or (None, set())."""
    with open(path, "rb") as f:
        m = tomllib.load(f)
    if not m.get("disc_delta"):
        return None, set()
    delta = m["disc_delta"][0]
    req = {}
    for c in m.get("constraint", []):
        if c.get("kind") == "requires_feature":
            req.setdefault(c["feature"], []).append(c["requires_feature"])

    def reaches(f):
        seen, todo = set(), [f]
        while todo:
            f = todo.pop()
            if f == delta["feature"]:
                return True
            if f not in seen:
                seen.add(f)
                todo += req.get(f, [])
        return False
    return delta["target_sha256"], {f["id"] for f in m.get("feature", [])
                                    if reaches(f["id"])}


def open_image(path):
    """-> (file, raw) or None when the size is neither sector layout."""
    size = os.path.getsize(path)
    if size % 2352 and size % 2048:
        return None
    return open(path, "rb"), size % 2352 == 0


def read_user(img, off, n):
    f, raw = img
    f.seek((off // 2048) * 2352 + 24 + (off % 2048) if raw else off)
    return f.read(n)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


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
    stock = open_image(disc)
    if not stock:
        print("disc image is neither 2352- nor 2048-byte sectored: %s" % disc)
        return 2
    print("disc: %s\n  %d bytes, %s sectors"
          % (disc, os.path.getsize(disc),
             "2352-byte raw" if stock[1] else "2048-byte user"))
    patched_path = sys.argv[2] if len(sys.argv) > 2 else PATCHED
    patched = {}          # target_sha256 -> image, or None if unavailable

    def delta_image(want):
        if want not in patched:
            img = None
            if not os.path.exists(patched_path):
                print("  patched disc missing: %s" % patched_path)
            elif sha256(patched_path) != want:
                print("  patched disc does not match target_sha256 %s: %s"
                      % (want, patched_path))
            else:
                img = open_image(patched_path)
                print("patched disc: %s" % patched_path)
            patched[want] = img
        return patched[want]

    total = bad = 0
    with stock[0]:
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
                target, after_delta = delta_features(man)
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
                    img = stock
                    if feature in after_delta:
                        img = delta_image(target)
                        if not img:
                            print("  FAIL %s:%d feature=%s needs the patched "
                                  "disc to verify" % (pkg, line, feature))
                            nbad += 1
                            continue
                    got = read_user(img, off, len(expected))
                    if got != expected:
                        print("  FAIL %s:%d feature=%s offset=%d\n"
                              "       expected %s\n       on %s  %s"
                              % (pkg, line, feature, off, expected.hex(" "),
                                 "disc" if img is stock else "patched disc",
                                 got.hex(" ")))
                        nbad += 1
                bad += nbad
                n15 = sum(1 for p in patches if p[0] in after_delta)
                print("  %s %-28s %3d disc_user patch(es)%s"
                      % ("OK  " if nbad == 0 else "FAIL", pkg, len(patches),
                         " (%d on the patched disc)" % n15 if n15 else ""))

    print("\nchecked %d disc_user patch(es); %d bad" % (total, bad))
    for img in patched.values():
        if img:
            img[0].close()
    if bad:
        print("A mismatch here disables EVERY disc patch at runtime, not just "
              "the bad one.")
        return 1
    print("OK: every disc_user patch's guard will pass.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
