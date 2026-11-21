"""Follow fill: lots in a band along a road, a core behind them (design: mapgen/TOWN_ORGANIC_SPEC.md section 7.4)."""
from __future__ import annotations

import bisect
import math
import random
from typing import Any, Dict, List, Optional, Tuple

from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import polygonize, unary_union

from .layerfill import Fill, Frame, SubRoad, Unfit, far, extend_ends as _extend_ends, finish, line_pieces, overshoot, roads_only
from .params import ParamDef

SUB_DEFS = [
    ParamDef("bands", "int", 1, 2, "lot bands along the reference chain", default=1),
    ParamDef("depth", "float", 25.0, 60.0, "depth of one band, m", draw_lo=30.0, draw_hi=48.0),
    ParamDef("lot_w", "float", 14.0, 40.0, "lot width along the road, m", draw_lo=18.0, draw_hi=30.0),
    ParamDef("lot_jitter", "float", 0.0, 0.4, "cross-line position spread, fraction of the lot width", default=0.2),
]
MIN_CHAIN = 40.0
ROAD_TURN = math.radians(70.0)
FREE_TURN = math.radians(35.0)


def _turn(a, b, c) -> float:
    t1 = math.atan2(b[1] - a[1], b[0] - a[0])
    t2 = math.atan2(c[1] - b[1], c[0] - b[0])
    return abs((t2 - t1 + math.pi) % (2 * math.pi) - math.pi)


