"""mapgen diff: report structure, raster/JSON/analysis logic on synthetic builds, and an
integration build of the river_crossing example. See mapgen/DIFF_SPEC.md."""
import builtins
import copy
import io
import json
import os

import numpy as np
import pytest
from PIL import Image

import regress
from mapgen import diff as D
from mapgen.__main__ import main

REPORT = {
    "name": "syn", "size": [400.0, 200.0], "warnings": [],
    "teams": {"A": {"name": "A", "base": [10.0, 10.0]}, "B": {"name": "B", "base": [390.0, 190.0]}},
    "base_times_s": {"A-B": 100.0},
    "area_share": {"A": 0.5, "B": 0.4, "contested": 0.1, "unreachable": 0.0},
    "objectives": [{"id": "o1", "type": "capture", "owner": None, "times": {"A": 50.0, "B": 60.0},
                    "spread": 0.2, "flag": ""}],
    "route_constriction": {"A-B": 0.1}, "openness_along_routes": {"A-B": 0.5},
    "openness_map_mean": 0.6, "openness_by_team": {"A": 0.7, "B": None}, "fair_zone_share": 0.3,
}

META = {"name": "syn", "size": [400.0, 200.0], "height_max": 100.0, "heightmap_res": 5, "splat_res": 4}

# override key -> relative path
PATHS = {
    "meta": "export/meta.json", "r16": "export/heightmap.r16", "hpng": "export/heightmap.png",
    "forest": "export/masks/forest.png", "splat": "export/splat_0.png", "map": "map.png",
    "md": "report.md", "report": "report.json",
}


def _height():
    return (np.arange(25, dtype=np.uint16) * 1000).reshape(5, 5)


def _splat():
    a = np.zeros((4, 4, 4), np.uint8)
    a[:, :, 0] = 255
    return a


def _defaults():
    return {
        "meta": copy.deepcopy(META), "r16": _height(), "hpng": _height()[::-1].copy(),
        "forest": np.zeros((4, 4), np.uint8), "splat": _splat(),
        "map": np.zeros((8, 8, 3), np.uint8), "md": "# syn\n", "report": copy.deepcopy(REPORT),
    }


def _write(root, rel, content, key=None):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if key == "r16":
        with open(path, "wb") as f:
            f.write(np.asarray(content).astype("<u2").tobytes())
    elif isinstance(content, bytes):
        with open(path, "wb") as f:
            f.write(content)
    elif isinstance(content, np.ndarray):
        if key == "hpng":
            Image.fromarray(content, mode="I;16").save(path)
        else:
            Image.fromarray(content).save(path)
    elif isinstance(content, str):
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
    else:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(content, f, indent=1)


def make_build(root, extra=None, **overrides):
    """Write a minimal build directory under `root`; return its path.

    Overrides (meta, r16, hpng, forest, splat, map, md, report) replace one file's content, or remove the
    file when None. `extra` maps relative paths to additional content (ndarray -> PNG, str -> raw text,
    other -> JSON)."""
    root = str(root)
    content = _defaults()
    for k, v in overrides.items():
        assert k in PATHS, k
        content[k] = v
    os.makedirs(root, exist_ok=True)
    for k, rel in PATHS.items():
        if content[k] is not None:
            _write(root, rel, content[k], k)
    for rel, c in (extra or {}).items():
        _write(root, rel, c)
    return root


def _mask_pair(tmp_path):
    a = make_build(tmp_path / "a")
    f = np.zeros((4, 4), np.uint8)
    f[0, 1] = 255
    f[2, 2] = 128
    b = make_build(tmp_path / "b", forest=f)
    return a, b


def _raster(r, path):
    return [x for x in r["rasters"] if x["path"] == path][0]


def _assert_zero(r):
    assert r["identical"] is True
    assert r["files"] == {"added": [], "removed": [], "changed": [], "unchanged_count": 8}
    assert r["heightmap"]["changed_texels"] == 0
    assert r["heightmap"]["max_delta_m"] == 0.0
    assert len(r["rasters"]) == 3
    assert all(x["changed_texels"] == 0 for x in r["rasters"])
    assert r["json"] == {}
    obj = r["analysis"]["objectives"][0]
    assert obj["id"] == "o1" and obj["changed"] is False
    assert obj["delta_s"] == {"A": 0.0, "B": 0.0}
    assert r["notes"] == []


def test_self_diff_zero(tmp_path):
    p = make_build(tmp_path / "a")
    _assert_zero(D.diff_builds(p, p))


def test_copy_diff_zero(tmp_path):
    a = make_build(tmp_path / "a")
    b = make_build(tmp_path / "b")
    _assert_zero(D.diff_builds(a, b))


def test_added_removed_files(tmp_path):
    a = make_build(tmp_path / "a")
    b = make_build(tmp_path / "b", map=None, extra={"export/masks/road.png": np.zeros((4, 4), np.uint8)})
    r = D.diff_builds(a, b)
    assert r["files"]["added"] == ["export/masks/road.png"]
    assert r["files"]["removed"] == ["map.png"]
    assert r["identical"] is False
    assert all(x["path"] != "export/masks/road.png" for x in r["rasters"])


