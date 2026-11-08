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

# ---- the screen ------------------------------------------------------------
ROW, FILE = 239, "MGEN17"            # free row; unused stock file slot we overlay
MAP_SECTORS, TFS_SECTORS = 6, 241    # MGEN17's own extents, adjacent on the disc: 247 sectors we lay out
MAX_FILE = 0x78800                   # readFile's window, GENERAL_BUFFER up to TEXTURE_BUFFER (241 sectors)
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


def in_ellipse(x, z, e, grow=0.0):
    return ((x - e["c"][0]) / (e["r"][0] + grow)) ** 2 + ((z - e["c"][1]) / (e["r"][1] + grow)) ** 2 <= 1


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


# ---- camera ----------------------------------------------------------------
def basis():
    f = (VR - VP) / np.linalg.norm(VR - VP)
    r = np.array([f[2], 0.0, -f[0]]) / np.hypot(f[0], f[2])
    return r, np.cross(f, r), f


def project(P, frame=None):
    """Backdrop pixel (u, v) of world points; `frame` (tiles wide, high) defaults to the backdrop's."""
    gw, gh = frame or (GW, GH)
    r, d, f = basis()
    q = np.asarray(P, float) - VP
    zc = q @ f
    return 64 * gw + H_PROJ * (q @ r) / zc, 64 * gh + H_PROJ * (q @ d) / zc


def depth(P):
    """Camera-space z: what the GTE puts in SZ, and what map objects and models are ordered by (>> 2)."""
    return (np.asarray(P, float) - VP) @ basis()[2]


def rays(width, height, scale, frame=None):
    """World-space ray directions through backdrop pixel centres (render at `scale`x)."""
    gw, gh = frame or (GW, GH)
    r, d, f = basis()
    v, u = np.mgrid[0:height, 0:width].astype(np.float32)
    xu = ((u + 0.5) / scale - 64 * gw) / H_PROJ
    yv = ((v + 0.5) / scale - 64 * gh) / H_PROJ
    dirs = xu[..., None] * r + yv[..., None] * d + f
    return (dirs / np.linalg.norm(dirs, axis=-1, keepdims=True)).astype(np.float32)


# ---- tracing ---------------------------------------------------------------
def hit(prim, o, d):
    """Distance along each ray to `prim` (inf where missed) and the surface normal."""
    inf = np.float32(np.inf)
    if prim["kind"] == "ell":
        c, rr = np.array(prim["c"], np.float32), np.array(prim["r"], np.float32)
        oo, dd = (o - c) / rr, d / rr
        a = (dd * dd).sum(-1)
        b = 2 * (oo * dd).sum(-1)
        cc = (oo * oo).sum(-1) - 1
        disc = b * b - 4 * a * cc
        sq = np.sqrt(np.maximum(disc, 0))
        t = (-b - sq) / (2 * a)
        t = np.where(t > 1e-2, t, (-b + sq) / (2 * a))
        t = np.where((disc > 0) & (t > 1e-2), t, inf)
        pt = o + d * np.where(np.isfinite(t), t, 0)[..., None]
        n = (pt - c) / (rr * rr)
    else:  # vertical cylinder
        cx, cz = prim["c"]
        ox, oz = o[..., 0] - cx, o[..., 2] - cz
        a = d[..., 0] ** 2 + d[..., 2] ** 2
        b = 2 * (ox * d[..., 0] + oz * d[..., 2])
        cc = ox * ox + oz * oz - prim["r"] ** 2
        disc = b * b - 4 * a * cc
        sq = np.sqrt(np.maximum(disc, 0))
        t = (-b - sq) / (2 * np.maximum(a, 1e-9))
        y = o[..., 1] + d[..., 1] * t
        t = np.where((disc > 0) & (t > 1e-2) & (y >= prim["y0"]) & (y <= prim["y1"]), t, inf)
        pt = o + d * np.where(np.isfinite(t), t, 0)[..., None]
        n = np.stack([pt[..., 0] - cx, np.zeros_like(t), pt[..., 2] - cz], -1)
    n = n / np.maximum(np.linalg.norm(n, axis=-1, keepdims=True), 1e-9)
    return t, n


def occluded(prims, o, d, limit=None):
    """True where a ray from o along d meets any prop (before `limit`)."""
    blocked = np.zeros(o.shape[:-1], bool)
    for prim in prims:
        t, _ = hit(prim, o, d)
        blocked |= t < (np.inf if limit is None else limit)
    return blocked


def trace(prims, scale, frame=None):
    """First hit per pixel: ray directions, distance, owning prop (-1 = floor), normal."""
    frame = frame or (GW, GH)
    w, h = frame[0] * 128 * scale, frame[1] * 128 * scale
    d = rays(w, h, scale, frame)
    vp32 = VP.astype(np.float32)
    best = np.where(d[..., 1] > 0, -VP[1] / np.maximum(d[..., 1], 1e-9), np.inf).astype(np.float32)
    owner = np.full((h, w), -1, np.int32)
    normal = np.zeros_like(d)
    normal[..., 1] = -1
    for i, prim in enumerate(prims):                 # trace each prop only inside its screen box
        u, v = project(corners(prim), frame)
        u0, u1 = max(0, int(u.min() * scale) - 2), min(w, int(u.max() * scale) + 3)
        v0, v1 = max(0, int(v.min() * scale) - 2), min(h, int(v.max() * scale) + 3)
        if u0 >= u1 or v0 >= v1:
            continue
        sub = d[v0:v1, u0:u1]
        t, n = hit(prim, np.broadcast_to(vp32, sub.shape), sub)
        closer = t < best[v0:v1, u0:u1]
        best[v0:v1, u0:u1][closer] = t[closer]
        owner[v0:v1, u0:u1][closer] = i
        normal[v0:v1, u0:u1][closer] = n[closer]
    return d, best, owner, normal


