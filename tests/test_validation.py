"""Spec validation errors: expected messages and exit code 1, in-process."""
import pytest

import mapgen.__main__
import mapgen.build
import mapgen.schema

BASE = """format_version: 1
name: tiny
size: [1000, 1000]
seed: 1
teams:
  A: {base: [100, 100]}
  B: {base: [900, 900]}
"""

ALLOWED = ("format_version, name, brief, size, seed, terrain, teams, points, regions, lines, crossings, pois, "
           "scatter, analysis, output, auto_crossings")
UNKNOWN_COLOUR = "colour: unknown key (allowed: %s)" % ALLOWED
IDMSG = "%s.id: must match [a-z][a-z0-9_]* and be at most 64 characters (got %s)"
VERMSG = "format_version: unsupported value %s (this mapgen reads format versions: 1)"
SEEDMSG = "regions[0].seed: expected an integer in [0, 4294967295], got %s"
SEED_ERR = "seed: expected an integer, got 'abc'"
T = "a point reference names a POI, a spawn base or a `points` entry"
PL = "point references are accepted only in region at/points/path, line path and crossing at/path"
STR_PT = "%s: expected a point [x, y] of two numbers, got %r (" + PL + ")"


REGION_SEED = "regions:\n  - {id: r0, seed: %s, type: forest, shape: blob, at: [500, 500], r: 100}\n"


class P(str):
    """Expected line matched as a prefix."""


def _case(cid, text, expected):
    return pytest.param(text, expected, id=cid)


def _lines(*ls):
    return "\n".join(ls) + "\n"