def test_heightmap_delta_and_bbox(tmp_path):
    h = _height()
    h[1, 3] = 8000 + 655
    a = make_build(tmp_path / "a")
    b = make_build(tmp_path / "b", r16=h)
    hm = D.diff_builds(a, b)["heightmap"]
    assert hm["changed_texels"] == 1
    assert hm["total_texels"] == 25
    assert hm["changed_share"] == 0.04
    assert hm["max_delta_m"] == pytest.approx(655 / 65535 * 100)
    assert hm["bbox_px"] == {"row_min": 1, "row_max": 1, "col_min": 3, "col_max": 3}
    assert hm["bbox_m"] == {"x_min": 240.0, "x_max": 320.0, "z_min": 40.0, "z_max": 80.0}


def test_heightmap_height_max_change(tmp_path):
    meta = dict(META, height_max=200.0)
    a = make_build(tmp_path / "a")
    b = make_build(tmp_path / "b", meta=meta)
    hm = D.diff_builds(a, b)["heightmap"]
    assert hm["changed_texels"] == 24
    assert hm["max_delta_m"] == pytest.approx(24000 / 65535 * 100)


def test_heightmap_res_mismatch(tmp_path):
    meta = dict(META, heightmap_res=3)
    a = make_build(tmp_path / "a")
    b = make_build(tmp_path / "b", meta=meta, r16=np.zeros((3, 3), np.uint16))
    hm = D.diff_builds(a, b)["heightmap"]
    assert hm["comparable"] is False
    assert hm["reason"] == "heightmap_res differs: 5 vs 3"
    assert hm["changed_texels"] is None


def test_heightmap_wrong_size(tmp_path):
    a = make_build(tmp_path / "a")
    b = make_build(tmp_path / "b", r16=np.zeros(24, np.uint16))
    hm = D.diff_builds(a, b)["heightmap"]
    assert hm["comparable"] is False
    assert hm["reason"] == "b: file has 48 bytes, heightmap_res 5 needs 50"


def test_heightmap_absent(tmp_path):
    a = make_build(tmp_path / "a", r16=None)
    b = make_build(tmp_path / "b", r16=None)
    r = D.diff_builds(a, b)
    assert r["heightmap"] is None
    assert r["notes"] == ["heightmap: export/heightmap.r16 is not in both builds"]


def test_mask_share_and_bbox(tmp_path):
    a, b = _mask_pair(tmp_path)
    x = _raster(D.diff_builds(a, b), "export/masks/forest.png")
    assert x["changed_texels"] == 2
    assert x["total_texels"] == 16
    assert x["changed_share"] == 0.125
    assert x["max_abs_delta"] == 255
    assert x["bbox_px"] == {"row_min": 0, "row_max": 2, "col_min": 1, "col_max": 2}
    assert x["bbox_m"] == {"x_min": 100.0, "x_max": 300.0, "z_min": 50.0, "z_max": 200.0}


def test_rgba_any_channel(tmp_path):
    s = _splat()
    s[3, 0, 1] = 10
    a = make_build(tmp_path / "a")
    b = make_build(tmp_path / "b", splat=s)
    x = _raster(D.diff_builds(a, b), "export/splat_0.png")
    assert x["changed_texels"] == 1
    assert x["max_abs_delta"] == 10
    assert x["bbox_m"] == {"x_min": 0.0, "x_max": 100.0, "z_min": 0.0, "z_max": 50.0}


def test_raster_format_mismatch(tmp_path):
    a = make_build(tmp_path / "a")
    b = make_build(tmp_path / "b", forest=np.zeros((2, 2), np.uint8))
    x = _raster(D.diff_builds(a, b), "export/masks/forest.png")
    assert x["comparable"] is False
    assert x["reason"] == "format differs: L 4x4 vs L 2x2"
    assert x["b"] == {"mode": "L", "width": 2, "height": 2}


def test_render_hash_only(tmp_path):
    m = np.zeros((8, 8, 3), np.uint8)
    m[3, 3] = 255
    a = make_build(tmp_path / "a")
    b = make_build(tmp_path / "b", map=m)
    r = D.diff_builds(a, b)
    assert "map.png" in r["files"]["changed"]
    assert all(x["path"] != "map.png" for x in r["rasters"])
    assert "map.png" not in r["json"]


def test_json_diff_ops_and_order():
    r = D.diff_json({"k": 1, "l": [1, 2, 3], "m": {"x": 1}, "n": 1},
                    {"k": 2, "l": [1, 5], "m": {"x": 1, "y": 2}, "o": 1.0}, 100)
    assert r["changes"] == [
        {"op": "changed", "path": "/k", "a": 1, "b": 2},
        {"op": "changed", "path": "/l/1", "a": 2, "b": 5},
        {"op": "removed", "path": "/l/2", "a": 3},
        {"op": "added", "path": "/m/y", "b": 2},
        {"op": "removed", "path": "/n", "a": 1},
        {"op": "added", "path": "/o", "b": 1.0},
    ]
    assert r["patterns"] == [
        {"pattern": "/k", "count": 1}, {"pattern": "/l/*", "count": 2}, {"pattern": "/m/y", "count": 1},
        {"pattern": "/n", "count": 1}, {"pattern": "/o", "count": 1},
    ]
    assert r["change_count"] == 6
    assert r["truncated"] is False
    assert r["error"] is None


