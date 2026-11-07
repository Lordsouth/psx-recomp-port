#!/usr/bin/env python3
"""Build an authored field map (our own art, collision and camera) for the free map row 239.

Everything the game loads for the new screen comes from the scene described in
scene() below: no byte of the game disc is copied. One description drives all
three outputs, so they cannot drift apart:

  * the backdrop, ray-traced from the map's own game camera with the projection
    the engine uses (INTERNALS.md 3.1: u = 64W + h*Xc/Zc, v = 64H + h*Yc/Zc,
    world +Y down, floor y = 0), quantised to one 255-colour 15-bit palette and
    tinted into the three time-of-day palettes the engine swaps between;
  * the 100x100 collision grid: floor where the scene says ground, blocked under
    props, the pond and anything outside the painted frame, and blocked wherever
    a prop would cover a character standing there (backdrops always draw behind
    characters, so nothing may be walked behind);
  * the MAP setup block (camera, lights, tile grid) and its warp table.

The files replace the unused MGEN17.MAP/TFS sectors through two file-backed
[[overlay]]s, both zero-padded to the stock sector counts so no stock byte of
those files reaches RAM. The remaining writes are small guarded patches: the
row itself, the row's MAPHEAD section (the stock filler binds the sewer
script), and, for now, Coela Point's east exit as a dev entrance.

    py -3.12 tools/gen_newmap.py [--bin disc.bin] [--build build-debug] [--preview]

Writes build/mods/installed/dw.newmaps/1.0.0/ (local only: entrance is a dev
redirect, no save isolation yet) and build/newmap/ previews. verify_newmap.py
checks it in the game.
"""
import argparse
import hashlib
import json
import os
import struct
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extract_disc_file import parse_record, read_extent, sector, walk  # noqa: E402

# ---- the screen ------------------------------------------------------------
ROW, FILE = 239, "MGEN17"            # free row; unused stock file slot we overlay
MAP_SECTORS, TFS_SECTORS = 6, 241    # MGEN17's own extents; 241 = the GENERAL_BUFFER window
MAP_ENTRIES = 0x801292D4
FLAGS = 0x80                         # sound bank 0 (Native Forest), time of day on, map-Digimon format
LOADING_NAME = 0                     # "Native Forest" on the arrival card
SCRIPT = 118                         # DG script whose only section is a bare END: no arrival side effects
BGM, BGM_PSTAT = 0x01, 0             # the Native Forest theme, day/night variants as MAYO01/02
MH_NEW, MH_BUFFER = 0x6070, 0x61A8   # MAPHEAD.SCN ends at 0x606E; MAPHEAD_SCRIPT_BUFFER size
HOST, HOST_ROW, HOST_WARP = "MAYO02", 1, 2   # dev entrance: Coela Point's east exit (stock -> map 4)

GW, GH = 6, 5                        # tile grid: 768 x 640 backdrop, 30 tiles
VP = np.array([0.0, -5600.0, -7400.0])    # eye; the MAP stores half (the engine doubles it)
VR = np.array([0.0, 0.0, -1500.0])        # look-at point, lands on the backdrop centre
H_PROJ = 927                              # GsSetProjection distance
LIGHTS = [(0, 170, -30, 178, 178, 178), (1, 1, 1, 0, 0, 0), (1, 1, 1, 0, 0, 0)]  # as the MAYO maps
SUN = np.array([0.35, 1.0, -0.55])        # direction the sunlight travels (+Y down): from behind-left
SUN /= np.linalg.norm(SUN)

SPAWN = (-100, 0, -3500)                  # arrival, facing up the path (rotation 0x800 = +z)
SPAWN_ROT = 0x800
WARP_STRIP_Z = -4050                      # path cells nearer the camera than this send you back

BW, BH = GW * 128, GH * 128


