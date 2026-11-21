"""Shape field and outline (design: mapgen/TOWN_ORGANIC_SPEC.md sections 4 and 10.2)."""
import pytest
from shapely.geometry import Point, Polygon

from mapgen.town import shape
from mapgen.town.gen_radial import GenerationError
from mapgen.town.params import ParamError

SEEDS = list(range(1, 31))
SIZE = (1000.0, 1000.0)


def _poly(sh):
    return Polygon(sh.ring)


@pytest.mark.parametrize("seed", SEEDS)
def test_footprint_guards(seed):
    sh = shape.footprint(SIZE, seed)
    P = _poly(sh)
    assert P.is_valid and P.exterior.is_simple and P.exterior.is_ccw
    x0, y0, x1, y1 = P.bounds
    assert x0 >= 10 and y0 >= 10 and x1 <= 990 and y1 <= 990
    assert P.contains(Point(sh.hub))
    assert shape.solidity(P) >= sh.params["min_solidity"]
    assert shape.neck_ok(P)
    assert all(round(c, 2) == c for p in sh.ring for c in p)


@pytest.mark.parametrize("seed", SEEDS)
def test_deterministic(seed):
    a = shape.footprint(SIZE, seed, sid="t")
    b = shape.footprint(SIZE, seed, sid="t")
    assert a.ring == b.ring and a.params == b.params and a.attempt == b.attempt


def test_seeds_differ():
    rings = {tuple(shape.footprint(SIZE, s).ring) for s in range(1, 9)}
    assert len(rings) == 8


def test_near_circular_without_irregularity():
    for seed in range(1, 6):
        sh = shape.footprint(SIZE, seed, params={"lobes": 0, "irregularity": 0, "ribbons": 0})
        assert shape.solidity(_poly(sh)) > 0.95


def test_irregularity_lowers_solidity():
    def mean(irr):
        v = [shape.solidity(_poly(shape.footprint(SIZE, s, params={"irregularity": irr, "lobes": 0, "ribbons": 0})))
             for s in range(1, 9)]
        return sum(v) / len(v)
    assert mean(0.5) < mean(0.0)


def test_lobes_and_ribbons_widen_the_outline():
    plain = shape.footprint(SIZE, 4, params={"lobes": 0, "ribbons": 0, "irregularity": 0.2})
    rich = shape.footprint(SIZE, 4, params={"lobes": 4, "ribbons": 3, "irregularity": 0.2})
    assert _poly(rich).convex_hull.area > _poly(plain).convex_hull.area


def test_pinch_is_rejected():
    dumbbell = Polygon([(0, 0), (300, 0), (300, 140), (400, 140), (400, 0), (700, 0), (700, 300), (400, 300),
                        (400, 160), (300, 160), (300, 300), (0, 300)])
    assert not shape.neck_ok(dumbbell)
    assert shape.neck_ok(Polygon([(0, 0), (400, 0), (400, 300), (0, 300)]))


def test_impossible_guard_raises():
    with pytest.raises(GenerationError):
        shape.footprint(SIZE, 1, params={"min_solidity": 0.9, "irregularity": 0.5, "lobes": 6, "ribbons": 4})


def test_fade_keeps_satellites_valid_and_apart():
    for seed in range(1, 11):
        sh = shape.footprint(SIZE, seed, edge="fade", params={"lobes": 5, "irregularity": 0.5})
        P = _poly(sh)
        for s in sh.satellites:
            Q = Polygon(s)
            assert Q.is_valid and not Q.intersects(P) and Q.area >= 1600


def test_bad_params():
    with pytest.raises(ParamError) as e:
        shape.footprint(SIZE, 1, params={"lobes": 9, "bogus": 1})
    assert len(e.value.args[0]) == 2 if isinstance(e.value.args[0], list) else True


def test_suitability_hook_shifts_footprint():
    import numpy as np
    base = shape.footprint(SIZE, 2, params={"lobes": 0, "ribbons": 0})
    east = shape.footprint(SIZE, 2, params={"lobes": 0, "ribbons": 0},
                           suitability=lambda X, Y: np.clip(0.5 + (X - 500.0) / 600.0, 0, 1))
    assert _poly(east).centroid.x > _poly(base).centroid.x
