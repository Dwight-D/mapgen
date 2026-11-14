# mapgen diff — implementation spec

This file is the complete brief for building the `mapgen diff` command. An agent can implement any wave
below by reading only this file and the repo files it names. Repo root: `E:\GameDev\Unity\tools\mapgen`
(all paths below are relative to it). Every command runs from the repo root with the venv interpreter
`.venv/Scripts/python`.

## 1. Purpose

mapgen turns a YAML map spec into a **build directory** `<out root>/<spec name>/`: rendered images
(`map.png`, `overlay_*.png`), an analysis report (`report.md`, `report.json`) and the `export/` bundle
(heightmap, splatmaps, masks, JSON files) that Battlehounds consumes. Builds are deterministic: the same
spec, code and dependency versions give byte-identical files.

`mapgen diff <build a> <build b>` compares two build directories and emits a machine-readable delta:

- the files that differ, and the files present in only one build;
- heightmap: share of texels changed, maximum height delta in metres, bounding box of the change;
- every mask and raster: share of texels changed, count, maximum value delta, bounding box;
- every JSON file that differs: the changed JSON paths with both values, added and removed keys and
  elements, a per-pattern count, capped;
- per objective: travel-time delta per team;
- per team, per team pair and map-wide: delta of each analysis summary value in `report.json`.

It has two readers:

1. **The owner**, approving an output change under the **stability rule** (a build of an unchanged spec
   is byte-identical to its committed baseline in `tests/baselines/`; an intended change is re-baselined
   only after the owner approves a diff report). The owner reads the `--text` summary in a terminal.
2. **An agent**, at the last step of the **tune loop** (edit spec → `mapgen build --only analyze` →
   `mapgen check` → `mapgen diff`). The agent parses the JSON.

The diff reads the two directories only. It never reads a spec file and never rebuilds anything.

Later roadmap packages (`docs/roadmap.md`) rely on it:

| package | what it needs from the diff |
|---|---|
| 3 Stable feature identity, 4 Feature references | proof that `meta.json`, `spec.resolved.json`, `crossings.json` differ only in id or echoed-spec values: the per-file JSON path list and path patterns (section 5.5) |
| 5 Local forest scatter | per map: share, count and bounding box of changed forest-mask and splat texels (section 5.4) |
| 9 Per-team balance summary | new per-team values appear in `analysis.per_team` as new metric keys, no format change (section 5.6) |
| 10 Balance targets and `check`, 16 tune loop | objective time deltas and summary deltas of two `--only analyze` builds (section 2.4) |

## 2. Measured facts this spec relies on

Measured 2026-11-14 at commit `2285b68`, Windows 11, Python 3.8.0 venv in `.venv`.

### 2.1 Build directory layout

A full build of `maps/concepts/thornwald.yaml` holds 33 files (13.7 MB):

```
map.png  overlay_balance.png  overlay_cost.png  overlay_openness.png
overlay_territory.png  overlay_traffic.png  overlay_travel.png
report.json  report.md
export/crossings.json  export/heightmap.png  export/heightmap.r16  export/lines.json
export/meta.json  export/pois.json  export/regions.json  export/spec.resolved.json
export/splat_0.png  export/splat_1.png
export/masks/bridge.png  clearing.png  field.png  ford.png  forest.png  hills.png  impassable.png
             lake.png  river.png  road.png  ruins.png  town.png  trail.png  water.png
```

Other maps differ in which `export/masks/<type>.png` files exist (one per region, line and crossing type
the map uses; `water.png` and `impassable.png` always). The folder name is the spec's `name` key
(`mapgen/build.py:173`).

`mapgen build <spec> --only analyze` produces the directory `<out root>/<name>/` holding exactly
`report.json` and `report.md` (`mapgen/build.py:187-203`; `tests/test_cli_exit_codes.py:73-77`).

`build` never clears its output directory, and `report.json` / `report.md` list every file present in
it (`mapgen/build.py:192-197`). Two builds that are to be diffed each go into their own empty output
root.

### 2.2 File encodings (read from the bundle, never assumed)

| file | encoding | where the diff gets dimensions and scale |
|---|---|---|
| `export/heightmap.r16` | raw little-endian uint16 (`numpy` dtype `"<u2"`), `heightmap_res × heightmap_res`, row 0 = south, column 0 = west; file size `heightmap_res² · 2` bytes (thornwald: 1025², 2 101 250 bytes) | `export/meta.json`: `heightmap_res` (int), `height_max` (metres, thornwald `400.0`); metres = `value / 65535 · height_max` (`mapgen/export.py:98-104`) |
| `export/heightmap.png` | PNG mode `I;16`, same values as the r16, row 0 = north; `np.asarray(Image.open(p))` gives dtype `uint16` | PNG header |
| `export/splat_<k>.png` | PNG mode `RGBA`, 8-bit, `splat_res²` (1024²), row 0 = north; array shape `(H, W, 4)` | PNG header |
| `export/masks/*.png` | PNG mode `L`, 8-bit coverage, `splat_res²`, row 0 = north; values seen: 0, 64, 128, 191, 255 | PNG header |
| `map.png`, `overlay_*.png` | PNG mode `RGB` (thornwald 1969 × 1344), map area plus side panel with legend and text | not decoded (section 6.4) |
| `export/*.json`, `report.json` | UTF-8 JSON, `indent=1`; `report.json` holds no NaN or infinity (`mapgen/build.py:19-27` maps them to `null`) | — |
| `report.md` | UTF-8 Markdown | not decoded |

`export/meta.json` also carries `size` (`[size_x, size_z]` metres, floats, thornwald `[3200.0, 2800.0]`),
`name`, `splat_res`, `seed`, `spec_sha256` (SHA-256 of the raw spec bytes) and `mapgen_version`
(`mapgen/export.py:284-305`).

Raster texels are cells of the map rectangle: `mapgen/raster.py` `Grid(size_x, size_y, cols, rows)` maps
`[0, size_x] × [0, size_y]` onto `cols × rows` cells, so a texel is `size_x / cols` by `size_y / rows`
metres. The heightmap is built on the same cell grid with `res × res` cells (`mapgen/height.py:65`).

### 2.3 Where analysis values live in `report.json`

Written by `mapgen/build.py:30-54` from `mapgen/analysis.py` (objectives at lines 209-237).

| value | JSON path | type |
|---|---|---|
| team ids | keys of `/teams` | `"A"`, `"B"`, optionally `"C"` |
| objectives | `/objectives` | list in spec POI order, objective POIs only (types with `objective: true`) |
| objective id, type, owner | `/objectives/<i>/id`, `/type`, `/owner` | str, str, team id or `null` |
| objective time per team | `/objectives/<i>/times/<team>` | seconds (float) or `null` = unreachable |
| objective spread | `/objectives/<i>/spread` | `(max − min) / min` (float) for neutral objectives; `null` for owned or unreachable ones |
| objective flag | `/objectives/<i>/flag` | `""`, `"IMBALANCED"`, `"UNREACHABLE"`, `"EXPOSED"` or `"UNREACHABLE, EXPOSED"` |
| area share per team | `/area_share/<team>` | 0–1 share of map cells the team reaches first |
| contested, unreachable share | `/area_share/contested`, `/area_share/unreachable` | 0–1 |
| openness per team | `/openness_by_team/<team>` | 0–1 mean over the team's territory, or `null` |
| base-to-base time | `/base_times_s/<A-B>` | seconds or `null`; one key per team pair, `"A-B"`, `"A-C"`, `"B-C"` |
| route constriction | `/route_constriction/<A-B>` | 0–1; key present only for reachable pairs |
| route openness | `/openness_along_routes/<A-B>` | 0–1; key present only for reachable pairs |
| fair-zone share | `/fair_zone_share` | 0–1 |
| map openness | `/openness_map_mean` | 0–1 |
| warnings | `/warnings` | list of str |
| map name, size | `/name`, `/size` | str, `[x, y]` floats |

### 2.4 The measured slice

Two thornwald builds, each into its own empty root:

- **a**: `maps/concepts/thornwald.yaml` unchanged (matches `tests/baselines/thornwald.json` exactly).
- **b**: a copy of that spec where the line
  `- { id: lodge,      type: capture,      at: [2050, 1230], label: Hunter's Lodge }` reads
  `at: [2150, 1230]` (the `lodge` capture point moved 100 m east). The copy keeps `name: thornwald`.

Plus the same pair built with `--only analyze`, and a pair of `maps/example_river_crossing.yaml` builds
(unchanged, and with `cap_s` moved from `[1500, 900]` to `[1500, 1000]` and the mountain `height: 220`
changed to `240`). Results are in section 9; they were produced by a throwaway implementation of
sections 5–7.

Findings:

- Moving one POI leaves the heightmap untouched (0 texels changed) but changes the forest scatter over
  the whole map: the forest mask changes in 1766 texels with a bounding box spanning 325–3091 m east and
  271–2737 m north, and `regions.json` differs in 5056 JSON leaves (coordinates shifted by 0.01 m in both
  scatter regions). Scatter avoids POI footprints and thresholds at a map-wide quantile (roadmap gap
  "Scatter thresholds at a map-wide quantile", package 5).
- The river_crossing edit changes the heightmap in 17.2 % of texels, maximum delta 19.46 m.
- **Runtime**: the whole diff computation on the thornwald pair (hashing 33 + 33 files, decoding the 2
  changed rasters and the heightmap, JSON diffs, analysis) takes 0.22 s with warm file cache; the full
  process including interpreter start and `numpy`/`PIL` import takes 0.61 s. A self-diff takes 0.09 s
  (identical hashes skip decoding). Target: under 3 s wall clock for a 33-file build.

## 3. CLI

```
.venv/Scripts/python -m mapgen diff <build a> <build b> [--text] [--max-changes N]
```

- `<build a>`, `<build b>`: build directories (the folder holding `report.json` and/or `export/`, e.g.
  `out/thornwald`), not output roots. They may be the same path. argparse dest names: `build_a`,
  `build_b`.
