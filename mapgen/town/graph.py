"""Graph derivation shared by every generator: the movement graph and the district graph (TOWN_SPEC.md section 5)."""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

from .model import (District, DistrictEdge, DistrictNode, Gate, GraphEdge, GraphNode, Road, Wall,
                    polyline_length, r2)

OUTSIDE = "outside"
BORDER_MIN = 1.0          # metres of shared boundary that make two districts adjacent
SNAP = 1.0                # metres: a gate or node counts as lying on a line / boundary within this distance


def district_polygon(d: District) -> Polygon:
    return Polygon(d.polygon)


def movement_edges(roads: List[Road], nodes: Dict[str, GraphNode]) -> List[GraphEdge]:
    """One edge per pair of consecutive nodes along each road polyline. A node sits on a polyline vertex (same
    coordinates); `Road.nodes` lists them in travel order, and a closed ring repeats its first node at the end."""
    edges: List[GraphEdge] = []
    seen = set()
    for road in roads:
        idx: List[int] = []
        last = -1
        for nid in road.nodes:
            at = nodes[nid].at
            j = next((k for k in range(last + 1, len(road.polyline)) if road.polyline[k] == at), None)
            if j is None:
                raise ValueError("road %s: node %s is not a vertex after index %d" % (road.id, nid, last))
            idx.append(j)
            last = j
        for k in range(len(idx) - 1):
            a, b = road.nodes[k], road.nodes[k + 1]
            if a == b:
                continue
            eid = "%s~%s" % tuple(sorted((a, b)))
            if eid in seen:
                raise ValueError("duplicate movement edge %s (road %s)" % (eid, road.id))
            seen.add(eid)
            length = polyline_length(road.polyline[idx[k]:idx[k + 1] + 1])
            edges.append(GraphEdge(id=eid, a=a, b=b, cls=road.cls, road=road.id, length=r2(length)))
    return sorted(edges, key=lambda e: e.id)


def attach_districts(nodes: List[GraphNode], districts: List[District]) -> None:
    """Fill GraphNode.districts: ids of the districts whose polygon contains or touches the node (within SNAP)."""
    polys = [(d.id, district_polygon(d)) for d in districts]
    for n in nodes:
        p = Point(n.at)
        n.districts = sorted(did for did, poly in polys if poly.distance(p) <= SNAP)


def district_graph(districts: List[District], roads: List[Road], walls: List[Wall],
                   gates: List[Gate], footprint: Optional[List[List[float]]] = None) -> Tuple[List[DistrictNode], List[DistrictEdge]]:
    """Nodes: every district plus `outside`. Edge: two districts sharing >= BORDER_MIN m of boundary (kind
    `adjacent`, passable) or, where a wall polyline lies on that boundary, kind `wall` (passable only through the
    listed gates); a district whose boundary lies on an `outer` wall is joined to `outside` the same way.
    A wall-less town (some gate has `wall == ""`, an entry) has no outer wall: every district whose boundary lies on the
    footprint ring (`footprint`, default the outline of the `inside_wall` districts) is joined to `outside` by an `adjacent`
    passable edge listing the roads along the border and the entries within SNAP (TOWN_ORGANIC_SPEC section 9.2)."""
    polys = [(d.id, district_polygon(d)) for d in districts]
    road_lines = [(r.id, LineString(r.polyline)) for r in roads]
    wall_lines = [(w, LineString(w.polyline)) for w in walls]
    gate_pts = [(g.id, Point(g.at)) for g in gates]
    nodes = [DistrictNode(id=d.id, kind="district") for d in districts] + [DistrictNode(id=OUTSIDE, kind="outside")]
    edges: List[DistrictEdge] = []

    def along(border) -> Tuple[List[str], bool, List[str]]:
        rids = sorted(rid for rid, ln in road_lines if ln.intersection(border).length > BORDER_MIN)
        walled = any(ln.intersection(border).length > BORDER_MIN for w, ln in wall_lines)
        gids = sorted(gid for gid, p in gate_pts if p.distance(border) <= SNAP) if walled else []
        return rids, walled, gids

    for i in range(len(polys)):
        ida, pa = polys[i]
        for j in range(i + 1, len(polys)):
            idb, pb = polys[j]
            if not pa.envelope.intersects(pb.envelope):
                continue
            border = pa.boundary.intersection(pb.boundary)
            length = border.length
            if length <= BORDER_MIN:
                continue
            rids, walled, gids = along(border)
            a, b = sorted((ida, idb))
            edges.append(DistrictEdge(id="%s~%s" % (a, b), a=a, b=b, kind="wall" if walled else "adjacent",
                                      length=r2(length), passable=not walled, gates=gids, roads=rids))
    outer = [(w, ln) for w, ln in wall_lines if w.kind == "outer"]
    for did, poly in polys:
        for w, ln in outer:
            border = poly.boundary.intersection(ln)
            if border.length > BORDER_MIN:
                rids = sorted(rid for rid, rl in road_lines if rl.intersection(border).length > BORDER_MIN)
                gids = sorted(gid for gid, p in gate_pts if p.distance(border) <= SNAP)
                edges.append(DistrictEdge(id="%s~%s" % (did, OUTSIDE), a=did, b=OUTSIDE, kind="wall",
                                          length=r2(border.length), passable=False, gates=gids, roads=rids))
                break
    if not outer and any(g.wall == "" for g in gates):
        if footprint:
            ring = LineString(list(footprint) + [footprint[0]])
        else:
            ring = unary_union([p for d, (_, p) in zip(districts, polys) if d.inside_wall]).boundary
        for did, poly in polys:
            border = poly.boundary.intersection(ring)
            if border.length > BORDER_MIN:
                rids = sorted(rid for rid, rl in road_lines if rl.intersection(border).length > BORDER_MIN)
                gids = sorted(gid for gid, p in gate_pts if p.distance(border) <= SNAP)
                edges.append(DistrictEdge(id="%s~%s" % (did, OUTSIDE), a=did, b=OUTSIDE, kind="adjacent",
                                          length=r2(border.length), passable=True, gates=gids, roads=rids))
        done = {e.a for e in edges if e.b == OUTSIDE}
        for d, (did, poly) in zip(districts, polys):       # fringe districts (edge=fade) lie outside the footprint
            if d.inside_wall or did in done:
                continue
            near = [q.boundary for oid, q in polys if oid != did and q.envelope.intersects(poly.envelope)]
            border = poly.boundary.difference(unary_union(near)) if near else poly.boundary
            if border.length <= BORDER_MIN:
                border = poly.boundary
            rids = sorted(rid for rid, rl in road_lines if rl.intersection(poly.boundary).length > BORDER_MIN)
            gids = sorted(gid for gid, p in gate_pts if p.distance(border) <= SNAP)
            edges.append(DistrictEdge(id="%s~%s" % (did, OUTSIDE), a=did, b=OUTSIDE, kind="adjacent",
                                      length=r2(border.length), passable=True, gates=gids, roads=rids))
    return nodes, sorted(edges, key=lambda e: e.id)
