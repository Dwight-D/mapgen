# Map editability and regeneration stability

- Date: 2026-11-14
- Checked against: commit `77e278b` (mapgen 0.2.1), working tree clean
- Scope: read-only investigation of `tools/mapgen`. No code or data file was changed.
- Question: how feasible is an agent-driven workflow in which a human gives creative direction and agents
  edit map inputs and re-run mapgen until the map fits — including local tweaks to an existing map (for
  example rebalancing one side) and regenerating existing maps after mapgen gains features?

Statements are marked **[verified]** (read in code at the cited line, or measured by running the tool) or
**[inference]** (reasoned from the code, not measured).

## Summary

- A map is defined by one YAML spec plus two shared data files (`mapgen/types.yaml`, `mapgen/palette.yaml`).
  All gameplay-relevant placement (bases, POIs, region centres/outlines, line control points, crossings) is
  hand-written coordinates. The seed only adds organic detail to that explicit geometry, plus `scatter`
  patches and base terrain relief.
- Randomness is stateless: every noise value is a hash of `(lattice x, lattice y, seed + offset)`. There is
  no RNG stream, so nothing depends on how much noise an earlier feature consumed.
- Local edits are mostly local. The couplings that spread a local edit are: list-index-derived noise seeds,
  list-index-derived export ids, the map-wide quantile threshold of `scatter`, and analysis results (which
  are global by nature).
- Tweaking an existing map without regenerating it wholesale is feasible today for edits that change a
  value in place or append an entry. Inserting or deleting a list entry reshapes every later entry of that
  list.
- An agent loop "edit → build → read metrics" works today through `report.json` (2.5 s in-process with
  `--only analyze`). It lacks a pass/fail gate, per-team aggregate metrics, machine-readable probe output and
  a build-to-build diff.
- Nothing guards regeneration stability across mapgen versions: no spec format version, no reference
  hashes, no test suite, and `out/` is git-ignored.

## 1. Inputs that define a map

### 1.1 Files

| input | role | scope |
|---|---|---|
| `maps/**/<name>.yaml` | the map spec | one map |
| `mapgen/types.yaml` | type table: travel cost, `los`, splat layer, default `height`/`width`/`r`/`edge`, `height_mode`, automatic crossing type, POI `objective` flag | every map |
| `mapgen/palette.yaml` (or `--palette <file>`) | render colours and patterns, default team colours | every map; affects images only, plus team `color` in `spec.resolved.json` |
| code constants | e.g. `BLOB_VERTICES`, `FRAGMENT_CELL`, `AUTO_MARGIN` (`mapgen/geometry.py:20-33`), chokepoint radii (`mapgen/analysis.py:22-27`), defaults in the spec dataclasses (`mapgen/schema.py:189-298`) | every map |

**[verified]** `load_spec` reads the YAML, `types.yaml` and the palette and nothing else
(`mapgen/schema.py:800-814`). The `map-concept` skill is a single `SKILL.md`; it ships no templates or
generator scripts.

### 1.2 Spec sections

Top-level keys are a closed list; an unknown key anywhere is a validation error (`mapgen/schema.py:492-495`,
`344-354`).

| section | keys | nature |
|---|---|---|
| `name`, `brief` | output folder name; free text copied to the report | authored; `brief` is not parsed |
| `size` | `[x, y]` metres | authored |
| `seed` | integer, default 0 (`schema.py:520-525`) | drives all noise |
| `terrain` | `height_max`, `base_height`, `relief`, `relief_scale`, `water_depth`, `detail`, `detail_scale` (`schema.py:528-540`) | authored parameters of a seed-derived noise field |
| `teams` | 2 or 3 entries: `base` point, `color`, `name` (`schema.py:543-565`) | explicit coordinates |
| `regions` | `type`, `shape` (`blob`: `at`, `r`, `rough`, `squash`, `angle`; `poly`: `points`, `rough`; `band`: `path`, `width`, `rough`), `label`, `label_at`, `id`, `height`, `edge`, `warp`, `warp_scale`, `fragment`, `fragment_scale` (`schema.py:437-443`, `567-622`) | explicit position, size and base outline; outline detail (roughness, warp, fragmentation) is seed-derived |
| `lines` | `type`, `path`, `width`, `meander`, `meander_scale`, `width_var`, `id`, `label`, `label_at`, `join`, `join_end` (`schema.py:624-655`) | explicit control points; meander and width variation are seed-derived |
| `crossings` | disc (`at`, `r`) or band (`path`, `width`), `type`, `id` (`schema.py:657-689`) | explicit, no noise |
| `auto_crossings` | boolean, default `true` | derived deterministically from line geometry, no noise |
| `pois` | `id`, `type`, `at`, `label`, `team`, `tier`, `r` (`schema.py:691-725`) | explicit, no noise |
| `scatter` | `type`, `coverage`, `scale`, `clump`, `avoid`, `within`, `on` (`schema.py:727-755`) | fully procedural: thresholded noise over the eligible area |
| `analysis` | `cell`, `speed`, `balance_tolerance` | authored |
| `output` | `render_px`, `heightmap_res`, `splat_res`, `grid_step` | authored |

