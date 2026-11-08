#!/usr/bin/env python3
"""Paint the authored map's flat guide into finished pre-rendered-CG art with local models.

gen_newmap.py --preview renders the scene as a flat-colour guide from the game camera.
Local ComfyUI passes turn it into art in the style of the game's own field maps:

  1. Qwen-Image-Edit 2511 (Q4 GGUF + Lightning 4-step LoRA) reads the guide as a blockout
     and renders a detailed 3D scene, keeping every outline where it is;
  2. Z-Image-Turbo img2img entering at step 4 of 8 repaints that with real materials,
     keeping the layout (props may shift a little: gen_newmap fits collision to the art);
  3. the forest alone (the scene's foliage, eroded) is repainted by a masked Z-Image pass
     entering at step 2, at 2.5x the backdrop so the leaves come out at the game's scale:
     matte, tangled, photographic leaf masses like the stock maps' photo-textured bushes.
     The clearing is untouched by the mask;
  4. at backdrop size, a grass and a dirt texture (Z-Image, generated once) are laid onto
     the painted ground in the camera's perspective as luminance detail, the way 1999
     pre-renders were textured: the fine grainy speckle of the stock maps' grass;
  5. the colour statistics are pulled half way toward the Lab mean/std of the stock Native
     Forest maps, then each material is toned toward the stock look: grass to the stock turf's
     olive at half the blade contrast with single-pixel rust and dark-green flecks, worn earth
     to a darker brown, the foliage calmer and a little darker. The targets are a handful of
     numbers below, measured on the stock maps; no pixel of the game is read.

    py tools/paint_newmap.py                          writes art/newmaps/glade.png + glade.json
    py tools/paint_newmap.py --sweep-foliage 21,34,55 candidates in <build>/newmap/paint/

Seeds reproduce on the same models, ComfyUI and GPU; the chosen PNG is committed, so building
the map never needs the models. Needs ComfyUI running or startable by the comfy.py client in
--comfy, with the models named in that client.
"""
import argparse
import json
import os
import shutil
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gen_newmap as G  # noqa: E402

QWEN_PROMPT = (
    "This is a rough colour blockout of a video game screen seen from above at an angle. Render it as a finished, "
    "detailed 3D scene. The dark green masses are dense leafy shrubs and tree canopies; the light green area is a grass "
    "clearing; the tan patches are worn bare earth; the tan line is a dirt footpath; the blue shape is a small pond with "
    "muddy banks; the grey shapes are mossy boulders; the brown ring is an old cut tree stump; the brown row is a fallen "
    "mossy log. Keep the outline, size and position of every shape exactly; only replace the flat colours with detailed "
    "realistic materials. No characters, no text.")
Z_PROMPT = (
    "Photorealistic pre-rendered 3D background from a 1999 PlayStation RPG, seen from a high fixed camera: a grass "
    "clearing walled in by a dense forest. Realistic photographic lawn texture with irregular worn patches of bare brown "
    "earth. The forest is dense rounded broadleaf shrubs and tree canopies like boxwood, laurel and oak, made of "
    "thousands of small oval leaves with deep dark shadows between them. A muddy-banked pond, mossy grey boulders, an "
    "old tree stump, a fallen mossy log, a worn dirt footpath. Soft overcast daylight, natural muted olive greens, fine "
    "film grain. Same layout and shapes as the input picture.")
FOLIAGE_PROMPT = (
    "High aerial photograph taken from far above, looking down at an angle at a small lawn clearing in a dense forest. "
    "The forest around it is a continuous canopy of thick, rounded, leafy bushes and hedges, all seen from the same "
    "height: dense masses of tiny matte green leaves with darker gaps between the masses, evenly lit, no bare "
    "branches, no twigs, no flowers. Overcast daylight, natural muted olive greens, sharp focus.")
