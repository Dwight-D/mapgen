"""Flowgraph: load and validate a `*.flow.yaml` topology file (design: mapgen/FLOWGRAPH_SPEC.md)."""
from __future__ import annotations

import json
import os
import re
import tempfile
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


def load_flowgraph_dict(raw: Any) -> Flowgraph:
    """Validate an already parsed graph document (the YAML mapping form). Raises SpecError listing every problem found."""
    g, errors = _validate(raw)
    if errors:
        raise SpecError(errors)
    return g


# ---- canonical emitter -------------------------------------------------------------------------------------------
# The canonical `.flow.yaml` text (what `flowgraph edit` saves; comments are not preserved):
#   format_version, name, brief (block scalar), nodes (one flow mapping per node, keys in NODE_KEYS order, ids and
#   kinds padded into columns), edges (`- A - B`, or `- { link: A - B, name: ..., desc: ... }` when named/described).
# Nodes and edges keep their list order. A bind equal to its default is omitted. Output uses LF endings.

_PLAIN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_ .'/()+-]*$")


def _scalar(s: str) -> str:
    """A YAML flow-context scalar for a string: plain when that reads back as the same string, else double-quoted."""
    if _PLAIN.match(s) and s == s.strip() and yaml.safe_load(s) == s and not s.endswith(":") and " - " not in s:
        return s
    return json.dumps(s, ensure_ascii=False)


def _default_bind(n: Node) -> str:
    return "base_%s" % n.team if n.kind == "spawn" and n.team else n.id.lower()


def graph_to_dict(g: Flowgraph) -> Dict[str, Any]:
    """JSON-friendly form of a graph (the editor's wire format): nodes and edges as ordered lists; default binds omitted."""
    return {
        "format_version": FORMAT_VERSIONS[-1], "name": g.name, "brief": g.brief,
        "nodes": [{"id": n.id, "kind": n.kind, "team": n.team or "", "name": n.name, "desc": n.desc,
                   "bind": "" if n.bind == _default_bind(n) else n.bind, "place": n.place or ""} for n in g.nodes],
        "edges": [{"a": e.a, "b": e.b, "name": e.name, "desc": e.desc} for e in g.edges],
    }


def dict_to_raw(d: Any) -> Dict[str, Any]:
    """Wire format (`graph_to_dict` shape) -> the parsed-YAML shape `load_flowgraph_dict` validates.
    Empty optional fields are dropped. Raises SpecError for a payload that is not that shape or repeats a node id."""
    errs: List[str] = []
    if not isinstance(d, dict) or not isinstance(d.get("nodes"), list) or not isinstance(d.get("edges"), list):
        raise SpecError(["graph: expected an object with node and edge lists"])
    nodes: Dict[str, Any] = {}
    for i, n in enumerate(d["nodes"]):
        if not isinstance(n, dict) or not isinstance(n.get("id"), str):
            errs.append("nodes[%d]: expected an object with a string id" % i)
            continue
        if n["id"] in nodes:
            errs.append("nodes.%s: duplicate node id" % n["id"])
            continue
        props: Dict[str, Any] = {}
        for k in NODE_KEYS:
            v = n.get(k)
            if v not in (None, ""):
                props[k] = v
        for k in n:
            if k != "id" and k not in NODE_KEYS:
                props[k] = n[k]  # surfaces as an unknown-key error
        nodes[n["id"]] = props
    edges: List[Any] = []
    for i, e in enumerate(d["edges"]):
        if not isinstance(e, dict) or not isinstance(e.get("a"), str) or not isinstance(e.get("b"), str):
            errs.append("edges[%d]: expected an object with string ends a and b" % i)
            continue
        item: Dict[str, Any] = {"link": "%s - %s" % (e["a"], e["b"])}
        for k in ("name", "desc"):
            if e.get(k) not in (None, ""):
                item[k] = e[k]
        edges.append(item)
    if errs:
        raise SpecError(errs)
    raw: Dict[str, Any] = {"format_version": d.get("format_version", FORMAT_VERSIONS[-1]), "nodes": nodes, "edges": edges}
    if "name" in d:
        raw["name"] = d["name"]
    if d.get("brief") not in (None, ""):
        raw["brief"] = d["brief"]
    return raw


def load_flowgraph_wire(d: Any) -> Flowgraph:
    """Validate the editor's wire format with the same rules as a file. Raises SpecError listing every problem found."""
    return load_flowgraph_dict(dict_to_raw(d))


def canonical_text(g: Flowgraph) -> str:
    """The canonical `.flow.yaml` text of a (valid) graph."""
    out: List[str] = ["format_version: %d" % FORMAT_VERSIONS[-1], "name: %s" % _scalar(g.name)]
    brief_lines = g.brief.split("\n")
    if g.brief and all(l == l.rstrip() and not l.startswith((" ", "\t")) and "\r" not in l and "\t" not in l for l in brief_lines) \
            and yaml.safe_load("x: |\n" + "\n".join("  " + l if l else "" for l in brief_lines) + "\n")["x"].strip() == g.brief:
        out.append("brief: |")
        out.extend("  " + l if l else "" for l in brief_lines)
    elif g.brief:
        out.append("brief: %s" % json.dumps(g.brief, ensure_ascii=False))
    out.append("")
    out.append("nodes:")
    idw = max(len(n.id) for n in g.nodes) + 1  # width of "<id>:"
    kw = len("kind: ") + max(len(n.kind) for n in g.nodes) + 1  # width of "kind: <kind>,"
    for n in g.nodes:
        fields = []
        if n.team:
            fields.append("team: %s" % _scalar(n.team))
        if n.name:
            fields.append("name: %s" % _scalar(n.name))
        if n.desc:
            fields.append("desc: %s" % _scalar(n.desc))
        if n.bind != _default_bind(n):
            fields.append("bind: %s" % _scalar(n.bind))
        if n.place:
            fields.append("place: %s" % n.place)
        kind = "kind: %s" % n.kind
        body = (kind + ",").ljust(kw) + " " + ", ".join(fields) if fields else kind
        out.append("  %s { %s }" % ((n.id + ":").ljust(idw + 1), body))
    out.append("")
    out.append("edges:")
    for e in g.edges:
        link = "%s - %s" % (e.a, e.b)
        if e.name or e.desc:
            extra = ["link: %s" % link] + (["name: %s" % _scalar(e.name)] if e.name else []) + (["desc: %s" % _scalar(e.desc)] if e.desc else [])
            out.append("  - { %s }" % ", ".join(extra))
        else:
            out.append("  - %s" % link)
    return "\n".join(out) + "\n"


def save_canonical(g: Flowgraph, path: str) -> int:
    """Write `canonical_text(g)` to `path` atomically (temp file in the same folder, then rename). Returns bytes written."""
    data = canonical_text(g).encode("utf-8")
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".flowgraph-", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    return len(data)
