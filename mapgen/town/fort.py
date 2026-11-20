"""Fort add-on: a post-processing step that adds a fortress and outlying fortified sites to any generator's settlement
(design: mapgen/TOWN_SPEC.md section 7.4).

`apply(settlement, params)` carves a reserved polygon (rectangle with corner towers or star bastion) out of the districts,
gives it its own `citadel` wall with an inward and an outward gate, a `citadel` district and a `fortress` landmark, cuts
the roads that cross it, and rebuilds the movement and district graphs. Detached forts (`island`, `fort_outposts`) are
separate districts outside the footprint joined by an `approach` road from an outer gate.
"""
from __future__ import annotations

import copy
import math
from typing import Any, Dict, List, Optional, Tuple

import shapely
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

from . import graph as G
from .check import check
from .gen_radial import GenerationError
from .model import District, DistrictEdge, Gate, GraphNode, Landmark, Road, Settlement, Wall, r2
from .params import ParamError, stage_rng

MODES = ("none", "corner", "centre", "peninsula", "island")
SHAPES = ("auto", "rectangle", "star")
DOC = {
    "fort": "none | corner (inside, on the outer wall) | centre (inside, off the plaza) | peninsula (juts out of the outer "
            "wall, footprint grows) | island (detached, joined by an approach road)",
    "fort_shape": "auto (star on voronoi, else rectangle) | rectangle (corner towers) | star (bastion tips)",
    "fort_size": "fort half extent as a fraction of the settlement radius, 0.08-0.45 (default 0.22)",
    "fort_outposts": "extra detached fortified sites outside the outer wall, 0-4 (default 0)",
}
MARGIN = 10.0
TOWER_MAX_ANGLE = 150.0
_SUFFIX = "bcdefghijklmnop"
Pt = Tuple[float, float]


class _Reject(Exception):
    pass


def is_fort_param(name: str) -> bool:
    return name == "fort" or name.startswith("fort_")


def resolve(overrides: Dict[str, Any]) -> Dict[str, Any]:
    """Fort parameters with defaults; unknown names and bad values are errors (all collected)."""
    errors: List[str] = []
    out: Dict[str, Any] = {"fort": "none", "fort_shape": "auto", "fort_size": 0.22, "fort_outposts": 0}
    for k in sorted(overrides):
        v = overrides[k]
        if k not in out:
            errors.append("params.%s: unknown parameter (known: %s)" % (k, ", ".join(sorted(out))))
        elif k in ("fort", "fort_shape"):
            allowed = MODES if k == "fort" else SHAPES
            if v not in allowed:
                errors.append("params.%s: %r is not one of %s" % (k, v, ", ".join(allowed)))
            else:
                out[k] = v
        else:
            try:
                if isinstance(v, bool):
                    raise ValueError
                x = float(v)
                if k == "fort_outposts":
                    if x != int(x) or not (0 <= x <= 4):
                        raise ValueError
                    out[k] = int(x)
                else:
                    if not (0.08 <= x <= 0.45):
                        raise ValueError
                    out[k] = x
            except (ValueError, TypeError):
                errors.append("params.%s: invalid value %r (%s)" % (k, v, "integer 0-4" if k == "fort_outposts" else "0.08-0.45"))
    if errors:
        raise ParamError(errors)
    return out


def active(p: Dict[str, Any]) -> bool:
    return p["fort"] != "none" or p["fort_outposts"] > 0


# --- geometry helpers -------------------------------------------------------------------------------------------

def _tup(c) -> Pt:
    return (r2(c[0]), r2(c[1]))


def _dedupe(pts: List[Pt]) -> List[Pt]:
    out: List[Pt] = []
    for p in pts:
        if not out or out[-1] != p:
            out.append(p)
    if len(out) > 1 and out[0] == out[-1]:
        out.pop()
    return out


def _ring(g: Polygon) -> List[Pt]:
    return _dedupe([_tup(c) for c in orient(g, 1.0).exterior.coords][:-1])


def _polys(g) -> List[Polygon]:
    if g.is_empty:
        return []
    if g.geom_type == "Polygon":
        return [g]
    return [x for x in getattr(g, "geoms", []) if x.geom_type == "Polygon"]


