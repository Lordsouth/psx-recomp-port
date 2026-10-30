#!/usr/bin/env python3
"""Which port features cannot run alongside Digimon World 1.5?

WHY THIS EXISTS. The dw.1_5 package swaps the whole disc for 1.5's (a BPS
applied in memory), so the executable and ten overlays the other packages were
written against are no longer the ones that load. A guarded patch whose bytes
1.5 changed fails its guard -- and a failed guard throws out the whole mod plan
(main_exe) or every disc patch (disc_user), not just the one feature. A plugin
hook on a function 1.5 rewrote silently stops firing, because the rewritten
function runs interpreted and the hook lives in the compiled body.

So a feature conflicts with 1.5 when:
  * any of its main_exe / disc_user / disc_raw guarded writes covers a byte
    that differs between the vanilla disc and the 1.5 disc;
  * a disc_user write lands in a file 1.5 moved (it would patch a stale copy
    the game no longer reads);
  * a game.toml hook it owns (mod_function_entry/exit/replace_funcs) sits in a
    function 1.5 changed, or a code/data range its plugin depends on changed.
The expanded roster is exclusive with 1.5 regardless: both use the same free
Digimon slots.

Cheats are per-frame RAM pokes, not guarded writes, and cheats.txt applies
even with the cheats feature off, so they are reported and never auto-disabled:
a cheat is flagged if it pokes a byte 1.5 changed in the original image or 1.5's
relocated code. Pokes into 1.5's boot staging block (copied away by the entry
stub, BSS afterwards) are listed too; they are safe only because the cheat
engine waits for the game's entry point.

  py -3.12 tools/check_dw15_compat.py
  py -3.12 tools/check_dw15_compat.py --vanilla <stock.bin> --patched <1.5.bin>

The 1.5 image is the BPS applied to the stock disc (Flips or Rom Patcher JS);
both hashes are checked against the dw.1_5 manifest. Exit 0 always prints the
list; exit 2 means an input was missing or did not match.
"""
import argparse
import glob
import hashlib
import mmap
import os
import struct
import sys
import tomllib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
from extract_disc_file import SECTOR, USER_OFF, USER_SIZE, parse_record, walk  # noqa: E402

PKG_GLOB = os.path.join(ROOT, "mods", "packages", "*", "*", "manifest.toml")
SELF = "dw.1_5"
ALWAYS_EXCLUSIVE = {"dw.vanilla.roster"}
NOT_SHIPPED = {"dw.vanilla.vice"}
WS = "psx.enhancement.widescreen/widescreen"
ROSTER = "dw.vanilla.roster/roster"

# Who owns each game.toml hook. A hook missing here is an error, so a new
# hook cannot slip past this check unowned.
HOOK_OWNER = {
    0x800E56EC: WS,      # dw_ws_backdrop.c  setPosDataPolyFT4
    0x800B8928: WS,      # dw_ws_backdrop.c  renderRectPolyFT4
    0x80096F90: WS,      # dw_ws_backdrop.c  GsSortSprite
    0x800D78BC: WS,      # dw_ws_camera.c    handleTileUpdate (entry + exit)
    0x800D6A4C: WS,      # dw_ws_camera.c    second camera X clamp
    0x800D7F68: WS,      # dw_ws_camera.c    cameraIsAtEdge
    0x800D57EC: WS,      # dw_ws_sliver.c    renderMap
    0x800EEBDC: ROSTER,  # dw_roster.c       initializeHeap
    0x800A1F68: ROSTER,  # dw_roster.c       loadMMD
    0x800A2810: ROSTER,  # dw_roster.c       loadMMDAsync
    0x800FF7F8: ROSTER,  # dw_roster_flags.c setDigimonRaised
    0x800FF824: ROSTER,  # dw_roster_flags.c hasDigimonRaised
    0x800E2544: "dw.vanilla.evolution/native-evolution",  # dw_evolution.c
}

# Code and data the widescreen plugins read or write besides their hooks
# (dw_ws_backdrop.c): call sites it matches by return address, two functions
# whose frames it brackets, and the BTL overlay's bar slide table.
EXTRA_EXE = [
    (0x800D71F8, 0x800D7200, WS, "setPosDataMapTile call site (RA match)"),
    (0x800B5074, 0x800B507C, WS, "buildMapOverlayPrim call site (RA match)"),
    (0x800ACFE0, 0x800ACFE8, WS, "renderFade call site (RA match)"),
    (0x800DD2A4, 0x800DD5E0, WS, "renderGameClock (bracketed)"),
    (0x800A936C, 0x800A97E0, WS, "renderStatusBars (bracketed)"),
]
EXTRA_FILE = [
    ("/BTL_REL.BIN", 0x80052AE0, 0x80073280, 16, WS, "BTL bar slide table"),
]