# ---- scene -----------------------------------------------------------------
def scene():
    """Props as primitives in world units (100 = one collision cell)."""
    p = []

    def tree(x, z, s=1.0, tone=0.0):
        p.append(dict(kind="cyl", c=(x, z), r=55 * s, y0=-420 * s, y1=0, mat="bark"))
        for dx, dy, dz, r in ((0, -620, 0, 330), (-160, -470, 60, 230), (170, -500, -40, 240), (20, -820, 30, 220)):
            p.append(dict(kind="ell", c=(x + dx * s, dy * s, z + dz * s), r=(r * s, r * s * 0.85, r * s),
                          mat="leaf", tone=tone))

    def rock(x, z, rx, ry, rz):
        p.append(dict(kind="ell", c=(x, -ry * 0.35, z), r=(rx, ry, rz), mat="rock"))

    def bush(x, z, r):
        p.append(dict(kind="ell", c=(x, -r * 0.55, z), r=(r * 1.25, r, r), mat="leaf", tone=0.4))

    # forest: three staggered rows across the back, tree lines hugging both frame edges
    for row, z in enumerate((2700, 3350, 4050, 4750)):
        for i, x in enumerate(range(-5600, 5700, 600)):
            tree(x + (row % 2) * 300 + (i % 3) * 60, z + (i % 2) * 180, 1.15 + 0.1 * ((i + row) % 3),
                 tone=((i + row) % 4) * 0.25)
    for i, z in enumerate(range(-5000, 2700, 520)):
        edge = half_width(z)
        tree(-edge + 120 - (i % 2) * 260, z, 1.0 + 0.1 * (i % 3), tone=(i % 3) * 0.3)
        tree(edge - 120 + (i % 2) * 260, z + 260, 1.0 + 0.1 * ((i + 1) % 3), tone=((i + 1) % 3) * 0.3)
    # inside the meadow
    tree(-1500, 1700, 1.35, tone=0.5)
    tree(1300, 2050, 1.2, tone=0.2)
    rock(1700, 700, 300, 220, 260)
    rock(2000, 520, 150, 110, 130)
    rock(-2100, -1200, 280, 200, 250)
    rock(1550, -2600, 210, 150, 190)
    rock(-650, 1300, 120, 80, 100)
    bush(-2400, 600, 210)
    bush(2550, -600, 190)
    bush(-1300, -2700, 160)
    bush(450, 1900, 170)
    return p


def half_width(z):
    """Half the floor width the frame shows at depth z. The constants are this camera's
    backdrop corners unprojected onto the floor: (+-2469, -4488) bottom, (+-5284, 4895) top."""
    t = (z + 4488) / (4895 + 4488)
    return 2469 + t * (5284 - 2469)


PATH = np.array([[-150, -4900], [-100, -3600], [150, -2400], [-150, -1300], [100, -200], [300, 700]], float)
POND = dict(c=(-1300, 600), r=(650, 380))
GLADE = dict(c=(0, -900), r=(3200, 3300))


def path_dist(x, z):
    """Distance to the dirt path's centre line."""
    d = np.full(np.shape(x), np.inf)
    for a, b in zip(PATH[:-1], PATH[1:]):
        ab = b - a
        t = np.clip(((x - a[0]) * ab[0] + (z - a[1]) * ab[1]) / (ab @ ab), 0, 1)
        d = np.minimum(d, np.hypot(x - (a[0] + t * ab[0]), z - (a[1] + t * ab[1])))
    return d


def in_ellipse(x, z, e, grow=0.0):
    return ((x - e["c"][0]) / (e["r"][0] + grow)) ** 2 + ((z - e["c"][1]) / (e["r"][1] + grow)) ** 2 <= 1


# ---- camera ----------------------------------------------------------------
def basis():
    f = (VR - VP) / np.linalg.norm(VR - VP)
    r = np.array([f[2], 0.0, -f[0]]) / np.hypot(f[0], f[2])
    return r, np.cross(f, r), f


def project(P):
    r, d, f = basis()
    q = np.asarray(P, float) - VP
    zc = q @ f
    return 64 * GW + H_PROJ * (q @ r) / zc, 64 * GH + H_PROJ * (q @ d) / zc


def rays(width, height, scale):
    """World-space ray directions through backdrop pixel centres (render at `scale`x)."""
    r, d, f = basis()
    v, u = np.mgrid[0:height, 0:width].astype(np.float32)
    xu = ((u + 0.5) / scale - 64 * GW) / H_PROJ
    yv = ((v + 0.5) / scale - 64 * GH) / H_PROJ
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