def _lines(g) -> List[LineString]:
    if g.is_empty:
        return []
    if g.geom_type == "LineString":
        return [g]
    return [x for x in getattr(g, "geoms", []) if x.geom_type == "LineString"]


def _embed(ring: List[Pt], new: List[Pt], tol: float = 0.02) -> List[Pt]:
    """Insert every point of `new` that lies within `tol` of an edge (and is not already a vertex) into the closed ring."""
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    near = [q for q in new if min(xs) - tol <= q[0] <= max(xs) + tol and min(ys) - tol <= q[1] <= max(ys) + tol]
    if not near:
        return ring
    have = set(ring)
    ins: Dict[int, List[Tuple[float, Pt]]] = {}
    n = len(ring)
    for q in near:
        if q in have:
            continue
        best = None
        for i in range(n):
            a, b = ring[i], ring[(i + 1) % n]
            dx, dy = b[0] - a[0], b[1] - a[1]
            L2 = dx * dx + dy * dy
            if L2 <= 0:
                continue
            t = ((q[0] - a[0]) * dx + (q[1] - a[1]) * dy) / L2
            if t <= 0.0 or t >= 1.0:
                continue
            d = math.hypot(a[0] + t * dx - q[0], a[1] + t * dy - q[1])
            if d <= tol and (best is None or d < best[0]):
                best = (d, i, t)
        if best is not None:
            ins.setdefault(best[1], []).append((best[2], q))
    if not ins:
        return ring
    out: List[Pt] = []
    for i in range(n):
        out.append(ring[i])
        for _, q in sorted(ins.get(i, [])):
            out.append(q)
    return out


def _rect(c: Pt, a: float, b: float, th: float) -> List[Pt]:
    n = (math.cos(th), math.sin(th))
    t = (-n[1], n[0])
    return [_tup((c[0] + sa * a * t[0] + sb * b * n[0], c[1] + sa * a * t[1] + sb * b * n[1]))
            for sa, sb in ((-1, -1), (1, -1), (1, 1), (-1, 1))]


def _star(c: Pt, r_out: float, r_in: float, tips: int, th: float) -> List[Pt]:
    return [_tup((c[0] + (r_out if k % 2 == 0 else r_in) * math.cos(th + k * math.pi / tips),
                  c[1] + (r_out if k % 2 == 0 else r_in) * math.sin(th + k * math.pi / tips))) for k in range(2 * tips)]


def _shape(kind: str, c: Pt, h: float, th: float, tips: int) -> Optional[Polygon]:
    pts = _rect(c, 1.1 * h, 0.8 * h, th) if kind == "rectangle" else _star(c, 1.15 * h, 0.78 * h, tips, th)
    g = Polygon(pts)
    if not g.is_valid or g.area < 1.0:
        return None
    return g


def towers(ring: List[Pt]) -> List[List[float]]:
    """Wall towers: the convex corners of a CCW ring that turn by more than 30 degrees (rectangle corners, bastion tips)."""
    out: List[List[float]] = []
    n = len(ring)
    for i in range(n):
        a, b, c = ring[i - 1], ring[i], ring[(i + 1) % n]
        v1, v2 = (a[0] - b[0], a[1] - b[1]), (c[0] - b[0], c[1] - b[1])
        cross = v2[0] * v1[1] - v2[1] * v1[0]          # CCW ring: > 0 is a convex corner
        dot = v1[0] * v2[0] + v1[1] * v2[1]
        n1, n2 = math.hypot(*v1), math.hypot(*v2)
        if n1 < 1e-9 or n2 < 1e-9 or cross <= 0:
            continue
        if math.degrees(math.acos(max(-1.0, min(1.0, dot / (n1 * n2))))) <= TOWER_MAX_ANGLE:
            out.append([b[0], b[1]])
    return out


def _hub(s: Settlement) -> Pt:
    for l in s.landmarks:
        if l.kind in ("square", "market"):
            return (l.at[0], l.at[1])
    return (s.centre[0], s.centre[1])


def _outer_wall(s: Settlement) -> Wall:
    return next(w for w in s.walls if w.kind == "outer")


