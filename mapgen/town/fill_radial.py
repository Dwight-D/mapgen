"""Radial fill: a core, concentric rings and spokes from the first ring outward (design: mapgen/TOWN_LAYERS_SPEC.md section 4.2)."""
from __future__ import annotations

import math
import random
from typing import Any, Dict, List

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import polylabel

from .layerfill import Fill, Frame, SubRoad, Unfit, far, finish, line_pieces, lines_of
from .params import ParamDef

SUB_DEFS = [
    ParamDef("spokes", "int", 3, 8, "spokes (0 = from the perimeter)", default=0),
    ParamDef("rings", "int", 1, 2, "concentric rings (0 = from the area)", default=0),
    ParamDef("ring_frac", "float", 0.4, 0.9, "radius of ring 1 as a fraction of the mean hub-to-boundary distance",
             draw_lo=0.52, draw_hi=0.78),
    ParamDef("phase", "float", 0.0, 360.0, "angle of the first spoke relative to the frame, degrees", draw_lo=0.0, draw_hi=30.0),
]
RAYS = 72
SCALES = (1.0, 0.9, 0.8, 0.7, 0.6)   # ring radius factors tried in turn
SOLIDITY = 0.4             # a face under this fraction of its convex hull wraps around the core
GRAZE_BAND = 2.5           # m
GRAZE_LEN = 12.0           # m


def _grazes(hub: Point, r: float, P: Polygon) -> bool:
    """True when the ring of radius `r` runs within GRAZE_BAND of the boundary over more than GRAZE_LEN in one piece (a shallow crossing)."""
    ring = Point(hub.x, hub.y).buffer(r, 32).exterior
    near = ring.intersection(P).intersection(P.boundary.buffer(GRAZE_BAND))
    return any(q.length > GRAZE_LEN for q in lines_of(near))


def fill(P: Polygon, rng: random.Random, p: Dict[str, Any], frame: Frame, min_area: float) -> Fill:
    hub = polylabel(P, tolerance=1.0)
    L = far(P)
    dists: List[float] = []
    for i in range(RAYS):
        a = 2 * math.pi * i / RAYS
        ray = LineString([(hub.x, hub.y), (hub.x + L * math.cos(a), hub.y + L * math.sin(a))])
        inter = ray.intersection(P)
        if inter.geom_type != "LineString":
            raise Unfit("not star-shaped about the hub")
        dists.append(inter.length)
    R = sum(dists) / len(dists)
    n = int(p["spokes"]) or max(3, min(8, int(round(P.length / 90.0))))
    rings = int(p["rings"]) or (1 if P.area < 20000.0 else 2)
    r0 = float(p["ring_frac"]) * R
    why = Unfit("no ring radius fits")
    for f in SCALES:        # the drawn radius first, then smaller ones when the ring grazes the boundary or a face wraps around the core
        r1 = r0 * f
        radii = [r1] if rings == 1 else [r1, r1 + (R - r1) * 0.5]
        if any(_grazes(hub, r, P) for r in radii):
            why = Unfit("a ring grazes the boundary")
            continue
        try:
            fl = _fill(P, hub, L, n, radii, frame.rot + math.radians(float(p["phase"])), min_area)
        except Unfit as e:
            why = e
            continue
        if any(not q.is_valid or q.area < SOLIDITY * q.convex_hull.area for _, q in fl.faces):
            why = Unfit("a face wraps around the core")
            continue
        return fl
    raise why


def _fill(P: Polygon, hub: Point, L: float, n: int, radii: List[float], a0: float, min_area: float) -> Fill:
    pieces: List[SubRoad] = []
    for k in range(n):
        a = a0 + 2 * math.pi * k / n
        ray = LineString([(hub.x + 0.8 * radii[0] * math.cos(a), hub.y + 0.8 * radii[0] * math.sin(a)),
                          (hub.x + L * math.cos(a), hub.y + L * math.sin(a))])
        for i, q in enumerate(line_pieces(ray, P)):
            pieces.append(SubRoad("spoke%d_%d" % (k, i), "artery", [tuple(c) for c in q.coords]))
    for j, r in enumerate(radii):
        circ = Point(hub.x, hub.y).buffer(r, 32).exterior
        for i, q in enumerate(line_pieces(LineString(circ.coords), P)):
            pieces.append(SubRoad("ring%d_%d" % (j + 1, i), "street", [tuple(c) for c in q.coords]))
    step = 2 * math.pi / n

    def key(pt: Point) -> str:
        d = math.hypot(pt.x - hub.x, pt.y - hub.y)
        ring = sum(1 for r in radii if d > r)
        if ring == 0:
            return "core"
        ang = (math.atan2(pt.y - hub.y, pt.x - hub.x) - a0) % (2 * math.pi)
        return "r%ds%d" % (ring, int(ang // step))
    return finish(P, pieces, key, min_area)
