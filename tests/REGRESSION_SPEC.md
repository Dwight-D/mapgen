# mapgen regression suite — implementation spec

This file is the complete brief for building the mapgen regression suite. An agent can implement any wave
below by reading only this file and the repo files it names. Repo root: `E:\GameDev\Unity\tools\mapgen`
(all paths below are relative to it). Every command runs from the repo root.

## 1. Purpose

mapgen turns a YAML map spec into a build output directory (`<out root>/<spec name>/`): rendered images,
an analysis report and the `export/` bundle that Battlehounds consumes. Builds are deterministic: the same
spec, code and dependency versions produce byte-identical files.

The regression suite guards three things:

1. **Stability rule.** A build of an unchanged spec is byte-identical to its baseline. A **baseline** is
   the committed set of SHA-256 hashes, one per output file, of one map's full build output. Baselines
   exist for the eight committed maps: the three `maps/example_*.yaml` maps and the five
   `maps/concepts/*.yaml` maps. Any difference — a changed file, a missing file or an extra file — fails
   the suite and names the map and the file. Render outputs (`map.png`, `overlay_*.png`) are part of the
   baselines.
2. **Spec validation.** A representative set of invalid specs produces the expected error messages and
   exit code 1.
3. **Command exit codes** of `build`, `validate`, `probe`, `types` and `palette`.

**Re-baselining** is a deliberate, explicit step: `tests/rebaseline.py`. The suite never runs it and never
writes a baseline. It is run only after the owner has approved a diff report for the change. The diff
report is the output of `mapgen diff <before build> <after build>` for each affected map (`mapgen/DIFF_SPEC.md`).

There is one suite. It always builds all eight maps in full. A single map can be run alone with pytest's
`-k <map name>`.

## 2. Measured facts this spec relies on

Measured on 2026-11-14 at commit `c6b0f3c`, Windows 11, Python 3.8.0 venv in `.venv`.

### 2.1 Determinism

`maps/example_natural.yaml` and `maps/concepts/thornwald.yaml` were each built twice through the CLI
(`python -m mapgen build <spec> --out <scratch>`, two separate processes) and once in-process
(`mapgen.build.build(spec_path, out_root)`). Every file was byte-identical across all three builds
(SHA-256 per file, 34 and 33 files). All eight maps built in sequence in one process were byte-identical
to the CLI builds in `out/`. No non-deterministic output was found. CLI and in-process builds agree.

Determinism holds under these conditions, which the suite must respect:

- **The output directory must be empty before the build.** `build.build` does not clear its output
  directory, and `report.md` / `report.json` list every file present in it (`mapgen/build.py:152-157`,
  `:190-199`). A stale file from an earlier build changes both reports. Every stability build goes into a
  fresh, empty temporary directory, never into `out/`.
- **Spec bytes, not just spec content, enter the output.** `export/meta.json` carries `spec_sha256`, the
  SHA-256 of the raw spec file bytes (`mapgen/schema.py:800-814`, `mapgen/export.py:301`). A spec checked
  out with CRLF line endings changes `meta.json` (verified: a CRLF copy of
  `maps/example_river_crossing.yaml` differs from the LF build in `export/meta.json` only). The specs are
  LF in the index; `.gitattributes` (section 4.6) pins them to LF in every checkout. This matters because
  the system git config on this machine sets `core.autocrlf=true` (`C:/Program Files/Git/etc/gitconfig`);
  only the user's global config overrides it to `false`.
- **Fonts come from the operating system.** `mapgen/render.py:28-41` loads `arial.ttf` (Windows), else
  `DejaVuSans.ttf`, else Pillow's default font. Render files with text (`map.png`, every `overlay_*.png`)
  are therefore tied to the machine's font files. The baselines are valid for Windows with the system
  Arial font and the pinned venv. On another platform the render files differ while the `export/` files
  and reports do not.
- **Dependency versions are pinned exactly** (section 4.5). Pillow's bundled zlib/FreeType, GEOS inside
  shapely and numpy/scipy numerics all reach the output bytes.

### 2.2 Output folder name and file list

The output folder is `<out root>/<spec name>`, where `<spec name>` is the spec's top-level `name` key, not
the file name (`mapgen/build.py:173`). Baselines are keyed by spec `name`.

| spec file | spec `name` | files | full build, in-process |
|---|---|---|---|
| `maps/example_natural.yaml` | `natural` | 34 | 13.9 s |
| `maps/example_river_crossing.yaml` | `river_crossing` | 31 | 10.7 s |
| `maps/example_three_team.yaml` | `three_team` | 28 | 14.8 s |
| `maps/concepts/ashen_steppe.yaml` | `ashen_steppe` | 33 | 15.1 s |
| `maps/concepts/gloamfen.yaml` | `gloamfen` | 33 | 16.9 s |
| `maps/concepts/kingsjaw.yaml` | `kingsjaw` | 33 | 16.4 s |
| `maps/concepts/saltreach_delta.yaml` | `saltreach_delta` | 35 | 19.3 s |
| `maps/concepts/thornwald.yaml` | `thornwald` | 33 | 15.1 s |

All eight in one process: 123.4 s including 1.3 s of imports. A CLI build adds about 2 s of interpreter
start and imports. No map produces a report warning. Estimated full suite runtime: about 2.5–3 minutes
(eight builds plus about 30 s of subprocess exit-code tests).

Layout of one build (thornwald; other maps differ only in which `export/masks/<type>.png` files exist,
one per region, line and crossing type the map uses, plus `water.png` and `impassable.png` always):

```
map.png
overlay_balance.png  overlay_cost.png  overlay_openness.png
overlay_territory.png  overlay_traffic.png  overlay_travel.png
report.json  report.md
export/crossings.json  export/heightmap.png  export/heightmap.r16  export/lines.json
export/meta.json  export/pois.json  export/regions.json  export/spec.resolved.json
export/splat_0.png  export/splat_1.png
export/masks/bridge.png  clearing.png  field.png  ford.png  forest.png  hills.png  impassable.png
             lake.png  river.png  road.png  ruins.png  town.png  trail.png  water.png
```

