#!/usr/bin/env python3
"""Build the Abandoned Factory: four authored screens on free map rows 240-243, files and scripts from MMM.DAT.

An abandoned factory whose machines still run, walked in a line: AFAC1 the entry hall, AFAC2 and AFAC3 two
production floors, AFAC4 the boss chamber. Each screen is a scene of boxes, cylinders and ellipsoids turned into
art, collision and MAP/TFS files by newmap_engine.py, as tools/gen_newmap.py does for the glade. The art is the
flat guide until painted art arrives; no game art or data goes into the package.

Every screen keeps the glade's elevated camera: you enter at the bottom (-z) and the way on is at the top (+z).
Warp slots are the same on each screen: slot 0 is the arrival walking forward and cell 110 leads back (to the
previous screen's exit 2; on AFAC1 to the host's new exit); slot 1 says where a save resumes, the host gate's
spawn (no cell is 111, so it never fires); slot 2 is the arrival walking back and cell 112 leads on to the next
screen's exit 0. AFAC4 has no slot 2.

The files go in the unused MMM.DAT area (docs/GAME.md 2.3) under names the game's file table does not have:
dw_newmaps.c serves the names in dw_newmaps_files.inc, which this script owns and writes. Their DG scripts take
new table rows 223-226, placed after the map files in MMM.DAT. The remaining writes are guarded patches: the four
MAP_ENTRIES rows, the DG table rows, the MAPHEAD sections (after the glade's) and their pointers, and the
entrance: a new exit on a Gear Savanna screen (HOST).

    py -3.12 tools/gen_factory.py [--bin disc.bin] [--build build-debug] [--preview] [--check]

Writes the package mods/preloaded/packages/dw.newmaps.factory/1.0.0/ (commit it, then update the MOD_PAYLOADS
hashes in verify_setup_package.py), dw_newmaps_files.inc, and build/newmap/factory/: per-screen previews and
spec.json, which verify_factory.py reads. --preview renders the previews only (no disc); --check builds in
memory and compares the manifest, the assets and dw_newmaps_files.inc with what is committed.
"""
import argparse
import functools
import hashlib
import json
import os
import struct
import sys
import tomllib
from collections import deque

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extract_disc_file import parse_record, read_extent, sector, walk  # noqa: E402
import newmap_engine as E  # noqa: E402
from newmap_engine import MAX_FILE, Screen, cell_centre, deep_floor, mix, noise, sectors, tile_of  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE, FEATURE = "dw.newmaps.factory", "factory"
ROWS = (240, 241, 242, 243)
NAMES = ("AFAC1", "AFAC2", "AFAC3", "AFAC4")
SCRIPTS = (223, 224, 225, 226)        # DG table rows: table[223..227], each row's end the next row's start
MMM_LBA, MMM_SECTORS = 148668, 13500  # docs/GAME.md 2.3
MAP_ENTRIES = 0x801292D4
FILE_OFFSET_TABLE = 0x8011FE1C
FLAGS = 0xC8                          # sound bank 8, no time of day (so one CLUT), map-Digimon records
LOADING_ROW, LOADING_NAME = 69, 30    # the arrival card says Gear Savanna: row 69's loadingName (asserted)
BGM_ROW, BGM, BGM_PSTAT = 152, 0x16, 0    # FACT01's music: its 5D operand and pstat 0xF5 (asserted)
MH_BUFFER, MH_BUDGET = 0x61A8, 180    # MAPHEAD_SCRIPT_BUFFER; the factory's sections must also fit 1.5's tail
PREVIEWS = os.path.join(ROOT, "build-debug", "newmap", "factory")   # main() follows --build
STOCK_FILLER = bytes.fromhex("fb00b400")  # a free row's MAPHEAD section binds script 180 (docs/GAME.md 3.11)

# The entrance. Gear Savanna's dead-end screen with the signs (GIAS09): past the campfire and its bucket the grass
# runs on between the camp and the boulders toward the open savanna, where the stock map walls you off and no warp
# goes. The gate faces east, so its two-cell strip is two columns: stock wall at column 54 (the wall test runs a
# radius ahead, so the floor column alone would never fire), stock floor at 53, rows 30-35.
HOST = dict(file="GIAS09", row=78, warp=1,          # the free MapWarps slot the new exit takes
            cells=((54, 53), range(30, 36)),        # (wall column, floor column), rows
            spawn=(50, 0, 1750, 0x400),             # coming back: x, y, z, rotation (0x400 faces west, away)
            save_exit=0)                            # the stock exit a card names instead (k_maps in dw_newmaps.c)

PAINT = (6, 5)                       # the frame the art will be painted in, as the glade's ...
GRID = (6, 4)                        # ... and the backdrop, its centre crop: 24 tiles
VP = np.array([0.0, -5600.0, -7400.0])    # the glade's camera
VR = np.array([0.0, 0.0, -1500.0])
H_PROJ = 927
LIGHTS = [(0, 170, -30, 150, 150, 160), (1, 1, 1, 0, 0, 0), (1, 1, 1, 0, 0, 0)]   # a cooler key light than the glade's
REGIONS = (3, -1, -1, -1, -1, -1, -1, -1)  # A0 = machines (docs/GAME.md 3.3)