There is no spawn list separate from team bases (`spawn` exists only as a POI type in `types.yaml`), and no
spec format version key.

### 1.3 Example: `maps/concepts/thornwald.yaml`

**[verified]** 75 lines: seed 4410 (line 15), 2 teams (25-26), 2 scatter entries (29-30: forest at 58 %
coverage, and forest on hills), 12 explicit regions (33-48), 7 lines (52-59), 10 POIs (62-72), no explicit
crossings (all 5 crossings are automatic), `balance_tolerance: 0.2` (75). No region carries an `id`; lines
and POIs do.

## 2. Pipeline and where noise enters

`build.build` (`mapgen/build.py:169-207`) runs the stages in this order. Stage times are from one build of
thornwald on this machine.

| # | stage | code | noise | time |
|---|---|---|---|---|
| 1 | load, default, validate | `schema.load_spec` | none | <0.1 s |
| 2 | geometry: regions → lines (parents before tributaries) → rivers clipped out of lakes → crossings → automatic crossings → scatter | `geometry.build_geometries` (`geometry.py:550-606`) | region outlines, warp, fragmentation, meander, width variation, scatter | 0.8 s |
| 3 | heightmap: base relief → hills → mountains → passes → plateaus → towns → ramps → roads/trails → water → blur and clamp | `height.synthesize` (`height.py:63-195`) | base relief, detail, hills, mountain ridges | 1.6 s |
| 4 | analysis: cost grid, travel times, territory, fair zone, objectives, traffic, chokepoints, openness, warnings | `analysis.analyze` (`analysis.py:133-325`) | none | 0.3 s |
| 5 | render: `map.png`, six overlays | `render.render_all` | stipple pattern jitter only (`render.py:509`) | 11.1 s |
| 6 | export bundle | `export.export_all` (`export.py:308-319`) | none | 3.4 s |
| 7 | report: `report.md`, `report.json` | `build.py:191-203` | none | <0.1 s |

### 2.1 How the seed is used

**[verified]** Noise is value noise whose lattice values come from an integer hash of `(ix, iy, seed)`
(`mapgen/noise.py:14-28`). No `random` or `numpy.random` call exists in the package. Each use derives its
own seed by adding a fixed offset and a per-item key to the spec seed:

| use | seed | sampled over | code |
|---|---|---|---|
| base relief | `seed` | world x, y | `height.py:72` |
| terrain detail | `seed + 500` | world x, y | `height.py:74` |
| hills height | `seed + 1000 + seed_key` | world x, y | `height.py:90` |
| mountain ridges | `seed + 2000 + seed_key` | world x, y | `height.py:105` |
| blob / poly / band outline roughness | `seed + key` | angle or perimeter fraction (moves with the shape) | `geometry.py:194`, `325`, `209`, `177` |
| region warp | `seed + 3000 + 2·key` and `+ 7919` | world x, y | `geometry.py:330`, `222` |
| stipple pattern (render) | `seed + 4000` | image pixels | `render.py:509` |
| line meander | `seed + line index` | arc length from the line start | `geometry.py:348` |
| line width variation | `seed + 5000 + line index` | arc length | `geometry.py:360` |
| region fragmentation | `seed + 6000 + key` | world x, y | `geometry.py:302` |
| scatter field | `seed + 8000 + 97·scatter index` (`+ 31` clump, `+ 53` clearance warp) | world x, y | `geometry.py:511`, `539`, `524` |
| each fbm octave `o` | the above `+ 1013·o` | — | `noise.py:63`, `96`, `112`, `144` |

`key` is the entry's position in the spec's `regions` list (`schema.py:587`, `geometry.py:53-55`); scatter
regions use `1000 + scatter index` (`geometry.py:599`). A line's key is its position in `lines`.