def test_json_strict_types():
    assert D.diff_json({"v": 1}, {"v": 1.0}, 100)["change_count"] == 1
    assert D.diff_json({"v": True}, {"v": 1}, 100)["change_count"] == 1
    assert D.diff_json([float("nan")], [float("nan")], 100)["change_count"] == 0


def test_json_pointer_escaping():
    r = D.diff_json({"a/b~": 1}, {"a/b~": 2}, 100)
    assert r["changes"][0]["path"] == "/a~1b~0"
    r = D.diff_json({"0": 1}, {"0": 2}, 100)
    assert r["patterns"] == [{"pattern": "/0", "count": 1}]


def test_json_cap():
    a = list(range(150))
    b = [v + 1 for v in range(150)]
    r = D.diff_json(a, b, 100)
    assert r["change_count"] == 150
    assert r["truncated"] is True
    assert len(r["changes"]) == 100
    assert r["changes"][0]["path"] == "/0"
    r = D.diff_json(a, b, 0)
    assert r["change_count"] == 150
    assert r["changes"] == []
    assert r["truncated"] is True
    assert r["patterns"] == [{"pattern": "/*", "count": 150}]


def test_json_files_in_report(tmp_path):
    a = make_build(tmp_path / "a")
    b = make_build(tmp_path / "b", meta=dict(META, spec_sha256="abc123"))
    r = D.diff_builds(a, b)
    assert r["json"]["export/meta.json"]["changes"] == [
        {"op": "added", "path": "/spec_sha256", "b": "abc123"}]


def test_json_unreadable(tmp_path):
    a = make_build(tmp_path / "a", extra={"export/pois.json": []})
    b = make_build(tmp_path / "b", extra={"export/pois.json": "{"})
    e = D.diff_builds(a, b)["json"]["export/pois.json"]
    assert e["error"].startswith("b: ")
    assert e["change_count"] is None
    assert e["changes"] == []


def test_objective_deltas(tmp_path):
    rep = copy.deepcopy(REPORT)
    rep["objectives"][0]["times"]["A"] = 45.0
    rep["objectives"].append({"id": "o2", "type": "capture", "owner": None,
                              "times": {"A": 10.0, "B": None}, "spread": None, "flag": ""})
    a = make_build(tmp_path / "a")
    b = make_build(tmp_path / "b", report=rep)
    objs = D.diff_builds(a, b)["analysis"]["objectives"]
    assert [o["id"] for o in objs] == ["o1", "o2"]
    o1, o2 = objs
    assert o1["delta_s"] == {"A": -5.0, "B": 0.0}
    assert o1["changed"] is True
    assert o2["in_a"] is False
    assert o2["times_a"] is None
    assert o2["delta_s"] == {"A": None, "B": None}
    assert o2["changed"] is True


def test_team_pair_map_metrics(tmp_path):
    rep = copy.deepcopy(REPORT)
    rep["area_share"]["A"] = 0.6
    rep["openness_by_team"]["B"] = 0.8
    rep["warnings"] = ["w1"]
    a = make_build(tmp_path / "a")
    b = make_build(tmp_path / "b", report=rep)
    an = D.diff_builds(a, b)["analysis"]
    assert an["per_team"]["A"]["area_share"] == {"a": 0.5, "b": 0.6, "delta": pytest.approx(0.1),
                                                 "changed": True}
    assert an["per_team"]["B"]["openness"] == {"a": None, "b": 0.8, "delta": None, "changed": True}
    assert an["per_pair"]["A-B"]["base_time_s"]["changed"] is False
    assert an["map"]["warning_count"] == {"a": 0, "b": 1, "delta": 1, "changed": True}
    assert an["warnings"] == {"added": ["w1"], "removed": []}
    assert an["teams"] == ["A", "B"]


def test_analysis_absent(tmp_path):
    a = make_build(tmp_path / "a", report=None)
    b = make_build(tmp_path / "b")
    r = D.diff_builds(a, b)
    assert r["analysis"] is None
    assert r["notes"] == ["analysis: report.json is not readable in both builds"]
    assert r["name_a"] == "syn"


def test_report_only_dirs(tmp_path):
    gone = dict(meta=None, r16=None, hpng=None, forest=None, splat=None, map=None)
    a = make_build(tmp_path / "a", **gone)
    b = make_build(tmp_path / "b", **gone)
    r = D.diff_builds(a, b)
    assert r["heightmap"] is None
    assert r["rasters"] == []
    assert r["analysis"] is not None


def test_check_build_dir_errors(tmp_path):
    missing = str(tmp_path / "nope")
    with pytest.raises(D.DiffError) as e:
        D.check_build_dir(missing)
    assert str(e.value) == "error: directory not found: %s" % missing
    f = tmp_path / "file.txt"
    f.write_text("x")
    with pytest.raises(D.DiffError) as e:
        D.check_build_dir(str(f))
    assert str(e.value) == "error: not a directory: %s" % f
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(D.DiffError) as e:
        D.check_build_dir(str(empty))
    assert str(e.value) == ("error: not a mapgen build directory (no report.json or export/meta.json): %s"
                            % empty)