# ---- guide -----------------------------------------------------------------
def noise(x, y, scale, octaves=4, seed=0):
    """Smooth value-noise fBm in [0, 1] on world coordinates."""
    total, amp, norm = 0.0, 1.0, 0.0
    for o in range(octaves):
        fx, fy = x / scale * 2 ** o, y / scale * 2 ** o
        ix, iy = np.floor(fx), np.floor(fy)
        tx, ty = fx - ix, fy - iy
        tx, ty = tx * tx * (3 - 2 * tx), ty * ty * (3 - 2 * ty)

        def h(i, j):
            v = np.sin(i * 127.1 + j * 311.7 + (seed + o) * 74.7) * 43758.5453
            return v - np.floor(v)
        a = h(ix, iy) * (1 - tx) + h(ix + 1, iy) * tx
        b = h(ix, iy + 1) * (1 - tx) + h(ix + 1, iy + 1) * tx
        total = total + amp * (a * (1 - ty) + b * ty)
        norm += amp
        amp *= 0.5
    return total / norm


def mix(a, b, t):
    return np.asarray(a) * (1 - t[..., None]) + np.asarray(b) * t[..., None]


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


def corners(prim):
    if prim["kind"] == "ell":
        c, r = np.array(prim["c"]), np.array(prim["r"])
        return np.array([c + r * s for s in np.array(np.meshgrid([-1, 1], [-1, 1], [-1, 1])).T.reshape(-1, 3)])
    (cx, cz), r = prim["c"], prim["r"]
    return np.array([(cx + sx * r, y, cz + sz * r) for sx in (-1, 1) for y in (prim["y0"], prim["y1"]) for sz in (-1, 1)])


def shadow_map(prims, res=6.0):
    """Orthographic depth of the props as seen along the sunlight (front-most surface)."""
    a = np.cross(SUN, [0.0, 0.0, 1.0])
    a /= np.linalg.norm(a)
    b = np.cross(SUN, a)
    allc = np.concatenate([corners(p) for p in prims])
    lo = np.array([allc @ a, allc @ b]).min(1) - 50
    hi = np.array([allc @ a, allc @ b]).max(1) + 50
    n = np.ceil((hi - lo) / res).astype(int)
    depth = np.full((n[1], n[0]), np.inf, np.float32)
    for prim in prims:
        c = corners(prim)
        i0, j0 = np.floor(([c @ a, c @ b] - lo[:, None]).min(1) / res).astype(int)
        i1, j1 = np.ceil(([c @ a, c @ b] - lo[:, None]).max(1) / res).astype(int) + 1
        jj, ii = np.mgrid[j0:j1, i0:i1]
        o = ((lo[0] + (ii + 0.5) * res)[..., None] * a + (lo[1] + (jj + 0.5) * res)[..., None] * b
             - 1e5 * SUN).astype(np.float32)
        t, _ = hit(prim, o, np.broadcast_to(SUN.astype(np.float32), o.shape))
        depth[j0:j1, i0:i1] = np.minimum(depth[j0:j1, i0:i1], t)
    return dict(a=a, b=b, lo=lo, res=res, depth=depth)


def sunlit(sm, pt, soft=2):
    """Fraction of a 5x5 shadow-map neighbourhood that sees the sun (soft edges)."""
    i = ((pt @ sm["a"] - sm["lo"][0]) / sm["res"]).astype(int)
    j = ((pt @ sm["b"] - sm["lo"][1]) / sm["res"]).astype(int)
    dep = pt @ SUN + 1e5 - 12.0                     # bias against self-shadow acne
    hgt, wid = sm["depth"].shape
    acc = np.zeros(pt.shape[:-1], np.float32)
    for dj in range(-soft, soft + 1):
        for di in range(-soft, soft + 1):
            jj, ii = np.clip(j + dj * 2, 0, hgt - 1), np.clip(i + di * 2, 0, wid - 1)
            acc += sm["depth"][jj, ii] >= dep
    return acc / (2 * soft + 1) ** 2


def guide(prims, scale=2, frame=None):
    """The painter's input: flat material colours, sun and sky shading, cast shadows."""
    frame = frame or (GW, GH)
    d, best, owner, normal = trace(prims, scale, frame)
    pt = VP.astype(np.float32) + d * best[..., None]
    alb = ground_albedo(pt[..., 0], pt[..., 2]).astype(np.float32)
    for i, prim in enumerate(prims):
        m = owner == i
        if m.any():
            alb[m] = np.array(FLAT[prim["mat"]], np.float32) * (0.85 + 0.3 * prim.get("tone", 0.5))
    lit = sunlit(shadow_map(prims), pt + normal * 2.0)
    lam = np.clip((normal * -SUN.astype(np.float32)).sum(-1), 0, 1)
    light = 0.42 + 0.18 * np.clip(-normal[..., 1], 0, 1) + 0.78 * lam * lit
    water = pond_e(pt[..., 0], pt[..., 2]) < 1
    glint = np.where(water, 0.25 * lit * np.clip(1 - np.abs(d[..., 0]) * 3, 0, 1), 0)   # sun on the pond
    rgb = (alb * light[..., None] + 255 * glint[..., None]).reshape(frame[1] * 128, scale, frame[0] * 128, scale,
                                                                     3).mean((1, 3))
    return np.clip(rgb, 0, 255)


