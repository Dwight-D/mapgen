# mapgen roadmap

## Direction

**AI-first map workflow.** The owner gives creative direction. Agents edit the map spec and re-run
deterministic tools until declared targets are met. mapgen gains features while existing maps regenerate
identically from their specs.

**The export bundle is the product.** It is a machine-readable description of a map for game-side
consumers. Every piece of information a consumer needs is available as structured data (JSON or raster
data with a documented encoding), carries a version, and is listed in an index. Rendered images exist for
human map authors only.

**Balance work is a process.** mapgen provides the measurements, targets, gate and diff that let an agent
tune a map. Tuning any particular map is map-authoring work, done with those tools.

Export work is driven by what the Battlehounds consumers need (`battlehounds-consumer.md`). The detailed
consumer-side source is section A of `E:\GameDev\Unity\battlehounds\docs\ai\MACRO_SIM_ASSESSMENT.md`.

The evidence behind the packages below is in `docs/investigations/`:

| report | content |
|---|---|
| `investigations/map-editability.md` | spec inputs, where noise enters, measured edit locality, regeneration stability |
| `investigations/concept-map-balance.md` | per-team balance of the five concept maps, cost-table sensitivity, the tuning loop tested on thornwald |
| `investigations/export-open-questions.md` | what Battlehounds reads from each bundle file, options and recommendations per export question |

Work items are tracked in the parent-tier backlog (`backlog.md`). Each package below is one item; the
item title is `Mapgen: ` followed by the package title with a lower-case first letter.

## Terminology

| term | meaning |
|---|---|
| spawn base | A `teams.<team>.base` entry in a spec. It is the origin for travel-time measurement. Spawn bases sit outside the area of operations, so no metric is measured at a spawn base. |
| buildable site | A place where a base footprint fits: flat enough ground that is neither water nor impassable. It says nothing about whether the game wants a base there. |
| objective | A POI whose type has `objective: true` in `types.yaml`. |
| location trait | A measured property of any map location (sightline share, relative elevation, approach count, ...), independent of what stands there. |
| baseline | The committed per-file reference hashes of one map's build output. |
| stability rule | A build of an unchanged spec is byte-identical to its baseline. Any difference fails the regression suite. An intended change is re-baselined only after the owner approves a diff report. |
| tune loop | Edit spec → `mapgen build --only analyze` → `mapgen check` → `mapgen diff`, repeated until `check` passes. |

## Known gaps

### Export

| gap | detail |
|---|---|
| Analysis results exist only as rendered images | Traffic, territory, openness, travel time and fair zone are computed on a 10 m grid in `analysis.py` and written only as `overlay_*.png`. |
| Movement costs are not consumed | The cost table is in `mapgen/types.yaml` and in `spec.resolved.json.types`. Battlehounds keeps a hand-copied table that differs (road 0.8 vs mapgen 0.6, trail 0.9 vs 0.8, bridge 1.0 vs 0.6, ford 1.0 vs 2.5) and has no slope cost. |
| Balance numbers disagree with the game | `thornwald` is balanced under mapgen's cost table (A first to 3 neutral objectives, B to 4; no flag) and B-favoured under the Battlehounds copy (B first to all 7, two objectives over tolerance). `saltreach_delta` shows the same pattern, `ashen_steppe` the reverse. |
| Thin linear features vanish at coarse resolution | The consumer re-rasterises masks into 16 m cells; a 10 m road averages away. Baked base-to-base travel on `thornwald` is 571 s against mapgen's 435 s. |
| Cover is binary | `los` in `types.yaml` is yes/no per type. The consumer keeps its own cover weights. |
| No route graph | Routes exist only implicitly in the travel-time fields; `lines.json` holds centrelines only. |
| Chokepoints are a top-5 list | Points in `report.json` with no extent or link to crossings and routes; scores are comparable only within one map; ids are rank-based. |
| Masks overlap and lose paint order | A texel can be set in several type masks (forest under a later-painted field or clearing). Nothing states which type is on top. Edge texels take 5 levels (0, 64, 128, 191, 255) from 2×2 supersampling; this encoding is undocumented. `impassable.png` is the 10 m cost grid upsampled. |
| Locations are duplicated across files | The same place appears as a POI, a region around it, a crossing and a chokepoint, with nothing linking them. |
| No schema, version, file index or content identity | Nothing states the export format version, the schema of each file, or which files are data and which are human renders. `spec_sha256` covers the spec only, so a change in `types.yaml` or code changes a bundle without changing its identity. `report.json` sits outside `export/` and names the north axis `y` where the bundle uses `z`. The type table encodes impassable cost as the string `"inf"`. |

