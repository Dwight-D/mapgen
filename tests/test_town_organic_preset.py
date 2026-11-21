"""Town layout: the `layers=organic` preset, `params_random`, and the mixed-fill variety metrics
(design: mapgen/TOWN_ORGANIC_SPEC.md sections 7.6, 10.2, 12.1)."""
import copy
import math
import statistics

import pytest
import yaml
from shapely.geometry import Polygon

from mapgen import schema, town
from mapgen.flowgraph import load_flowgraph_dict
from mapgen.flowgraph_check import check as flow_check, raw_line_paths_text
from mapgen.town import layers_params
from mapgen.town.params import ParamError

SEEDS = list(range(1, 21))
HEADER = """format_version: 1
name: organictest
size: [1000, 1000]
seed: 1
teams:
  A: { base: %s }
  B: { base: %s }
"""


def _gen(seed, **extra):
    p = {"layers": "organic", "warp": "0.9", "edge": "wall" if seed % 2 else "open"}
    p.update(extra)
    return town.generate("organic", seed, size=(1000, 1000), params=p)


def _corners(poly, tol=2.5, ang=35.0):
    c = list(poly.simplify(tol).exterior.coords)[:-1]
    k = 0
    for i in range(len(c)):
        a, b, e = c[i - 1], c[i], c[(i + 1) % len(c)]
        u, v = (b[0] - a[0], b[1] - a[1]), (e[0] - b[0], e[1] - b[1])
        l = math.hypot(*u) * math.hypot(*v)
        if l == 0:
            continue
        k += math.degrees(math.acos(max(-1.0, min(1.0, (u[0] * v[0] + u[1] * v[1]) / l)))) >= ang
    return k


def _shape_classes(s):
    c = {"tri": 0, "quad": 0, "poly": 0}
    for d in s.districts:
        if d.kind == "core":
            continue
        k = _corners(Polygon(d.polygon))
        c["tri" if k <= 3 else "quad" if k == 4 else "poly"] += 1
    return c


def _rot_spread(s):
    rots = [n.params["rot"] % 90.0 for n in s.layers]
    return any(min(abs(a - b), 90 - abs(a - b)) >= 15 for a in rots for b in rots)


def test_preset_expands_to_canonical_form():
    r = layers_params.resolve("organic", 1600.0)
    assert r["max_depth"] == 3 and r["align"] == "edge"
    assert r["rules"][1]["params_random"] is True and r["rules"][0]["params_random"] is False
    assert r == layers_params.resolve(layers_params.preset("organic"), 1600.0)
    assert layers_params.resolve({**r, "skipped": {}}, 1600.0)["rules"] == r["rules"]      # echo round trip
    with pytest.raises(ParamError):
        layers_params.resolve({"rules": [{"use": "grid", "params_random": "yes"}]}, 1600.0)


@pytest.mark.parametrize("seed", SEEDS[:6])
def test_preset_checks_and_determinism(seed):
    s = _gen(seed)
    assert town.check(s) == []
    assert town.check_determinism(s) == []
    assert town.Settlement.from_json(s.to_json()).to_json() == s.to_json()
    assert s.params["layers"]["rules"][1]["params_random"] is True


def test_all_fills_occur_and_params_random_draws():
    uses, shears, fans = set(), [], []
    for seed in SEEDS:
        for n in _gen(seed).layers:
            uses.add(n.use)
            if n.use == "grid":
                shears.append(n.params["shear"])
                fans.append(n.params["fan"])
    assert {"grid", "bsp", "wedge", "follow", "voronoi"} <= uses
    assert max(shears) > 5 and min(shears) < 5 and max(fans) > 1


def test_identity_stability_when_a_rule_for_another_district_is_added():
    base = layers_params.resolve("organic", 1600.0)
    spec = copy.deepcopy(base)
    spec["rules"].insert(0, {"key": "a0s0", "use": "none", "depth": [0, 0]})
    for seed in (2, 5, 9):
        a = town.generate("organic", seed, (1000, 1000), {"layers": base})
        b = town.generate("organic", seed, (1000, 1000), {"layers": spec})
        sig = lambda n: (n.use, n.params, n.children)      # not `rule` (an index the added rule shifts) nor polygons (stitching adds neighbour vertices)
        ka = {n.id: sig(n) for n in a.layers if "_d_a0s0" not in n.id}
        kb = {n.id: sig(n) for n in b.layers if "_d_a0s0" not in n.id}
        assert ka and ka == kb
        da = {d.id: (d.kind, d.key) for d in a.districts if "_d_a0s0" not in d.id}
        db = {d.id: (d.kind, d.key) for d in b.districts if "_d_a0s0" not in d.id}
        assert da == db


def test_variety_metrics_over_20_seeds():
    classes_ok = rot_ok = 0
    for seed in SEEDS:
        s = _gen(seed)
        assert town.check(s) == []
        c = _shape_classes(s)
        classes_ok += sum(1 for v in c.values() if v >= 2) >= 3
        rot_ok += _rot_spread(s)
        med = statistics.median(d.area for d in s.districts)
        big = max(d.area for d in s.districts if d.kind not in ("core", "citadel"))
        assert big <= 6 * med, (seed, big, med)
    assert classes_ok >= 18 and rot_ok >= 18


@pytest.mark.parametrize("seed", SEEDS[:8])
def test_preset_flowgraph_strict(seed):
    s = _gen(seed)
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
