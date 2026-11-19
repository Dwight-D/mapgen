"""Impassable scatter entries (GDU-45): validation, cost grid, road clearance, render, export, determinism."""
import json
import os

import numpy as np
import pytest
from PIL import Image

from mapgen import analysis, build, schema

SPEC = """format_version: 1
name: simp
size: [1200, 1200]
seed: 5
teams:
  A: {base: [100, 600]}
  B: {base: [1100, 600]}
output: {heightmap_res: 129, splat_res: 128, render_px: 600}
lines:
  - {id: rd, type: road, path: [[0, 600], [1200, 600]], width: 8}
scatter:
  - {id: wood, type: forest, coverage: 0.6, scale: 150, clump: 0.5, avoid: %s%s}
"""
FLAG = ", impassable: true"


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def _spec(avoid="0", flag=""):
    return SPEC % (avoid, flag)


def _prepare(tmp_path, name, text):
    return build.prepare(_write(tmp_path, name, text))


def _errors(tmp_path, text):
    with pytest.raises(schema.SpecError) as ei:
        schema.load_spec(_write(tmp_path, "bad.yaml", text))
    return ei.value.errors


def test_valid_flag_and_default(tmp_path):
    on = schema.load_spec(_write(tmp_path, "a.yaml", _spec(flag=FLAG)))
    off = schema.load_spec(_write(tmp_path, "b.yaml", _spec()))
    assert on.scatter[0].impassable is True and off.scatter[0].impassable is False


@pytest.mark.parametrize("val", ["yes", "1", "'true'", "null"])
def test_non_bool_rejected(tmp_path, val):
    errs = _errors(tmp_path, _spec(flag=", impassable: " + val))
    assert len(errs) == 1 and errs[0].startswith("scatter[0].impassable: expected true or false")


def test_cost_inf_on_scatter_only(tmp_path):
    spec0, g0, _, an0 = _prepare(tmp_path, "off.yaml", _spec())
    spec1, g1, _, an1 = _prepare(tmp_path, "on.yaml", _spec(flag=FLAG))
    i = [k for k, r in enumerate(spec1.regions) if r.scatter is not None][0]
    assert spec1.regions[i].impassable
    from mapgen import raster
    m = raster.hard(raster.mask(g1.regions[i], an1.grid))
    assert m.any()
    assert np.isinf(an1.cost[m]).all()
    # elsewhere the cost is unchanged (the geometry is identical: clearance floor is below the authored avoid)
    spec2, g2, _, an2 = _prepare(tmp_path, "off2.yaml", _spec(avoid="20"))
    spec3, g3, _, an3 = _prepare(tmp_path, "on2.yaml", _spec(avoid="20", flag=FLAG))
    assert g2.regions[0].equals(g3.regions[0])
    d = an2.cost != an3.cost
    m3 = raster.hard(raster.mask(g3.regions[0], an3.grid))
    assert d.any() and not (d & ~m3).any()
    assert np.isinf(an3.cost[m3]).all()


def test_road_walkable_end_to_end(tmp_path):
    spec, g, _, an = _prepare(tmp_path, "on.yaml", _spec(avoid="0", flag=FLAG))
    r, _ = analysis._cell(an.grid, 600, 600)
    assert np.isfinite(an.cost[r]).all()          # the whole road row is walkable
    for dy in (-1, 0, 1):
        assert np.isfinite(an.cost[r + dy]).all()
    assert an.base_times["A-B"] is not None
    assert not an.warnings or not any("unreachable" in w for w in an.warnings)
    imp = np.isinf(an.cost)
    assert imp.sum() > 100                        # the scatter really blocks ground


def test_clearance_floor_applies(tmp_path):
    spec, g, _, _ = _prepare(tmp_path, "on.yaml", _spec(avoid="0", flag=FLAG))
    from shapely.geometry import LineString
    assert g.regions[0].distance(LineString([(0, 600), (1200, 600)])) >= 2 * spec.analysis.cell - 4 - 0.5


def test_build_render_export_determinism(tmp_path):
    outs = {}
    for name, text in (("off", _spec(avoid="20")), ("on", _spec(avoid="20", flag=FLAG)), ("on2", _spec(avoid="20", flag=FLAG))):
        root = tmp_path / ("o_" + name)
        root.mkdir()
        outs[name] = __import__("pathlib").Path(build.build(_write(tmp_path, name + ".yaml", text), str(root), log=lambda s: None))
    def h(p):
        return open(str(p), "rb").read()
    for rel in ("map.png", "export/regions.json", "export/spec.resolved.json", "export/masks/impassable.png"):
        assert h(outs["on"] / rel) == h(outs["on2"] / rel), rel
    # render differs from the plain one only by the hatch inside the scatter
    a = np.asarray(Image.open(str(outs["off"] / "map.png")).convert("RGB")).astype(int)
    b = np.asarray(Image.open(str(outs["on"] / "map.png")).convert("RGB")).astype(int)
    assert a.shape == b.shape
    diff = (a != b).any(axis=2)
    assert diff.any()
    # export presence
    roff = json.loads(h(outs["off"] / "export/regions.json"))
    ron = json.loads(h(outs["on"] / "export/regions.json"))
    pl = lambda d: d["regions"] if isinstance(d, dict) and "regions" in d else d
    assert all("impassable" not in r for r in pl(roff))
    sc = [r for r in pl(ron) if r.get("scatter")]
    assert sc and all(r.get("impassable") is True for r in sc)
    res = json.loads(h(outs["on"] / "export/spec.resolved.json"))
    assert "impassable" not in json.dumps(json.loads(h(outs["off"] / "export/spec.resolved.json")))
    assert '"impassable": true' in json.dumps(res)
