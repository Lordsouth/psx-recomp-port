#!/usr/bin/env python3
"""Build an authored field map (our own art, collision and camera) for the free map row 239.

Everything the game loads for the new screen comes from the scene described in
scene() below and the backdrop painted over it: no game art or data is copied,
only numeric constants such as the character lights, set as the MAYO maps set
them. The scene drives all outputs, so they cannot drift apart:

  * a guide render: the scene ray-traced from the map's own game camera with the
    projection the engine uses (INTERNALS.md 3.1: u = 64W + h*Xc/Zc,
    v = 64H + h*Yc/Zc, world +Y down, floor y = 0) in flat colours, plus a
    per-pixel material label map. tools/paint_newmap.py paints the guide into
    finished pre-rendered-CG art with local models and writes art/newmaps/glade.png;
  * the backdrop: that painted art, checked against the label map (it must show
    ground where the scene has ground), quantised to one 255-colour 15-bit
    palette and tinted into the three time-of-day palettes the engine swaps
    between. Without painted art the flat guide is used (playable, plain);
  * the 100x100 collision grid: floor where the scene says ground, blocked under
    props, the pond and anything outside the painted frame, and blocked wherever
    the painting shows something other than ground under a character's feet;
  * the secret fight (BOSS, boss_script), in the entity block and DG script 118;
  * foreground sprites, cut from the art, so characters can walk behind foliage
    and trunks (backdrops always draw behind characters: anything without a
    sprite may not be walked behind);
  * the MAP setup block (camera, lights, tile grid) and its warp table.

The files replace the unused MGEN17.MAP/TFS sectors through two file-backed
[[overlay]]s, zero-padded so no stock byte of those files reaches RAM. The
remaining writes are small guarded patches: the row itself, the two files'
FILE_OFFSET_TABLE entries and ISO directory records, the row's MAPHEAD section
(the stock filler binds the sewer script), and the entrance: a new exit on
Native Forest's north edge (MAYO03), which takes no stock route away.
dw_newmaps.c keeps the row off every memory card.

    py -3.12 tools/gen_newmap.py [--bin disc.bin] [--build build-debug] [--art PNG] [--preview]

Writes the shipped package mods/preloaded/packages/dw.newmaps/1.0.0/ (commit it,
then update the MOD_PAYLOADS hashes in verify_setup_package.py) and build/newmap/
previews (guide.png is the painter's input). It reads your disc only to check
every guard; no game art or data goes into the assets. Rebuild to stage it into
build/mods/bundled, then verify_newmap.py checks the result in the game.
"""
import argparse
import hashlib
import json
import os
import re
import struct
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extract_disc_file import parse_record, read_extent, sector, walk  # noqa: E402
import newmap_engine as E  # noqa: E402
from newmap_engine import MAX_FILE, Screen, cell_centre, deep_floor, iso_record, mix, noise, sectors  # noqa: E402,F401

# ---- the screen ------------------------------------------------------------
ROW, FILE = 239, "MGEN17"            # free row; unused stock file slot we overlay
MAP_SECTORS, TFS_SECTORS = 6, 241    # MGEN17's own extents, adjacent on the disc: 247 sectors we lay out
MAP_ENTRIES = 0x801292D4
FILE_OFFSET_TABLE = 0x8011FE1C       # {sector, size, name*} per disc file: lookupFileTable resolves names here
FLAGS = 0x80                         # sound bank 0 (Native Forest), time of day on, map-Digimon format
LOADING_NAME = 0                     # "Native Forest" on the arrival card
SCRIPT = 118                         # DG slot we rewrite (boss_script). Stock it is one bare END section, which stays:
                                     # no arrival side effects, and row 122, the other map bound to it, plays on
BGM, BGM_PSTAT = 0x01, 0             # the Native Forest theme, day/night variants as MAYO01/02
MH_NEW, MH_BUFFER = 0x6070, 0x61A8   # MAPHEAD.SCN ends at 0x606E; MAPHEAD_SCRIPT_BUFFER size
# The entrance: on Native Forest's north edge a lit lane runs on between the bushes, over a notch of floor.
HOST, HOST_ROW, HOST_WARP = "MAYO03", 2, 4   # the host map and the free MapWarps slot the new exit takes
HOST_CELLS = ((22, 23), range(62, 68))       # collision rows and columns that become warp 4. Row 22 is stock wall:
                                             # the wall test runs a radius ahead, so row 23 alone would never fire