# ---- materials -------------------------------------------------------------
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
    n1, n2, fine = noise(x, z, 900, 4, 1), noise(x, z, 260, 3, 2), noise(x, z, 30, 2, 3)
    grass = mix([62, 112, 40], [118, 150, 62], np.clip(n1 * 1.4 - 0.25, 0, 1))
    grass = mix(grass, [42, 86, 32], np.clip(0.9 - n2 * 1.6, 0, 1) * 0.6)
    grass *= (0.85 + 0.3 * fine)[..., None]
    pd = path_dist(x, z) + (noise(x, z, 160, 2, 4) - 0.5) * 120
    dirt = mix([132, 102, 66], [98, 76, 50], noise(x, z, 120, 3, 5))
    dirt *= (0.9 + 0.2 * noise(x, z, 18, 2, 6))[..., None]
    col = mix(grass, dirt, np.clip((190 - pd) / 60, 0, 1))
    edge = ((x - POND["c"][0]) / POND["r"][0]) ** 2 + ((z - POND["c"][1]) / POND["r"][1]) ** 2
    mud = np.clip((1.25 - edge) / 0.25, 0, 1)
    col = mix(col, [92, 82, 56], mud * 0.8)
    water = mix([34, 70, 98], [70, 118, 140], np.clip((z - POND["c"][1] + POND["r"][1]) / (2 * POND["r"][1]), 0, 1))
    water *= (0.92 + 0.16 * noise(x * 3, z, 300, 2, 7))[..., None]
    return np.where((edge < 1)[..., None], water, col), edge < 1


def prop_albedo(prim, pt, n):
    x, y, z = pt[..., 0], pt[..., 1], pt[..., 2]
    if prim["mat"] == "bark":
        streak = noise(np.arctan2(z - prim["c"][1], x - prim["c"][0]) * 400, y, 90, 3, 11)
        return mix([70, 50, 34], [112, 84, 58], streak)
    if prim["mat"] == "leaf":
        clump = noise(x + y * 0.5, z - y * 0.3, 110, 4, 12 + int(prim.get("tone", 0) * 10))
        base = mix([30, 74, 32], [70, 118, 44], np.clip(prim.get("tone", 0) * 0.5 + clump * 0.8, 0, 1))
        return base * (0.7 + 0.5 * clump)[..., None]
    moss = np.clip(-n[..., 1] * 1.5 - 0.6, 0, 1) * noise(x, z, 80, 2, 13)
    stone = mix([104, 102, 96], [150, 146, 136], noise(x + y, z, 70, 4, 14))
    return mix(stone, [70, 104, 48], moss)


# ---- rendering -------------------------------------------------------------
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


def render(prims, scale=2):
    w, h = BW * scale, BH * scale
    d = rays(w, h, scale)
    vp32 = VP.astype(np.float32)
    best = np.where(d[..., 1] > 0, -VP[1] / np.maximum(d[..., 1], 1e-9), np.inf).astype(np.float32)
    owner = np.full((h, w), -1, np.int32)
    normal = np.zeros_like(d)
    normal[..., 1] = -1
    for i, prim in enumerate(prims):                 # trace each prop only inside its screen box
        u, v = project(corners(prim))
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
    pt = vp32 + d * best[..., None]
    alb = np.zeros_like(d)
    gmask = owner < 0
    galb, water = ground_albedo(pt[..., 0], pt[..., 2])
    alb[gmask] = galb[gmask]
    for i, prim in enumerate(prims):
        m = owner == i
        if m.any():
            alb[m] = prop_albedo(prim, pt[m], normal[m])
    lit = sunlit(shadow_map(prims), pt + normal * 2.0)
    lam = np.clip((normal * -SUN.astype(np.float32)).sum(-1), 0, 1)
    sky = 0.42 + 0.18 * np.clip(-normal[..., 1], 0, 1)
    light = sky + 0.78 * lam * lit
    spec = np.where(water, 0.25 * lit * np.clip(1 - np.abs(d[..., 0]) * 3, 0, 1), 0)   # sun glint
    rgb = alb * light[..., None] + 255 * spec[..., None]
    rgb = rgb.reshape(BH, scale, BW, scale, 3).mean((1, 3))
    return np.clip(rgb, 0, 255)


# ---- collision -------------------------------------------------------------
def cell_centre(tx, tz):
    return (tx - 50) * 100 + 50, (50 - tz) * 100 - 50