# --- graph rebuild ----------------------------------------------------------------------------------------------

def _rebuild(s: Settlement, extra_nodes: List[GraphNode]) -> None:
    """Movement graph from the roads' node lists, district graph from the polygons; detached sites join `outside`."""
    nodes = {n.id: n for n in s.graph_nodes}
    for n in extra_nodes:
        nodes[n.id] = n
    used = set()
    for r in s.roads:
        used.update(r.nodes)
    used.update(g.id for g in s.gates)
    gn = [nodes[k] for k in sorted(used)]
    G.attach_districts(gn, s.districts)
    s.graph_nodes = gn
    s.roads.sort(key=lambda r: r.id)
    s.gates.sort(key=lambda g: g.id)
    s.graph_edges = G.movement_edges(s.roads, {n.id: n for n in gn})
    s.district_nodes, s.district_edges = G.district_graph(s.districts, s.roads, s.walls, s.gates)
    for d in s.districts:
        if d.inside_wall:
            continue
        gids = sorted(g.id for g in s.gates if g.id.startswith(d.id.replace("_d_", "_gate_", 1)))
        rids = sorted(r.id for r in s.roads if any(g.road == r.id for g in s.gates if g.id in gids))
        s.district_edges.append(DistrictEdge(id="%s~%s" % (d.id, G.OUTSIDE), a=d.id, b=G.OUTSIDE, kind="wall", length=0.0,
                                             passable=False, gates=gids, roads=rids))
    s.district_edges.sort(key=lambda e: e.id)


def _repair_connectivity(s: Settlement, sid: str, base: str, avoid: Optional[Polygon]) -> None:
    """Join every movement-graph component to the plaza's with a straight `artery` road between the closest node pair."""
    k = 0
    while True:
        ids = {n.id: n for n in s.graph_nodes}
        adj: Dict[str, set] = {i: set() for i in ids}
        for r in s.roads:
            for a, b in zip(r.nodes, r.nodes[1:]):
                adj[a].add(b)
                adj[b].add(a)
        main_id = "%s_plaza" % sid if "%s_plaza" % sid in ids else sorted(ids)[0]
        comp: Dict[str, int] = {}
        for i in sorted(ids):
            if i in comp:
                continue
            comp[i] = len(set(comp.values()))
            todo = [i]
            while todo:
                for m in sorted(adj[todo.pop()]):
                    if m not in comp:
                        comp[m] = comp[i]
                        todo.append(m)
        if len(set(comp.values())) <= 1:
            return
        mc = comp[main_id]
        best = None
        for a in sorted(ids):
            if comp[a] == mc:
                continue
            for b in sorted(ids):
                if comp[b] != mc:
                    continue
                d = math.hypot(ids[a].at[0] - ids[b].at[0], ids[a].at[1] - ids[b].at[1])
                if avoid is not None and avoid.intersects(LineString([ids[a].at, ids[b].at])) and avoid.distance(Point(ids[a].at)) > 0.5 \
                        and avoid.distance(Point(ids[b].at)) > 0.5:
                    d += 1000.0
                if best is None or d < best[0]:
                    best = (d, a, b)
        k += 1
        _, a, b = best
        s.roads.append(Road(id="%s_%s_link%d" % (sid, base, k), cls="artery", polyline=[list(ids[a].at), list(ids[b].at)],
                            width=8.0, nodes=[a, b]))
        if k > 40:
            raise _Reject("cannot connect the movement graph")


# --- inner fort -------------------------------------------------------------------------------------------------

