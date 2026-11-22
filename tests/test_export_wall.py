"""`mapgen export-wall` (mapgen/export_wall.py, docs/wall.md)."""
import json
import math
import os

import pytest
from shapely.geometry import LineString, Point

from mapgen import export_wall as ew
from mapgen.__main__ import main


def _ring():
    return [[100.0, 100.0], [200.0, 100.0], [200.0, 200.0], [100.0, 200.0], [100.0, 100.0]]


def _sdoc(open_shore=None, gates=None):
    wall = {"id": "t_wall_outer", "kind": "outer", "thickness": 8.0, "polyline": _ring(), "lines": ["t_wall_outer"],
            "open_shore": open_shore or [], "towers": []}
    roads = [{"id": "t_artery_0", "class": "artery", "width": 14.0, "closed": False,
              "polyline": [[150.0, 100.0], [150.0, 150.0]], "nodes": []}]
    if gates is None:
        gates = [{"id": "t_gate_a0", "kind": "gate", "x": 150.0, "z": 100.0, "normal": [0.0, -1.0],
                  "wall": "t_wall_outer", "road": "t_artery_0"},
                 {"id": "t_gate_e1", "kind": "entry", "x": 10.0, "z": 10.0, "normal": [1.0, 0.0],
                  "wall": None, "road": None}]
    st = {"id": "t", "generator": "radial", "seed": 3, "roads": roads, "walls": [wall], "gates": gates}
    return {"format": "mapgen-settlements", "format_version": 1, "name": "t", "settlements": [st]}


def test_short_gate_id():
    assert ew.short_gate_id("cinder_gate_a4") == "a4"
    assert ew.short_gate_id("x_gate_y_gate_p2") == "p2"
    assert ew.short_gate_id("weird") == "weird"


def test_closed_ring_is_one_closed_spline():
    doc = ew.wall_doc(_sdoc())
    assert doc["format"] == "nodachi-layout" and doc["version"] == 1 and doc["cells"] == []
    assert "export-wall" in doc["meta"]["generator"]
    (sp,) = doc["splines"]
    assert sp["closed"] is True and sp["interpolation"] == "linear"
    assert len(sp["points"]) == 4 and sp["points"][0] != sp["points"][-1]
    assert all(p[1] == 0.0 for p in sp["points"])
    assert sp["attrs"] == {"kind": "wall", "wallId": "t_wall_outer", "wallKind": "outer", "wallWidth": 8.0,
                           "runIndex": 0, "settlement": "t"}


def test_gates_on_walls_only():
    doc = ew.wall_doc(_sdoc())
    (g,) = doc["points"]                         # the wall-less entry gate is skipped
    assert g["id"] == 0 and g["position"] == [150.0, 0.0, 100.0]
    assert g["rotationY"] == 180.0               # normal (0, -1): out of town is south
    assert g["attrs"] == {"gateId": "a0", "gateRef": "t_gate_a0", "kind": "gate", "wallId": "t_wall_outer",
                          "settlement": "t", "roadId": "t_artery_0", "roadWidth": 14.0, "width": 14.0}


def test_open_shore_gives_open_runs():
    doc = ew.wall_doc(_sdoc(open_shore=[[1, 2]]))  # edge 1 (east side) open to water
    (sp,) = doc["splines"]                        # the run wraps past the ring start and is joined
    assert "closed" not in sp
    assert [p[0] for p in sp["points"]] == [200.0, 100.0, 100.0, 200.0]
    doc = ew.wall_doc(_sdoc(open_shore=[[0, 1], [2, 3]]))
    assert [len(s["points"]) for s in doc["splines"]] == [2, 2]
    assert [s["attrs"]["runIndex"] for s in doc["splines"]] == [0, 1]
    assert not any(s.get("closed") for s in doc["splines"])


def test_deterministic():
    assert ew.dumps(ew.wall_doc(_sdoc())) == ew.dumps(ew.wall_doc(_sdoc()))


def test_cli_build_dir(tmp_path, capsys):
    d = tmp_path / "t" / "export"
    d.mkdir(parents=True)
    (d / "settlements.json").write_text(json.dumps(_sdoc()), encoding="utf-8")
    assert main(["export-wall", str(tmp_path / "t")]) == 0
    out = d / "t.wall.nlayout.json"
    assert json.loads(out.read_text(encoding="utf-8")) == ew.wall_doc(_sdoc())
    assert "1 wall splines (1 closed" in capsys.readouterr().out
    assert main(["export-wall", str(tmp_path / "missing")]) == 1


CINDER = os.path.join(os.path.dirname(__file__), "..", "out", "cindermarket", "export", "settlements.json")


@pytest.mark.skipif(not os.path.isfile(CINDER), reason="cindermarket not built")
def test_cindermarket_wall():
    with open(CINDER, "r", encoding="utf-8") as f:
        sdoc = json.load(f)
    doc = ew.wall_doc(sdoc)
    (sp,) = doc["splines"]
    assert sp["closed"] and len(sp["points"]) == 208 and sp["attrs"]["wallWidth"] == 8.0
    ring = LineString([(p[0], p[2]) for p in sp["points"] + sp["points"][:1]])
    assert 1580 < ring.length < 1600
    assert sorted(p["attrs"]["gateId"] for p in doc["points"]) == ["a0", "a1", "a2", "a3", "a4", "a5"]
    for p in doc["points"]:
        assert ring.distance(Point(p["position"][0], p["position"][2])) < 0.01
        assert p["attrs"]["width"] == 14.0
        r = math.radians(p["rotationY"])
        assert -180.0 <= p["rotationY"] <= 180.0 and abs(math.hypot(math.sin(r), math.cos(r)) - 1) < 1e-9
