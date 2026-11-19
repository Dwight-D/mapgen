"""Flowgraph editor: HTTP API (token, Host, Origin guards, validate/layout/save), canonical emitter round-trip, --new, binding."""
import copy
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import threading

import pytest
import yaml

from mapgen import flowgraph
from mapgen.flowgraph import canonical_text, graph_to_dict, load_flowgraph, load_flowgraph_dict, load_flowgraph_wire, save_canonical
from mapgen.flowgraph_editor import EditorServer, template_graph
from test_cli_exit_codes import REPO_ROOT, run
from test_flowgraph import TOWN_SQUARE


@pytest.fixture
def copy_path(tmp_path):
    p = tmp_path / "town.flow.yaml"
    shutil.copyfile(TOWN_SQUARE, str(p))
    return str(p)


class Client:
    def __init__(self, srv):
        self.srv = srv
        self.port = srv.server_address[1]

    def req(self, method, path, body=None, headers=None, host=None, token=True):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": host or "127.0.0.1:%d" % self.port}
        if token:
            h["X-Token"] = self.srv.token if token is True else token
        h.update(headers or {})
        data = None if body is None else json.dumps(body).encode("utf-8")
        if data is not None:
            h["Content-Type"] = "application/json"
        c.request(method, path, data, h)
        r = c.getresponse()
        raw = r.read()
        c.close()
        return r.status, raw

    def post(self, path, graph, **kw):
        st, raw = self.req("POST", path, {"graph": graph}, **kw)
        return st, json.loads(raw.decode("utf-8"))


@pytest.fixture
def server(copy_path):
    srv = EditorServer(copy_path)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield Client(srv)
    srv.shutdown()
    srv.server_close()


def _graph(path):
    return graph_to_dict(load_flowgraph(path))


# ---- page -------------------------------------------------------------------------------------------------------

def test_page_has_no_external_urls_and_embeds_token(server):
    st, raw = server.req("GET", "/", token=False)
    html = raw.decode("utf-8")
    assert st == 200 and server.srv.token in html
    assert "http://www.w3.org/2000/svg" in html
    stripped = html.replace("http://www.w3.org/2000/svg", "")
    assert not re.search(r"https?://|//cdn|@import|<link ", stripped)
    assert "not preserved" in html  # canonical-save warning near the Save button
    for tool in ("t-node", "t-edge", "b-delete", "b-undo", "b-redo", "b-layout", "b-save"):
        assert 'id="%s"' % tool in html


def test_page_for_new_graph_uses_template(tmp_path):
    srv = EditorServer(str(tmp_path / "fresh_one.flow.yaml"))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        c = Client(srv)
        st, raw = c.req("GET", "/", token=False)
        boot = json.loads(re.search(r'id="boot" type="application/json">(.*?)</script>', raw.decode("utf-8"), re.S).group(1))
        assert st == 200 and boot["graph"]["name"] == "fresh_one" and boot["exists"] is False
        assert [n["id"] for n in boot["graph"]["nodes"]] == ["A", "B"]
    finally:
        srv.shutdown()
        srv.server_close()


def test_binds_loopback_only(server):
    assert server.srv.server_address[0] == "127.0.0.1"


# ---- guards -----------------------------------------------------------------------------------------------------

def test_wrong_or_missing_token_rejected(server, copy_path):
    g = _graph(copy_path)
    assert server.post("/api/validate", g, token="nope")[0] == 403
    assert server.post("/api/validate", g, token=False)[0] == 403
    assert server.post("/api/save", g, token="nope")[0] == 403


def test_wrong_host_rejected(server, copy_path):
    g = _graph(copy_path)
    assert server.req("GET", "/", host="evil.example:%d" % server.port, token=False)[0] == 403
    assert server.post("/api/validate", g, host="evil.example")[0] == 403
    assert server.post("/api/validate", g, host="127.0.0.1:1")[0] == 403
    assert server.post("/api/validate", g, host="localhost:%d" % server.port)[0] == 200


