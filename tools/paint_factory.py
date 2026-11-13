#!/usr/bin/env python3
"""Paint the Abandoned Factory's four flat guides into finished pre-rendered-CG art with local models.

gen_factory.py --preview renders each screen's scene as a flat-colour guide and a label map from the game camera
(build/newmap/factory/guide_<n>.png, labels_<n>.png). The glade's painter (paint_newmap.py) supplies the passes:

  1. Qwen-Image-Edit 2511 reads the guide as a blockout and renders a detailed 3D scene, keeping every outline
     (the prompt names only what the screen has: AFAC4 is told its back wall is solid and it has no crates);
  2. Z-Image-Turbo img2img entering at step 4 of 8 repaints that with real materials;
  3. at paint-frame size (768x640; gen_factory crops the 768x512 backdrop from it), a concrete and a steel-plate
     texture (Z-Image, generated once, shared by the four screens) are laid onto the painted floor and grates in the
     camera's perspective as luminance detail;
  4. the colour statistics are pulled half way toward the Lab mean/std of the stock interior field maps.

Unlike the glade there is no foliage pass and no per-material tone: the factory has no leaves to bring to the game's
scale, and its materials are the ones passes 1-2 already paint well (grey metal and concrete). The grade target is
the interiors' (MAP_ENTRIES rows with flag 0x40, no day/night: what an indoor screen looks like in this game), not
Factorial Town's: one town would set the palette of one place, the interiors set the look of the game's rooms. It is
a handful of numbers, measured on the disc; no pixel of the game is read here.

    py tools/paint_factory.py [--screen N]               writes art/newmaps/factory_<n>.png + .json
    py tools/paint_factory.py --screen N --sweep 1:1,2:7 candidates (qwen:z seeds) in <build>/newmap/factory/paint/

Seeds reproduce on the same models, ComfyUI and GPU; the chosen PNGs are committed, so building the screens never
needs the models. Needs ComfyUI running or startable by the comfy.py client in --comfy.
"""
import argparse
import json
import os
import shutil
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gen_factory as F  # noqa: E402
import paint_newmap as P  # noqa: E402

LOOK = ("pre-rendered 1999 PlayStation field-map CG of an abandoned factory interior, machines still running, dust "
        "and dim work lights, no people")
QWEN_PROMPT = (
    "This is a rough colour blockout of a video game screen seen from above at an angle. Render it as a "
    "finished, detailed 3D scene: " + LOOK + ". The light grey floor with dark seams is worn concrete plates; the "
    "dark striped patches are steel floor grates; the khaki blocks and cylinders are heavy industrial "
    "machines, presses, furnaces and tanks, and the dark blue-grey cylinders on them are pipes and exhaust "
    "stacks; the dark blocks along the sides and the back are the factory's walls, with two pipe runs along "
    "the back wall and a lit doorway wherever the back wall has a gap, its concrete floor as bright as the "
    "hall's; the orange-brown boxes are wooden crates; the near-black is darkness beyond the walls. The open "
    "floor stays open, flat and empty, right up to the plain walls, with nothing added on it and nothing in "
    "the doorways. Keep the outline, size and position of every shape exactly; only replace the flat colours "
    "with detailed realistic materials. No characters, no text.")
Z_PROMPT = (
    "Photorealistic " + LOOK + ", seen from a high fixed camera. A worn concrete floor of square plates with dark "
    "seams and faint grey grime, steel floor grates, heavy rusted industrial machines, presses, furnaces and "
    "tanks with pipes and exhaust stacks, wooden crates, grimy concrete walls with pipe runs and lit "
    "doorways; the open floor between them is empty and flat. Dim warm work lights, dusty air, muted greys "
    "and rust browns, fine film grain. Same layout and shapes as the input picture.")
# what a screen does not have, it is told so (AFAC4: no doorway, no crates). Mentioned anyway, the painter cut a lit
# doorway in its solid wall and stacked crates by the walls, which classify as floor where no scene crate taught the
# classifier what a crate looks like
NO_DOOR = ((" and a lit doorway wherever the back wall has a gap, its concrete floor as bright as the hall's",
            ", which is solid, with no doorway"),
           (" and nothing in the doorways", ""), (" with pipe runs and lit doorways", " with pipe runs"))
NO_CRATE = (("; the orange-brown boxes are wooden crates", ""), (" wooden crates,", ""),
            ("with nothing added on it.", "with nothing added on it: no crates, no boxes."))


def prompts(n):
    """(Qwen prompt, Z prompt) for screen n."""
    door, prims = F.SCENES[n - 1]()[2], F.SCENES[n - 1]()[0]
    out = [QWEN_PROMPT, Z_PROMPT]
    for a, b in (() if door is not None else NO_DOOR) + (() if any(p["mat"] == "crate" for p in prims) else NO_CRATE):
        assert sum(a in t for t in out) == 1, a
        out = [t.replace(a, b) for t in out]
    return tuple(out)


