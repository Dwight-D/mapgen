"""Flowgraph -> draft mapgen spec text (design: mapgen/FLOWGRAPH_SPEC.md section 5, `scaffold`).

The text comes from a fixed-order template, never a YAML round trip, so the same graph, size and layout give
byte-identical output.
"""
from __future__ import annotations

import json
import re
from typing import Dict, List, Tuple

from .flowgraph import Flowgraph, Node
from .flowgraph_layout import layout, team_order
from .schema import ID_MAX_LEN, _ID

DEFAULT_SIZE = (3000.0, 3000.0)
TEAM_COLORS = ("#2f6fdf", "#f2a81d", "#6a2cc0")   # blue, amber, purple: the palette's team defaults (lightness-separated)
ROAD_WIDTH = 8
POI_TYPE = {"objective": "capture", "zone": "capture", "area": "landmark"}
_TEAM = re.compile(r"^[A-Za-z0-9_]+$")


class ScaffoldError(Exception):
    def __init__(self, errors: List[str]):
        Exception.__init__(self, "; ".join(errors))
        self.errors = errors


def _one_line(s: str) -> str:
    return " ".join(s.split())


def _num(v: float) -> str:
    return "%g" % v


def _pt(p: Tuple[float, float]) -> str:
    return "[%s, %s]" % (_num(p[0]), _num(p[1]))


def _label(n: Node, key: str = "label") -> str:
    return ", %s: %s" % (key, json.dumps(_one_line(n.name))) if n.name else ""


def _describe(name: str, desc: str) -> str:
    return " - ".join(s for s in (_one_line(name), _one_line(desc)) if s)


def _valid_id(x: str) -> bool:
    return bool(_ID.fullmatch(x)) and len(x) <= ID_MAX_LEN


def _refs(g: Flowgraph) -> Dict[str, str]:
    """node id -> the spec feature id its edges reference."""
    errors: List[str] = []
    refs: Dict[str, str] = {}
    for n in g.nodes:
        if n.kind == "spawn":
            if not _TEAM.match(n.team or ""):
                errors.append("nodes.%s.team: scaffold needs a team id matching [A-Za-z0-9_]+ (got %r)" % (n.id, n.team))
            if n.bind != "base_%s" % n.team:
                errors.append("nodes.%s.bind: a spawn binds base_<team> (got %r)" % (n.id, n.bind))
        elif not _valid_id(n.bind):
            errors.append("nodes.%s.bind: spec feature id must match [a-z][a-z0-9_]* and be at most %d characters (got %r)"
                          % (n.id, ID_MAX_LEN, n.bind))
        refs[n.id] = n.bind
    if len(team_order(g)) > len(TEAM_COLORS):
        errors.append("nodes: a spec supports at most %d teams (got %d)" % (len(TEAM_COLORS), len(team_order(g))))
    if errors:
        raise ScaffoldError(errors)
    return refs


def _brief(g: Flowgraph) -> List[str]:
    lines: List[str] = []
    if g.brief:
        lines += g.brief.splitlines() + [""]
    else:
        lines += ["Draft scaffolded from flowgraph %s." % g.name, ""]
    lines.append("Flowgraph nodes:")
    for n in g.nodes:
        d = _describe(n.name, n.desc)
        lines.append("- %s (%s%s)%s" % (n.id, n.kind, ", team %s" % n.team if n.team else "", ": " + d if d else ""))
    if g.edges:
        lines.append("Flowgraph edges:")
        for e in g.edges:
            d = _describe(e.name, e.desc)
            lines.append("- %s - %s%s" % (e.a, e.b, ": " + d if d else ""))
    return lines


def scaffold(g: Flowgraph, size: Tuple[float, float] = DEFAULT_SIZE) -> str:
    """Draft spec text for `g` on a `size` map. Raises ScaffoldError when a node cannot become a valid spec feature."""
    refs = _refs(g)
    pos = layout(g, size[0], size[1])
    teams = team_order(g)
    out: List[str] = []
    w = out.append
    w("# TODO(agent): this is a scaffold drafted from flowgraph '%s'; positions are placeholders." % g.name)
    w("# TODO(agent): shape the terrain with regions, route the road lines along sensible paths, tune positions")
    w("# TODO(agent): and POI types, then keep `flowgraph check --strict` passing against the graph.")
    w("format_version: 1")
    w("name: %s" % g.name)
    w("brief: |")
    for ln in _brief(g):
        w(("  " + ln).rstrip())
    w("size: [%s, %s]" % (_num(size[0]), _num(size[1])))
    w("seed: 1")
    w("")
    w("teams:")
    for i, t in enumerate(teams):
        n = next(x for x in g.nodes if x.kind == "spawn" and x.team == t)
        d = _describe(n.name, n.desc)
        w("  # %s%s" % (n.id, ": " + d if d else ""))
        w("  %s: { base: %s, color: \"%s\"%s }" % (t, _pt(pos[n.id]), TEAM_COLORS[i], _label(n, "name")))
    chokes = [n for n in g.nodes if n.kind == "choke"]
    if chokes:
        w("")
        w("points:")
        for n in chokes:
            d = _describe(n.name, n.desc)
            w("  # %s%s" % (n.id, ": " + d if d else ""))
            w("  %s: %s" % (refs[n.id], _pt(pos[n.id])))
    pois = [n for n in g.nodes if n.kind in POI_TYPE]
    if pois:
        w("")
        w("pois:")
        for n in pois:
            d = _describe(n.name, n.desc)
            w("  # %s (%s)%s" % (n.id, n.kind, ": " + d if d else ""))
            w("  - { id: %s, type: %s, at: %s%s }" % (refs[n.id], POI_TYPE[n.kind], _pt(pos[n.id]), _label(n)))
    if g.edges:
        w("")
        w("lines:")
        taken = set(refs.values())
        for i, e in enumerate(g.edges):
            lid = "road_%s_%s" % (e.a.lower(), e.b.lower())
            if not _valid_id(lid) or lid in taken:
                lid = "road_%d" % i
                while lid in taken:
                    lid += "_"
            taken.add(lid)
            d = _describe(e.name, e.desc)
            w("  # %s - %s%s" % (e.a, e.b, ": " + d if d else ""))
            w("  - { id: %s, seed: %d, type: road, path: [%s, %s], width: %d }" % (lid, i, refs[e.a], refs[e.b], ROAD_WIDTH))
    return "\n".join(out) + "\n"