LABELS = ("floor", "grate", "machine", "pipe", "wall", "crate")
GROUND = (0, 1)
MATS = dict(machine=2, pipe=3, wall=4, crate=5)
FLAT = dict(machine=(112, 104, 88), pipe=(92, 96, 100), wall=(84, 80, 76), crate=(132, 104, 64))

# the hall every screen stands in. This camera's backdrop shows the floor from z -4000 (bottom edge) to about
# z 1950, where a character's head reaches the top edge; the frame is 2350 units either side of x = 0 at the
# bottom and 4150 at z 2000, so the side walls come into view from about z -600.
HX = 3600                            # the side walls' inner faces
ZB = 1500                            # the back wall's front face; it is 400 thick, its doorway the way on
WALL_H = 1400
ENTRY_X = 0                          # the way back: the bottom edge, around x = 0
SPAWN0 = (0, 0, -3500)               # arrival walking forward, facing +z (0x800)
BACK_STRIP_Z = -3750                 # floor nearer the camera than this, round ENTRY_X, leads back
SPAWN2_Z = 1250                      # arrival walking back, facing -z (0): two rows clear of the forward strip


# ---- primitives --------------------------------------------------------------
def box(x, z, w, d, h, mat, y0=0.0, tone=0.5):
    """A box w wide (x), d deep (z), h tall, its base y0 above the floor."""
    return dict(kind="box", c=(x, -(y0 + h / 2), z), r=(w / 2, h / 2, d / 2), mat=mat, tone=tone, group=mat)


def cyl(x, z, r, h, mat, y0=0.0, tone=0.5):
    return dict(kind="cyl", c=(x, z), r=r, y0=-(y0 + h), y1=-y0, mat=mat, tone=tone, group=mat)


def ell(x, y, z, rx, ry, rz, mat, tone=0.5):
    return dict(kind="ell", c=(x, -y, z), r=(rx, ry, rz), mat=mat, tone=tone, group=mat)


def hall(door):
    """Side walls, the back wall (with a doorway `door` = (x, half width), or solid), and pipes on the walls."""
    p = [box(-HX - 250, -1550, 500, 6900, WALL_H, "wall", tone=0.3), box(HX + 250, -1550, 500, 6900, WALL_H, "wall", tone=0.3)]
    lo, hi = -HX - 500, HX + 500
    if door is None:
        p.append(box(0, ZB + 200, hi - lo, 400, WALL_H, "wall", tone=0.4))
    else:
        x, hw = door
        p += [box((lo + x - hw) / 2, ZB + 200, x - hw - lo, 400, WALL_H, "wall", tone=0.4),
              box((x + hw + hi) / 2, ZB + 200, hi - x - hw, 400, WALL_H, "wall", tone=0.4)]
    for y0, tone in ((880, 0.2), (1060, 0.7)):     # two pipe runs along the back wall, out of reach
        p.append(box(0, ZB - 70, hi - lo, 70, 70, "pipe", y0=y0, tone=tone))
    return p


def machine(x, z, w, d, h, tone=0.5, stacks=()):
    """A machine body with exhaust stacks (x offset, radius, height) on top, kept inside the body's footprint."""
    p = [box(x, z, w, d, h, "machine", tone=tone)]
    for dx, r, sh in stacks:
        assert abs(dx) + r + 20 <= w / 2 and r + 20 <= d / 2, "a stack must stay inside its machine's footprint"
        p.append(cyl(x + dx, z, r, sh, "pipe", y0=h, tone=0.6))
    return p


# ---- the four scenes -----------------------------------------------------------
# Each scene: props, floor grates (x0, z0, x1, z1), the doorway (x, half width) or None, three walk spots, and for
# the production floors the line of machinery: its z extent and the gap's edges (x0, x1), exactly 3 cells apart.
def entry_hall():
    """Open floor and idle machinery along the walls: no chokepoint. The way on is a wide doorway, back left."""
    door = (-900, 450)
    p = hall(door)
    p += machine(-2950, -250, 700, 800, 560, 0.3, ((0, 140, 420),))         # two stopped presses on the left
    p += machine(-2950, 800, 700, 800, 640, 0.6, ((-120, 110, 300), (140, 110, 520)))
    p += [cyl(2950, 900, 400, 1150, "machine", tone=0.7), ell(2950, 1150, 900, 410, 160, 410, "machine", 0.7),
          cyl(3000, -250, 340, 950, "machine", tone=0.5), ell(3000, 950, -250, 350, 140, 350, "machine", 0.5)]  # tanks
    p += [box(2500, -1900, 500, 1300, 240, "machine", tone=0.2)]           # a dead conveyor bed, bottom right
    p += [box(-1900, -2900, 380, 380, 380, "crate", tone=0.3), box(-1480, -2950, 380, 380, 300, "crate", tone=0.7),
          box(-1750, -2880, 340, 340, 340, "crate", y0=380, tone=0.5), box(1500, -3250, 420, 420, 420, "crate", tone=0.4)]
    grates = [(-300, -3300, 300, 900), (2350, 300, 3550, 1450)]             # a drain down the middle, under a tank
    walk = [(-1700, -1400), (1300, 300), (700, -2500)]
    return p, grates, door, walk, None


