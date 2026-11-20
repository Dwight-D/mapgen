"""Spec editor slice 5 (GDU-54): read-only flowgraph overlay and live check badge. Server API on town_square v4 with a copy of
flowgraphs/town_square.flow.yaml: overlay data, check findings after edits, rename warning, the graph file is never written,
an invalid graph never blocks the editor, no graph = no layer; the real-browser drive lives in test_spec_editor.py's pattern."""
import os
import shutil
import subprocess
import sys

import pytest

from mapgen import flowgraph_check, schema
from mapgen.flowgraph import load_flowgraph
from mapgen.spec_editor import SpecEditor, SpecEditorServer
from mapgen.spec_editor_flowgraph import find_flowgraph
from test_cli_exit_codes import REPO_ROOT, run
from test_spec_editor import V4, Client, _start

GRAPH = os.path.join(REPO_ROOT, "flowgraphs", "town_square.flow.yaml")


@pytest.fixture
def files(tmp_path):
    spec = str(tmp_path / "town_square_v4.yaml")
    graph = str(tmp_path / "town_square.flow.yaml")
    shutil.copyfile(V4, spec)
    shutil.copyfile(GRAPH, graph)
    return spec, graph


def _serve(spec, graph):
    srv = SpecEditorServer(spec, preview_px=256, flowgraph_path=graph)
    import threading
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def fg(files):
    srv = _serve(*files)
    yield Client(srv)
    srv.shutdown()
    srv.server_close()


def _overlay(c):
    v = c.state()["version"]
    st, j = c.post("/api/flowgraph", {"version": v})
    assert st == 200 and j["ok"], j
    return j["flowgraph"]


def _kinds(o):
    return sorted((f["kind"], f["text"]) for f in o["findings"])


def _bytes_mtime(path):
    return open(path, "rb").read(), os.stat(path).st_mtime_ns


def test_overlay_data_on_v4(fg):
    o = _overlay(fg)
    assert o["name"] == "town_square" and o["unbound"] == [] and o["errors"] == []
    nodes = {n["id"]: n for n in o["nodes"]}
    assert len(nodes) == 9 and all(n["bound"] for n in nodes.values())
    assert nodes["A"]["at"] == [110, 420] and nodes["A"]["r"] == 120   # base: 120 m
    assert nodes["B"]["at"] == [705, 370] and nodes["B"]["r"] == 120
    assert nodes["O"]["at"] == [400, 400] and nodes["O"]["r"] == 40    # POI r
    assert nodes["AO"]["at"] == [255, 410] and nodes["AO"]["kind"] == "choke"   # `points` entry
    assert nodes["Fa"]["r"] == 30 and nodes["G"]["name"] == "Great Oak Hill" and nodes["A"]["kind"] == "spawn"
    assert len(o["edges"]) == 12 and all(e["status"] == "ok" and e["drawn"] for e in o["edges"])
    assert o["findings"] == [] and o["problems"] == 0 and o["problems_strict"] == 0


def test_overlay_matches_cli_check(fg, files):
    spec, graph = files
    res = flowgraph_check.check_files(graph, spec)
    assert res.lines(True)[-1].startswith("OK")
    assert _overlay(fg)["problems_strict"] == len(res.lines(True)) - 1


def test_state_carries_flowgraph_info_and_no_graph_means_none(fg, files, tmp_path):
    st = fg.state()
    assert st["flowgraph"]["path"] == os.path.abspath(files[1]) and st["flowgraph"]["errors"] == []
    srv = _start(files[0])
    try:
        c = Client(srv)
        assert c.state()["flowgraph"] is None
        code, j = c.post("/api/flowgraph", {"version": 1})
        assert j["ok"] is False
        code, j = c.post("/api/flowgraph_terrain", {"version": 1})
        assert j["ok"] is False
        html = c.req("GET", "/", token=False)[1].decode("utf-8")
        assert "if (BOOT.state.flowgraph) LAYERS.push" in html and '"flowgraph": null' in html
    finally:
        srv.shutdown()
        srv.server_close()


def test_missing_edge_after_deleting_a_road(fg):
    code, j = fg.apply([{"op": "delete", "path": "lines.road_bo_o"}])
    assert code == 200, j
    o = _overlay(fg)
    assert o["problems"] == 1 and o["problems_strict"] == 1
    f = o["findings"][0]
    assert f["kind"] == "missing_edge" and f["edge"] == ["BO", "O"] and f["text"].startswith("missing edge BO - O")
    st = {(e["a"], e["b"]): e["status"] for e in o["edges"]}
    assert st[("BO", "O")] == "missing" and st[("A", "AO")] == "ok"
    # undo restores OK
    fg.post("/api/undo")
    assert _overlay(fg)["problems"] == 0


