"""Grid fill: a rotated street lattice clipped to the polygon (design: mapgen/TOWN_LAYERS_SPEC.md section 4.1)."""
from __future__ import annotations

import math
import random
from typing import Any, Dict, List

from shapely.geometry import LineString, Point, Polygon

from .layerfill import Fill, Frame, SubRoad, Unfit, far, finish, line_pieces, unit
from .params import ParamDef

SUB_DEFS = [
    ParamDef("block", "float", 25.0, 250.0, "lattice spacing along the first axis, m (0 = derived from the polygon area)", default=0.0),
    ParamDef("block_ratio", "float", 0.6, 1.6, "second spacing relative to the first", draw_lo=0.8, draw_hi=1.25),
    ParamDef("jitter", "float", 0.0, 0.25, "per-line offset, fraction of the spacing", draw_lo=0.0, draw_hi=0.1),
    ParamDef("main_every", "int", 0, 6, "every n-th line of each family is an artery (0 = none)", default=3),
]


def _k(prefix: str, k: int) -> str:
    return "%s%s%d" % (prefix, "n" if k < 0 else "", abs(k))


def _idx(i: int) -> str:
    return ("n%d" % -i) if i < 0 else str(i)


def fill(P: Polygon, rng: random.Random, p: Dict[str, Any], frame: Frame, min_area: float) -> Fill:
    base = max(math.sqrt(1.3 * min_area), math.sqrt(P.area) / 3.0)
    bu = float(p["block"]) if p["block"] else base * rng.uniform(0.75, 1.25)
    bv = bu * float(p["block_ratio"])
    jit = float(p["jitter"])
    ph_u, ph_v = rng.uniform(0.0, 0.5), rng.uniform(0.0, 0.5)
    ox, oy = frame.origin
    ux, uy = unit(frame.rot)
    vx, vy = -uy, ux
    L = far(P)
    pieces: List[SubRoad] = []
    every = int(p["main_every"])

    def family(prefix, step, phase, dx, dy, ex, ey):
        n = int(L / step) + 2
        for k in range(-n, n + 1):
            off = (k + phase) * step + rng.uniform(-jit, jit) * step
            cx, cy = ox + dx * off, oy + dy * off
            ln = LineString([(cx - ex * L, cy - ey * L), (cx + ex * L, cy + ey * L)])
            cls = "artery" if every and k % every == 0 else "street"
            for i, q in enumerate(line_pieces(ln, P)):
                if q.length >= 8.0:
                    pieces.append(SubRoad("%s_%d" % (_k(prefix, k), i), cls, [tuple(c) for c in q.coords]))
    family("u", bu, ph_u, ux, uy, vx, vy)     # a `u<k>` line runs along v, spaced along u
    family("v", bv, ph_v, vx, vy, ux, uy)

    def key(pt: Point) -> str:
        dx, dy = pt.x - ox, pt.y - oy
        i = math.floor((dx * ux + dy * uy) / bu - ph_u)
        j = math.floor((dx * vx + dy * vy) / bv - ph_v)
        return "x%sy%s" % (_idx(i), _idx(j))
    if not pieces:
        raise Unfit("no lattice line crosses the polygon")
    return finish(P, pieces, key, min_area)
