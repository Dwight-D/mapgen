"""Stability rule: a build of an unchanged spec is byte-identical to its baseline."""
import os

import pytest

import regress

MAPS = regress.discover_maps()


@pytest.mark.parametrize("name,spec", MAPS, ids=[m[0] for m in MAPS])
def test_build_matches_baseline(name, spec, tmp_path):
    try:
        expected = regress.load_baseline(name)
    except FileNotFoundError:
        pytest.fail("no baseline for map '%s' (%s): create it with "
                    ".venv/Scripts/python tests/rebaseline.py %s" % (name, spec, name), pytrace=False)
    actual = regress.build_and_hash(spec, str(tmp_path / "out"))
    problems = regress.compare(name, expected, actual)
    if problems:
        pytest.fail(regress.format_failure(name, problems), pytrace=False)


def test_baselines_match_maps():
    stems = set(f[:-5] for f in os.listdir(regress.BASELINE_DIR) if f.endswith(".json"))
    names = set(m[0] for m in MAPS)
    msgs = []
    if stems - names:
        msgs.append("baselines without a map: %s" % sorted(stems - names))
    if names - stems:
        msgs.append("maps without a baseline: %s" % sorted(names - stems))
    if msgs:
        pytest.fail("; ".join(msgs), pytrace=False)
