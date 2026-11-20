"""Town layers: core, grid fill, parameters, checks (design: mapgen/TOWN_LAYERS_SPEC.md)."""
import json
import random

import pytest
import yaml

from layers_helpers import check_fill_contract, frame_of, polygons
from mapgen import schema, town
from mapgen.flowgraph import load_flowgraph_dict
from mapgen.flowgraph_check import check as flow_check, raw_line_paths_text
from mapgen.town import fill_grid, layers_params
from mapgen.town.params import ParamError, resolve

SEEDS = list(range(1, 21))
BASES = ["radial", "voronoi", "grid"]


def _gen(base, seed, layers="grid;min_split_area=2500", **extra):
    p = {"layers": layers}
    p.update(extra)
    return town.generate(base, seed, params=p)


def _fill(mod, P, seed=1, min_area=1600.0):
    p = resolve(mod.SUB_DEFS, seed, "t", "layers")
    return mod.fill(P, random.Random(seed), p, frame_of(P), min_area)


def test_grid_fill_contract():
    convex, nonconvex = polygons()
    for P in convex + nonconvex:
        fl = _fill(fill_grid, P)
        check_fill_contract(P, fl, 1600.0)
        assert [(k, q.wkt) for k, q in fl.faces] == [(k, q.wkt) for k, q in _fill(fill_grid, P).faces]


@pytest.mark.parametrize("base", BASES)
@pytest.mark.parametrize("seed", SEEDS)
def test_grid_layers_checks(base, seed):
    s = _gen(base, seed)
    top = town.generate(base, seed)
    assert town.check(s) == []
    assert town.check_determinism(s) == []
    assert len(s.districts) > len(top.districts)
    assert s.layers
    for d in s.districts:
        if d.depth:
            assert d.id.startswith(d.parent + "_")
    assert sum(d.area for d in s.districts) == pytest.approx(sum(d.area for d in top.districts), rel=0.01)


def test_absent_layers_identical():
    for base in BASES:
        a = town.generate(base, 3)
        b = town.generate(base, 3, params={"layers": "none"})
        assert a.to_json() == b.to_json()
        assert "layers" not in json.loads(a.to_json())


def test_depth_two_and_ids():
    s = _gen("radial", 2, "max_depth=2;0:grid;1:voronoi")
    assert town.check(s) == []
    assert max(d.depth for d in s.districts) == 2
    assert any(n.depth == 1 for n in s.layers)
    ids = [d.id for d in s.districts] + [r.id for r in s.roads]
    assert len(ids) == len(set(ids)) and max(len(i) for i in ids) <= schema.ID_MAX_LEN


def test_identity_stability_other_kinds():
    base = town.generate("radial", 4)
    kinds = sorted({d.kind for d in base.districts if d.kind not in ("core", "citadel", "harbour")})
    assert len(kinds) >= 2
    a = _gen("radial", 4, {"min_split_area": 2500, "rules": [{"kind": [kinds[0]], "use": "grid"}]})
    b = _gen("radial", 4, {"min_split_area": 2500, "rules": [{"kind": [kinds[0]], "use": "grid"}, {"kind": [kinds[1]], "use": "grid"}]})
    ids_a = {d.id for d in a.districts}
    for d in base.districts:
        if d.kind == kinds[1]:
            assert d.id in ids_a                       # untouched when no rule matches it
    la = {d.id: d.polygon for d in a.districts if d.parent}
    lb = {d.id: d.polygon for d in b.districts if d.parent}
    k0 = {d.id for d in base.districts if d.kind == kinds[0]}
    for k, v in la.items():
        if any(k.startswith(p + "_") for p in k0):
            assert k in lb


def test_seed_changes_layout():
    assert _gen("radial", 1).to_json() != _gen("radial", 2).to_json()


def test_mixed_rules_and_skips():
    s = _gen("voronoi", 5, "0:grid@kind=market;0:radial@kind=craft;0:voronoi@kind=residential")
    assert town.check(s) == []
    assert {n.use for n in s.layers} <= {"grid", "radial", "voronoi"}
    for k in s.params["layers"]["skipped"]:
        assert k.startswith("town_d_")


@pytest.mark.parametrize("extra", [{"water": "river"}, {"water": "bay"}, {"fort": "corner"}])
@pytest.mark.parametrize("base,seed", [("radial", 3), ("voronoi", 2), ("grid", 4)])
def test_with_water_and_fort(base, seed, extra):
    s = _gen(base, seed, **extra)
    assert town.check(s) == []
    assert town.check_determinism(s) == []


def test_boundary_and_graph_modes():
    a = _gen("radial", 3, "grid;graph=all")
    b = _gen("radial", 3, "grid;graph=major")
    assert len(a.graph_nodes) >= len(b.graph_nodes)
    c = _gen("radial", 3, "grid;boundary=roads_only")
    assert town.check(c) == []
    d = _gen("radial", 3, "grid;max_districts=60")
    assert len(d.districts) <= 60