def labels(prims, frame=None):
    """Per backdrop pixel, the material a ray meets first (an index into LABELS)."""
    d, best, owner, _ = trace(prims, 1, frame)
    lab = np.zeros(owner.shape, np.uint8)
    for i, prim in enumerate(prims):
        lab[owner == i] = MATS[prim["mat"]]
    pt = VP.astype(np.float32) + d * best[..., None]
    px, pz = pt[..., 0], pt[..., 2]
    pd = path_dist(px, pz)
    floor = owner < 0
    lab[floor & ((pd < 180) | (earth_patch(px, pz, pd) > 0.5))] = 5
    lab[floor & (pond_e(px, pz) < 1)] = 1
    return lab


# ---- painted art -----------------------------------------------------------
def classify(rgb, lab_map):
    """What the painting shows per pixel, as LABELS: one Gaussian per material over colour,
    blurred colour and local contrast, fitted on the painting itself where the scene's label
    is unambiguous (eroded masks). Cast shadows on grass read as foliage: harmless, as that
    only blocks more of the ground beside a bush."""
    import cv2
    lab = cv2.cvtColor(rgb.astype(np.uint8), cv2.COLOR_RGB2LAB).astype(np.float32)
    lum = lab[..., 0]
    mu = cv2.GaussianBlur(lum, (0, 0), 2)
    std = np.sqrt(np.maximum(cv2.GaussianBlur(lum * lum, (0, 0), 2) - mu * mu, 0))
    F = np.concatenate([lab, cv2.GaussianBlur(lab, (0, 0), 4), std[..., None]], -1)
    X = F.reshape(-1, F.shape[-1])
    ll = np.full((len(X), len(LABELS)), -np.inf, np.float32)
    for c in range(len(LABELS)):
        m = cv2.erode((lab_map == c).astype(np.uint8), np.ones((9, 9), np.uint8)).astype(bool)
        if m.sum() < 200:
            continue
        x = F[m]
        mean, cov = x.mean(0), np.cov(x.T) + np.eye(F.shape[-1])
        inv, (_, logdet) = np.linalg.inv(cov), np.linalg.slogdet(cov)
        dx = X - mean
        ll[:, c] = -0.5 * (np.einsum("ij,jk,ik->i", dx, inv, dx) + logdet) + np.log(m.mean())
    return ll.argmax(1).reshape(lab_map.shape)


# ---- collision -------------------------------------------------------------
def cell_centre(tx, tz):
    return (tx - 50) * 100 + 50, (50 - tz) * 100 - 50


FG_MATS = ("leaf", "bark")           # scene props drawn over characters by foreground sprites: foliage, trunks
FG_MARGIN = 100                      # camera-depth gap kept between a sprite's anchor and a character it overlaps
CHAR_SAMPLES = [(dx, y, dz) for dx in np.arange(-50, 51, 12.5) for y in np.arange(-10, -350, -25) for dz in (-40, 0, 40)]
                                     # a character's box at about pixel pitch: a pixel is 7-15 units here


def cell_grid():
    tz, tx = np.mgrid[0:100, 0:100]
    x, z = cell_centre(tx, tz)
    return x.astype(float), z.astype(float)