CASES = [
    _case("not_mapping", _lines("- a", "- b"), [": expected a mapping, got list"]),
    _case("empty_file", "", [": expected a mapping, got NoneType"]),
    _case("yaml_syntax", _lines("format_version: 1", "name: tiny", "size: [1000, 1000", "teams: {}"),
          [P("<yaml>: while parsing a flow sequence")]),
    _case("unknown_top_key", BASE + "colour: red\n", [UNKNOWN_COLOUR]),
    _case("missing_teams", _lines("format_version: 1", "name: tiny", "size: [1000, 1000]"),
          ["teams: missing required key"]),
    _case("bad_name", BASE.replace("name: tiny", "name: Tiny-Map"),
          ["name: must match [a-z0-9_]+ (got 'Tiny-Map')"]),
    _case("size_too_small", BASE.replace("size: [1000, 1000]", "size: [100, 1000]"),
          ["size: each dimension must be 200..20000 metres (got [100, 1000])"]),
    _case("size_not_pair", BASE.replace("size: [1000, 1000]", "size: 1000"),
          ["size: expected [x, y] of two numbers, got 1000"]),
    _case("seed_not_int", BASE.replace("seed: 1", "seed: abc"), [SEED_ERR]),
    _case("auto_crossings_not_bool", BASE + "auto_crossings: yes\n",
          ["auto_crossings: expected true or false, got 'yes'"]),
    _case("one_team", _lines("format_version: 1", "name: tiny", "size: [1000, 1000]", "teams:",
                        "  A: {base: [100, 100]}"),
          ["teams: needs 2 or 3 teams (got 1)"]),
    _case("base_outside_map", BASE.replace("[900, 900]", "[1500, 900]"),
          ["teams.B.base: point [1500, 900] is outside the map [0..1000.0, 0..1000.0]"]),
    _case("unknown_region_type",
          BASE + _lines("regions:", "  - {id: r0, type: lava, shape: blob, at: [500, 500], r: 100}"),
          [P("regions[0].type: unknown region type 'lava' (known: ")]),
    _case("unknown_shape",
          BASE + _lines("regions:", "  - {id: r0, type: forest, shape: circle, at: [500, 500], r: 100}"),
          ["regions[0].shape: unknown shape 'circle' (known: blob, poly, band)"]),
    _case("blob_missing_r",
          BASE + _lines("regions:", "  - {id: r0, type: forest, shape: blob, at: [500, 500]}"),
          ["regions[0].r: missing required key"]),
    _case("poly_two_points",
          BASE + _lines("regions:", "  - {id: r0, type: forest, shape: poly, points: [[100, 100], [200, 200]]}"),
          ["regions[0].points: polygon needs at least 3 points (got 2)"]),
    _case("fragment_out_of_range",
          BASE + _lines("regions:", "  - {id: r0, type: forest, shape: blob, at: [500, 500], r: 100, fragment: 1.5}"),
          ["regions[0].fragment: must be in [0.0, 1.0] (got 1.5)"]),
    _case("join_unknown_line",
          BASE + _lines("lines:", "  - {id: l0, type: river, path: [[0, 500], [1000, 500]], join: nowhere}"),
          ["lines[0].join: no line with id 'nowhere'"]),
    _case("disc_crossing_with_width",
          BASE + _lines("crossings:", "  - {id: c0, type: bridge, at: [500, 500], width: 20}"),
          ["crossings[0].width: `width` needs `path` (a disc crossing takes `at` and `r`)"]),
    _case("duplicate_poi_id",
          BASE + _lines("pois:", "  - {id: a, type: capture, at: [500, 500]}",
                        "  - {id: a, type: capture, at: [600, 500]}"),
          ["pois[1].id: duplicate id 'a' (first used at pois[0].id)"]),
    _case("poi_unknown_team",
          BASE + _lines("pois:", "  - {id: a, type: capture, at: [500, 500], team: C}"),
          ["pois[0].team: unknown team 'C' (teams: A, B)"]),
    _case("poi_reserved_base_id",
          BASE.replace("A:", "a:").replace("B:", "b:")
          + _lines("pois:", "  - {id: base_a, type: capture, at: [500, 500]}"),
          ["pois[0].id: 'base_a' is reserved for the implicit base of team a"]),
    _case("poi_id_charset",
          BASE + _lines("pois:", "  - {id: base_A, type: capture, at: [500, 500]}"),
          [IDMSG % ("pois[0]", "'base_A'")]),
    _case("missing_format_version", BASE.split("\n", 1)[1],
          ["format_version: missing required key (this mapgen reads format versions: 1)"]),
    _case("unversioned_without_ids",
          BASE.split("\n", 1)[1] + _lines("regions:", "  - {type: forest, shape: blob, at: [500, 500], r: 100}"),
          ["format_version: missing required key (this mapgen reads format versions: 1)",
           "regions[0].id: missing required key"]),
    _case("unsupported_format_version", BASE.replace("format_version: 1", "format_version: 2") + "colour: red\n",
          [VERMSG % "2"]),
    _case("format_version_string", BASE.replace("format_version: 1", "format_version: '1'"),
          [VERMSG % "'1'"]),
    _case("format_version_float", BASE.replace("format_version: 1", "format_version: 1.0"),
          [VERMSG % "1.0"]),
    _case("format_version_bool", BASE.replace("format_version: 1", "format_version: true"),
          [VERMSG % "True"]),
    _case("id_trailing_newline",
          BASE + _lines("regions:", '  - {id: "wood\\n", type: forest, shape: blob, at: [500, 500], r: 100}'),
          [IDMSG % ("regions[0]", repr("wood\n"))]),
    _case("name_trailing_newline", BASE.replace("name: tiny", 'name: "tiny\\n"'),
          ["name: must match [a-z0-9_]+ (got 'tiny\\n')"]),
    _case("region_without_id",
          BASE + _lines("regions:", "  - {type: forest, shape: blob, at: [500, 500], r: 100}"),
          ["regions[0].id: missing required key"]),
    _case("line_without_id",
          BASE + _lines("lines:", "  - {type: river, path: [[0, 500], [1000, 500]]}"),
          ["lines[0].id: missing required key"]),
    _case("scatter_without_id",
          BASE + _lines("scatter:", "  - {type: forest, coverage: 0.5, scale: 200}"),
          ["scatter[0].id: missing required key"]),
    _case("crossing_without_id",
          BASE + _lines("crossings:", "  - {type: bridge, at: [500, 500], r: 20}"),
          ["crossings[0].id: missing required key"]),
    _case("bad_id",
          BASE + _lines("regions:", "  - {id: Big-Wood, type: forest, shape: blob, at: [500, 500], r: 100}"),
          [IDMSG % ("regions[0]", "'Big-Wood'")]),
    _case("id_too_long",
          BASE + _lines("regions:", "  - {id: %s, type: forest, shape: blob, at: [500, 500], r: 100}" % ("a" * 65)),
          [IDMSG % ("regions[0]", repr("a" * 65))]),
    _case("duplicate_id_across_kinds",
          BASE + _lines("regions:", "  - {id: wood, type: forest, shape: blob, at: [500, 500], r: 100}",
                        "lines:", "  - {id: wood, type: river, path: [[0, 500], [1000, 500]]}"),
          ["lines[0].id: duplicate id 'wood' (first used at regions[0].id)"]),
    _case("crossing_id_equals_poi_id",
          BASE + _lines("crossings:", "  - {id: eyrie, type: bridge, at: [500, 500], r: 20}",
                        "pois:", "  - {id: eyrie, type: capture, at: [600, 500]}"),
          ["pois[0].id: duplicate id 'eyrie' (first used at crossings[0].id)"]),
    _case("region_id_reserved",
          BASE.replace("A:", "a:").replace("B:", "b:")
          + _lines("regions:", "  - {id: base_a, type: forest, shape: blob, at: [500, 500], r: 100}"),
          ["regions[0].id: 'base_a' is reserved for the implicit base of team a"]),
    _case("seed_negative", BASE + REGION_SEED % "-1", [SEEDMSG % "-1"]),
    _case("seed_float", BASE + REGION_SEED % "1.5", [SEEDMSG % "1.5"]),
    _case("seed_too_big", BASE + REGION_SEED % "4294967296", [SEEDMSG % "4294967296"]),
    _case("seed_bool", BASE + REGION_SEED % "true", [SEEDMSG % "True"]),
    _case("crossing_seed",
          BASE + _lines("crossings:", "  - {id: c0, type: bridge, at: [500, 500], r: 20, seed: 3}"),
          ["crossings[0].seed: unknown key (allowed: type, at, r, path, width, id)"]),
    _case("heightmap_res_not_pow2", BASE + "output: {heightmap_res: 1000}\n",
          ["output.heightmap_res: must be 2^n+1, e.g. 513, 1025, 2049 (got 1000)"]),
    _case("ref_unknown", BASE + _lines("lines:", "  - {id: road, type: road, path: [base_A, base_c]}"),
          ["lines[0].path[1]: unknown reference 'base_c' (%s)" % T]),
    _case("ref_region_id",
          BASE + _lines("regions:", "  - {id: wood, type: forest, shape: blob, at: [500, 500], r: 100}",
                        "lines:", "  - {id: road, type: road, path: [base_A, wood]}"),
          ["lines[0].path[1]: 'wood' is a region id; %s" % T]),
    _case("ref_line_id", BASE + _lines("lines:", "  - {id: road, type: road, path: [base_A, road]}"),
          ["lines[0].path[1]: 'road' is a line id; %s" % T]),
    _case("ref_scatter_id",
          BASE + _lines("scatter:", "  - {id: s0, type: forest, coverage: 0.2}",
                        "lines:", "  - {id: road, type: road, path: [base_A, s0]}"),
          ["lines[0].path[1]: 's0' is a scatter entry id; %s" % T]),
    _case("ref_crossing_id",
          BASE + _lines("crossings:", "  - {id: ford1, type: ford, at: [500, 500]}",
                        "lines:", "  - {id: road, type: road, path: [base_A, ford1]}"),
          ["lines[0].path[1]: 'ford1' is a crossing id; %s" % T]),
    _case("ref_in_poi_at",
          BASE + _lines("points:", "  j: [500, 500]", "pois:", "  - {id: c, type: capture, at: j}"),
          [STR_PT % ("pois[0].at", "j")]),
    _case("ref_in_base", BASE.replace("{base: [900, 900]}", "{base: j}") + _lines("points:", "  j: [500, 500]"),
          [STR_PT % ("teams.B.base", "j")]),
    _case("ref_in_label_at",
          BASE + _lines("points:", "  j: [500, 500]", "lines:",
                        "  - {id: road, type: road, path: [base_A, j], label_at: j}"),
          [STR_PT % ("lines[0].label_at", "j")]),
    _case("ref_in_within",
          BASE + _lines("points:", "  j: [500, 500]", "scatter:",
                        "  - {id: s0, type: forest, coverage: 0.2, within: [j, [0, 0], [0, 900]]}"),
          [STR_PT % ("scatter[0].within[0]", "j")]),
    _case("points_chain", BASE + _lines("points:", "  j: base_A"), [STR_PT % ("points.j", "base_A")]),
    _case("points_not_mapping", BASE + "points: [[1, 2]]\n",
          ["points: expected a mapping of name -> [x, y], got list"]),
    _case("points_bad_name", BASE + _lines("points:", "  Junction: [500, 500]"),
          ["points.Junction: name must match [a-z][a-z0-9_]* and be at most 64 characters (got 'Junction')"]),
    _case("points_outside_map", BASE + _lines("points:", "  j: [1500, 500]"),
          ["points.j: point [1500, 500] is outside the map [0..1000.0, 0..1000.0]"]),
    _case("points_duplicate_poi",
          BASE + _lines("points:", "  j: [500, 500]", "pois:", "  - {id: j, type: capture, at: [400, 400]}"),
          ["pois[0].id: duplicate id 'j' (first used at points.j)"]),
    _case("points_duplicate_region",
          BASE + _lines("points:", "  j: [500, 500]", "regions:",
                        "  - {id: j, type: forest, shape: blob, at: j, r: 50}"),
          ["regions[0].id: duplicate id 'j' (first used at points.j)"]),
    _case("points_reserved_base",
          BASE.replace("A:", "a:").replace("B:", "b:") + _lines("points:", "  base_a: [500, 500]"),
          ["points.base_a: 'base_a' is reserved for the implicit base of team a"]),
    _case("ref_to_invalid_poi",
          BASE + _lines("pois:", "  - {id: c, type: capture, at: [1500, 400]}",
                        "lines:", "  - {id: road, type: road, path: [base_A, c]}"),
          ["pois[0].at: point [1500, 400] is outside the map [0..1000.0, 0..1000.0]"]),
    _case("ref_in_region_label_at",
          BASE + _lines("points:", "  j: [500, 500]", "regions:",
                        "  - {id: r0, type: forest, shape: blob, at: [500, 500], r: 100, label_at: j}"),
          [STR_PT % ("regions[0].label_at", "j")]),
    _case("duplicate_key_points",
          BASE + _lines("points:", "  j: [5, 5]", "  j: [6, 6]"),
          ["<yaml>: line 10: duplicate key 'j'"]),
    _case("duplicate_key_teams",
          BASE + _lines("  A: {base: [200, 200]}"),
          ["<yaml>: line 8: duplicate key 'A'"]),
    _case("duplicate_key_region",
          BASE + _lines("regions:", "  - id: r0", "    type: forest", "    shape: blob", "    at: [500, 500]",
                        "    r: 100", "    r: 50"),
          ["<yaml>: line 14: duplicate key 'r'"]),
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
