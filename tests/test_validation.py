"""Spec validation errors: expected messages and exit code 1, in-process."""
import pytest

import mapgen.__main__
import mapgen.build
import mapgen.schema

BASE = """name: tiny
size: [1000, 1000]
seed: 1
teams:
  A: {base: [100, 100]}
  B: {base: [900, 900]}
"""

ALLOWED = ("name, brief, size, seed, terrain, teams, regions, lines, crossings, pois, scatter, "
           "analysis, output, auto_crossings")
UNKNOWN_COLOUR = "colour: unknown key (allowed: %s)" % ALLOWED
SEED_ERR = "seed: expected an integer, got 'abc'"


class P(str):
    """Expected line matched as a prefix."""


def _case(cid, text, expected):
    return pytest.param(text, expected, id=cid)


def _lines(*ls):
    return "\n".join(ls) + "\n"


CASES = [
    _case("not_mapping", _lines("- a", "- b"), [": expected a mapping, got list"]),
    _case("empty_file", "", [": expected a mapping, got NoneType"]),
    _case("yaml_syntax", _lines("name: tiny", "size: [1000, 1000", "teams: {}"),
          [P("<yaml>: while parsing a flow sequence")]),
    _case("unknown_top_key", BASE + "colour: red\n", [UNKNOWN_COLOUR]),
    _case("missing_teams", _lines("name: tiny", "size: [1000, 1000]"), ["teams: missing required key"]),
    _case("bad_name", BASE.replace("name: tiny", "name: Tiny-Map"),
          ["name: must match [a-z0-9_]+ (got 'Tiny-Map')"]),
    _case("size_too_small", BASE.replace("size: [1000, 1000]", "size: [100, 1000]"),
          ["size: each dimension must be 200..20000 metres (got [100, 1000])"]),
    _case("size_not_pair", BASE.replace("size: [1000, 1000]", "size: 1000"),
          ["size: expected [x, y] of two numbers, got 1000"]),
    _case("seed_not_int", BASE.replace("seed: 1", "seed: abc"), [SEED_ERR]),
    _case("auto_crossings_not_bool", BASE + "auto_crossings: yes\n",
          ["auto_crossings: expected true or false, got 'yes'"]),
    _case("one_team", _lines("name: tiny", "size: [1000, 1000]", "teams:", "  A: {base: [100, 100]}"),
          ["teams: needs 2 or 3 teams (got 1)"]),
    _case("base_outside_map", BASE.replace("[900, 900]", "[1500, 900]"),
          ["teams.B.base: point [1500, 900] is outside the map [0..1000.0, 0..1000.0]"]),
    _case("unknown_region_type",
          BASE + _lines("regions:", "  - {type: lava, shape: blob, at: [500, 500], r: 100}"),
          [P("regions[0].type: unknown region type 'lava' (known: ")]),
    _case("unknown_shape",
          BASE + _lines("regions:", "  - {type: forest, shape: circle, at: [500, 500], r: 100}"),
          ["regions[0].shape: unknown shape 'circle' (known: blob, poly, band)"]),
    _case("blob_missing_r",
          BASE + _lines("regions:", "  - {type: forest, shape: blob, at: [500, 500]}"),
          ["regions[0].r: missing required key"]),
    _case("poly_two_points",
          BASE + _lines("regions:", "  - {type: forest, shape: poly, points: [[100, 100], [200, 200]]}"),
          ["regions[0].points: polygon needs at least 3 points (got 2)"]),
    _case("fragment_out_of_range",
          BASE + _lines("regions:", "  - {type: forest, shape: blob, at: [500, 500], r: 100, fragment: 1.5}"),
          ["regions[0].fragment: must be in [0.0, 1.0] (got 1.5)"]),
    _case("join_unknown_line",
          BASE + _lines("lines:", "  - {type: river, path: [[0, 500], [1000, 500]], join: nowhere}"),
          ["lines[0].join: no line with id 'nowhere'"]),
    _case("disc_crossing_with_width",
          BASE + _lines("crossings:", "  - {type: bridge, at: [500, 500], width: 20}"),
          ["crossings[0].width: `width` needs `path` (a disc crossing takes `at` and `r`)"]),
    _case("duplicate_poi_id",
          BASE + _lines("pois:", "  - {id: a, type: capture, at: [500, 500]}",
                        "  - {id: a, type: capture, at: [600, 500]}"),
          ["pois[1].id: duplicate POI id 'a' (first used at pois[0])"]),
    _case("poi_unknown_team",
          BASE + _lines("pois:", "  - {id: a, type: capture, at: [500, 500], team: C}"),
          ["pois[0].team: unknown team 'C' (teams: A, B)"]),
    _case("poi_reserved_base_id",
          BASE + _lines("pois:", "  - {id: base_A, type: capture, at: [500, 500]}"),
          ["pois[0].id: 'base_A' is reserved for the implicit base of team A"]),
    _case("heightmap_res_not_pow2", BASE + "output: {heightmap_res: 1000}\n",
          ["output.heightmap_res: must be 2^n+1, e.g. 513, 1025, 2049 (got 1000)"]),
    _case("two_errors", BASE.replace("seed: 1", "seed: abc") + "colour: red\n",
          [UNKNOWN_COLOUR, SEED_ERR]),
]


def _write(tmp_path, text):
    path = tmp_path / "spec.yaml"
    with open(str(path), "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


@pytest.mark.parametrize("text,expected", CASES)
def test_invalid_spec(text, expected, tmp_path, capsys):
    path = _write(tmp_path, text)
    rc = mapgen.__main__.main(["validate", str(path)])
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert rc == 1
    assert lines[0] == "%d error(s) in %s:" % (len(expected), path)
    # Every error is one line (schema.py joins a YAML syntax error's lines with spaces), so the
    # count is exact for every case and the zip below cannot truncate.
    assert len(lines) == len(expected) + 1, out
    for line, exp in zip(lines[1:], expected):
        if isinstance(exp, P):
            assert line.startswith("  " + str(exp)), line
        else:
            assert line == "  " + exp


def test_valid_base_spec(tmp_path, capsys):
    path = _write(tmp_path, BASE)
    rc = mapgen.__main__.main(["validate", str(path)])
    assert rc == 0
    assert capsys.readouterr().out == "OK: tiny (1000x1000 m, 2 teams, 0 regions, 0 lines, 0 crossings, 0 pois)\n"


def test_load_spec_raises_spec_error(tmp_path):
    path = _write(tmp_path, BASE.replace("seed: 1", "seed: abc") + "colour: red\n")
    with pytest.raises(mapgen.schema.SpecError) as ei:
        mapgen.schema.load_spec(str(path))
    assert ei.value.errors == [UNKNOWN_COLOUR, SEED_ERR]


def test_invalid_build_writes_nothing(tmp_path):
    path = _write(tmp_path, BASE.replace("seed: 1", "seed: abc"))
    out = tmp_path / "out"
    with pytest.raises(mapgen.schema.SpecError):
        mapgen.build.build(str(path), str(out))
    assert not out.exists()