def collision(prims, painted=None, fg=None):
    """100x100 grid: 0 floor, 0x80 blocked, 110 warp 0 (back to the host), and the sprite order.

    `painted` is classify()'s map of the art: a cell then also needs ground painted under its
    footprint, and the small PAINTED_PROPS stop counting (the painter may nudge a rock, and the
    painting decides where it is). Painted non-ground unprojected onto the floor covers both a
    prop's base and the floor hidden behind it, so this also keeps characters from walking behind
    anything the painter drew. `fg` is foreground_pixels(): its pixels are redrawn over characters
    by sprites, so they hide floor rather than block it, and sprite_order() decides who stands in
    front of whom. Without `fg` nothing may be walked behind: backdrops draw behind characters."""
    x, z = cell_grid()
    ok = (in_clearing(x, z) | (path_dist(x, z) < 160)) & (pond_e(x, z, grow=90) > 1)
    if painted is not None:
        prims = [prim for prim in prims if prim["mat"] not in PAINTED_PROPS]
    for prim in prims:                      # footprints, plus a character radius
        if prim["kind"] == "cyl":
            ok &= np.hypot(x - prim["c"][0], z - prim["c"][1]) > prim["r"] + 110
        elif prim["c"][1] + prim["r"][1] > -300:     # an ellipsoid low enough to bump into
            e = dict(c=(prim["c"][0], prim["c"][2]), r=prim["r"][::2])
            if fg is not None and prim["mat"] in FG_MATS:
                # behind a bush (farther from the camera) a character may stand under its overhang, down to
                # the patch it touches the floor with: its sprite hides the character's legs there. In front,
                # its whole width stays blocked, or a character would seem to stand on the drawn bush.
                touch = np.sqrt(max(0.0, 1 - (prim["c"][1] / prim["r"][1]) ** 2))
                base = dict(c=e["c"], r=(e["r"][0] * touch, e["r"][1] * touch))
                behind = z > prim["c"][2]
                ok &= ~((~behind & in_ellipse(x, z, e, grow=90)) | (behind & in_ellipse(x, z, base, grow=60)))
            else:
                ok &= ~in_ellipse(x, z, e, grow=90)
    # whole character inside the painted frame (feet to head, with a margin)
    for y in (0, -380):
        u, v = project(np.stack([x, np.full_like(x, y), z], -1))
        ok &= (u > 24) & (u < BW - 24) & (v > 16) & (v < BH - 8)
    if painted is not None:                 # the painting must show ground under the feet (7 of 9 samples)
        seen = painted if fg is None else np.where(fg["pix"], 0, painted)   # sprite pixels hide floor
        cand = np.argwhere(ok)
        cx, cz = x[ok], z[ok]
        good = np.zeros(len(cx), int)
        for a in [None] + list(np.linspace(0, 2 * np.pi, 8, endpoint=False)):
            ox, oz = (0.0, 0.0) if a is None else (70 * np.cos(a), 70 * np.sin(a))
            u, v = project(np.stack([cx + ox, np.zeros_like(cx), cz + oz], -1))
            good += np.isin(seen[np.clip(v.astype(int), 0, BH - 1), np.clip(u.astype(int), 0, BW - 1)], GROUND)
        ok[tuple(cand[good < 7].T)] = False
    order = None
    if fg is None:                          # nothing may be walked behind: a prop between the eye and the character
        for dx, dz in ((0, 0), (-50, 0), (50, 0), (0, 50)):
            for y in (-20, -180, -340):
                cand = np.argwhere(ok)
                P = np.stack([x + dx, np.full_like(x, y), z + dz], -1)[ok].astype(np.float32)
                dvec = P - VP.astype(np.float32)
                dist = np.linalg.norm(dvec, axis=-1)
                hid = occluded(prims, np.broadcast_to(VP.astype(np.float32), P.shape), dvec / dist[:, None],
                               limit=dist - 5)
                ok[tuple(cand[hid].T)] = False
    else:
        ok, order = sprite_order(ok, fg, x, z)
    grid = np.where(ok, 0, 0x80).astype(np.uint8)
    strip = ok & (z < WARP_STRIP_Z) & (path_dist(x, z) < 160)
    grid[strip] = 110
    return grid, order


# ---- foreground sprites ----------------------------------------------------
def foreground_pixels(prims, painted):
    """Pixels that foreground sprites redraw over characters: the scene's foliage and trunks where
    the painting shows them, grown 6 px into the painter's own foliage round their silhouettes.
    Each pixel carries its prop's sprite group and the camera depth of its surface."""
    import cv2
    d, best, owner, _ = trace(prims, 1)
    surf = (best * (d @ basis()[2].astype(np.float32))).astype(np.float32)
    groups = sorted({p["group"] for p in prims if p["mat"] in FG_MATS})
    gid_of = np.array([groups.index(p["group"]) if p["mat"] in FG_MATS else -1 for p in prims])
    gid = np.where(owner >= 0, gid_of[np.maximum(owner, 0)], -1)
    gid[np.isin(painted, GROUND + (1,))] = -1           # the painting shows floor or water there
    fsurf = np.where(gid >= 0, surf, np.inf).astype(np.float32)
    shifts = [(dy, dx) for dy in (-1, 0, 1) for dx in (-1, 0, 1) if dy or dx]
    for _ in range(6):                                  # into adjacent painted foliage: each new pixel takes
        pad_s = np.pad(fsurf, 1, constant_values=np.inf)   # group and depth from its nearest-surface neighbour
        pad_g = np.pad(gid, 1, constant_values=-1)
        ns = np.stack([pad_s[1 + dy:1 + dy + fsurf.shape[0], 1 + dx:1 + dx + fsurf.shape[1]] for dy, dx in shifts])
        ng = np.stack([pad_g[1 + dy:1 + dy + gid.shape[0], 1 + dx:1 + dx + gid.shape[1]] for dy, dx in shifts])
        pick = ns.argmin(0)
        best_s = np.take_along_axis(ns, pick[None], 0)[0]
        best_g = np.take_along_axis(ng, pick[None], 0)[0]
        add = (gid < 0) & np.isfinite(best_s) & (painted == 2)
        gid[add], fsurf[add] = best_g[add], best_s[add]
    mats = np.array([p["mat"] for p in prims])
    other = (owner >= 0) & ~np.isin(mats[np.maximum(owner, 0)], FG_MATS) & ~np.isin(painted, GROUND + (1,))
    return dict(gid=gid, pix=gid >= 0, groups=groups, fsurf=fsurf, surf=surf, other=other)


