"""Town coordinate warp (design: mapgen/TOWN_ORGANIC_SPEC.md sections 6 and 10.2)."""
import json
import os

import pytest
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

from mapgen import town
from mapgen.town import warp
from mapgen.town.params import ParamError
from test_cli_exit_codes import run

SEEDS = list(range(1, 21))
BASES = ["radial", "grid", "voronoi"]
LAYERS = "grid;min_split_area=2500"


def _gen(base, seed, warp_value="0.6", **extra):
    p = {"layers": LAYERS}
    if warp_value is not None:
        p["warp"] = warp_value
    p.update(extra)
    return town.generate(base, seed, params=p)


def _ids(s):
    return (sorted(d.id for d in s.districts), sorted(r.id for r in s.roads), sorted(n.id for n in s.graph_nodes),
            sorted(n.id for n in s.layers), sorted(g.id for g in s.gates), sorted(w.id for w in s.walls))


def _shared_borders_identical(s):
    """Every vertex of a district lying on a neighbour's boundary is a vertex of that neighbour."""
    polys = [(d.id, Polygon(d.polygon), {tuple(p) for p in d.polygon}) for d in s.districts]
    for i, (ida, pa, va) in enumerate(polys):
        for idb, pb, vb in polys[i + 1:]:
            if not pa.envelope.intersects(pb.envelope):
                continue
            for p in va:
                if p not in vb and pb.boundary.distance(Point(p)) < 0.005:
                    return "%s vertex %s lies on %s without being its vertex" % (ida, p, idb)
            for p in vb:
                if p not in va and pa.boundary.distance(Point(p)) < 0.005:
                    return "%s vertex %s lies on %s without being its vertex" % (idb, p, ida)
    return None


def _check_warped(s, base_s):
    assert town.check(s) == []
    assert town.check_determinism(s) == []
    assert _ids(s) == _ids(base_s)
    assert {e.id for e in base_s.district_edges if e.length > 3.0} <= {e.id for e in s.district_edges}
    w = s.params["warp"]
    assert 0.0 <= w["scale"] <= 1.0 and "seed" in w
    foot = Polygon(s.footprint)
    union = unary_union([Polygon(d.polygon) for d in s.districts if d.inside_wall])
    if not s.water:
        assert abs(union.area - foot.area) <= 0.01 * foot.area
    for g in s.gates:
        if g.wall:
            wl = next(x for x in s.walls if x.id == g.wall)
            assert any(abs(g.at[0] - p[0]) < 1e-9 and abs(g.at[1] - p[1]) < 1e-9 for p in wl.polyline), g.id
    return w["scale"]


@pytest.mark.parametrize("base", BASES)
@pytest.mark.parametrize("seed", SEEDS)
def test_warp_invariants(base, seed):
    s = _gen(base, seed)
    base_s = _gen(base, seed, warp_value=None)
    scale = _check_warped(s, base_s)
    assert "warp" not in base_s.params
    if scale > 0:
        assert s.footprint != base_s.footprint
        assert [d.polygon for d in s.districts] != [d.polygon for d in base_s.districts]
        assert _shared_borders_identical(s) is None


@pytest.mark.parametrize("base", BASES)
@pytest.mark.parametrize("seed", [1, 2, 3, 4])
@pytest.mark.parametrize("extra", [{"water": "river"}, {"fort": "corner"}, {"water": "river", "fort": "corner"}],
                         ids=["river", "fort", "river_fort"])
def test_warp_with_water_and_fort(base, seed, extra):
    s = _gen(base, seed, **extra)
    base_s = _gen(base, seed, warp_value=None, **extra)
    _check_warped(s, base_s)


@pytest.mark.parametrize("base", BASES)
def test_warp_absent_is_identical(base):
    plain = town.generate(base, 5, params={"layers": LAYERS})
    for v in (None, "none", 0, "0"):
        s = town.generate(base, 5, params={"layers": LAYERS, "warp": v})
        assert s.to_json() == plain.to_json()
        assert "warp" not in s.params
    plain = town.generate(base, 5)
    assert town.generate(base, 5, params={"warp": "none"}).to_json() == plain.to_json()


def test_warp_without_layers():
    s = town.generate("radial", 3, params={"warp": "0.6"})
    assert town.check(s) == [] and town.check_determinism(s) == []


def test_warp_curves_roads():
    s = _gen("grid", 2, "0.8")
    base_s = _gen("grid", 2, None)
    assert s.params["warp"]["scale"] > 0
    assert max(len(r.polyline) for r in s.roads) > 2 * max(len(r.polyline) for r in base_s.roads) // 2
    assert sum(len(r.polyline) for r in s.roads) > 3 * sum(len(r.polyline) for r in base_s.roads)


def test_densify_same_points_from_either_side():
    d = warp._Densify([], 10.0)
    a, b = (0.0, 0.0), (103.37, 41.2)
    fwd = d.seq([list(a), list(b)], False)
    rev = warp._Densify([], 10.0).seq([list(b), list(a)], False)
    assert fwd == rev[::-1]
    assert fwd[0] == list(a) and fwd[-1] == list(b)
    assert len(fwd) > 10
    ring = warp._Densify([], 10.0).seq([[0, 0], [50, 0], [50, 50]], True)
    assert [0, 0] in ring and [50, 0] in ring and [50, 50] in ring
    assert d.seq([[0, 0], [3, 0]], False) == [[0, 0], [3, 0]]


