"""Settlement -> flowgraph fragment (design: mapgen/TOWN_SPEC.md section 9).

A flowgraph needs two spawn nodes; the settlement has none, so the output is a fragment: the `nodes:` and `edges:`
entries of the movement graph, to merge into a flowgraph that adds spawns, approaches and the rest of the map.
Node ids equal the spec feature ids emitted by `emit_spec` (the default `bind`).
"""
from __future__ import annotations

import json
from typing import Dict, List

import math

from .model import Settlement
from .emit_spec import road_path


def emit_flow(s: Settlement) -> str:
    names: Dict[str, str] = {l.id: l.label for l in s.landmarks}
    for g in s.gates:
        names[g.id] = "Gate %s" % g.id.rsplit("_", 1)[1]
    out: List[str] = []
    w = out.append
    w("# Flowgraph fragment of settlement %s (generator %s, seed %d): the movement graph." % (s.id, s.generator, s.seed))
    w("# Merge `nodes` and `edges` into a *.flow.yaml that adds two spawn nodes and links them to gates.")
    w("# Kinds: zone = square or citadel, choke = gate or junction. Edge `name` is the road the edge runs along.")
    w("nodes:")
    for n in s.graph_nodes:
        nm = names.get(n.id)
        w("  %s: { kind: %s%s }" % (n.id, n.kind, ", name: %s" % json.dumps(nm) if nm else ""))
    w("")
    w("edges:")
    pairs = {tuple(sorted((e.a, e.b))): e.road for e in s.graph_edges}
    if s.layers:
        pairs = _derived(s)
    for (a, b), road in sorted(pairs.items()):
        w("  - { link: \"%s - %s\", name: %s }" % (a, b, road))
    return "\n".join(out) + "\n"


def _derived(s: Settlement) -> Dict[tuple, str]:
    """Edges `flowgraph check --strict` derives from the emitted lines (a literal vertex resolves to the nearest node within its
    reach: 30 m for a point, the landmark radius for a landmark). A layered settlement has many junction nodes closer together
    than that reach, so the literal vertices of a line resolve to neighbouring nodes; the fragment lists the edges the spec derives."""
    reach = {n.id: 30.0 for n in s.graph_nodes}
    for l in s.landmarks:
        if l.id in reach:
            reach[l.id] = max(l.r, 30.0)
    at = {n.id: n.at for n in s.graph_nodes}
    out: Dict[tuple, str] = {}
    for r in s.roads:
        seq: List[str] = []
        for tok, v in zip(road_path(s, r), r.polyline):
            nid = tok if tok in at else None
            if nid is None:
                best = None
                for cand in sorted(at):
                    d = math.hypot(v[0] - at[cand][0], v[1] - at[cand][1])
                    if d <= reach[cand] and (best is None or d < best[0]):
                        best = (d, cand)
                nid = best[1] if best else None
            if nid is not None and (not seq or seq[-1] != nid):
                seq.append(nid)
        for a, b in zip(seq, seq[1:]):
            out.setdefault(tuple(sorted((a, b))), r.id)
    return out
