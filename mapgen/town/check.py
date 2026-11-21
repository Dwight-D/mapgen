"""Settlement invariants (design: mapgen/TOWN_SPEC.md section 8). Every check returns error strings; empty = pass."""
from __future__ import annotations

import math
from typing import Dict, List, Set

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

from .graph import OUTSIDE, SNAP
from .model import Settlement


def _geometry(s: Settlement) -> List[str]:
    errs: List[str] = []
    polys = []
    for d in s.districts:
        poly = Polygon(d.polygon)
        if not poly.is_valid:
            errs.append("district %s: invalid polygon" % d.id)
            continue
        if abs(poly.area - d.area) > 0.5:
            errs.append("district %s: area %g does not match its polygon (%g)" % (d.id, d.area, poly.area))
        minarea = s.params.get("min_district_area", 0.0)
        if d.depth > 0:
            minarea = s.params.get("layers", {}).get("min_area", minarea)
        if "warp" in s.params:
            minarea *= 0.7      # the warp changes areas by up to about 25 % (TOWN_ORGANIC_SPEC section 13)
        if d.area < minarea:
            errs.append("district %s: area %g below min_district_area %g" % (d.id, d.area, minarea))
        polys.append((d.id, poly))
    for i in range(len(polys)):
        for j in range(i + 1, len(polys)):
            a, b = polys[i], polys[j]
            if a[1].envelope.intersects(b[1].envelope) and a[1].intersection(b[1]).area > 1.0:
                errs.append("districts %s and %s overlap" % (a[0], b[0]))
    foot = Polygon(s.footprint)
    if not foot.is_valid:
        errs.append("footprint: invalid polygon")
    elif polys:
        if s.water:
            from .water import water_union
            foot = foot.difference(water_union(s))
        union = unary_union([Polygon(d.polygon) for d in s.districts if d.inside_wall and Polygon(d.polygon).is_valid])
        if abs(union.area - foot.area) > 0.01 * foot.area:
            errs.append("districts cover %.1f m2 of the %.1f m2 footprint (more than 1 %% apart)" % (union.area, foot.area))
        if foot.difference(union).area > 0.01 * foot.area or union.difference(foot).area > 0.01 * foot.area:
            errs.append("districts and footprint differ by more than 1 %")
    for x, y in s.footprint:
        if not (0.0 <= x <= s.size[0] and 0.0 <= y <= s.size[1]):
            errs.append("footprint vertex [%g, %g] lies outside the %gx%g map" % (x, y, s.size[0], s.size[1]))
            break
    return errs


def _walls_gates(s: Settlement) -> List[str]:
    errs: List[str] = []
    wall_lines = {}
    for w in s.walls:
        ln = LineString(w.polyline)
        if len(w.polyline) < 4 or w.polyline[0] != w.polyline[-1]:
            errs.append("wall %s: not a closed ring (first point must repeat at the end)" % w.id)
        elif not Polygon(w.polyline).is_valid:
            errs.append("wall %s: ring is not simple" % w.id)
        wall_lines[w.id] = ln
    road_lines = {r.id: LineString(r.polyline) for r in s.roads}
    outer = [w for w in s.walls if w.kind == "outer"]
    if s.walls and len(outer) != 1:
        errs.append("walls: expected exactly one outer wall, found %d" % len(outer))
    for w in outer:
        if sum(1 for g in s.gates if g.wall == w.id) < 2:
            errs.append("wall %s: fewer than 2 gates" % w.id)
    entries = [g for g in s.gates if g.wall == ""]
    if not outer and s.params.get("edge") in ("open", "fade") and len(entries) < 2:
        errs.append("wall-less town: fewer than 2 entries")
    ring = LineString(list(s.footprint) + [s.footprint[0]]) if s.footprint else None
    for g in s.gates:
        p = Point(g.at)
        if g.wall == "":
            if g.kind != "entry":
                errs.append("gate %s: no wall, so its kind must be entry (got %s)" % (g.id, g.kind))
            if ring is not None and ring.distance(p) > SNAP and s.params.get("edge") != "fade":
                errs.append("entry %s: %.2f m from the footprint ring (limit %g)" % (g.id, ring.distance(p), SNAP))
        elif g.wall not in wall_lines:
            errs.append("gate %s: unknown wall %s" % (g.id, g.wall))
        elif wall_lines[g.wall].distance(p) > SNAP:
            errs.append("gate %s: %.2f m from wall %s (limit %g)" % (g.id, wall_lines[g.wall].distance(p), g.wall, SNAP))
        if g.road not in road_lines:
            errs.append("gate %s: unknown road %s" % (g.id, g.road))
        elif road_lines[g.road].distance(p) > SNAP:
            errs.append("gate %s: %.2f m from road %s (limit %g)" % (g.id, road_lines[g.road].distance(p), g.road, SNAP))
        if abs(math.hypot(g.normal[0], g.normal[1]) - 1.0) > 0.02:
            errs.append("gate %s: normal is not a unit vector" % g.id)
    return errs


