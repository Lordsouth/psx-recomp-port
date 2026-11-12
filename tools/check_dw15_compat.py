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
  * any of its main_exe guarded writes covers a byte that differs between the
    vanilla and the 1.5 executable (as guest RAM, below);
  * any of its disc_user / disc_raw writes touches a sector the 1.5 delta owns
    -- one whose raw 2352 bytes 1.5 changed, or one past the stock disc's end.
    The runtime refuses to launch such a plan ("collides at disc LBA ... with
    disc delta"), whether or not the write's own bytes changed;
  * a disc_user write lands in a file 1.5 moved (it would patch a stale copy
    the game no longer reads);
  * a game.toml hook it owns (mod_function_entry/exit/replace_funcs) sits in a
    function 1.5 changed, or a code/data range its plugin depends on changed.
The expanded roster's own writes always conflict with 1.5 (both use slot 114);
its 1.5 edition, roster-15, runs in its place.

main_exe guards are checked when the plan is applied, at the stock entry point
0x800A4240 -- under 1.5 that is after its entry stub has copied its blocks out
of the staging area -- so both sides are compared as guest RAM at that moment.

1.5-only features (dw.1_5's own extras, which reach dw15 through
requires_feature constraints, or any feature with a [[requirement]] on
dw.1_5/dw15) are expected to differ from vanilla: their guards are checked
against 1.5 instead (main_exe as guest RAM at the entry point, disc writes
against the patched disc, where past-end sectors exist), and a dw.1_5 feature
without that constraint is an error (it could be ticked alone and fail its
guard on the stock game). Mirroring the runtime, only the in-package
requires_feature chain lets a write onto a delta-owned sector (it is then
guarded and applied after the delta); a cross-package [[requirement]] does
not, so such a write is an error.

Last, the conflicts declared in manifests (dw15's list, plus any feature that
names dw.1_5/dw15) must equal the computed list: a missing entry lets a failing
guard throw out every mod, a stale one turns a working feature off.

Cheats are per-frame RAM pokes, not guarded writes, and cheats.txt applies
even with the cheats feature off, so they are reported and never auto-disabled:
a cheat is flagged if it pokes a byte 1.5 changed in the original image or 1.5's
relocated code. Pokes into 1.5's boot staging block (copied away by the entry
stub, BSS afterwards) are listed too; they are safe only because the cheat
engine waits for the game's entry point. A write behind a game gate (a D0-E3
line on a word the two executables hold differently) runs on one game only and
is listed as such instead; a game gate on a byte a mod patches, or one that
passes on neither game, is an error.

  py -3.12 tools/check_dw15_compat.py
  py -3.12 tools/check_dw15_compat.py --vanilla <stock.bin> --patched <1.5.bin>

The 1.5 image is the BPS applied to the stock disc (Flips or Rom Patcher JS);
both hashes are checked against the dw.1_5 manifest. Exit 0: the list printed
and the manifests agree with it; exit 1: a declared conflict is missing or
stale, or a 1.5-only guard does not match 1.5; exit 2: an input was missing or
did not match.
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

