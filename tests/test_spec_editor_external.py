"""Spec editor external-change safety (GDU-55): disk watcher, clean reload, Replay / Discard / Save as copy of a dirty buffer,
stale Save 409, own Save not flagged, deleted / renamed-over / invalid / CRLF files, the op log, and a real-browser drive
(tests/browser/run_external_drive.py; skipped without node >= 22 and Edge/Chrome)."""
import difflib
import os
import shutil
import sys

import pytest

from mapgen import specpatch
from test_spec_editor import Client, V4, _start
from test_cli_exit_codes import REPO_ROOT  # noqa: F401


@pytest.fixture
def spec_path(tmp_path):
    p = str(tmp_path / "town_square_v4.yaml")
    shutil.copyfile(V4, p)
    return p


@pytest.fixture
def server(spec_path):
    srv = _start(spec_path)
    srv.shutdown  # noqa: B018
    yield Client(srv)
    srv.shutdown()
    srv.server_close()


def _read(p):
    with open(p, "rb") as f:
        return f.read().decode("utf-8")


def _write(p, text):
    with open(p, "wb") as f:
        f.write(text.encode("utf-8"))


def _poll(c):
    return c.post("/api/poll")[1]["state"]


def _resolve(c, action, version=None):
    return c.post("/api/resolve", {"action": action, "version": c.state()["version"] if version is None else version})


def _line_diff(a, b):
    return [l for l in difflib.unified_diff(a.splitlines(), b.splitlines(), lineterm="", n=0)
            if l[:1] in "+-" and not l.startswith(("+++", "---"))]


MOVE_FA = {"op": "set", "path": "pois.fa.at", "value": [400, 270], "detach": False}
MOVE_G = {"op": "set", "path": "pois.g.at", "value": [385, 105], "detach": False}
COVER = {"op": "put", "path": "scatter.wild_wood.coverage", "value": 0.45}


def _fb_label_edit(text):
    assert "label: Attic }" in text
    return text.replace("label: Attic }", "label: Attic2 }")


# ---- clean buffer ------------------------------------------------------------------------------------------------

def test_clean_buffer_reloads_automatically(server, spec_path):
    ed = server.srv.ed
    st0 = server.state()
    new = _fb_label_edit(_read(spec_path))
    _write(spec_path, new)
    st = _poll(server)
    assert st["external"] is None and st["dirty"] is False
    assert st["notice"]["kind"] == "reloaded" and "reloaded" in st["notice"]["message"]
    assert st["version"] > st0["version"]
    assert ed.text == new
    assert st["can_undo"] is False and st["can_redo"] is False   # history cleared


def test_clean_reload_clears_history(server, spec_path):
    assert server.apply([MOVE_FA])[0] == 200
    assert server.post("/api/save")[0] == 200
    assert server.state()["can_undo"] is True
    _write(spec_path, _fb_label_edit(_read(spec_path)))
    st = _poll(server)
    assert st["notice"]["kind"] == "reloaded" and st["can_undo"] is False


def test_watcher_thread_detects_without_poll(server, spec_path):
    import time
    server.srv.ed.check_disk()
    _write(spec_path, _fb_label_edit(_read(spec_path)))
    deadline = time.time() + 10
    while time.time() < deadline and "Attic2" not in server.srv.ed.text:
        time.sleep(0.1)
    assert "Attic2" in server.srv.ed.text


def test_own_save_does_not_trigger_watcher(server, spec_path):
    assert server.apply([MOVE_FA])[0] == 200
    code, j = server.post("/api/save")
    assert code == 200
    v = server.state()["version"]
    st = _poll(server)
    assert st["external"] is None and st["notice"] is None and st["version"] == v and st["dirty"] is False
    assert _read(spec_path) == server.srv.ed.text


# ---- dirty buffer: Replay ----------------------------------------------------------------------------------------

def _dirty_session(server):
    assert server.apply([MOVE_FA])[0] == 200
    assert server.apply([MOVE_G, COVER])[0] == 200
    return server.state()