def test_params_forms():
    canon = {"max_depth": 2, "rules": [{"depth": [0, 0], "kind": ["market"], "use": {"grid": 2}, "p": 0.8,
                                         "params": {"block_ratio": 1.0}}, {"depth": 1, "use": "radial"}]}
    a = layers_params.resolve(canon, 1600)
    b = layers_params.resolve("0:grid*2@kind=market,p=0.8,sub.block_ratio=1.0;1:radial;max_depth=2", 1600)
    assert a == b
    assert layers_params.resolve(json.dumps(canon), 1600) == a
    assert layers_params.resolve(a, 1600) == a
    assert layers_params.resolve("none", 1600) is None
    assert layers_params.resolve("grid", 1600)["rules"][0]["use"] == {"grid": 1.0}


def test_params_errors_collected():
    with pytest.raises(ParamError) as e:
        layers_params.resolve({"max_depth": 9, "bogus": 1, "rules": [{"use": "hex", "p": 2}, {"use": "grid", "params": {"nope": 1}}]}, 1600)
    msg = " | ".join(e.value.errors)
    for frag in ("max_depth", "bogus", "hex", "rules[0].p", "nope"):
        assert frag in msg
    with pytest.raises(ParamError):
        town.generate("radial", 1, params={"layers": "bogus"})


def test_json_round_trip_and_old_json():
    s = _gen("radial", 2)
    assert town.Settlement.from_json(s.to_json()).to_json() == s.to_json()
    old = town.generate("radial", 2)
    assert town.Settlement.from_json(old.to_json()).layers == []


def test_check_catches_breakage():
    s = _gen("radial", 2)
    s.layers[0].children = s.layers[0].children[:-1]
    assert town.check(s)


HEADER = """format_version: 1
name: towntest
size: [2000, 2000]
seed: 1
teams:
  A: { base: %s }
  B: { base: %s }
"""


@pytest.mark.parametrize("base,seed", [("radial", 2), ("grid", 3), ("voronoi", 1)])
def test_fragment_loads_and_flow(base, seed):
    s = town.generate(base, seed, size=(2000, 2000), params={"layers": "grid"})
    ga, gb = s.gates[0], s.gates[len(s.gates) // 2]

    def bp(g):
        return [round(g.at[0] + g.normal[0] * 260, 2), round(g.at[1] + g.normal[1] * 260, 2)]
    text = HEADER % (bp(ga), bp(gb)) + town.emit_spec(s)
    text = text.replace("lines:\n", "lines:\n  - { id: road_a, seed: 1, type: road, path: [base_A, %s] }\n"
                        "  - { id: road_b, seed: 2, type: road, path: [base_B, %s] }\n" % (ga.id, gb.id), 1)
    spec = schema.load_spec_text(text)
    assert sum(1 for r in spec.regions if r.type == "town") == len(s.districts)
    frag = yaml.safe_load(town.emit_flow(s))
    nodes = dict(frag["nodes"])
    nodes["A"] = {"kind": "spawn", "team": "A"}
    nodes["B"] = {"kind": "spawn", "team": "B"}
    edges = list(frag["edges"]) + ["A - %s" % ga.id, "B - %s" % gb.id]
    g = load_flowgraph_dict({"format_version": 1, "name": "towntest", "nodes": nodes, "edges": edges})
    res = flow_check(g, spec, raw_paths=raw_line_paths_text(text))
    assert not res.failed(strict=True), res.lines(True)
    assert town.emit_spec(s) == town.emit_spec(town.generate(base, seed, size=(2000, 2000), params={"layers": "grid"}))


def test_cli_and_preview(tmp_path, capsys):
    from mapgen.town import cli
    import argparse
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers()
    cli.add_parser(sub, float)
    out = str(tmp_path / "o")
    a = ap.parse_args(["town", "generate", "radial", "--seed", "2", "--param", "layers=grid;min_split_area=2500", "--out", out])
    assert a.func(a) == 0
    assert (tmp_path / "o" / "settlement.json").exists() and (tmp_path / "o" / "preview.png").exists()
    a = ap.parse_args(["town", "generate", "radial", "--param", "layers=0:hex@p=3;max_depth=9", "--out", out])
    assert a.func(a) == 1
    assert "3 error(s)" in capsys.readouterr().out


def test_preview_siblings_differ_in_grey():
    import numpy as np
    s = _gen("radial", 2)
    img = np.asarray(town.render_png(s, px=600).convert("L"), dtype=float)
    assert img.shape == (600, 600)
    n = next(n for n in s.layers if len(n.children) >= 2)
    from shapely.geometry import Polygon
    c = [Polygon(next(d for d in s.districts if d.id == k).polygon) for k in n.children[:2]]
    xs = [p[0] for p in s.footprint]
    ys = [p[1] for p in s.footprint]
    half = max(max(xs) - min(xs), max(ys) - min(ys)) / 2.0 * 1.12
    cx, cy = (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0

    def mean(poly):
        q = poly.representative_point()
        px = int((q.x - cx) / (2 * half) * 600 + 300)
        py = int(300 - (q.y - cy) / (2 * half) * 600)
        return img[py - 2:py + 3, px - 2:px + 3].mean()
    assert abs(mean(c[0]) - mean(c[1])) > 3
