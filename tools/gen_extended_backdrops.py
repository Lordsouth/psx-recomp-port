#!/usr/bin/env python3
"""Fold repainted map art into the widescreen feature.

Some rooms were pre-rendered only as far as the original 4:3 camera could see,
so a widened view shows flat black where the render stopped (the Item Shop's
right 37 columns are colour index 0 on every row). The repaint lives in
art/backdrops/<MAP>.png: the WHOLE map stitched from its tile file, as an
8-bit indexed PNG whose palette is the map's own CLUT. Painting in the CLUT's
indices is what keeps the art on every renderer and under any palette change
the game makes later.

This tool diffs that image against the tile file on YOUR disc and emits
guarded disc_user writes for the bytes that differ, so the game's own loader
reads the new art and its own VRAM ring uploads it. No plugin is involved.
The writes belong to the widescreen feature (a marked block at the end of the
game's widescreen manifest), so they apply exactly when widescreen is on.

Only paint columns the 4:3 camera never reaches: anything it does reach is the
original look and stays as shipped. Measure the room's 4:3 camera range first.

    python tools/gen_extended_backdrops.py --bin "disc/Digimon World (USA).bin"
    python tools/gen_extended_backdrops.py --bin ... --write

Stdlib only. Without --write it verifies and writes nothing.
"""
import argparse
import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extract_disc_file import parse_record, read_extent, sector, walk  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ART = os.path.join(ROOT, "art", "backdrops")
MANIFEST = os.path.join(ROOT, "mods", "preloaded", "packages", "psx.enhancement.widescreen", "1.0.0", "manifest.toml")
SECTOR_USER = 2048
TILE = 128
TILE_HDR = 4

# map file stem -> (disc directory, player-facing room name)
ROOMS = {
    "ROOM06": ("/MAP/MAP15", "Item Shop"),
    "ROOM08": ("/MAP/MAP15", "Jijimon's house"),
}


