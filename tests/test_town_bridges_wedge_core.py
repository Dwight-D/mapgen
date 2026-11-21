"""Bridges attach to roads, wedge fits small districts, radial core discs (design: TOWN_SPEC.md section 7.5, TOWN_ORGANIC_SPEC.md sections 7.3, 7.7; TOWN_LAYERS_SPEC.md section 4.2)."""
import copy
import math
import random

import pytest
from shapely.geometry import Point, Polygon
from shapely.geometry.polygon import orient

from layers_helpers import check_fill_contract, frame_of
from mapgen import town
from mapgen.town import fill_radial, fill_wedge, water
from mapgen.town.layers_params import PRESETS
from mapgen.town.params import resolve

PRESET_NAMES = ["organic", "organic-radial", "organic-grid", "organic-mixed"]


def _river(preset, seed, **kw):
    params = {"edge": "wall", "layers": preset, "warp": "0.5", "water": "river", "radius_frac": "0.6", "citadel": "false"}
    params.update(kw)
    return town.generate("organic", seed, size=(1000, 1000), params=params, sid="river")


def _own_errors(errs):
    """The errors of this feature: the leaf-balance checks belong to the layer subdivision."""
    return [e for e in errs if "unsplit leaf" not in e and "orphan leaf" not in e]


def _connected(s):
    ids = {n.id for n in s.graph_nodes}
    adj = {i: set() for i in ids}
    for e in s.graph_edges:
        if e.passable and e.a in adj and e.b in adj:
            adj[e.a].add(e.b)
            adj[e.b].add(e.a)
    seen = {sorted(ids)[0]}
    todo = list(seen)
    while todo:
        for m in adj[todo.pop()]:
            if m not in seen:
                seen.add(m)
                todo.append(m)
    return seen == ids


@pytest.mark.parametrize("preset", PRESET_NAMES)
@pytest.mark.parametrize("seed", [1, 2, 3, 4])
def test_bridges_attach_to_roads(preset, seed):
    s = _river(preset, seed)
    bridges = [r for r in s.roads if r.cls == "bridge"]
    assert bridges
    assert water.unattached_bridge_ends(s) == []
    assert _own_errors(town.check(s)) == []
    assert _connected(s)
    # the banks are joined by the movement graph only through bridges: dropping them splits it
    keep = [e for e in s.graph_edges if e.cls != "bridge"]
    ids = {n.id for n in s.graph_nodes}
    adj = {i: set() for i in ids}
    for e in keep:
        adj[e.a].add(e.b)
        adj[e.b].add(e.a)
    seen = {sorted(ids)[0]}
    todo = list(seen)
    while todo:
        for m in adj[todo.pop()]:
            if m not in seen:
                seen.add(m)
                todo.append(m)
    assert seen != ids


@pytest.mark.parametrize("preset", PRESET_NAMES)
def test_no_road_ends_in_the_river_except_bridges(preset):
    """The renderer draws a bridge wherever a road reaches a river: dry road ends keep off the water, bridge ends excepted. The one
    case left is a junction of several roads that all end on the bank (at most one per three towns)."""
    touching = 0
    for seed in (1, 3, 4):
        s = _river(preset, seed)
        W = Polygon(s.water["rivers"][0]["polygon"])
        bridge_nodes = {n for r in s.roads if r.cls == "bridge" for n in r.nodes}
        for r in s.roads:
            if r.cls == "bridge":
                continue
            for k in (0, -1):
                if not (r.nodes and r.nodes[k] in bridge_nodes) and W.distance(Point(r.polyline[k])) < water.BANK_TOUCH:
                    touching += 1
    assert touching <= 1


@pytest.mark.parametrize("gen", ["grid", "radial", "voronoi"])
def test_non_organic_bridges_attach(gen):
    for seed in (1, 2, 3):
        s = town.generate(gen, seed, params={"water": "river", "layers": "organic-radial"})
        assert water.unattached_bridge_ends(s) == []
        assert town.check(s) == []


