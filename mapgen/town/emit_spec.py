"""Settlement -> YAML fragment of a map spec (design: mapgen/TOWN_SPEC.md section 9).

Fixed-order text template, never a YAML round trip: the same settlement gives byte-identical text. The fragment holds
the `points`, `regions`, `lines`, `crossings` and `pois` entries; the author pastes each list into the spec.
"""
from __future__ import annotations

import json
from typing import List, Tuple

from shapely.geometry import LineString, Point, Polygon

from ..geometry import chaikin
from ..schema import feature_seed
from .model import Settlement
from .naming import district_names
from .water import wall_runs

ROAD_TYPE = {"artery": "road", "ring": "road", "boulevard": "road", "bridge": "road", "approach": "road", "street": "road"}
LANDMARK_POI = {"square": "landmark", "market": "landmark", "temple": "landmark", "docks": "landmark",
                "citadel": "landmark", "fortress": "landmark"}   # `landmark` is never an extra node for `flowgraph check --strict`


def num(v: float) -> str:
    return ("%.2f" % v).rstrip("0").rstrip(".") or "0"


def _p(p) -> str:
    return "[%s, %s]" % (num(p[0]), num(p[1]))


def _pts(ps) -> str:
    return "[" + ", ".join(_p(p) for p in ps) + "]"


def road_path(s: Settlement, road) -> List[str]:
    """The road's `path` tokens: a node's id (a point reference) where the polyline vertex is a node, else the vertex
    coordinates. Flowgraph edge derivation reads the references, so consecutive nodes along the road are the edges."""
    return [tok for tok, _ in road_tokens(s, road)]


def road_tokens(s: Settlement, road) -> List[Tuple[str, Tuple[float, float]]]:
    """`road_path` with the position each token stands for (a node's position, or the literal vertex): (token, (x, y))."""
    node_at = {n.id: n.at for n in s.graph_nodes}
    if "warp" in s.params:
        return _warped_road_path(s, road, node_at)
    by_at = {}
    for nid in road.nodes:
        by_at.setdefault(tuple(node_at[nid]), nid)
    out: List[Tuple[str, Tuple[float, float]]] = []
    for v in road.polyline:
        nid = by_at.get(tuple(v))
        out.append((nid if nid else _p(v), (v[0], v[1])))
    return out


SIMPLIFY_ROAD = 0.8      # m, Douglas-Peucker tolerance between a road's nodes (warped settlements)
SIMPLIFY_REGION = 0.5    # m, tolerance of the emitted district rings (warped settlements)
FOREIGN_NODE = 30.0      # m: a literal vertex this close to a node of another road resolves to that node in flowgraph derivation
KEEP_WITHIN = 3.0        # m: a dropped vertex may move the line at most this far


