#!/usr/bin/env python3
"""Release gate: can the package a player downloads actually be built?

Why this exists. Releases 0.1.6, 0.2.0 and 0.3.0 all shipped a setup package
that could not build AT ALL. The packager staged the framework with
`--exclude 'generated'`; a bare name matches at every depth, so as well as
dropping psxrecomp/generated/ (recompiled OpenBIOS C, correctly excluded) it
dropped lib/rabbitizer/include/generated/ -- 21 headers that are checked-in
SOURCE. Every player's build died on the first file:

    RabbitizerInstrId.h:16: fatal error: 'generated/InstrId_enum.h' file not found

and the launcher surfaced only "psxrecomp generate failed (exit 1)". Nothing in
the release process looked at the package as a compiler sees it, so three
releases went out before a player reported it.

Checks, cheap first (plus a mod-payload check: under mods/preloaded/packages only
manifests ship, and the few allowed binaries, the Digimon World 1.5 BPS and the
Hidden Glade's and the Abandoned Factory's map files, must match their hashes):

  headers  Every quoted #include in the staged framework's C/C++ resolves to a
           file that is actually in the package. This is the check that would
           have caught the bug, in about a second.

  build    (--build) Configure and compile the recompiler FROM THE STAGED TREE
           in a scratch directory. Slower, and the only thing that proves the
           package builds rather than merely looks complete.

Usage:
    py -3.12 tools/verify_setup_package.py dist/stage-setup-windows-x64
    py -3.12 tools/verify_setup_package.py dist/digimon-world-0.4.0-windows-x64.zip
    py -3.12 tools/verify_setup_package.py <path> --build
"""

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

INCLUDE_RE = re.compile(r'^\s*#\s*include\s*"([^"]+)"', re.M)
SRC_EXT = (".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".hxx", ".inc")

# Directories a compiler is given with -I when building the recompiler. Read
# from the staged CMakeLists where possible; this is the fallback, and also the
# set used to decide whether an unresolved include is real.
DEFAULT_ROOTS = (
    "psxrecomp/recompiler/src",
    "psxrecomp/recompiler/include",
    "psxrecomp/recompiler/lib/rabbitizer/include",
    "psxrecomp/recompiler/lib/rabbitizer/cplusplus/include",
    "psxrecomp/recompiler/lib/fmt/include",
    "psxrecomp/runtime/include",
)

# Files whose absence is individually fatal. Kept short on purpose: the include
# scan is the general check, and a canary list that tries to be exhaustive just
# rots. These are the ones a regression has actually reached.
CANARIES = (
    "psxrecomp/recompiler/lib/rabbitizer/include/generated/InstrId_enum.h",
    "psxrecomp/recompiler/lib/rabbitizer/include/generated/InstrId_Names_array.h",
    "psxrecomp/recompiler/lib/rabbitizer/include/generated/"
    "InstrDescriptor_Descriptors_array.h",
    "psxrecomp/recompiler/lib/rabbitizer/cplusplus/include/generated/"
    "UniqueId_enum_class.hpp",
    "psxrecomp/psxrecomp_cli.py",
    # Local rewind's snap ring (psxrecomp submodule lib/retcomm-rbengine).
    # Without it CMake only warns and builds the game with Rewind silently
    # left out -- how 0.4.0 shipped, while the README promised F8 rewind.
    "psxrecomp/lib/retcomm-rbengine/src/snap/rbe_snap_ring.c",
    "psxrecomp/lib/retcomm-rbengine/include/retcomm_rbengine/snap_ring.h",
)


def load_tree(path, full=False):
    """-> (root_dir, set_of_relative_posix_paths, cleanup_callable)."""
    if os.path.isdir(path):
        rel = set()
        for dirpath, _dirs, files in os.walk(path):
            for fn in files:
                full = os.path.join(dirpath, fn)
                rel.add(os.path.relpath(full, path).replace(os.sep, "/"))
        return path, rel, lambda: None

    if not zipfile.is_zipfile(path):
        sys.exit("not a directory and not a zip: %s" % path)

    tmp = tempfile.mkdtemp(prefix="pkgverify-")
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if not n.endswith("/")]
        # Only the framework's source is needed for the header check; a full
        # extract of ~1800 files is wasted IO on a tight disk. The BUILD check
        # needs everything -- the .cmake helpers, fmt's support modules -- and
        # a sources-only extract fails configure with a misleading 'include
        # could not find requested file', which read as a broken package once.
        z.extractall(tmp, members=names if full else
                     [n for n in names
                      if n.lower().endswith(SRC_EXT)
                      or n.endswith("CMakeLists.txt")])
    return tmp, set(names), lambda: shutil.rmtree(tmp, ignore_errors=True)