def production_floor_1():
    """A line of presses across the back, one gap right of centre, and the doorway straight behind it."""
    gap = (300, 800)                                                        # centres 450, 550, 650 walk
    z, d = 600, 400
    door = ((gap[0] + gap[1]) / 2, 300)
    p = hall(door)
    p += machine(-2900, z, 2000, d, 520, 0.3, ((-300, 130, 520),))       # from inside the wall to -1900
    p += machine(-1450, z, 900, d, 420, 0.6, ((0, 120, 360),))
    p += machine((-1000 + gap[0]) / 2, z, gap[0] + 1000, d, 480, 0.45)
    p += machine((gap[1] + 2100) / 2, z, 2100 - gap[1], d, 500, 0.55, ((-250, 130, 600), (250, 130, 420)))
    p += machine((2100 + HX + 300) / 2, z, HX + 300 - 2100, d, 440, 0.35)
    p += [box(-2050, -1700, 420, 420, 420, "crate", 0.4), box(-2050, -1250, 420, 420, 340, "crate", 0.7),
          box(2550, -900, 360, 900, 300, "machine", tone=0.25)]             # crates; a workbench
    grates = [(-1700, -900, -700, 100), (1000, -2600, 2000, -1600)]
    walk = [(-1300, -400), (1500, -1100), (-200, -2300)]
    return p, grates, door, walk, dict(z=(z - d / 2, z + d / 2), gap=gap)


def production_floor_2():
    """A line of furnaces and tanks across the middle, one gap left of centre, a back yard and the doorway behind."""
    gap = (-1100, -600)                                                     # centres -950, -850, -750 walk
    z, d = -300, 400
    door = ((gap[0] + gap[1]) / 2, 300)
    p = hall(door)
    p += [box((-HX - 300 + gap[0]) / 2, z, gap[0] + HX + 300, d, 300, "machine", tone=0.35)]   # tank cradle
    p += [cyl(-2700, z, 160, 900, "machine", y0=300, tone=0.6), cyl(-1900, z, 160, 800, "machine", y0=300, tone=0.45),
          ell(-2700, 1200, z, 170, 80, 170, "machine", 0.6), ell(-1900, 1100, z, 170, 80, 170, "machine", 0.45)]
    p += machine((gap[1] + 1200) / 2, z, 1200 - gap[1], d, 620, 0.5, ((-500, 140, 700), (300, 140, 900)))   # furnaces
    p += machine((1200 + HX + 300) / 2, z, HX + 300 - 1200, d, 560, 0.3, ((-600, 140, 800), (400, 140, 600)))
    p += [box(1800, 1050, 400, 400, 400, "crate", 0.5), box(2250, 1050, 400, 400, 300, "crate", 0.2),   # the back yard
          box(-2600, 1000, 600, 500, 260, "machine", tone=0.7)]
    p += [box(1700, -3000, 400, 400, 380, "crate", 0.8), box(-1900, -2200, 500, 1000, 280, "machine", tone=0.2)]
    grates = [(-500, -3300, 500, -1400), (1200, -2000, 2400, -1000)]
    walk = [(1500, -1300), (-1100, -2900), (400, -2400)]
    return p, grates, door, walk, dict(z=(z - d / 2, z + d / 2), gap=gap)


ARENA = (0, 600)                     # the boss chamber's open floor: where Machinedramon (radius 400) will stand
ARENA_REACH = 4000                   # its distance from spawn 0: the boss's tracking range + 300 must stay below it


def boss_chamber():
    """Sparse: two great dormant machines in the back corners, a grate in an open floor, no way on."""
    p = hall(None)
    p += machine(-2700, 900, 1400, 1000, 1100, 0.3, ((-300, 180, 900), (300, 180, 700)))
    p += machine(2700, 900, 1400, 1000, 1000, 0.55, ((0, 220, 1100),))
    p += [cyl(-2400, -2200, 200, 1300, "pipe", tone=0.4), cyl(2400, -2200, 200, 1300, "pipe", tone=0.6)]
    grates = [(-1300, ARENA[1] - 1000, 1300, ARENA[1] + 700)]
    walk = [(-1500, -1000), (1500, -1000), (0, -2300)]
    return p, grates, None, walk, None


SCENES = (entry_hall, production_floor_1, production_floor_2, boss_chamber)


# ---- the screens -------------------------------------------------------------
def in_rects(x, z, rects):
    m = np.zeros(np.shape(x), bool)
    for x0, z0, x1, z1 in rects:
        m |= (x >= x0) & (x <= x1) & (z >= z0) & (z <= z1)
    return m


def in_door(x, z, door):
    return np.zeros(np.shape(x), bool) if door is None else (np.abs(x - door[0]) < door[1]) & (z >= ZB - 10) & (z < ZB + 400)


def floor_fn(door):
    return lambda x, z: ((np.abs(x) < HX) & (z < ZB)) | in_door(x, z, door)


def void(x, z, door):
    """Outside the hall, and the dark beyond the doorway."""
    return ~(((np.abs(x) < HX + 500) & (z < ZB + 400)) | in_door(x, z, door))


