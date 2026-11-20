"""Water overlay: a river through the settlement and / or a bay on one side (design: mapgen/TOWN_SPEC.md section 7.5).

`apply(settlement, params)` post-processes any generator's output. A river is a smooth polyline buffered to a polygon; a
bay is a blob with a channel to the map edge, subtracted from the footprint. Districts are clipped to the dry land, roads
are cut at the banks and rejoined by `bridge` roads (bridge nodes, `bridge` movement edges and district-graph edges),
walls carry `open_shore` ranges where they meet water, a harbour district and a `docks` landmark mark the bay.
`water` in the JSON echoes the geometry. `water.islands` lists reserved island sites (the fort add-on's hook).
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

from . import graph as G
from .gen_radial import GenerationError
from .model import District, DistrictEdge, Gate, GraphNode, Landmark, Road, Settlement, Wall, polyline_length, r2
from .params import ParamError, stage_rng

MODES = ("none", "river", "bay", "both")
DOC = {
    "water": "none | river (a river through the settlement, bridges on the roads) | bay (coast on one side, harbour district, "
             "docks, wall open on the shore) | both (a river that runs into the bay)",
    "water_width": "river width in metres, 12-80 (default drawn 24-40)",
    "water_angle": "river heading, or the direction of the bay from the centre, degrees 0-360 (default drawn)",
    "water_bay_size": "bay radius as a fraction of the settlement radius, 0.2-0.7 (default drawn 0.35-0.5)",
    "water_island": "true: reserve an island site in the bay (`water.islands`, the fort add-on's hook; default false)",
}
ATTEMPTS = 100
MIN_PIECE = 600.0          # m2: a clipped district piece below this is dropped when it has no neighbour to merge into
NODE_GAP = 10.0           # m: a bank or bridge node closer than this to an existing node is rejected
OPEN_TOL = 0.06            # m: a wall edge this close to water is a shore edge
Pt = Tuple[float, float]


class _Reject(Exception):
    pass


def is_water_param(name: str) -> bool:
    return name == "water" or name.startswith("water_")


def resolve(overrides: Dict[str, Any]) -> Dict[str, Any]:
    """Water parameters; unset numeric ones are None (drawn at apply time). Unknown names and bad values are errors."""
    errors: List[str] = []
    out: Dict[str, Any] = {"water": "none", "water_width": None, "water_angle": None, "water_bay_size": None,
                           "water_island": False}
    ranges = {"water_width": (12.0, 80.0), "water_angle": (0.0, 360.0), "water_bay_size": (0.2, 0.7)}
    for k in sorted(overrides):
        v = overrides[k]
        if k not in out:
            errors.append("params.%s: unknown parameter (known: %s)" % (k, ", ".join(sorted(out))))
        elif k == "water":
            if v not in MODES:
                errors.append("params.water: %r is not one of %s" % (v, ", ".join(MODES)))
            else:
                out[k] = v
        elif k == "water_island":
            if isinstance(v, bool):
                out[k] = v
            elif isinstance(v, str) and v.lower() in ("true", "false"):
                out[k] = v.lower() == "true"
            else:
                errors.append("params.water_island: expected true or false (got %r)" % (v,))
        else:
            lo, hi = ranges[k]
            try:
                if isinstance(v, bool):
                    raise ValueError
                x = float(v)
                if not (lo <= x <= hi):
                    raise ValueError
                out[k] = x
            except (ValueError, TypeError):
                errors.append("params.%s: invalid value %r (%g-%g)" % (k, v, lo, hi))
    if errors:
        raise ParamError(errors)
    return out


def active(p: Dict[str, Any]) -> bool:
    return p["water"] != "none"


# --- geometry helpers -------------------------------------------------------------------------------------------

def _polys(g) -> List[Polygon]:
    if g is None or g.is_empty:
        return []
    if isinstance(g, Polygon):
        return [g]
    return [q for part in getattr(g, "geoms", []) for q in _polys(part)]


def _ring(poly: Polygon) -> List[List[float]]:
    """Exterior ring, counter-clockwise, rounded to 0.01 m, first point not repeated."""
    pts: List[List[float]] = []
    for x, y in list(orient(poly, 1.0).exterior.coords)[:-1]:
        p = [r2(x), r2(y)]
        if not pts or pts[-1] != p:
            pts.append(p)
    while len(pts) > 1 and pts[0] == pts[-1]:
        pts.pop()
    return pts


def _clean(poly: Polygon) -> Optional[Polygon]:
    """The polygon rebuilt from its rounded ring; the largest valid part, or None."""
    pts = _ring(poly)
    if len(pts) < 3:
        return None
    q = Polygon(pts)
    if not q.is_valid:
        parts = sorted(_polys(q.buffer(0)), key=lambda g: -g.area)
        if not parts:
            return None
        q = Polygon(_ring(parts[0]))
    return q if q.area > 0.5 else None


def water_union(s: Settlement):
    """Union of every water polygon of the settlement (rivers, bays), as a shapely geometry; empty when there is none."""
    rings = [r["polygon"] for r in s.water.get("rivers", [])] + [b["polygon"] for b in s.water.get("bays", [])]
    return unary_union([Polygon(r) for r in rings]) if rings else Polygon()


def wall_runs(w: Wall) -> List[List[List[float]]]:
    """The solid (walled) parts of a wall as polylines. No open shore: the closed ring itself. Otherwise each maximal run of
    edges outside the `open_shore` ranges, in ring order (a run that wraps past the ring start is joined)."""
    pts = w.polyline
    if not w.open_shore:
        return [pts]
    n = len(pts) - 1
    openf = [False] * n
    for a, b in w.open_shore:
        for i in range(a, b):
            openf[i % n] = True
    runs: List[List[List[float]]] = []
    cur: List[List[float]] = []
    for i in range(n):
        if openf[i]:
            if cur:
                runs.append(cur)
                cur = []
        else:
            if not cur:
                cur = [pts[i]]
            cur.append(pts[i + 1])
    if cur:
        if runs and not openf[0] and runs[0][0] == cur[-1]:
            runs[0] = cur + runs[0][1:]
        else:
            runs.append(cur)
    return runs


def _split_line(pts: List[Pt], W) -> Tuple[List[Pt], List[bool]]:
    """Insert the points where the polyline crosses the boundary of W; flags[i] is True when edge i lies in W (its midpoint
    is inside, or for `shore` use by the caller a distance test is applied separately)."""
    out: List[Pt] = [pts[0]]
    flags: List[bool] = []
    bnd = W.boundary
    for a, b in zip(pts, pts[1:]):
        seg = LineString([a, b])
        cuts: List[Pt] = []
        if seg.intersects(bnd):
            x = seg.intersection(bnd)
            geoms = list(getattr(x, "geoms", [x]))
            for g in geoms:
                coords = [g.coords[0]] if g.geom_type == "Point" else [g.coords[0], g.coords[-1]]
                for c in coords:
                    q = (r2(c[0]), r2(c[1]))
                    if q != a and q != b and q not in cuts:
                        cuts.append(q)
        cuts.sort(key=lambda q: (q[0] - a[0]) ** 2 + (q[1] - a[1]) ** 2)
        chain = [a] + cuts + [b]
        for p, q in zip(chain, chain[1:]):
            if p == q:
                continue
            out.append(q)
            flags.append(p)   # placeholder, replaced below
    # recompute flags from the final vertices
    res: List[bool] = []
    for p, q in zip(out, out[1:]):
        res.append(W.contains(Point((p[0] + q[0]) / 2.0, (p[1] + q[1]) / 2.0)))
    return out, res


def _mark_wall(w: Wall, W, shore: bool) -> Wall:
    """Split the wall ring at the water boundary and flag the edges in or on the water as open shore (`shore`: edges lying on
    the boundary count, for rings that follow a coast)."""
    pts = [tuple(p) for p in w.polyline]
    chain, wet = _split_line(pts, W)
    flags: List[bool] = []
    for (p, q), wf in zip(zip(chain, chain[1:]), wet):
        mid = Point((p[0] + q[0]) / 2.0, (p[1] + q[1]) / 2.0)
        flags.append(bool(wf or (shore and W.distance(mid) < OPEN_TOL)))
    n = len(chain) - 1
    if not any(flags):
        return w
    # rotate so the ring starts at a dry vertex: the smallest (x, y) vertex with both adjacent edges solid
    dry = [i for i in range(n) if not flags[i] and not flags[i - 1]]
    if not dry:
        raise _Reject("wall %s lies wholly in water" % w.id)
    st = min(dry, key=lambda i: chain[i])
    verts = chain[st:n] + chain[:st]
    fl = flags[st:] + flags[:st]
    ring = [list(v) for v in verts] + [list(verts[0])]
    ranges: List[List[int]] = []
    i = 0
    while i < n:
        if fl[i]:
            j = i
            while j < n and fl[j]:
                j += 1
            ranges.append([i, j % n if j < n else n])
            i = j
        else:
            i += 1
    ranges = [[a, b] for a, b in ranges]
    return Wall(id=w.id, kind=w.kind, polyline=ring, thickness=w.thickness, open_shore=ranges, towers=list(w.towers))


# --- water bodies -----------------------------------------------------------------------------------------------

def _catmull(ctrl: List[Pt], per: int = 10) -> List[Pt]:
    pts = [ctrl[0]] + ctrl + [ctrl[-1]]
    out: List[Pt] = []
    for i in range(1, len(pts) - 2):
        p0, p1, p2, p3 = pts[i - 1], pts[i], pts[i + 1], pts[i + 2]
        for k in range(per):
            t = k / float(per)
            t2, t3 = t * t, t * t * t
            out.append(tuple(0.5 * ((2 * p1[j]) + (-p0[j] + p2[j]) * t + (2 * p0[j] - 5 * p1[j] + 4 * p2[j] - p3[j]) * t2
                                    + (-p0[j] + 3 * p1[j] - 3 * p2[j] + p3[j]) * t3) for j in range(2)))
    out.append(ctrl[-1])
    return out


def _river(s: Settlement, hub: Pt, R: float, width: float, ang: float, rng, toward: Optional[Pt]):
    """(polyline, polygon) of a river heading `ang` (radians) past the hub at a seeded lateral offset; with `toward` it ends
    at that point (the bay)."""
    u = (math.cos(ang), math.sin(ang))
    n = (-u[1], u[0])
    lo = width / 2.0 + 0.16 * R
    off = rng.choice((-1.0, 1.0)) * rng.uniform(lo, lo + 0.55 * R)
    amp = rng.uniform(0.04, 0.1) * R
    ph = rng.uniform(0, 2 * math.pi)
    t0 = -1.7 * R
    t1 = 1.7 * R
    ctrl: List[Pt] = []
    k = 7
    for i in range(k):
        t = t0 + (t1 - t0) * i / (k - 1.0)
        lat = off + amp * math.sin(ph + i * 1.35) * (1.0 if 0 < i < k - 1 else 0.4)
        ctrl.append((hub[0] + u[0] * t + n[0] * lat, hub[1] + u[1] * t + n[1] * lat))
    if toward is not None:
        # bend the downstream half to finish at the bay
        keep = [c for c in ctrl if (c[0] - hub[0]) * u[0] + (c[1] - hub[1]) * u[1] < 0.2 * R]
        ctrl = keep + [toward]
    mapbox = box(0.0, 0.0, s.size[0], s.size[1])
    line = LineString(_catmull(ctrl))
    clipped = line.intersection(mapbox)
    parts = [g for g in getattr(clipped, "geoms", [clipped]) if g.geom_type == "LineString" and g.length > 1.0]
    if not parts:
        raise _Reject("river leaves the map")
    line = max(parts, key=lambda g: g.length)
    poly = line.buffer(width / 2.0, cap_style=2, join_style=1, resolution=6).intersection(mapbox)
    ps = _polys(poly)
    if not ps:
        raise _Reject("empty river")
    return [[r2(x), r2(y)] for x, y in line.coords], max(ps, key=lambda g: g.area)


def _ray_exit(poly: Polygon, o: Pt, u: Pt) -> Optional[Pt]:
    far = LineString([o, (o[0] + u[0] * 10 * math.sqrt(poly.area), o[1] + u[1] * 10 * math.sqrt(poly.area))])
    x = poly.exterior.intersection(far)
    pts = [g for g in getattr(x, "geoms", [x]) if g.geom_type == "Point"]
    if not pts:
        return None
    q = max(pts, key=lambda g: (g.x - o[0]) * u[0] + (g.y - o[1]) * u[1])
    return (q.x, q.y)


def _bay(s: Settlement, foot: Polygon, hub: Pt, R: float, ang: float, size: float, rng):
    """(polygon, centre, unit direction) of a bay biting into the footprint at heading `ang`, with a channel to the map edge."""
    u = (math.cos(ang), math.sin(ang))
    q = _ray_exit(foot, hub, u)
    if q is None:
        raise _Reject("no footprint exit")
    rb = size * R
    cb = (q[0] + u[0] * 0.05 * R, q[1] + u[1] * 0.05 * R)
    a2, a3 = rng.uniform(0, 6.28), rng.uniform(0, 6.28)
    e2, e3 = rng.uniform(0.08, 0.2), rng.uniform(0.04, 0.12)
    blob = []
    for i in range(64):
        f = 2 * math.pi * i / 64.0
        r = rb * (1.0 + e2 * math.sin(2 * f + a2) + e3 * math.sin(3 * f + a3))
        blob.append((cb[0] + r * math.cos(f), cb[1] + r * math.sin(f)))
    chan = LineString([cb, (cb[0] + u[0] * 6 * R, cb[1] + u[1] * 6 * R)]).buffer(rb * 0.85, resolution=6)
    mapbox = box(0.0, 0.0, s.size[0], s.size[1])
    poly = unary_union([Polygon(blob), chan]).intersection(mapbox)
    ps = _polys(poly)
    if not ps:
        raise _Reject("empty bay")
    return max(ps, key=lambda g: g.area), cb, u, rb


# --- the rebuild ------------------------------------------------------------------------------------------------

def _clip_districts(s: Settlement, W, minarea: float) -> List[District]:
    pieces: List[Dict[str, Any]] = []
    for d in s.districts:
        poly = Polygon(d.polygon)
        if poly.intersection(W).area < 0.01:
            pieces.append({"d": d, "poly": poly, "orig": True, "id": d.id, "key": d.key, "label": d.label})
            continue
        parts = [g for g in _polys(poly.difference(W)) if g.area > 0.5]
        parts.sort(key=lambda g: (round(g.centroid.x, 1), round(g.centroid.y, 1)))
        for i, g in enumerate(parts):
            suf = "" if len(parts) == 1 else "abcdefgh"[i % 8]
            c = _clean(g)
            if c is None:
                continue
            pieces.append({"d": d, "poly": c, "orig": False, "id": d.id + suf, "key": d.key + suf,
                           "label": d.label if not suf else "%s %s" % (d.label, suf.upper())})
    while True:
        small = [p for p in pieces if p["poly"].area < minarea]
        if not small:
            break
        p = min(small, key=lambda x: (x["poly"].area, x["id"]))
        cands = []
        for q in pieces:
            if q is p:
                continue
            if not p["poly"].envelope.intersects(q["poly"].envelope):
                continue
            ln = p["poly"].boundary.intersection(q["poly"].boundary).length
            if ln > 1.0:
                cands.append((q["d"].kind == "citadel", -ln, q["id"], q))
        cands.sort(key=lambda c: c[:3])
        merged = False
        for _, _, _, q in cands:
            u = unary_union([p["poly"], q["poly"]])
            if isinstance(u, Polygon) and u.is_valid:
                c = _clean(u)
                if c is not None:
                    q["poly"] = c
                    q["orig"] = False
                    merged = True
                    break
        pieces.remove(p)
    out: List[District] = []
    for p in pieces:
        d = p["d"]
        if p["orig"]:
            out.append(d)
        else:
            ring = _ring(p["poly"])
            out.append(District(id=p["id"], key=p["key"], kind=d.kind, polygon=ring, area=r2(Polygon(ring).area),
                                label=p["label"], inside_wall=d.inside_wall, waterfront=d.waterfront, landmarks=[]))
    return out


class _Ctx:
    def __init__(self, s: Settlement):
        self.sid = s.id
        self.nodes = {n.id: n for n in s.graph_nodes}
        self.new: Dict[str, GraphNode] = {}
        self.bridges: List[Road] = []
        self.banks = 0
        self.lost: Dict[str, List[float]] = {}     # gate id -> shore point where its road now ends
        self.roads: List[Road] = []


def _cut_roads(s: Settlement, W, ctx: _Ctx, maxbridge: float) -> None:
    gate_ids = {g.id for g in s.gates}
    for r in sorted(s.roads, key=lambda r: r.id):
        pts = [tuple(p) for p in r.polyline]
        coord_node = {tuple(ctx.nodes[n].at): n for n in r.nodes}
        chain, wet = _split_line(pts, W)
        if not any(wet):
            ctx.roads.append(r)
            continue
        if all(wet):
            continue
        if chain[0] == chain[-1] and len(chain) > 3:
            n = len(chain) - 1
            i = next(i for i in range(n) if wet[i] and not wet[i - 1])
            chain = chain[i:n] + chain[:i] + [chain[i]]
            wet = wet[i:] + wet[:i]
        runs: List[Tuple[bool, int, int]] = []        # (wet, first vertex, last vertex)
        i = 0
        while i < len(wet):
            j = i
            while j < len(wet) and wet[j] == wet[i]:
                j += 1
            runs.append((wet[i], i, j))
            i = j
        # nodes per run end
        starts: Dict[int, str] = {}
        ends: Dict[int, str] = {}
        for k, (w, a, b) in enumerate(runs):
            if w:
                continue
            verts = chain[a:b + 1]
            # start node
            if verts[0] in coord_node:
                starts[k] = coord_node[verts[0]]
            if verts[-1] in coord_node:
                ends[k] = coord_node[verts[-1]]
        # bridges / banks across each wet run
        for k, (w, a, b) in enumerate(runs):
            if not w:
                continue
            wv = chain[a:b + 1]
            inner_nodes = [coord_node[v] for v in wv if v in coord_node]
            before = k - 1 if k - 1 >= 0 else None
            after = k + 1 if k + 1 < len(runs) else None
            blen = polyline_length([list(v) for v in wv])
            if before is not None and after is not None and blen <= maxbridge:
                if any(nid in gate_ids for nid in inner_nodes):
                    raise _Reject("gate in the river")
                kk = len(ctx.bridges) + 1
                na, nb = "%s_bridge_%d_a" % (ctx.sid, kk), "%s_bridge_%d_b" % (ctx.sid, kk)
                ends[before] = na
                starts[after] = nb
                ctx.new[na] = GraphNode(id=na, kind="choke", at=[r2(wv[0][0]), r2(wv[0][1])])
                ctx.new[nb] = GraphNode(id=nb, kind="choke", at=[r2(wv[-1][0]), r2(wv[-1][1])])
                ctx.bridges.append(Road(id="%s_bridge_%d" % (ctx.sid, kk), cls="bridge", polyline=[[r2(x), r2(y)] for x, y in wv],
                                        width=r.width, nodes=[na, nb]))
            else:
                gids0 = [nid for nid in inner_nodes if nid in gate_ids]
                if gids0 and before is not None and after is not None:
                    raise _Reject("gate in the river")
                for side, kidx in (("end", before), ("start", after)):
                    if kidx is None:
                        continue
                    pt = wv[0] if side == "end" else wv[-1]
                    gids = [nid for nid in inner_nodes if nid in gate_ids]
                    if gids:
                        nid = gids[0]
                        ctx.lost[nid] = [r2(pt[0]), r2(pt[1])]
                    else:
                        ctx.banks += 1
                        nid = "%s_bank_%d" % (ctx.sid, ctx.banks)
                    ctx.new[nid] = GraphNode(id=nid, kind="choke", at=[r2(pt[0]), r2(pt[1])])
                    (ends if side == "end" else starts)[kidx] = nid
        piece = 0
        for k, (w, a, b) in enumerate(runs):
            if w:
                continue
            verts = chain[a:b + 1]
            if polyline_length([list(v) for v in verts]) < 0.5:
                continue
            nodes: List[str] = []
            if k in starts:
                nodes.append(starts[k])
            for v in verts[1:-1]:
                if v in coord_node:
                    nodes.append(coord_node[v])
            if k in ends:
                nodes.append(ends[k])
            seen = set()
            nodes = [x for x in nodes if not (x in seen or seen.add(x))]
            piece += 1
            rid = r.id if piece == 1 else "%s_s%d" % (r.id, piece)
            ctx.roads.append(Road(id=rid, cls=r.cls, polyline=[[r2(x), r2(y)] for x, y in verts], width=r.width, nodes=nodes))


def _components(roads: List[Road]) -> List[set]:
    adj: Dict[str, set] = {}
    for r in roads:
        for a, b in zip(r.nodes, r.nodes[1:]):
            adj.setdefault(a, set()).add(b)
            adj.setdefault(b, set()).add(a)
        for n in r.nodes:
            adj.setdefault(n, set())
    seen: set = set()
    comps: List[set] = []
    for n in sorted(adj):
        if n in seen:
            continue
        comp = {n}
        todo = [n]
        while todo:
            for m in adj[todo.pop()]:
                if m not in comp:
                    comp.add(m)
                    todo.append(m)
        seen |= comp
        comps.append(comp)
    return comps


def _link_components(s: Settlement, W, ctx: _Ctx, maxbridge: float) -> None:
    """Roads that never cross the water leave the banks unjoined: add an `artery` link, a bridge, an artery link between the
    nearest node pair of two components whose straight segment crosses the water once."""
    nodes = dict(ctx.nodes)
    nodes.update(ctx.new)
    for _ in range(4):
        comps = _components(ctx.roads + ctx.bridges)
        if len(comps) < 2:
            return
        best = None
        for i in range(len(comps)):
            for j in range(i + 1, len(comps)):
                for a in sorted(comps[i]):
                    for b in sorted(comps[j]):
                        pa, pb = nodes[a].at, nodes[b].at
                        ln = LineString([pa, pb])
                        x = ln.intersection(W)
                        if x.is_empty or x.geom_type != "LineString" or x.length > maxbridge:
                            continue
                        if best is None or ln.length < best[0]:
                            best = (ln.length, a, b, x)
        if best is None:
            return
        _, a, b, x = best
        c0, c1 = x.coords[0], x.coords[-1]
        pa = nodes[a].at
        if (c0[0] - pa[0]) ** 2 + (c0[1] - pa[1]) ** 2 > (c1[0] - pa[0]) ** 2 + (c1[1] - pa[1]) ** 2:
            c0, c1 = c1, c0
        kk = len(ctx.bridges) + 1
        na, nb = "%s_bridge_%d_a" % (ctx.sid, kk), "%s_bridge_%d_b" % (ctx.sid, kk)
        q0, q1 = [r2(c0[0]), r2(c0[1])], [r2(c1[0]), r2(c1[1])]
        ctx.new[na] = GraphNode(id=na, kind="choke", at=q0)
        ctx.new[nb] = GraphNode(id=nb, kind="choke", at=q1)
        nodes[na], nodes[nb] = ctx.new[na], ctx.new[nb]
        ctx.bridges.append(Road(id="%s_bridge_%d" % (ctx.sid, kk), cls="bridge", polyline=[q0, q1], width=8.0, nodes=[na, nb]))
        ctx.roads.append(Road(id="%s_link_%da" % (ctx.sid, kk), cls="artery", polyline=[list(nodes[a].at), q0], width=8.0, nodes=[a, na]))
        ctx.roads.append(Road(id="%s_link_%db" % (ctx.sid, kk), cls="artery", polyline=[q1, list(nodes[b].at)], width=8.0, nodes=[nb, b]))


def _link_banks(s: Settlement, W, ctx: _Ctx, maxbridge: float) -> None:
    """A bank with no road across (and no gate) is cut off: join the district cluster to the nearest node over the water."""
    nodes = dict(ctx.nodes)
    nodes.update(ctx.new)
    polys = [(d.id, Polygon(d.polygon), d) for d in s.districts]
    gate_pts = [Point(g.at) for g in s.gates]
    n = len(polys)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i in range(n):
        for j in range(i + 1, n):
            if polys[i][1].envelope.intersects(polys[j][1].envelope) and polys[i][1].boundary.intersection(
                    polys[j][1].boundary.buffer(0.2)).length > 1.0:
                parent[find(i)] = find(j)
    groups: Dict[int, List[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    used = {nid for r in ctx.roads + ctx.bridges for nid in r.nodes}
    for _, members in sorted(groups.items(), key=lambda kv: min(polys[i][0] for i in kv[1])):
        if any(polys[i][1].distance(g) <= G.SNAP for i in members for g in gate_pts):
            continue
        if any(polys[i][1].distance(Point(nodes[nid].at)) <= G.SNAP for i in members for nid in used if nid in nodes
               and nodes[nid].kind != "zone" and nid not in ctx.new):
            continue
        big = max(members, key=lambda i: (polys[i][1].area, polys[i][0]))
        anchor = polys[big][1].representative_point()
        best = None
        for nid in sorted(used):
            if nid not in nodes or nid in ctx.lost:
                continue
            pa = nodes[nid].at
            ln = LineString([pa, (anchor.x, anchor.y)])
            x = ln.intersection(W)
            if x.is_empty or x.geom_type != "LineString" or x.length > maxbridge:
                continue
            if best is None or ln.length < best[0]:
                best = (ln.length, nid, x)
        if best is None:
            continue
        _, a, x = best
        c0, c1 = x.coords[0], x.coords[-1]
        pa = nodes[a].at
        if (c0[0] - pa[0]) ** 2 + (c0[1] - pa[1]) ** 2 > (c1[0] - pa[0]) ** 2 + (c1[1] - pa[1]) ** 2:
            c0, c1 = c1, c0
        kk = len(ctx.bridges) + 1
        na, nb, nc = "%s_bridge_%d_a" % (ctx.sid, kk), "%s_bridge_%d_b" % (ctx.sid, kk), "%s_link_%d" % (ctx.sid, kk)
        q0, q1, qa = [r2(c0[0]), r2(c0[1])], [r2(c1[0]), r2(c1[1])], [r2(anchor.x), r2(anchor.y)]
        for nid, at in ((na, q0), (nb, q1), (nc, qa)):
            ctx.new[nid] = GraphNode(id=nid, kind="choke", at=at)
            nodes[nid] = ctx.new[nid]
        used.update((na, nb, nc))
        ctx.bridges.append(Road(id="%s_bridge_%d" % (ctx.sid, kk), cls="bridge", polyline=[q0, q1], width=8.0, nodes=[na, nb]))
        ctx.roads.append(Road(id="%s_link_%da" % (ctx.sid, kk), cls="artery", polyline=[list(pa), q0], width=8.0, nodes=[a, na]))
        ctx.roads.append(Road(id="%s_link_%db" % (ctx.sid, kk), cls="artery", polyline=[q1, qa], width=8.0, nodes=[nb, nc]))


def _rebuild_graphs(s: Settlement, ctx: _Ctx) -> None:
    allroads = sorted(ctx.roads + ctx.bridges, key=lambda r: r.id)
    s.roads = allroads
    nodes = dict(ctx.nodes)
    nodes.update(ctx.new)
    used = set()
    for r in allroads:
        used.update(r.nodes)
    used.update(g.id for g in s.gates)
    missing = sorted(used - set(nodes))
    if missing:
        raise _Reject("unknown nodes %s" % ", ".join(missing))
    gn = [nodes[k] for k in sorted(used)]
    for n in gn:
        n.districts = []
    G.attach_districts(gn, s.districts)
    s.graph_nodes = gn
    try:
        s.graph_edges = G.movement_edges(allroads, {n.id: n for n in gn})
    except ValueError as e:
        raise _Reject(str(e))
    pseudo: List[Wall] = []
    for w in s.walls:
        for k, run in enumerate(wall_runs(w)):
            pseudo.append(Wall(id=w.id if k == 0 else "%s_%d" % (w.id, k + 1), kind=w.kind, polyline=run, thickness=w.thickness))
    s.district_nodes, edges = G.district_graph(s.districts, s.roads, pseudo, s.gates)
    byid = {e.id: e for e in edges}
    polys = [(d.id, Polygon(d.polygon)) for d in s.districts]
    for br in ctx.bridges:
        ends = [Point(ctx.new[br.nodes[0]].at), Point(ctx.new[br.nodes[1]].at)]
        sides = [sorted(did for did, pg in polys if pg.distance(p) <= G.SNAP) for p in ends]
        for x in sides[0]:
            for y in sides[1]:
                if x == y:
                    continue
                a, b = sorted((x, y))
                eid = "%s~%s" % (a, b)
                if eid in byid:
                    if br.id not in byid[eid].roads:
                        byid[eid].roads.append(br.id)
                        byid[eid].roads.sort()
                else:
                    byid[eid] = DistrictEdge(id=eid, a=a, b=b, kind="bridge", length=r2(polyline_length(br.polyline)),
                                             passable=True, gates=[], roads=[br.id])
    s.district_edges = sorted(byid.values(), key=lambda e: e.id)


def _assign_landmarks(s: Settlement) -> None:
    polys = [(d, Polygon(d.polygon)) for d in s.districts]
    for d in s.districts:
        d.landmarks = []
    for l in s.landmarks:
        p = Point(l.at)
        best = min(polys, key=lambda dp: (dp[1].distance(p), dp[0].id))
        l.district = best[0].id
        best[0].landmarks.append(l.id)
    for d in s.districts:
        d.landmarks.sort()


def _fix_gates(s: Settlement, ctx: _Ctx, bay_centre: Optional[Pt]) -> None:
    roads = {r.id: LineString(r.polyline) for r in s.roads if r.cls != "bridge"}
    new: List[Gate] = []
    for g in s.gates:
        if g.id in ctx.lost:
            at = ctx.lost[g.id]
            dx, dy = g.at[0] - at[0], g.at[1] - at[1]
            ln = math.hypot(dx, dy)
            if ln > 1.0:
                nrm = [r2(dx / ln), r2(dy / ln)]
            elif bay_centre is not None:
                dx, dy = bay_centre[0] - at[0], bay_centre[1] - at[1]
                ln = math.hypot(dx, dy) or 1.0
                nrm = [r2(dx / ln), r2(dy / ln)]
            else:
                nrm = list(g.normal)
            m = math.hypot(nrm[0], nrm[1]) or 1.0
            nrm = [r2(nrm[0] / m), r2(nrm[1] / m)]
            if min(LineString(w.polyline).distance(Point(at)) for w in s.walls if w.id == g.wall) > G.SNAP:
                raise _Reject("water gate %s is off the wall" % g.id)
            road = next((rid for rid in sorted(roads) if roads[rid].distance(Point(at)) <= G.SNAP and
                         any(r.id == rid and g.id in r.nodes for r in s.roads)), None)
            if road is None:
                raise _Reject("water gate %s has no road" % g.id)
            new.append(Gate(id=g.id, wall=g.wall, at=at, normal=nrm, road=road, kind="water_gate"))
        else:
            p = Point(g.at)
            road = g.road if g.road in roads and roads[g.road].distance(p) <= G.SNAP and any(
                r.id == g.road and g.id in r.nodes for r in s.roads) else next(
                (rid for rid in sorted(roads) if roads[rid].distance(p) <= G.SNAP and any(
                    r.id == rid and g.id in r.nodes for r in s.roads)), None)
            if road is None:
                raise _Reject("gate %s lost its road" % g.id)
            new.append(Gate(id=g.id, wall=g.wall, at=g.at, normal=g.normal, road=road, kind=g.kind))
    s.gates = sorted(new, key=lambda g: g.id)


def _hubs(s: Settlement) -> List[Pt]:
    return [(l.at[0], l.at[1]) for l in s.landmarks if l.kind in ("square", "market", "citadel", "fortress", "temple")]


def _mark_waterfront(s: Settlement, W) -> None:
    for d in s.districts:
        if Polygon(d.polygon).distance(W) <= 1.0:
            d.waterfront = True


def _harbour(s: Settlement, B, rb: float) -> None:
    cands = []
    for d in s.districts:
        pg = Polygon(d.polygon)
        shore = pg.boundary.intersection(B.boundary.buffer(0.2)).length
        if shore < 5.0 or d.kind in ("citadel", "core", "temple") or any(l.kind in ("square", "market") for l in s.landmarks if l.id in d.landmarks):
            continue
        cands.append((-shore, d.id, d))
    if not cands:
        raise _Reject("no district can become the harbour")
    cands.sort(key=lambda c: c[:2])
    hd = cands[0][2]
    hd.kind = "harbour"
    hd.label = "Harbour"
    pg = Polygon(hd.polygon)
    zone = pg.intersection(B.buffer(30.0))
    rp = (zone if not zone.is_empty and zone.area > 1 else pg).representative_point()
    sid = s.id
    s.landmarks.append(Landmark(id="%s_docks" % sid, kind="docks", at=[r2(rp.x), r2(rp.y)], r=25.0, district=hd.id, label="Docks"))
    s.landmarks.sort(key=lambda l: l.id)


def _island(s: Settlement, B, cb: Pt, u: Pt, rb: float, rng) -> Dict[str, Any]:
    """An island site inside the bay: a small blob, kept clear of the shore."""
    for _ in range(24):
        dist = rng.uniform(0.25, 0.7) * rb
        side = rng.uniform(-0.4, 0.4) * rb
        c = (cb[0] + u[0] * dist - u[1] * side, cb[1] + u[1] * dist + u[0] * side)
        r = max(28.0, 0.2 * rb)
        blob = Point(c).buffer(r, resolution=6)
        if B.buffer(-35.0).contains(blob) and not Polygon(s.footprint).buffer(25.0).intersects(blob):
            return {"id": "%s_island_1" % s.id, "at": [r2(c[0]), r2(c[1])], "r": r2(r), "polygon": _ring(blob),
                    "fort": False, "link": "none"}
    raise _Reject("no room for an island")


def _build(s0: Settlement, p: Dict[str, Any], attempt: int, rng_draws: Dict[str, float]) -> Settlement:
    import copy
    s = copy.deepcopy(s0)
    rng = stage_rng(s.id, s.generator, s.seed, "water%d" % attempt)
    foot = Polygon(s.footprint)
    hub = (foot.centroid.x, foot.centroid.y)
    R = math.sqrt(foot.area / math.pi)
    mode = p["water"]
    angle_given = p.get("water_angle") is not None
    ang = math.radians((p["water_angle"] if angle_given else rng_draws["angle"] + attempt * 23.0) % 360.0)
    width = p.get("water_width") or rng_draws["width"]
    size = p.get("water_bay_size") or rng_draws["bay"]
    waters: List[Any] = []
    water: Dict[str, Any] = {"kind": mode}
    bay = None
    if mode in ("bay", "both"):
        B, cb, u, rb = _bay(s, foot, hub, R, ang, size, rng)
        land = foot.difference(B)
        parts = sorted(_polys(land), key=lambda g: -g.area)
        if not parts or parts[0].area < 0.6 * foot.area or foot.area - parts[0].area < 0.025 * foot.area:
            raise _Reject("bay removes %.0f %% of the footprint" % (100.0 * (1 - land.area / foot.area)))
        newfoot = _clean(parts[0])
        if newfoot is None:
            raise _Reject("land is empty")
        if any(B.distance(Point(h)) < 14.0 for h in _hubs(s)):
            raise _Reject("a landmark is in the bay")
        bay = (B, cb, u, rb)
        waters.append(B)
        water["bays"] = [{"id": "%s_bay" % s.id, "polygon": _ring(B), "at": [r2(cb[0]), r2(cb[1])], "r": r2(rb)}]
        s.footprint = _ring(newfoot)
        for w in s.walls:
            if w.kind == "outer":
                ring = _ring(newfoot)
                w.polyline = ring + [ring[0]]
                w.open_shore = []
    if mode in ("river", "both"):
        rang = ang if mode == "river" else ang + math.pi + rng.uniform(-0.4, 0.4)
        toward = (bay[1][0], bay[1][1]) if bay else None
        line, rp = _river(s, hub, R, width, rang, rng, toward)
        water["rivers"] = [{"id": "%s_river" % s.id, "polyline": line, "width": r2(width), "polygon": _ring(rp)}]
        waters.append(rp)
    W = unary_union(waters)
    if bay is not None and "rivers" in water:
        # the river's polygon ends in the bay: the union is the water; walls keep their shore marks
        pass
    for h in _hubs(s):
        if W.distance(Point(h)) < 12.0:
            raise _Reject("a landmark is in the water")
    for g in s.gates:
        if W.distance(Point(g.at)) < 1.0 and not (bay is not None and bay[0].contains(Point(g.at))):
            raise _Reject("gate %s in the river" % g.id)
        if "rivers" in water and Polygon(water["rivers"][0]["polygon"]).distance(Point(g.at)) < 4.0:
            raise _Reject("gate %s on the river" % g.id)
    # walls: the outer wall follows the shore (shore edges open); any wall crossing water is open there
    s.walls = [_mark_wall(w, W, bay is not None and w.kind == "outer") if LineString(w.polyline).distance(W) < OPEN_TOL
               else w for w in s.walls]
    minarea = float(s.params.get("min_district_area", 1600.0))
    s.districts = _clip_districts(s, W, minarea)
    ctx = _Ctx(s)
    _cut_roads(s, W, ctx, maxbridge=2.2 * (width if "rivers" in water else 40.0) + 30.0)
    _link_components(s, W, ctx, maxbridge=2.2 * (width if "rivers" in water else 40.0) + 30.0)
    _link_banks(s, W, ctx, maxbridge=2.2 * (width if "rivers" in water else 40.0) + 30.0)
    s.roads = ctx.roads + ctx.bridges
    _fix_gates(s, ctx, bay[1] if bay else None)
    _mark_waterfront(s, W)
    if bay is not None:
        _harbour(s, bay[0], bay[3])
        if p.get("water_island"):
            water["islands"] = [_island(s, bay[0], bay[1], bay[2], bay[3], rng)]
    s.landmarks = sorted(s.landmarks, key=lambda l: l.id)
    _assign_landmarks(s)
    _rebuild_graphs(s, ctx)
    for n in s.graph_nodes:
        if n.id in ctx.new:
            for m in s.graph_nodes:
                if m.id != n.id and m.id not in ctx.new and math.hypot(n.at[0] - m.at[0], n.at[1] - m.at[1]) < NODE_GAP:
                    raise _Reject("nodes %s and %s are %.1f m apart" % (n.id, m.id, math.hypot(n.at[0] - m.at[0], n.at[1] - m.at[1])))
    water["bridges"] = sorted(b.id for b in ctx.bridges)
    water["bridge_nodes"] = sorted(n for b in ctx.bridges for n in b.nodes)
    s.water = water
    s.params = dict(s.params)
    s.params.update({k: v for k, v in p.items() if v is not None})   # only what was given: regeneration reads these back
    water["angle"] = r2(math.degrees(ang) % 360.0)
    return s


def apply(s: Settlement, p: Dict[str, Any]) -> Settlement:
    """Add the water described by the resolved parameters `p`. The first of up to 60 seeded attempts whose result passes the
    checks is returned; GenerationError when none does."""
    if not active(p):
        return s
    from .check import check
    draw = stage_rng(s.id, s.generator, s.seed, "water")
    draws = {"angle": draw.uniform(0.0, 360.0), "width": draw.uniform(24.0, 40.0), "bay": draw.uniform(0.35, 0.5)}
    reason = "no attempt"
    for attempt in range(ATTEMPTS):
        pa = dict(p)
        try:
            out = _build(s, pa, attempt, draws)
        except (_Reject, ValueError) as e:
            reason = str(e)
            continue
        errs = check(out)
        if not errs:
            return out
        reason = errs[0]
    raise GenerationError("water %s fits nowhere on this settlement (%s); try another seed or parameters" % (p["water"], reason))


# --- checks -----------------------------------------------------------------------------------------------------

def check_water(s: Settlement) -> List[str]:
    """Water invariants (TOWN_SPEC section 8): nothing dry in the water, bridges span the water between banks, walls closed
    against the shore, the harbour in place."""
    if not s.water:
        return []
    errs: List[str] = []
    W = water_union(s)
    if W.is_empty:
        return ["water: no polygon"]
    for d in s.districts:
        a = Polygon(d.polygon).intersection(W).area
        if a > 1.0:
            errs.append("district %s lies %.1f m2 in the water" % (d.id, a))
    foot = Polygon(s.footprint)
    if foot.intersection(W).area > 0.005 * foot.area:
        # a river crosses the footprint by design; only a bay may not
        for b in s.water.get("bays", []):
            if foot.intersection(Polygon(b["polygon"])).area > 1.0:
                errs.append("footprint overlaps the bay")
    Wi = W.buffer(-0.5)
    for g in s.gates:
        if Wi.contains(Point(g.at)):
            errs.append("gate %s lies in the water" % g.id)
        if g.kind == "water_gate" and W.distance(Point(g.at)) > 1.0:
            errs.append("water gate %s is %.1f m from the water" % (g.id, W.distance(Point(g.at))))
    bridge_ids = set(s.water.get("bridges", []))
    node_at = {n.id: n.at for n in s.graph_nodes}
    for r in s.roads:
        ln = LineString(r.polyline)
        inside = ln.intersection(W).length
        if r.id in bridge_ids or r.cls == "bridge":
            if r.cls != "bridge" or len(r.nodes) != 2:
                errs.append("bridge %s: expected class bridge and two nodes" % r.id)
                continue
            for nid in r.nodes:
                if W.boundary.distance(Point(node_at[nid])) > 0.5:
                    errs.append("bridge %s: end %s is not on the bank" % (r.id, nid))
            if inside < 0.5 * ln.length:
                errs.append("bridge %s does not span water" % r.id)
        elif inside > 0.5:
            errs.append("road %s runs %.1f m through the water" % (r.id, inside))
    # bridges must be graph edges, and joined to something on the movement graph
    edge_roads = {e.road for e in s.graph_edges if e.cls == "bridge"}
    for b in sorted(bridge_ids):
        if b not in edge_roads:
            errs.append("bridge %s is not a movement-graph edge" % b)
    # walls closed against the shore
    for w in s.walls:
        n = len(w.polyline) - 1
        openf = [False] * max(n, 0)
        for a, b in w.open_shore:
            if not (0 <= a < b <= n):
                errs.append("wall %s: bad open_shore range [%d, %d]" % (w.id, a, b))
                continue
            for i in range(a, b):
                openf[i] = True
            for i in (a, b % n if n else 0):
                if W.boundary.distance(Point(w.polyline[i])) > 0.5:
                    errs.append("wall %s: open range [%d, %d] does not start and end on the shore" % (w.id, a, b))
                    break
        for i in range(n):
            p, q = w.polyline[i], w.polyline[i + 1]
            seg = LineString([p, q])
            if openf[i]:
                mid = Point((p[0] + q[0]) / 2.0, (p[1] + q[1]) / 2.0)
                if W.distance(mid) > 0.5:
                    errs.append("wall %s: open edge %d is not on the water" % (w.id, i))
                    break
            elif seg.intersection(W).length > 0.5:
                errs.append("wall %s: solid edge %d runs through the water" % (w.id, i))
                break
    if s.water.get("bays"):
        if not any(d.kind == "harbour" for d in s.districts):
            errs.append("bay: no harbour district")
        docks = [l for l in s.landmarks if l.kind == "docks"]
        if not docks:
            errs.append("bay: no docks landmark")
        for l in docks:
            if W.distance(Point(l.at)) > 80.0:
                errs.append("docks %s are %.0f m from the water" % (l.id, W.distance(Point(l.at))))
        outer = [w for w in s.walls if w.kind == "outer"]
        if outer and not outer[0].open_shore:
            errs.append("bay: the outer wall has no open shore")
    for isl in s.water.get("islands", []):
        pg = Polygon(isl["polygon"])
        if not any(pg.within(Polygon(b["polygon"])) for b in s.water.get("bays", [])):
            errs.append("island %s lies outside the bay" % isl["id"])
    return errs
