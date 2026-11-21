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
    ParamDef("shear", "float", 0.0, 40.0, "degrees the second family is rotated from perpendicular (parallelogram blocks)", default=0.0),
    ParamDef("fan", "float", 0.0, 12.0, "degrees each successive first-family line is rotated about the lattice origin", default=0.0),
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
    shear, fan = math.radians(float(p["shear"])), float(p["fan"])
    # direction of the `u<k>` lines: perpendicular to u, rotated by the shear
    cs, sn = math.cos(shear), math.sin(shear)
    dx_, dy_ = (vx, vy) if not shear else (vx * cs - vy * sn, vx * sn + vy * cs)

    def family(prefix, step, phase, dx, dy, ex, ey, theta=None):
        n = int(L / step) + 2
        for k in range(-n, n + 1):
            off = (k + phase) * step + rng.uniform(-jit, jit) * step
            cx, cy = ox + dx * off, oy + dy * off
            fx, fy = ex, ey
            if theta is not None:
                a = theta(k)
                ca, sa = math.cos(a), math.sin(a)
                cx, cy = ox + (cx - ox) * ca - (cy - oy) * sa, oy + (cx - ox) * sa + (cy - oy) * ca
                fx, fy = ex * ca - ey * sa, ex * sa + ey * ca
            ln = LineString([(cx - fx * L, cy - fy * L), (cx + fx * L, cy + fy * L)])
            cls = "artery" if every and k % every == 0 else "street"
            for i, q in enumerate(line_pieces(ln, P)):
                if q.length >= 8.0:
                    pieces.append(SubRoad("%s_%d" % (_k(prefix, k), i), cls, [tuple(c) for c in q.coords]))
    theta = None
    if fan:
        facs = {}

        def theta(k, _f=facs):
            if k not in _f:
                _f[k] = math.radians(fan * rng.uniform(0.7, 1.3))
            return _f[k] * k
    family("u", bu, ph_u, ux, uy, dx_, dy_, theta)     # a `u<k>` line runs along d, spaced along u
    family("v", bv, ph_v, vx, vy, ux, uy)
    if fan:
        _uncross(pieces, P)

    def key(pt: Point) -> str:
        qx, qy = pt.x - ox, pt.y - oy
        if shear:
            # point = o + s*u + t*d: s is the u-family coordinate
            det = ux * dy_ - uy * dx_
            sc = (qx * dy_ - qy * dx_) / det
        else:
            sc = qx * ux + qy * uy
        i = math.floor(sc / bu - ph_u)
        j = math.floor((qx * vx + qy * vy) / bv - ph_v)
        return "x%sy%s" % (_idx(i), _idx(j))
    if not pieces:
        raise Unfit("no lattice line crosses the polygon")
    return finish(P, pieces, key, min_area)


def _uncross(pieces: List[SubRoad], P: Polygon) -> None:
    """Fan lines of the first family must not cross each other inside P: drop a piece that crosses an already kept `u` piece."""
    kept: List[LineString] = []
    out: List[SubRoad] = []
    order = sorted((r for r in pieces if r.key.startswith("u")), key=lambda r: (abs(_kn(r.key)), r.key))
    for r in order:
        ln = LineString(r.polyline)
        if any(ln.crosses(o) for o in kept):
            continue
        kept.append(ln)
        out.append(r)
    keep = set(id(r) for r in out)
    pieces[:] = [r for r in pieces if not r.key.startswith("u") or id(r) in keep]


def _kn(key: str) -> int:
    body = key[1:].split("_")[0]
    return -int(body[1:]) if body.startswith("n") else int(body)
