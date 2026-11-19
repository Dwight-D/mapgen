"""Flowgraph scaffold and layout: determinism, self-pass (validate + check --strict), --out/--force, exit codes."""
import os

import pytest

from mapgen import flowgraph_scaffold
from mapgen.flowgraph import load_flowgraph
from mapgen.flowgraph_layout import layout
from mapgen.schema import load_spec
from test_cli_exit_codes import REPO_ROOT, run
from test_flowgraph import TOWN_SQUARE

CHOKES_AREAS = """format_version: 1
name: tiny_ca
nodes:
  A: {kind: spawn, team: A}
  B: {kind: spawn, team: B}
  C1: {kind: choke, name: West gate}
  C2: {kind: choke}
  R: {kind: area, desc: "ruins,   with: colons"}
edges:
  - A - C1
  - C1 - R
  - R - C2
  - C2 - B
  - {link: "A - R", name: back alley, desc: narrow}
"""

BOUND = """format_version: 1
name: bound
brief: one line
nodes:
  A: {kind: spawn, team: red, name: Red}
  B: {kind: spawn, team: blue}
  X: {kind: objective, bind: hill_top}
  Y: {kind: choke, bind: pass_y}
  Z: {kind: zone}
edges:
  - A - X
  - X - Y
  - Y - B
  - Z - X
  - Z - B
"""

THREE = """format_version: 1
name: three
nodes:
  A: {kind: spawn, team: A}
  B: {kind: spawn, team: B}
  C: {kind: spawn, team: C}
  M: {kind: objective}
edges:
  - A - M
  - B - M
  - C - M
"""


def _w(path, text):
    with open(str(path), "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return str(path)


def _selfpass(tmp_path, graph_path, *extra):
    code, out, err = run("flowgraph", "scaffold", graph_path, "--out", tmp_path / "s.yaml", "--force", *extra)
    assert code == 0, out + err
    spec = str(tmp_path / "s.yaml")
    code, out, err = run("validate", spec)
    assert code == 0, out + err
    code, out, err = run("flowgraph", "check", graph_path, spec, "--strict")
    assert code == 0, out + err
    return spec


def test_town_square_scaffold_deterministic():
    g = load_flowgraph(TOWN_SQUARE)
    assert flowgraph_scaffold.scaffold(g) == flowgraph_scaffold.scaffold(load_flowgraph(TOWN_SQUARE))
    c1, o1, _ = run("flowgraph", "scaffold", TOWN_SQUARE)
    c2, o2, _ = run("flowgraph", "scaffold", TOWN_SQUARE)
    assert (c1, c2) == (0, 0) and o1 == o2 and o1.startswith("# TODO(agent):")
    assert "\r" not in o1


def test_layout_deterministic_and_in_bounds():
    g = load_flowgraph(TOWN_SQUARE)
    a, b = layout(g, 3000, 3000), layout(load_flowgraph(TOWN_SQUARE), 3000, 3000)
    assert a == b and set(a) == {n.id for n in g.nodes}
    assert a["A"][0] < a["B"][0] and a["A"][0] == 300 and a["B"][0] == 2700
    assert len(set(a.values())) == len(a)
    for x, y in a.values():
        assert 300 <= x <= 2700 and 300 <= y <= 2700 and x % 10 == 0 and y % 10 == 0
    s = layout(g, 2000, 1000)
    assert all(100 <= x <= 1900 and 100 <= y <= 900 for x, y in s.values())


def test_town_square_selfpass(tmp_path):
    spec = load_spec(_selfpass(tmp_path, TOWN_SQUARE))
    assert {t for t in spec.teams} == {"A", "B"} and set(spec.points) == {"ao", "bo"}
    assert {p.id for p in spec.pois if p.type == "capture"} >= {"o", "g", "f"}
    assert len(spec.lines) == 12


@pytest.mark.parametrize("text", [CHOKES_AREAS, BOUND, THREE], ids=["chokes_areas", "bind_override", "three_teams"])
def test_other_shapes_selfpass(tmp_path, text):
    g = _w(tmp_path / "g.flow.yaml", text)
    _selfpass(tmp_path, g)
    first = (tmp_path / "s.yaml").read_bytes()
    _selfpass(tmp_path, g)
    assert (tmp_path / "s.yaml").read_bytes() == first


def test_bind_override_used(tmp_path):
    g = _w(tmp_path / "g.flow.yaml", BOUND)
    code, out, _ = run("flowgraph", "scaffold", g)
    assert code == 0
    assert "id: hill_top, type: capture" in out and "pass_y: [" in out and "path: [hill_top, pass_y]" in out
    assert "id: z, type: capture" in out and "path: [base_red, hill_top]" in out


def test_size_option(tmp_path):
    code, out, _ = run("flowgraph", "scaffold", TOWN_SQUARE, "--size", "2000", "1000")
    assert code == 0 and "size: [2000, 1000]" in out
    _selfpass(tmp_path, TOWN_SQUARE, "--size", "2000", "1000")


def test_out_and_force(tmp_path):
    out_path = tmp_path / "draft.yaml"
    code, out, _ = run("flowgraph", "scaffold", TOWN_SQUARE, "--out", out_path)
    assert code == 0 and out_path.exists() and "\r" not in out_path.read_text(encoding="utf-8")
    first = out_path.read_bytes()
    code, out, _ = run("flowgraph", "scaffold", TOWN_SQUARE, "--out", out_path)
    assert code == 1 and "--force" in out and out_path.read_bytes() == first
    out_path.write_text("stale", encoding="utf-8")
    code, _, _ = run("flowgraph", "scaffold", TOWN_SQUARE, "--out", out_path, "--force")
    assert code == 0 and out_path.read_bytes() == first


def test_cli_errors(tmp_path):
    code, out, _ = run("flowgraph", "scaffold", tmp_path / "missing.flow.yaml")
    assert code == 1 and "file not found" in out
    bad = _w(tmp_path / "bad.flow.yaml", "format_version: 1\nname: x\nnodes: {}\n")
    code, out, _ = run("flowgraph", "scaffold", bad)
    assert code == 1 and "error(s)" in out
    code, _, err = run("flowgraph", "scaffold", TOWN_SQUARE, "--size", "0", "100")
    assert code == 2 and "expected a number > 0" in err


def test_scaffold_errors(tmp_path):
    g = _w(tmp_path / "g.flow.yaml", BOUND.replace("bind: pass_y", "bind: Pass-Y"))
    code, out, _ = run("flowgraph", "scaffold", g)
    assert code == 1 and "nodes.Y.bind" in out
    g = _w(tmp_path / "t.flow.yaml", THREE.replace("  M:", "  D: {kind: spawn, team: D}\n  M:").replace("  - C - M", "  - C - M\n  - D - M"))
    code, out, _ = run("flowgraph", "scaffold", g)
    assert code == 1 and "at most 3 teams" in out
