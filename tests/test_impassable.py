"""Impassable regions (FLOWGRAPH_SPEC 13): validation, cost grid, strike-through render, export field."""
import json
import os

import numpy as np
import pytest
from PIL import Image

from mapgen import analysis, build, schema

SPEC = """format_version: 1
name: imp
size: [600, 600]
seed: 3
teams:
  A: {base: [60, 300]}
  B: {base: [540, 300]}
output: {heightmap_res: 129, splat_res: 128, render_px: 400}
regions:
  - {id: wood, type: forest, shape: band, path: [[300, 0], [300, 600]], width: 120, rough: 0, warp: 0%s}
"""
FLAG = ", impassable: true"


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def _errors(tmp_path, text):
    with pytest.raises(schema.SpecError) as ei:
        schema.load_spec(_write(tmp_path, "bad.yaml", text))
    return ei.value.errors


def test_valid_flag_and_default(tmp_path):
    on = schema.load_spec(_write(tmp_path, "a.yaml", SPEC % FLAG))
    off = schema.load_spec(_write(tmp_path, "b.yaml", SPEC % ""))
    explicit = schema.load_spec(_write(tmp_path, "c.yaml", SPEC % ", impassable: false"))
    assert on.regions[0].impassable is True
    assert off.regions[0].impassable is False and explicit.regions[0].impassable is False


@pytest.mark.parametrize("val", ["yes", "1", "'true'", "null"])
def test_non_bool_rejected(tmp_path, val):
    errs = _errors(tmp_path, SPEC % (", impassable: " + val))
    assert len(errs) == 1 and errs[0].startswith("regions[0].impassable: expected true or false")


def test_rejected_on_non_region_features(tmp_path):
    text = SPEC % "" + "lines:\n  - {id: rd, type: road, path: [[0, 100], [600, 100]], impassable: true}\n"
    errs = _errors(tmp_path, text)
    assert len(errs) == 1 and errs[0].startswith("lines[0].impassable: unknown key")
    text = SPEC % "" + "pois:\n  - {id: p, type: capture, at: [300, 100], impassable: true}\n"
    assert _errors(tmp_path, text)[0].startswith("pois[0].impassable: unknown key")


def _prepare(tmp_path, name, text):
    return build.prepare(_write(tmp_path, name, text))


def test_cost_grid_blocks_route(tmp_path):
    spec0, _, _, an0 = _prepare(tmp_path, "off.yaml", SPEC % "")
    spec1, _, _, an1 = _prepare(tmp_path, "on.yaml", SPEC % FLAG)
    assert np.isfinite(an0.cost).all()
    r, c = analysis._cell(an1.grid, 300, 300)
    assert np.isinf(an1.cost[r, c]) and np.isfinite(an0.cost[r, c])
    assert np.isinf(an1.cost[:, c]).all()                       # the band cuts the whole map
    assert np.isfinite(an1.cost[:, : c - 30]).all() and np.isfinite(an1.cost[:, c + 30:]).all()
    rb, cb = analysis._cell(an1.grid, 540, 300)
    assert np.isfinite(an0.times["A"][rb, cb])
    assert np.isinf(an1.times["A"][rb, cb])


def test_hills_height_unchanged_by_flag(tmp_path):
    _, _, hm0, _ = _prepare(tmp_path, "off.yaml", SPEC.replace("forest", "hills") % "")
    _, _, hm1, _ = _prepare(tmp_path, "on.yaml", SPEC.replace("forest", "hills") % FLAG)
    assert np.array_equal(hm0.heights if hasattr(hm0, "heights") else hm0.h, hm1.heights if hasattr(hm1, "heights") else hm1.h)


def _build(tmp_path, name, text):
    out = tmp_path / name
    build.build(_write(tmp_path, name + ".yaml", text), str(out), log=lambda s: None)
    return out / "imp"


def test_render_hatch_and_export(tmp_path):
    off = _build(tmp_path, "off", SPEC % "")
    on = _build(tmp_path, "on", SPEC % FLAG)
    a = np.asarray(Image.open(off / "map.png").convert("RGB")).astype(int)
    b = np.asarray(Image.open(on / "map.png").convert("RGB")).astype(int)
    assert a.shape == b.shape
    diff = np.abs(a - b).sum(axis=2) > 0
    assert diff.sum() > 500                                      # hatch pixels inside the forest band and legend row
    # the changed pixels in the map area are bright strike cores (white) and dark casings
    changed = b[diff]
    assert (changed.min(axis=1) >= 250).any() and (changed.max(axis=1) <= 40).any()
    # outside the band the map is identical (type colour and outline kept)
    left = slice(0, a.shape[0]), slice(0, 100)
    assert np.array_equal(a[left], b[left])
    # export: field only on flagged regions
    reg_on = json.loads((on / "export" / "regions.json").read_text(encoding="utf-8"))
    reg_off = json.loads((off / "export" / "regions.json").read_text(encoding="utf-8"))
    assert reg_on[0]["impassable"] is True and "impassable" not in reg_off[0]
    res_on = json.loads((on / "export" / "spec.resolved.json").read_text(encoding="utf-8"))
    res_off = json.loads((off / "export" / "spec.resolved.json").read_text(encoding="utf-8"))
    assert res_on["regions"][0]["impassable"] is True and "impassable" not in res_off["regions"][0]
    imp = np.asarray(Image.open(on / "export" / "masks" / "impassable.png"))
    assert imp.any() and not np.asarray(Image.open(off / "export" / "masks" / "impassable.png")).any()
