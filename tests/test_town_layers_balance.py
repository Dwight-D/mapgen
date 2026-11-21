"""Town layers: the final oversized-leaf pass and the leaf-balance check (design: mapgen/TOWN_ORGANIC_SPEC.md section 7.8)."""
import statistics

import pytest

from mapgen import town
import importlib

town_check = importlib.import_module("mapgen.town.check")
EXEMPT = town_check._EXEMPT
CASES = [
    ("organic-radial", dict(edge="wall", water="river")),
    ("organic-grid", dict(edge="wall", fort="corner")),
    ("organic-mixed", dict(edge="wall", water="bay")),
    ("organic-voronoi", dict(edge="open")),
]


def _gen(preset, seed, **kw):
    p = {"layers": preset, "radius_frac": "0.55"}
    p.update({k: str(v) for k, v in kw.items()})
    return town.generate("organic", seed, size=(1000, 1000), params=p)


def _balance(s):
    return [e for e in town_check._leaf_balance(s)]


@pytest.mark.parametrize("preset,kw", CASES)
@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 6])
def test_no_unsplit_or_orphan_leaf(preset, kw, seed):
    try:
        s = _gen(preset, seed, **kw)
    except town.GenerationError as e:       # a fort that fits nowhere at this seed is the generator's refusal, not a layers result
        pytest.skip(str(e))
    assert _balance(s) == []
    ds = [d.area for d in s.districts if d.kind not in EXEMPT]
    assert max(ds) <= 3.0 * statistics.median(ds)
    assert town.check(s) == []


def test_rivermere_spur_district_is_split():
    # the macro district cut by a parent road spur (skipped `road_inside` before the final pass) is subdivided
    s = _gen("organic-radial", 4, edge="wall", water="river", warp="0.5")
    sk = s.params["layers"]["skipped"]
    assert "road_inside" not in sk.values()
    assert town.check(s) == []


def test_final_pass_splits_coarse_blocks():
    layers = {"max_depth": 2, "min_split_area": 3000, "balance": True, "rules": [{"depth": 0, "use": "grid", "params": {"block": 140}}]}
    s = town.generate("organic", 3, size=(1000, 1000), params={"layers": layers, "radius_frac": "0.55"})
    assert any(n.rule == -1 for n in s.layers)           # the pass committed splits (rule index -1)
    ds = [d.area for d in s.districts if d.kind not in EXEMPT]
    assert max(ds) <= 3.0 * statistics.median(ds)


def test_deterministic():
    a = _gen("organic-mixed", 3, edge="wall", water="bay")
    b = _gen("organic-mixed", 3, edge="wall", water="bay")
    assert a.to_json() == b.to_json()


def test_check_flags_unsplit_leaf():
    s = _gen("organic-radial", 2, edge="wall")
    big = sorted((d for d in s.districts if d.kind not in EXEMPT), key=lambda d: d.id)[0]
    big.area = 100000.0
    errs = town_check._leaf_balance(s)
    assert any("unsplit leaf" in e and big.id in e for e in errs)


def test_check_flags_orphan_leaf(monkeypatch):
    monkeypatch.setattr(town_check, "UNSPLIT_RATIO", 1e9)
    s = _gen("organic-radial", 2, edge="wall")
    nodes = {n.id: n for n in s.layers}
    leaves = {d.id: d for d in s.districts}
    for n in s.layers:
        sibs = [c for c in n.children if c in nodes]
        kids = [c for c in n.children if c in leaves and leaves[c].kind not in EXEMPT]
        if kids and sibs:
            under = []
            todo = list(sibs)
            while todo:
                m = nodes[todo.pop()]
                for k in m.children:
                    if k in nodes:
                        todo.append(k)
                    elif k in leaves:
                        under.append(leaves[k].area)
            if len(under) >= 3:
                leaves[kids[0]].area = 4.0 * statistics.median(under) + 1.0
                errs = town_check._leaf_balance(s)
                assert any("orphan leaf" in e and kids[0] in e for e in errs)
                return
    pytest.skip("no node with a leaf child and subdivided siblings at this seed")
