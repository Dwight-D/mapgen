"""Town layout: core model, radial generator, checks, determinism, emitters, CLI (design: mapgen/TOWN_SPEC.md)."""
import json
import os

import pytest
import yaml

from mapgen import schema, town
from mapgen.flowgraph import load_flowgraph_dict
from mapgen.flowgraph_check import check as flow_check, raw_line_paths_text
from mapgen.town.emit_spec import wall_gate_types
from mapgen.town.params import ParamError
from test_cli_exit_codes import run as _run


def run(*args):
    rc, out, err = _run(*args)
    return type("R", (), {"returncode": rc, "stdout": out, "stderr": err})

SEEDS = list(range(1, 21))


def _gen(seed, **kw):
    return town.generate("radial", seed, **kw)


@pytest.mark.parametrize("seed", SEEDS)
def test_checks_pass(seed):
    s = _gen(seed)
    assert town.check(s) == []
    assert town.check_determinism(s) == []


@pytest.mark.parametrize("seed", [1, 7, 16])
def test_same_seed_identical_json(seed):
    assert _gen(seed).to_json() == _gen(seed).to_json()


def test_seeds_differ():
    assert len({_gen(s).to_json() for s in SEEDS}) == len(SEEDS)


def test_params_resolved_and_override():
    s = _gen(3)
    assert 4 <= s.params["spokes"] <= 14 and s.params["min_district_area"] == 1600.0
    t = _gen(3, params={"spokes": 6, "rings": 2, "gates": 4, "citadel": True, "elongation": 1.0})
    assert t.params["spokes"] == 6 and t.params["rings"] == 2 and len(t.gates) == 4
    assert any(l.kind == "citadel" for l in t.landmarks)
    assert town.check(t) == []


def test_override_does_not_shift_other_draws():
    a, b = _gen(5), _gen(5, params={"spokes": 7})
    for k in ("radius_frac", "rings", "ring_spacing", "wall_irregularity", "rotation", "gates"):
        assert a.params[k] == b.params[k]


def test_bad_arguments():
    with pytest.raises(ParamError) as e:
        town.generate("radial", 1, params={"spokes": 99, "nope": 1})
    assert len(e.value.errors) == 2
    with pytest.raises(ParamError):
        town.generate("nope", 1)
    with pytest.raises(ParamError):
        town.generate("radial", 1, size=(100, 100))
    with pytest.raises(ParamError):
        town.generate("radial", 1, sid="Bad-Id")


def test_json_round_trip():
    s = _gen(4)
    d = json.loads(s.to_json())
    assert d["format_version"] == 1 and d["graph"]["nodes"] and d["district_graph"]["edges"]
    assert town.Settlement.from_json(s.to_json()).to_json() == s.to_json()


def test_structure():
    s = _gen(2, params={"spokes": 6, "rings": 2, "spoke_dropout": 0.0})
    # plaza + (rings + 1) annuli x spokes
    assert len(s.districts) == 1 + 3 * 6
    assert {w.kind for w in s.walls} == {"outer"}
    g = {n.id: n for n in s.graph_nodes}
    for gate in s.gates:
        assert g[gate.id].kind == "choke"
    assert g["town_plaza"].kind == "zone"
    outside = [e for e in s.district_edges if e.b == "outside"]
    assert len(outside) == 6                      # every outer wedge touches the wall
    assert all(not e.passable for e in outside)
    assert sum(1 for e in outside if e.gates) >= 2


def test_check_catches_breakage():
    s = _gen(2)
    s.gates[0].at = [s.centre[0], s.centre[1]]    # gate off the wall
    assert any("from wall" in e for e in town.check(s))
    s = _gen(2)
    s.walls[0].polyline = s.walls[0].polyline[:-1]
    assert any("not a closed ring" in e for e in town.check(s))
    s = _gen(2)
    for e in s.district_edges:
        e.gates = []                              # wall without openings: nothing reachable
    assert any("not reachable" in e for e in town.check(s))
    s = _gen(2)
    s.graph_edges = [e for e in s.graph_edges if e.cls != "artery"]
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


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 8, 16])
def test_fragment_validates_and_flowgraph_matches(seed):
    s = _gen(seed, size=(2000, 2000), params={"radius_frac": 0.45})
    text, flow = _spec_and_graph(s)
    spec = schema.load_spec_text(text)
    assert len(spec.regions) == len(s.districts)
    wall_type, gate_type = wall_gate_types()
    assert sum(1 for l in spec.lines if l.type == wall_type) == 1
    assert sum(1 for c in spec.crossings if c.type == gate_type) == len(s.gates)
    g = load_flowgraph_dict(flow)
    res = flow_check(g, spec, raw_paths=raw_line_paths_text(text))
    assert not res.failed(strict=True), res.lines(True)


def test_emit_deterministic():
    s = _gen(6)
    assert town.emit_spec(s) == town.emit_spec(_gen(6))
    assert town.emit_flow(s) == town.emit_flow(_gen(6))


def test_preview_renders(tmp_path):
    s = _gen(9)
    im = town.render_png(s, px=300)
    assert im.size == (300, 300)
    assert town.render_sheet([s, _gen(10)], px=200).size == (404, 200)


def test_cli_generate_check_preview(tmp_path):
    out = str(tmp_path / "t")
    r = run("town", "generate", "radial", "--seed", "3", "--size", "1200", "1200", "--out", out)
    assert r.returncode == 0, r.stdout + r.stderr
    for f in ("settlement.json", "preview.png", "spec_fragment.yaml", "flow_fragment.yaml"):
        assert os.path.getsize(os.path.join(out, f)) > 0
    js = os.path.join(out, "settlement.json")
    assert run("town", "check", js).returncode == 0
    png = str(tmp_path / "sheet.png")
    assert run("town", "preview", js, js, "--out", png).returncode == 0
    assert run("town", "preview", js, "--out", png).returncode == 1      # exists, no --force
    assert run("town", "generate", "radial", "--param", "spokes=99").returncode == 1
    assert run("town", "check", str(tmp_path / "missing.json")).returncode == 1
    assert run("town", "list").returncode == 0


def test_cli_same_seed_same_files(tmp_path):
    a, b = str(tmp_path / "a"), str(tmp_path / "b")
    for o in (a, b):
        assert run("town", "generate", "radial", "--seed", "8", "--out", o).returncode == 0
    for f in ("settlement.json", "spec_fragment.yaml", "flow_fragment.yaml"):
        assert open(os.path.join(a, f), "rb").read() == open(os.path.join(b, f), "rb").read()
