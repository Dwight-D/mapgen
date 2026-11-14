"""Stable feature identity: feature seeds, explicit-seed equivalence, automatic crossing ids, locality."""
import json
import os

import pytest

import regress
from mapgen import build, diff, geometry, schema
from test_validation import BASE

MASK_FILES = {"export/masks/clearing.png", "export/masks/forest.png", "export/masks/impassable.png",
              "export/splat_0.png", "export/splat_1.png"}
THORNWALD = os.path.join(regress.REPO_ROOT, "maps", "concepts", "thornwald.yaml")
SCATTER_IDS = ("scatter0", "scatter1")

SYNTHETIC = """format_version: 1
name: synthetic_auto
size: [2000, 2000]
seed: 3
teams:
  A: { base: [100, 100] }
  B: { base: [1900, 1900] }
regions:
  - { id: west_lake, type: lake, shape: blob, at: [500, 1500], r: 200, rough: 0.2 }
lines:
  - { id: snake, type: river, path: [[1000, 0], [1000, 2000]], width: 20 }
  - { id: zigzag, type: trail, path: [[100, 100], [1500, 500], [500, 900], [1500, 1300], [100, 1500], [900, 1500]], width: 5 }
"""

SMALL = BASE + """regions:
  - { id: w, type: forest, shape: blob, at: [400, 500], r: 150, rough: 0.5, warp: 40, warp_scale: 90, fragment: 0.3 }
lines:
  - { id: r, type: river, path: [[0, 300], [1000, 700]], width: 20, meander: 40, width_var: 0.4 }
"""