def test_densify_stitches_foreign_vertex():
    d = warp._Densify([(0.0, 0.0), (40.0, 0.0), (20.0, 0.0), (20.0, 20.0)], 10.0)
    out = d.seq([[0, 0], [40, 0]], False)
    assert [20.0, 0.0] in out and out == sorted(out)


def test_resolve_forms():
    assert warp.resolve(None) is None and warp.resolve("none") is None and warp.resolve(0) is None
    a = warp.resolve(0.5)
    assert a["amp"] == pytest.approx(0.09) and a["fine_amp"] == pytest.approx(0.006) and a["step"] == 10.0
    assert warp.resolve("0.5") == a
    b = warp.resolve("amp=0.1,wavelength=2,step=8")
    assert b["amp"] == 0.1 and b["wavelength"] == 2.0 and b["step"] == 8.0 and b["fort_damp"] == 0.8
    assert warp.resolve({"amp": 0.1, "scale": 0.5625, "seed": 7})["seed"] == 7
    assert warp.resolve(json.dumps({"amp": 0.1}))["amp"] == 0.1


def test_resolve_errors_collected():
    with pytest.raises(ParamError) as e:
        warp.resolve({"amp": 0.9, "step": 1, "bogus": 1, "wavelength": "x"})
    assert len(e.value.errors) == 4
    for bad in (1.5, -0.1, True, "abc", [1]):
        with pytest.raises(ParamError):
            warp.resolve(bad)


def test_never_fails_extreme_amplitude():
    for seed in (1, 2, 3):
        s = town.generate("radial", seed, params={"layers": LAYERS, "warp": "amp=0.35,wavelength=0.4,fine_amp=0.03"})
        assert town.check(s) == []
        assert town.check_determinism(s) == []
        assert 0.0 <= s.params["warp"]["scale"] <= 1.0


def test_unwarped_fallback_echoes_scale_zero(monkeypatch):
    base_s = town.generate("radial", 2, params={"layers": LAYERS})
    import sys
    monkeypatch.setattr(sys.modules["mapgen.town.check"], "check", lambda s: ["forced failure"])
    out = warp.apply(base_s, warp.resolve(0.6))
    assert out.params["warp"]["scale"] == 0.0
    assert [d.polygon for d in out.districts] == [d.polygon for d in base_s.districts]


def test_scale_is_fixed_point():
    s = _gen("grid", 4, "0.9")
    again = town.generate("grid", 4, params=s.params)
    assert again.to_json() == s.to_json()


def test_area_floor_with_warp():
    s = _gen("radial", 1)
    s.params["min_district_area"] = 1e9
    assert all("below min_district_area" in e for e in town.check(s) if "below" in e)
    d = s.districts[0]
    s.params["min_district_area"] = d.area / 0.7 - 1.0
    s.params["layers"] = dict(s.params["layers"], min_area=0.0)
    assert not [e for e in town.check(s) if e.startswith("district %s: area" % d.id)]


def test_emission_round_trip_and_determinism():
    s = _gen("grid", 1)
    t1, t2 = town.emit_spec(s), town.emit_spec(s)
    assert t1 == t2 and town.emit_flow(s) == town.emit_flow(s)
    import yaml
    doc = yaml.safe_load(t1)
    assert len(doc["regions"]) >= len(s.districts)


def test_cli_warp(tmp_path):
    out = str(tmp_path / "w")
    rc, so, se = run("town", "generate", "radial", "--seed", "3", "--param", "layers=grid", "--param", "warp=0.6", "--out", out)
    assert rc == 0, so + se
    js = os.path.join(out, "settlement.json")
    assert json.load(open(js))["params"]["warp"]["scale"] > 0
    assert run("town", "check", js)[0] == 0
    rc, so, se = run("town", "generate", "radial", "--param", "warp=amp=0.9,step=1")
    assert rc == 1 and so.count("warp.") == 2
    assert run("town", "generate", "radial", "--param", "warp=7")[0] == 1
    rc, so, se = run("town", "list")
    assert rc == 0 and "fort_damp" in so


def test_warped_map_passes_flowgraph_strict(tmp_path, monkeypatch):
    """A warped, layered settlement embedded in a map with two gate-linked spawns: `flowgraph check --strict` passes."""
    import shutil
    import sys
    from test_cli_exit_codes import REPO_ROOT
    towns = os.path.join(REPO_ROOT, "maps", "towns")
    shutil.copytree(os.path.join(towns, "src"), str(tmp_path / "src"))
    monkeypatch.syspath_prepend(towns)
    import build
    monkeypatch.setattr(build, "HERE", str(tmp_path))
    cfg = dict(build.MAPS["hearthmoor"])
    cfg["params"] = dict(cfg["params"], warp=0.6, layers=LAYERS)
    build.build("hearthmoor", cfg)
    rc, so, se = run("flowgraph", "check", str(tmp_path / "hearthmoor.flow.yaml"), str(tmp_path / "hearthmoor.yaml"), "--strict")
    assert rc == 0, so + se
    assert run("validate", str(tmp_path / "hearthmoor.yaml"))[0] == 0
