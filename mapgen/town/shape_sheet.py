"""Debug sheet of shape-field outlines (design: TOWN_ORGANIC_SPEC.md section 4).

    .venv/Scripts/python -m mapgen.town.shape_sheet [--seeds 30] [--size 1000] [--out out/town_organic/outlines.png]
        [--param irregularity=0.3 --param lobes=2 ...] [--edge wall|fade]

Draws one tile per seed: footprint (light fill, dark outline), hub, ribbon axes (dashed-look dots), satellite parcels (hatched
by a lighter fill). Tiles are labelled with the seed, solidity and drawn parameters. Prints one stats line per seed.
"""
from __future__ import annotations

import argparse
import os
import sys

from PIL import Image, ImageDraw

from . import shape
from .gen_radial import GenerationError

TILE = 300


def render(shapes, size, cols=6):
    n = len(shapes)
    rows = (n + cols - 1) // cols
    img = Image.new("RGB", (cols * TILE, rows * TILE), (245, 245, 245))
    d = ImageDraw.Draw(img)
    k = (TILE - 24) / float(size)
    for i, (seed, sh) in enumerate(shapes):
        ox, oy = (i % cols) * TILE + 12, (i // cols) * TILE + 20

        def tp(p):
            return (ox + p[0] * k, oy + (size - p[1]) * k)

        d.rectangle([ox, oy, ox + size * k, oy + size * k], outline=(190, 190, 190))
        if sh is None:
            d.text((ox + 4, oy + 4), "seed %d: FAILED" % seed, fill=(0, 0, 0))
            continue
        d.polygon([tp(p) for p in sh.ring], fill=(205, 205, 205), outline=(30, 30, 30))
        d.line([tp(p) for p in sh.ring] + [tp(sh.ring[0])], fill=(30, 30, 30), width=2)
        for s in sh.satellites:
            d.polygon([tp(p) for p in s], fill=(225, 225, 225), outline=(90, 90, 90))
        for ax in sh.axes:
            for p in ax[::2]:
                x, y = tp(p)
                d.ellipse([x - 1, y - 1, x + 1, y + 1], fill=(60, 60, 60))
        x, y = tp(sh.hub)
        d.rectangle([x - 3, y - 3, x + 3, y + 3], fill=(0, 0, 0))
        q = sh.params
        d.text((ox + 4, oy + 4), "s%d sol %.2f irr %.2f L%d R%d" % (seed, sh.solidity, q["irregularity"], q["lobes"], q["ribbons"]),
               fill=(0, 0, 0))
    return img


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seeds", type=int, default=30)
    ap.add_argument("--size", type=float, default=1000.0)
    ap.add_argument("--edge", default="wall")
    ap.add_argument("--out", default=os.path.join("out", "town_organic", "outlines.png"))
    ap.add_argument("--param", action="append", default=[], help="key=value shape parameter (see shape.DEFS)")
    a = ap.parse_args(argv)
    params = dict(kv.split("=", 1) for kv in a.param)
    shapes = []
    for seed in range(1, a.seeds + 1):
        try:
            sh = shape.footprint((a.size, a.size), seed, params=params, edge=a.edge)
            print("seed %2d attempt %d solidity %.2f area %6.0f verts %3d %s" % (seed, sh.attempt, sh.solidity, sh.area, len(sh.ring), sh.params))
        except GenerationError as e:
            sh = None
            print("seed %2d FAILED %s" % (seed, e))
        shapes.append((seed, sh))
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    render(shapes, a.size).save(a.out)
    print("wrote", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
