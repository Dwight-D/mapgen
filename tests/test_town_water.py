"""Town layout: water overlay (design: mapgen/TOWN_SPEC.md section 7.5)."""
import os

import pytest
import yaml
from shapely.geometry import LineString, Point, Polygon

import mapgen
from mapgen import schema, town
from mapgen.flowgraph import load_flowgraph_dict
from mapgen.flowgraph_check import check as flow_check, raw_line_paths_text
from mapgen.town import water
from mapgen.town.params import ParamError

SEEDS = list(range(1, 21))
BASES = ["radial", "voronoi", "grid"]
BOTH_SEEDS = {"radial": SEEDS, "voronoi": SEEDS, "grid": SEEDS[:10]}


def _gen(base, seed, mode, **kw):
    params = dict(kw.pop("params", {}))
    params["water"] = mode
    return town.generate(base, seed, params=params, **kw)


@pytest.mark.parametrize("base", BASES)
@pytest.mark.parametrize("mode", ["river", "bay"])
@pytest.mark.parametrize("seed", SEEDS)
def test_checks_pass(base, mode, seed):
    s = _gen(base, seed, mode)
    assert town.check(s) == []
    assert town.check_determinism(s) == []


@pytest.mark.parametrize("base", BASES)
def test_both_passes(base):
    for seed in BOTH_SEEDS[base]:
        s = _gen(base, seed, "both")
        assert town.check(s) == []
        assert town.check_determinism(s) == []
        assert s.water["rivers"] and s.water["bays"]


def test_none_is_the_plain_settlement():
    for base in BASES:
        assert _gen(base, 3, "none").to_json() == town.generate(base, 3).to_json()
        assert town.generate(base, 3).water == {}


@pytest.mark.parametrize("base", BASES)
def test_same_seed_identical_json(base):
    assert _gen(base, 4, "river").to_json() == _gen(base, 4, "river").to_json()
    assert _gen(base, 4, "bay").to_json() == _gen(base, 4, "bay").to_json()
    assert _gen(base, 4, "river").to_json() != _gen(base, 5, "river").to_json()


def test_bad_params():
    for params in ({"water": "lake"}, {"water": "river", "water_width": 5}, {"water": "bay", "water_bay_size": 0.9},
                   {"water": "river", "water_angle": 400}, {"water": "river", "water_x": 1}, {"water": "bay", "water_island": 3}):
        with pytest.raises(ParamError):
            town.generate("radial", 1, params=params)


def test_overrides_echoed():
    s = _gen("radial", 2, "river", params={"water_width": 50.0, "water_angle": 30.0})
    assert s.params["water_width"] == 50.0 and s.params["water_angle"] == 30.0 and s.water["angle"] == 30.0
    assert s.water["rivers"][0]["width"] == 50.0
    assert town.check(s) == [] and town.check_determinism(s) == []


def _components(s, edges):
    ids = {n.id for n in s.graph_nodes}
    adj = {i: set() for i in ids}
    for e in edges:
        adj[e.a].add(e.b)
        adj[e.b].add(e.a)
    seen, n = set(), 0
    for i in sorted(ids):
        if i in seen:
            continue
        n += 1
        todo = [i]
        seen.add(i)
        while todo:
            for m in adj[todo.pop()]:
                if m not in seen:
                    seen.add(m)
                    todo.append(m)
    return n


@pytest.mark.parametrize("base", BASES)
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_river_splits_and_bridges_join(base, seed):
    s = _gen(base, seed, "river")
    W = water.water_union(s)
    assert s.water["bridges"]
    assert {e.road for e in s.graph_edges if e.cls == "bridge"} == set(s.water["bridges"])
    assert _components(s, s.graph_edges) == 1
    assert _components(s, [e for e in s.graph_edges if e.cls != "bridge"]) >= 2
    for b in s.roads:
        if b.cls == "bridge":
            for nid in b.nodes:
                at = next(n.at for n in s.graph_nodes if n.id == nid)
                assert W.boundary.distance(Point(at)) < 0.5
    assert all(Polygon(d.polygon).intersection(W).area < 1.0 for d in s.districts)
    assert any(e.kind == "bridge" for e in s.district_edges)
    assert any(d.waterfront for d in s.districts)


@pytest.mark.parametrize("base", BASES)
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_bay_harbour_and_open_shore(base, seed):
    s = _gen(base, seed, "bay")
    bay = Polygon(s.water["bays"][0]["polygon"])
    assert Polygon(s.footprint).intersection(bay).area < 1.0
    assert sum(1 for d in s.districts if d.kind == "harbour") == 1
    docks = [l for l in s.landmarks if l.kind == "docks"]
    assert len(docks) == 1 and bay.distance(Point(docks[0].at)) < 80.0
    outer = next(w for w in s.walls if w.kind == "outer")
    assert outer.open_shore
    runs = water.wall_runs(outer)
    assert runs and all(LineString(r).intersection(bay).length < 0.5 for r in runs)
    for a, b in outer.open_shore:
        for i in range(a, b):
            mid = Point([(outer.polyline[i][k] + outer.polyline[i + 1][k]) / 2.0 for k in (0, 1)])
            assert bay.distance(mid) < 0.5
    assert all(g.kind in ("gate", "water_gate") for g in s.gates)