TEXTURES = {
    "grass": ("Top-down photograph of a lawn texture filling the whole frame: short dense olive-green grass with small "
              "brown dry patches and specks of bare soil, flat overcast light, no shadows, no flowers, no objects, "
              "uniform pattern, seamless tileable texture.", 4),
    "dirt": ("Top-down photograph of a soil texture filling the whole frame: dry packed brown dirt with fine grit, a "
             "few tiny pebbles and sparse dry grass bits, flat overcast light, no shadows, no objects, uniform "
             "pattern, seamless tileable texture.", 4),
}
WORK = (1152, 960)          # passes 1-2 at 1.5x the backdrop (about one megapixel)
Z_START = 4                 # of 8 Z-Image-Turbo steps: 5 keeps every prop but adds little; 3 redesigns the layout
FOLIAGE_WORK = (1920, 1600)  # pass 3 at 2.5x: leaves land at 3-6 px once scaled to the backdrop
FOLIAGE_START = 2
SEEDS = dict(qwen=2, z=13, foliage=55)    # foliage 55: cleanest of six by a blind artefact + style panel
TEXEL = 1.2                 # world units per grass texel (dirt: 1.3x); 100 units = one collision cell
DETAIL = dict(grass=0.9, dirt=0.7)
FOREST_LAB = (np.array([97.1, 112.6, 148.9]), np.array([37.2, 8.1, 9.5]))   # mean, std: MAYO01-03 (OpenCV Lab)
GRADE = 0.5
TURF_RGB, EARTH_RGB = (100, 122, 66), (122, 102, 72)   # stock Native Forest turf and worn-earth means
FLECKS = dict(density=0.34, brown=(100, 72, 48), green=(52, 74, 38))


def grade(rgb, k=GRADE):
    """Move the image's Lab mean/std a fraction k of the way to the stock forest maps'."""
    import cv2
    lab = cv2.cvtColor(rgb.astype(np.uint8), cv2.COLOR_RGB2LAB).astype(np.float32)
    m, s = lab.reshape(-1, 3).mean(0), lab.reshape(-1, 3).std(0)
    tm, ts = m + (FOREST_LAB[0] - m) * k, s + (FOREST_LAB[1] - s) * k
    return cv2.cvtColor(np.clip((lab - m) / s * ts + tm, 0, 255).astype(np.uint8), cv2.COLOR_LAB2RGB)


# ---- ground textures -------------------------------------------------------
def floor_xz(scale):
    """World x, z of the floor point under each backdrop pixel (rendered at `scale`x)."""
    d = G.rays(G.BW * scale, G.BH * scale, scale)
    t = np.where(d[..., 1] > 1e-6, -G.VP[1] / np.maximum(d[..., 1], 1e-6), np.nan)
    p = G.VP + d * t[..., None]
    return p[..., 0], p[..., 2]


def sample(tex, x, z, texel, rot=0.0, off=(0.0, 0.0)):
    import cv2
    c, s = np.cos(rot), np.sin(rot)
    n = tex.shape[0]
    u = ((c * x - s * z) / texel + off[0]) / n
    v = ((s * x + c * z) / texel + off[1]) / n
    u = np.abs((u % 2) - 1) * (n - 1)                  # mirrored repeat
    v = np.abs((v % 2) - 1) * (n - 1)
    return cv2.remap(tex, np.nan_to_num(u).astype(np.float32), np.nan_to_num(v).astype(np.float32), cv2.INTER_LINEAR)


def ground_detail(tex, texel, ss=3):
    """Mean-1 luminance detail of `tex` laid on the floor, per backdrop pixel (ss x ss supersampled)."""
    import cv2
    x, z = floor_xz(ss)
    smp = 0.6 * sample(tex, x, z, texel) + 0.4 * sample(tex, x, z, texel * 1.7, rot=0.9, off=(300, 700))
    smp = smp.reshape(G.BH, ss, G.BW, ss, 3).mean((1, 3))
    ratio = smp / np.maximum(cv2.GaussianBlur(smp, (0, 0), 6), 1)
    return np.repeat(ratio.mean(-1, keepdims=True), 3, -1)


def texture_ground(P, grass, dirt, lab_map):
    """Grass detail where the painting shows grass on scene ground, dirt detail on its earth."""
    import cv2
    cls = G.classify(P, lab_map)
    ground = np.isin(lab_map, G.GROUND) & np.isin(cls, G.GROUND)
    k3 = np.ones((3, 3), np.uint8)
    mg, md = [cv2.GaussianBlur(cv2.erode((ground & (cls == c)).astype(np.uint8), k3).astype(np.float32),
                               (0, 0), 1.5)[..., None] for c in (0, 5)]
    base = cv2.GaussianBlur(P, (0, 0), 1.2)            # the painter's soft strokes give way to the texture
    tg = base * (1 + (ground_detail(grass, TEXEL) - 1) * DETAIL["grass"])
    td = base * (1 + (ground_detail(dirt, TEXEL * 1.3) - 1) * DETAIL["dirt"])
    return P * (1 - mg - md) + tg * mg + td * md