def test_dirty_external_change_blocks_save_and_offers_resolution(server, spec_path):
    _dirty_session(server)
    _write(spec_path, _fb_label_edit(_read(spec_path)))
    st = _poll(server)
    ex = st["external"]
    assert ex["kind"] == "changed" and ex["can_replay"] and ex["can_discard"] and st["dirty"] and st["unsaved_ops"] == 3
    assert st["notice"] is None
    code, j = server.post("/api/save")
    assert code == 409 and "changed on disk" in j["errors"][0]
    assert "Attic2" in _read(spec_path)   # not overwritten


def test_replay_applies_all_ops_unrelated_change(server, spec_path):
    _dirty_session(server)
    before_buffer = server.srv.ed.text
    disk = _fb_label_edit(_read(spec_path))
    _write(spec_path, disk)
    _poll(server)
    code, j = _resolve(server, "replay")
    assert code == 200, j
    st = j["state"]
    assert st["replay"] == {"applied": 3, "skipped": []} and st["external"] is None and st["dirty"]
    ed = server.srv.ed
    expected = specpatch.apply_ops(disk, [specpatch.op_from_json(o) for o in (MOVE_FA, MOVE_G, COVER)])
    assert ed.text == expected
    # only the intended diffs relative to the disk text: the two POI lines and the scatter line
    d = _line_diff(disk, ed.text)
    assert len(d) == 6 and all(any(k in l for k in ("id: fa", "id: g,", "id: wild_wood")) for l in d)
    assert "Attic2" in ed.text and ed.text != before_buffer
    # undoable: one step back is the disk text; dirty vs the new baseline
    assert st["can_undo"] is True
    assert server.post("/api/undo")[1]["state"]["dirty"] is False and ed.text == disk
    assert server.post("/api/redo")[1]["state"]["dirty"] is True
    code, j = server.post("/api/save")
    assert code == 200 and _read(spec_path) == expected
    assert _poll(server)["external"] is None


def test_replay_skips_op_whose_target_is_gone(server, spec_path):
    _dirty_session(server)
    disk = _read(spec_path)
    gone = "\n".join(l for l in disk.split("\n") if "{ id: wild_wood," not in l)
    assert gone != disk
    _write(spec_path, gone)
    st = _poll(server)
    assert st["external"]["kind"] == "changed", st["external"]
    code, j = _resolve(server, "replay")
    assert code == 200, j
    rp = j["state"]["replay"]
    assert rp["applied"] == 2 and len(rp["skipped"]) == 1
    assert rp["skipped"][0]["text"] == "put scatter.wild_wood.coverage" and rp["skipped"][0]["reason"]
    expected = specpatch.apply_ops(gone, [specpatch.op_from_json(o) for o in (MOVE_FA, MOVE_G)])
    assert server.srv.ed.text == expected


def test_replay_of_convert_skipped_when_region_deleted(server, spec_path):
    assert server.post("/api/paint", {"version": server.state()["version"], "mode": "convert", "id": "garden_se"})[0] == 200
    assert server.apply([MOVE_FA])[0] == 200
    disk = _read(spec_path)
    gone = "\n".join(l for l in disk.split("\n") if "id: garden_se," not in l)
    assert gone != disk
    _write(spec_path, gone)
    _poll(server)
    code, j = _resolve(server, "replay")
    assert code == 200, j
    rp = j["state"]["replay"]
    assert rp["applied"] == 1 and len(rp["skipped"]) >= 1
    assert all("garden_se" in k["text"] for k in rp["skipped"])
    assert all(k["reason"] for k in rp["skipped"])
    assert "garden_se" not in server.srv.ed.text and "[400, 270]" in server.srv.ed.text