def test_foreign_origin_rejected(server, copy_path):
    g = _graph(copy_path)
    assert server.post("/api/validate", g, headers={"Origin": "http://evil.example"})[0] == 403
    assert server.post("/api/validate", g, headers={"Origin": "http://127.0.0.1:%d" % server.port})[0] == 200


def test_bad_payloads(server):
    assert server.req("POST", "/api/validate", {"nope": 1})[0] == 400
    assert server.req("POST", "/api/other", {"graph": {}})[0] == 404
    assert server.req("GET", "/api/validate", token=False)[0] == 404
    st, r = server.post("/api/validate", {"nodes": "x", "edges": []})
    assert st == 200 and not r["ok"] and r["errors"]


# ---- validate ---------------------------------------------------------------------------------------------------

def test_validate_ok(server, copy_path):
    st, r = server.post("/api/validate", _graph(copy_path))
    assert st == 200 and r == {"ok": True, "errors": []}


def test_validate_bad_edit_matches_loader_messages(server, copy_path):
    g = _graph(copy_path)
    g["nodes"].append({"id": "Z", "kind": "spawn", "team": "", "name": "", "desc": "", "bind": "", "place": ""})
    g["nodes"].append({"id": "Z", "kind": "area", "team": "", "name": "", "desc": "", "bind": "", "place": "up"})
    g["edges"].append({"a": "A", "b": "A", "name": "", "desc": ""})
    g["edges"].append({"a": "A", "b": "Nope", "name": "", "desc": ""})
    st, r = server.post("/api/validate", g)
    assert st == 200 and not r["ok"]
    joined = "\n".join(r["errors"])
    assert "nodes.Z: duplicate node id" in joined


def test_validate_errors_equal_file_validator(server, tmp_path, copy_path):
    g = _graph(copy_path)
    g["nodes"][2]["kind"] = "bogus"
    g["nodes"][3]["team"] = "A"          # team on a choke
    g["edges"].append({"a": "A", "b": "AO", "name": "", "desc": ""})  # duplicate edge
    st, r = server.post("/api/validate", g)
    text = open(copy_path, encoding="utf-8").read()
    f = tmp_path / "bad.flow.yaml"
    raw = yaml.safe_load(text)
    raw["nodes"]["O"]["kind"] = "bogus"
    raw["nodes"]["AO"]["team"] = "A"
    raw["edges"].append("A - AO")
    f.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    with pytest.raises(flowgraph.SpecError) as e:
        load_flowgraph(str(f))
    assert r["errors"] == e.value.errors and len(r["errors"]) == 3


def test_validate_disconnected_and_missing_spawn(server, copy_path):
    g = _graph(copy_path)
    g["nodes"].append({"id": "Island", "kind": "area", "team": "", "name": "", "desc": "", "bind": "", "place": ""})
    st, r = server.post("/api/validate", g)
    assert not r["ok"] and any("not connected" in e for e in r["errors"])
    g2 = _graph(copy_path)
    g2["nodes"][0]["kind"] = "area"
    g2["nodes"][0]["team"] = ""
    st, r = server.post("/api/validate", g2)
    assert not r["ok"] and any("two spawn nodes" in e for e in r["errors"])


# ---- layout -----------------------------------------------------------------------------------------------------

def test_layout_endpoint_matches_python_layout_with_hints(server, copy_path):
    from mapgen import flowgraph_preview
    st, r = server.post("/api/layout", _graph(copy_path))
    w, h, pos = flowgraph_preview._canvas(load_flowgraph(copy_path))
    assert st == 200 and r["w"] == w and r["h"] == h
    assert {k: tuple(v) for k, v in r["pos"].items()} == pos
    assert r["pos"]["AO"][0] < r["pos"]["O"][0] < r["pos"]["BO"][0]  # place: west / center / east honoured


def test_layout_refused_when_invalid(server, copy_path):
    g = _graph(copy_path)
    g["edges"] = []
    st, r = server.post("/api/layout", g)
    assert st == 422 and not r["ok"] and "pos" not in r


# ---- save -------------------------------------------------------------------------------------------------------

def _node(i, **kw):
    d = {"id": i, "kind": "area", "team": "", "name": "", "desc": "", "bind": "", "place": ""}
    d.update(kw)
    return d