def _rich_pair(root):
    """a/ and b/ under root: b has a changed mask, a changed report, an unreadable splat PNG."""
    rep = copy.deepcopy(REPORT)
    rep["area_share"]["A"] = 0.6
    make_build(root / "a")
    make_build(root / "b", forest=_changed_forest(), report=rep, splat=b"not a png")


def test_format_json_deterministic(tmp_path, monkeypatch):
    texts = []
    for loc in ("x", "y"):
        root = tmp_path / loc
        _rich_pair(root)
        monkeypatch.chdir(str(root))
        r = D.diff_builds("a", "b")
        texts.append(D.format_json(r))
        texts.append(D.format_json(D.diff_builds("a", "b")))
    assert len(set(texts)) == 1  # two runs, two absolute locations, same relative arguments
    s1 = texts[0]
    assert s1.endswith("}\n")
    assert "\r" not in s1
    rep = json.loads(s1)
    assert sorted(rep) == ["a", "analysis", "b", "diff_format", "files", "heightmap",
                           "identical", "json", "name_a", "name_b", "notes", "rasters"]
    assert rep["a"] == "a" and rep["b"] == "b"
    assert "export/masks/forest.png" in [x["path"] for x in rep["rasters"] if x["changed_texels"]]
    assert "report.json" in rep["json"]
    assert json.dumps(str(tmp_path))[1:-1] not in s1


def test_paths_do_not_enter_report_apart_from_arguments(tmp_path):
    reps = []
    for loc in ("x", "y"):
        root = tmp_path / loc
        _rich_pair(root)
        r = json.loads(D.format_json(D.diff_builds(str(root / "a"), str(root / "b"))))
        assert r.pop("a") == str(root / "a") and r.pop("b") == str(root / "b")
        reps.append(r)
    assert reps[0] == reps[1]


def _changed_forest():
    f = np.zeros((4, 4), np.uint8)
    f[0, 1] = 255
    f[2, 2] = 128
    return f


def test_text_summary(tmp_path):
    p = make_build(tmp_path / "self")
    lines = D.format_text(D.diff_builds(p, p)).splitlines()
    assert "identical: yes" in lines
    assert "files: 0 changed, 0 added, 0 removed, 8 unchanged" in lines
    assert "rasters: 0 of 3 changed" in lines

    a, b = _mask_pair(tmp_path)
    text = D.format_text(D.diff_builds(a, b))
    assert any(ln.startswith("  export/masks/forest.png  12.5000% (2 texels), max delta 255, "
                             "x 100..300 z 50..200") for ln in text.splitlines())
    text.encode("ascii")
    assert text.endswith("\n")


def _arg_forms(root):
    """Build-argument spellings: relative, absolute, absolute with '/' separators (mixed on Windows)."""
    return {
        "relative": ("a", "b"),
        "absolute": (str(root / "a"), str(root / "b")),
        "mixed": (str(root / "a").replace("\\", "/"), str(root / "b").replace("\\", "/")),
    }


@pytest.mark.parametrize("form", ["relative", "absolute", "mixed"])
def test_unreadable_png_reason_is_build_relative(tmp_path, monkeypatch, form):
    make_build(tmp_path / "a")
    make_build(tmp_path / "b", forest=b"not a png")
    monkeypatch.chdir(str(tmp_path))
    a, b = _arg_forms(tmp_path)[form]
    x = _raster(D.diff_builds(a, b), "export/masks/forest.png")
    assert x["comparable"] is False
    assert x["b"] is None
    assert x["reason"] == "b: cannot read: cannot identify image file 'export/masks/forest.png'"
    assert str(tmp_path) not in x["reason"] and str(tmp_path).replace("\\", "/") not in x["reason"]


def _deny_text_open(monkeypatch, under):
    real = builtins.open

    def fake(file, mode="r", *args, **kw):
        if "b" not in mode and os.path.abspath(str(file)).startswith(os.path.abspath(under)):
            raise PermissionError(13, "Permission denied", file)
        return real(file, mode, *args, **kw)

    monkeypatch.setattr(builtins, "open", fake)


@pytest.mark.parametrize("form", ["relative", "absolute", "mixed"])
def test_json_os_error_is_build_relative(tmp_path, monkeypatch, form):
    make_build(tmp_path / "a", extra={"export/pois.json": []})
    make_build(tmp_path / "b", extra={"export/pois.json": [1]})
    monkeypatch.chdir(str(tmp_path))
    a, b = _arg_forms(tmp_path)[form]
    _deny_text_open(monkeypatch, str(tmp_path / "b"))
    e = D.diff_builds(a, b)["json"]["export/pois.json"]
    assert e["error"] == "b: [Errno 13] Permission denied: 'export/pois.json'"
    assert e["change_count"] is None


