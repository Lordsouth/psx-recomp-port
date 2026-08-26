#!/usr/bin/env python3
"""Generate a "Vice" .psxmod: everything the Vice hack changes, as one toggle.

WHAT THIS PRODUCES, precisely. It diffs the vanilla game against a Vice disc
and emits every difference as guarded patches. The result turns the vanilla
build into the Vice build and back again from the Mods panel.

READ THIS BEFORE USING IT. The Vice image this is generated from is RANDOMIZED,
and the randomizer bakes its seed into the same bytes Vice edits. There is no
way to separate them from a randomized image alone. So the mod carries Vice's
changes AND that one seed: it reproduces exactly that playthrough, and is not a
generic Vice mod that re-randomises per player. Generating from a
non-randomized Vice build would give the clean separation; this is the honest
description of what you get without one.

SCOPE, measured rather than assumed:

  main executable   3823 runs / 17909 bytes   (2.65% of SLUS_010.32)
  11 overlay files  1986 runs / 10253 bytes

Both images are byte-for-byte the same size, so every patch is an equal-length
guarded write and switching the feature off restores the original exactly.

THE COST, also measured. This is a static recompilation: functions whose
instruction bytes no longer match the image the recompiler built from cannot be
dispatched to their native translation and fall back to the interpreter. The
executable diff touches 117 of 2568 recompiled functions -- 4.6%. That is the
price of this mod and it is not zero. Data-only differences are free; they are
read from RAM at runtime and never affect dispatch.

Overlays are patched through `disc_user`, whose offsets are
`lba * 2048 + byte_in_sector`, so the LBA of every overlay is read out of the
ISO directory here rather than hardcoded.

REDISTRIBUTION. The generated manifest embeds Vice's bytes. It is written to
mods/packages/ which is gitignored for exactly this reason -- keep the
generator, never commit its output.

  py -3.12 tools/gen_vice_mod.py            # write the manifest
  py -3.12 tools/gen_vice_mod.py --check    # report scope only
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
VANILLA_DIR = os.path.normpath(os.path.join(ROOT, "..", "game-vanilla", "extracted"))
VICE_DIR = os.path.normpath(os.path.join(ROOT, "..", "game", "extracted"))
VANILLA_BIN = (r"C:\path\to\your\disc"
               r"\Windows\x86_64\Digimon World (USA).bin")

PKG_ID = "dw.vanilla.vice"
PKG_VER = "1.0.0"
GAME_ID = "SLUS-01032"

EXE = "SLUS_010.32"
LOAD_ADDR = 0x80090800
HDR = 0x800
SECTOR_RAW = 2352
USER_DATA = 2048

# Runs closer together than this are merged into one patch. Fewer, longer
# patches mean stronger guards (more context proven before writing) and a
# smaller manifest, at the cost of rewriting a few identical bytes.
MERGE_GAP = 8


def read_sector(f, lba):
    f.seek(lba * SECTOR_RAW)
    raw = f.read(SECTOR_RAW)
    return raw[24:24 + USER_DATA] if len(raw) == SECTOR_RAW else b""


def iso_files(path):
    """name -> (lba, size), from the ISO9660 root directory."""
    out = {}
    with open(path, "rb") as f:
        pvd = read_sector(f, 16)
        root_lba, root_size = struct.unpack_from("<II", pvd, 158)[0], \
            struct.unpack_from("<I", pvd, 166)[0]
        root_lba = struct.unpack_from("<I", pvd, 158)[0]
        nsec = (root_size + USER_DATA - 1) // USER_DATA
        data = b"".join(read_sector(f, root_lba + i) for i in range(nsec))
        off = 0
        while off < len(data):
            rec = data[off]
            if rec == 0:
                off = (off // USER_DATA + 1) * USER_DATA
                if off >= len(data):
                    break
                continue
            extent = struct.unpack_from("<I", data, off + 2)[0]
            size = struct.unpack_from("<I", data, off + 10)[0]
            nlen = data[off + 32]
            name = data[off + 33:off + 33 + nlen].decode("ascii", "replace")
            name = name.split(";")[0]
            if name and name not in (".", ".."):
                out[name] = (extent, size)
            off += rec
    return out


def diff_runs(a, b, merge_gap=MERGE_GAP):
    """[(offset, length)] where a and b differ, near runs merged."""
    n = min(len(a), len(b))
    runs = []
    i = 0
    while i < n:
        if a[i] == b[i]:
            i += 1
            continue
        start = i
        gap = 0
        while i < n and gap <= merge_gap:
            if a[i] != b[i]:
                gap = 0
                end = i
            else:
                gap += 1
            i += 1
        runs.append((start, end - start + 1))
    return runs


def chunk(runs, limit, boundary=0, base=0):
    """Split runs so no patch exceeds `limit` bytes, and -- when `boundary` is
    set -- so none straddles a boundary.

    The runtime refuses a disc patch that crosses a sector ("disc patch may not
    cross a sector boundary"), and one bad patch throws out the whole manifest,
    silently: the package simply never applies. So the splitting happens here.
    """
    out = []
    for off, n in runs:
        pos = 0
        while pos < n:
            take = min(limit, n - pos)
            if boundary:
                abs_off = base + off + pos
                room = boundary - (abs_off % boundary)
                take = min(take, room)
            out.append((off + pos, take))
            pos += take
    return out


def hexs(bs):
    return " ".join("%02x" % x for x in bs)


def main():
    check_only = "--check" in sys.argv

    for d, label in ((VANILLA_DIR, "vanilla"), (VICE_DIR, "vice")):
        if not os.path.isfile(os.path.join(d, EXE)):
            print("missing %s executable: %s" % (label, os.path.join(d, EXE)))
            return 1

    van_exe = open(os.path.join(VANILLA_DIR, EXE), "rb").read()
    vice_exe = open(os.path.join(VICE_DIR, EXE), "rb").read()
    if len(van_exe) != len(vice_exe):
        print("executables differ in size (%d vs %d) -- equal-length guarded "
              "patching does not apply" % (len(van_exe), len(vice_exe)))
        return 1

    exe_runs = chunk(diff_runs(van_exe, vice_exe), 256)
    exe_bytes = sum(n for _o, n in exe_runs)
    print("%-16s %6d patches  %7d bytes" % (EXE, len(exe_runs), exe_bytes))

    # Overlays: every shared file that differs, addressed by its LBA on the
    # VANILLA disc (that is the image this mod is applied to).
    catalog = iso_files(VANILLA_BIN)
    ov = []
    ov_patches = ov_bytes = 0
    for name in sorted(set(os.listdir(VANILLA_DIR)) & set(os.listdir(VICE_DIR))):
        if name == EXE:
            continue
        a = open(os.path.join(VANILLA_DIR, name), "rb").read()
        b = open(os.path.join(VICE_DIR, name), "rb").read()
        if a == b:
            continue
        if len(a) != len(b):
            print("  SKIP %s: size differs (%d vs %d)" % (name, len(a), len(b)))
            continue
        if name not in catalog:
            print("  SKIP %s: not in the vanilla ISO directory" % name)
            continue
        lba, size = catalog[name]
        runs = chunk(diff_runs(a, b), USER_DATA,
                     boundary=USER_DATA, base=lba * USER_DATA)
        ov.append((name, lba, runs, a, b))
        ov_patches += len(runs)
        ov_bytes += sum(n for _o, n in runs)
        print("%-16s %6d patches  %7d bytes   lba %d" % (name, len(runs),
                                                         sum(n for _o, n in runs), lba))

    print("\ntotal: %d patches, %d bytes" % (len(exe_runs) + ov_patches,
                                             exe_bytes + ov_bytes))
    if check_only:
        print("--check: nothing written")
        return 0

    out = [
        "# Digimon World - Vice. Generated by tools/gen_vice_mod.py.",
        "# DO NOT COMMIT: this manifest embeds bytes from the Vice hack.",
        "#",
        "# Generated from a RANDOMIZED Vice image, so it carries Vice's changes",
        "# and that one randomizer seed together -- it reproduces exactly that",
        "# playthrough rather than re-randomising per player.",
        "",
        "format_version = 5",
        'id = "%s"' % PKG_ID,
        'version = "%s"' % PKG_VER,
        'name = "Vice"',
        'author = "DigimonWorldVanilla"',
        'description = "Applies everything the Vice hack changes -- the '
        'executable and 11 overlay files -- as one toggle, then restores the '
        'original bytes exactly when switched off. Generated from a randomized '
        'image, so one fixed seed comes with it."',
        'resolver = "declarative"',
        # Vice rebalances the raising sim, so its saves are not interchangeable
        # with vanilla ones. Keep them in separate save namespaces.
        'save_compatibility = "isolated"',
        "",
        "[[target]]",
        'game_id = "%s"' % GAME_ID,
        "",
        "[[feature]]",
        'id = "vice"',
        'name = "Vice 2.2 (with this randomizer seed)"',
        'description = "Everything the Vice hack changes. NOTE: %d of 2568 '
        'recompiled functions no longer match the image this port was built '
        'from and fall back to the interpreter while this is on."' % 117,
        'group = "Total conversion"',
        "default_enabled = false",
        "",
    ]

    for off, n in exe_runs:
        out += [
            "[[patch]]",
            'feature = "vice"',
            'target = "main_exe"',
            "address = 0x%08X" % (LOAD_ADDR + off - HDR),
            'expected = "%s"' % hexs(van_exe[off:off + n]),
            'replace = "%s"' % hexs(vice_exe[off:off + n]),
            "",
        ]

    for name, lba, runs, a, b in ov:
        out.append("# %s -- disc_user offsets are lba * 2048 + byte_in_sector"
                   % name)
        for off, n in runs:
            out += [
                "[[patch]]",
                'feature = "vice"',
                'target = "disc_user"',
                "offset = %d" % (lba * USER_DATA + off),
                'expected = "%s"' % hexs(a[off:off + n]),
                'replace = "%s"' % hexs(b[off:off + n]),
                "",
            ]

    dest_dir = os.path.join(ROOT, "mods", "packages", PKG_ID, PKG_VER)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, "manifest.toml")
    with open(dest, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out) + "\n")
    print("wrote %s (%.1f KB)" % (dest, os.path.getsize(dest) / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
