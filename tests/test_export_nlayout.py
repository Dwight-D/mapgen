"""`mapgen export-nlayout` (mapgen/export_nlayout.py, docs/nlayout.md)."""
import json
import os
import subprocess

import pytest
from shapely.geometry import LineString, Point

from mapgen import export_nlayout as nl
from mapgen import export_settlements as es
from mapgen import schema, town
from mapgen.__main__ import main

from test_export_settlements import town_spec


@pytest.fixture(scope="module")
def river_town():
    return town.generate("radial", 12, (1200, 1200), {"radius_frac": "0.55", "spokes": "6", "rings": "2", "gates": "6",
                                                       "citadel": "false", "water": "river"}, "twin")


@pytest.fixture(scope="module")
def sdoc(river_town):
    return es.settlements_doc(schema.load_spec_text(town_spec(river_town)), [river_town])


@pytest.fixture(scope="module")
def doc(sdoc):
    return nl.nlayout_doc(sdoc)


def test_header(doc):
    assert doc["format"] == "nodachi-layout" and doc["version"] == 1
    assert doc["meta"]["boundaryConvention"] == "centreline"
    assert doc["meta"]["units"] == "metres" and doc["meta"]["frame"] == "generator-local"
    assert doc["meta"]["generator"].startswith("mapgen ")


def test_counts_and_ids(doc, sdoc):
    st = sdoc["settlements"][0]
    assert len(doc["cells"]) == len(st["districts"])
    assert len(doc["splines"]) == len(st["roads"])
    assert len(doc["points"]) == len(st["gates"]) + len(st["landmarks"])
    for key in ("cells", "splines", "points"):
        assert [x["id"] for x in doc[key]] == list(range(len(doc[key])))


def _area2(o):
    return sum(o[i][0] * o[(i + 1) % len(o)][2] - o[(i + 1) % len(o)][0] * o[i][2] for i in range(len(o)))


def test_cells(doc):
    for c in doc["cells"]:
        o = c["outer"]
        assert len(o) >= 3 and o[0] != o[-1] and all(p[1] == 0.0 for p in o)
        assert _area2(o) > 0                          # counter-clockwise seen from above
        assert len(c["edges"]) == len(o)
        assert "holes" not in c
        assert isinstance(c["attrs"]["districtType"], str) and isinstance(c["attrs"]["density"], float)


def test_edges_lie_on_their_spline(doc):
    lines = {s["id"]: LineString([(p[0], p[2]) for p in s["points"] + (s["points"][:1] if s.get("closed") else [])])
             for s in doc["splines"]}
    on = 0
    for c in doc["cells"]:
        o = c["outer"]
        for i, e in enumerate(c["edges"]):
            if e["splineRef"] < 0:
                continue
            on += 1
            a, b = o[i], o[(i + 1) % len(o)]
            ls = lines[e["splineRef"]]
            for p in (a, b, [(a[0] + b[0]) / 2, 0, (a[2] + b[2]) / 2]):
                assert ls.distance(Point(p[0], p[2])) <= nl.ON_SPLINE + 1e-9
            assert isinstance(e["attrs"]["frontagePriority"], int)
    assert on > 0


def test_neighbours_reciprocal(doc):
    cells = {c["id"]: c for c in doc["cells"]}
    linked = 0
    for c in doc["cells"]:
        for e in c["edges"]:
            n = e["neighbour"]
            if n < 0:
                continue
            linked += 1
            assert n != c["id"] and any(f["neighbour"] == c["id"] for f in cells[n]["edges"])
    assert linked > 0


def test_attribute_types_consistent(doc):
    seen = {}
    for rec in ([c["attrs"] for c in doc["cells"]] + [e.get("attrs", {}) for c in doc["cells"] for e in c["edges"]]
                + [s["attrs"] for s in doc["splines"]] + [p["attrs"] for p in doc["points"]]):
        for k, v in rec.items():
            seen.setdefault(k, set()).add(type(v).__name__)
    assert all(len(t) == 1 for t in seen.values()), seen


def test_splines_use_nodachi_classes(doc):
    assert {s["attrs"]["roadClass"] for s in doc["splines"]} <= {"main", "street"}
    assert all(s["interpolation"] == "linear" and isinstance(s["attrs"]["roadWidth"], float) for s in doc["splines"])


def test_plaza_has_no_frontage(doc, sdoc):
    st = sdoc["settlements"][0]
    open_d = {l["district"] for l in st["landmarks"] if l["kind"] == "square"}
    cells = [c for c in doc["cells"] if c["attrs"]["districtId"] in open_d]
    assert cells
    for c in cells:
        assert all(e["attrs"]["frontage"] is False for e in c["edges"])


def _plaza_cells(doc):
    return [c for c in doc["cells"] if c["attrs"]["districtType"] == "plaza"]


def test_square_district_is_plaza(doc, sdoc):
    st = sdoc["settlements"][0]
    sq = [l for l in st["landmarks"] if l["kind"] == "square"]
    assert sq
    cells = _plaza_cells(doc)
    assert {c["attrs"]["districtId"] for c in cells} == {l["district"] for l in sq}
    for c in cells:
        lm = next(l for l in sq if l["district"] == c["attrs"]["districtId"])
        assert c["attrs"]["landmarkId"] == lm["id"] and c["attrs"]["plazaRadius"] == float(lm["r"])
        assert c["attrs"]["mapgenKind"] == "core"
    pts = {p["attrs"]["landmarkId"]: p for p in doc["points"] if "landmarkId" in p["attrs"]}
    assert all(pts[l["id"]]["attrs"]["landmarkType"] == "plaza" for l in sq)


