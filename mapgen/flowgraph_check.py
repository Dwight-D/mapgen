"""Flowgraph vs spec: bound-feature lookup, edge derivation and `check` (design: mapgen/FLOWGRAPH_SPEC.md sections 3-5)."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

import yaml

from .flowgraph import Flowgraph
from .schema import MapSpec, SpecError, _SpecLoader, load_spec

WAYPOINT_R = 30.0
MIN_R = 30.0
TRAVERSABLE = ("road", "trail")


@dataclass
class Feature:
    id: str
    at: Tuple[float, float]
    r: float
    poi_type: Optional[str]   # None for `points` entries


def spec_features(spec: MapSpec) -> Dict[str, Feature]:
    """Every feature a node can bind to: POIs, `base_<team>`, `points` entries (waypoint radius 30 m)."""
    feats: Dict[str, Feature] = {}
    for p in spec.all_pois():
        feats[p.id] = Feature(p.id, p.at, max(p.r, MIN_R), p.type)
    for name, pt in spec.points.items():
        feats.setdefault(name, Feature(name, pt, WAYPOINT_R, None))
    return feats


def raw_line_paths_text(text: str) -> Dict[int, list]:
    """Line list position -> the line's `path` exactly as written (strings are point references), from spec text."""
    return raw_line_paths_doc(yaml.load(text, Loader=_SpecLoader))


def raw_line_paths_doc(raw) -> Dict[int, list]:
    """As `raw_line_paths_text`, from the already parsed document."""
    out: Dict[int, list] = {}
    lines = raw.get("lines") if isinstance(raw, dict) else None
    if isinstance(lines, list):
        for i, ln in enumerate(lines):
            if isinstance(ln, dict) and isinstance(ln.get("path"), list):
                out[i] = ln["path"]
    return out


def _raw_line_paths(spec_path: str) -> Dict[int, list]:
    with open(spec_path, "rb") as f:
        return raw_line_paths_text(f.read().decode("utf-8"))


def derive_edges(g: Flowgraph, spec: MapSpec, raw_paths: Optional[Dict[int, list]] = None) -> Set[frozenset]:
    """Present edges (frozensets of two node ids) derived from the spec's road/trail lines. Only nodes whose
    bound feature exists in the spec take part. `raw_paths` (`raw_line_paths_text` / `raw_line_paths_doc` of the spec
    text) replaces the read of the spec file, so an in-memory buffer can be checked without touching disk."""
    feats = spec_features(spec)
    by_bind = {n.bind: n.id for n in g.nodes if n.bind in feats}
    bound = [(nid, feats[b]) for b, nid in by_bind.items()]
    if raw_paths is None:
        raw_paths = _raw_line_paths(spec.spec_path)
    edges: Set[frozenset] = set()
    for ln in spec.lines:
        if ln.type not in TRAVERSABLE:
            continue
        raw = raw_paths.get(ln.index, [])
        seq: List[str] = []
        for j, pt in enumerate(ln.path):
            tok = raw[j] if j < len(raw) else None
            if isinstance(tok, str):
                nid = by_bind.get(tok)
            else:
                nid, best = None, None
                for cand, f in bound:
                    d = math.hypot(pt[0] - f.at[0], pt[1] - f.at[1])
                    if d <= f.r and (best is None or d < best):
                        nid, best = cand, d
            if nid is not None and (not seq or seq[-1] != nid):
                seq.append(nid)
        for a, b in zip(seq, seq[1:]):
            edges.add(frozenset((a, b)))
    return edges


@dataclass
class CheckResult:
    missing_nodes: List[str] = field(default_factory=list)
    missing_edges: List[str] = field(default_factory=list)
    extra_edges: List[str] = field(default_factory=list)
    extra_nodes: List[str] = field(default_factory=list)
    terrain: bool = False                      # --terrain was requested
    terrain_missing: List[str] = field(default_factory=list)
    terrain_extra: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def failed(self, strict: bool) -> bool:
        return bool(self.missing_nodes or self.missing_edges or (strict and (self.extra_edges or self.extra_nodes))
                    or self.terrain_missing or self.terrain_extra)

    def lines(self, strict: bool) -> List[str]:
        out = list(self.missing_nodes) + list(self.missing_edges)
        if strict:
            out += self.extra_edges + self.extra_nodes
        out += self.terrain_missing + self.terrain_extra + self.notes
        summary = "%d missing node(s), %d missing edge(s)" % (len(self.missing_nodes), len(self.missing_edges))
        if strict:
            summary += ", %d extra edge(s), %d extra node(s)" % (len(self.extra_edges), len(self.extra_nodes))
        if self.terrain:
            summary += ", %d terrain-missing edge(s), %d terrain-extra edge(s)" % (
                len(self.terrain_missing), len(self.terrain_extra))
        out.append("%s: %s" % ("FAIL" if self.failed(strict) else "OK", summary))
        return out


def _label(n) -> str:
    return "%s: %s" % (n.id, n.name) if n.name else n.id


def check(g: Flowgraph, spec: MapSpec, terrain: bool = False, raw_paths: Optional[Dict[int, list]] = None) -> CheckResult:
    feats = spec_features(spec)
    res = CheckResult()
    for n in g.nodes:
        if n.bind not in feats:
            res.missing_nodes.append("missing node %s (%s)" % (n.bind, _label(n)))
    derived = derive_edges(g, spec, raw_paths)
    wanted = set()
    for e in g.edges:
        key = frozenset((e.a, e.b))
        wanted.add(key)
        if key not in derived:
            res.missing_edges.append("missing edge %s - %s%s" % (e.a, e.b, " (%s)" % e.name if e.name else ""))
    order = {n.id: i for i, n in enumerate(g.nodes)}
    for key in sorted(derived - wanted, key=lambda k: sorted(order[x] for x in k)):
        a, b = sorted(key, key=lambda x: order[x])
        res.extra_edges.append("extra edge %s - %s" % (a, b))
    bound = {n.bind for n in g.nodes}
    for fid, f in feats.items():
        if f.poi_type is None or f.poi_type == "landmark" or fid in bound:
            continue
        res.extra_nodes.append("extra node %s (%s)" % (fid, f.poi_type))
    if terrain:
        from .flowgraph_terrain import terrain_findings

        res.terrain = True
        res.terrain_missing, res.terrain_extra, res.notes = terrain_findings(g, spec)
    return res


def check_files(graph_path: str, spec_path: str) -> CheckResult:
    from .flowgraph import load_flowgraph

    return check(load_flowgraph(graph_path), load_spec(spec_path))
