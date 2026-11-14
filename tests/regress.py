"""Baseline helpers for the mapgen regression suite: discover maps, hash build output,
read, write and compare baselines. See tests/REGRESSION_SPEC.md."""
import glob
import hashlib
import json
import os
import re
import sys
import tempfile
from typing import Dict, List, Tuple

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)
BASELINE_DIR = os.path.join(TESTS_DIR, "baselines")
MAP_GLOBS = ("maps/example_*.yaml", "maps/concepts/*.yaml")

if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import mapgen.build  # noqa: E402
import mapgen.schema  # noqa: E402

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def discover_maps() -> List[Tuple[str, str]]:
    """Return (spec name, repo-relative spec path) for every committed map."""
    result = []  # type: List[Tuple[str, str]]
    seen = {}  # type: Dict[str, str]
    for pattern in MAP_GLOBS:
        matches = sorted(glob.glob(os.path.join(REPO_ROOT, *pattern.split("/"))))
        for abs_path in matches:
            rel = os.path.relpath(abs_path, REPO_ROOT).replace(os.sep, "/")
            name = mapgen.schema.load_spec(abs_path).name
            if name in seen:
                raise ValueError("duplicate spec name '%s': %s, %s" % (name, seen[name], rel))
            seen[name] = rel
            result.append((name, rel))
    return result


def hash_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def hash_tree(root: str) -> Dict[str, str]:
    found = {}  # type: Dict[str, str]
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            found[rel] = hash_file(full)
    return {k: found[k] for k in sorted(found)}


def build_and_hash(spec: str, work_dir: str) -> Dict[str, str]:
    if os.path.exists(work_dir) and (not os.path.isdir(work_dir) or os.listdir(work_dir)):
        raise ValueError("work dir is not empty: %s" % work_dir)
    out_dir = mapgen.build.build(os.path.join(REPO_ROOT, spec), work_dir, log=lambda s: None)
    return hash_tree(out_dir)


def baseline_path(name: str) -> str:
    return os.path.join(BASELINE_DIR, name + ".json")


def format_baseline(name: str, spec: str, files: Dict[str, str]) -> str:
    return json.dumps({"map": name, "spec": spec, "files": files},
                      indent=1, sort_keys=True, ensure_ascii=True) + "\n"


def write_baseline(name: str, spec: str, files: Dict[str, str]) -> str:
    os.makedirs(BASELINE_DIR, exist_ok=True)
    path = baseline_path(name)
    fd, tmp_path = tempfile.mkstemp(prefix=name + ".", suffix=".tmp", dir=BASELINE_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(format_baseline(name, spec, files))
        os.replace(tmp_path, path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise
    return path


def load_baseline(name: str) -> Dict[str, str]:
    path = baseline_path(name)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or sorted(data.keys()) != ["files", "map", "spec"]:
        raise ValueError("%s: top-level keys must be exactly files, map, spec" % path)
    if data["map"] != name:
        raise ValueError("%s: map is '%s', expected '%s'" % (path, data["map"], name))
    files = data["files"]
    if not isinstance(files, dict):
        raise ValueError("%s: files must be an object" % path)
    for k, v in files.items():
        if not isinstance(v, str) or not _HEX64.match(v):
            raise ValueError("%s: files['%s'] is not a 64-character lower-case hex string" % (path, k))
    return files


def compare(name: str, expected: Dict[str, str], actual: Dict[str, str]) -> List[str]:
    problems = []  # type: List[str]
    for path in sorted(set(expected) | set(actual)):
        if path not in actual:
            problems.append("%s: %s: missing from build" % (name, path))
        elif path not in expected:
            problems.append("%s: %s: not in baseline" % (name, path))
        elif expected[path] != actual[path]:
            short_e, short_a = expected[path][:12], actual[path][:12]
            if short_e == short_a:
                short_e, short_a = expected[path], actual[path]
            problems.append("%s: %s: changed (baseline %s, build %s)" % (name, path, short_e, short_a))
    return problems


def format_failure(name: str, problems: List[str]) -> str:
    lines = ["map '%s' differs from tests/baselines/%s.json in %d file(s):" % (name, name, len(problems))]
    lines.extend("  " + p for p in problems)
    lines.append("Re-baseline only after the owner approves a mapgen diff report: "
                 ".venv/Scripts/python tests/rebaseline.py %s" % name)
    return "\n".join(lines)