def _carve_inner(s0: Settlement, F: Polygon, mode: str, base: str) -> Settlement:
    s = copy.deepcopy(s0)
    sid = s.id
    minarea = float(s.params.get("min_district_area", 1600.0))
    FP = Polygon(s.footprint)
    if F.area < max(1500.0, minarea):
        raise _Reject("fort too small")
    for g in s.gates:
        if F.distance(Point(g.at)) < 8.0:
            raise _Reject("fort covers a gate")
    for w in s.walls:
        if w.kind != "outer" and F.buffer(3.0).intersects(LineString(w.polyline)):
            raise _Reject("fort meets an inner wall")
    for l in s.landmarks:
        if l.kind in ("square", "market") and F.distance(Point(l.at)) < l.r + 8.0:
            raise _Reject("fort covers the plaza")
    if any(w.kind == "outer" and w.open_shore for w in s.walls) and mode == "peninsula":
        raise _Reject("open shore")

    plan: List[Tuple[District, Optional[List[Polygon]]]] = []
    small: List[Polygon] = []
    for d in s.districts:
        dp = Polygon(d.polygon)
        if not dp.envelope.intersects(F.envelope) or dp.intersection(F).area < 0.5:
            plan.append((d, None))
            continue
        parts = [g for g in _polys(dp.difference(F)) if g.area > 0.01]
        keep: List[Polygon] = []
        for g in parts:
            if g.interiors or g.area < minarea:
                small.append(Polygon(g.exterior))        # swallowed by the fort (a hole means the fort sits inside the piece)
            else:
                keep.append(g)
        plan.append((d, keep))
    F2 = unary_union([F] + small)
    if F2.geom_type == "Polygon" and F2.is_valid:
        F2 = shapely.set_precision(F2, 0.01)      # the 0.01 m grid of the emitted rings: rounding must not self-intersect the fort ring
    if F2.geom_type != "Polygon" or F2.interiors or not F2.is_valid:
        raise _Reject("fort region is not a simple polygon")
    if F2.area > 0.4 * FP.area:
        raise _Reject("fort too large")
    for g in s.gates:
        if F2.distance(Point(g.at)) < 4.0:
            raise _Reject("fort covers a gate")
    for d in s.districts:
        if d.kind == "core" and Polygon(d.polygon).intersection(F2).area > 0.5:
            raise _Reject("fort covers the core")

    # districts
    rings: List[Tuple[str, List[Pt]]] = []          # (district id, ring)
    meta: Dict[str, District] = {}
    order: List[str] = []
    for d, keep in plan:
        if keep is None:
            rings.append((d.id, [tuple(p) for p in d.polygon]))
            meta[d.id] = d
            order.append(d.id)
            continue
        for i, g in enumerate(sorted(keep, key=lambda g: (-g.area, g.representative_point().x, g.representative_point().y))):
            did = d.id if i == 0 else "%s_%s" % (d.id, _SUFFIX[i - 1])
            nd = District(id=did, key=d.key if i == 0 else d.key + _SUFFIX[i - 1], kind=d.kind, polygon=[], area=0.0,
                          label=d.label, inside_wall=d.inside_wall, waterfront=d.waterfront)
            rings.append((did, _ring(g)))
            meta[did] = nd
            order.append(did)
    fring = _ring(F2)

    # roads
    hub = _hub(s)
    node_at = {n.id: n for n in s.graph_nodes}
    node_by_pos = {tuple(n.at): n.id for n in s.graph_nodes}
    roads_out: List[Road] = []
    cross: List[Tuple[Pt, Road, bool]] = []         # (point on the fort wall, piece, at piece start)
    F2ext = F2.exterior
    for r in s.roads:
        L = LineString(r.polyline)
        if L.intersection(F2).length < 0.01:
            roads_out.append(r)
            continue
        pieces = sorted(_lines(L.difference(F2)), key=lambda ln: L.project(ln.interpolate(0.5, True)))
        coords = [[_tup(c) for c in ln.coords] for ln in pieces]
        if len(coords) > 1 and tuple(r.polyline[0]) == tuple(r.polyline[-1]) and coords[0][0] == tuple(r.polyline[0]) \
                and coords[-1][-1] == tuple(r.polyline[0]):
            coords = [coords[-1] + coords[0][1:]] + coords[1:-1]
        at2nid = {tuple(node_at[n].at): n for n in r.nodes}
        kept = 0
        for pc in coords:
            pc = _dedupe(pc) if pc[0] != pc[-1] else pc
            if len(pc) < 2 or LineString(pc).length < 1.0:
                continue
            nodes: List[str] = []
            for v in pc:
                nid = at2nid.get(v)
                if nid and (not nodes or nodes[-1] != nid):
                    nodes.append(nid)
            if not nodes:
                continue
            rid = r.id if kept == 0 else "%s_%s" % (r.id, _SUFFIX[kept - 1])
            piece = Road(id=rid, cls=r.cls, polyline=[list(v) for v in pc], width=r.width, nodes=nodes)
            kept += 1
            roads_out.append(piece)
            for v, at_start in ((pc[0], True), (pc[-1], False)):
                if F2ext.distance(Point(v)) < 0.05 and v not in node_by_pos:
                    cross.append((v, piece, at_start))
    # an existing gate keeps its road: re-point it at the piece that now carries it
    by_id = {r.id: r for r in s.roads}
    for g in s.gates:
        if g.road in by_id and not any(p.id == g.road and LineString(p.polyline).distance(Point(g.at)) <= 0.5 for p in roads_out):
            near = [p for p in roads_out if p.id.startswith(g.road) and LineString(p.polyline).distance(Point(g.at)) <= 0.5]
            if near:
                g.road = near[0].id
    # fort centre, gates
    c = F2.centroid
    centre = _tup((c.x, c.y)) if F2.contains(c) else _tup((F2.representative_point().x, F2.representative_point().y))
    fort_id = "%s_%s" % (sid, base)
    wall_id = "%s_wall_%s" % (sid, base)
    road_id = "%s_%s_road" % (sid, base)
    gid_in, gid_out = "%s_gate_%s_in" % (sid, base), "%s_gate_%s_out" % (sid, base)
    h = math.sqrt(F2.area / 3.5)
    seen = set()
    cand = []
    for x in cross:
        if x[0] not in seen:
            seen.add(x[0])
            cand.append(x)
    cand.sort(key=lambda x: (math.hypot(x[0][0] - hub[0], x[0][1] - hub[1]), x[0]))
    extra: List[Road] = []

    def synth(point: Pt, tag: str) -> Tuple[Pt, Road, bool]:
        """A gate on the ring with no road crossing it: a straight link road to the nearest remaining node."""
        point = _tup(F2ext.interpolate(F2ext.project(Point(point))).coords[0])
        outside = [n for n in s.graph_nodes if not F2.contains(Point(n.at)) and tuple(n.at) != point]
        n = min(outside, key=lambda n: (math.hypot(n.at[0] - point[0], n.at[1] - point[1]), n.id))
        link = Road(id="%s_%s_link_%s" % (sid, base, tag), cls="artery", polyline=[list(point), list(n.at)], width=8.0, nodes=[n.id])
        extra.append(link)
        return (point, link, True)

    if cand:
        gin = cand[0]
        rest = [x for x in cand[1:] if math.hypot(x[0][0] - gin[0][0], x[0][1] - gin[0][1]) >= 0.5 * h and x[1] is not gin[1]] \
            or [x for x in cand[1:] if x[0] != gin[0]]
        gout = max(rest, key=lambda x: (math.hypot(x[0][0] - hub[0], x[0][1] - hub[1]), x[0])) if rest else None
    else:
        gin = synth(min(fring, key=lambda v: math.hypot(v[0] - hub[0], v[1] - hub[1])), "in")
        gout = None
    if gout is None:
        gout = synth(max(fring, key=lambda v: math.hypot(v[0] - gin[0][0], v[1] - gin[0][1])), "out")
    for (pt_, piece, at_start), gid in ((gin, gid_in), (gout, gid_out)):
        if at_start:
            piece.nodes.insert(0, gid)
        else:
            piece.nodes.append(gid)
    roads_out.extend(extra)
    roads_out.append(Road(id=road_id, cls="artery", polyline=[list(gin[0]), list(centre), list(gout[0])], width=12.0,
                          nodes=[gid_in, fort_id, gid_out]))

    def normal(p: Pt) -> List[float]:
        dx, dy = p[0] - centre[0], p[1] - centre[1]
        n = math.hypot(dx, dy) or 1.0
        return [r2(dx / n), r2(dy / n)]
    gates = [Gate(id=gid_in, wall=wall_id, at=list(gin[0]), normal=normal(gin[0]), road=road_id),
             Gate(id=gid_out, wall=wall_id, at=list(gout[0]), normal=normal(gout[0]), road=road_id)]

    # embed every new vertex into every ring that passes through it
    old = {tuple(p) for d in s.districts for p in d.polygon}
    new = sorted({v for _, rg in rings for v in rg} | set(fring) | {gin[0], gout[0]} - old)
    rings = [(i, _embed(rg, new)) for i, rg in rings]
    fring = _embed(fring, new)
    foot_ring: Optional[List[Pt]] = None
    if mode == "peninsula":
        U = unary_union([FP, F2])
        if U.geom_type != "Polygon" or U.interiors:
            raise _Reject("footprint union is not simple")
        foot_ring = _embed(_ring(U), new)
    new_foot = foot_ring if foot_ring is not None else _embed([tuple(p) for p in s.footprint], new)
    walls: List[Wall] = []
    for w in s.walls:
        if w.kind == "outer":
            old_pl = [tuple(p) for p in w.polyline[:-1]]
            pl = (foot_ring if foot_ring is not None else _embed(old_pl, new))
            if foot_ring is None and w.open_shore:
                # embedding only inserts vertices: carry each open-shore range over to the new vertex indices
                at_new = {p: i for i, p in enumerate(pl)}
                n_old = len(old_pl)
                w.open_shore = [[at_new[old_pl[a]], len(pl) if b >= n_old else at_new[old_pl[b]]] for a, b in w.open_shore]
            else:
                w.open_shore = []
            w.polyline = [list(p) for p in pl] + [list(pl[0])]
        else:
            pl = _embed([tuple(p) for p in w.polyline[:-1]], new)
            w.polyline = [list(p) for p in pl] + [list(pl[0])]
        walls.append(w)
    walls.append(Wall(id=wall_id, kind="citadel", polyline=[list(p) for p in fring] + [list(fring[0])], thickness=7.0,
                      towers=towers(fring)))

    # final districts
    districts: List[District] = []
    for did, rg in rings:
        poly = Polygon(rg)
        if not poly.is_valid or poly.area < minarea:
            raise _Reject("district %s invalid after the cut" % did)
        d = meta[did]
        d.polygon, d.area = [list(p) for p in rg], r2(poly.area)
        d.landmarks = []
        districts.append(d)
    if not Polygon(fring).is_valid:
        raise _Reject("fort polygon invalid after embedding")
    fd = District(id="%s_d_%s" % (sid, base), key=base, kind="citadel", polygon=[list(p) for p in fring], area=r2(Polygon(fring).area),
                  label="Fortress", inside_wall=True)
    districts.append(fd)
    landmarks: List[Landmark] = []
    for l in s.landmarks:
        p = Point(l.at)
        if F2.contains(p):
            continue
        home = next((d for d in districts[:-1] if d.id != fd.id and Polygon(d.polygon).distance(p) < 0.5), None)
        if home is None:
            continue
        l.district = home.id
        home.landmarks.append(l.id)
        landmarks.append(l)
    landmarks.append(Landmark(id=fort_id, kind="fortress", at=list(centre), r=r2(0.6 * h), district=fd.id, label="Fortress"))
    fd.landmarks.append(fort_id)

    s.districts, s.walls, s.landmarks = districts, walls, landmarks
    s.gates = list(s.gates) + gates
    s.footprint = [list(p) for p in new_foot]
    s.roads = roads_out
    _prune_nodes(s)
    _rebuild(s, [GraphNode(id=fort_id, kind="zone", at=list(centre))] +
             [GraphNode(id=g.id, kind="choke", at=list(g.at)) for g in gates])
    _repair_connectivity(s, sid, base, F2)
    _rebuild(s, [])
    errs = check(s)
    if errs:
        raise _Reject("; ".join(errs[:3]))
    return s


