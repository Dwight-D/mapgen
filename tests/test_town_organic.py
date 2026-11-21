"""Town layout: the organic generator, edge=wall and edge=open (design: mapgen/TOWN_ORGANIC_SPEC.md sections 5, 8, 9)."""
import json

import pytest
import yaml
from shapely.geometry import LineString, Polygon

from mapgen import schema, town
from mapgen.flowgraph import load_flowgraph_dict
from mapgen.flowgraph_check import check as flow_check, raw_line_paths_text
from mapgen.town import graph
from mapgen.town.params import ParamError
from test_cli_exit_codes import run

SEEDS = list(range(1, 21))
EDGES = ["wall", "open"]
HEADER = """format_version: 1
name: organictest
size: [2000, 2000]
seed: 1
teams:
  A: { base: %s }
  B: { base: %s }
"""


def _gen(seed, edge="wall", **extra):
    p = {"edge": edge}
    p.update(extra)
    return town.generate("organic", seed, size=(2000, 2000), params=p)


@pytest.mark.parametrize("edge", EDGES)
@pytest.mark.parametrize("seed", SEEDS)
def test_checks_determinism_round_trip(edge, seed):
    s = _gen(seed, edge)
    assert town.check(s) == []
    assert town.check_determinism(s) == []
    assert s.params["edge"] == edge
    assert town.Settlement.from_json(s.to_json()).to_json() == s.to_json()
    assert len(s.gates) >= 3 and len(s.gates) == len([r for r in s.roads if r.cls == "artery"])


@pytest.mark.parametrize("edge", EDGES)
@pytest.mark.parametrize("seed", SEEDS)
def test_fragment_validates_and_flowgraph_strict(edge, seed):
    s = _gen(seed, edge)
    ga, gb = s.gates[0], s.gates[-1]
    pos = lambda g: [min(1995.0, max(5.0, round(g.at[0] + g.normal[0] * 260, 2))), min(1995.0, max(5.0, round(g.at[1] + g.normal[1] * 260, 2)))]
    text = HEADER % (pos(ga), pos(gb)) + town.emit_spec(s)
    text = text.replace("lines:\n", "lines:\n  - { id: road_a, seed: 1, type: road, path: [base_A, %s] }\n"
                        "  - { id: road_b, seed: 2, type: road, path: [base_B, %s] }\n" % (ga.id, gb.id), 1)
    spec = schema.load_spec_text(text)
    assert len(spec.regions) == len(s.districts)
    frag = yaml.safe_load(town.emit_flow(s))
    nodes = dict(frag["nodes"])
    nodes["A"] = {"kind": "spawn", "team": "A"}
    nodes["B"] = {"kind": "spawn", "team": "B"}
    edges = list(frag["edges"]) + ["A - %s" % ga.id, "B - %s" % gb.id]
    g = load_flowgraph_dict({"format_version": 1, "name": "organictest", "nodes": nodes, "edges": edges})
    res = flow_check(g, spec, raw_paths=raw_line_paths_text(text))
    assert not res.failed(strict=True), res.lines(True)


@pytest.mark.parametrize("seed", SEEDS[:10])
def test_open_edge_has_entries_and_no_wall(seed):
    s = _gen(seed, "open")
    assert s.walls == []
    assert len(s.gates) >= 3
    ring = LineString(s.footprint + [s.footprint[0]])
    for g in s.gates:
        assert g.kind == "entry" and g.wall == "" and "_entry_" in g.id
        assert ring.distance(LineString([g.at, g.at]).centroid) <= 1.0
    out = [e for e in s.district_edges if "outside" in (e.a, e.b)]
    assert out and all(e.passable and e.kind == "adjacent" for e in out)
    assert {gid for e in out for gid in e.gates} == {g.id for g in s.gates}
    assert "crossings" not in town.emit_spec(s).split("crossings:")[1].split("pois:")[0].replace("\n", "")  # no gate crossings
    assert "type: wall" not in town.emit_spec(s)


