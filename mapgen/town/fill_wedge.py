"""Wedge fill: a fan of streets from an apex, with broken cross arcs (design: mapgen/TOWN_ORGANIC_SPEC.md section 7.3)."""
from __future__ import annotations

import math
import random
from typing import Any, Dict, List

from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import split

from .layerfill import Fill, Frame, SubRoad, Unfit, far, finish, line_pieces
from .params import ParamDef

SUB_DEFS = [
    ParamDef("spokes", "int", 1, 9, "streets radiating from the apex", draw_lo=3, draw_hi=6),
    ParamDef("angle_jitter", "float", 0.0, 0.4, "spoke angle jitter, fraction of the spoke spacing", draw_lo=0.0, draw_hi=0.25),
    ParamDef("cross", "int", 0, 2, "cross arcs between the spokes", default=1),
]
ARC_FRACS = (0.45, 0.75)
ARC_BREAK = 0.25
SHARP = math.radians(100.0)
FACE_AREA = 1.2            # target minimum face area, in multiples of min_area, when capping the face count
GRAZE = 4.5                # m: a spoke may lie within 0.6 m of the boundary for at most this length
MIN_WIDTH = 9.0            # m: faces narrower than this (short side of the minimum rotated rectangle) are slivers


def _norm(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


def _apex(P: Polygon, frame: Frame, rng: random.Random):
    """The sharpest boundary vertex below 100 degrees, else a point outside P on the side of the frame normal."""
    c = list(P.exterior.coords)[:-1]
    n = len(c)
    best = None
    for i in range(n):
        v, a, b = c[i], c[i - 1], c[(i + 1) % n]
        u1, u2 = (b[0] - v[0], b[1] - v[1]), (a[0] - v[0], a[1] - v[1])
        ang = math.atan2(u1[0] * u2[1] - u1[1] * u2[0], u1[0] * u2[0] + u1[1] * u2[1]) % (2 * math.pi)
        if best is None or ang < best[0] - 1e-9:
            best = (ang, v)
    if best[0] < SHARP:
        return best[1]
    cen = P.centroid if P.contains(P.centroid) else P.representative_point()
    sign = rng.choice((-1.0, 1.0))
    nx, ny = -math.sin(frame.rot) * sign, math.cos(frame.rot) * sign
    L = far(P)
    parts = line_pieces(LineString([(cen.x, cen.y), (cen.x + nx * L, cen.y + ny * L)]), P)
    ext = max([q.length for q in parts] or [0.0])
    dist = ext + 0.35 * max(math.sqrt(P.area), 1.0) * 1.2
    return (cen.x + nx * dist, cen.y + ny * dist)


def _grazes(q: LineString, edge) -> bool:
    """True when a spoke piece runs within 0.6 m of the boundary for more than GRAZE m (a shallow angle at the boundary)."""
    return q.intersection(edge).length > GRAZE


def _slivers(fl: Fill) -> bool:
    """True when a face is a sliver: the short side of its minimum rotated rectangle is under MIN_WIDTH."""
    for _, f in fl.faces:
        if f.minimum_rotated_rectangle.geom_type != "Polygon":
            return True
        c = list(f.minimum_rotated_rectangle.exterior.coords)
        a, b = math.hypot(c[1][0] - c[0][0], c[1][1] - c[0][1]), math.hypot(c[2][0] - c[1][0], c[2][1] - c[1][1])
        if min(a, b) < MIN_WIDTH:
            return True
    return False


def fill(P: Polygon, rng: random.Random, p: Dict[str, Any], frame: Frame, min_area: float) -> Fill:
    """The requested layout, thinned until it fits: the face count is capped by the area (`FACE_AREA` x `min_area` per face), then
    on Unfit or slivers cross arcs and spokes are dropped down to one spoke (two faces) without arcs."""
    P = orient(P, 1.0)
    apex = _apex(P, frame, rng)
    n0, c0 = int(p["spokes"]), int(p["cross"])
    cap = max(2, int(P.area / (FACE_AREA * min_area)))
    configs = []
    n, c = n0, c0
    while True:
        if (n + 1) * (c + 1) <= cap:
            configs.append((n, c))
        if n <= 1 and c == 0:
            break
        if c > 0 and (n + 1) * (c + 1) > cap:
            c -= 1
        elif n > 1:
            n -= 1
        else:
            c = 0
    if not configs:
        configs = [(1, 0)]
    why = Unfit("no layout fits")
    for n, c in configs:
        q = dict(p)
        q["spokes"], q["cross"] = n, c
        try:
            fl = _fill(P, apex, rng, q, frame, min_area)
        except Unfit as e:
            why = e
            continue
        if _slivers(fl):
            why = Unfit("sliver faces")
            continue
        return fl
    raise why


def _fill(P: Polygon, apex, rng: random.Random, p: Dict[str, Any], frame: Frame, min_area: float) -> Fill:
    ax, ay = apex
    cen = P.centroid if P.contains(P.centroid) else P.representative_point()
    dir0 = math.atan2(cen.y - ay, cen.x - ax)
    verts = [v for v in list(P.exterior.coords)[:-1] if math.hypot(v[0] - ax, v[1] - ay) > 1e-6]
    rel = [_norm(math.atan2(v[1] - ay, v[0] - ax) - dir0) for v in verts]
    amin, amax = min(rel), max(rel)
    if amax - amin < math.radians(15):
        raise Unfit("polygon too narrow seen from the apex")
    n = int(p["spokes"])
    spacing = (amax - amin) / (n + 1)
    jit = float(p["angle_jitter"])
    angs = sorted(amin + spacing * (i + 1) + spacing * jit * rng.uniform(-1.0, 1.0) for i in range(n))
    L = far(P) + math.hypot(ax - cen.x, ay - cen.y)
    if n == 1:      # one spoke: the angle that halves the area, so neither side merges away
        best = None
        for k in range(1, 20):
            a = amin + (amax - amin) * k / 20.0
            ray = LineString([(ax, ay), (ax + L * math.cos(dir0 + a), ay + L * math.sin(dir0 + a))])
            parts = split(P, ray)
            if len(parts.geoms) == 2 and not any(_grazes(q, P.boundary.buffer(0.6)) for q in line_pieces(ray, P)):
                im = abs(parts.geoms[0].area - parts.geoms[1].area)
                if best is None or im < best[0]:
                    best = (im, a)
        if best is not None:
            angs = [best[1]]
    pieces: List[SubRoad] = []
    inner = P.buffer(-1.0)
    edge = P.boundary.buffer(0.6)
    rmax = max(math.hypot(v[0] - ax, v[1] - ay) for v in verts)
    start = 0.8 * ARC_FRACS[0] * rmax if int(p["cross"]) else 0.0      # with arcs the spokes begin at the first arc: no slivers at the apex
    for i, a in enumerate(angs):
        t = dir0 + a
        ray = LineString([(ax + start * math.cos(t), ay + start * math.sin(t)), (ax + L * math.cos(t), ay + L * math.sin(t))])
        cls = "artery" if n >= 4 and i == n // 2 else "street"
        for j, q in enumerate(line_pieces(ray, P)):
            if q.intersection(inner).length < 0.5 * q.length:      # a spoke that runs along the boundary is no street
                continue
            if _grazes(q, edge):
                raise Unfit("a spoke grazes the boundary")
            pieces.append(SubRoad("w%d_%d" % (i, j), cls, [tuple(c) for c in q.coords]))
    if not pieces:
        raise Unfit("no ray enters the polygon")
    radii = [f * rmax for f in ARC_FRACS[:int(p["cross"])]]
    edges = [amin - 0.02] + angs + [amax + 0.02]
    last = len(edges) - 2
    for b, r in enumerate(radii):
        broken = [rng.random() < ARC_BREAK for _ in range(last + 1)]
        sct = 0
        while sct <= last:
            if broken[sct]:
                sct += 1
                continue
            end = sct
            while end < last and not broken[end + 1]:
                end += 1
            dl = 0.4 / r        # an end at a spoke runs 0.4 m past it so the junction is a true crossing
            a0 = edges[sct] - (dl if sct > 0 else 0.0)
            a1 = edges[end + 1] + (dl if end < last else 0.0)
            steps = max(2, int(math.ceil((a1 - a0) / math.radians(3.0))))
            pts = [(ax + r * math.cos(dir0 + a0 + (a1 - a0) * k / steps), ay + r * math.sin(dir0 + a0 + (a1 - a0) * k / steps))
                   for k in range(steps + 1)]
            for j, q in enumerate(line_pieces(LineString(pts), P)):
                pieces.append(SubRoad("c%d_%d_%d" % (b, sct, j), "street", [tuple(c) for c in q.coords]))
            sct = end + 1

    def key(pt: Point) -> str:
        a = _norm(math.atan2(pt.y - ay, pt.x - ax) - dir0)
        d = math.hypot(pt.x - ax, pt.y - ay)
        return "w%dc%d" % (sum(1 for x in angs if x < a), sum(1 for r in radii if d > r))
    return finish(P, pieces, key, min_area)