def tone(rgb, lab_map):
    """Per-material finish. Ground is one soft field: `earth` (the blurred painted-earth share)
    blends the turf and earth targets over all scene ground, so no pixel between the classes is
    left unadjusted (hard masks leave bright rims round the dirt)."""
    import cv2
    cls = G.classify(rgb.astype(np.float32), lab_map)
    lab = cv2.cvtColor(rgb.astype(np.uint8), cv2.COLOR_RGB2LAB).astype(np.float32)
    to_lab = lambda c: cv2.cvtColor(np.uint8([[c]]), cv2.COLOR_RGB2LAB)[0, 0].astype(np.float32)
    low = cv2.GaussianBlur(lab, (0, 0), 3)
    ground = np.isin(lab_map, G.GROUND) & np.isin(cls, G.GROUND)
    gw = cv2.GaussianBlur(ground.astype(np.float32), (0, 0), 1.0)[..., None]
    earth = cv2.GaussianBlur((cls == 5).astype(np.float32), (0, 0), 2.5)[..., None]
    shift_g = to_lab(TURF_RGB) - lab[ground & (cls == 0)].mean(0)
    shift_e = to_lab(EARTH_RGB) - lab[ground & (cls == 5)].mean(0)
    adj = low + (lab - low) * (0.55 * (1 - earth) + 0.7 * earth) + shift_g * (1 - earth) + shift_e * earth
    out = lab * (1 - gw) + adj * gw
    fw = cv2.GaussianBlur((cls == 2).astype(np.float32), (0, 0), 1.0)[..., None]
    fol = low + (lab - low) * 0.75
    fol[..., 0] *= 0.92
    out = out * (1 - fw) + fol * fw
    res = cv2.cvtColor(np.clip(out, 0, 255).astype(np.uint8), cv2.COLOR_LAB2RGB).astype(np.float32)
    rng = np.random.default_rng(5)                         # the stock turf's per-pixel mottle
    on = rng.random(lab_map.shape) < FLECKS["density"]
    strength = ((0.35 + 0.35 * rng.random(lab_map.shape)) * on)[..., None] * gw * (1 - earth)
    col = np.where((rng.random(lab_map.shape) < 0.6)[..., None], np.float32(FLECKS["brown"]),
                   np.float32(FLECKS["green"]))
    return np.clip(res * (1 - strength) + col * strength, 0, 255).astype(np.uint8)


# ---- passes ----------------------------------------------------------------
def masked(g, mask_name):
    """Only denoise where the mask image (red channel) is white."""
    g["mask_img"] = {"class_type": "LoadImage", "inputs": {"image": mask_name}}
    g["mask"] = {"class_type": "ImageToMask", "inputs": {"image": ["mask_img", 0], "channel": "red"}}
    g["masked"] = {"class_type": "SetLatentNoiseMask", "inputs": {"samples": ["latent", 0], "mask": ["mask", 0]}}
    g["sample"]["inputs"]["latent_image"] = ["masked", 0]
    return g


def paint(comfy, work, guide_name, lab_map, seeds, cache):
    """Guide (an input-folder name) -> finished 768x640 RGB. Passes 1-2 and the textures are cached."""
    import cv2
    q = os.path.join(work, "qwen_%d.png" % seeds["qwen"])
    z = os.path.join(work, "z_%d_%d.png" % (seeds["qwen"], seeds["z"]))
    if z not in cache:
        comfy.run(comfy.qwen_edit_graph(QWEN_PROMPT, [guide_name], seeds["qwen"], *WORK), q, timeout=1800)
        comfy.run(comfy.zgraph(Z_PROMPT, seeds["z"], *WORK, init=comfy.upload(q), start_step=Z_START), z,
                  timeout=1800)
        big = os.path.join(work, "z_big.png")
        Image.open(z).convert("RGB").resize(FOLIAGE_WORK, Image.LANCZOS).save(big)
        m = cv2.erode((lab_map == 2).astype(np.uint8), np.ones((9, 9), np.uint8))
        m = cv2.resize(m * 255, FOLIAGE_WORK, interpolation=cv2.INTER_NEAREST)
        mp = os.path.join(work, "foliage_mask.png")
        Image.fromarray(np.stack([m] * 3, -1)).save(mp)
        cache[z] = (comfy.upload(big), comfy.upload(mp))
    for name, (prompt, seed) in TEXTURES.items():
        t = os.path.join(work, "tex_%s_%d.png" % (name, seed))
        if t not in cache:
            comfy.run(comfy.zgraph(prompt, seed, 1024, 1024), t, timeout=1800)
            cache[t] = np.asarray(Image.open(t).convert("RGB"), np.float32)
    f = os.path.join(work, "foliage_%d_%d_%d.png" % (seeds["qwen"], seeds["z"], seeds["foliage"]))
    init, mask = cache[z]
    comfy.run(masked(comfy.zgraph(FOLIAGE_PROMPT, seeds["foliage"], *FOLIAGE_WORK, init=init,
                                  start_step=FOLIAGE_START), mask), f, timeout=1800)
    P = np.asarray(Image.open(f).convert("RGB").resize((G.BW, G.BH), Image.LANCZOS), np.float32)
    grass, dirt = [cache[os.path.join(work, "tex_%s_%d.png" % (n, TEXTURES[n][1]))] for n in ("grass", "dirt")]
    return tone(grade(np.clip(texture_ground(P, grass, dirt, lab_map), 0, 255)), lab_map)


