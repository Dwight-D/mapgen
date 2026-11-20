"""Spec editor slice 7 (GDU-56): coarse analysis preview (shape, timing, cache, latest wins, no files written), the build job
(lifecycle, ordered progress, report warnings, cancel, one at a time, byte-identical to the CLI), guarded image endpoints, a spec
that does not load cannot be built, and a real-browser drive (skipped without node >= 22 and Edge/Chrome)."""
import hashlib
import os
import shutil
import sys
import threading
import time

import pytest

from mapgen import analysis, schema
from mapgen.spec_editor import SpecEditorServer
from mapgen.spec_editor_analysis import BuildManager, BuildRefused, STAGES
from test_spec_editor import Client, V4
from test_cli_exit_codes import run
from test_validation import BASE

MOVE_FA = {"op": "set", "path": "pois.fa.at", "value": [400, 270], "detach": False}


def _start(path, scratch):
    srv = SpecEditorServer(path, preview_px=256, scratch_root=scratch)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _make(tmp_path, text=None, name="town_square_v4.yaml"):
    p = str(tmp_path / name)
    if text is None:
        shutil.copyfile(V4, p)
    else:
        with open(p, "wb") as f:
            f.write(text.encode("utf-8"))
    scratch = str(tmp_path / "scratch")
    srv = _start(p, scratch)
    return Client(srv), p, scratch


@pytest.fixture
def v4(tmp_path):
    c, p, scratch = _make(tmp_path)
    yield c, p, scratch
    c.srv.shutdown()
    c.srv.server_close()


@pytest.fixture
def tiny(tmp_path):
    c, p, scratch = _make(tmp_path, BASE, "tiny.yaml")
    yield c, p, scratch
    c.srv.shutdown()
    c.srv.server_close()


def _analysis(c, version=None, timeout=30):
    v = c.state()["version"] if version is None else version
    t0 = time.time()
    while True:
        st, j = c.post("/api/analysis", {"version": v})
        assert st == 200
        if j.get("stale") or j["status"] != "computing":
            return j
        assert time.time() - t0 < timeout, "analysis did not finish"
        time.sleep(0.02)


def _wait_build(c, timeout=240):
    t0 = time.time()
    while True:
        b = c.post("/api/build_status")[1]["build"]
        if b["job"] is None or b["job"]["status"] != "running":
            return b
        assert time.time() - t0 < timeout, "build did not finish"
        time.sleep(0.2)


def _hashes(root):
    out = {}
    for d, _, names in os.walk(root):
        for n in names:
            with open(os.path.join(d, n), "rb") as f:
                out[os.path.relpath(os.path.join(d, n), root).replace(os.sep, "/")] = hashlib.sha256(f.read()).hexdigest()
    return out


def _tree(root):
    out = []
    for d, ds, names in os.walk(root):
        out += [os.path.join(d, n) for n in ds + names]
    return sorted(out)


# ---- coarse analysis ------------------------------------------------------------------------------------------------

def test_analysis_result_shape_and_budget_v4(v4):
    c, _, _ = v4
    t0 = time.time()
    j = _analysis(c)
    wall = time.time() - t0
    assert j["ok"] and j["status"] == "ready"
    r = j["result"]
    assert r["label"] == "coarse preview" and r["cell"] >= 20
    assert len(r["owner"]) == r["rows"] and all(len(row) == r["cols"] for row in r["owner"])
    assert set("".join(r["owner"])) <= set("01cu")
    assert [t["id"] for t in r["teams"]] == ["A", "B"]
    assert r["objectives"] and set(r["objectives"][0]) == {"id", "type", "owner", "times", "spread", "flag"}
    assert r["verdict"]["neutral"] >= 1 and "ok" in r["verdict"] and r["tolerance"] == 0.2
    kinds = {x["kind"] for x in r["routes"]}
    assert kinds == {"base", "objective"} and "A-B" in {x["key"] for x in r["routes"] if x["kind"] == "base"}
    assert all(len(x["pts"]) >= 2 for x in r["routes"])
    assert 0 <= r["shares"]["unreachable"] <= 1
    assert r["ms"] < 1500 and wall < 6.0, (r["ms"], wall)


def test_analysis_small_spec(tiny):
    c, _, _ = tiny
    r = _analysis(c)["result"]
    assert r["objectives"] == [] and r["verdict"]["neutral"] == 0 and r["verdict"]["ok"] is True
    assert [x["key"] for x in r["routes"]] == ["A-B"]


def test_coarse_uses_real_analysis_at_other_cell(v4):
    """The preview is analysis.analyze run at a coarser cell: same code, objective ids and team set as the full-grid analysis."""
    c, p, _ = v4
    r = _analysis(c)["result"]
    from mapgen import build
    spec, geoms, hm, an = build.prepare(p)
    assert [o["id"] for o in an.objectives] == [o["id"] for o in r["objectives"]]
    assert an.grid.rows > r["rows"]   # the real run is finer
    import inspect
    assert "cell" in inspect.signature(analysis.analyze).parameters


