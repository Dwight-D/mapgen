"""Town layout: voronoi generator and its organic regime (design: mapgen/TOWN_SPEC.md section 7.2)."""
import json
import os

import pytest
import yaml

from mapgen import schema, town
from mapgen.flowgraph import load_flowgraph_dict
from mapgen.flowgraph_check import check as flow_check, raw_line_paths_text
from mapgen.town.params import ParamError
from test_cli_exit_codes import run as _run


def run(*args):
    rc, out, err = _run(*args)
    return type("R", (), {"returncode": rc, "stdout": out, "stderr": err})

SEEDS = list(range(1, 21))
REGIMES = [{}, {"organic": True}]
IDS = ["plain", "organic"]


def _gen(seed, **kw):
    return town.generate("voronoi", seed, **kw)


@pytest.mark.parametrize("params", REGIMES, ids=IDS)
@pytest.mark.parametrize("seed", SEEDS)
def test_checks_pass(seed, params):
    s = _gen(seed, params=params)
    assert town.check(s) == []
    assert town.check_determinism(s) == []
    assert 4 <= len(s.districts) <= 30 and len(s.gates) >= 2


@pytest.mark.parametrize("seed", [1, 7, 16])
def test_same_seed_identical_json(seed):
    assert _gen(seed).to_json() == _gen(seed).to_json()


def test_seeds_differ():
    assert len({_gen(s).to_json() for s in SEEDS}) == len(SEEDS)


def test_organic_is_a_regime():
    a, b = _gen(3), _gen(3, params={"organic": True})
    assert b.params["organic"] is True and a.params["organic"] is False
    assert a.to_json() != b.to_json()
    # same stable draws apart from the regime flag
    for k in ("patches", "gates", "radius_frac"):
        assert a.params[k] == b.params[k]


def test_override_does_not_shift_other_draws():
    a, b = _gen(5), _gen(5, params={"patches": 11})
    for k in ("jitter", "gates", "radius_frac", "merge_prob"):
        assert a.params[k] == b.params[k]


def test_ids_and_structure():
    s = _gen(2)
    assert s.districts[0].key == "p0" and s.districts[0].kind == "core"
    assert all(d.id == "town_d_" + d.key for d in s.districts)
    assert {w.kind for w in s.walls} == {"outer"}
    g = {n.id: n for n in s.graph_nodes}
    assert g["town_plaza"].kind == "zone" and all(g[x.id].kind == "choke" for x in s.gates)
    outside = [e for e in s.district_edges if e.b == "outside"]
    assert outside and all(not e.passable for e in outside) and sum(1 for e in outside if e.gates) >= 2


def test_bad_arguments():
    with pytest.raises(ParamError) as e:
        town.generate("voronoi", 1, params={"patches": 99, "nope": 1})
    assert len(e.value.errors) == 2


def test_json_round_trip():
    s = _gen(4)
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


@pytest.mark.parametrize("params", REGIMES, ids=IDS)
@pytest.mark.parametrize("seed", [1, 2, 3, 8])
def test_fragment_validates_and_flowgraph_matches(seed, params):
    p = dict(params)
    p["radius_frac"] = 0.45
    s = _gen(seed, size=(2000, 2000), params=p)
    text, flow = _spec_and_graph(s)
    spec = schema.load_spec_text(text)
    assert len(spec.regions) == len(s.districts)
    assert sum(1 for l in spec.lines if l.type == "wall") == 1
    assert sum(1 for c in spec.crossings if c.type == "gate") == len(s.gates)
    g = load_flowgraph_dict(flow)
    res = flow_check(g, spec, raw_paths=raw_line_paths_text(text))
    assert not res.failed(strict=True), res.lines(True)


def test_emit_deterministic():
    s = _gen(6)
    assert town.emit_spec(s) == town.emit_spec(_gen(6))
    assert town.emit_flow(s) == town.emit_flow(_gen(6))


def test_cli_generate_organic(tmp_path):
    out = str(tmp_path / "t")
    r = run("town", "generate", "voronoi", "--seed", "3", "--param", "organic=true", "--out", out)
    assert r.returncode == 0, r.stdout + r.stderr
    assert run("town", "check", os.path.join(out, "settlement.json")).returncode == 0
    assert run("town", "generate", "voronoi", "--param", "patches=99").returncode == 1
