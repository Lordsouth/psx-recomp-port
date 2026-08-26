#!/usr/bin/env python3
"""Build a play-free overlay_captures.json for Digimon World.

psxrecomp normally discovers overlays by watching the game DMA them into RAM,
so an area only goes native after someone visits it. The framework's generic
AOT extractor (tools/aot_overlay_spike/extract_generic.py) skips this disc
entirely -- it recognises PS-X EXE, header-table, indexed-archive and HED
producers, and Digimon World ships Metrowerks "MWo1" containers, which are none
of those. It reports "0 producers, 0 regions".

So this emits the capture records directly. Two inputs make it possible without
playing a single frame:

  overlay_map.jsonl              -- the 16 overlays with validated load
                                    addresses (tools/gen_overlay_map.py)
  dw_decomp config/symbols_*.txt -- ~2800 real function entry addresses

Function entries matter: a capture is just bytes at an address, and the
compiler needs to know where functions begin. Prologue scanning alone misses
frameless functions; the decomp's symbols are ground truth for the ones it has
mapped.

  python tools/gen_aot_captures.py            # write aot_captures.json
  python tools/gen_aot_captures.py --check    # report only
"""
import base64
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
MAP = os.path.join(ROOT, "overlay_map.jsonl")
OVL = os.path.join(ROOT, "overlays")
DECOMP = os.path.normpath(os.path.join(ROOT, "..", "dw_decomp", "config"))
OUT = os.path.join(ROOT, "aot_captures.json")

SCHEMA = "psxrecomp overlay capture v2"

# BTL/STD/VS share one RAM slot and one dw_decomp symbol file each; the rest map
# by lowercasing the file stem. SHOP has no dw_decomp coverage yet.
def symbols_file(overlay_name):
    stem = overlay_name.replace("_REL.BIN", "").lower()
    path = os.path.join(DECOMP, "symbols_%s.txt" % stem)
    return path if os.path.isfile(path) else None


SYM_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(0x[0-9A-Fa-f]+)\s*;")


def load_symbols(path, lo, hi):
    """Function entry addresses inside [lo, hi). Data symbols are excluded:
    dw_decomp marks them <PREFIX>_D_<addr>, and feeding a data address to the
    compiler as a function entry would translate constants as instructions."""
    funcs = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = SYM_RE.match(line.strip())
            if not m:
                continue
            name, addr = m.group(1), int(m.group(2), 16)
            if "_D_" in name:
                continue
            if lo <= addr < hi:
                funcs.append(addr)
    return sorted(set(funcs))


def main():
    check_only = "--check" in sys.argv
    if not os.path.isfile(MAP):
        print("missing %s -- run tools/gen_overlay_map.py first" % MAP)
        return 1

    entries = []
    with open(MAP, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))

    records, total_funcs, missing = [], 0, []
    print("%-14s %-10s %9s %8s  %s" % ("overlay", "load", "size", "funcs", "symbols"))
    for e in entries:
        name = e["name"]
        crc = e["crc32"].upper()
        load = int(e["load_addr"], 16)
        blob = os.path.join(OVL, "%s.bin" % crc)
        if not os.path.isfile(blob):
            missing.append("%s: extracted blob %s.bin not found" % (name, crc))
            continue
        with open(blob, "rb") as fh:
            data = fh.read()
        if len(data) != e["size"]:
            missing.append("%s: blob is %d bytes, map says %d"
                           % (name, len(data), e["size"]))
            continue

        sp = symbols_file(name)
        funcs = load_symbols(sp, load, load + len(data)) if sp else []
        total_funcs += len(funcs)
        print("%-14s 0x%08X %9d %8d  %s"
              % (name, load, len(data), len(funcs),
                 os.path.basename(sp) if sp else "(none in dw_decomp)"))

        hexed = ["0x%08X" % a for a in funcs]
        records.append({
            "schema": SCHEMA,
            "load_addr": "0x%08X" % load,
            "size": len(data),
            "bytes_b64": base64.b64encode(data).decode("ascii"),
            "executed_pcs": [],
            "dispatch_entry_pcs": hexed,
            "function_entry_pcs": hexed,
            "seeds": hexed,
        })

    if missing:
        print("\nPROBLEMS:")
        for m in missing:
            print("  " + m)
        return 1

    print("\n%d overlays, %d function entries from dw_decomp"
          % (len(records), total_funcs))
    if check_only:
        print("--check: nothing written")
        return 0
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(records, f)
    print("wrote %s (%.1f MB)" % (OUT, os.path.getsize(OUT) / 1e6))
    return 0


if __name__ == "__main__":
    sys.exit(main())
