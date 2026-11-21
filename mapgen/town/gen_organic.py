"""`organic` generator: a footprint from the shape-field contour, curved arteries and arcs, a perturbed plaza
(design: mapgen/TOWN_ORGANIC_SPEC.md sections 5 and 8). `edge=wall` rings the footprint with a wall and gates; `edge=open`
leaves it wall-less with entries (`Gate.kind == "entry"`, `wall == ""`)."""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import polygonize, unary_union

from .. import noise
from . import graph as G
from . import shape
from .gen_radial import GenerationError
from .model import District, Gate, GraphNode, Landmark, Road, Settlement, Wall, pt, r2
from .params import ParamDef, stage_rng
from .water import _catmull

NAME = "organic"
EDGES = ("wall", "open")
ATTEMPTS = 8
MIN_ANGLE = math.radians(38.0)      # smallest angle between two arteries at the hub
MIN_ARTERY = 60.0                   # m an artery must run inside the footprint
EDGE_DOC = "wall (outer wall, gates) | open (no wall, entries)"

DEFS = [
    ParamDef("radius_frac", "float", 0.3, 0.95, "base radius of the footprint as a fraction of the map's shorter half",
             draw_lo=0.4, draw_hi=0.65),
    ParamDef("irregularity", "float", 0.0, 0.5, "noise weight of the shape field (splotchiness)", draw_lo=0.15, draw_hi=0.4),
    ParamDef("lobes", "int", 0, 6, "satellite lobes of the footprint", draw_lo=0, draw_hi=3),
    ParamDef("ribbons", "int", 0, 4, "arteries that grow a tongue of footprint along them", draw_lo=0, draw_hi=2),
    ParamDef("arteries", "int", 3, 7, "curved arteries from the plaza to the footprint edge", draw_lo=3, draw_hi=5),
    ParamDef("curvature", "float", 0.0, 1.0, "lateral bend of the arteries", draw_lo=0.3, draw_hi=0.8),
    ParamDef("arcs", "int", 0, 3, "partial cross arcs between adjacent arteries", draw_lo=0, draw_hi=2),
    ParamDef("plaza_frac", "float", 0.05, 0.2, "plaza radius as a fraction of the base radius (at least 25 m)",
             draw_lo=0.07, draw_hi=0.12),
    ParamDef("hub_offset", "float", 0.0, 0.3, "hub distance from the map centre as a fraction of the base radius",
             draw_lo=0.0, draw_hi=0.15),
    ParamDef("min_solidity", "float", 0.4, 0.9, "smallest footprint area / convex hull area", default=0.62),
    ParamDef("min_district_area", "float", 100.0, 20000.0, "smallest allowed district, m2", default=1600.0),
    ParamDef("citadel", "bool", 0, 1, "the outer district farthest from the entries becomes a citadel", draw_hi=0.3),
]
SHAPE_KEYS = ("radius_frac", "irregularity", "lobes", "ribbons", "hub_offset", "min_solidity", "min_district_area")

Pt = Tuple[float, float]


class _Retry(Exception):
    """This attempt's layout fails a rule; redraw from the next stream."""


def _sfx(attempt: int) -> str:
    return "" if attempt == 0 else str(attempt)


def _insert(ring: List[Pt], p: Pt, closed: bool) -> None:
    """Insert `p` into the polyline/ring `ring` at the nearest segment unless it already is a vertex (ring: no repeated end)."""
    if p in ring:
        return
    n = len(ring)
    segs = n if closed else n - 1
    best, bi = 1e18, 0
    for i in range(segs):
        a, b = ring[i], ring[(i + 1) % n]
        dx, dy = b[0] - a[0], b[1] - a[1]
        L2 = dx * dx + dy * dy
        t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
        d = math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))
        if d < best:
            best, bi = d, i
    ring.insert(bi + 1, p)


