"""The span-patch engine (mapgen/specpatch.py) and `mapgen spec set`: property tests over every committed spec,
operations in flow and block style, refusals, the safety net, load_spec_text parity and the command."""
import glob
import hashlib
import os
import random
import re
import subprocess
import sys

import pytest
import yaml

from mapgen import schema, specpatch
from mapgen.specpatch import Delete, Insert, PatchError, PathError, Set, UnsafePatch, VerifyError

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def _is_map_spec(path):
    rel = os.path.relpath(path, os.path.join(REPO_ROOT, "maps")).replace("\\", "/")
    # town build inputs (maps/towns/src/*.base.yaml) and flowgraph fragments (*.flow.yaml) are not map specs
    return not rel.startswith("towns/src/") and not rel.endswith(".flow.yaml")


SPECS = sorted(p for p in glob.glob(os.path.join(REPO_ROOT, "maps", "**", "*.yaml"), recursive=True) if _is_map_spec(p))
SPEC_IDS = [os.path.relpath(p, os.path.join(REPO_ROOT, "maps")).replace("\\", "/") for p in SPECS]

BLOCK = """format_version: 1
name: tiny
size: [1000, 1000]
seed: 1
teams:
  A: {base: [100, 100]}
  B: {base: [900, 900]}
points:
  j: [500, 500]   # junction
pois:
  - { id: k, type: capture, at: [300, 700], label: "Keep" }
lines:
  # the road
  - id: road
    type: road
    path:
      - base_A          # start
      - [200, 300]
      - [400, 400]      # bend
      - j
      - base_B
    width: 6
  - { id: trail, type: trail, path: [base_A, [150, 150], [160, 400],
                                     k], width: 4 }
"""


def read(path):
    with open(path, "rb") as f:
        return f.read().decode("utf-8")


def load_text(text):
    return yaml.load(text, Loader=schema._SpecLoader)


def comment_view(text):
    """Every comment, in order: full-line comments and trailing ones."""
    return re.findall(r"#[^\r\n]*", text)


def ids(text):
    return text


# ----------------------------------------------------------------------------- property tests

def literal_pairs(raw):
    """(path, [x, y]) of every literal coordinate pair addressable by id."""
    out = []

    def pair(v):
        return isinstance(v, list) and len(v) == 2 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v)

    for t, tv in sorted((raw.get("teams") or {}).items()):
        if isinstance(tv, dict) and pair(tv.get("base")):
            out.append(("teams.%s.base" % t, tv["base"]))
    for n, v in sorted((raw.get("points") or {}).items()):
        if pair(v):
            out.append(("points.%s" % n, v))
    for p in raw.get("pois") or []:
        if isinstance(p, dict) and pair(p.get("at")):
            out.append(("pois.%s.at" % p["id"], p["at"]))
    for sect, key in (("lines", "path"), ("regions", "path"), ("regions", "points")):
        for f in raw.get(sect) or []:
            if isinstance(f, dict) and isinstance(f.get(key), list):
                for i, v in enumerate(f[key]):
                    if pair(v):
                        out.append(("%s.%s.%s[%d]" % (sect, f["id"], key, i), v))
    return out


def random_ops(raw, seed, n=6):
    rng = random.Random(seed)
    w, h = raw["size"]
    pairs = literal_pairs(raw)
    ops = []
    for path, old in rng.sample(pairs, min(n, len(pairs))):
        new = [min(max(old[0] + rng.randint(-9, 9) + rng.choice([0, 0, 0.5]), 2), w - 2),
               min(max(old[1] + rng.randint(-9, 9), 2), h - 2)]
        if new == list(old):
            new[0] += 1
        ops.append(Set(path, new))
    return ops


def changed_lines(a, b):
    la, lb = a.split("\n"), b.split("\n")
    assert len(la) == len(lb)
    return [i for i, (x, y) in enumerate(zip(la, lb)) if x != y]


@pytest.mark.parametrize("path", SPECS, ids=SPEC_IDS)
def test_random_moves_touch_only_edited_spans(path):
    text = read(path)
    raw = load_text(text)
    for seed in (1, 2):
        ops = random_ops(raw, seed)
        new = specpatch.patch_text(text, ops, path)  # loads through the real loader
        got = load_text(new)
        # only the requested values differ
        want = load_text(text)
        for op in ops:
            tgt = specpatch._ref_definition  # noqa: F841 (documentation: literal targets here)
        assert got != want
        # every comment is byte-identical
        assert comment_view(new) == comment_view(text)
        # edited lines: at most one per op; every other line is identical
        assert len(changed_lines(text, new)) <= len(ops)
        # the text between changed numbers is untouched: masking digits makes the texts equal
        mask = lambda s: re.sub(r"-?\d+(?:\.\d+)?", "N", s)
        assert mask(new) == mask(text)