- Output: the JSON report on stdout (section 5). It is the machine-readable form and the default.
- `--text`: print the human-readable summary (section 7) instead of the JSON.
  Design reason: the agent is the frequent, automated reader and needs no flag; the owner reads
  occasionally and adds `--text`.
- `--max-changes N`: maximum number of change entries listed per JSON file (default 100). `N` is an
  integer ≥ 0. Counts and patterns are always complete; only the `changes` list is capped.
- The command writes no files.
- The report (JSON and `--text`) is written to stdout as UTF-8 bytes with LF line endings on every
  platform, through `sys.stdout.buffer` (flushed before and after the write), so redirected output is
  byte-identical everywhere and ends with a single `\n`. Error messages are written with `print`
  (section 4).

## 4. Exit codes and messages

| situation | exit | output |
|---|---|---|
| report produced, builds identical | 0 | report on stdout, `"identical": true` |
| report produced, builds differ | 0 | report on stdout, `"identical": false` |
| a path does not exist | 1 | stdout `error: directory not found: <path as given>` |
| a path exists but is not a directory | 1 | stdout `error: not a directory: <path as given>` |
| a directory holds neither `report.json` nor `export/meta.json` | 1 | stdout `error: not a mapgen build directory (no report.json or export/meta.json): <path as given>` |
| missing positional, bad `--max-changes` | 2 | argparse usage on stderr, e.g. `the following arguments are required: build_b`, `argument --max-changes: expected an integer >= 0, got '-1'` |

- `<build a>` is checked before `<build b>`; the first failing check prints its single line and nothing
  else is printed.
- "Builds differ" exits 0. Design reason: `diff` is a report; the gate in the tune loop is `mapgen
  check`, and exit code 1 means an error in every mapgen command.
- A file that cannot be decoded or parsed (corrupt PNG, invalid JSON, heightmap of the wrong size) is
  reported inside its entry (`comparable: false` / `error`) and the command still exits 0.
- Messages follow the existing commands: printed with `print` to stdout, `error: ` prefix
  (`mapgen/__main__.py:19`, `tests/REGRESSION_SPEC.md` section 2.4).

## 5. JSON report

### 5.1 Serialisation and ordering rules

- `json.dumps(report, indent=1, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n"`, written
  as UTF-8 bytes with LF line endings (section 3). Every object's keys are therefore sorted.
- Before dumping, every non-finite float anywhere in the report is replaced by the string `"NaN"`,
  `"Infinity"` or `"-Infinity"` (only possible inside verbatim JSON values of section 5.5).
- No timestamps, no durations, no machine-dependent values. `a` and `b` are the arguments exactly as
  given; no other path in the report is absolute. All file paths in the report are relative to the build
  directory with `/` separators.
- Every list has a defined order, stated with its key below. The same two directories always give the
  same bytes.
- Units: shares are fractions 0–1 (never percent); times in seconds; heights and bounding boxes in
  metres; raster value deltas in raw file units (0–255 for 8-bit, 0–65535 for 16-bit).

### 5.2 Top level

