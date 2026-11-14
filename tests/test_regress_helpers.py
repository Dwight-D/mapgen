"""Fast unit tests of regress.py (no map builds)."""
import hashlib
import json
import os

import pytest

import regress


def test_discover_maps():
    assert regress.discover_maps() == [
        ("natural", "maps/example_natural.yaml"),
        ("river_crossing", "maps/example_river_crossing.yaml"),
        ("three_team", "maps/example_three_team.yaml"),
        ("ashen_steppe", "maps/concepts/ashen_steppe.yaml"),
        ("gloamfen", "maps/concepts/gloamfen.yaml"),
        ("kingsjaw", "maps/concepts/kingsjaw.yaml"),
        ("saltreach_delta", "maps/concepts/saltreach_delta.yaml"),
        ("thornwald", "maps/concepts/thornwald.yaml"),
    ]


def test_hash_tree(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.txt").write_bytes(b"x")
    (tmp_path / "sub" / "b.bin").write_bytes(b"\x00\x01")
    h = regress.hash_tree(str(tmp_path))
    assert list(h.keys()) == ["a.txt", "sub/b.bin"]
    assert h["a.txt"] == hashlib.sha256(b"x").hexdigest()


def test_compare_identical():
    d = {"a": "0" * 64}
    assert regress.compare("m", d, dict(d)) == []


def test_compare_changed_missing_extra():
    expected = {"a": "0" * 64, "b": "1" * 64}
    actual = {"a": "f" * 64, "c": "2" * 64}
    assert regress.compare("m", expected, actual) == [
        "m: a: changed (baseline 000000000000, build ffffffffffff)",
        "m: b: missing from build",
        "m: c: not in baseline",
    ]


def test_compare_equal_prefix_shows_full_hashes():
    h1 = "abcdef012345" + "0" * 52
    h2 = "abcdef012345" + "1" * 52
    assert regress.compare("m", {"a": h1}, {"a": h2}) == [
        "m: a: changed (baseline %s, build %s)" % (h1, h2)]


def test_write_baseline_replaces_file_without_leftovers(tmp_path, monkeypatch):
    monkeypatch.setattr(regress, "BASELINE_DIR", str(tmp_path))
    files = {"a": "0" * 64}
    path = regress.write_baseline("m", "maps/m.yaml", files)
    assert path == str(tmp_path / "m.json")
    with open(path, "rb") as f:
        assert f.read() == regress.format_baseline("m", "maps/m.yaml", files).encode("ascii")
    regress.write_baseline("m", "maps/m.yaml", {"b": "1" * 64})
    assert [p.name for p in tmp_path.iterdir()] == ["m.json"]


def test_format_failure():
    assert regress.format_failure("m", ["m: a: missing from build"]) == (
        "map 'm' differs from tests/baselines/m.json in 1 file(s):\n"
        "  m: a: missing from build\n"
        "Re-baseline only after the owner approves a diff report: "
        ".venv/Scripts/python tests/rebaseline.py m")


def test_format_baseline_deterministic():
    f1 = {"a": "0" * 64, "b": "1" * 64}
    f2 = {"b": "1" * 64, "a": "0" * 64}
    s1 = regress.format_baseline("m", "maps/m.yaml", f1)
    s2 = regress.format_baseline("m", "maps/m.yaml", f2)
    assert s1 == s2
    assert s1.endswith("}\n")
    assert "\r" not in s1
    assert json.loads(s1) == {"map": "m", "spec": "maps/m.yaml", "files": f1}


def test_committed_baselines_well_formed():
    for name, spec in regress.discover_maps():
        files = regress.load_baseline(name)
        with open(regress.baseline_path(name), "rb") as f:
            raw = f.read()
        assert raw == regress.format_baseline(name, spec, files).encode("ascii"), name
        assert b"\r" not in raw


def test_build_and_hash_rejects_nonempty_dir(tmp_path):
    (tmp_path / "x.txt").write_bytes(b"x")
    with pytest.raises(ValueError):
        regress.build_and_hash("maps/example_natural.yaml", str(tmp_path))
