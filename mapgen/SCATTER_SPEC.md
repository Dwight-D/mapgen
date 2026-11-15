# Local forest scatter - implementation spec

This file is the complete brief for roadmap package 5, "Local forest scatter" (`docs/roadmap.md`, backlog item
`mapgen-local-forest-scatter`). An agent can execute any wave in section 11 by reading only this file and the repo files
it names. Repo root: `E:\GameDev\Unity\tools\mapgen` (paths below are relative to it; every command runs from it).
Interpreter: `.venv/Scripts/python` (Python 3.8). `PRE` is commit `fb3d610`.

## 1. Purpose

A `scatter` entry (MAPGEN_SPEC.md §4.6) covers the map with irregular patches of one region type (forest, field,
marsh). Its threshold is the `1 − coverage` quantile of the entry's noise field over the **entry's area**: every grid
cell inside the map and inside `within`. The threshold depends on the entry's own keys (`seed`/`id`, `coverage`,
`scale`, `clump`, `within`), the map size and the map `seed`, and on nothing else. Patches are the parts of the
eligible ground where the field lies above the threshold.

The mechanism covers every scatter entry, whatever its type.

### 1.1 Locality property (testable)

Take a build `a` and a build `b` of two specs that differ only in one feature `X` that is not a scatter entry (a
line, crossing, POI, base or explicit region). For each scatter entry `S`:

- **Changed area `D`.** `D` is the symmetric difference of `S`'s blocked area (section 2, step 2) in `a` and in `b`.
  - For a line, crossing, POI or base, `D` lies inside the footprint of `X` in `a` and in `b` (for a line also its
    automatic crossings), each widened by `2.25 · avoid` (clearance radius `1.5 · avoid` plus the warp displacement,
    at most `0.5 · avoid` per axis, `√2 · 0.5 · avoid` diagonally, rounded up).
  - For an explicit region, `D` lies inside the outline of `X` in `a` and in `b`, plus the line areas and automatic
    crossings the region edit changes (lakes clip rivers). A region blocks `S` only when its type is `water`, `ridge`
    or `flatten`, or when `S` has `on` and the region's type is not listed there. An edit to any other region leaves
    `S` byte-identical.
