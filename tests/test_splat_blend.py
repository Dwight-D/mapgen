"""Opt-in splat blend (output.splat_blend): validation, the weight transform, and the build/export contract."""
import hashlib
import json
import os

import numpy as np
import pytest
from PIL import Image

from mapgen import build, export, schema
from mapgen.raster import Grid

SPEC = """format_version: 1
name: sb
size: [600, 600]
seed: 11
teams:
  A: {base: [60, 300]}
  B: {base: [540, 300]}
output: {heightmap_res: 129, splat_res: 128, render_px: 300%s}
regions:
  - {id: burg, type: town, shape: blob, at: [300, 300], r: 110, rough: 0, warp: 0}
  - {id: acre, type: field, shape: blob, at: [120, 480], r: 70, rough: 0, warp: 0}
lines:
  - {id: rd, type: road, path: [[0, 300], [600, 300]]}
"""


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def _load(tmp_path, extra=""):
    return schema.load_spec(_write(tmp_path, "sb.yaml", SPEC % extra))


def _errors(tmp_path, extra):
    with pytest.raises(schema.SpecError) as ei:
        _load(tmp_path, extra)
    return ei.value.errors


# --- validation -------------------------------------------------------------------------------------------

def test_absent_and_false_are_off(tmp_path):
    assert _load(tmp_path).output.splat_blend is None
    assert _load(tmp_path, ", splat_blend: false").output.splat_blend is None


def test_true_gives_defaults(tmp_path):
    assert _load(tmp_path, ", splat_blend: true").output.splat_blend == schema.SplatBlend()


def test_mapping_overrides_keys(tmp_path):
    b = _load(tmp_path, ", splat_blend: {edge: 6, tufts: 0, seed: 42}").output.splat_blend
    assert (b.edge, b.tufts, b.seed) == (6.0, 0.0, 42)
    assert b.patches == schema.SplatBlend().patches


@pytest.mark.parametrize("extra,prefix", [
    (", splat_blend: {width: 3}", "output.splat_blend.width: unknown key"),
    (", splat_blend: {patches: 1.5}", "output.splat_blend.patches: must be in"),
    (", splat_blend: {edge: -1}", "output.splat_blend.edge: must be >= 0"),
    (", splat_blend: {noise_scale: 0}", "output.splat_blend.noise_scale: must be > 0"),
    (", splat_blend: {seed: -2}", "output.splat_blend.seed: expected an integer"),
    (", splat_blend: 3", "output.splat_blend: expected a mapping"),
])
def test_invalid_values(tmp_path, extra, prefix):
    errs = _errors(tmp_path, extra)
    assert len(errs) == 1 and errs[0].startswith(prefix), errs


# --- the weight transform ---------------------------------------------------------------------------------

LAYERS = ["grass", "field", "forest", "mud", "rock", "sand", "dirt", "road"]
IDX = {n: i for i, n in enumerate(LAYERS)}


def _hard_weights(n=128):
    """Grass base, a dirt square (town) and a road row through it: hard 0/1 weights."""
    w = np.zeros((len(LAYERS), n, n), dtype=np.float32)
    w[IDX["grass"]] = 1.0
    w[:, 32:96, 32:96] = 0.0
    w[IDX["dirt"], 32:96, 32:96] = 1.0
    w[:, 62:66, :] = 0.0
    w[IDX["road"], 62:66, :] = 1.0
    return w


def _blend(b, seed=7):
    w = _hard_weights()
    export.blend_splats(w, IDX, b, seed, Grid(128, 128, 128, 128))    # 1 m pixels
    return w


def test_weight_sum_preserved_and_non_negative():
    w = _blend(schema.SplatBlend())
    assert np.allclose(w.sum(axis=0), 1.0, atol=1e-5)
    assert w.min() >= 0.0