@pytest.mark.parametrize("meta_patch", [
    {"heightmap_res": 0}, {"heightmap_res": -5}, {"heightmap_res": "5"}, {"heightmap_res": True},
    {"heightmap_res": 5.0}, {"heightmap_res": None}, {"heightmap_res": [5]},
    {"height_max": "x"}, {"height_max": None}, {"height_max": float("nan")},
    {"height_max": float("inf")}, {"height_max": 0}, {"height_max": -1.0}, {"height_max": True},
    {"height_max": 10 ** 400},
])
@pytest.mark.parametrize("side", ["a", "b"])
def test_heightmap_invalid_meta_is_not_comparable(tmp_path, meta_patch, side):
    bad = dict(META, **meta_patch)
    kw = {"meta": bad}
    a = make_build(tmp_path / "a", **(kw if side == "a" else {}))
    b = make_build(tmp_path / "b", **(kw if side == "b" else {}))
    hm = D.diff_builds(a, b)["heightmap"]
    assert hm["comparable"] is False
    assert hm["reason"] == "%s: export/meta.json lacks heightmap_res or height_max" % side
    assert hm["changed_texels"] is None


@pytest.mark.parametrize("missing", ["heightmap_res", "height_max"])
def test_heightmap_meta_lacks_key(tmp_path, missing):
    meta = dict(META)
    del meta[missing]
    a = make_build(tmp_path / "a")
    b = make_build(tmp_path / "b", meta=meta)
    hm = D.diff_builds(a, b)["heightmap"]
    assert hm["comparable"] is False
    assert hm["reason"] == "b: export/meta.json lacks heightmap_res or height_max"
    assert hm["b"] is None


def test_heightmap_meta_unparseable(tmp_path):
    a = make_build(tmp_path / "a")
    b = make_build(tmp_path / "b", meta="{")
    hm = D.diff_builds(a, b)["heightmap"]
    assert hm["reason"] == "b: export/meta.json lacks heightmap_res or height_max"


def test_objective_id_not_string_is_skipped(tmp_path):
    rep = copy.deepcopy(REPORT)
    good = rep["objectives"][0]
    rep["objectives"] = [{"id": 5}, {"id": None}, {"id": ["x"]}, {"id": {"k": 1}}, {"id": True}, good]
    a = make_build(tmp_path / "a", report=rep)
    b = make_build(tmp_path / "b", report=rep)
    objs = D.diff_builds(a, b)["analysis"]["objectives"]
    assert [o["id"] for o in objs] == ["o1"]


def test_duplicate_objective_ids(tmp_path):
    ra, rb = copy.deepcopy(REPORT), copy.deepcopy(REPORT)

    def obj(oid, t):
        return {"id": oid, "type": "capture", "owner": None, "times": {"A": t, "B": 60.0},
                "spread": 0.2, "flag": ""}
    ra["objectives"] = [obj("o1", 50.0), obj("o1", 1.0), obj("o2", 7.0), obj("o2", 8.0), obj("o2", 9.0)]
    rb["objectives"] = [obj("o1", 50.0), obj("o1", 2.0), obj("o1", 3.0)]
    a = make_build(tmp_path / "a", r16=None, report=ra)
    b = make_build(tmp_path / "b", r16=None, report=rb)
    r = D.diff_builds(a, b)
    objs = r["analysis"]["objectives"]
    assert [o["id"] for o in objs] == ["o1", "o2"]
    assert objs[0]["times_a"] == objs[0]["times_b"] == {"A": 50.0, "B": 60.0}
    assert objs[0]["changed"] is False
    assert objs[1]["in_b"] is False and objs[1]["times_a"]["A"] == 7.0
    assert r["notes"] == [
        "heightmap: export/heightmap.r16 is not in both builds",
        "analysis: objective id o1 occurs more than once in a; first entry used",
        "analysis: objective id o2 occurs more than once in a; first entry used",
        "analysis: objective id o1 occurs more than once in b; first entry used",
    ]
    text = D.format_text(r)
    assert "note: analysis: objective id o2 occurs more than once in a; first entry used" in text


def test_record_strict_nested():
    nan = float("nan")
    assert D._record({"k": 1}, {"k": 1.0})["changed"] is True
    assert D._record([True], [1])["changed"] is True
    assert D._record({"k": [1, {"z": 2}]}, {"k": [1, {"z": 2.0}]})["changed"] is True
    assert D._record([nan], [nan])["changed"] is False
    assert D._record({"k": nan}, {"k": nan})["changed"] is False
    assert D._record({"a": 1, "b": 2}, {"b": 2, "a": 1})["changed"] is False
    assert D._record([1, 2], [1, 2, 3])["changed"] is True
    assert D._record({"a": 1}, {"b": 1})["changed"] is True
    assert D._record(None, 1) == {"a": None, "b": 1, "delta": None, "changed": True}


def test_objective_times_strict_types(tmp_path):
    rep = copy.deepcopy(REPORT)
    rep["objectives"][0]["times"]["A"] = 50  # int where a has 50.0
    a = make_build(tmp_path / "a")
    b = make_build(tmp_path / "b", report=rep)
    o = D.diff_builds(a, b)["analysis"]["objectives"][0]
    assert o["delta_s"] == {"A": 0.0, "B": 0.0}
    assert o["changed"] is True