def _seg_hit(a: Pt, b: Pt, ring: List[Pt]) -> Optional[Pt]:
    """First intersection point of segment a-b with the closed ring, nearest to `a`."""
    ln = LineString([a, b])
    x = ln.intersection(LineString(ring + [ring[0]]))
    if x.is_empty:
        return None
    pts = [g for g in getattr(x, "geoms", [x]) if g.geom_type == "Point"]
    if not pts:
        return None
    q = min(pts, key=lambda g: (g.x - a[0]) ** 2 + (g.y - a[1]) ** 2)
    return (q.x, q.y)


def _artery(hub: Pt, th: float, L: float, curvature: float, rng) -> List[Pt]:
    u, nrm = (math.cos(th), math.sin(th)), (-math.sin(th), math.cos(th))
    fr = [0.4, 0.75] if rng.random() < 0.5 else [0.55]
    ctrl: List[Pt] = [hub]
    for f in fr:
        lat = curvature * L / 3.0 * rng.uniform(-1, 1)
        ctrl.append((hub[0] + u[0] * L * f + nrm[0] * lat, hub[1] + u[1] * L * f + nrm[1] * lat))
    ctrl.append((hub[0] + u[0] * L, hub[1] + u[1] * L))
    return _catmull(ctrl, per=8)


def _clip(line: List[Pt], P: Polygon, plaza_ring: List[Pt], hub: Pt) -> Optional[List[Pt]]:
    """The part of `line` (starting at the hub) from the first vertex past the plaza to the first exit of P, starting with
    the hub itself; None when it is shorter than MIN_ARTERY inside P."""
    ring = [tuple(c) for c in P.exterior.coords[:-1]]
    pl = Polygon(plaza_ring)
    out: List[Pt] = [hub]
    for i in range(1, len(line)):
        a, b = line[i - 1], line[i]
        if P.contains(Point(b)):
            if pl.contains(Point(b)):
                continue
            out.append(b)
        else:
            x = _seg_hit(a, b, ring)
            if x is None:
                break
            out.append(x)
            break
    else:
        return None                     # never left the footprint: extend and retry in the caller
    if len(out) < 3 or LineString(out).length - 0.0 < MIN_ARTERY:
        return None
    return out


def _at_radius(line: List[Pt], hub: Pt, r: float) -> Optional[Tuple[Pt, int]]:
    """Point of the polyline at distance r from the hub (first crossing) and the index of the segment it lies on."""
    for i in range(1, len(line)):
        da = math.hypot(line[i - 1][0] - hub[0], line[i - 1][1] - hub[1])
        db = math.hypot(line[i][0] - hub[0], line[i][1] - hub[1])
        if da <= r <= db and db > da:
            t = (r - da) / (db - da)
            return ((line[i - 1][0] + t * (line[i][0] - line[i - 1][0]), line[i - 1][1] + t * (line[i][1] - line[i - 1][1])), i)
    return None


def _angle(p: Pt, hub: Pt) -> float:
    return math.atan2(p[1] - hub[1], p[0] - hub[0])


def _turn_ok(line: List[Pt], limit: float = 100.0) -> bool:
    for i in range(1, len(line) - 1):
        a = (line[i][0] - line[i - 1][0], line[i][1] - line[i - 1][1])
        b = (line[i + 1][0] - line[i][0], line[i + 1][1] - line[i][1])
        la, lb = math.hypot(*a), math.hypot(*b)
        if la < 1e-6 or lb < 1e-6:
            continue
        c = max(-1.0, min(1.0, (a[0] * b[0] + a[1] * b[1]) / (la * lb)))
        if math.degrees(math.acos(c)) > limit:
            return False
    return True


def _plaza(hub: Pt, rp: float, seed_n: int) -> List[Pt]:
    n = 24
    th = np.arange(n) * 2 * math.pi / n
    ns = noise.fbm1_periodic(th / (2 * math.pi), 3, seed_n, 2)
    return [(r2(hub[0] + rp * (1 + 0.09 * float(v)) * math.cos(a)), r2(hub[1] + rp * (1 + 0.09 * float(v)) * math.sin(a)))
            for a, v in zip(th, ns)]