- **Touched patches.** A patch (one polygon part of `S`'s region) is touched when it, in `a` or in `b`, comes within
  `M = 20 m` of `D`. `M` covers one grid cell of contour interpolation (5 m; 10 m on maps over 4 km, where the measured
  deviation is at most 4.2 m), the 5 m raster cell of the blocked mask, the 4 m simplification and slack.
- **May change:** geometry inside `D`, and touched patches anywhere within their own extent: a cut outline is
  re-contoured and re-simplified along its whole ring, a remainder smaller than 1 500 m² vanishes, a hole smaller than
  300 m² closes.
- **May not change:** every patch that is not touched keeps its exact coordinates (identical WKB). The symmetric
  difference of `S`'s geometry in `a` and `b` lies inside `D` ∪ the touched patches of `a` and `b`. Mask texels
  (`export/masks/<type>.png`) change only within `D` ∪ touched patches, widened by 1.5 texels.
- An edit to another scatter entry never changes `S` (entries do not block each other).
- The order of parts in `regions.json` `polygons` is the contour order: an appearing or vanishing touched patch
  shifts the index of later parts; their coordinates are unchanged (backlog item 12.3).

Measured (prototype, thornwald, POI `great_oak` [1300, 2550] → [1410, 2550], `r` 20, `avoid` 8): 21 forest texels
change, all within 62 m of the two positions, bbox x 1362.5–1440.6, z 2586.7–2605.9; 96 of 97 parts of `scatter1` and
19 of 19 of `scatter0` keep identical coordinates; heightmap unchanged.

### 1.2 Meaning of `coverage`

`coverage` (0–1) is the share of the entry's area where the noise field lies above the threshold. Where eligible ground spans many patch widths (`scale`), it is
covered at about that share; an entry whose eligible ground is a sliver can yield no patches. Blockers sit on high and low noise alike, and parts below 1 500 m² are dropped. No
correction toward exact eligible coverage is made, because any such correction depends on what other features block.
For an entry with `on`, the entry's area is still the map cut to `within`; eligible ground is open ground plus the
listed region types. Measured achieved share of eligible area on the committed maps: within 0.038 of `coverage`
(section 9.1).

## 2. Algorithm (`mapgen/geometry.py:scatter_geometry`)

Per entry `sc`, with `sd = spec.seed + 8000 + 97 · sc.seed`:

1. Grid: cell size `cs` = 5 m (10 m when `max(spec.size) > 4000`), `cols = ceil(W / cs)`, `rows = ceil(H / cs)`.
2. Blocked mask `bm`: rasterized union of `blockers["excluded"]` (or `excluded_on(sc.on)`), plus the clearance of
   every line area, crossing and POI/base footprint (`avoid > 0`: `buffer(avoid)` ∪ cleaned warped
   `buffer(1.5 · avoid)`, warp amplitude `0.5 · avoid`, scale `3 · avoid`, seed `sd + 53`); `bm = raster.mask(...) > 0.02`.
3. Entry area: `area` = all-true `(rows, cols)` bool array; with `within`, `area = raster.mask(Polygon(sc.within), grid) >= 0.5`.
   Eligible cells: `ok = area & ~bm`. If `coverage <= 0` or no cell is eligible: empty polygon.
4. Noise `n` (float64, world-anchored at cell centres): `fbm2_grid(xs/scale, ys/scale, sd, 4) · (1 − 0.4·clump)`,
   plus `1.2 · clump · fbm2_grid(xs/(4·scale), ys/(4·scale), sd + 31, 2)` when `clump > 0`.
5. Threshold: `q = float(np.quantile(n[area], 1.0 - sc.coverage))` — numpy 1.24.4 default `method="linear"`, on the
   1-D float64 array that boolean indexing yields in row-major order. Deterministic: same input bytes, same `q`.
6. `f = np.where(ok, n - q, -2.0)` → `field_to_polygons` (contour at 0, simplify 4 m, `min_area` 1 500 m²,
   `hole_min` 300 m²) → `clean_polygon` → subtract the blocked union exactly → `drop_small_parts(g, 0.0, 1500)`.

Determinism and identity: no seed, constant or iteration order is added; nothing keys on list position. Two builds
of the same spec are byte-identical (prototype: example_natural rebuilt twice, identical).

## 3. Code change

Only `mapgen/geometry.py:scatter_geometry` (LF file). Replace lines 540–542 and 550:

```python
    bm = raster.mask(unary_union(bl), grid) > 0.02 if bl else np.zeros(grid.shape, bool)
    area = np.ones(grid.shape, bool)   # the entry's own area: the map, cut to `within`
    if sc.within is not None:
        area = raster.mask(Polygon(sc.within), grid) >= 0.5
    ok = area & ~bm
    if sc.coverage <= 0 or not ok.any():
        return Polygon()
    ...
    # the threshold depends on the noise and the entry's own area only, never on what other features block,
    # so an edit to a feature changes patches only where that feature's clearance changes
    q = float(np.quantile(n[area], 1.0 - sc.coverage))
```

Docstring: `"""Irregular patches of one region type: noise thresholded over the entry's own area (map ∩ within),
kept on eligible cells (spec 4.6)."""`. No change to `schema.py` (no key), `export.py`, `analysis.py`, `render.py`,
`types.yaml`, `palette.yaml`, constants. `__version__` stays `0.2.1`; `format_version` stays `1` (every valid spec
stays valid; output changes are re-baselined under the stability rule).

## 4. Contract impact

- `EXPORT_FORMAT.md`: no file, key or encoding changes. Content of scatter-type masks, `splat_*.png`,
  `regions.json` scatter regions and analysis values changes once (section 9).
- Battlehounds (`docs/battlehounds-consumer.md`) reads the masks (`forest.png` for tree scatter), `regions.json` and
  the top-5 chokepoints of `report.json`. No importer change. The five concept bundles need one re-import: gloamfen's
  top-5 chokepoint at (2515, 1505) moves to (2545, 1515) (score Δ 0.045); saltreach_delta's chokepoint scores shift
  by ≤ 0.034 at the same positions. A re-import after a local edit leaves trees in untouched patches in place. Battlehounds backlog item: section 12.6.

## 5. Tests: `tests/test_scatter.py` (in-process, LF; target under 5 s)

Helpers as in `tests/test_identity.py` (`_write`, `schema.load_spec`, `geometry.build_geometries`,
`geometry.parts`, `build.build(..., only="export", log=lambda s: None)`, `regress.hash_tree`). Spec template:

```python
TINY = """format_version: 1
name: tiny
size: [1000, 1000]
seed: 1
teams:
  A: {base: [100, 100]}
  B: {base: [900, 900]}
lines:
  - { id: t, type: trail, path: [[0, 500], [1000, 560]], width: 6, meander: 20 }
pois:
  - { id: far, type: neutral_camp, at: %s }
scatter:
  - { id: s, type: forest, coverage: 0.4, scale: 120, clump: 0.4, avoid: 20 }
output: { render_px: 256, heightmap_res: 129, splat_res: 256 }
"""
OLD, MOVED = (750.0, 250.0), (860.0, 250.0)
```

Zone `D` = `Point(OLD).buffer(R) ∪ Point(MOVED).buffer(R)`, `R = poi.r + 2.25 · sc.avoid` (`poi.r` from
`spec.all_pois()` entry `far`, 50; `avoid` 20 → `R` 95). Touched = parts of `a` or `b` intersecting `D.buffer(20)`.

| test | asserts | measured, `PRE` code / patched |
|---|---|---|
| `test_poi_move_keeps_far_patches` | parts of `b` farther than 20 m from `D`: at least 5, and each one's WKB is in the set of part WKBs of `a` | 0 of 10 / 10 of 10 |
| `test_poi_move_changes_only_near_geometry` | `sym_difference(a, b).difference(D ∪ touched).area < 1e-6` | 1207.6 m² / 0.0 |
| `test_poi_move_mask_is_local` | both specs built (module fixture, `only="export"`); every texel that differs in `export/masks/forest.png` has its centre inside `(D ∪ touched).buffer(1.5 · 1000/256)` | 250 texels outside / 0 |
| `test_coverage_of_eligible_ground` (parametrized) | achieved / eligible within ±0.04 of `coverage`, where achieved = area of `s` and eligible = area of `s` in the same spec with `coverage: 1.0` | patched: 0.4096, 0.2750, 0.4134 |
| `test_poi_move_is_local_with_within_and_on` (parametrized `within`, `on`) | the POI move of TINY with the entry given `within: [[0,0],[900,0],[900,1000],[0,1000]]`, or `on: [hills]` plus a `hills` region `h` (blob at [300, 750], r 160): at least 3 parts of `b` farther than 20 m from `D`, each in the part set of `a`; `sym_difference(a, b)` outside `D ∪ touched` has area < 1e-6 (geometry only, no builds) | fails on `PRE` code / passes, 0.2 s |
| `test_build_is_deterministic` | TINY (POI at OLD) full build (`build.build`, no `only`) twice into two empty dirs; `regress.hash_tree` equal | 23 files, identical, 0.6 s per build |

Coverage cases (BASE = TINY's first seven lines): `plain` = BASE + `scatter: [{ id: s, type: forest, coverage: 0.4,
scale: 120, clump: 0.4, avoid: 20 }]`; `within` = BASE + `{ id: s, type: field, coverage: 0.3, scale: 100, clump: 0.3,
avoid: 20, within: [[0, 0], [500, 0], [500, 1000], [0, 1000]] }`; `blocked` = TINY with the POI at OLD.

The first three tests and the `within`/`on` locality test fail on `PRE` code and pass with section 3 (verified on a scratch copy, values above).
The two maps without scatter (`river_crossing`, `three_team`) are covered by `tests/test_stability.py`: their
baselines are not re-baselined and must stay green. Existing tests stay unchanged and green
(`test_identity.py::test_insert_region_is_local` asserts forest `changed_share <= 0.01`, which holds).

## 6. Documentation updates (same commit)

### 6.1 `MAPGEN_SPEC.md` (CRLF; keep CRLF)

§4.6 table, `coverage` row becomes:
`| `coverage` | 0–1 (required): share of the entry's area (the map, cut to `within`) where the noise field lies above the threshold; eligible ground is covered at about this share |`

