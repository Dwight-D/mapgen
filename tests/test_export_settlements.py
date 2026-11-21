"""settlements.json (mapgen/export_settlements.py, EXPORT_FORMAT.md section 3.10)."""
import json
import math
import os

import pytest

from mapgen import build as buildmod
from mapgen import export_settlements as es
from mapgen import schema, town

HEADER = """format_version: 1
name: %s
size: [1200, 1200]
seed: 1
teams:
  A: { base: [60, 60] }
  B: { base: [1140, 1140] }
"""


def town_spec(s, name="settletest"):
    return HEADER % name + town.emit_spec(s)


@pytest.fixture(scope="module")
def river_town():
    """A walled radial town with a river, so it has bridges, gates and both road classes."""
    return town.generate("radial", 12, (1200, 1200), {"radius_frac": "0.55", "spokes": "6", "rings": "2", "gates": "6",
                                                       "citadel": "false", "water": "river"}, "twin")


@pytest.fixture(scope="module")
def spec(river_town):
    return schema.load_spec_text(town_spec(river_town))


@pytest.fixture(scope="module")
def doc(spec, river_town):
    return es.settlements_doc(spec, [river_town])


def test_header(doc):
    assert doc["format"] == "mapgen-settlements"
    assert doc["format_version"] == es.FORMAT_VERSION
    assert doc["name"] == "settletest"
    assert len(doc["settlements"]) == 1


def test_ids_match_bundle_features(doc, spec):
    st = doc["settlements"][0]
    towns = {r.id for r in spec.regions if r.type == "town"}
    roads = {ln.id for ln in spec.lines if ln.type == "road"}
    walls = {ln.id for ln in spec.lines if ln.type == "wall"}
    assert {d["id"] for d in st["districts"]} == towns
    assert {r["id"] for r in st["roads"]} <= roads
    road_ids = {r["id"] for r in st["roads"]}
    for g in st["gates"]:
        assert g["road"] in road_ids
        assert abs(math.hypot(*g["normal"]) - 1.0) < 1e-2
    for w in st["walls"]:
        assert w["lines"] and set(w["lines"]) <= walls
    assert all(g["wall"] in {w["id"] for w in st["walls"]} for g in st["gates"] if g["kind"] != "entry")


def test_district_fields(doc, river_town):
    st = doc["settlements"][0]
    by_id = {d.id: d for d in river_town.districts}
    for d in st["districts"]:
        src = by_id[d["id"]]
        assert d["kind"] == src.kind and d["density"] == src.density and d["depth"] == src.depth
        assert d["parent"] == (src.parent or None)
        assert len(d["edges"]) == len(d["polygon"]) == len(src.polygon)
    assert any(d["top_label"] for d in st["districts"])


def test_edges_face_roads(doc):
    st = doc["settlements"][0]
    roads = {r["id"]: r for r in st["roads"]}
    edges = [e for d in st["districts"] for e in d["edges"]]
    facing = [e for e in edges if e is not None]
    assert len(facing) > len(edges) // 3
    assert any(e is None for e in edges)     # wall and river sides face no road
    for e in facing:
        r = roads[e["road"]]
        assert e["class"] == r["class"] and e["width"] == r["width"]
        assert 0.0 <= e["offset"] <= es.FACE_TOL


def test_facing_rejects_a_crossing_road():
    class R:
        def __init__(self, id, cls, pts):
            self.id, self.cls, self.width, self.polyline = id, cls, 5.0, pts
    idx = es._RoadIndex([R("along", "street", [[0, 0.2], [100, 0.2]]), R("across", "artery", [[50, -50], [50, 50]])])
    hit = idx.facing([10, 0], [60, 0])
    assert hit[0].id == "along" and abs(hit[1] - 0.2) < 1e-9
    assert idx.facing([45, 10], [55, 10]) is None


def test_bridges(doc, river_town):
    st = doc["settlements"][0]
    n = sum(1 for r in river_town.roads if r.cls == "bridge")
    assert n > 0 and len(st["bridges"]) == n
    for b in st["bridges"]:
        assert b["id"] == b["road"] + "_x"
        assert abs(math.hypot(*b["span"]) - 1.0) < 1e-3


def test_deterministic(spec, river_town):
    a = json.dumps(es.settlements_doc(spec, [river_town]))
    b = json.dumps(es.settlements_doc(spec, [river_town]))
    assert a == b


def test_find_settlement(tmp_path, river_town):
    spec_path = tmp_path / "t.yaml"
    spec_path.write_text(town_spec(river_town), encoding="utf-8")
    sp = schema.load_spec(str(spec_path))
    build_dir = tmp_path / "out" / sp.name
    build_dir.mkdir(parents=True)
    assert es.find_settlement(sp, str(build_dir)) is None
    (build_dir / "settlement.json").write_text(river_town.to_json(), encoding="utf-8")
    assert es.find_settlement(sp, str(build_dir)) == str(build_dir / "settlement.json")
    side = tmp_path / "t.settlement.json"
    side.write_text(river_town.to_json(), encoding="utf-8")
    assert os.path.normcase(es.find_settlement(sp, str(build_dir))) == os.path.normcase(str(side))


def test_write_skips_without_or_with_a_stale_settlement(tmp_path, spec, river_town):
    exp = tmp_path / "export"
    exp.mkdir()
    warnings = []
    assert es.write_settlements(spec, str(exp), str(tmp_path), warnings) == [] and not warnings
    other = town.generate("grid", 2, (1200, 1200), None, "other")
    (tmp_path / "settlement.json").write_text(other.to_json(), encoding="utf-8")
    assert es.write_settlements(spec, str(exp), str(tmp_path), warnings) == []
    assert len(warnings) == 1 and "does not match" in warnings[0]
    assert not (exp / "settlements.json").exists()


def test_build_writes_settlements_json(tmp_path, river_town):
    spec_path = tmp_path / "settletest.yaml"
    spec_path.write_text(town_spec(river_town), encoding="utf-8")
    (tmp_path / "settletest.settlement.json").write_text(river_town.to_json(), encoding="utf-8")
    out = buildmod.build(str(spec_path), str(tmp_path / "out"), only="export", log=lambda s: None)
    path = os.path.join(out, "export", "settlements.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    assert data["settlements"][0]["id"] == "twin"
    with open(os.path.join(out, "export", "regions.json"), encoding="utf-8") as f:
        regions = {r["id"] for r in json.load(f) if r["type"] == "town"}
    assert {d["id"] for d in data["settlements"][0]["districts"]} == regions


def test_build_without_settlement_writes_no_file(tmp_path):
    spec_path = tmp_path / "plain.yaml"
    spec_path.write_text("format_version: 1\nname: plain\nsize: [400, 400]\nseed: 1\n"
                         "teams:\n  A: { base: [40, 40] }\n  B: { base: [360, 360] }\n"
                         "regions:\n  - { id: wood, type: forest, shape: blob, at: [200, 200], r: 80 }\n", encoding="utf-8")
    out = buildmod.build(str(spec_path), str(tmp_path / "out"), only="export", log=lambda s: None)
    assert not os.path.exists(os.path.join(out, "export", "settlements.json"))
