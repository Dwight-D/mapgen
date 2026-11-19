"""Flowgraph: load and validate a `*.flow.yaml` topology file (design: mapgen/FLOWGRAPH_SPEC.md)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import yaml

from .schema import SpecError, _SpecLoader

FORMAT_VERSIONS = (1,)
KINDS = ("spawn", "objective", "zone", "choke", "area")
TOP_KEYS = ("format_version", "name", "brief", "nodes", "edges")
NODE_KEYS = ("kind", "team", "name", "desc", "bind", "place")
PLACES = ("west", "east", "north", "south", "center", "north-west", "north-east", "south-west", "south-east")
EDGE_KEYS = ("link", "name", "desc")
_NODE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_NAME = re.compile(r"^[a-z0-9_]+$")


@dataclass
class Node:
    id: str
    kind: str = "area"
    team: Optional[str] = None
    name: str = ""
    desc: str = ""
    bind: str = ""
    place: Optional[str] = None


@dataclass
class Edge:
    a: str
    b: str
    name: str = ""
    desc: str = ""


@dataclass
class Flowgraph:
    name: str
    brief: str = ""
    nodes: List[Node] = field(default_factory=list)
    edges: List[Edge] = field(default_factory=list)


def _unknown(path: str, d: dict, allowed: Tuple[str, ...], errors: List[str]) -> None:
    for k in d:
        if k not in allowed:
            errors.append("%s: unknown key (allowed: %s)" % ("%s.%s" % (path, k) if path else str(k), ", ".join(allowed)))


def _text(path: str, v: Any, errors: List[str]) -> str:
    if v is None:
        return ""
    if not isinstance(v, str):
        errors.append("%s: expected a string, got %r" % (path, v))
        return ""
    return v.strip()


def _parse_nodes(raw: Any, errors: List[str]) -> List[Node]:
    nodes: List[Node] = []
    if not isinstance(raw, dict):
        errors.append("nodes: expected a mapping of node id -> properties, got %s" % type(raw).__name__)
        return nodes
    if not raw:
        errors.append("nodes: must declare at least one node")
    for nid, props in raw.items():
        p = "nodes.%s" % nid
        if not isinstance(nid, str) or not _NODE_ID.match(nid):
            errors.append("%s: node id must match [A-Za-z][A-Za-z0-9_]* (got %r)" % (p, nid))
            continue
        if props is None:
            props = {}
        if not isinstance(props, dict):
            errors.append("%s: expected a mapping, got %s" % (p, type(props).__name__))
            continue
        _unknown(p, props, NODE_KEYS, errors)
        kind = props.get("kind", "area")
        if kind not in KINDS:
            errors.append("%s.kind: expected one of %s, got %r" % (p, ", ".join(KINDS), kind))
            kind = "area"
        team = props.get("team")
        if kind == "spawn":
            if team is None:
                errors.append("%s.team: required for kind spawn" % p)
            elif not isinstance(team, str) or not team.strip():
                errors.append("%s.team: expected a non-empty string, got %r" % (p, team))
                team = None
        elif "team" in props:
            errors.append("%s.team: only allowed for kind spawn" % p)
            team = None
        bind = props.get("bind")
        if bind is not None and (not isinstance(bind, str) or not bind.strip()):
            errors.append("%s.bind: expected a non-empty string, got %r" % (p, bind))
            bind = None
        place = props.get("place")
        if place is not None and place not in PLACES:
            errors.append("%s.place: expected one of %s, got %r" % (p, ", ".join(PLACES), place))
            place = None
        if not bind:
            bind = "base_%s" % team if kind == "spawn" and team else nid.lower()
        nodes.append(Node(nid, kind, team if kind == "spawn" else None, _text(p + ".name", props.get("name"), errors),
                          _text(p + ".desc", props.get("desc"), errors), bind.strip(), place))
    return nodes


def _parse_link(path: str, link: Any, errors: List[str]) -> Optional[Tuple[str, str]]:
    if not isinstance(link, str):
        errors.append("%s: expected a string \"X - Y\", got %r" % (path, link))
        return None
    parts = [s.strip() for s in link.split("-")]
    if len(parts) != 2 or not parts[0] or not parts[1]:
        errors.append("%s: expected \"X - Y\", got %r" % (path, link))
        return None
    return parts[0], parts[1]


def _parse_edges(raw: Any, ids: set, errors: List[str]) -> List[Edge]:
    edges: List[Edge] = []
    if raw is None:
        return edges
    if not isinstance(raw, list):
        errors.append("edges: expected a list, got %s" % type(raw).__name__)
        return edges
    seen: Dict[frozenset, int] = {}
    for i, item in enumerate(raw):
        p = "edges[%d]" % i
        name = desc = ""
        if isinstance(item, dict):
            _unknown(p, item, EDGE_KEYS, errors)
            if "link" not in item:
                errors.append("%s.link: missing required key" % p)
                continue
            ends = _parse_link(p + ".link", item["link"], errors)
            name = _text(p + ".name", item.get("name"), errors)
            desc = _text(p + ".desc", item.get("desc"), errors)
        else:
            ends = _parse_link(p, item, errors)
        if ends is None:
            continue
        a, b = ends
        bad = False
        for e in (a, b):
            if e not in ids:
                errors.append("%s: undeclared node %r" % (p, e))
                bad = True
        if bad:
            continue
        if a == b:
            errors.append("%s: self-edge on %r" % (p, a))
            continue
        key = frozenset((a, b))
        if key in seen:
            errors.append("%s: duplicate edge %s - %s (first at edges[%d])" % (p, a, b, seen[key]))
            continue
        seen[key] = i
        edges.append(Edge(a, b, name, desc))
    return edges


def _components(nodes: List[Node], edges: List[Edge]) -> List[List[str]]:
    adj: Dict[str, List[str]] = {n.id: [] for n in nodes}
    for e in edges:
        adj[e.a].append(e.b)
        adj[e.b].append(e.a)
    seen: set = set()
    comps: List[List[str]] = []
    for n in nodes:
        if n.id in seen:
            continue
        comp, stack = [], [n.id]
        seen.add(n.id)
        while stack:
            cur = stack.pop()
            comp.append(cur)
            for nb in adj[cur]:
                if nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        comps.append(comp)
    return comps


def _validate(raw: Any) -> Tuple[Optional[Flowgraph], List[str]]:
    errors: List[str] = []
    if not isinstance(raw, dict):
        return None, [": expected a mapping, got %s" % type(raw).__name__]
    _unknown("", raw, TOP_KEYS, errors)
    if "format_version" not in raw:
        errors.append("format_version: missing required key")
    elif isinstance(raw["format_version"], bool) or raw["format_version"] not in FORMAT_VERSIONS:
        errors.append("format_version: unsupported value %r (flowgraph format versions: %s)" % (
            raw["format_version"], ", ".join(str(v) for v in FORMAT_VERSIONS)))
    name = raw.get("name")
    if name is None:
        errors.append("name: missing required key")
        name = ""
    elif not isinstance(name, str) or not _NAME.match(name):
        errors.append("name: must match [a-z0-9_]+ (got %r)" % (name,))
    brief = _text("brief", raw.get("brief"), errors)
    if "nodes" not in raw:
        errors.append("nodes: missing required key")
        nodes: List[Node] = []
    else:
        nodes = _parse_nodes(raw["nodes"], errors)
    edges = _parse_edges(raw.get("edges"), {n.id for n in nodes}, errors)

    spawns = [n for n in nodes if n.kind == "spawn"]
    teams = {n.team for n in spawns if n.team}
    if nodes and len(teams) < 2:
        errors.append("nodes: need at least two spawn nodes with distinct teams (got %d spawn(s), %d team(s))" % (
            len(spawns), len(teams)))
    by_bind: Dict[str, str] = {}
    for n in nodes:
        if n.bind in by_bind:
            errors.append("nodes.%s.bind: feature %r is already bound by node %s" % (n.id, n.bind, by_bind[n.bind]))
        else:
            by_bind[n.bind] = n.id
    if nodes and not errors:  # edges dropped by earlier errors would make connectivity misleading
        comps = _components(nodes, edges)
        if len(comps) > 1:
            errors.append("edges: graph is not connected (%d components: %s)" % (
                len(comps), "; ".join("{%s}" % ", ".join(c) for c in comps)))
    if errors:
        return None, errors
    return Flowgraph(name, brief, nodes, edges), []


def load_flowgraph(path: str) -> Flowgraph:
    """Load and validate a flowgraph file. Raises SpecError listing every problem found."""
    with open(path, "rb") as f:
        data = f.read()
    try:
        raw = yaml.load(data.decode("utf-8"), Loader=_SpecLoader)
    except yaml.YAMLError as e:
        raise SpecError(["<yaml>: %s" % str(e).replace("\n", " ")])
    g, errors = _validate(raw)
    if errors:
        raise SpecError(errors)
    return g