TEXTURES = {
    "concrete": ("Top-down photograph of a concrete floor texture filling the whole frame: worn grey industrial "
                 "concrete with fine grit, hairline cracks and faint scuffs, flat even light, no shadows, no "
                 "objects, uniform pattern, seamless tileable texture.", 4),
    "steel": ("Top-down photograph of a steel floor plate texture filling the whole frame: worn dark grey diamond "
              "tread steel plate with scratches, grime and spots of rust, flat even light, no shadows, no objects, "
              "uniform pattern, seamless tileable texture.", 4),
}
LAYERS = dict(concrete=0, steel=1)          # the label (gen_factory.LABELS) each texture is laid on: floor, grate
TEXEL = dict(concrete=1.5, steel=1.0)       # world units per texel; 100 units = one collision cell
DETAIL = dict(concrete=0.6, steel=0.6)
# mean, std (OpenCV Lab, CLUT 0, transparent texels left out) over the 107 TFS files the 110 flag-0x40 rows name
# (rows 50 and 61 name files not on the disc), pooled; measured as FOREST_LAB was (MAYO01-03 that way: 97.3 112.7
# 148.9 / 37.6 8.2 9.6). The large L spread is the interiors' black surrounds.
INTERIOR_LAB = (np.array([74.9, 124.9, 135.8]), np.array([65.4, 14.4, 14.7]))
GRADE = P.GRADE
# picked from sweeps by the art checks, the ground IoU and a look (seeds 1-4 each; AFAC2 also 4-9 on earlier prompts)
SEEDS = {1: dict(qwen=4, z=4), 2: dict(qwen=3, z=3), 3: dict(qwen=2, z=2), 4: dict(qwen=2, z=2)}


def paint(comfy, work, tex_dir, guide_name, lab_map, n, seeds, cache):
    """Screen n's guide (an input-folder name) -> finished paint-frame RGB (uint8). Passes and textures are cached."""
    scr = F.SCREENS[n - 1]
    z = P.passes_12(comfy, work, guide_name, seeds, cache, *prompts(n))
    rgb = np.asarray(Image.open(z).convert("RGB").resize((scr.pbw, scr.pbh), Image.LANCZOS), np.float32)
    layers = [(label, P.texture(comfy, tex_dir, name, *TEXTURES[name], cache), TEXEL[name], DETAIL[name])
              for name, label in LAYERS.items()]
    return P.grade(np.clip(P.texture_ground(rgb, lab_map, layers, scr), 0, 255), GRADE, INTERIOR_LAB)


def iou(n, rgb):
    """The ground IoU gen_factory gates on: the backdrop crop of paint-frame art."""
    scr = F.SCREENS[n - 1]
    return float(F.painted_map(n - 1, rgb[scr.crop:scr.crop + scr.bh].astype(np.float32))[1])


def main():
    root = F.ROOT
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", default=os.path.join(root, "build-debug"))
    ap.add_argument("--comfy", default=r"C:\Users\boura\projects\local design and assets studio",
                    help="folder holding the comfy.py ComfyUI client")
    ap.add_argument("--screen", type=int, choices=(1, 2, 3, 4), help="one screen (default: all four)")
    ap.add_argument("--sweep", help="comma-separated qwen:z seed pairs: write candidates instead of the art")
    a = ap.parse_args()
    prev = os.path.join(a.build, "newmap", "factory")
    sys.path.insert(0, a.comfy)
    import comfy
    comfy.start()
    tex_dir = os.path.join(prev, "paint")
    cache = {}
    for n in [a.screen] if a.screen else [1, 2, 3, 4]:
        guide = os.path.join(prev, "guide_%d.png" % n)
        if not os.path.exists(guide):
            raise SystemExit("no guide at %s: run gen_factory.py --preview first" % guide)
        lab_map = np.asarray(Image.open(os.path.join(prev, "labels_%d.png" % n)))
        work = os.path.join(prev, "paint", str(n))
        os.makedirs(work, exist_ok=True)
        shutil.copy(guide, os.path.join(work, "factory_guide_%d.png" % n))
        guide_name = comfy.upload(os.path.join(work, "factory_guide_%d.png" % n))
        pairs = [tuple(map(int, s.split(":"))) for s in a.sweep.split(",")] if a.sweep else \
            [(SEEDS[n]["qwen"], SEEDS[n]["z"])]
        for q, zs in pairs:
            seeds = dict(qwen=q, z=zs)
            rgb = paint(comfy, work, tex_dir, guide_name, lab_map, n, seeds, cache)
            score = iou(n, rgb)
            out = os.path.join(work, "cand_q%d_z%d.png" % (q, zs)) if a.sweep else F.ART % n
            os.makedirs(os.path.dirname(out), exist_ok=True)
            Image.fromarray(rgb).save(out)
            print("%s: ground IoU %.3f" % (out, score), flush=True)
            if a.sweep:
                continue
            meta = dict(screen=n, name=F.NAMES[n - 1], seeds=seeds,
                        models=dict(qwen=comfy.QWEN_EDIT, lora=comfy.QWEN_LIGHTNING, z_image=comfy.Z_MODEL),
                        passes=dict(qwen=dict(size=P.WORK, steps=4, prompt=prompts(n)[0]),
                                    z=dict(size=P.WORK, start_step=P.Z_START, steps=8, prompt=prompts(n)[1]),
                                    textures={k: dict(seed=s, prompt=p, label=F.LABELS[LAYERS[k]], texel=TEXEL[k],
                                                      detail=DETAIL[k]) for k, (p, s) in TEXTURES.items()}),
                        grade=dict(strength=GRADE, lab_mean=INTERIOR_LAB[0].tolist(),
                                   lab_std=INTERIOR_LAB[1].tolist()),
                        ground_iou=round(score, 3))
            with open(os.path.splitext(out)[0] + ".json", "w", newline="\n") as fh:
                json.dump(meta, fh, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
