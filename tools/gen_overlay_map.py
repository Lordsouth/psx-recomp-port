#!/usr/bin/env python3
"""Generate overlay_map.jsonl for psxrecomp's play-free overlay extractor.

tools/extract_overlays.py (framework) needs committed metadata per overlay:
crc32, load address and size. Building it by hand would be guesswork; this
derives every field from the disc's own files and then CHECKS the result
against dw_decomp's independently-derived memory map, so a wrong load address
fails loudly instead of silently producing overlays compiled at the wrong base.

Two container shapes exist on this disc:

  MWo1 - 13 of the 16 overlays. Metrowerks container: magic "MWo1", u32 id,
         u32 load address, u32 section size. The load address is read straight
         out of the header.
  raw  - BTL / STD / VS. No magic; texture and model sections precede the code,
         and the image is mapped flat from a fixed base. dw_decomp's yaml is
         the only source for that base, so it is supplied here.

  python tools/gen_overlay_map.py            # write overlay_map.jsonl
  python tools/gen_overlay_map.py --check    # verify only
"""
import binascii
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.normpath(os.path.join(HERE, "..", "..", "game-vanilla", "extracted"))
OUT = os.path.normpath(os.path.join(HERE, "..", "overlay_map.jsonl"))

MWO1_MAGIC = b"MWo1"

# Bases for the three raw multi-section overlays, from dw_decomp config/*.yaml
# (btl.yaml / std.yaml / vs.yaml all start their first segment here).
RAW_BASE = {
    "BTL_REL.BIN": 0x80052AE0,
    "STD_REL.BIN": 0x80052AE0,
    "VS_REL.BIN":  0x80052AE0,
}

# Expected load addresses from dw_decomp's per-overlay yaml, used purely as a
# cross-check on what we parse out of the MWo1 headers.
DW_DECOMP_VRAM = {
    "MOV_REL.BIN":  0x80010000,
    "KAR_REL.BIN":  0x80053800,
    "EAB_REL.BIN":  0x80060000,
    "ENDI_REL.BIN": 0x80060000,
    "EVL_REL.BIN":  0x80060000,
    "DOO2_REL.BIN": 0x80070000,
    "FISH_REL.BIN": 0x80070000,
    "MURD_REL.BIN": 0x8007C000,
    "DOOA_REL.BIN": 0x80080000,
    "DGET_REL.BIN": 0x80080800,
    "TRN_REL.BIN":  0x80088800,
    "TRN2_REL.BIN": 0x80088800,
}


def parse(path):
    """-> (load_addr, size, crc32, kind) for one overlay file."""
    name = os.path.basename(path)
    with open(path, "rb") as f:
        data = f.read()
    crc = binascii.crc32(data) & 0xFFFFFFFF

    if data[:4] == MWO1_MAGIC:
        load = struct.unpack_from("<I", data, 8)[0]
        return load, len(data), crc, "MWo1"
    if name in RAW_BASE:
        return RAW_BASE[name], len(data), crc, "raw"
    return None, len(data), crc, "unknown"


def main():
    check_only = "--check" in sys.argv
    if not os.path.isdir(SRC):
        print("missing extracted overlays: %s" % SRC)
        return 1

    names = sorted(n for n in os.listdir(SRC) if n.endswith("_REL.BIN"))
    rows, problems = [], []

    for name in names:
        load, size, crc, kind = parse(os.path.join(SRC, name))
        if load is None:
            problems.append("%s: no MWo1 magic and no known raw base" % name)
            continue
        if not (0x80000000 <= load < 0x80200000):
            problems.append("%s: load 0x%08X outside PS1 RAM" % (name, load))
            continue
        expect = DW_DECOMP_VRAM.get(name)
        if expect is not None and expect != load:
            problems.append(
                "%s: header says 0x%08X but dw_decomp says 0x%08X"
                % (name, load, expect))
            continue
        rows.append((name, load, size, crc, kind))

    print("%-14s %-10s %10s  %-8s %s" % ("overlay", "load", "size", "crc32", "kind"))
    for name, load, size, crc, kind in rows:
        tag = "" if name in DW_DECOMP_VRAM else "  (no dw_decomp entry)"
        print("%-14s 0x%08X %10d  %08X %s%s" % (name, load, size, crc, kind, tag))

    if problems:
        print("\nPROBLEMS:")
        for p in problems:
            print("  " + p)
        return 1

    print("\n%d overlays, all load addresses agree with dw_decomp where it has "
          "an entry" % len(rows))
    if check_only:
        print("--check: nothing written")
        return 0

    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        for name, load, size, crc, kind in rows:
            f.write(json.dumps({
                "name": name,
                "crc32": "%08X" % crc,
                "load_addr": "%08X" % load,
                "size": size,
                "start_lba": "unknown",
                "container": kind,
            }) + "\n")
    print("wrote %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