def test_add_node_edge_then_save_reload(server, copy_path):
    g = _graph(copy_path)
    g["nodes"].append(_node("Rooftop", kind="objective", name="Roof, with: odd chars", desc="high ground", place="north"))
    g["edges"].append({"a": "Rooftop", "b": "G", "name": "ladder", "desc": "single file"})
    st, r = server.post("/api/save", g)
    assert st == 200 and r["ok"] and r["bytes"] > 0
    g2 = load_flowgraph(copy_path)
    assert [n.id for n in g2.nodes][-1] == "Rooftop" and g2.nodes[-1].place == "north"
    assert g2.nodes[-1].name == "Roof, with: odd chars"
    assert (g2.edges[-1].a, g2.edges[-1].b, g2.edges[-1].name, g2.edges[-1].desc) == ("Rooftop", "G", "ladder", "single file")
    assert load_flowgraph_wire(g) == g2
    assert not [f for f in os.listdir(os.path.dirname(copy_path)) if f.endswith(".tmp")]


def test_node_delete_cascade_then_save(server, copy_path):
    g = _graph(copy_path)
    g["nodes"] = [n for n in g["nodes"] if n["id"] != "Fb"]
    g["edges"] = [e for e in g["edges"] if "Fb" not in (e["a"], e["b"])]
    g["nodes"] = [n for n in g["nodes"] if n["id"] != "F"]   # F only hung off Fb, A and B
    g["edges"] = [e for e in g["edges"] if "F" not in (e["a"], e["b"])]
    st, r = server.post("/api/save", g)
    assert st == 200
    g2 = load_flowgraph(copy_path)
    assert len(g2.nodes) == 7 and len(g2.edges) == 8


def test_save_refused_for_invalid_graph_and_file_untouched(server, copy_path):
    before = open(copy_path, "rb").read()
    g = _graph(copy_path)
    g["nodes"].append(_node("Island"))
    st, r = server.post("/api/save", g)
    assert st == 422 and not r["ok"] and r["errors"]
    assert open(copy_path, "rb").read() == before


def test_save_new_file_written_on_first_save(tmp_path):
    p = str(tmp_path / "start.flow.yaml")
    srv = EditorServer(p)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        c = Client(srv)
        assert not os.path.exists(p)
        g = graph_to_dict(template_graph(p))
        st, r = c.post("/api/save", g)
        assert st == 200 and os.path.exists(p)
        assert load_flowgraph(p).name == "start"
    finally:
        srv.shutdown()
        srv.server_close()


def test_build_town_square_like_graph_from_template(tmp_path):
    """Reachable-state bar: every node kind, place hint, edge and desc of town_square can be authored from the template."""
    p = str(tmp_path / "town_square.flow.yaml")
    srv = EditorServer(p)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        c = Client(srv)
        target = graph_to_dict(load_flowgraph(TOWN_SQUARE))
        g = graph_to_dict(template_graph(p))
        g["name"], g["brief"] = target["name"], target["brief"]
        g["nodes"], g["edges"] = [g["nodes"][0], g["nodes"][1]], []
        for n in target["nodes"][2:]:
            g["nodes"].append(dict(n))
            assert c.post("/api/validate", g)[1]["ok"] is False   # island nodes: invalid until wired
        g["nodes"][0].update(name="Team A spawn")
        g["edges"] = [dict(e) for e in target["edges"]]
        assert c.post("/api/save", g)[0] == 200
        assert load_flowgraph(p) == load_flowgraph(TOWN_SQUARE)
    finally:
        srv.shutdown()
        srv.server_close()


# ---- canonical emitter ------------------------------------------------------------------------------------------

def test_canonical_round_trip_town_square(tmp_path):
    g = load_flowgraph(TOWN_SQUARE)
    out = str(tmp_path / "copy.flow.yaml")
    save_canonical(g, out)
    assert load_flowgraph(out) == g
    text = open(out, "rb").read().decode("utf-8")
    assert "\r" not in text and text.startswith("format_version: 1\nname: town_square\nbrief: |\n")
    assert text.index("nodes:") < text.index("edges:")
    # canonical form is a fixed point
    out2 = str(tmp_path / "copy2.flow.yaml")
    save_canonical(load_flowgraph(out), out2)
    assert open(out2, "rb").read() == open(out, "rb").read()


