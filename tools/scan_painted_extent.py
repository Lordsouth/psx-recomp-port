#!/usr/bin/env python3
"""Propose per-map painted extents for the widescreen camera clamp.

Some rooms were pre-rendered only as far as the original 4:3 camera looked,
so their tile art is flat black past that point and the widened view can show
the cut. This stitches every map from its tile file on YOUR disc, finds the
columns at each edge that are dark on at least 90% of rows, and prints the
painted extent [P0, P1) left between them, with the clamp that would keep a
view of margin m inside it:

    lo = P0 + m,  hi = P1 - 320 - m      (centred if hi < lo)

against today's clamp [m, MAP_WIDTH*128 - 320 - m].

It only PROPOSES. Two things it cannot know decide whether a proposal is an
entry (docs/INTERNALS.md 5.0f):

  * whether the black is a hard cut through painted objects or a room drawn
    floating in black on purpose (the mansion, the caves): look at the map,
    --png writes each candidate out;
  * how far the vanilla camera actually gets. An entry may move a clamp end
    by at most m past the vanilla camera's reach, measured by a sweep with
    widescreen off, or the wide view stops showing something vanilla showed.

It reads the disc as shipped, so the two rooms repainted under 5.0b (216,
218) are still listed.

    python tools/scan_painted_extent.py --bin "disc/Digimon World (USA).bin"
    python tools/scan_painted_extent.py --bin ... --margin 32 --png out/

Stdlib only; developers only, players never run it.
"""
import argparse
import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extract_disc_file import parse_record, read_extent, sector, walk  # noqa: E402

MAP_ENTRIES = 0x801292D4  # MapEntry[255], 16 bytes, filename char[10] first
TILE = 128
SCREEN_W = 320
DARK = 20                 # max channel below this is black
DARK_ROWS = 0.9           # a column this dark is part of an edge run


def disc(bin_path):
    f = open(bin_path, "rb")
    root = parse_record(sector(f, 16)[156:156 + 34])
    files = []
    walk(f, root["lba"], root["size"], "", files)
    return f, {name.upper(): (lba, size) for name, lba, size in files}


def map_names(f, files):
    """map id -> file stem, from the executable's MapEntry table."""
    exe = next(read_extent(f, *v) for k, v in files.items() if k.startswith("/SLUS_"))
    base = MAP_ENTRIES - struct.unpack_from("<I", exe, 0x18)[0] + 0x800
    names = {}
    for i in range(255):
        stem = exe[base + 16 * i:base + 16 * i + 10].split(b"\0")[0].decode("ascii")
        if stem:
            names[i] = stem
    return names


def load(f, files, folder, stem):
    """(tiles wide, tiles high, {(tx, ty): 128x128 index bytes}, CLUT as (r,g,b))."""
    mp = read_extent(f, *files["%s/%s.MAP" % (folder, stem)])
    tfs = read_extent(f, *files["%s/%s.TFS" % (folder, stem)])
    p = struct.unpack_from("<i", mp, 0)[0]
    tw, th = struct.unpack_from("<2i", mp, p + 36 * 4)
    ids = struct.unpack_from("<%di" % (tw * th), mp, p + 38 * 4)
    ncl = struct.unpack_from("<i", tfs, 4)[0]
    rgb = [((c & 31) << 3, ((c >> 5) & 31) << 3, ((c >> 10) & 31) << 3)
           for c in struct.unpack_from("<256H", tfs, 8)]
    tiles = {}
    off = 8 + ncl * 512
    for ty in range(th):
        for tx in range(tw):
            if ids[tx + ty * tw] == -1:
                continue
            tiles[tx, ty] = tfs[off + 4:off + 4 + TILE * TILE]
            off += 4 + TILE * TILE
    return tw, th, tiles, rgb


def lit_columns(tw, th, tiles, rgb):
    """Per column, the share of rows that are not black (a missing tile is black)."""
    table = bytes(1 if max(c) >= DARK else 0 for c in rgb)
    lit = [0] * (tw * TILE)
    for (tx, ty), t in tiles.items():
        for x in range(TILE):
            lit[tx * TILE + x] += t[x::TILE].translate(table).count(1)
    return [n / (th * TILE) for n in lit]


def rows_rgb(tw, th, tiles, rgb):
    rows = []
    for y in range(th * TILE):
        line = bytearray()
        for tx in range(tw):
            t = tiles.get((tx, y // TILE))
            line += b"\0\0\0" * TILE if t is None else b"".join(
                bytes(rgb[v]) for v in t[(y % TILE) * TILE:(y % TILE + 1) * TILE])
        rows.append(bytes(line))
    return rows


def edge_run(lit):
    n = 0
    while n < len(lit) and lit[n] <= 1 - DARK_ROWS:
        n += 1
    return n


def propose(width, p0, p1, m):
    """(lo, hi) camera clamp keeping [C - m, C + 320 + m) inside [p0, p1)."""
    lo, hi = p0 + m, p1 - SCREEN_W - m
    if hi < lo:
        lo = hi = (p0 + p1 - SCREEN_W) // 2
    return lo, hi


def current(width, m):
    """dw_clamp_range in dw_ws_camera.c."""
    return propose(width, 0, width, m)


def write_png(path, rows):
    raw = b"".join(b"\0" + r for r in rows)
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    with open(path, "wb") as out:
        out.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", len(rows[0]) // 3, len(rows), 8, 2, 0, 0, 0))
                  + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def selftest():
    assert current(640, 53) == (53, 267)
    assert propose(640, 0, 603, 53) == (53, 230)          # Item Shop's 37 black columns
    assert current(384, 53) == (32, 32)                   # three-tile maps centre
    assert propose(384, 0, 371, 53) == (25, 25)
    assert edge_run([0.0, 0.05, 0.5, 0.0]) == 2
    print("selftest ok")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", help="the game's .bin")
    ap.add_argument("--margin", type=int, default=53, help="reveal per side (53 at 16:9, 32 at 16:10)")
    ap.add_argument("--min-run", type=int, default=6, help="smallest dark edge run to report")
    ap.add_argument("--png", help="write each candidate map here as a PNG")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest()
        return 0
    if not a.bin:
        ap.error("--bin is required")
    f, files = disc(a.bin)
    names = map_names(f, files)
    if a.png:
        os.makedirs(a.png, exist_ok=True)
    print("%3s  %-9s %5s  %4s %4s  %-10s %-10s" % ("id", "map", "tiles", "L", "R", "clamp now", "proposed"))
    found = 0
    for mid, stem in sorted(names.items()):
        folder = "/MAP/MAP%d" % (mid // 15 + 1)
        if "%s/%s.TFS" % (folder, stem) not in files:
            continue
        tw, th, tiles, rgb = load(f, files, folder, stem)
        lit = lit_columns(tw, th, tiles, rgb)
        L, R = edge_run(lit), edge_run(lit[::-1])
        if max(L, R) < a.min_run:
            continue
        found += 1
        width = tw * TILE
        print("%3d  %-9s %2dx%-2d  %4d %4d  %-10s %-10s" % (
            mid, stem, tw, th, L, R, "%d..%d" % current(width, a.margin),
            "%d..%d" % propose(width, L, width - R, a.margin)))
        if a.png:
            write_png(os.path.join(a.png, "%03d_%s.png" % (mid, stem)), rows_rgb(tw, th, tiles, rgb))
    print("%d map(s) with a dark edge run >= %d px; classify each by eye, then measure the vanilla camera reach"
          % (found, a.min_run))
    return 0


if __name__ == "__main__":
    sys.exit(main())
