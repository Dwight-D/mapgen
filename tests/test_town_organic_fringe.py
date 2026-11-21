"""Town layout: edge=fade fringe, hamlets, density (design: mapgen/TOWN_ORGANIC_SPEC.md sections 5.3, 9, 10.2)."""
import json

import pytest
from shapely.geometry import LineString, Point, Polygon

from mapgen import town
from mapgen.town import graph
from mapgen.town.params import ParamError
from mapgen.town.render import render_png

SEEDS = list(range(1, 21))


def _gen(seed, **extra):
    p = {"edge": "fade"}
    p.update(extra)
    return town.generate("organic", seed, size=(2000, 2000), params=p)


@pytest.mark.parametrize("seed", SEEDS)
def test_fade_checks_determinism_round_trip(seed):
    s = _gen(seed)
    assert town.check(s) == []
    assert town.check_determinism(s) == []
    assert s.walls == [] and len(s.gates) >= 3
    assert all(g.kind == "entry" and g.wall == "" for g in s.gates)
    assert town.Settlement.from_json(s.to_json()).to_json() == s.to_json()
    assert 80.0 <= s.params["fade_len"] <= 400.0


@pytest.mark.parametrize("seed", SEEDS)
def test_fringe_districts(seed):
    s = _gen(seed)
    foot = Polygon(s.footprint)
    out = [d for d in s.districts if d.kind == "outskirt"]
    assert len(out) >= 3
    roads = [LineString(r.polyline) for r in s.roads]
    for d in out:
        poly = Polygon(d.polygon)
        assert not d.inside_wall and d.area >= s.params["min_district_area"]
        assert 0.0 < d.density < 0.7
        assert poly.intersection(foot).area < 1.0
        assert any(r.intersection(poly.boundary.buffer(0.3)).length > 1.0 for r in roads)
    # every district is reachable from outside and every entry is listed on a district edge
    listed = {g for e in s.district_edges for g in e.gates}
    assert {g.id for g in s.gates} <= listed


def test_enough_clusters_over_sweep():
    counts = [sum(d.kind == "outskirt" for d in _gen(sd).districts) for sd in SEEDS]
    assert min(counts) >= 3 and sum(c >= 8 for c in counts) >= 12
    assert any(any(r.cls == "street" for r in _gen(sd).roads) for sd in SEEDS[:6])


def test_hamlets_param():
    s = _gen(3, hamlets=0)
    assert not [r for r in s.roads if r.cls == "street"]
    assert town.check(s) == []
    s = _gen(3, hamlets=6)
    assert town.check(s) == []


def test_fade_params_only_with_fade():
    with pytest.raises(ParamError):
        town.generate("organic", 1, size=(2000, 2000), params={"edge": "open", "fade_len": 200})
    assert "fade_len" not in town.generate("organic", 1, size=(2000, 2000), params={"edge": "open"}).params


def test_density_decreases_with_distance():
    bins = {}
    for sd in SEEDS:
        s = _gen(sd)
        hub = Point(s.centre)
        for d in s.districts:
            if d.kind == "plaza":
                continue
            r = Polygon(d.polygon).centroid.distance(hub)
            bins.setdefault(min(int(r // 250), 3), []).append(d.density)
    means = [sum(v) / len(v) for k, v in sorted(bins.items())]
    assert len(means) >= 3 and all(means[i] > means[i + 1] for i in range(len(means) - 1)), means


def test_density_json_and_default():
    s = _gen(2)
    d = json.loads(s.to_json())
    assert any("density" in x for x in d["districts"])
    assert all("density" not in x for x in json.loads(town.generate("organic", 2, size=(2000, 2000), params={"edge": "open"}).to_json())["districts"])


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 6])
def test_fade_with_warp_and_layers(seed):
    s = _gen(seed, warp="0.7", layers="organic")
    assert town.check(s) == []
    assert town.check_determinism(s) == []
    assert all(d.depth == 0 for d in s.districts if d.kind == "outskirt")


def test_fade_with_fort_rejected():
    with pytest.raises(ParamError):
        _gen(1, fort="citadel")


def test_preview_draws_fringe():
    s = _gen(1)
    img = render_png(s, 400)
    assert img.size == (400, 400)


@pytest.mark.parametrize("seed", [10, 12, 19])
def test_fade_fragment_with_hamlets_validates(seed):
    from mapgen import schema
    s = _gen(seed, hamlets=6)
    text = ("format_version: 1\nname: fadetest\nsize: [2000, 2000]\nseed: 1\nteams:\n  A: { base: [100, 100] }\n  B: { base: [1900, 1900] }\n"
            + town.emit_spec(s))
    spec = schema.load_spec_text(text)
    assert len(spec.regions) == len(s.districts)
