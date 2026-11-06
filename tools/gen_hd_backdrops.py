#!/usr/bin/env python3
"""Build an HD backdrop pack from YOUR disc with a local ComfyUI upscaler (4x-UltraSharp).

Every field map's pre-rendered backdrop is stitched whole from its tile file
(so the upscaler sees across tile edges and no seams appear), upscaled 4x,
then mapped back to the map's own 256-colour palette and cut into the game's
128x128 tiles. Each tile is written as a Beetle-style INDEXED replacement,
`<crc32 of the tile's upload words>-index.png`: red = palette index, alpha 0
where the original is transparent. Indexed replacements resolve through the
live CLUT at draw time, so the game's time-of-day palette changes keep
working on the HD art.

The rooms the widescreen feature repaints (art/backdrops/*.png) are packed a
second time from the repainted indices, so the pack matches with widescreen
on and off.

Needs ComfyUI running (or startable by the client at --comfy) with the
upscale model in models/upscale_models. Writes nothing from the disc into
this repository: the output is your own pack folder.

    python tools/gen_hd_backdrops.py --bin "disc/Digimon World (USA).bin" --out <pack dir> [--only TWNA01,...]
"""
import argparse
import os
import struct
import sys
import time
import zlib

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gen_extended_backdrops import ART, ROOMS, disc_files, read_file, read_png_indexed  # noqa: E402

TILE = 128
SCALE = 4
TILE_BYTES = TILE * TILE
MODEL = "4x-UltraSharp.pth"   # keeps the grain the pre-renders are made of; RealESRGAN smears it