Consequences **[verified by reading]**:

- The noise of one feature never depends on another feature's parameters or on evaluation order.
- A feature's noise depends on its list position, not on its `id`.
- Seeds are sums, so distinct uses can share a lattice seed (region 0 outline, line 0 meander and base
  relief all use `seed`; hills with `seed_key` 13 share `seed + 1013` with octave 1 of the base relief). This
  correlates patterns; it does not make output unstable.

## 3. Edit locality

### 3.1 Measured

Method: variants of `thornwald.yaml` built into a scratch directory, each compared file-by-file against the
baseline build. Baseline determinism holds: two builds, and a CLI build versus an in-process build, give 33
byte-identical files. Type-table variants were applied in memory, not to `types.yaml`.

| edit | heightmap | `masks/forest.png` (scatter) | other export data | analysis |
|---|---|---|---|---|
| move a landmark POI 110 m (`great_oak`) | identical | 0.04 % of texels differ, spread over the whole map | `pois.json`, `regions.json`, `splat_0.png` | times and shares unchanged to 4 digits |
| move an objective POI 70 m (`lodge`) | identical | 0.13 % differ, map-wide | same | `lodge` A 284→296 s, B 275→267 s; `lea_keep` A −0.7 s; chokepoint 4 moves |
| resize one hill (`r` 520→560) | 8.8 % of texels differ, all inside the hill's surroundings (north 25 % of the map), max 21 m | identical | `masks/hills.png`, `lines.json`, `pois.json` (sampled heights) | B's times shift by 0.1–5.5 s; area share A +0.5 pt |
| append a 2 km trail to `lines` | 0.5 % differ, along the trail, max 0.6 m | 1.06 % differ, map-wide | `masks/trail.png`, `lines.json`, splats | `charcoal` A 404→344 s; share A 46.8→48.8 %, B 43.8→40.3 % |
| append a region to `regions` | identical | 0.07 % differ, map-wide | `masks/clearing.png`, `regions.json` | unchanged |
| insert a region at the front of `regions` | 11.4 % differ, max 38 m, map-wide | 1.06 % differ | 28 of 33 files differ, including `lake`, `town`, `field`, `ruins`, `impassable` masks | base-to-base 435→431 s; every objective time changes; chokepoint 3 moves |
| forest cost 1.6→1.8 in the type table | identical | identical | only `spec.resolved.json` | six objectives change by up to 10 s |
| landmark footprint `r` 20→40 in the type table | identical | 0.12 % differ, map-wide | `pois.json`, splat | two objectives change by ~6 s |
| move a landmark POI, spec without `scatter` | identical | — | only `pois.json`, `meta.json`, `spec.resolved.json` | `report.json` byte-identical |

`meta.json` differs whenever the spec file differs because it records the spec's SHA-256.

### 3.2 Couplings that make a local edit non-local

1. **List position is the noise key** (`schema.py:587`, `geometry.py:53-55`, `348`, `360`, `height.py:90`,
   `105`). Inserting, deleting or reordering an entry in `regions` or `lines` gives every later entry a new
   seed: outlines, warp, fragmentation, hill and ridge height patterns, meanders. Appending at the end, or
   editing values in place, leaves other entries' seeds untouched. List order of regions is also paint order
   ("later wins", `analysis.py:62-65`), so appending is not always the wanted position.
2. **List position is the default export id.** Regions, lines and crossings without an `id` are exported as
   `region_<i>`, `line_<i>`, `crossing_<i>` (`export.py:188`, `202`, `215`). After an insert the same id
   names a different feature. Automatic crossings are `auto_<n>` in generation order — lines in spec order,
   then rivers in spec order (`geometry.py:448-451`, `461-484`) — so a new road earlier in the list or a new
   river crossing renumbers them. The report's chokepoint names refer to these ids.
3. **Scatter thresholds at a map-wide quantile.** The field is cut at the `1 − coverage` quantile over all
   eligible cells (`geometry.py:540`). Eligible area excludes buffers around every line, crossing, base and
   POI footprint and certain region types (`geometry.py:512-529`, `580-595`). Any edit to any of those
   changes the threshold slightly and with it every patch edge on the map; patches near the size limits
   (`SCATTER_MIN_AREA`, `geometry.py:547`) appear or vanish. Measured size: 0.04–1.1 % of forest texels.
   Region fragmentation uses the same mechanism per region (`geometry.py:304`): the quantile is over that
   region's cells only.
