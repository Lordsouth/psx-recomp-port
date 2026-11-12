#!/usr/bin/env python3
"""The shared machinery behind the authored field maps (gen_newmap.py's glade, gen_factory.py's screens).

A `Screen` holds everything one map screen needs (camera, tile grid, materials, lights, floor test, warp
strip, warps, entity records); the functions below take it as their first argument and turn a scene (a
list of primitives, world units, 100 = one collision cell, +Y down, floor y = 0) into the guide render,
the material label map, the 100x100 collision grid, foreground sprites and the MAP / TFS files. The
primitives are `ell` (ellipsoid: c, r), `cyl` (vertical cylinder: c = (x, z), r, y0, y1) and `box`
(axis-aligned box: c, r = half-extents). Facts about the formats are in docs/GAME.md section 3.

    py -3.12 -I tools/newmap_engine.py      # self-check of the box primitive
"""
import struct
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
from PIL import Image

MAX_FILE = 0x78800                   # readFile's window, GENERAL_BUFFER up to TEXTURE_BUFFER (241 sectors)
FG_MARGIN = 100                      # camera-depth gap kept between a sprite's anchor and a character it overlaps
CHAR_SAMPLES = [(dx, y, dz) for dx in np.arange(-50, 51, 12.5) for y in np.arange(-10, -350, -25) for dz in (-40, 0, 40)]
                                     # a character's box at about pixel pitch: a pixel is 7-15 units here
DEFAULT_SUN = np.array([0.35, 1.0, -0.55]) / np.linalg.norm([0.35, 1.0, -0.55])   # +Y down: from behind-left


def _flat_albedo(x, z):
    col = np.zeros(np.shape(x) + (3,))
    col[:] = (104, 140, 60)
    return col


@dataclass
class Screen:
    name: str
    row: int
    vp: np.ndarray                   # eye; the MAP stores half (the engine doubles it)
    vr: np.ndarray                   # look-at point
    h: int                           # GsSetProjection distance
    grid: tuple                      # (GW, GH): the backdrop in 128 px tiles
    paint: tuple                     # the frame the art is painted in, in tiles (a superset of the backdrop)
    lights: list
    labels: tuple                    # material names, indexed by label
    ground: tuple                    # labels a character may stand on
    mats: dict                       # prop material -> label
    flat: dict                       # prop material -> guide colour
    fg_mats: tuple                   # materials drawn over characters by foreground sprites
    painted_props: tuple             # small props whose collision follows the painting
    floor: Callable                  # (x, z) -> bool array: walkable before props
    strips: dict                     # collision value -> (x, z) -> bool array of cells
    warps: np.ndarray                # 6 x 10 int16: the MapWarps block
    records: list                    # entity records (bytes)
    regions: tuple = (-1,) * 8       # A0..A3, B0..B3
    albedo: Callable = _flat_albedo  # (x, z) -> rgb of the bare floor, for the guide
    floor_label: Callable = lambda x, z: np.zeros(np.shape(x), np.uint8)   # (x, z) -> label of the bare floor
    water: Callable = lambda x, z: np.zeros(np.shape(x), bool)             # (x, z) -> sun glints here
    sun: np.ndarray = field(default_factory=lambda: DEFAULT_SUN.copy())    # direction the sunlight travels

    @property
    def bw(self):
        return self.grid[0] * 128

    @property
    def bh(self):
        return self.grid[1] * 128

    @property
    def pbw(self):
        return self.paint[0] * 128

    @property
    def pbh(self):
        return self.paint[1] * 128

    @property
    def crop(self):
        return (self.paint[1] - self.grid[1]) * 64      # rows cut from the top (and bottom) of the painted art


# ---- camera ----------------------------------------------------------------
def basis(scr):
    f = (scr.vr - scr.vp) / np.linalg.norm(scr.vr - scr.vp)
    r = np.array([f[2], 0.0, -f[0]]) / np.hypot(f[0], f[2])
    return r, np.cross(f, r), f


def project(scr, P, frame=None):
    """Backdrop pixel (u, v) of world points; `frame` (tiles wide, high) defaults to the backdrop's."""
    gw, gh = frame or scr.grid
    r, d, f = basis(scr)
    q = np.asarray(P, float) - scr.vp
    zc = q @ f
    return 64 * gw + scr.h * (q @ r) / zc, 64 * gh + scr.h * (q @ d) / zc