def reference_chain(P: Polygon, roads) -> List[Tuple[float, float]]:
    """The longest chain of consecutive boundary edges lying on a road (else the longest smooth boundary chain), CCW."""
    c = list(P.exterior.coords)[:-1]
    n = len(c)
    on = []
    for i in range(n):
        a, b = c[i], c[(i + 1) % n]
        m = Point((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        on.append(roads is not None and not roads.is_empty and roads.distance(m) < 0.5)
    lim = ROAD_TURN
    if not any(on):
        on = [True] * n
        lim = FREE_TURN
    link = [on[i] and on[(i + 1) % n] and _turn(c[i], c[(i + 1) % n], c[(i + 2) % n]) <= lim for i in range(n)]
    if all(link):
        j = max(range(n), key=lambda i: (_turn(c[i], c[(i + 1) % n], c[(i + 2) % n]), -i))
        link[j] = False
    best, best_len = None, -1.0
    for s in range(n):
        if not on[s] or link[(s - 1) % n]:
            continue
        pts, e, ln = [c[s]], s, 0.0
        while True:
            a, b = c[e], c[(e + 1) % n]
            pts.append(b)
            ln += math.hypot(b[0] - a[0], b[1] - a[1])
            if not link[e]:
                break
            e = (e + 1) % n
        if ln > best_len + 1e-9:
            best, best_len = pts, ln
    if best is None or best_len < MIN_CHAIN:
        raise Unfit("no boundary chain of %d m" % MIN_CHAIN)
    return best


def _offset(chain: LineString, d: float, P: Polygon) -> Optional[LineString]:
    for sign in (1.0, -1.0):
        try:
            o = chain.offset_curve(sign * d, join_style=2, mitre_limit=2.0)
        except Exception:
            return None
        if o.is_empty or o.geom_type != "LineString" or not o.is_simple or o.length < 0.5 * chain.length:
            continue
        if P.contains(o.interpolate(0.5, normalized=True)):
            return o
    return None


def _extend(o: LineString, P: Polygon) -> LineString:
    """Prolong both ends of `o` along their tangent, then keep the part inside P that lies nearest the middle of `o`."""
    c = list(o.coords)
    L = far(P)

    def tan(a, b):
        d = math.hypot(b[0] - a[0], b[1] - a[1]) or 1.0
        return (b[0] - a[0]) / d, (b[1] - a[1]) / d
    t0, t1 = tan(c[1], c[0]), tan(c[-2], c[-1])
    ext = LineString([(c[0][0] + t0[0] * L, c[0][1] + t0[1] * L)] + c + [(c[-1][0] + t1[0] * L, c[-1][1] + t1[1] * L)])
    mid = o.interpolate(0.5, normalized=True)
    parts = line_pieces(ext, P)
    if not parts:
        raise Unfit("band line misses the polygon")
    return min(parts, key=lambda q: q.distance(mid))


def fill(P: Polygon, rng: random.Random, p: Dict[str, Any], frame: Frame, min_area: float) -> Fill:
    P = orient(P, 1.0)
    chain = LineString(reference_chain(P, frame.roads))
    bands, depth = int(p["bands"]), float(p["depth"])
    offs = None
    for attempt in range(2):
        base = chain if attempt == 0 else chain.simplify(2.0)
        d = depth if attempt == 0 else depth * 0.7
        cand = [_offset(base, d * (i + 1), P) for i in range(bands)]
        if all(o is not None for o in cand):
            offs, depth = cand, d
            break
    if offs is None:
        raise Unfit("offset curve self-intersects")
    ext = [_extend(o, P) for o in offs]
    pieces = [SubRoad("o%d" % i, "street", [tuple(c) for c in q.coords]) for i, q in enumerate(ext)]
    total = chain.length
    lot_w = max(float(p["lot_w"]), 1.3 * min_area / depth)
    ncut = max(1, int(round(total / lot_w)))
    w = total / ncut
    jit = float(p["lot_jitter"])
    cross: List[Tuple[float, LineString]] = []         # (arclength along the chain, alley piece)
    for k in range(1, ncut):
        s = w * k + w * jit * rng.uniform(-1.0, 1.0)
        a, b = chain.interpolate(max(0.0, s - 0.5)), chain.interpolate(min(total, s + 0.5))
        tl = math.hypot(b.x - a.x, b.y - a.y) or 1.0
        nx, ny = -(b.y - a.y) / tl, (b.x - a.x) / tl
        o = chain.interpolate(s)
        seg = LineString([(o.x, o.y), (o.x + nx * depth * bands * 1.8, o.y + ny * depth * bands * 1.8)])
        hit = seg.intersection(ext[-1])
        pts = [g for g in ([hit] if hit.geom_type == "Point" else list(getattr(hit, "geoms", []))) if g.geom_type == "Point"]
        if pts:
            q = min(pts, key=lambda g: g.distance(o))
            if q.distance(o) >= 3.0:
                cross.append((s, LineString([(o.x, o.y), (q.x, q.y)])))
    cross.sort(key=lambda t: t[0])
    reach = depth * bands

    def cell_key(pt: Point, cuts: List[float]):
        dd = chain.distance(pt)
        if dd >= reach - 0.5:
            return None
        return int(dd // depth) + 1, bisect.bisect(cuts, chain.project(pt))

    def lots_small(active):
        """The index of a cross line to drop because a lot beside it is under min_area, else None."""
        cuts = [c[0] for c in active]
        faces = polygonize(unary_union([P.boundary] + [_extend_ends(q, P) for q in ext]
                                  + [_extend_ends(LineString(overshoot(list(c[1].coords), P)), P) for c in active]))
        area: Dict[int, float] = {}
        for f in faces:
            rp = f.representative_point()
            if not P.contains(rp):
                continue
            ck = cell_key(rp, cuts)
            if ck is not None:
                area[ck[1]] = area.get(ck[1], 0.0) + f.area
        small = sorted((a_, i) for i, a_ in area.items() if a_ < min_area * 1.05)
        if not small or not active:
            return None
        _, i = small[0]
        left = area.get(i - 1, 1e18) if i > 0 else 1e18
        right = area.get(i + 1, 1e18) if i < len(active) else 1e18
        return i - 1 if left <= right else i        # cut i-1 separates cells i-1 and i; cut i separates i and i+1
    active = list(cross)
    while active:
        j = lots_small(active)
        if j is None or not (0 <= j < len(active)):
            break
        del active[j]
    cuts = [c[0] for c in active]
    pieces += [SubRoad("xc%d" % i, "street", overshoot(list(c[1].coords), P)) for i, c in enumerate(active)]

    def key(pt: Point) -> str:
        ck = cell_key(pt, cuts)
        return "core" if ck is None else "l%d_%d" % ck
    fl = finish(P, pieces, key, min_area)
    return roads_only(P, fl, "o")