4. **World-anchored noise.** Warp, fragmentation, hills, ridges and scatter sample noise at world
   coordinates. Moving a region moves it across a fixed noise field, so its detailed outline and height
   pattern change rather than translate rigidly. Blob roughness is sampled over angle and travels with the
   shape. Default `warp` and `warp_scale` scale with `r` or `width` (`geometry.py:227-234`), so a resize also
   changes the warp unless both keys are set explicitly.
5. **Arc-length-anchored line noise.** Meander and width variation are functions of arc length from the
   line's first point (`geometry.py:345-352`, `359-361`). Moving one control point changes the wiggles from
   that point to the end of the line **[inference]**. A tributary's end is snapped to its parent's final
   centreline (`geometry.py:339-342`), so editing a parent moves its tributaries.
6. **Heightmap neighbourhood and whole-feature statistics.** Plateau and town targets are the median height
   under the region (`height.py:123`, `131`); a lake surface is the 10th percentile of the bank under it
   (`height.py:166`); a river's water surface is a running minimum along the entire centreline
   (`height.py:178-181`); mountain falloff scales with the region's maximum interior distance
   (`height.py:100-104`). Gaussian blends (60 m for roads, 80 m for banks, `edge`, ramp radius,
   `height.py:139`, `145`, `158`) spread an edit a few hundred metres. Steps are applied in a fixed order, so
   a change in an early step (hills) feeds the later ones (roads, water) where they overlap. Outside those
   radii the heightmap is byte-identical (measured).
7. **Analysis is global by definition.** Travel time from each base is a shortest-path field
   (`analysis.py:156-165`); a cost change on a route changes every cell reached through it. Territory
   shares, fair zone, traffic, the top-5 chokepoint ranking and openness-by-territory all derive from those
   fields. The chokepoint exclusion radius depends on the base-to-base distance (`analysis.py:255-256`).
8. **Shared data files.** A value in `types.yaml` applies to every map. Costs change analysis only; default
   `height`, `width`, `edge` change heightmaps; POI and crossing default radii change scatter clearance and
   therefore forests. `meta.json` records `seed`, the spec hash and `mapgen_version` (`export.py:300-302`)
   but not a hash of `types.yaml`.
9. **Render label placement** is greedy in a fixed order (MAPGEN_SPEC §6.4 step 9); one moved label can
   displace others. Images only.

### 3.3 What this means for a rebalancing edit

**[verified for the measured cases, inference beyond them]** Moving a POI or base, changing a crossing,
adding a trail at the end of `lines`, changing a region's size in place, or changing costs leaves the rest of
the terrain data byte-identical except for sub-1 % drift in scatter patch edges. The heightmap is untouched
by POI, base, crossing-disc (bridge, ford) and scatter edits. The reliable rules for a minimal-diff edit are:
edit values in place, append instead of insert, give every region and line an `id`, and set `warp` /
`warp_scale` explicitly on regions that will be resized.

## 4. Stability when mapgen gains features

### 4.1 What protects existing maps

- **Stateless noise** (`noise.py:14-28`): a new stage that draws noise with a new offset changes no existing
  value. No stream position exists to shift.
- **Every optional key has a default** applied in `schema.py`; a new key with a default that reproduces
  current behaviour leaves existing specs valid and their geometry unchanged.
- **Strict validation**: unknown keys are errors (`schema.py:348-350`), so a spec using a newer key fails
  loudly on an older mapgen.
- **Provenance in the bundle**: `meta.json` carries `seed`, `spec_sha256`, `mapgen_version`;
  `spec.resolved.json` carries every resolved default and the type table in use (`export.py:227-281`).
- **Determinism** of a build on one machine (measured: byte-identical).

### 4.2 What threatens them

- **No spec format version.** A spec cannot state which generator behaviour it was authored against, and
  mapgen has no mechanism to keep old behaviour for old specs. `mapgen/__init__.py:3` holds a manually
  maintained `__version__`.
- **Defaults live in code and in `types.yaml`.** Changing a default or a constant silently changes every map
  that relies on it.
- **No reference output.** `out/` is git-ignored (`.gitignore:4`), there is no test suite (`CLAUDE.md`,
  Verification), and no stored hashes. "Regenerates mostly identical" cannot be checked, and no tolerance
  metric defines "mostly". A regression suite is planned (`docs/roadmap.md` package 9; backlog item
  `mapgen-small-regression-suite-validation-exit`).