def test_cache_hit_and_latest_wins(v4):
    c, _, _ = v4
    ed = c.srv.ed
    v1 = c.state()["version"]
    first = _analysis(c)
    assert first["status"] == "ready"
    t0 = time.time()
    st, again = c.post("/api/analysis", {"version": v1})
    assert again["status"] == "ready" and time.time() - t0 < 0.5      # served from the cache in one call
    assert again["result"] == first["result"]
    # an edit makes the old version stale; the new text computes on its own
    st, j = c.apply([MOVE_FA])
    v2 = j["state"]["version"]
    assert c.post("/api/analysis", {"version": v1})[1]["stale"] is True
    second = _analysis(c, v2)
    assert second["status"] == "ready" and second["result"] != first["result"]
    # undo returns to the first text: its entry is still cached
    c.post("/api/undo")
    v3 = c.state()["version"]
    assert c.post("/api/analysis", {"version": v3})[1]["status"] == "ready"
    assert len(ed.preview._cache) <= 3


def test_latest_wins_superseded_request(v4):
    """A request for a newer text while an older one computes: the older result is not reported as current."""
    c, _, _ = v4
    v1 = c.state()["version"]
    assert c.post("/api/analysis", {"version": v1})[1]["status"] == "computing"   # worker started
    v2 = c.apply([MOVE_FA])[1]["state"]["version"]
    assert c.post("/api/analysis", {"version": v1})[1]["stale"] is True
    j = _analysis(c, v2)
    assert j["status"] == "ready" and j["version"] == v2


def test_preview_writes_no_files(v4, tmp_path):
    c, p, scratch = v4
    before = _tree(str(tmp_path))
    _analysis(c)
    _analysis(c)
    c.post("/api/probe", {"version": c.state()["version"], "x": 400, "y": 400})
    assert _tree(str(tmp_path)) == before
    assert not os.path.exists(scratch)


def test_probe_semantics(v4):
    c, _, _ = v4
    v = c.state()["version"]
    assert c.post("/api/probe", {"version": v, "x": 400, "y": 400})[1]["ok"] is False   # nothing computed yet
    _analysis(c, v)
    st, j = c.post("/api/probe", {"version": v, "x": 400, "y": 400})
    assert st == 200 and j["ok"]
    pr = j["probe"]
    assert set(pr["times"]) == {"A", "B"} and pr["spread"] is not None and pr["region"]
    assert {"fair", "cost", "lines", "crossings", "cell", "tolerance"} <= set(pr)
    assert c.post("/api/probe", {"version": v, "x": "a", "y": 1})[0] == 400
    assert c.post("/api/probe", {"version": v + 5, "x": 1, "y": 1})[1]["stale"] is True


