#!/usr/bin/env python3
"""Local-only proof package for the new-maps groundwork: map row 240 "FACT0" from MMM.DAT.

Carries the Hidden Glade's own files and DG script, but placed in the unused MMM.DAT disc area under a new name
and a new DG script row (223), to prove (a) lookupFileTable answers for names in dw_newmaps_files.inc (the
dw.newmaps plugin's hook) and (b) a script table row can point into MMM.DAT. Writes

  build/mods/installed/dw.newmaps.proof/1.0.0/   manifest.toml + assets/   (never committed)
  dw_newmaps_files.inc                           {"FACT0.MAP", lba, size}, {"FACT0.TFS", lba, size},

The glade's assets are the source (mods/preloaded/packages/dw.newmaps/1.0.0); its row-239 record and MAPHEAD
section are read from its manifest patches. See docs/GAME.md 2.3, 3.1, 4.1.

    py -3.12 tools/gen_newmap_proof.py [--bin disc.bin] [--build build-debug]
"""
import argparse
import hashlib
import os
import struct
import sys
import tomllib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extract_disc_file import parse_record, read_extent, sector, walk  # noqa: E402

ROW, NAME, GLADE_ROW, GLADE_SCRIPT, SCRIPT = 240, "FACT0", 239, 118, 223
MAP_ENTRIES = 0x801292D4
MMM_LBA, MMM_SECTORS = 148668, 13500
DG_LBA = 142589
GLADE_MH_END, MH_BUFFER = 0x6084, 0x61A8        # the glade's section is 0x6070..0x6084; script buffer size
J = lambda *p: os.path.join(*p)


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", default=J(root, "disc", "Digimon World (USA).bin"))
    ap.add_argument("--build", default=J(root, "build-debug"))
    a = ap.parse_args()

    gdir = J(root, "mods", "preloaded", "packages", "dw.newmaps", "1.0.0")
    gm = tomllib.load(open(J(gdir, "manifest.toml"), "rb"))
    gp = {(p["target"], p.get("address", p.get("offset"))): p for p in gm["patch"]}
    b = lambda s: bytes.fromhex(s)
    rec = b(gp[("main_exe", MAP_ENTRIES + GLADE_ROW * 16)]["replace"])
    section = bytearray(b(next(p for p in gm["patch"] if p["target"] == "disc_user"
                               and p["replace"].startswith("fb 00"))["replace"]))
    assert section[:6] == struct.pack("<BBHH", 0xFB, 0, GLADE_SCRIPT, GLADE_ROW), "glade section shape"
    section[2:6] = struct.pack("<HH", SCRIPT, ROW)
    row = NAME.encode().ljust(10, b"\0") + rec[10:]
    assert len(row) == 16 and rec[:6] == b"MGEN17"
    # file sizes: the glade's file-table patches hold the real byte sizes of its MAP and TFS
    fsz = [struct.unpack("<2i", b(p["replace"]))[1] for p in gm["patch"] if p["target"] == "main_exe"
           and p["address"] in (0x801214A8, 0x801214B4)]
    assert len(fsz) == 2
    blobs = []
    for fn, size in zip(("glade.map", "glade.tfs"), fsz):
        data = open(J(gdir, "assets", fn), "rb").read()
        assert not any(data[size:]), fn + " has data past its real size"
        blobs.append((fn, data[:size]))
    dg = open(J(gdir, "assets", "glade.dg"), "rb").read()
    blobs.append(("glade.dg", dg))
    sec = lambda n: (n + 2047) // 2048
    k, places = 0, []                                  # MMM.DAT sector of each file, in order
    for _, d in blobs:
        places.append(k)
        k += sec(len(d))
    assert k <= MMM_SECTORS

    f = open(a.bin, "rb")
    listing = []
    rr = parse_record(sector(f, 16)[156:190])
    walk(f, rr["lba"], rr["size"], "", listing)
    files = {os.path.basename(n): (lba, size) for n, lba, size in listing}
    assert files["MMM.DAT"] == (MMM_LBA, MMM_SECTORS * 2048) and files["DG.SCN"][0] == DG_LBA
    for s in range(k):
        assert not any(sector(f, MMM_LBA + s)), "MMM.DAT sector %d is not zero" % s
    rd = lambda n: read_extent(f, *files[n])
    hexs = lambda x: " ".join("%02x" % c for c in x)
    exe = rd("SLUS_010.32")
    ram = lambda addr: addr - struct.unpack_from("<I", exe, 0x18)[0] + 0x800      # PS-X EXE: text at 0x800
    assert not any(exe[ram(MAP_ENTRIES + ROW * 16):][:16]), "MAP_ENTRIES row 240 is not stock zeros"
    dgd = rd("DG.SCN")
    dg_base = files["DG.SCN"][0] * 2048
    assert struct.unpack_from("<I", dgd, 222 * 4)[0] == files["DG.SCN"][1], "table[222] must be the file's end"
    # offset from DG.SCN's start of a file at MMM.DAT sector s
    dgoff = lambda s: (MMM_LBA + s - DG_LBA) * 2048
    dg_start = dgoff(places[2])
    dg_end = dg_start + sec(len(dg)) * 2048
    mh = rd("MAPHEAD.SCN")
    mh_base = files["MAPHEAD.SCN"][0] * 2048
    p = 2
    while struct.unpack_from("<H", mh, p)[0] != ROW:
        p += 4
    old_ptr = struct.unpack_from("<H", mh, p + 2)[0]
    assert mh[old_ptr:][:4] == bytes.fromhex("fb00b400"), "row 240's section is not the stock filler"
    mh_at = GLADE_MH_END + (GLADE_MH_END & 1)
    assert mh_at + len(section) <= MH_BUFFER, "the section must fit MAPHEAD_SCRIPT_BUFFER"
    assert len(mh) <= GLADE_MH_END - 20 and not any(
        (b"".join(sector(f, files["MAPHEAD.SCN"][0] + i) for i in range(mh_at // 2048 + 1)))[len(mh):mh_at + len(section)]), \
        "MAPHEAD tail past EOF is not zero"
    h = hashlib.sha256()
    f.seek(0)
    for block in iter(lambda: f.read(1 << 22), b""):
        h.update(block)
    disc_sha = h.hexdigest()
    assert disc_sha == gm["target"][0]["disc_sha256"], "disc differs from the glade's"

    def patch(target, where, old, new):
        return ('target = "%s"\n%s = %s\nexpected = "%s"\nreplace = "%s"'
                % (target, "address" if target == "main_exe" else "offset",
                   ("0x%08X" if target == "main_exe" else "%d") % where, hexs(old), hexs(new)))
    pkg = J(a.build, "mods", "installed", "dw.newmaps.proof", "1.0.0")
    os.makedirs(J(pkg, "assets"), exist_ok=True)
    out = ["# LOCAL ONLY: carries a copy of the glade's assets and stock bytes. Generated by tools/gen_newmap_proof.py.",
           "format_version = 13", 'id = "dw.newmaps.proof"', 'version = "1.0.0"', 'name = "New Maps Proof (dev)"',
           'author = "DigimonWorldRecomp"',
           'description = "Dev test: map row 240 FACT0 (the glade) read from MMM.DAT, its script from DG row 223."',
           'resolver = "declarative"', 'save_compatibility = "isolated"', "",
           "[[target]]", 'game_id = "SLUS-01032"', 'disc_sha256 = "%s"' % disc_sha, "",
           "[[feature]]", 'id = "proof"', 'name = "MMM.DAT proof"', 'description = "Dev only."', 'group = "Dev"',
           "default_enabled = false", "",
           "[[plugin]]", 'feature = "proof"', 'id = "dw.newmaps"', ""]
    for (fn, data), s in zip(blobs, places):
        open(J(pkg, "assets", fn), "wb").write(data)
        out += ["# %s at MMM.DAT sector %d (LBA %d)" % (fn, s, MMM_LBA + s), "[[overlay]]", 'feature = "proof"',
                'target = "disc_user"', "offset = %d" % ((MMM_LBA + s) * 2048), 'file = "assets/%s"' % fn,
                'sha256 = "%s"' % hashlib.sha256(data).hexdigest(), ""]
    old_tab = dgd[SCRIPT * 4:SCRIPT * 4 + 8]
    patches = [("DG table[223], table[224]: script offset and end (MMM.DAT sector %d)" % places[2],
                patch("disc_user", dg_base + SCRIPT * 4, old_tab, struct.pack("<2I", dg_start, dg_end))),
               ("MAP_ENTRIES[%d] = %s" % (ROW, NAME), patch("main_exe", MAP_ENTRIES + ROW * 16, bytes(16), row)),
               ("MAPHEAD section %d body at 0x%x" % (ROW, mh_at),
                patch("disc_user", mh_base + mh_at, bytes(len(section)), bytes(section))),
               ("MAPHEAD section %d -> 0x%x" % (ROW, mh_at),
                patch("disc_user", mh_base + p + 2, mh[p + 2:p + 4], struct.pack("<H", mh_at)))]
    for why, body in patches:
        out += ["# " + why, "[[patch]]", 'feature = "proof"', body, ""]
    with open(J(pkg, "manifest.toml"), "w", newline="\n") as fh:
        fh.write("\n".join(out))
    rows = ['{"%s.%s", %d, %d},' % (NAME, ext, MMM_LBA + places[i], len(blobs[i][1])) for i, ext in ((0, "MAP"), (1, "TFS"))]
    open(J(root, "dw_newmaps_files.inc"), "w", newline="\n").write("\n".join(rows) + "\n")
    print("wrote %s\n%s" % (J(pkg, "manifest.toml"), "\n".join(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