### 2.3 In-process API

- `mapgen.build.build(spec_path: str, out_root: str = "out", only: Optional[str] = None,
  log: Callable[[str], None] = print, palette_path: Optional[str] = None) -> str` builds one spec and
  returns the output directory `os.path.join(out_root, spec.name)`. It raises `mapgen.schema.SpecError`
  (attribute `.errors: List[str]`) on an invalid spec before writing anything, and `FileNotFoundError`
  for a missing spec file. Pass `log=lambda s: None` to silence stage timings.
- `mapgen.schema.load_spec(path: str, palette_path: Optional[str] = None) -> MapSpec` loads and validates;
  `.name` is the spec name. Same exceptions.
- `mapgen.__main__.main(argv: Optional[List[str]] = None) -> int` runs the CLI in-process and returns the
  exit code; argparse usage errors raise `SystemExit(2)`.
- Module-level caches (`schema._TYPES_CACHE`, `schema._PALETTE_CACHE`, `render._FONTS`) do not affect
  output: sequential in-process builds of all eight maps match separate CLI builds.

### 2.4 Exit codes and message shapes

All messages go to stdout (the CLI prints errors with `print`; only argparse writes to stderr).

| situation | exit code | stdout |
|---|---|---|
| spec errors (`validate`, `build`, `probe`) | 1 | `<N> error(s) in <spec path as given>:` then one line per error, each indented by two spaces |
| missing spec file (`validate`, `build`, `probe`) | 1 | `error: file not found: <path as given>` |
| `build <dir>` with no `*.yaml` | 1 | `error: no *.yaml files in <dir>` |
| `build <dir>` with some invalid specs | 1 | valid specs are built; each invalid one prints its error block |
| `probe` with a dangling or non-numeric point token | 1 | `error: expected x y pairs or POI ids, got '<tokens>'` |
| `palette --palette <missing>` | 1 | `error: file not found: <path>` |
| `palette --palette <bad file>` | 1 | one error line per problem, no header line (e.g. `palette.teams: expected a list of at least 3 colours`) |
| `validate` of a valid spec | 0 | `OK: <name> (<x>x<y> m, <n> teams, <n> regions, <n> lines, <n> crossings, <n> pois)` |
| `build`, `probe`, `types`, `palette` success | 0 | stage log / probe lines / type table starting `regions:` / `Region fill lightness (CIE L*) and pattern:` |
| no command, unknown command, missing positional | 2 | argparse usage on stderr |

An invalid spec makes `build` write nothing: the output root is not created.

## 3. File layout

| file | responsibility |
|---|---|
| `pytest.ini` | pytest configuration; anchors the pytest rootdir at the repo root |
| `requirements.txt` | exact `==` pins of every runtime dependency, transitive ones included |
| `requirements-dev.txt` | `-r requirements.txt` plus exact pins of pytest and everything it pulls in |
| `.gitattributes` | LF line endings for map specs and baseline files in every checkout |
| `.gitignore` | gains `.pytest_cache/` |
| `tests/REGRESSION_SPEC.md` | this spec |
| `tests/conftest.py` | puts the repo root and `tests/` on `sys.path`; shared fixtures |
| `tests/regress.py` | baseline helper module: map discovery, hashing, baseline read/write/format, compare, failure message |
| `tests/rebaseline.py` | the explicit re-baselining script (CLI) |
| `tests/baselines/<spec name>.json` | one baseline per map, eight files |
| `tests/test_regress_helpers.py` | fast unit tests of `regress.py` (no map builds) |
| `tests/test_stability.py` | the stability test, one case per map, plus the baseline/map correspondence check |
| `tests/test_validation.py` | invalid-spec cases, in-process |
| `tests/test_identity.py` | feature identity: id rules, id-derived seeds, format version |
| `tests/test_references.py` | feature references: resolution, equivalence of references and literals |
| `tests/test_cli_exit_codes.py` | command exit codes, through real subprocesses |
| `tests/test_diff.py` | diff report logic on synthetic builds; river_crossing integration build |
| `tests/test_diff_cli.py` | diff command exit codes, through real subprocesses |

`tests/` has no `__init__.py`. pytest's default `prepend` import mode puts `tests/` on `sys.path`, so test
modules `import regress`; `conftest.py` also inserts it explicitly so `tests/rebaseline.py` and pytest
behave the same.

## 4. Component specs

### 4.1 `pytest.ini`

```ini
[pytest]
testpaths = tests
addopts = -ra
```