- **Ordered, coupled stages.** A new heightmap step changes slope, therefore the cost grid, therefore every
  analysis number and `masks/impassable.png`. A new scatter blocker shifts the scatter quantile map-wide
  (§3.2 item 3). A new rule in automatic crossings can renumber `auto_<n>` ids.
- **Positional seeds and ids** (§3.2 items 1-2): a migration that inserts entries reshapes maps.
- **Additive seed offsets without a registry** (§2.1): a new stage has to pick an offset by reading the
  code; a collision correlates two patterns.
- **Dependency drift.** `requirements.txt:1-6` gives upper bounds, not exact versions. Installed: numpy
  1.24.4, scipy 1.10.1, shapely 2.0.7 (GEOS 3.11.4), Pillow 10.4.0, scikit-image 0.21.0, PyYAML 6.0.3. A
  rebuilt venv may resolve differently. **[inference]** Polygon operations (GEOS), contour extraction and
  PNG encoding can then differ at byte level; this was not tested.
- **No way to freeze derived geometry.** Scatter patches, fragmented outlines and automatic crossings exist
  only as build output. The spec's `poly` shape is a single ring without holes (`schema.py:616`), so the
  multi-part, holed result in `regions.json` cannot be written back as explicit input to pin it.

## 5. Balance tooling

### 5.1 What exists

| output | content | per team | machine-readable |
|---|---|---|---|
| `report.json` (`build.py:30-54`) | see below | partly | yes (JSON; spec axes `x`, `y`) |
| `report.md` (`build.py:57-149`) | same data as tables | partly | no |
| `overlay_travel.png`, `overlay_territory.png`, `overlay_traffic.png`, `overlay_openness.png`, `overlay_balance.png`, `overlay_cost.png` | isochrones, territory, path heat and chokepoints, cover, fair zone, cost grid | visual | no (the underlying grids are not exported; `docs/roadmap.md` package 3) |
| `probe <spec> x y \| <poi id>` (`__main__.py:74-114`) | per point: each team's travel time, spread, cost, fair-zone membership, region, lines, crossings | yes | no — prints text; `analysis.probe_point` returns a dict (`analysis.py:328-351`) |
| `validate` | schema errors only | — | exit code 0/1 |

`report.json` fields:

- per team: `area_share[team]` (share of cells reached first, contested excluded), `objectives[].times[team]`
  (seconds), `openness_by_team[team]` (mean openness over own territory), `teams[team].base`;
- per team pair: `base_times_s`, `route_constriction`, `openness_along_routes`;
- per objective: `spread` = (max − min) / min for neutral objectives, `flag` = `IMBALANCED` (spread above
  `balance_tolerance`), `UNREACHABLE`, `EXPOSED` (an enemy reaches an owned objective first)
  (`analysis.py:209-237`);
- map-wide: `area_share.contested`, `area_share.unreachable`, `fair_zone_share`, `openness_map_mean`, top-5
  `chokepoints` (position, path count, constriction, score, nearest feature), `warnings`, `auto_crossings`.

Warnings (`analysis.py:145-150`, `200-203`, `295-318`, `geometry.py:312-317`): POI or base on an impassable
cell, base cannot reach base, road/trail over water with no crossing, fragmentation removed far more than
asked.

Thornwald at this commit **[verified]**: no warnings; base-to-base 435 s; area share A 46.8 %, B 43.8 %,
contested 8.2 %; neutral spreads 3–19 % against a tolerance of 20 %; fair zone 22.7 % of the map.

### 5.2 What does not exist

- No symmetry or mirror analysis. Concept maps are asymmetric by design.
- No per-team aggregate: no count or value of objectives each team reaches first, no summed advantage, no
  single fairness score. `tier` is exported but unused by analysis.
- One movement profile: a single `analysis.speed` and one cost table. No per-unit-class (cavalry, flier)
  fields.
- No pass/fail gate: `build` exits 0 with `IMBALANCED` flags or warnings present; only spec errors give
  exit code 1 (`__main__.py:117-142`).
- No comparison between two builds.

### 5.3 Timing (thornwald, 3200 × 2800 m, this machine)

| command | in-process | wall clock |
|---|---|---|
| `build` (all stages) | 17.1 s (render 11.1 s, export 3.4 s) | 19.5 s |
| `build --only analyze` (geometry, heightmap, analysis, report files) | 2.5 s | 4.7 s |
| `probe` (two points) | — | 4.7 s |

About 2 s of each invocation is interpreter start and imports.

## 6. How concept maps are authored