def test_replay_paint_uses_same_ids_and_set_points(server, spec_path):
    v = server.state()["version"]
    stroke = [[150, 150], [200, 150], [250, 150]]
    code, j = server.post("/api/paint", {"version": v, "mode": "paint", "stroke": stroke, "radius": 20, "type": "forest"})
    assert code == 200, j
    created = j["state"]["results"][0]["paint"]["created_ids"]
    assert created
    code, j = server.post("/api/paint", {"version": j["state"]["version"], "mode": "paint", "stroke": [[250, 150], [300, 160]],
                                         "radius": 20, "type": "forest"})
    assert code == 200
    log = [o for e in server.srv.ed.oplog for o in e["ops"]]
    assert any(o["op"] == "add" and o.get("id") == created[0] for o in log)   # generated id recorded
    assert any(o["op"] == "set" and o["path"].endswith(".points") for o in log)
    buf = server.srv.ed.text
    disk = _fb_label_edit(_read(spec_path))
    _write(spec_path, disk)
    _poll(server)
    code, j = _resolve(server, "replay")
    assert code == 200 and j["state"]["replay"]["skipped"] == []
    assert server.srv.ed.text == _fb_label_edit(buf) or server.srv.ed.text == specpatch.apply_ops(
        disk, [specpatch.op_from_json(o) for o in log])


def test_op_log_records_every_edit_kind(server):
    ed = server.srv.ed
    v = server.state()["version"]
    steps = [
        [{"op": "set", "path": "pois.fa.at", "value": [401, 266], "detach": False}],                 # move
        [{"op": "insert", "path": "lines.stream_b.path", "index": 1, "value": [700, 220]}],          # insert vertex
        [{"op": "delete", "path": "lines.stream_b.path[1]"}],                                        # delete vertex
        [{"op": "add", "section": "pois", "value": {"type": "landmark", "at": [100, 100], "label": "N"}}],   # add feature
        [{"op": "put", "path": "regions.rock_mid.rough", "value": 0.5}],                              # property
    ]
    for ops in steps:
        code, j = server.apply(ops)
        assert code == 200, j
    new_id = [o for o in ed.oplog[3]["ops"]][0]["id"]
    assert new_id
    code, j = server.apply([{"op": "rename", "from": new_id, "to": "extra"}])
    assert code == 200
    code, j = server.apply([{"op": "delete", "path": "pois.extra"}])
    assert code == 200
    code, j = server.post("/api/paint", {"version": j["state"]["version"], "mode": "paint", "stroke": [[10, 700], [60, 700]],
                                         "radius": 15, "type": "forest"})
    assert code == 200
    code, j = server.post("/api/paint", {"version": j["state"]["version"], "mode": "convert", "id": "rock_n"})
    assert code == 200
    assert len(ed.oplog) == 9 and all(not e["saved"] and e["ops"] for e in ed.oplog)
    # every logged op round-trips through the engine from the original text
    text = _read(V4)
    for e in ed.oplog:
        text = specpatch.apply_ops(text, [specpatch.op_from_json(o) for o in e["ops"]])
    assert text == ed.text
    # undo/redo move the entries with the buffer
    server.post("/api/undo")
    assert len(ed.oplog) == 8 and len(ed.redo_oplog) == 1
    server.post("/api/redo")
    assert len(ed.oplog) == 9 and not ed.redo_oplog


def test_scatter_within_is_logged(server):
    ed = server.srv.ed
    ring = [[300, 100], [360, 100], [360, 160], [300, 160]]
    code, j = server.apply([{"op": "put", "path": "scatter.wild_wood.within", "value": ring}])
    assert code == 200, j
    assert ed.oplog[-1]["ops"][0]["path"] == "scatter.wild_wood.within"


def test_replay_blocked_after_undo_past_save(server, spec_path):
    assert server.apply([MOVE_FA])[0] == 200
    assert server.post("/api/save")[0] == 200
    assert server.apply([MOVE_G])[0] == 200
    server.post("/api/undo")
    server.post("/api/undo")   # undid the saved step: buffer differs from disk by a revert the log cannot express
    _write(spec_path, _fb_label_edit(_read(spec_path)))
    st = _poll(server)
    assert st["external"]["kind"] == "changed" and st["external"]["can_replay"] is False and "undo" in st["external"]["replay_blocked"]
    code, j = _resolve(server, "replay")
    assert code == 409
    assert _resolve(server, "discard")[0] == 200