# Test Build 1.3.1's entry stub (0x80135000) copies three blocks out of the
# staging area, then jumps to the stock entry 0x800A4240:
#   0x80135100 -> 0x801F0000  (40960 B)
#   0x8013F100 -> 0x801FA000  (12032 B)
#   0x80142000 -> 0x80089000  (30720 B)
# Only valid for that build; the target hash check below pins it.
RELOCATED = [(0x80135100, 0x801F0000, 40960),
             (0x8013F100, 0x801FA000, 12032),
             (0x80142000, 0x80089000, 30720)]


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


class Disc:
    def __init__(self, path):
        self.f = open(path, "rb")
        self.m = mmap.mmap(self.f.fileno(), 0, access=mmap.ACCESS_READ)
        pvd = self.user(16 * USER_SIZE, USER_SIZE)
        root = parse_record(pvd[156:156 + 34])
        files = []
        walk(self.m, root["lba"], root["size"], "", files)
        self.files = {n.upper(): (lba, size) for n, lba, size in files}

    def user(self, off, n):
        """disc_user bytes; may span sectors (reads per sector)."""
        out = bytearray()
        while n > 0:
            lba, at = divmod(off, USER_SIZE)
            take = min(n, USER_SIZE - at)
            p = lba * SECTOR + USER_OFF + at
            out += self.m[p:p + take]
            off, n = off + take, n - take
        return bytes(out)

    def raw(self, off, n):
        return self.m[off:off + n]

    def file(self, name):
        lba, size = self.files[name.upper()]
        return self.user(lba * USER_SIZE, size)

    def file_at(self, lba):
        for name, (flba, size) in self.files.items():
            if flba <= lba < flba + (size + USER_SIZE - 1) // USER_SIZE:
                return name
        return None


class Exe:
    """A PS-EXE as loaded: text at t_addr, BSS (zero) everywhere else."""
    def __init__(self, blob):
        self.t_addr, t_size = struct.unpack_from("<II", blob, 0x18)
        self.text = blob[0x800:0x800 + t_size]
        self.end = self.t_addr + len(self.text)

    def at(self, a, n):
        out = bytearray(n)
        lo, hi = max(a, self.t_addr), min(a + n, self.end)
        if lo < hi:
            out[lo - a:hi - a] = self.text[lo - self.t_addr:hi - self.t_addr]
        return bytes(out)


def first_diff(a, b):
    return next(i for i in range(len(a)) if a[i] != b[i])


def load_manifests():
    out = {}
    for mf in sorted(glob.glob(PKG_GLOB)):
        with open(mf, "rb") as f:
            m = tomllib.load(f)
        out[m["id"]] = m
    return out