def sprite_order(ok, fg, x, z):
    """Who is drawn over whom. A sprite has one depth (its anchor); a character is ordered by its
    own (about its waist). Each candidate cell is sampled over a character's box: a sample hidden
    by a sprite's surface needs that sprite in front (anchor < character - FG_MARGIN); a visible
    sample on sprite pixels needs it behind (anchor > character + FG_MARGIN). Each group's anchor
    is the depth that satisfies the most cells; cells it cannot satisfy, and cells hidden by props
    that have no sprite, stay blocked."""
    cells = np.argwhere(ok)
    cx, cz = x[ok], z[ok]
    zc = depth(np.stack([cx, np.full_like(cx, -190.0), cz], -1))
    n, ns = len(cx), len(CHAR_SAMPLES)
    occ, gid = np.zeros((n, ns), bool), np.full((n, ns), -1)
    uv = np.zeros((n, ns, 2), int)
    bad = np.zeros(n, bool)
    for k, (dx, y, dz) in enumerate(CHAR_SAMPLES):
        P = np.stack([cx + dx, np.full_like(cx, float(y)), cz + dz], -1)
        u, v = project(P)
        ui, vi = np.clip(u.astype(int), 0, BW - 1), np.clip(v.astype(int), 0, BH - 1)
        zs = depth(P)
        g = fg["gid"][vi, ui]
        occ[:, k] = (g >= 0) & (fg["fsurf"][vi, ui] < zs - 5)
        bad |= (g < 0) & fg["other"][vi, ui] & (fg["surf"][vi, ui] < zs - 5)
        gid[:, k], uv[:, k] = g, np.stack([ui, vi], -1)
    anchors = {}
    for g in range(len(fg["groups"])):
        on = gid == g
        rows = on.any(1)
        if not rows.any():
            continue
        front, back = (on & occ).any(1), (on & ~occ).any(1)

        def sat(a):
            return rows & (~front | (a < zc - FG_MARGIN)) & (~back | (a > zc + FG_MARGIN))
        cand = np.unique(np.concatenate([zc[rows] - FG_MARGIN - 1, zc[rows] + FG_MARGIN + 1]))
        a = cand[int(np.argmax([sat(c).sum() for c in cand]))]
        anchors[g] = float(a)
        bad |= rows & ~sat(a)
    ok = ok.copy()
    ok[tuple(cells[bad].T)] = False
    return ok, dict(anchors=anchors, cells=cells, keep=~bad, occ=occ, gid=gid, uv=uv)


def build_sprites(fg, order, idx, prims):
    """Cut the sprites: per group, its pixels inside the boxes of the characters it must cover
    (elsewhere a sprite would only repaint the backdrop's own pixels), split into rects that fit
    a texture page, shelf-packed into 256x256 8bpp pages. Texels are the backdrop's own palette
    indices, 0 (transparent) off the sprite, so the live palette and its time of day apply."""
    need = np.zeros(idx.shape, bool)                    # union of character boxes on walkable cells
    for c in np.nonzero(order["keep"])[0]:
        u, v = order["uv"][c, :, 0], order["uv"][c, :, 1]
        need[max(v.min() - 6, 0):v.max() + 3, max(u.min() - 4, 0):u.max() + 5] = True
    f = basis()[2]
    rects = []
    for g, a in order["anchors"].items():
        m = (fg["gid"] == g) & need
        if not m.any():
            continue
        members = [p for p in prims if p.get("group") == fg["groups"][g]]
        ax = float(np.mean([p["c"][0] for p in members]))
        az = VP[2] + (a - (0 - VP[1]) * f[1] - (ax - VP[0]) * f[0]) / f[2]   # the floor point at that depth
        ys, xs = np.nonzero(m)
        for y0 in range(ys.min(), ys.max() + 1, 248):
            for x0 in range(xs.min(), xs.max() + 1, 248):
                sub = m[y0:min(y0 + 248, ys.max() + 1), x0:min(x0 + 248, xs.max() + 1)]
                if not sub.any():
                    continue
                sy, sx = np.nonzero(sub)
                r = (x0 + sx.min(), y0 + sy.min(), sx.max() - sx.min() + 1, sy.max() - sy.min() + 1)
                rects.append(dict(rect=r, anchor=(int(round(ax)), int(round(az))), group=fg["groups"][g],
                                  depth=a, mask=m[r[1]:r[1] + r[3], r[0]:r[0] + r[2]]))
    pages = [np.zeros((256, 256), np.uint8)]
    shelf = dict(y=0, h=0, x=0)
    for s in sorted(rects, key=lambda s: -s["rect"][3]):
        u, v, w, h = s["rect"]
        if shelf["x"] + w > 255:                        # stay inside the page: UVs of 256 would wrap
            shelf.update(y=shelf["y"] + shelf["h"], h=0, x=0)
        if shelf["y"] + h > 255:
            pages.append(np.zeros((256, 256), np.uint8))
            shelf.update(y=0, h=0, x=0)
        assert len(pages) <= 3, "foreground sprites need more than three texture pages"
        x0, y0 = shelf["x"], shelf["y"]
        pages[-1][y0:y0 + h, x0:x0 + w] = np.where(s["mask"], idx[v:v + h, u:u + w], 0)
        s["tex"] = ((len(pages) - 1) * 256 + x0, y0)
        shelf.update(x=x0 + w, h=max(shelf["h"], h))
    if not rects:                                       # no sprites: no images, and n8 stays 0
        return [], []
    used = [int(np.nonzero(pg.any(1))[0].max()) + 1 if pg.any() else 1 for pg in pages]
    return rects, [pg[:hh] for pg, hh in zip(pages, used)]