def _warped_road_path(s: Settlement, road, node_at) -> List[Tuple[str, Tuple[float, float]]]:
    """Path tokens of a densified road (TOWN_ORGANIC_SPEC section 6.5): node ids at the road's nodes; between consecutive nodes the
    Douglas-Peucker simplified vertices as coordinates, except a vertex whose nearest choke node within 30 m belongs to no node of
    this road (flowgraph would bind it there): it is dropped when the line stays within 3 m of the full one, else replaced by the nearer
    of the two bounding node references (consecutive duplicates are removed, so no edge appears)."""
    node_idx = {}
    last = -1
    for nid in road.nodes:
        j = next(k for k in range(last + 1, len(road.polyline)) if list(road.polyline[k]) == list(node_at[nid]))
        node_idx[j] = nid
        last = j
    marks = sorted(set([0, len(road.polyline) - 1] + list(node_idx)))

    def tok(j: int) -> Tuple[str, Tuple[float, float]]:
        return (node_idx.get(j) or _p(road.polyline[j]), (road.polyline[j][0], road.polyline[j][1]))
    own = set(road.nodes)
    chokes = [(n.id, n.at) for n in s.graph_nodes if n.kind == "choke"]
    out: List[Tuple[str, Tuple[float, float]]] = [tok(marks[0])]
    for i0, i1 in zip(marks, marks[1:]):
        full = road.polyline[i0:i1 + 1]
        a, b = node_idx.get(i0), node_idx.get(i1)
        simp = [tuple(c) for c in LineString(full).simplify(SIMPLIFY_ROAD).coords] if len(full) > 2 else [tuple(p) for p in full]
        kept = list(simp)
        res: List[Tuple[str, Tuple[float, float]]] = []
        for v in simp[1:-1]:
            near = min((((v[0] - at[0]) ** 2 + (v[1] - at[1]) ** 2) ** 0.5, nid) for nid, at in chokes) if chokes else (1e9, "")
            if near[0] > FOREIGN_NODE or near[1] in own:
                res.append((_p(v), v))
                continue
            trial = [q for q in kept if q != v]
            if max(LineString(trial).distance(Point(p)) for p in full) <= KEEP_WITHIN:
                kept = trial
                continue
            da = ((v[0] - simp[0][0]) ** 2 + (v[1] - simp[0][1]) ** 2) ** 0.5
            db = ((v[0] - simp[-1][0]) ** 2 + (v[1] - simp[-1][1]) ** 2) ** 0.5
            ref = (a if da <= db else b) if (a and b) else (a or b)
            res.append((ref, (node_at[ref][0], node_at[ref][1])) if ref else (_p(v), v))
        for t_ in res + [tok(i1)]:
            if t_[0] != out[-1][0]:
                out.append(t_)
    return out


def region_ring(s: Settlement, d) -> List[List[float]]:
    """District outline for its `town` region; a warped (densified) settlement emits the ring simplified to 0.5 m."""
    pts = region_points(s, d)
    if "warp" not in s.params:
        return pts
    g = Polygon(pts).simplify(SIMPLIFY_REGION, preserve_topology=True)
    if g.is_empty or g.geom_type != "Polygon":
        return pts
    out = [[round(x, 2), round(y, 2)] for x, y in list(g.exterior.coords)[:-1]]
    return out if len(out) >= 3 else pts


def wall_gate_types():
    """(line type of walls, crossing type of gates): always `wall` and `gate`."""
    return "wall", "gate"


def _drawn_ring(wl):
    """The outer wall as drawn: each solid run Chaikin-smoothed, the open-shore stretches between runs left straight."""
    runs = wall_runs(wl)
    pts = [tuple(p) for p in wl.polyline[:-1]]
    n = len(pts)
    at = {p: i for i, p in enumerate(pts)}
    out = []
    for k, run in enumerate(runs):
        out.extend(tuple(q) for q in chaikin([tuple(p) for p in run]))
        if len(runs) > 1 or wl.open_shore:
            a, b = at.get(tuple(run[-1])), at.get(tuple(runs[(k + 1) % len(runs)][0]))
            if a is None or b is None:
                return None
            i = (a + 1) % n
            while a != b and i != b:
                out.append(pts[i])
                i = (i + 1) % n
    if len(out) < 3:
        return None
    g = Polygon(out)
    if not g.is_valid:
        g = g.buffer(0)
    return g if g.geom_type == "Polygon" and not g.is_empty else None


def region_points(s: Settlement, d) -> List[List[float]]:
    """District outline for its `town` region. Wall lines are drawn Chaikin-smoothed, which cuts the corners of the closed outer
    wall ring; the outline is clipped to that smoothed ring so no region pokes past the wall drawn in the map."""
    wl = next((w for w in s.walls if w.kind == "outer" and len(w.polyline) >= 4), None)
    if wl is None:
        return d.polygon
    ring = _drawn_ring(wl)
    if ring is None:
        return d.polygon
    dp = Polygon(d.polygon)
    if not ring.is_valid or not dp.is_valid or dp.difference(ring).area < 0.01:
        return d.polygon
    inter = dp.intersection(ring)
    parts = [g for g in getattr(inter, "geoms", [inter]) if g.geom_type == "Polygon" and not g.is_empty]
    if not parts:
        return d.polygon
    best = max(parts, key=lambda g: g.area)
    if best.area < 0.5 * dp.area:
        return d.polygon     # outside the wall (a detached fort): left as it is
    out = []
    for x, y in list(best.exterior.coords)[:-1]:
        q = [round(x, 2), round(y, 2)]
        if not out or out[-1] != q:
            out.append(q)
    return out if len(out) >= 3 else d.polygon


