"""Town organic fills: bsp, wedge, follow, grid shear/fan, align random, Unfit fallback (design: mapgen/TOWN_ORGANIC_SPEC.md section 7)."""
import math
import random

import pytest
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.polygon import orient

from layers_helpers import check_fill_contract, frame_of, polygons
from mapgen import town
from mapgen.town import fill_bsp, fill_follow, fill_grid, fill_wedge
from mapgen.town.layerfill import Frame, Unfit
from mapgen.town.params import resolve

MIN = 1600.0
MODS = {"bsp": fill_bsp, "wedge": fill_wedge, "follow": fill_follow, "grid": fill_grid}


def road_frame(P, rot=0.3):
    """A frame whose parent road runs along the longest boundary edge of P."""
    c = list(orient(P, 1.0).exterior.coords)
    i = max(range(len(c) - 1), key=lambda k: math.hypot(c[k + 1][0] - c[k][0], c[k + 1][1] - c[k][1]))
    f = frame_of(P, rot)
    return Frame(f.origin, f.rot, LineString([c[i], c[i + 1]]))


def run(mod, P, seed=1, min_area=MIN, **over):
    p = resolve(mod.SUB_DEFS, seed, "t", "layers", over)
    return mod.fill(orient(P, 1.0), random.Random(seed), p, road_frame(P), min_area)


def shapes():
    convex, nonconvex = polygons()
    return convex + nonconvex


@pytest.mark.parametrize("name", ["bsp", "wedge", "follow"])
def test_fill_contract(name):
    n = 0
    for P in shapes():
        P = orient(P, 1.0)
        try:
            fl = run(MODS[name], P)
        except Unfit:
            continue
        n += 1
        check_fill_contract(P, fl, MIN)
        again = run(MODS[name], P)
        assert [(k, q.wkt) for k, q in fl.faces] == [(k, q.wkt) for k, q in again.faces]
        assert [(r.key, r.polyline) for r in fl.roads] == [(r.key, r.polyline) for r in again.roads]
    assert n >= 28, "%s fitted only %d of %d polygons" % (name, n, len(shapes()))


@pytest.mark.parametrize("name", ["bsp", "wedge", "follow"])
def test_fill_seed_changes_layout(name):
    P = shapes()[0]
    a, b = run(MODS[name], P, seed=1), run(MODS[name], P, seed=2)
    assert [q.wkt for _, q in a.faces] != [q.wkt for _, q in b.faces]


def test_bsp_variety_and_roads():
    counts = set()
    for P in shapes():
        fl = run(fill_bsp, P)
        counts.update(len(q.exterior.coords) - 1 for _, q in fl.faces)
        assert len(fl.faces) >= (3 if P.area > 20000 else 2)
        assert any(r.cls == "artery" for r in fl.roads)
    assert len(counts) >= 3
    fl = run(fill_bsp, shapes()[0], road_depth=0, main_road=False, cells=12)
    assert len(fl.roads) >= 1 and all(r.cls == "street" for r in fl.roads)


@pytest.mark.parametrize("name", ["bsp", "wedge", "follow"])
def test_no_slivers(name):
    for P in shapes():
        try:
            fl = run(MODS[name], orient(P, 1.0))
        except Unfit:
            continue
        for k, q in fl.faces:
            r = list(q.minimum_rotated_rectangle.exterior.coords)
            w = min(math.hypot(r[1][0] - r[0][0], r[1][1] - r[0][1]), math.hypot(r[2][0] - r[1][0], r[2][1] - r[1][1]))
            assert w >= 8.0, "%s face %s is a sliver (%.1f m wide)" % (name, k, w)


def test_bsp_unroaded_borders():
    """Deep cuts are borders only: more faces than roads' enclosed cells."""
    P = shapes()[3]
    deep, shallow = run(fill_bsp, P, road_depth=3, cells=12), run(fill_bsp, P, road_depth=0, cells=12)
    assert len(deep.faces) == len(shallow.faces)
    assert len(deep.roads) > len(shallow.roads)


def test_wedge_fans_and_arcs():
    P = Polygon([(0, 0), (400, 0), (400, 300), (0, 300)])
    fl = run(fill_wedge, P, spokes=5, cross=2)
    check_fill_contract(P, fl, MIN)
    assert {k[:2] for k, _ in fl.faces} >= {"w0", "w1", "w2"}
    assert any("c2" in k for k, _ in fl.faces)
    assert sum(1 for r in fl.roads if r.cls == "artery") >= 1


def test_follow_bands_and_core():
    P = Polygon([(0, 0), (400, 0), (400, 300), (0, 300)])
    fl = run(fill_follow, P, bands=2, depth=40.0)
    check_fill_contract(P, fl, MIN)
    keys = [k for k, _ in fl.faces]
    assert "core" in keys and any(k.startswith("l1_") for k in keys) and any(k.startswith("l2_") for k in keys)


