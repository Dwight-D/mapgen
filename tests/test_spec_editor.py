"""Spec editor (GDU-51): HTTP guards, features API, id-addressed reference-aware edits through the span-patch engine, undo/redo,
refusals, atomic save, CRLF, preview endpoints, CLI, and a real-browser drive (skipped without node >= 22 and Edge/Chrome)."""
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import threading

import pytest

from mapgen import schema, specpatch
from mapgen.spec_editor import SpecEditor, SpecEditorServer
from test_cli_exit_codes import REPO_ROOT, run

V4 = os.path.join(REPO_ROOT, "maps", "iterations", "town_square", "v4_natural", "town_square_v4.yaml")


class Client:
    def __init__(self, srv):
        self.srv = srv
        self.port = srv.server_address[1]

    def req(self, method, path, body=None, headers=None, host=None, token=True, raw_body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        h = {"Host": host or "127.0.0.1:%d" % self.port}
        if token:
            h["X-Token"] = self.srv.token if token is True else token
        h.update(headers or {})
        data = raw_body if raw_body is not None else (None if body is None else json.dumps(body).encode("utf-8"))
        if data is not None:
            h["Content-Type"] = "application/json"
        c.request(method, path, data, h)
        r = c.getresponse()
        out = r.read()
        hdrs = dict(r.getheaders())
        c.close()
        return r.status, out, hdrs

    def post(self, path, body=None, **kw):
        st, raw, _ = self.req("POST", path, {} if body is None else body, **kw)
        return st, json.loads(raw.decode("utf-8"))

    def state(self):
        return self.post("/api/state")[1]["state"]

    def apply(self, ops, version=None):
        return self.post("/api/apply", {"version": self.state()["version"] if version is None else version, "ops": ops})


def _start(path):
    srv = SpecEditorServer(path, preview_px=256)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def spec_path(tmp_path):
    p = str(tmp_path / "town_square_v4.yaml")
    shutil.copyfile(V4, p)
    return p


@pytest.fixture
def server(spec_path):
    srv = _start(spec_path)
    yield Client(srv)
    srv.shutdown()
    srv.server_close()


def _read(path):
    with open(path, "rb") as f:
        return f.read().decode("utf-8")


def _handle(st, path):
    return next(h for h in st["handles"] if h["path"] == path)


def _changed_span(a, b):
    """(old, new) of the one contiguous region where the texts differ (common prefix and suffix stripped)."""
    i = 0
    while i < min(len(a), len(b)) and a[i] == b[i]:
        i += 1
    j = 0
    while j < min(len(a), len(b)) - i and a[len(a) - 1 - j] == b[len(b) - 1 - j]:
        j += 1
    return a[i:len(a) - j], b[i:len(b) - j]


def _diff_lines(a, b):
    la, lb = a.split("\n"), b.split("\n")
    assert len(la) == len(lb)
    return [i for i, (x, y) in enumerate(zip(la, lb)) if x != y]


# ---- page and guards ----------------------------------------------------------------------------------------------

def test_page_has_no_external_urls_embeds_token_and_csp_allows_blob_images(server):
    st, raw, hdrs = server.req("GET", "/", token=False)
    html = raw.decode("utf-8")
    assert st == 200 and server.srv.token in html
    stripped = html.replace("http://www.w3.org/2000/svg", "")
    assert not re.search(r"https?://|//cdn|@import|<link ", stripped)
    for el in ("b-undo", "b-redo", "b-save", "dirty", "path", "errors", "panel", "map"):
        assert 'id="%s"' % el in html
    csp = hdrs["Content-Security-Policy"]
    assert "img-src blob:" in csp and "default-src 'none'" in csp and "connect-src 'self'" in csp


def test_binds_loopback_only(server):
    assert server.srv.server_address[0] == "127.0.0.1"


def test_wrong_or_missing_token_rejected(server):
    assert server.post("/api/state", token="nope")[0] == 403
    assert server.post("/api/state", token=False)[0] == 403
    assert server.post("/api/save", token="nope")[0] == 403
    assert server.post("/api/apply", {"version": 1, "ops": []}, token=False)[0] == 403


def test_wrong_host_rejected(server):
    assert server.req("GET", "/", host="evil.example:%d" % server.port, token=False)[0] == 403
    assert server.post("/api/state", host="evil.example")[0] == 403
    assert server.post("/api/state", host="127.0.0.1:1")[0] == 403
    assert server.post("/api/state", host="localhost:%d" % server.port)[0] == 200


def test_foreign_origin_rejected(server):
    assert server.post("/api/state", headers={"Origin": "http://evil.example"})[0] == 403
    assert server.post("/api/state", headers={"Origin": "http://127.0.0.1:%d" % server.port})[0] == 200


def test_bad_requests(server):
    assert server.req("POST", "/api/nope", {})[0] == 404
    assert server.req("GET", "/api/state", token=False)[0] == 404
    assert server.req("POST", "/api/state", raw_body=b"{not json")[0] == 400
    assert server.req("POST", "/api/state", raw_body=b"[1]")[0] == 400
    assert server.req("POST", "/api/state", headers={"Content-Length": "9999999"}, raw_body=b"{}")[0] in (400, 413)


# ---- features API -------------------------------------------------------------------------------------------------

def test_state_lists_handles_that_match_the_resolved_spec(server, spec_path):
    st = server.state()
    spec = schema.load_spec(spec_path)
    assert st["size"] == list(spec.size) and st["version"] == 1 and st["dirty"] is False
    kinds = {}
    for h in st["handles"]:
        kinds.setdefault(h["kind"], []).append(h)
    assert len(kinds["poi"]) == len(spec.pois) and len(kinds["base"]) == len(spec.teams) and len(kinds["point"]) == len(spec.points)
    assert sum(len(ln.path) for ln in spec.lines) == len(kinds["waypoint"])
    for ln in spec.lines:
        for j, pt in enumerate(ln.path):
            assert _handle(st, "lines.%s.path[%d]" % (ln.id, j))["at"] == [pt[0], pt[1]]
    wp = _handle(st, "lines.road_ao_o.path[0]")
    assert wp["ref"] == "ao" and wp["at"] == _handle(st, "points.ao")["at"]
    assert _handle(st, "lines.road_ao_o.path[1]")["ref"] is None
    assert {r["path"] for r in st["referrers"]["ao"]} == {"lines.road_a_ao.path[2]", "lines.road_ao_o.path[0]"}
    assert len(st["referrers"]["o"]) == 5
    assert st["path"] == os.path.abspath(spec_path)


# ---- edits --------------------------------------------------------------------------------------------------------

def test_move_poi_changes_only_that_span(server, spec_path):
    before = server.srv.ed.text
    code, r = server.apply([{"op": "set", "path": "pois.fa.at", "value": [401, 270]}])
    assert code == 200 and r["ok"] and r["state"]["dirty"] and r["state"]["version"] == 2
    after = server.srv.ed.text
    old, new = _changed_span(before, after)
    assert len(_diff_lines(before, after)) == 1 and old != new
    assert re.search(r"id: fa\b.*\bat: \[401, 270\]", after) and "[395, 265]" in before
    assert _handle(r["state"], "pois.fa.at")["at"] == [401, 270]
    assert _read(spec_path) == before   # nothing written without Save


def test_move_referenced_point_moves_the_definition_and_all_referrers(server):
    before = server.srv.ed.text
    # drag the waypoint that references `ao`: the definition in `points` moves
    code, r = server.apply([{"op": "set", "path": "lines.road_ao_o.path[0]", "value": [260, 412]}])
    assert code == 200
    after = server.srv.ed.text
    assert _diff_lines(before, after) == [i for i, l in enumerate(before.split("\n")) if l.strip().startswith("ao:")]
    assert "ao: [260, 412]" in after
    st = r["state"]
    for p in ("points.ao", "lines.road_ao_o.path[0]", "lines.road_a_ao.path[2]"):
        assert _handle(st, p)["at"] == [260, 412]
    assert _handle(st, "lines.road_ao_o.path[0]")["ref"] == "ao"
    # a POI used as a waypoint: moving the POI handle moves its referrers too
    code, r = server.apply([{"op": "set", "path": "pois.o.at", "value": [405, 405]}])
    st = r["state"]
    assert all(_handle(st, p)["at"] == [405, 405] for p in ("lines.road_ao_o.path[2]", "lines.road_bo_o.path[2]", "lines.road_fa_o.path[2]"))


def test_detach_turns_a_reference_waypoint_into_a_literal(server):
    before = server.srv.ed.text
    code, r = server.apply([{"op": "set", "path": "lines.road_ao_o.path[0]", "value": [250, 405], "detach": True}])
    assert code == 200
    st = r["state"]
    wp = _handle(st, "lines.road_ao_o.path[0]")
    assert wp["ref"] is None and wp["at"] == [250, 405]
    assert _handle(st, "points.ao")["at"] == [255, 410]
    assert _handle(st, "lines.road_a_ao.path[2]")["ref"] == "ao"
    assert "path: [[250, 405], [330, 398], o]" in server.srv.ed.text and len(_diff_lines(before, server.srv.ed.text)) == 1


def test_insert_and_delete_waypoint_round_trip_bytes(server):
    before = server.srv.ed.text
    code, r = server.apply([{"op": "insert", "path": "lines.river_main.path", "index": 1, "value": [545, 650]}])
    assert code == 200
    assert _handle(r["state"], "lines.river_main.path[1]")["at"] == [545, 650]
    assert _handle(r["state"], "lines.river_main.path[1]")["ref"] is None
    assert len([h for h in r["state"]["handles"] if h.get("line") == "river_main"]) == 10
    assert "path: [[515, 676], [545, 650], [575, 625]" in server.srv.ed.text and len(_diff_lines(before, server.srv.ed.text)) == 1
    code, r = server.apply([{"op": "delete", "path": "lines.river_main.path[1]"}])
    assert code == 200 and server.srv.ed.text == before
    assert r["state"]["dirty"] is False   # back to the disk text


def test_undo_redo_restore_buffer_byte_exactly(server):
    ed = server.srv.ed
    texts = [ed.text]
    for ops in ([{"op": "set", "path": "pois.g.at", "value": [381, 101]}],
                [{"op": "set", "path": "lines.road_ao_o.path[1]", "value": [331, 399]}],
                [{"op": "insert", "path": "lines.river_main.path", "index": None, "value": [600, 1]}],
                [{"op": "set", "path": "teams.A.base", "value": [112, 421]}]):
        assert server.apply(ops)[0] == 200
        texts.append(ed.text)
    assert len(set(texts)) == 5
    for want in reversed(texts[:-1]):
        code, r = server.post("/api/undo")
        assert code == 200 and ed.text == want
    assert r["state"]["can_undo"] is False and r["state"]["dirty"] is False
    assert server.post("/api/undo")[0] == 409
    for want in texts[1:]:
        code, r = server.post("/api/redo")
        assert code == 200 and ed.text == want
    assert r["state"]["can_redo"] is False
    # a new edit after an undo clears the redo stack
    server.post("/api/undo")
    server.apply([{"op": "set", "path": "pois.g.at", "value": [383, 100]}])
    assert server.state()["can_redo"] is False


def test_invalid_edit_refused_with_errors_and_buffer_unchanged(server):
    ed = server.srv.ed
    before, version = ed.text, ed.version
    code, r = server.apply([{"op": "set", "path": "pois.fa.at", "value": [-50, 265]}])
    assert code == 422 and r["ok"] is False and r["errors"] and any("pois" in e for e in r["errors"])
    assert ed.text == before and ed.version == version and r["state"]["dirty"] is False
    # unknown id, bad op, deleting the only waypoints, unmatched stale version
    assert server.apply([{"op": "set", "path": "pois.nope.at", "value": [1, 1]}])[0] == 422
    assert server.apply([{"op": "bogus", "path": "pois.fa.at"}])[0] == 422
    code, r = server.apply([{"op": "set", "path": "pois.fa.at", "value": [1, 2]}], version=version + 5)
    assert code == 409 and ed.text == before
    assert server.apply([])[0] == 400
    # a batch is atomic: the valid first op is not kept when the second is refused
    code, r = server.apply([{"op": "set", "path": "pois.fa.at", "value": [396, 266]}, {"op": "set", "path": "pois.fb.at", "value": [9999, 1]}])
    assert code == 422 and ed.text == before


# ---- save ---------------------------------------------------------------------------------------------------------

def test_save_writes_in_place_atomically_only_the_edited_span(server, spec_path):
    original = _read(spec_path)
    server.apply([{"op": "set", "path": "pois.fa.at", "value": [401, 270]}])
    assert _read(spec_path) == original
    code, r = server.post("/api/save")
    assert code == 200 and r["ok"] and r["bytes"] > 0 and r["state"]["dirty"] is False
    saved = _read(spec_path)
    assert saved == server.srv.ed.text and saved != original
    old, new = _changed_span(original, saved)
    assert old == "395, 265" and new == "401, 270"
    assert [f for f in os.listdir(os.path.dirname(spec_path)) if f.endswith(".tmp")] == []
    schema.load_spec(spec_path)
    # a clean buffer saves nothing
    mtime = os.stat(spec_path).st_mtime_ns
    code, r = server.post("/api/save")
    assert code == 200 and r["bytes"] == 0 and os.stat(spec_path).st_mtime_ns == mtime
    # the undo stack survives a save, and the undone buffer is dirty again
    code, r = server.post("/api/undo")
    assert r["state"]["dirty"] is True and _read(spec_path) == saved


def test_save_refused_when_buffer_invalid_and_file_untouched(server, spec_path):
    original = _read(spec_path)
    ed = server.srv.ed
    ed.text = ed.text.replace("[395, 265]", "[-395, 265]")   # force an invalid buffer (the API itself never allows one)
    code, r = server.post("/api/save")
    assert code == 422 and r["errors"] and _read(spec_path) == original


def test_save_refused_when_file_changed_on_disk(server, spec_path):
    server.apply([{"op": "set", "path": "pois.fa.at", "value": [401, 270]}])
    with open(spec_path, "ab") as f:
        f.write(b"\n# edited elsewhere\n")
    disk = open(spec_path, "rb").read()
    code, r = server.post("/api/save")
    assert code == 409 and "changed on disk" in r["errors"][0]
    assert open(spec_path, "rb").read() == disk and r["state"]["dirty"] is True


def test_crlf_file_keeps_its_line_endings(tmp_path):
    p = str(tmp_path / "crlf.yaml")
    with open(V4, "rb") as f:
        data = f.read().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    with open(p, "wb") as f:
        f.write(data)
    srv = _start(p)
    try:
        c = Client(srv)
        assert c.apply([{"op": "set", "path": "pois.fa.at", "value": [401, 270]}])[0] == 200
        assert c.apply([{"op": "insert", "path": "lines.river_main.path", "index": 2, "value": [590, 590]}])[0] == 200
        assert c.post("/api/save")[1]["ok"]
        out = open(p, "rb").read()
        assert out.count(b"\r\n") == out.count(b"\n") == data.count(b"\r\n")
        assert b"401, 270" in out and b"[590, 590]" in out
    finally:
        srv.shutdown()
        srv.server_close()


# ---- preview ------------------------------------------------------------------------------------------------------

def test_geometry_endpoint_returns_resolved_shapes_and_stale_versions_are_dropped(server):
    v = server.state()["version"]
    code, r = server.post("/api/geometry", {"version": v})
    assert code == 200 and r["ok"]
    ids = {ln["id"] for ln in r["geometry"]["lines"]}
    assert {"river_main", "road_ao_o", "cliff_nw"} <= ids
    assert all(len(ln["pts"]) >= 2 for ln in r["geometry"]["lines"]) and r["geometry"]["regions"]
    code, r = server.post("/api/geometry", {"version": v + 3})
    assert r == {"ok": False, "stale": True, "errors": []}


def test_image_endpoint_serves_png_for_the_current_buffer_and_writes_no_files(server, spec_path):
    folder = os.path.dirname(spec_path)
    names = set(os.listdir(folder))
    out_dir = os.path.join(REPO_ROOT, "out", "town_square_v4")
    out_before = sorted(os.listdir(out_dir)) if os.path.isdir(out_dir) else None
    v = server.state()["version"]
    st, raw, hdrs = server.req("POST", "/api/image", {"version": v})
    assert st == 200 and raw[:8] == b"\x89PNG\r\n\x1a\n" and hdrs["X-Version"] == str(v) and int(hdrs["X-Render-Ms"]) >= 0
    st, raw2, _ = server.req("POST", "/api/image", {"version": v})
    assert raw2 == raw   # cached per buffer text
    server.apply([{"op": "set", "path": "pois.fa.at", "value": [401, 270]}])
    st, raw3, _ = server.req("POST", "/api/image", {"version": v})   # old version: stale, no render
    assert st == 200 and json.loads(raw3.decode("utf-8"))["stale"] is True
    assert set(os.listdir(folder)) == names
    assert (sorted(os.listdir(out_dir)) if os.path.isdir(out_dir) else None) == out_before


def test_preview_geometry_equals_build_geometries_of_the_saved_file(server, spec_path):
    from mapgen import geometry
    server.apply([{"op": "set", "path": "lines.road_ao_o.path[1]", "value": [331, 399]}])
    v = server.state()["version"]
    r = server.post("/api/geometry", {"version": v})[1]
    server.post("/api/save")
    spec = schema.load_spec(spec_path)
    geoms = geometry.build_geometries(spec)
    for ln, lg in zip(spec.lines, geoms.lines):
        got = next(x for x in r["geometry"]["lines"] if x["id"] == ln.id)
        assert abs(got["pts"][0][0] - lg.centreline.coords[0][0]) < 0.06 and abs(got["pts"][-1][1] - lg.centreline.coords[-1][1]) < 0.06


# ---- engine additions ---------------------------------------------------------------------------------------------

def test_op_from_json_validation():
    assert isinstance(specpatch.op_from_json({"op": "set", "path": "pois.a.at", "value": [1, 2]}), specpatch.Set)
    ins = specpatch.op_from_json({"op": "insert", "path": "lines.l.path", "index": 2, "value": [1, 2]})
    assert isinstance(ins, specpatch.Insert) and ins.path == "lines.l.path[+2]" and ins.index == 2
    assert specpatch.op_from_json({"op": "insert", "path": "lines.l.path", "index": None, "value": [1, 2]}).path == "lines.l.path[+]"
    assert isinstance(specpatch.op_from_json({"op": "delete", "path": "lines.l.path[1]"}), specpatch.Delete)
    for bad in ({}, {"op": "set", "path": "pois.a.at"}, {"op": "delete", "path": "a[+]"}, {"op": "put", "path": "a[1]", "value": 1}, {"op": "add", "section": "x", "value": {}}, {"op": "rename", "from": "a"}, {"op": "x", "path": "a"},
                {"op": "insert", "path": "a.b", "index": -1, "value": 1}, {"op": "set", "path": "a..b", "value": 1},
                {"op": "set", "path": "a", "value": float("inf")}):
        with pytest.raises(specpatch.PatchError):
            specpatch.op_from_json(bad)


def test_parse_cache_does_not_leak_between_texts():
    a = open(V4, encoding="utf-8").read()
    b = a.replace("[395, 265]", "[396, 265]")
    assert specpatch.apply_ops(a, [specpatch.Set("pois.fa.at", [396, 265])]) == b
    assert specpatch.apply_ops(b, [specpatch.Set("pois.fa.at", [395, 265])]) == a
    assert specpatch.parsed_document(a) != specpatch.parsed_document(b)


# ---- CLI ----------------------------------------------------------------------------------------------------------

def test_cli_errors(tmp_path):
    code, o, e = run("spec-edit", str(tmp_path / "missing.yaml"), "--no-open")
    assert code == 1 and "file not found" in o
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: x\n", encoding="utf-8")
    code, o, e = run("spec-edit", str(bad), "--no-open")
    assert code == 1 and "error(s)" in o


def test_cli_serves_and_binds_loopback(spec_path):
    import urllib.request
    p = subprocess.Popen([sys.executable, "-u", "-m", "mapgen", "spec-edit", spec_path, "--no-open"],
                         cwd=REPO_ROOT, stdout=subprocess.PIPE, universal_newlines=True)
    try:
        line = p.stdout.readline()
        m = re.search(r"at (http://127\.0\.0\.1:\d+/)", line)
        assert m, line
        html = urllib.request.urlopen(m.group(1), timeout=20).read().decode("utf-8")
        assert "town_square_v4.yaml - spec editor" in html
    finally:
        p.kill()
        p.wait()


# ---- real browser -------------------------------------------------------------------------------------------------

def test_browser_drive_if_available(tmp_path):
    """Real mouse and keyboard input in headless Edge/Chrome over DevTools; skipped when node >= 22 or a browser is missing."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "browser"))
    import run_spec_drive
    node = shutil.which("node")
    if not node or not run_spec_drive.find_browser():
        pytest.skip("node or browser missing")
    ver = subprocess.run([node, "--version"], stdout=subprocess.PIPE, universal_newlines=True).stdout.strip().lstrip("v")
    if int(ver.split(".")[0]) < 22:
        pytest.skip("node >= 22 needed")
    code, out = run_spec_drive.run(str(tmp_path / "shots"))
    assert code == 0, out
    saved = str(tmp_path / "shots" / "session_saved.yaml")   # written by the drive after its add/delete/rename session
    spec = schema.load_spec(saved)
    assert any(r.id.startswith("forest_") for r in spec.regions) and any(p.id == "oak" for p in spec.pois)
    assert run("validate", saved)[0] == 0


# ---- slice 3 (GDU-52): shape tools, structure ops, schema-driven properties ---------------------------------------------

BLOB = {"type": "forest", "shape": "blob", "at": [650, 650], "r": 40, "rough": 0.3}
RIVER = {"type": "river", "path": [[560, 560], [600, 500], [660, 450], [700, 380]]}
O_REFERRERS = {"regions.square.at", "lines.road_ao_o.path[2]", "lines.road_bo_o.path[2]", "lines.road_fa_o.path[2]",
               "lines.road_fb_o.path[2]"}


def _add(server, section, value):
    code, j = server.apply([{"op": "add", "section": section, "value": value}])
    assert code == 200 and j["ok"], j
    return j["state"]["results"][0]["id"], j["state"]


def test_add_returns_the_generated_id_and_new_handles(server, spec_path):
    before = _read(spec_path)
    fid, st = _add(server, "regions", BLOB)
    assert re.fullmatch(r"forest_\d+", fid)
    assert {h["kind"] for h in st["handles"] if h["feature"] == fid} == {"center", "radius"}
    r = _handle(st, "regions.%s.r" % fid)
    assert r["center"] == [650.0, 650.0] and r["at"] == [690.0, 650.0]
    rid, st = _add(server, "lines", RIVER)
    assert re.fullmatch(r"river_\d+", rid)
    assert [h["index"] for h in st["handles"] if h["feature"] == rid and h["kind"] == "waypoint"] == [0, 1, 2, 3]
    w = _handle(st, "lines.%s.width" % rid)
    assert w["kind"] == "width" and w["chain"] == "lines.%s.path" % rid and w["value"] == 30
    assert {f["id"] for f in st["features"]} >= {fid, rid, "o", "base_A", "ao"}
    assert _read(spec_path) == before   # nothing is written without Save


def test_add_poly_band_crossing_poi_scatter_and_point(server):
    pid, st = _add(server, "regions", {"type": "hills", "shape": "poly", "points": [[100, 600], [200, 640], [150, 540]]})
    assert [h["kind"] for h in st["handles"] if h["feature"] == pid] == ["vertex"] * 3
    assert all(h["closed"] and h["minimum"] == 3 and h["chain"] == "regions.%s.points" % pid for h in st["handles"] if h["feature"] == pid)
    bid, st = _add(server, "regions", {"type": "field", "shape": "band", "path": [[300, 700], [420, 740]], "width": 30})
    assert {h["kind"] for h in st["handles"] if h["feature"] == bid} == {"vertex", "width"}
    cid, st = _add(server, "crossings", {"type": "ford", "at": [100, 100]})
    assert {h["kind"] for h in st["handles"] if h["feature"] == cid} == {"crossing", "radius"}
    cb, st = _add(server, "crossings", {"type": "bridge", "path": [[100, 200], [140, 200]], "width": 12})
    assert {h["kind"] for h in st["handles"] if h["feature"] == cb} == {"vertex", "width"}
    iid, st = _add(server, "pois", {"type": "supply", "at": [700, 700]})
    assert iid == "poi_1" and _handle(st, "pois.poi_1.at")["kind"] == "poi"
    sid, st = _add(server, "scatter", {"type": "forest", "coverage": 0.2})
    assert next(f for f in st["features"] if f["id"] == sid)["kind"] == "scatter"
    ptid, st = _add(server, "points", [333, 222])
    assert ptid == "p1" and _handle(st, "points.p1")["at"] == [333.0, 222.0]


def test_deleted_ids_are_not_reissued_in_the_session(server):
    a, _ = _add(server, "regions", BLOB)
    code, j = server.apply([{"op": "delete", "path": "regions.%s" % a}])
    assert code == 200 and j["state"]["results"][0]["deleted"] == a
    b, _ = _add(server, "regions", BLOB)
    assert b != a


def test_delete_referenced_feature_is_refused_with_referrers_then_detached(server, spec_path):
    before = _read(spec_path)
    v = server.state()["version"]
    code, j = server.apply([{"op": "delete", "path": "pois.o"}])
    assert code == 422 and not j["ok"] and j["referenced"] == "o"
    assert {r["path"] for r in j["referrers"]} == O_REFERRERS
    assert j["state"]["version"] == v and not j["state"]["dirty"]
    code, j = server.apply([{"op": "delete", "path": "pois.o", "detach_refs": True}])
    assert code == 200 and set(j["state"]["results"][0]["detached"]) == O_REFERRERS
    st = j["state"]
    assert "o" not in {f["id"] for f in st["features"]} and not any(h["ref"] == "o" for h in st["handles"])
    assert _handle(st, "regions.square.at")["at"] == [400.0, 400.0]
    assert server.post("/api/undo")[0] == 200
    assert _read(spec_path) == before and not server.state()["dirty"]


def test_delete_a_line_that_another_joins_needs_detach(server):
    rid, _ = _add(server, "lines", {"type": "river", "path": [[300, 700], [420, 740]], "join": "river_main", "join_end": "end"})
    code, j = server.apply([{"op": "delete", "path": "lines.river_main"}])
    assert code == 422 and j["referenced"] == "river_main" and any(r["path"].endswith(".join") for r in j["referrers"])
    code, j = server.apply([{"op": "delete", "path": "lines.river_main", "detach_refs": True}])
    assert code == 200 and "river_main" not in {f["id"] for f in j["state"]["features"]}
    assert set(j["state"]["results"][0]["detached"]) == {"lines.%s.join" % rid}
    assert "join" not in next(f for f in j["state"]["features"] if f["id"] == rid)["props"]


def test_rename_rewrites_references_and_keeps_the_shape(server):
    st0 = server.state()
    code, j = server.apply([{"op": "rename", "from": "g", "to": "oak"}])
    assert code == 200
    st = j["state"]
    res = st["results"][0]
    assert res["renamed"] == ["g", "oak"] and len(res["references"]) == len(st0["referrers"]["g"])
    assert len(st["referrers"]["oak"]) == len(st0["referrers"]["g"]) and "g" not in st["referrers"]
    assert "oak" in {h["ref"] for h in st["handles"] if h["kind"] == "waypoint"}
    code, j = server.apply([{"op": "rename", "from": "rock_ne", "to": "north_rock"}])
    assert code == 200 and j["state"]["results"][0]["seed_pinned"] is None   # the committed regions carry a seed
    fid, _ = _add(server, "regions", BLOB)
    code, j = server.apply([{"op": "rename", "from": fid, "to": "wood_x"}])
    assert code == 200 and j["state"]["results"][0]["seed_pinned"] == schema.feature_seed(fid)
    # refusals: id in use, invalid id, team base, unknown
    for body in ({"from": "wood_x", "to": "oak"}, {"from": "wood_x", "to": "Bad Id"}, {"from": "base_A", "to": "start"}, {"from": "nope", "to": "x"}):
        code, j = server.apply([dict(op="rename", **body)])
        assert code == 422 and not j["ok"]


def test_errors_map_to_features_and_refused_edits_leave_the_buffer(server):
    v = server.state()["version"]
    code, j = server.apply([{"op": "set", "path": "pois.g.at", "value": [-50, 100]}])
    assert code == 422 and j["error_refs"][0] == {"feature": "g", "path": "pois.g.at", "key": "at"}
    fid, _ = _add(server, "regions", BLOB)
    code, j = server.apply([{"op": "put", "path": "regions.%s.r" % fid, "value": -5}])
    assert code == 422 and j["error_refs"][0]["feature"] == fid and j["error_refs"][0]["key"] == "r"
    code, j = server.apply([{"op": "set", "path": "lines.river_main.path[2]", "value": [9000, 9000]}])
    assert code == 422 and j["error_refs"][0]["feature"] == "river_main" and j["error_refs"][0]["path"] == "lines.river_main.path[2]"
    assert server.state()["version"] == v + 1   # only the add counted


def test_map_error_cases():
    from mapgen import spec_model
    raw = specpatch.parsed_document(_read(V4))
    assert spec_model.map_error(raw, "pois[3].at: point is outside the map") == {"feature": "g", "path": "pois.g.at", "key": "at"}
    assert spec_model.map_error(raw, "regions[0].r: must be > 0")["feature"] == "rock_ne"
    assert spec_model.map_error(raw, "points.ao: expected a point")["feature"] == "ao"
    assert spec_model.map_error(raw, "teams.A.base: outside")["feature"] == "base_A"
    assert spec_model.map_error(raw, "<yaml>: broken")["feature"] is None
    assert spec_model.map_error(raw, "regions[999].r: x")["feature"] is None


def test_schema_tables_are_read_from_schema_py_and_match_the_loader_messages():
    from mapgen import spec_model
    sj = spec_model.schema_json(schema.load_types())
    for kind in ("line", "crossing", "poi", "scatter"):
        assert sj["keys"][kind]["allowed"] == schema.allowed_keys(kind)[0]
    for shape in ("blob", "poly", "band"):
        assert sj["keys"]["region_" + shape]["allowed"] == schema.allowed_keys("region", shape)[0]
    assert "rough" in sj["keys"]["region_blob"]["allowed"] and "rough" in sj["keys"]["region_poly"]["allowed"]
    assert "width" in sj["keys"]["region_band"]["allowed"] and "width" not in sj["keys"]["region_blob"]["allowed"]
    assert {"forest", "lake", "mountain"} <= set(sj["types"]["region"]) and set(sj["types"]["line"]) == {"river", "road", "trail", "cliff"}
    assert sj["types"]["region"]["mountain"]["cost"] == "inf"
    assert all(k in sj["props"] for k in ("rough", "meander", "impassable", "label", "join_end", "coverage"))
    # the loader's own unknown-key message lists exactly the allowed keys the editor uses
    for kind, sect, item in (("line", "lines", "{ id: l, type: road, path: [[1, 1], [2, 2]], bogus: 1 }"),
                             ("poi", "pois", "{ id: q, type: capture, at: [5, 5], bogus: 1 }"),
                             ("crossing", "crossings", "{ id: c, type: bridge, at: [5, 5], bogus: 1 }"),
                             ("scatter", "scatter", "{ id: s, type: forest, coverage: 0.2, bogus: 1 }")):
        text = "format_version: 1\nname: t\nsize: [100, 100]\nteams:\n  A: { base: [1, 1] }\n  B: { base: [9, 9] }\n%s:\n  - %s\n" % (sect, item)
        with pytest.raises(schema.SpecError) as e:
            schema.load_spec_text(text.encode("utf-8"))
        assert "allowed: %s" % ", ".join(sj["keys"][kind]["allowed"]) in str(e.value)


def test_page_carries_the_schema_and_type_names(server):
    st, body, _ = server.req("GET", "/")
    html = body.decode("utf-8")
    boot = json.loads(re.search(r'<script id="boot" type="application/json">(.*?)</script>', html, re.S).group(1))
    assert "mountain" in boot["schema"]["types"]["region"] and boot["schema"]["keys"]["line"]["required"] == ["id", "type", "path"]
    for tool in ("poi", "blob", "band", "poly", "line", "crossing"):
        assert "'%s'" % tool in html


def test_mixed_session_undoes_to_the_original_bytes_and_saves_a_loadable_spec(server, spec_path):
    original = _read(spec_path)
    fid, _ = _add(server, "regions", BLOB)
    rid, _ = _add(server, "lines", RIVER)
    assert server.apply([{"op": "put", "path": "regions.%s.warp" % fid, "value": 30},
                         {"op": "put", "path": "regions.%s.label" % fid, "value": "North wood"}])[0] == 200
    assert server.apply([{"op": "rename", "from": "g", "to": "oak"}])[0] == 200
    assert server.apply([{"op": "delete", "path": "lines.%s" % rid}])[0] == 200
    assert server.apply([{"op": "delete", "path": "regions.%s.warp" % fid}])[0] == 200
    code, j = server.post("/api/save")
    assert code == 200
    spec = schema.load_spec(spec_path)
    assert any(r.id == fid for r in spec.regions) and any(p.id == "oak" for p in spec.pois) and not any(l.id == rid for l in spec.lines)
    assert run("validate", spec_path)[0] == 0
    n = 0
    while server.state()["can_undo"]:
        assert server.post("/api/undo")[0] == 200
        n += 1
    assert n == 6
    code, j = server.post("/api/save")
    assert code == 200 and _read(spec_path) == original


def test_structure_edits_keep_crlf(tmp_path):
    p = str(tmp_path / "crlf.yaml")
    with open(p, "wb") as f:
        f.write(_read(V4).replace("\n", "\r\n").encode("utf-8"))
    srv = _start(p)
    try:
        c = Client(srv)
        fid, _ = _add(c, "regions", BLOB)
        assert c.apply([{"op": "put", "path": "regions.%s.rough" % fid, "value": 0.5}])[0] == 200
        assert c.apply([{"op": "rename", "from": "ao", "to": "west_gate"}])[0] == 200
        assert c.post("/api/save")[0] == 200
        data = open(p, "rb").read().decode("utf-8")
        assert "\n" not in data.replace("\r\n", "") and "id: %s" % fid in data
    finally:
        srv.shutdown()
        srv.server_close()
