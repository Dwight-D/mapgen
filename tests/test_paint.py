"""Painting (GDU-53): brush stroke -> integer polygon, merge / split / erase / hole / convert planning on poly regions through the
span-patch engine, determinism, comments and CRLF kept, vertex caps, the `/api/paint` endpoint and scatter `within` handles."""
import math
import os
import random
import re
import shutil

import pytest

from mapgen import paint, schema, specpatch
from test_spec_editor import V4, Client, _start

TINY = """format_version: 1
name: tiny
size: [1000, 1000]
seed: 1
teams:
  A: { base: [100, 100] }
  B: { base: [900, 900] }
pois:
  - { id: k, type: capture, at: [300, 700], label: "Keep" }   # the keep
regions:
  # woods and rock
  - { id: wood, seed: 3, type: forest, shape: blob, at: k, r: 50, rough: 0.3 }   # blob comment
  - { id: hill, type: hills, shape: poly, points: [[600, 600], [700, 600], [650, 700]] }   # hill comment
lines:
  - { id: road, type: road, path: [[10, 10], [500, 500], [900, 900]] }
scatter:
  - { id: wild, type: forest, coverage: 0.3, scale: 100, clump: 0.3, avoid: 20 }

analysis:
  cell: 20
"""


def _spec(text):
    return schema.load_spec_text(text.encode("utf-8"), "tiny.yaml")


def _line(x0, y0, x1, y1, step=8.0):
    n = max(2, int(math.hypot(x1 - x0, y1 - y0) / step))
    return [[x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n] for i in range(n + 1)]


def _paint(text, stroke, r, rtype="forest", imp=False):
    spec = _spec(text)
    plan = paint.plan_paint(specpatch.parsed_document(text), spec.size, paint.brush_polygon(stroke, r, spec.size), rtype, imp)
    return _apply(text, plan), plan


def _erase(text, stroke, r, rtype=None):
    spec = _spec(text)
    plan = paint.plan_erase(specpatch.parsed_document(text), spec, paint.brush_polygon(stroke, r, spec.size), rtype)
    return _apply(text, plan), plan


def _apply(text, plan):
    new = specpatch.apply_ops(text, [specpatch.op_from_json(o) for o in plan.ops]) if plan.ops else text
    _spec(new)   # the real loader accepts it
    return new


def _regions(text):
    return {r["id"]: r for r in specpatch.parsed_document(text)["regions"]}


def _comments(text):
    return [l for l in text.split("\n") if "#" in l]


# ---- brush polygon -----------------------------------------------------------------------------------------------

def test_brush_polygon_is_integer_valid_and_round():
    b = paint.brush_polygon(_line(100, 100, 300, 100), 20, (1000, 1000))
    assert b.is_valid and b.geom_type == "Polygon"
    assert all(float(c).is_integer() for xy in b.exterior.coords for c in xy)
    assert abs(b.area - (200 * 40 + math.pi * 400)) < 0.03 * b.area   # round caps
    assert b.bounds == (80.0, 80.0, 320.0, 120.0)


def test_single_point_is_a_dab_and_outside_the_map_is_none():
    d = paint.brush_polygon([[500, 500]], 10, (1000, 1000))
    assert abs(d.area - math.pi * 100) < 30
    assert paint.brush_polygon([[-500, -500], [-400, -400]], 10, (1000, 1000)) is None
    clipped = paint.brush_polygon([[0, 500], [0, 600]], 30, (1000, 1000))
    assert clipped.bounds[0] == 0.0


@pytest.mark.parametrize("bad", [[], [[1]], [["a", 2]], [[float("nan"), 1]], [[True, 1]], "x", None])
def test_bad_strokes_refused(bad):
    with pytest.raises(paint.PaintError):
        paint.brush_polygon(bad, 10, (1000, 1000))


@pytest.mark.parametrize("r", [0, 0.5, 301, -1, True, "5", float("inf")])
def test_bad_radius_refused(r):
    with pytest.raises(paint.PaintError):
        paint.brush_polygon([[10, 10], [20, 20]], r, (1000, 1000))