def depth(scr, P):
    """Camera-space z: what the GTE puts in SZ, and what map objects and models are ordered by (>> 2)."""
    return (np.asarray(P, float) - scr.vp) @ basis(scr)[2]


def rays(scr, width, height, scale, frame=None):
    """World-space ray directions through backdrop pixel centres (render at `scale`x)."""
    gw, gh = frame or scr.grid
    r, d, f = basis(scr)
    v, u = np.mgrid[0:height, 0:width].astype(np.float32)
    xu = ((u + 0.5) / scale - 64 * gw) / scr.h
    yv = ((v + 0.5) / scale - 64 * gh) / scr.h
    dirs = xu[..., None] * r + yv[..., None] * d + f
    return (dirs / np.linalg.norm(dirs, axis=-1, keepdims=True)).astype(np.float32)


def cell_centre(tx, tz):
    return (tx - 50) * 100 + 50, (50 - tz) * 100 - 50


def tile_of(x, z):
    """The collision cell (tx, tz) a point is in: tile maths as getTileTrigger (C truncation)."""
    return int(x / 100) + 50 - (x < 0), 50 - int(z / 100) - (z > 0)


def cell_grid():
    tz, tx = np.mgrid[0:100, 0:100]
    x, z = cell_centre(tx, tz)
    return x.astype(float), z.astype(float)


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
    elif prim["kind"] == "box":
        c, rr = np.array(prim["c"], np.float32), np.array(prim["r"], np.float32)
        with np.errstate(divide="ignore", invalid="ignore"):
            inv = np.float32(1) / d
            t1, t2 = (c - rr - o) * inv, (c + rr - o) * inv
        t1, t2 = np.nan_to_num(t1, nan=-np.inf, posinf=np.inf, neginf=-np.inf), np.nan_to_num(t2, nan=np.inf, posinf=np.inf, neginf=-np.inf)   # a ray parallel to a face
        lo, hi = np.minimum(t1, t2), np.maximum(t1, t2)
        near, far = lo.max(-1), hi.min(-1)
        enter = near > 1e-2                              # else the ray starts inside: it leaves through `far`
        t = np.where(enter, near, far)
        t = np.where((far >= near) & (t > 1e-2), t, inf).astype(np.float32)
        axis = np.where(enter, lo.argmax(-1), hi.argmin(-1))
        sign = np.where(enter, -1.0, 1.0) * np.sign(np.take_along_axis(d, axis[..., None], -1)[..., 0])
        n = (np.arange(3) == axis[..., None]) * sign[..., None]
        n = n.astype(np.float32)
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


def corners(prim):
    if prim["kind"] in ("ell", "box"):
        c, r = np.array(prim["c"]), np.array(prim["r"])
        return np.array([c + r * s for s in np.array(np.meshgrid([-1, 1], [-1, 1], [-1, 1])).T.reshape(-1, 3)])
    (cx, cz), r = prim["c"], prim["r"]
    return np.array([(cx + sx * r, y, cz + sz * r) for sx in (-1, 1) for y in (prim["y0"], prim["y1"]) for sz in (-1, 1)])


def trace(scr, prims, scale, frame=None):
    """First hit per pixel: ray directions, distance, owning prop (-1 = floor), normal."""
    frame = frame or scr.grid
    w, h = frame[0] * 128 * scale, frame[1] * 128 * scale
    d = rays(scr, w, h, scale, frame)
    vp32 = scr.vp.astype(np.float32)
    best = np.where(d[..., 1] > 0, -scr.vp[1] / np.maximum(d[..., 1], 1e-9), np.inf).astype(np.float32)
    owner = np.full((h, w), -1, np.int32)
    normal = np.zeros_like(d)
    normal[..., 1] = -1
    for i, prim in enumerate(prims):                 # trace each prop only inside its screen box
        u, v = project(scr, corners(prim), frame)
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


def in_ellipse(x, z, e, grow=0.0):
    return ((x - e["c"][0]) / (e["r"][0] + grow)) ** 2 + ((z - e["c"][1]) / (e["r"][1] + grow)) ** 2 <= 1


