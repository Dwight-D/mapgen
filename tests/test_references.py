"""Point references: resolution in every accepted place, moving a target moves its references, a spec written with
references equals the same spec written with literal coordinates, the unknown-reference error through the CLI."""
import json
import os

import pytest
import yaml

import regress
from mapgen import build, schema
import mapgen.__main__
from test_validation import BASE, PL, T

THORNWALD = os.path.join(regress.REPO_ROOT, "maps", "concepts", "thornwald.yaml")

ALL_PLACES = BASE + """points:
  j: [500, 500]
pois:
  - {id: c, type: capture, at: [400, 600]}
regions:
  - {id: r1, type: forest, shape: blob, at: c, r: 50}
  - {id: r2, type: field, shape: poly, points: [j, c, base_B]}
  - {id: r3, type: hills, shape: band, path: [base_A, j], width: 60}
lines:
  - {id: road, type: road, path: [base_A, j, c, base_B]}
crossings:
  - {id: x1, type: ford, at: j}
  - {id: x2, type: bridge, path: [j, c], width: 20}
"""

# Small build pair: the same map written with references and with literal coordinates.
BUILD_REFS = BASE.replace("size: [1000, 1000]", "size: [600, 600]").replace("[900, 900]", "[500, 500]") + """output: {heightmap_res: 129, splat_res: 128, render_px: 300}
points:
  fork: [300, 250]
pois:
  - {id: keep, type: capture, at: [450, 150]}
regions:
  - {id: wood, type: forest, shape: blob, at: keep, r: 60, rough: 0.3}
lines:
  - {id: road, type: road, path: [base_A, fork, base_B], width: 8}
  - {id: trail, type: trail, path: [fork, keep], width: 4}
crossings:
  - {id: ford, type: ford, at: fork}
"""
BUILD_LITERAL = BUILD_REFS.replace("points:\n  fork: [300, 250]\n", "").replace(
    "[base_A, fork, base_B]", "[[100, 100], [300, 250], [500, 500]]").replace(
    "[fork, keep]", "[[300, 250], [450, 150]]").replace("at: keep", "at: [450, 150]").replace("at: fork", "at: [300, 250]")


