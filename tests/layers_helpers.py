"""Shared helpers of the town layer tests (design: mapgen/TOWN_LAYERS_SPEC.md section 14)."""
import math
import random

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

from mapgen.town.layerfill import Frame


def convex_polygon(rng, n=None, lo=8000.0, hi=150000.0):
    n = n or rng.randint(3, 9)
    area = rng.uniform(lo, hi)
    r = math.sqrt(area / math.pi) * 1.3
    angs = sorted(rng.uniform(0, 2 * math.pi) for _ in range(n))
    pts = [(r * rng.uniform(0.8, 1.0) * math.cos(a) + 500, r * rng.uniform(0.8, 1.0) * math.sin(a) + 500) for a in angs]
    p = Polygon(pts).convex_hull
    return p if p.area > 0.5 * lo else Polygon([(0, 0), (300, 0), (300, 300), (0, 300)])


def l_shape():
    return Polygon([(0, 0), (300, 0), (300, 120), (130, 120), (130, 300), (0, 300)])


def polygons():
    rng = random.Random(7)
    return [convex_polygon(rng) for _ in range(30)], [l_shape(), Polygon([(0, 0), (250, 10), (260, 200), (20, 240)])]


def frame_of(P, rot=0.3):
    c = P.centroid if P.contains(P.centroid) else P.representative_point()
    return Frame((c.x, c.y), rot)


def check_fill_contract(P, fl, min_area):
    """The section 4 contract of a fill."""
    assert len(fl.faces) >= 2
    keys = [k for k, _ in fl.faces]
    assert len(set(keys)) == len(keys)
    union = unary_union([q for _, q in fl.faces])
    assert abs(union.area - P.area) <= 0.005 * P.area
    for k, q in fl.faces:
        assert q.is_valid and not q.interiors and q.area >= min_area - 1.0, k
    for i in range(len(fl.faces)):
        for j in range(i + 1, len(fl.faces)):
            assert fl.faces[i][1].intersection(fl.faces[j][1]).area < 1.0
    inner = unary_union([q.buffer(-0.1) for _, q in fl.faces])
    border = P.boundary
    lines = [LineString(r.polyline) for r in fl.roads]
    for r, ln in zip(fl.roads, lines):
        assert r.cls in ("artery", "street")
        assert ln.within(P.buffer(0.05))
        assert ln.intersection(inner).length < 0.5, "road %s buried in a face" % r.key
        if r.polyline[0] != r.polyline[-1]:
            others = unary_union([o for o in lines if o is not ln]) if len(lines) > 1 else None
            for e in (r.polyline[0], r.polyline[-1]):
                ok = border.distance(Point(e)) <= 0.1 or (others is not None and others.distance(Point(e)) <= 0.1)
                assert ok, "road %s has a dangling end" % r.key
        assert all(round(c, 2) == c for pt in r.polyline for c in pt)
    assert [r.key for r in fl.roads] == sorted(r.key for r in fl.roads)
