"""`grid` generator: a rotated lattice of streets clipped by a superellipse outline, a market square with radial
boulevards, an optional inner circular wall (design: mapgen/TOWN_SPEC.md section 7.3)."""
from __future__ import annotations

import bisect
import math
from typing import Any, Dict, List, Optional, Set, Tuple

from shapely.geometry import LineString, MultiLineString, MultiPoint, Point, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import polygonize, unary_union

from . import graph as G
from .gen_radial import GenerationError
from .model import District, Gate, GraphNode, Landmark, Road, Settlement, Wall, pt, r2
from .params import ParamDef, stage_rng

NAME = "grid"
OUTLINE_N = 120          # vertices of the outline before crossings are inserted
SQUARE_N = 8             # vertices of the market square polygon
INNER_N = 32             # vertices of the inner wall
SNAP_R = 3.0             # crossings closer than this to an existing vertex reuse it
MIN_PIECE = 10.0         # lattice pieces shorter than this (m) are dropped
MIN_GATE_SEP = 50.0      # preferred spacing between outer gates, m

DEFS = [
    ParamDef("extent_frac", "float", 0.3, 0.95, "diameter of the walled area as a fraction of the map's shorter side",
             draw_lo=0.4, draw_hi=0.7),
    ParamDef("aspect", "float", 1.0, 1.8, "outline length / width", draw_lo=1.0, draw_hi=1.4),
    ParamDef("roundness", "float", 2.0, 12.0, "superellipse exponent of the outline: 2 ellipse, above 8 nearly a rectangle",
             draw_lo=3.0, draw_hi=10.0),
    ParamDef("rotation", "float", 0.0, 360.0, "rotation of the whole layout, degrees"),
    ParamDef("block", "float", 40.0, 250.0, "lattice spacing along the lattice u axis, m", draw_lo=70.0, draw_hi=130.0),
    ParamDef("block_ratio", "float", 0.5, 2.0, "lattice spacing along v / spacing along u", draw_lo=0.7, draw_hi=1.4),
    ParamDef("spacing_jitter", "float", 0.0, 0.3, "per-street offset, fraction of the spacing", draw_lo=0.0, draw_hi=0.25),
    ParamDef("lattice_phase", "float", 0.0, 0.5, "offset of the lattice from the market square, fraction of the spacing",
             draw_lo=0.0, draw_hi=0.5),
    ParamDef("hub_offset", "float", 0.0, 0.4, "market square distance from the outline centre, fraction of the half size",
             draw_lo=0.0, draw_hi=0.2),
    ParamDef("plaza_frac", "float", 0.05, 0.3, "market square radius as a fraction of the half width (minimum 25 m)",
             draw_lo=0.08, draw_hi=0.14),
    ParamDef("boulevards", "int", 2, 8, "boulevards radiating from the market square", draw_lo=2, draw_hi=5),
    ParamDef("boulevard_twist", "float", 0.0, 45.0, "angle of the boulevards to the lattice axes, degrees (45 = diagonals)",
             draw_lo=0.0, draw_hi=45.0),
    ParamDef("boulevard_jitter", "float", 0.0, 0.3, "boulevard angle jitter, fraction of the angular spacing",
             draw_lo=0.0, draw_hi=0.15),
    ParamDef("gates", "int", 2, 12, "gates in the outer wall", draw_lo=3, draw_hi=6),
    ParamDef("inner_wall", "bool", 0, 1, "a circular inner wall around the market square, gated where streets cross it",
             draw_hi=0.4),
    ParamDef("inner_wall_frac", "float", 0.3, 0.8, "inner wall radius as a fraction of the market square's distance to the outline",
             draw_lo=0.4, draw_hi=0.65),
    ParamDef("citadel", "bool", 0, 1, "the district farthest from every gate becomes a citadel", draw_hi=0.4),
    ParamDef("min_district_area", "float", 100.0, 20000.0, "smallest allowed district, m2 (smaller faces merge into a neighbour)",
             default=1600.0),
    ParamDef("snap", "float", 1.0, 12.0, "crossings closer than this (m) to an existing vertex share it; raised when a lattice needs fitting",
             default=SNAP_R),
]


