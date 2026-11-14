"""Command exit codes of build, validate, probe, types and palette, through real subprocesses."""
import os
import subprocess
import sys

import pytest

from test_validation import BASE

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BAD_TEXT = BASE.replace("seed: 1", "seed: abc")


def run(*args, cwd=REPO_ROOT):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.run([sys.executable, "-m", "mapgen"] + [str(a) for a in args], cwd=cwd, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
    return p.returncode, p.stdout, p.stderr


def _w(path, text):
    with open(str(path), "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


@pytest.fixture
def tiny(tmp_path):
    return _w(tmp_path / "tiny.yaml", BASE)


@pytest.fixture
def bad(tmp_path):
    return _w(tmp_path / "bad.yaml", BAD_TEXT)


@pytest.fixture
def missing(tmp_path):
    return tmp_path / "missing.yaml"


def _files(root):
    return [os.path.join(d, f) for d, _, fs in os.walk(str(root)) for f in fs]


def test_validate_ok():
    rc, out, _ = run("validate", "maps/example_river_crossing.yaml")
    assert rc == 0
    assert out.startswith("OK: river_crossing (")


def test_validate_missing(missing):
    rc, out, _ = run("validate", missing)
    assert rc == 1
    assert out == "error: file not found: %s\n" % missing


def test_validate_invalid(bad):
    rc, out, _ = run("validate", bad)
    assert rc == 1
    assert out.splitlines()[0] == "1 error(s) in %s:" % bad


def test_build_ok(tiny, tmp_path):
    out = tmp_path / "out"
    rc, _, _ = run("build", tiny, "--out", out)
    assert rc == 0
    assert (out / "tiny" / "report.json").is_file()
    assert (out / "tiny" / "export" / "meta.json").is_file()
    assert len(_files(out / "tiny")) == 21


def test_build_only_analyze(tiny, tmp_path):
    out = tmp_path / "out"
    rc, _, _ = run("build", tiny, "--only", "analyze", "--out", out)
    assert rc == 0
    assert sorted(os.listdir(str(out / "tiny"))) == ["report.json", "report.md"]


def test_build_invalid(bad, tmp_path):
    out = tmp_path / "out"
    rc, stdout, _ = run("build", bad, "--out", out)
    assert rc == 1
    assert stdout.splitlines()[0] == "1 error(s) in %s:" % bad
    assert not out.exists()


def test_build_missing(missing, tmp_path):
    rc, out, _ = run("build", missing, "--out", tmp_path / "out")
    assert rc == 1
    assert out == "error: file not found: %s\n" % missing


def test_build_empty_dir(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    rc, out, _ = run("build", d, "--out", tmp_path / "out")
    assert rc == 1
    assert out == "error: no *.yaml files in %s\n" % d


def test_build_dir_mixed(tmp_path):
    d = tmp_path / "specs"
    d.mkdir()
    _w(d / "a_ok.yaml", BASE)
    _w(d / "b_bad.yaml", BAD_TEXT)
    out = tmp_path / "out"
    rc, stdout, _ = run("build", d, "--only", "analyze", "--out", out)
    assert rc == 1
    assert (out / "tiny" / "report.json").is_file()
    assert "1 error(s) in " in stdout
    assert "b_bad.yaml:" in stdout


def test_probe_ok(tiny):
    rc, out, _ = run("probe", tiny, "base_A", 500, 500)
    assert rc == 0
    # each point prints a result line followed by an indented "region:" detail line
    lines = [ln for ln in out.splitlines() if ln.strip() and not ln.startswith(" ")]
    assert len(lines) == 2, out
    assert lines[0].startswith("(100, 100)  A 0:00")
    assert lines[1].startswith("(500, 500)  ")
    # each result line is followed by exactly one indented detail line
    all_lines = out.splitlines()
    assert len(all_lines) == 4, out
    assert all_lines[1].startswith(" ") and all_lines[3].startswith(" "), out


def test_probe_odd_coords(tiny):
    rc, out, _ = run("probe", tiny, 500)
    assert rc == 1
    assert out == "error: expected x y pairs or POI ids, got '500'\n"


def test_probe_unknown_poi(tiny):
    rc, out, _ = run("probe", tiny, "nosuchpoi")
    assert rc == 1
    assert out == "error: expected x y pairs or POI ids, got 'nosuchpoi'\n"


def test_probe_missing(missing):
    rc, out, _ = run("probe", missing, 1, 1)
    assert rc == 1
    assert out == "error: file not found: %s\n" % missing


def test_probe_invalid(bad):
    rc, out, _ = run("probe", bad, 1, 1)
    assert rc == 1
    assert out.splitlines()[0] == "1 error(s) in %s:" % bad


def test_types_ok():
    rc, out, _ = run("types")
    assert rc == 0
    lines = out.splitlines()
    assert lines[0] == "regions:"
    for prefix in ("lines:", "crossings:", "pois:", "splat_layers: "):
        assert any(ln.startswith(prefix) for ln in lines), prefix


def test_palette_ok():
    rc, out, _ = run("palette")
    assert rc == 0
    assert out.splitlines()[0] == "Region fill lightness (CIE L*) and pattern:"


def test_palette_missing(missing):
    rc, out, _ = run("palette", "--palette", missing)
    assert rc == 1
    assert out == "error: file not found: %s\n" % missing


def test_palette_invalid(tmp_path):
    p = _w(tmp_path / "pal.yaml", 'teams: ["#111111"]\n')
    rc, out, _ = run("palette", "--palette", p)
    assert rc == 1
    assert out == "palette.teams: expected a list of at least 3 colours\n"


def test_no_command():
    rc, _, err = run()
    assert rc == 2
    assert "the following arguments are required: command" in err


def test_unknown_command():
    rc, _, err = run("frobnicate")
    assert rc == 2
    assert "invalid choice: 'frobnicate'" in err


def test_build_no_spec():
    rc, _, err = run("build")
    assert rc == 2
    assert "the following arguments are required: spec" in err