def collision(prims):
    """100x100 grid: 0 floor, 0x80 blocked, 110 warp 0 (back to the host)."""
    tz, tx = np.mgrid[0:100, 0:100]
    x, z = cell_centre(tx, tz)
    x, z = x.astype(float), z.astype(float)
    ok = (in_ellipse(x, z, GLADE) | (path_dist(x, z) < 160)) & ~in_ellipse(x, z, POND, grow=90)
    for prim in prims:                      # footprints, plus a character radius
        if prim["kind"] == "cyl":
            ok &= np.hypot(x - prim["c"][0], z - prim["c"][1]) > prim["r"] + 110
        elif prim["c"][1] + prim["r"][1] > -300:     # an ellipsoid low enough to bump into
            ok &= ~in_ellipse(x, z, dict(c=(prim["c"][0], prim["c"][2]), r=prim["r"][::2]), grow=90)
    # whole character inside the painted frame (feet to head, with a margin)
    for y in (0, -380):
        u, v = project(np.stack([x, np.full_like(x, y), z], -1))
        ok &= (u > 24) & (u < BW - 24) & (v > 16) & (v < BH - 8)
    # nothing may be walked behind: a prop between the eye and the character
    pts = []
    for dx, dz in ((0, 0), (-50, 0), (50, 0), (0, 50)):
        for y in (-20, -180, -340):
            pts.append(np.stack([x + dx, np.full_like(x, y), z + dz], -1))
    cand = np.argwhere(ok)
    for pnt in pts:
        P = pnt[ok].astype(np.float32)
        dvec = P - VP.astype(np.float32)
        dist = np.linalg.norm(dvec, axis=-1)
        hid = occluded(prims, np.broadcast_to(VP.astype(np.float32), P.shape), dvec / dist[:, None], limit=dist - 5)
        ok[tuple(cand[hid].T)] = False
        cand = np.argwhere(ok)
    grid = np.where(ok, 0, 0x80).astype(np.uint8)
    strip = ok & (z < WARP_STRIP_Z) & (path_dist(x, z) < 160)
    grid[strip] = 110
    return grid


# ---- palettes and files ----------------------------------------------------
def to15(rgb):
    c = (rgb[:, 0] >> 3) | ((rgb[:, 1] >> 3) << 5) | ((rgb[:, 2] >> 3) << 10)
    return np.where(c == 0, 0x8000, c).astype(np.uint16)       # 0x0000 is the transparent texel


def quantise(rgb):
    im = Image.fromarray((rgb.astype(np.uint8) >> 3) << 3)
    pal_img = im.quantize(255, method=Image.Quantize.MEDIANCUT)
    q = im.quantize(palette=pal_img, dither=Image.Dither.FLOYDSTEINBERG)
    pal = np.array(q.getpalette()[:255 * 3], np.uint16).reshape(-1, 3)
    clut = np.full(256, 0x8000, np.uint16)
    clut[0] = 0                                                  # index 0 reserved, never painted
    clut[1:1 + len(pal)] = to15(pal)
    return (np.asarray(q, np.uint8).astype(np.int32) + 1).astype(np.uint8), clut


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
    assert len(out) == 8 + 512 * len(cluts) + 16388 * GW * GH <= TFS_SECTORS * 2048
    return bytes(out).ljust(TFS_SECTORS * 2048, b"\0")


def build_map(grid, host_row, host_exit):
    setup = (list((VP / 2).astype(int)) + list((VR / 2).astype(int)) + [v for l in LIGHTS for v in l]
             + [0, 0, 0, H_PROJ] + [-1] * 8 + [GW, GH] + list(range(GW * GH)))
    setup_b = struct.pack("<%di" % len(setup), *setup)
    warps = np.zeros((6, 10), np.int16)
    warps[4:, :] = -1                                            # unused targets, as stock
    warps[:, 0] = (SPAWN[0], SPAWN[1], SPAWN[2], SPAWN_ROT, host_row, host_exit)
    entity = warps.astype("<i2").tobytes() + struct.pack("<h", 0)  # no map Digimon
    ent_off = 12 + len(setup_b)
    coll_off = ent_off + len(entity)
    out = struct.pack("<3i", 12, ent_off, coll_off) + setup_b + entity + grid.tobytes()
    assert len(out) <= MAP_SECTORS * 2048
    return out.ljust(MAP_SECTORS * 2048, b"\0")