class _Refit(Exception):
    """This lattice does not fit; widen the blocks and retry. `drop` names a lattice road to leave out of the next try."""

    def __init__(self, msg: str, drop: Optional[str] = None) -> None:
        Exception.__init__(self, msg)
        self.drop = drop


def _num(v: float) -> str:
    return ("%g" % v)


def _sgn_key(i: int) -> str:
    return "n%d" % -i if i < 0 else "%d" % i


class _Net:
    """Vertex registry with snapping; every road, ring and district polygon is a sequence of registry vertices."""

    def __init__(self, snap: float = SNAP_R) -> None:
        self.v: List[List[float]] = []
        self.snap = snap

    def vid(self, x: float, y: float) -> int:
        for i, (vx, vy) in enumerate(self.v):
            if math.hypot(vx - x, vy - y) <= self.snap:
                return i
        self.v.append(pt(x, y))
        return len(self.v) - 1

    def exact(self, x: float, y: float) -> int:
        self.v.append(pt(x, y))
        return len(self.v) - 1


class _Ring:
    """A closed ring of registry vertices that later receives inserted crossing vertices."""

    def __init__(self, net: _Net, coords: List[Tuple[float, float]]) -> None:
        self.net = net
        self.ids = [net.exact(x, y) for x, y in coords]
        self.pending: List[Tuple[int, float, int]] = []     # (edge index, parameter, vertex id)

    def poly(self) -> Polygon:
        return Polygon([self.net.v[i] for i in self.ids])

    def add(self, vid: int) -> None:
        if vid in self.ids or any(p[2] == vid for p in self.pending):
            return
        p = self.net.v[vid]
        best = None
        n = len(self.ids)
        for i in range(n):
            a, b = self.net.v[self.ids[i]], self.net.v[self.ids[(i + 1) % n]]
            dx, dy = b[0] - a[0], b[1] - a[1]
            L2 = dx * dx + dy * dy
            t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2)) if L2 > 0 else 0.0
            d = math.hypot(a[0] + t * dx - p[0], a[1] + t * dy - p[1])
            if best is None or d < best[0] - 1e-9:
                best = (d, i, t)
        self.pending.append((best[1], best[2], vid))

    def finish(self) -> List[int]:
        out: List[int] = []
        by_edge: Dict[int, List[Tuple[float, int]]] = {}
        for e, t, vid in self.pending:
            by_edge.setdefault(e, []).append((t, vid))
        for i, vid in enumerate(self.ids):
            out.append(vid)
            for t, nv in sorted(by_edge.get(i, [])):
                if nv != out[-1] and nv != self.ids[(i + 1) % len(self.ids)]:
                    out.append(nv)
        self.ids, self.pending = out, []
        return out


class _Road:
    def __init__(self, rk: str, cls: str, width: float, line: LineString, fam: str) -> None:
        self.rk, self.cls, self.width, self.line, self.fam = rk, cls, width, line, fam
        self.pts: List[Tuple[float, int]] = []          # (parameter along the ideal line, vertex id)
        self.ids: List[int] = []

    def add(self, vid: int, at: Point) -> None:
        self.pts.append((self.line.project(at), vid))


def _points(geom) -> List[Point]:
    if geom.is_empty:
        return []
    if geom.geom_type == "Point":
        return [geom]
    if geom.geom_type in ("MultiPoint", "GeometryCollection"):
        return [g for g in geom.geoms if g.geom_type == "Point"]
    return []


def _lines(geom) -> List[LineString]:
    if geom.is_empty:
        return []
    if geom.geom_type == "LineString":
        return [geom]
    if geom.geom_type in ("MultiLineString", "GeometryCollection"):
        return [g for g in geom.geoms if g.geom_type == "LineString"]
    return []


