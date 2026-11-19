"""Flowgraph preview: deterministic self-contained HTML, labels/edges/desc present, no external URLs, --out/--force, exit codes, layout clearance."""
import math
import os
import re

import pytest

from mapgen import flowgraph_preview
from mapgen.flowgraph import load_flowgraph
from mapgen.flowgraph_layout import layout
from test_cli_exit_codes import REPO_ROOT, run
from test_flowgraph import TOWN_SQUARE
from test_flowgraph_scaffold import BOUND, CHOKES_AREAS, THREE, _w


def _html(tmp_path, graph=TOWN_SQUARE):
    out = tmp_path / "p.html"
    code, o, e = run("flowgraph", "preview", graph, "--out", out, "--force")
    assert code == 0, o + e
    return out.read_text(encoding="utf-8")


def test_deterministic(tmp_path):
    a = _html(tmp_path)
    assert a == _html(tmp_path) and a == flowgraph_preview.render(load_flowgraph(TOWN_SQUARE))
    assert "\r" not in a


def test_town_square_contents(tmp_path):
    h = _html(tmp_path)
    g = load_flowgraph(TOWN_SQUARE)
    assert len(re.findall(r'class="node"', h)) == 9 and len(re.findall(r'class="edge"', h)) == 12
    for n in g.nodes:
        assert '<tspan class="nid">%s</tspan>' % n.id in h
        assert n.name in h
        if n.desc:
            assert n.desc in h
    assert "<title>town_square - flowgraph preview</title>" in h and "contested open town square" in h
    assert "team A" in h and "team B" in h
    for kind in ("Spawn", "Objective", "Control zone", "Chokepoint", "Area"):
        assert kind in h
    assert not [u for u in re.findall(r"https?://[^\s\"'<>]+", h) if u != "http://www.w3.org/2000/svg"]
    assert "src=" not in h and "href=" not in h and "@import" not in h


def test_edge_names_and_desc(tmp_path):
    g = _w(tmp_path / "g.flow.yaml", CHOKES_AREAS)
    h = _html(tmp_path, g)
    assert "back alley" in h and "narrow" in h and "ruins,   with: colons" in h


def test_out_force_and_default(tmp_path):
    g = _w(tmp_path / "g.flow.yaml", CHOKES_AREAS)
    out = tmp_path / "p.html"
    assert run("flowgraph", "preview", g, "--out", out)[0] == 0
    first = out.read_bytes()
    code, o, _ = run("flowgraph", "preview", g, "--out", out)
    assert code == 1 and "--force" in o and out.read_bytes() == first
    out.write_text("stale", encoding="utf-8")
    assert run("flowgraph", "preview", g, "--out", out, "--force")[0] == 0 and out.read_bytes() == first
    default = os.path.join(REPO_ROOT, "out", "flowgraph_tiny_ca.html")  # default path is overwritten freely
    for _ in range(2):
        code, o, _ = run("flowgraph", "preview", g)
        assert code == 0 and open(default, "rb").read() == first


def test_exit_codes(tmp_path):
    code, o, _ = run("flowgraph", "preview", tmp_path / "missing.flow.yaml")
    assert code == 1 and "file not found" in o
    bad = _w(tmp_path / "bad.flow.yaml", "format_version: 1\nname: x\nnodes: {}\n")
    code, o, _ = run("flowgraph", "preview", bad, "--out", tmp_path / "b.html")
    assert code == 1 and "error(s)" in o and not (tmp_path / "b.html").exists()


def _seg_dist(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy)))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


@pytest.mark.parametrize("text", [None, CHOKES_AREAS, BOUND, THREE], ids=["town_square", "chokes_areas", "bound", "three"])
def test_layout_clearance(tmp_path, text):
    g = load_flowgraph(TOWN_SQUARE if text is None else _w(tmp_path / "g.flow.yaml", text))
    w, h, pos = flowgraph_preview._canvas(g)
    assert len(set(pos.values())) == len(pos)
    for e in g.edges:
        for n in g.nodes:
            if n.id not in (e.a, e.b):
                assert _seg_dist(pos[n.id], pos[e.a], pos[e.b]) > flowgraph_preview.R * 1.3, (e.a, e.b, n.id)


def test_town_square_spawns_on_opposite_sides():
    g = load_flowgraph(TOWN_SQUARE)
    p = layout(g, 3000, 3000)
    assert p["A"][0] < p["AO"][0] < p["BO"][0] < p["B"][0]
    assert p["A"][0] < p["O"][0] < p["B"][0]