HOST_SPAWN = (1350, 0, 2350, 0)              # coming back: x, y, z, rotation (0 faces south, away from the exit)
HOST_SAVE_EXIT = 0                           # the stock exit a card names instead of slot 4 (dw_newmaps.c)
# The secret fight: at the end of the dirt path, turns to face you, and fights when you press confirm next to it.
# tools/fight_sim.py and verify_newmap.py's measure mode show how hard it is. No flags: it is back on every visit.
BOSS_SPECIES, BOSS_DG_SECTION = 60, 5        # H-Kabuterimon, which the game places nowhere
BOSS_AT = (350, 0, 1150)
BOSS = struct.pack("<45h", BOSS_SPECIES, 2, *BOSS_AT,  # type, followMode 2 (turns, never walks), position
                   0, 0, 0, 1200, 0, BOSS_DG_SECTION,  # rotation, tracking range, unused, DG section to run when talked to
                   # HP, MP, current HP, current MP. Fights you can flee spend the enemy's MP (cost x 3, 217 a move on
                   # average), and a dry enemy only backs away, so MP must outlast the longest fight
                   8800, 16000, 8800, 16000,
                   820, 640, 520, 400, 5000,           # offense, defense, speed, brains (at 300 or less it can go senile), bits
                   1, 1,                               # charge mode (reset at its first decision), read only with more enemies
                   # animations, each picking from the species' move list: Buster Dive, Bug, Insect Plague, and in the
                   # only slot a finisher can use, High Electro Shocker (tech 0x70, on this species and animation)
                   50, 56, 55, 60,
                   35, 25, 40, 10,                     # how often it picks each; the finisher ignores this, it fires on its meter
                   3400, 0, 1150,                      # the bearing it runs off on when beaten: east, into the foliage
                   1, 0, -1, -1, -1, -1, -1, -1, -1,   # one AI step, a wait, whose count is the next word
                   *BOSS_AT)
BOSS_LINES = ("So you found my glade.", "Show me your strength!")   # at most 28 characters each

PAINT = (6, 5)                       # the frame the art is painted in (768 x 640) ...
GW, GH = 6, 4                        # ... and the backdrop, its centre crop: 768 x 512, 24 tiles. Six fewer
                                     # tiles leave room in the 247 sectors for the foreground sprites' image
CROP = (PAINT[1] - GH) * 64          # rows cut from the top (and from the bottom) of the painted art
VP = np.array([0.0, -5600.0, -7400.0])    # eye; the MAP stores half (the engine doubles it)
VR = np.array([0.0, 0.0, -1500.0])        # look-at point, lands on the backdrop centre
H_PROJ = 927                              # GsSetProjection distance
LIGHTS = [(0, 170, -30, 178, 178, 178), (1, 1, 1, 0, 0, 0), (1, 1, 1, 0, 0, 0)]  # as the MAYO maps
SUN = np.array([0.35, 1.0, -0.55])        # direction the sunlight travels (+Y down): from behind-left
SUN /= np.linalg.norm(SUN)

SPAWN = (-100, 0, -3500)                  # arrival, facing up the path (rotation 0x800 = +z)
SPAWN_ROT = 0x800
WARP_STRIP_Z = -3750                      # path cells nearer the camera than this send you back
MIN_GROUND_IOU = 0.75                     # painted art must show ground where the scene has ground
PAINTED_PROPS = ("rock", "stump", "log")  # small props: their collision follows where the painting puts them

BW, BH = GW * 128, GH * 128
PBW, PBH = PAINT[0] * 128, PAINT[1] * 128


# ---- scene -----------------------------------------------------------------
CLEAR_C = np.array([0.0, -700.0])         # the clearing: a wobbly ellipse around this point
PATH = np.array([[-200, -5200], [-150, -3800], [150, -2600], [-100, -1500], [200, -400], [500, 600], [300, 1500]],
                float)
POND = [(-1500, 900, 620, 360), (-1050, 700, 380, 260), (-1850, 1150, 300, 220)]   # union of ellipses (x, z, rx, rz)
LABELS = ("grass", "water", "foliage", "rock", "wood", "earth")
GROUND = (0, 5)                           # labels a character may stand on
MATS = dict(leaf=2, rock=3, bark=4, stump=4, log=4)
FLAT = dict(leaf=(46, 92, 40), bark=(96, 70, 46), rock=(128, 126, 120), stump=(110, 84, 56),
            log=(96, 70, 46))


def half_width(z):
    """Half the floor width the frame shows at depth z. The constants are this camera's
    backdrop corners unprojected onto the floor: (+-2469, -4488) bottom, (+-5284, 4895) top."""
    t = (z + 4488) / (4895 + 4488)
    return 2469 + t * (5284 - 2469)


def clear_r(theta):
    ex, ez = 2700.0, 2900.0
    base = 1 / np.sqrt((np.cos(theta) / ex) ** 2 + (np.sin(theta) / ez) ** 2)
    return base * (1 + 0.10 * np.sin(3 * theta + 1.0) + 0.06 * np.sin(5 * theta + 2.3) + 0.04 * np.sin(9 * theta))


def in_clearing(x, z, grow=0.0):
    dx, dz = x - CLEAR_C[0], z - CLEAR_C[1]
    return np.hypot(dx, dz) <= clear_r(np.arctan2(dz, dx)) + grow