def test_dragging_a_waypoint_away_breaks_an_edge(fg):
    # road_bo_o is [bo, [460, 402], o]; moving the literal middle point does not break it, moving `o` end out of disc does
    code, j = fg.apply([{"op": "set", "path": "lines.road_bo_o.path[2]", "value": [400, 600], "detach": True}])
    assert code == 200, j
    o = _overlay(fg)
    assert any(f["kind"] == "missing_edge" and f["edge"] == ["BO", "O"] for f in o["findings"])


def test_extra_edge_when_adding_a_road_between_unrelated_nodes(fg):
    code, j = fg.apply([{"op": "add", "section": "lines", "value": {"id": "road_x", "type": "road", "path": [[395, 265], [405, 545]], "width": 8}}])
    assert code == 200, j
    o = _overlay(fg)
    extra = [f for f in o["findings"] if f["kind"] == "extra_edge"]
    assert [f["edge"] for f in extra] == [["Fa", "Fb"]] and extra[0]["strict_only"] is True
    assert o["problems"] == 0 and o["problems_strict"] == 1   # extras count only in strict mode
    assert any(e["status"] == "extra" and {e["a"], e["b"]} == {"Fa", "Fb"} for e in o["edges"])


def test_unbound_node_when_feature_deleted_or_renamed_and_rename_warning(fg, files):
    graph_before = _bytes_mtime(files[1])
    code, j = fg.apply([{"op": "rename", "from": "g", "to": "oak"}])
    assert code == 200, j
    warns = j["state"]["flowgraph_warnings"]
    assert len(warns) == 1 and "node G" in warns[0] and "'g'" in warns[0] and "'oak'" in warns[0] and "flowgraph edit" in warns[0]
    o = _overlay(fg)
    assert o["unbound"] == ["G"]
    g = next(n for n in o["nodes"] if n["id"] == "G")
    assert g["bound"] is False and g["at"] is None and g["bind"] == "g"
    assert any(f["kind"] == "missing_node" and f["nodes"] == ["G"] for f in o["findings"])
    assert any(f["kind"] == "extra_node" and f["feature"] == "oak" for f in o["findings"])
    assert any(e["a"] == "G" and e["drawn"] is False for e in o["edges"])
    assert _bytes_mtime(files[1]) == graph_before   # the graph was not rewritten
    # a rename of something no node binds warns about nothing
    code, j = fg.apply([{"op": "rename", "from": "rock_ne", "to": "north_rock"}])
    assert code == 200 and j["state"]["flowgraph_warnings"] == []