def test_analyze_default_cell_unchanged_for_builds(v4):
    """analysis.analyze without a cell is the spec's cell (the CLI path)."""
    _, p, _ = v4
    from mapgen import build
    spec, geoms, hm, an = build.prepare(p)
    assert an.grid.cols == int(-(-spec.size[0] // spec.analysis.cell))


# ---- guards ---------------------------------------------------------------------------------------------------------

def test_new_endpoints_guarded(v4):
    c, _, _ = v4
    for path in ("/api/analysis", "/api/probe", "/api/build", "/api/build_status", "/api/build_cancel", "/api/build_file"):
        assert c.req("POST", path, {}, token=False)[0] == 403
        assert c.req("POST", path, {}, token="wrong")[0] == 403
        assert c.req("POST", path, {}, host="evil.example:80")[0] == 403
        assert c.req("POST", path, {}, headers={"Origin": "http://evil.example"})[0] == 403
    assert c.req("GET", "/api/build_file")[0] == 404


@pytest.mark.parametrize("name", ["../x.png", "..\\map.png", "map.png/../../x.png", "overlay_x/../map.png", "", None, 5, ["map.png"],
                                  "report.json", "report.md", "/etc/passwd", "C:\\Windows\\win.ini", "map.png\x00.txt", "MAP.PNG",
                                  "overlay_.png/..", "sub/map.png"])
def test_build_file_rejects_everything_but_the_whitelist(tiny, name):
    c, _, _ = tiny
    st, j = c.post("/api/build_file", {"name": name})
    assert st == 404 and j["ok"] is False


def test_build_file_404_before_any_build(tiny):
    c, _, _ = tiny
    assert c.post("/api/build_file", {"name": "map.png"})[0] == 404


# ---- build job --------------------------------------------------------------------------------------------------------

def test_build_refused_for_stale_version_and_loader_error(tiny, tmp_path):
    c, _, scratch = tiny
    v = c.state()["version"]
    st, j = c.post("/api/build", {"version": v + 3})
    assert st == 409 and j["ok"] is False
    bm = BuildManager(str(tmp_path / "s2"), "tiny.yaml")
    with pytest.raises(BuildRefused) as e:
        bm.start(BASE.replace("seed: 1", "seed: abc"))
    assert e.value.code == 422 and any("seed" in m for m in e.value.errors)
    assert not os.path.exists(str(tmp_path / "s2" / "tiny")) and bm.status()["job"] is None
    assert not os.path.exists(scratch)


def test_second_build_refused_while_running_and_cancel_leaves_nothing(tiny):
    c, _, scratch = tiny
    v = c.state()["version"]
    st, j = c.post("/api/build", {"version": v})
    assert st == 200 and j["build"]["status"] == "running"
    st, j2 = c.post("/api/build", {"version": v})
    assert st == 409 and "already running" in j2["errors"][0]
    # editing and polling stay possible while the build runs
    assert c.state()["version"] == v
    assert c.post("/api/poll")[0] == 200
    st, j3 = c.post("/api/build_cancel")
    assert st == 200
    b = _wait_build(c)
    assert b["job"]["status"] == "cancelled" and b["last"] is None
    assert c.post("/api/build_file", {"name": "map.png"})[0] == 404
    assert not os.path.exists(os.path.join(scratch, "tiny"))
    work = os.path.join(scratch, ".work")
    assert not os.path.isdir(work) or os.listdir(work) == []
    assert c.post("/api/build_cancel")[0] == 409


def test_build_lifecycle_v4_matches_cli_byte_for_byte(v4, tmp_path):
    c, p, scratch = v4
    # build the BUFFER (an unsaved edit), not the saved file
    st, j = c.apply([MOVE_FA])
    ed = c.srv.ed
    v = j["state"]["version"]
    with open(p, "rb") as f:
        disk = f.read()
    snap = ed.text
    assert snap.encode("utf-8") != disk
    t0 = time.time()
    st, j = c.post("/api/build", {"version": v})
    assert st == 200 and j["build"]["status"] == "running" and j["build"]["version"] == v
    # an edit made while the build runs is accepted at once (the build is a snapshot of the text at the click)
    te = time.time()
    st, je = c.apply([{"op": "set", "path": "pois.g.at", "value": [385, 105], "detach": False}])
    assert st == 200 and time.time() - te < 3.0
    seen = []
    done = None
    while True:
        b = c.post("/api/build_status")[1]["build"]
        seen.append([s["state"] for s in b["job"]["stages"]])
        if b["job"]["status"] != "running":
            done = b
            break
        time.sleep(0.1)
    build_s = time.time() - t0
    job = done["job"]
    assert job["status"] == "done" and job["exit_code"] == 0
    assert [s["name"] for s in job["stages"]] == STAGES and all(s["state"] == "done" for s in job["stages"])
    # stages advance in order: once a stage is done every earlier one is done, and at most one runs
    for snapshot in seen:
        order = ["done", "running", "pending"]
        ranks = [order.index(s) for s in snapshot]
        assert ranks == sorted(ranks) and snapshot.count("running") <= 1
    assert any("running" in s for s in seen)
    last = done["last"]
    assert isinstance(last["report"]["warnings"], list) and last["report"]["area_share"]["unreachable"] <= 1
    assert last["files"][0] == "map.png" and "overlay_territory.png" in last["files"]
    # served images
    for name in last["files"]:
        st, raw, hdrs = c.req("POST", "/api/build_file", {"name": name})
        assert st == 200 and raw[:8] == b"\x89PNG\r\n\x1a\n" and hdrs["Content-Type"] == "image/png"
    # the saved file is untouched; the scratch folder holds the build; no staging leftovers
    with open(p, "rb") as f:
        assert f.read() == disk
    out = os.path.join(scratch, "town_square_v4")
    assert os.path.isfile(os.path.join(out, "report.md")) and os.path.isfile(os.path.join(out, "map.png"))
    work = os.path.join(scratch, ".work")
    assert not os.path.isdir(work) or os.listdir(work) == []
    # byte-identical to the CLI build of the same text
    cli_dir = tmp_path / "cli"
    cli_dir.mkdir()
    cli_spec = tmp_path / "cli_in" / "town_square_v4.yaml"
    cli_spec.parent.mkdir()
    cli_spec.write_bytes(snap.encode("utf-8"))
    code, so, se = run("build", str(cli_spec), "--out", str(cli_dir))
    assert code == 0, so + se
    a, b2 = _hashes(out), _hashes(str(cli_dir / "town_square_v4"))
    assert a and a == b2
    print("build job v4: %.1f s wall, %d files byte-identical to the CLI build" % (build_s, len(a)))


# ---- page and browser -------------------------------------------------------------------------------------------------

def test_page_has_analysis_and_build_panels(tiny):
    c, _, _ = tiny
    html = c.req("GET", "/")[1].decode("utf-8")
    for needle in ("id=\"b-analyze\"", "id=\"b-build\"", "id=\"b-bcancel\"", "id=\"viewer\"", "/api/analysis", "/api/build_status",
                   "/api/probe", "an-territory", "coarse preview"):
        assert needle in html


def test_browser_analysis_drive_if_available(tmp_path):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "browser"))
    import run_analysis_drive
    if not shutil.which("node") or not run_analysis_drive.find_browser():
        pytest.skip("node or browser missing")
    code, out = run_analysis_drive.run(str(tmp_path / "shots"))
    if code == 77:
        pytest.skip(out)
    assert code == 0, out
