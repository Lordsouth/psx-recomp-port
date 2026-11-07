#!/usr/bin/env python3
"""Local-only dev package: a new map on the free MAP_ENTRIES row 239 (M0 spike).

Fills row 239 with the unused Mt. Infinity floor MGEN17 and wires it into the
world, all through guarded writes on the player's own disc:

  * MAP_ENTRIES[239]  stock zeros -> "MGEN17", flags 0xD1, name "Mt. Infinity"
  * MAPHEAD.SCN       section 239 relocated past EOF: the stock filler binds the
                      sewer script (fog cutscene, Numemon flags), the new one
                      binds MGEN11's script and spawns one Piddomon in slot 0
  * MAYO02.MAP        Coela Point's east exit (warp 2) -> map 239, spawn 0
  * MGEN17.MAP        a new entity block past EOF (its warps, warp 0 -> Coela
                      Point exit 2, plus a weakened Piddomon) and a strip of
                      warp-trigger tiles (110) in a dead end

The manifest it writes carries stock bytes copied from the disc at generation
time (MGEN17's warp table, MGEN11's Piddomon record), so it is written into a
build's mods/installed/ and must never be committed or shipped. INTERNALS.md
section 3.1 explains each write; verify_newmap_spike.py checks it in the game.

    py -3.12 tools/gen_newmap_spike.py [--bin disc.bin] [--build build-debug]
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extract_disc_file import parse_record, read_extent, sector, walk  # noqa: E402

ROW = 239
MAP_ENTRIES = 0x801292D4
HOST, HOST_ROW, HOST_WARP = "MAYO02", 1, 2       # Coela Point east exit; stock -> map 4 exit 0
NEW, NEW_SPAWN, RETURN_WARP = "MGEN17", 0, 0     # spawn 0 is tile (64,54); spawn 2 is inside a wall
RETURN_ROWS, RETURN_COLS = range(63, 66), range(50, 58)   # dead end south of spawn 0
FLAGS, NAME = 0xD1, 0x3B                         # bank 0x11 | 0x40 no time of day (1 CLUT) | 0x80 NPCs
SCRIPT, PIDDOMON, ENEMY_SCRIPT = 0xB9, 85, 5     # MGEN11's DG script; its section 5 is this battle
ENEMY_POS = (2050, 0, -250)                      # tile (70,52), spawn room
ENEMY_STATS = (40, 40, 40, 40, 30, 10, 50, 50)   # hp mp curHP curMP off def speed brain
NEW_ENTITY = 10408                               # MGEN17.MAP ends at 10406; its 6th sector is zero after
MH_NEW, MH_BUFFER = 0x6070, 0x61A8               # MAPHEAD.SCN ends at 0x606E; MAPHEAD_SCRIPT_BUFFER size


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", default=os.path.join(root, "disc", "Digimon World (USA).bin"))
    ap.add_argument("--build", default=os.path.join(root, "build-debug"))
    a = ap.parse_args()

    f = open(a.bin, "rb")
    listing = []
    root_rec = parse_record(sector(f, 16)[156:190])
    walk(f, root_rec["lba"], root_rec["size"], "", listing)
    files = {os.path.basename(n): (lba, size) for n, lba, size in listing}
    read = lambda name: read_extent(f, *files[name])
    hexs = lambda b: " ".join("%02x" % x for x in b)
    patches = []

    def disc(name, byte, new, why):
        """Guarded equal-length disc_user writes, split at sector boundaries."""
        lba = files[name][0]
        while new:
            n = min(len(new), 2048 - byte % 2048)
            old = sector(f, lba + byte // 2048)[byte % 2048:byte % 2048 + n]
            if old != new[:n]:
                patches.append((why, 'target = "disc_user"\noffset = %d\nexpected = "%s"\nreplace = "%s"'
                                % (lba * 2048 + byte, hexs(old), hexs(new[:n]))))
            byte, new = byte + n, new[n:]

    def header(data):
        return list(struct.unpack_from("<%di" % (struct.unpack_from("<i", data)[0] // 4), data))

    # 1. The row.
    row = NEW.encode().ljust(10, b"\0") + bytes([0, 0, FLAGS, 0, 0, NAME])
    patches.append(("MAP_ENTRIES[%d] = %s" % (ROW, NEW),
                    'target = "main_exe"\naddress = 0x%08X\nexpected = "%s"\nreplace = "%s"'
                    % (MAP_ENTRIES + ROW * 16, hexs(bytes(16)), hexs(row))))

    # 2. MAPHEAD section 239, relocated into the zero tail of the file's last sector.
    mh = read("MAPHEAD.SCN")
    p = 2
    while struct.unpack_from("<H", mh, p)[0] != ROW:
        p += 4
    stock = struct.unpack_from("<H", mh, p + 2)[0]
    assert mh[stock:stock + 6] == bytes.fromhex("fb00b400ef00"), "MAPHEAD 239 is not the stock filler"
    section = (bytes([0xFB, 0]) + struct.pack("<HH", SCRIPT, ROW)   # SET_SCRIPT script, map
               + bytes.fromhex("1e00f501")                           # SET_PSTAT 245 = 1, as every MGEN row
               + bytes.fromhex("5d1b")                               # SET_BGM 0x1B, the MGEN track
               + bytes([0x46, PIDDOMON])                             # LOAD_DIGIMON
               + bytes([0x47, PIDDOMON, 0, 1])                       # SET_DIGIMON type, slot 0, autotalk
               + bytes.fromhex("fe00"))
    assert len(mh) <= MH_NEW and MH_NEW + len(section) <= MH_BUFFER
    disc("MAPHEAD.SCN", MH_NEW, section, "MAPHEAD section %d body" % ROW)
    disc("MAPHEAD.SCN", p + 2, struct.pack("<H", MH_NEW), "MAPHEAD section %d -> 0x%x" % (ROW, MH_NEW))

    dg = read("DG.SCN")                       # the battle section this relies on must be there
    so, se = struct.unpack_from("<II", dg, 4 * SCRIPT)
    sc, q, sec = dg[so:se], 2, None
    while struct.unpack_from("<H", sc, q)[0] != 0xFFFF:
        s, o = struct.unpack_from("<HH", sc, q)
        sec, q = (o if s == ENEMY_SCRIPT else sec), q + 4
    assert sec and sc[sec:sec + 2] == bytes([0x1B, ENEMY_SCRIPT]) and b"\x66\x00" in sc[sec:sec + 120]

    # 3. Host exit -> (239, spawn 0).
    hd = read(HOST + ".MAP")
    hent = header(hd)[-2]
    assert struct.unpack_from("<h", hd, hent + 100 + HOST_WARP * 2)[0] == NEW_SPAWN
    disc(HOST + ".MAP", hent + 80 + HOST_WARP * 2, struct.pack("<h", ROW),
         "%s warp %d target map -> %d" % (HOST, HOST_WARP, ROW))

    # 4. MGEN17: its warps (warp 0 -> host) + one Piddomon, in a new block past EOF.
    nd = read(NEW + ".MAP")
    nent, ncoll = header(nd)[1:]
    assert header(nd) == [12, nent, ncoll] and struct.unpack_from("<h", nd, nent + 120)[0] == 0
    assert len(nd) <= NEW_ENTITY and files[NEW + ".MAP"][1] <= 6 * 2048
    warps = bytearray(nd[nent:nent + 120])
    struct.pack_into("<h", warps, 80 + RETURN_WARP * 2, HOST_ROW)
    struct.pack_into("<h", warps, 100 + RETURN_WARP * 2, HOST_WARP)
    g11 = read("MGEN11.MAP")
    rec = list(struct.unpack_from("<45h", g11, header(g11)[-2] + 122))   # 42 fixed + 1 waypoint
    assert rec[0] == PIDDOMON and rec[10] == ENEMY_SCRIPT and rec[33] == 1
    rec[2:5] = ENEMY_POS; rec[11:19] = ENEMY_STATS; rec[30:33] = ENEMY_POS; rec[42:45] = ENEMY_POS
    block = bytes(warps) + struct.pack("<h", 1) + struct.pack("<45h", *rec)
    assert NEW_ENTITY + len(block) <= 6 * 2048    # readFile loads whole sectors
    disc(NEW + ".MAP", NEW_ENTITY, block, "%s entity block at %d" % (NEW, NEW_ENTITY))
    disc(NEW + ".MAP", 4, struct.pack("<i", NEW_ENTITY), "%s header entity offset" % NEW)

    # 5. Warp-trigger strip (110 + warp) on floor tiles.
    for tz in RETURN_ROWS:
        off = ncoll + tz * 100 + RETURN_COLS.start
        assert not any(nd[off:off + len(RETURN_COLS)]), "return strip must be floor"
        disc(NEW + ".MAP", off, bytes([110 + RETURN_WARP]) * len(RETURN_COLS),
             "%s row %d trigger %d" % (NEW, tz, 110 + RETURN_WARP))
    ex, ez = ENEMY_POS[0], ENEMY_POS[2]           # tile maths as getTileTrigger (C truncation)
    tile = (int(ex / 100) + 50 - (ex < 0)) + (50 - int(ez / 100) - (ez > 0)) * 100
    assert nd[ncoll + tile] == 0, "Piddomon must stand on floor"
    f.close()

    out = ["# LOCAL ONLY: carries stock bytes. Generated by tools/gen_newmap_spike.py.",
           "format_version = 5", 'id = "dw.newmaps.spike"', 'version = "1.0.0"',
           'name = "New Map Spike (dev)"', 'author = "DigimonWorldRecomp"',
           'description = "Dev test: Coela Point\'s east exit leads to the unused Mt. Infinity floor on free map row 239."',
           'resolver = "declarative"', 'save_compatibility = "shared"', "",
           "[[target]]", 'game_id = "SLUS-01032"', "",
           "[[feature]]", 'id = "spike"', 'name = "Row 239 spike"', 'description = "Dev only."',
           'group = "Dev"', "default_enabled = false", ""]
    for why, body in patches:
        out += ["# " + why, "[[patch]]", 'feature = "spike"', body, ""]
    dst = os.path.join(a.build, "mods", "installed", "dw.newmaps.spike", "1.0.0")
    os.makedirs(dst, exist_ok=True)
    with open(os.path.join(dst, "manifest.toml"), "w", newline="\n") as fh:
        fh.write("\n".join(out))
    print("wrote %s (%d patches)" % (os.path.join(dst, "manifest.toml"), len(patches)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