def albedo_fn(grates, door):
    def albedo(x, z):
        """Flat guide colours: worn concrete plates with dark seams and oil stains, grates, the void."""
        col = np.zeros(np.shape(x) + (3,))
        col[:] = (114, 112, 104)
        seam = (np.abs((x + 200) % 400 - 200) > 188) | (np.abs((z + 200) % 400 - 200) > 188)
        col[seam] = (86, 84, 78)
        col = mix(col, np.array([88, 80, 66]), np.clip((noise(x, z, 900, 4, 31) - 0.58) / 0.08, 0, 1) * 0.7)
        g = in_rects(x, z, grates)
        col[g] = np.where(((x[g] % 60) < 24)[..., None], (46, 48, 50), (70, 72, 74))
        col[void(x, z, door)] = (30, 29, 28)
        return col
    return albedo


def floor_label_fn(grates, door):
    def label(x, z):
        lab = np.zeros(np.shape(x), np.uint8)
        lab[in_rects(x, z, grates)] = 1
        lab[void(x, z, door)] = 4                    # dark beyond the walls: never ground
        return lab
    return label


def strip_back(x, z):
    return (z < BACK_STRIP_Z) & (np.abs(x - ENTRY_X) < 350)


def warps(i):
    w = np.zeros((6, 10), np.int16)
    w[4:, :] = -1
    back = (HOST["row"], HOST["warp"]) if i == 0 else (ROWS[i - 1], 2)
    w[:, 0] = SPAWN0 + (0x800,) + back
    w[:, 1] = HOST["spawn"] + (HOST["row"], HOST["save_exit"])     # where a save resumes: dw_newmaps.c reads this
    if i < 3:
        door = SCENES[i]()[2]
        w[:, 2] = (door[0], 0, SPAWN2_Z, 0, ROWS[i + 1], 0)
    return w


def make_screen(i):
    _, grates, door, _, _ = SCENES[i]()
    strips = {110: strip_back}
    if door is not None:
        strips[112] = lambda x, z, door=door: in_door(x, z, door) & (z > ZB)
    return Screen(name=NAMES[i].lower(), row=ROWS[i], vp=VP, vr=VR, h=H_PROJ, grid=GRID, paint=PAINT, lights=LIGHTS,
                  labels=LABELS, ground=GROUND, mats=MATS, flat=FLAT, fg_mats=("machine", "pipe"), painted_props=(),
                  floor=floor_fn(door), strips=strips, warps=warps(i), records=[], regions=REGIONS,
                  albedo=albedo_fn(grates, door), floor_label=floor_label_fn(grates, door))


SCREENS = [make_screen(i) for i in range(4)]


def cell(x, z):
    tx, tz = tile_of(x, z)
    return tz, tx


def reach(grid, start, blocked=()):
    """Cells reachable from `start` (row, col) over walkable cells, 4-connected, not entering `blocked`."""
    seen, todo, blocked = {start}, deque([start]), set(blocked)
    while todo:
        r, c = todo.popleft()
        for n in ((r + 1, c), (r - 1, c), (r, c + 1), (r, c - 1)):
            if 0 <= n[0] < 100 and 0 <= n[1] < 100 and n not in seen and n not in blocked and grid[n] != 0x80:
                seen.add(n)
                todo.append(n)
    return seen


def partner_at(x, z, rot):
    """Where a warp puts the partner (tamer.c loadMapEntities; dw_newmaps.c move_save_to_host)."""
    if rot <= 0x200 or rot > 0xE00:
        return x, z + 200
    return (x + 200, z) if rot <= 0x600 else (x, z - 200) if rot <= 0xA00 else (x - 200, z)


