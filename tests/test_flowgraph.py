"""Flowgraph graph loading and validation: expected messages, plus `flowgraph validate` exit codes."""
import os

import pytest

from mapgen.flowgraph import load_flowgraph
from mapgen.schema import SpecError
from test_cli_exit_codes import REPO_ROOT, run

TOWN_SQUARE = os.path.join(REPO_ROOT, "flowgraphs", "town_square.flow.yaml")

BASE = """format_version: 1
name: tiny
nodes:
  A: {kind: spawn, team: A}
  B: {kind: spawn, team: B}
  M: {}
edges:
  - A - M
  - M - B
"""

ALLOWED_NODE = "kind, team, name, desc, bind"


class P(str):
    """Expected line matched as a prefix."""


def _case(cid, text, expected):
    return pytest.param(text, expected, id=cid)


def _w(path, text):
    with open(str(path), "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return str(path)


def _errors(tmp_path, text):
    with pytest.raises(SpecError) as ei:
        load_flowgraph(_w(tmp_path / "g.flow.yaml", text))
    return ei.value.errors


CASES = [
    _case("not_mapping", "- a\n- b\n", [": expected a mapping, got list"]),
    _case("yaml_syntax", "nodes: {A: [\n", [P("<yaml>:")]),
    _case("duplicate_key", BASE.replace("  M: {}\n", "  M: {}\n  M: {}\n"), [P("<yaml>: line")]),
    _case("unknown_top_key", BASE + "colour: red\n",
          ["colour: unknown key (allowed: format_version, name, brief, nodes, edges)"]),
    _case("missing_version", BASE.replace("format_version: 1\n", ""), ["format_version: missing required key"]),
    _case("bad_version", BASE.replace("format_version: 1", "format_version: 2"),
          ["format_version: unsupported value 2 (flowgraph format versions: 1)"]),
    _case("bad_name", BASE.replace("name: tiny", "name: Tiny-Map"), ["name: must match [a-z0-9_]+ (got 'Tiny-Map')"]),
    _case("missing_nodes", "format_version: 1\nname: tiny\n", ["nodes: missing required key"]),
    _case("bad_node_id", BASE.replace("  M: {}", "  1M: {}").replace("A - M", "A - A"),
          ["nodes.1M: node id must match [A-Za-z][A-Za-z0-9_]* (got '1M')", "edges[0]: self-edge on 'A'", "edges[1]: undeclared node 'M'"]),
    _case("hyphen_node_id", BASE.replace("  M: {}", "  M-x: {}"),
          ["nodes.M-x: node id must match [A-Za-z][A-Za-z0-9_]* (got 'M-x')",
           "edges[0]: undeclared node 'M'", "edges[1]: undeclared node 'M'"]),
    _case("unknown_node_key", BASE.replace("  M: {}", "  M: {size: 3}"),
          ["nodes.M.size: unknown key (allowed: %s)" % ALLOWED_NODE]),
    _case("bad_kind", BASE.replace("  M: {}", "  M: {kind: hill}"),
          ["nodes.M.kind: expected one of spawn, objective, zone, choke, area, got 'hill'"]),
    _case("spawn_without_team", BASE.replace("{kind: spawn, team: A}", "{kind: spawn}"),
          ["nodes.A.team: required for kind spawn",
           "nodes: need at least two spawn nodes with distinct teams (got 2 spawn(s), 1 team(s))"]),
    _case("team_on_non_spawn", BASE.replace("  M: {}", "  M: {kind: choke, team: A}"),
          ["nodes.M.team: only allowed for kind spawn"]),
    _case("one_spawn", BASE.replace("  B: {kind: spawn, team: B}", "  B: {kind: area}"),
          ["nodes: need at least two spawn nodes with distinct teams (got 1 spawn(s), 1 team(s))"]),
    _case("same_team_spawns", BASE.replace("team: B", "team: A"),
          ["nodes: need at least two spawn nodes with distinct teams (got 2 spawn(s), 1 team(s))",
           "nodes.B.bind: feature 'base_A' is already bound by node A"]),
    _case("undeclared_endpoint", BASE + "  - M - Z\n", ["edges[2]: undeclared node 'Z'"]),
    _case("self_edge", BASE + "  - M - M\n", ["edges[2]: self-edge on 'M'"]),
    _case("duplicate_edge", BASE + "  - M - A\n", ["edges[2]: duplicate edge M - A (first at edges[0])"]),
    _case("duplicate_edge_mapping", BASE + "  - {link: 'A - M', name: x}\n",
          ["edges[2]: duplicate edge A - M (first at edges[0])"]),
    _case("bad_link_format", BASE + "  - A M\n", ['edges[2]: expected "X - Y", got \'A M\'']),
    _case("link_not_string", BASE + "  - 5\n", ['edges[2]: expected a string "X - Y", got 5']),
    _case("edge_missing_link", BASE + "  - {name: x}\n", ["edges[2].link: missing required key"]),
    _case("unknown_edge_key", BASE + "  - {link: 'A - B', width: 3}\n",
          ["edges[2].width: unknown key (allowed: link, name, desc)"]),
    _case("edges_not_list", BASE.split("edges:")[0] + "edges: {a: b}\n", ["edges: expected a list, got dict"]),
    _case("bind_collision", BASE.replace("  M: {}", "  M: {bind: base_A}"),
          ["nodes.M.bind: feature 'base_A' is already bound by node A"]),
    _case("bind_collision_default", BASE.replace("  M: {}", "  M: {}\n  m: {}").replace("- M - B", "- M - B\n  - m - M"),
          ["nodes.m.bind: feature 'm' is already bound by node M"]),
    _case("disconnected", BASE.replace("  - M - B\n", ""),
          ["edges: graph is not connected (2 components: {A, M}; {B})"]),
    _case("collects_all", BASE.replace("  M: {}", "  M: {kind: hill}") + "  - M - M\n  - M - Z\n",
          ["nodes.M.kind: expected one of spawn, objective, zone, choke, area, got 'hill'",
           "edges[2]: self-edge on 'M'", "edges[3]: undeclared node 'Z'"]),
]


def _match(actual, expected):
    assert len(actual) == len(expected), actual
    for a, e in zip(actual, expected):
        assert a.startswith(e) if isinstance(e, P) else a == e


@pytest.mark.parametrize("text,expected", CASES)
def test_graph_errors(tmp_path, text, expected):
    _match(_errors(tmp_path, text), expected)


def test_town_square_loads():
    g = load_flowgraph(TOWN_SQUARE)
    assert g.name == "town_square"
    assert len(g.nodes) == 9 and len(g.edges) == 12
    binds = {n.id: n.bind for n in g.nodes}
    assert binds == {"A": "base_A", "B": "base_B", "O": "o", "AO": "ao", "BO": "bo",
                     "Fa": "fa", "Fb": "fb", "G": "g", "F": "f"}
    assert g.nodes[0].kind == "spawn" and g.nodes[0].team == "A"
    assert (g.edges[0].a, g.edges[0].b) == ("A", "AO")


def test_defaults_and_edge_forms(tmp_path):
    text = BASE.replace("  M: {}", "  M: {bind: mid, name: Middle, desc: d}") + "  - {link: 'B - A', name: n, desc: e}\n"
    g = load_flowgraph(_w(tmp_path / "g.flow.yaml", text))
    m = [n for n in g.nodes if n.id == "M"][0]
    assert (m.kind, m.team, m.bind, m.name, m.desc) == ("area", None, "mid", "Middle", "d")
    assert (g.edges[2].a, g.edges[2].b, g.edges[2].name, g.edges[2].desc) == ("B", "A", "n", "e")


# ----------------------------------------------------------------------------- CLI


def test_cli_ok():
    rc, out, _ = run("flowgraph", "validate", TOWN_SQUARE)
    assert rc == 0
    assert out.strip() == "OK: town_square (9 nodes, 12 edges)"


def test_cli_invalid(tmp_path):
    p = _w(tmp_path / "bad.flow.yaml", BASE + "  - M - M\n")
    rc, out, _ = run("flowgraph", "validate", p)
    assert rc == 1
    assert "1 error(s) in" in out and "  edges[2]: self-edge on 'M'" in out


def test_cli_missing(tmp_path):
    rc, out, _ = run("flowgraph", "validate", tmp_path / "nope.flow.yaml")
    assert rc == 1 and out.startswith("error: file not found:")


def test_cli_requires_subcommand():
    rc, _, _ = run("flowgraph")
    assert rc == 2