§4.6, the sentence ending `... and thresholded at the `1 − coverage` quantile over eligible cells.` becomes:
`... and thresholded at the `1 − coverage` quantile of its values over the entry's area: every grid cell inside the
map and inside `within` (a cell is inside when at least half of it is covered). The threshold depends on the field
and that area only, never on what other features block; patches are the eligible cells above it.`

After the paragraph ending `... holes smaller than 300 m² are dropped.`, add:
`**Locality.** An edit to another feature changes an entry's patches only where the edit changes the entry's blocked
area (for a line, crossing, POI or base: its footprint before and after the edit, widened by `2.25 · avoid`; for a blocking
region: its outline before and after the edit) and in the patches that come within 20 m of that area, which may change along
their whole outline or vanish below 1 500 m². Every other patch keeps its exact coordinates. Scatter entries never
affect each other.`

### 6.2 `README.md` (CRLF; keep CRLF)

`scatter` row (line ~99): after `... painted under the explicit regions`, append
`; `coverage` is the share of the entry's area (map cut to `within`) above its noise threshold, so eligible ground is
covered at about that share, and an edit to another feature changes only the patches near that feature`.

### 6.3 `EXPORT_FORMAT.md` (LF)

After the bullet `- `scatter: true` marks background patches ... and have no label.`, add the bullet:
`- A scatter patch depends on its scatter entry and on the features near it only (MAPGEN_SPEC.md §4.6): after an edit
  to a feature, patches away from it keep identical coordinates; their index in `polygons` can shift.`