def check_screen(i, grid):
    """Every spawn (and the partner beside it) is floor, every strip is reachable from both, and on a production
    floor the gap is exactly 3 cells wide and the only way to the forward strip."""
    scr = SCREENS[i]
    s0 = cell(SPAWN0[0], SPAWN0[2])
    spots = [(SPAWN0[0], SPAWN0[2], 0x800)]
    if i < 3:
        spots.append((int(scr.warps[0, 2]), SPAWN2_Z, 0))
    for x, z, rot in spots:
        r, c = cell(x, z)
        assert grid[r, c] == 0 and grid[cell(*partner_at(x, z, rot))] == 0, \
            "%s: the spawn at (%d, %d) and the partner's spot must be floor" % (NAMES[i], x, z)
        near = grid[max(r - 2, 0):r + 3, max(c - 2, 0):c + 3]
        assert not ((near >= 110) & (near < 120)).any(), "%s: an arrival must stay 2 cells off the exit strips" % NAMES[i]
    if i == 3:                                       # room for the boss, out of reach of the arrival
        r, c = cell(*ARENA)
        assert (grid[r - 6:r + 7, c - 6:c + 7] == 0).all() and np.hypot(ARENA[0] - SPAWN0[0], ARENA[1] - SPAWN0[2]) >= ARENA_REACH, \
            "AFAC4 needs a wide open floor far from spawn 0"
    seen = reach(grid, s0)
    for value in scr.strips:
        cells = set(zip(*np.nonzero(grid == value)))
        assert cells and cells <= seen, "%s: strip %d must be reachable floor" % (NAMES[i], value)
    if i < 3:
        assert cell(scr.warps[0, 2], SPAWN2_Z) in seen, "%s: spawn 2 must be reachable from spawn 0" % NAMES[i]
    line = SCENES[i]()[4]
    if line:
        rows = [r for r in range(100) if line["z"][0] <= cell_centre(0, r)[1] <= line["z"][1]]
        want = set(np.nonzero(grid[rows[len(rows) // 2]] != 0x80)[0])
        assert len(want) == 3 and max(want) - min(want) == 2 and \
            all(line["gap"][0] < cell_centre(c, 0)[0] < line["gap"][1] for c in want), \
            "%s: the gap must be exactly 3 cells wide (walkable columns %s)" % (NAMES[i], sorted(want))
        for r in rows:
            got = set(np.nonzero(grid[r] != 0x80)[0])
            assert got == want, "%s: row %d across the line has walkable columns %s, not the gap's %s" % (NAMES[i], r, sorted(got), sorted(want))
        assert not set(zip(*np.nonzero(grid == 112))) & reach(grid, s0, [(r, c) for r in rows for c in want]), \
            "%s: the gap must be the only way to the forward strip" % NAMES[i]


@functools.lru_cache(None)
def built(i):
    """(prims, grid, MAP bytes, TFS bytes, palette indices, day CLUT) of screen i, from the flat guide."""
    scr, prims = SCREENS[i], SCENES[i]()[0]
    grid, _ = E.collision(scr, prims)
    rgb = E.guide(scr, prims)
    idx, day = E.quantise(rgb)
    assert not (day[idx] == 0).any()
    try:
        check_screen(i, grid)
    except AssertionError:                           # show what failed
        path = os.path.join(PREVIEWS, "failed_%s.png" % scr.name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        Image.fromarray(overlay(scr, grid, pal(day)[idx])).save(path)
        print("collision overlay of the failed screen: %s" % path)
        raise
    mapb, tfsb = E.build_map(scr, grid), E.build_tfs(scr, idx, [day])
    assert len(mapb) <= MAX_FILE and len(tfsb) <= MAX_FILE
    return prims, grid, mapb, tfsb, idx, day


def dg_script(i):
    """Screen i's DG script: the section table and one arrival section (0xFE), a bare END for now."""
    s = struct.pack("<4H", 8, 0xFE, 8, 0xFFFF) + b"\xfe\x00"
    assert len(s) <= 0x4000
    return s


def section(i):
    return (bytes([0xFB, 0]) + struct.pack("<HH", SCRIPTS[i], ROWS[i]) + bytes([0x1E, 0, 0xF5, BGM_PSTAT, 0x5D, BGM])
            + b"\xfe\x00")


def mh_sections():
    """The four MAPHEAD row sections, back to back: bind the script, set the music, end."""
    return b"".join(section(i) for i in range(4))


def layout():
    """[(name, MMM.DAT sector, bytes)]: AFAC1.MAP, AFAC1.TFS, ... AFAC4.TFS, then the DG scripts, sector-aligned."""
    out, at = [], 0
    for i in range(4):
        for ext, blob in (("MAP", built(i)[2]), ("TFS", built(i)[3])):
            out.append(("%s.%s" % (NAMES[i], ext), at, blob))
            at += sectors(blob)
    for i in range(4):
        out.append(("%s.DG" % NAMES[i], at, dg_script(i)))
        at += sectors(dg_script(i))
    assert at <= MMM_SECTORS
    return out


# ---- previews and the test's spec ------------------------------------------------
def pal(c):
    return np.stack([(c & 31) << 3, ((c >> 5) & 31) << 3, ((c >> 10) & 31) << 3], -1).astype(np.uint8)


def overlay(scr, grid, rgb):
    """The backdrop with a dot per walkable cell (green floor, blue strip) and the spawns (yellow)."""
    ov = rgb.astype(float)
    tz, tx = np.nonzero(grid != 0x80)
    for cx, cz, g in zip(tx, tz, grid[tz, tx]):
        x, z = cell_centre(cx, cz)
        u, v = E.project(scr, (x, 0, z))
        if 0 <= u < scr.bw and 0 <= v < scr.bh:
            ov[int(v) - 1:int(v) + 2, int(u) - 1:int(u) + 2] = (0, 255, 0) if g == 0 else (0, 128, 255)
    for w in (0, 2):
        if scr.warps[4, w] >= 0:
            u, v = E.project(scr, (scr.warps[0, w], 0, scr.warps[2, w]))
            if 0 <= u < scr.bw and 0 <= v < scr.bh:
                ov[int(v) - 3:int(v) + 4, int(u) - 3:int(u) + 4] = (255, 255, 0)
    return ov.astype(np.uint8)


def previews(out):
    """Per screen: guide.png (the painter's input, paint frame), backdrop_day.png, collision_overlay.png; spec.json."""
    spec = dict(host=dict(row=HOST["row"], exit=HOST["warp"], spawn=HOST["spawn"][:3], save_exit=HOST["save_exit"],
                          file=HOST["file"]), screens=[])
    for i, scr in enumerate(SCREENS):
        prims, grid, mapb, tfsb, idx, day = built(i)
        d = os.path.join(out, scr.name)
        os.makedirs(d, exist_ok=True)
        Image.fromarray(E.guide(scr, prims, frame=PAINT).astype(np.uint8)).save(os.path.join(d, "guide.png"))
        Image.fromarray(pal(day)[idx]).save(os.path.join(d, "backdrop_day.png"))
        Image.fromarray(overlay(scr, grid, pal(day)[idx])).save(os.path.join(d, "collision_overlay.png"))
        _, _, door, walk_at, line = SCENES[i]()
        via = []
        if line:                                     # straight through the gap's middle column
            gx = (line["gap"][0] + line["gap"][1]) // 2
            via = [(gx, int(line["z"][0]) - 400), (gx, int(line["z"][1]) + 300)]
        spec["screens"].append(dict(
            row=scr.row, name=NAMES[i], spawn=[[int(v) for v in scr.warps[:3, w]] if scr.warps[4, w] >= 0 else None
                                               for w in range(3)],
            strip_back=(ENTRY_X, BACK_STRIP_Z - 150), strip_fwd=(int(door[0]), ZB + 250) if door else None, via=via,
            walk=[deep_floor(grid, w) for w in walk_at],
            camera=dict(vp=VP.tolist(), vr=VR.tolist(), h=H_PROJ, gw=GRID[0], gh=GRID[1]),
            backdrop=os.path.join(d, "backdrop_day.png")))
        print("%s: walkable cells %d, strips %s, colours %d, MAP %d B, TFS %d B (%d sectors)"
              % (NAMES[i], (grid == 0).sum(), {v: int((grid == v).sum()) for v in scr.strips}, len(np.unique(idx)),
                 len(mapb), len(tfsb), sectors(mapb) + sectors(tfsb)))
    with open(os.path.join(out, "spec.json"), "w") as fh:
        json.dump(spec, fh, indent=1)


# ---- package ---------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", default=os.path.join(ROOT, "disc", "Digimon World (USA).bin"))
    ap.add_argument("--build", default=os.path.join(ROOT, "build-debug"))
    ap.add_argument("--preview", action="store_true", help="render previews only")
    ap.add_argument("--check", action="store_true", help="build in memory and compare with the committed package")
    a = ap.parse_args()
    global PREVIEWS
    PREVIEWS = os.path.join(a.build, "newmap", "factory")
    if not a.check:
        previews(PREVIEWS)
    if a.preview:
        return 0

    f = open(a.bin, "rb")
    listing = []
    rr = parse_record(sector(f, 16)[156:190])
    walk(f, rr["lba"], rr["size"], "", listing)
    files = {os.path.basename(n): (lba, size) for n, lba, size in listing}
    rd = lambda n: read_extent(f, *files[n])
    hexs = lambda b: " ".join("%02x" % x for x in b)
    exe = rd("SLUS_010.32")
    ram = lambda addr: addr - struct.unpack_from("<I", exe, 0x18)[0] + 0x800      # PS-X EXE: text at 0x800
    cstr = lambda addr: exe[ram(addr):exe.index(b"\0", ram(addr))].decode("ascii")

    # names: in neither the file table nor the ISO directory, so the hook only ever serves our own
    table, k = set(), 0
    while struct.unpack_from("<2I", exe, ram(FILE_OFFSET_TABLE) + 12 * k) != (0, 0):    # a sector 0, size 0 row ends it
        table.add(cstr(struct.unpack_from("<I", exe, ram(FILE_OFFSET_TABLE) + 12 * k + 8)[0]).split("\\")[-1])
        k += 1
    assert "MGEN17.MAP" in table and len(table) > 800, "FILE_OFFSET_TABLE not read"
    lay = layout()
    for name, _, _ in lay:
        assert name not in table and name not in files, "%s is already a file name on the disc" % name
    assert files["MMM.DAT"] == (MMM_LBA, MMM_SECTORS * 2048)
    used = lay[-1][1] + sectors(lay[-1][2])
    for s in range(used):
        assert not any(sector(f, MMM_LBA + s)), "MMM.DAT sector %d is not zero" % s
    # rows
    for row in ROWS:
        assert not any(exe[ram(MAP_ENTRIES + row * 16):][:16]), "MAP_ENTRIES row %d is not stock zeros" % row
    assert exe[ram(MAP_ENTRIES + LOADING_ROW * 16) + 15] == LOADING_NAME, "Gear Savanna's loadingName moved"
    # DG rows 223..226 in MMM.DAT, after the map files
    dgd, dg_lba = rd("DG.SCN"), files["DG.SCN"][0]
    assert struct.unpack_from("<I", dgd, 222 * 4)[0] == files["DG.SCN"][1], "table[222] must be DG.SCN's size"
    dgs = lay[8:]
    offs = [(MMM_LBA + s - dg_lba) * 2048 for _, s, _ in dgs] + [(MMM_LBA + used - dg_lba) * 2048]
    for k in range(4):
        assert dgs[k][0] == NAMES[k] + ".DG" and len(dgs[k][2]) <= 0x4000
        assert offs[k + 1] - offs[k] == sectors(dgs[k][2]) * 2048, "DG rows must be consecutive, each ending where the next starts"
    # MAPHEAD: the factory's sections start where the glade's ends (its manifest), rounded up to even
    gdir = os.path.join(ROOT, "mods", "preloaded", "packages", "dw.newmaps", "1.0.0")
    gm = tomllib.load(open(os.path.join(gdir, "manifest.toml"), "rb"))
    mh, mh_base = rd("MAPHEAD.SCN"), files["MAPHEAD.SCN"][0] * 2048
    gsec = next(p for p in gm["patch"] if p["feature"] == "glade" and p["target"] == "disc_user"
                and p["replace"].startswith("fb 00"))
    mh_at = gsec["offset"] - mh_base + len(bytes.fromhex(gsec["replace"]))
    mh_at += mh_at & 1
    sections = mh_sections()
    assert len(sections) <= MH_BUDGET and mh_at + len(sections) <= MH_BUFFER, "the sections must fit the MAPHEAD tail"
    tail = b"".join(sector(f, files["MAPHEAD.SCN"][0] + s) for s in range((mh_at + len(sections)) // 2048 + 1))
    assert len(mh) <= mh_at and not any(tail[len(mh):mh_at + len(sections)]), "the MAPHEAD tail past EOF is not zero"
    ptr = {}
    p = 2
    while struct.unpack_from("<H", mh, p)[0] != 0xFFFF:
        ptr[struct.unpack_from("<H", mh, p)[0]] = p
        p += 4
    for row in ROWS:
        o = struct.unpack_from("<H", mh, ptr[row] + 2)[0]
        assert mh[o:o + 4] == STOCK_FILLER, "row %d's MAPHEAD section is not the stock filler" % row
    o = struct.unpack_from("<H", mh, ptr[BGM_ROW] + 2)[0]
    body = mh[o:mh.index(b"\xfe\x00", o)]
    assert body[6:12] == bytes([0x1E, 0, 0xF5, BGM_PSTAT, 0x5D, BGM]) and body.count(b"\x5d") == 1, \
        "row %d's music is not one 5D %02x after pstat 0xF5 = %d" % (BGM_ROW, BGM, BGM_PSTAT)
    # the host: a free warp slot, a real stock exit for saves, a wall-then-floor strip, floor to land on
    hd, hbase = rd(HOST["file"] + ".MAP"), files[HOST["file"] + ".MAP"][0] * 2048
    assert cstr(MAP_ENTRIES + HOST["row"] * 16) == HOST["file"]
    hent, hcol = struct.unpack_from("<2i", hd, 4 * (struct.unpack_from("<i", hd)[0] // 4 - 2))
    field = lambda t, slot: hent + 2 * (10 * t + slot)
    hw = lambda t, slot: struct.unpack_from("<h", hd, field(t, slot))[0]
    assert [hw(t, HOST["warp"]) for t in range(6)] == [0, 0, 0, 0, -1, -1], "the host's warp slot must be free"
    assert hw(4, HOST["save_exit"]) >= 0 and (hw(0, HOST["save_exit"]), hw(2, HOST["save_exit"])) != (0, 0), \
        "the exit a save names must be a stock one"
    (wall_col, floor_col), host_rows = HOST["cells"]
    assert floor_col + 1 == wall_col, "the strip's two columns must be adjacent (one patch per row)"
    for r in host_rows:
        assert (hd[hcol + r * 100 + wall_col], hd[hcol + r * 100 + floor_col]) == (0x80, 0), "host row %d: wall then floor" % r
    hx, _, hz, hrot = HOST["spawn"]
    for x, z in ((hx, hz), partner_at(hx, hz, hrot)):
        tz, tx = cell(x, z)
        assert hd[hcol + tz * 100 + tx] == 0, "the return spawn and the partner's spot must be floor"
        assert tx <= floor_col - 1, "the return spawn must stay off the strip"
    E.assert_in_k_maps(open(os.path.join(ROOT, "dw_newmaps.c"), encoding="utf-8").read(), PACKAGE, ROWS[0], ROWS[-1],
                       HOST["row"], HOST["warp"], HOST["save_exit"])
    h = hashlib.sha256()
    f.seek(0)
    for block in iter(lambda: f.read(1 << 22), b""):
        h.update(block)
    disc_sha = h.hexdigest()
    assert disc_sha == gm["target"][0]["disc_sha256"], "the disc differs from the glade's"

    # the package
    pkg = os.path.join(ROOT, "mods", "preloaded", "packages", PACKAGE, "1.0.0")
    assets = {name.lower(): blob for name, _, blob in lay}
    out = ["# The Abandoned Factory. Generated by tools/gen_factory.py: regenerate rather than hand-editing, so every guard",
           "# stays byte-exact. assets/ is this port's own maps and scripts (no game art or data); regenerating means updating",
           "# MOD_PAYLOADS in tools/verify_setup_package.py.",
           "format_version = 13", 'id = "%s"' % PACKAGE, 'version = "1.0.0"', 'name = "New Maps: Abandoned Factory"',
           'author = "DigimonWorldRecomp"',
           'description = "A new area of four screens the original game never had, built from this port\'s own art and '
           'collision. No game data is in this package."',
           'resolver = "declarative"', 'save_compatibility = "isolated"', "",
           "[[target]]", 'game_id = "SLUS-01032"', 'disc_sha256 = "%s"' % disc_sha, "",
           "[[feature]]", 'id = "%s"' % FEATURE, 'name = "Abandoned Factory"',
           'description = "A new area to explore: an abandoned factory of four screens whose machines still run. Find it '
           'in Gear Savanna, on the screen with the signs and the campfire: walk east past the bucket, between the camp '
           'and the boulders. Needs the exact disc image listed in DISC.md. Not for Digimon World 1.5 yet."',
           'group = "New maps"', "default_enabled = false", 'conflicts = ["dw.1_5/dw15"]', "",
           "# dw_newmaps.c: serves the screens' file names (dw_newmaps_files.inc) to the game's file lookup.",
           "[[plugin]]", 'feature = "%s"' % FEATURE, 'id = "%s"' % PACKAGE, ""]
    for name, s, blob in lay:
        out += ["# %s at MMM.DAT sector %d (LBA %d)" % (name, s, MMM_LBA + s), "[[overlay]]", 'feature = "%s"' % FEATURE,
                'target = "disc_user"', "offset = %d" % ((MMM_LBA + s) * 2048), 'file = "assets/%s"' % name.lower(),
                'sha256 = "%s"' % hashlib.sha256(blob).hexdigest(), ""]
    patch = lambda target, where, old, new: ('target = "%s"\n%s = %s\nexpected = "%s"\nreplace = "%s"'
                                             % (target, "address" if target == "main_exe" else "offset",
                                                ("0x%08X" if target == "main_exe" else "%d") % where,
                                                hexs(old), hexs(new)))
    patches = []
    for i, row in enumerate(ROWS):
        entry = NAMES[i].encode().ljust(10, b"\0") + bytes([0, 0, FLAGS, 0, 0, LOADING_NAME])
        patches.append(("MAP_ENTRIES[%d] = %s" % (row, NAMES[i]), patch("main_exe", MAP_ENTRIES + row * 16, bytes(16), entry)))
    dg_base = files["DG.SCN"][0] * 2048
    patches.append(("DG table[%d..%d]: the screens' scripts in MMM.DAT" % (SCRIPTS[0], SCRIPTS[-1] + 1),
                    patch("disc_user", dg_base + SCRIPTS[0] * 4, dgd[SCRIPTS[0] * 4:SCRIPTS[0] * 4 + 20],
                          struct.pack("<5I", *offs))))
    patches.append(("MAPHEAD sections %d-%d at 0x%x" % (ROWS[0], ROWS[-1], mh_at),
                    patch("disc_user", mh_base + mh_at, bytes(len(sections)), sections)))
    at = mh_at
    for i, row in enumerate(ROWS):
        patches.append(("MAPHEAD section %d -> 0x%x" % (row, at),
                        patch("disc_user", mh_base + ptr[row] + 2, mh[ptr[row] + 2:ptr[row] + 4], struct.pack("<H", at))))
        at += len(section(i))
    new_slot = HOST["spawn"] + (ROWS[0], 0)                       # arrives at AFAC1's spawn 0
    for t, value in enumerate(new_slot):
        if value != hw(t, HOST["warp"]):
            patches.append(("entrance: %s warp %d field %d = %d" % (HOST["file"], HOST["warp"], t, value),
                            patch("disc_user", hbase + field(t, HOST["warp"]), hd[field(t, HOST["warp"]):field(t, HOST["warp"]) + 2],
                                  struct.pack("<h", value))))
    for r in host_rows:
        o = hcol + r * 100 + floor_col
        patches.append(("entrance: %s collision row %d, columns %d-%d" % (HOST["file"], r, floor_col, wall_col),
                        patch("disc_user", hbase + o, hd[o:o + 2], bytes([0x6E + HOST["warp"]]) * 2)))
    for why, body in patches:
        out += ["# " + why, "[[patch]]", 'feature = "%s"' % FEATURE, body, ""]
    manifest = "\n".join(out)
    inc = "".join('{"%s", %d, %d},\n' % (name, MMM_LBA + s, len(blob)) for name, s, blob in lay if not name.endswith(".DG"))
    inc_path, man_path = os.path.join(ROOT, "dw_newmaps_files.inc"), os.path.join(pkg, "manifest.toml")
    if a.check:
        bad = [n for n, blob in assets.items() if not os.path.exists(os.path.join(pkg, "assets", n))
               or open(os.path.join(pkg, "assets", n), "rb").read() != blob]
        if not os.path.exists(man_path) or open(man_path, encoding="utf-8").read().replace("\r\n", "\n") != manifest:
            bad.append("manifest.toml")
        if open(inc_path, encoding="utf-8").read().replace("\r\n", "\n") != inc:
            bad.append("dw_newmaps_files.inc")
        if bad:
            print("DIFFERS: %s" % ", ".join(bad))
            return 1
        print("factory package and dw_newmaps_files.inc identical to the committed ones")
        return 0
    os.makedirs(os.path.join(pkg, "assets"), exist_ok=True)
    for name, blob in assets.items():
        open(os.path.join(pkg, "assets", name), "wb").write(blob)
    with open(man_path, "w", newline="\n") as fh:
        fh.write(manifest)
    with open(inc_path, "w", newline="\n") as fh:
        fh.write(inc)
    print("wrote %s and dw_newmaps_files.inc" % pkg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