def order_errors(grid, fg, order, rects):
    """Cells where the finished sprites would draw wrong over or under a character, checked densely
    (a 5 x 10 unit pitch over the character's box, finer than a pixel): a point a sprite group's
    surface hides must be under a sprite drawn in front of the character, and a visible point
    must not be under one. sprite_order samples coarser; this catches the thin edges it misses."""
    drawn = np.full(fg["gid"].shape, -1)
    for s in rects:
        u, v, w, h = s["rect"]
        drawn[v:v + h, u:u + w][s["mask"]] = fg["groups"].index(s["group"])
    anchor = np.full(len(fg["groups"]) + 1, np.nan)
    for g, a in order["anchors"].items():
        anchor[g] = a
    x, z = cell_grid()
    walk = grid != 0x80
    cx, cz = x[walk], z[walk]
    zc = depth(np.stack([cx, np.full_like(cx, -190.0), cz], -1))
    bad = np.zeros(len(cx), bool)
    for dx in np.arange(-50, 51, 5):
        for y in np.arange(-10, -350, -10):
            for dz in (-40, 0, 40):
                P = np.stack([cx + dx, np.full_like(cx, float(y)), cz + dz], -1)
                u, v = project(P)
                ui, vi = np.clip(u.astype(int), 0, BW - 1), np.clip(v.astype(int), 0, BH - 1)
                hidden = (fg["gid"][vi, ui] >= 0) & (fg["fsurf"][vi, ui] < depth(P) - 5)
                g = drawn[vi, ui]
                front = (g >= 0) & (anchor[g] < zc)
                bad |= (hidden & ~front) | (~hidden & front)
    out = np.zeros(grid.shape, bool)
    out[walk] = bad
    return out


def build_tim(pixels, page, clut):
    """8bpp TIM for texture page `page` (VRAM x 384 + 128*page, y 0). Its CLUT rect is row 480, so
    loadMapImage1 skips uploading it and the sprites sample the live, time-of-day palette."""
    h = pixels.shape[0]
    return (struct.pack("<II", 0x10, 0x09) + struct.pack("<I4H", 12 + 512, 0, 480, 256, 1)
            + clut.astype("<u2").tobytes() + struct.pack("<I4H", 12 + 256 * h, 384 + 128 * page, 0, 128, h)
            + pixels.tobytes())


def build_objects(rects):
    """The MAP object block: sprite definitions, then one still instance per sprite."""
    assert len(rects) <= 180, "the engine keeps 180 sprite definitions (LOCAL_MAP_OBJECTS) and 188 instances"
    out = struct.pack("<h", len(rects))
    for s in rects:
        (u, v, w, h), (tx, ty), (ax, az) = s["rect"], s["tex"], s["anchor"]
        out += struct.pack("<9h", tx, ty, w, h, ax, 0, az, -1, 4)       # clut -1: row 480; 4: opaque
    out += struct.pack("<h", len(rects))
    for k, s in enumerate(rects):
        u, v = s["rect"][:2]
        out += struct.pack("<19h", *([k] + [-1] * 7 + [1] + [0] * 7 + [u, v, 0]))
    return out


def deep_floor(grid, want):
    """The floor cell nearest `want` (x, z) whose 5x5 neighbourhood is all floor: a test target."""
    floor = grid == 0
    deep = floor.copy()
    for dz in range(-2, 3):
        for dx in range(-2, 3):
            deep[2:-2, 2:-2] &= floor[2 + dz:98 + dz, 2 + dx:98 + dx]
    deep[:2], deep[-2:], deep[:, :2], deep[:, -2:] = False, False, False, False
    tz, tx = np.nonzero(deep)
    x, z = cell_centre(tx, tz)
    i = np.argmin(np.hypot(x - want[0], z - want[1]))
    return int(x[i]), int(z[i])


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


# ---- palettes and files ----------------------------------------------------
def to15(rgb):
    c = (rgb[:, 0] >> 3) | ((rgb[:, 1] >> 3) << 5) | ((rgb[:, 2] >> 3) << 10)
    return np.where(c == 0, 0x8000, c).astype(np.uint16)       # 0x0000 is the transparent texel