def _reachability(s: Settlement) -> List[str]:
    """Every district reachable from `outside` through the district graph (wall edges only through gates), every
    gate listed on a wall edge, and the movement graph connected with every gate in it."""
    errs: List[str] = []
    adj: Dict[str, Set[str]] = {n.id: set() for n in s.district_nodes}
    listed: Set[str] = set()
    for e in s.district_edges:
        listed.update(e.gates)
        if e.passable or e.gates:
            adj[e.a].add(e.b)
            adj[e.b].add(e.a)
    if OUTSIDE in adj:
        seen = {OUTSIDE}
        todo = [OUTSIDE]
        while todo:
            for m in sorted(adj[todo.pop()]):
                if m not in seen:
                    seen.add(m)
                    todo.append(m)
        for n in s.district_nodes:
            if n.id not in seen:
                errs.append("district %s is not reachable from outside through the gates" % n.id)
    for g in s.gates:
        if g.id not in listed and g.kind != "water_gate":
            errs.append("gate %s lies on no district-graph wall edge" % g.id)
    ids = {n.id for n in s.graph_nodes}
    madj: Dict[str, Set[str]] = {i: set() for i in ids}
    for e in s.graph_edges:
        if e.a not in ids or e.b not in ids:
            errs.append("movement edge %s: unknown node" % e.id)
            continue
        if e.passable:
            madj[e.a].add(e.b)
            madj[e.b].add(e.a)
    if ids:
        start = sorted(ids)[0]
        seen = {start}
        todo = [start]
        while todo:
            for m in sorted(madj[todo.pop()]):
                if m not in seen:
                    seen.add(m)
                    todo.append(m)
        for i in sorted(ids - seen):
            errs.append("movement graph: node %s is not connected to %s" % (i, start))
    for g in s.gates:
        if g.id not in ids:
            errs.append("gate %s is not a movement graph node" % g.id)
    return errs


def _identity(s: Settlement) -> List[str]:
    errs: List[str] = []
    allids = ([d.id for d in s.districts] + [r.id for r in s.roads] + [w.id for w in s.walls] + [g.id for g in s.gates] +
              [l.id for l in s.landmarks])
    nodeids = [n.id for n in s.graph_nodes]
    for what, ids in (("feature", allids), ("graph node", nodeids)):
        dup = sorted({i for i in ids if ids.count(i) > 1})
        if dup:
            errs.append("duplicate %s ids: %s" % (what, ", ".join(dup)))
    # a graph node may share its id with the gate or landmark it stands for, never with a district, road or wall
    clash = sorted(set(nodeids) & ({d.id for d in s.districts} | {r.id for r in s.roads} | {w.id for w in s.walls}))
    if clash:
        errs.append("graph node ids clash with district, road or wall ids: %s" % ", ".join(clash))
    for r in s.roads:
        for n in r.nodes:
            if n not in set(nodeids):
                errs.append("road %s: unknown node %s" % (r.id, n))
    return errs


BANK_TOL = 7.0    # m: a layer street ends at most this far from a river without a junction (water.BANK_MARGIN + slack)


