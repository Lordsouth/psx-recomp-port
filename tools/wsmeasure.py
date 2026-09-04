#!/usr/bin/env python3
"""Measure a native-wide screenshot instead of squinting at it.

Every visual claim about widescreen in this project has to survive the rule
that reading a screenshot is not verification. These are the numbers that are
checkable, so a survey reports them rather than adjectives.

The debug server's `screenshot` command returns the frame AS PRESENTED: at
16:9 that is (320 + 106) x 240 native, times the renderer's supersampling, so
1704x960 at 4x. A frame that comes back 320x240 was NOT presented wide -- the
runtime pillarboxes 4:3 for FMV, menus and boot, which is correct behaviour and
exactly what a survey needs to be able to tell apart from a failure.

Column geometry, measured (see docs/INTERNALS.md 5.0): the vanilla 4:3 image
occupies wide columns 53..372. 0..52 is the left reveal, 373..425 the right.
"""

import os

from PIL import Image

NATIVE_W = 320          # canonical PSX display width for this game
NATIVE_H = 240          # never widened: native-wide adds columns only
WIDE_W = 426            # 16:9 at 240 lines -- the DEFAULT, not an assumption
REVEAL = (WIDE_W - NATIVE_W) // 2      # 53 columns per side at 16:9
BLACK = 14              # max channel value still counted as void


def geometry(size):
    """(wide_width, reveal, supersample) for a presented frame, derived from
    the image rather than assumed.

    The frame HEIGHT is the invariant: native-wide adds columns and never
    rows, so scale = h / 240 and the wide width follows. Deriving it from the
    width instead only works at one aspect -- this file hardcoded 426 and so
    reported every 16:10 frame (384 px) as "presented 4:3", which reads as a
    pass when nothing was measured at all.

    Returns (None, None, None) when the frame is not a whole multiple of the
    native size.
    """
    w, h = size
    if h % NATIVE_H:
        return None, None, None
    sc = h // NATIVE_H
    if sc < 1 or w % sc:
        return None, None, None
    wide = w // sc
    if wide < NATIVE_W or (wide - NATIVE_W) % 2:
        return None, None, None
    return wide, (wide - NATIVE_W) // 2, sc


def _cols(px, h, sc, c0, c1, ystep=4, xstep=1):
    """Fraction of sampled pixels in wide columns [c0, c1) that are void."""
    dark = total = 0
    for cx in range(c0, c1, xstep):
        x = cx * sc + sc // 2
        for y in range(0, h, ystep):
            r, g, b = px[x, y]
            if max(r, g, b) < BLACK:
                dark += 1
            total += 1
    return 100.0 * dark / total if total else 0.0


def measure(path):
    """Void fractions for a presented frame.

    Returns a dict. `wide` False means the frame was presented 4:3 -- the
    reveal fields are then None, because there is no reveal to measure, not
    because it measured clean.
    """
    im = Image.open(path).convert("RGB")
    w, h = im.size
    out = {"path": os.path.basename(path), "size": (w, h)}
    wide, reveal, sc = geometry(im.size)
    if wide is None or reveal == 0:
        out["wide"] = False
        out["left"] = out["centre"] = out["right"] = None
        out["scale"] = sc
        out["wide_w"] = wide
        out["reveal"] = reveal
        return out
    px = im.load()
    out["wide"] = True
    out["scale"] = sc
    out["wide_w"] = wide
    out["reveal"] = reveal
    out["left"] = round(_cols(px, h, sc, 0, reveal, xstep=2), 1)
    out["centre"] = round(_cols(px, h, sc, reveal, wide - reveal, xstep=2), 1)
    out["right"] = round(_cols(px, h, sc, wide - reveal, wide, xstep=2), 1)
    return out


def void_edge(path, side="right"):
    """Where a reveal's void starts, measured row by row from the outer edge.

    A void whose start column is pinned to the SAME column across nearly every
    row is a straight vertical cut -- the signature of an interior whose set
    was only modelled out to the old 4:3 frustum, sliced flat along it. A void
    that wanders row to row is ordinary scene silhouette.

    Returns None when the reveal is filled on every sampled row, else
    (start_column, rows_agreeing, rows_with_any_void, rows_sampled) with the
    column in wide (426) space. start_column == 373 on the right (or 52 on the
    left) means the void covers the whole reveal.
    """
    im = Image.open(path).convert("RGB")
    w, h = im.size
    wide, reveal, sc = geometry(im.size)
    if wide is None or reveal == 0:
        return None
    px = im.load()
    outer = wide - 1 if side == "right" else 0
    inner = wide - reveal if side == "right" else reveal - 1
    step = -1 if side == "right" else 1
    hits = {}
    voided = sampled = 0
    for y in range(0, h, 20):
        sampled += 1
        start = None                      # first void column walking inward
        for cx in range(outer, inner + step, step):
            r, g, b = px[cx * sc + sc // 2, y]
            if max(r, g, b) >= BLACK:
                break                     # hit content: void ends here
            start = cx
        if start is None:
            continue                      # outermost column is lit: filled row
        voided += 1
        # `start` walked inward while the columns stayed dark, so it is the
        # void's INNER boundary -- the column the cut actually sits on.
        hits[start] = hits.get(start, 0) + 1
    if not hits:
        return None
    col, n = max(hits.items(), key=lambda kv: kv[1])
    return (col, n, voided, sampled)


def report(paths):
    """One line per frame, wide or not, for pasting into a survey."""
    lines = ["%-26s %-11s %6s %6s %6s" %
             ("frame", "presented", "L%", "C%", "R%")]
    for p in paths:
        m = measure(p)
        if not m["wide"]:
            lines.append("%-26s %-11s %6s %6s %6s"
                         % (m["path"], "4:3 %dx%d" % m["size"], "-", "-", "-"))
        else:
            lines.append("%-26s %-11s %6.1f %6.1f %6.1f"
                         % (m["path"], "wide %dx%d" % m["size"],
                            m["left"], m["centre"], m["right"]))
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    print(report(sys.argv[1:]))
