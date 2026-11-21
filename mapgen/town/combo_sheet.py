"""Contact sheets of the named layers presets on organic towns (design: TOWN_ORGANIC_SPEC.md section 7.7).

    .venv/Scripts/python -m mapgen.town.combo_sheet [preset ...] [--seeds 6] [--px 420] [--warp 0.6] [--out out/town_organic]

One PNG per preset, `combo_<preset>.png`; odd seeds `edge=wall`, even seeds `edge=open`. Prints one stats line per seed.
"""
from __future__ import annotations

import argparse
import os
import statistics
import sys

from PIL import Image

from . import generate, layers_params
from .render import render_png


def build(preset, seed, warp="0.6"):
    return generate("organic", seed, size=(1000, 1000),
                    params={"layers": preset, "warp": warp, "edge": "wall" if seed % 2 else "open"})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("presets", nargs="*")
    ap.add_argument("--seeds", type=int, default=6)
    ap.add_argument("--first", type=int, default=1)
    ap.add_argument("--px", type=int, default=420)
    ap.add_argument("--cols", type=int, default=3)
    ap.add_argument("--warp", default="0.6")
    ap.add_argument("--out", default=os.path.join("out", "town_organic"))
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    for name in a.presets or sorted(layers_params.PRESETS):
        tiles = []
        for seed in range(a.first, a.first + a.seeds):
            s = build(name, seed, a.warp)
            ar = [d.area for d in s.districts if d.kind != "core"]
            uses = {}
            for n in s.layers:
                uses["%d%s" % (n.depth, n.use)] = uses.get("%d%s" % (n.depth, n.use), 0) + 1
            print("%s seed %d: %d districts, max/median %.1f, %s" % (name, seed, len(s.districts), max(ar) / statistics.median(ar),
                                                                   " ".join("%s:%d" % kv for kv in sorted(uses.items()))))
            tiles.append(render_png(s, a.px, "%s s%d %s" % (name, seed, s.params.get("edge", ""))))
        rows = (len(tiles) + a.cols - 1) // a.cols
        sheet = Image.new("RGB", (a.cols * a.px + 4 * (a.cols - 1), rows * a.px + 4 * (rows - 1)), (0, 0, 0))
        for i, t in enumerate(tiles):
            sheet.paste(t, ((i % a.cols) * (a.px + 4), (i // a.cols) * (a.px + 4)))
        path = os.path.join(a.out, "combo_%s.png" % name)
        sheet.save(path)
        print("wrote %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