def _layers(s: Settlement) -> List[str]:
    """Layer invariants (TOWN_LAYERS_SPEC section 7); applies only to a layered settlement."""
    if not s.layers and not any(d.depth > 0 for d in s.districts):
        return []
    errs: List[str] = []
    spec = s.params.get("layers", {})
    nodes = {n.id: n for n in s.layers}
    leaves = {d.id: d for d in s.districts}
    if len(leaves) > spec.get("max_districts", 10 ** 9):
        errs.append("layers: %d districts exceed max_districts %d" % (len(leaves), spec["max_districts"]))
    for n in s.layers:
        if n.parent and n.parent not in nodes:
            errs.append("layer node %s: unknown parent %s" % (n.id, n.parent))
        elif n.parent and nodes[n.parent].depth + 1 != n.depth:
            errs.append("layer node %s: depth %d is not parent depth + 1" % (n.id, n.depth))
        if n.parent and not n.id.startswith(n.parent + "_"):
            errs.append("layer node %s: id does not start with its parent id" % n.id)
        if n.depth >= spec.get("max_depth", 99):
            errs.append("layer node %s: depth %d reaches max_depth" % (n.id, n.depth))
        P = Polygon(n.polygon)
        kids = []
        for c in n.children:
            poly = leaves[c].polygon if c in leaves else (nodes[c].polygon if c in nodes else None)
            if poly is None:
                errs.append("layer node %s: unknown child %s" % (n.id, c))
            else:
                kids.append((c, Polygon(poly)))
        for i in range(len(kids)):
            for j in range(i + 1, len(kids)):
                if kids[i][1].envelope.intersects(kids[j][1].envelope) and kids[i][1].intersection(kids[j][1]).area > 1.0:
                    errs.append("layer node %s: children %s and %s overlap" % (n.id, kids[i][0], kids[j][0]))
        if kids and abs(unary_union([k[1] for k in kids]).area - P.area) > 0.01 * P.area:
            errs.append("layer node %s: children do not cover the node within 1 %%" % n.id)
    for d in s.districts:
        if d.depth > 0:
            if d.parent not in nodes:
                errs.append("district %s: unknown parent %s" % (d.id, d.parent))
            else:
                if not d.id.startswith(d.parent + "_"):
                    errs.append("district %s: id does not start with its parent id" % d.id)
                if nodes[d.parent].depth + 1 != d.depth:
                    errs.append("district %s: depth %d is not parent depth + 1" % (d.id, d.depth))
    lay = [r for r in s.roads if "_sr_" in r.id or "_bs_" in r.id]
    if lay:
        borders = unary_union([Polygon(d.polygon).boundary for d in s.districts]).buffer(0.5)
        walls = unary_union([LineString(w.polyline) for w in s.walls]) if s.walls else None
        rivers = [Polygon(rv["polygon"]) for rv in s.water.get("rivers", [])] if s.water else []
        river = unary_union(rivers) if rivers else None    # a street may end BANK_TOL short of the river (water.trim_banks)
        cmap: Dict[tuple, Set[str]] = {}
        for r in s.roads:
            for p in r.polyline:
                cmap.setdefault((p[0], p[1]), set()).add(r.id)
        uf: Dict[str, str] = {r.id: r.id for r in s.roads}

        def find(x: str) -> str:
            while uf[x] != x:
                uf[x] = uf[uf[x]]
                x = uf[x]
            return x
        for ids in cmap.values():
            for o in ids:
                uf[find(o)] = find(next(iter(ids)))
        main = {find(r.id) for r in s.roads if r not in lay}
        for r in lay:
            ln = LineString(r.polyline)
            if ln.difference(borders).length > 0.5:
                errs.append("road %s runs through a district interior" % r.id)
            if walls is not None and ln.intersection(walls.buffer(0.5)).length > 5.0:
                errs.append("road %s crosses a wall" % r.id)
            if find(r.id) not in main:
                errs.append("road %s is not connected to the parent road network" % r.id)
            if "_sr_" in r.id and r.polyline[0] != r.polyline[-1]:
                for e in (r.polyline[0], r.polyline[-1]):
                    if not (cmap[(e[0], e[1])] - {r.id}) and not (river is not None and river.distance(Point(e)) <= BANK_TOL):
                        errs.append("road %s: dangling end at %s" % (r.id, e))
    ids = [e.id for e in s.graph_edges]
    if len(ids) != len(set(ids)):
        errs.append("duplicate movement edge ids")
    return errs


UNSPLIT_RATIO = 3.0     # a leaf district above this multiple of the median leaf area is an unsplit blob
ORPHAN_LIMIT = 3.0      # ... or above this multiple of the median leaf area under its subdivided siblings (an orphan blob)
_EXEMPT = ("core", "citadel", "harbour", "outskirt", "plaza", "park", "fort")