def test_replay_skips_ops_saved_before(server, spec_path):
    assert server.apply([MOVE_FA])[0] == 200
    assert server.post("/api/save")[0] == 200
    assert server.apply([MOVE_G])[0] == 200
    _write(spec_path, _fb_label_edit(_read(spec_path)))
    _poll(server)
    code, j = _resolve(server, "replay")
    assert code == 200 and j["state"]["replay"] == {"applied": 1, "skipped": []}
    assert "[385, 105]" in server.srv.ed.text


# ---- Discard / Save as copy / stale save -------------------------------------------------------------------------

def test_discard_loads_disk_text(server, spec_path):
    _dirty_session(server)
    disk = _fb_label_edit(_read(spec_path))
    _write(spec_path, disk)
    _poll(server)
    code, j = _resolve(server, "discard")
    assert code == 200
    st = j["state"]
    assert st["external"] is None and st["dirty"] is False and st["can_undo"] is False and st["unsaved_ops"] == 0
    assert server.srv.ed.text == disk
    assert server.post("/api/save")[0] == 200 and _read(spec_path) == disk


def test_save_as_copy_never_overwrites(server, spec_path):
    _dirty_session(server)
    buf = server.srv.ed.text
    disk = _fb_label_edit(_read(spec_path))
    _write(spec_path, disk)
    _poll(server)
    folder = os.path.dirname(spec_path)
    c1 = os.path.join(folder, "town_square_v4.edited1.yaml")
    code, j = _resolve(server, "copy")
    assert code == 200 and j["state"]["copy"] == c1 and _read(c1) == buf
    code, j = _resolve(server, "copy")
    c2 = os.path.join(folder, "town_square_v4.edited2.yaml")
    assert j["state"]["copy"] == c2 and _read(c1) == buf
    _write(os.path.join(folder, "town_square_v4.edited3.yaml"), "sentinel")
    code, j = _resolve(server, "copy")
    assert j["state"]["copy"].endswith("edited4.yaml")
    assert _read(os.path.join(folder, "town_square_v4.edited3.yaml")) == "sentinel"
    # the original buffer is kept and the disk change is still pending: Save stays blocked
    st = server.state()
    assert st["external"]["kind"] == "changed" and st["dirty"] and server.srv.ed.text == buf
    assert server.post("/api/save")[0] == 409
    assert _read(spec_path) == disk


def test_stale_save_409_until_resolved(server, spec_path):
    _dirty_session(server)
    _write(spec_path, _fb_label_edit(_read(spec_path)))
    for _ in range(2):
        assert server.post("/api/save")[0] == 409
    assert _resolve(server, "replay")[0] == 200
    code, j = server.post("/api/save")
    assert code == 200 and j["bytes"] > 0


def test_resolve_without_external_change_is_refused(server):
    assert _resolve(server, "replay")[0] == 409
    assert _resolve(server, "discard")[0] == 409
    assert _resolve(server, "bogus")[0] == 400


# ---- deleted / renamed / replaced / invalid / CRLF ---------------------------------------------------------------

def test_deleted_file_allows_only_save_as_copy(server, spec_path):
    _dirty_session(server)
    orig = _read(spec_path)
    os.remove(spec_path)
    st = _poll(server)
    assert st["external"]["kind"] == "deleted" and st["external"]["can_replay"] is False and st["external"]["can_discard"] is False
    code, j = server.post("/api/save")
    assert code == 409 and "no longer exists" in j["errors"][0] and not os.path.exists(spec_path)
    assert _resolve(server, "replay")[0] == 409 and _resolve(server, "discard")[0] == 409
    code, j = _resolve(server, "copy")
    assert code == 200 and os.path.exists(j["state"]["copy"])
    # the file coming back unchanged clears the banner
    _write(spec_path, orig)
    assert _poll(server)["external"] is None


def test_renamed_away_is_deleted(server, spec_path):
    os.rename(spec_path, spec_path + ".bak")
    st = _poll(server)
    assert st["external"]["kind"] == "deleted"