@pytest.fixture(scope="module")
def grid_doc():
    s = town.generate("grid", 2, (1200, 1200), None, "gridt")
    sd = es.settlements_doc(schema.load_spec_text(town_spec(s, "gridtest")), [s])
    return sd, nl.nlayout_doc(sd)


def test_grid_market_square_is_plaza(grid_doc):
    sd, doc = grid_doc
    st = sd["settlements"][0]
    assert st["generator"] == "grid"
    mk = [l for l in st["landmarks"] if l["kind"] == "market"]
    assert len(mk) == 1
    cells = _plaza_cells(doc)
    assert [c["attrs"]["districtId"] for c in cells] == [mk[0]["district"]]
    c = cells[0]
    assert c["attrs"]["landmarkId"] == mk[0]["id"] and c["attrs"]["plazaRadius"] == float(mk[0]["r"])
    assert all(e["attrs"]["frontage"] is False for e in c["edges"])
    pt = next(p for p in doc["points"] if p["attrs"].get("landmarkId") == mk[0]["id"])
    assert pt["attrs"]["landmarkType"] == "plaza" and pt["attrs"]["mapgenKind"] == "market"
    others = [x for x in doc["cells"] if x is not c]
    assert not any(e.get("attrs", {}).get("frontage") is False for x in others for e in x["edges"])


def test_market_landmark_of_other_generator_is_not_plaza(sdoc):
    d = json.loads(json.dumps(sdoc))
    st = d["settlements"][0]
    target = next(x for x in st["districts"] if x["kind"] == "residential")
    st["landmarks"].append({"id": "twin_hall", "kind": "market", "x": 1.0, "z": 1.0, "r": 10.0,
                            "district": target["id"], "label": None})
    doc = nl.nlayout_doc(d)
    c = next(x for x in doc["cells"] if x["attrs"]["districtId"] == target["id"])
    assert c["attrs"]["districtType"] == "residential"
    assert doc["points"][-1]["attrs"]["landmarkType"] == "market"


def test_plaza_kind_district(sdoc):
    d = json.loads(json.dumps(sdoc))
    target = next(x for x in d["settlements"][0]["districts"] if x["kind"] == "residential")
    target["kind"] = "plaza"
    doc = nl.nlayout_doc(d)
    c = next(x for x in doc["cells"] if x["attrs"]["districtId"] == target["id"])
    assert c["attrs"]["districtType"] == "plaza" and c["attrs"]["mapgenKind"] == "plaza"
    assert "plazaRadius" not in c["attrs"] and "landmarkId" not in c["attrs"]
    assert all(e["attrs"]["frontage"] is False for e in c["edges"])


def test_clockwise_input_is_reversed(sdoc):
    d = json.loads(json.dumps(sdoc))
    dist = d["settlements"][0]["districts"][0]
    dist["polygon"] = dist["polygon"][::-1]
    dist["edges"] = dist["edges"][::-1][1:] + dist["edges"][::-1][:1]
    a = nl.nlayout_doc(sdoc)["cells"][0]
    b = nl.nlayout_doc(d)["cells"][0]
    assert _area2(b["outer"]) > 0
    k = b["outer"].index(a["outer"][0])
    assert b["outer"][k:] + b["outer"][:k] == a["outer"]
    assert [e["splineRef"] for e in b["edges"][k:] + b["edges"][:k]] == [e["splineRef"] for e in a["edges"]]


def test_deterministic(sdoc):
    assert nl.dumps(nl.nlayout_doc(sdoc)) == nl.dumps(nl.nlayout_doc(sdoc))


def test_cli_spec_and_bundle(tmp_path, river_town, sdoc, capsys):
    spec_path = tmp_path / "settletest.yaml"
    spec_path.write_text(town_spec(river_town), encoding="utf-8")
    sett = tmp_path / "twin.json"
    sett.write_text(river_town.to_json(), encoding="utf-8")
    out_root = tmp_path / "out"
    assert main(["export-nlayout", str(spec_path), "--out", str(out_root), "--settlement", str(sett)]) == 0
    a = (out_root / "settletest" / "export" / "settletest.nlayout.json").read_text(encoding="utf-8")
    bundle = tmp_path / "bundle" / "export"
    bundle.mkdir(parents=True)
    es.dump(str(bundle / "settlements.json"), sdoc)
    assert main(["export-nlayout", str(tmp_path / "bundle")]) == 0
    b = (bundle / "settletest.nlayout.json").read_text(encoding="utf-8")
    assert a == b
    assert main(["export-nlayout", str(spec_path), "--out", str(tmp_path / "none")]) == 1
    assert "no settlement file" in capsys.readouterr().out


LAYOUT_CHECK = os.environ.get("NODACHI_LAYOUT_CHECK", "")   # path to layout-check.dll (nodachi tools/layout-check build)


@pytest.mark.skipif(not os.path.isfile(LAYOUT_CHECK), reason="NODACHI_LAYOUT_CHECK not set to a layout-check.dll")
def test_nodachi_layout_check(tmp_path, doc):
    p = tmp_path / "t.nlayout.json"
    p.write_text(nl.dumps(doc), encoding="utf-8")
    r = subprocess.run(["dotnet", LAYOUT_CHECK, "--no-schema", str(p)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       universal_newlines=True)
    assert r.returncode == 0, r.stdout
    assert "0 error(s)" in r.stdout