### 6.4 `docs/roadmap.md` (LF)

- Delete the gap row `| Scatter thresholds at a map-wide quantile | ... |`. The table under
  `### Editability and regeneration stability` is then empty: delete that heading and its table header too.
- Package 5 section body becomes:

```
The scatter threshold of an entry is the `1 − coverage` quantile of its noise field over the entry's own area
(the map, cut to `within`), independent of every other feature. An edit to a feature changes scatter patches only
where that feature's blocked footprint and clearance change, and in the patches touching that area. `coverage` is
the share of the entry's area above the threshold; eligible ground is covered at about that share.

Implementation spec: `mapgen/SCATTER_SPEC.md`.

**Migration.** The threshold moves patch edges once on every map that uses `scatter`: 1.5–10 % of the occupied
texels of each scatter-type mask (0.1–2.2 % of the map). thornwald objective `charcoal` flips to IMBALANCED, filed as
a separate balance item. Each map's shift is shown with `mapgen diff`, approved by the owner per map, then
re-baselined; the two maps without scatter stay byte-identical.
```

### 6.5 `CLAUDE.md` (LF; the repo's agent file, edit allowed for this row)

Knowledge table, after the `mapgen/REFERENCES_SPEC.md` row, add:
`| `mapgen/SCATTER_SPEC.md` | the scatter threshold over the entry's own area: locality property, `coverage` meaning, migration of the scatter maps, tests |`

### 6.6 `tests/REGRESSION_SPEC.md` (LF)

File table, after the `tests/test_references.py` row, add:
`| `tests/test_scatter.py` | scatter: locality of edits, coverage of eligible ground, determinism (small in-process specs) |`

### 6.7 `mapgen/DIFF_SPEC.md` (LF)

The measured-build note on scatter (the paragraph that cites the map-wide quantile gap) states that the measured build
thresholded scatter at a quantile over the eligible cells of the whole map, and points to `mapgen/SCATTER_SPEC.md`.

### 6.8 `mapgen/IDENTITY_SPEC.md` (LF)

