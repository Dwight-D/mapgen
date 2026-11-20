"""Settlement -> YAML fragment of a map spec (design: mapgen/TOWN_SPEC.md section 9).

Fixed-order text template, never a YAML round trip: the same settlement gives byte-identical text. The fragment holds
the `points`, `regions`, `lines`, `crossings` and `pois` entries; the author pastes each list into the spec.
"""
from __future__ import annotations

import json
from typing import List

from ..schema import feature_seed, load_types
from .model import Settlement

ROAD_TYPE = {"artery": "road", "ring": "road", "boulevard": "road", "bridge": "road", "approach": "road"}
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
    node_at = {n.id: n.at for n in s.graph_nodes}
    by_at = {}
    for nid in road.nodes:
        by_at.setdefault(tuple(node_at[nid]), nid)
    out: List[str] = []
    for v in road.polyline:
        nid = by_at.get(tuple(v))
        out.append(nid if nid else _p(v))
    return out


def wall_gate_types():
    """(line type of walls, crossing type of gates): `wall` and `gate` when the type table has them, else the types
    that exist today, `cliff` (impassable, blocks sight) and `ramp` (walkable crossing)."""
    t = load_types()
    return ("wall" if "wall" in t.lines else "cliff"), ("gate" if "gate" in t.crossings else "ramp")


def emit_spec(s: Settlement) -> str:
    wall_type, gate_type = wall_gate_types()
    out: List[str] = []
    w = out.append
    w("# Fragment of a mapgen spec generated from settlement %s (generator %s, seed %d)." % (s.id, s.generator, s.seed))
    w("# Paste each list under the same key of the spec; every id starts with `%s_`. Coordinates are map metres." % s.id)
    w("# Districts are `town` regions, roads are `road` lines, the wall is a `%s` line, gates are `%s` crossings" % (wall_type, gate_type))
    w("# on `points` entries, squares, citadels and other landmarks are `landmark` POIs (change one to `keep_site` or `capture` to make it an objective).")
    w("")
    w("points:")
    for n in s.graph_nodes:
        if n.kind == "choke":
            w("  %s: %s" % (n.id, _p(n.at)))
    w("")
    w("regions:")
    for d in s.districts:
        w("  - { id: %s, seed: %d, type: town, shape: poly, points: %s, rough: 0, label: %s }"
          % (d.id, feature_seed(d.id), _pts(d.polygon), json.dumps(d.label)))
    w("")
    w("lines:")
    for r in s.roads:
        w("  - { id: %s, seed: %d, type: road, path: [%s], width: %s, smooth: true }"
          % (r.id, feature_seed(r.id), ", ".join(road_path(s, r)), num(r.width)))
    for wl in s.walls:
        w("  - { id: %s, seed: %d, type: %s, path: %s, width: %s }"
          % (wl.id, feature_seed(wl.id), wall_type, _pts(wl.polyline), num(wl.thickness)))
    w("")
    w("crossings:")
    for g in s.gates:
        w("  - { id: %s_x, type: %s, at: %s }" % (g.id, gate_type, g.id))
    w("")
    w("pois:")
    for l in s.landmarks:
        w("  - { id: %s, type: %s, at: %s, r: %s, label: %s }" % (l.id, LANDMARK_POI[l.kind], _p(l.at), num(l.r), json.dumps(l.label)))
    return "\n".join(out) + "\n"