# ---- package ---------------------------------------------------------------
def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", default=os.path.join(root, "disc", "Digimon World (USA).bin"))
    ap.add_argument("--build", default=os.path.join(root, "build-debug"))
    ap.add_argument("--preview", action="store_true", help="render previews only")
    a = ap.parse_args()
    prev = os.path.join(a.build, "newmap")
    os.makedirs(prev, exist_ok=True)

    prims = scene()
    grid = collision(prims)
    sx, sz = SPAWN[0], SPAWN[2]                  # tile maths as getTileTrigger (C truncation)
    assert grid[50 - int(sz / 100) - (sz > 0), int(sx / 100) + 50 - (sx < 0)] == 0, "spawn must be floor"
    rgb = render(prims)
    idx, day = quantise(rgb)
    cluts = [day, tint(day, (1.0, 0.78, 0.62)), tint(day, (0.42, 0.5, 0.78))]
    assert not (day[idx] == 0).any()
    pal = lambda c: np.stack([(c & 31) << 3, ((c >> 5) & 31) << 3, ((c >> 10) & 31) << 3], -1).astype(np.uint8)
    for name, c in zip(("day", "dusk", "night"), cluts):
        Image.fromarray(pal(c)[idx]).save(os.path.join(prev, "backdrop_%s.png" % name))
    ov = pal(day)[idx].astype(float)
    tz, tx = np.nonzero(grid != 0x80)
    for cx, cz, g in zip(tx, tz, grid[tz, tx]):
        x, z = cell_centre(cx, cz)
        u, v = project((x, 0, z))
        if 0 <= u < BW and 0 <= v < BH:
            ov[int(v) - 1:int(v) + 2, int(u) - 1:int(u) + 2] = (0, 255, 0) if g == 0 else (0, 128, 255)
    u, v = project(SPAWN)
    ov[int(v) - 3:int(v) + 4, int(u) - 3:int(u) + 4] = (255, 255, 0)
    Image.fromarray(ov.astype(np.uint8)).save(os.path.join(prev, "collision_overlay.png"))
    print("walkable cells %d, warp cells %d, colours %d" % ((grid == 0).sum(), (grid == 110).sum(), len(np.unique(idx))))
    if a.preview:
        return 0

    f = open(a.bin, "rb")
    listing = []
    rr = parse_record(sector(f, 16)[156:190])
    walk(f, rr["lba"], rr["size"], "", listing)
    files = {os.path.basename(n): (lba, size) for n, lba, size in listing}
    rd = lambda n: read_extent(f, *files[n])
    hexs = lambda b: " ".join("%02x" % x for x in b)
    for name, sectors in ((FILE + ".MAP", MAP_SECTORS), (FILE + ".TFS", TFS_SECTORS)):
        assert -(-files[name][1] // 2048) == sectors, name
    dg = rd("DG.SCN")
    so = struct.unpack_from("<I", dg, 4 * SCRIPT)[0]
    assert dg[so + 2:so + 8] == bytes.fromhex("fe000800ffff") and dg[so + 8:so + 10] == b"\xfe\x00"
    mh = rd("MAPHEAD.SCN")
    p = 2
    while struct.unpack_from("<H", mh, p)[0] != ROW:
        p += 4
    assert mh[struct.unpack_from("<H", mh, p + 2)[0]:][:6] == bytes.fromhex("fb00b400ef00"), "stock filler"
    assert len(mh) <= MH_NEW and not any(sector(f, files["MAPHEAD.SCN"][0] + MH_NEW // 2048)[MH_NEW % 2048:][:32])
    hd = rd(HOST + ".MAP")
    hent = struct.unpack_from("<i", hd, 4 * (struct.unpack_from("<i", hd)[0] // 4 - 2))[0]
    host_map_off = hent + 80 + HOST_WARP * 2
    stock_target = hd[host_map_off:host_map_off + 2]
    assert struct.unpack_from("<h", hd, hent + 100 + HOST_WARP * 2)[0] == 0      # arrives at our spawn 0
    h = hashlib.sha256()
    f.seek(0)
    for block in iter(lambda: f.read(1 << 22), b""):
        h.update(block)
    disc_sha = h.hexdigest()

    pkg = os.path.join(a.build, "mods", "installed", "dw.newmaps", "1.0.0")
    os.makedirs(os.path.join(pkg, "assets"), exist_ok=True)
    mapb, tfsb = build_map(grid, HOST_ROW, HOST_WARP), build_tfs(idx, cluts)
    for name, blob in (("glade.map", mapb), ("glade.tfs", tfsb)):
        open(os.path.join(pkg, "assets", name), "wb").write(blob)
    section = (bytes([0xFB, 0]) + struct.pack("<HH", SCRIPT, ROW) + bytes([0x1E, 0, 0xF5, BGM_PSTAT])
               + bytes([0x5D, BGM]) + b"\xfe\x00")
    row = FILE.encode().ljust(10, b"\0") + bytes([0, 0, FLAGS, 0, 0, LOADING_NAME])
    mh_base = files["MAPHEAD.SCN"][0] * 2048
    out = ["# LOCAL ONLY (dev entrance, no save isolation). Generated by tools/gen_newmap.py.",
           "format_version = 12", 'id = "dw.newmaps"', 'version = "1.0.0"', 'name = "New Maps (dev)"',
           'author = "DigimonWorldRecomp"',
           'description = "An authored glade on the free map row 239, reached from Coela Point\'s east exit."',
           'resolver = "declarative"', 'save_compatibility = "shared"', "",
           "[[target]]", 'game_id = "SLUS-01032"', 'disc_sha256 = "%s"' % disc_sha, "",
           "[[feature]]", 'id = "glade"', 'name = "Hidden Glade"', 'description = "Dev build of the first authored map."',
           'group = "Dev"', "default_enabled = false",
           'conflicts = ["dw.1_5/dw15", "dw.newmaps.spike/spike"]', ""]
    for name, lba, blob in (("glade.map", files[FILE + ".MAP"][0], mapb), ("glade.tfs", files[FILE + ".TFS"][0], tfsb)):
        out += ["# our %s over %s's own sectors" % (name, FILE), "[[overlay]]", 'feature = "glade"',
                'target = "disc_user"', "offset = %d" % (lba * 2048), 'file = "assets/%s"' % name,
                'sha256 = "%s"' % hashlib.sha256(blob).hexdigest(), ""]
    patches = [("MAP_ENTRIES[%d] = %s" % (ROW, FILE), 'target = "main_exe"\naddress = 0x%08X\nexpected = "%s"\nreplace = "%s"'
                % (MAP_ENTRIES + ROW * 16, hexs(bytes(16)), hexs(row))),
               ("MAPHEAD section %d body" % ROW, 'target = "disc_user"\noffset = %d\nexpected = "%s"\nreplace = "%s"'
                % (mh_base + MH_NEW, hexs(bytes(len(section))), hexs(section))),
               ("MAPHEAD section %d -> 0x%x" % (ROW, MH_NEW), 'target = "disc_user"\noffset = %d\nexpected = "%s"\nreplace = "%s"'
                % (mh_base + p + 2, hexs(mh[p + 2:p + 4]), hexs(struct.pack("<H", MH_NEW)))),
               ("dev entrance: %s warp %d -> map %d" % (HOST, HOST_WARP, ROW), 'target = "disc_user"\noffset = %d\nexpected = "%s"\nreplace = "%s"'
                % (files[HOST + ".MAP"][0] * 2048 + host_map_off, hexs(stock_target), hexs(struct.pack("<h", ROW))))]
    for why, body in patches:
        out += ["# " + why, "[[patch]]", 'feature = "glade"', body, ""]
    with open(os.path.join(pkg, "manifest.toml"), "w", newline="\n") as fh:
        fh.write("\n".join(out))
    spec = dict(row=ROW, host_row=HOST_ROW, host_exit=HOST_WARP, spawn=SPAWN, strip_z=WARP_STRIP_Z,
                camera=dict(vp=VP.tolist(), vr=VR.tolist(), h=H_PROJ, gw=GW, gh=GH),
                path=PATH.tolist(), backdrop=os.path.join(prev, "backdrop_day.png"))
    json.dump(spec, open(os.path.join(prev, "spec.json"), "w"), indent=1)
    print("wrote %s (MAP %d B, TFS %d B before padding)" % (pkg, len(mapb.rstrip(b"\0")), 8 + 3 * 512 + 16388 * GW * GH))
    return 0


if __name__ == "__main__":
    sys.exit(main())
