"""Polygon-fill contract and shared helpers of the layer fills (design: mapgen/TOWN_LAYERS_SPEC.md section 4).

A fill tiles a polygon with faces and roads; it builds no outline, walls or gates. Each `fill_*` module exports
`SUB_DEFS` and `fill(P, rng, p, frame, min_area) -> Fill`.
"""
from __future__ import annotations

import math
import string
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Sequence, Tuple

from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import linemerge, nearest_points, polygonize, unary_union

from .model import r2

MIN_PIECE = 8.0          # road pieces shorter than this (m) are dropped
STUB_TOL = 0.06          # an end this close to P.boundary or another road is joined


class Unfit(Exception):
    """This polygon cannot take this layout: the caller leaves it a leaf."""


@dataclass
class SubRoad:
    key: str
    cls: str                                    # artery | street
    polyline: List[Tuple[float, float]]


@dataclass
class Fill:
    faces: List[Tuple[str, Polygon]]
    roads: List[SubRoad]


@dataclass
class Frame:
    origin: Tuple[float, float]
    rot: float                                  # radians: direction of the first layout axis
    roads: Any = None                           # shapely geometry of the parent roads (None = unknown); read by `follow`


def lines_of(g) -> List[LineString]:
    if g.is_empty:
        return []
    if g.geom_type == "LineString":
        return [g]
    if g.geom_type in ("MultiLineString", "GeometryCollection"):
        out: List[LineString] = []
        for h in g.geoms:
            out.extend(lines_of(h))
        return out
    return []


def line_pieces(line: LineString, P: Polygon) -> List[LineString]:
    """The parts of `line` inside P, ordered along `line`, merged where they join."""
    parts = lines_of(line.intersection(P))
    if len(parts) > 1:
        parts = lines_of(linemerge(parts))
    parts = [q for q in parts if q.length > 1e-6]
    return sorted(parts, key=lambda q: (line.project(Point(q.coords[0])), line.project(Point(q.coords[-1]))))


def _suffix(i: int) -> str:
    return "" if i == 0 else string.ascii_lowercase[(i - 1) % 26]


def _share(a: Polygon, b: Polygon) -> float:
    return a.boundary.intersection(b.boundary).length


def _merge_small(items: List[List], min_area: float) -> None:
    """items: [key, polygon]; merge faces under `min_area` into the neighbour sharing the longest border (smallest first)."""
    while len(items) > 1:
        small = sorted((it for it in items if it[1].area < min_area), key=lambda it: (it[1].area, it[0]))
        if not small:
            return
        merged = False
        for it in small:
            nb = sorted(((_share(it[1], o[1]), o) for o in items if o is not it and o[1].envelope.intersects(it[1].envelope)),
                        key=lambda t: (-t[0], t[1][0]))
            for ln, o in nb:
                if ln < 0.5:
                    break
                u = unary_union([it[1], o[1]])
                if u.geom_type == "Polygon" and not u.interiors and u.is_valid:
                    o[1] = u
                    items.remove(it)
                    merged = True
                    break
            if merged:
                break
        if not merged:
            raise Unfit("a face under min_area has no mergeable neighbour")


def _ring(poly: Polygon) -> Polygon:
    return orient(Polygon([(r2(x), r2(y)) for x, y in poly.exterior.coords[:-1]]), 1.0)


def _end_joined(pt, P: Polygon, others) -> bool:
    p = Point(pt)
    return P.boundary.distance(p) <= STUB_TOL or (others is not None and others.distance(p) <= STUB_TOL)


def _extend(ln: LineString, P: Polygon, by: float = 0.3) -> LineString:
    """Push ends lying on P.boundary slightly outward so they cross it (exact noding); the clip to P restores them."""
    c = [tuple(q) for q in ln.coords]
    if c[0] == c[-1] or len(c) < 2:
        return ln
    for end, nb, idx in ((c[0], c[1], 0), (c[-1], c[-2], -1)):
        if P.boundary.distance(Point(end)) <= STUB_TOL:
            d = math.hypot(end[0] - nb[0], end[1] - nb[1]) or 1.0
            x, y = end[0] + (end[0] - nb[0]) / d * by, end[1] + (end[1] - nb[1]) / d * by
            if idx == 0:
                c.insert(0, (x, y))
            else:
                c.append((x, y))
    return LineString(c)


extend_ends = _extend