def _layout(p: Dict[str, Any], seed: int, size: Tuple[float, float], sid: str, sh, attempt: int, n_arcs: int) -> Settlement:
    edge = p["edge"]
    hub = (r2(sh.hub[0]), r2(sh.hub[1]))
    R = sh.R
    P0 = Polygon(sh.ring)
    ring: List[Pt] = [tuple(q) for q in sh.ring]
    rp = max(25.0, p["plaza_frac"] * R)
    krng = stage_rng(sid, NAME, seed, "kinds" + _sfx(attempt))
    plaza = _plaza(hub, rp, krng.randrange(1 << 30))
    if not P0.buffer(-10.0).contains(Polygon(plaza)):
        raise _Retry("plaza does not fit the footprint")

    # arteries: ribbon axes first (they carry the footprint tongues), then free ones spread around the hub
    arng = stage_rng(sid, NAME, seed, "arteries" + _sfx(attempt))
    reach = max(math.hypot(q[0] - hub[0], q[1] - hub[1]) for q in ring) * 1.15 + 20.0
    lines: List[List[Pt]] = []
    angles: List[float] = []
    nart = int(p["arteries"])
    for ax in sh.axes[:nart]:
        ext = list(ax)
        d = (ext[-1][0] - ext[-2][0], ext[-1][1] - ext[-2][1])
        h = math.hypot(*d) or 1.0
        ext.append((ext[-1][0] + d[0] / h * reach, ext[-1][1] + d[1] / h * reach))
        ext[0] = hub
        lines.append(ext)
        angles.append(_angle(ax[min(len(ax) - 1, 4)], hub))
    base = arng.uniform(0, 2 * math.pi)
    spacing = 2 * math.pi / nart
    cand = [base + i * spacing + arng.uniform(-0.3, 0.3) * spacing for i in range(nart)]
    for th in cand:
        if len(lines) >= nart:
            break
        if all(abs(math.atan2(math.sin(th - a), math.cos(th - a))) >= MIN_ANGLE for a in angles):
            lines.append(_artery(hub, th, reach, p["curvature"], arng))
            angles.append(th)
    arteries: List[List[Pt]] = []
    for ln in lines:
        c = _clip(ln, P0, plaza, hub)
        if c is not None:
            arteries.append(c)
    if len(arteries) < 3:
        raise _Retry("fewer than 3 arteries reach the footprint")
    arteries.sort(key=lambda ln: _angle(ln[min(len(ln) - 1, 2)], hub))
    # snap every artery end to the ring and insert it there; round the polylines
    gates_at: List[Pt] = []
    pl = [tuple(q) for q in plaza]
    arts: List[List[Pt]] = []
    for ln in arteries:
        ln = [(r2(x), r2(y)) for x, y in ln]
        ded: List[Pt] = []
        for q in ln:
            if not ded or q != ded[-1]:
                ded.append(q)
        end = ded[-1]
        _insert(ring, end, True)
        gates_at.append(end)
        # plaza crossing
        for i in range(1, len(ded)):
            if Polygon(pl).contains(Point(ded[i])):
                continue
            x = _seg_hit(ded[i - 1], ded[i], pl)
            if x is not None:
                xr = (r2(x[0]), r2(x[1]))
                if xr not in ded:
                    ded.insert(i, xr)
                _insert(pl, xr, True)
            break
        arts.append(ded)
    if len({tuple(g) for g in gates_at}) != len(gates_at):
        raise _Retry("duplicate gate positions")

    # arcs between adjacent arteries
    crng = stage_rng(sid, NAME, seed, "arcs" + _sfx(attempt))
    n = len(arts)
    ang = [_angle(a[min(len(a) - 1, 3)], hub) for a in arts]
    pairs = [(i, (i + 1) % n) for i in range(n)]
    crng.shuffle(pairs)
    arcs: List[Tuple[int, int, List[Pt]]] = []
    inner = P0.buffer(-8.0)
    for i, j in pairs:
        if len(arcs) >= n_arcs:
            break
        gap = (ang[j] - ang[i]) % (2 * math.pi)
        r = R * crng.uniform(0.45, 0.7)
        amp, phase, k = crng.uniform(0.03, 0.11), crng.uniform(0, 2 * math.pi), crng.choice((1, 2))
        if gap > math.radians(150) or r < 2.2 * rp:
            continue
        ea, eb = _at_radius(arts[i], hub, r), _at_radius(arts[j], hub, r * crng.uniform(0.9, 1.1))
        if ea is None or eb is None:
            continue
        a0, a1 = _angle(ea[0], hub), _angle(eb[0], hub)
        gap = (a1 - a0) % (2 * math.pi)
        r0, r1 = math.hypot(ea[0][0] - hub[0], ea[0][1] - hub[1]), math.hypot(eb[0][0] - hub[0], eb[0][1] - hub[1])
        m = max(6, int(gap * (r0 + r1) / 2.0 / 16.0))
        pts_: List[Pt] = []
        for q in range(m + 1):
            t = q / float(m)
            rad = (r0 + (r1 - r0) * t) * (1.0 + amp * math.sin(math.pi * k * t + phase * (t > 0)) * math.sin(math.pi * t))
            a = a0 + gap * t
            pts_.append((r2(hub[0] + rad * math.cos(a)), r2(hub[1] + rad * math.sin(a))))
        pts_[0], pts_[-1] = (r2(ea[0][0]), r2(ea[0][1])), (r2(eb[0][0]), r2(eb[0][1]))
        dd: List[Pt] = []
        for q in pts_:
            if not dd or q != dd[-1]:
                dd.append(q)
        ln = LineString(dd)
        if not ln.is_simple or not inner.contains(ln):
            continue
        others = [LineString(a) for x, a in enumerate(arts) if x not in (i, j)] + [LineString(a[2]) for a in arcs]
        if any(ln.distance(o) < 3.0 for o in others):
            continue
        ok = True
        for x in (i, j):
            hit = ln.intersection(LineString(arts[x]))
            ps = [g for g in getattr(hit, "geoms", [hit]) if not g.is_empty]
            if len(ps) != 1 or ps[0].geom_type != "Point":
                ok = False
        if not ok or not _turn_ok(dd, 60.0):
            continue
        arcs.append((i, j, dd))
    arcs.sort(key=lambda t: (t[0], t[1]))
    for i, j, dd in arcs:                  # junctions become vertices of their arteries
        _insert(arts[i], dd[0], False)
        _insert(arts[j], dd[-1], False)
    return _assemble(p, seed, size, sid, sh, hub, rp, ring, pl, arts, arcs, gates_at, krng, edge)


