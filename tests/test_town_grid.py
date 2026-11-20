"""Town layout: `grid` generator (design: mapgen/TOWN_SPEC.md section 7.3)."""
import json
import os

import pytest
import yaml

from mapgen import schema, town
from mapgen.flowgraph import load_flowgraph_dict
from mapgen.flowgraph_check import check as flow_check, raw_line_paths_text
from mapgen.town.emit_spec import wall_gate_types
from mapgen.town.params import ParamError

SEEDS = list(range(1, 21))
HEARTHMOOR = dict(extent_frac=0.5, roundness=8.0, rotation=0.0, boulevards=4, boulevard_twist=0.0, boulevard_jitter=0.0,
                  gates=4, hub_offset=0.0, lattice_phase=0.0, spacing_jitter=0.0, inner_wall=False, aspect=1.15)
MANHATTAN = dict(aspect=1.7, roundness=12.0, block_ratio=0.5, rotation=29.0, boulevards=2, boulevard_twist=32.0,
                 inner_wall=False, hub_offset=0.25, block=120.0)


def _gen(seed, **kw):
    return town.generate("grid", seed, **kw)


@pytest.mark.parametrize("seed", SEEDS)
def test_checks_pass(seed):
    s = _gen(seed)
    assert town.check(s) == []
    assert town.check_determinism(s) == []


@pytest.mark.parametrize("name,params,size", [("hearthmoor", HEARTHMOOR, (600, 600)), ("manhattan", MANHATTAN, (1200, 1200)),
                                              ("walled", dict(inner_wall=True, citadel=True, boulevards=5), (1000, 1000))])
@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_variants_pass(name, params, size, seed):
    s = _gen(seed, size=size, params=params)
    assert town.check(s) == []
    assert town.check_determinism(s) == []


@pytest.mark.parametrize("seed", [1, 7, 16])
def test_same_seed_identical_json(seed):
    assert _gen(seed).to_json() == _gen(seed).to_json()


def test_seeds_differ():
    assert len({_gen(s).to_json() for s in SEEDS}) == len(SEEDS)


def test_params_resolved_and_override():
    s = _gen(3)
    assert 2 <= s.params["boulevards"] <= 8 and s.params["min_district_area"] == 1600.0
    t = _gen(3, params={"boulevards": 3, "gates": 5, "citadel": True, "inner_wall": True})
    assert t.params["boulevards"] == 3
    assert any(l.kind == "citadel" for l in t.landmarks)
    assert town.check(t) == []


def test_override_does_not_shift_other_draws():
    a, b = _gen(5), _gen(5, params={"boulevards": 3})
    for k in ("extent_frac", "aspect", "roundness", "rotation", "gates", "hub_offset"):
        assert a.params[k] == b.params[k]


def test_bad_arguments():
    with pytest.raises(ParamError) as e:
        town.generate("grid", 1, params={"boulevards": 99, "nope": 1})
    assert len(e.value.errors) == 2
    with pytest.raises(ParamError):
        town.generate("grid", 1, size=(100, 100))


def test_json_round_trip():
    s = _gen(4)
    d = json.loads(s.to_json())
    assert d["format_version"] == 1 and d["generator"] == "grid" and d["graph"]["nodes"] and d["district_graph"]["edges"]
    assert town.Settlement.from_json(s.to_json()).to_json() == s.to_json()


def test_structure():
    s = _gen(2, size=(1000, 1000), params={"boulevards": 4, "inner_wall": True, "gates": 4})
    assert s.districts[0].key == "plaza" and s.districts[0].kind == "core"
    assert {w.kind for w in s.walls} == {"outer", "inner"}
    assert sum(1 for b in s.roads if b.cls == "boulevard") == 4
    assert any(r.id == "town_ring_1" and r.cls == "ring" for r in s.roads)
    outer = [g for g in s.gates if g.wall == "town_wall_outer"]
    inner = [g for g in s.gates if g.wall == "town_wall_inner"]
    assert len(outer) == 4 and len(inner) >= 4
    g = {n.id: n for n in s.graph_nodes}
    assert g["town_plaza"].kind == "zone" and all(g[x.id].kind == "choke" for x in s.gates)
    assert all(not e.passable for e in s.district_edges if e.b == "outside")
    assert any(d.key.startswith("x") for d in s.districts)


def test_hearthmoor_four_axis_gates():
    s = _gen(1, size=(600, 600), params=HEARTHMOOR)
    assert len(s.gates) == 4 and sum(1 for r in s.roads if r.cls == "boulevard") == 4


def test_check_catches_breakage():
    s = _gen(2)
    s.gates[0].at = [s.centre[0], s.centre[1]]
    assert any("from wall" in e for e in town.check(s))
    s = _gen(2)
    s.walls[0].polyline = s.walls[0].polyline[:-1]
    assert any("not a closed ring" in e for e in town.check(s))
    s = _gen(2)
    s.graph_edges = [e for e in s.graph_edges if e.cls != "boulevard"]
    assert any("not connected" in e for e in town.check(s))


HEADER = """format_version: 1
name: towntest
size: [2000, 2000]
seed: 1
teams:
  A: { base: %s }
  B: { base: %s }
"""


def _spec_and_graph(s):
    """A full spec around the fragment: two team bases on roads to two gates, plus the matching flowgraph."""
    ga, gb = s.gates[0], s.gates[len(s.gates) // 2]
    base = lambda g: [round(g.at[0] + g.normal[0] * 260, 2), round(g.at[1] + g.normal[1] * 260, 2)]
    text = HEADER % (base(ga), base(gb)) + town.emit_spec(s)
    text = text.replace("lines:\n", "lines:\n  - { id: road_a, seed: 1, type: road, path: [base_A, %s] }\n"
                        "  - { id: road_b, seed: 2, type: road, path: [base_B, %s] }\n" % (ga.id, gb.id), 1)
    frag = yaml.safe_load(town.emit_flow(s))
    nodes = dict(frag["nodes"])
    nodes["A"] = {"kind": "spawn", "team": "A"}
    nodes["B"] = {"kind": "spawn", "team": "B"}
    edges = list(frag["edges"]) + ["A - %s" % ga.id, "B - %s" % gb.id]
    flow = {"format_version": 1, "name": "towntest", "nodes": nodes, "edges": edges}
    return text, flow


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 8, 16, 20])
def test_fragment_validates_and_flowgraph_matches(seed):
    s = _gen(seed, size=(2000, 2000), params={"extent_frac": 0.45})
    text, flow = _spec_and_graph(s)
    spec = schema.load_spec_text(text)
    assert len(spec.regions) == len(s.districts)
    assert sum(1 for l in spec.lines if l.type == "wall") == len(s.walls)
    assert sum(1 for c in spec.crossings if c.type == "gate") == len(s.gates)
    g = load_flowgraph_dict(flow)
    res = flow_check(g, spec, raw_paths=raw_line_paths_text(text))
    assert not res.failed(strict=True), res.lines(True)


def test_emit_deterministic():
    s = _gen(6)
    assert town.emit_spec(s) == town.emit_spec(_gen(6))
    assert town.emit_flow(s) == town.emit_flow(_gen(6))


def test_preview_renders():
    s = _gen(9)
    assert town.render_png(s, px=300).size == (300, 300)
    assert town.render_sheet([s, _gen(10)], px=200).size == (404, 200)