PKG_GLOB = os.path.join(ROOT, "mods", "preloaded", "packages", "*", "*", "manifest.toml")
SELF = "dw.1_5"
DW15 = SELF + "/dw15"
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
    0x800D57EC: WS,      # dw_ws_sliver.c    renderMap (+ dw_ws_world.c)
    0x800D5608: WS,      # dw_ws_camera.c    isOffScreen
    0x800D6F64: WS,      # dw_ws_camera.c    tickCameraFollowPlayer
    0x800D6308: WS,      # dw_ws_sliver.c    readMapTFS
    0x80098808: WS,      # dw_ws_backdrop.c  GsDrawOt (full-screen plates)
    0x800B4CF4: WS,      # dw_ws_weather.c   buildSnowflakePrim
    0x800B4780: WS,      # dw_ws_weather.c   renderMist
    0x800EEBDC: ROSTER,  # dw_roster.c       initializeHeap
    0x800A1F68: ROSTER,  # dw_roster.c       loadMMD
    0x800A2810: ROSTER,  # dw_roster.c       loadMMDAsync
    0x800FF7F8: ROSTER,  # dw_roster_flags.c setDigimonRaised
    0x800FF824: ROSTER,  # dw_roster_flags.c hasDigimonRaised
    0x800E2544: "dw.vanilla.evolution/native-evolution",  # dw_evolution.c
    0x80112D34: "dw.newmaps/glade",  # dw_newmaps.c  createSavegameChecksum
    # lookupFileTable: a filter hook serving the new map names in dw_newmaps_files.inc (gen_factory.py)
    0x800A31F8: "dw.newmaps.factory/factory",  # dw_newmaps.c  lookupFileTable (filter)
}