The file is required: without an ini file pytest derives the rootdir from the arguments and can walk up
to `C:\` (observed: a collection error on `C:\Documents and Settings`).

### 4.2 `tests/conftest.py`

```python
"""pytest setup for the mapgen regression suite: import paths and shared fixtures."""
import os
import sys

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)
for p in (REPO_ROOT, TESTS_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)
```

No fixtures beyond pytest's built-in `tmp_path` and `capsys` are needed.

### 4.3 `tests/regress.py` — baseline helper module

Module docstring: `"""Baseline helpers for the mapgen regression suite: discover maps, hash build output,
read, write and compare baselines. See tests/REGRESSION_SPEC.md."""`

Constants:

```python
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)
BASELINE_DIR = os.path.join(TESTS_DIR, "baselines")
MAP_GLOBS = ("maps/example_*.yaml", "maps/concepts/*.yaml")
```

`regress.py` inserts `REPO_ROOT` into `sys.path` if absent (so `import mapgen` works when
`tests/rebaseline.py` imports it), then imports `mapgen.build` and `mapgen.schema`.

Functions (all type hints via `typing`, Python 3.8):

- `discover_maps() -> List[Tuple[str, str]]`
  Returns `(spec name, spec path)` pairs for every file matching `MAP_GLOBS`, in glob order, each glob's
  matches sorted. The spec path is relative to `REPO_ROOT` with forward slashes
  (`"maps/concepts/thornwald.yaml"`). The name is `mapgen.schema.load_spec(abs path).name`. Raises
  `ValueError("duplicate spec name '<name>': <path a>, <path b>")` if two specs share a name. Result
  today: `natural, river_crossing, three_team, ashen_steppe, gloamfen, kingsjaw, saltreach_delta,
  thornwald`.
- `hash_file(path: str) -> str`
  SHA-256 hex digest of the file bytes, read in binary mode in 1 MiB chunks.
- `hash_tree(root: str) -> Dict[str, str]`
  Walks `root` with `os.walk`; maps each file's path relative to `root`, with `os.sep` replaced by `/`,
  to `hash_file`. Returns a dict whose keys are inserted in sorted order.
- `build_and_hash(spec: str, work_dir: str) -> Dict[str, str]`
  `spec` is a repo-relative spec path. Requires `work_dir` to be absent or an empty directory, else
  raises `ValueError("work dir is not empty: <work_dir>")`. Calls
  `mapgen.build.build(os.path.join(REPO_ROOT, spec), work_dir, log=lambda s: None)` and returns
  `hash_tree(<returned out dir>)`. Lets `SpecError` and `FileNotFoundError` propagate.
- `baseline_path(name: str) -> str` — `os.path.join(BASELINE_DIR, name + ".json")`.
- `format_baseline(name: str, spec: str, files: Dict[str, str]) -> str`
  Returns `json.dumps({"map": name, "spec": spec, "files": files}, indent=1, sort_keys=True,
  ensure_ascii=True) + "\n"`. The same arguments always give the same string.
- `write_baseline(name: str, spec: str, files: Dict[str, str]) -> str`
  Creates `BASELINE_DIR` if needed, writes `format_baseline(...)` (UTF-8, `newline="\n"`) to a temporary
  file in `BASELINE_DIR` (`tempfile.mkstemp`), moves it onto `baseline_path(name)` with `os.replace`, and
  returns the path. A failed write removes the temporary file and leaves any existing baseline untouched.
  The file content is exactly `format_baseline(...)`.
- `load_baseline(name: str) -> Dict[str, str]`
  Reads `baseline_path(name)` (UTF-8) and returns its `files` mapping. Raises
  `FileNotFoundError` if absent. Raises `ValueError("<path>: <problem>")` if the top-level keys are not
  exactly `files`, `map`, `spec`, if `map != name`, or if any value in `files` is not a 64-character
  lower-case hex string.
- `compare(name: str, expected: Dict[str, str], actual: Dict[str, str]) -> List[str]`
  Returns one problem line per differing path, sorted by path; an empty list means identical. Line
  formats (hashes shortened to their first 12 hex digits; when the two shortened hashes are equal, both
  are shown in full, so a `changed` line always shows two different values):
  - `<name>: <path>: changed (baseline <12 hex>, build <12 hex>)`
  - `<name>: <path>: missing from build`
  - `<name>: <path>: not in baseline`
- `format_failure(name: str, problems: List[str]) -> str`
  ```
  map '<name>' differs from tests/baselines/<name>.json in <N> file(s):
    <problem line 1>
    <problem line 2>
  Re-baseline only after the owner approves a mapgen diff report: .venv/Scripts/python tests/rebaseline.py <name>
  ```
  (lines joined with `\n`; problem lines indented by two spaces; no trailing newline).

### 4.4 Baseline file format

`tests/baselines/<spec name>.json`: JSON object with exactly three keys, written by `format_baseline`:
sorted keys, `indent=1`, ASCII only, LF line endings, one trailing newline.

- `map`: the spec `name`;
- `spec`: repo-relative spec path, forward slashes;
- `files`: every output file of a full build, path relative to the map's output folder with forward
  slashes → lower-case SHA-256 hex.

Real example — `tests/baselines/three_team.json` as it must be generated at commit `c6b0f3c` (the
baseline-generation step checks this exact content):

```json
{
 "files": {
  "export/crossings.json": "37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570",
  "export/heightmap.png": "da2e94d91a68eb364c4951438f33a9d3cf2beee2a9a97932ddb8d497b982105e",
  "export/heightmap.r16": "acbd8d47c08a819de56bda4956dc3fe0890cac23a25d1d2f8b4ec8aac4d21385",
  "export/lines.json": "9f4900e8f4c690aa7dc3f3ef80d502da5540b86bf9065f50f9243a83ae771049",
  "export/masks/forest.png": "3c319eb781cb7aa4e88398a2c6386b06fa5810f73b89aa6fc342971e2b5417e5",
  "export/masks/hills.png": "c4ec07abd9cd8dcd4cc33bb5df982b3315dc8ad168204b7508fc40469a07029c",
  "export/masks/impassable.png": "d2fcf79acb83a8e93aca44403f87c13c0f3a75a2d0f859448512dc75d4febc47",
  "export/masks/lake.png": "8c7f32dfa919dfd037a9fbf116937e2eddeaafefe18e156d32db1457dab61469",
  "export/masks/road.png": "8c646ed06020c52bf942b3898c4580259acf8093d9696ebe72ca731ea1696db2",
  "export/masks/ruins.png": "b5956a9ed72bbd6d22b7ba0add17378a0e0b6e42bc46ae48c33055bd69d12973",
  "export/masks/town.png": "d32af527e987adbf753161a5c7f692d4ac8245d6fbc746cb8bb85400ccab2ace",
  "export/masks/trail.png": "b4ef43a27d502e2645d520eb7247fd1e6374d2f4a82e0fdcb3e4d45e29b4da3b",
  "export/masks/water.png": "8c7f32dfa919dfd037a9fbf116937e2eddeaafefe18e156d32db1457dab61469",
  "export/meta.json": "129398f004f09b540292a5fd3d5424d284cd67f869dce1ce67633d3aa2d8d9b9",
  "export/pois.json": "3b12219bbfc2b3d7eeb951943ce342e1a545b1158dd97eff19410ba45d7eb473",
  "export/regions.json": "832d6ced0b14d36b43912a4445cf5205a2d8fff8be2266eacfab474ccd6d086b",
  "export/spec.resolved.json": "7831a6b0f6c26323b63808a2e1d493a839aeb76733dc6624b4ce0630dc05186f",
  "export/splat_0.png": "6b57b97c092cfa1318c1ab9051f5d9c70e685b5a29fe5ec05f6b9fd9d2ea6018",
  "export/splat_1.png": "71688c9ed8cf43c96053c88c7e43d286d5bf103784285215723997c6072d5a81",
  "map.png": "0b2b9d0d606e9b3d36ff05802a623323007dfb367cc8ee8356787ce404b346ca",
  "overlay_balance.png": "4d6cc5be38998fd75a4769c58ae7a6e101209a30a6df0843d69ff994d275107f",
  "overlay_cost.png": "3415525e13fa7b63af775d20218532ab06446acf506edf37cf7057db1a91fae1",
  "overlay_openness.png": "84332480a233ebf6dd41a08f2daa1ed3977e239a487aaf7c0994300964c0346b",
  "overlay_territory.png": "f4a23e4d35adba4f807fc3ebbbe510e8d0674c65b30e967b00d1bb9dd02c7f2d",
  "overlay_traffic.png": "2ec4afef113289bc5fd0ef004a31551aac8fdd8bb7bd4dfd0225c262176f0fee",
  "overlay_travel.png": "11be8e81e1ac0555b858df08ffb74832da18d8b5f326b85adae0bd3ed2a9547e",
  "report.json": "9886678e6291f6d32341e89975713e121d6e09691bb1a26eac84ada455d80d6b",
  "report.md": "12cf75ce889af5919d9e14fa767a378206f8e26dae7e29673e8191afc20828cb"
 },
 "map": "three_team",
 "spec": "maps/example_three_team.yaml"
}
```

Identical hashes for two files (here `lake.png` and `water.png`) are expected when two masks cover the
same texels.

### 4.5 `tests/rebaseline.py` — re-baselining script

Docstring and `argparse` description (same text):

> Re-baseline mapgen regression maps: build each named map into a fresh temporary directory and write its
> per-file SHA-256 hashes to tests/baselines/<map>.json. Re-baselining is a deliberate step: run it only
> after the owner has approved a diff report for the change. The regression suite never runs this script.

CLI:

```
.venv/Scripts/python tests/rebaseline.py [--dry-run] (--all | MAP [MAP ...])
```

- `MAP` is a spec `name` as returned by `regress.discover_maps()` (e.g. `thornwald`, `natural`).
- `--all` selects every discovered map. `--all` together with `MAP` arguments, or neither, is a usage
  error (argparse, exit 2). Implement with a mutually exclusive group plus `nargs="*"`, then
  `parser.error("give MAP names or --all")` when the list is empty and `--all` is absent.
- `--dry-run` builds and compares but writes nothing.

Behaviour, per selected map in discovery order:

1. Unknown names are checked before any build: print
   `error: unknown map '<name>' (known: <comma-separated names>)` and exit 1.
2. Build with `regress.build_and_hash(spec, work_dir)` where
   `work_dir = os.path.join(tempfile.mkdtemp(prefix="mapgen-rebaseline-"), "out")`; remove the
   `mkdtemp` directory with `shutil.rmtree` in a `finally`.
3. If a baseline exists, print `<name>: unchanged` when `compare` returns nothing, else
   `<name>: <N> file(s) differ` followed by the `compare` lines indented by two spaces. If none exists,
   print `<name>: new baseline (<N> files)`.
4. Every selected map is built and hashed first; baselines are written only after all builds have
   finished. Unless `--dry-run`, each built map is then written with `regress.write_baseline` and
   `  wrote tests/baselines/<name>.json` is printed. If any build raises an exception other than
   `SpecError`, the script ends with that exception and writes no baseline.
5. A `SpecError` prints `<N> error(s) in <spec>:` plus the indented errors, skips that map and makes the
   final exit code 1. Other maps continue and are written.

Exit 0 when every selected map was built. The script never deletes a baseline file.

### 4.6 `.gitattributes`

```
maps/**/*.yaml text eol=lf
tests/baselines/*.json text eol=lf
```

Only these patterns. A repo-wide `* text=auto` is not used: `README.md`, `MAPGEN_SPEC.md`,
`mapgen/export.py`, `mapgen/height.py` and `mapgen/palette.yaml` are stored with CRLF in the index and
would be renormalised. The specs and baselines are LF in the index already, so the file causes no
content change. Baseline JSON is compared after parsing, so its line endings do not affect the suite;
the entry keeps rebaseline output byte-identical to the committed file in every checkout.

### 4.7 `.gitignore`

Append `.pytest_cache/`. The file becomes:

```
.venv/
__pycache__/
*.pyc
out/
.pytest_cache/
```

### 4.8 Requirements

`requirements.txt` holds runtime pins; `requirements-dev.txt` holds the test runner and its
dependencies. A consumer that only builds maps installs `requirements.txt`; development installs
`requirements-dev.txt`. Every pin is `==`, transitive dependencies included. Versions are those
installed in `.venv` on 2026-11-14 (`pip freeze`). pytest 8.3.5 is the newest pytest that supports
Python 3.8 (pytest 8.4 requires Python 3.9); it is installed in `.venv`.

`requirements.txt` (exact content):

```
# Runtime dependencies of mapgen, every package pinned exactly (transitive ones included).
# Output bytes depend on these versions; changing one is a re-baselining change (tests/REGRESSION_SPEC.md).
imageio==2.35.1
lazy_loader==0.4
networkx==3.1
numpy==1.24.4
packaging==26.2
pillow==10.4.0
PyWavelets==1.4.1
PyYAML==6.0.3
scikit-image==0.21.0
scipy==1.10.1
shapely==2.0.7
tifffile==2023.7.10
```

`requirements-dev.txt` (exact content):

```
# Development dependencies: the regression suite runner, every package pinned exactly.
-r requirements.txt
colorama==0.4.6; sys_platform == "win32"
exceptiongroup==1.3.1
iniconfig==2.1.0
pluggy==1.5.0
pytest==8.3.5
tomli==2.5.0
typing_extensions==4.13.2
```

`packaging` is needed by both scikit-image and pytest and lives in `requirements.txt`.

## 5. Test cases

All tests use pytest's `tmp_path` for scratch files. No test writes into `out/`, `maps/` or
`tests/baselines/`.

### 5.1 `tests/test_stability.py`

```python
MAPS = regress.discover_maps()

@pytest.mark.parametrize("name,spec", MAPS, ids=[m[0] for m in MAPS])
def test_build_matches_baseline(name, spec, tmp_path): ...
```

- Load the baseline first: if `load_baseline(name)` raises `FileNotFoundError`, fail with
  `no baseline for map '<name>' (<spec>): create it with .venv/Scripts/python tests/rebaseline.py <name>`.
- `actual = regress.build_and_hash(spec, str(tmp_path / "out"))`.
- `problems = regress.compare(name, expected, actual)`; `assert not problems, regress.format_failure(name, problems)`.
  Use `pytest.fail(..., pytrace=False)` instead of `assert` so the message prints without assertion
  rewriting noise.

`test_baselines_match_maps()`: the set of `*.json` stems in `tests/baselines/` equals the set of
discovered names. On mismatch, fail naming `baselines without a map: [...]` and/or
`maps without a baseline: [...]`.

Running one map alone: `.venv/Scripts/python -m pytest -k thornwald`.

### 5.2 `tests/test_regress_helpers.py` (no map builds; under 2 s)

| test | checks |
|---|---|
| `test_discover_maps` | returns exactly these pairs in this order: `("natural", "maps/example_natural.yaml")`, `("river_crossing", "maps/example_river_crossing.yaml")`, `("three_team", "maps/example_three_team.yaml")`, `("ashen_steppe", "maps/concepts/ashen_steppe.yaml")`, `("gloamfen", "maps/concepts/gloamfen.yaml")`, `("kingsjaw", "maps/concepts/kingsjaw.yaml")`, `("saltreach_delta", "maps/concepts/saltreach_delta.yaml")`, `("thornwald", "maps/concepts/thornwald.yaml")` |
| `test_hash_tree` | a tree `a.txt` (`b"x"`) and `sub/b.bin` (`b"\x00\x01"`) gives keys `["a.txt", "sub/b.bin"]` and `hashlib.sha256(b"x").hexdigest()` for `a.txt` |
| `test_compare_identical` | equal dicts → `[]` |
| `test_compare_changed_missing_extra` | expected `{"a": "0"*64, "b": "1"*64}`, actual `{"a": "f"*64, "c": "2"*64}` → exactly `["m: a: changed (baseline 000000000000, build ffffffffffff)", "m: b: missing from build", "m: c: not in baseline"]` |
| `test_compare_equal_prefix_shows_full_hashes` | hashes `"abcdef012345" + "0"*52` and `"abcdef012345" + "1"*52` → `["m: a: changed (baseline <full hash 1>, build <full hash 2>)"]` |
| `test_write_baseline_replaces_file_without_leftovers` | with `BASELINE_DIR` pointed at `tmp_path`, `write_baseline` writes exactly `format_baseline(...)` bytes; a second write replaces it; the directory then holds only `m.json` |
| `test_format_failure` | `format_failure("m", ["m: a: missing from build"])` equals `"map 'm' differs from tests/baselines/m.json in 1 file(s):\n  m: a: missing from build\nRe-baseline only after the owner approves a mapgen diff report: .venv/Scripts/python tests/rebaseline.py m"` |
| `test_format_baseline_deterministic` | two calls with dicts of the same content but different insertion order give the same string; it ends with `"}\n"`, contains no `\r`, and `json.loads` of it round-trips |
| `test_committed_baselines_well_formed` | for every discovered map: `load_baseline(name)` succeeds, and the file bytes equal `format_baseline(name, spec, files)` encoded as ASCII (no `\r`, canonical formatting) |
| `test_build_and_hash_rejects_nonempty_dir` | a `tmp_path` containing one file → `ValueError` |

### 5.3 `tests/test_validation.py` (in-process)

Each case writes its YAML to `tmp_path / "spec.yaml"` with `open(..., "w", encoding="utf-8",
newline="\n")`, runs `rc = mapgen.__main__.main(["validate", str(path)])`, reads `capsys.readouterr().out`
and asserts:

- `rc == 1`;
- the first output line equals `"%d error(s) in %s:" % (count, path)`;
- each expected line appears, in the given order, as an output line `"  " + expected` (exact match); a
  line marked *prefix* must match as a prefix of the output line;
- the output has exactly `count + 1` lines, for every case. Each error is one line (`mapgen/schema.py`
  joins the lines of a YAML syntax error with spaces), so the count assertion also guarantees that the
  line-by-line comparison covers every expected line.

Parametrize with ids equal to the case ids below.

`BASE` (the valid spec all cases start from):

```yaml
name: tiny
size: [1000, 1000]
seed: 1
teams:
  A: {base: [100, 100]}
  B: {base: [900, 900]}
```

"BASE +" means BASE followed by the given lines. "BASE with X → Y" means BASE with that text replaced.

| id | input | count | expected error line(s) |
|---|---|---|---|
| `not_mapping` | `- a`, `- b` (two lines) | 1 | `: expected a mapping, got list` |
| `empty_file` | empty file | 1 | `: expected a mapping, got NoneType` |
| `yaml_syntax` | `name: tiny` / `size: [1000, 1000` / `teams: {}` | 1 | *prefix* `<yaml>: while parsing a flow sequence`; the error is a single line labelled with the literal `<yaml>:` (not the spec path), matched by prefix |
| `unknown_top_key` | BASE + `colour: red` | 1 | `colour: unknown key (allowed: format_version, name, brief, size, seed, terrain, teams, points, regions, lines, crossings, pois, scatter, analysis, output, auto_crossings)` |
| `missing_teams` | `name: tiny` / `size: [1000, 1000]` | 1 | `teams: missing required key` |
| `bad_name` | BASE with `name: tiny` → `name: Tiny-Map` | 1 | `name: must match [a-z0-9_]+ (got 'Tiny-Map')` |
| `size_too_small` | BASE with `size: [1000, 1000]` → `size: [100, 1000]` | 1 | `size: each dimension must be 200..20000 metres (got [100, 1000])` |
| `size_not_pair` | BASE with `size: [1000, 1000]` → `size: 1000` | 1 | `size: expected [x, y] of two numbers, got 1000` |
| `seed_not_int` | BASE with `seed: 1` → `seed: abc` | 1 | `seed: expected an integer, got 'abc'` |
| `auto_crossings_not_bool` | BASE + `auto_crossings: yes` | 1 | `auto_crossings: expected true or false, got 'yes'` |
| `one_team` | `name: tiny` / `size: [1000, 1000]` / `teams:` / `  A: {base: [100, 100]}` | 1 | `teams: needs 2 or 3 teams (got 1)` |
| `base_outside_map` | BASE with `[900, 900]` → `[1500, 900]` | 1 | `teams.B.base: point [1500, 900] is outside the map [0..1000.0, 0..1000.0]` |
| `unknown_region_type` | BASE + `regions:` / `  - {type: lava, shape: blob, at: [500, 500], r: 100}` | 1 | *prefix* `regions[0].type: unknown region type 'lava' (known: ` |
| `unknown_shape` | BASE + `regions:` / `  - {type: forest, shape: circle, at: [500, 500], r: 100}` | 1 | `regions[0].shape: unknown shape 'circle' (known: blob, poly, band)` |
| `blob_missing_r` | BASE + `regions:` / `  - {type: forest, shape: blob, at: [500, 500]}` | 1 | `regions[0].r: missing required key` |
| `poly_two_points` | BASE + `regions:` / `  - {type: forest, shape: poly, points: [[100, 100], [200, 200]]}` | 1 | `regions[0].points: polygon needs at least 3 points (got 2)` |
| `fragment_out_of_range` | BASE + `regions:` / `  - {type: forest, shape: blob, at: [500, 500], r: 100, fragment: 1.5}` | 1 | `regions[0].fragment: must be in [0.0, 1.0] (got 1.5)` |
| `join_unknown_line` | BASE + `lines:` / `  - {type: river, path: [[0, 500], [1000, 500]], join: nowhere}` | 1 | `lines[0].join: no line with id 'nowhere'` |
| `disc_crossing_with_width` | BASE + `crossings:` / `  - {type: bridge, at: [500, 500], width: 20}` | 1 | ``crossings[0].width: `width` needs `path` (a disc crossing takes `at` and `r`)`` |
| `duplicate_poi_id` | BASE + `pois:` / `  - {id: a, type: capture, at: [500, 500]}` / `  - {id: a, type: capture, at: [600, 500]}` | 1 | `pois[1].id: duplicate POI id 'a' (first used at pois[0])` |
| `poi_unknown_team` | BASE + `pois:` / `  - {id: a, type: capture, at: [500, 500], team: C}` | 1 | `pois[0].team: unknown team 'C' (teams: A, B)` |
| `poi_reserved_base_id` | BASE + `pois:` / `  - {id: base_A, type: capture, at: [500, 500]}` | 1 | `pois[0].id: 'base_A' is reserved for the implicit base of team A` |
| `heightmap_res_not_pow2` | BASE + `output: {heightmap_res: 1000}` | 1 | `output.heightmap_res: must be 2^n+1, e.g. 513, 1025, 2049 (got 1000)` |
| `ref_in_region_label_at` | BASE + `points:` / `  j: [500, 500]` / `regions:` / `  - {id: r0, type: forest, shape: blob, at: [500, 500], r: 100, label_at: j}` | 1 | `regions[0].label_at: expected a point [x, y] of two numbers, got 'j' (point references are accepted only in region at/points/path, line path and crossing at/path)` |
| `duplicate_key_points` | BASE + `points:` / `  j: [5, 5]` / `  j: [6, 6]` | 1 | `<yaml>: line 10: duplicate key 'j'` |
| `duplicate_key_teams` | BASE + `  A: {base: [200, 200]}` | 1 | `<yaml>: line 8: duplicate key 'A'` |
| `duplicate_key_region` | BASE + a block-style region whose `r` key appears twice | 1 | `<yaml>: line 14: duplicate key 'r'` |
| `two_errors` | BASE with `seed: 1` → `seed: abc`, + `colour: red` | 2 | `colour: unknown key (allowed: format_version, name, brief, size, seed, terrain, teams, points, regions, lines, crossings, pois, scatter, analysis, output, auto_crossings)` then `seed: expected an integer, got 'abc'` |

Additional tests in the same file:

| test | checks |
|---|---|
| `test_valid_base_spec` | BASE → `rc == 0`, output exactly `OK: tiny (1000x1000 m, 2 teams, 0 regions, 0 lines, 0 crossings, 0 pois)\n` |
| `test_load_spec_raises_spec_error` | `mapgen.schema.load_spec` on the `two_errors` input raises `SpecError` whose `.errors` equals the two expected lines in order |
| `test_invalid_build_writes_nothing` | `mapgen.build.build(<seed_not_int spec>, str(tmp_path / "out"))` raises `SpecError` and `tmp_path / "out"` does not exist |

### 5.4 `tests/test_cli_exit_codes.py` (real subprocesses)

Helper:

```python
def run(*args, cwd=REPO_ROOT):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.run([sys.executable, "-m", "mapgen"] + [str(a) for a in args], cwd=cwd, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
    return p.returncode, p.stdout, p.stderr
```

`TINY` = BASE from 5.3, written to `tmp_path / "tiny.yaml"`; `BAD` = the `seed_not_int` input written to
`tmp_path / "bad.yaml"`; `MISSING` = `tmp_path / "missing.yaml"` (not created). Every `build` passes
`--out <tmp_path / "out">`.

| id | command | exit | output check |
|---|---|---|---|
| `validate_ok` | `validate maps/example_river_crossing.yaml` | 0 | stdout starts `OK: river_crossing (` |
| `validate_missing` | `validate MISSING` | 1 | stdout == `error: file not found: <MISSING>\n` |
| `validate_invalid` | `validate BAD` | 1 | first line `1 error(s) in <BAD>:` |
| `build_ok` | `build TINY` | 0 | `out/tiny/report.json` and `out/tiny/export/meta.json` exist; 21 files under `out/tiny` (about 6 s) |
| `build_only_analyze` | `build TINY --only analyze` | 0 | `out/tiny` holds exactly `report.json`, `report.md` |
| `build_invalid` | `build BAD` | 1 | first line `1 error(s) in <BAD>:`; `out` does not exist |
| `build_missing` | `build MISSING` | 1 | stdout == `error: file not found: <MISSING>\n` |
| `build_empty_dir` | `build <empty dir>` | 1 | stdout == `error: no *.yaml files in <dir>\n` |
| `build_dir_mixed` | `build <dir with a_ok.yaml = TINY, b_bad.yaml = BAD> --only analyze` | 1 | `out/tiny/report.json` exists; stdout contains `1 error(s) in ` and `b_bad.yaml:` |
| `probe_ok` | `probe TINY base_A 500 500` | 0 | each probed point prints one result line followed by indented detail lines (e.g. `region:`); the test counts the non-indented lines: exactly two, starting `(100, 100)  A 0:00` and `(500, 500)  `; the total output is exactly 4 lines, each result line followed by one indented detail line |
| `probe_odd_coords` | `probe TINY 500` | 1 | stdout == `error: expected x y pairs or POI ids, got '500'\n` |
| `probe_unknown_poi` | `probe TINY nosuchpoi` | 1 | stdout == `error: expected x y pairs or POI ids, got 'nosuchpoi'\n` |
| `probe_missing` | `probe MISSING 1 1` | 1 | stdout == `error: file not found: <MISSING>\n` |
| `probe_invalid` | `probe BAD 1 1` | 1 | first line `1 error(s) in <BAD>:` |
| `types_ok` | `types` | 0 | first line `regions:`; contains lines starting `lines:`, `crossings:`, `pois:`, `splat_layers: ` |
| `palette_ok` | `palette` | 0 | first line `Region fill lightness (CIE L*) and pattern:` |
| `palette_missing` | `palette --palette MISSING` | 1 | stdout == `error: file not found: <MISSING>\n` |
| `palette_invalid` | `palette --palette <file containing teams: ["#111111"]>` | 1 | stdout == `palette.teams: expected a list of at least 3 colours\n` |
| `no_command` | *(no arguments)* | 2 | stderr contains `the following arguments are required: command` |
| `unknown_command` | `frobnicate` | 2 | stderr contains `invalid choice: 'frobnicate'` |
| `build_no_spec` | `build` | 2 | stderr contains `the following arguments are required: spec` |

`<path>` in an expected message is `str()` of the path exactly as passed on the command line.

## 6. Python 3.8 constraints

The venv runs Python 3.8.0. In all new files:

- no `match` statements, no `X | Y` type unions, no builtin generics at runtime (`list[str]`,
  `dict[str, str]`); use `typing.List`, `Dict`, `Tuple`, `Optional`;
- no `str.removeprefix` / `removesuffix`, no `dict | dict`, no parenthesised multi-item `with`;
- `subprocess.run` with `stdout=PIPE, stderr=PIPE, encoding=...` (no reliance on 3.9+ additions);
- f-strings are allowed but the codebase style is `%` formatting; use `%` formatting;
- determinism: no timestamps, randomness or unordered iteration in anything written (baselines are
  `sort_keys=True`).

## 7. Documentation updates

Apply in the same commit as the suite.

**`CLAUDE.md`, section `## Verification`** — replace the section body with:

```
Run the regression suite: `.venv/Scripts/python -m pytest` (about 3 minutes). It builds the three
`maps/example_*.yaml` maps and the five `maps/concepts/*.yaml` maps into temporary directories and
compares the SHA-256 of every output file with the committed baseline in `tests/baselines/<map>.json`.
It also covers spec validation errors and command exit codes. Run one map with `-k <map name>`.

A change that must not alter output passes the suite unchanged. A change that alters output fails it,
naming each map and file. Show the owner the diff report for the change: build each affected map before and after the change
(e.g. the previous commit from a `git worktree`), each into its own empty `--out` root, and run
`.venv/Scripts/python -m mapgen diff <before>/<map> <after>/<map> --text` (agents read the JSON form,
without `--text`); once the owner approves it,
re-baseline with `.venv/Scripts/python tests/rebaseline.py <map>... | --all` and commit the new
baselines with the change. Never re-baseline without that approval. Design: `tests/REGRESSION_SPEC.md`.
```

Also in `CLAUDE.md`: the Rules bullet ``Dependencies are pinned in `requirements.txt`.`` becomes
`Dependencies are pinned exactly: runtime in requirements.txt, development (pytest) in
requirements-dev.txt.`; the "Where knowledge lives" table gains the row
`| tests/REGRESSION_SPEC.md | the regression suite: baselines, helper API, test cases, re-baselining |`.

**`README.md`**:

- `## Setup`: replace `(already created; deps in \`requirements.txt\`)` with
  `(already created; runtime dependencies pinned in \`requirements.txt\`, development dependencies in
  \`requirements-dev.txt\`: \`.venv/Scripts/python -m pip install -r requirements-dev.txt\`)`.
- `## Commands`: replace the sentence `Exit code 0 on success, 1 on spec errors.` with
  `Exit code 0 on success; 1 on spec errors, a missing file or invalid probe points; 2 on usage errors.`
- New section after `## Edit loop`:

  ```
  ## Tests

  `.venv/Scripts/python -m pytest` runs the regression suite (about 3 minutes): every committed map is
  built and compared file by file with its baseline in `tests/baselines/`, plus spec-validation and
  exit-code tests. `-k <map name>` runs one map. After the owner has approved the
  `mapgen diff` report of an output change,
  `.venv/Scripts/python tests/rebaseline.py <map>... | --all` rewrites the baselines (`--dry-run` lists
  the differing files without writing). Details: `tests/REGRESSION_SPEC.md`.
  ```

**`docs/roadmap.md`**, table "Editability and regeneration stability": the row
`| Nothing guards regeneration | ... |` becomes

```
| No spec format version | A spec cannot state which generator behaviour it was authored against (package 3). The eight committed maps are guarded by the regression suite (`tests/`): per-file reference hashes and exact dependency pins. |
```

`docs/investigations/` reports are dated records and are not edited.

## 8. Acceptance checks

Run by the orchestrator after wave B. All must hold.

1. `.venv/Scripts/python -m pytest` exits 0; the stability test reports 8 passed cases
   (`test_build_matches_baseline[natural]` … `[thornwald]`), and every other test passes.
2. **Hash tamper.** Change one hex digit of one hash in `tests/baselines/thornwald.json`
   (e.g. in `export/meta.json`); `.venv/Scripts/python -m pytest -k thornwald` fails with a message
   containing `map 'thornwald' differs from tests/baselines/thornwald.json in 1 file(s):` and
   `thornwald: export/meta.json: changed`. Restore with `git checkout -- tests/baselines/thornwald.json`.
3. **Output change.** Change `seed` in `maps/example_river_crossing.yaml` (e.g. add 1);
   `.venv/Scripts/python -m pytest -k river_crossing` fails naming `river_crossing` and changed files
   (at least `export/heightmap.r16` and `export/meta.json`). `tests/rebaseline.py --dry-run river_crossing`
   lists the same files and writes nothing (`git status` clean apart from the spec). Restore the spec with
   `git checkout`.
4. **Missing / extra file.** Delete one entry from `files` in a baseline → the failure names that path
   with `not in baseline`; add a fake entry → `missing from build`. Restore with `git checkout`.
5. **Pins.** In `requirements.txt` and `requirements-dev.txt`, every line that is not blank, not a
   comment and not `-r requirements.txt` matches
   `^[A-Za-z0-9_.-]+==[0-9][0-9A-Za-z.]*(; sys_platform == "win32")?$`. The union of pins equals
   `.venv/Scripts/python -m pip freeze` (case-insensitive, `_`/`-` equivalent).
6. **Line endings.** `git ls-files --eol maps tests/baselines` shows `i/lf w/lf attr/text eol=lf` for
   every file.
7. **Rebaseline is idempotent.** `tests/rebaseline.py --all` on a clean tree prints `<name>: unchanged`
   for all eight maps and leaves `git status` clean.
8. `out/` is untouched by the suite (its modification times do not change).

## 9. Implementation waves

Each task lists its inputs (sections of this spec) and outputs. A task touches only its listed files.
Do not commit inside a task; the orchestrator commits after acceptance (commits in this repo end with the
attribution lines the session provides).

### Wave A (parallel)

**A1 — pins and repo config.** Read sections 2.1, 4.1, 4.6, 4.7, 4.8. Create/replace `requirements.txt`,
`requirements-dev.txt`, `pytest.ini`, `.gitattributes`; append to `.gitignore`. Verify:
`.venv/Scripts/python -m pip install -r requirements-dev.txt` reports every requirement already
satisfied and installs nothing; acceptance check 5; `git ls-files --eol maps` shows `attr/text eol=lf`
and `git status` shows no modified spec files.

**A2 — helper module and rebaseline script.** Read sections 2, 3, 4.2, 4.3, 4.4, 4.5, 6. Create
`tests/conftest.py`, `tests/regress.py`, `tests/rebaseline.py`. Do not create baselines. Verify:
`.venv/Scripts/python -c "import sys; sys.path.insert(0, 'tests'); import regress; print(regress.discover_maps())"`
prints the eight pairs of section 5.2; `.venv/Scripts/python tests/rebaseline.py --help` shows the
description text; `tests/rebaseline.py` with no arguments exits 2; `tests/rebaseline.py nosuch` exits 1
with the unknown-map message.

### Baseline generation (sequential, after wave A; orchestrator or one agent)

1. `.venv/Scripts/python tests/rebaseline.py --all` (about 2 minutes). Every map prints
   `new baseline (<N> files)` with N as in the table of section 2.2.
2. `tests/baselines/three_team.json` must equal the example in section 4.4 byte for byte. If it does
   not, stop and report: the code or the environment differs from the measured state, and the cause
   must be found before baselines are committed.
3. `.venv/Scripts/python tests/rebaseline.py --all --dry-run` prints `unchanged` for all eight maps.

### Wave B (parallel, after baseline generation)

**B1 — stability and helper tests.** Read sections 4.3, 4.4, 5.1, 5.2, 6. Create
`tests/test_stability.py`, `tests/test_regress_helpers.py`. Verify:
`.venv/Scripts/python -m pytest tests/test_regress_helpers.py` passes;
`.venv/Scripts/python -m pytest -k three_team` passes.

**B2 — validation tests.** Read sections 2.3, 2.4, 5.3, 6. Create `tests/test_validation.py`. Verify:
`.venv/Scripts/python -m pytest tests/test_validation.py` passes (27 tests).

**B3 — exit-code tests.** Read sections 2.4, 5.4, 6. Create `tests/test_cli_exit_codes.py`. Verify:
`.venv/Scripts/python -m pytest tests/test_cli_exit_codes.py` passes (21 tests).

**B4 — documentation.** Read section 7. Edit `CLAUDE.md`, `README.md`, `docs/roadmap.md` exactly as
given. Preserve each file's existing line endings (`README.md` is CRLF; `CLAUDE.md` and
`docs/roadmap.md` are LF).

### Wave C (orchestrator)

Run every acceptance check in section 8, then commit all files of sections 3 and 7 in one commit.
