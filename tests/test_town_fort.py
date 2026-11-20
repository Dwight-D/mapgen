"""Town layout: fort add-on (design: mapgen/TOWN_SPEC.md section 7.4)."""
import json

import pytest
import yaml
from shapely.geometry import Point, Polygon

from mapgen import schema, town
from mapgen.flowgraph import load_flowgraph_dict
from mapgen.flowgraph_check import check as flow_check, raw_line_paths_text
from mapgen.town import fort
from mapgen.town.fort_preview import render_fort
from mapgen.town.params import ParamError

SEEDS = list(range(1, 21))
BASES = ["radial", "voronoi", "grid"]


def _gen(base, seed, **params):
    return town.generate(base, seed, params=params)


@pytest.mark.parametrize("base", BASES)
@pytest.mark.parametrize("seed", SEEDS)
def test_corner_fort_checks_pass(base, seed):
    s = _gen(base, seed, fort="corner", fort_outposts=1)
    assert town.check(s) == []
    assert town.check_determinism(s) == []
    assert s.params["fort"] == "corner"


@pytest.mark.parametrize("base", BASES)
@pytest.mark.parametrize("mode", ["centre", "peninsula", "island"])
@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 6])
def test_other_modes_pass(base, mode, seed):
    s = _gen(base, seed, fort=mode, fort_shape="star" if seed % 2 else "rectangle")
    assert town.check(s) == []
    assert town.check_determinism(s) == []


@pytest.mark.parametrize("base", BASES)
def test_same_seed_identical_json(base):
    assert _gen(base, 5, fort="corner").to_json() == _gen(base, 5, fort="corner").to_json()


def test_no_fort_is_untouched():
    plain = _gen("radial", 3)
    assert _gen("radial", 3, fort="none").to_json() == plain.to_json()
    assert "fort" not in plain.params and not any(w.towers for w in plain.walls)
    assert '"towers"' not in plain.to_json()


def test_fort_structure():
    s = _gen("radial", 2, fort="centre", fort_outposts=2)
    f = [d for d in s.districts if d.key == "fort"]
    assert len(f) == 1 and f[0].kind == "citadel" and f[0].inside_wall
    wall = next(w for w in s.walls if w.id == "town_wall_fort")
    assert wall.kind == "citadel" and len(wall.towers) >= 4
    gates = [g for g in s.gates if g.wall == wall.id]
    assert sorted(g.id for g in gates) == ["town_gate_fort_in", "town_gate_fort_out"]
    ids = {n.id: n for n in s.graph_nodes}
    assert ids["town_fort"].kind == "zone" and all(ids[g.id].kind == "choke" for g in gates)
    assert any(l.kind == "fortress" and l.id == "town_fort" for l in s.landmarks)
    outposts = [d for d in s.districts if not d.inside_wall]
    assert 1 <= len(outposts) <= 2
    for d in outposts:
        assert Polygon(s.footprint).distance(Polygon(d.polygon)) > 20
        assert any(e.a == d.id and e.b == "outside" and e.gates for e in s.district_edges)
    assert any(r.cls == "approach" for r in s.roads)
    assert any(e.gates for e in s.district_edges if "town_d_fort" in (e.a, e.b))


def test_peninsula_grows_footprint():
    base = _gen("grid", 2)
    s = _gen("grid", 2, fort="peninsula")
    assert Polygon(s.footprint).area > Polygon(base.footprint).area


def test_fort_districts_do_not_overlap_and_cover_footprint():
    s = _gen("voronoi", 3, fort="corner")
    inside = [Polygon(d.polygon) for d in s.districts if d.inside_wall]
    assert abs(sum(p.area for p in inside) - Polygon(s.footprint).area) < 0.01 * Polygon(s.footprint).area


def test_params_resolved_and_bad_arguments():
    s = _gen("radial", 1, fort="corner")
    assert s.params["fort_shape"] == "auto" and s.params["fort_size"] == 0.22 and s.params["fort_outposts"] == 0
    with pytest.raises(ParamError) as e:
        town.generate("radial", 1, params={"fort": "moat", "fort_size": 9, "fort_outposts": 7, "fort_x": 1, "nope": 1})
    assert len(e.value.errors) >= 4
    assert fort.resolve({}) == {"fort": "none", "fort_shape": "auto", "fort_size": 0.22, "fort_outposts": 0}


def test_json_round_trip_keeps_towers():
    s = _gen("grid", 4, fort="corner")
    t = town.Settlement.from_json(s.to_json())
    assert t.to_json() == s.to_json()
    assert any(w.towers for w in t.walls)
    json.loads(s.to_json())


def test_check_catches_broken_fort_gate():
    s = _gen("radial", 2, fort="corner")
    g = next(g for g in s.gates if g.id == "town_gate_fort_in")
    g.at = [s.centre[0], s.centre[1]]
    assert any("from wall" in e for e in town.check(s))


HEADER = """format_version: 1
name: forttest
size: [2000, 2000]
seed: 1
teams:
  A: { base: %s }
  B: { base: %s }
"""


@pytest.mark.parametrize("base,mode", [("radial", "corner"), ("voronoi", "centre"), ("grid", "peninsula"), ("grid", "island")])
def test_fragment_validates_and_flowgraph_matches(base, mode):
    s = town.generate(base, 3, size=(2000, 2000), params={"fort": mode, "fort_outposts": 1})
    ga = next(g for g in s.gates if g.wall == "town_wall_outer")
    gb = [g for g in s.gates if g.wall == "town_wall_outer"][-1]
    pos = lambda g: [round(g.at[0] + g.normal[0] * 260, 2), round(g.at[1] + g.normal[1] * 260, 2)]
    text = HEADER % (pos(ga), pos(gb)) + town.emit_spec(s)
    text = text.replace("lines:\n", "lines:\n  - { id: road_a, seed: 1, type: road, path: [base_A, %s] }\n"
                        "  - { id: road_b, seed: 2, type: road, path: [base_B, %s] }\n" % (ga.id, gb.id), 1)
    spec = schema.load_spec_text(text)
    assert len(spec.regions) == len(s.districts)
    assert sum(1 for c in spec.crossings if c.type == "gate") == len(s.gates)
    frag = yaml.safe_load(town.emit_flow(s))
    nodes = dict(frag["nodes"])
    nodes["A"] = {"kind": "spawn", "team": "A"}
    nodes["B"] = {"kind": "spawn", "team": "B"}
    edges = list(frag["edges"]) + ["A - %s" % ga.id, "B - %s" % gb.id]
    g = load_flowgraph_dict({"format_version": 1, "name": "forttest", "nodes": nodes, "edges": edges})
    res = flow_check(g, spec, raw_paths=raw_line_paths_text(text))
    assert not res.failed(strict=True), res.lines(True)


def test_preview_draws_towers():
    s = _gen("radial", 3, fort="corner", fort_outposts=1)
    assert render_fort(s, px=300).size == (300, 300)


@pytest.mark.parametrize("base", BASES)
@pytest.mark.parametrize("mode", ["corner", "centre"])
def test_inner_fort_with_river_never_crashes(base, mode):
    """An inner fort on a river settlement is valid (open-shore wall ranges survive the carve) or reports GenerationError."""
    ok = 0
    for seed in SEEDS:
        try:
            s = _gen(base, seed, fort=mode, water="river")
        except town.GenerationError:
            continue
        ok += 1
        assert town.check(s) == [], (base, mode, seed)
        assert any(d.kind == "citadel" for d in s.districts)
    assert ok >= 18, (base, mode, ok)