# Code and data the widescreen plugins read or write besides their hooks
# (dw_ws_backdrop.c, dw_ws_camera.c): call sites matched by return address,
# two functions whose frames they bracket, and the BTL overlay's bar slide
# table.
EXTRA_EXE = [
    (0x800D71F8, 0x800D7200, WS, "setPosDataMapTile call site (RA match)"),
    (0x800B5074, 0x800B507C, WS, "buildMapOverlayPrim call site (RA match)"),
    (0x800ACFE0, 0x800ACFE8, WS, "renderFade call site (RA match)"),
    (0x800D5598, 0x800D55A0, WS, "entityIsOffScreen's isOffScreen call (RA match)"),
    (0x800E20F0, 0x800E20F8, WS, "isBoxOffScreen's isOffScreen call (RA match)"),
    (0x800D312C, 0x800D313C, WS, "isTileOffScreen X bounds ([widescreen.cull] slti sites)"),
    (0x800B4460, 0x800B446C, WS, "renderMapOverlays sprite cull (relative_x_bound sites)"),
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


RAM_BASE, RAM_SIZE = 0x80000000, 0x200000


class Exe:
    """A PS-EXE as loaded: text at t_addr, BSS (zero) everywhere else; `ram`
    is guest RAM at the stock entry point, after `copies` (src, dst, n) ran."""
    def __init__(self, blob, copies=()):
        self.t_addr, t_size = struct.unpack_from("<II", blob, 0x18)
        self.text = blob[0x800:0x800 + t_size]
        self.end = self.t_addr + len(self.text)
        self.ram = bytearray(RAM_SIZE)
        self.ram[self.t_addr - RAM_BASE:self.end - RAM_BASE] = self.text
        for src, dst, n in copies:
            self.ram[dst - RAM_BASE:dst - RAM_BASE + n] = \
                self.ram[src - RAM_BASE:src - RAM_BASE + n]

    def at(self, a, n):
        o = a & 0x1FFFFFFF                  # KUSEG/KSEG0/KSEG1 alias the RAM
        if o + n > RAM_SIZE:
            raise ValueError("0x%08X+%d is outside guest RAM" % (a, n))
        return bytes(self.ram[o:o + n])


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
    vexe = Exe(van.file("/SLUS_010.32"))
    pexe = Exe(pat.file("/SLUS_010.32"), RELOCATED)

    def exe_changed(lo, hi):
        try:
            v, p = vexe.at(lo, hi - lo), pexe.at(lo, hi - lo)
        except ValueError:
            return lo                       # outside guest RAM: never "ok"
        return None if v == p else lo + first_diff(v, p)

    van_sectors = len(van.m) // SECTOR

    def owned(p, n):
        """First sector of the write the runtime hands to the 1.5 delta
        (build_delta_sectors: raw bytes changed, or past the stock end)."""
        unit = SECTOR if p["target"] == "disc_raw" else USER_SIZE
        for lba in range(p["offset"] // unit, (p["offset"] + n - 1) // unit + 1):
            if lba >= van_sectors or \
                    van.raw(lba * SECTOR, SECTOR) != pat.raw(lba * SECTOR, SECTOR):
                return lba
        return None

    reasons = {}          # "pkg/feature" -> [reason, ...]
    features = []         # every vanilla (pkg/feature) we looked at, in order
    only15 = {}           # 1.5-only "pkg/feature" -> [patches, [bad, ...]]
    errors = []

    def flag(owner, why):
        reasons.setdefault(owner, []).append(why)

    def guard_ok_on_1_5(p, exp):
        t, n = p["target"], len(exp)
        if t == "main_exe":
            try:
                return pexe.at(p["address"], n) == exp
            except ValueError:
                return False
        if t == "disc_user":
            return pat.user(p["offset"], n) == exp
        return pat.raw(p["offset"], n) == exp

    for pkg, m in manifests.items():
        if pkg in NOT_SHIPPED:
            continue
        if pkg == SELF:
            req = {}
            for c in m.get("constraint", []):
                if c.get("kind") == "requires_feature":
                    req.setdefault(c["feature"], []).append(c["requires_feature"])

            def reaches_dw15(f):            # chains, like requires_delta
                seen, todo = set(), [f]
                while todo:
                    f = todo.pop()
                    if f == "dw15":
                        return True
                    if f not in seen:
                        seen.add(f)
                        todo += req.get(f, [])
                return False
            needs = {f["id"] for f in m.get("feature", [])
                     if f["id"] != "dw15" and reaches_dw15(f["id"])}
            for feat in m.get("feature", []):
                if feat["id"] != "dw15" and feat["id"] not in needs:
                    errors.append("%s/%s has no requires_feature dw15 constraint"
                                  % (pkg, feat["id"]))
        else:
            # An unconditional [[requirement]] on dw15 keeps the delta beneath
            # the feature's writes, as the runtime's requires_delta follows it.
            needs = {r["feature"] for r in m.get("requirement", [])
                     if r.get("package") == SELF and r.get("requires_feature") == "dw15"
                     and not r.get("when")}
        for feat in m.get("feature", []):
            owner = "%s/%s" % (pkg, feat["id"])
            if feat["id"] in needs:
                only15[owner] = [0, []]
            elif owner != DW15:
                features.append(owner)
            if pkg in ALWAYS_EXCLUSIVE and owner not in only15:
                flag(owner, "uses the same free Digimon slots as 1.5")
        for p in m.get("patch", []):
            owner = "%s/%s" % (pkg, p["feature"])
            exp = bytes.fromhex(p["expected"].replace(" ", ""))
            t = p["target"]
            if owner in only15:
                only15[owner][0] += 1
                where = "%s 0x%X" % (
                    t, p["address"] if t == "main_exe" else p["offset"])
                if not guard_ok_on_1_5(p, exp):
                    only15[owner][1].append(where)
                continue
            if owner == DW15:
                continue
            if t == "main_exe":
                lo = p["address"]
                hit = exe_changed(lo, lo + len(exp))
                if hit is not None:
                    flag(owner, "main_exe 0x%08X changed by 1.5" % hit)
                continue
            hit = owned(p, len(exp))
            name = van.file_at(p["offset"] // USER_SIZE) if t == "disc_user" else None
            if hit is not None:
                flag(owner, "%s LBA %d (%s) collides with the 1.5 delta" % (
                    t, hit, pat.file_at(hit) or van.file_at(hit) or "no file"))
            elif name and pat.files.get(name) != van.files[name]:
                flag(owner, "disc_user %s: 1.5 moved this file" % name)

    # Overlays: the runtime refuses any on a delta-owned sector, required or not.
    for pkg, m in manifests.items():
        if pkg in NOT_SHIPPED:
            continue
        for o in m.get("overlay", []):
            owner = "%s/%s" % (pkg, o["feature"])
            if owner == DW15 or o["target"] == "main_exe":
                continue
            asset = os.path.join(ROOT, "mods", "preloaded", "packages", pkg, "1.0.0", o["file"])
            hit = owned(o, os.path.getsize(asset))
            if hit is None:
                continue
            why = "overlay %s at LBA %d is on a 1.5-delta sector" % (o["file"], hit)
            if owner in only15:
                only15[owner][1].append(why)
            else:
                flag(owner, why)

    # Hooks: function = [start, next seed start).
    with open(os.path.join(ROOT, "game.toml"), "rb") as f:
        rec = tomllib.load(f)["recompiler"]
    starts = function_starts()
    # Only the kinds that die when the function runs interpreted are checked: generated code
    # fires exit and replace hooks, but dirty_ram_interp.c fires only psx_mod_function_entry
    # (entry callbacks and filters) and psx_mod_try_function_replacement. An entry hook
    # follows the function wherever it is interpreted at the same address.
    for key in ("mod_function_exit_funcs", "mod_function_replace_funcs"):
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

    # Cheats (cheats.txt), reported per cheat. A D0-D3/E0-E3 line gates the
    # next line (psx_cheats.c). It is a game gate when its address is in the
    # original image and the two executables differ there: it then passes on
    # one game only, so the write it gates is checked for that game only. A
    # mod patch on a game gate's word could flip it, which is an error.
    exe_patched = [(p["address"], p["address"] + len(p["expected"].replace(" ", "")) // 2)
                   for pkg, m in manifests.items() if pkg not in NOT_SHIPPED
                   for p in m.get("patch", []) if p["target"] == "main_exe"]

    def gate_games(op, addr, val):
        n = 2 if op < 0xE0 else 1
        if not vexe.t_addr <= addr <= vexe.end - n or exe_changed(addr, addr + n) is None:
            return None                      # a runtime value: not a game gate
        if any(lo < addr + n and addr < hi for lo, hi in exe_patched):
            errors.append("cheats.txt gate 0x%08X is on a byte a mod patches"
                          % addr)
        val &= 0xFFFF if n == 2 else 0xFF
        test = (lambda x: x == val, lambda x: x != val,
                lambda x: x < val, lambda x: x > val)[op & 3]
        return {g for g, e in (("stock", vexe), ("1.5", pexe))
                if test(int.from_bytes(e.at(addr, n), "little"))}

    cheat_conflicts, cheat_staging, cheat_gated = {}, {}, []
    name, gate = None, None
    for line in open(os.path.join(ROOT, "cheats.txt"), encoding="utf-8"):
        line = line.split("#")[0].strip()
        if line.startswith("["):
            name, gate = line[3:].strip(), None
            continue
        parts = line.split()
        if not name or len(parts) != 2 or len(parts[0]) != 8:
            continue
        op, addr = int(parts[0][:2], 16), 0x80000000 | int(parts[0][2:], 16)
        if 0xD0 <= op <= 0xD3 or 0xE0 <= op <= 0xE3:
            gate = (addr, gate_games(op, addr, int(parts[1], 16)))
            continue
        (gaddr, games), gate = gate or (None, None), None
        if op not in (0x30, 0x80):
            continue
        n = 1 if op == 0x30 else 2
        if games is not None and len(games) < 2:
            if not games:
                errors.append("cheats.txt '%s': gate 0x%08X passes on neither "
                              "game, so 0x%08X is never written" % (name, gaddr, addr))
            cheat_gated.append((name, addr, "%s only" % games.pop() if games else "never", gaddr))
            continue
        if any(d <= addr < d + ln for _, d, ln in RELOCATED):
            cheat_conflicts.setdefault(name, (addr, "1.5 relocated code"))
        elif addr < vexe.end and exe_changed(addr, addr + n) is not None:
            cheat_conflicts.setdefault(name, (addr, "original image changed"))
        elif vexe.end <= addr < pexe.end:
            # Every byte here is copied out by the stub, zero or not.
            cheat_staging.setdefault(name, addr)

    # Report.
    print("vanilla exe 0x%08X..0x%08X, 1.5 exe 0x%08X..0x%08X"
          % (vexe.t_addr, vexe.end, pexe.t_addr, pexe.end))
    changed = sum(vexe.text[i] != pexe.text[i] for i in range(len(vexe.text)))
    print("%d bytes of the original image differ\n" % changed)
    # A feature with a 1.5 edition (edition_of) gives way to it by itself while 1.5 is on.
    editions = {f["edition_of"]: "%s/%s" % (pkg, f["id"])
                for pkg, m in manifests.items() if pkg not in NOT_SHIPPED
                for f in m.get("feature", [])
                if f.get("edition_of") and "%s/%s" % (pkg, f["id"]) in only15}
    for owner in features:
        why = reasons.get(owner)
        if not why:
            print("  ok        %s" % owner)
            if owner in editions:
                errors.append("%s has a 1.5 edition but runs with 1.5 as it is" % owner)
            continue
        if owner in editions:
            print("  edition   %s  (on 1.5 runs %s instead; %d writes differ)"
                  % (owner, editions[owner], len(why)))
            continue
        print("  CONFLICT  %s  (%d)" % (owner, len(why)))
        for w in why[:3]:
            print("              %s" % w)
        if len(why) > 3:
            print("              ... %d more" % (len(why) - 3))
    for owner, (n, bad) in only15.items():
        if bad:
            errors.append("%s: %d of %d patches fail on 1.5, first %s"
                          % (owner, len(bad), n, bad[0]))
        else:
            print("  ok (1.5)  %s  (needs dw15; %d guards match 1.5)" % (owner, n))
    stray = sorted(set(reasons) - set(features))
    for owner in stray:
        print("  CONFLICT  %s  (owner has no manifest feature): %s"
              % (owner, reasons[owner][0]))

    # Cheats are not guarded writes and cheats.txt applies with the cheats
    # feature off too, so they are reported, never auto-disabled.
    print("\ncheats.txt (reported only):")
    if not cheat_conflicts and not cheat_staging:
        print("  no ungated cheat pokes a byte 1.5 changed")
    for nm, (addr, why) in cheat_conflicts.items():
        print("  changed  0x%08X  %s  (%s)" % (addr, nm, why))
    for nm, addr, games, gaddr in cheat_gated:
        print("  gated    0x%08X  %s  (%s, gate 0x%08X)" % (addr, nm, games, gaddr))
    if cheat_staging:
        print("  %d cheat(s) poke 1.5's boot staging block 0x%08X..0x%08X;"
              % (len(cheat_staging), vexe.end, pexe.end))
        print("  safe only because psx_cheats.c waits for the game's entry "
              "point:")
        for nm, addr in cheat_staging.items():
            print("           0x%08X  %s" % (addr, nm))

    off = [o for o in features if o in reasons and o not in editions]
    print("\nturn off with 1.5 (%d):" % len(off))
    for o in off:
        print("  " + o)

    # What the manifests declare: dw15's list plus features naming dw15.
    declared = set(next(f for f in manifests[SELF]["feature"]
                        if f["id"] == "dw15").get("conflicts", []))
    for pkg, m in manifests.items():
        for feat in m.get("feature", []):
            if DW15 in feat.get("conflicts", []):
                declared.add("%s/%s" % (pkg, feat["id"]))
    errors += ["%s conflicts with 1.5 but no manifest declares it" % o
               for o in off if o not in declared]
    errors += ["%s is declared as a 1.5 conflict but runs with 1.5" % o
               for o in sorted(declared - set(off))]
    print("\nmanifests: %s" % ("declared conflicts match, 1.5-only guards match"
                                if not errors else "%d problem(s)" % len(errors)))
    for e in errors:
        print("  error: " + e)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