def shadow_map(scr, prims, res=6.0):
    """Orthographic depth of the props as seen along the sunlight (front-most surface)."""
    sun = scr.sun
    a = np.cross(sun, [0.0, 0.0, 1.0])
    a /= np.linalg.norm(a)
    b = np.cross(sun, a)
    allc = np.concatenate([corners(p) for p in prims])
    lo = np.array([allc @ a, allc @ b]).min(1) - 50
    hi = np.array([allc @ a, allc @ b]).max(1) + 50
    n = np.ceil((hi - lo) / res).astype(int)
    dep = np.full((n[1], n[0]), np.inf, np.float32)
    for prim in prims:
        c = corners(prim)
        i0, j0 = np.floor(([c @ a, c @ b] - lo[:, None]).min(1) / res).astype(int)
        i1, j1 = np.ceil(([c @ a, c @ b] - lo[:, None]).max(1) / res).astype(int) + 1
        jj, ii = np.mgrid[j0:j1, i0:i1]
        o = ((lo[0] + (ii + 0.5) * res)[..., None] * a + (lo[1] + (jj + 0.5) * res)[..., None] * b
             - 1e5 * sun).astype(np.float32)
        t, _ = hit(prim, o, np.broadcast_to(sun.astype(np.float32), o.shape))
        dep[j0:j1, i0:i1] = np.minimum(dep[j0:j1, i0:i1], t)
    return dict(a=a, b=b, lo=lo, res=res, depth=dep)


def sunlit(scr, sm, pt, soft=2):
    """Fraction of a 5x5 shadow-map neighbourhood that sees the sun (soft edges)."""
    i = ((pt @ sm["a"] - sm["lo"][0]) / sm["res"]).astype(int)
    j = ((pt @ sm["b"] - sm["lo"][1]) / sm["res"]).astype(int)
    dep = pt @ scr.sun + 1e5 - 12.0                 # bias against self-shadow acne
    hgt, wid = sm["depth"].shape
    acc = np.zeros(pt.shape[:-1], np.float32)
    for dj in range(-soft, soft + 1):
        for di in range(-soft, soft + 1):
            jj, ii = np.clip(j + dj * 2, 0, hgt - 1), np.clip(i + di * 2, 0, wid - 1)
            acc += sm["depth"][jj, ii] >= dep
    return acc / (2 * soft + 1) ** 2


def guide(scr, prims, scale=2, frame=None):
    """The painter's input: flat material colours, sun and sky shading, cast shadows."""
    frame = frame or scr.grid
    d, best, owner, normal = trace(scr, prims, scale, frame)
    pt = scr.vp.astype(np.float32) + d * best[..., None]
    alb = scr.albedo(pt[..., 0], pt[..., 2]).astype(np.float32)
    for i, prim in enumerate(prims):
        m = owner == i
        if m.any():
            alb[m] = np.array(scr.flat[prim["mat"]], np.float32) * (0.85 + 0.3 * prim.get("tone", 0.5))
    lit = sunlit(scr, shadow_map(scr, prims), pt + normal * 2.0)
    lam = np.clip((normal * -scr.sun.astype(np.float32)).sum(-1), 0, 1)
    light = 0.42 + 0.18 * np.clip(-normal[..., 1], 0, 1) + 0.78 * lam * lit
    water = scr.water(pt[..., 0], pt[..., 2])
    glint = np.where(water, 0.25 * lit * np.clip(1 - np.abs(d[..., 0]) * 3, 0, 1), 0)   # sun on the water
    rgb = (alb * light[..., None] + 255 * glint[..., None]).reshape(frame[1] * 128, scale, frame[0] * 128, scale,
                                                                     3).mean((1, 3))
    return np.clip(rgb, 0, 255)


def labels(scr, prims, frame=None):
    """Per backdrop pixel, the material a ray meets first (an index into scr.labels)."""
    d, best, owner, _ = trace(scr, prims, 1, frame)
    lab = np.zeros(owner.shape, np.uint8)
    for i, prim in enumerate(prims):
        lab[owner == i] = scr.mats[prim["mat"]]
    pt = scr.vp.astype(np.float32) + d * best[..., None]
    floor = owner < 0
    lab[floor] = scr.floor_label(pt[..., 0], pt[..., 2])[floor]
    return lab


