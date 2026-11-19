"""Flowgraph scaffold and layout: determinism, self-pass (validate + check --strict), --out/--force, exit codes."""
import math
import os
import statistics

import pytest

from mapgen import flowgraph_scaffold
from mapgen.flowgraph import load_flowgraph
from mapgen.flowgraph_layout import _layered_layout, layout
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
    assert layout(g, 3000, 3000) == a  # repeated calls in one process
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


def _edge_lengths(g, pos):
    return [math.hypot(pos[e.a][0] - pos[e.b][0], pos[e.a][1] - pos[e.b][1]) for e in g.edges]


def test_town_square_diamond():
    """Symmetric diamond: A, AO, O, BO on one horizontal line; G, Fa, O, Fb, F on one vertical line; both mirror-symmetric."""
    g = load_flowgraph(TOWN_SQUARE)
    p = layout(g, 800, 800)
    cx, cy = p["O"]
    for n in ("A", "AO", "BO", "B"):  # the horizontal axis
        assert p[n][1] == cy, (n, p)
    for n in ("G", "Fa", "Fb", "F"):  # the vertical column
        assert p[n][0] == cx, (n, p)
    assert p["A"][0] < p["AO"][0] < cx < p["BO"][0] < p["B"][0]
    assert p["G"][1] > p["Fa"][1] > cy > p["Fb"][1] > p["F"][1]
    for u, v in (("A", "B"), ("AO", "BO")):  # left-right mirror
        assert p[u][0] + p[v][0] == 2 * cx and p[u][1] == p[v][1]
    for u, v in (("G", "F"), ("Fa", "Fb")):  # top-bottom mirror
        assert p[u][1] + p[v][1] == 2 * cy and p[u][0] == p[v][0]
    assert len(set(p.values())) == len(p)


DIAMOND = """format_version: 1
name: d
nodes:
  A: {kind: spawn, team: A}
  B: {kind: spawn, team: B}
  X: {}
  Y: {}
  M: {}
edges:
  - A - X
  - X - B
  - A - Y
  - Y - B
  - X - M
  - Y - M
"""


def test_symmetric_graph_without_hints_is_mirrored(tmp_path):
    g = load_flowgraph(_w(tmp_path / "d.flow.yaml", DIAMOND))
    p = layout(g, 3000, 3000)
    assert p["A"][0] + p["B"][0] == p["X"][0] + p["X"][0] == p["Y"][0] + p["Y"][0] == 3000
    assert p["X"][1] != p["Y"][1] and len(set(p.values())) == len(p)


def test_spawns_default_west_east():
    g = load_flowgraph(TOWN_SQUARE)
    p = layout(g, 3000, 3000)
    assert p["A"][0] <= 0.2 * 3000 and p["B"][0] >= 0.8 * 3000


ZONES = """format_version: 1
name: zones
nodes:
  A: {kind: spawn, team: A}
  B: {kind: spawn, team: B}
  N: {place: north}
  S: {place: south}
  W: {place: west}
  E: {place: east}
  C: {place: center}
  NE: {place: north-east}
  SW: {place: south-west}
edges:
  - A - N
  - A - S
  - N - C
  - S - C
  - C - W
  - C - E
  - E - B
  - W - A
  - NE - B
  - SW - A
  - NE - N
  - SW - S
"""


def _in_zone(place, pt, w, h):
    x, y = pt
    ok = True
    if "west" in place:
        ok = ok and x <= 0.35 * w
    if "east" in place:
        ok = ok and x >= 0.65 * w
    if "north" in place:
        ok = ok and y >= 0.65 * h
    if "south" in place:
        ok = ok and y <= 0.35 * h
    if place == "center":
        ok = ok and 0.4 * w <= x <= 0.6 * w and 0.4 * h <= y <= 0.6 * h
    return ok


@pytest.mark.parametrize("size", [(3000, 3000), (800, 800), (2000, 1000)])
def test_anchored_nodes_land_in_zone(tmp_path, size):
    path = _w(tmp_path / "g.flow.yaml", ZONES)
    g = load_flowgraph(path)
    p = layout(g, size[0], size[1])
    for n in g.nodes:
        if n.place:
            assert _in_zone(n.place, p[n.id], size[0], size[1]), (n.id, p[n.id])
    assert len(set(p.values())) == len(p)
    spec = load_spec(_selfpass_size(tmp_path, path, size))
    pts = {poi.id: poi.at for poi in spec.pois}
    assert _in_zone("north", pts["n"], size[0], size[1])


def _selfpass_size(tmp_path, graph_path, size):
    spec = str(tmp_path / "s.yaml")
    code, out, err = run("flowgraph", "scaffold", graph_path, "--out", spec, "--force", "--size", str(size[0]), str(size[1]))
    assert code == 0, out + err
    code, out, err = run("validate", spec)
    assert code == 0, out + err
    code, out, err = run("flowgraph", "check", graph_path, spec, "--strict")
    assert code == 0, out + err
    return spec