def _assemble(p, seed, size, sid, sh, hub, rp, ring, pl, arts, arcs, gates_at, krng, edge) -> Settlement:
    P0 = Polygon(ring)
    line_geoms = [LineString(ring + [ring[0]]), LineString(pl + [pl[0]])] + [LineString(a[1:]) for a in arts] + \
        [LineString(a[2]) for a in arcs]
    faces: List[Polygon] = []
    for f in polygonize(unary_union(line_geoms)):
        if f.area < 0.5:
            continue
        if P0.buffer(0.01).contains(f.representative_point()):
            faces.append(orient(Polygon([(r2(x), r2(y)) for x, y in f.exterior.coords[:-1]]), 1.0))
    if abs(sum(f.area for f in faces) - P0.area) > 0.005 * P0.area:
        raise _Retry("faces do not tile the footprint")
    plaza_poly = Polygon(pl)
    plaza_face = None
    others: List[Polygon] = []
    for f in faces:
        if plaza_face is None and abs(f.area - plaza_poly.area) < 1.0 and f.representative_point().distance(Point(hub)) < rp:
            plaza_face = f
        else:
            others.append(f)
    if plaza_face is None:
        raise _Retry("no plaza face")
    n = len(arts)
    ang = [_angle(a[min(len(a) - 1, 3)], hub) for a in arts]

    def sector(f: Polygon) -> int:
        rpnt = f.representative_point()
        a = _angle((rpnt.x, rpnt.y), hub)
        return max(range(n), key=lambda k: (-((a - ang[k]) % (2 * math.pi)), k))   # artery just clockwise of the face
    groups: Dict[int, List[Polygon]] = {}
    for f in others:
        groups.setdefault(sector(f), []).append(f)
    districts: List[District] = []
    plaza_id = "%s_d_plaza" % sid
    districts.append(District(id=plaza_id, key="plaza", kind="core", polygon=[list(q) for q in plaza_face.exterior.coords[:-1]],
                              area=r2(plaza_face.area), label="Central Plaza"))
    hubp = Point(hub)
    for k in sorted(groups):
        fs = sorted(groups[k], key=lambda f: (round(f.representative_point().distance(hubp), 2), f.bounds))
        for m, f in enumerate(fs):
            key = "a%ds%d" % (k, m)
            districts.append(District(id="%s_d_%s" % (sid, key), key=key, kind="residential",
                                      polygon=[list(q) for q in f.exterior.coords[:-1]], area=r2(f.area), label=""))
    if any(d.area < p["min_district_area"] for d in districts):
        raise _Retry("a district is below min_district_area")
    # kinds
    Pp = {d.id: Polygon(d.polygon) for d in districts}
    pol_ring = LineString(ring + [ring[0]])
    plz = Pp[plaza_id]
    touch_ring: Dict[str, float] = {}
    for d in districts[1:]:
        if Pp[d.id].boundary.intersection(plz.boundary).length > 1.0:
            d.kind = "market"
        c = Pp[d.id].boundary.intersection(pol_ring).length
        if c > 1.0:
            touch_ring[d.id] = c
    if touch_ring:
        med = sorted(touch_ring.values())[len(touch_ring) // 2]
        for d in districts[1:]:
            if d.id in touch_ring and d.kind != "market" and touch_ring[d.id] >= med:
                d.kind = "craft"
    landmarks: List[Landmark] = [Landmark(id="%s_plaza" % sid, kind="square", at=pt(hub[0], hub[1]), r=r2(rp), district=plaza_id,
                                          label="Central Plaza")]
    districts[0].landmarks.append("%s_plaza" % sid)
    if p["citadel"]:
        cands = [d for d in districts[1:] if d.id in touch_ring and d.kind != "market"]
        if cands:
            best = max(cands, key=lambda d: (min(Pp[d.id].centroid.distance(Point(g)) for g in gates_at), d.id))
            best.kind = "citadel"
            rpnt = Pp[best.id].representative_point()
            landmarks.append(Landmark(id="%s_citadel" % sid, kind="citadel", at=pt(rpnt.x, rpnt.y), r=45.0, district=best.id,
                                      label="Citadel"))
            best.landmarks.append("%s_citadel" % sid)
    names = {"market": "Market Ward", "craft": "Craft Quarter", "residential": "Residential Ward", "citadel": "Citadel Ward"}
    counters: Dict[str, int] = {}
    for d in districts[1:]:
        counters[d.kind] = counters.get(d.kind, 0) + 1
        d.label = "%s %d" % (names[d.kind], counters[d.kind])

    # roads and movement nodes
    hubl = [r2(hub[0]), r2(hub[1])]
    cpt = hubl
    nodes: Dict[str, GraphNode] = {"%s_plaza" % sid: GraphNode(id="%s_plaza" % sid, kind="zone", at=cpt)}
    roads: List[Road] = []
    gates: List[Gate] = []
    gate_pref = "gate" if edge == "wall" else "entry"
    wall_id = "%s_wall_outer" % sid
    junc2: Dict[Tuple[int, Tuple[float, float]], str] = {}
    for m, (i, j, dd) in enumerate(arcs):
        junc2[(i, dd[0])] = "%s_j_a%d_c%d" % (sid, i, m)
        junc2[(j, dd[-1])] = "%s_j_a%d_c%d" % (sid, j, m)
    for k, a in enumerate(arts):
        rid = "%s_artery_%d" % (sid, k)
        gid = "%s_%s_a%d" % (sid, gate_pref, k)
        ids: List[str] = []
        for v in a:
            if v == hub or list(v) == hubl:
                ids.append("%s_plaza" % sid)
            elif (k, v) in junc2:
                nid = junc2[(k, v)]
                nodes[nid] = GraphNode(id=nid, kind="choke", at=[v[0], v[1]])
                ids.append(nid)
            elif v == a[-1]:
                nodes[gid] = GraphNode(id=gid, kind="choke", at=[v[0], v[1]])
                ids.append(gid)
        roads.append(Road(id=rid, cls="artery", polyline=[[x, y] for x, y in a], width=14.0, nodes=ids))
        tx, ty = a[-1][0] - a[-2][0], a[-1][1] - a[-2][1]
        h = math.hypot(tx, ty) or 1.0
        gates.append(Gate(id=gid, wall=wall_id if edge == "wall" else "", at=[a[-1][0], a[-1][1]],
                          normal=[r2(tx / h), r2(ty / h)], road=rid, kind="gate" if edge == "wall" else "entry"))
    for m, (i, j, dd) in enumerate(arcs):
        ids = [junc2[(i, dd[0])], junc2[(j, dd[-1])]]
        roads.append(Road(id="%s_arc_%d" % (sid, m), cls="ring", polyline=[[x, y] for x, y in dd], width=10.0, nodes=ids))
    walls: List[Wall] = []
    if edge == "wall":
        walls.append(Wall(id=wall_id, kind="outer", polyline=[[x, y] for x, y in ring] + [[ring[0][0], ring[0][1]]], thickness=8.0))
    gnodes = [nodes[k] for k in sorted(nodes)]
    G.attach_districts(gnodes, districts)
    roads.sort(key=lambda r: r.id)
    edges = G.movement_edges(roads, nodes)
    foot = [[x, y] for x, y in ring]
    dn, de = G.district_graph(districts, roads, walls, gates, foot)
    gates.sort(key=lambda g: g.id)
    return Settlement(id=sid, generator=NAME, seed=seed, params=dict(p), size=(size[0], size[1]), centre=cpt, footprint=foot,
                      districts=districts, roads=roads, walls=walls, gates=gates, landmarks=landmarks, graph_nodes=gnodes,
                      graph_edges=edges, district_nodes=dn, district_edges=de)


def generate(p: Dict[str, Any], seed: int, size: Tuple[float, float], centre: Tuple[float, float], sid: str) -> Settlement:
    """Build from resolved parameters (`edge` included). A layout failing a rule redraws from the next attempt stream
    (arcs are dropped one at a time first); after ATTEMPTS failures raises GenerationError."""
    sp = {k: p[k] for k in SHAPE_KEYS}
    sh = shape.footprint(size, seed, sid, sp, edge=p["edge"], generator=NAME)
    why: List[str] = []
    for attempt in range(ATTEMPTS):
        for n_arcs in range(int(p["arcs"]), -1, -1):
            try:
                s = _layout(p, seed, size, sid, sh, attempt, n_arcs)
            except (_Retry, ValueError) as e:
                why.append(str(e))
                continue
            from .check import check
            errs = check(s)
            if not errs:
                return s
            why.append(errs[0])
    raise GenerationError("organic: no acceptable layout in %d attempts (%s)" % (ATTEMPTS, "; ".join(why[-3:])))