**[verified]** The `map-concept` skill (`E:\GameDev\Unity\.claude\skills\map-concept\SKILL.md`, one file)
defines this process:

1. Pitch with the user: name, two sentences, intended flow, each team's character, playtest questions.
2. Topology in words before geometry: bases, the 2–4 routes between them, the features forcing those
   routes, where objectives sit.
3. Hand-write the spec in `maps/concepts/<name>.yaml`; pitch, flow and test questions go into `brief`.
4. Build and view `map.png`, the territory, traffic and balance overlays, and `report.md`.
5. Iterate, 3–6 rounds per concept, using `probe` to test candidate objective positions and the fair-zone
   overlay to find balanced spots.
6. Present renders, base-to-base time, objective table, chokepoints.

Design goals in the skill: natural outlines (warp, fragment, scatter, meander), "asymmetric but fair" with
"no mirrored layouts", neutral objectives within `balance_tolerance` 0.2, no report warnings, 2.5–3.8 km
maps, 4–8 min base-to-base, 6–10 POIs. The skill forbids editing mapgen itself.

Specs are hand-written coordinates. No script generates or mirrors them, and no concept uses symmetry;
balance is reached by iteration against the report. Design intent — which imbalances are deliberate, target
time ranges — lives in the free-text `brief` and in prose (`MAPGEN_SPEC.md` §10 names kingsjaw's two
deliberately imbalanced objectives).

## 7. Gaps

### 7.1 For an agent loop "edit spec → rebuild → check metrics → repeat"

1. **No gate.** Nothing turns "all neutral objectives within tolerance, no warnings" into an exit code or a
   single boolean. An agent has to parse `report.json` and apply the rules itself.
2. **No declared targets.** Acceptable ranges (base-to-base time, area-share difference, per-objective
   exceptions) are not data in the spec, so intended imbalance is indistinguishable from a defect.
3. **Thin per-team metrics.** Missing: objectives reached first per team (count, weighted by `tier`),
   aggregate time advantage, per-team approach cover, any one-number balance score.
4. **`probe` prints text only**, and evaluates one spec; there is no batch "what if this POI were at these
   candidate points" in JSON.
5. **Analysis grids are images only.** The fair zone, travel-time fields and cost grid cannot be read as data
   to choose a position; an agent has to sample with `probe` (roadmap package 3 addresses the export).
6. **No build diff.** Nothing reports what changed between two builds (metric deltas, changed area per
   raster). This investigation needed an ad-hoc script for it.
7. **Features are hard to address.** Regions in the concept specs have no `id`; an instruction such as "the
   northern hill" resolves by label or list index. Spec edits are text edits to YAML with no helper that
   preserves comments and ordering.
8. **Positional seeds penalise structural edits** (§3.2 item 1): the natural edits "insert a region here" and
   "delete that forest" reshape unrelated features.
9. **Scatter drift** (§3.2 item 3): every placement edit perturbs forest edges map-wide, so "nothing else
   changed" is never literally true on maps with scatter, and diffs are noisy.
10. **Single movement profile**: balance is measured for one unit speed only.

### 7.2 For regeneration stability across mapgen versions

1. No spec format version and no compatibility behaviour keyed on it.
2. No committed reference bundle or hash list per map, and no regression suite.
3. No definition or measurement of "mostly identical" (for example: max height delta, share of changed mask
   texels, max objective-time delta).
4. Noise keys and default ids derive from list position instead of a stable identity.
5. `meta.json` omits a hash of `types.yaml`, the palette and the dependency versions.
6. Dependencies are bounded, not pinned.
7. No registry of seed offsets.
8. No bake or lock mechanism to freeze derived geometry (scatter patches, fragmented outlines, automatic
   crossings) as explicit input.
9. Scatter and fragmentation thresholds are quantiles over an area that other features define, so any
   feature that changes eligible area changes them.

## Reproduction

```
.venv/Scripts/python -m mapgen build maps/concepts/thornwald.yaml
.venv/Scripts/python -m mapgen build maps/concepts/thornwald.yaml --only analyze --out <scratch>
.venv/Scripts/python -m mapgen probe maps/concepts/thornwald.yaml lodge 1500 1560
```

The edit-locality table was produced by building text-edited copies of `thornwald.yaml` through
`mapgen.build.build(spec_path, out_root)` into a scratch directory and comparing SHA-256 per file, the share
of differing texels per raster, and `report.json` values. The scratch script is not part of the repo.