def emit_spec(s: Settlement) -> str:
    out: List[str] = []
    w = out.append
    w("# Fragment of a mapgen spec generated from settlement %s (generator %s, seed %d)." % (s.id, s.generator, s.seed))
    w("# Paste each list under the same key of the spec; every id starts with `%s_`. Coordinates are map metres." % s.id)
    w("# Districts are `town` regions, roads are `road` lines, the wall is a `wall` line, gates are `gate` crossings")
    w("# on `points` entries, squares, citadels and other landmarks are `landmark` POIs (change one to `keep_site` or `capture` to make it an objective).")
    if s.layers:
        w("# layers: %d subdivided districts, %d districts in all" % (len(s.layers), len(s.districts)))
    w("")
    w("points:")
    for n in s.graph_nodes:
        if n.kind == "choke":
            w("  %s: %s" % (n.id, _p(n.at)))
    w("")
    w("regions:")
    for b in s.water.get("bays", []):
        w("  - { id: %s, seed: %d, type: lake, shape: poly, points: %s, rough: 0, label: \"Bay\" }"
          % (b["id"], feature_seed(b["id"]), _pts(b["polygon"])))
    for isl in s.water.get("islands", []):   # a walkable `clearing` painted over the lake
        w("  - { id: %s, seed: %d, type: clearing, shape: poly, points: %s, rough: 0, label: \"Island\" }"
          % (isl["id"], feature_seed(isl["id"]), _pts(isl["polygon"])))
    names = district_names(s)      # only the 3-5 named top-level districts carry a `label`; the `label` key is omitted otherwise
    for d in s.districts:
        lab = ", label: %s" % json.dumps(names[d.id]) if d.id in names else ""
        w("  - { id: %s, seed: %d, type: town, shape: poly, points: %s, rough: 0%s }"
          % (d.id, feature_seed(d.id), _pts(region_ring(s, d)), lab))
    w("")
    w("lines:")
    for r in s.roads:
        w("  - { id: %s, seed: %d, type: road, path: [%s], width: %s, smooth: true }"
          % (r.id, feature_seed(r.id), ", ".join(road_path(s, r)), num(r.width)))
    for rv in s.water.get("rivers", []):
        w("  - { id: %s, seed: %d, type: river, path: %s, width: %s, smooth: true }"
          % (rv["id"], feature_seed(rv["id"]), _pts(rv["polyline"]), num(rv["width"])))
    for wl in s.walls:
        for k, run in enumerate(wall_runs(wl)):
            rid = wl.id if k == 0 else "%s_%d" % (wl.id, k + 1)
            w("  - { id: %s, seed: %d, type: wall, path: %s, width: %s }"
              % (rid, feature_seed(rid), _pts(run), num(wl.thickness)))
    w("")
    cross: List[str] = []
    for g in s.gates:
        if g.kind != "entry":      # an entry is a point in an open edge: no wall to cross
            cross.append("  - { id: %s_x, type: gate, at: %s }" % (g.id, g.id))
    rw = {r["id"]: r["width"] for r in s.water.get("rivers", [])}
    half = max(rw.values()) / 2.0 if rw else 0.0
    for r in s.roads:
        if r.cls == "bridge":
            mid = r.polyline[len(r.polyline) // 2]
            cross.append("  - { id: %s_x, type: bridge, at: %s, r: %s }" % (r.id, _p(mid), num(half + 15.0)))
    if cross:      # an empty `crossings:` key would be a null, not a list
        w("crossings:")
        out.extend(cross)
        w("")
    w("pois:")
    for l in s.landmarks:
        w("  - { id: %s, type: %s, at: %s, r: %s, label: %s }" % (l.id, LANDMARK_POI[l.kind], _p(l.at), num(l.r), json.dumps(l.label)))
    return "\n".join(out) + "\n"