def test_water_gate_replaces_lost_gate():
    kinds = set()
    for seed in SEEDS[:8]:
        s = _gen("radial", seed, "bay")
        kinds.update(g.kind for g in s.gates)
        assert all(g.kind != "water_gate" or any(n.id == g.id for n in s.graph_nodes) for g in s.gates)
    assert "water_gate" in kinds


def test_island_hook():
    s = _gen("radial", 2, "bay", params={"water_island": True})
    assert len(s.water["islands"]) == 1
    isl = s.water["islands"][0]
    assert Polygon(isl["polygon"]).within(Polygon(s.water["bays"][0]["polygon"]))
    assert isl["fort"] is False and isl["link"] == "none"
    assert town.check(s) == [] and town.check_determinism(s) == []
    assert "islands" not in _gen("radial", 2, "bay").water


def test_check_catches_breakage():
    s = _gen("grid", 2, "river")
    s.roads = [r for r in s.roads if r.cls != "bridge"]
    s.graph_edges = [e for e in s.graph_edges if e.cls != "bridge"]
    assert town.check(s)
    s = _gen("radial", 2, "bay")
    next(w for w in s.walls if w.kind == "outer").open_shore = []
    assert any("water" in e for e in town.check(s))


def test_json_round_trip():
    for mode in ("river", "bay", "both"):
        s = _gen("radial", 3, mode)
        assert town.Settlement.from_json(s.to_json()).to_json() == s.to_json()


HEADER = """format_version: 1
name: towntest
size: [2000, 2000]
seed: 1
teams:
  A: { base: %s }
  B: { base: %s }
"""


def _spec_and_graph(s):
    ga, gb = s.gates[0], s.gates[len(s.gates) // 2]

    def base(g):
        return [round(g.at[0] + g.normal[0] * 260, 2), round(g.at[1] + g.normal[1] * 260, 2)]
    text = HEADER % (base(ga), base(gb)) + town.emit_spec(s)
    text = text.replace("lines:\n", "lines:\n  - { id: road_a, seed: 1, type: road, path: [base_A, %s] }\n"
                        "  - { id: road_b, seed: 2, type: road, path: [base_B, %s] }\n" % (ga.id, gb.id), 1)
    frag = yaml.safe_load(town.emit_flow(s))
    nodes = dict(frag["nodes"])
    nodes["A"] = {"kind": "spawn", "team": "A"}
    nodes["B"] = {"kind": "spawn", "team": "B"}
    edges = list(frag["edges"]) + ["A - %s" % ga.id, "B - %s" % gb.id]
    return text, {"format_version": 1, "name": "towntest", "nodes": nodes, "edges": edges}


def test_water_types_exist():
    with open(os.path.join(os.path.dirname(mapgen.__file__), "types.yaml"), encoding="utf-8") as f:
        t = yaml.safe_load(f)
    assert "river" in t["lines"] and "wall" in t["lines"]
    assert "lake" in t["regions"] and "clearing" in t["regions"]
    assert "bridge" in t["crossings"] and "gate" in t["crossings"]


@pytest.mark.parametrize("base,mode,seed", [("radial", "river", 2), ("grid", "river", 5), ("voronoi", "river", 1),
                                            ("radial", "bay", 2), ("grid", "bay", 4), ("voronoi", "bay", 3)])
def test_fragment_loads(base, mode, seed):
    s = _gen(base, seed, mode, size=(2000, 2000))
    text, flow = _spec_and_graph(s)
    spec = schema.load_spec_text(text)
    assert len(spec.regions) >= len(s.districts)
    assert [l.type for l in spec.lines].count("river") == len(s.water.get("rivers", []))
    assert sum(1 for c in spec.crossings if c.type == "bridge") == len(s.water.get("bridges", []))
    assert sum(1 for r in spec.regions if r.type == "lake") == len(s.water.get("bays", []))
    assert sum(1 for c in spec.crossings if c.type == "gate") == len(s.gates)
    g = load_flowgraph_dict(flow)
    res = flow_check(g, spec, raw_paths=raw_line_paths_text(text))
    assert not res.failed(strict=True), res.lines(True)


def test_emit_deterministic_and_preview():
    s = _gen("grid", 5, "river")
    assert town.emit_spec(s) == town.emit_spec(_gen("grid", 5, "river"))
    assert town.render_png(s, px=300).size == (300, 300)
    assert town.render_png(_gen("radial", 5, "bay"), px=300).size == (300, 300)