def test_check_reports_and_repair_fixes_an_unattached_bridge_end():
    s = _river("organic-radial", 4)
    br = next(r for r in s.roads if r.cls == "bridge")
    nid = br.nodes[0]
    broken = copy.deepcopy(s)
    broken.roads = [r for r in broken.roads if r.cls == "bridge" or nid not in r.nodes]
    errs = town.check(broken)
    assert any("bridge %s: end %s is not on a road" % (br.id, nid) in e for e in errs)
    fixed = water.attach_bridges(broken)
    assert water.unattached_bridge_ends(fixed) == []
    assert not any("is not on a road" in e for e in town.check(fixed))
    assert any(r.id.startswith("river_conn_") for r in fixed.roads)


def test_wedge_fits_a_small_district():
    """A district of two minimum faces takes one spoke; the requested spokes and arcs are thinned until it fits."""
    P = Polygon([(0, 0), (70, 0), (70, 52), (0, 52)])
    p = resolve(fill_wedge.SUB_DEFS, 1, "t", "layers", {"spokes": 5, "cross": 2})
    fl = fill_wedge.fill(orient(P, 1.0), random.Random(1), p, frame_of(P, 0.3), 1600.0)
    check_fill_contract(P, fl, 1600.0)
    assert len(fl.faces) >= 2
    assert all(f.area >= 1600.0 for _, f in fl.faces)


def test_wedge_big_district_keeps_its_fan():
    P = Polygon([(0, 0), (400, 0), (400, 300), (0, 300)])
    p = resolve(fill_wedge.SUB_DEFS, 1, "t", "layers", {"spokes": 5, "cross": 2})
    fl = fill_wedge.fill(orient(P, 1.0), random.Random(1), p, frame_of(P, 0.3), 1600.0)
    assert len(fl.faces) >= 6


def test_wedge_faces_are_not_slivers():
    for seed in range(1, 6):
        s = town.generate("organic", seed, params={"layers": "organic-grid", "warp": "0.5"})
        for n in s.layers:
            if n.use != "wedge":
                continue
            for c in n.children:
                d = next((d for d in s.districts if d.id == c), None)
                if d is None:
                    continue
                rect = Polygon(d.polygon).minimum_rotated_rectangle.exterior.coords
                side = min(math.hypot(rect[1][0] - rect[0][0], rect[1][1] - rect[0][1]), math.hypot(rect[2][0] - rect[1][0], rect[2][1] - rect[1][1]))
                assert side >= 5.0, (seed, d.id)


@pytest.mark.parametrize("preset", ["organic-radial", "organic-grid", "organic-mixed"])
def test_wedge_appears_in_the_organic_presets(preset):
    uses = set()
    for seed in range(1, 7):
        s = town.generate("organic", seed, params={"layers": preset, "warp": "0.5"})
        uses |= {n.use for n in s.layers}
    assert "wedge" in uses
    assert any("wedge" in r["use"] for r in PRESETS[preset]["rules"])


def test_radial_core_is_larger():
    d = next(d for d in fill_radial.SUB_DEFS if d.name == "ring_frac")
    assert (d.draw_lo, d.draw_hi) == (0.52, 0.78)
    P = Polygon([(100 + 120 * math.cos(a * math.pi / 16), 100 + 120 * math.sin(a * math.pi / 16)) for a in range(32)])
    fracs = []
    for seed in range(1, 9):
        p = resolve(fill_radial.SUB_DEFS, seed, "t", "layers", {})
        fl = fill_radial.fill(orient(P, 1.0), random.Random(seed), p, frame_of(P, 0.0), 1600.0)
        check_fill_contract(P, fl, 1600.0)
        core = dict(fl.faces)["core"]
        assert core.is_valid and core.area / core.convex_hull.area > 0.95
        fracs.append(math.sqrt(core.area / math.pi) / 120.0)
    assert min(fracs) >= 0.45 and sum(fracs) / len(fracs) >= 0.55
