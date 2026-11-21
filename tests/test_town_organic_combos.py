"""Town layout: the named layers presets on organic footprints (design: mapgen/TOWN_ORGANIC_SPEC.md section 7.7)."""
import functools
import statistics

import pytest
import yaml

from mapgen import schema, town
from mapgen.flowgraph import load_flowgraph_dict
from mapgen.flowgraph_check import check as flow_check, raw_line_paths_text
from mapgen.town import layers_params
from mapgen.town.params import ParamError

PRESETS = sorted(layers_params.PRESETS)
NEW = [n for n in PRESETS if n != "organic"]
SEEDS = list(range(1, 21))
HEADER = """format_version: 1
name: organictest
size: [1000, 1000]
seed: 1
teams:
  A: { base: %s }
  B: { base: %s }
"""
# the fills each preset must produce somewhere over 20 seeds, as (depth, fill)
EXPECT = {
    "organic-radial": {(0, "radial"), (1, "bsp"), (1, "grid")},
    "organic-voronoi": {(0, "voronoi"), (1, "grid"), (1, "bsp")},
    "organic-grid": {(0, "grid"), (1, "radial"), (1, "voronoi")},
    "organic-cells": {(0, "voronoi"), (1, "radial"), (1, "bsp")},
    "organic-mixed": {(0, "radial"), (0, "voronoi"), (0, "grid"), (0, "wedge"), (1, "bsp"), (1, "grid"), (1, "radial"), (1, "voronoi"), (2, "bsp")},
}


@functools.lru_cache(maxsize=None)
def _gen(preset, seed):
    return town.generate("organic", seed, size=(1000, 1000),
                         params={"layers": preset, "warp": "0.6", "edge": "wall" if seed % 2 else "open"})


def test_presets_resolve_and_echo():
    assert {"organic", "organic-radial", "organic-voronoi", "organic-grid", "organic-cells", "organic-mixed"} <= set(PRESETS)
    for n in PRESETS:
        r = layers_params.resolve(n, 1600.0)
        assert r == layers_params.resolve(layers_params.preset(n), 1600.0)
        assert layers_params.resolve({**r, "skipped": {}}, 1600.0)["rules"] == r["rules"]
    with pytest.raises(ParamError):
        layers_params.resolve({"rules": [{"use": "grid", "parent_use": ["nope"]}]}, 1600.0)


def test_parent_use_string_form():
    r = layers_params.resolve("0:radial;1:grid@parent_use=radial/wedge,p=0.5", 1600.0)
    assert r["rules"][1]["parent_use"] == ["radial", "wedge"] and r["rules"][0]["parent_use"] is None


@pytest.mark.parametrize("preset", PRESETS)
def test_checks_determinism_roundtrip(preset):
    for seed in SEEDS:
        s = _gen(preset, seed)
        assert town.check(s) == [], (preset, seed)
        assert town.check_determinism(s) == [], (preset, seed)
    assert town.Settlement.from_json(s.to_json()).to_json() == s.to_json()


@pytest.mark.parametrize("preset", NEW)
def test_fill_kinds_occur(preset):
    seen = set()
    for seed in SEEDS:
        s = _gen(preset, seed)
        seen |= {(n.depth, n.use) for n in s.layers}
    assert EXPECT[preset] <= seen, (preset, sorted(EXPECT[preset] - seen))


@pytest.mark.parametrize("preset", NEW)
def test_nested_mixes_and_depth(preset):
    pairs = set()
    deep = 0
    for seed in SEEDS:
        s = _gen(preset, seed)
        use = {n.id: n for n in s.layers}
        for n in s.layers:
            if n.parent:
                pairs.add((use[n.parent].use, n.use))
            deep += n.depth >= 1
    assert len(pairs) >= 4, (preset, sorted(pairs))
    assert deep >= 20


@pytest.mark.parametrize("preset", PRESETS)
def test_no_giant_districts(preset):
    for seed in SEEDS:
        s = _gen(preset, seed)
        ar = [d.area for d in s.districts if d.kind not in ("core", "citadel", "plaza")]
        assert max(ar) <= 6 * statistics.median(ar), (preset, seed, max(ar), statistics.median(ar))


@pytest.mark.parametrize("preset", NEW)
def test_bsp_does_not_dominate(preset):
    bsp = tot = 0
    for seed in SEEDS:
        s = _gen(preset, seed)
        use = {n.id: n.use for n in s.layers}
        for d in s.districts:
            if d.parent:
                tot += 1
                bsp += use[d.parent] == "bsp"
    assert bsp <= 0.35 * tot, (preset, bsp, tot)


@pytest.mark.parametrize("preset", NEW)
@pytest.mark.parametrize("seed", SEEDS[:6])
def test_flowgraph_strict(preset, seed):
    s = _gen(preset, seed)
    ga, gb = s.gates[0], s.gates[-1]
    pos = lambda g: [min(995.0, max(5.0, round(g.at[0] + g.normal[0] * 160, 2))), min(995.0, max(5.0, round(g.at[1] + g.normal[1] * 160, 2)))]
    text = HEADER % (pos(ga), pos(gb)) + town.emit_spec(s)
    text = text.replace("lines:\n", "lines:\n  - { id: road_a, seed: 1, type: road, path: [base_A, %s] }\n"
                        "  - { id: road_b, seed: 2, type: road, path: [base_B, %s] }\n" % (ga.id, gb.id), 1)
    spec = schema.load_spec_text(text)
    frag = yaml.safe_load(town.emit_flow(s))
    nodes = dict(frag["nodes"])
    nodes["A"] = {"kind": "spawn", "team": "A"}
    nodes["B"] = {"kind": "spawn", "team": "B"}
    edges = list(frag["edges"]) + ["A - %s" % ga.id, "B - %s" % gb.id]
    g = load_flowgraph_dict({"format_version": 1, "name": "organictest", "nodes": nodes, "edges": edges})
    res = flow_check(g, spec, raw_paths=raw_line_paths_text(text))
    assert not res.failed(strict=True), res.lines(True)
