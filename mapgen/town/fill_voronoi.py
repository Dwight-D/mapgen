"""Voronoi fill: relaxed random seeds, cell borders as roads (design: mapgen/TOWN_LAYERS_SPEC.md section 4.3)."""
from __future__ import annotations

import math
import random
from typing import Any, Dict, List, Tuple

from shapely.geometry import LineString, MultiPoint, Point, Polygon
from shapely.ops import unary_union, voronoi_diagram

from .gen_voronoi import _dist, _route
from shapely.ops import linemerge
from .layerfill import Fill, Frame, SubRoad, Unfit, finish, lines_of
from .params import ParamDef

SUB_DEFS = [
    ParamDef("cells", "int", 0, 14, "cells (0 = from the area and min_area)", default=0),
    ParamDef("lloyd", "int", 0, 2, "Lloyd relaxation passes", draw_lo=0, draw_hi=2),
]


def _sample(P: Polygon, rng: random.Random, n: int, spacing: float) -> List[Tuple[float, float]]:
    minx, miny, maxx, maxy = P.bounds
    pts: List[Tuple[float, float]] = []
    sp = spacing
    for _ in range(40):
        pts = []
        for _ in range(4000):
            if len(pts) >= n:
                break
            q = (rng.uniform(minx, maxx), rng.uniform(miny, maxy))
            if not P.contains(Point(q)):
                continue
            if all(_dist(q, o) >= sp for o in pts):
                pts.append(q)
        if len(pts) >= n:
            return pts
        sp *= 0.85
    return pts


def _cells(P: Polygon, pts) -> List[Polygon]:
    vd = voronoi_diagram(MultiPoint(pts), envelope=P.buffer(50.0))
    return list(vd.geoms)


def linemerge_nodes(segl):
    """Merge segments into chains that break at every junction (degree != 2)."""
    return linemerge(unary_union(segl)) if segl else LineString()


def fill(P: Polygon, rng: random.Random, p: Dict[str, Any], frame: Frame, min_area: float) -> Fill:
    n = int(p["cells"]) or max(3, min(14, int(round(P.area / (2.2 * min_area)))))
    pts = _sample(P, rng, n, 0.6 * math.sqrt(P.area / n))
    if len(pts) < 3:
        raise Unfit("could not place seeds")
    for _ in range(int(p["lloyd"])):
        npts = []
        for c in _cells(P, pts):
            seed = next((q for q in pts if c.buffer(1e-6).contains(Point(q))), None)
            g = c.intersection(P)
            ctr = g.centroid if not g.is_empty and P.contains(g.centroid) else None
            if seed is not None:
                npts.append((ctr.x, ctr.y) if ctr is not None else seed)
        pts = npts if len(npts) == len(pts) else pts
    pts = sorted(pts)
    cells = _cells(P, pts)
    segs = []
    for c in cells:
        g = c.intersection(P)
        for h in ([g] if g.geom_type == "Polygon" else [x for x in getattr(g, "geoms", []) if x.geom_type == "Polygon"]):
            segs.append(h.exterior)
    seen = set()
    segl = []
    for r in segs:
        cc = list(r.coords)
        for a, b in zip(cc, cc[1:]):
            k = tuple(sorted(((round(a[0], 4), round(a[1], 4)), (round(b[0], 4), round(b[1], 4)))))
            if k in seen:
                continue
            seen.add(k)
            if P.boundary.distance(Point((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)) > 0.05 and _dist(a, b) > 1e-6:
                segl.append(LineString([a, b]))
    chains = lines_of(linemerge_nodes(segl))
    # graph of border vertices for the artery routes
    adj: Dict[tuple, List[Tuple[tuple, float]]] = {}
    for ch in chains:
        cc = [tuple(c) for c in ch.coords]
        for a, b in zip(cc, cc[1:]):
            adj.setdefault(a, []).append((b, _dist(a, b)))
            adj.setdefault(b, []).append((a, _dist(a, b)))
    arteries = set()
    ends = [tuple(c) for ch in chains for c in (ch.coords[0], ch.coords[-1]) if P.boundary.distance(Point(c)) <= 0.3]
    ends = sorted(set(ends))
    if len(ends) >= 2:
        best = max(((_dist(a, b), a, b) for a in ends for b in ends if a < b), key=lambda t: (t[0], t[1], t[2]))
        try:
            path = _route(adj, best[1], {best[2]})
            arteries.update(zip(path, path[1:]))
            arteries.update(zip(path[1:], path))
        except Exception:
            pass
        ctr = P.centroid
        mid = min(adj, key=lambda v: (_dist(v, (ctr.x, ctr.y)), v)) if adj else None
        if mid is not None:
            try:
                path = _route(adj, mid, set(ends))
                arteries.update(zip(path, path[1:]))
                arteries.update(zip(path[1:], path))
            except Exception:
                pass
    pieces: List[SubRoad] = []
    for i, ch in enumerate(sorted(chains, key=lambda c: tuple(c.coords[0]))):
        cc = [tuple(c) for c in ch.coords]
        art = any((a, b) in arteries for a, b in zip(cc, cc[1:]))
        pieces.append(SubRoad("b%d" % i, "artery" if art else "street", cc))
    if not pieces:
        raise Unfit("no cell borders")

    def key(pt: Point) -> str:
        j = min(range(len(pts)), key=lambda t: (_dist(pts[t], (pt.x, pt.y)), t))
        return "p%d" % j
    return finish(P, pieces, key, min_area)
