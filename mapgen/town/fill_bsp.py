"""BSP fill: irregular polygon blocks by recursive oriented-box cuts (design: mapgen/TOWN_ORGANIC_SPEC.md section 7.2)."""
from __future__ import annotations

import math
import random
from typing import Any, Dict, List, Tuple

from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import split

from .layerfill import Fill, Frame, SubRoad, Unfit, far, finish, lines_of, overshoot, roads_only
from .params import ParamDef

SUB_DEFS = [
    ParamDef("cells", "int", 3, 24, "target number of blocks", draw_lo=4, draw_hi=10),
    ParamDef("skew", "float", 0.0, 25.0, "random tilt of each cut, degrees", draw_lo=3.0, draw_hi=14.0),
    ParamDef("road_depth", "int", 0, 3, "cuts up to this recursion depth are streets, deeper ones are unroaded borders", default=1),
    ParamDef("ratio_jitter", "float", 0.0, 0.3, "cut position spread around the middle of the longest side", default=0.15),
    ParamDef("main_road", "bool", 0, 1, "the first cut is an artery", default=True),
]
MAX_DEPTH = 8


def _longest_side(face: Polygon) -> Tuple[Tuple[float, float], Tuple[float, float], float]:
    c = list(face.minimum_rotated_rectangle.exterior.coords)[:4]
    best = None
    for i in range(4):
        a, b = c[i], c[(i + 1) % 4]
        ln = math.hypot(b[0] - a[0], b[1] - a[1])
        k = (round(ln, 6), -i)
        if best is None or k > best[0]:
            best = (k, a, b, ln)
    _, a, b, ln = best
    return a, ((b[0] - a[0]) / ln, (b[1] - a[1]) / ln), ln


SAFE = 1.005       # parts must beat min_area by this factor: rounding and stitching shave a little off


def _candidates(face: Polygon, a, d, ln: float, drawn):
    """The drawn cut first; then, when a part would fall under min_area, cuts square to the longest side at positions spreading from
    its middle (+-5 %, +-10 % ... of it), then the same positions along it."""
    yield drawn
    cx, cy = face.centroid.x, face.centroid.y
    for k in range(0, 9):
        for sg in ((1.0,) if k == 0 else (1.0, -1.0)):
            r = 0.5 + sg * 0.05 * k
            yield (a[0] + d[0] * ln * r, a[1] + d[1] * ln * r, -d[1], d[0])
    for k in range(0, 5):
        for sg in ((1.0,) if k == 0 else (1.0, -1.0)):
            yield (cx - d[1] * sg * 0.08 * ln * k, cy + d[0] * sg * 0.08 * ln * k, d[0], d[1])


def fill(P: Polygon, rng: random.Random, p: Dict[str, Any], frame: Frame, min_area: float) -> Fill:
    P = orient(P, 1.0)
    target = max(min_area * 1.6, P.area / max(1, int(p["cells"])))
    skew, rj, rdepth = float(p["skew"]), float(p["ratio_jitter"]), int(p["road_depth"])
    leaves: List[Tuple[str, Polygon]] = []
    pieces: List[SubRoad] = []

    def rec(face: Polygon, path: str, depth: int) -> None:
        if face.area <= target or depth >= MAX_DEPTH:
            leaves.append((path, face))
            return
        a, d, ln = _longest_side(face)
        ratio = rng.uniform(0.5 - rj, 0.5 + rj)
        tilt = math.radians(rng.uniform(-skew, skew))
        px, py = a[0] + d[0] * ln * ratio, a[1] + d[1] * ln * ratio
        nx, ny = -d[1], d[0]
        ct, st = math.cos(tilt), math.sin(tilt)
        nx, ny = nx * ct - ny * st, nx * st + ny * ct
        L = far(face)
        parts: List[Polygon] = []
        for qx, qy, mx, my in (_candidates(face, a, d, ln, (px, py, nx, ny)) if frame.rescue else [(px, py, nx, ny)]):
            cut = LineString([(qx - mx * L, qy - my * L), (qx + mx * L, qy + my * L)])
            try:
                parts = [g for g in split(face, cut).geoms if g.geom_type == "Polygon" and g.area > 1e-6]
            except Exception:
                parts = []
            if len(parts) >= 2 and min(g.area for g in parts) >= SAFE * min_area:
                break
        if len(parts) < 2 or min(g.area for g in parts) < SAFE * min_area:
            leaves.append((path, face))
            return
        seg = lines_of(cut.intersection(face))
        road = depth <= rdepth
        cls = "artery" if depth == 0 and p["main_road"] else "street"
        for i, q in enumerate(seg):
            pieces.append(SubRoad(("s%s_%d" if road else "x%s_%d") % (path or "r", i), cls,
                                  overshoot(q.coords, P)))
        parts.sort(key=lambda g: (round(g.centroid.x, 3), round(g.centroid.y, 3)))
        for i, g in enumerate(parts):
            rec(orient(g, 1.0), path + str(i), depth + 1)

    rec(P, "", 0)
    if len(leaves) < 2:
        raise Unfit("polygon too small to split")

    def key(pt: Point) -> str:
        best = None
        for path, g in leaves:
            d = g.distance(pt)
            if best is None or d < best[0]:
                best = (d, path)
        return "b" + best[1]
    fl = finish(P, pieces, key, min_area)
    return roads_only(P, fl, "s")
