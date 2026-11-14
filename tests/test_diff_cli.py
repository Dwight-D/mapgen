"""mapgen diff command line: exit codes and output, through real subprocesses."""
import copy
import json
import os
import subprocess
import sys

import numpy as np
import pytest

from test_cli_exit_codes import REPO_ROOT, run
from test_diff import REPORT, make_build


@pytest.fixture(scope="module")
def builds(tmp_path_factory):
    base = tmp_path_factory.mktemp("diffcli")
    f = np.zeros((4, 4), np.uint8)
    f[0, 1] = 255
    f[2, 2] = 128
    rep = copy.deepcopy(REPORT)
    rep["area_share"]["A"] = 0.6
    rep["area_share"]["B"] = 0.3
    return {
        "A": make_build(base / "a"),
        "B": make_build(base / "b", forest=f),
        "B2": make_build(base / "b2", report=rep),
        "base": base,
    }


def test_diff_self(builds):
    rc, out, _ = run("diff", builds["A"], builds["A"])
    assert rc == 0
    assert json.loads(out)["identical"] is True


def test_diff_differ(builds):
    rc, out, _ = run("diff", builds["A"], builds["B"])
    assert rc == 0
    r = json.loads(out)
    assert r["identical"] is False
    x = [e for e in r["rasters"] if e["path"] == "export/masks/forest.png"][0]
    assert x["changed_texels"] == 2


def test_diff_text(builds):
    rc, out, _ = run("diff", builds["A"], builds["B"], "--text")
    assert rc == 0
    lines = out.splitlines()
    assert lines[0] == "mapgen diff"
    assert "identical: no" in lines


@pytest.mark.parametrize("flags", [(), ("--text",)])
def test_diff_stdout_bytes_lf(builds, flags):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.run([sys.executable, "-m", "mapgen", "diff", builds["A"], builds["B"]] + list(flags),
                       cwd=REPO_ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert p.returncode == 0
    assert b"\r" not in p.stdout
    assert p.stdout.endswith(b"\n") and not p.stdout.endswith(b"\n\n")


def test_diff_max_changes(builds):
    rc, out, _ = run("diff", builds["A"], builds["B2"], "--max-changes", 1)
    assert rc == 0
    e = json.loads(out)["json"]["report.json"]
    assert len(e["changes"]) == 1
    assert e["truncated"] is True


def test_diff_missing(builds):
    missing = str(builds["base"] / "missing")
    rc, out, _ = run("diff", missing, builds["A"])
    assert rc == 1
    assert out == "error: directory not found: %s\n" % missing


def test_diff_missing_b(builds):
    missing = str(builds["base"] / "missing")
    rc, out, _ = run("diff", builds["A"], missing)
    assert rc == 1
    assert out == "error: directory not found: %s\n" % missing


def test_diff_not_dir(builds):
    f = builds["base"] / "afile.txt"
    f.write_text("x")
    rc, out, _ = run("diff", f, builds["A"])
    assert rc == 1
    assert out == "error: not a directory: %s\n" % f


def test_diff_not_build(builds):
    d = builds["base"] / "emptydir"
    d.mkdir()
    rc, out, _ = run("diff", d, builds["A"])
    assert rc == 1
    assert out == ("error: not a mapgen build directory (no report.json or export/meta.json): %s\n" % d)


def test_diff_one_arg(builds):
    rc, _, err = run("diff", builds["A"])
    assert rc == 2
    assert "the following arguments are required: build_b" in err


def test_diff_bad_max(builds):
    rc, _, err = run("diff", builds["A"], builds["A"], "--max-changes", "-1")
    assert rc == 2
    assert "argument --max-changes: expected an integer >= 0, got '-1'" in err