| key | type | meaning |
|---|---|---|
| `diff_format` | int | report format version, `1` |
| `a`, `b` | str | the two arguments as given |
| `name_a`, `name_b` | str or null | map name: `report.json` `name`, else `export/meta.json` `name`, else null; a missing or unparseable file falls through to the next source |
| `identical` | bool | true when both builds hold the same set of files and every file has the same SHA-256 |
| `files` | object | section 5.3 |
| `heightmap` | object or null | section 5.4; null when `export/heightmap.r16` is not in both builds |
| `rasters` | list | section 5.4; one entry per raster file present in both builds, sorted by `path` |
| `json` | object | section 5.5; one entry per JSON file in `files.changed`, keyed by path |
| `analysis` | object or null | section 5.6; null when `report.json` is not readable in both builds |
| `notes` | list of str | why a section is null, and data irregularities, in this order: heightmap note, then either the analysis note or the duplicate-objective notes (a's, then b's, each in order of the id's first repetition) |

Note strings (exact): `heightmap: export/heightmap.r16 is not in both builds`,
`analysis: report.json is not readable in both builds`,
`analysis: objective id <id> occurs more than once in <a|b>; first entry used` (one note per repeated id
per build; see section 6.7).

### 5.3 `files`

| key | type | meaning |
|---|---|---|
| `added` | list of str | paths present only in b, sorted |
| `removed` | list of str | paths present only in a, sorted |
| `changed` | list of str | paths present in both with different SHA-256, sorted |
| `unchanged_count` | int | paths present in both with equal SHA-256 |

Renders (`map.png`, `overlay_*.png`), `report.md` and files of unknown kind appear here only.

### 5.4 `heightmap` and `rasters`

`heightmap` entry:

| key | type | meaning |
|---|---|---|
| `path` | str | `"export/heightmap.r16"` |
| `a`, `b` | object or null | `{"res": int, "height_max": number, "bytes": int}` read from that build's `export/meta.json` and the file size; null when that build's `meta.json` lacks the values or holds invalid ones (section 6.3) |
| `comparable` | bool | false when dimensions or scale cannot be established or differ in resolution |
| `reason` | str or null | why not comparable, else null (exact strings in section 6.3) |
| `changed_texels` | int or null | texels whose height in metres differs |
| `total_texels` | int or null | `res²` |
| `changed_share` | float or null | `changed_texels / total_texels` |
| `max_delta_m` | float or null | `max |h_b − h_a|` in metres over all texels |
| `bbox_px` | object or null | `{"row_min", "row_max", "col_min", "col_max"}` (ints, inclusive) of changed texels in file rows (row 0 = south); null when nothing changed |
| `bbox_m` | object or null | `{"x_min", "x_max", "z_min", "z_max"}` metres, each rounded to 2 decimals, outer cell edges of `bbox_px`; null when nothing changed or no map size is known |

When `comparable` is false, `changed_texels`, `total_texels`, `changed_share`, `max_delta_m`, `bbox_px`,
`bbox_m` are null.

`rasters` entry (every `export/**/*.png` present in both builds: `heightmap.png`, `splat_<k>.png`,
`masks/*.png`):

| key | type | meaning |
|---|---|---|
| `path` | str | relative path |
| `a`, `b` | object or null | `{"mode": str, "width": int, "height": int}` from the PNG header (`"L"`, `"RGBA"`, `"I;16"`); null for a side Pillow cannot open |
| `comparable` | bool | true when `a` equals `b` |
| `reason` | str or null | e.g. `format differs: L 1024x1024 vs L 512x512` |
| `changed_texels` | int or null | texels where any channel differs |
| `total_texels` | int or null | `width · height` |
| `changed_share` | float or null | `changed_texels / total_texels` |
| `max_abs_delta` | int or null | maximum absolute difference of any channel value, raw units; palette (`P`) images compare as RGBA colours |
| `pixels_identical` | bool or null | true when comparable and `changed_texels` is 0; false when comparable and `changed_texels` is above 0; null when not comparable |
| `bbox_px` | object or null | as for the heightmap, in PNG rows (row 0 = north) |
| `bbox_m` | object or null | as for the heightmap |

An unchanged raster has `changed_texels: 0`, `changed_share: 0.0`, `max_abs_delta: 0`,
`pixels_identical: true`, null boxes.

**Raster invariant.** A raster whose file hash differs between the builds is in `files.changed` and its
entry has exactly one of: `comparable: false` with a `reason`; `changed_texels` above 0; or
`changed_texels: 0` with `pixels_identical: true`, which states that the decoded pixel data are equal
while the file bytes differ (for example a metadata-only difference). An entry never reports zero change
without `pixels_identical: true`.

### 5.5 `json`

One entry per JSON file in `files.changed` (`export/*.json`, `report.json`, any other `*.json`), keyed by
path. JSON files present in only one build appear in `files.added` / `files.removed` only.

| key | type | meaning |
|---|---|---|
| `change_count` | int or null | number of change entries in the full structural diff; null on `error` |
| `truncated` | bool | `change_count > max_changes` |
| `patterns` | list | `{"pattern": str, "count": int}` per distinct path pattern, sorted by `pattern`; complete, never capped |
| `changes` | list | the first `max_changes` change entries in traversal order (section 6.6) |
| `error` | str or null | `a: <message>` or `b: <message>` when that side does not parse or cannot be opened; then `patterns` and `changes` are empty. The message holds only the build-relative `/` path, never an absolute one (section 6.4, item 1) |

Change entry:

| `op` | keys | meaning |
|---|---|---|
| `"changed"` | `op`, `path`, `a`, `b` | the value at `path` differs (different JSON type, or same type and different value) |
| `"added"` | `op`, `path`, `b` | key or list element present only in b |
| `"removed"` | `op`, `path`, `a` | key or list element present only in a |

- `path` is a JSON Pointer (RFC 6901): `""` is the document root, each step is `/` plus an object key
  (with `~` written `~0` and `/` written `~1`) or a decimal list index. Example: `/pois/7/at/0`.
- `a` / `b` are the JSON values verbatim (scalars, or whole objects/lists for an added or removed
  element, or for a type change between object/list and another type).
- A **pattern** is the path with every list-index step replaced by `*` (object keys stay as written, even
  when they look like numbers). Example: `/pois/7/at/0` → `/pois/*/at/*`. The pattern list answers "what
  kind of values changed" for the whole file: a file whose only patterns are `/*/id` changed in id values
  and nothing else.

### 5.6 `analysis`

Computed from `report.json` of both builds (section 2.3).

| key | type | meaning |
|---|---|---|
| `teams` | list of str | union of the keys of `/teams` in a and b, sorted |
| `objectives` | list | one entry per objective id: ids in a's `/objectives` order, then ids only in b in b's order |
| `per_team` | object | team id → metric name → value record |
| `per_pair` | object | pair key (`"A-B"`) → metric name → value record; pairs = union of `/base_times_s` keys, sorted |
| `map` | object | metric name → value record |
| `warnings` | object | `{"added": [str], "removed": [str]}`: warnings in b not in a (b's order), in a not in b (a's order) |

**Value record** — the one shape used for every compared analysis value:

```
{"a": <JSON value or null>, "b": <JSON value or null>, "delta": <number or null>, "changed": <bool>}
```

`delta` is `b − a` when both are numbers (int or float, not bool), else null. `changed` is true when the
two values differ under the strict comparison of section 6.6 (null vs a number is a change). A value
missing on one side is null on that side.

Objective entry:

| key | type | meaning |
|---|---|---|
| `id` | str | POI id |
| `type`, `owner` | str, str or null | from b when present in b, else from a |
| `in_a`, `in_b` | bool | presence in each build |
| `times_a`, `times_b` | object or null | team → seconds or null, copied from `/objectives/<i>/times`; null when absent from that build |
| `delta_s` | object | team → `time_b − time_a` in seconds, or null when either time is null or absent; teams = union of both `times` keys, sorted |
| `spread` | value record | spread of a and b |
| `flag_a`, `flag_b` | str or null | flag strings; null when absent from that build |
| `changed` | bool | absent from one build, or times, spread or flag differ |

Metrics (names are the keys in the report; each maps to a `report.json` path, `<team>` / `<pair>`
substituted):

| section | metric | `report.json` path |
|---|---|---|
| `per_team` | `area_share` | `/area_share/<team>` |
| `per_team` | `openness` | `/openness_by_team/<team>` |
| `per_pair` | `base_time_s` | `/base_times_s/<pair>` |
| `per_pair` | `route_constriction` | `/route_constriction/<pair>` |
| `per_pair` | `route_openness` | `/openness_along_routes/<pair>` |
| `map` | `contested_share` | `/area_share/contested` |
| `map` | `unreachable_share` | `/area_share/unreachable` |
| `map` | `fair_zone_share` | `/fair_zone_share` |
| `map` | `openness_mean` | `/openness_map_mean` |
| `map` | `warning_count` | length of `/warnings` |

**Extension rule (package 9 and later).** A new per-team value is one new row in the metric table
(`TEAM_METRICS` in `mapgen/diff.py`) naming its source path; it appears as a new metric key under each
team with the same value-record shape. A value record holds any JSON value (number, list of ids, object),
so counts, id lists and per-objective raid times all fit; numbers get a `delta`, other values a `changed`
flag. Adding keys to `per_team`, `per_pair` or `map` does not change `diff_format`.

## 6. Algorithms

### 6.1 Listing, hashing, classification

1. `list_files(root)`: walk `root` with `os.walk`; map each file's path relative to `root`, `os.sep`
   replaced by `/`, to its absolute path; return in sorted key order.
2. Hash every path present in both builds with SHA-256 (binary, 1 MiB chunks) — the same algorithm as
   `tests/regress.py:hash_file`. Files present in one build only are not hashed.
3. `file_kind(rel)` in this order:
   - `"export/heightmap.r16"` → `heightmap`
   - starts with `export/` and ends with `.png` → `raster`
   - ends with `.json` → `json`
   - ends with `.png` → `render`
   - anything else → `other`
4. `identical` = `added`, `removed`, `changed` all empty.

### 6.2 Map size for bounding boxes

`size` = b's `export/meta.json` `size` if it is a list of two numbers, else b's `report.json` `size`,
else null; a missing or unparseable `meta.json` or `report.json` falls through to the next source (then every `bbox_m` is null).

### 6.3 Heightmap

Only when `export/heightmap.r16` is in both builds. For each side, in order a then b:

- `meta` = that build's parsed `export/meta.json`. If it is missing, unparseable, or lacks a valid
  `heightmap_res` (an int above 0, not a bool) or a valid `height_max` (an int or float, not a bool,
  finite and above 0): `comparable: false`,
  `reason: "<side>: export/meta.json lacks heightmap_res or height_max"`, stop.
- If the file size is not `res · res · 2`: `comparable: false`,
  `reason: "<side>: file has <bytes> bytes, heightmap_res <res> needs <res·res·2>"`, stop.

Then if `res_a != res_b`: `reason: "heightmap_res differs: <res_a> vs <res_b>"`, not comparable.
If the two hashes are equal and `height_max_a == height_max_b`: zero change without decoding
(`changed_texels 0`, `changed_share 0.0`, `max_delta_m 0.0`). Otherwise (equal file bytes with a
different `height_max` are different heights):

```python
ha = np.fromfile(pa, dtype="<u2").reshape(res, res).astype(np.float64) / 65535.0 * height_max_a
hb = np.fromfile(pb, dtype="<u2").reshape(res, res).astype(np.float64) / 65535.0 * height_max_b
changed = ha != hb
max_delta_m = float(np.abs(hb - ha).max())
```

Comparing metres (not raw values) keeps the result right when `height_max` differs between builds.

### 6.4 Rasters and renders

For each `raster` path in both builds, sorted:

1. Read `mode` and `size` from both PNG headers (`with Image.open(p) as im:` — `im.mode`, `im.size`
   = `(width, height)`). Unequal → not comparable, `reason: "format differs: <mode_a> <w_a>x<h_a> vs
   <mode_b> <w_b>x<h_b>"`. A file that Pillow cannot open → not comparable,
   `reason: "<side>: cannot read: <exception message>"` with that side's `a`/`b` object set to null. The
   message never contains an absolute path, whether the build arguments are relative, absolute or of
   mixed separators: every spelling of the file's path inside it (as given, normalised, absolute, with
   `\` or `/` separators, escaped once or twice in a repr) is replaced by the build-relative `/` path,
   longest spelling first, e.g. `b: cannot read: cannot identify image file 'export/masks/forest.png'`.
   The same replacement applies to the `error` string of a JSON file that fails to open (section 5.5).
2. Equal hashes → zero change without decoding (`pixels_identical: true`).
3. Else, when the mode is neither `P` nor one of `1`, `L`, `LA`, `RGB`, `RGBA`, `I`, `I;16`:
   `comparable: false`, `reason: "mode <mode> cannot be compared"`.
4. Else `x = np.asarray(im_a)`, `y = np.asarray(im_b)` (after `.load()` inside the `with`; a `P` image is
   first converted with `im.convert("RGBA")`, so a palette change counts as colour change, not index change);
   `d = x != y`; if `d.ndim == 3`: `d = d.any(axis=2)`; `changed_texels = int(d.sum())`;
   `max_abs_delta = int(np.abs(x.astype(np.int64) - y.astype(np.int64)).max())`;
   `pixels_identical = (changed_texels == 0)`.

Renders (`render` kind) are compared by hash only and never decoded. Reason: they are human-only images
whose side panel and labels mix text with map content, so a pixel share does not measure a map change;
the data rasters carry the measurable change and the owner views the renders directly.

### 6.5 Bounding box

For a boolean change mask of shape `(H, W)` with at least one true texel:

```python
rows = np.flatnonzero(mask.any(axis=1)); cols = np.flatnonzero(mask.any(axis=0))
r0, r1, c0, c1 = int(rows[0]), int(rows[-1]), int(cols[0]), int(cols[-1])
cx, cz = float(size[0]) / W, float(size[1]) / H
x_min, x_max = c0 * cx, (c1 + 1) * cx
# south-first file (heightmap.r16):  z_min, z_max = r0 * cz, (r1 + 1) * cz
# north-first file (every PNG):      z_min, z_max = size[1] - (r1 + 1) * cz, size[1] - r0 * cz
```

Each metre value is `round(v, 2)`. The box is the union of the changed cells' outer edges. Its cost is
two `any` reductions over the mask (negligible).

### 6.6 JSON structural diff

`diff_json(a, b, max_changes)` walks both documents together, carrying the pointer `path` and the
`pattern`:

```python
def rec(x, y, path, pat):
    if isinstance(x, dict) and isinstance(y, dict):
        for k in sorted(set(x) | set(y)):
            p, q = path + "/" + esc(k), pat + "/" + esc(k)
            if k not in y:   emit({"op": "removed", "path": p, "a": x[k]}, q)
            elif k not in x: emit({"op": "added", "path": p, "b": y[k]}, q)
            else:            rec(x[k], y[k], p, q)
    elif isinstance(x, list) and isinstance(y, list):
        n = min(len(x), len(y))
        for i in range(n): rec(x[i], y[i], path + "/%d" % i, pat + "/*")
        for i in range(n, len(x)): emit({"op": "removed", "path": path + "/%d" % i, "a": x[i]}, pat + "/*")
        for i in range(n, len(y)): emit({"op": "added", "path": path + "/%d" % i, "b": y[i]}, pat + "/*")
    elif not same(x, y):
        emit({"op": "changed", "path": path, "a": x, "b": y}, pat)
```

- `esc(k)` = `k.replace("~", "~0").replace("/", "~1")`.
- `same(x, y)` is recursive: `type(x) is type(y)` and, for objects, equal key sets with `same` values;
  for lists, equal length with `same` elements by position; for scalars, `x == y` or both NaN floats.
  So `1` vs `1.0` and `true` vs `1` are changes at any depth (`{"k": 1}` vs `{"k": 1.0}` differ,
  `[NaN]` vs `[NaN]` do not); this matches byte identity of the files. Value records (section 5.6)
  and the objective comparison use the same function.
- Lists are compared by position (list order is paint order and POI order). An inserted or removed entry
  therefore shows as changes at every later index; a feature's identity across two builds is its `id` value.
- `emit` appends to the full change list and increments the pattern count. `change_count` = length of
  the full list; `changes` = its first `max_changes` entries; `truncated` = `change_count > max_changes`.
- Documents are loaded with `json.load` (UTF-8). A load error on either side yields the `error` entry.

### 6.7 Analysis

As section 5.6. Entries of `/objectives` that are not objects, or whose `id` is not a string, are
skipped. When an id occurs more than once within one build, the first entry is used and one note
`analysis: objective id <id> occurs more than once in <a|b>; first entry used` is added to the top-level
`notes` (section 5.2). Lookup of a metric path returns null when any step is missing or not an object.
Objective times, spread and flag compare with `same` (dicts compare by key and value).

## 7. Text output (`--text`)

ASCII lines, each ending `\n`, in this order. `<v>` formatting: null → `-`; bool → `true`/`false`; int →
`%d`; float → `%.6g`; str as is; list/object → `json.dumps(v, sort_keys=True)`. A value record prints as
`<name> <v a> -> <v b>` plus ` (%+.6g)` of `delta` when delta is not null. Percent = `%.4f%%` of
`100 · share`. A box prints as `x %g..%g z %g..%g`.

**No negative zero.** Any number whose formatted text reads as zero prints as positive zero: `-0.0`
(IEEE negative zero, or a small negative delta that rounds to zero under `%+.1f`) prints `+0.0`, a
`%+.6g` delta prints `+0`, an unsigned `%.6g` or `%g` value prints `0`.

```
mapgen diff
  a: <a>
  b: <b>
identical: yes|no
files: <n> changed, <n> added, <n> removed, <n> unchanged
  changed  <path>                        (one line per path; "%-8s %s" % (key, path))
  added    <path>
  removed  <path>
heightmap: <pct> of <total> texels changed, max delta <%.2f> m[, <box>]
rasters: <k> of <n> changed
  <path>  <pct> (<count> texels), max delta <int>[, <box>]   (rasters in `files.changed` or not comparable only)
  <path>  not comparable: <reason>
  <path>  pixels identical, file bytes differ
json: <n> file(s) changed
  <path>: <count> change(s)
    <pattern>  x<count>                  (first 10 patterns)
    ... <k> more pattern(s)              (when more than 10)
  <path>: unreadable: <error>
objectives: <k> of <n> changed (time delta b - a, seconds)
  <id>  <team> <%+.1f or ->  <team> ...  <spread value record>[  flag <a or ok> -> <b or ok>]
  <id>  only in a|b
per team:
  <team>  <metric record>  <metric record> ...     (metrics sorted by name)
per pair:
  <pair>  <metric record> ...
map: <metric record>  <metric record> ...
warning added: <text>
warning removed: <text>
note: <note>
```

- The heightmap line reads `heightmap: not in both builds` when the entry is null, and
  `heightmap: not comparable: <reason>` when not comparable.
- Objective lines list only objectives with `changed: true`; the flag part appears only when the flags
  differ (empty flag prints `ok`).
- The section from `objectives:` to the warning lines is omitted when `analysis` is null.

## 8. Module layout and API

### 8.1 `mapgen/diff.py`

Imports: standard library, `numpy`, `PIL.Image` only (no `shapely`, `skimage`, other mapgen modules), so
the command starts fast. Module docstring: `"""Build-to-build diff (mapgen diff): compare two build
directories and report what changed. Design: mapgen/DIFF_SPEC.md."""`

```python
DIFF_FORMAT = 1
DEFAULT_MAX_CHANGES = 100

class DiffError(Exception):
    """Invalid build directory; str(e) is the exact one-line message of section 4."""

def check_build_dir(path: str) -> None              # raises DiffError
def list_files(root: str) -> Dict[str, str]          # 6.1
def sha256_file(path: str) -> str                    # 6.1
def file_kind(rel: str) -> str                       # 6.1
def json_same(x, y) -> bool                          # 6.6 `same`
def diff_json(a, b, max_changes: int) -> dict        # 5.5 entry, "error": None
def diff_heightmap(...) -> dict                      # 6.3
def diff_raster(...) -> dict                         # 6.4
def diff_analysis(report_a: dict, report_b: dict) -> dict   # 5.6
def diff_builds(a: str, b: str, max_changes: int = DEFAULT_MAX_CHANGES) -> dict  # whole report, 5.2
def format_json(report: dict) -> str                 # 5.1
def format_text(report: dict) -> str                 # 7
```

- `diff_json` returns `{"change_count", "truncated", "patterns", "changes", "error": None}`.
- `diff_builds` calls `check_build_dir(a)` then `check_build_dir(b)` first, then computes everything.
- The metric tables are module-level lists of `(metric name, path tuple)` with the placeholders
  `"<team>"` / `"<pair>"`: `TEAM_METRICS`, `PAIR_METRICS`, `MAP_METRICS` (section 5.6). `warning_count`
  is added to `map` after `MAP_METRICS`.
- `diff_heightmap` and `diff_raster` signatures are free; they take the relative path, both absolute
  paths, whether the hashes are equal, the map size, and (heightmap) both parsed `meta.json` objects.
- Each JSON file is parsed at most once per side (`meta.json` and `report.json` are used by several
  sections).

### 8.2 `mapgen/__main__.py`

- Module docstring: `"""mapgen command line: build | validate | probe | diff | types | palette."""`.
- The `diff` parser, registered after `probe`:

```python
df = sub.add_parser("diff", help="compare two build directories (JSON on stdout; --text for a summary)")
df.add_argument("build_a")
df.add_argument("build_b")
df.add_argument("--text", action="store_true", help="print a human-readable summary instead of JSON")
df.add_argument("--max-changes", type=_non_negative_int, default=100,
                help="change entries listed per JSON file (default 100)")
df.set_defaults(func=cmd_diff)
```

- `_non_negative_int(s)`: `int(s)`; on `ValueError` or a negative value raise
  `argparse.ArgumentTypeError("expected an integer >= 0, got %r" % s)` (argparse then prints
  `argument --max-changes: expected an integer >= 0, got '-1'` and exits 2).
- `cmd_diff(args) -> int`: import `diff` lazily inside the function (`from . import diff as diffmod`, as
  `cmd_build` and `cmd_probe` import lazily); call `diffmod.diff_builds(args.build_a, args.build_b,
  args.max_changes)`; on `DiffError` print `str(e)` and return 1; else
  write `diffmod.format_text(r) if args.text else diffmod.format_json(r)` to stdout as UTF-8 bytes with
  LF endings (section 3) and return 0.

## 9. Worked example (measured)

### 9.1 thornwald, `lodge` moved 100 m east

```
.venv/Scripts/python -m mapgen build maps/concepts/thornwald.yaml --out <tmp>/a
.venv/Scripts/python -m mapgen build <tmp>/thornwald_moved.yaml --out <tmp>/b
.venv/Scripts/python -m mapgen diff <tmp>/a/thornwald <tmp>/b/thornwald
```

(`<tmp>/thornwald_moved.yaml` as in section 2.4; `<tmp>/a` and `<tmp>/b` empty before the builds.)
Expected report, by section (`a`, `b` are the arguments as given):

`diff_format` 1, `name_a` / `name_b` `"thornwald"`, `identical` false, `notes` `[]`.

`files`:

```json
{"added": [], "removed": [], "unchanged_count": 18,
 "changed": ["export/masks/forest.png", "export/meta.json", "export/pois.json", "export/regions.json",
             "export/spec.resolved.json", "export/splat_0.png", "map.png", "overlay_balance.png",
             "overlay_cost.png", "overlay_openness.png", "overlay_territory.png", "overlay_traffic.png",
             "overlay_travel.png", "report.json", "report.md"]}
```

`heightmap`:

```json
{"path": "export/heightmap.r16", "comparable": true, "reason": null,
 "a": {"bytes": 2101250, "height_max": 400.0, "res": 1025},
 "b": {"bytes": 2101250, "height_max": 400.0, "res": 1025},
 "changed_texels": 0, "total_texels": 1050625, "changed_share": 0.0, "max_delta_m": 0.0,
 "bbox_px": null, "bbox_m": null}
```

`rasters`: 17 entries (`export/heightmap.png`, 14 masks, `splat_0.png`, `splat_1.png`); two changed:

```json
{"path": "export/masks/forest.png", "a": {"height": 1024, "mode": "L", "width": 1024},
 "b": {"height": 1024, "mode": "L", "width": 1024}, "comparable": true, "reason": null,
 "changed_texels": 1766, "total_texels": 1048576, "changed_share": 0.0016841888427734375,
 "max_abs_delta": 255, "pixels_identical": false,
 "bbox_px": {"col_max": 988, "col_min": 104, "row_max": 924, "row_min": 23},
 "bbox_m": {"x_max": 3090.62, "x_min": 325.0, "z_max": 2737.11, "z_min": 270.7}}
{"path": "export/splat_0.png", "a": {"height": 1024, "mode": "RGBA", "width": 1024}, ...,
 "changed_texels": 819, "total_texels": 1048576, "changed_share": 0.0007810592651367188,
 "max_abs_delta": 255, "pixels_identical": false,
 "bbox_px": {"col_max": 989, "col_min": 104, "row_max": 924, "row_min": 23},
 "bbox_m": {"x_max": 3093.75, "x_min": 325.0, "z_max": 2737.11, "z_min": 270.7}}
```

Every other raster: `changed_texels 0`, `changed_share 0.0`, `max_abs_delta 0`, `pixels_identical` true, null boxes
(`export/heightmap.png` has `mode "I;16"`, 1025 × 1025).

`json` (default `--max-changes 100`):

| file | `change_count` | `truncated` | `patterns` |
|---|---|---|---|
| `export/meta.json` | 1 | false | `/spec_sha256` ×1 |
| `export/pois.json` | 2 | false | `/*/x` ×1, `/*/y` ×1 |
| `export/regions.json` | 5056 | true | `/*/polygons/*` ×1, `/*/polygons/*/holes/*` ×6, `/*/polygons/*/holes/*/*` ×1, `/*/polygons/*/holes/*/*/*` ×219, `/*/polygons/*/outer/*` ×449, `/*/polygons/*/outer/*/*` ×4380 |
| `export/spec.resolved.json` | 1 | false | `/pois/*/at/*` ×1 |
| `report.json` | 16 | false | `/area_share/B`, `/area_share/contested`, `/chokepoints/*/constriction`, `/chokepoints/*/near`, `/chokepoints/*/score`, `/chokepoints/*/x`, `/chokepoints/*/y`, `/fair_zone_share`, `/openness_by_team/A`, `/openness_by_team/B`, `/openness_map_mean` ×1 each; `/objectives/*/spread` ×2, `/objectives/*/times/A` ×2, `/objectives/*/times/B` ×1 |

`export/pois.json` `changes` (index 7 is `lodge`; `y` is the terrain height at the new point):

```json
[{"a": 2050.0, "b": 2150.0, "op": "changed", "path": "/7/x"},
 {"a": 35.63, "b": 35.3, "op": "changed", "path": "/7/y"}]
```

`export/meta.json` `changes`: `[{"op": "changed", "path": "/spec_sha256", "a":
"a94ed6e25f19c475a9a0b4f08fcc4737ef80394849fb2a1213333acf12d06121", "b":
"40eb9f8f3d4ebe2d7c6bf275e9d32420b08a65dc4bf50aa25749bab2dc211172"}]`.
`export/spec.resolved.json` `changes`: `[{"op": "changed", "path": "/pois/7/at/0", "a": 2050.0, "b": 2150.0}]`.
`export/regions.json` `changes` begins `{"op": "changed", "path": "/0/polygons/0/holes/0/2/0", "a": 550.5,
"b": 550.51}`; all changed leaves are in elements 0 (`scatter0`) and 13 (`scatter1`).

`analysis.teams` `["A", "B"]`. Objectives in order `thornhold, lea_keep, abbey_camp, barrow,
barrow_relic, lodge, charcoal, supply_a, supply_b`; `changed` is true for `lea_keep` and `lodge` only:

```json
{"id": "lodge", "type": "capture", "owner": null, "in_a": true, "in_b": true, "changed": true,
 "times_a": {"A": 284.47162474105437, "B": 275.495974784226},
 "times_b": {"A": 304.47162474105437, "B": 276.07214132520704},
 "delta_s": {"A": 20.0, "B": 0.5761665409810348},
 "spread": {"a": 0.03257996768866867, "b": 0.10286979077107729, "changed": true, "delta": 0.07028982308240861},
 "flag_a": "", "flag_b": ""}
{"id": "lea_keep", "type": "keep_site", "owner": null, "in_a": true, "in_b": true, "changed": true,
 "times_a": {"A": 419.316112371372, "B": 360.34912749690335},
 "times_b": {"A": 418.64159577096586, "B": 360.34912749690335},
 "delta_s": {"A": -0.6745166004061502, "B": 0.0},
 "spread": {"a": 0.16363848383391852, "b": 0.16176664192024012, "changed": true, "delta": -0.001871841913678396},
 "flag_a": "", "flag_b": ""}
```

(`lodge` is 100 m further from A along cost-1 ground at 5 m/s: +20.0 s. `lea_keep` moves because the
forest scatter changed.) Owned objectives (`supply_a`, `supply_b`) have `spread` `{"a": null, "b": null,
"changed": false, "delta": null}`.

`per_team`:

| team | metric | a | b | delta | changed |
|---|---|---|---|---|---|
| A | `area_share` | 0.4678125 | 0.4678125 | 0.0 | false |
| A | `openness` | 0.20635725557804108 | 0.20639541745185852 | 3.816187381744385e-05 | true |
| B | `area_share` | 0.43753348214285714 | 0.43758928571428574 | 5.580357142859205e-05 | true |
| B | `openness` | 0.3950101137161255 | 0.39572668075561523 | 0.0007165670394897461 | true |

`per_pair["A-B"]`: `base_time_s` 435.31390993758 both (delta 0.0, unchanged), `route_constriction`
0.10204081632653056 both, `route_openness` 0.6898841857910156 both.

`map`: `contested_share` 0.08233258928571428 → 0.08227678571428572 (delta −5.580357142856429e-05),
`fair_zone_share` 0.22736607142857143 → 0.22745535714285714 (8.928571428570842e-05), `openness_mean`
0.3020261228084564 → 0.30218908190727234 (0.00016295909881591797), `unreachable_share`
0.012321428571428572 unchanged, `warning_count` 0 → 0 (delta 0, unchanged). `warnings` `{"added": [],
"removed": []}`.

`--text` output (the `changed` file lines abbreviated here to the first two):

```
mapgen diff
  a: <tmp>/a/thornwald
  b: <tmp>/b/thornwald
identical: no
files: 15 changed, 0 added, 0 removed, 18 unchanged
  changed  export/masks/forest.png
  changed  export/meta.json
  ...
heightmap: 0.0000% of 1050625 texels changed, max delta 0.00 m
rasters: 2 of 17 changed
  export/masks/forest.png  0.1684% (1766 texels), max delta 255, x 325..3090.62 z 270.7..2737.11
  export/splat_0.png  0.0781% (819 texels), max delta 255, x 325..3093.75 z 270.7..2737.11
json: 5 file(s) changed
  export/meta.json: 1 change(s)
    /spec_sha256  x1
  export/pois.json: 2 change(s)
    /*/x  x1
    /*/y  x1
  export/regions.json: 5056 change(s)
    /*/polygons/*  x1
    /*/polygons/*/holes/*  x6
    /*/polygons/*/holes/*/*  x1
    /*/polygons/*/holes/*/*/*  x219
    /*/polygons/*/outer/*  x449
    /*/polygons/*/outer/*/*  x4380
  export/spec.resolved.json: 1 change(s)
    /pois/*/at/*  x1
  report.json: 16 change(s)
    /area_share/B  x1
    /area_share/contested  x1
    /chokepoints/*/constriction  x1
    /chokepoints/*/near  x1
    /chokepoints/*/score  x1
    /chokepoints/*/x  x1
    /chokepoints/*/y  x1
    /fair_zone_share  x1
    /objectives/*/spread  x2
    /objectives/*/times/A  x2
    ... 4 more pattern(s)
objectives: 2 of 9 changed (time delta b - a, seconds)
  lea_keep  A -0.7  B +0.0  spread 0.163638 -> 0.161767 (-0.00187184)
  lodge  A +20.0  B +0.6  spread 0.03258 -> 0.10287 (+0.0702898)
per team:
  A  area_share 0.467813 -> 0.467813 (+0)  openness 0.206357 -> 0.206395 (+3.81619e-05)
  B  area_share 0.437533 -> 0.437589 (+5.58036e-05)  openness 0.39501 -> 0.395727 (+0.000716567)
per pair:
  A-B  base_time_s 435.314 -> 435.314 (+0)  route_constriction 0.102041 -> 0.102041 (+0)  route_openness 0.689884 -> 0.689884 (+0)
map: contested_share 0.0823326 -> 0.0822768 (-5.58036e-05)  fair_zone_share 0.227366 -> 0.227455 (+8.92857e-05)  openness_mean 0.302026 -> 0.302189 (+0.000162959)  unreachable_share 0.0123214 -> 0.0123214 (+0)  warning_count 0 -> 0 (+0)
```

### 9.2 Same pair, self-diff and `--only analyze`

- `diff <tmp>/a/thornwald <tmp>/a/thornwald`: exit 0, `identical` true, `files` `{"added": [],
  "removed": [], "changed": [], "unchanged_count": 33}`, heightmap and all 17 rasters zero, `json` `{}`,
  every objective `changed` false with every `delta_s` 0.0, `notes` `[]`.
- Both specs built with `--only analyze` (directories hold `report.json`, `report.md`): `files.changed`
  `["report.json", "report.md"]`, `unchanged_count` 0, `heightmap` null, `rasters` `[]`, `json` holds
  `report.json` only (16 changes, the patterns of 9.1), analysis identical to 9.1, `notes`
  `["heightmap: export/heightmap.r16 is not in both builds"]`.
- Full build a against the `--only analyze` build of the same spec: 31 paths in `files.removed`,
  `files.changed` `["report.json", "report.md"]`; `report.json` differs only in `/files/*` (33 changes:
  the `files` list in the report names the files present at build time).

### 9.3 river_crossing, `cap_s` moved and mountain raised (integration test data)

Edit of `maps/example_river_crossing.yaml`: `at: [1500, 900] }` → `at: [1500, 1000] }` (`cap_s`) and
`width: 450, height: 220 }` → `width: 450, height: 240 }` (the mountain band). Measured diff:

- 20 changed files, 11 unchanged, none added or removed. Changed:
  `export/crossings.json, export/heightmap.png, export/heightmap.r16, export/lines.json,
  export/masks/impassable.png, export/meta.json, export/pois.json, export/regions.json,
  export/spec.resolved.json, export/splat_0.png, export/splat_1.png`, the 7 renders, `report.json`,
  `report.md`.
- heightmap: 180 966 of 1 050 625 texels (share 0.17224604402141583), `max_delta_m`
  19.464408331425943, `bbox_m` x 664.39–2300.49, z 0.0–3000.0.
- rasters changed: `heightmap.png` 180 966 (max delta 3189), `masks/impassable.png` 110 (x 1491.21–1529.3,
  z 1918.95–3000.0), `splat_0.png` 150, `splat_1.png` 1245; the 12 other masks unchanged.
- json patterns: `meta.json` `/spec_sha256` ×1; `spec.resolved.json` `/pois/*/at/*` ×1,
  `/regions/*/height` ×1 with changes `/pois/6/at/1` 900.0 → 1000.0 and `/regions/2/height` 220.0 →
  240.0; `pois.json` `/*/y` ×2, `/*/z` ×1 (`/6/z` 900.0 → 1000.0); `regions.json` `/*/height` ×1;
  `crossings.json` `/*/y` ×2; `lines.json` 442 changes (`/*/points/*/*` ×293, `/*/water_surface/*`
  ×149); `report.json` 41 changes.
- objectives in order `bridge_keep, camp_ne, camp_sw, relic, cap_s`, all `changed` true; `cap_s`
  `delta_s` A −22.740593176743232, B −22.740593176743175; `relic` A +3.5085233335275348, B
  +3.911734589510843. `bridge_keep`, `camp_ne`, `camp_sw` carry flag `IMBALANCED` in both builds.
- per team: A unchanged; B `area_share` delta −7.777777777778772e-05, `openness` delta
  −1.6450881958007812e-05. No warnings in either build.

## 10. Tests

Two files under `tests/` in the existing pytest suite (`tests/REGRESSION_SPEC.md`; `pytest.ini`
anchors the rootdir; `tests/conftest.py` puts the repo root and `tests/` on `sys.path`). Use `tmp_path`
/ `tmp_path_factory` for every file written. No test writes into `out/`, `maps/` or `tests/baselines/`.

### 10.1 `tests/test_diff.py` (in-process)

Docstring: `"""mapgen diff: report structure, raster/JSON/analysis logic on synthetic builds, and an
integration build of the river_crossing example. See mapgen/DIFF_SPEC.md."""`

**Synthetic build helper** `make_build(root, **overrides) -> str` writes a minimal build directory and
returns its path (as `str`). Defaults:

- `export/meta.json`: `{"name": "syn", "size": [400.0, 200.0], "height_max": 100.0, "heightmap_res": 5,
  "splat_res": 4}`
- `export/heightmap.r16`: `(np.arange(25, dtype=np.uint16) * 1000).reshape(5, 5)` written with
  `.astype("<u2").tobytes()`
- `export/heightmap.png`: the same array flipped vertically, `Image.fromarray(arr, mode="I;16")`
- `export/masks/forest.png`: `np.zeros((4, 4), np.uint8)`, mode `L`
- `export/splat_0.png`: `(4, 4, 4)` uint8, channel 0 = 255, others 0, mode `RGBA`
- `map.png`: `(8, 8, 3)` uint8 zeros, mode `RGB`
- `report.md`: `"# syn\n"`
- `report.json` = `REPORT`:

```python
REPORT = {
    "name": "syn", "size": [400.0, 200.0], "warnings": [],
    "teams": {"A": {"name": "A", "base": [10.0, 10.0]}, "B": {"name": "B", "base": [390.0, 190.0]}},
    "base_times_s": {"A-B": 100.0},
    "area_share": {"A": 0.5, "B": 0.4, "contested": 0.1, "unreachable": 0.0},
    "objectives": [{"id": "o1", "type": "capture", "owner": None, "times": {"A": 50.0, "B": 60.0},
                    "spread": 0.2, "flag": ""}],
    "route_constriction": {"A-B": 0.1}, "openness_along_routes": {"A-B": 0.5},
    "openness_map_mean": 0.6, "openness_by_team": {"A": 0.7, "B": None}, "fair_zone_share": 0.3,
}
```

Overrides replace one file's content (array or dict), or remove it when given `None`. JSON is written
with `json.dump(..., indent=1)`, UTF-8, `newline="\n"`.

| test | setup | checks |
|---|---|---|
| `test_self_diff_zero` | one build; `diff_builds(p, p)` | `identical` true; `files` == `{"added": [], "removed": [], "changed": [], "unchanged_count": 8}`; heightmap `changed_texels` 0, `max_delta_m` 0.0; every raster `changed_texels` 0; `json` == `{}`; objective `o1` `changed` false, `delta_s` `{"A": 0.0, "B": 0.0}`; `notes` `[]` |
| `test_copy_diff_zero` | two builds with defaults in different dirs | same as above |
| `test_added_removed_files` | b: `map.png` removed, extra `export/masks/road.png` (4 × 4 L zeros) | `files.added == ["export/masks/road.png"]`, `files.removed == ["map.png"]`, `identical` false; no raster entry for `road.png` |
| `test_heightmap_delta_and_bbox` | b: r16 texel `[1, 3]` = 8000 + 655 | heightmap `changed_texels` 1, `total_texels` 25, `changed_share` 0.04, `max_delta_m` == approx(655 / 65535 · 100), `bbox_px` `{"row_min": 1, "row_max": 1, "col_min": 3, "col_max": 3}`, `bbox_m` `{"x_min": 240.0, "x_max": 320.0, "z_min": 40.0, "z_max": 80.0}` |
| `test_heightmap_height_max_change` | b: `meta.json` `height_max` 200.0, r16 identical | every texel except the value-0 texel counts as changed: `changed_texels` 24; `max_delta_m` == approx(24000 / 65535 · 100) |
| `test_heightmap_res_mismatch` | b: `heightmap_res` 3 and a 3 × 3 r16 | `comparable` false, `reason` `"heightmap_res differs: 5 vs 3"`, `changed_texels` null |
| `test_heightmap_wrong_size` | b: r16 with 48 bytes | `comparable` false, `reason` `"b: file has 48 bytes, heightmap_res 5 needs 50"` |
| `test_heightmap_absent` | both without `heightmap.r16` | `heightmap` null; `notes == ["heightmap: export/heightmap.r16 is not in both builds"]` |
| `test_mask_share_and_bbox` | b: forest `[0, 1]` = 255, `[2, 2]` = 128 | `changed_texels` 2, `total_texels` 16, `changed_share` 0.125, `max_abs_delta` 255, `bbox_px` `{"row_min": 0, "row_max": 2, "col_min": 1, "col_max": 2}`, `bbox_m` `{"x_min": 100.0, "x_max": 300.0, "z_min": 50.0, "z_max": 200.0}` |
| `test_rgba_any_channel` | b: splat `[3, 0, 1]` = 10 | `changed_texels` 1, `max_abs_delta` 10, `bbox_m` `{"x_min": 0.0, "x_max": 100.0, "z_min": 0.0, "z_max": 50.0}` |
| `test_raster_format_mismatch` | b: forest 2 × 2 L | `comparable` false, `reason` `"format differs: L 4x4 vs L 2x2"`, `b` `{"mode": "L", "width": 2, "height": 2}` |
| `test_render_hash_only` | b: `map.png` one pixel changed | `"map.png"` in `files.changed`; no `rasters` entry for it; not in `json` |
| `test_json_diff_ops_and_order` | `diff_json({"k": 1, "l": [1, 2, 3], "m": {"x": 1}, "n": 1}, {"k": 2, "l": [1, 5], "m": {"x": 1, "y": 2}, "o": 1.0}, 100)` | `changes` == `[{"op": "changed", "path": "/k", "a": 1, "b": 2}, {"op": "changed", "path": "/l/1", "a": 2, "b": 5}, {"op": "removed", "path": "/l/2", "a": 3}, {"op": "added", "path": "/m/y", "b": 2}, {"op": "removed", "path": "/n", "a": 1}, {"op": "added", "path": "/o", "b": 1.0}]`; `patterns` == `[{"pattern": "/k", "count": 1}, {"pattern": "/l/*", "count": 2}, {"pattern": "/m/y", "count": 1}, {"pattern": "/n", "count": 1}, {"pattern": "/o", "count": 1}]`; `change_count` 6, `truncated` false, `error` null |
| `test_json_strict_types` | `diff_json({"v": 1}, {"v": 1.0}, 100)`, `diff_json({"v": True}, {"v": 1}, 100)`, `diff_json([float("nan")], [float("nan")], 100)` | 1, 1 and 0 changes |
| `test_json_pointer_escaping` | `diff_json({"a/b~": 1}, {"a/b~": 2}, 100)`; `diff_json({"0": 1}, {"0": 2}, 100)` | path `/a~1b~0`; second: pattern `/0` (key, not wildcarded) |
| `test_json_cap` | `diff_json(list(range(150)), [v + 1 for v in range(150)], 100)` and the same with cap 0 | `change_count` 150, `truncated` true, 100 entries, first path `/0`; cap 0: `changes` `[]`, `truncated` true, patterns `[{"pattern": "/*", "count": 150}]` |
| `test_json_files_in_report` | b: `meta.json` with `spec_sha256` added | `json["export/meta.json"]["changes"] == [{"op": "added", "path": "/spec_sha256", "b": "<value>"}]` |
| `test_json_unreadable` | b: `export/pois.json` = `"{"`, a: `export/pois.json` = `[]` | entry `error` starts with `"b: "`, `change_count` null, `changes` `[]` |
| `test_objective_deltas` | b: `o1` times A 45.0; plus objective `o2` only in b (times A 10.0, B null) | `o1` `delta_s` `{"A": -5.0, "B": 0.0}`, `changed` true; `o2` `in_a` false, `times_a` null, `delta_s` `{"A": None, "B": None}`, `changed` true; order `["o1", "o2"]` |
| `test_team_pair_map_metrics` | b: `area_share.A` 0.6, `openness_by_team.B` 0.8, `warnings` `["w1"]` | `per_team.A.area_share` == `{"a": 0.5, "b": 0.6, "delta": approx(0.1), "changed": True}`; `per_team.B.openness` `{"a": None, "b": 0.8, "delta": None, "changed": True}`; `per_pair["A-B"].base_time_s.changed` false; `map.warning_count` `{"a": 0, "b": 1, "delta": 1, "changed": True}`; `warnings` `{"added": ["w1"], "removed": []}`; `teams` `["A", "B"]` |
| `test_analysis_absent` | a without `report.json` | `analysis` null; `notes == ["analysis: report.json is not readable in both builds"]`; `name_a` `"syn"` (from `meta.json`) |
| `test_report_only_dirs` | both builds hold only `report.json` and `report.md` (all export files and `map.png` removed) | `heightmap` null, `rasters` `[]`, analysis present |
| `test_check_build_dir_errors` | missing path; a file; an empty dir | `DiffError` with exactly `error: directory not found: <p>`, `error: not a directory: <p>`, `error: not a mapgen build directory (no report.json or export/meta.json): <p>` |
| `test_format_json_deterministic` | the same pair (changed mask, changed report, unreadable splat PNG) built in two different absolute locations, diffed with the relative arguments `a`, `b` from each location, twice | all four `format_json` strings equal (a separate `diff_builds` call each time); ends with `"}\n"`; no `"\r"`; `json.loads` round-trips; top-level keys == `["a", "analysis", "b", "diff_format", "files", "heightmap", "identical", "json", "name_a", "name_b", "notes", "rasters"]`; `a`, `b` equal the arguments; the absolute location appears nowhere in the text |
| `test_paths_do_not_enter_report_apart_from_arguments` | the same pair in two locations, absolute arguments | after removing `a` and `b`, the two parsed reports are equal |
| `test_unreadable_png_reason_is_build_relative` | b: forest = garbage bytes; arguments relative (with `cwd` at the build parent), absolute, and absolute with `/` separators | `reason == "b: cannot read: cannot identify image file 'export/masks/forest.png'"`; no absolute path in it |
| `test_json_os_error_is_build_relative` | b's JSON file raises `PermissionError` on text open (`builtins.open` patched); the three argument forms | `error == "b: [Errno 13] Permission denied: 'export/pois.json'"` |
| `test_heightmap_invalid_meta_is_not_comparable` | `heightmap_res` 0, -5, `"5"`, true, 5.0, null, `[5]`; `height_max` `"x"`, null, NaN, Infinity, 0, -1.0, true, `10**400`; on side a and on side b | `comparable` false, `reason == "<side>: export/meta.json lacks heightmap_res or height_max"`, `changed_texels` null |
| `test_heightmap_meta_lacks_key` / `test_heightmap_meta_unparseable` | b's `meta.json` without `heightmap_res`, without `height_max`, and `"{"` | same reason, `b` null |
| `test_objective_id_not_string_is_skipped` | objectives with ids 5, null, list, object, true, plus `o1` | ids `["o1"]`, no exception |
| `test_duplicate_objective_ids` | a: `o1` ×2, `o2` ×3; b: `o1` ×3; no `heightmap.r16` | first entries used; `notes` == the heightmap note, then `analysis: objective id o1 occurs more than once in a; first entry used`, the same for `o2` in a, then for `o1` in b; `--text` prints each as `note: ...` |
| `test_record_strict_nested` | `_record` on `{"k": 1}` vs `{"k": 1.0}`, `[true]` vs `[1]`, nested lists and objects, `[NaN]` vs `[NaN]`, key order, length differences, `None` vs `1` | changed exactly where the strict rules say; `[NaN]` unchanged; `None` vs `1`: `delta` null, `changed` true |
| `test_objective_times_strict_types` | b: a time `50` where a has `50.0` | `delta_s` 0.0, objective `changed` true |
| `test_palette_png_compared_by_colour` / `test_palette_png_same_colours_different_indices` | mode `P` PNGs with the same indices and different palettes; with different indices and equal colours | 16 texels changed, `max_abs_delta` 255, `a.mode` `"P"`; 0 texels changed, `pixels_identical` true |
| `test_raster_metadata_only_difference_is_explicit` | b: same pixels, extra PNG text chunk | path in `files.changed`; `comparable` true, `changed_texels` 0, `pixels_identical` true; `--text` lists `pixels identical, file bytes differ`, `rasters: 1 of 3 changed` |
| `test_raster_hash_differs_is_never_silently_zero` | the `test_mask_share_and_bbox` pair | raster invariant of section 5.4 for every entry; `pixels_identical` null exactly for not-comparable entries |
| `test_raster_mode_not_numerically_comparable` | `_NUMERIC_MODES` patched to exclude `L` | `comparable` false, `reason == "mode L cannot be compared"`, `pixels_identical` null; `--text` line `not comparable: mode L cannot be compared` |
| `test_other_png_modes_compare` | PNG modes `1`, `LA`, `L` with one pixel differing | `comparable` true, `changed_texels` 1 |
| `test_team_in_one_build_only` | b: team `C` with `area_share` and `openness` | `teams == ["A", "B", "C"]`; `per_team.C.area_share == {"a": None, "b": 0.1, "delta": None, "changed": True}`; reversed arguments mirror it |
| `test_objective_only_in_a` / `test_objective_only_in_b` | `o1` missing from b / from a | `in_a`/`in_b`, null times and flag on the missing side, `delta_s` values null, `changed` true; `--text` line `  o1  only in a` / `  o1  only in b` |
| `test_null_time_against_number` | b: `o1` time B null | `delta_s` `{"A": 0.0, "B": None}`, `changed` true; `--text` line starts `  o1  A +0.0  B -  ` |
| `test_non_finite_values_serialised_as_strings` | `pois.json` `[1.0, 2.0, 3.0, NaN]` vs `[NaN, Infinity, -Infinity, NaN]` | `b` values `"NaN"`, `"Infinity"`, `"-Infinity"`; 3 changes; no bare constant token in the output |
| `test_json_truncated_boundary` / `test_json_truncated_cap_and_cap_plus_one` | `diff_json` with `change_count` equal to the cap, and cap + 1 | `truncated` false at the cap, true at cap + 1; `changes` length `min(cap, change_count)` |
| `test_text_not_comparable_raster` | b: forest 2 × 2 | `rasters: 1 of 3 changed`; line `  export/masks/forest.png  not comparable: format differs: L 4x4 vs L 2x2` |
| `test_text_analysis_absent_and_notes` | a without `report.json`, no `heightmap.r16` | lines `heightmap: not in both builds`, `note: heightmap: ...`, `note: analysis: ...` (in that order); no `objectives:`, `per team:`, `per pair:`, `map:` line |
| `test_text_unreadable_json_and_not_comparable_heightmap` | b: invalid `pois.json`, `heightmap_res` 0 | lines `  export/pois.json: unreadable: b: ...` and `heightmap: not comparable: b: export/meta.json lacks heightmap_res or height_max` |
| `test_text_never_prints_negative_zero` | a time delta of -0.04, a `-0.0` value and delta | line contains `o1  A +0.0  B +0.0`; no `-0.0`, `(-0)` in any line; `_rec_text` of delta `-0.0` is `x 1 -> 1 (+0)`; `_v(-0.0)` is `0` |
| `test_text_summary` | self-diff, and the `test_mask_share_and_bbox` pair | self: lines include `identical: yes`, `files: 0 changed, 0 added, 0 removed, 8 unchanged`, `rasters: 0 of 3 changed`; pair: a line starting `  export/masks/forest.png  12.5000% (2 texels), max delta 255, x 100..300 z 50..200`; every character ASCII; output ends with `"\n"` |

**Integration test** `test_river_crossing_edit`, with a module-scoped fixture that builds twice into
`tmp_path_factory` directories with `mapgen.build.build(spec, root, log=lambda s: None)`:

- a: `maps/example_river_crossing.yaml` (absolute path via `regress.REPO_ROOT`);
- b: its text with `"at: [1500, 900] }"` → `"at: [1500, 1000] }"` and `"width: 450, height: 220 }"` →
  `"width: 450, height: 240 }"`, each replacement asserted to occur exactly once, written LF to
  `river_crossing.yaml` in its own temporary directory.

Checks (bands, so that later packages that change output keep the test valid):

- `diff_builds(A, A)`: `identical` true; heightmap `changed_texels` 0; `json` `{}`; every objective
  `changed` false.
- `diff_builds(A, B)`:
  - `files.added == files.removed == []`; `set(files.changed)` equals the set of paths whose
    `regress.hash_tree` hashes differ between A and B; it contains `export/heightmap.r16`,
    `export/pois.json`, `report.json`; it does not contain `export/masks/forest.png`;
  - heightmap `comparable`, `changed_share > 0.05`, `10 < max_delta_m < 30`;
  - every raster entry has `changed_texels > 0` exactly when its path is in `files.changed`;
    `export/masks/impassable.png` `changed_texels > 0`;
  - `json["export/meta.json"]["patterns"] == [{"count": 1, "pattern": "/spec_sha256"}]`;
  - `json["export/spec.resolved.json"]["patterns"] == [{"count": 1, "pattern": "/pois/*/at/*"},
    {"count": 1, "pattern": "/regions/*/height"}]`, and its `changes` contain
    `{"op": "changed", "path": "/pois/6/at/1", "a": 900.0, "b": 1000.0}` and
    `{"op": "changed", "path": "/regions/2/height", "a": 220.0, "b": 240.0}`;
  - analysis objective ids `["bridge_keep", "camp_ne", "camp_sw", "relic", "cap_s"]`; `cap_s` `delta_s`
    A and B both `< -10`; `per_team` keys `["A", "B"]`, each with metrics `area_share` and `openness`;
    `per_pair["A-B"]["base_time_s"]["a"]` a number;
  - a second `diff_builds(A, B)` serialises to the same string as the first.
- `mapgen.__main__.main(["diff", A, B])` returns 0 and `json.loads(capsys.readouterr().out)["identical"]`
  is false.

### 10.2 `tests/test_diff_cli.py` (real subprocesses)

Docstring: `"""mapgen diff command line: exit codes and output, through real subprocesses."""` Import
`run` from `test_cli_exit_codes` and `make_build` from `test_diff`. `A`, `B` = synthetic builds
(`B` = the `test_mask_share_and_bbox` pair's b).

| id | command | exit | output check |
|---|---|---|---|
| `diff_self` | `diff A A` | 0 | `json.loads(stdout)["identical"]` true |
| `diff_differ` | `diff A B` | 0 | `identical` false; `rasters` entry `export/masks/forest.png` `changed_texels` 2 |
| `diff_text` | `diff A B --text` | 0 | stdout line `identical: no`; first line `mapgen diff` |
| `diff_max_changes` | `diff A B2 --max-changes 1` where B2's `report.json` has `area_share` A, B changed | 0 | `json["report.json"]["changes"]` length 1, `truncated` true |
| `diff_missing` | `diff MISSING A` | 1 | stdout == `error: directory not found: <MISSING>\n` |
| `diff_missing_b` | `diff A MISSING` | 1 | stdout == `error: directory not found: <MISSING>\n` |
| `diff_stdout_bytes_lf` (JSON and `--text`) | `diff A B` and `diff A B --text`, `PYTHONIOENCODING=utf-8` | 0 | stdout holds no `\r` and ends with a single `\n` |
| `diff_not_dir` | `diff <file> A` | 1 | stdout == `error: not a directory: <file>\n` |
| `diff_not_build` | `diff <empty dir> A` | 1 | stdout == `error: not a mapgen build directory (no report.json or export/meta.json): <dir>\n` |
| `diff_one_arg` | `diff A` | 2 | stderr contains `the following arguments are required: build_b` |
| `diff_bad_max` | `diff A A --max-changes -1` | 2 | stderr contains `argument --max-changes: expected an integer >= 0, got '-1'` |

### 10.3 Runtime

`tests/test_diff.py`: synthetic tests under 2 s; the integration fixture builds river_crossing twice
(about 11–13 s each in-process) — about 27 s in total. `tests/test_diff_cli.py`: 12 subprocesses at
about 0.6 s — about 7 s. The suite grows by about 35 s, to about 3.5 minutes.

## 11. Documentation updates

Apply in the same commit as the command. Preserve each file's line endings: `README.md` and
`MAPGEN_SPEC.md` are CRLF; `CLAUDE.md`, `docs/roadmap.md`, `tests/REGRESSION_SPEC.md`, `tests/*.py`
are LF. `EXPORT_FORMAT.md` is unchanged: the diff reads the bundle and changes nothing in `export/`.
`docs/investigations/` reports are dated records and are not edited.

**`README.md`**

- In the `## Commands` code block, after the `probe` line, add:
  `python -m mapgen diff     out/a/thornwald out/b/thornwald      # what changed between two build folders: JSON (--text: summary)`
- Replace the sentence `Exit code 0 on success; 1 on spec errors, a missing file or invalid probe points;
  2 on usage errors.` with `Exit code 0 on success; 1 on spec errors, a missing file, invalid probe
  points or a path that is not a build folder; 2 on usage errors.`
- After the `probe` paragraph add:

  ```
  `diff` compares two build folders (`<out root>/<name>/`, e.g. two `--out` roots of the same map) and
  prints JSON: changed, added and removed files; heightmap share of texels changed and maximum height
  delta in metres; share, count and bounding box of changed texels per mask and raster; the changed JSON
  paths of every differing JSON file (`--max-changes N` per file, default 100); per-objective travel-time
  deltas per team; per-team, per-pair and map-wide deltas of the report values. `--text` prints a summary
  for reading. It reads only the two folders and also works on `--only analyze` builds. Exit code 0 when
  the report is printed, whether or not the builds differ (`"identical"` in the JSON). Details:
  `mapgen/DIFF_SPEC.md`.
  ```

- In `## Tests`, replace `After an owner-approved output change,` with `After the owner has approved the
  \`mapgen diff\` report of an output change,`.

**`CLAUDE.md`**

- In the `## Run` code block, after the `probe` line, add:
  `.venv/Scripts/python -m mapgen diff <build a> <build b> [--text]   # delta of two build folders`
- In `## Verification`, replace `Show the owner a diff report for the change;` with
  ``Show the owner the diff report for the change: build each affected map before and after the change
  (e.g. the previous commit from a `git worktree`), each into its own empty `--out` root, and run
  `.venv/Scripts/python -m mapgen diff <before>/<map> <after>/<map> --text` (agents read the JSON form,
  without `--text`);``
- In "Where knowledge lives", add the row
  `| mapgen/DIFF_SPEC.md | the diff command: report format, algorithms, tests |` after the
  `tests/REGRESSION_SPEC.md` row.

**`MAPGEN_SPEC.md`**

- Section 2 layout: `__main__.py           CLI (argparse): build, validate, probe, types, palette`
  becomes `__main__.py           CLI (argparse): build, validate, probe, diff, types, palette`; after the
  `build.py` line add
  `    diff.py               build-to-build comparison (`diff` command; design in DIFF_SPEC.md)`.
- Section 7 code block: after the `probe` line add
  `python -m mapgen diff <build a> <build b> [--text] [--max-changes N]   # delta of two build folders (no files)`
- Section 7, new bullet after the `probe` bullet: `` `diff` reads two build directories, never the spec,
  and prints a JSON report (default) or a text summary (`--text`): changed/added/removed files, heightmap
  and raster texel changes, JSON path changes, objective time deltas and report-value deltas. Exit code 0
  when the report is printed; 1 when a path is missing or not a build directory. Full design:
  `mapgen/DIFF_SPEC.md`. ``

**`docs/roadmap.md`**

- Table "Editability and regeneration stability": delete the row
  `| No build-to-build comparison | Nothing reports what changed between two builds. |`.
- Section `### 2. Build diff command`: append the line `Implementation spec: \`mapgen/DIFF_SPEC.md\`.`

**`tests/REGRESSION_SPEC.md`**

- Section 1, the sentences `The diff report is the output of \`mapgen diff\` (roadmap package 2) once that
  command exists; until then it is the suite's failure listing plus \`tests/rebaseline.py --dry-run\`
  output plus the changed files themselves.` become `The diff report is the output of \`mapgen diff
  <before build> <after build>\` for each affected map (\`mapgen/DIFF_SPEC.md\`).`
- Section 3 file table: add rows `| tests/test_diff.py | diff report logic on synthetic builds; river_crossing integration build |`
  and `| tests/test_diff_cli.py | diff command exit codes, through real subprocesses |`.
- Section 4.3 `format_failure` and section 5.2 `test_format_failure`: the last line becomes
  `Re-baseline only after the owner approves a mapgen diff report: .venv/Scripts/python tests/rebaseline.py <name>`.
- Section 7 quotes the `CLAUDE.md` Verification text and the README Tests text: apply the same two
  replacements given above for `CLAUDE.md` (`Show the owner a diff report for the change;`) and
  `README.md` (`After an owner-approved output change,`) inside those quoted blocks.

**`tests/regress.py`** — `format_failure`: `"Re-baseline only after the owner approves a diff report: "`
becomes `"Re-baseline only after the owner approves a mapgen diff report: "`.

**`tests/test_regress_helpers.py`** — `test_format_failure` expected string: same change.

## 12. Python 3.8 constraints

- No `match`, no `X | Y` unions, no builtin generics at runtime (`list[str]`); use `typing.List`,
  `Dict`, `Optional`, `Tuple`, `Any`. `from __future__ import annotations` at the top of `diff.py`, as in
  the other mapgen modules.
- No `str.removeprefix` / `removesuffix`, no `dict | dict`, no parenthesised multi-item `with`.
- `%` formatting (codebase style).
- `numpy 1.24.4`, `Pillow 10.4.0` (pinned in `requirements.txt`); no new dependency.
- Determinism: sorted file lists, sorted object keys, defined list orders, no timestamps, no
  randomness, no set iteration without `sorted`.

## 13. Acceptance checks

Run by the orchestrator after the last wave. All must hold.

1. `.venv/Scripts/python -m pytest tests/test_diff.py tests/test_diff_cli.py` passes; runtime under 60 s.
2. `.venv/Scripts/python -m pytest` passes in full: every example and concept map still matches its
   reference hashes (`git status` shows no change under `tests/baselines/`).
3. **Self-diff.** For a full thornwald build `<a>`: `mapgen diff <a> <a>` exits 0, `identical` true,
   `files.unchanged_count` 33, all deltas zero.
4. **Worked example.** Build the pair of section 9.1 into fresh roots and run `mapgen diff`: every value
   listed in section 9.1 matches (files, heightmap, the two changed rasters with boxes, the `json` table,
   `pois.json` changes, objective entries for `lodge` and `lea_keep`, `per_team`, `per_pair`, `map`), and
   `--text` prints the lines of 9.1.
5. **`--only analyze`.** The 9.2 analyze-only pair gives the result stated there.
6. **Determinism.** Two runs of the worked-example diff give byte-identical stdout.
7. **Runtime.** The worked-example diff takes under 3 s wall clock including interpreter start
   (measured 0.6 s).
8. **Errors.** `mapgen diff nosuchdir out` exits 1 with `error: directory not found: nosuchdir`;
   `mapgen diff out out` with `out` holding map folders exits 1 with the "not a mapgen build directory"
   line; `mapgen diff x` exits 2.
9. `git status` shows only: `mapgen/diff.py`, `mapgen/DIFF_SPEC.md`, `mapgen/__main__.py`,
   `tests/test_diff.py`, `tests/test_diff_cli.py`, `tests/regress.py`, `tests/test_regress_helpers.py`, `README.md`,
   `CLAUDE.md`, `MAPGEN_SPEC.md`, `docs/roadmap.md`, `tests/REGRESSION_SPEC.md`; nothing under `export/`
   code paths (`mapgen/export.py`, `mapgen/build.py`) changed.

## 14. Implementation waves

Each task lists the sections it reads and the files it touches; it touches only those files. Do not
commit inside a task; the orchestrator commits after acceptance (commit messages end with the
attribution lines the session provides).

### Wave A

**A1 — core module.** Read sections 1, 2, 5, 6, 8.1, 12. Create `mapgen/diff.py` with everything of 8.1
except `format_text` (leave it out). Verify in a scratch directory outside the repo: build
`maps/concepts/thornwald.yaml` and the moved-`lodge` copy (section 2.4) each into its own empty `--out`
root; run
`.venv/Scripts/python -c "import sys; from mapgen import diff; sys.stdout.write(diff.format_json(diff.diff_builds(sys.argv[1], sys.argv[2])))" <a>/thornwald <b>/thornwald`
and compare with section 9.1; run it with `<a>` twice and compare with 9.2.

### Wave B (parallel, after A)

**B1 — CLI and text output.** Read sections 3, 4, 7, 8, 9.1 (text block), 12. Add `format_text` to
`mapgen/diff.py`; edit `mapgen/__main__.py` per 8.2. Verify: `.venv/Scripts/python -m mapgen diff
<a>/thornwald <b>/thornwald --text` prints the text of 9.1; the error cases of section 4 print their
messages with exit codes 1 and 2 (`echo $?` in bash).

**B2 — module tests.** Read sections 2.3, 5, 6, 9.3, 10.1, 12, and `tests/REGRESSION_SPEC.md` sections
3–5 for suite conventions. Create `tests/test_diff.py`. The `test_text_summary` and the final `main([...])`
check of the integration test need B1; write them as specified and run them after B1 lands. Verify:
`.venv/Scripts/python -m pytest tests/test_diff.py` passes.

**B3 — documentation and failure hint.** Read section 11. Apply every edit there exactly, preserving line
endings. Verify: `.venv/Scripts/python -m pytest tests/test_regress_helpers.py` passes; `git diff --stat`
lists only the files of section 11.

### Wave C (after B1)

**C1 — CLI tests.** Read sections 4, 10.2, 12 and `tests/test_cli_exit_codes.py`. Create
`tests/test_diff_cli.py`. Verify: `.venv/Scripts/python -m pytest tests/test_diff_cli.py` passes (12
tests).

### Wave D (orchestrator)

Run every acceptance check of section 13, review `mapgen/diff.py` against sections 5–8, then commit all
files of section 13 check 9 in one commit.
