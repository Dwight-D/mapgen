"""Scatter: locality of edits, coverage of eligible ground, determinism (small in-process specs)."""
import os
import re

import numpy as np
import pytest
from PIL import Image
from shapely.geometry import Point, Polygon, box
from shapely.ops import unary_union

import regress
from mapgen import build, geometry, schema

TINY = """format_version: 1
name: tiny
size: [1000, 1000]
seed: 1
teams:
  A: {base: [100, 100]}
  B: {base: [900, 900]}
lines:
  - { id: t, type: trail, path: [[0, 500], [1000, 560]], width: 6, meander: 20 }
pois:
  - { id: far, type: neutral_camp, at: %s }
scatter:
  - { id: s, type: forest, coverage: 0.4, scale: 120, clump: 0.4, avoid: 20 }
output: { render_px: 256, heightmap_res: 129, splat_res: 256 }
"""
OLD, MOVED = (750.0, 250.0), (860.0, 250.0)
MARGIN = 20.0
SIZE = 1000.0

BASE = "\n".join(TINY.split("\n")[:7]) + "\n"
COVERAGE_CASES = {
    "plain": BASE + "scatter:\n  - { id: s, type: forest, coverage: 0.4, scale: 120, clump: 0.4, avoid: 20 }\n",
    "within": BASE + "scatter:\n  - { id: s, type: field, coverage: 0.3, scale: 100, clump: 0.3, avoid: 20, "
                     "within: [[0, 0], [500, 0], [500, 1000], [0, 1000]] }\n",
    "blocked": TINY % "[750, 250]",
}


def _write(path, text):
    with open(str(path), "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return str(path)


def _pt(at):
    return "[%g, %g]" % at


def _region(spec, geoms, rid):
    return geoms.regions[[r.id for r in spec.regions].index(rid)]


def _forest_mask(out_dir):
    return np.asarray(Image.open(os.path.join(str(out_dir), "export", "masks", "forest.png"))).astype(int)


@pytest.fixture(scope="module")
def pair(tmp_path_factory):
    """Builds of TINY with the POI at OLD (a) and MOVED (b): spec, geometry, forest mask."""
    tmp = tmp_path_factory.mktemp("scatter_pair")
    res = {}
    for key, at in (("a", OLD), ("b", MOVED)):
        p = _write(tmp / (key + ".yaml"), TINY % _pt(at))
        spec = schema.load_spec(p)
        geoms = geometry.build_geometries(spec)
        out = build.build(p, str(tmp / key), only="export", log=lambda s: None)
        res[key] = (spec, _region(spec, geoms, "s"), _forest_mask(out))
    return res


def _zone(spec):
    sc = spec.scatter[0]
    poi = [p for p in spec.all_pois() if p.id == "far"][0]
    reach = poi.r + 2.25 * sc.avoid
    return unary_union([Point(*OLD).buffer(reach), Point(*MOVED).buffer(reach)])


def _touched(pair_):
    spec, ga, _ = pair_["a"]
    gb = pair_["b"][1]
    zone = _zone(spec)
    near = zone.buffer(MARGIN)
    parts = [x for x in list(geometry.parts(ga)) + list(geometry.parts(gb)) if x.intersects(near)]
    return zone, unary_union([zone] + parts)


def test_poi_move_keeps_far_patches(pair):
    spec, ga, _ = pair["a"]
    gb = pair["b"][1]
    zone = _zone(spec)
    wkb_a = set(x.wkb for x in geometry.parts(ga))
    far = [x for x in geometry.parts(gb) if x.distance(zone) > MARGIN]
    assert len(far) >= 5
    assert all(x.wkb in wkb_a for x in far)


def test_poi_move_changes_only_near_geometry(pair):
    ga, gb = pair["a"][1], pair["b"][1]
    _, affected = _touched(pair)
    assert ga.symmetric_difference(gb).difference(affected).area < 1e-6


def test_poi_move_mask_is_local(pair):
    ma, mb = pair["a"][2], pair["b"][2]
    _, affected = _touched(pair)
    tx = SIZE / ma.shape[1]
    allowed = affected.buffer(1.5 * tx)
    rows, cols = np.nonzero(ma != mb)
    assert len(rows) > 0
    outside = [(c, r) for r, c in zip(rows, cols)
               if not allowed.contains(Point((c + 0.5) * tx, SIZE - (r + 0.5) * tx))]
    assert outside == []


@pytest.mark.parametrize("case", sorted(COVERAGE_CASES))
def test_coverage_of_eligible_ground(case, tmp_path):
    text = COVERAGE_CASES[case]
    spec = schema.load_spec(_write(tmp_path / "t.yaml", text))
    sc = spec.scatter[0]
    achieved = _region(spec, geometry.build_geometries(spec), "s").area
    full = re.sub(r"coverage: [0-9.]+", "coverage: 1.0", text)
    spec1 = schema.load_spec(_write(tmp_path / "t1.yaml", full))
    eligible = _region(spec1, geometry.build_geometries(spec1), "s").area
    assert eligible > 0
    assert abs(achieved / eligible - sc.coverage) <= 0.04


def test_build_is_deterministic(tmp_path):
    p = _write(tmp_path / "tiny.yaml", TINY % _pt(OLD))
    hashes = []
    for k in range(2):
        out = build.build(p, str(tmp_path / ("o%d" % k)), log=lambda s: None)
        hashes.append(regress.hash_tree(str(out)))
    assert hashes[0] == hashes[1]
    assert len(hashes[0]) > 0


SCATTER_S = "  - { id: s, type: forest, coverage: 0.4, scale: 120, clump: 0.4, avoid: 20 }\n"
HILLS = "regions:\n  - { id: h, type: hills, shape: blob, at: [300, 750], r: 160, rough: 0.2, height: 40 }\nlines:\n"
KEYED_CASES = {
    "within": TINY.replace(SCATTER_S, "  - { id: s, type: forest, coverage: 0.4, scale: 120, clump: 0.4, avoid: 20, "
                                      "within: [[0, 0], [900, 0], [900, 1000], [0, 1000]] }\n"),
    "on": TINY.replace("lines:\n", HILLS).replace(
        SCATTER_S, "  - { id: s, type: forest, coverage: 0.4, scale: 120, clump: 0.4, avoid: 20, on: [hills] }\n"),
}


@pytest.mark.parametrize("case", sorted(KEYED_CASES))
def test_poi_move_is_local_with_within_and_on(case, tmp_path):
    """The touched-patch rule of section 1.1 holds for an entry with `within` and for one with `on`."""
    geoms = {}
    for key, at in (("a", OLD), ("b", MOVED)):
        spec = schema.load_spec(_write(tmp_path / (key + ".yaml"), KEYED_CASES[case] % _pt(at)))
        geoms[key] = _region(spec, geometry.build_geometries(spec), "s")
    sc = spec.scatter[0]
    poi = [p for p in spec.all_pois() if p.id == "far"][0]
    reach = poi.r + 2.25 * sc.avoid
    zone = unary_union([Point(*OLD).buffer(reach), Point(*MOVED).buffer(reach)])
    parts_a, parts_b = list(geometry.parts(geoms["a"])), list(geometry.parts(geoms["b"]))
    touched = unary_union([zone] + [x for x in parts_a + parts_b if x.intersects(zone.buffer(MARGIN))])
    wkb_a = set(x.wkb for x in parts_a)
    far = [x for x in parts_b if x.distance(zone) > MARGIN]
    assert len(far) >= 3
    assert all(x.wkb in wkb_a for x in far)
    assert geoms["a"].symmetric_difference(geoms["b"]).difference(touched).area < 1e-6