def test_atomic_replace_by_other_editor_detected_by_hash(server, spec_path):
    _dirty_session(server)
    tmp = spec_path + ".other"
    _write(tmp, _fb_label_edit(_read(spec_path)))
    os.replace(tmp, spec_path)
    st = _poll(server)
    assert st["external"]["kind"] == "changed"
    assert server.post("/api/save")[0] == 409


def test_same_content_rewrite_is_not_a_change(server, spec_path):
    _dirty_session(server)
    _write(spec_path, _read(spec_path))   # new mtime, same bytes
    assert _poll(server)["external"] is None


def test_invalid_on_disk_keeps_buffer_and_blocks_replay(server, spec_path):
    _dirty_session(server)
    buf = server.srv.ed.text
    v = server.state()["version"]
    good = _fb_label_edit(_read(spec_path))
    _write(spec_path, good.replace("pois:", "pois:\n  - { id: bad, type: nonsense_type, at: [1, 1] }", 1))
    st = _poll(server)
    ex = st["external"]
    assert ex["kind"] == "invalid" and ex["errors"] and ex["can_replay"] is False and ex["can_discard"] is False
    assert st["version"] == v and server.srv.ed.text == buf
    assert _resolve(server, "replay")[0] == 409 and _resolve(server, "discard")[0] == 409
    assert server.post("/api/save")[0] == 409
    assert _resolve(server, "copy")[0] == 200
    # garbage bytes are an invalid file too, never a server error
    with open(spec_path, "wb") as f:
        f.write(b"\xff\xfe: [unclosed")
    assert _poll(server)["external"]["kind"] == "invalid"
    _write(spec_path, good)   # fixed: now a normal changed file
    st = _poll(server)
    assert st["external"]["kind"] == "changed" and st["external"]["can_replay"]
    assert _resolve(server, "replay")[0] == 200


def test_invalid_on_disk_with_clean_buffer_keeps_buffer_then_reloads(server, spec_path):
    ed = server.srv.ed
    orig = ed.text
    _write(spec_path, "size: [")
    st = _poll(server)
    assert st["external"]["kind"] == "invalid" and ed.text == orig
    good = _fb_label_edit(orig)
    _write(spec_path, good)
    st = _poll(server)
    assert st["external"] is None and ed.text == good and st["notice"]["kind"] == "reloaded"


def test_crlf_change_clean_and_replay_keep_line_endings(server, spec_path):
    _dirty_session(server)
    disk = _read(spec_path).replace("\n", "\r\n")
    _write(spec_path, disk)
    st = _poll(server)
    assert st["external"]["kind"] == "changed"
    code, j = _resolve(server, "replay")
    assert code == 200 and j["state"]["replay"]["skipped"] == []
    text = server.srv.ed.text
    assert "\r\n" in text and "\n" not in text.replace("\r\n", "")
    assert server.post("/api/save")[0] == 200
    raw = open(spec_path, "rb").read()
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")
    assert raw.decode("utf-8") == text


def test_lf_to_crlf_with_clean_buffer_reloads(server, spec_path):
    disk = _read(spec_path).replace("\n", "\r\n")
    _write(spec_path, disk)
    st = _poll(server)
    assert st["external"] is None and st["notice"]["kind"] == "reloaded" and server.srv.ed.text == disk


# ---- page and browser --------------------------------------------------------------------------------------------

def test_page_has_banner_and_polls(server):
    st, raw, _ = server.req("GET", "/")
    html = raw.decode("utf-8")
    for needle in ("id=\"ext\"", "/api/poll", "/api/resolve", "ext-replay", "ext-discard", "ext-copy", "ext-cancel"):
        assert needle in html


def test_browser_external_drive_if_available(tmp_path):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "browser"))
    import run_external_drive
    if not shutil.which("node") or not run_external_drive.find_browser():
        pytest.skip("node or browser missing")
    shots = str(tmp_path / "shots")
    code, out = run_external_drive.run(shots)
    if code == 77:
        pytest.skip(out)
    assert code == 0, out
