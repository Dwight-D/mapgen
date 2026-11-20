"""Settlement -> flowgraph fragment (design: mapgen/TOWN_SPEC.md section 9).

A flowgraph needs two spawn nodes; the settlement has none, so the output is a fragment: the `nodes:` and `edges:`
entries of the movement graph, to merge into a flowgraph that adds spawns, approaches and the rest of the map.
Node ids equal the spec feature ids emitted by `emit_spec` (the default `bind`).
"""
from __future__ import annotations

import json
from typing import Dict, List

from .model import Settlement


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
    for e in s.graph_edges:
        w("  - { link: \"%s - %s\", name: %s }" % (e.a, e.b, e.road))
    return "\n".join(out) + "\n"