def test_edges_feathered():
    hard = _hard_weights()
    w = _blend(schema.SplatBlend(patches=0, tufts=0, road_edge=0))
    g = w[IDX["grass"]]
    assert set(np.unique(hard[IDX["grass"]])) == {0.0, 1.0}
    across = g[48, 26:38]                   # grass -> dirt edge at column 32
    assert ((across > 0.05) & (across < 0.95)).sum() >= 3
    assert np.all(np.diff(across) <= 1e-6)   # monotone falloff into the town


def test_edge_zero_and_no_noise_is_identity():
    w = _blend(schema.SplatBlend(edge=0, patches=0, tufts=0, road_edge=0))
    assert np.array_equal(w, _hard_weights())


def test_road_shoulder_turns_grass_to_dirt():
    w = _blend(schema.SplatBlend(edge=0, patches=0, tufts=0, road_edge=4))
    d = w[IDX["dirt"]]
    assert d[60, 10] > 0.3 and d[67, 10] > 0.3     # 1-2 m off the road, outside town
    assert d[50, 10] == 0.0                         # beyond road_edge
    assert np.array_equal(w[IDX["road"]], _hard_weights()[IDX["road"]])


def test_noise_patches_and_tufts():
    w = _blend(schema.SplatBlend(edge=0, road_edge=0, patches=0.8, tufts=0.8, noise_scale=12))
    d, g = w[IDX["dirt"]], w[IDX["grass"]]
    outside = np.ones((128, 128), bool)
    outside[32:96, 32:96] = False
    outside[62:66, :] = False
    assert d[outside].max() > 0.2                  # dirt patches in grass near the town/road
    assert g[34:94, 34:62].max() > 0.2             # grass tufts in the town dirt
    far = _blend(schema.SplatBlend(edge=0, road_edge=0, patches=0.8, tufts=0, patch_reach=1))
    assert far[IDX["dirt"]][2, 2] == 0.0           # no patches beyond patch_reach


def test_deterministic_and_seeded():
    b = schema.SplatBlend()
    assert np.array_equal(_blend(b, 5), _blend(b, 5))
    assert not np.array_equal(_blend(b, 5), _blend(b, 6))


# --- build / export contract ------------------------------------------------------------------------------

def _hashes(root):
    out = {}
    for dp, _, fs in os.walk(root):
        for f in fs:
            p = os.path.join(dp, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, root).replace("\\", "/")] = hashlib.sha256(fh.read()).hexdigest()
    return out


def test_build_on_changes_only_splats_and_resolved(tmp_path):
    spec = _write(tmp_path, "sb.yaml", SPEC % "")
    quiet = lambda s: None    # noqa: E731
    off = build.build(spec, str(tmp_path / "off"), "export", log=quiet)
    flag = build.build(spec, str(tmp_path / "flag"), "export", log=quiet, splat_blend=True)
    keyed = build.build(_write(tmp_path, "sb2.yaml", SPEC % ", splat_blend: true"), str(tmp_path / "key"),
                        "export", log=quiet)
    a, b = _hashes(os.path.join(off, "export")), _hashes(os.path.join(flag, "export"))
    assert sorted(a) == sorted(b)
    changed = sorted(k for k in a if a[k] != b[k])
    assert changed == ["spec.resolved.json", "splat_0.png", "splat_1.png"]
    c = _hashes(os.path.join(keyed, "export"))
    assert c["splat_0.png"] == b["splat_0.png"] and c["splat_1.png"] == b["splat_1.png"]
    res_off = json.load(open(os.path.join(off, "export", "spec.resolved.json"), encoding="utf-8"))
    res_on = json.load(open(os.path.join(flag, "export", "spec.resolved.json"), encoding="utf-8"))
    assert "splat_blend" not in res_off["output"]
    assert res_on["output"]["splat_blend"]["edge"] == schema.SplatBlend().edge
    s0 = np.asarray(Image.open(os.path.join(flag, "export", "splat_0.png"))).astype(int)
    s1 = np.asarray(Image.open(os.path.join(flag, "export", "splat_1.png"))).astype(int)
    assert np.all(s0.sum(axis=2) + s1.sum(axis=2) == 255)
