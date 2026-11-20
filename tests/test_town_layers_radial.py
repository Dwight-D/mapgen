"""Town layers: radial fill (design: mapgen/TOWN_LAYERS_SPEC.md section 4.2)."""
import random

import pytest
from shapely.geometry import Polygon

from layers_helpers import check_fill_contract, frame_of, polygons
from mapgen import town
from mapgen.town import fill_radial
from mapgen.town.layerfill import Unfit
from mapgen.town.params import resolve

SEEDS = list(range(1, 21))


def _fill(P, seed=1, min_area=1600.0, **over):
    p = resolve(fill_radial.SUB_DEFS, seed, "t", "layers", over)
    return fill_radial.fill(P, random.Random(seed), p, frame_of(P), min_area)


def test_radial_fill_contract():
    convex, nonconvex = polygons()
    n = 0
    for P in convex + nonconvex:
        try:
            fl = _fill(P)
        except Unfit:
            continue
        n += 1
        check_fill_contract(P, fl, 1600.0)
        assert [(k, q.wkt) for k, q in fl.faces] == [(k, q.wkt) for k, q in _fill(P).faces]
    assert n >= 25


def test_radial_keys_and_params():
    P = Polygon([(0, 0), (400, 0), (400, 400), (0, 400)])
    fl = _fill(P, spokes=6, rings=2, min_area=500.0)
    keys = {k for k, _ in fl.faces}
    assert "core" in keys and any(k.startswith("r1s") for k in keys) and any(k.startswith("r2s") for k in keys)
    assert sum(1 for r in fl.roads if r.cls == "artery") >= 6


def test_radial_not_star_shaped_is_unfit():
    comb = Polygon([(0, 0), (300, 0), (300, 40), (40, 40), (40, 100), (300, 100), (300, 140), (0, 140)])
    with pytest.raises(Unfit):
        _fill(comb)


@pytest.mark.parametrize("base", ["radial", "voronoi", "grid"])
@pytest.mark.parametrize("seed", SEEDS)
def test_radial_layers_checks(base, seed):
    s = town.generate(base, seed, params={"layers": "radial;min_split_area=2500"})
    assert town.check(s) == []
    assert town.check_determinism(s) == []
    assert all(n.use == "radial" for n in s.layers)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_radial_into_grid_into_voronoi(seed):
    s = town.generate("radial", seed, params={"layers": "0:radial;1:grid;max_depth=2;min_split_area=2500"})
    assert town.check(s) == []
    assert town.check_determinism(s) == []