def _prune_nodes(s: Settlement) -> None:
    used = set()
    for r in s.roads:
        used.update(r.nodes)
    s.graph_nodes = [n for n in s.graph_nodes if n.id in used or any(g.id == n.id for g in s.gates)]


def _inner_candidates(s: Settlement, mode: str, rng, kind: str, h: float):
    FP = Polygon(s.footprint)
    c0 = FP.centroid
    R = math.sqrt(FP.area / math.pi)
    ring = [tuple(q) for q in s.footprint]
    for attempt in range(360):
        hh = h * (1.0 if attempt < 120 else 0.8 if attempt < 240 else 0.6)
        tips = rng.choice((5, 6, 7, 8))
        th_free = rng.uniform(0.0, math.pi)
        if mode in ("corner", "peninsula"):
            v = ring[rng.randrange(len(ring))]
            ux, uy = v[0] - c0.x, v[1] - c0.y
            n = math.hypot(ux, uy) or 1.0
            ux, uy = ux / n, uy / n
            th = math.atan2(uy, ux)
            back = 0.7 * hh if mode == "corner" else -0.3 * hh
            c = _tup((v[0] - ux * back, v[1] - uy * back))
        else:
            room = FP.buffer(-(1.2 * hh + 8.0))
            plazas = [(Point(l.at), l.r + 12.0 + 1.2 * hh) for l in s.landmarks if l.kind in ("square", "market")] + [
                (Point(g.at), 1.2 * hh + 40.0) for g in s.gates]
            b = FP.bounds
            c = None
            for _ in range(30):
                q = Point(rng.uniform(b[0], b[2]), rng.uniform(b[1], b[3]))
                if room.contains(q) and all(q.distance(pp) >= rr for pp, rr in plazas):
                    c = _tup((q.x, q.y))
                    break
            if c is None:
                continue
            th = th_free
        F = _shape(kind, c, hh, th, tips)
        if F is None:
            continue
        if mode == "corner":
            F = F.intersection(FP)
            parts = sorted(_polys(F), key=lambda g: -g.area)
            if not parts or parts[0].area < 0.5 * Polygon(_shape(kind, c, hh, th, tips)).area:
                continue
            F = Polygon([_tup(q) for q in parts[0].exterior.coords][:-1])
            if not F.is_valid:
                continue
        elif mode == "centre":
            if not FP.buffer(-8.0).contains(F):
                continue
        else:
            lo, hi = MARGIN, None
            b = F.bounds
            if b[0] < lo or b[1] < lo or b[2] > s.size[0] - lo or b[3] > s.size[1] - lo:
                continue
        if any(F.distance(Point(g.at)) < 10.0 for g in s.gates) or                 any(l.kind in ("square", "market") and F.distance(Point(l.at)) < l.r + 10.0 for l in s.landmarks):
            continue
        yield F


