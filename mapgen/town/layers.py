"""Layered district subdivision: apply a resolved `layers` spec to a settlement (design: mapgen/TOWN_LAYERS_SPEC.md section 5)."""
from __future__ import annotations

import hashlib
import math
from fnmatch import fnmatchcase
from typing import Any, Dict, List, Optional, Set, Tuple

from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

from . import graph as G
from .layerfill import Frame, Unfit
from .layers_params import FILLS
from .model import District, DistrictEdge, GraphNode, LayerNode, Road, Settlement, Wall, polyline_length, r2
from .params import ParamError, resolve as resolve_params, stage_rng
from .water import water_union, wall_runs

ID_MAX = 64
TOL = 0.02            # a vertex this close to an edge is stitched into it
CLUSTER = 0.015       # vertices this close are one vertex
Pt = Tuple[float, float]


def _key(sid: str, did: str) -> str:
    p = sid + "_d_"
    return did[len(p):] if did.startswith(p) else did


def _fit(prefix: str, parts: List[str]) -> str:
    """`prefix + "_".join(parts)`, hashed when over the id length limit."""
    s = prefix + "_".join(parts)
    if len(s) <= ID_MAX:
        return s
    return prefix + hashlib.sha1("_".join(parts).encode("utf-8")).hexdigest()[:12]


def _ring(poly: Polygon) -> List[List[float]]:
    return [[x, y] for x, y in list(orient(poly, 1.0).exterior.coords)[:-1]]