def quantise(rgb, painted=None):
    """255 colours + Floyd-Steinberg. For painted art the palette is k-means in Lab over a sample
    stratified by material (quota ~ sqrt(area), at least 8% each): median cut spends nearly every
    colour on the greens and leaves the rocks and the pond to dither noise."""
    im = Image.fromarray((rgb.astype(np.uint8) >> 3) << 3)
    if painted is None:
        pal_img = im.quantize(255, method=Image.Quantize.MEDIANCUT)
    else:
        import cv2
        lab = cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(np.float32)
        flat = painted.reshape(-1)
        areas = np.bincount(flat, minlength=len(LABELS)).astype(float)
        share = np.where(areas > 0, np.maximum(np.sqrt(areas) / np.sqrt(areas).sum(), 0.08), 0)   # every material
        quota = share / share.sum() * 60000                                                     # gets colours
        rng = np.random.default_rng(0)
        pick = np.concatenate([rng.choice(np.nonzero(flat == c)[0], int(quota[c])) for c in range(len(LABELS))
                               if areas[c] > 0])
        cv2.setRNGSeed(0)
        _, _, centres = cv2.kmeans(lab[pick], 255, None, (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_MAX_ITER, 30, 0.5),
                                   1, cv2.KMEANS_PP_CENTERS)
        centres = cv2.cvtColor(np.clip(centres, 0, 255).astype(np.uint8)[None], cv2.COLOR_LAB2RGB)[0]
        pal_img = Image.new("P", (1, 1))
        centres = (centres >> 3) << 3
        pal_img.putpalette(np.concatenate([centres, centres[:1]]).reshape(-1).tolist())   # 256th = 1st, folded below
    q = im.quantize(palette=pal_img, dither=Image.Dither.FLOYDSTEINBERG)
    pal = np.array(q.getpalette()[:255 * 3], np.uint16).reshape(-1, 3)
    clut = np.full(256, 0x8000, np.uint16)
    clut[0] = 0                                                  # index 0 reserved, never painted
    clut[1:1 + len(pal)] = to15(pal)
    idx = np.asarray(q, np.uint8)
    idx = np.where(idx > 254, 0, idx)                            # the padding entry is a copy of entry 0
    return (idx.astype(np.int32) + 1).astype(np.uint8), clut


def tint(clut, k):
    """A time-of-day palette over the same indices."""
    r, g, b = clut & 31, (clut >> 5) & 31, (clut >> 10) & 31
    out = (np.minimum(31, r * k[0]).astype(np.uint16) | (np.minimum(31, g * k[1]).astype(np.uint16) << 5)
           | (np.minimum(31, b * k[2]).astype(np.uint16) << 10))
    out = np.where(out == 0, 0x8000, out).astype(np.uint16)
    out[0] = 0
    return out


def build_tfs(idx, cluts):
    out = bytearray(struct.pack("<HHi", GW, GH, len(cluts)))
    for c in cluts:
        out += c.astype("<u2").tobytes()
    for gy in range(GH):
        for gx in range(GW):
            out += struct.pack("<HH", gx * 64, gy * 128)
            out += idx[gy * 128:(gy + 1) * 128, gx * 128:(gx + 1) * 128].tobytes()
    assert len(out) == 8 + 512 * len(cluts) + 16388 * GW * GH <= MAX_FILE
    return bytes(out)


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


def build_map(grid, host_row, host_exit, tims=(), objects=b""):
    """Header (offsets: setup, the 8bpp images, the object block, entities, collision; the images
    and objects only when MAP_ENTRIES says n8 > 0), then the blocks in that order."""
    setup = (list((VP / 2).astype(int)) + list((VR / 2).astype(int)) + [v for l in LIGHTS for v in l]
             + [0, 0, 0, H_PROJ] + [-1] * 8 + [GW, GH] + list(range(GW * GH)))
    warps = np.zeros((6, 10), np.int16)
    warps[4:, :] = -1                                            # unused targets, as stock
    warps[:, 0] = (SPAWN[0], SPAWN[1], SPAWN[2], SPAWN_ROT, host_row, host_exit)
    warps[:, 1] = HOST_SPAWN + (host_row, HOST_SAVE_EXIT)        # where a save resumes: dw_newmaps.c reads this.
                                                                 # No cell is 111, so it never fires as a warp
    blocks = ([struct.pack("<%di" % len(setup), *setup)] + list(tims) + ([objects] if tims else [])
              + [warps.astype("<i2").tobytes() + struct.pack("<h", 1) + BOSS, grid.tobytes()])
    offs, at = [], 4 * len(blocks)
    for b in blocks:
        offs.append(at)
        at += len(b)
    out = struct.pack("<%di" % len(blocks), *offs) + b"".join(blocks)
    assert len(out) <= MAX_FILE
    return out