§9.3 is headed "measured on migrated thornwald at commit `d348b5c`" and states that scatter thresholded map-wide there
(the forest-mask drift is a dated measurement); the paragraph after the table points to `mapgen/SCATTER_SPEC.md`.

### 6.9 Unchanged

The map-concept skill (`E:\GameDev\Unity\.claude\skills\map-concept\SKILL.md`) gives no coverage or threshold
guidance: no edit. `docs/investigations/map-editability.md` is a dated report: no edit. `docs/battlehounds-consumer.md`:
no edit (no contract change).

## 7. Acceptance checks

1. `python -m pytest tests/test_scatter.py tests/test_identity.py tests/test_references.py tests/test_validation.py
   tests/test_diff.py` passes.
2. Wave C check passes (section 11, wave C step 5), including the thornwald `great_oak` case.
3. After re-baselining: `python -m pytest` fully green; `tests/baselines/river_crossing.json` and
   `three_team.json` untouched (`git diff --stat tests/baselines/` lists exactly the six scatter maps).
4. Documentation of section 6 in the same commit as the code.

## 8. Prototype

Validated 2026-11-14 on a scratch copy of `PRE` with section 3 applied: example_natural builds byte-identical to the
investigation prototype; the section 5 values were measured with it. All eight maps were built with it in the
investigation; the figures of section 9 come from those builds.

## 9. Migration

Output changes once on the six maps with scatter; `example_river_crossing` and `example_three_team` stay
byte-identical. On every changed map: heightmap 0 texels changed; no file added or removed; no warning added or
removed; `lines.json`, `pois.json`, `crossings.json`, `meta.json`, `spec.resolved.json`, `heightmap.*` unchanged.

### 9.1 Expected per-map impact (check in wave C)

Changed files on each scatter map: the scatter-type masks, `export/regions.json`, `export/splat_0.png`,
`export/splat_1.png`, `map.png`, the six `overlay_*.png`, `report.json`, `report.md`; plus `export/masks/impassable.png`
on kingsjaw and thornwald only. "occ" = changed texels / texels non-zero in either build.

| map | files | masks: % of map / % of occ (mean cover a → b) | objectives changed, max \|Δt\| | flags | team / map deltas |
|---|---|---|---|---|---|
| natural | 14 | field 0.323 / 3.31; forest 0.638 / 2.95 (0.213 → 0.210) | 1/7, 1.2 s | none | openness A +0.004, B +0.003 |
| ashen_steppe | 14 | field 0.826 / 3.01; forest 0.271 / 3.68 | 1/9, 0.8 s | none | all < 0.002 |
| gloamfen | 15 | field 0.457 / 6.36 (0.068 → 0.070); forest 0.107 / 1.51; marsh 0.685 / 1.76 | 0/8 | none | < 0.0005; top-5 chokepoint (2515,1505) → (2545,1515) |
| kingsjaw | 15 | field 2.130 / 10.06 (0.205 → 0.195); forest 2.157 / 7.43 (0.271 → 0.287); impassable 0.013 % | 1/7, 1.0 s | none | B.openness −0.031, openness_mean −0.017, A-B route_openness −0.010 |
| saltreach_delta | 15 | field 0.639 / 4.98; forest 0.410 / 4.58; marsh 0.164 / 2.01 | 0/10 | none | openness A −0.003; chokepoint scores ≤ 0.034, same positions |
| thornwald | 14 | forest 2.193 / 3.22 (0.658 → 0.672); impassable 0.003 % (33 texels) | 4/9, 4.6 s | `charcoal` ok → IMBALANCED (spread 0.187 → 0.2005, tolerance 0.20) | A.openness −0.010, B.openness −0.018 |

Tolerance: the code is deterministic and these figures came from the same code, so they reproduce to the printed
rounding. A file set that differs from this table, a heightmap change, a change on a no-scatter map, or any figure
off by more than 0.05 percentage points (0.002 for analysis deltas): STOP and report.