def ground_iou(rgb, lab_map):
    painted = G.classify(rgb.astype(np.float32), lab_map)
    gl, pg = np.isin(lab_map, G.GROUND), np.isin(painted, G.GROUND)
    return (gl & pg).sum() / (gl | pg).sum()


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", default=os.path.join(root, "build-debug"))
    ap.add_argument("--comfy", default=r"C:\Users\boura\projects\local design and assets studio",
                    help="folder holding the comfy.py ComfyUI client")
    for k, v in SEEDS.items():
        ap.add_argument("--%s-seed" % k, type=int, default=v)
    ap.add_argument("--sweep-foliage", help="comma-separated foliage seeds: write candidates instead of the art")
    ap.add_argument("--out", default=os.path.join(root, "art", "newmaps", "glade.png"))
    a = ap.parse_args()
    guide = os.path.join(a.build, "newmap", "guide.png")
    if not os.path.exists(guide):
        raise SystemExit("no guide at %s: run gen_newmap.py --preview first" % guide)
    sys.path.insert(0, a.comfy)
    import comfy
    comfy.start()
    work = os.path.join(a.build, "newmap", "paint")
    os.makedirs(work, exist_ok=True)
    shutil.copy(guide, os.path.join(work, "newmap_guide.png"))
    guide_name = comfy.upload(os.path.join(work, "newmap_guide.png"))
    lab_map = G.labels(G.scene())
    seeds = dict(qwen=a.qwen_seed, z=a.z_seed, foliage=a.foliage_seed)
    cache = {}
    if a.sweep_foliage:
        for fs in map(int, a.sweep_foliage.split(",")):
            rgb = paint(comfy, work, guide_name, lab_map, dict(seeds, foliage=fs), cache)
            out = os.path.join(work, "cand_q%d_z%d_f%d.png" % (seeds["qwen"], seeds["z"], fs))
            Image.fromarray(rgb).save(out)
            print("%s: ground IoU %.3f" % (out, ground_iou(rgb, lab_map)), flush=True)
        return 0
    rgb = paint(comfy, work, guide_name, lab_map, seeds, cache)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    Image.fromarray(rgb).save(a.out)
    meta = dict(seeds=seeds, models=dict(qwen=comfy.QWEN_EDIT, lora=comfy.QWEN_LIGHTNING, z_image=comfy.Z_MODEL),
                passes=dict(qwen=dict(size=WORK, steps=4, prompt=QWEN_PROMPT),
                            z=dict(size=WORK, start_step=Z_START, steps=8, prompt=Z_PROMPT),
                            foliage=dict(size=FOLIAGE_WORK, start_step=FOLIAGE_START, steps=8, prompt=FOLIAGE_PROMPT),
                            textures={k: dict(seed=s, prompt=p, texel=TEXEL, detail=DETAIL[k])
                                      for k, (p, s) in TEXTURES.items()}),
                grade=dict(strength=GRADE, lab_mean=FOREST_LAB[0].tolist(), lab_std=FOREST_LAB[1].tolist()),
                tone=dict(turf=TURF_RGB, earth=EARTH_RGB, flecks=FLECKS),
                ground_iou=round(float(ground_iou(rgb, lab_map)), 3))
    with open(os.path.splitext(a.out)[0] + ".json", "w", newline="\n") as fh:
        json.dump(meta, fh, indent=1)
    print("wrote %s (ground IoU %.3f)" % (a.out, meta["ground_iou"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