def _build(p: Dict[str, Any], seed: int, size: Tuple[float, float], centre: Tuple[float, float], sid: str, skip: Set[str] = frozenset()) -> Settlement:
    net = _Net(float(p['snap']))
    rot = math.radians(p["rotation"])
    cr, sr = math.cos(rot), math.sin(rot)

    def to_map(u: float, v: float, o: Tuple[float, float]) -> Tuple[float, float]:
        return o[0] + u * cr - v * sr, o[1] + u * sr + v * cr

    # outline: superellipse in the layout frame, centred on the map centre, scaled to stay 10 m inside the map
    margin = min(centre[0], centre[1], size[0] - centre[0], size[1] - centre[1]) - 10.0
    R = p["extent_frac"] * min(size) / 2.0
    asp, n = float(p["aspect"]), float(p["roundness"])
    a, b = R * math.sqrt(asp), R / math.sqrt(asp)

    def outline(a: float, b: float) -> List[Tuple[float, float]]:
        out = []
        for k in range(OUTLINE_N):
            t = 2 * math.pi * k / OUTLINE_N
            c, s = math.cos(t), math.sin(t)
            r = (abs(c / a) ** n + abs(s / b) ** n) ** (-1.0 / n)
            out.append(to_map(r * c, r * s, centre))
        return out
    ring = outline(a, b)
    ext = max(max(abs(x - centre[0]), abs(y - centre[1])) for x, y in ring)
    if ext > margin:
        a, b = a * margin / ext, b * margin / ext
        ring = outline(a, b)
    if min(a, b) < 60.0:
        raise GenerationError("map %gx%g is too small for a settlement (half width %.1f m < 60 m)" % (size[0], size[1], min(a, b)))
    outer = _Ring(net, ring)
    F = outer.poly()
    if not F.is_valid:
        raise GenerationError("invalid outline")

    # hub (market square centre) and the lattice frame centred on it
    hrng = stage_rng(sid, NAME, seed, "hub")
    ang = hrng.uniform(0.0, 2 * math.pi)
    ho = float(p["hub_offset"])
    hub = to_map(ho * a * math.cos(ang), ho * b * math.sin(ang), centre)
    hub = (r2(hub[0]), r2(hub[1]))
    dmin = F.exterior.distance(Point(hub))
    rm = min(max(25.0, p["plaza_frac"] * min(a, b)), 0.4 * dmin)
    hub_v = net.exact(*hub)
    square = _Ring(net, [to_map(rm * math.cos(2 * math.pi * k / SQUARE_N), rm * math.sin(2 * math.pi * k / SQUARE_N), hub)
                         for k in range(SQUARE_N)])
    Q = square.poly()
    sq_corners = list(square.ids)
    inner: Optional[_Ring] = None
    Q2 = None
    ri = float(p["inner_wall_frac"]) * dmin
    if p["inner_wall"]:
        if ri >= rm + 30.0:
            inner = _Ring(net, [to_map(ri * math.cos(2 * math.pi * k / INNER_N), ri * math.sin(2 * math.pi * k / INNER_N), hub)
                                for k in range(INNER_N)])
            Q2 = inner.poly()
        else:
            p["inner_wall"] = False

    # lattice lines: u-lines run along v (constant u), v-lines run along u
    lrng = stage_rng(sid, NAME, seed, "lattice")
    reach = 2.0 * max(a, b) * 1.5

    def positions(spacing: float) -> List[float]:
        K = int(reach / spacing) + 2
        out = []
        for k in range(-K, K + 1):
            out.append((k, (k + p["lattice_phase"]) * spacing + lrng.uniform(-1, 1) * p["spacing_jitter"] * spacing))
        return out
    bu = float(p["block"])
    bv = bu * float(p["block_ratio"])
    ulines, vlines = positions(bu), positions(bv)

    # boulevards from the hub to the outline
    brng = stage_rng(sid, NAME, seed, "boulevards")
    nb = int(p["boulevards"])
    step = 360.0 / nb
    roads: List[_Road] = []
    for k in range(nb):
        deg = p["boulevard_twist"] + k * step + brng.uniform(-1, 1) * p["boulevard_jitter"] * step
        ux, uy = math.cos(math.radians(deg) + rot), math.sin(math.radians(deg) + rot)
        far = LineString([hub, (hub[0] + ux * 4 * (a + b), hub[1] + uy * 4 * (a + b))])
        cut = far.intersection(F.exterior)
        ends = _points(cut)
        if not ends:
            raise _Refit("boulevard misses the outline")
        e = max(ends, key=lambda q: q.distance(Point(hub)))
        roads.append(_Road("b%d" % k, "boulevard", 16.0, LineString([hub, (e.x, e.y)]), "b"))
    blv = [r for r in roads]

    def add_lattice(fam: str, lst: List[Tuple[int, float]]) -> None:
        for k, off in lst:
            if fam == "u":
                p0, p1 = to_map(off, -reach, hub), to_map(off, reach, hub)
            else:
                p0, p1 = to_map(-reach, off, hub), to_map(reach, off, hub)
            seg = LineString([p0, p1]).intersection(F)
            if seg.is_empty or seg.geom_type != "LineString" or seg.length < 2 * MIN_PIECE:
                continue
            seg = LineString(sorted(seg.coords, key=lambda c: LineString([p0, p1]).project(Point(c))))
            pieces = _lines(seg.difference(Q))
            pieces.sort(key=lambda l: LineString([p0, p1]).project(Point(l.coords[0])))
            key = "%s%s" % (fam, _sgn_key(k))
            for pi, pc in enumerate(pieces):
                if pc.length < MIN_PIECE:
                    continue
                if any(bl.line.buffer(2.0).intersection(pc).length > 0.5 * pc.length for bl in blv):
                    continue                                   # runs along a boulevard
                if len(pieces) == 1 and seg.distance(Q) > 0:
                    suffix = ""
                elif len(pieces) == 1:
                    suffix = "n" if LineString([p0, p1]).project(pc.interpolate(0.5, True)) < LineString([p0, p1]).project(Point(hub)) else "p"
                else:
                    suffix = "n" if pi == 0 else "p"
                if key + suffix in skip:
                    continue
                roads.append(_Road(key + suffix, "artery", 10.0, pc, fam + key[1:]))
    add_lattice("u", ulines)
    add_lattice("v", vlines)
    roads.sort(key=lambda r: r.rk)
    if len({r.rk for r in roads}) != len(roads):
        raise _Refit("duplicate road keys")

    # vertices: endpoints on the outline and the square, pairwise crossings, crossings of the rings
    for r in roads:
        c0, c1 = r.line.coords[0], r.line.coords[-1]
        if r.fam == "b":
            r.add(hub_v, Point(c0))
        else:
            if Q.exterior.distance(Point(c0)) < 0.5:
                v0 = net.vid(*c0)
                square.add(v0)
                r.add(v0, Point(c0))
            else:
                v0 = net.vid(*c0)
                outer.add(v0)
                r.add(v0, Point(c0))
        v1 = net.vid(*c1)
        if Q.exterior.distance(Point(c1)) < 0.5 and r.fam != "b":
            square.add(v1)
        else:
            outer.add(v1)
        r.add(v1, Point(c1))
    for i in range(len(roads)):
        for j in range(i + 1, len(roads)):
            ri_, rj = roads[i], roads[j]
            if ri_.fam == "b" and rj.fam == "b":
                continue
            ba, bb = ri_.line.bounds, rj.line.bounds
            if ba[0] > bb[2] + 1e-6 or bb[0] > ba[2] + 1e-6 or ba[1] > bb[3] + 1e-6 or bb[1] > ba[3] + 1e-6:
                continue
            for q in _points(ri_.line.intersection(rj.line)):
                v = net.vid(q.x, q.y)
                ri_.add(v, q)
                rj.add(v, q)
    for r in roads:
        for q in _points(r.line.intersection(Q.exterior)):
            v = net.vid(q.x, q.y)
            square.add(v)
            r.add(v, q)
    inner_x: Dict[int, str] = {}                          # vertex id -> road key that crosses the inner wall there
    if inner is not None:
        for r in roads:
            for q in _points(r.line.intersection(Q2.exterior)):
                v = net.vid(q.x, q.y)
                inner.add(v)
                r.add(v, q)
                inner_x.setdefault(v, r.rk)
    for r in roads:
        seq: List[int] = []
        for _, v in sorted(r.pts):
            if not seq or seq[-1] != v:
                seq.append(v)
        r.ids = seq
    square_ids = square.finish()
    outer_ids = outer.finish()
    inner_ids = inner.finish() if inner is not None else []

    # faces
    segs = []

    def add_path(ids: List[int], closed: bool) -> None:
        pts_ = [net.v[i] for i in ids] + ([net.v[ids[0]]] if closed else [])
        for k in range(len(pts_) - 1):
            if pts_[k] != pts_[k + 1]:
                segs.append(LineString([pts_[k], pts_[k + 1]]))
    add_path(outer_ids, True)
    add_path(square_ids, True)
    if inner_ids:
        add_path(inner_ids, True)
    sq_set = set(square_ids)
    for r in roads:
        ids = r.ids
        if r.fam == "b":
            first = next((k for k, v in enumerate(ids) if v in sq_set), None)
            if first is None:
                raise _Refit("boulevard never reaches the square")
            ids = ids[first:]
        add_path(ids, False)
    faces = [f for f in polygonize(unary_union(segs))]
    known = {tuple(v) for v in net.v}
    for f in faces:
        for c in f.exterior.coords:
            if (round(c[0], 2) + 0.0, round(c[1], 2) + 0.0) not in known:
                raise _Refit("unsnapped vertex in a face")
        if f.interiors:
            raise _Refit("face with a hole")
    ftot = sum(f.area for f in faces)
    if abs(ftot - Polygon([net.v[i] for i in outer_ids]).area) > 1.0:
        raise _Refit("faces do not tile the outline")
    plaza_face = next((f for f in faces if abs(f.area - Polygon([net.v[i] for i in square_ids]).area) < 1.0 and f.contains(Point(hub))), None)
    if plaza_face is None:
        raise _Refit("no market square face")

    # keys, then merge faces below the minimum area into a neighbour
    items = [[f, None] for f in faces]
    cut = hub

    def cell(f: Polygon) -> str:
        rp = f.representative_point()
        dx, dy = rp.x - cut[0], rp.y - cut[1]
        u, v = dx * cr + dy * sr, -dx * sr + dy * cr
        us = sorted(o for _, o in ulines)
        vs = sorted(o for _, o in vlines)
        i = bisect.bisect(us, u) - bisect.bisect(us, 0.0)
        j = bisect.bisect(vs, v) - bisect.bisect(vs, 0.0)
        return "x%sy%s" % (_sgn_key(i), _sgn_key(j))
    minarea = float(p["min_district_area"])
    while True:
        small = sorted((it for it in items if it[0] is not plaza_face and it[0].area < minarea), key=lambda it: it[0].area)
        if not small:
            break
        it = small[0]
        best = None
        for other in items:
            if other is it:
                continue
            bl = it[0].boundary.intersection(other[0].boundary).length
            if bl > G.BORDER_MIN:
                if Q2 is not None and it[0].boundary.intersection(other[0].boundary).intersection(Q2.exterior.buffer(0.05)).length > 0.5 * bl:
                    continue                                   # never merge across the inner wall
                score = (other[0] is not plaza_face, bl)
                if best is None or score > best[0]:
                    best = (score, other)
        if best is None:
            raise _Refit("isolated small face")
        merged = unary_union([it[0], best[1][0]])
        if merged.geom_type != "Polygon" or merged.interiors:
            raise _Refit("merge produced a non-simple district")
        if best[1][0] is plaza_face:
            plaza_face = merged
        best[1][0] = merged
        items.remove(it)
    allv = [tuple(v) for v in net.v]
    polys: List[Polygon] = []
    for f, _ in items:
        f = orient(f, 1.0)
        coords = [(round(c[0], 2) + 0.0, round(c[1], 2) + 0.0) for c in f.exterior.coords][:-1]
        polys.append(Polygon(coords))
    # every polygon carries every vertex that lies on its boundary (merging may have dropped collinear ones)
    for k, poly in enumerate(polys):
        ring_c = list(poly.exterior.coords)[:-1]
        have = set(ring_c)
        extra = [v for v in allv if v not in have and poly.exterior.distance(Point(v)) < 0.02]
        if extra:
            out = []
            for i, c in enumerate(ring_c):
                out.append(c)
                nxt = ring_c[(i + 1) % len(ring_c)]
                seg = LineString([c, nxt])
                on = sorted((seg.project(Point(v)), v) for v in extra if seg.distance(Point(v)) < 0.02)
                out.extend(v for _, v in on)
            polys[k] = Polygon(out)
    by_key: Dict[str, List[int]] = {}
    for k, (f, _) in enumerate(items):
        key = "plaza" if f is plaza_face else cell(f)
        by_key.setdefault(key, []).append(k)
    keyed: List[Tuple[str, Polygon]] = []
    for key in sorted(by_key):
        idxs = sorted(by_key[key], key=lambda k: (round(polys[k].representative_point().x, 1), round(polys[k].representative_point().y, 1)))
        for n_, k in enumerate(idxs):
            keyed.append((key if len(idxs) == 1 else key + "abcdefghijklmnopqrstuvwxyz"[n_], polys[k]))
    keyed.sort(key=lambda kp: (kp[0] != "plaza", kp[0]))
    if any(len(k) > 16 for k, _ in keyed):
        raise _Refit("district key too long")

    # districts
    districts: List[District] = []
    outer_line = LineString([net.v[i] for i in outer_ids] + [net.v[outer_ids[0]]])
    sq_poly = Polygon([net.v[i] for i in square_ids])
    for key, poly in keyed:
        if key == "plaza":
            kind = "core"
        elif poly.boundary.intersection(sq_poly.boundary).length > G.BORDER_MIN:
            kind = "market"
        elif poly.boundary.intersection(outer_line).length > G.BORDER_MIN:
            kind = "craft"
        else:
            kind = "residential"
        districts.append(District(id="%s_d_%s" % (sid, key), key=key, kind=kind, polygon=[list(c) for c in poly.exterior.coords][:-1],
                                  area=r2(poly.area), label="Market Square" if key == "plaza" else ""))
    plaza_id = districts[0].id
    if districts[0].key != "plaza":
        raise _Refit("no plaza district")

    # nodes: hub, chosen outer gates, inner gates, junctions
    members: Dict[int, List[str]] = {}
    for r in roads:
        for v in r.ids:
            members.setdefault(v, []).append(r.rk)
    for v in square_ids:
        members.setdefault(v, []).append("ring")
    # outer gate candidates: road ends on the outline
    cands: List[Tuple[str, str, int]] = []                 # (gate key, road key, vertex)
    outer_set = set(outer_ids)
    for r in roads:
        for end, side in ((r.ids[0], "n"), (r.ids[-1], "p")):
            if end in outer_set:
                cands.append(("gate_" + (r.rk if r.fam == "b" else r.rk.rstrip("np") + side), r.rk, end))
    cands.sort(key=lambda c: c[0])
    gkeys = {c[0] for c in cands}
    if len(gkeys) != len(cands):
        raise _Refit("gate keys collide")
    if len(cands) < 2:
        raise _Refit("fewer than two gate candidates")
    grng = stage_rng(sid, NAME, seed, "gates")
    want = min(int(p["gates"]), len(cands))
    chosen = [cands[grng.randrange(len(cands))]]
    bonus = lambda c: 1.5 if c[1].startswith("b") else 1.0
    while len(chosen) < want:
        best = None
        for c in cands:
            if c in chosen:
                continue
            d = min(math.hypot(net.v[c[2]][0] - net.v[o[2]][0], net.v[c[2]][1] - net.v[o[2]][1]) for o in chosen) * bonus(c)
            if best is None or d > best[0] + 1e-9:
                best = (d, c)
        chosen.append(best[1])
    chosen.sort()
    p["gates"] = len(chosen)
    node_of: Dict[int, str] = {hub_v: "%s_plaza" % sid}
    gate_of: Dict[int, Tuple[str, str, str]] = {}          # vertex -> (gate id, road key, wall id)
    wall_id = "%s_wall_outer" % sid
    for gk, rk, v in chosen:
        gid = "%s_%s" % (sid, gk)
        node_of[v] = gid
        gate_of[v] = (gid, rk, wall_id)
    inner_id = "%s_wall_inner" % sid
    if inner is not None:
        seen_per_road: Dict[str, int] = {}
        for v in sorted(inner_x, key=lambda v: (inner_x[v], v)):
            if v in node_of:
                continue
            rk = inner_x[v]
            gid = "%s_igate_%s" % (sid, rk)
            if gid in {x[0] for x in gate_of.values()}:
                seen_per_road[rk] = seen_per_road.get(rk, 1) + 1
                gid = "%s_igate_%s_%d" % (sid, rk, seen_per_road[rk])
            node_of[v] = gid
            gate_of[v] = (gid, rk, inner_id)
    for v in sorted(members):
        if v in node_of:
            continue
        rks = sorted(set(members[v]))
        if len(rks) >= 2:
            node_of[v] = "%s_j_%s_%s" % (sid, rks[0], rks[1])
    for gk, rk, v in cands:
        if v not in node_of:
            node_of[v] = "%s_%s" % (sid, "e" + gk[4:])
    if len(set(node_of.values())) != len(node_of):
        raise _Refit("node ids collide")

    nodes: Dict[str, GraphNode] = {}
    for v, nid in node_of.items():
        nodes[nid] = GraphNode(id=nid, kind="zone" if v == hub_v else "choke", at=list(net.v[v]))
    out_roads: List[Road] = []
    for r in roads:
        ids = [node_of[v] for v in r.ids if v in node_of]
        out_roads.append(Road(id="%s_%s" % (sid, "blvd_" + r.rk if r.fam == "b" else "av_" + r.rk), cls=r.cls,
                              polyline=[list(net.v[v]) for v in r.ids], width=r.width, nodes=ids))
    rk_id = {r.rk: "%s_%s" % (sid, "blvd_" + r.rk if r.fam == "b" else "av_" + r.rk) for r in roads}
    # ring road around the square
    ring_nodes = [v for v in square_ids if v in node_of]
    for corner, v in enumerate(sq_corners):
        if v not in node_of:
            node_of[v] = "%s_rv_%d" % (sid, corner)
            nodes[node_of[v]] = GraphNode(id=node_of[v], kind="choke", at=list(net.v[v]))
    ring_nodes = [v for v in square_ids if v in node_of]
    if len(ring_nodes) < 2:
        raise _Refit("square ring has fewer than two junctions")
    k0 = square_ids.index(ring_nodes[0])
    sq_rot = square_ids[k0:] + square_ids[:k0]
    out_roads.append(Road(id="%s_ring_1" % sid, cls="ring", polyline=[list(net.v[v]) for v in sq_rot] + [list(net.v[sq_rot[0]])],
                          width=10.0, nodes=[node_of[v] for v in sq_rot if v in node_of] + [node_of[sq_rot[0]]]))
    out_roads.sort(key=lambda r: r.id)

    # walls and gates
    gates: List[Gate] = []
    fpoly = Polygon([net.v[i] for i in outer_ids])
    for v, (gid, rk, wid) in sorted(gate_of.items(), key=lambda kv: kv[1][0]):
        at = list(net.v[v])
        if wid == wall_id:
            ring_i = outer_ids
            k = ring_i.index(v)
            a_, b_ = net.v[ring_i[k - 1]], net.v[ring_i[(k + 1) % len(ring_i)]]
            tx, ty = b_[0] - a_[0], b_[1] - a_[1]
            nx, ny = ty, -tx
        else:
            nx, ny = at[0] - hub[0], at[1] - hub[1]
        h = math.hypot(nx, ny) or 1.0
        gates.append(Gate(id=gid, wall=wid, at=at, normal=[r2(nx / h), r2(ny / h)], road=rk_id[rk]))
    walls = [Wall(id=wall_id, kind="outer", polyline=[list(net.v[i]) for i in outer_ids] + [list(net.v[outer_ids[0]])], thickness=8.0)]
    if inner is not None:
        walls.append(Wall(id=inner_id, kind="inner", polyline=[list(net.v[i]) for i in inner_ids] + [list(net.v[inner_ids[0]])],
                          thickness=6.0))

    # landmarks and labels
    landmarks = [Landmark(id="%s_plaza" % sid, kind="market", at=list(net.v[hub_v]), r=r2(rm), district=plaza_id, label="Market Square")]
    districts[0].landmarks.append("%s_plaza" % sid)
    if p["citadel"]:
        gpts = [Point(g.at) for g in gates if g.wall == wall_id]
        best = None
        for d in districts[1:]:
            rp = Polygon(d.polygon).representative_point()
            dist = min(rp.distance(q) for q in gpts)
            if best is None or dist > best[0] + 1e-9 or (abs(dist - best[0]) <= 1e-9 and d.key < best[1].key):
                best = (dist, d)
        d = best[1]
        d.kind = "citadel"
        rp = Polygon(d.polygon).representative_point()
        landmarks.append(Landmark(id="%s_citadel" % sid, kind="citadel", at=pt(rp.x, rp.y), r=45.0, district=d.id, label="Citadel"))
        d.landmarks.append("%s_citadel" % sid)
    names = {"market": "Market Ward", "craft": "Craft Quarter", "residential": "Residential Ward", "citadel": "Citadel Ward"}
    counters: Dict[str, int] = {}
    for d in districts[1:]:
        counters[d.kind] = counters.get(d.kind, 0) + 1
        d.label = "%s %d" % (names[d.kind], counters[d.kind])

    gnodes = [nodes[k] for k in sorted(nodes)]
    G.attach_districts(gnodes, districts)
    used: Dict[Tuple[str, str], str] = {}
    for rd in out_roads:
        for x, y in zip(rd.nodes, rd.nodes[1:]):
            if x == y:
                continue
            pair = (x, y) if x < y else (y, x)
            if pair in used:
                other = [q for q in (used[pair], rd.id) if "_av_" in q]
                raise _Refit("duplicate movement edge %s~%s" % pair, drop=other[0].split("_av_", 1)[1] if other else None)
            used[pair] = rd.id
    edges = G.movement_edges(out_roads, nodes)
    dn, de = G.district_graph(districts, out_roads, walls, gates)
    return Settlement(id=sid, generator=NAME, seed=seed, params=dict(p), size=(size[0], size[1]), centre=pt(centre[0], centre[1]),
                      footprint=[list(net.v[i]) for i in outer_ids], districts=districts, roads=out_roads, walls=walls,
                      gates=gates, landmarks=landmarks, graph_nodes=gnodes, graph_edges=edges, district_nodes=dn,
                      district_edges=de)


def generate(p: Dict[str, Any], seed: int, size: Tuple[float, float], centre: Tuple[float, float], sid: str) -> Settlement:
    """Build from resolved parameters. A lattice that does not fit drops the offending street (degenerate crossings), then
    widens the blocks and raises the snap distance, and tries again."""
    p = dict(p)
    last = "no attempt"
    for _ in range(10):
        skip: Set[str] = set()
        for _ in range(12):
            try:
                return _finish(p, seed, size, centre, sid, skip)
            except _Refit as e:
                last = str(e)
                if e.drop is None or e.drop in skip:
                    break
                skip.add(e.drop)
        p["block"] = min(250.0, round(p["block"] * 1.15, 2))
        p["snap"] = min(12.0, round(p["snap"] + 1.5, 2))
    raise GenerationError("no usable grid layout (%s); raise extent_frac or lower min_district_area" % last)


def _finish(p: Dict[str, Any], seed: int, size: Tuple[float, float], centre: Tuple[float, float], sid: str,
            skip: Set[str]) -> Settlement:
    q = dict(p)
    s = _build(q, seed, size, centre, sid, skip)
    p.update(q)          # parameters adjusted while building (inner wall dropped, gates clamped) are echoed
    return s