def path_dist(x, z):
    """Distance to the dirt path's centre line."""
    d = np.full(np.shape(x), np.inf)
    for a, b in zip(PATH[:-1], PATH[1:]):
        ab = b - a
        t = np.clip(((x - a[0]) * ab[0] + (z - a[1]) * ab[1]) / (ab @ ab), 0, 1)
        d = np.minimum(d, np.hypot(x - (a[0] + t * ab[0]), z - (a[1] + t * ab[1])))
    return d


def pond_e(x, z, grow=0.0):
    """Smallest normalised ellipse distance over the pond's lobes (< 1 is water)."""
    return np.min([((x - cx) / (rx + grow)) ** 2 + ((z - cz) / (rz + grow)) ** 2 for cx, cz, rx, rz in POND], axis=0)


def earth_patch(x, z, pd):
    """Worn bare-earth patches in the grass (0..1), kept off the path's verge."""
    return np.clip((noise(x, z, 700, 4, 21) - 0.62) / 0.04, 0, 1) * np.clip((pd - 260) / 80, 0, 1)


def scene():
    """Props as primitives in world units (100 = one collision cell). Seeded: the painted
    art was made over exactly this scene, so changing it means repainting."""
    rng = np.random.default_rng(7)
    p = []

    def blob(x, z, r, h, mat="leaf", tone=0.0, flat=0.85, group=None):
        group = group or "bush %d,%d" % (x // 700, z // 700)        # a clump: one foreground sprite group
        p.append(dict(kind="ell", c=(x, -h, z), r=(r, r * flat, r), mat=mat, tone=tone, group=group))

    def tree(x, z, s, tone):
        group = "tree %d,%d" % (x, z)
        p.append(dict(kind="cyl", c=(x, z), r=60 * s, y0=-520 * s, y1=0, mat="bark", group=group))
        for k in range(6):
            a = k * 1.05 + rng.uniform(0, 0.6)
            rr = rng.uniform(80, 220) * s
            blob(x + np.cos(a) * rr, z + np.sin(a) * rr * 0.6, rng.uniform(230, 330) * s, rng.uniform(650, 900) * s,
                 tone=tone, group=group)
    # foliage outside the clearing on a jittered grid, bigger toward the back
    for gz in np.arange(-5200, 6200, 330):
        for gx in np.arange(-6500, 6600, 330):
            x, z = gx + rng.uniform(-140, 140), gz + rng.uniform(-140, 140)
            if in_clearing(x, z, grow=120) or path_dist(x, z) < 380:
                continue
            if abs(x) > half_width(z) + 700:
                continue
            depth = np.clip((z + 4000) / 9000, 0, 1)
            r = rng.uniform(200, 330) * (1 + 0.6 * depth)
            outside = not in_clearing(x, z, grow=700)
            h = r * (0.55 if not outside else rng.uniform(0.9, 1.6))
            blob(x, z, r, h, tone=rng.uniform(0, 1))
    # broadleaf trees standing at the clearing's back edge
    for x, z, s in ((-2300, 2300, 1.2), (900, 2700, 1.35), (2600, 1500, 1.1), (-200, 3300, 1.4)):
        tree(x, z, s, rng.uniform(0, 1))
    for k in range(14):                         # a bush island right of the path
        blob(1550 + rng.uniform(-450, 450), -900 + rng.uniform(-300, 300), rng.uniform(200, 290),
             rng.uniform(150, 260), tone=rng.uniform(0, 0.5), group="island")
    for x, z, rx, ry, rz in ((-2050, 450, 230, 170, 200), (-850, 1250, 160, 110, 140), (-2300, 1300, 190, 150, 170),
                             (2100, -2700, 200, 150, 180), (-1700, -2300, 240, 170, 210)):
        p.append(dict(kind="ell", c=(x, -ry * 0.35, z), r=(rx, ry, rz), mat="rock"))
    p.append(dict(kind="cyl", c=(-1100, -1200), r=170, y0=-140, y1=0, mat="stump"))   # old stump
    for k in range(5):                                                              # fallen log
        p.append(dict(kind="ell", c=(700 + k * 120, -70, -3000 + k * 40), r=(110, 70, 75), mat="log"))
    return p


def ground_albedo(x, z):
    """Flat guide colours: grass, worn earth, the path, the pond."""
    col = np.zeros(np.shape(x) + (3,))
    col[:] = (104, 140, 60)
    pd = path_dist(x, z)
    col = mix(col, np.array([138, 112, 72]), earth_patch(x, z, pd) * 0.85)
    col = mix(col, np.array([150, 118, 78]), np.clip((180 - pd) / 40, 0, 1))
    water = pond_e(x, z) < 1
    col[water] = (60, 110, 150)
    return col


def shore_spot(grid):
    """A floor cell due south of the pond's main lobe whose way north ends at a flat wall: the
    same blocked row in the columns either side, so a push north stops dead instead of sliding
    along a jagged edge. Returns the spot 4 cells south of the wall, and the wall's z."""
    cx, cz, rx, rz = POND[0]
    best = None
    for tx in range(100):
        x = (tx - 50) * 100 + 50
        if abs(x - cx) > 0.6 * rx:
            continue
        walls = []
        for c in (tx - 1, tx, tx + 1):
            tz = 50 - int((cz - rz - 700) / 100)           # start well south of the water
            while tz > 0 and grid[tz, c] == 0:
                tz -= 1
            walls.append(tz)
        if len(set(walls)) == 1 and grid[walls[0] + 1:walls[0] + 6, tx - 1:tx + 2].min() == 0 == \
                grid[walls[0] + 1:walls[0] + 6, tx - 1:tx + 2].max():
            wall_z = (50 - walls[0]) * 100 - 100                 # the wall cell's south edge
            if best is None or wall_z > best[1]:
                best = ((x, (50 - walls[0] - 4) * 100 - 50), wall_z)
    assert best, "no flat stretch of shore south of the pond"
    return best


def boss_script():
    """The glade's DG script: section table, the bare END section map arrival runs, the boss's section.
    Text is fullwidth Shift-JIS. 1B 05 pauses the game (it must come first), 1A 00 shows the text and waits
    for confirm, 27 00 closes the box, 67 00 01 00 waits a frame, 66 00 starts the battle, FE 00 ends."""
    wide = {32: 0x3000, 46: 0x3002, **{c: c + 0xFEE0 for c in range(33, 127) if c != 46}}
    # the font has no glyph behind other symbols (they draw a triangle), and Shift-JIS has no fullwidth apostrophe
    assert all(len(line) <= 28 and re.fullmatch(r"[A-Za-z0-9 .!?]*", line) for line in BOSS_LINES)
    text = b"\x0d\x00".join(line.translate(wide).encode("shift_jis") for line in BOSS_LINES)
    return (struct.pack("<6H", 12, 0xFE, 12, BOSS_DG_SECTION, 14, 0xFFFF) + b"\xfe\x00"
            + bytes.fromhex("1b05 1a00") + text + bytes.fromhex("0d00 0000 2700 6700 0100 6600 fe00 ff00"))


# ---- the glade as an engine Screen -----------------------------------------
def _warps():
    warps = np.zeros((6, 10), np.int16)
    warps[4:, :] = -1                                            # unused targets, as stock
    warps[:, 0] = (SPAWN[0], SPAWN[1], SPAWN[2], SPAWN_ROT, HOST_ROW, HOST_WARP)
    warps[:, 1] = HOST_SPAWN + (HOST_ROW, HOST_SAVE_EXIT)        # where a save resumes: dw_newmaps.c reads this.
    return warps                                                 # No cell is 111, so it never fires as a warp


def _floor_label(x, z):
    pd = path_dist(x, z)
    lab = np.zeros(np.shape(x), np.uint8)
    lab[(pd < 180) | (earth_patch(x, z, pd) > 0.5)] = 5
    lab[pond_e(x, z) < 1] = 1
    return lab


GLADE = Screen(
    name="glade", row=ROW, vp=VP, vr=VR, h=H_PROJ, grid=(GW, GH), paint=PAINT, lights=LIGHTS, labels=LABELS,
    ground=GROUND, mats=MATS, flat=FLAT, fg_mats=("leaf", "bark"), painted_props=PAINTED_PROPS,
    floor=lambda x, z: (in_clearing(x, z) | (path_dist(x, z) < 160)) & (pond_e(x, z, grow=90) > 1),
    strips={110: lambda x, z: (z < WARP_STRIP_Z) & (path_dist(x, z) < 160)},
    warps=_warps(), records=[BOSS], regions=(-1,) * 8,
    albedo=ground_albedo, floor_label=_floor_label, water=lambda x, z: pond_e(x, z) < 1, sun=SUN)


# the painter (paint_newmap.py) works on the glade through these
def rays(width, height, scale, frame=None):
    return E.rays(GLADE, width, height, scale, frame)


def classify(rgb, lab_map):
    return E.classify(GLADE, rgb, lab_map)


def labels(prims, frame=None):
    return E.labels(GLADE, prims, frame)


# ---- package ---------------------------------------------------------------
def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", default=os.path.join(root, "disc", "Digimon World (USA).bin"))
    ap.add_argument("--build", default=os.path.join(root, "build-debug"))
    ap.add_argument("--art", default=os.path.join(root, "art", "newmaps", "glade.png"),
                    help="painted art (paint_newmap.py); the flat guide is used if it does not exist")
    ap.add_argument("--preview", action="store_true", help="render previews only")
    ap.add_argument("--check", action="store_true", help="build in memory and compare with the committed package")
    a = ap.parse_args()
    prev = os.path.join(a.build, "newmap")
    os.makedirs(prev, exist_ok=True)
    pal = lambda c: np.stack([(c & 31) << 3, ((c >> 5) & 31) << 3, ((c >> 10) & 31) << 3], -1).astype(np.uint8)

    prims = scene()
    Image.fromarray(E.guide(GLADE, prims, frame=PAINT).astype(np.uint8)).save(os.path.join(prev, "guide.png"))
    lab_map = labels(prims)
    painted = fg = None
    if os.path.exists(a.art):
        art = Image.open(a.art).convert("RGB")
        assert art.size == (PBW, PBH), "painted art must be %dx%d (the paint frame)" % (PBW, PBH)
        rgb = np.asarray(art, np.float32)[CROP:CROP + BH]
        painted = classify(rgb, lab_map)
        gl, pg = np.isin(lab_map, GROUND), np.isin(painted, GROUND)
        iou = (gl & pg).sum() / (gl | pg).sum()
        print("painted art %s: ground IoU %.3f against the scene" % (os.path.relpath(a.art, root), iou))
        if iou < MIN_GROUND_IOU:
            raise SystemExit("the painted art does not match the scene (ground IoU %.3f < %.2f): repaint it"
                             % (iou, MIN_GROUND_IOU))
        fg = E.foreground_pixels(GLADE, prims, painted)
    else:
        print("no painted art at %s: using the flat guide, without foreground sprites" % a.art)
        rgb = E.guide(GLADE, prims)
    grid, order = E.collision(GLADE, prims, painted, fg)
    sx, sz = SPAWN[0], SPAWN[2]                  # tile maths as getTileTrigger (C truncation)
    assert grid[50 - int(sz / 100) - (sz > 0), int(sx / 100) + 50 - (sx < 0)] == 0, "spawn must be floor"
    assert (grid == 110).any(), "the warp strip back to the host must be reachable floor"
    idx, day = E.quantise(rgb, painted)
    cluts = [day, E.tint(day, (1.0, 0.78, 0.62)), E.tint(day, (0.42, 0.5, 0.78))]
    assert not (day[idx] == 0).any()
    rects, pages = E.build_sprites(GLADE, fg, order, idx, prims) if fg is not None else ([], [])
    if rects:
        bad = E.order_errors(GLADE, grid, fg, order, rects)
        grid[bad] = 0x80
        print("dense order check: %d cells blocked" % bad.sum())
        assert grid[50 - int(sz / 100) - (sz > 0), int(sx / 100) + 50 - (sx < 0)] == 0 and (grid == 110).any()
    bx, bz = BOSS_AT[0], BOSS_AT[2]
    assert grid[50 - int(bz / 100) - (bz > 0), int(bx / 100) + 50 - (bx < 0)] == 0, "the boss must stand on floor"
    tims = [E.build_tim(pg, k, day) for k, pg in enumerate(pages)]
    objects = E.build_objects(rects) if rects else b""
    for name, c in zip(("day", "dusk", "night"), cluts):
        Image.fromarray(pal(c)[idx]).save(os.path.join(prev, "backdrop_%s.png" % name))
    ov = pal(day)[idx].astype(float)
    fgmask = np.zeros(idx.shape, bool)
    for s in rects:
        u, v, w, h = s["rect"]
        fgmask[v:v + h, u:u + w] |= s["mask"]
    Image.fromarray((fgmask * 255).astype(np.uint8)).save(os.path.join(prev, "fg_mask.png"))
    ov[fgmask] = ov[fgmask] * 0.5 + np.array([255, 0, 255]) * 0.5
    tz, tx = np.nonzero(grid != 0x80)
    for cx, cz, g in zip(tx, tz, grid[tz, tx]):
        x, z = cell_centre(cx, cz)
        u, v = E.project(GLADE, (x, 0, z))
        if 0 <= u < BW and 0 <= v < BH:
            ov[int(v) - 1:int(v) + 2, int(u) - 1:int(u) + 2] = (0, 255, 0) if g == 0 else (0, 128, 255)
    u, v = E.project(GLADE, SPAWN)
    ov[int(v) - 3:int(v) + 4, int(u) - 3:int(u) + 4] = (255, 255, 0)
    Image.fromarray(ov.astype(np.uint8)).save(os.path.join(prev, "collision_overlay.png"))
    print("walkable cells %d, warp cells %d, colours %d, foreground sprites %d (%d groups, %d texture pages)"
          % ((grid == 0).sum(), (grid == 110).sum(), len(np.unique(idx)), len(rects),
             len({s["group"] for s in rects}), len(pages)))
    # in-game test targets: three meadow spots, a shore spot due south of the pond, and the floor
    # cell behind the bush island that its sprite hides most of a character on
    meadow = [deep_floor(grid, w) for w in ((-1900, -1000), (300, 300), (1500, -2000))]
    shore, _ = shore_spot(grid)
    spec = dict(row=ROW, host=dict(row=HOST_ROW, exit=HOST_WARP, spawn=HOST_SPAWN[:3], save_exit=HOST_SAVE_EXIT,
                                   strip=(HOST_SPAWN[0], (50 - HOST_CELLS[0][0]) * 100 - 50)),
                spawn=SPAWN, strip_z=WARP_STRIP_Z, camera=dict(vp=VP.tolist(), vr=VR.tolist(), h=H_PROJ, gw=GW, gh=GH),
                path=PATH.tolist(), meadow=meadow, pond=dict(shore=shore, lobes=POND),
                boss=dict(species=BOSS_SPECIES, at=BOSS_AT, record=list(struct.unpack("<45h", BOSS))),
                backdrop=os.path.join(prev, "backdrop_day.png"))
    if rects:
        gi = fg["groups"].index("island")
        hidden = ((order["gid"] == gi) & order["occ"]).sum(1) * order["keep"]
        hx, hz = cell_centre(order["cells"][int(np.argmax(hidden)), 1], order["cells"][int(np.argmax(hidden)), 0])
        spec["fg"] = dict(count=len(rects), behind=(int(hx), int(hz)), via=deep_floor(grid, (hx, hz + 700)),
                          mask=os.path.join(prev, "fg_mask.png"), samples=int(hidden.max()))
    json.dump(spec, open(os.path.join(prev, "spec.json"), "w"), indent=1)
    mapb, tfsb = E.build_map(GLADE, grid, tims, objects), E.build_tfs(GLADE, idx, cluts)
    assert sectors(mapb) + sectors(tfsb) <= MAP_SECTORS + TFS_SECTORS, \
        "MAP %d + TFS %d sectors do not fit MGEN17's %d" % (sectors(mapb), sectors(tfsb), MAP_SECTORS + TFS_SECTORS)
    print("MAP %d B (%d sectors), TFS %d B (%d sectors), %d of %d sectors"
          % (len(mapb), sectors(mapb), len(tfsb), sectors(tfsb), sectors(mapb) + sectors(tfsb),
             MAP_SECTORS + TFS_SECTORS))
    if a.preview:
        return 0

    f = open(a.bin, "rb")
    listing = []
    rr = parse_record(sector(f, 16)[156:190])
    walk(f, rr["lba"], rr["size"], "", listing)
    files = {os.path.basename(n): (lba, size) for n, lba, size in listing}
    rd = lambda n: read_extent(f, *files[n])
    hexs = lambda b: " ".join("%02x" % x for x in b)
    slot = files[FILE + ".MAP"][0]
    assert files[FILE + ".TFS"][0] == slot + MAP_SECTORS, "MGEN17's MAP and TFS must be adjacent"
    for name, n in ((FILE + ".MAP", MAP_SECTORS), (FILE + ".TFS", TFS_SECTORS)):
        assert sectors(bytes(files[name][1])) == n, name
    exe = rd("SLUS_010.32")
    ram = lambda addr: addr - struct.unpack_from("<I", exe, 0x18)[0] + 0x800      # PS-X EXE: text at 0x800
    k = 0
    while struct.unpack_from("<2i", exe, ram(FILE_OFFSET_TABLE) + 12 * k) != (slot, files[FILE + ".MAP"][1]):
        k += 1
        assert k < 1000, "MGEN17.MAP not found in FILE_OFFSET_TABLE"
    fte = FILE_OFFSET_TABLE + 12 * k
    assert struct.unpack_from("<2i", exe, ram(fte) + 12) == (slot + MAP_SECTORS, files[FILE + ".TFS"][1])
    dg = rd("DG.SCN")
    so = struct.unpack_from("<I", dg, 4 * SCRIPT)[0]
    assert dg[so + 2:so + 8] == bytes.fromhex("fe000800ffff") and dg[so + 8:so + 10] == b"\xfe\x00"
    dg_new = boss_script()
    assert len(dg_new) <= struct.unpack_from("<I", dg, 4 * SCRIPT + 4)[0] - so, "the script must fit its DG slot"
    mh = rd("MAPHEAD.SCN")
    p = 2
    while struct.unpack_from("<H", mh, p)[0] != ROW:
        p += 4
    assert mh[struct.unpack_from("<H", mh, p + 2)[0]:][:6] == bytes.fromhex("fb00b400ef00"), "stock filler"
    assert len(mh) <= MH_NEW and not any(sector(f, files["MAPHEAD.SCN"][0] + MH_NEW // 2048)[MH_NEW % 2048:][:32])
    hd = rd(HOST + ".MAP")
    hent, hcol = struct.unpack_from("<2i", hd, 4 * (struct.unpack_from("<i", hd)[0] // 4 - 2))
    hbase = files[HOST + ".MAP"][0] * 2048
    field = lambda table, slot_: hent + 2 * (10 * table + slot_)
    assert [struct.unpack_from("<h", hd, field(t, HOST_WARP))[0] for t in range(6)] == [0, 0, 0, 0, -1, -1], \
        "the host's warp slot must be free"
    assert struct.unpack_from("<h", hd, field(0, HOST_SAVE_EXIT))[0] != 0, "the exit a save names must be a stock one"
    (wall_row, floor_row), cols = HOST_CELLS
    assert set(hd[hcol + wall_row * 100 + cols[0]:hcol + wall_row * 100 + cols[-1] + 1]) == {0x80}
    assert set(hd[hcol + floor_row * 100 + cols[0]:hcol + floor_row * 100 + cols[-1] + 1]) == {0}
    for x, z in ((HOST_SPAWN[0], HOST_SPAWN[2]), (HOST_SPAWN[0], HOST_SPAWN[2] + 200)):   # tamer, then partner
        assert hd[hcol + (50 - int(z / 100) - (z > 0)) * 100 + int(x / 100) + 50 - (x < 0)] == 0, "spawn must be floor"
    src = open(os.path.join(root, "dw_newmaps.c"), encoding="utf-8").read()
    for name, value in (("DW_ROW", ROW), ("DW_HOST_ROW", HOST_ROW), ("DW_HOST_EXIT", HOST_WARP),
                        ("DW_STOCK_EXIT", HOST_SAVE_EXIT)):
        assert re.search(r"#define %s\s+%d\b" % (name, value), src), "dw_newmaps.c disagrees on " + name
    h = hashlib.sha256()
    f.seek(0)
    for block in iter(lambda: f.read(1 << 22), b""):
        h.update(block)
    disc_sha = h.hexdigest()

    pkg = os.path.join(root, "mods", "preloaded", "packages", "dw.newmaps", "1.0.0")
    assets = {}                                 # name -> bytes: written, or compared by --check
    ms = sectors(mapb)                          # our MAP, then our TFS, across MGEN17's 247 sectors
    blobs = [("glade.map", slot, mapb.ljust(ms * 2048, b"\0")),
             ("glade.tfs", slot + ms, tfsb.ljust((MAP_SECTORS + TFS_SECTORS - ms) * 2048, b"\0"))]
    for name, _, blob in blobs:
        assets[name] = blob
    section = (bytes([0xFB, 0]) + struct.pack("<HH", SCRIPT, ROW) + bytes([0x1E, 0, 0xF5, BGM_PSTAT])
               + bytes([0x5D, BGM]) + bytes([0x46, BOSS_SPECIES, 0x47, BOSS_SPECIES, 0, 0]) + b"\xfe\x00")
    assert MH_NEW + len(section) <= MH_BUFFER
    row = FILE.encode().ljust(10, b"\0") + bytes([len(tims), 0, FLAGS, 0, 0, LOADING_NAME])
    mh_base = files["MAPHEAD.SCN"][0] * 2048
    out = ["# New maps. Generated by tools/gen_newmap.py: regenerate rather than hand-editing, so every guard stays",
           "# byte-exact. assets/ is this port's own map and art (no game art or data); regenerating means updating",
           "# MOD_PAYLOADS in tools/verify_setup_package.py. Isolated keeps savestates from crossing into a plan",
           "# without the map; memory cards stay shared, because dw_newmaps.c keeps the new row off them.",
           "format_version = 13", 'id = "dw.newmaps"', 'version = "1.0.0"', 'name = "New Maps"',
           'author = "DigimonWorldRecomp"',
           'description = "Maps the original game never had, built from this port\'s own art and collision. '
           'No game data is in this package."',
           'resolver = "declarative"', 'save_compatibility = "isolated"', "",
           "[[target]]", 'game_id = "SLUS-01032"', 'disc_sha256 = "%s"' % disc_sha, "",
           "[[feature]]", 'id = "glade"', 'name = "Hidden Glade"',
           'description = "A new map to explore: a quiet forest glade with a pond, painted for this port, and '
           'something strong waiting at the end of the path. Find it '
           'in Native Forest, on the screen where Palmon waits: walk north through the gap in the bushes at the top '
           'right. A save made inside resumes just outside it, so your cards stay safe with this off. Needs the '
           'exact disc image listed in DISC.md. Works with Digimon World 1.5 too: there it runs a 1.5 '
           'edition by itself."',
           'group = "New maps"', "default_enabled = false",
           'conflicts = ["dw.newmaps.spike/spike"]', "",
           "# dw_newmaps.c: a save made on the new map names its host instead.",
           "[[plugin]]", 'feature = "glade"', 'id = "dw.newmaps"', ""]
    for name, lba, blob in blobs:
        out += ["# our %s in %s's sectors" % (name, FILE), "[[overlay]]", 'feature = "glade"',
                'target = "disc_user"', "offset = %d" % (lba * 2048), 'file = "assets/%s"' % name,
                'sha256 = "%s"' % hashlib.sha256(blob).hexdigest(), ""]
    # A file, not a [[patch]]: a patch must repeat the stock bytes it replaces, and these are live script bytecode.
    # The generator's asserts above and disc_sha256 guard the slot.
    assets["glade.dg"] = dg_new
    out += ["# DG script %d: the arrival END, plus the boss's section" % SCRIPT, "[[overlay]]", 'feature = "glade"',
            'target = "disc_user"', "offset = %d" % (files["DG.SCN"][0] * 2048 + so), 'file = "assets/glade.dg"',
            'sha256 = "%s"' % hashlib.sha256(dg_new).hexdigest(), ""]
    patch = lambda target, where, old, new: ('target = "%s"\n%s = %s\nexpected = "%s"\nreplace = "%s"'
                                             % (target, "address" if target == "main_exe" else "offset",
                                                ("0x%08X" if target == "main_exe" else "%d") % where,
                                                hexs(old), hexs(new)))
    patches = [("MAP_ENTRIES[%d] = %s, %d 8bpp images" % (ROW, FILE, len(tims)),
                patch("main_exe", MAP_ENTRIES + ROW * 16, bytes(16), row)),
               ("file table: %s.MAP at sector %d, %d bytes" % (FILE, slot, len(mapb)),
                patch("main_exe", fte, exe[ram(fte):ram(fte) + 8], struct.pack("<2i", slot, len(mapb)))),
               ("file table: %s.TFS at sector %d, %d bytes" % (FILE, slot + ms, len(tfsb)),
                patch("main_exe", fte + 12, exe[ram(fte) + 12:ram(fte) + 20], struct.pack("<2i", slot + ms, len(tfsb)))),
               ("MAPHEAD section %d body" % ROW, patch("disc_user", mh_base + MH_NEW, bytes(len(section)), section)),
               ("MAPHEAD section %d -> 0x%x" % (ROW, MH_NEW),
                patch("disc_user", mh_base + p + 2, mh[p + 2:p + 4], struct.pack("<H", MH_NEW)))]
    for name, lba_new, data in ((".MAP", slot, mapb), (".TFS", slot + ms, tfsb)):
        off, old = iso_record(f, rr, ("MAP", "MAP16", FILE + name))
        patches.append(("ISO directory: %s%s at sector %d, %d bytes" % (FILE, name, lba_new, len(data)),
                        patch("disc_user", off + 2, old[2:18], struct.pack("<I", lba_new) + struct.pack(">I", lba_new)
                              + struct.pack("<I", len(data)) + struct.pack(">I", len(data)))))
    new_slot = HOST_SPAWN + (ROW, 0)                     # arrives at the map's spawn 0
    for t, value in enumerate(new_slot):
        if value != struct.unpack_from("<h", hd, field(t, HOST_WARP))[0]:
            patches.append(("entrance: %s warp %d field %d = %d" % (HOST, HOST_WARP, t, value),
                            patch("disc_user", hbase + field(t, HOST_WARP), hd[field(t, HOST_WARP):field(t, HOST_WARP) + 2],
                                  struct.pack("<h", value))))
    for r in HOST_CELLS[0]:
        at = hcol + r * 100 + cols[0]
        patches.append(("entrance: %s collision row %d, columns %d-%d" % (HOST, r, cols[0], cols[-1]),
                        patch("disc_user", hbase + at, hd[at:at + len(cols)], bytes([0x6E + HOST_WARP]) * len(cols))))
    for why, body in patches:
        out += ["# " + why, "[[patch]]", 'feature = "glade"', body, ""]
    path = os.path.join(pkg, "manifest.toml")
    marker = "# ---- generated by tools/gen_newmap_dw15.py below this line"
    old = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
    tail = marker + old.split(marker, 1)[1] if marker in old else ""     # the 1.5 edition: rerun gen_newmap_dw15.py
    head = "\n".join(out) + ("\n\n" if tail else "")
    if a.check:
        for name, blob in assets.items():
            cur = os.path.join(pkg, "assets", name)
            if not os.path.exists(cur) or open(cur, "rb").read() != blob:
                print("DIFFERS: assets/" + name)
                return 1
        cur = old.split(marker, 1)[0].replace("\r\n", "\n").rstrip()     # gen_newmap_dw15.py owns the seam's blank lines
        head = head.rstrip()
        if cur != head:
            first = next((i for i, (x, y) in enumerate(zip(cur.split("\n"), head.split("\n"))) if x != y), None)
            print("DIFFERS: manifest.toml (above the marker), first differing line %s" % first)
            if first is not None:
                print("  committed: " + cur.split("\n")[first][:160] + "\n  built:     " + head.split("\n")[first][:160])
            return 1
        print("glade package identical to the committed one")
        return 0
    os.makedirs(os.path.join(pkg, "assets"), exist_ok=True)
    for name, blob in assets.items():
        open(os.path.join(pkg, "assets", name), "wb").write(blob)
    with open(path, "w", newline="\n") as fh:
        fh.write(head + tail)
    print("wrote %s" % pkg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