def _png(img, **save):
    bio = io.BytesIO()
    img.save(bio, "PNG", **save)
    return bio.getvalue()


def _palette_png(rgb):
    im = Image.new("P", (4, 4), 0)
    im.putpalette(list(rgb) + [0] * 765)
    return _png(im)


def test_palette_png_compared_by_colour(tmp_path):
    a = make_build(tmp_path / "a", forest=_palette_png((0, 0, 0)))
    b = make_build(tmp_path / "b", forest=_palette_png((255, 0, 0)))
    r = D.diff_builds(a, b)
    x = _raster(r, "export/masks/forest.png")
    assert x["a"]["mode"] == "P"
    assert x["comparable"] is True
    assert x["changed_texels"] == 16
    assert x["max_abs_delta"] == 255
    assert x["pixels_identical"] is False
    same = D.diff_builds(a, make_build(tmp_path / "c", forest=_palette_png((0, 0, 0))))
    assert _raster(same, "export/masks/forest.png")["changed_texels"] == 0


def test_palette_png_same_colours_different_indices(tmp_path):
    def two(indices):
        im = Image.new("P", (4, 4), 0)
        im.putpalette([10, 10, 10, 10, 10, 10] + [0] * 762)
        im.putdata(indices)
        return _png(im)
    a = make_build(tmp_path / "a", forest=two([0] * 16))
    b = make_build(tmp_path / "b", forest=two([1] * 16))
    x = _raster(D.diff_builds(a, b), "export/masks/forest.png")
    assert x["comparable"] is True
    assert x["changed_texels"] == 0  # same colours under different indices
    assert x["pixels_identical"] is True


def test_raster_metadata_only_difference_is_explicit(tmp_path):
    from PIL.PngImagePlugin import PngInfo
    meta = PngInfo()
    meta.add_text("note", "x")
    zeros = Image.fromarray(np.zeros((4, 4), np.uint8))
    a = make_build(tmp_path / "a", forest=_png(zeros))
    b = make_build(tmp_path / "b", forest=_png(zeros, pnginfo=meta))
    r = D.diff_builds(a, b)
    assert "export/masks/forest.png" in r["files"]["changed"]
    x = _raster(r, "export/masks/forest.png")
    assert x["comparable"] is True
    assert x["changed_texels"] == 0
    assert x["pixels_identical"] is True
    assert "  export/masks/forest.png  pixels identical, file bytes differ" in D.format_text(r).splitlines()
    assert "rasters: 1 of 3 changed" in D.format_text(r).splitlines()


def test_raster_hash_differs_is_never_silently_zero(tmp_path):
    """Invariant: a raster whose bytes differ is not comparable, has changed texels, or says
    pixels_identical explicitly."""
    a, b = _mask_pair(tmp_path)
    r = D.diff_builds(a, b)
    for x in r["rasters"]:
        if x["path"] in r["files"]["changed"]:
            assert (not x["comparable"]) or x["changed_texels"] > 0 or x["pixels_identical"] is True
    for x in r["rasters"]:
        if x["comparable"]:
            assert x["pixels_identical"] == (x["changed_texels"] == 0)
        else:
            assert x["pixels_identical"] is None


def test_raster_mode_not_numerically_comparable(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "_NUMERIC_MODES", ("RGBA",))
    a, b = _mask_pair(tmp_path)
    r = D.diff_builds(a, b)
    x = _raster(r, "export/masks/forest.png")
    assert x["comparable"] is False
    assert x["reason"] == "mode L cannot be compared"
    assert x["changed_texels"] is None and x["pixels_identical"] is None
    assert "  export/masks/forest.png  not comparable: mode L cannot be compared" in D.format_text(r)


@pytest.mark.parametrize("mode", ["1", "LA", "L"])
def test_other_png_modes_compare(tmp_path, mode):
    def img(v):
        im = Image.new(mode, (4, 4))
        im.putpixel((1, 1), v)
        return _png(im)
    one = (1,) if mode == "1" else ((1, 255) if mode == "LA" else 1)
    two = (0,) if mode == "1" else ((0, 255) if mode == "LA" else 0)
    a = make_build(tmp_path / "a", forest=img(one))
    b = make_build(tmp_path / "b", forest=img(two))
    x = _raster(D.diff_builds(a, b), "export/masks/forest.png")
    assert x["comparable"] is True
    assert x["changed_texels"] == 1


def test_team_in_one_build_only(tmp_path):
    rep = copy.deepcopy(REPORT)
    rep["teams"]["C"] = {"name": "C", "base": [200.0, 100.0]}
    rep["area_share"]["C"] = 0.1
    rep["openness_by_team"]["C"] = 0.4
    a = make_build(tmp_path / "a")
    b = make_build(tmp_path / "b", report=rep)
    an = D.diff_builds(a, b)["analysis"]
    assert an["teams"] == ["A", "B", "C"]
    assert an["per_team"]["C"]["area_share"] == {"a": None, "b": 0.1, "delta": None, "changed": True}
    assert an["per_team"]["C"]["openness"] == {"a": None, "b": 0.4, "delta": None, "changed": True}
    back = D.diff_builds(b, a)["analysis"]
    assert back["per_team"]["C"]["area_share"] == {"a": 0.1, "b": None, "delta": None, "changed": True}