def _leaf_balance(s: Settlement) -> List[str]:
    """No leaf far larger than the rest of a layered town (TOWN_ORGANIC_SPEC section 7.8): a leaf over UNSPLIT_RATIO times the median
    leaf area, or over ORPHAN_LIMIT times the median leaf area below its subdivided siblings, is a district the layers missed."""
    if not s.layers or not s.params.get("layers", {}).get("balance"):
        return []
    leaves = {d.id: d for d in s.districts}
    nodes = {n.id: n for n in s.layers}
    areas = sorted(d.area for d in s.districts if d.kind not in _EXEMPT)
    if len(areas) < 4:
        return []
    med = areas[len(areas) // 2]
    errs: List[str] = []
    for d in sorted(s.districts, key=lambda d: d.id):
        if d.kind in _EXEMPT:
            continue
        if d.area > UNSPLIT_RATIO * med:
            errs.append("district %s: unsplit leaf of %g m2 exceeds %g times the median leaf area %g" % (d.id, d.area, UNSPLIT_RATIO, med))
            continue
        par = nodes.get(d.parent)
        if par is None:
            continue
        sib: List[float] = []
        todo = [c for c in par.children if c in nodes]
        while todo:
            n = nodes[todo.pop()]
            for k in n.children:
                if k in nodes:
                    todo.append(k)
                elif k in leaves:
                    sib.append(leaves[k].area)
        if len(sib) >= 3:
            sm = sorted(sib)[len(sib) // 2]
            if d.area > ORPHAN_LIMIT * sm:
                errs.append("district %s: orphan leaf of %g m2 exceeds %g times the median %g of its subdivided siblings" % (
                    d.id, d.area, ORPHAN_LIMIT, sm))
    return errs


def _simple(line) -> bool:
    return LineString(line).is_simple


def _turn_ok(line, limit: float = 100.0) -> bool:
    for i in range(1, len(line) - 1):
        a = (line[i][0] - line[i - 1][0], line[i][1] - line[i - 1][1])
        b = (line[i + 1][0] - line[i][0], line[i + 1][1] - line[i][1])
        la, lb = math.hypot(*a), math.hypot(*b)
        if la < 1e-6 or lb < 1e-6:
            continue
        if math.degrees(math.acos(max(-1.0, min(1.0, (a[0] * b[0] + a[1] * b[1]) / (la * lb))))) > limit:
            return False
    return True


def _organic(s: Settlement) -> List[str]:
    """Invariants of the `organic` generator and of its warped result (TOWN_ORGANIC_SPEC section 9.3)."""
    if s.generator != "organic":
        return []
    from . import shape
    errs: List[str] = []
    warped = "warp" in s.params
    foot = Polygon(s.footprint)
    if foot.is_valid and foot.area > 0:
        ms = s.params.get("min_solidity", 0.62)
        if warped:
            ms = min(ms, 0.5)
        if shape.solidity(foot) < ms:
            errs.append("footprint solidity %.2f below %.2f" % (shape.solidity(foot), ms))
        if not shape.neck_ok(foot, 30.0 if warped else shape.NECK):
            errs.append("footprint has a neck narrower than %g m" % (30.0 if warped else shape.NECK))
    if not s.layers and len(s.districts) > 2:
        areas = sorted(d.area for d in s.districts if d.kind != "outskirt")
        med = areas[len(areas) // 2]
        for d in s.districts:
            if d.area > 6.0 * med and d.kind not in ("core", "citadel", "outskirt"):
                errs.append("district %s: area %g exceeds 6 times the median %g" % (d.id, d.area, med))
    for r in s.roads:
        if "_sr_" in r.id or "_bs_" in r.id:
            continue
        if not _simple(r.polyline):
            errs.append("road %s: polyline crosses itself" % r.id)
        elif not _turn_ok(r.polyline):
            errs.append("road %s: turn of more than 100 degrees at a vertex" % r.id)
    for d in s.districts:
        if not (0.0 < d.density <= 1.0):
            errs.append("district %s: density %g outside (0, 1]" % (d.id, d.density))
        if d.kind == "outskirt":
            poly = Polygon(d.polygon)
            if d.inside_wall:
                errs.append("district %s: outskirt must have inside_wall false" % d.id)
            elif not any(LineString(r.polyline).intersection(poly.boundary.buffer(0.3)).length > 1.0 for r in s.roads):
                errs.append("district %s: outskirt has no road on its border" % d.id)
    return errs


def check(s: Settlement) -> List[str]:
    """Geometry, walls and gates, reachability, identity, layers, leaf balance, organic. (Determinism: `check_determinism`.)"""
    from .water import check_water
    return _geometry(s) + _walls_gates(s) + _reachability(s) + _identity(s) + check_water(s) + _layers(s) + _leaf_balance(s) + _organic(s)


def check_determinism(s: Settlement) -> List[str]:
    """Regenerate from the settlement's own arguments and compare the canonical JSON byte for byte; also round trip it."""
    from .registry import generate
    errs: List[str] = []
    again = generate(s.generator, s.seed, s.size, s.params, s.id, s.centre)
    if again.to_json() != s.to_json():
        errs.append("regenerating %s from its own parameters gives different JSON" % s.id)
    if Settlement.from_json(s.to_json()).to_json() != s.to_json():
        errs.append("JSON round trip of %s is not stable" % s.id)
    return errs