def test_follow_bends_with_a_road():
    """A road bend (two consecutive on-road edges) is followed: the lots hug the bend."""
    P = Polygon([(0, 0), (300, 0), (400, 100), (400, 350), (0, 350)])
    fr = Frame((200, 175), 0.0, LineString([(0, 0), (300, 0), (400, 100)]))
    p = resolve(fill_follow.SUB_DEFS, 1, "t", "layers", {})
    fl = fill_follow.fill(P, random.Random(1), p, fr, MIN)
    check_fill_contract(P, fl, MIN)
    assert any(r.key.startswith("o") for r in fl.roads)
    assert len([1 for k, _ in fl.faces if k.startswith("l")]) >= 3


def test_unfit_not_error():
    tiny = Polygon([(0, 0), (30, 0), (30, 30), (0, 30)])
    for mod in (fill_wedge, fill_follow, fill_bsp):
        with pytest.raises(Unfit):
            run(mod, tiny)
    assert fill_follow.reference_chain  # chain finder is public


def test_grid_defaults_identical():
    for P in shapes()[:6]:
        a = run(fill_grid, P)
        b = run(fill_grid, P, shear=0.0, fan=0.0)
        assert [(k, q.wkt) for k, q in a.faces] == [(k, q.wkt) for k, q in b.faces]


def corner_dev(P, fl):
    worst = 0.0
    inside = None
    for _, q in fl.faces:
        if q.boundary.distance(P.boundary) < 1.0 or any(P.boundary.distance(Point(c)) < 1.0 for c in q.exterior.coords):
            continue
        c = list(q.exterior.coords)[:-1]
        n = len(c)
        for i in range(n):
            a, b, d = c[i - 1], c[i], c[(i + 1) % n]
            v1, v2 = (a[0] - b[0], a[1] - b[1]), (d[0] - b[0], d[1] - b[1])
            ang = math.degrees(abs(math.atan2(v1[0] * v2[1] - v1[1] * v2[0], v1[0] * v2[0] + v1[1] * v2[1])))
            if 25 < ang < 155 or ang < 5:
                worst = max(worst, abs(ang - 90))
    return worst


def test_grid_shear_and_fan():
    P = Polygon([(0, 0), (500, 0), (500, 500), (0, 500)])
    flat = run(fill_grid, P, block=80.0, jitter=0.0)
    sheared = run(fill_grid, P, block=80.0, jitter=0.0, shear=30.0)
    check_fill_contract(P, sheared, MIN)
    assert corner_dev(P, flat) < 1.0
    assert corner_dev(P, sheared) > 20.0
    fan = run(fill_grid, P, block=80.0, jitter=0.0, fan=6.0)
    check_fill_contract(P, fan, MIN)
    assert [q.wkt for _, q in fan.faces] != [q.wkt for _, q in flat.faces]
    for P in shapes()[:10]:
        check_fill_contract(P, run(fill_grid, P, shear=25.0, fan=8.0), MIN)


def test_grid_fan_lines_do_not_cross():
    P = Polygon([(0, 0), (500, 0), (500, 500), (0, 500)])
    fl = run(fill_grid, P, block=80.0, fan=12.0, seed=3)
    us = [LineString(r.polyline) for r in fl.roads if r.key.startswith("u")]
    for i in range(len(us)):
        for j in range(i + 1, len(us)):
            assert not us[i].crosses(us[j])


SPEC = {"max_depth": 1, "min_split_area": 2500}


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
@pytest.mark.parametrize("use", ["bsp", "wedge", "follow", "grid"])
def test_layers_generate(seed, use):
    s = town.generate("grid", seed, params={"layers": "%s;min_split_area=2500" % use})
    assert town.check(s) == []
    assert town.check_determinism(s) == []
    assert all(n.use == use for n in s.layers)


def test_layers_all_fills_occur_and_validate():
    seen = set()
    for seed in range(1, 6):
        for base in ("radial", "voronoi", "grid"):
            s = town.generate(base, seed, params={"layers": "bsp*2/wedge/follow/grid/radial/voronoi;min_split_area=2500"})
            assert town.check(s) == []
            seen.update(n.use for n in s.layers)
    assert seen >= {"bsp", "wedge", "follow", "grid"}


def test_align_random_and_jitter():
    a = town.generate("grid", 3, params={"layers": "grid@align=random;min_split_area=2500"})
    b = town.generate("grid", 3, params={"layers": "grid@align=random,align_jitter=45;min_split_area=2500"})
    assert town.check(a) == [] and town.check(b) == []
    rots = {round(n.params["rot"]) for n in a.layers}
    assert len(rots) > 2
    with pytest.raises(Exception):
        town.generate("grid", 3, params={"layers": "grid@align_jitter=50;min_split_area=2500"})


def test_unfit_falls_back_to_next_use():
    """A wedge-only run skips tiny districts; with a grid alternative the district is still subdivided, deterministically."""
    s1 = town.generate("grid", 2, params={"layers": "follow*5/grid;min_split_area=2500;min_area=1600"})
    s2 = town.generate("grid", 2, params={"layers": "follow*5/grid;min_split_area=2500;min_area=1600"})
    assert town.check(s1) == []
    assert [n.id for n in s1.layers] == [n.id for n in s2.layers]
    assert {n.use for n in s1.layers} <= {"follow", "grid"}
