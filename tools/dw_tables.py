#!/usr/bin/env python3
"""Dump the map-Digimon records, species and moves from your own disc as CSV.

    py -3.12 tools/dw_tables.py [--bin disc.bin] [--out build/tables]

Writes records.csv, species.csv and moves.csv. They are game data, so a folder
inside this repo is refused unless git ignores it. Reads only; the image is
never written. The other tools import Disc from here.

Beyond the raw fields (docs/GAME.md 3.8, 5.2, 7), from tracing the scripts:
  records.csv   dg_scripts     DG scripts its rows bind
                autotalk       from the rows' SET_DIGIMON on its slot
                starts_battle  its talk section reaches START_BATTLE; through file
                               jumps; unknown if the section does not exist
                trigger1       that trace also sets trigger 1: no flee, no MP paid
                These two see talk sections only. A fight that a tile or arrival
                section starts, such as MGEN98's MetalGreymon, reads unknown or no.
  species.csv   mmd_bytes, finisher (species 60 really fires tech 0x70: utils2.c:338), and missing_anims: anims the field and battle
                code plays, or its move slots name, that its MMD has no entry for
"""
import argparse
import csv
import os
import struct
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scan_trigger_ids as S  # noqa: E402
from extract_disc_file import parse_record, read_extent, sector, walk  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_BIN = os.path.join(ROOT, "disc", "Digimon World (USA).bin")

MAP_ENTRIES = 0x801292D4             # 16 bytes per row: name[10] at +0, flags at +12
SPECIES = 0x8012CEB4                 # DIGIMON_DATA, 52 bytes each
SPECIES_FILES = 0x8011D19C           # char* per species: its MMD's name
MOVE_NAMES = 0x80126054              # char* per tech
MOVE_DATA = 0x8012623C               # 16 bytes each
ITEMS = 0x801269DC                   # 32 bytes each, name first
TYPE_FACTORS = 0x80125F70            # 7x7 bytes
NUM_ROWS, NUM_SPECIES, NUM_MOVES = 255, 180, 122
# The 42 fixed words of a map record. BOSS in gen_newmap.py has this layout too.
REC = ("species_id followMode x y z rx ry rz tracking unk2 script hp mp curHP curMP off defense speed brains bits "
       "chargeMode unk1 m0 m1 m2 m3 p0 p1 p2 p3 fleeX fleeY fleeZ wpts a0 a1 a2 a3 a4 a5 a6 a7").split()
FIELD_BATTLE_ANIMS = (0, 2, 4, 0x21, 0x22, 0x23, 0x24, 0x25, 0x28, 0x29, 0x2A, 0x2B, 0x2C)


def cstr(b):
    return b.split(b"\0")[0].decode("ascii", "replace")


def is_finisher(tech):
    return 0x3A <= tech < 0x71