@pytest.mark.parametrize("seed", SEEDS[:10])
def test_wall_edge_has_wall_and_gates(seed):
    s = _gen(seed, "wall")
    assert len(s.walls) == 1 and s.walls[0].kind == "outer"
    assert all(g.kind == "gate" and g.wall == s.walls[0].id for g in s.gates)
    assert all(e.kind == "wall" for e in s.district_edges if "outside" in (e.a, e.b))


@pytest.mark.parametrize("seed", SEEDS[:8])
def test_outline_not_circular_and_roads_curved(seed):
    s = _gen(seed)
    P = Polygon(s.footprint)
    assert P.is_valid and P.exterior.is_ccw
    assert P.area / P.convex_hull.area < 0.97
    arteries = [r for r in s.roads if r.cls == "artery"]
    assert any(len(r.polyline) > 4 for r in arteries)


def test_seeds_differ():
    assert len({tuple(map(tuple, _gen(s).footprint)) for s in range(1, 9)}) == 8


@pytest.mark.parametrize("edge", EDGES)
@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 6])
def test_with_warp_and_layers(edge, seed):
    s = _gen(seed, edge, warp="0.6", layers="bsp*2/grid;max_depth=1")
    assert town.check(s) == []
    assert town.check_determinism(s) == []
    assert len(s.districts) > 8


@pytest.mark.parametrize("seed", [1, 2, 3, 4])
def test_open_edge_with_river(seed):
    try:
        s = _gen(seed, "open", water="river")
    except town.GenerationError:
        pytest.skip("no layout")
    assert town.check(s) == []


def test_density_omitted_from_json_while_default():
    s = _gen(1)
    d = json.loads(s.to_json())
    assert all("density" not in x for x in d["districts"])
    s.districts[0].density = 0.5
    assert json.loads(s.to_json())["districts"][0]["density"] == 0.5
    assert town.Settlement.from_json(s.to_json()).districts[0].density == 0.5


def test_district_graph_open_edge_footprint_default_matches_explicit():
    s = _gen(3, "open")
    a = graph.district_graph(s.districts, s.roads, s.walls, s.gates, s.footprint)
    b = graph.district_graph(s.districts, s.roads, s.walls, s.gates)
    assert [e.id for e in a[1]] == [e.id for e in b[1]]


def test_check_catches_bad_entry():
    s = _gen(2, "open")
    s.gates[0].kind = "gate"
    assert any("must be entry" in e for e in town.check(s))
    s = _gen(2, "open")
    s.gates[0].at = [s.centre[0], s.centre[1]]
    assert any("footprint ring" in e or "from road" in e for e in town.check(s))


def test_bad_params():
    with pytest.raises(ParamError):
        town.generate("organic", 1, params={"edge": "moat"})
    with pytest.raises(ParamError) as e:
        town.generate("organic", 1, params={"edge": "open", "fort": "corner"})
    assert "fort" in str(e.value)
    with pytest.raises(ParamError):
        town.generate("radial", 1, params={"edge": "open"})
    with pytest.raises(ParamError):
        town.generate("organic", 1, params={"arteries": 2})


def test_cli_generate_and_shape(tmp_path):
    code, out, _ = run("town", "generate", "organic", "--seed", "2", "--param", "edge=open", "--param", "warp=0.5",
                       "--out", tmp_path / "t")
    assert code == 0, out
    assert (tmp_path / "t" / "settlement.json").exists()
    code, out, _ = run("town", "generate", "organic", "--param", "edge=moat")
    assert code == 1 and "edge" in out
    code, out, _ = run("town", "shape", "--seeds", "2", "--out", tmp_path / "o.png")
    assert code == 0 and (tmp_path / "o.png").exists()
    code, out, _ = run("town", "list")
    assert code == 0 and "organic" in out