def test_objective_only_in_a(tmp_path):
    rep = copy.deepcopy(REPORT)
    rep["objectives"] = []
    a = make_build(tmp_path / "a")
    b = make_build(tmp_path / "b", report=rep)
    r = D.diff_builds(a, b)
    o = r["analysis"]["objectives"][0]
    assert o["id"] == "o1"
    assert (o["in_a"], o["in_b"]) == (True, False)
    assert o["times_b"] is None and o["times_a"] == {"A": 50.0, "B": 60.0}
    assert o["delta_s"] == {"A": None, "B": None}
    assert (o["type"], o["owner"]) == ("capture", None)
    assert o["flag_b"] is None
    assert o["changed"] is True
    assert "  o1  only in a" in D.format_text(r).splitlines()


def test_objective_only_in_b(tmp_path):
    rep = copy.deepcopy(REPORT)
    rep["objectives"] = []
    a = make_build(tmp_path / "a", report=rep)
    b = make_build(tmp_path / "b")
    r = D.diff_builds(a, b)
    o = r["analysis"]["objectives"][0]
    assert (o["in_a"], o["in_b"]) == (False, True)
    assert o["times_a"] is None and o["flag_a"] is None
    assert o["delta_s"] == {"A": None, "B": None}
    assert o["changed"] is True
    assert "  o1  only in b" in D.format_text(r).splitlines()


def test_null_time_against_number(tmp_path):
    rep = copy.deepcopy(REPORT)
    rep["objectives"][0]["times"]["B"] = None
    a = make_build(tmp_path / "a")
    b = make_build(tmp_path / "b", report=rep)
    r = D.diff_builds(a, b)
    o = r["analysis"]["objectives"][0]
    assert o["delta_s"] == {"A": 0.0, "B": None}
    assert o["changed"] is True
    assert any(ln.startswith("  o1  A +0.0  B -  ") for ln in D.format_text(r).splitlines())


def test_non_finite_values_serialised_as_strings(tmp_path):
    nan, inf = float("nan"), float("inf")
    a = make_build(tmp_path / "a", extra={"export/pois.json": [1.0, 2.0, 3.0, nan]})
    b = make_build(tmp_path / "b", extra={"export/pois.json": [nan, inf, -inf, nan]})
    r = D.diff_builds(a, b)
    s = D.format_json(r)

    def refuse(token):
        raise AssertionError("bare %s in output" % token)
    out = json.loads(s, parse_constant=refuse)
    changes = out["json"]["export/pois.json"]["changes"]
    assert [c["b"] for c in changes] == ["NaN", "Infinity", "-Infinity"]
    assert out["json"]["export/pois.json"]["change_count"] == 3  # NaN equals NaN at index 3
    assert out["json"]["export/pois.json"]["changes"][0]["a"] == 1.0


@pytest.mark.parametrize("cap,truncated", [(3, False), (2, True), (4, False)])
def test_json_truncated_boundary(cap, truncated):
    r = D.diff_json([0, 0, 0], [1, 1, 1], cap)
    assert r["change_count"] == 3
    assert r["truncated"] is truncated
    assert len(r["changes"]) == min(cap, 3)


def test_json_truncated_cap_and_cap_plus_one():
    cap = 5
    exact = D.diff_json(list(range(cap)), [v + 1 for v in range(cap)], cap)
    over = D.diff_json(list(range(cap + 1)), [v + 1 for v in range(cap + 1)], cap)
    assert (exact["change_count"], exact["truncated"], len(exact["changes"])) == (cap, False, cap)
    assert (over["change_count"], over["truncated"], len(over["changes"])) == (cap + 1, True, cap)


def test_text_not_comparable_raster(tmp_path):
    a = make_build(tmp_path / "a")
    b = make_build(tmp_path / "b", forest=np.zeros((2, 2), np.uint8))
    lines = D.format_text(D.diff_builds(a, b)).splitlines()
    assert "rasters: 1 of 3 changed" in lines
    assert "  export/masks/forest.png  not comparable: format differs: L 4x4 vs L 2x2" in lines


def test_text_analysis_absent_and_notes(tmp_path):
    a = make_build(tmp_path / "a", report=None, r16=None)
    b = make_build(tmp_path / "b", r16=None)
    text = D.format_text(D.diff_builds(a, b))
    lines = text.splitlines()
    assert "heightmap: not in both builds" in lines
    assert "note: heightmap: export/heightmap.r16 is not in both builds" in lines
    assert "note: analysis: report.json is not readable in both builds" in lines
    for prefix in ("objectives:", "per team:", "per pair:", "map:"):
        assert not any(ln.startswith(prefix) for ln in lines)
    assert lines.index("note: heightmap: export/heightmap.r16 is not in both builds") < lines.index(
        "note: analysis: report.json is not readable in both builds")