# ---- painted art -----------------------------------------------------------
def classify(scr, rgb, lab_map):
    """What the painting shows per pixel, as scr.labels: one Gaussian per material over colour,
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
    ll = np.full((len(X), len(scr.labels)), -np.inf, np.float32)
    for c in range(len(scr.labels)):
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
def collision(scr, prims, painted=None, fg=None):
    """100x100 grid: 0 floor, 0x80 blocked, the strips' values (110 = warp 0, back to the host), and the sprite order.

    `painted` is classify()'s map of the art: a cell then also needs ground painted under its
    footprint, and the small painted_props stop counting (the painter may nudge a rock, and the
    painting decides where it is). Painted non-ground unprojected onto the floor covers both a
    prop's base and the floor hidden behind it, so this also keeps characters from walking behind
    anything the painter drew. `fg` is foreground_pixels(): its pixels are redrawn over characters
    by sprites, so they hide floor rather than block it, and sprite_order() decides who stands in
    front of whom. Without `fg` nothing may be walked behind: backdrops draw behind characters."""
    x, z = cell_grid()
    bw, bh = scr.bw, scr.bh
    ok = scr.floor(x, z)
    if painted is not None:
        prims = [prim for prim in prims if prim["mat"] not in scr.painted_props]
    for prim in prims:                      # footprints, plus a character radius
        if prim["kind"] == "cyl":
            ok &= np.hypot(x - prim["c"][0], z - prim["c"][1]) > prim["r"] + 110
        elif prim["kind"] == "box":         # a box reaching down to the floor blocks its whole footprint
            if prim["c"][1] + prim["r"][1] > -300:
                ok &= ~((np.abs(x - prim["c"][0]) <= prim["r"][0] + 90) & (np.abs(z - prim["c"][2]) <= prim["r"][2] + 90))
        elif prim["c"][1] + prim["r"][1] > -300:     # an ellipsoid low enough to bump into
            e = dict(c=(prim["c"][0], prim["c"][2]), r=prim["r"][::2])
            if fg is not None and prim["mat"] in scr.fg_mats:
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
        u, v = project(scr, np.stack([x, np.full_like(x, y), z], -1))
        ok &= (u > 24) & (u < bw - 24) & (v > 16) & (v < bh - 8)
    if painted is not None:                 # the painting must show ground under the feet (7 of 9 samples)
        seen = painted if fg is None else np.where(fg["pix"], 0, painted)   # sprite pixels hide floor
        cand = np.argwhere(ok)
        cx, cz = x[ok], z[ok]
        good = np.zeros(len(cx), int)
        for a in [None] + list(np.linspace(0, 2 * np.pi, 8, endpoint=False)):
            ox, oz = (0.0, 0.0) if a is None else (70 * np.cos(a), 70 * np.sin(a))
            u, v = project(scr, np.stack([cx + ox, np.zeros_like(cx), cz + oz], -1))
            good += np.isin(seen[np.clip(v.astype(int), 0, bh - 1), np.clip(u.astype(int), 0, bw - 1)], scr.ground)
        ok[tuple(cand[good < 7].T)] = False
    order = None
    if fg is None:                          # nothing may be walked behind: a prop between the eye and the character
        vp32 = scr.vp.astype(np.float32)
        for dx, dz in ((0, 0), (-50, 0), (50, 0), (0, 50)):
            for y in (-20, -180, -340):
                cand = np.argwhere(ok)
                P = np.stack([x + dx, np.full_like(x, y), z + dz], -1)[ok].astype(np.float32)
                dvec = P - vp32
                dist = np.linalg.norm(dvec, axis=-1)
                hid = occluded(prims, np.broadcast_to(vp32, P.shape), dvec / dist[:, None], limit=dist - 5)
                ok[tuple(cand[hid].T)] = False
    else:
        ok, order = sprite_order(scr, ok, fg, x, z)
    grid = np.where(ok, 0, 0x80).astype(np.uint8)
    for value, strip in scr.strips.items():
        grid[ok & strip(x, z)] = value
    return grid, order


# ---- foreground sprites ----------------------------------------------------
def foreground_pixels(scr, prims, painted):
    """Pixels that foreground sprites redraw over characters: the scene's foliage and trunks where
    the painting shows them, grown 6 px into the painter's own foliage round their silhouettes.
    Each pixel carries its prop's sprite group and the camera depth of its surface."""
    d, best, owner, _ = trace(scr, prims, 1)
    surf = (best * (d @ basis(scr)[2].astype(np.float32))).astype(np.float32)
    groups = sorted({p["group"] for p in prims if p["mat"] in scr.fg_mats})
    gid_of = np.array([groups.index(p["group"]) if p["mat"] in scr.fg_mats else -1 for p in prims])
    gid = np.where(owner >= 0, gid_of[np.maximum(owner, 0)], -1)
    gid[np.isin(painted, scr.ground + (1,))] = -1       # the painting shows floor or water there
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
    other = (owner >= 0) & ~np.isin(mats[np.maximum(owner, 0)], scr.fg_mats) & ~np.isin(painted, scr.ground + (1,))
    return dict(gid=gid, pix=gid >= 0, groups=groups, fsurf=fsurf, surf=surf, other=other)