def _ref_rot(P: Polygon, road_geom) -> float:
    """Direction of the reference edge: the longest boundary edge lying on a road, else the longest edge."""
    c = list(orient(P, 1.0).exterior.coords)
    best, best_key = 0.0, None
    for i in range(len(c) - 1):
        a, b = c[i], c[i + 1]
        ln = math.hypot(b[0] - a[0], b[1] - a[1])
        if ln < 1e-6:
            continue
        on = road_geom is not None and road_geom.distance(Point((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)) < 0.5
        k = (1 if on else 0, ln, -i)
        if best_key is None or k > best_key:
            best_key, best = k, math.atan2(b[1] - a[1], b[0] - a[0])
    return best


def _match(rules: List[Dict[str, Any]], d: District, depth: int) -> Optional[int]:
    for i, r in enumerate(rules):
        if not (r["depth"][0] <= depth <= r["depth"][1]):
            continue
        if r["kind"] is not None and d.kind not in r["kind"]:
            continue
        if d.kind in r["skip_kinds"]:
            continue
        if r["area"] is not None and not (r["area"][0] <= d.area <= r["area"][1]):
            continue
        if r["key"] is not None and not fnmatchcase(d.key, r["key"]):
            continue
        return i
    return None


def _weighted(rng, weights: Dict[str, float]) -> str:
    names = sorted(weights)
    x = rng.random() * sum(weights[n] for n in names)
    for n in names:
        x -= weights[n]
        if x < 0:
            return n
    return names[-1]


class _State:
    pass


def _subdivide(s: Settlement, spec: Dict[str, Any]) -> _State:
    sid = s.id
    st = _State()
    st.leaves = {d.id: d for d in s.districts}
    st.nodes = {}            # id -> LayerNode
    st.sub = []              # Road objects of sub-roads
    st.skipped = {}
    walls = [LineString(w.polyline) for w in s.walls]
    wallg = unary_union(walls).buffer(1.0) if walls else None
    st.wallg = wallg
    st.shore = water_union(s).boundary.buffer(1.0) if s.water and not water_union(s).is_empty else None
    parent_roads = [(r.id, LineString(r.polyline)) for r in s.roads]
    road_geom = unary_union([ln for _, ln in parent_roads]) if parent_roads else None
    rules = spec["rules"]
    for depth in range(spec["max_depth"]):
        cands = sorted((d for d in st.leaves.values() if d.depth == depth), key=lambda d: d.id)
        for d in cands:
            if d.area < spec["min_split_area"]:
                continue
            ri = _match(rules, d, depth)
            if ri is None:
                continue
            rule = rules[ri]
            rp = stage_rng(sid, "layers", s.seed, d.id + "/pick")
            roll = rp.random()
            if roll >= rule["p"]:
                continue
            use = _weighted(rp, rule["use"]) if rule["use"] else "none"
            if use == "none":
                continue
            P = orient(Polygon(d.polygon), 1.0)
            bad = None
            for rid, ln in parent_roads:
                if ln.envelope.intersects(P.envelope) and ln.intersection(P.buffer(-0.05)).length > 1.0:
                    bad = "road_inside"
                    break
            if bad:
                st.skipped[d.id] = bad
                continue
            mod = FILLS[use]
            overrides = {k: v for k, v in rule["params"].items()}
            p = resolve_params(mod.SUB_DEFS, s.seed, sid, "layers/%s/%s" % (d.id, use), overrides)
            al = rule["align"]
            if al == "edge":
                rot = _ref_rot(P, road_geom)
            elif al == "parent":
                par = st.nodes.get(d.parent)
                if par is not None:
                    rot = math.radians(par.params.get("rot", 0.0))
                elif "rotation" in s.params:
                    rot = math.radians(float(s.params["rotation"]))
                else:
                    rot = _ref_rot(P, road_geom)
            elif al == "none":
                rot = 0.0
            else:
                rot = math.radians(float(al.split(":")[1]))
            if rule["align_jitter"]:
                rot += math.radians(rp.uniform(-1.0, 1.0) * rule["align_jitter"])
            cen = P.centroid if P.contains(P.centroid) else P.representative_point()
            frame = Frame((cen.x, cen.y), rot)
            try:
                fl = mod.fill(P, stage_rng(sid, "layers", s.seed, d.id + "/geom"), p, frame, spec["min_area"])
            except Unfit as e:
                st.skipped[d.id] = "unfit: %s" % e
                continue
            pk = _key(sid, d.id)
            ids = ["%s_%s" % (d.id, k) for k, _ in fl.faces] + ["%s_sr_%s_%s" % (sid, pk, r.key) for r in fl.roads]
            if max(len(i) for i in ids) > ID_MAX:
                st.skipped[d.id] = "id_length"
                continue
            if len(st.leaves) - 1 + len(fl.faces) > spec["max_districts"]:
                st.skipped[d.id] = "max_districts"
                continue
            kdraw = stage_rng(sid, "layers", s.seed, d.id + "/kinds") if rule["kinds"] else None
            children = []
            for n, (k, poly) in enumerate(sorted(fl.faces, key=lambda t: t[0])):
                kind = _weighted(kdraw, rule["kinds"]) if kdraw is not None else d.kind
                wf = d.waterfront
                if wf and s.water:
                    wf = poly.distance(water_union(s)) <= 1.0
                children.append(District(id="%s_%s" % (d.id, k), key=k, kind=kind, polygon=_ring(poly), area=r2(poly.area),
                                         label="%s %d" % (d.label, n + 1), inside_wall=d.inside_wall, waterfront=wf,
                                         landmarks=[], parent=d.id, depth=d.depth + 1))
            pp = dict(p)
            pp["rot"] = round(math.degrees(rot) % 360.0, 4)
            st.nodes[d.id] = LayerNode(id=d.id, parent=d.parent, depth=d.depth, polygon=d.polygon, use=use, rule=ri, params=pp,
                                       children=sorted(c.id for c in children))
            del st.leaves[d.id]
            for c in children:
                st.leaves[c.id] = c
            for r in fl.roads:
                st.sub.append(Road(id="%s_sr_%s_%s" % (sid, pk, r.key), cls=r.cls, polyline=[[x, y] for x, y in r.polyline],
                                   width=float(max(7, 10 - 2 * d.depth)) if r.cls == "artery" else 5.0))
    return st


# --- stitching ------------------------------------------------------------------------------------------------

class _Grid:
    def __init__(self, cell: float):
        self.cell = cell
        self.h: Dict[Tuple[int, int], List[Pt]] = {}

    def _c(self, x: float, y: float) -> Tuple[int, int]:
        return (int(math.floor(x / self.cell)), int(math.floor(y / self.cell)))

    def add(self, p: Pt) -> None:
        self.h.setdefault(self._c(*p), []).append(p)

    def near(self, x0: float, y0: float, x1: float, y1: float) -> List[Pt]:
        a, b = self._c(x0, y0), self._c(x1, y1)
        out: List[Pt] = []
        for i in range(a[0], b[0] + 1):
            for j in range(a[1], b[1] + 1):
                out.extend(self.h.get((i, j), []))
        return out


def _cluster(orig: List[Pt], new: List[Pt]) -> Dict[Pt, Pt]:
    """Original points never move; a new point within CLUSTER of an earlier point maps onto it."""
    g = _Grid(0.1)
    m: Dict[Pt, Pt] = {}
    for p in orig:
        if p not in m:
            m[p] = p
            g.add(p)
    for p in new:
        if p in m:
            continue
        rep = None
        for q in g.near(p[0] - CLUSTER, p[1] - CLUSTER, p[0] + CLUSTER, p[1] + CLUSTER):
            if math.hypot(p[0] - q[0], p[1] - q[1]) <= CLUSTER:
                rep = q
                break
        if rep is None:
            g.add(p)
            m[p] = p
        else:
            m[p] = rep
    return m


def _stitch_seq(seq: List[Pt], closed: bool, g: _Grid) -> List[Pt]:
    n = len(seq)
    own = set(seq)
    out: List[Pt] = []
    segs = n if closed else n - 1
    for i in range(segs):
        a, b = seq[i], seq[(i + 1) % n]
        out.append(a)
        dx, dy = b[0] - a[0], b[1] - a[1]
        L2 = dx * dx + dy * dy
        if L2 < 1e-12:
            continue
        ins = []
        for c in g.near(min(a[0], b[0]) - TOL, min(a[1], b[1]) - TOL, max(a[0], b[0]) + TOL, max(a[1], b[1]) + TOL):
            if c in own or math.hypot(c[0] - a[0], c[1] - a[1]) <= 0.05 or math.hypot(c[0] - b[0], c[1] - b[1]) <= 0.05:
                continue
            t = ((c[0] - a[0]) * dx + (c[1] - a[1]) * dy) / L2
            if not (0.0 < t < 1.0):
                continue
            if math.hypot(c[0] - (a[0] + t * dx), c[1] - (a[1] + t * dy)) <= TOL:
                ins.append((t, c))
        for t, c in sorted(ins):
            if out[-1] != c:
                out.append(c)
    if not closed:
        out.append(seq[-1])
    return out


def _dedupe(seq: List[Pt], closed: bool) -> List[Pt]:
    out: List[Pt] = []
    for p in seq:
        if not out or out[-1] != p:
            out.append(p)
    if closed and len(out) > 1 and out[0] == out[-1]:
        out.pop()
    return out


def _stitch_all(s: Settlement, st: _State, roads: List[Road]) -> None:
    """Snap near-equal vertices together and insert every vertex lying on an edge into that edge (section 5.4)."""
    orig: List[Pt] = []
    for r in roads:
        orig.extend((p[0], p[1]) for p in r.polyline)
    orig.extend((g.at[0], g.at[1]) for g in s.gates)
    orig.extend((n.at[0], n.at[1]) for n in s.graph_nodes)
    new: List[Pt] = []
    for d in list(st.leaves.values()) + list(st.nodes.values()):
        new.extend((p[0], p[1]) for p in d.polygon)
    m = _cluster(orig, new)
    reps = sorted(set(m.values()))
    g = _Grid(2.0)
    for p in reps:
        g.add(p)
    st.grid = g

    def fix(pts, closed):
        seq = _dedupe([m[(p[0], p[1])] for p in pts], closed)
        if len(seq) < (3 if closed else 2):
            return [list(p) for p in pts]
        return [[x, y] for x, y in _stitch_seq(seq, closed, g)]
    for d in list(st.leaves.values()) + list(st.nodes.values()):
        d.polygon = fix(d.polygon, True)
    for d in st.leaves.values():
        d.area = r2(Polygon(d.polygon).area)
    for r in roads:
        r.polyline = fix(r.polyline, False)
    for gt in s.gates:
        gt.at = [m[(gt.at[0], gt.at[1])][0], m[(gt.at[0], gt.at[1])][1]]
    for n in s.graph_nodes:
        n.at = [m[(n.at[0], n.at[1])][0], m[(n.at[0], n.at[1])][1]]


# --- boundary streets ------------------------------------------------------------------------------------------

def _boundary_streets(s: Settlement, st: _State, roads: List[Road]) -> List[Road]:
    sid = s.id
    road_geom = unary_union([LineString(r.polyline) for r in roads]) if roads else None
    nodes = sorted(st.nodes.values(), key=lambda n: (n.depth, n.id))
    polys: Dict[str, Tuple[Polygon, int]] = {}
    for d in st.leaves.values():
        polys[d.id] = (Polygon(d.polygon), d.depth)
    for n in nodes:
        polys[n.id] = (Polygon(n.polygon), n.depth)
    used: Set[frozenset] = set()
    counts: Dict[str, int] = {}
    out: List[Road] = []
    for n in nodes:
        P = polys[n.id][0]
        anc = set()
        a = n.parent
        while a:
            anc.add(a)
            a = st.nodes[a].parent if a in st.nodes else ""
        cands = sorted(((dep, i) for i, (_, dep) in polys.items() if i != n.id and i not in anc and dep <= n.depth))
        cpolys = [(i, polys[i][0]) for _, i in cands if polys[i][0].envelope.intersects(P.envelope)]
        ring = [tuple(p) for p in n.polygon]
        m = len(ring)
        edges = []
        for i in range(m):
            a, b = ring[i], ring[(i + 1) % m]
            mid = Point((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            ek = frozenset((a, b))
            if ek in used:
                edges.append(None)
                continue
            if (st.wallg is not None and st.wallg.distance(mid) <= 0.0) or (st.shore is not None and st.shore.distance(mid) <= 0.0):
                edges.append(None)
                continue
            if road_geom is not None and road_geom.distance(mid) < 0.3:
                edges.append(None)
                continue
            nb = "edge"
            for i2, q in cpolys:
                if q.distance(mid) <= 0.1:
                    nb = _key(sid, i2)
                    break
            edges.append((a, b, nb, ek))
        # runs of consecutive free edges with the same neighbour (cyclic)
        start = 0
        for i in range(m):
            if edges[i] is None or edges[(i - 1) % m] is None or edges[(i - 1) % m][2] != edges[i][2]:
                start = i
                break
        else:
            start = 0
        runs: List[Tuple[str, List[Pt]]] = []
        cur: Optional[Tuple[str, List[Pt]]] = None
        for k in range(m):
            e = edges[(start + k) % m]
            if e is None:
                cur = None
                continue
            if cur is None or cur[0] != e[2]:
                cur = (e[2], [e[0], e[1]])
                runs.append(cur)
            else:
                cur[1].append(e[1])
            used.add(e[3])
        ka = _key(sid, n.id)
        for nb, pts in runs:
            pair = sorted([ka, nb]) if nb != "edge" else [ka, "edge"]
            base = "%s_bs_%s_x_%s" % (sid, pair[0], pair[1])
            if len(base) > ID_MAX:
                base = "%s_bs_%s" % (sid, hashlib.sha1(base.encode("utf-8")).hexdigest()[:12])
            c = counts.get(base, 0) + 1
            counts[base] = c
            rid = base if c == 1 else "%s_%d" % (base, c)
            out.append(Road(id=rid, cls="street", polyline=[[x, y] for x, y in pts], width=5.0))
    return out


# --- pruning, nodes, graphs --------------------------------------------------------------------------------------

def _prune(sub: List[Road], bs: List[Road], parents: List[Road]) -> Tuple[List[Road], List[Road]]:
    others = parents + bs
    sub = list(sub)
    while True:
        cmap: Dict[Tuple[float, float], Set[str]] = {}
        for r in sub + others:
            for p in r.polyline:
                cmap.setdefault((p[0], p[1]), set()).add(r.id)
        changed = False
        for r in list(sub):
            pl = r.polyline
            if pl[0] == pl[-1]:
                continue

            def shared(i):
                return len(cmap[(pl[i][0], pl[i][1])] - {r.id}) > 0
            if shared(0) and shared(-1):
                continue
            lo, hi = 0, len(pl) - 1
            while lo < hi and not shared(lo):
                lo += 1
            while hi > lo and not shared(hi):
                hi -= 1
            sub.remove(r)
            if hi > lo:
                r.polyline = pl[lo:hi + 1]
                if polyline_length(r.polyline) >= 8.0:
                    sub.append(r)
            changed = True
            break
        if not changed:
            uf: Dict[str, str] = {r.id: r.id for r in sub + others}

            def find(x: str) -> str:
                while uf[x] != x:
                    uf[x] = uf[uf[x]]
                    x = uf[x]
                return x
            for ids in cmap.values():
                for o in ids:
                    uf[find(o)] = find(next(iter(ids)))
            main = {find(r.id) for r in parents}
            keep = [r for r in sub if find(r.id) in main]
            keepb = [r for r in bs if find(r.id) in main]
            if len(keep) == len(sub) and len(keepb) == len(bs):
                return sorted(sub, key=lambda r: r.id), sorted(bs, key=lambda r: r.id)
            sub, bs, others = keep, keepb, parents + keepb


def _seq(r: Road, at: Dict[Tuple[float, float], List[str]], orig: List[str], existing: Dict[str, GraphNode]) -> List[Tuple[int, str]]:
    """(vertex index, node id) along the road: the nodes the road already listed, in their order, plus the new junction nodes."""
    out: List[Tuple[int, str]] = []
    last = -1
    for nid in orig:
        at_n = existing[nid].at
        j = next((k for k in range(last + 1, len(r.polyline)) if r.polyline[k][0] == at_n[0] and r.polyline[k][1] == at_n[1]), None)
        if j is None:
            continue
        out.append((j, nid))
        last = j
    occupied = {j for j, _ in out}
    have = {i for _, i in out}
    for i, p in enumerate(r.polyline):
        ids = [x for x in at.get((p[0], p[1]), []) if x not in have]
        news = [x for x in ids if x not in existing]
        olds = [x for x in ids if x in existing]
        out.extend((i, x) for x in news)
        if olds and i not in occupied:
            out.append((i, olds[0]))
    return sorted(out, key=lambda e: e[0])


def _build_nodes(s: Settlement, st: _State, spec: Dict[str, Any], roads: List[Road]) -> None:
    sid = s.id
    existing = {n.id: n for n in s.graph_nodes}
    at: Dict[Tuple[float, float], List[str]] = {}
    for n in sorted(s.graph_nodes, key=lambda n: n.id):
        at.setdefault((n.at[0], n.at[1]), []).append(n.id)
    orig_nodes = {r.id: list(r.nodes) for r in roads}
    used_ids = set(existing) | {d.id for d in s.districts} | {r.id for r in roads} | {g.id for g in s.gates} | \
        {l.id for l in s.landmarks} | {w.id for w in s.walls}
    major = spec["graph"] == "major"
    in_graph = []
    for r in roads:
        if r.cls == "street" and "_sr_" in r.id and major:
            r.nodes = []
        else:
            in_graph.append(r)

    pids = {r.id for r in s.roads}
    for _ in range(40):
        uf: Dict[str, str] = {r.id: r.id for r in in_graph}

        def find(x: str) -> str:
            while uf[x] != x:
                uf[x] = uf[uf[x]]
                x = uf[x]
            return x
        byc: Dict[Tuple[float, float], List[str]] = {}
        for r in in_graph:
            for p in r.polyline:
                byc.setdefault((p[0], p[1]), []).append(r.id)
        for ids in byc.values():
            for o in ids[1:]:
                uf[find(o)] = find(ids[0])
        main = {find(i) for i in pids if i in uf}
        lost = {find(r.id) for r in in_graph if find(r.id) not in main}
        if not lost:
            break
        lostc = {c for c, ids in byc.items() if any(find(i) in lost for i in ids)}
        promote = [r for r in roads if r not in in_graph and any((p[0], p[1]) in lostc for p in r.polyline)]
        if not promote:
            break
        in_graph.extend(promote)

    def rkey(r: Road) -> str:
        p = sid + "_sr_"
        if r.id.startswith(p):
            return r.id[len(p):]
        p = sid + "_"
        return r.id[len(p):] if r.id.startswith(p) else r.id
    cmap: Dict[Tuple[float, float], List[Road]] = {}
    for r in in_graph:
        for i, p in enumerate(r.polyline):
            if r.cls == "bridge" and 0 < i < len(r.polyline) - 1:
                continue   # a bridge joins the network only at its ends
            lst = cmap.setdefault((p[0], p[1]), [])
            if r not in lst:
                lst.append(r)
    newn: List[GraphNode] = []
    for c in sorted(cmap):
        if c in at or len(cmap[c]) < 2:
            continue
        rs = sorted(cmap[c], key=lambda r: r.id)
        base = _fit(sid + "_j_", [rkey(rs[0]), rkey(rs[1])])
        nid, k = base, 1
        while nid in used_ids:
            k += 1
            nid = "%s_%d" % (base, k)
        used_ids.add(nid)
        at[c] = [nid]
        newn.append(GraphNode(id=nid, kind="choke", at=[c[0], c[1]]))
    nodes = dict(existing)
    for n in newn:
        nodes[n.id] = n
    seen: Set[str] = set()
    for r in sorted(in_graph, key=lambda r: r.id):
        for _ in range(8):
            seq = _seq(r, at, orig_nodes[r.id], existing)
            fix = None
            for k in range(len(seq) - 1):
                (i0, a), (i1, b) = seq[k], seq[k + 1]
                if a == b:
                    continue
                if "%s~%s" % tuple(sorted((a, b))) in seen:
                    fix = (i0, i1)
                    break
            if fix is None:
                break
            i0, i1 = fix
            if i1 - i0 >= 2:
                mi = (i0 + i1) // 2
            else:
                pa, pb = r.polyline[i0], r.polyline[i1]
                r.polyline.insert(i1, [r2((pa[0] + pb[0]) / 2), r2((pa[1] + pb[1]) / 2)])
                mi = i1
            mp = r.polyline[mi]
            base = _fit(sid + "_jm_", [rkey(r)])
            nid, k = base, 1
            while nid in used_ids:
                k += 1
                nid = "%s_%d" % (base, k)
            used_ids.add(nid)
            at[(mp[0], mp[1])] = [nid]
            nodes[nid] = GraphNode(id=nid, kind="choke", at=[mp[0], mp[1]])
        seq = [nid for _, nid in _seq(r, at, orig_nodes[r.id], existing)]
        r.nodes = seq
        for k in range(len(seq) - 1):
            if seq[k] != seq[k + 1]:
                seen.add("%s~%s" % tuple(sorted((seq[k], seq[k + 1]))))
    # keep a node that lost every road only when it is a gate or zone
    used = set()
    for r in roads:
        used.update(r.nodes)
    keep = used | {g.id for g in s.gates} | {k for k, n in nodes.items() if n.kind == "zone"}
    s.graph_nodes = [nodes[k] for k in sorted(keep) if k in nodes]
    for n in s.graph_nodes:
        n.districts = []
    s.roads = sorted(roads, key=lambda r: r.id)
    G.attach_districts(s.graph_nodes, s.districts)
    s.graph_edges = G.movement_edges(s.roads, {n.id: n for n in s.graph_nodes})


def _district_graph(s: Settlement, grid: _Grid) -> None:
    pseudo: List[Wall] = []
    for w in s.walls:
        for k, run in enumerate(wall_runs(w)):
            pseudo.append(Wall(id=w.id if k == 0 else "%s_%d" % (w.id, k + 1), kind=w.kind, polyline=[[x, y] for x, y in _stitch_seq([(p[0], p[1]) for p in run], False, grid)],
                           thickness=w.thickness))
    s.district_nodes, edges = G.district_graph(s.districts, s.roads, pseudo, s.gates)
    byid = {e.id: e for e in edges}
    polys = [(d.id, Polygon(d.polygon)) for d in s.districts]
    for br in [r for r in s.roads if r.cls == "bridge"]:
        ends = [Point(br.polyline[0]), Point(br.polyline[-1])]
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


def _connected(s: Settlement) -> bool:
    ids = {n.id for n in s.graph_nodes}
    adj: Dict[str, Set[str]] = {i: set() for i in ids}
    for e in s.graph_edges:
        if e.passable and e.a in adj and e.b in adj:
            adj[e.a].add(e.b)
            adj[e.b].add(e.a)
    if not ids:
        return True
    start = sorted(ids)[0]
    seen = {start}
    todo = [start]
    while todo:
        for m in adj[todo.pop()]:
            if m not in seen:
                seen.add(m)
                todo.append(m)
    return len(seen) == len(ids)


def _finish(s0: Settlement, spec: Dict[str, Any], st: _State, boundary: str) -> Settlement:
    import copy
    s = copy.deepcopy(s0)
    st = copy.deepcopy(st)
    sid = s.id
    parents = [r for r in s.roads]
    sub = list(st.sub)
    _stitch_all(s, st, parents + sub)
    s.districts = sorted(st.leaves.values(), key=lambda d: d.id)
    bs: List[Road] = []
    if boundary == "streets":
        bs = _boundary_streets(s, st, parents + sub)
        # the streets' vertices are existing ring vertices, so they need no further stitching
    sub, bs = _prune(sub, bs, parents)
    roads = parents + sub + bs
    s.layers = sorted(st.nodes.values(), key=lambda n: (n.depth, n.id))
    spec = dict(spec)
    # landmarks of subdivided districts go to the nearest leaf
    polys = [(d, Polygon(d.polygon)) for d in s.districts]
    moved = {l.id for l in s.landmarks if l.district in st.nodes}
    for l in s.landmarks:
        if l.id in moved:
            pt = Point(l.at)
            best = min(polys, key=lambda dp: (dp[1].distance(pt), dp[0].id))
            l.district = best[0].id
    for d in s.districts:
        d.landmarks = sorted(l.id for l in s.landmarks if l.district == d.id)
    _build_nodes(s, st, spec, roads)
    _district_graph(s, st.grid)
    return s


def apply(s: Settlement, spec: Dict[str, Any]) -> Settlement:
    """Subdivide the districts of `s` as the resolved `layers` spec says. The spec's `skipped` map is filled in the echo
    `Settlement.params["layers"]`."""
    st = _subdivide(s, spec)
    spec = dict(spec)
    spec["skipped"] = {k: st.skipped[k] for k in sorted(st.skipped)}
    if not st.nodes:
        out = s
        out.params = dict(s.params)
        out.params["layers"] = spec
        return out
    out = _finish(s, spec, st, spec["boundary"])
    if spec["boundary"] == "roads_only" and not _connected(out):
        spec["boundary"] = "roads_only"
        out = _finish(s, spec, st, "streets")
    out.params = dict(s.params)
    out.params["layers"] = spec
    return out