def function_starts():
    starts = []
    with open(os.path.join(ROOT, "seeds", "ghidra_funcs.txt")) as f:
        for line in f:
            line = line.split("#")[0].strip()
            if line:
                starts.append(int(line, 16))
    return sorted(set(starts))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--vanilla", default=os.path.join(
        ROOT, "disc", "Digimon World (USA).bin"))
    ap.add_argument("--patched", default=os.path.normpath(os.path.join(
        ROOT, "..", "dw15-release", "patched", "digimon_world_1_5_test1.bin")))
    a = ap.parse_args()

    manifests = load_manifests()
    delta = manifests[SELF]["disc_delta"][0]
    for path, want in ((a.vanilla, delta["source_sha256"]),
                       (a.patched, delta["target_sha256"])):
        if not os.path.exists(path):
            print("missing: %s" % path)
            return 2
        if sha256(path) != want:
            print("hash mismatch (want %s): %s" % (want, path))
            return 2

    van, pat = Disc(a.vanilla), Disc(a.patched)
    vexe, pexe = Exe(van.file("/SLUS_010.32")), Exe(pat.file("/SLUS_010.32"))

    def exe_changed(lo, hi):
        v, p = vexe.at(lo, hi - lo), pexe.at(lo, hi - lo)
        return None if v == p else lo + first_diff(v, p)

    reasons = {}          # "pkg/feature" -> [reason, ...]
    features = []         # every (pkg/feature) we looked at, in order

    def flag(owner, why):
        reasons.setdefault(owner, []).append(why)

    for pkg, m in manifests.items():
        if pkg == SELF or pkg in NOT_SHIPPED:
            continue
        for feat in m.get("feature", []):
            owner = "%s/%s" % (pkg, feat["id"])
            features.append(owner)
            if pkg in ALWAYS_EXCLUSIVE:
                flag(owner, "uses the same free Digimon slots as 1.5")
        for p in m.get("patch", []):
            owner = "%s/%s" % (pkg, p["feature"])
            exp = bytes.fromhex(p["expected"].replace(" ", ""))
            t = p["target"]
            if t == "main_exe":
                lo = p["address"]
                hit = exe_changed(lo, lo + len(exp))
                if hit is not None:
                    flag(owner, "main_exe 0x%08X changed by 1.5" % hit)
            elif t == "disc_user":
                off = p["offset"]
                v, q = van.user(off, len(exp)), pat.user(off, len(exp))
                name = van.file_at(off // USER_SIZE)
                if v != q:
                    flag(owner, "disc_user %s+0x%X changed by 1.5" % (
                        name, off + first_diff(v, q)
                        - van.files[name][0] * USER_SIZE))
                elif name and pat.files.get(name) != van.files[name]:
                    flag(owner, "disc_user %s: 1.5 moved this file" % name)
            elif t == "disc_raw":
                off = p["offset"]
                if van.raw(off, len(exp)) != pat.raw(off, len(exp)):
                    flag(owner, "disc_raw %d changed by 1.5" % off)

    # Hooks: function = [start, next seed start).
    with open(os.path.join(ROOT, "game.toml"), "rb") as f:
        rec = tomllib.load(f)["recompiler"]
    starts = function_starts()
    for key in ("mod_function_entry_funcs", "mod_function_exit_funcs",
                "mod_function_replace_funcs"):
        for s in rec.get(key, []):
            addr = int(s, 16)
            owner = HOOK_OWNER.get(addr)
            if owner is None:
                print("error: game.toml hook 0x%08X has no owner in "
                      "HOOK_OWNER; add it" % addr)
                return 2
            end = next((x for x in starts if x > addr), addr + 4)
            hit = exe_changed(addr, end)
            if hit is not None:
                flag(owner, "%s 0x%08X: function changed at 0x%08X"
                     % (key.replace("mod_function_", "").replace("_funcs", ""),
                        addr, hit))
    for lo, hi, owner, label in EXTRA_EXE:
        hit = exe_changed(lo, hi)
        if hit is not None:
            flag(owner, "%s changed at 0x%08X" % (label, hit))
    for name, base, vaddr, n, owner, label in EXTRA_FILE:
        v = van.file(name)[vaddr - base:vaddr - base + n]
        q = pat.file(name)[vaddr - base:vaddr - base + n]
        if v != q:
            flag(owner, "%s changed (%s)" % (label, name))

    # Cheats (cheats.txt), reported per cheat.
    cheat_conflicts, cheat_staging = {}, {}
    name = None
    for line in open(os.path.join(ROOT, "cheats.txt"), encoding="utf-8"):
        line = line.split("#")[0].strip()
        if line.startswith("["):
            name = line[3:].strip()
            continue
        parts = line.split()
        if not name or len(parts) != 2 or len(parts[0]) != 8:
            continue
        op, addr = int(parts[0][:2], 16), 0x80000000 | int(parts[0][2:], 16)
        if op not in (0x30, 0x80):
            continue
        n = 1 if op == 0x30 else 2
        if addr < vexe.end and exe_changed(addr, addr + n) is not None:
            cheat_conflicts.setdefault(name, (addr, "original image changed"))
        elif any(d <= addr < d + ln for _, d, ln in RELOCATED):
            cheat_conflicts.setdefault(name, (addr, "1.5 relocated code"))
        elif vexe.end <= addr < pexe.end:
            # Every byte here is copied out by the stub, zero or not.
            cheat_staging.setdefault(name, addr)

    # Report.
    print("vanilla exe 0x%08X..0x%08X, 1.5 exe 0x%08X..0x%08X"
          % (vexe.t_addr, vexe.end, pexe.t_addr, pexe.end))
    changed = sum(vexe.text[i] != pexe.text[i] for i in range(len(vexe.text)))
    print("%d bytes of the original image differ\n" % changed)
    for owner in features:
        why = reasons.get(owner)
        if not why:
            print("  ok        %s" % owner)
            continue
        print("  CONFLICT  %s  (%d)" % (owner, len(why)))
        for w in why[:3]:
            print("              %s" % w)
        if len(why) > 3:
            print("              ... %d more" % (len(why) - 3))
    stray = sorted(set(reasons) - set(features))
    for owner in stray:
        print("  CONFLICT  %s  (owner has no manifest feature): %s"
              % (owner, reasons[owner][0]))

    # Cheats are not guarded writes and cheats.txt applies with the cheats
    # feature off too, so they are reported, never auto-disabled.
    print("\ncheats.txt (reported only):")
    if not cheat_conflicts and not cheat_staging:
        print("  no cheat pokes a byte 1.5 changed")
    for nm, (addr, why) in cheat_conflicts.items():
        print("  changed  0x%08X  %s  (%s)" % (addr, nm, why))
    if cheat_staging:
        print("  %d cheat(s) poke 1.5's boot staging block 0x%08X..0x%08X;"
              % (len(cheat_staging), vexe.end, pexe.end))
        print("  safe only because psx_cheats.c waits for the game's entry "
              "point:")
        for nm, addr in cheat_staging.items():
            print("           0x%08X  %s" % (addr, nm))

    off = [o for o in features if o in reasons]
    print("\nturn off with 1.5 (%d):" % len(off))
    for o in off:
        print("  " + o)
    return 0


if __name__ == "__main__":
    sys.exit(main())