def sprite_order(scr, ok, fg, x, z):
    """Who is drawn over whom. A sprite has one depth (its anchor); a character is ordered by its
    own (about its waist). Each candidate cell is sampled over a character's box: a sample hidden
    by a sprite's surface needs that sprite in front (anchor < character - FG_MARGIN); a visible
    sample on sprite pixels needs it behind (anchor > character + FG_MARGIN). Each group's anchor
    is the depth that satisfies the most cells; cells it cannot satisfy, and cells hidden by props
    that have no sprite, stay blocked."""
    bw, bh = scr.bw, scr.bh
    cells = np.argwhere(ok)
    cx, cz = x[ok], z[ok]
    zc = depth(scr, np.stack([cx, np.full_like(cx, -190.0), cz], -1))
    n, ns = len(cx), len(CHAR_SAMPLES)
    occ, gid = np.zeros((n, ns), bool), np.full((n, ns), -1)
    uv = np.zeros((n, ns, 2), int)
    bad = np.zeros(n, bool)
    for k, (dx, y, dz) in enumerate(CHAR_SAMPLES):
        P = np.stack([cx + dx, np.full_like(cx, float(y)), cz + dz], -1)
        u, v = project(scr, P)
        ui, vi = np.clip(u.astype(int), 0, bw - 1), np.clip(v.astype(int), 0, bh - 1)
        zs = depth(scr, P)
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


def build_sprites(scr, fg, order, idx, prims):
    """Cut the sprites: per group, its pixels inside the boxes of the characters it must cover
    (elsewhere a sprite would only repaint the backdrop's own pixels), split into rects that fit
    a texture page, shelf-packed into 256x256 8bpp pages. Texels are the backdrop's own palette
    indices, 0 (transparent) off the sprite, so the live palette and its time of day apply."""
    need = np.zeros(idx.shape, bool)                    # union of character boxes on walkable cells
    for c in np.nonzero(order["keep"])[0]:
        u, v = order["uv"][c, :, 0], order["uv"][c, :, 1]
        need[max(v.min() - 6, 0):v.max() + 3, max(u.min() - 4, 0):u.max() + 5] = True
    f = basis(scr)[2]
    vp = scr.vp
    rects = []
    for g, a in order["anchors"].items():
        m = (fg["gid"] == g) & need
        if not m.any():
            continue
        members = [p for p in prims if p.get("group") == fg["groups"][g]]
        ax = float(np.mean([p["c"][0] for p in members]))
        az = vp[2] + (a - (0 - vp[1]) * f[1] - (ax - vp[0]) * f[0]) / f[2]   # the floor point at that depth
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


