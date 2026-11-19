"""Road/trail shaping: `wobble`, `wobble_scale`, `smooth` (MAPGEN_SPEC 4.3)."""
import numpy as np
import pytest

from mapgen import export, geometry, schema
from mapgen.flowgraph import load_flowgraph
from mapgen.flowgraph_check import derive_edges
from mapgen.schema import SpecError

HEAD = """format_version: 1
name: shape
size: [2000, 2000]
seed: 3
teams:
  A: {base: [200, 1000]}
  B: {base: [1800, 1000]}
points:
  mid: [1000, 1200]
pois:
  - {id: c, type: capture, at: [1000, 1000], r: 40}
lines:
"""
PATH = "[base_A, [500, 1300], mid, [1400, 700], c, [1500, 1100], base_B]"
GRAPH = """format_version: 1
name: g
nodes:
  A: {kind: spawn, team: A}
  B: {kind: spawn, team: B}
  C: {kind: objective, bind: c}
edges:
  - A - C
  - C - B
"""


def _w(path, text):
    with open(str(path), "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return str(path)


def _spec(tmp_path, keys="", name="s", path=PATH):
    p = _w(tmp_path / (name + ".yaml"), HEAD + "  - {id: rd, type: road, path: %s, width: 8%s}\n" % (path, keys))
    return schema.load_spec(p)


def _centre(spec):
    return geometry.line_centreline(spec.lines[0], spec.seed)


def _nearest(pts, q):
    return float(np.min(np.hypot(pts[:, 0] - q[0], pts[:, 1] - q[1])))


def test_default_is_chaikin(tmp_path):
    spec = _spec(tmp_path)
    ln = spec.lines[0]
    assert (ln.wobble, ln.smooth) == (0.0, False)
    assert np.array_equal(_centre(spec), geometry.chaikin([tuple(p) for p in ln.path]))


def test_deterministic(tmp_path):
    a = _centre(_spec(tmp_path, ", wobble: 30", "a"))
    b = _centre(_spec(tmp_path, ", wobble: 30", "b"))
    assert np.array_equal(a, b)


def test_wobble_changes_shape_and_depends_on_id(tmp_path):
    flat = _centre(_spec(tmp_path, ", smooth: true", "a"))
    wob = _centre(_spec(tmp_path, ", wobble: 30", "b"))
    assert flat.shape == wob.shape and not np.allclose(flat, wob)
    assert float(np.max(np.hypot(*(flat - wob).T))) <= 30.0 + 1e-6
    other = _centre(_spec(tmp_path, ", wobble: 30, seed: 99", "d"))
    assert not np.allclose(wob, other)


def test_ends_and_reference_vertices_exact(tmp_path):
    spec = _spec(tmp_path, ", wobble: 60, wobble_scale: 150")
    ln = spec.lines[0]
    assert ln.fixed == [0, 2, 4, 6]
    pts = _centre(spec)
    for j in ln.fixed:
        assert _nearest(pts, ln.path[j]) < 1e-6
    assert tuple(pts[0]) == tuple(ln.path[0]) and tuple(pts[-1]) == tuple(ln.path[-1])


def test_smooth_passes_through_every_waypoint(tmp_path):
    spec = _spec(tmp_path, ", smooth: true")
    pts = _centre(spec)
    for p in spec.lines[0].path:
        assert _nearest(pts, p) < 1e-6


def test_flowgraph_derivation_unchanged_by_shaping(tmp_path):
    g = load_flowgraph(_w(tmp_path / "g.flow.yaml", GRAPH))
    plain = _spec(tmp_path, "", "p")
    shaped = _spec(tmp_path, ", wobble: 60, smooth: true", "q")
    assert derive_edges(g, plain) == derive_edges(g, shaped) == {frozenset(("A", "C")), frozenset(("C", "B"))}
    # the drawn centreline also stays within the node disc (max(r, 30) m) of the node
    assert _nearest(_centre(shaped), (1000, 1000)) <= 40.0


def test_unset_keys_absent_from_export_echo(tmp_path):
    assert export._shaping(_spec(tmp_path).lines[0]) == {}
    assert export._shaping(_spec(tmp_path, ", wobble: 5, smooth: true", "w").lines[0]) == {
        "wobble": 5.0, "wobble_scale": 200.0, "smooth": True}


@pytest.mark.parametrize("keys,err", [
    (", wobble: -1", "lines[0].wobble: "),
    (", wobble_scale: 0", "lines[0].wobble_scale: "),
    (", wobble: abc", "lines[0].wobble: "),
    (", smooth: yes", "lines[0].smooth: expected true or false, got 'yes'"),
])
def test_validation(tmp_path, keys, err):
    with pytest.raises(SpecError) as ei:
        _spec(tmp_path, keys)
    assert any(e.startswith(err) for e in ei.value.errors), ei.value.errors