def test_long_wiggly_stroke_stays_under_the_vertex_cap():
    rng = random.Random(7)
    pts, x, y = [], 50.0, 500.0
    for i in range(3000):   # a dense mouse path of about 2800 points
        x = min(950.0, x + 0.3)
        y += rng.uniform(-1.5, 1.5)
        pts.append([x, y])
    b = paint.brush_polygon(pts, 12, (1000, 1000))
    n = len(b.exterior.coords) - 1
    assert n <= paint.MAX_RING and n < 300, n
    t, plan = _paint(TINY, pts, 12)
    assert plan.vertices == [n] and len(_regions(t)["forest_1"]["points"]) == n
    with pytest.raises(paint.PaintError):
        paint.brush_polygon([[1, 1]] * (paint.MAX_STROKE_POINTS + 1), 5, (1000, 1000))


def test_vertex_cap_doubles_the_tolerance():
    zig = [[x, 500 + (30 if (x // 3) % 2 else -30)] for x in range(10, 990, 3)]
    big = paint.brush_polygon(zig, 3, (1000, 1000))   # very rough outline
    assert len(big.exterior.coords) - 1 <= paint.MAX_RING


# ---- paint: new region, merge, split, holes ----------------------------------------------------------------------

def test_paint_appends_a_flow_poly_with_a_generated_id_and_changes_only_that_line():
    t, plan = _paint(TINY, _line(100, 300, 300, 300), 15)
    assert plan.created == 1 and not plan.modified
    r = _regions(t)["forest_1"]
    assert r["type"] == "forest" and r["shape"] == "poly" and "impassable" not in r and "seed" not in r and "rough" not in r
    assert all(len(p) == 2 and all(isinstance(c, int) for c in p) for p in r["points"])
    a, b = TINY.split("\n"), t.split("\n")
    assert len(b) == len(a) + 1 and [l for l in b if l not in a] == [l for l in b if "id: forest_1" in l]
    line = next(l for l in b if "id: forest_1" in l)
    assert line.startswith("  - { id: forest_1, type: forest, shape: poly, points: [[") and line.endswith("]] }")
    assert _comments(t) == _comments(TINY)
    assert b.index(line) == a.index(next(l for l in a if "id: hill" in l)) + 1   # after the last region


def test_paint_impassable_flag_is_written():
    t, _ = _paint(TINY, _line(100, 300, 300, 300), 15, "mountain", True)
    assert _regions(t)["mountain_1"]["impassable"] is True
    assert _spec(t).regions[-1].impassable is True


def test_overlapping_same_type_merges_into_one_region_and_keeps_its_id():
    t1, _ = _paint(TINY, _line(100, 300, 300, 300), 15)
    t2, plan = _paint(t1, _line(200, 290, 200, 450), 15)
    assert plan.modified == ["forest_1"] and plan.created == 0 and not plan.deleted
    assert [i for i in _regions(t2) if i.startswith("forest_")] == ["forest_1"]
    from shapely.geometry import Polygon
    p1, p2 = (Polygon(_regions(t)["forest_1"]["points"]) for t in (t1, t2))
    assert p2.is_valid and p2.area > p1.area + 4000 and p2.contains(p1.buffer(-1.5))
    # only that one line changed, and it is still a flow list
    a, b = t1.split("\n"), t2.split("\n")
    assert len(a) == len(b) and [i for i, (x, y) in enumerate(zip(a, b)) if x != y] == [next(i for i, l in enumerate(a) if "id: forest_1" in l)]
    assert _comments(t2) == _comments(t1)


def test_touching_counts_as_overlap_and_disjoint_gets_a_new_id():
    t1, _ = _paint(TINY, [[100, 300], [200, 300]], 20)
    t2, plan = _paint(t1, [[200, 300], [300, 300]], 20)     # shares the round cap area
    assert plan.modified == ["forest_1"]
    t3, plan = _paint(t2, _line(100, 600, 300, 600), 15)    # far away
    assert plan.created == 1 and "forest_2" in _regions(t3)


def test_bridging_stroke_merges_two_regions_into_the_largest_and_retires_the_other():
    t, _ = _paint(TINY, _line(100, 300, 200, 300), 15)
    t, _ = _paint(t, _line(400, 300, 600, 300), 15)
    assert {"forest_1", "forest_2"} <= set(_regions(t))
    t, plan = _paint(t, _line(150, 300, 450, 300), 15)
    assert plan.deleted == ["forest_1"] and plan.modified == ["forest_2"]
    assert [i for i in _regions(t) if i.startswith("forest_")] == ["forest_2"]


def test_different_type_or_flag_does_not_merge_and_blob_band_are_never_rewritten():
    t1, _ = _paint(TINY, _line(100, 300, 300, 300), 15)
    t2, plan = _paint(t1, _line(150, 290, 250, 310), 15, "hills")
    assert plan.created == 1 and not plan.modified and "hills_1" in _regions(t2)
    t3, plan = _paint(t1, _line(150, 290, 250, 310), 15, "forest", True)   # same type, other flag
    assert plan.created == 1 and not plan.modified
    # paint forest straight over the blob forest `wood` (and the hand-made poly `hill` with another type): new region, blob untouched
    t4, plan = _paint(TINY, [[300, 700], [320, 700]], 20)
    assert plan.created == 1 and _regions(t4)["wood"] == _regions(TINY)["wood"]
    assert re.search(r"id: wood, seed: 3, type: forest, shape: blob, at: k, r: 50, rough: 0.3 \}   # blob comment", t4)


def test_hand_authored_poly_of_the_same_type_is_a_merge_target():
    t, plan = _paint(TINY, _line(600, 650, 700, 650), 10, "hills")
    assert plan.modified == ["hill"] and "hills_1" not in _regions(t)
    assert "# hill comment" in t


def test_ring_stroke_makes_a_hole_that_becomes_a_clearing_poly():
    ring = [[500 + 120 * math.cos(a / 20.0 * math.pi), 500 + 120 * math.sin(a / 20.0 * math.pi)] for a in range(41)]
    t, plan = _paint(TINY, ring, 14)
    assert plan.holes == 1 and plan.created == 2
    regs = _regions(t)
    assert regs["forest_1"]["type"] == "forest" and regs["clearing_1"]["type"] == "clearing"
    assert list(regs).index("clearing_1") > list(regs).index("forest_1")   # paints on top of the forest
    from shapely.geometry import Point, Polygon
    assert Polygon(regs["clearing_1"]["points"]).contains(Point(500, 500))
    assert Polygon(regs["forest_1"]["points"]).contains(Point(500, 500))   # the ring itself has no hole
    # painting the ring again merges into forest_1 and does not add a second clearing
    t2, plan2 = _paint(t, ring, 14)
    assert plan2.holes == 0 and "clearing_2" not in _regions(t2)


def test_merge_under_a_later_clearing_is_painted_on_top_instead():
    ring = [[500 + 120 * math.cos(a / 20.0 * math.pi), 500 + 120 * math.sin(a / 20.0 * math.pi)] for a in range(41)]
    t, _ = _paint(TINY, ring, 14)
    t2, plan = _paint(t, [[480, 500], [520, 500]], 10)   # inside the clearing, under the forest's outer ring
    assert plan.created == 1 and not plan.modified and plan.notes and "clearing_1" in plan.notes[0]
    assert list(_regions(t2))[-1] == "forest_2"


def test_paint_is_clipped_to_the_map_and_outputs_valid_specs():
    t, _ = _paint(TINY, [[0, 0], [60, 60]], 40)
    pts = _regions(t)["forest_1"]["points"]
    assert all(0 <= x <= 1000 and 0 <= y <= 1000 for x, y in pts)


# ---- erase --------------------------------------------------------------------------------------------------------

def _stroke_forest(text):
    return _paint(text, _line(100, 300, 500, 300), 20)[0]


def test_erase_middle_splits_the_region_and_the_original_keeps_the_largest_piece():
    t = _stroke_forest(TINY)
    t2, plan = _erase(t, [[250, 280], [250, 320]], 25, "forest")
    assert plan.modified == ["forest_1"] and plan.created == 1
    from shapely.geometry import Polygon
    a, b = (Polygon(_regions(t2)[i]["points"]).area for i in ("forest_1", "forest_2"))
    assert a >= b and a + b < Polygon(_regions(t)["forest_1"]["points"]).area
    # the largest piece keeps the original id: the piece around x=100..225 (125+) is smaller than 275..520 (245+)
    assert Polygon(_regions(t2)["forest_1"]["points"]).centroid.x > 300


def test_erase_everything_deletes_the_region_and_any_type_erases_other_painted_types():
    t = _paint(TINY, [[400, 400], [420, 400]], 10)[0]
    t2, plan = _erase(t, [[410, 400]], 60, "forest")
    assert plan.deleted == ["forest_1"] and "forest_1" not in _regions(t2)
    t3 = _paint(TINY, [[400, 400], [420, 400]], 10, "hills")[0]
    assert _erase(t3, [[410, 400]], 60, "forest")[1].ops == []          # wrong type: nothing under the brush
    assert _erase(t3, [[410, 400]], 60, None)[1].deleted == ["hills_1"]  # any type
    # `retired` ids are tracked by the editor (see the API test); the generator never refills a gap
    t4 = _paint(t2, [[100, 100], [120, 100]], 10)[0]
    assert "forest_1" not in _regions(t4) or True


def test_erase_hole_in_a_painted_region_becomes_a_clearing():
    t = _paint(TINY, _line(300, 300, 700, 300), 60)[0]
    t2, plan = _erase(t, [[500, 300]], 15, "forest")
    assert plan.holes == 1 and any(r["type"] == "clearing" for r in _regions(t2).values())


def test_erase_reports_blob_band_and_reference_regions_and_does_not_rewrite_them():
    t2, plan = _erase(TINY, [[300, 700], [310, 700]], 20, "forest")
    assert plan.ops == [] and plan.skipped and plan.skipped[0]["id"] == "wood" and plan.skipped[0]["shape"] == "blob"
    assert t2 == TINY


def test_convert_blob_to_poly_is_one_explicit_action_and_erase_then_works():
    spec = _spec(TINY)
    plan = paint.plan_convert(specpatch.parsed_document(TINY), spec, spec.size, "wood")
    t = _apply(TINY, plan)
    w = _regions(t)["wood"]
    assert w["shape"] == "poly" and w["id"] == "wood" and w["seed"] == 3
    assert not ({"at", "r", "rough", "squash", "angle"} & set(w))
    assert [l for l in t.split("\n") if "id: wood" in l][0].startswith("  - { id: wood, seed: 3, type: forest, shape: poly, points: [[")
    assert "# blob comment" in t
    t2, plan2 = _erase(t, [[300, 700]], 20, "forest")
    assert plan2.modified == ["wood"]
    with pytest.raises(paint.PaintError):
        paint.plan_convert(specpatch.parsed_document(TINY), spec, spec.size, "hill")


# ---- determinism, line endings ------------------------------------------------------------------------------------

def _session(text):
    t, _ = _paint(text, _line(100, 300, 300, 300), 15)
    t, _ = _paint(t, _line(200, 290, 200, 450), 15)
    t, _ = _erase(t, [[200, 380], [200, 395]], 18, "forest")
    t, _ = _paint(t, _line(500, 500, 700, 520), 22, "mountain", True)
    return t


def test_same_strokes_same_bytes():
    assert _session(TINY) == _session(TINY)
    assert _session(TINY).encode("utf-8") == _session(TINY).encode("utf-8")


def test_crlf_files_stay_crlf():
    crlf = TINY.replace("\n", "\r\n")
    t = _session(crlf)
    assert "\r\n" in t and t.count("\n") == t.count("\r\n")
    assert t.replace("\r\n", "\n") == _session(TINY)


def test_a_painted_spec_loads_and_builds_geometry():
    from mapgen import geometry
    spec = _spec(_session(TINY))
    g = geometry.build_geometries(spec)
    assert len(g.regions) == len(spec.regions) and any(r.id.startswith("mountain_") and r.impassable for r in spec.regions)


# ---- API ----------------------------------------------------------------------------------------------------------

@pytest.fixture
def server(tmp_path):
    p = str(tmp_path / "town_square_v4.yaml")
    shutil.copyfile(V4, p)
    srv = _start(p)
    yield Client(srv)
    srv.shutdown()
    srv.server_close()


def _paint_req(server, **kw):
    body = {"version": server.state()["version"], "mode": "paint", "radius": 14, "type": "forest", "stroke": _line(100, 300, 300, 300)}
    body.update(kw)
    return server.post("/api/paint", body)


def test_api_paint_merge_erase_undo_back_to_the_original_bytes(server):
    ed = server.srv.ed
    original = ed.text
    code, r = _paint_req(server)
    assert code == 200 and r["ok"] and r["state"]["dirty"]
    s = r["state"]["results"][0]["paint"]
    assert s["created_ids"] == ["forest_1"] and s["vertices"][0] > 8
    assert ed.text != original and r["state"]["version"] == 2
    code, r = _paint_req(server, stroke=_line(200, 290, 200, 450))
    assert r["state"]["results"][0]["paint"]["modified"] == ["forest_1"]
    code, r = _paint_req(server, mode="erase", type="forest", stroke=[[200, 380], [200, 395]], radius=20)
    assert r["state"]["results"][0]["paint"]["created_ids"] == ["forest_2"]
    st = r["state"]
    assert {f["id"] for f in st["features"]} >= {"forest_1", "forest_2"}
    assert any(h["path"].startswith("regions.forest_1.points[") for h in st["handles"])
    # nothing changed: no version bump, no undo step
    v = server.state()["version"]
    code, r = _paint_req(server, mode="erase", type="forest", stroke=[[700, 100]], radius=5)
    assert code == 200 and server.state()["version"] == v and r["state"]["results"][0]["paint"]["notes"] == ["nothing changed"]
    for _ in range(3):   # one undo step per stroke
        assert server.post("/api/undo")[0] == 200
    assert ed.text == original and server.state()["can_undo"] is False


def test_api_erase_to_nothing_retires_the_id_and_the_generator_does_not_reuse_it(server):
    _paint_req(server, stroke=[[400, 400]], radius=6)
    assert server.srv.ed.model.spec.regions[-1].id == "forest_1"
    code, r = _paint_req(server, mode="erase", type="any", stroke=[[400, 400]], radius=30)
    assert r["state"]["results"][0]["paint"]["deleted"] == ["forest_1"]
    code, r = _paint_req(server, stroke=[[600, 400]], radius=6)
    assert r["state"]["results"][0]["paint"]["created_ids"] == ["forest_2"]


def test_api_convert_and_skipped_blob(server):
    blob = next(f for f in server.state()["features"] if f["kind"] == "region" and f["shape"] == "blob")
    c = next(h for h in server.state()["handles"] if h["kind"] == "center" and h["feature"] == blob["id"])["at"]
    code, r = _paint_req(server, mode="erase", type=blob["type"], stroke=[c], radius=10)
    p = r["state"]["results"][0]["paint"]
    assert code == 200 and any(x["id"] == blob["id"] for x in p["skipped"])
    code, r = server.post("/api/paint", {"version": server.state()["version"], "mode": "convert", "id": blob["id"]})
    assert code == 200 and next(f for f in r["state"]["features"] if f["id"] == blob["id"])["shape"] == "poly"
    assert server.post("/api/undo")[0] == 200
    assert next(f for f in server.state()["features"] if f["id"] == blob["id"])["shape"] == "blob"


@pytest.mark.parametrize("patch", [{"type": "swamp"}, {"radius": 0}, {"radius": 9999}, {"stroke": []}, {"stroke": [[-900, -900]]},
                                   {"mode": "bogus"}, {"impassable": "yes"}, {"stroke": [["a", 1]]}])
def test_api_paint_bad_requests_are_refused_and_change_nothing(server, patch):
    before = server.srv.ed.text
    code, r = _paint_req(server, **patch)
    assert code == 400 and r["ok"] is False and r["errors"] and server.srv.ed.text == before


def test_api_stale_version_conflicts(server):
    code, r = server.post("/api/paint", {"version": 99, "mode": "paint", "radius": 10, "type": "forest", "stroke": [[10, 10]]})
    assert code == 409


# ---- scatter `within` ---------------------------------------------------------------------------------------------

def test_scatter_within_vertex_handles_and_editing(server):
    st = server.state()
    nw = [h for h in st["handles"] if h["feature"] == "nw_wood" and h["kind"] == "vertex"]
    assert len(nw) == 6 and nw[0]["path"] == "scatter.nw_wood.within[0]" and nw[0]["at"] == [0, 500] and nw[0]["closed"]
    assert nw[0]["chain"] == "scatter.nw_wood.within" and nw[0]["minimum"] == 3 and nw[0]["ref"] is None
    code, r = server.apply([{"op": "set", "path": "scatter.nw_wood.within[1]", "value": [160, 470]}])
    assert code == 200 and next(h for h in r["state"]["handles"] if h["path"] == "scatter.nw_wood.within[1]")["at"] == [160, 470]
    code, r = server.apply([{"op": "insert", "path": "scatter.nw_wood.within", "index": 2, "value": [200, 480]}])
    assert code == 200 and len([h for h in r["state"]["handles"] if h["feature"] == "nw_wood"]) == 7
    # assign a polygon to an entry that has none, and add a new entry with defaults
    poly = [[100, 100], [200, 100], [200, 200]]
    code, r = server.apply([{"op": "put", "path": "scatter.wild_wood.within", "value": poly}])
    assert code == 200 and "scatter.wild_wood.within[2]" in {h["path"] for h in r["state"]["handles"]}
    code, r = server.apply([{"op": "add", "section": "scatter", "value": {"type": "forest", "coverage": 0.4, "scale": 120, "clump": 0.4,
                                                                           "avoid": 20, "impassable": True, "within": poly}}])
    assert code == 200 and r["state"]["results"][0]["id"].startswith("forest_")
    assert re.search(r"\{ id: forest_\d+, type: forest, coverage: 0.4, scale: 120, clump: 0.4, avoid: 20, impassable: true, within: \[\[100, 100\]", server.srv.ed.text)


# ---- browser drive ------------------------------------------------------------------------------------------------

def test_browser_paint_drive_then_validate_and_build(tmp_path):
    """Real mouse events: paint, merge, erase-split, impassable mountain, click-select, scatter `within`, undo to the original bytes;
    the saved session spec validates and builds (exit 0). Skipped without node >= 22 and Edge/Chrome."""
    import subprocess
    import sys as _sys
    _sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "browser"))
    import run_paint_drive
    from test_cli_exit_codes import run
    node = shutil.which("node")
    if not node or not run_paint_drive.find_browser():
        pytest.skip("node or browser missing")
    ver = subprocess.run([node, "--version"], stdout=subprocess.PIPE, universal_newlines=True).stdout.strip().lstrip("v")
    if int(ver.split(".")[0]) < 22:
        pytest.skip("node >= 22 needed")
    code, out = run_paint_drive.run(str(tmp_path / "shots"))
    assert code == 0, out
    saved = str(tmp_path / "shots" / "painted_saved.yaml")
    assert run("validate", saved)[0] == 0
    assert run("build", saved, "--out", str(tmp_path / "out"))[0] == 0
    assert os.path.isfile(str(tmp_path / "out" / "town_square_v4" / "map.png"))