def order_errors(scr, grid, fg, order, rects):
    """Cells where the finished sprites would draw wrong over or under a character, checked densely
    (a 5 x 10 unit pitch over the character's box, finer than a pixel): a point a sprite group's
    surface hides must be under a sprite drawn in front of the character, and a visible
    point must not be under one. sprite_order samples coarser; this catches the thin edges it misses."""
    bw, bh = scr.bw, scr.bh
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
    zc = depth(scr, np.stack([cx, np.full_like(cx, -190.0), cz], -1))
    bad = np.zeros(len(cx), bool)
    for dx in np.arange(-50, 51, 5):
        for y in np.arange(-10, -350, -10):
            for dz in (-40, 0, 40):
                P = np.stack([cx + dx, np.full_like(cx, float(y)), cz + dz], -1)
                u, v = project(scr, P)
                ui, vi = np.clip(u.astype(int), 0, bw - 1), np.clip(v.astype(int), 0, bh - 1)
                hidden = (fg["gid"][vi, ui] >= 0) & (fg["fsurf"][vi, ui] < depth(scr, P) - 5)
                g = drawn[vi, ui]
                front = (g >= 0) & (anchor[g] < zc)
                bad |= (hidden & ~front) | (~hidden & front)
    out = np.zeros(grid.shape, bool)
    out[walk] = bad
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
        areas = np.bincount(flat).astype(float)          # one entry per label present; absent ones count nothing
        share = np.where(areas > 0, np.maximum(np.sqrt(areas) / np.sqrt(areas).sum(), 0.08), 0)   # every material
        quota = share / share.sum() * 60000                                                     # gets colours
        rng = np.random.default_rng(0)
        pick = np.concatenate([rng.choice(np.nonzero(flat == c)[0], int(quota[c])) for c in range(len(areas))
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


def build_tfs(scr, idx, cluts):
    gw, gh = scr.grid
    out = bytearray(struct.pack("<HHi", gw, gh, len(cluts)))
    for c in cluts:
        out += c.astype("<u2").tobytes()
    for gy in range(gh):
        for gx in range(gw):
            out += struct.pack("<HH", gx * 64, gy * 128)
            out += idx[gy * 128:(gy + 1) * 128, gx * 128:(gx + 1) * 128].tobytes()
    assert len(out) == 8 + 512 * len(cluts) + 16388 * gw * gh <= MAX_FILE
    return bytes(out)


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


def build_map(scr, grid, tims=(), objects=b""):
    """Header (offsets: setup, the 8bpp images, the object block, entities, collision; the images
    and objects only when MAP_ENTRIES says n8 > 0), then the blocks in that order."""
    gw, gh = scr.grid
    setup = (list((scr.vp / 2).astype(int)) + list((scr.vr / 2).astype(int)) + [v for l in scr.lights for v in l]
             + [0, 0, 0, scr.h] + list(scr.regions) + [gw, gh] + list(range(gw * gh)))
    blocks = ([struct.pack("<%di" % len(setup), *setup)] + list(tims) + ([objects] if tims else [])
              + [scr.warps.astype("<i2").tobytes() + struct.pack("<h", len(scr.records)) + b"".join(scr.records),
                 grid.tobytes()])
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
    from extract_disc_file import parse_record, read_extent
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


# ---- self-check ------------------------------------------------------------
def _selfcheck():
    box = dict(kind="box", c=(0, -100, 0), r=(50, 50, 50), mat="x")
    f32 = lambda *v: np.array(v, np.float32)
    for o, d, want_t, want_n in ((f32(0, -100, -1000), f32(0, 0, 1), 950, (0, 0, -1)),
                                 (f32(-1000, -100, 0), f32(1, 0, 0), 950, (-1, 0, 0)),
                                 (f32(0, -100, 0), f32(0, 0, 1), 50, (0, 0, 1))):    # from inside: leaves the far face
        t, n = hit(box, o, d)
        assert abs(float(t) - want_t) < 1e-3 and np.allclose(n, want_n), (o, d, t, n)
    t, _ = hit(box, f32(0, -300, -1000), f32(0, 0, 1))                               # passes above
    assert np.isinf(t), t
    t, _ = hit(box, f32(0, -100, -1000), f32(0, 0, -1))                              # points away
    assert np.isinf(t), t
    # a box on the floor blocks its footprint plus the character radius, and occludes
    scr = Screen("t", 0, np.array([0.0, -5600.0, -7400.0]), np.array([0.0, 0.0, -1500.0]), 927, (6, 4), (6, 5), [],
                 ("a",), (0,), dict(b=1), dict(b=(1, 1, 1)), (), (), lambda x, z: np.ones(np.shape(x), bool), {},
                 np.zeros((6, 10), np.int16), [])
    prim = dict(kind="box", c=(0, -100, 0), r=(150, 100, 100), mat="b")
    grid, _ = collision(scr, [prim])
    tx, tz = tile_of(0, 0)
    free, _ = collision(scr, [])
    assert free[tz, tx] == 0 and grid[tz, tx] == 0x80 and grid[tz, tx + 1] == 0x80 and grid[tz, tx + 2] == free[tz, tx + 2], "box footprint"
    assert occluded([prim], np.broadcast_to(scr.vp.astype(np.float32), (1, 3)),
                    ((np.array([[0, -100, 0]], np.float32) - scr.vp.astype(np.float32)) / 8000), None)[0]
    print("newmap_engine self-check: ok")


if __name__ == "__main__":
    _selfcheck()
