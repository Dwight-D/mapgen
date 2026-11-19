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


# ----------------------------------------------------------------------------- derivation and check

from mapgen.flowgraph_check import check, derive_edges  # noqa: E402
from mapgen.schema import load_spec  # noqa: E402

GRAPH = """format_version: 1
name: tri
nodes:
  A: {kind: spawn, team: A, name: Team A spawn}
  B: {kind: spawn, team: B}
  C: {kind: objective, name: Centre}
edges:
  - A - C
  - C - B
"""

C_POI = "  - {id: c, type: capture, at: [1000, 1000]}\n"

SPEC = """format_version: 1
name: tri
size: [2000, 2000]
seed: 1
teams:
  A: {base: [200, 1000]}
  B: {base: [1800, 1000]}
pois:
%s
lines:
%s"""


def _load(tmp_path, lines, graph=GRAPH, pois=C_POI):
    sp = _w(tmp_path / "s.yaml", SPEC % (pois, lines))
    gp = _w(tmp_path / "g.flow.yaml", graph)
    return load_flowgraph(gp), load_spec(sp)


def _edges(tmp_path, lines):
    g, spec = _load(tmp_path, lines)
    return {tuple(sorted(e)) for e in derive_edges(g, spec)}


ROAD_AC = "  - {id: r1, type: road, path: [base_A, c], width: 8}\n"
ROAD_CB = "  - {id: r2, type: road, path: [c, base_B], width: 8}\n"


def test_derive_refs(tmp_path):
    assert _edges(tmp_path, ROAD_AC + ROAD_CB) == {("A", "C"), ("B", "C")}


def test_derive_through_node_splits(tmp_path):
    e = _edges(tmp_path, "  - {id: r1, type: road, path: [base_A, c, base_B], width: 8}\n")
    assert e == {("A", "C"), ("B", "C")}


def test_derive_through_node_by_coordinate(tmp_path):
    # [1000, 1020] is within max(r=40, 30) of c; [1100, 1000] is not
    e = _edges(tmp_path, "  - {id: r1, type: road, path: [base_A, [1000, 1020], [1100, 1000], base_B], width: 8}\n")
    assert e == {("A", "C"), ("B", "C")}


def test_derive_coordinate_outside_radius_dropped(tmp_path):
    e = _edges(tmp_path, "  - {id: r1, type: road, path: [base_A, [1000, 1050], base_B], width: 8}\n")
    assert e == {("A", "B")}


def test_derive_coordinate_at_base(tmp_path):
    # base r is 120 m
    assert _edges(tmp_path, "  - {id: r1, type: road, path: [[300, 1000], [1000, 1000]], width: 8}\n") == {("A", "C")}


def test_derive_repeats_collapse(tmp_path):
    e = _edges(tmp_path, "  - {id: r1, type: road, path: [base_A, [210, 1000], c, [1000, 1010], c], width: 8}\n")
    assert e == {("A", "C")}


def test_derive_river_cliff_ignored_trail_counted(tmp_path):
    lines = ("  - {id: r1, type: river, path: [base_A, c], width: 8}\n"
             "  - {id: r2, type: cliff, path: [c, base_B], width: 8}\n")
    assert _edges(tmp_path, lines) == set()
    assert _edges(tmp_path, "  - {id: r1, type: trail, path: [base_A, c], width: 4}\n") == {("A", "C")}


def _check(tmp_path, lines, graph=GRAPH, pois=C_POI):
    return check(*_load(tmp_path, lines, graph, pois))


def test_check_pass(tmp_path):
    assert not _check(tmp_path, ROAD_AC + ROAD_CB).failed(True)


def test_check_missing_node(tmp_path):
    lines = "  - {id: r1, type: road, path: [base_A, [1000, 1000], base_B], width: 8}\n"
    r = _check(tmp_path, lines, pois="  - {id: x, type: landmark, at: [1000, 1000]}\n")
    assert r.missing_nodes == ["missing node c (C: Centre)"]
    assert r.failed(False)


def test_check_missing_edge(tmp_path):
    r = _check(tmp_path, ROAD_AC)
    assert r.missing_edges == ["missing edge C - B"] and not r.missing_nodes
    assert r.failed(False)


def test_check_strict_extra_edge(tmp_path):
    r = _check(tmp_path, ROAD_AC + ROAD_CB + "  - {id: r3, type: road, path: [base_A, base_B], width: 8}\n")
    assert r.extra_edges == ["extra edge A - B"]
    assert not r.failed(False) and r.failed(True)


def test_check_strict_extra_node_and_landmark_exempt(tmp_path):
    pois = C_POI + ("  - {id: lm, type: landmark, at: [1000, 500]}\n"
                    "  - {id: cap2, type: supply, at: [1000, 1500]}\n")
    r = _check(tmp_path, ROAD_AC + ROAD_CB, pois=pois)
    assert r.extra_nodes == ["extra node cap2 (supply)"]
    assert not r.failed(False) and r.failed(True)