Achieved coverage (patch area / eligible area, target → `PRE` → this package): natural forest 0.16 → 0.157 → 0.152,
field 0.10 → 0.097 → 0.094; kingsjaw scatter0 field 0.45 → 0.442 → 0.412 (largest deviation, 0.038), scatter2 forest
on hills 0.62 → 0.614 → 0.646; thornwald scatter0 0.58 → 0.575 → 0.598, scatter1 0.42 → 0.408 → 0.412. Patch counts
equal `PRE` ±2. kingsjaw has the largest visual shift: the owner compares `pre/kingsjaw/map.png` and
`post/kingsjaw/map.png` side by side.

Owner decisions applied: threshold over the entry's area (not a windowed local quantile); `coverage` as defined in
1.2 with no correction; thornwald `charcoal` → IMBALANCED accepted, balance item filed (12.1), no retuning here.

## 10. Python 3.8

No syntax beyond 3.8 in code or tests (`typing` for hints, no `X | Y`, no `match`).

## 11. Implementation waves

Contrast with the pre-package behaviour is allowed in this section only. Nothing is committed until wave D.

### Wave 0 (coordinator)

Working tree clean on `main` at `PRE` (`fb3d610`).

### Wave A (one agent, sonnet): code

Section 3 in `mapgen/geometry.py` only (the threshold was the quantile over `n[ok]`, eligible cells; it becomes the
quantile over `n[area]`). Then `python -m mapgen validate` on the eight specs (exit 0) and
`python -m pytest tests/test_identity.py tests/test_references.py tests/test_validation.py` green. Do not touch
`maps/`, `tests/`, docs.

### Wave B (parallel, after A; two separate agents)

- **B1 tests:** section 5. Confirm the first three tests fail against `PRE` code (copy `tests/test_scatter.py` into a temporary
  `git worktree add --detach <scratch dir> fb3d610`, run it there, then `git worktree remove` it) and pass on the
  working tree.
- **B2 docs:** section 6.1–6.8. Keep CRLF in `MAPGEN_SPEC.md` and `README.md` (edit with a tool that preserves line
  endings; `git diff --stat` shows only the intended lines).

### Wave C (one agent, after B): review material under `out/scatter-review/`, then STOP

1. `git worktree add --detach out/scatter-review/pre-src fb3d610`.
2. PRE builds: from `out/scatter-review/pre-src`, for each of the eight specs
   `../../../.venv/Scripts/python -m mapgen build <spec> --out ../pre` (empty dir first). Confirm with
   `tests/regress.py` (`hash_tree`, `load_baseline`, `compare`) that every PRE build equals its baseline.
3. POST builds: from the repo root, `python -m mapgen build <spec> --out out/scatter-review/post` for the eight specs.
   Edit case: copy `maps/concepts/thornwald.yaml` to `out/scatter-review/edit/thornwald.yaml` with
   `at: [1300, 2550], label: Great Oak` → `at: [1410, 2550], label: Great Oak` (exactly one occurrence), build it to
   `out/scatter-review/edit-out`.
4. Reports per map `<name>`: `python -m mapgen diff out/scatter-review/pre/<name> out/scatter-review/post/<name>`
   into `diff/<name>.json`, and with `--text` into `diff/<name>.txt`; same for `post/thornwald` vs
   `edit-out/thornwald` into `diff/thornwald_great_oak.{json,txt}`.
5. `out/scatter-review/check_review.py` (uses `mapgen.diff.diff_builds(a, b)`; report keys in `mapgen/DIFF_SPEC.md`),
   prints `<name>: OK (<n> files changed)` or `<name>: FAIL <reason>`, exit 1 on any failure:
   - every map: `heightmap.changed_texels == 0`, `files.added == files.removed == []`;
   - `river_crossing`, `three_team`: `identical` is true;
   - scatter maps: `files.changed` equals the set of 9.1 for that map (scatter types from the spec's `scatter`
     entries; `impassable.png` on kingsjaw and thornwald only);
   - great_oak case: changed rasters ⊆ {`export/masks/forest.png`, `export/splat_0.png`, `export/splat_1.png`};
     `forest.png` `bbox_m` within x 1200–1510, z 2450–2650 (measured 21 texels, x 1362.5–1440.6, z 2586.7–2605.9).