def test_text_unreadable_json_and_not_comparable_heightmap(tmp_path):
    a = make_build(tmp_path / "a", extra={"export/pois.json": []})
    b = make_build(tmp_path / "b", extra={"export/pois.json": "{"}, meta=dict(META, heightmap_res=0))
    lines = D.format_text(D.diff_builds(a, b)).splitlines()
    assert any(ln.startswith("  export/pois.json: unreadable: b: ") for ln in lines)
    assert ("heightmap: not comparable: b: export/meta.json lacks heightmap_res or height_max") in lines


def test_text_never_prints_negative_zero(tmp_path):
    rep = copy.deepcopy(REPORT)
    rep["objectives"][0]["times"]["A"] = 49.96   # delta -0.04 prints as -0.0 under %+.1f
    rep["area_share"]["A"] = -0.0
    ra = copy.deepcopy(REPORT)
    ra["area_share"]["A"] = 0.0
    a = make_build(tmp_path / "a", report=ra)
    b = make_build(tmp_path / "b", report=rep)
    text = D.format_text(D.diff_builds(a, b))
    assert "o1  A +0.0  B +0.0" in text
    lines = text.splitlines()
    assert not any(" -0.0" in ln or "(-0)" in ln or " -0 " in ln or ln.endswith(" -0") for ln in lines)
    assert D._rec_text("x", {"a": 1.0, "b": 1.0, "delta": -0.0, "changed": False}) == "x 1 -> 1 (+0)"
    assert D._v(-0.0) == "0"
    assert D._box_text({"x_min": -0.0, "x_max": 1.0, "z_min": 0.0, "z_max": -0.0}) == ", x 0..1 z 0..0"


@pytest.fixture(scope="module")
def river(tmp_path_factory):
    from mapgen import build
    src = os.path.join(regress.REPO_ROOT, "maps", "example_river_crossing.yaml")
    with open(src, "r", encoding="utf-8", newline="") as f:
        text = f.read()
    for old, new in (("at: [1500, 900] }", "at: [1500, 1000] }"),
                     ("width: 450, height: 220 }", "width: 450, height: 240 }")):
        assert text.count(old) == 1, old
        text = text.replace(old, new)
    base = tmp_path_factory.mktemp("river")
    spec_b = base / "spec_b"
    spec_b.mkdir()
    with open(str(spec_b / "river_crossing.yaml"), "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    a = build.build(src, str(base / "out_a"), log=lambda s: None)
    b = build.build(str(spec_b / "river_crossing.yaml"), str(base / "out_b"), log=lambda s: None)
    return str(a), str(b)


def test_river_crossing_edit(river, capsys):
    A, B = river
    s = D.diff_builds(A, A)
    assert s["identical"] is True
    assert s["heightmap"]["changed_texels"] == 0
    assert s["json"] == {}
    assert all(o["changed"] is False for o in s["analysis"]["objectives"])

    r = D.diff_builds(A, B)
    f = r["files"]
    assert f["added"] == [] and f["removed"] == []
    ha, hb = regress.hash_tree(A), regress.hash_tree(B)
    differ = set(k for k in ha if ha[k] != hb.get(k))
    assert set(f["changed"]) == differ
    for p in ("export/heightmap.r16", "export/pois.json", "report.json"):
        assert p in f["changed"]
    assert "export/masks/forest.png" not in f["changed"]

    hm = r["heightmap"]
    assert hm["comparable"] is True
    assert hm["changed_share"] > 0.05
    assert 10 < hm["max_delta_m"] < 30

    for x in r["rasters"]:
        assert (x["changed_texels"] > 0) == (x["path"] in f["changed"]), x["path"]
    assert _raster(r, "export/masks/impassable.png")["changed_texels"] > 0

    assert r["json"]["export/meta.json"]["patterns"] == [{"count": 1, "pattern": "/spec_sha256"}]
    sr = r["json"]["export/spec.resolved.json"]
    assert sr["patterns"] == [{"count": 1, "pattern": "/pois/*/at/*"},
                              {"count": 1, "pattern": "/regions/*/height"}]
    assert {"op": "changed", "path": "/pois/6/at/1", "a": 900.0, "b": 1000.0} in sr["changes"]
    assert {"op": "changed", "path": "/regions/2/height", "a": 220.0, "b": 240.0} in sr["changes"]

    an = r["analysis"]
    assert [o["id"] for o in an["objectives"]] == ["bridge_keep", "camp_ne", "camp_sw", "relic", "cap_s"]
    cap = [o for o in an["objectives"] if o["id"] == "cap_s"][0]
    assert cap["delta_s"]["A"] < -10 and cap["delta_s"]["B"] < -10
    assert sorted(an["per_team"]) == ["A", "B"]
    for t in ("A", "B"):
        assert "area_share" in an["per_team"][t] and "openness" in an["per_team"][t]
    base_a = an["per_pair"]["A-B"]["base_time_s"]["a"]
    assert isinstance(base_a, (int, float)) and not isinstance(base_a, bool)
    assert D.format_json(D.diff_builds(A, B)) == D.format_json(r)

    capsys.readouterr()
    assert main(["diff", A, B]) == 0
    assert json.loads(capsys.readouterr().out)["identical"] is False