def read_png_indexed(path):
    """(width, height, rows of palette indices, palette as (r,g,b) list) from an
    8-bit, non-interlaced, colour-type-3 PNG."""
    data = open(path, "rb").read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise SystemExit("%s: not a PNG" % path)
    pos, idat, plte = 8, b"", []
    while pos < len(data):
        n, kind = struct.unpack_from(">I4s", data, pos)
        body = data[pos + 8:pos + 8 + n]
        if kind == b"IHDR":
            w, h, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", body)
            if (depth, ctype, interlace) != (8, 3, 0):
                raise SystemExit("%s: need 8-bit indexed, non-interlaced" % path)
        elif kind == b"PLTE":
            plte = [tuple(body[i:i + 3]) for i in range(0, len(body), 3)]
        elif kind == b"IDAT":
            idat += body
        pos += 12 + n
    raw = zlib.decompress(idat)
    rows, prev = [], bytearray(w)
    for y in range(h):
        f, line = raw[y * (w + 1)], bytearray(raw[y * (w + 1) + 1:(y + 1) * (w + 1)])
        for x in range(w):
            a = line[x - 1] if x else 0
            b, c = prev[x], prev[x - 1] if x else 0
            if f == 1:
                line[x] = (line[x] + a) & 255
            elif f == 2:
                line[x] = (line[x] + b) & 255
            elif f == 3:
                line[x] = (line[x] + (a + b) // 2) & 255
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append(bytes(line))
        prev = line
    return w, h, rows, plte


def disc_files(path):
    with open(path, "rb") as f:
        root = parse_record(sector(f, 16)[156:156 + 34])
        files = []
        walk(f, root["lba"], root["size"], "", files)
        return {name.upper(): (lba, size) for name, lba, size in files}


def read_file(bin_path, files, name):
    lba, size = files[name.upper()]
    with open(bin_path, "rb") as f:
        return lba, read_extent(f, lba, size)


def room_writes(bin_path, files, stem, folder):
    """Guarded writes for one room, split at sector boundaries."""
    _, mp = read_file(bin_path, files, "%s/%s.MAP" % (folder, stem))
    lba, tfs = read_file(bin_path, files, "%s/%s.TFS" % (folder, stem))
    p = struct.unpack_from("<i", mp, 0)[0]
    tw, th = struct.unpack_from("<2i", mp, p + 36 * 4)
    ids = struct.unpack_from("<%di" % (tw * th), mp, p + 38 * 4)
    ncl = struct.unpack_from("<i", tfs, 4)[0]
    clut = struct.unpack_from("<256H", tfs, 8)
    w, h, rows, plte = read_png_indexed(os.path.join(ART, stem + ".png"))
    if (w, h) != (tw * TILE, th * TILE):
        raise SystemExit("%s.png is %dx%d, the map is %dx%d" % (stem, w, h, tw * TILE, th * TILE))
    for i, (r, g, b) in enumerate(plte[:256]):
        c = clut[i]
        if (r >> 3, g >> 3, b >> 3) != (c & 31, (c >> 5) & 31, (c >> 10) & 31):
            raise SystemExit("%s.png palette entry %d is not the map's CLUT" % (stem, i))
    new = bytearray(tfs)
    off = 8 + ncl * 512
    for ty in range(th):
        for tx in range(tw):
            if ids[tx + ty * tw] == -1:
                continue
            off += TILE_HDR
            for y in range(TILE):
                row = rows[ty * TILE + y][tx * TILE:(tx + 1) * TILE]
                for x, v in enumerate(row):
                    if v != tfs[off + y * TILE + x] and clut[v] == 0:
                        raise SystemExit("%s: (%d,%d) paints a transparent CLUT entry" % (stem, tx * TILE + x, ty * TILE + y))
                new[off + y * TILE:off + (y + 1) * TILE] = row
            off += TILE * TILE
    diff = [i for i in range(len(tfs)) if tfs[i] != new[i]]
    writes = []
    i = 0
    while i < len(diff):
        start = end = diff[i]
        while i + 1 < len(diff) and diff[i + 1] // SECTOR_USER == start // SECTOR_USER:
            i += 1
            end = diff[i]
        writes.append((lba * SECTOR_USER + start, bytes(tfs[start:end + 1]), bytes(new[start:end + 1])))
        i += 1
    return writes, len(diff)


def hexs(b):
    return " ".join("%02x" % v for v in b)


BEGIN = "# >>> extended backdrops: generated by tools/gen_extended_backdrops.py, do not hand-edit"
END = "# <<< extended backdrops"


def block(rooms):
    out = [BEGIN,
           "#",
           "# Rooms pre-rendered only as far as the 4:3 camera could see, repainted so",
           "# the widened view shows the room instead of a black strip. Part of the",
           "# widescreen feature itself, so choosing widescreen is all a player does.",
           "# Source art: art/backdrops/*.png (each map's own CLUT indices)."]
    for room, writes in rooms:
        out += ["", "# %s" % room]
        for at, old, new in writes:
            out += ["", "[[patch]]", 'feature = "widescreen"', 'target = "disc_user"',
                    "offset = %d" % at, 'expected = "%s"' % hexs(old), 'replace = "%s"' % hexs(new)]
    return "\n".join(out + ["", END]) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", required=True)
    ap.add_argument("--write", action="store_true", help="update the widescreen manifest's generated block")
    a = ap.parse_args()
    files = disc_files(a.bin)
    rooms = []
    for stem, (folder, name) in ROOMS.items():
        writes, n = room_writes(a.bin, files, stem, folder)
        print("%s (%s): %d bytes changed, %d writes" % (stem, name, n, len(writes)))
        rooms.append((name, writes))
    if not a.write:
        print("verified; nothing written (pass --write to update %s)" % os.path.relpath(MANIFEST, ROOT))
        return 0
    text = open(MANIFEST, encoding="utf-8").read()
    if BEGIN in text:
        text = text[:text.index(BEGIN)] + text[text.index(END) + len(END) + 1:]
    open(MANIFEST, "w", encoding="utf-8", newline="\n").write(text.rstrip("\n") + "\n\n" + block(rooms))
    print("wrote", os.path.relpath(MANIFEST, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