# --- detached forts ---------------------------------------------------------------------------------------------

def _add_detached(s0: Settlement, idx: int, F: Polygon, gate: Gate, towers_ring: bool = True) -> Settlement:
    s = copy.deepcopy(s0)
    sid = s.id
    base = "o%d" % idx
    ring = _ring(F)
    ext = Polygon(ring).exterior
    x = _tup(ext.interpolate(ext.project(Point(gate.at))).coords[0])
    ring = _embed(ring, [x])
    c = F.centroid
    centre = _tup((c.x, c.y))
    h = math.sqrt(F.area / 3.5)
    rid = "%s_apr_%s" % (sid, base)
    gid = "%s_gate_%s" % (sid, base)
    nid = "%s_%s" % (sid, base)
    wid = "%s_wall_%s" % (sid, base)
    dx, dy = x[0] - centre[0], x[1] - centre[1]
    n = math.hypot(dx, dy) or 1.0
    s.roads.append(Road(id=rid, cls="approach", polyline=[list(gate.at), list(x), list(centre)], width=10.0,
                        nodes=[gate.id, gid, nid]))
    s.gates.append(Gate(id=gid, wall=wid, at=list(x), normal=[r2(dx / n), r2(dy / n)], road=rid))
    s.walls.append(Wall(id=wid, kind="citadel", polyline=[list(p) for p in ring] + [list(ring[0])], thickness=7.0, towers=towers(ring)))
    d = District(id="%s_d_%s" % (sid, base), key=base, kind="citadel", polygon=[list(p) for p in ring], area=r2(Polygon(ring).area),
                 label="Outlying Fort %d" % idx, inside_wall=False)
    d.landmarks.append(nid)
    s.districts.append(d)
    s.landmarks.append(Landmark(id=nid, kind="fortress", at=list(centre), r=r2(0.6 * h), district=d.id, label="Outlying Fort %d" % idx))
    _rebuild(s, [GraphNode(id=nid, kind="zone", at=list(centre)), GraphNode(id=gid, kind="choke", at=list(x))])
    errs = check(s)
    if errs:
        raise _Reject("; ".join(errs[:3]))
    return s