def test_canonical_flow_style_and_key_order():
    t = canonical_text(load_flowgraph(TOWN_SQUARE))
    assert re.search(r"^  A:\s+\{ kind: spawn,\s+team: A, name: Team A spawn \}$", t, re.M)
    assert re.search(r"^  O:\s+\{ kind: objective, name: Town square, desc: open town-square style area, place: center \}$", t, re.M)
    assert "  - A - AO\n" in t
    ids = re.findall(r"^  (\w+):\s+\{ kind", t, re.M)
    assert ids == [n.id for n in load_flowgraph(TOWN_SQUARE).nodes]


def test_canonical_tricky_strings_and_explicit_bind(tmp_path):
    raw = {"format_version": 1, "name": "t", "brief": "line one\n\n  indented: yes\nlast",
           "nodes": {"A": {"kind": "spawn", "team": "A", "name": "true", "desc": "a: b, {c} # d \"q\""},
                     "B": {"kind": "spawn", "team": "B", "bind": "custom_b", "name": "12", "desc": " pad "},
                     "C": {"name": "naïve – dash", "place": "south"}},
           "edges": [{"link": "A - C", "name": "x: y"}, "C - B"]}
    g = load_flowgraph_dict(raw)
    p = str(tmp_path / "t.flow.yaml")
    save_canonical(g, p)
    g2 = load_flowgraph(p)
    assert g2 == g and g2.nodes[1].bind == "custom_b" and g2.nodes[0].bind == "base_A"
    assert "bind" not in canonical_text(g).split("B:")[0]  # default bind of A is omitted


def test_wire_format_round_trips_and_omits_default_bind():
    g = load_flowgraph(TOWN_SQUARE)
    d = graph_to_dict(g)
    assert all(n["bind"] == "" for n in d["nodes"])
    assert load_flowgraph_wire(copy.deepcopy(d)) == g


# ---- CLI --------------------------------------------------------------------------------------------------------

def test_cli_edit_errors(tmp_path):
    code, o, e = run("flowgraph", "edit", str(tmp_path / "missing.flow.yaml"), "--no-open")
    assert code == 1 and "file not found" in o
    code, o, e = run("flowgraph", "edit", TOWN_SQUARE, "--new", "--no-open")
    assert code == 1 and "exists" in o
    bad = tmp_path / "bad.flow.yaml"
    bad.write_text("format_version: 1\nname: x\nnodes: {}\n", encoding="utf-8")
    code, o, e = run("flowgraph", "edit", str(bad), "--no-open")
    assert code == 1 and "error(s)" in o


def test_cli_edit_serves_and_binds_loopback(tmp_path):
    import urllib.request
    p = subprocess.Popen([sys.executable, "-u", "-m", "mapgen", "flowgraph", "edit", "--new", str(tmp_path / "cli.flow.yaml"), "--no-open"],
                         cwd=REPO_ROOT, stdout=subprocess.PIPE, universal_newlines=True)
    try:
        line = p.stdout.readline()
        m = re.search(r"at (http://127\.0\.0\.1:\d+/)", line)
        assert m, line
        html = urllib.request.urlopen(m.group(1), timeout=10).read().decode("utf-8")
        assert "cli - flowgraph editor" in html
    finally:
        p.kill()
        p.wait()


def test_browser_drive_if_available(tmp_path):
    """Real-browser run of the UI (headless Edge/Chrome over DevTools); skipped when node >= 22 or a browser is missing."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "browser"))
    import run_drive
    node = shutil.which("node")
    if not node or not run_drive.find_browser():
        pytest.skip("node or browser missing")
    ver = subprocess.run([node, "--version"], stdout=subprocess.PIPE, universal_newlines=True).stdout.strip().lstrip("v")
    if int(ver.split(".")[0]) < 22:
        pytest.skip("node >= 22 needed")
    code, out = run_drive.run(str(tmp_path / "shots"))
    assert code == 0, out