def parse_map(mp, tfs):
    """(width tiles, height tiles, [(grid x, grid y, tile bytes)], [clut words, one per stored palette])"""
    p = struct.unpack_from("<i", mp, 0)[0]
    tw, th = struct.unpack_from("<2i", mp, p + 36 * 4)
    ids = struct.unpack_from("<%di" % (tw * th), mp, p + 38 * 4)
    ncl = struct.unpack_from("<i", tfs, 4)[0]
    cluts = [np.array(struct.unpack_from("<256H", tfs, 8 + k * 512), np.uint16) for k in range(ncl)]
    off, tiles = 8 + ncl * 512, []
    for i in range(tw * th):
        if ids[i] == -1:
            continue
        off += 4
        tiles.append((i % tw, i // tw, tfs[off:off + TILE_BYTES]))
        off += TILE_BYTES
    return tw, th, tiles, cluts


def stitch(tw, th, tiles):
    idx = np.zeros((th * TILE, tw * TILE), np.uint8)
    have = np.zeros((th, tw), bool)
    for gx, gy, data in tiles:
        idx[gy * TILE:(gy + 1) * TILE, gx * TILE:(gx + 1) * TILE] = np.frombuffer(data, np.uint8).reshape(TILE, TILE)
        have[gy, gx] = True
    return idx, have


def palette_rgb(clut):
    return np.stack([(clut & 31) << 3, ((clut >> 5) & 31) << 3, ((clut >> 10) & 31) << 3], -1).astype(np.uint8)


def nearest_lut(clut):
    """32^3 table: 5-bit RGB -> nearest opaque palette index (PSX colour precision)."""
    usable = np.where(clut != 0)[0]
    pal = palette_rgb(clut)[usable].astype(np.int32) >> 3
    g = np.stack(np.meshgrid(np.arange(32), np.arange(32), np.arange(32), indexing="ij"), -1).reshape(-1, 3)
    best = np.empty(len(g), np.int64)
    for s in range(0, len(g), 4096):
        d = ((g[s:s + 4096, None, :] - pal[None, :, :]) ** 2).sum(-1)
        best[s:s + 4096] = usable[d.argmin(1)]
    return best.reshape(32, 32, 32).astype(np.uint8)


def upscale(comfy, rgb, tmp):
    src = os.path.join(tmp, "hd_src.png")
    Image.fromarray(rgb).save(src)
    name = comfy.upload(src)
    g = {"img": {"class_type": "LoadImage", "inputs": {"image": name}},
         "model": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": MODEL}},
         "up": {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["model", 0], "image": ["img", 0]}},
         "save": {"class_type": "SaveImage", "inputs": {"images": ["up", 0], "filename_prefix": "hdbd"}}}
    out = os.path.join(tmp, "hd_out.png")
    comfy.run(g, out, timeout=1800)
    return np.asarray(Image.open(out).convert("RGB"))


DETAIL_PROMPT = ("Pre-rendered 3D CGI background from a 1999 PlayStation game, fixed high camera. Very detailed, "
                 "crisp, sharp textures, rich natural colours. Same layout, same shapes, same colours as the input "
                 "picture.")
DETAIL_TILE, DETAIL_STEP = 1024, 768
BAYER8 = np.array([[0, 32, 8, 40, 2, 34, 10, 42], [48, 16, 56, 24, 50, 18, 58, 26],
                   [12, 44, 4, 36, 14, 46, 6, 38], [60, 28, 52, 20, 62, 30, 54, 22],
                   [3, 35, 11, 43, 1, 33, 9, 41], [51, 19, 59, 27, 49, 17, 57, 25],
                   [15, 47, 7, 39, 13, 45, 5, 37], [63, 31, 55, 23, 61, 29, 53, 21]], np.float32) / 64 - 0.5


def colour_lock(img, ref, sigma=6):
    """Low frequencies from `ref` (the original), high frequencies from `img`:
    upscalers and the detail pass drift colour, the art must not."""
    import cv2
    return img + (cv2.GaussianBlur(ref, (0, 0), sigma) - cv2.GaussianBlur(img, (0, 0), sigma))


def detail_pass(comfy, img, tmp, start_step):
    """Z-Image-Turbo img2img over overlapping tiles, entering at `start_step` of 8
    so the model adds texture; pack_map keeps only its fine detail, so shapes
    stay the original's. Tiles are feather-blended, so no seams."""
    H, W = img.shape[:2]
    ph, pw = max(H, DETAIL_TILE), max(W, DETAIL_TILE)
    src = np.pad(img, ((0, ph - H), (0, pw - W), (0, 0)), mode="reflect")
    acc = np.zeros((ph, pw, 3), np.float32)
    wsum = np.zeros((ph, pw, 1), np.float32)
    ramp = np.minimum(np.arange(DETAIL_TILE) + 1, DETAIL_TILE - np.arange(DETAIL_TILE)).astype(np.float32)
    feather = np.minimum(np.minimum.outer(ramp, ramp) / 128.0, 1.0)[..., None]
    ys = sorted(set(list(range(0, ph - DETAIL_TILE, DETAIL_STEP)) + [ph - DETAIL_TILE]))
    xs = sorted(set(list(range(0, pw - DETAIL_TILE, DETAIL_STEP)) + [pw - DETAIL_TILE]))
    for y in ys:
        for x in xs:
            p = os.path.join(tmp, "detail_in.png")
            Image.fromarray(np.clip(src[y:y + DETAIL_TILE, x:x + DETAIL_TILE], 0, 255).astype(np.uint8)).save(p)
            g = comfy.zgraph(DETAIL_PROMPT, 7, DETAIL_TILE, DETAIL_TILE, steps=8, init=comfy.upload(p),
                             start_step=start_step)
            o = os.path.join(tmp, "detail_out.png")
            comfy.run(g, o, timeout=900)
            acc[y:y + DETAIL_TILE, x:x + DETAIL_TILE] += np.asarray(Image.open(o).convert("RGB"), np.float32) * feather
            wsum[y:y + DETAIL_TILE, x:x + DETAIL_TILE] += feather
    return (acc / np.maximum(wsum, 1e-6))[:H, :W]


def vibrance(img, amount):
    grey = img.mean(-1, keepdims=True)
    return grey + (img - grey) * amount


def pack_map(comfy, idx, have, cluts, tw, th, out, tmp, written, detail=6, sat=1.15, dither=4.0):
    """Per tile: one full-colour image for each stored palette (`<tex>-<palette
    crc>.png`, what the game shows while that palette is live: sampled in game,
    the live CLUT always equals one of them) and the indexed `<tex>-index.png`
    the reader falls back to for any other palette, so day/night fades still
    resolve. The AI runs once, on palette 0; the others are recoloured from it."""
    import cv2
    clut = cluts[0]
    trans = clut[idx] == 0
    rgb = palette_rgb(clut)[idx]
    rgb[trans] = 0
    big = upscale(comfy, rgb, tmp).astype(np.float32)
    if big.shape[:2] != (idx.shape[0] * SCALE, idx.shape[1] * SCALE):
        raise SystemExit("upscaler returned %s for %s" % (big.shape, idx.shape))
    ref = cv2.resize(rgb.astype(np.float32), (big.shape[1], big.shape[0]), interpolation=cv2.INTER_CUBIC)
    big = colour_lock(big, ref)
    if detail:
        # Shapes (above ~4 px at 4x) from the upscale, everything finer from the
        # detail pass: step 6 gives real texture (step 7 smeared grain into
        # streaks) and the shape lock stops it moving outlines and props.
        z = colour_lock(detail_pass(comfy, big, tmp, detail), ref)
        big = cv2.GaussianBlur(big, (0, 0), 4) + (z - cv2.GaussianBlur(z, (0, 0), 4))
    big = vibrance(big, sat)
    big = np.clip(big + (big - cv2.GaussianBlur(big, (0, 0), 1.2)) * 0.6, 0, 255)
    H4, W4 = big.shape[:2]
    lut = nearest_lut(clut)
    q = big.astype(np.int32) >> 3
    near = lut[q[..., 0], q[..., 1], q[..., 2]]
    q = np.clip(big + np.tile(BAYER8, (H4 // 8, W4 // 8))[..., None] * dither, 0, 255).astype(np.int32) >> 3
    hi = lut[q[..., 0], q[..., 1], q[..., 2]]
    hi_trans = trans.repeat(SCALE, 0).repeat(SCALE, 1)
    pal0 = palette_rgb(clut).astype(np.float32)
    colours = []
    for c in cluts:
        # Recolour through the palette: each pixel scaled by how its nearest
        # palette entry changes from palette 0 to this one (smoothed so index
        # boundaries do not show).
        ratio = (palette_rgb(c).astype(np.float32)[near] + 8) / (pal0[near] + 8)
        ratio = cv2.GaussianBlur(ratio, (0, 0), 1.5)
        # ...then held to the original under THIS palette, as palette 0 is held
        # to its own: a per-entry ratio alone left dusk and night too green.
        ref_c = cv2.resize(palette_rgb(c)[idx].astype(np.float32), (W4, H4), interpolation=cv2.INTER_CUBIC)
        pal_hash = zlib.crc32(c.astype("<u2").tobytes()) & 0xFFFFFFFF
        colours.append((pal_hash, np.clip(colour_lock(big * ratio, ref_c), 0, 255).astype(np.uint8)))
    n = 0
    for gy in range(th):
        for gx in range(tw):
            if not have[gy, gx]:
                continue
            tile = idx[gy * TILE:(gy + 1) * TILE, gx * TILE:(gx + 1) * TILE]
            h = zlib.crc32(tile.tobytes()) & 0xFFFFFFFF
            if h in written:
                continue
            ys, xs = gy * TILE * SCALE, gx * TILE * SCALE
            rgba = np.zeros((TILE * SCALE, TILE * SCALE, 4), np.uint8)
            rgba[..., 0] = hi[ys:ys + TILE * SCALE, xs:xs + TILE * SCALE]
            rgba[..., 3] = np.where(hi_trans[ys:ys + TILE * SCALE, xs:xs + TILE * SCALE], 0, 255)
            Image.fromarray(rgba, "RGBA").save(os.path.join(out, "%08X-index.png" % h), optimize=False)
            for pal_hash, col in colours:
                full = rgba.copy()
                full[..., :3] = col[ys:ys + TILE * SCALE, xs:xs + TILE * SCALE]
                Image.fromarray(full, "RGBA").save(os.path.join(out, "%08X-%08X.png" % (h, pal_hash)))
            written.add(h)
            n += 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", required=True)
    ap.add_argument("--out", required=True, help="pack folder (a Hashes.ini is written so it reads as Beetle format)")
    ap.add_argument("--comfy", default=r"C:\Users\boura\projects\local design and assets studio",
                    help="folder holding the comfy.py ComfyUI client")
    ap.add_argument("--only", default="", help="comma-separated map stems, e.g. TWNA01,ROOM08")
    a = ap.parse_args()
    sys.path.insert(0, a.comfy)
    import comfy
    comfy.start()
    os.makedirs(a.out, exist_ok=True)
    open(os.path.join(a.out, "Hashes.ini"), "a").close()
    tmp = os.path.join(a.out, ".work")
    os.makedirs(tmp, exist_ok=True)
    files = disc_files(a.bin)
    only = {s.strip().upper() for s in a.only.split(",") if s.strip()}
    maps = sorted(k[:-4] for k in files if k.startswith("/MAP/") and k.endswith(".TFS") and k[:-4] + ".MAP" in files)
    written = set(int(f.split("-")[0], 16) for f in os.listdir(a.out) if f.endswith("-index.png"))
    for path in maps:
        stem = path.rsplit("/", 1)[1]
        if only and stem not in only:
            continue
        _, mp = read_file(a.bin, files, path + ".MAP")
        _, tfs = read_file(a.bin, files, path + ".TFS")
        try:
            tw, th, tiles, cluts = parse_map(mp, tfs)
        except struct.error:
            print("%s: unreadable layout, skipped" % stem, flush=True)
            continue
        if not tiles:
            continue
        idx, have = stitch(tw, th, tiles)
        for attempt in range(5):    # ComfyUI sometimes stalls for minutes; finished tiles are kept
            try:
                n = pack_map(comfy, idx, have, cluts, tw, th, a.out, tmp, written)
                break
            except OSError as e:
                print("%s: ComfyUI error (%s), retrying in 60s" % (stem, e), flush=True)
                time.sleep(60)
        else:
            raise SystemExit("%s: ComfyUI kept failing" % stem)
        if stem in ROOMS:   # widescreen's repainted edges: their own tile hashes
            _, _, rows, _ = read_png_indexed(os.path.join(ART, stem + ".png"))
            n += pack_map(comfy, np.array([np.frombuffer(r, np.uint8) for r in rows]), have, cluts, tw, th,
                          a.out, tmp, written)
        print("%s: %dx%d tiles, %d written (%d total)" % (stem, tw, th, n, len(written)), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