def sectors(blob):
    return -(-len(blob) // 2048)


def iso_record(f, rr, path):
    """(disc_user offset, record bytes) of a file's ISO directory record. The game finds files through
    FILE_OFFSET_TABLE, but dw_ws_sliver.c finds the TFS through the directory, so both must agree."""
    lba, size = rr["lba"], rr["size"]
    for part in path:
        data = read_extent(f, lba, size)
        i = 0
        while True:
            rec = parse_record(data[i:])
            if rec is None:
                i = (i // 2048 + 1) * 2048                    # records never span sectors
                assert i < len(data), path
            elif rec["name"].split(b";")[0] == part.encode():
                break
            else:
                i += rec["len"]
        where, lba, size = lba * 2048 + i, rec["lba"], rec["size"]
    return where, data[i:i + rec["len"]]


# ---- package ---------------------------------------------------------------
def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", default=os.path.join(root, "disc", "Digimon World (USA).bin"))
    ap.add_argument("--build", default=os.path.join(root, "build-debug"))
    ap.add_argument("--art", default=os.path.join(root, "art", "newmaps", "glade.png"),
                    help="painted art (paint_newmap.py); the flat guide is used if it does not exist")
    ap.add_argument("--preview", action="store_true", help="render previews only")
    a = ap.parse_args()
    prev = os.path.join(a.build, "newmap")
    os.makedirs(prev, exist_ok=True)
    pal = lambda c: np.stack([(c & 31) << 3, ((c >> 5) & 31) << 3, ((c >> 10) & 31) << 3], -1).astype(np.uint8)

    prims = scene()
    Image.fromarray(guide(prims, frame=PAINT).astype(np.uint8)).save(os.path.join(prev, "guide.png"))
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
        fg = foreground_pixels(prims, painted)
    else:
        print("no painted art at %s: using the flat guide, without foreground sprites" % a.art)
        rgb = guide(prims)
    grid, order = collision(prims, painted, fg)
    sx, sz = SPAWN[0], SPAWN[2]                  # tile maths as getTileTrigger (C truncation)
    assert grid[50 - int(sz / 100) - (sz > 0), int(sx / 100) + 50 - (sx < 0)] == 0, "spawn must be floor"
    assert (grid == 110).any(), "the warp strip back to the host must be reachable floor"
    idx, day = quantise(rgb, painted)
    cluts = [day, tint(day, (1.0, 0.78, 0.62)), tint(day, (0.42, 0.5, 0.78))]
    assert not (day[idx] == 0).any()
    rects, pages = build_sprites(fg, order, idx, prims) if fg is not None else ([], [])
    if rects:
        bad = order_errors(grid, fg, order, rects)
        grid[bad] = 0x80
        print("dense order check: %d cells blocked" % bad.sum())
        assert grid[50 - int(sz / 100) - (sz > 0), int(sx / 100) + 50 - (sx < 0)] == 0 and (grid == 110).any()
    bx, bz = BOSS_AT[0], BOSS_AT[2]
    assert grid[50 - int(bz / 100) - (bz > 0), int(bx / 100) + 50 - (bx < 0)] == 0, "the boss must stand on floor"
    tims = [build_tim(pg, k, day) for k, pg in enumerate(pages)]
    objects = build_objects(rects) if rects else b""
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
        u, v = project((x, 0, z))
        if 0 <= u < BW and 0 <= v < BH:
            ov[int(v) - 1:int(v) + 2, int(u) - 1:int(u) + 2] = (0, 255, 0) if g == 0 else (0, 128, 255)
    u, v = project(SPAWN)
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
    mapb, tfsb = build_map(grid, HOST_ROW, HOST_WARP, tims, objects), build_tfs(idx, cluts)
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
    os.makedirs(os.path.join(pkg, "assets"), exist_ok=True)
    ms = sectors(mapb)                          # our MAP, then our TFS, across MGEN17's 247 sectors
    blobs = [("glade.map", slot, mapb.ljust(ms * 2048, b"\0")),
             ("glade.tfs", slot + ms, tfsb.ljust((MAP_SECTORS + TFS_SECTORS - ms) * 2048, b"\0"))]
    for name, _, blob in blobs:
        open(os.path.join(pkg, "assets", name), "wb").write(blob)
    section = (bytes([0xFB, 0]) + struct.pack("<HH", SCRIPT, ROW) + bytes([0x1E, 0, 0xF5, BGM_PSTAT])
               + bytes([0x5D, BGM]) + bytes([0x46, BOSS_SPECIES, 0x47, BOSS_SPECIES, 0, 0]) + b"\xfe\x00")
    assert MH_NEW + len(section) <= MH_BUFFER
    row = FILE.encode().ljust(10, b"\0") + bytes([len(tims), 0, FLAGS, 0, 0, LOADING_NAME])
    mh_base = files["MAPHEAD.SCN"][0] * 2048
    out = ["# New maps. Generated by tools/gen_newmap.py: regenerate rather than hand-editing, so every guard stays",
           "# byte-exact. assets/ is this port's own map and art (no game art or data); regenerating means updating",
           "# MOD_PAYLOADS in tools/verify_setup_package.py. Isolated keeps savestates from crossing into a plan",
           "# without the map; memory cards stay shared, because dw_newmaps.c keeps the new row off them.",
           "format_version = 12", 'id = "dw.newmaps"', 'version = "1.0.0"', 'name = "New Maps"',
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
           'exact disc image listed in DISC.md. Turns off Digimon World 1.5, which rewrites the same files."',
           'group = "New maps"', "default_enabled = false",
           'conflicts = ["dw.1_5/dw15", "dw.newmaps.spike/spike"]', "",
           "# dw_newmaps.c: a save made on the new map names its host instead.",
           "[[plugin]]", 'feature = "glade"', 'id = "dw.newmaps"', ""]
    for name, lba, blob in blobs:
        out += ["# our %s in %s's sectors" % (name, FILE), "[[overlay]]", 'feature = "glade"',
                'target = "disc_user"', "offset = %d" % (lba * 2048), 'file = "assets/%s"' % name,
                'sha256 = "%s"' % hashlib.sha256(blob).hexdigest(), ""]
    # A file, not a [[patch]]: a patch must repeat the stock bytes it replaces, and these are live script bytecode.
    # The generator's asserts above and disc_sha256 guard the slot.
    open(os.path.join(pkg, "assets", "glade.dg"), "wb").write(dg_new)
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
    with open(os.path.join(pkg, "manifest.toml"), "w", newline="\n") as fh:
        fh.write("\n".join(out))
    print("wrote %s" % pkg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