def _write(path, text):
    with open(str(path), "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return str(path)


def _geoms(path):
    spec = schema.load_spec(path)
    return spec, geometry.build_geometries(spec)


def test_feature_seed_golden():
    assert schema.feature_seed("mossbeck") == 1892764035
    assert schema.feature_seed("region_6") == 2640903791
    assert schema.feature_seed("a") == 3398926610


def test_explicit_seed_equals_derived(tmp_path):
    _, g1 = _geoms(_write(tmp_path / "a.yaml", SMALL))
    text = (SMALL.replace("{ id: w,", "{ id: w, seed: %d," % schema.feature_seed("w"))
                 .replace("{ id: r,", "{ id: r, seed: %d," % schema.feature_seed("r")))
    assert text != SMALL
    spec2, g2 = _geoms(_write(tmp_path / "b.yaml", text))
    assert spec2.regions[0].seed == schema.feature_seed("w")
    assert not g1.regions[0].is_empty and not g1.lines[0].area.is_empty
    assert g1.regions[0].wkb == g2.regions[0].wkb
    assert g1.lines[0].centreline.wkb == g2.lines[0].centreline.wkb
    assert g1.lines[0].area.wkb == g2.lines[0].area.wkb


def test_auto_crossing_ids(tmp_path):
    spec, _ = _geoms(_write(tmp_path / "synthetic_auto.yaml", SYNTHETIC))
    auto = [c for c in spec.crossings if c.auto]
    assert [c.id for c in auto] == ["zigzag-snake-1", "zigzag-snake-2", "zigzag-snake-3", "zigzag-snake-4",
                                    "zigzag-west_lake-1"]
    assert [(round(c.at[0]), round(c.at[1])) for c in auto] == [(1000, 374), (1000, 700), (1000, 1100),
                                                                (1000, 1364), (364, 1489)]


def _load(out, rel):
    with open(os.path.join(out, *rel.split("/")), "r", encoding="utf-8") as f:
        return json.load(f)


def _by_id(entries):
    return {e["id"]: e for e in entries}


def _raster(report, path):
    for r in report["rasters"]:
        if r["path"] == path:
            return r
    raise AssertionError("raster not in diff: " + path)


def _within(box, x0, x1, z0, z1):
    return box["x_min"] >= x0 and box["x_max"] <= x1 and box["z_min"] >= z0 and box["z_max"] <= z1


def _changed_rasters(r):
    return set(x["path"] for x in r["rasters"] if x["pixels_identical"] is False)


@pytest.fixture(scope="module")
def thornwald_a(tmp_path_factory):
    out = tmp_path_factory.mktemp("thorn_a")
    return build.build(THORNWALD, str(out), only="export", log=lambda s: None)


def _build_edited(tmp_path_factory, name, old, new, expect_count=1):
    with open(THORNWALD, "r", encoding="utf-8", newline="") as f:
        text = f.read()
    assert text.count(old) == expect_count, old
    text = text.replace(old, new)
    base = tmp_path_factory.mktemp(name)
    (base / "spec").mkdir()
    spec_path = _write(base / "spec" / "thornwald.yaml", text)
    return build.build(spec_path, str(base / "out"), only="export", log=lambda s: None)


@pytest.fixture(scope="module")
def inserted(tmp_path_factory):
    line = ("  - { id: glade_x, type: clearing, shape: blob, at: [2750, 1950], r: 150, rough: 0.6, "
            "warp: 60, warp_scale: 80 }\n")
    return _build_edited(tmp_path_factory, "thorn_ins", "\nregions:\n", "\nregions:\n" + line)


@pytest.fixture(scope="module")
def unseeded(tmp_path_factory):
    return _build_edited(tmp_path_factory, "thorn_noseed", "{ id: region_6, seed: 5, ", "{ id: region_6, ")


def test_insert_region_is_local(thornwald_a, inserted):
    a, b = str(thornwald_a), str(inserted)
    r = diff.diff_builds(a, b)
    assert r["heightmap"]["changed_texels"] == 0
    assert _changed_rasters(r) <= MASK_FILES
    clearing = _raster(r, "export/masks/clearing.png")
    assert clearing["pixels_identical"] is False
    assert _within(clearing["bbox_m"], 2495, 3005, 1695, 2205), clearing["bbox_m"]
    assert _raster(r, "export/masks/forest.png")["changed_share"] <= 0.01
    assert _raster(r, "export/masks/impassable.png")["changed_share"] <= 0.001
    changed = r["files"]["changed"]
    for f in ("export/lines.json", "export/crossings.json", "export/pois.json"):
        assert f not in changed
    ra, rb = _load(a, "export/regions.json"), _load(b, "export/regions.json")
    ids_a, ids_b = [e["id"] for e in ra], [e["id"] for e in rb]
    assert [i for i in ids_b if i != "glade_x"] == ids_a
    assert "glade_x" in ids_b
    ea, eb = _by_id(ra), _by_id(rb)
    for i in ids_a:
        if i not in SCATTER_IDS:
            assert ea[i] == eb[i], i
    sa, sb = _load(a, "export/spec.resolved.json"), _load(b, "export/spec.resolved.json")
    ra2, rb2 = _by_id(sa["regions"]), _by_id(sb["regions"])
    for i in ra2:
        assert ra2[i] == rb2[i], i
    for key in ("lines", "crossings", "pois", "teams"):
        assert sa[key] == sb[key], key


def test_id_derived_seed_takes_effect(thornwald_a, unseeded):
    a, b = str(thornwald_a), str(unseeded)
    r = diff.diff_builds(a, b)
    assert r["heightmap"]["changed_texels"] == 0
    assert _changed_rasters(r) <= MASK_FILES
    clearing = _raster(r, "export/masks/clearing.png")
    assert clearing["pixels_identical"] is False
    assert _within(clearing["bbox_m"], 331, 749, 2191, 2609), clearing["bbox_m"]
    ea, eb = _by_id(_load(a, "export/regions.json")), _by_id(_load(b, "export/regions.json"))
    assert ea["region_6"] != eb["region_6"]
    for i in ea:
        if i not in SCATTER_IDS and i != "region_6":
            assert ea[i] == eb[i], i
    sa = _by_id(_load(a, "export/spec.resolved.json")["regions"])
    sb = _by_id(_load(b, "export/spec.resolved.json")["regions"])
    assert sb["region_6"]["seed"] == schema.feature_seed("region_6") == 2640903791
    for i in sa:
        if i != "region_6":
            assert sa[i] == sb[i], i


# ----------------------------------------------------------------------------- seeds do not come from list position

LINE_FEATURE = "{ id: r, %stype: river, path: [[0, 300], [1000, 700]], width: 20, meander: 40, width_var: 0.4 }"
LINE_FILLER = "{ id: filler, type: trail, path: [[100, 900], [900, 950]], width: 6, meander: 20 }"
SCATTER_FEATURE = "{ id: s, %stype: forest, coverage: 0.4, scale: 150, clump: 0.5, avoid: 8 }"
SCATTER_FILLER = "{ id: s_filler, type: forest, coverage: 0.3, scale: 90 }"
REGION_W = "{ id: w, type: hills, shape: blob, at: [250, 250], r: 120 }"


def _line_spec(seed, before=False):
    items = [LINE_FEATURE % seed]
    if before:
        items.insert(0, LINE_FILLER)
    return BASE + "regions:\n  - " + REGION_W + "\nlines:\n" + "".join("  - %s\n" % i for i in items)


def _scatter_spec(seed, before=False):
    items = [SCATTER_FEATURE % seed]
    if before:
        items.insert(0, SCATTER_FILLER)
    return (BASE + "regions:\n  - " + REGION_W + "\nlines:\n  - " + LINE_FILLER + "\nscatter:\n"
            + "".join("  - %s\n" % i for i in items))


def _line_wkb(tmp_path, name, text):
    spec, g = _geoms(_write(tmp_path / (name + ".yaml"), text))
    k = [ln.id for ln in spec.lines].index("r")
    assert not g.lines[k].centreline.is_empty and not g.lines[k].area.is_empty
    return g.lines[k].centreline.wkb, g.lines[k].area.wkb


def _scatter_wkb(tmp_path, name, text):
    spec, g = _geoms(_write(tmp_path / (name + ".yaml"), text))
    k = [r.id for r in spec.regions].index("s")
    assert not g.regions[k].is_empty
    assert spec.regions[k].seed == (spec.scatter[[s.id for s in spec.scatter].index("s")].seed)
    return g.regions[k].wkb


def test_line_seed_is_id_derived_not_positional(tmp_path):
    derived = _line_wkb(tmp_path, "none", _line_spec(""))
    explicit = _line_wkb(tmp_path, "explicit", _line_spec("seed: %d, " % schema.feature_seed("r")))
    positional = _line_wkb(tmp_path, "index", _line_spec("seed: 0, "))
    assert derived == explicit
    assert derived[0] != positional[0]     # meander
    assert derived[1] != positional[1]     # width variation


def test_line_geometry_independent_of_list_position(tmp_path):
    first = _line_wkb(tmp_path, "first", _line_spec(""))
    moved = _line_wkb(tmp_path, "moved", _line_spec("", before=True))
    assert first == moved


def test_scatter_seed_is_id_derived_not_positional(tmp_path):
    derived = _scatter_wkb(tmp_path, "none", _scatter_spec(""))
    explicit = _scatter_wkb(tmp_path, "explicit", _scatter_spec("seed: %d, " % schema.feature_seed("s")))
    positional = _scatter_wkb(tmp_path, "index", _scatter_spec("seed: 0, "))
    assert derived == explicit
    assert derived != positional


def test_scatter_geometry_independent_of_list_position(tmp_path):
    first = _scatter_wkb(tmp_path, "first", _scatter_spec(""))
    moved = _scatter_wkb(tmp_path, "moved", _scatter_spec("", before=True))
    assert first == moved
