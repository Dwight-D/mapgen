"""Radial fill: a core, concentric rings and spokes from the first ring outward (design: mapgen/TOWN_LAYERS_SPEC.md section 4.2)."""
from __future__ import annotations

import math
import random
from typing import Any, Dict, List

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import polylabel

from .layerfill import Fill, Frame, SubRoad, Unfit, far, finish, line_pieces
from .params import ParamDef

SUB_DEFS = [
    ParamDef("spokes", "int", 3, 8, "spokes (0 = from the perimeter)", default=0),
    ParamDef("rings", "int", 1, 2, "concentric rings (0 = from the area)", default=0),
    ParamDef("ring_frac", "float", 0.3, 0.7, "radius of ring 1 as a fraction of the mean hub-to-boundary distance",
             draw_lo=0.4, draw_hi=0.6),
    ParamDef("phase", "float", 0.0, 360.0, "angle of the first spoke relative to the frame, degrees", draw_lo=0.0, draw_hi=30.0),
]
RAYS = 72


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
    r1 = float(p["ring_frac"]) * R
    radii = [r1] if rings == 1 else [r1, r1 + (R - r1) * 0.5]
    a0 = frame.rot + math.radians(float(p["phase"]))
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
