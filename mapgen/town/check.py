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
        if d.area < s.params.get("min_district_area", 0.0):
            errs.append("district %s: area %g below min_district_area %g" % (d.id, d.area, s.params["min_district_area"]))
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
        union = unary_union([p for _, p in polys])
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
    for g in s.gates:
        p = Point(g.at)
        if g.wall not in wall_lines:
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
        if g.id not in listed:
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


def check(s: Settlement) -> List[str]:
    """Geometry, walls and gates, reachability, identity. (Determinism: `check_determinism`.)"""
    return _geometry(s) + _walls_gates(s) + _reachability(s) + _identity(s)


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
