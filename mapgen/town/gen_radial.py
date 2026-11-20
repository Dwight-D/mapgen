"""`radial` generator: ring roads, wedge districts, a central plaza, an irregular wall with gates on spokes
(design: mapgen/TOWN_SPEC.md section 7.1)."""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from shapely.geometry import Point, Polygon

from ..noise import fbm1_periodic
from . import graph as G
from .model import District, Gate, GraphNode, Landmark, Road, Settlement, Wall, pt, r2
from .params import ParamDef, stage_rng

NAME = "radial"
GRID_DEG = 5.0                  # angular step of the shared vertex grid
GRID_SKIP_DEG = 1.5             # grid angles this close to a spoke angle are dropped (no sliver edges)

DEFS = [
    ParamDef("radius_frac", "float", 0.3, 0.95, "diameter of the walled area as a fraction of the map's shorter side",
             draw_lo=0.45, draw_hi=0.75),
    ParamDef("spokes", "int", 4, 14, "number of spokes (radial boundaries, most of them roads)", draw_lo=5, draw_hi=10),
    ParamDef("rings", "int", 1, 4, "number of ring roads inside the wall", draw_lo=1, draw_hi=3),
    ParamDef("ring_spacing", "float", 0.6, 2.0, "exponent of the ring radii; above 1 packs rings toward the centre",
             draw_lo=0.8, draw_hi=1.4),
    ParamDef("plaza_frac", "float", 0.06, 0.3, "plaza radius as a fraction of the wall radius", draw_lo=0.1, draw_hi=0.18),
    ParamDef("angle_jitter", "float", 0.0, 0.3, "spoke angle jitter as a fraction of the spoke spacing",
             draw_lo=0.0, draw_hi=0.25),
    ParamDef("spoke_dropout", "float", 0.0, 0.8, "probability that a non-core spoke starts at an outer ring instead of the plaza",
             draw_lo=0.0, draw_hi=0.4),
    ParamDef("wall_irregularity", "float", 0.0, 0.2, "radial noise amplitude of the rings and wall, fraction of the radius",
             draw_lo=0.04, draw_hi=0.16),
    ParamDef("wall_freq", "int", 1, 8, "noise lattice cells around the wall", draw_lo=2, draw_hi=5),
    ParamDef("elongation", "float", 1.0, 1.6, "stretch along the rotated x axis (area preserving)", draw_lo=1.0, draw_hi=1.3),
    ParamDef("rotation", "float", 0.0, 360.0, "rotation of the whole layout, degrees"),
    ParamDef("gates", "int", 2, 12, "number of gates in the outer wall", draw_lo=3, draw_hi=6),
    ParamDef("citadel", "bool", 0, 1, "reserve an outer wedge next to the wall as a citadel", draw_hi=0.5),
    ParamDef("min_district_area", "float", 100.0, 20000.0, "smallest allowed district, m2 (spokes then rings are dropped to fit)",
             default=1600.0),
    ParamDef("min_ring_gap", "float", 20.0, 200.0, "smallest radial gap between plaza, rings and wall, m", default=40.0),
]


class GenerationError(Exception):
    pass


class _Refit(Exception):
    """The lattice does not fit the size limits; drop a spoke or ring and retry."""


class _Geo:
    """The polar lattice and its mapping to map coordinates."""

    def __init__(self, p: Dict[str, Any], size: Tuple[float, float], centre: Tuple[float, float], seed: int,
                 sid: str, spokes_theta: List[float]):
        self.p = p
        self.centre = centre
        e = float(p["elongation"])
        self.sx, self.sy = math.sqrt(e), 1.0 / math.sqrt(e)
        rot = math.radians(p["rotation"])
        self.cos, self.sin = math.cos(rot), math.sin(rot)
        irr = float(p["wall_irregularity"])
        margin = min(centre[0], centre[1], size[0] - centre[0], size[1] - centre[1]) - 10.0
        R = p["radius_frac"] * min(size) / 2.0
        R = min(R, margin / ((1.0 + irr) * self.sx))
        if R < 60.0:
            raise GenerationError("map %gx%g is too small for a settlement (wall radius %.1f m < 60 m)" % (size[0], size[1], R))
        self.R = R
        self.rp = max(25.0, p["plaza_frac"] * R)
        L = p["rings"] + 1
        self.L = L
        self.radii = [self.rp + (R - self.rp) * (i / float(L)) ** p["ring_spacing"] for i in range(L + 1)]
        if min(self.radii[i + 1] - self.radii[i] for i in range(L)) < p["min_ring_gap"]:
            raise _Refit("ring gap below min_ring_gap")
        grid = [round(2 * math.pi * k / (360.0 / GRID_DEG), 6) for k in range(int(360 / GRID_DEG))]
        skip = math.radians(GRID_SKIP_DEG)
        angles = set(spokes_theta)
        for a in grid:
            if all(min(abs(a - t), 2 * math.pi - abs(a - t)) > skip for t in spokes_theta):
                angles.add(a)
        self.angles = sorted(angles)
        nrng = stage_rng(sid, NAME, seed, "wall")
        ns = nrng.randrange(1 << 30)
        vals = fbm1_periodic(np.array(self.angles) / (2 * math.pi), p["wall_freq"], ns, 2)
        self.noise = {a: float(v) for a, v in zip(self.angles, vals)}
        self.irr = irr

    def vec(self, vx: float, vy: float) -> Tuple[float, float]:
        x, y = vx * self.sx, vy * self.sy
        return x * self.cos - y * self.sin, x * self.sin + y * self.cos

    def P(self, level: int, theta: float) -> List[float]:
        r = self.radii[level]
        rel = (r - self.rp) / (self.R - self.rp)
        rr = r * (1.0 + self.irr * self.noise[theta] * rel)
        x, y = self.vec(rr * math.cos(theta), rr * math.sin(theta))
        return pt(self.centre[0] + x, self.centre[1] + y)

    def ring(self, level: int) -> List[List[float]]:
        return [self.P(level, a) for a in self.angles]

    def arc(self, level: int, ia: int, ib: int) -> List[List[float]]:
        """Vertices of `level` from angle index ia to ib going counter-clockwise (wrapping)."""
        n = len(self.angles)
        out, j = [], ia
        while True:
            out.append(self.P(level, self.angles[j]))
            if j == ib:
                return out
            j = (j + 1) % n