class Disc:
    """The user's image: files by name, and the EXE by RAM address."""

    def __init__(self, path):
        self.f = open(path, "rb")
        root = parse_record(sector(self.f, 16)[156:190])
        listing = []
        walk(self.f, root["lba"], root["size"], "", listing)
        self.files = {os.path.basename(name): (lba, size) for name, lba, size in listing}    # base names are unique
        self.exe = self.read("SLUS_010.32")

    def read(self, name):
        return read_extent(self.f, *self.files[name])

    def ex(self, addr, n):
        return self.exe[addr - 0x80090000:addr - 0x80090000 + n]

    def str_at(self, table, i):
        p = struct.unpack("<I", self.ex(table + i * 4, 4))[0]
        return cstr(self.exe[p - 0x80090000:p - 0x80090000 + 64]) if 0x80090000 <= p < 0x80200000 else ""

    def species(self, i):
        b = self.ex(SPECIES + i * 52, 52)
        bones, radius, height = struct.unpack_from("<ihh", b, 20)
        return dict(id=i, name=cstr(b[:20]), code=self.str_at(SPECIES_FILES, i), bones=bones, radius=radius,
                    height=height, type=b[28], level=b[29], special=list(b[30:33]), drop=b[33],
                    drop_item=cstr(self.ex(ITEMS + b[33] * 32, 20)), chance=b[34], moves=list(b[35:51]))

    def move(self, i):
        b = self.ex(MOVE_DATA + i * 16, 16)
        dist, power = struct.unpack_from("<ih", b)
        return dict(id=i, name=self.str_at(MOVE_NAMES, i), dist=dist, power=power, mp=b[6], iframes=b[7], range=b[8],
                    special=b[9], status=b[10], acc=b[11], statusChance=b[12], flags=b[13])

    def map_records(self):
        """Every record of the maps a row loads with flag 0x80; the others ignore their block."""
        rows = {}
        for r in range(NUM_ROWS):
            b = self.ex(MAP_ENTRIES + r * 16, 16)
            if cstr(b[:10]):
                rows.setdefault(cstr(b[:10]).upper(), []).append((r, b[12]))
        out = []
        for file in sorted(n for n in self.files if n.upper().endswith(".MAP")):
            name = file.upper()[:-4]
            if not any(flags & 0x80 for _, flags in rows.get(name, ())):
                continue
            d = self.read(file)
            ent = struct.unpack_from("<%di" % (struct.unpack_from("<i", d)[0] // 4), d)[-2]
            p = ent + 122
            for slot in range(struct.unpack_from("<h", d, ent + 120)[0]):
                words = struct.unpack_from("<42h", d, p)
                p += 84 + 6 * words[33]
                s = self.species(words[0])
                out.append(dict(map=name, rows=[r for r, _ in rows[name]], slot=slot, species=s["name"], level=s["level"],
                                **dict(zip(REC, words))))
        return out

    def species_row(self, i):
        """species() plus what only the MMD can say."""
        s = self.species(i)
        have, size = set(), 0
        if s["code"].upper() + ".MMD" in self.files:
            d = self.read(s["code"].upper() + ".MMD")
            size, table = len(d), struct.unpack_from("<i", d, 4)[0]
            have = {a for a, off in enumerate(struct.unpack_from("<%di" % min((size - table) // 4, 0x50), d, table))
                    if off and 0 < table + off < size}
        wanted = list(FIELD_BATTLE_ANIMS) + [0x2E + k for k, m in enumerate(s["moves"]) if m != 0xFF]
        return dict(s, mmd_bytes=size, finisher=[m for m in s["moves"] if is_finisher(m)],
                    missing_anims=["%x" % a for a in wanted if a not in have])


class Tracer(S.Decoder):
    """scan_trigger_ids' decoder, going on past FB and keeping what the tables need."""

    def __init__(self, *a):
        super().__init__(*a)
        self.ops, self.scripts, self.placed = set(), set(), []

    def step(self, op, p, work, far):
        self.ops.add(op)
        if op == 0xFB:
            self.scripts.add(self.u16(p + 1))
            return p + 5
        if op == 0x47:
            self.placed.append((self.u8(p + 1), self.u8(p + 2)))
        return super().step(op, p, work, far)


def tag_records(disc, recs):
    """Add dg_scripts, autotalk, starts_battle and trigger1 to each record."""
    dg, mh = disc.read("DG.SCN"), disc.read("MAPHEAD.SCN")
    blobs = {int(b[0][6:]): b for b in S.blob_list("DG.SCN", dg)}
    row_scripts, autotalk = {}, {}
    for sid, off in S.parse_sections(mh, 0, len(mh))[0]:
        t = Tracer(mh, 0, len(mh), "mh")
        t.trace(off, sid, [])
        row_scripts[sid] = t.scripts
        for slot, talk in t.placed:
            autotalk.setdefault((sid, slot), set()).add(talk)

    def reach(script, section):
        """(battle?, trigger 1?) over every path from a DG section, or None if it is not there."""
        todo, seen, battle, t1 = [(script, section)], set(), False, False
        while todo:
            k = todo.pop()
            if k in seen or k[0] not in blobs:
                continue
            seen.add(k)
            name, s, e, secs, _ = blobs[k[0]]
            if k[1] not in dict(secs):
                continue
            t = Tracer(dg, s, e, name)
            t.trace(dict(secs)[k[1]], k[1], todo)
            battle = battle or 0x66 in t.ops
            t1 = t1 or any(r.kind == S.SET and r.tid == 1 for r in t.refs)
        return (battle, t1) if script in blobs and section in dict(blobs[script][3]) else None

    for r in recs:
        scripts = sorted({s for row in r["rows"] for s in row_scripts.get(row, ())})
        found = [x for x in (reach(s, r["script"]) for s in scripts) if x]
        r["dg_scripts"] = scripts
        r["autotalk"] = sorted({a for row in r["rows"] for a in autotalk.get((row, r["slot"]), ())})
        r["starts_battle"] = "unknown" if not found else "yes" if any(b for b, _ in found) else "no"
        r["trigger1"] = "yes" if any(b and t for b, t in found) else "no"
    return recs


def write_csv(path, rows):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows({k: " ".join(map(str, v)) if isinstance(v, list) else v for k, v in r.items()} for r in rows)
    print("%s: %d rows" % (path, len(rows)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", default=DEFAULT_BIN)
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "tables"))
    a = ap.parse_args()
    out = os.path.abspath(a.out)
    if (os.path.normcase(out + os.sep).startswith(os.path.normcase(ROOT + os.sep))
            and subprocess.run(["git", "-C", ROOT, "check-ignore", "-q", os.path.join(out, "records.csv")]).returncode):
        sys.exit("refusing %s: inside the repo and not gitignored, and these tables are game data" % out)
    os.makedirs(out, exist_ok=True)
    disc = Disc(a.bin)
    write_csv(os.path.join(out, "records.csv"), tag_records(disc, disc.map_records()))
    write_csv(os.path.join(out, "species.csv"), [disc.species_row(i) for i in range(NUM_SPECIES)])
    write_csv(os.path.join(out, "moves.csv"), [disc.move(i) for i in range(NUM_MOVES)])


if __name__ == "__main__":
    main()