def test_check_extra_edge_to_unbound_ignored(tmp_path):
    pois = C_POI + "  - {id: z, type: landmark, at: [1000, 1500]}\n"
    r = _check(tmp_path, ROAD_AC + ROAD_CB + "  - {id: r3, type: road, path: [c, z], width: 8}\n", pois=pois)
    assert r.extra_edges == [] and not r.failed(True)


def test_check_extra_base_without_spawn_node(tmp_path):
    graph = GRAPH.replace("  B: {kind: spawn, team: B}\n", "  B: {kind: spawn, team: B, bind: c2}\n")
    pois = C_POI + "  - {id: c2, type: capture, at: [1800, 1000]}\n"
    r = _check(tmp_path, ROAD_AC + "  - {id: r2, type: road, path: [c, c2], width: 8}\n", graph=graph, pois=pois)
    assert r.extra_nodes == ["extra node base_B (base)"]


# town_square realisation

TS_SPEC = """format_version: 1
name: town_square
size: [3000, 3000]
seed: 1
teams:
  A: {base: [300, 1500]}
  B: {base: [2700, 1500]}
points:
  ao: [800, 1500]
  bo: [2200, 1500]
pois:
  - {id: o,  type: capture,  at: [1500, 1500]}
  - {id: fa, type: landmark, at: [1500, 2300]}
  - {id: fb, type: landmark, at: [1500, 700]}
  - {id: g,  type: capture,  at: [900, 2400]}
  - {id: f,  type: capture,  at: [900, 600]}
  - {id: x1, type: landmark, at: [2100, 2700]}
lines:
  - {id: a_ao, type: road, path: [base_A, ao], width: 8}
  - {id: ao_o, type: road, path: [ao, o], width: 8}
  - {id: b_bo, type: road, path: [base_B, bo], width: 8}
  - {id: bo_o, type: road, path: [bo, o], width: 8}
  - {id: fa_o, type: road, path: [fa, o], width: 8}
  - {id: fb_o, type: trail, path: [fb, o], width: 4}
  - {id: g_fa, type: road, path: [g, fa], width: 8}
  - {id: f_fb, type: road, path: [f, fb], width: 8}
  - {id: a_g, type: road, path: [base_A, g], width: 8}
  - {id: a_f, type: road, path: [base_A, f], width: 8}
  - {id: b_g, type: road, path: [base_B, [2100, 2700], g], width: 8}
  - {id: b_f, type: road, path: [base_B, f], width: 8}
"""


def test_town_square_realised_passes(tmp_path):
    sp = _w(tmp_path / "ts.yaml", TS_SPEC)
    rc, out, _ = run("flowgraph", "check", TOWN_SQUARE, sp, "--strict")
    assert rc == 0, out
    assert out.strip().splitlines()[-1] == "OK: 0 missing node(s), 0 missing edge(s), 0 extra edge(s), 0 extra node(s)"


def test_town_square_broken_fails(tmp_path):
    t = TS_SPEC.replace("  - {id: f,  type: capture,  at: [900, 600]}\n", "  - {id: f2, type: capture, at: [900, 600]}\n")
    t = t.replace("{id: bo_o, type: road, path: [bo, o]", "{id: bo_o, type: road, path: [base_B, o]")
    t = t.replace("path: [base_B, f]", "path: [base_B, f2]").replace("path: [base_A, f]", "path: [base_A, f2]")
    t = t.replace("path: [f, fb]", "path: [f2, fb]")
    sp = _w(tmp_path / "ts.yaml", t)
    rc, out, _ = run("flowgraph", "check", TOWN_SQUARE, sp, "--strict")
    print(out)
    assert rc == 1
    assert "missing node f (F: Forest Glen)" in out
    assert "missing edge BO - O" in out
    assert "extra edge B - O" in out
    assert "extra node f2 (capture)" in out
    rc, out, _ = run("flowgraph", "check", TOWN_SQUARE, sp)
    assert rc == 1 and "extra" not in out


def test_cli_check_extras_default_pass(tmp_path):
    sp = _w(tmp_path / "ts.yaml", TS_SPEC.replace("lines:\n", "  - {id: zz, type: supply, at: [300, 300]}\nlines:\n"))
    assert run("flowgraph", "check", TOWN_SQUARE, sp)[0] == 0
    assert run("flowgraph", "check", TOWN_SQUARE, sp, "--strict")[0] == 1


def test_cli_check_load_errors(tmp_path):
    bad_graph = _w(tmp_path / "bad.flow.yaml", BASE + "  - M - M\n")
    sp = _w(tmp_path / "ts.yaml", TS_SPEC)
    rc, out, _ = run("flowgraph", "check", bad_graph, sp)
    assert rc == 1 and "1 error(s) in" in out and "self-edge" in out
    bad_spec = _w(tmp_path / "bs.yaml", TS_SPEC.replace("seed: 1", "seed: abc"))
    rc, out, _ = run("flowgraph", "check", TOWN_SQUARE, bad_spec)
    assert rc == 1 and "seed: expected an integer" in out
    rc, out, _ = run("flowgraph", "check", TOWN_SQUARE, tmp_path / "nope.yaml")
    assert rc == 1 and out.startswith("error: file not found:")
    assert run("flowgraph", "check", TOWN_SQUARE)[0] == 2