def finish(P: Polygon, pieces: Sequence[SubRoad], face_key: Callable[[Point], str], min_area: float) -> Fill:
    """Common tail of every fill: polygonize `P.boundary` plus the candidate road pieces into faces, merge small faces, drop road
    pieces buried in merged faces and stubs, round coordinates. Raises Unfit unless at least two valid faces tile P."""
    P = orient(P, 1.0)
    lines = [_extend(LineString(r.polyline), P) for r in pieces]
    noded = unary_union([P.boundary] + lines)      # ends past the boundary stay: they node exactly, polygonize drops the dangles
    keep = P.buffer(0.01)
    raw: List[Tuple[Polygon, Point]] = []
    for f in polygonize(noded):
        if f.area < 0.5:          # degenerate sliver between a line and its own overshoot
            continue
        rp = f.representative_point()
        if keep.contains(rp):
            raw.append((f, rp))
    if len(raw) < 2:
        raise Unfit("fewer than 2 faces")
    raw.sort(key=lambda t: (round(t[1].x, 3), round(t[1].y, 3)))
    names: Dict[str, int] = {}
    items: List[List] = []
    for f, rp in raw:
        base = face_key(rp)
        n = names.get(base, 0)
        names[base] = n + 1
        items.append([base + _suffix(n), f])
    _merge_small(items, min_area)
    if len(items) < 2:
        raise Unfit("fewer than 2 faces after merging")
    items.sort(key=lambda it: it[0])
    inner = unary_union([it[1].buffer(-0.05) for it in items])
    roads: List[SubRoad] = []
    for r in sorted(pieces, key=lambda r: r.key):
        ln = LineString(r.polyline).intersection(P.buffer(0.01))
        parts = lines_of(ln.difference(inner))
        if len(parts) > 1:
            parts = lines_of(linemerge(parts))
        parts = [q for q in parts if q.length >= MIN_PIECE]
        parts.sort(key=lambda q: q.coords[0])
        for i, q in enumerate(parts):
            roads.append(SubRoad(r.key if len(parts) == 1 else "%s_%d" % (r.key, i), r.cls, [tuple(c) for c in q.coords]))
    changed = True
    while changed:
        changed = False
        for r in list(roads):
            if r.polyline[0] == r.polyline[-1]:
                continue
            others = unary_union([LineString(o.polyline) for o in roads if o is not r]) if len(roads) > 1 else None
            if not (_end_joined(r.polyline[0], P, others) and _end_joined(r.polyline[-1], P, others)):
                roads.remove(r)
                changed = True
                break
    for r in roads:
        if r.polyline[0] == r.polyline[-1]:
            continue
        others = unary_union([LineString(o.polyline) for o in roads if o is not r]) if len(roads) > 1 else None
        for idx in (0, -1):
            e = Point(r.polyline[idx])
            best = None
            for tgt in (P.boundary, others):
                if tgt is None:
                    continue
                d = tgt.distance(e)
                if best is None or d < best[0]:
                    best = (d, nearest_points(tgt, e)[0])
            if best is not None and 1e-9 < best[0] <= STUB_TOL:
                r.polyline[idx] = (best[1].x, best[1].y)
    faces = []
    for k, f in items:
        q = _ring(f)
        if not q.is_valid or q.is_empty or q.interiors:
            raise Unfit("invalid face %s" % k)
        faces.append((k, q))
    if abs(sum(q.area for _, q in faces) - P.area) > 0.005 * P.area:
        raise Unfit("faces do not tile the polygon")
    out = [SubRoad(r.key, r.cls, [(r2(x), r2(y)) for x, y in r.polyline]) for r in sorted(roads, key=lambda r: r.key)]
    return Fill(faces, [r for r in out if len(set(r.polyline)) >= 2])


def overshoot(coords, P: Polygon, by: float = 0.4) -> List[Tuple[float, float]]:
    """Push each end of a polyline that is not on P.boundary slightly further along its tangent so a T junction with another
    piece is a true crossing (exact noding); `finish` removes the overshoot again."""
    c = [tuple(q) for q in coords]
    out = list(c)
    for end, nb, first in ((c[0], c[1], True), (c[-1], c[-2], False)):
        if P.boundary.distance(Point(end)) <= STUB_TOL:
            continue
        d = math.hypot(end[0] - nb[0], end[1] - nb[1]) or 1.0
        q = (end[0] + (end[0] - nb[0]) / d * by, end[1] + (end[1] - nb[1]) / d * by)
        if first:
            out.insert(0, q)
        else:
            out.append(q)
    return out


def roads_only(P: Polygon, fl: Fill, prefix: str) -> Fill:
    """Drop the roads whose key starts with `prefix` (borders that are not streets) from a finished fill, then every road left
    with an end on neither P.boundary nor another road."""
    roads = [r for r in fl.roads if r.key.startswith(prefix)]
    changed = True
    while changed:
        changed = False
        for r in roads:
            others = unary_union([LineString(o.polyline) for o in roads if o is not r]) if len(roads) > 1 else None
            if r.polyline[0] != r.polyline[-1] and not (_end_joined(r.polyline[0], P, others) and _end_joined(r.polyline[-1], P, others)):
                roads.remove(r)
                changed = True
                break
    return Fill(fl.faces, roads)


def unit(rot: float) -> Tuple[float, float]:
    return math.cos(rot), math.sin(rot)


def far(P: Polygon) -> float:
    minx, miny, maxx, maxy = P.bounds
    return math.hypot(maxx - minx, maxy - miny) + 10.0