def include_roots(root, present):
    roots = [r for r in DEFAULT_ROOTS if any(p.startswith(r + "/")
                                             for p in present)]
    return roots


# Quoted includes that legitimately resolve to nothing in the SOURCE package.
GENERATED_AT_BUILD = ("psxrecomp_baked_codegen_hash.h",)
QUOTED_SYSTEM = ("stdio.h", "stdlib.h", "string.h", "assert.h", "stdint.h",
                 "stdbool.h", "math.h", "time.h")


def is_scanned(rel):
    """Files the recompiler build actually compiles.

    Vendored libraries ship their own test suites, and those pull in gtest,
    gmock and absl headers that are not vendored and never will be -- fmt's
    test/ alone accounts for 49 unresolved includes. Scanning them turns a
    gate that must be trusted into 70 lines of noise around the 21 that
    matter, so skip any test tree inside lib/.
    """
    if not rel.startswith("psxrecomp/recompiler/"):
        return False
    if not rel.lower().endswith(SRC_EXT):
        return False
    parts = rel.split("/")
    if "lib" in parts:
        after = parts[parts.index("lib"):]
        if any(p in ("test", "tests", "support", "benchmark", "doc", "docs")
               for p in after):
            return False
    return True


def check_headers(root, present):
    """Every quoted include in the staged framework resolves inside it."""
    roots = include_roots(root, present)
    # Every suffix a quoted include could name, for the fallback below.
    by_suffix = set()
    for p in present:
        parts = p.split("/")
        for i in range(len(parts)):
            by_suffix.add("/".join(parts[i:]))
    missing = {}
    scanned = 0
    for rel in sorted(present):
        if not is_scanned(rel):
            continue
        full = os.path.join(root, rel.replace("/", os.sep))
        if not os.path.exists(full):
            continue                      # zip mode: only sources extracted
        scanned += 1
        try:
            text = open(full, "r", encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        here = os.path.dirname(rel)
        for inc in INCLUDE_RE.findall(text):
            if inc.startswith(("..", "/")):
                continue                  # relative escapes: not our business
            if inc in GENERATED_AT_BUILD or inc in QUOTED_SYSTEM:
                continue
            cands = [os.path.normpath(os.path.join(here, inc)).replace(os.sep, "/")]
            cands += [r + "/" + inc for r in roots]
            if any(c in present for c in cands):
                continue
            # Fall back to "does this file exist ANYWHERE in the package".
            # The question this gate answers is whether the packager DELETED
            # something, not whether the include path is spelled right -- and
            # -I roots vary per vendored library (rabbitizer alone resolves
            # tables/*.inc against lib/rabbitizer/tables/). Suffix matching
            # gets the deletion answer right without a hand-kept root list
            # that would silently rot.
            if inc in by_suffix:
                continue
            missing.setdefault(inc, []).append(rel)
    return scanned, missing


def check_canaries(present):
    return [c for c in CANARIES if c not in present]


# The only non-manifest files allowed under mods/preloaded/packages, by exact path and
# SHA-256: the Digimon World 1.5 author's own BPS, the SIL OFL texts its Chinese
# glyphs (Ark Pixel, Cubic 11) require alongside them, and the Hidden Glade's MAP
# and TFS, this port's own map and art (tools/gen_newmap.py writes them and
# copies no byte of the game: refresh these hashes when it regenerates them).
# Anything else there is a payload nobody reviewed for redistribution, so it
# fails the gate.
MOD_PAYLOADS = {
    "mods/preloaded/packages/dw.1_5/1.0.0/assets/digimon_world_1_5_test1.bps":
        "be45b36408f473da7200bcf0b120b634521d43ad1fe51e885439db0bc2cd0efe",
    "mods/preloaded/packages/dw.1_5/1.0.0/licenses/LICENSE-OFL-ArkPixel.txt":
        "3ab41567e68e3988ba1ef16dd2644eca95ca5648ea12e7d46e6287fc0bbe5aee",
    "mods/preloaded/packages/dw.1_5/1.0.0/licenses/LICENSE-OFL-Cubic11.txt":
        "2b6e5938e5cffa0b9e183bd05f8c363e174e7ebed1a0556e2855fd1707fa2188",
    "mods/preloaded/packages/dw.newmaps/1.0.0/assets/glade.map":
        "858369ce8d5e3def667db4306889673d0e40a48a1b31a7af1c0b51a302519b74",
    "mods/preloaded/packages/dw.newmaps/1.0.0/assets/glade.dg":
        "19115e350ac326fe4426e67628e178719be00d505364114ab53d2a6dde0f8b0b",
    "mods/preloaded/packages/dw.newmaps/1.0.0/assets/glade.tfs":
        "11a3c9fbf3743f9452f9808266dbe524477924b120ec23f4008bf15288a130d8",
    "mods/preloaded/packages/dw.newmaps.factory/1.0.0/assets/afac1.dg":
        "d44ac74bff958a97676622a75a540fcb7a4038c73f86cba18db129389410f6b6",
    "mods/preloaded/packages/dw.newmaps.factory/1.0.0/assets/afac1.map":
        "80e4e7f4f2ffdad08bcbd8e03a1a5ee9af796ed1e2a567f419a90d20761bd554",
    "mods/preloaded/packages/dw.newmaps.factory/1.0.0/assets/afac1.tfs":
        "c3f31e9e5fb5b9f250d1f6940d16ab55bbec9fbd7463a07b62d5740dd5db1108",
    "mods/preloaded/packages/dw.newmaps.factory/1.0.0/assets/afac2.dg":
        "0974540f274f910dcd4fe4ce48a546b25c89a71fbc29efe348abafbf1ca2db94",
    "mods/preloaded/packages/dw.newmaps.factory/1.0.0/assets/afac2.map":
        "3010baf33aba0fa912ab39f66714ed37cd80191f602a7fb540fd2fc960eec678",
    "mods/preloaded/packages/dw.newmaps.factory/1.0.0/assets/afac2.tfs":
        "c2529de48bc70bd90061dcd7307e33b7db8d156bcdc8f76cd8aacaafc05091e5",
    "mods/preloaded/packages/dw.newmaps.factory/1.0.0/assets/afac3.dg":
        "0d484d61b726f18a591011400ca87e08787eebfc36ba1cc4cb964c4d39a6ed38",
    "mods/preloaded/packages/dw.newmaps.factory/1.0.0/assets/afac3.map":
        "c95e8624cac0eee2efef33219c7dc28d3fc3b314b8051105b97c090e46f9608a",
    "mods/preloaded/packages/dw.newmaps.factory/1.0.0/assets/afac3.tfs":
        "99cf632a77194cfda53c8a8046e6bee36c88a5fbe3cede09532dbf437ca5a4d8",
    "mods/preloaded/packages/dw.newmaps.factory/1.0.0/assets/afac4.dg":
        "a54111a978138819fbf4af793c3c491d18ecd45a8477f0ea3239364249f3d49b",
    "mods/preloaded/packages/dw.newmaps.factory/1.0.0/assets/afac4.map":
        "b342cf7fa735e6f826131f4b2c5e6b4c17b57b26fb21828c0638ec6c049b6b45",
    "mods/preloaded/packages/dw.newmaps.factory/1.0.0/assets/afac4.tfs":
        "d5cb786079d4524e408b97a28d15c90507137681be24c276e06e6452bd93a64e",
}


def check_mod_payloads(package, root, present):
    """-> list of problems with files under mods/preloaded/packages."""
    bad = []
    for rel in sorted(present):
        if not rel.startswith("mods/preloaded/packages/") or rel.endswith("/manifest.toml"):
            continue
        want = MOD_PAYLOADS.get(rel)
        if want is None:
            bad.append("unexpected mod payload  %s" % rel)
            continue
        if os.path.isdir(package):
            with open(os.path.join(root, rel), "rb") as f:
                data = f.read()
        else:
            with zipfile.ZipFile(package) as z:
                data = z.read(rel)
        if hashlib.sha256(data).hexdigest() != want:
            bad.append("mod payload hash mismatch  %s" % rel)
    for rel in MOD_PAYLOADS:
        # mods/preloaded/packages/<id>/<version>/manifest.toml
        manifest = "/".join(rel.split("/")[:5]) + "/manifest.toml"
        if manifest in present and rel not in present:
            bad.append("mod payload missing  %s" % rel)
    return bad


def check_build(root):
    """Configure and build the recompiler from the staged tree."""
    src = os.path.join(root, "psxrecomp", "recompiler")
    if not os.path.isdir(src):
        return 1, "no psxrecomp/recompiler in the package (nothing to build)"
    if not shutil.which("cmake"):
        return 1, "cmake not on PATH; cannot run the build check"
    build = tempfile.mkdtemp(prefix="pkgbuild-")
    gen = ["-G", "Ninja"] if shutil.which("ninja") else []
    try:
        for stage, cmd in (
            # Tests off: a player's setup builds the emitters, never the test
            # suite, and two upstream interp tests do not link on mingw clang.
            ("configure", ["cmake", "-S", src, "-B", build,
                           "-DCMAKE_BUILD_TYPE=Release",
                           "-DBUILD_TESTING=OFF"] + gen),
            ("build", ["cmake", "--build", build]),
        ):
            p = subprocess.run(cmd, capture_output=True, text=True)
            if p.returncode != 0:
                tail = (p.stdout + p.stderr).strip().splitlines()[-25:]
                return p.returncode, "%s failed:\n    %s" % (
                    stage, "\n    ".join(tail))
        return 0, "recompiler configured and built from the staged tree"
    finally:
        shutil.rmtree(build, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("package", help="staged directory or the built .zip")
    ap.add_argument("--build", action="store_true",
                    help="also compile the recompiler from the staged tree")
    args = ap.parse_args()

    root, present, cleanup = load_tree(args.package, full=args.build)
    try:
        print("package: %s (%d files)" % (args.package, len(present)))

        gone = check_canaries(present)
        if gone:
            print("\nFAIL: required files are not in the package")
            for g in gone:
                print("   missing  %s" % g)

        scanned, missing = check_headers(root, present)
        print("\nscanned %d framework source files for quoted includes" % scanned)
        if missing:
            print("FAIL: %d include target(s) do not exist in the package"
                  % len(missing))
            for inc in sorted(missing):
                users = missing[inc]
                print('   #include "%s"  <- %d file(s), e.g. %s'
                      % (inc, len(users), users[0]))
        else:
            print("OK: every quoted include resolves inside the package.")

        payloads = check_mod_payloads(args.package, root, present)
        print("\nchecked mods/preloaded/packages for non-manifest files")
        if payloads:
            print("FAIL: mod packages carry files that must not ship as-is")
            for p in payloads:
                print("   " + p)
        else:
            print("OK: only manifests, plus the allowed Digimon World 1.5 BPS and font licences and the "
                  "new maps' files (Hidden Glade, Abandoned Factory).")

        rc = 1 if (gone or missing or payloads) else 0

        if args.build:
            print("\nbuilding the recompiler from the staged tree ...")
            brc, msg = check_build(root)
            print(("OK: " if brc == 0 else "FAIL: ") + msg)
            rc = rc or brc

        if rc == 0:
            print("\nPASS")
        else:
            print("\nFAILED -- do not publish this package.")
        return rc
    finally:
        cleanup()


if __name__ == "__main__":
    sys.exit(main())