def test_rename_warning_uses_explicit_bind(files):
    spec, graph = files
    text = open(graph, encoding="utf-8").read().replace("Fa: { kind: area,", "Fa: { kind: area, bind: g,")
    text = text.replace("  G:  { kind: zone,", "  G:  { kind: zone, bind: oak_x,")
    with open(graph, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    ed = SpecEditor(spec, flowgraph_path=graph)
    st = ed.apply(1, [{"op": "rename", "from": "g", "to": "g2"}])
    assert len(st["flowgraph_warnings"]) == 1 and "node Fa" in st["flowgraph_warnings"][0]


def test_graph_file_never_written_through_a_whole_session(fg, files):
    spec, graph = files
    before = _bytes_mtime(graph)
    fg.apply([{"op": "delete", "path": "lines.road_bo_o"}])
    fg.apply([{"op": "rename", "from": "g", "to": "oak"}])
    _overlay(fg)
    fg.post("/api/flowgraph_terrain", {"version": fg.state()["version"]})
    st, j = fg.post("/api/save")
    assert st == 200 and j["bytes"] > 0
    fg.post("/api/undo")
    fg.post("/api/save")
    assert _bytes_mtime(graph) == before
    assert not [n for n in os.listdir(os.path.dirname(graph)) if n.endswith(".tmp")]


def test_invalid_flowgraph_is_reported_and_the_editor_still_works(files):
    spec, graph = files
    with open(graph, "w", encoding="utf-8", newline="\n") as f:
        f.write("format_version: 1\nname: Bad Name\nnodes: {}\n")
    srv = _serve(spec, graph)
    try:
        c = Client(srv)
        st = c.state()
        assert st["flowgraph"]["errors"] and st["flowgraph"]["name"] is None
        code, j = c.post("/api/flowgraph", {"version": st["version"]})
        assert j["ok"] and j["flowgraph"]["errors"] and "nodes" not in j["flowgraph"]
        code, j = c.apply([{"op": "set", "path": "pois.g.at", "value": [380, 110], "detach": False}])
        assert code == 200 and j["state"]["flowgraph_warnings"] == []
        code, j = c.post("/api/flowgraph_terrain", {"version": c.state()["version"]})
        assert j["ok"] is False
        # fixing the file is picked up without a restart
        shutil.copyfile(GRAPH, graph)
        assert c.state()["flowgraph"]["errors"] == []
        assert c.post("/api/flowgraph", {"version": c.state()["version"]})[1]["flowgraph"]["name"] == "town_square"
    finally:
        srv.shutdown()
        srv.server_close()


def test_missing_flowgraph_file_is_an_error_not_a_crash(files):
    spec, graph = files
    os.remove(graph)
    ed = SpecEditor(spec, flowgraph_path=graph)
    st = ed.state()
    assert st["flowgraph"]["errors"] and "cannot read" in st["flowgraph"]["errors"][0]
    assert ed.flowgraph(1)["flowgraph"]["errors"]


def test_stale_version_is_refused(fg):
    code, j = fg.post("/api/flowgraph", {"version": 999})
    assert j == {"ok": False, "stale": True, "errors": []}


def test_terrain_findings_endpoint(fg):
    v = fg.state()["version"]
    code, j = fg.post("/api/flowgraph_terrain", {"version": v})
    assert code == 200 and j["ok"] and isinstance(j["findings"], list) and j["ms"] >= 0
    for f in j["findings"]:
        assert f["kind"] in ("terrain_missing", "terrain_extra", "note")
    code, j2 = fg.post("/api/flowgraph_terrain", {"version": v})   # cached per text
    assert j2["findings"] == j["findings"]
    code, j3 = fg.post("/api/flowgraph_terrain", {"version": v + 50})
    assert j3["stale"] is True


def test_find_flowgraph_auto_detect(tmp_path):
    root = str(tmp_path)
    os.makedirs(os.path.join(root, "flowgraphs"))
    d = os.path.join(root, "flowgraphs")
    assert find_flowgraph("maps/x/town_square_v4.yaml", root) is None
    open(os.path.join(d, "town_square.flow.yaml"), "w").close()
    assert find_flowgraph("maps/x/town_square_v4.yaml", root) == os.path.join(d, "town_square.flow.yaml")
    assert find_flowgraph("maps/x/town_square.yaml", root) == os.path.join(d, "town_square.flow.yaml")
    open(os.path.join(d, "town_square_v4.flow.yaml"), "w").close()
    assert find_flowgraph("maps/x/town_square_v4.yaml", root) is None   # both exist: ambiguous
    assert find_flowgraph("maps/x/other.yaml", root) is None
    assert find_flowgraph(V4) == GRAPH   # the repo's own


def test_cli_flag_is_documented():
    code, out, err = run("spec-edit", "--help")
    assert code == 0 and "--flowgraph" in out and "--no-flowgraph" in out


def test_derive_edges_text_path_equals_file_path(files):
    spec, graph = files
    g = load_flowgraph(graph)
    s = schema.load_spec(spec)
    with open(spec, "rb") as f:
        paths = flowgraph_check.raw_line_paths_text(f.read().decode("utf-8"))
    assert flowgraph_check.derive_edges(g, s) == flowgraph_check.derive_edges(g, s, paths)


def test_flowgraph_browser_drive_if_available(tmp_path):
    """Real mouse input in headless Edge/Chrome: layer toggle, Alt-drag of a road end breaks an edge, badge, finding click,
    rename warning, terrain button; skipped when node >= 22 or a browser is missing."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "browser"))
    import run_flowgraph_drive
    node = shutil.which("node")
    if not node or not run_flowgraph_drive.find_browser():
        pytest.skip("node or browser missing")
    ver = subprocess.run([node, "--version"], stdout=subprocess.PIPE, universal_newlines=True).stdout.strip().lstrip("v")
    if int(ver.split(".")[0]) < 22:
        pytest.skip("node >= 22 needed")
    code, out = run_flowgraph_drive.run(str(tmp_path / "shots"))
    assert code == 0, out
    assert os.path.isfile(str(tmp_path / "shots" / "flowgraph_broken_edge.png"))
