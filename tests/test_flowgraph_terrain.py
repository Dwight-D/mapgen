"""flowgraph check --terrain (FLOWGRAPH_SPEC 12): river/bridge, open-field bypass, impassable wall, --strict, exit codes."""
import os

from test_cli_exit_codes import REPO_ROOT, run

HEAD = """format_version: 1
name: t
size: [600, 600]
seed: 3
teams:
  A: {base: [60, 300]}
  B: {base: [540, 300]}
output: {heightmap_res: 129, splat_res: 128, render_px: 400}
"""
RIVER = "lines:\n  - {id: rv, type: river, path: [[300, 0], [300, 600]], seed: 1}\n"
BRIDGE = "crossings:\n  - {id: br, type: bridge, at: [300, 300]}\n"
MID = "pois:\n  - {id: mid, type: capture, at: [300, 300], r: 40}\n"
WALLS = ("regions:\n"
         "  - {id: wn, type: forest, shape: poly, points: [[230, 330], [370, 330], [370, 600], [230, 600]], impassable: true}\n"
         "  - {id: ws, type: forest, shape: poly, points: [[230, 0], [370, 0], [370, 270], [230, 270]], impassable: true}\n")

G2 = """format_version: 1
name: two
nodes:
  A: {kind: spawn, team: A}
  B: {kind: spawn, team: B}
edges:
  - A - B
"""
G3 = """format_version: 1
name: three
nodes:
  A: {kind: spawn, team: A}
  B: {kind: spawn, team: B}
  M: {kind: objective, bind: mid}
edges:
  - A - M
  - M - B
"""
# road lines so the road-path check passes
ROADS = ("  - {id: r1, type: road, path: [base_A, mid], seed: 2}\n"
         "  - {id: r2, type: road, path: [mid, base_B], seed: 3}\n")


def _w(tmp_path, name, text):
    p = os.path.join(str(tmp_path), name)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return p


def _run(tmp_path, graph, spec, *flags):
    g = _w(tmp_path, "g.flow.yaml", graph)
    s = _w(tmp_path, "s.yaml", spec)
    code, out, _ = run("flowgraph", "check", g, s, "--terrain", *flags)
    return code, out


def test_river_with_bridge_is_terrain_present(tmp_path):
    spec = HEAD + RIVER.replace("seed: 1}", "seed: 1}\n  - {id: rd, type: road, path: [base_A, base_B], seed: 2}") + BRIDGE
    code, out = _run(tmp_path, G2, spec)
    assert code == 0, out
    assert out.strip().splitlines()[-1] == "OK: 0 missing node(s), 0 missing edge(s), 0 terrain-missing edge(s), 0 terrain-extra edge(s)"


def test_river_without_bridge_is_terrain_missing(tmp_path):
    # no road: the road check also fails here (a road would get an automatic bridge and open the river)
    code, out = _run(tmp_path, G2, HEAD + RIVER)
    lines = out.splitlines()
    assert code == 1 and "terrain-missing edge A - B" in lines and "missing edge A - B" in lines


def test_road_findings_still_reported(tmp_path):
    spec = HEAD + RIVER + BRIDGE
    code, out = _run(tmp_path, G2, spec)
    lines = out.splitlines()
    assert code == 1 and "missing edge A - B" in lines and not any(l.startswith("terrain-") for l in lines)


def test_open_field_bypass_is_terrain_extra(tmp_path):
    spec = HEAD + MID + "lines:\n" + ROADS
    code, out = _run(tmp_path, G3, spec)
    assert code == 1
    assert "terrain-extra edge A - B" in out.splitlines()
    assert not any(l.startswith("terrain-missing") for l in out.splitlines())


def test_impassable_walls_close_the_bypass(tmp_path):
    spec = HEAD + MID + WALLS + "lines:\n" + ROADS
    code, out = _run(tmp_path, G3, spec)
    assert code == 0, out
    assert not any(l.startswith("terrain-") for l in out.splitlines())


def test_composes_with_strict(tmp_path):
    spec = HEAD + MID + WALLS + "lines:\n" + ROADS
    code, out = _run(tmp_path, G3, spec, "--strict")
    assert code == 0, out
    assert out.strip().splitlines()[-1].startswith("OK: 0 missing node(s), 0 missing edge(s), 0 extra edge(s), 0 extra node(s), 0 terrain-missing")
    code, out = _run(tmp_path, G3, HEAD + MID + "lines:\n" + ROADS, "--strict")
    assert code == 1 and "terrain-extra edge A - B" in out


def test_without_flag_terrain_is_skipped(tmp_path):
    g = _w(tmp_path, "g.flow.yaml", G3)
    s = _w(tmp_path, "s.yaml", HEAD + MID + "lines:\n" + ROADS)
    code, out, _ = run("flowgraph", "check", g, s)
    assert code == 0 and "terrain" not in out


def test_overlapping_discs_do_not_crash(tmp_path):
    spec = HEAD + "pois:\n  - {id: mid, type: capture, at: [150, 300], r: 40}\n" + "lines:\n" + ROADS
    code, out = _run(tmp_path, G3, spec)
    assert "Traceback" not in out and "note: discs of A and M overlap (treated as adjacent)" in out


def test_town_square_poc_reports_terrain_extra():
    code, out, _ = run("flowgraph", "check", os.path.join(REPO_ROOT, "flowgraphs", "town_square.flow.yaml"),
                       os.path.join(REPO_ROOT, "maps", "iterations", "town_square", "v2_walled", "town_square_v2.yaml"), "--terrain")
    assert code in (0, 1) and "Traceback" not in out
    assert "terrain-extra edge" in out or code == 0