def _detached(s: Settlement, idx: int, rng, kind: str, h: float, used: set) -> Optional[Settlement]:
    FP = Polygon(s.footprint)
    minarea = float(s.params.get("min_district_area", 1600.0))
    outer = _outer_wall(s)
    gates = [g for g in s.gates if g.wall == outer.id and g.id not in used]
    order = sorted(gates, key=lambda g: (rng.random(), g.id))
    others = [Polygon(d.polygon) for d in s.districts if not d.inside_wall]
    for g in order:
        for attempt in range(6):
            hh = h * (1.0 if attempt < 3 else 0.7)
            tips = rng.choice((5, 6, 7, 8))
            gap = rng.uniform(55.0, 110.0)
            th = math.atan2(g.normal[1], g.normal[0])
            c = _tup((g.at[0] + g.normal[0] * (gap + 0.8 * hh), g.at[1] + g.normal[1] * (gap + 0.8 * hh)))
            F = _shape(kind, c, hh, th, tips)
            if F is None or F.area < max(1500.0, 1.2 * minarea):
                continue
            b = F.bounds
            if b[0] < MARGIN or b[1] < MARGIN or b[2] > s.size[0] - MARGIN or b[3] > s.size[1] - MARGIN:
                continue
            if F.distance(FP) < 30.0 or any(F.distance(o) < 25.0 for o in others):
                continue
            if LineString([g.at, c]).intersection(FP).length > 2.0:
                continue
            try:
                out = _add_detached(s, idx, F, g)
            except (_Reject, ValueError):
                continue
            used.add(g.id)
            return out
    return None


