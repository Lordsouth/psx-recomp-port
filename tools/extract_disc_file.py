#!/usr/bin/env python3
"""Pull one file out of a Digimon World disc image by its ISO directory entry.

Written for verify_roster_evolution.py, which needs Machinedramon's model as a
byte probe and must take it from the SAME disc the build was made from. A copy
of MUGE.MMD lying around from a modified disc measured 79774 bytes against the
vanilla 78588 and shared no prefix, so it would have failed the probe for a
reason that has nothing to do with what was being tested.

    py -3.12 tools/extract_disc_file.py --bin "Digimon World (USA).bin" \
        --path /CHDAT/MMD3/MUGE.MMD --out MUGE.MMD

    py -3.12 tools/extract_disc_file.py --bin ... --list      # names + sizes

The image is MODE2/2352: 2352 bytes per sector, 24 bytes of header, 2048 bytes
of user data. Reads only; the image is never written.
"""
import argparse
import os
import struct
import sys

SECTOR, USER_OFF, USER_SIZE = 2352, 24, 2048


def sector(f, n):
    f.seek(n * SECTOR + USER_OFF)
    return f.read(USER_SIZE)


def read_extent(f, lba, size):
    n = (size + USER_SIZE - 1) // USER_SIZE
    buf = bytearray()
    for i in range(n):
        buf += sector(f, lba + i)
    return bytes(buf[:size])


def parse_record(b):
    if not b or b[0] == 0:
        return None
    name_len = b[32]
    return {"len": b[0], "lba": struct.unpack_from("<I", b, 2)[0],
            "size": struct.unpack_from("<I", b, 10)[0], "flags": b[25],
            "name": b[33:33 + name_len]}


def walk(f, lba, size, path, out):
    data = read_extent(f, lba, size)
    i = 0
    while i < len(data):
        rec = parse_record(data[i:])
        if rec is None:
            i = (i // USER_SIZE + 1) * USER_SIZE      # records never span sectors
            if i >= len(data):
                break
            continue
        i += rec["len"]
        if rec["name"] in (b"\x00", b"\x01"):
            continue
        name = rec["name"].split(b";")[0].decode("ascii", "replace")
        full = path + "/" + name
        if rec["flags"] & 2:
            walk(f, rec["lba"], rec["size"], full, out)
        else:
            out.append((full, rec["lba"], rec["size"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", required=True, help="path to the .bin disc image")
    ap.add_argument("--path", help="e.g. /CHDAT/MMD3/MUGE.MMD (case-insensitive)")
    ap.add_argument("--out", help="where to write the extracted file")
    ap.add_argument("--list", action="store_true", help="list every file")
    a = ap.parse_args()

    with open(a.bin, "rb") as f:
        pvd = sector(f, 16)
        if pvd[1:6] != b"CD001":
            print("not an ISO9660 image: PVD magic is %r" % pvd[1:6], file=sys.stderr)
            return 2
        root = parse_record(pvd[156:156 + 34])
        files = []
        walk(f, root["lba"], root["size"], "", files)
        if a.list:
            for name, lba, size in sorted(files):
                print("%-40s lba=%-8d size=%d" % (name, lba, size))
            return 0
        if not a.path:
            print("give --path or --list", file=sys.stderr)
            return 2
        want = a.path.upper().replace("\\", "/")
        hit = [x for x in files if x[0].upper() == want]
        if not hit:
            print("not on this disc: %s" % a.path, file=sys.stderr)
            return 3
        name, lba, size = hit[0]
        blob = read_extent(f, lba, size)

    out = a.out or os.path.basename(name)
    open(out, "wb").write(blob)
    print("%s  lba=%d size=%d -> %s" % (name, lba, size, out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