6. `out/scatter-review/summary.py` + `SUMMARY.txt`, shaped like `out/references-review/SUMMARY.txt`: title line
   `SCATTER THRESHOLD: pre (fb3d610) vs post builds, per map (mapgen diff --text excerpts)`; per map a `== <name> ==`
   block with the `files:` (and changed list), `heightmap:`, `rasters:` (with each raster line), `json:` and
   `objectives:` lines of `diff/<name>.txt`, then analysis deltas from the JSON: per changed mask `% of map`,
   `% of occ`, mean cover a → b (PIL, mean/255); objectives changed / total, max |`delta_s`|, flag changes
   (`flag_a` ≠ `flag_b`); non-zero `delta` values under `analysis.per_team`, `analysis.per_pair`, `analysis.map`;
   warnings added/removed; then the check line. Append the great_oak block and a comparison against table 9.1.
7. `python -m pytest`: everything passes except `test_build_matches_baseline` on exactly the six scatter maps.
8. `git worktree remove out/scatter-review/pre-src`. **STOP.** Do not re-baseline, do not commit. Report the paths of
   `diff/`, `SUMMARY.txt` and the check output to the coordinator, who shows the six diff reports to the owner for
   per-map approval (kingsjaw `map.png` side by side; thornwald `charcoal` flag).

### Wave D (coordinator, only after the owner approves each map's diff)

1. `python tests/rebaseline.py natural ashen_steppe gloamfen kingsjaw saltreach_delta thornwald`; then
   `python -m pytest` fully green.
2. One commit: `mapgen/geometry.py`, `tests/test_scatter.py`, the six baselines, docs of 6.1–6.6, this file.
3. File the items of section 12; close the item:
   `anthill backlog close mapgen-local-forest-scatter --done --root E:\GameDev\Unity`.

## 12. Backlog items to file

In this repo's workstream: `anthill backlog new --title "<title>" --value "<value>" --hint mapgen --root E:\GameDev\Unity`.

1. **thornwald charcoal objective balance.** Value: "thornwald objective `charcoal` sits at spread 0.2005 against
   tolerance 0.20 (IMBALANCED in the package 5 baseline). Any forest change near it tips the flag. Retune the
   map around charcoal (game-feel, owner decides)."
2. **Check whether `on` scatter can lower impassable cost at blocking-region edges.** Value: "`masks/impassable.png`
   changes by a few texels on thornwald/kingsjaw when only scatter changes. `analysis.build_cost` paints region cost
   in paint order and `on` entries paint after explicit regions; verify whether a scatter patch can turn an `inf` cell
   finite, and fix if so."
3. **Stable part order or geometry-matched diff for multi-part regions.** Value: "One appearing/vanishing scatter
   patch shifts the index of every later part in `regions.json`, so `mapgen diff` reports thousands of JSON changes
   for a local edit. Order parts stably (e.g. by centroid) or match parts by geometry in the diff."
4. **Local region fragmentation threshold.** Value: "Region `fragment` thresholds at a quantile over the region's own
   cells, so an edit to a region's outline re-rolls all its clearings. Low priority: the effect stays inside the
   region."
5. **Report achieved scatter coverage.** Value: "`report.md`/`report.json` list no target vs achieved coverage per
   scatter entry; authors cannot see the 0.04 drift from `coverage` on eligible ground."
6. **Battlehounds: re-import the concept bundles** (Battlehounds backlog, per `docs/battlehounds-consumer.md` step 3,
   `--root E:\GameDev\Unity\battlehounds`). Value: "mapgen's scatter threshold changed once: forest/field/marsh masks,
   splats and `regions.json` scatter regions of all five concept bundles differ (1.5–10 % of occupied mask texels);
   gloamfen's top-5 chokepoint at (2515,1505) moves to (2545,1515); saltreach scores shift ≤ 0.034. No format change.
   Re-import once; afterwards local map edits leave untouched patches' trees in place."
