"""`mapgen export-landcover` (mapgen/export_landcover.py, docs/landcover.md)."""
import json
import os
import subprocess

import pytest
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

from mapgen import export_landcover as lc
from mapgen.__main__ import main

from test_export_settlements import town_spec

SIZE = [1000.0, 800.0]


def _sq(x0, z0, x1, z1):
    return [[x0, z0], [x1, z0], [x1, z1], [x0, z1], [x0, z0]]


def _circle(cx, cz, r, n=48):
    pts = list(Point(cx, cz).buffer(r, quad_segs=n // 4).exterior.coords)
    return [[round(x, 3), round(z, 3)] for x, z in pts]


def _region(rid, rtype, polygons, scatter=False, label=None, height=None):
    return {"id": rid, "type": rtype, "label": label, "height": height, "scatter": scatter, "water_surface": None,
            "polygons": polygons}


REGIONS = [
    _region("meadow", "field", [{"outer": _sq(50, 50, 400, 300), "holes": []}], label="Long Meadow"),
    _region("wood", "forest", [{"outer": _sq(500, 400, 900, 700), "holes": [_sq(600, 500, 700, 600)]},
                               {"outer": _sq(50, 600, 150, 700), "holes": []}], scatter=True),
    _region("ridge", "hills", [{"outer": _circle(250, 500, 60)[::-1], "holes": []}], height=25.0),  # clockwise input
    _region("mere", "lake", [{"outer": _circle(800, 150, 80), "holes": [_circle(800, 150, 20)]}]),
    _region("t_a", "town", [{"outer": _sq(420, 50, 480, 120), "holes": []}]),
    _region("t_b", "town", [{"outer": _sq(480, 50, 540, 120), "holes": []}]),
]
RING = [list(p) for p in _circle(250, 500, 100)]
LINES = [
    {"id": "east_road", "type": "road", "width": 10.0, "points": [[0, 350, 1], [500, 350, 1], [1000, 360, 1]],
     "water_surface": None},
    {"id": "ring", "type": "trail", "width": 5.0, "points": [p + [0.0] for p in RING], "water_surface": None},
    {"id": "brook", "type": "river", "width": 20.0, "points": [[700, 0, 0], [700, 400, 0]], "water_surface": [0, 0, 0]},
    {"id": "scarp", "type": "cliff", "width": 8.0, "points": [[100, 750, 5], [300, 750, 5]], "water_surface": None},
]
POIS = [{"id": "base_A", "type": "base", "team": "A", "tier": None, "label": None, "r": 120.0, "x": 50.0, "z": 50.0,
         "y": 0.0},
        {"id": "relic", "type": "treasure", "team": None, "tier": None, "label": "Old Relic", "r": 10.0, "x": 300.0,
         "z": 200.0, "y": 0.0}]


@pytest.fixture(scope="module")
def doc():
    return lc.landcover_doc("lctest", SIZE, REGIONS, LINES, POIS, margin=2.0)


def _poly(c):
    return Polygon([(p[0], p[2]) for p in c["outer"]])


def _area2(o):
    return sum(o[i][0] * o[(i + 1) % len(o)][2] - o[(i + 1) % len(o)][0] * o[i][2] for i in range(len(o)))


def _kind(doc, lc_, **kw):
    return [c for c in doc["cells"] if c["attrs"]["landCover"] == lc_ and all(c["attrs"].get(k) == v
                                                                              for k, v in kw.items())]


def test_header(doc):
    assert doc["format"] == "nodachi-layout" and doc["version"] == 1
    assert doc["meta"] == {"units": "metres", "frame": "generator-local", "boundaryConvention": "centreline",
                           "generator": doc["meta"]["generator"]}
    assert doc["meta"]["generator"].startswith("mapgen ") and "lctest" in doc["meta"]["generator"]


def test_ids_and_cell_shape(doc):
    for key in ("cells", "splines", "points"):
        assert [x["id"] for x in doc[key]] == list(range(len(doc[key])))
    for c in doc["cells"]:
        o = c["outer"]
        assert len(o) >= 3 and o[0] != o[-1] and all(p[1] == 0.0 for p in o)
        assert all(o[i] != o[(i + 1) % len(o)] for i in range(len(o)))
        assert _area2(o) > 0 and _poly(c).is_valid
        assert "holes" not in c and "edges" not in c


def test_cover_cells(doc):
    cover = [c for c in doc["cells"] if c["attrs"]["landCover"] not in ("hole", "keepout")]
    assert [(c["attrs"]["regionId"], c["attrs"]["part"]) for c in cover] == [("meadow", 0), ("wood", 0), ("wood", 1),
                                                                              ("ridge", 0)]
    m, w0, w1, r = cover
    assert m["attrs"]["landCover"] == "field" and m["attrs"]["label"] == "Long Meadow" and m["attrs"]["scatter"] is False
    assert m["attrs"]["area"] == 350.0 * 250.0 and m["attrs"]["paintOrder"] == 0
    assert w0["attrs"]["landCover"] == "forest" and w0["attrs"]["scatter"] is True
    assert w0["attrs"]["area"] == 400.0 * 300.0 - 100.0 * 100.0          # net of its hole
    assert _poly(w0).area == pytest.approx(400.0 * 300.0)                  # the outer loop only
    assert r["attrs"]["landCover"] == "hills" and r["attrs"]["height"] == 25.0
    assert "height" not in m["attrs"] and "label" not in w0["attrs"]
    assert not any(c["attrs"].get("regionId") in ("mere", "t_a", "t_b") for c in doc["cells"])


def test_hole_cells(doc):
    holes = _kind(doc, "hole")
    assert len(holes) == 1
    h = holes[0]
    assert h["attrs"]["parentRegion"] == "wood" and h["attrs"]["parentCover"] == "forest"
    assert h["attrs"]["part"] == 0 and h["attrs"]["hole"] == 0 and h["attrs"]["area"] == 10000.0
    assert _poly(h).equals(Polygon(_sq(600, 500, 700, 600)))
    ids = [c["id"] for c in doc["cells"]]
    parent = next(c for c in doc["cells"] if c["attrs"].get("regionId") == "wood" and c["attrs"]["part"] == 0)
    assert ids.index(h["id"]) == ids.index(parent["id"]) + 1               # right after its polygon


def _union(cells):
    return unary_union([_poly(c) for c in cells])


def test_road_keepout_width(doc):
    ko = _kind(doc, "keepout", sourceId="east_road")
    assert len(ko) == 1
    a = ko[0]["attrs"]
    assert a["source"] == "road" and a["sourceType"] == "road" and a["width"] == 10.0 and a["margin"] == 2.0
    assert doc["splines"][a["splineId"]]["attrs"]["lineId"] == "east_road"
    g = _poly(ko[0])
    assert g.contains(Point(250, 350 + 6.9)) and not g.contains(Point(250, 350 + 7.2))
    assert g.contains(LineString([(1, 350), (499, 350)]))


def test_ring_keepout_is_hole_free_and_keeps_its_inside_open(doc):
    ko = _kind(doc, "keepout", sourceId="ring")
    assert len(ko) >= 2 and all(c["attrs"]["source"] == "road" and c["attrs"]["sourceType"] == "trail" for c in ko)
    u = _union(ko)
    exact = LineString([tuple(p) for p in RING]).buffer(4.5, quad_segs=lc.LINE_QUAD_SEGS)
    assert u.symmetric_difference(exact).area < 0.01 * exact.area
    assert not u.contains(Point(250, 500))                                  # the hills inside the ring stay open
    assert sum(c["attrs"]["area"] for c in ko) == pytest.approx(u.area, rel=1e-3)


def test_lake_island_stays_open(doc):
    ko = _kind(doc, "keepout", source="water")
    assert ko and all(c["attrs"]["sourceId"] == "mere" and c["attrs"]["sourceType"] == "lake" for c in ko)
    u = _union(ko)
    assert u.contains(Point(800, 200)) and not u.contains(Point(800, 150))


def test_town_poi_river_cliff_keepouts(doc):
    town = _kind(doc, "keepout", source="town")
    assert len(town) == 1 and "sourceId" not in town[0]["attrs"]
    assert _poly(town[0]).contains(Polygon(_sq(421, 51, 539, 119)))
    assert _kind(doc, "keepout", source="river", sourceId="brook")
    cliff = _kind(doc, "keepout", source="cliff", sourceId="scarp")
    assert cliff and "splineId" not in cliff[0]["attrs"]
    relic = _kind(doc, "keepout", source="poi", sourceId="relic")[0]
    assert relic["attrs"]["sourceType"] == "treasure" and relic["attrs"]["radius"] == 10.0
    assert _poly(relic).contains(Point(311.5, 200)) and not _poly(relic).contains(Point(312.5, 200))


def test_keepouts_clipped_to_map(doc):
    for c in _kind(doc, "keepout"):
        for x, _, z in c["outer"]:
            assert 0.0 <= x <= SIZE[0] and 0.0 <= z <= SIZE[1]
    base = _kind(doc, "keepout", sourceId="base_A")[0]
    assert min(p[0] for p in base["outer"]) == 0.0


def test_keepout_order(doc):
    srcs = [c["attrs"]["source"] for c in doc["cells"] if c["attrs"]["landCover"] == "keepout"]
    order = ["road", "river", "cliff", "water", "town", "poi"]
    assert srcs == sorted(srcs, key=order.index)


def test_splines_and_points(doc):
    assert [s["attrs"]["lineId"] for s in doc["splines"]] == ["east_road", "ring", "brook"]
    assert all(s["interpolation"] == "linear" and all(p[1] == 0.0 for p in s["points"]) for s in doc["splines"])
    assert [p["attrs"]["poiId"] for p in doc["points"]] == ["base_A", "relic"]
    assert doc["points"][0]["attrs"]["team"] == "A" and "team" not in doc["points"][1]["attrs"]
    assert doc["points"][1]["attrs"]["label"] == "Old Relic" and doc["points"][1]["position"] == [300.0, 0.0, 200.0]


def test_attribute_types_consistent(doc):
    seen = {}
    for rec in [c["attrs"] for c in doc["cells"]] + [s["attrs"] for s in doc["splines"]] + [p["attrs"] for p in doc["points"]]:
        for k, v in rec.items():
            seen.setdefault(k, set()).add(type(v).__name__)
    assert all(len(t) == 1 for t in seen.values()), seen


def test_margin_and_summary():
    d0 = lc.landcover_doc("lctest", SIZE, REGIONS, LINES, POIS, margin=0.0)
    road = next(c for c in d0["cells"] if c["attrs"].get("sourceId") == "east_road")
    assert _poly(road).contains(Point(250, 354.9)) and not _poly(road).contains(Point(250, 355.3))
    s = lc.summary(d0)
    assert s["field"] == 1 and s["forest"] == 2 and s["hills"] == 1 and s["hole"] == 1 and s["keepout:town"] == 1
    assert list(s) == sorted(s)


def test_deterministic(doc):
    assert lc.dumps(lc.landcover_doc("lctest", SIZE, REGIONS, LINES, POIS, margin=2.0)) == lc.dumps(doc)


def _write_bundle(d):
    d.mkdir(parents=True)
    for name, data in (("meta.json", {"name": "settletest", "size": SIZE}), ("regions.json", REGIONS),
                       ("lines.json", LINES), ("pois.json", POIS)):
        (d / name).write_text(json.dumps(data), encoding="utf-8")


def test_cli(tmp_path, capsys):
    from mapgen import town
    s = town.generate("radial", 3, (1200, 1200), None, "tw")
    spec = tmp_path / "settletest.yaml"
    spec.write_text(town_spec(s), encoding="utf-8")
    bundle = tmp_path / "out" / "settletest" / "export"
    _write_bundle(bundle)
    assert main(["export-landcover", str(spec), "--out", str(tmp_path / "out")]) == 0
    out = bundle / "settletest.landcover.nlayout.json"
    a = out.read_text(encoding="utf-8")
    assert "keepout:road" in capsys.readouterr().out
    out.unlink()
    assert main(["export-landcover", str(tmp_path / "out" / "settletest")]) == 0
    assert out.read_text(encoding="utf-8") == a
    assert json.loads(a) == json.loads(lc.dumps(lc.landcover_doc("settletest", SIZE, REGIONS, LINES, POIS)))
    assert main(["export-landcover", str(spec), "--out", str(tmp_path / "none")]) == 1
    assert "run mapgen build first" in capsys.readouterr().out
    assert main(["export-landcover", str(tmp_path / "out" / "settletest"), "--margin", "-1"]) == 1


LAYOUT_CHECK = os.environ.get("NODACHI_LAYOUT_CHECK", "")   # path to layout-check.dll (nodachi tools/layout-check build)


@pytest.mark.skipif(not os.path.isfile(LAYOUT_CHECK), reason="NODACHI_LAYOUT_CHECK not set to a layout-check.dll")
def test_nodachi_layout_check(tmp_path, doc):
    p = tmp_path / "t.landcover.nlayout.json"
    p.write_text(lc.dumps(doc), encoding="utf-8")
    r = subprocess.run(["dotnet", LAYOUT_CHECK, "--no-schema", str(p)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       universal_newlines=True)
    assert r.returncode == 0, r.stdout
    assert "0 error(s)" in r.stdout
