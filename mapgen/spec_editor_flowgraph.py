"""Spec editor, flowgraph side (slice 5). Design: docs/investigations/2026-11-19-map-editor-design.md section 8.

The editor only READS a flowgraph: `FlowgraphSource` loads the `*.flow.yaml` file (never writes it; errors are data, never
fatal) and `overlay` turns the graph plus the spec buffer into the JSON the page draws: node discs and positions, edges
with their satisfied / missing / extra status, structured check findings and the nodes whose bound feature is absent.
The check runs in-process on the buffer (`flowgraph_check.check` with the buffer's raw line paths): no file is read or written.
"""
from __future__ import annotations

import os
import re
import threading
from typing import Any, Dict, List, Optional, Tuple

from . import flowgraph, flowgraph_check, schema
from .flowgraph import Flowgraph

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_EDGE = re.compile(r"^(?:(terrain)-)?(missing|extra) edge (\S+) - (\S+)")
_NODE = re.compile(r"^missing node (\S+) \((\w+)")
_XNODE = re.compile(r"^extra node (\S+) ")


def find_flowgraph(spec_path: str, root: str = ROOT) -> Optional[str]:
    """`<root>/flowgraphs/<spec name>.flow.yaml` or, for a name ending `_v<N>`, `<name minus _vN>.flow.yaml`. The path when
    exactly one of the two candidates exists, else None (none found, or both: ambiguous)."""
    name = os.path.splitext(os.path.basename(spec_path))[0]
    cands = [name]
    m = re.match(r"^(.*)_v\d+$", name)
    if m and m.group(1):
        cands.append(m.group(1))
    found = [os.path.join(root, "flowgraphs", c + ".flow.yaml") for c in cands]
    found = [p for p in found if os.path.isfile(p)]
    return found[0] if len(found) == 1 else None


class FlowgraphSource:
    """A flowgraph file, loaded read-only and re-read when its size or mtime changes (the graph editor may be saving it)."""

    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        self._lock = threading.Lock()
        self._key = None  # type: Any
        self.graph = None  # type: Optional[Flowgraph]
        self.errors = []  # type: List[str]
        self.reload()

    def reload(self) -> None:
        with self._lock:
            try:
                st = os.stat(self.path)
                key = (st.st_mtime_ns, st.st_size)
            except OSError as e:
                key = ("missing", str(e))
            if key == self._key:
                return
            self._key = key
            self.graph, self.errors = None, []
            try:
                self.graph = flowgraph.load_flowgraph(self.path)
            except schema.SpecError as e:
                self.errors = list(e.errors)
            except (OSError, UnicodeDecodeError) as e:
                self.errors = ["cannot read %s: %s" % (self.path, e)]

    def info(self) -> Dict[str, Any]:
        self.reload()
        return {"path": self.path, "name": self.graph.name if self.graph else None, "errors": list(self.errors)}


def _finding(kind: str, text: str, nodes: List[str], edge: Optional[List[str]] = None, feature: Optional[str] = None) -> Dict[str, Any]:
    return {"kind": kind, "text": text, "nodes": nodes, "edge": edge, "feature": feature,
            "strict_only": kind in ("extra_edge", "extra_node")}


def findings_of(res: "flowgraph_check.CheckResult") -> List[Dict[str, Any]]:
    """Structured form of a CheckResult's lines (kind, text, the node ids / edge / spec feature each is about)."""
    out = []  # type: List[Dict[str, Any]]
    for kind, lines in (("missing_node", res.missing_nodes), ("missing_edge", res.missing_edges), ("extra_edge", res.extra_edges),
                        ("extra_node", res.extra_nodes), ("terrain_missing", res.terrain_missing), ("terrain_extra", res.terrain_extra)):
        for t in lines:
            if kind == "missing_node":
                m = _NODE.match(t)
                out.append(_finding(kind, t, [m.group(2)] if m else []))
            elif kind == "extra_node":
                m = _XNODE.match(t)
                out.append(_finding(kind, t, [], None, m.group(1) if m else None))
            else:
                m = _EDGE.match(t)
                out.append(_finding(kind, t, [m.group(3), m.group(4)] if m else [], [m.group(3), m.group(4)] if m else None))
    for t in res.notes:
        out.append(_finding("note", t, []))
    return out


def overlay(g: Flowgraph, spec: schema.MapSpec, raw_doc: Any) -> Dict[str, Any]:
    """Everything the page draws for graph `g` over `spec` (whose parsed document is `raw_doc`). No terrain."""
    raw_paths = flowgraph_check.raw_line_paths_doc(raw_doc)
    feats = flowgraph_check.spec_features(spec)
    res = flowgraph_check.check(g, spec, False, raw_paths)
    derived = flowgraph_check.derive_edges(g, spec, raw_paths)
    nodes, unbound, pos = [], [], {}
    for n in g.nodes:
        f = feats.get(n.bind)
        rec = {"id": n.id, "kind": n.kind, "team": n.team, "name": n.name, "bind": n.bind, "bound": f is not None,
               "at": [f.at[0], f.at[1]] if f else None, "r": f.r if f else None}
        nodes.append(rec)
        if f is None:
            unbound.append(n.id)
        else:
            pos[n.id] = f.at
    edges, wanted = [], set()
    for e in g.edges:
        key = frozenset((e.a, e.b))
        wanted.add(key)
        edges.append({"a": e.a, "b": e.b, "name": e.name, "status": "ok" if key in derived else "missing",
                      "drawn": e.a in pos and e.b in pos})
    order = {n.id: i for i, n in enumerate(g.nodes)}
    for key in sorted(derived - wanted, key=lambda k: sorted(order[x] for x in k)):
        a, b = sorted(key, key=lambda x: order[x])
        edges.append({"a": a, "b": b, "name": "", "status": "extra", "drawn": True})
    fnd = findings_of(res)
    return {"name": g.name, "nodes": nodes, "edges": edges, "unbound": unbound, "findings": fnd,
            "problems": sum(1 for f in fnd if f["kind"] != "note" and not f["strict_only"]),
            "problems_strict": sum(1 for f in fnd if f["kind"] != "note")}


def terrain(g: Flowgraph, spec: schema.MapSpec) -> List[Dict[str, Any]]:
    """Terrain-aware findings (`check --terrain`); costs seconds."""
    from .flowgraph_terrain import terrain_findings

    missing, extra, notes = terrain_findings(g, spec)
    res = flowgraph_check.CheckResult(terrain=True, terrain_missing=missing, terrain_extra=extra, notes=notes)
    return findings_of(res)


def rename_warnings(g: Optional[Flowgraph], results: List[Dict[str, Any]], path: str) -> List[str]:
    """Warnings for renames that leave a flowgraph node unbound (the graph is never rewritten)."""
    out = []  # type: List[str]
    if g is None:
        return out
    for r in results:
        old, new = r["renamed"][0], r["renamed"][1]
        for n in g.nodes:
            if n.bind == old:
                out.append("flowgraph node %s (%s) binds '%s', which is now '%s': the node is unbound. The graph file is not "
                           "changed; set `bind: %s` on node %s in `mapgen flowgraph edit %s`." % (n.id, n.name or n.kind, old, new, new, n.id, path))
    return out