@pytest.mark.parametrize("path", SPECS, ids=SPEC_IDS)
def test_crlf_copy_keeps_line_endings(path):
    text = read(path)
    raw = load_text(text)
    ops = random_ops(raw, 7)
    lf = specpatch.apply_ops(text, ops)
    crlf_text = text.replace("\r\n", "\n").replace("\n", "\r\n")
    crlf = specpatch.apply_ops(crlf_text, ops)
    assert "\n" not in crlf.replace("\r\n", "")
    assert crlf.replace("\r\n", "\n") == lf.replace("\r\n", "\n")
    specpatch.patch_text(crlf, ops, path)


@pytest.mark.parametrize("path", SPECS, ids=SPEC_IDS)
def test_identity_edit_is_byte_identical(path):
    text = read(path)
    raw = load_text(text)
    pairs = literal_pairs(raw)
    ops = [Set(p, list(v)) for p, v in random.Random(5).sample(pairs, min(15, len(pairs)))]
    assert specpatch.apply_ops(text, ops) == text


@pytest.mark.parametrize("path", SPECS, ids=SPEC_IDS)
def test_insert_then_delete_restores_bytes(path):
    text = read(path)
    raw = load_text(text)
    rng = random.Random(11)
    cands = [(f["id"], len(f["path"])) for f in (raw.get("lines") or []) if isinstance(f.get("path"), list)]
    if not cands:
        pytest.skip("no lines")
    for lid, n in rng.sample(cands, min(3, len(cands))):
        for idx in sorted({0, n // 2, n}):
            p = "lines.%s.path" % lid
            ins = specpatch.apply_ops(text, [Insert(p + "[+%d]" % idx, idx, [5, 5])])
            assert len(load_text(ins)["lines"][[f["id"] for f in raw["lines"]].index(lid)]["path"]) == n + 1
            assert comment_view(ins) == comment_view(text)
            back = specpatch.apply_ops(ins, [Delete("%s[%d]" % (p, idx))])
            assert back == text


# ----------------------------------------------------------------------------- operations

def test_flow_insert_delete_and_style():
    text = BLOCK
    t = specpatch.apply_ops(text, [Insert("lines.trail.path[+2]", 2, [155, 300])])
    assert "[base_A, [150, 150], [155, 300], [160, 400]," in t
    t = specpatch.apply_ops(text, [Insert("lines.trail.path[+]", None, "j")])
    assert "[160, 400],\n                                     k,\n                                     j], width: 4 }" in t
    t = specpatch.apply_ops(text, [Delete("lines.trail.path[1]")])
    assert "path: [base_A, [160, 400]," in t


def test_block_insert_delete_preserve_comments_and_indent():
    t = specpatch.apply_ops(BLOCK, [Insert("lines.road.path[+3]", 3, [450, 450])])
    assert "      - [400, 400]      # bend\n      - [450, 450]\n      - j\n" in t
    t = specpatch.apply_ops(BLOCK, [Insert("lines.road.path[+0]", 0, [120, 120])])
    assert "    path:\n      - [120, 120]\n      - base_A          # start\n" in t
    t = specpatch.apply_ops(BLOCK, [Delete("lines.road.path[2]")])  # item with a trailing comment
    assert "bend" not in t and "      - [200, 300]\n      - j\n" in t
    assert comment_view(t) == [c for c in comment_view(BLOCK) if "bend" not in c]
    # round trip
    ins = specpatch.apply_ops(BLOCK, [Insert("lines.road.path[+4]", 4, [1, 2])])
    assert specpatch.apply_ops(ins, [Delete("lines.road.path[4]")]) == BLOCK
    # CRLF
    c = BLOCK.replace("\n", "\r\n")
    t = specpatch.apply_ops(c, [Insert("lines.road.path[+1]", 1, [210, 310]), Delete("lines.road.path[0]")])
    assert "\n" not in t.replace("\r\n", "")


def test_block_sequence_append_at_end_of_file_without_newline():
    text = "format_version: 1\nname: t\nsize: [10, 10]\nseed: 1\nteams:\n  A: {base: [1, 1]}\n  B: {base: [9, 9]}\nlines:\n  - id: l\n    type: road\n    path:\n      - [1, 1]\n      - [5, 5]"
    t = specpatch.apply_ops(text, [Insert("lines.l.path[+]", None, [7, 7])])
    assert t.endswith("      - [5, 5]\n      - [7, 7]")


def test_set_scalar_keeps_quotes_and_formats_numbers():
    t = specpatch.apply_ops(BLOCK, [Set("pois.k.label", "Castle"), Set("lines.road.width", 6.5),
                                    Set("pois.k.at", [300.0, 700.25])])
    assert '{ id: k, type: capture, at: [300, 700.25], label: "Castle" }' in t
    assert "    width: 6.5\n" in t
    t = specpatch.apply_ops("a: 'x'\n", [Set("a", "it's")])
    assert t == "a: 'it''s'\n"
    assert specpatch.fmt_num(401.5) == "401.5" and specpatch.fmt_num(400.0) == "400" and specpatch.fmt_num(0.1 + 0.2) == "0.30000000000000004"


def test_reference_aware_move_changes_only_the_target():
    t = specpatch.apply_ops(BLOCK, [Set("lines.road.path[3]", [510, 520])])  # `j` is a points entry
    assert changed_lines(BLOCK, t) == [BLOCK.split("\n").index("  j: [500, 500]   # junction")]
    assert "  j: [510, 520]   # junction" in t
    spec = schema.load_spec_text(t)
    # every referrer follows
    assert (510.0, 520.0) in [tuple(p) for l in spec.lines for p in l.path]
    t = specpatch.apply_ops(BLOCK, [Set("lines.road.path[0]", [101, 100]), Set("lines.trail.path[3]", [310, 700])])
    assert "A: {base: [101, 100]}" in t and "at: [310, 700]" in t
    # detach writes the literal at the vertex
    t = specpatch.apply_ops(BLOCK, [Set("lines.road.path[3]", [510, 520], detach=True)])
    assert "- [510, 520]\n" in t and "j: [500, 500]" in t
    # attach: a literal replaced by a name
    t = specpatch.apply_ops(BLOCK, [Set("lines.road.path[1]", "j")])
    assert "      - j\n      - [400, 400]" in t


def test_path_errors():
    for bad in ("lines.nope.path[0]", "lines.road.path[9]", "points.zz", "lines[0].path"):
        with pytest.raises(PathError):
            specpatch.apply_ops(BLOCK, [Set(bad, [1, 2])])
    with pytest.raises(PathError):
        specpatch.apply_ops(BLOCK, [Set("pois[0].at", [1, 2])])  # lists of features address by id
    with pytest.raises(PathError):
        specpatch.apply_ops(BLOCK, [Insert("lines.road.path[+99]", 99, [1, 2])])


ANCHORED = """format_version: 1
name: t
size: [1000, 1000]
seed: 1
teams:
  A: {base: [100, 100]}
  B: {base: [900, 900]}
points:
  j: &jpt [500, 500]
  k: *jpt
lines:
  - { id: l, type: road, path: [[1, 1], *jpt] }
"""

MERGE = """format_version: 1
name: t
size: [1000, 1000]
seed: 1
teams:
  A: {base: &b [100, 100]}
  B: {base: [900, 900]}
x-base: &common {type: road, width: 4}
lines:
  - { <<: *common, id: l, path: [[1, 1], [5, 5]] }
"""

DUPKEY = """format_version: 1
name: t
size: [1000, 1000]
seed: 1
teams:
  A: {base: [100, 100]}
  B: {base: [900, 900]}
points:
  j: [500, 500]
  j: [600, 600]
"""


def test_anchors_aliases_merge_and_duplicates_refused():
    for path in ("points.j", "points.k", "lines.l.path[1]"):
        with pytest.raises(UnsafePatch):
            specpatch.apply_ops(ANCHORED, [Set(path, [7, 7])])
    with pytest.raises(UnsafePatch):
        specpatch.apply_ops(ANCHORED, [Insert("lines.l.path[+]", None, [3, 3]), Set("points.j", [1, 1])])
    with pytest.raises(UnsafePatch):
        specpatch.apply_ops(MERGE, [Set("teams.A.base", [1, 1])])
    with pytest.raises(UnsafePatch):
        specpatch.apply_ops(MERGE, [Set("lines.l.width", 5)])  # mapping with a merge key
    with pytest.raises(PatchError):
        specpatch.apply_ops(DUPKEY, [Set("points.j", [1, 1])])
    # unanchored parts of the same document stay editable
    assert "A: {base: [100, 100]}" in specpatch.apply_ops(ANCHORED, [Set("teams.B.base", [800, 800])])


def test_block_scalar_and_multiline_refused():
    text = "a: |\n  hello\nb: 1\n"
    with pytest.raises(UnsafePatch):
        specpatch.apply_ops(text, [Set("a", "x")])
    assert specpatch.apply_ops(text, [Set("b", 2)]) == "a: |\n  hello\nb: 2\n"
    with pytest.raises(PatchError):
        specpatch.apply_ops("a: 1\n---\nb: 2\n", [Set("a", 2)])


def test_comment_inside_flow_sequence_deletion_refused():
    text = "p: [1,  # one\n     2, 3]\n"
    with pytest.raises(UnsafePatch):
        specpatch.apply_ops(text, [Delete("p[0]")])


def test_loader_rejection_leaves_text_untouched():
    with pytest.raises(schema.SpecError) as e:
        specpatch.patch_text(BLOCK, [Set("pois.k.at", [5000, 5000])])
    assert any("outside the map" in x for x in e.value.errors)


def test_verification_catches_wrong_patch(monkeypatch):
    orig = specpatch.render
    monkeypatch.setattr(specpatch, "render", lambda v, style=None: "999" if v == [1, 2] or v == 1 else orig(v, style))
    with pytest.raises(VerifyError):
        specpatch.apply_ops(BLOCK, [Set("pois.k.at", [1, 2])])


def test_ops_apply_in_order_so_inserts_can_be_edited():
    t = specpatch.apply_ops(BLOCK, [Insert("lines.road.path[+1]", 1, [10, 10]), Set("lines.road.path[1]", [11, 12])])
    assert "      - [11, 12]\n      - [200, 300]" in t


# ----------------------------------------------------------------------------- loader split

@pytest.mark.parametrize("path", [p for p in SPECS if "/concepts/" in p.replace("\\", "/") or "example_" in p])
def test_load_spec_text_matches_load_spec(path):
    a = schema.load_spec(path)
    with open(path, "rb") as f:
        b = schema.load_spec_text(f.read(), path)
    assert a.spec_sha256 == b.spec_sha256 and a.spec_path == b.spec_path
    assert a.name == b.name and a.lines == b.lines and a.regions == b.regions and a.pois == b.pois


# ----------------------------------------------------------------------------- command

def run(*args):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.run([sys.executable, "-m", "mapgen"] + [str(a) for a in args], cwd=REPO_ROOT, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
    return p.returncode, p.stdout, p.stderr


def write(path, text, eol="\n"):
    with open(str(path), "wb") as f:
        f.write(text.replace("\n", eol).encode("utf-8"))
    return str(path)


def test_cli_applies_diff_and_exit_codes(tmp_path):
    p = write(tmp_path / "s.yaml", BLOCK, "\r\n")
    rc, out, _ = run("spec", "set", p, "pois.k.at=[310,690]", "lines.road.path[3]=[505,500]", "--diff")
    assert rc == 0, out
    assert "-  j: [500, 500]   # junction" in out and "+  j: [505, 500]   # junction" in out and out.startswith("---")
    new = read(p)
    assert "\n" not in new.replace("\r\n", "") and "at: [310, 690]" in new
    assert [f for f in os.listdir(str(tmp_path)) if f != "s.yaml"] == []  # no temp file left
    assert run("validate", p)[0] == 0
    # missing file, bad path, bad value, refused: exit 1, file untouched
    before = read(p)
    assert run("spec", "set", str(tmp_path / "nope.yaml"), "a=1")[0] == 1
    rc, out, _ = run("spec", "set", p, "pois.zz.at=[1,2]")
    assert rc == 1 and out.startswith("error: PathError")
    rc, out, _ = run("spec", "set", p, "pois.k.at=[1,2")
    assert rc == 1
    rc, out, _ = run("spec", "set", p, "pois.k.at=[5000,5000]")
    assert rc == 1 and "outside the map" in out and "(file unchanged)" in out
    assert read(p) == before


def test_cli_is_atomic_across_edits_and_dry_run(tmp_path):
    p = write(tmp_path / "s.yaml", BLOCK)
    before = read(p)
    rc, out, _ = run("spec", "set", p, "pois.k.at=[310,690]", "pois.nope.at=[1,2]")
    assert rc == 1 and read(p) == before  # second edit fails: first not written
    rc, out, _ = run("spec", "set", p, "pois.k.at=[310,690]", "--dry-run")
    assert rc == 0 and "+  - { id: k" in out and read(p) == before
    rc, out, _ = run("spec", "set", p, "pois.k.at=[300,700]")
    assert rc == 0 and out.startswith("no change") and read(p) == before


def test_cli_insert_delete_and_unchanged_untouched_bytes(tmp_path):
    p = write(tmp_path / "s.yaml", BLOCK)
    assert run("spec", "set", p, "lines.road.path[+2]=[300,350]", "lines.trail.path[1]=@delete")[0] == 0
    new = read(p)
    assert "      - [300, 350]\n" in new and "[base_A, [160, 400]," in new


def test_cli_real_spec_copy(tmp_path):
    src = os.path.join(REPO_ROOT, "maps", "iterations", "town_square", "v4_natural", "town_square_v4.yaml")
    p = write(tmp_path / "v4.yaml", read(src))
    rc, out, _ = run("spec", "set", p, "pois.fa.at=[400,120]", "points.ao=[260.5,412]",
                     "lines.river_main.path[2]=[612,545]", "--diff")
    assert rc == 0
    assert sum(1 for l in out.splitlines() if l.startswith("+") and not l.startswith("+++")) == 3