def _min_area(s: Settlement) -> float:
    return min(d.area for d in s.districts)


def _build(p: Dict[str, Any], seed: int, size: Tuple[float, float], centre: Tuple[float, float], sid: str) -> Settlement:
    S, rings = int(p["spokes"]), int(p["rings"])
    srng = stage_rng(sid, NAME, seed, "spokes")
    thetas: List[float] = []
    aj = float(p["angle_jitter"])
    for s in range(S):
        j = srng.uniform(-aj, aj)
        if s == 0:
            j = abs(j)
        thetas.append(round(2 * math.pi * (s + j) / S, 6))
    core = {int(round(k * S / 3.0)) % S for k in range(3)}
    start = []
    for s in range(S):
        u, lvl = srng.random(), srng.randint(1, rings)
        start.append(lvl if (s not in core and u < p["spoke_dropout"]) else 0)
    grng = stage_rng(sid, NAME, seed, "gates")
    g = min(int(p["gates"]), S)
    off = grng.random()
    gated: List[int] = []
    for j in range(g):
        idx = int((j + off) * S / g) % S
        while idx in gated:
            idx = (idx + 1) % S
        gated.append(idx)
    gated_set = set(gated)

    geo = _Geo(p, size, centre, seed, sid, thetas)
    L = geo.L
    ai = {t: geo.angles.index(t) for t in thetas}
    cpt = pt(centre[0], centre[1])

    # districts: plaza and annular wedges
    districts: List[District] = []
    plaza_id = "%s_d_plaza" % sid
    plaza_poly = geo.ring(0)
    districts.append(District(id=plaza_id, key="plaza", kind="core", polygon=plaza_poly,
                              area=r2(Polygon(plaza_poly).area), label="Central Plaza"))
    wedge_span = {}     # key -> (annulus, spoke a, spoke b)
    for i in range(L):
        present = [s for s in range(S) if start[s] <= i]
        for k, a in enumerate(present):
            b = present[(k + 1) % len(present)]
            outer = geo.arc(i + 1, ai[thetas[a]], ai[thetas[b]])
            inner = geo.arc(i, ai[thetas[a]], ai[thetas[b]])
            ring_pts = outer + inner[::-1]
            poly = Polygon(ring_pts)
            if not poly.is_valid or poly.area <= 0:
                raise GenerationError("invalid district polygon r%ds%d" % (i, a))
            key = "r%ds%d" % (i, a)
            kind = "market" if i == 0 else ("craft" if i == L - 1 else "residential")
            wedge_span[key] = (i, a, b)
            districts.append(District(id="%s_d_%s" % (sid, key), key=key, kind=kind, polygon=ring_pts,
                                      area=r2(poly.area), label=""))

    # citadel: the outer wedge farthest (by angle) from every gate
    landmarks: List[Landmark] = []
    if p["citadel"] and L >= 1:
        best, best_key = -1.0, None
        for key in sorted(wedge_span):
            i, a, b = wedge_span[key]
            if i != L - 1:
                continue
            ta, tb = thetas[a], thetas[b] + (2 * math.pi if b <= a else 0.0)
            mid = ((ta + tb) / 2.0) % (2 * math.pi)
            gd = min(min(abs(mid - thetas[s]), 2 * math.pi - abs(mid - thetas[s])) for s in gated)
            if gd > best + 1e-9:
                best, best_key = gd, key
        if best_key is not None:
            d = next(x for x in districts if x.key == best_key)
            d.kind = "citadel"
            rp_ = Polygon(d.polygon).representative_point()
            landmarks.append(Landmark(id="%s_citadel" % sid, kind="citadel", at=pt(rp_.x, rp_.y), r=45.0,
                                      district=d.id, label="Citadel"))
            d.landmarks.append("%s_citadel" % sid)
    landmarks.insert(0, Landmark(id="%s_plaza" % sid, kind="square", at=cpt, r=r2(geo.rp), district=plaza_id,
                                 label="Central Plaza"))
    districts[0].landmarks.append("%s_plaza" % sid)
    names = {"market": "Market Ward", "craft": "Craft Quarter", "residential": "Residential Ward", "citadel": "Citadel Ward"}
    counters: Dict[str, int] = {}
    for d in districts[1:]:
        counters[d.kind] = counters.get(d.kind, 0) + 1
        d.label = "%s %d" % (names[d.kind], counters[d.kind])

    # roads, nodes
    roads: List[Road] = []
    nodes: Dict[str, GraphNode] = {"%s_plaza" % sid: GraphNode(id="%s_plaza" % sid, kind="zone", at=cpt)}

    def junction(i: int, s: int) -> str:
        nid = "%s_j_r%ds%d" % (sid, i, s)
        if nid not in nodes:
            nodes[nid] = GraphNode(id=nid, kind="choke", at=geo.P(i, thetas[s]))
        return nid

    gates: List[Gate] = []
    wall_id = "%s_wall_outer" % sid
    for s in range(S):
        end = L if s in gated_set else rings
        lv0 = max(start[s], 1)
        if end <= start[s]:
            continue
        poly: List[List[float]] = []
        ids: List[str] = []
        if start[s] == 0:
            poly.append(cpt)
            ids.append("%s_plaza" % sid)
        for i in range(lv0, end + 1):
            poly.append(geo.P(i, thetas[s]))
            if i == L:
                gid = "%s_gate_%d" % (sid, s)
                nodes[gid] = GraphNode(id=gid, kind="choke", at=geo.P(i, thetas[s]))
                ids.append(gid)
            else:
                ids.append(junction(i, s))
        rid = "%s_spoke_%d" % (sid, s)
        roads.append(Road(id=rid, cls="artery", polyline=poly, width=14.0 if s in gated_set else 10.0, nodes=ids))
        if s in gated_set:
            at = geo.P(L, thetas[s])
            nx, ny = geo.vec(math.cos(thetas[s]), math.sin(thetas[s]))
            h = math.hypot(nx, ny)
            gates.append(Gate(id="%s_gate_%d" % (sid, s), wall=wall_id, at=at, normal=[r2(nx / h), r2(ny / h)], road=rid))
    for i in range(1, rings + 1):
        members = [s for s in range(S) if start[s] <= i]
        ids = [junction(i, s) for s in members]
        roads.append(Road(id="%s_ring_%d" % (sid, i), cls="ring", polyline=geo.ring(i) + [geo.P(i, geo.angles[0])],
                          width=10.0, nodes=ids + [ids[0]]))
    # the ring polyline starts at angles[0]; rotate it so it starts at the first member junction
    for road in roads:
        if road.cls != "ring":
            continue
        first = nodes[road.nodes[0]].at
        body = road.polyline[:-1]
        k = body.index(first)
        body = body[k:] + body[:k]
        road.polyline = body + [body[0]]

    wall_poly = geo.ring(L)
    walls = [Wall(id=wall_id, kind="outer", polyline=wall_poly + [wall_poly[0]], thickness=8.0)]

    gnodes = [nodes[k] for k in sorted(nodes)]
    G.attach_districts(gnodes, districts)
    # roads in id order for a stable file; edges derive from road node order
    roads.sort(key=lambda r: r.id)
    edges = G.movement_edges(roads, nodes)
    dn, de = G.district_graph(districts, roads, walls, gates)
    gates.sort(key=lambda x: x.id)
    return Settlement(id=sid, generator=NAME, seed=seed, params=dict(p), size=(size[0], size[1]), centre=cpt,
                      footprint=wall_poly, districts=districts, roads=roads, walls=walls, gates=gates,
                      landmarks=landmarks, graph_nodes=gnodes, graph_edges=edges, district_nodes=dn, district_edges=de)


def generate(p: Dict[str, Any], seed: int, size: Tuple[float, float], centre: Tuple[float, float], sid: str) -> Settlement:
    """Build from resolved parameters; drops spokes, then rings, until every district and ring gap fits."""
    p = dict(p)
    for _ in range(32):
        try:
            s = _build(p, seed, size, centre, sid)
            if _min_area(s) >= p["min_district_area"]:
                return s
        except _Refit:
            pass
        if p["spokes"] > 4:
            p["spokes"] -= 1
        elif p["rings"] > 1:
            p["rings"] -= 1
        else:
            break
    raise GenerationError("cannot fit districts of at least %g m2 and gaps of %g m into this settlement; raise radius_frac "
                          "or lower min_district_area / min_ring_gap" % (p["min_district_area"], p["min_ring_gap"]))