def _write(path, text):
    with open(str(path), "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return str(path)


def _read(path):
    with open(path, "r", encoding="utf-8", newline="") as f:
        return f.read()


def _edited(text, old, new):
    assert text.count(old) == 1, old
    return text.replace(old, new)


def _by_id(items):
    return {x.id: x for x in items}


def _load_json(out, rel):
    with open(os.path.join(out, *rel.split("/")), "r", encoding="utf-8") as f:
        return json.load(f)


def test_all_places_resolve(tmp_path):
    spec = schema.load_spec(_write(tmp_path / "a.yaml", ALL_PLACES))
    assert spec.points == {"j": (500.0, 500.0)}
    reg, cr = _by_id(spec.regions), _by_id(spec.crossings)
    assert reg["r1"].at == (400.0, 600.0)
    assert reg["r2"].points == [(500.0, 500.0), (400.0, 600.0), (900.0, 900.0)]
    assert reg["r3"].path == [(100.0, 100.0), (500.0, 500.0)]
    assert spec.lines[0].path == [(100.0, 100.0), (500.0, 500.0), (400.0, 600.0), (900.0, 900.0)]
    assert cr["x1"].at == (500.0, 500.0)
    assert cr["x2"].path == [(500.0, 500.0), (400.0, 600.0)]
    assert cr["x2"].at == (450.0, 550.0)


def test_quoted_reference_equals_bare(tmp_path):
    bare = schema.load_spec(_write(tmp_path / "a.yaml", ALL_PLACES))
    quoted = schema.load_spec(_write(tmp_path / "b.yaml", _edited(ALL_PLACES, "at: c, r: 50", "at: 'c', r: 50")))
    assert _by_id(quoted.regions)["r1"].at == _by_id(bare.regions)["r1"].at == (400.0, 600.0)


def test_unquoted_null_is_a_malformed_point(tmp_path):
    text = BASE + "lines:\n  - {id: road, type: road, path: [base_A, null]}\n"
    with pytest.raises(schema.SpecError) as ei:
        schema.load_spec(_write(tmp_path / "a.yaml", text))
    assert ei.value.errors == ["lines[0].path[1]: expected a point [x, y] of two numbers, got None"]


def test_empty_points_section_is_valid(tmp_path):
    for text in (BASE + "points:\n", BASE + "points: {}\n"):
        assert schema.load_spec(_write(tmp_path / "a.yaml", text)).points == {}


def test_points_names_are_references_and_keep_mapping_order(tmp_path):
    text = BASE + "points:\n  z_last: [10, 10]\n  a_first: [20, 20]\nlines:\n  - {id: road, type: road, path: [z_last, a_first]}\n"
    spec = schema.load_spec(_write(tmp_path / "a.yaml", text))
    assert list(spec.points) == ["z_last", "a_first"]
    assert spec.lines[0].path == [(10.0, 10.0), (20.0, 20.0)]


def test_move_base_in_small_spec(tmp_path):
    before = schema.load_spec(_write(tmp_path / "a.yaml", BUILD_REFS))
    after = schema.load_spec(_write(tmp_path / "b.yaml", _edited(BUILD_REFS, "{base: [100, 100]}", "{base: [120, 140]}")))
    assert before.lines[0].path[0] == (100.0, 100.0)
    assert after.lines[0].path[0] == (120.0, 140.0)
    assert after.lines[0].path[1:] == before.lines[0].path[1:]


def test_move_named_point_in_small_spec(tmp_path):
    before = schema.load_spec(_write(tmp_path / "a.yaml", BUILD_REFS))
    after = schema.load_spec(_write(tmp_path / "b.yaml", _edited(BUILD_REFS, "fork: [300, 250]", "fork: [310, 260]")))
    assert after.lines[0].path[1] == after.lines[1].path[0] == after.crossings[0].at == (310.0, 260.0)
    assert before.lines[0].path[1] == (300.0, 250.0)


@pytest.fixture(scope="module")
def moved_base(tmp_path_factory):
    text = _read(THORNWALD)
    assert text.count("[2050, 2560]") == 1
    text = _edited(text, "B: { base: [2050, 2560]", "B: { base: [2100, 2600]")
    base = tmp_path_factory.mktemp("thorn_base")
    (base / "spec").mkdir()
    spec_path = _write(base / "spec" / "thornwald.yaml", text)
    return build.build(spec_path, str(base / "out"), only="export", log=lambda s: None)


def test_move_base_moves_references(moved_base):
    lines = {x["id"]: x for x in _load_json(moved_base, "export/lines.json")}
    resolved = {x["id"]: x for x in _load_json(moved_base, "export/spec.resolved.json")["lines"]}
    for lid in ("b_track", "abbey_path"):
        assert lines[lid]["points"][0][:2] == [2100.0, 2600.0], lid
        assert resolved[lid]["path"][0] == [2100.0, 2600.0], lid


def _all_points(spec):
    """Every point of every line, region and crossing path, keyed by feature, field and index."""
    found = {}
    for r in spec.regions:
        if r.scatter is not None:
            continue
        if r.at is not None:
            found[("region", r.id, "at", 0)] = r.at
        for field in ("points", "path"):
            for i, p in enumerate(getattr(r, field) or []):
                found[("region", r.id, field, i)] = p
    for ln in spec.lines:
        for i, p in enumerate(ln.path):
            found[("line", ln.id, "path", i)] = p
    for c in spec.crossings:
        if c.path is None:
            found[("crossing", c.id, "at", 0)] = c.at
        for i, p in enumerate(c.path or []):
            found[("crossing", c.id, "path", i)] = p
    return found


def test_move_named_point(tmp_path):
    text = _read(THORNWALD)
    before = _all_points(schema.load_spec(_write(tmp_path / "a.yaml", text)))
    moved = _edited(text, "lea_crossroads: [2366, 1906]", "lea_crossroads: [2400, 1950]")
    after = _all_points(schema.load_spec(_write(tmp_path / "b.yaml", moved)))
    assert set(before) == set(after)
    changed = sorted(k for k in before if before[k] != after[k])
    assert changed == [("line", "b_track", "path", 3), ("line", "kings_road", "path", 11),
                       ("line", "lea_path", "path", 0), ("line", "lodge_path", "path", 6)]
    for k in changed:
        assert after[k] == (2400.0, 1950.0)


def test_thornwald_states_base_b_once():
    assert _read(THORNWALD).count("[2050, 2560]") == 1


def _literal_form(raw):
    """The raw spec with every reference in an accepted place replaced by its target's coordinates; `points` removed."""
    targets = {"base_" + str(t): v["base"] for t, v in raw["teams"].items()}
    targets.update(raw.get("points") or {})
    targets.update({p["id"]: p["at"] for p in raw.get("pois") or []})

    def lit(v):
        return list(targets[v]) if isinstance(v, str) else v

    for r in raw.get("regions") or []:
        if "at" in r:
            r["at"] = lit(r["at"])
        for key in ("points", "path"):
            if key in r:
                r[key] = [lit(p) for p in r[key]]
    for ln in raw.get("lines") or []:
        ln["path"] = [lit(p) for p in ln["path"]]
    for c in raw.get("crossings") or []:
        if "at" in c:
            c["at"] = lit(c["at"])
        if "path" in c:
            c["path"] = [lit(p) for p in c["path"]]
    raw.pop("points", None)
    return raw


def _signature(spec):
    return {
        "regions": [(r.id, r.at, r.points, r.path, r.label_at) for r in spec.regions],
        "lines": [(ln.id, ln.path, ln.label_at) for ln in spec.lines],
        "crossings": [(c.id, c.at, c.path) for c in spec.crossings],
        "pois": [(p.id, p.at) for p in spec.all_pois()],
        "scatter": [(s.id, s.within) for s in spec.scatter],
    }


@pytest.mark.parametrize("name,spec_rel", regress.discover_maps(), ids=[m[0] for m in regress.discover_maps()])
def test_migrated_specs_resolve_to_literals(name, spec_rel, tmp_path):
    path = os.path.join(regress.REPO_ROOT, spec_rel)
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.load(f, Loader=schema._SpecLoader)
    literal = _write(tmp_path / "literal.yaml", yaml.safe_dump(_literal_form(raw), sort_keys=False))
    assert _signature(schema.load_spec(path)) == _signature(schema.load_spec(literal))


def test_references_build_identical_to_literals(tmp_path):
    outs = {}
    for label, text in (("refs", BUILD_REFS), ("lit", BUILD_LITERAL)):
        spec_path = _write(tmp_path / ("%s.yaml" % label), text)
        outs[label] = build.build(spec_path, str(tmp_path / label), only="export", log=lambda s: None)
    refs, lit = regress.hash_tree(outs["refs"]), regress.hash_tree(outs["lit"])
    assert sorted(refs) == sorted(lit)
    assert [k for k in refs if refs[k] != lit[k]] == ["export/meta.json"]
    ma, mb = (_load_json(outs[k], "export/meta.json") for k in ("refs", "lit"))
    assert ma.pop("spec_sha256") != mb.pop("spec_sha256")
    assert ma == mb


def test_unknown_reference_cli(tmp_path, capsys):
    text = _edited(_read(THORNWALD), "path: [base_B, [2150, 2300]", "path: [base_b, [2150, 2300]")
    path = _write(tmp_path / "thornwald.yaml", text)
    assert mapgen.__main__.main(["validate", path]) == 1
    out = capsys.readouterr().out
    assert "lines[2].path[0]: unknown reference 'base_b' (%s)" % T in out.splitlines()[1]


def test_reference_in_excluded_place_names_accepted_places(tmp_path, capsys):
    text = BASE + "points:\n  j: [500, 500]\nlines:\n  - {id: road, type: road, path: [base_A, j], label_at: j}\n"
    path = _write(tmp_path / "a.yaml", text)
    assert mapgen.__main__.main(["validate", path]) == 1
    assert PL in capsys.readouterr().out