# --- entry point ------------------------------------------------------------------------------------------------

def apply(s: Settlement, p: Dict[str, Any]) -> Settlement:
    """Add the fort(s) described by the resolved fort parameters `p`. Raises GenerationError when the main fort fits nowhere."""
    if not active(p):
        return s
    rng = stage_rng(s.id, s.generator, s.seed, "fort")
    kind = p["fort_shape"] if p["fort_shape"] != "auto" else ("star" if s.generator == "voronoi" else "rectangle")
    FP = Polygon(s.footprint)
    R = math.sqrt(FP.area / math.pi)
    minarea = float(s.params.get("min_district_area", 1600.0))
    h = max(p["fort_size"] * R, math.sqrt(max(3000.0, 1.3 * minarea) / 3.5))
    mode = p["fort"]
    reason = "no candidate"
    if mode in ("corner", "centre", "peninsula"):
        done = None
        for m in ([mode] + (["corner"] if mode == "centre" else [])):       # no interior site: fall back to the wall corner
            for F in _inner_candidates(s, m, rng, kind, h):
                try:
                    done = _carve_inner(s, F, m, "fort")
                    break
                except (_Reject, ValueError) as e:
                    reason = str(e)
            if done is not None:
                break
        if done is None:
            raise GenerationError("fort %s fits nowhere on this settlement (%s); try another seed, fort_size or mode" % (mode, reason))
        s = done
    used: set = set()
    n = 0
    if mode == "island":
        n += 1
        out = _detached(s, n, rng, kind, h, used)
        if out is None:
            raise GenerationError("island fort fits nowhere on this settlement (no free outer gate with room outside)")
        s = out
    ho = max(0.6 * h, math.sqrt(max(2200.0, 1.3 * minarea) / 3.5))
    for _ in range(p["fort_outposts"]):
        out = _detached(s, n + 1, rng, kind, ho, used)
        if out is not None:
            n += 1
            s = out
    s.params = dict(s.params)
    s.params.update(p)
    return s