### Editability and regeneration stability

| gap | detail |
|---|---|
| No spec format version | A spec cannot state which generator behaviour it was authored against (package 3). The eight committed maps are guarded by the regression suite (`tests/`): per-file reference hashes and exact dependency pins. |
| List position is feature identity | Noise seeds and default export ids of regions and lines derive from list position. Inserting one region at the front of `thornwald` changes 28 of 33 output files. |
| Coordinates are repeated by hand | Features that meet repeat the same literal point (`thornwald` states B's base position on three lines). A missed copy leaves a valid spec with a different result. |
| Scatter thresholds at a map-wide quantile | Any placement edit shifts forest edges across the whole map (0.04–1.1 % of forest texels). |
| No build-to-build comparison | Nothing reports what changed between two builds. |

### Balance tooling

| gap | detail |
|---|---|
| No per-team summary | Objectives reached first, summed or weighted lead and raid times are derived by hand from the objective table. Objective `tier` is unused. |
| No gate and no declared targets | `build` exits 0 with `IMBALANCED` flags present. Only the per-objective spread has a tolerance. Intended asymmetry (kingsjaw's paired home objectives) is indistinguishable from a defect. |
| `probe` prints text only | No machine-readable probe output. |
| Location strength is unmeasured | No number for high ground, sightlines or the count of approaches. On `thornwald`, B's base stands 24 m above A's with openness 0.79 against 0.03; neither is scored. |

## Work packages

Listed in implementation order. Every package after package 1 changes build output; it lands by passing
the stability rule: the change is shown with `mapgen diff`, approved by the owner, then re-baselined.
Every package that touches `export/` updates `EXPORT_FORMAT.md` and `MAPGEN_SPEC.md` in the same commit.

| # | package | backlog item | needs |
|---|---|---|---|
| 1 | Regression suite and reference baselines | `mapgen-small-regression-suite-validation-exit` | — |
| 2 | Build diff command | `mapgen-build-diff-command` | 1 |
| 3 | Stable feature identity | `mapgen-stable-feature-identity` | 1, 2 |
| 4 | Feature references in the spec | `mapgen-feature-references-in-the-spec` | 3 |
| 5 | Local forest scatter | `mapgen-local-forest-scatter` | 3 |
| 6 | Manifest, JSON schemas and export_version | `mapgen-export-1-manifest-json-schemas-and-export` | 1, 2 |
| 7 | Type table file types.json | `mapgen-export-2-type-table-as-a-documented-part` | 6 |
| 8 | Analysis grids | `mapgen-export-3-analysis-grids-as-data-export` | 6 |
| 9 | Per-team balance summary | `mapgen-per-team-balance-summary` | 6 |
| 10 | Balance targets and check command | `mapgen-balance-targets-and-check-command` | 2, 9 |
| 11 | Surface layers | `mapgen-export-4-mask-semantics-and-surface-type` | 6, 7 |
| 12 | Cover grid | `mapgen-export-7-graded-cover-line-of-sight-raster` | 7, 8 |
| 13 | Routes and strategic.json | `mapgen-export-5-strategic-json-chokepoint-extents` | 8 |
| 14 | Location trait grids | `mapgen-location-trait-grids` | 8, 13 |
| 15 | Zones file zones.json | `mapgen-export-8-zones-json-one-deduplicated-named` | 11, 12, 13, 14 |
| 16 | map-concept skill tune loop | `mapgen-map-concept-skill-tune-loop` | 2, 10 |

The tune loop is usable once package 10 has landed.

### Implementation waves

| wave | packages | approval |
|---|---|---|
| 1 | 1–5 | approved by the owner 2026-11-14 |
| 2 | 6–10 | approved after wave 1 has landed; the tune loop is usable after package 10 |
| 3 | 11–16 | — |

### 1. Regression suite and reference baselines

- A test suite covering spec validation errors, command exit codes and the stability rule.
- Baselines: committed reference hashes, one per output file, for the three `maps/example_*.yaml` maps
  and all five `maps/concepts/*.yaml` maps. The concept maps are the bundles Battlehounds bakes and
  commits.
- Any output difference fails the suite and names the map and file.
- Re-baselining is a deliberate step, run only after the owner has approved a diff report (package 2)
  for the change.
- `requirements.txt` pins every dependency to an exact version.

### 2. Build diff command

`mapgen diff <build a> <build b>` emits a machine-readable delta between two build output directories:

- heightmap: share of texels changed, maximum height delta in metres;
- every mask and raster: share of texels changed;
- per objective: travel-time delta per team;
- per team: delta of each balance-summary value (package 9);
- the list of files that differ.

It is the diff report of the stability rule and the last step of the tune loop.

### 3. Stable feature identity

- Every region and line in a spec has an `id`. A spec with a region or line without one is invalid.
- A feature's noise seeds derive from its `id`. List position has no effect on a feature's shape; list
  order of regions is paint order only.
- A feature may carry an explicit `seed` value, which takes precedence over the id-derived seed.
- Exported ids are the spec ids.
- The id of an automatically generated crossing derives from the id of the line and the id of the water
  feature that meet, plus the crossing's order along that line.
- The spec has a top-level format version key. mapgen states which format versions it reads.

**Migration.** Each of the eight existing specs receives the format version key, an `id` on every region
and line that lacks one (the id it is exported under today), and explicit per-feature `seed` values equal
to the seeds its list position gives today. Acceptance: the heightmap, every raster and the analysis
outputs are byte-identical to their baselines. `meta.json` and `spec.resolved.json` echo the spec, and
any JSON file carrying feature or crossing ids (e.g. `crossings.json`) differs only in its id values;
these files are shown with `mapgen diff`, approved by the owner and re-baselined.

### 4. Feature references in the spec

- A point in a line `path`, a region `at` or a region outline may be the id of a POI or of a spawn base,
  or the name of an entry in `points`. It resolves to that position at load time.
- `points` is a spec section of named coordinates for shared points that are neither POIs nor spawn
  bases, such as road junctions. Features reference a point by its name.
- A reference to an unknown id or name is a validation error.
- Moving a POI, a spawn base or a named point moves everything that refers to it.

**Migration.** In the existing specs, literal points that coincide with a POI or spawn base position
become references to it (`thornwald`: B's base position on `teams.B.base`, `b_track` and `abbey_path`).
A literal point shared by several features becomes a `points` entry referenced by each of them
(`thornwald`: the junction `[2366, 1906]` on four lines). Acceptance: the heightmap, every raster and
the analysis outputs are byte-identical to their baselines; `meta.json` and `spec.resolved.json` echo
the spec and are re-baselined via an owner-approved diff.

### 5. Local forest scatter

The scatter threshold at a location is computed from that location's surroundings, so an edit to a
feature changes scatter patches only near that feature. `coverage` is the target share of eligible area.

**Migration.** The local threshold moves forest edges once on every map that uses `scatter` (about 1 % of
forest texels). Each map's shift is shown with `mapgen diff`, approved by the owner per map, then
re-baselined.

### 6. Manifest, JSON schemas and export_version

- `export_version` in `meta.json`, independent of `mapgen_version`. `EXPORT_FORMAT.md` states the bump
  policy: major for a removed or re-encoded file, minor for an added file or key.
- `meta.json.files[]` lists every file of the bundle: `path`, `kind`, `schema` (for JSON files),
  `human` (true for renders) and `sha256`. Raster entries also carry `width`, `height`, `dtype` and
  `row0`.
- One JSON Schema per JSON file, kept in the repo and copied into `export/schema/`.
- Bundle files use the axis names `x` (east), `z` (north), `y` (height).
- `report.json` is a human-facing file outside the contract. All of its data is available in bundle
  files (packages 8, 9 and 13).

### 7. Type table file types.json

- `export/types.json`: the resolved type table (cost, `los`, splat layer, widths, crossing radii per
  region, line and crossing type), covered by its own schema.
- `mapgen/types.yaml` is the single authority for movement costs, for mapgen's analysis and for the
  game.
- Impassable is encoded explicitly: `"cost": null, "passable": false`. Every `cost` is a number or null.

Battlehounds follow-up (exists in its intake): `macro-map-bake-use-mapgen-s-own-cost-table-and` — read
the exported table and grids, and remove the consumer's own cost table.

### 8. Analysis grids

`export/analysis/` holds one raw little-endian raster per field on the analysis grid (10 m,
`analysis.cell`), row 0 = south, plus `index.json` describing each: `cell_x`, `cell_z`, `width`,
`height`, origin, `row0`, `dtype`, units, `nodata`. The analysis resolution is the only resolution
exported.

| field | encoding |
|---|---|
| `cost` | final travel cost including lines, crossings and slope; 0 = impassable |
| `travel_<team>` | seconds from that team's spawn base; `+inf` = unreachable |
| `territory` | team index in `teams` order; 254 = contested, 255 = unreachable |
| `traffic` | path count, after the 3×3 dilation the analysis applies |
| `constriction` | 0–1 |
| `openness` | 0–1 |
| `fair` | fair-zone membership |
| `height` | metres |
| `slope` | rise over run |

Rule for the whole bundle: raw files are south-first, image files are north-first.

### 9. Per-team balance summary

A machine-readable block per team, in a bundle file and repeated in the report:

- area share (cells reached first);
- neutral objectives reached first: count and ids;
- summed lead in seconds over those objectives;
- tier-weighted lead: each objective's lead multiplied by its `tier`; an objective without a `tier` is
  weighted as the lowest tier;
- raid times: this team's time to each enemy-owned objective, and the enemy's time to each of its own.

Nothing in the summary is measured at a spawn base.

### 10. Balance targets and check command

- A `balance` block in the spec declares targets (ranges for summary values and objective times) and
  intended asymmetries (objectives or values exempt from a target, with a stated reason).
- `mapgen build` always succeeds on a valid spec and reports every target with its measured value.
- `mapgen check <spec>` exits non-zero when a target is missed and names each missed target, as text
  and as JSON.
- `mapgen probe --json` prints probe results as JSON.
- Default: a spec without a `balance` block is checked against the 20 % per-objective tolerance only.
  Every other target is opt-in per map.
- Threshold values are gameplay values set by the owner.

### 11. Surface layers

- `surface_type`: index raster, one top-most region type per texel, paint order applied.
- `feature_type`: index raster for lines and crossings (0 = none).
- Derived layers `water`, `impassable` and `forest`. `impassable` is generated at mask resolution.
- The index → type-name table is in `types.json`.
- Edge coverage encoding of coverage layers: value = round(coverage · 255) from 2×2 supersampling
  (0, 64, 128, 191, 255); ≥ 128 is mapgen's own inside test.
- Every raster declares its dimensions in the manifest.
- Per-type coverage images are outside the data contract.

### 12. Cover grid

- A `cover` weight per type in `types.yaml`, exported in `types.json`. Owner-approved starting values:
  forest 1.0, town 0.8, ruins 0.6, hills 0.2, every other type 0.
- `export/analysis/cover`: the weight of the top-most type per cell, on the analysis grid, listed in
  `index.json`.
- Cover weights are gameplay values; the owner decides any change.

### 13. Routes and strategic.json

`routes.json` — the route graph:

- Edges come from the shortest-path tracebacks of the analysis (base to base, base to objective). Each
  edge has a polyline, length, travel seconds per team and `via`: what it runs on (line id and type,
  crossing id, or off-road).
- Nodes: spawn bases, objective POIs, traversed crossings, chokepoints, merge and split points.
- Alternative (non-fastest) routes are included and marked as such. The method that produces them is an
  implementation choice, validated in the first step of the package.

`strategic.json`:

- Chokepoints: stable `id`, centre, `radius` (half-width of the passable gap), `width`, `crossing` (id
  or null), `bypass` (whether a route around it exists, and its extra cost in seconds), raw `count`,
  `constriction`, and `score_norm` = (paths through the cell / total traced paths) × constriction, a
  0–1 scale comparable across maps.
- Every local maximum with `score_norm` above the score floor is a chokepoint, at most 12 per map,
  at least 150 m apart.
- The score floor is a gameplay value (never-auto). It is measured on the five concept maps during
  implementation, and the owner approves the value before it is committed.
- Objective balance: travel time per team, spread, flag (`IMBALANCED`, `UNREACHABLE`, `EXPOSED`).
- Band crossings are referred to by crossing id; their shape is in `crossings.json`.

### 14. Location trait grids

Traits of any location, exported as analysis grids and sampled per zone and objective. Nothing is
measured at spawn bases.

- **Sightline share**: the share of surrounding ground visible from a location. Observers stand on a
  coarse grid (40 m). Each casts a fixed fan of rays (32–64) over the 10 m heightmap, with
  sight-blocking types added as height, out to the sight radius. Sight radius: 300 m by default
  (approximate maximum siege engagement range; infantry effective range is about 100 m, vision 500 m
  and more), a tunable parameter. Height added per sight-blocking type: forest 15 m, town 8 m, ruins
  4 m. These are owner-approved gameplay values stored in `types.yaml`.
- **Relative elevation**: height above the mean height of the ground within 300 m, in metres. The
  neighbourhood radius equals the sight radius default.
- **Approach count**: the number of distinct approaches that reach a location, from the route graph
  (package 13).

First step of the package: measure the sightline computation cost on `thornwald`. Fallback when it is
too slow for the tune loop: compute sightlines only from buildable zones and objectives.

### 15. Zones file zones.json

- One zone per location, generated from geometry: a POI, the region painted around it, and the
  crossings and chokepoints inside its footprint form one zone. Each zone has an id, polygon, centre,
  radius, kind and the ids of its members.
- Optional per-zone overrides in the spec correct grouping and names.
- Each zone carries independent trait signals, each interpretable on its own: nearby road traffic,
  relative elevation, sightline share and openness, distance to objectives, distance to chokepoints and
  crossings, approach count, travel time per team.
- `buildable` means only that a base footprint physically fits: a 20 × 20 m footprint by default
  (tunable; expected tuning range about 10 × 10 to 100 × 100 m) on ground that is below the maximum
  slope and neither water nor impassable. Slope is mapgen's analysis slope: rise over run of the
  heightmap sampled on the analysis grid, the quantity that raises travel cost above 0.15 and makes
  ground impassable above 0.7. Maximum slope is 10 degrees by default (rise over run 0.176), a tunable
  parameter.
- The file holds no judgement values. The game decides which locations it wants.
- One aggregate score per zone exists purely as a reading aid for humans and visual inspection. It is
  documented as a heuristic.

### 16. map-concept skill tune loop

The `map-concept` skill (`E:\GameDev\Unity\.claude\skills\map-concept\SKILL.md`) documents the tune
loop with exact commands: edit the spec → `mapgen build <spec> --only analyze` → `mapgen check <spec>`
→ `mapgen diff <previous build> <new build>`, repeated until `check` passes, followed by a full build
and the owner's review of the diff.

## Consumer follow-up

Each export package changes the contract Battlehounds reads. When a package lands, the matching
Battlehounds work item is filed in the Battlehounds backlog
(`E:\GameDev\Unity\battlehounds\.anthill\backlog\`): read the exported data and remove the consumer's
own derivation. Known consumer-side decisions that belong there: adopting the 10 m analysis grid as the
macro grid, gating on `export_version`, reading band-crossing shapes from `crossings.json`.
