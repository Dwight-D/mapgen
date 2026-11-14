# Export roadmap open questions — consumer-side evidence

Date: 2026-11-14. Read-only investigation; no code was changed.

| repo | path | commit |
|---|---|---|
| mapgen | `E:\GameDev\Unity\tools\mapgen` | `77e278b` (clean) |
| Battlehounds | `E:\GameDev\Unity\battlehounds` | `8b283aea` (working tree has unrelated uncommitted changes; none of the files cited here are modified) |

## Purpose

`mapgen` is a Python CLI that turns a YAML map spec into an export bundle (`out/<name>/export/`:
heightmap, splatmaps, masks, JSON). The only consumer is the Unity game Battlehounds. `docs/roadmap.md`
lists nine work packages (P1–P9) with open design questions. This report gives, for each question, what the
consumer does today, the options, a recommendation, and whether the project owner has to decide it.

Each recommendation is labelled:

- **Evidence** — follows from code or data cited here.
- **Judgement** — a reasoned default; the evidence does not force it.
- **Owner** — needs the owner's decision (gameplay value, scope, or a trade-off between projects).
- **Default** — a technical default that can be implemented without asking.

## The consumer in brief

Battlehounds has two readers of the bundle. Path prefixes: `BH/` = `E:\GameDev\Unity\battlehounds\`,
`E/` = `BH/Assets/_Project/Editor/`, `P/` = `BH/Assets/_Project/Scripts/AI/`, `MG/` = the mapgen repo.

1. **Scene importer** (`E/MapImport/Core/`). Builds a graybox Unity scene. `MapBundleReader.Read`
   (`E/MapImport/Core/Bundle/MapBundleReader.cs:39-73`) decodes `meta.json`, `heightmap.r16`, splats, every
   `masks/*.png`, `regions.json`, `lines.json`, `crossings.json`, `pois.json` and, from `spec.resolved.json`,
   only `brief` and `teams` (`:179-180`, `:271-281`).
2. **Macro map baker** (`E/MacroSim/MacroMapBaker.cs`). Bakes a 16 m strategic grid and a list of strategic
   areas (`MacroMapData`) for the macro simulator and AI planners. It uses the same reader plus
   `report.json` (`:144-150`), from which it takes only `chokepoints[]` (`:54-60`).

Facts that several answers below rest on:

- The reader refuses any `mapgen_version` whose major.minor is not `0.2`
  (`E/MapImport/Core/ImportVersion.cs:10`, `MapBundleReader.cs:50-52`).
- The reader requires every mask to be exactly `splat_res × splat_res` (`MapBundleReader.cs:115-123`,
  `:159-165`) and `splat_res` to be a power of two (`:85-87`). Masks are discovered by listing the
  `masks/` directory (`:160`), not from any index.
- PNGs are decoded with Unity's `LoadImage`, which yields 8-bit RGBA with row 0 = south
  (`MapBundleReader.cs:17-20`, `BH/docs/specs/MAP_IMPORT_SPEC.md:109`). The reader takes the heightmap from
  `heightmap.r16`, never from the 16-bit `heightmap.png` (`MapBundleReader.cs:26`, `:104-113`).
- All consumer grids index row 0 = south (`ByteGrid`, `MAP_IMPORT_SPEC.md:277`; baker grid
  `MacroMapBaker.cs:314-324`).
- Every mask read thresholds at 128 (`MacroMapBakeSettings.cs:54`; `E/MapImport/Core/Build/Exclusions.cs:44-45`;
  `BlockingBuilder.cs:8-9`, `:36`), with one exception: cover uses the fractional value
  (`MacroMapBaker.cs:330`).
- The scene importer's "already imported" key is `{spec_sha256, mapgen_version, importer version, profile
  hash, binding}` (`E/MapImport/Core/MapImporter.cs:232-234`).
- The baker's source analysis is `BH/docs/ai/MACRO_SIM_ASSESSMENT.md` section A (written against Battlehounds
  commit `5389bb7e`); its recommendations are A.5 (`:107-138`).

---

## P1 — Manifest, schema, export version

### What the consumer does

- Reads `meta.json` first, with Newtonsoft; unknown keys are ignored (`MapBundleReader.cs:46`, `:300-304`).
- Hard-codes the required file list (`MapBundleReader.cs:24-28`) and lists `masks/` from disk (`:160`).
- Does no schema validation anywhere. Unity ships no JSON Schema validator.
- Gates on `mapgen_version` major.minor (above).
- Reads `report.json` from the map folder, outside `export/` (`MacroMapBaker.cs:107`, `:144-145`), and maps
  its `y` to world z by a code comment (`:47-48`). It reads only `chokepoints[]`.
- `report.json` already carries a `files` list of everything written (`MG/mapgen/build.py:30-54`): an
  unversioned, untyped proto-manifest.
- A.5 #7 asks for `export_version`, `meta.json.files[{path, kind, schema}]` with `human: true` on renders, and
  calls the `report.json` location and axis naming "a trap" (`MACRO_SIM_ASSESSMENT.md:132-135`).

### Q1.1 One schema file or one per JSON file

| option | trade-off |
|---|---|
| One file with a `$defs` entry per bundle file | Single thing to version; manifest refs become `schema.json#/$defs/pois`; grows large. |
| One schema per JSON file | Manifest `schema` field is a plain path; each package adds its own file without touching others; more files. |

Recommendation: **one schema per JSON file**, kept in the mapgen repo and copied into `export/schema/` so a
bundle is self-describing. **Judgement / Default.** The consumer cannot validate schemas, so their real
users are the mapgen regression suite (P9) and human readers; per-file schemas match "each later package
adds files covered by a schema from the start".

### Q1.2 Does `report.json` move into `export/`

| option | trade-off |
|---|---|
| Move it into `export/` and rename axes | Breaks the baker's path and its `y` field in the same step; the file mixes human summary (`brief`, `warnings`) with data. |
| Leave it human-facing; cover all its data in export files | No consumer break; needs the covering files (P3 index for shares, P5 `strategic.json` for chokepoints, objectives, base times) before the consumer can drop it. |

Recommendation: **leave it human-facing and outside `export/`; its data is fully covered by export files.**
**Judgement / Default.** The consumer needs exactly one key from it, and P5 delivers that key in better form.
Until P5 lands the baker keeps reading `report.json`; `EXPORT_FORMAT.md` states that it is not part of the
contract. Consequence for sequencing: the P1 goal "all machine-readable analysis output lives inside
`export/`" is only reached when P3 and P5 land.

### Q1.3 Manifest inside `meta.json` or a separate file

| option | trade-off |
|---|---|
| `meta.json.files[]` | The consumer already reads `meta.json` first; one fewer file; what A.5 #7 asks for. `meta.json` lists itself without a content hash. |
| Separate `manifest.json` | `meta.json` stays small; one more required file and one more read. |

Recommendation: **inside `meta.json`.** **Evidence** (A.5 #7; reader flow). **Default.**

### Additional findings for P1

- **Per-file content hash in the manifest.** `spec_sha256` hashes the spec file only
  (`MG/mapgen/schema.py:813`). A change to `mapgen/types.yaml` or to mapgen code changes the bundle without
  changing `spec_sha256`, and without a version bump the importer reports "up to date"
  (`MapImporter.cs:101`, `:232-234`). A `sha256` per file (or one bundle hash) in the manifest closes this.
  No package covers it. **Evidence / Default.**
- **Version policy.** Adding `export_version` leaves the consumer working only while `mapgen_version` stays
  `0.2.x`; a bump to `0.3` makes the reader refuse the bundle until Battlehounds updates
  `ImportVersion.SupportedMapgen`. The consumer should gate on `export_version` once it exists. State in
  `EXPORT_FORMAT.md` which changes bump `export_version` major (removed or re-encoded file) and minor (added
  file or key). **Default.**
- Each raster entry in the manifest carries its own `width`, `height`, `dtype` and `row0`, so no consumer
  has to infer dimensions from `splat_res`.

**Owner needed for P1: no.**

---

## P2 — Type table as data

### What the consumer does

- Reads nothing from `spec.resolved.json.types` (`MapBundleReader.cs:179`).
- Keeps its own table in `E/MacroSim/MacroMapBakeSettings.cs:62-75`:

| type | consumer | mapgen (`MG/mapgen/types.yaml`) |
|---|---|---|
| road | 0.8 | 0.6 |
| trail | 0.9 | 0.8 |
| bridge | 1.0 | 0.6 |
| ford | 1.0 | 2.5 |
| pass | 1.0 | 1.5 |
| ramp | 1.0 | 1.2 |
| field, clearing, plateau 1.0; town 1.1; hills, ruins 1.3; forest 1.6; marsh 2.5 | same | same |

- The consumer has no slope cost. mapgen multiplies cost by `1 + 2·max(0, slope − 0.15)` and blocks slopes
  above 0.7 (`MG/mapgen/analysis.py:86-95`).

### Q2.1 Document `spec.resolved.json.types` or export a standalone `types.json`

| option | trade-off |
|---|---|
| Document the existing key | No new file. The key sits in a file that uses spec axis naming and holds authored (not final) geometry; a consumer has to parse the whole spec to get the table. |
| Standalone `types.json` | Small, independently schema-covered and versioned; the table then exists in two places inside the bundle. |

Recommendation: **standalone `types.json`**; `spec.resolved.json` keeps its copy as part of "the whole spec
as data". **Judgement / Default.**

Encoding finding (**Evidence / Default**): impassable cost is written as the string `"inf"`
(`MG/mapgen/export.py:223-224`, `:275-277`), so `cost` is number-or-string. A typed C# reader with a `float`
field fails on it. `types.json` encodes impassable explicitly, e.g. `"cost": null, "passable": false`.

Scope note: once P3 exports the final cost grid, the consumer no longer needs type costs for movement. It
then uses the type table for `los`, for labels, and as the key set for its own per-type sim weights.

### Q2.2 Deliberate tuning or accidental drift

Evidence points to **drift, not tuning**:

- The values appear in one place with no rationale: `BH/docs/specs/MACRO_SIM_SPEC.md:498` ("road 0.8 … bridge
  and ford cells = 1.0"). `trail 0.9`, `pass`/`ramp` = 1.0 were added during implementation as deviation
  W1-D4 (`MACRO_SIM_SPEC.md:1215-1216`), again without a reason.
- The research notes written before that spec record the mapgen values correctly: "road (0.6× cost), trail
  (0.8×)" (`BH/.anthill/supervisor/scratchpad/2026-11-14-macro-sim-mapdata.md:56-57`).
- `BH/docs/DECISIONS.md` has no entry on macro movement costs.
- The settings file has a single commit (`cf0dac7b`); the values were never revisited.
- The consumer's own assessment lists them under "Guesses, hardcoded values and heuristics"
  (`MACRO_SIM_ASSESSMENT.md:49-54`) and its backlog item says "Baker copies costs by hand"
  (`BH/.anthill/backlog/intake/macro-map-bake-use-mapgen-s-own-cost-table-and.md`).
- A single flat crossing cost of 1.0 for all four crossing types is a simplification, not a per-type choice.

Effect of adopting mapgen's values: fords become 2.5× slower and roads faster in the sim, and slope cost
appears. Sim benchmarks were calibrated on the current grid (`MACRO_SIM_SPEC.md:1196-1207`, `:1523-1535`)
and will shift.

Recommendation: **mapgen's `types.yaml` is the single authority for movement cost.** **Evidence** for drift;
**Owner** to confirm, because movement cost is a gameplay value and the owner may know of an intent that
was never written down.

**Owner needed for P2: one confirmation (Q2.2).**

---

## P3 — Analysis grids as data

### What the consumer does

- Recomputes cost on a 16 m grid by re-sampling masks, 5×5 samples per cell (`MacroMapBaker.cs:296-351`), and
  recomputes travel time with its own Dijkstra at 5 m/s (`:372-408`; `MacroMapBakeSettings.cs:56`). mapgen's
  analysis speed is also 5 m/s (`spec.resolved.json.analysis.speed`), so the fields are directly comparable.
- Result: roads average away. Base-to-base travel on `thornwald` is 571 s baked against mapgen's 435.3 s; on
  `kingsjaw` 499 s against 454.6 s (`MACRO_SIM_ASSESSMENT.md:55-60`; `out/<map>/report.json.base_times_s`).
- Stores cost as a byte, `cost × 32`, 0 = impassable (`MacroMapBaker.cs:353-355`); travel as `float[]` with
  `+inf` for unreachable (`:375-376`).
- Runtime users: `MacroNav` A* over `cost` (`P/Macro/MacroNav.cs:65`, `:196-209`); planners through
  `HomeTravel` and `Front = tOwn / (tOwn + tEnemy)` (`P/Macro/MacroMapQueries.cs:42-57`;
  `P/Macro/GoalPlanners.cs:138-143`, `:578`, `:654-657`, `:719-738`, `:825`).
- Reads no traffic, territory, openness, constriction or fair-zone data. Territory is implicit in `Front`.

### Q3.1 Raw binary or 16-bit PNG

| option | trade-off |
|---|---|
| Raw little-endian (`.f32`, `.u8`, `.u16`) + `index.json` | Exact floats; trivial to read (`File.ReadAllBytes` + `Buffer.BlockCopy`); the consumer already reads `heightmap.r16` this way. Not viewable in an image tool. |
| 16-bit PNG | Viewable. Float fields must be quantised with a scale and offset per file. Unity's `LoadImage` reduces PNGs to 8 bits, so the consumer's existing decode path cannot read them. |

Recommendation: **raw binary.** **Evidence** (`MapBundleReader.cs:104-113`; `MAP_IMPORT_SPEC.md:109`; A.5 #1).
**Default.** The `overlay_*.png` renders remain the human view.

### Q3.2 Row 0 south or north

Recommendation: **row 0 = south**, the convention of `heightmap.r16` and of every consumer grid.
**Evidence** (`MAP_IMPORT_SPEC.md:277`; `MacroMapBaker.cs:314-324`; A.5 #1 writes `row0:"south"`).
**Default.** Rule to document: raw files are south-first, image files are north-first; `index.json` states
`row0` per file. mapgen's internal arrays are north-first (`MG/mapgen/analysis.py:88`), so the writer flips,
as `write_heightmap` does (`MG/mapgen/export.py:103`).

### Q3.3 Analysis resolution only, or others

| option | trade-off |
|---|---|
| Analysis grid only (10 m, `spec.analysis.cell`) | One grid, identical to what mapgen's own numbers come from. The consumer either adopts 10 m as its macro grid (thornwald 320×280 = 89 600 cells against 35 000 at 16 m; about 2.6× memory and A* work) or resamples and loses thin roads again. |
| Also export a consumer-chosen resolution | Removes consumer work; mapgen then carries a second, consumer-specific rasterisation whose travel times differ from the report. |

Recommendation: **analysis resolution only; the consumer adopts mapgen's grid** (cell size read from
`index.json`). **Judgement.** The mapgen side is a **Default**; whether the macro sim moves from 16 m to
10 m cells is a Battlehounds decision with a performance cost, to be raised with the owner as a consumer
follow-up, not as a mapgen design question.

`index.json` must carry `cell_x` and `cell_z` separately: the grid is `ceil(size / cell)` cells stretched
over the map (`MG/mapgen/analysis.py:135-137`), so cells are not exactly square when the size is not a
multiple of the cell.

### Q3.4 Which fields the consumer would use

| field | consumer use | priority |
|---|---|---|
| `cost` (final, incl. lines, crossings, slope; 0 = impassable) | replaces `Rasterise` cost; feeds `MacroNav` | must |
| `travel_<team>` (seconds; `+inf` unreachable) | replaces `TravelField`; feeds `HomeTravel`, `Front`, every planner score | must |
| `constriction` | chokepoint radius/width and value; currently read from the report and unused (`MacroMapBaker.cs:49-50`) | useful |
| `traffic` | lane overlay, chokepoint context | useful |
| `openness` | candidate cover input (see P7) | useful, depends on P7 |
| `territory`, `fair` | derivable from the travel fields; no current reader | low |
| `height`, `slope` on the analysis grid (not in the roadmap's candidate list) | the sim has no elevation or slope term; A.5 #6 asks for "elevation_advantage or simply use the heightmap" | candidate addition |

Recommendation: **export every candidate field** (each is under 400 kB at 10 m and the index makes extras
free to ignore) **and add `height` and `slope`.** **Evidence** for the two must-haves; **Judgement** for the
rest. **Default.**

Encoding details to settle in the spec (**Default**): `cost` uses 0 for impassable (matches the consumer's
byte encoding and A.5 #1); travel uses IEEE `+inf` for unreachable, stated as `nodata` in the index;
`territory.u8` = team index in `teams` order, 254 contested, 255 unreachable (source arrays are separate:
`owner` int8 and `contested` bool, `analysis.py:172-178`); `traffic` is exported as computed, i.e. after the
3×3 dilation (`analysis.py:243`), and the index says so.

**Owner needed for P3: no** (the 16 m → 10 m consumer change is a Battlehounds follow-up).

---

## P4 — Masks and surface types

### What the consumer reads, and for what

| reader | masks | use |
|---|---|---|
| Baker, blocking | `impassable`, `water`, `lake`, `river`, `mountain` | cell impassable when ≥ 25 % of samples blocked (`MacroMapBakeSettings.cs:71`, `:77`; `MacroMapBaker.cs:327`, `:346`) |
| Baker, crossings | `bridge`, `ford`, `pass`, `ramp` | any sample makes the cell passable (`MacroMapBakeSettings.cs:73`; `MacroMapBaker.cs:325`, `:345`) |
| Baker, path cost | `road`, `trail` | overrides terrain cost (`MacroMapBakeSettings.cs:69`; `MacroMapBaker.cs:336-337`) |
| Baker, terrain cost | `field`, `clearing`, `plateau`, `town`, `hills`, `ruins`, `forest`, `marsh` (`grass` is listed but no such mask exists) | max cost over set masks (`MacroMapBakeSettings.cs:62-67`; `MacroMapBaker.cs:338-340`) |
| Baker, cover | `forest`, `town`, `ruins`, `hills` | `max(mask/255 · weight)` (`MacroMapBakeSettings.cs:80-83`; `MacroMapBaker.cs:329-331`) |
| Baker, backdrop | most of the above | greyscale overlay background (`MacroMapBaker.cs:643-706`) |
| Scene importer, trees | `forest` | tree placement, combined with the forest polygon and the importer's own paint-order lookup `TopRegionAt` (`E/MapImport/Core/Build/TreeScatter.cs:30`, `:52-53`) |
| Scene importer, exclusions | `water`, `impassable` | no props where ≥ 128 (`Exclusions.cs:25-26`, `:44-45`) |
| Scene importer, blocking | `impassable`, `water` | NavMesh "not walkable" volumes and water walls, built per mask texel (`BlockingBuilder.cs:8-12`, `:26-36`) |

`water.png` and `impassable.png` are the only masks the reader requires (`MapBundleReader.cs:26`).

Findings:

- Every use except cover is binary at 128.
- Overlaps are resolved wrongly by the baker: it takes max cost and max cover, so forest wins under a
  later-painted field or clearing (thornwald forest∩field 8 953 texels, kingsjaw 27 723;
  `MACRO_SIM_ASSESSMENT.md:65-67`). The scene importer resolves paint order itself from `regions.json`.
- `impassable.png` has 10 m real resolution: it is the analysis cost grid upsampled with nearest-neighbour
  to `splat_res` (`MG/mapgen/export.py:160`). `water.png` and the per-type masks are rasterised at
  `splat_res` (about 3 m texels). The importer builds NavMesh blocking from `impassable` at texel
  resolution, so its blocking edges are 10 m steps.
- After P3, the baker needs masks only for cover and the backdrop. The scene importer still needs `forest`,
  `water` and `impassable` at fine resolution.

### Q4.1 Would a single `surface_type` index raster replace the per-type masks

For region types: **yes for every current consumer use.** All of them are binary, and an index raster with
paint order applied removes the overlap error. **Evidence.** The one thing lost is fractional edge coverage,
used only by the baker's cover term, which is averaged over 25 samples per cell anyway.

It does not replace `water` and `impassable`, which are derived semantic layers (`impassable` includes
slope) and are what the scene importer reads.

### Q4.2 Lines and crossings in the same layer or separate

| option | trade-off |
|---|---|
| Same layer (regions < lines < crossings, top-most wins) | One file; matches mapgen's cost order (`analysis.py:62-85`). Loses the terrain under a road or crossing. |
| Separate `feature_type` raster for lines and crossings | A consumer can ask "what ground" and "is there a road or crossing" independently, as the baker's rules do (blocking, then crossing, then path, then terrain). Two files. |

Recommendation: **two index rasters, `surface_type.u8` (regions) and `feature_type.u8` (lines and
crossings)**, with the index → type-name table in the manifest or `types.json`. **Judgement.**

### Q4.3 Edge coverage encoding

Fact: coverage is the mean of 2×2 supersamples (`MG/mapgen/raster.py:71-87`), written as
`round(coverage · 255)`, giving exactly 0, 64, 128, 191, 255. mapgen's own inside test is coverage ≥ 0.5
(`raster.py:90-91`), which equals mask value ≥ 128 — the threshold the consumer already uses.

Recommendation: **document this as the contract**: "value = round(coverage · 255); ≥ 128 is mapgen's own
inside test". **Evidence / Default.**

### Q4.4 Should mask resolution stay tied to `splat_res`

The reader refuses a mask whose size differs from `splat_res` (`MapBundleReader.cs:121-122`). Non-square maps
give non-square texels (thornwald 3.13 × 2.73 m).

Recommendation: **keep masks at `splat_res` by default, and declare width and height per raster in the
manifest** so the tie is stated, not assumed. Export `impassable` rasterised at mask resolution, or state its
10 m origin in the manifest. **Judgement / Default.**

### What the design session has to decide

- Which per-type masks remain once `surface_type` exists (candidates to keep: `forest` for scattering,
  `water`, `impassable`; the rest become redundant).
- Whether the bundle offers further derived semantic layers (cover, passability) or leaves derivation to the
  consumer using `surface_type` + `types.json`. This is the same decision as P7.

**Owner needed for P4: yes** — the roadmap already assigns it a design session. The evidence above narrows
the session to the two points just listed; Q4.3 and Q4.4 are defaults.

---

## P5 / P6 — `strategic.json` and the route graph

### What the consumer does

- No route graph. Movement is grid A* with a path cache (`P/Macro/MacroNav.cs`); planners score areas by
  travel time and `Front` (`GoalPlanners.cs:578`, `:654-657`, `:719-738`).
- Chokepoints become disc areas (`MacroMapBaker.cs:495-506`): position from the report; **radius 50 m, a
  guess** (`MacroMapBakeSettings.cs:95`); `value = 0.5 + 0.5 · score / maxScore` **normalised per map**
  (`:97`; `MacroMapBaker.cs:496-504`); label from `near`; `canHostBase = true` (`:98`).
- `count` and `constriction` are parsed and never used (`MacroMapBaker.cs:49-50`).
- An auto crossing within 60 m of a chokepoint is dropped as a duplicate (`MacroMapBaker.cs:547-554`;
  `MacroMapBakeSettings.cs:105`) — a distance heuristic standing in for a missing link between the two.
- A strategic area is `pos + radius` only (`MACRO_SIM_SPEC.md:506-516`). Polygons and orientation have no
  place to go in the consumer today.
- Band crossings: `crossings.json` already contains the band polyline and width
  (`MG/mapgen/export.py:207-219`; kingsjaw `needle` has 48 points, `eyrie` 52). The baker discards them when
  it copies crossings (`MacroMapBaker.cs:175-178`) and substitutes a 30 m disc
  (`MacroMapBakeSettings.cs:101`). **This gap is consumer-side; the data is already exported.**
- mapgen stops at five chokepoints by a literal (`MG/mapgen/analysis.py:264`), at least 150 m apart (`:22`,
  `:273`). Raw score = path count × constriction (`:260`). Top scores: thornwald 0.42, 0.42, 0.36, 0.17, 0.16;
  kingsjaw 1.90, 1.85, 1.66, 1.57, 1.15 (`out/<map>/report.json`).

### Q6.1 Derive the graph from the line network, from tracebacks, or both

| option | trade-off |
|---|---|
| Line network (roads, trails; crossings and junctions as nodes) | Clean topology and `via` labels. Misses off-road legs; two of thornwald's five chokepoints are not on a crossing or junction ("field Kingsmead", "river Mossbeck"). Travel seconds must be computed separately. |
| Shortest-path tracebacks (base→base, base→objective; already computed, `analysis.py:205`, `:240`) | Exactly the routes behind the travel fields, traffic and chokepoint counts, so "routes through this chokepoint" is consistent by construction. Contains only optimal routes; needs path merging to form a graph. |
| Both | Tracebacks give the used routes; the line network adds unused alternatives (flanking roads). Most work; two edge provenances to explain. |

Recommendation: **tracebacks as the edge source, each edge labelled `via` by intersecting it with lines and
crossings; nodes at bases, objective POIs, traversed crossings, chokepoints and merge/split points.**
**Judgement.** Reason: A.5 #2 defines a chokepoint's `routes[]` as "which base↔objective paths use it"
(`MACRO_SIM_ASSESSMENT.md:116-119`), which is a statement about tracebacks. Adding unused line-network edges
is a later extension.

This is the least evidenced package: no consumer code exists that would read a route graph. The one question
worth the owner's time is whether AI planners should know about **alternative (non-shortest) routes** for
flanking and raiding; a yes selects "both".

### Q5.1 What the consumer needs from a chokepoint

| attribute | need | basis |
|---|---|---|
| stable `id` | yes | areas are referenced by id; current ids are rank-based (`choke_<rank>`) and change when scores change |
| centre | yes | used today |
| radius or width | yes | replaces the 50 m guess; one number is all a disc area can hold |
| polygon extent | no reader today | `MacroArea` is a disc |
| orientation | no reader today | useful later for "which side do I hold"; cheap to emit as the route direction at the centre |
| score on a map-independent scale | yes | replaces per-map normalisation, which values a weak thornwald chokepoint like kingsjaw's strongest (`MACRO_SIM_ASSESSMENT.md:74-77`) |
| `routes[]` | wanted (A.5 #2) | no reader yet |
| link to a crossing or zone | yes | replaces the 60 m dedup heuristic |

Recommendation: **emit centre, `radius` (half-width of the passable gap), `width`, `orientation`, `routes[]`,
`crossing` (id or null), raw `score`, and `score_norm`; emit a polygon only if it falls out of the
computation.** **Evidence** for the required set; **Judgement** on polygon and orientation. **Default.**

Score scale. Recommendation: **`score_norm = (paths through the cell / total traced paths) × constriction`**,
both factors in [0, 1], so the value means "share of all base and objective routes that pass here, times how
enclosed the spot is". Total traced paths = base pairs + objectives × teams (19 on thornwald, 15 on
kingsjaw). Raw `count` and `constriction` stay in the file. **Judgement / Default.** How the sim turns the
score into an area value stays in Battlehounds.

### Q5.2 How many chokepoints

| option | trade-off |
|---|---|
| Fixed top N | Predictable size; includes weak entries on open maps and cuts real ones on tight maps. |
| All local maxima above a score floor, 150 m apart, with a cap | Count reflects the map; needs a floor value. |

Recommendation: **all local maxima with `score_norm` above a floor, capped (about 12);** `report.md` keeps
showing the top five. **Judgement.** The floor decides what counts as a chokepoint for AI planners, so the
owner should see the resulting lists for the five concept maps and confirm; it is a light **Owner** item.

**Owner needed for P5/P6: two light items** — alternative routes (yes/no) and the chokepoint floor.

---

## P7 — Cover

### What the consumer invented and how it uses it

- Weights: forest 1.0, town 0.8, ruins 0.6, hills 0.2 (`MacroMapBakeSettings.cs:80-83`;
  `MACRO_SIM_SPEC.md:499`). mapgen marks forest, mountain, town, ruins and cliff as `los: true` and hills as
  `los: false` (`MG/mapgen/types.yaml`).
- Rasterisation: per sample `max(mask/255 · weight)`, mean over the 16 m cell, stored 0–255
  (`MacroMapBaker.cs:329-331`, `:349`).
- Uses:
  1. **Battle resolution.** A battle's cover `c` is the mean of `Cover(pos)` over its participants
     (`P/Simulation/GameMaster/GameMaster.cs:253-254`; `MACRO_SIM_SPEC.md:631`). Unit output is
     `attack × lerp(openMult, coverMult, c)` (`MACRO_SIM_SPEC.md:694`): artillery 1.6 → 0.5, berserker
     0.9 → 1.4 (`:646-647`). Cover decides whether missile or melee troops win.
  2. **Area attribute.** `MacroArea.cover` = mean cover within the area radius (`MacroMapBaker.cs:190`;
     `P/Macro/MacroMapQueries.cs:131-144`).
  3. **Scenario authoring.** Benchmarks pick locations by cover threshold (`MACRO_SIM_SPEC.md:1002`,
     `:1360-1361`).
- Thornwald's mean cover is 0.67, so almost every battle there runs in heavy cover
  (`MACRO_SIM_ASSESSMENT.md:61-64`).
- The sim has no line-of-sight or elevation model (`MACRO_SIM_ASSESSMENT.md:92-93`).

The consumer's cover is therefore a **local, graded, per-type value sampled at a unit's position**.

### The three candidates against that use

| candidate | fit |
|---|---|
| Openness field (`1 − box_filter(los, 100 m)`, `analysis.py:279-288`) | Poor as the cover source. It is binary per type, so town and forest are equal and hills are zero; the 100 m filter blurs it, so a unit in a 150 m clearing inside forest reads as partly covered. Good as a separate "how open is this neighbourhood" signal. |
| Per-type cover weight rasterised to a `cover` grid | Direct fit: it is the baker's computation, done at mapgen's resolution with paint order applied. |
| Line-of-sight height layer | No reader: the sim has no LOS model. Useful only for a future LOS or overwatch feature. |

Recommendation: **per-type cover weight, rasterised** on the analysis grid (`cover.u8` or `.f32` in
`export/analysis/`). **Evidence.** Openness is exported under P3 regardless.

### The decision behind it

A.5 pulls two ways: #4 says cover weights are sim-specific and stay in Unity, keyed by mapgen type; #6 asks
mapgen for a cover raster (`MACRO_SIM_ASSESSMENT.md:123-131`). Options:

| option | trade-off |
|---|---|
| `cover` weight per type in `types.yaml`; mapgen exports the raster | One source; the map author sees cover in overlays and reports; retuning cover means rebuilding bundles. |
| Weights stay in Unity; the consumer computes cover from `surface_type` (P4) and its own table | Fast tuning inside the sim; mapgen analysis and map authoring cannot see cover; P7 shrinks to nothing beyond P4. |

**Owner:** (1) is cover a map property owned by mapgen or a sim tuning value owned by Battlehounds; (2) the
weights themselves, including whether hills give cover at all. Both are gameplay values.

**Owner needed for P7: yes.**

---

## P8 — Zones

### What the consumer does

- Builds strategic areas from four unrelated sources: POIs by type, regions by type, crossings, chokepoints
  (`MacroMapBaker.cs:459-544`). Every `value` and `canHostBase` is a hand-picked constant per type
  (`MacroMapBakeSettings.cs:86-110`; table at `MACRO_SIM_SPEC.md:520-530`).
- Regions become a disc at the centroid of the largest polygon, radius `min(150, √(area/π))`; holes and
  other parts are ignored (`MacroMapBaker.cs:557-574`). Large regions are truncated (thornwald Ravenscar
  equivalent radius 280 m).
- The same place appears two to four times, because map authors paint a clearing or ruins around a POI:
  thornwald `thornhold` + ruins `region_11` + Kingsmead `region_3`; kingsjaw `gate_keep` + pass `gate` +
  `region_11` + two chokepoints (`MACRO_SIM_ASSESSMENT.md:78-82`). Planners score each copy separately.
- POI types `treasure` and `spawn` have no rule and are dropped; `tier` is ignored
  (`MacroMapBakeSettings.cs:86-94`; `MacroMapBaker.cs:475-476`).
- Attributes the planners read: `value` (`GoalPlanners.cs:578`, `:657`, `:722`), `canHostBase` (`:295`,
  `:652`; `P/Simulation/SimBase.cs:224`), `kind`, `pos`, `radius`, `cover`, `homeSide`.
- The map spec has no zone concept and no strategic fields (`MG/mapgen/schema.py`).

### Q8.1 Authored, generated, or generated with overrides

| option | trade-off |
|---|---|
| Authored in the spec | Exact designer intent; every one of the five concept maps and the `map-concept` skill must be extended; easy to leave incomplete. |
| Generated from geometry | No authoring cost; merge rules (a POI plus the labelled region containing it, plus a crossing or chokepoint within its footprint) will misgroup some places. |
| Generated with authored overrides | Works for existing maps unchanged; the author corrects grouping and sets values where the default is wrong; two mechanisms to document. |

Recommendation: **generated with authored overrides.** **Judgement.** The duplication is a by-product of how
maps are authored, so generation fixes most of it at no authoring cost, and gameplay values need a place to
be authored anyway.

### Q8.2 Strategic attributes the consumer wants

- Used today: `kind`, `value` (0–1), `can_host_base`, plus geometry.
- Asked for in A.4/A.5 #5: `strategic_value`, `can_host_keep`, parent POI, polygon, one zone per location;
  also defensive value and sightline/overwatch spots (`MACRO_SIM_ASSESSMENT.md:101-102`, `:127-129`).
- Derivable by mapgen without authoring: travel time per team, `front` ratio, mean cover/openness, area,
  contained POIs, crossings and chokepoints.

Recommendation: zones carry **derived facts by default and authored `value` / `can_host_base` only where the
author sets them**; per-kind default values stay in Battlehounds settings until the owner moves them.
**Judgement.** A zone needs a centre and radius next to its polygon, because the consumer's areas are discs.

**Owner:** authored vs generated; which attributes exist; and where per-kind values live (mapgen `types.yaml`
or Battlehounds settings). The values are gameplay values.

**Owner needed for P8: yes.**

---

## P9 — Regression suite

### What in the consumer depends on byte determinism

- **Nothing compares bundle bytes.** No Battlehounds test reads a real bundle; `MacroMapBakeTests` uses
  synthetic grids and inline JSON (`BH/Assets/_Project/Tests/EditMode/MacroMapBakeTests.cs:12`;
  `MACRO_SIM_SPEC.md:1044`).
- **The import key assumes determinism.** "Up to date" is decided by `spec_sha256` + `mapgen_version` + importer
  and profile hashes (`MapImporter.cs:232-234`), which is only valid if the same spec and version always give
  the same bundle. The importer's own content hash promises "same bundle, same output"
  (`MAP_IMPORT_SPEC.md:17`, `:486-504`).
- **Baked assets are committed** (`MACRO_SIM_SPEC.md:565-566`), while `out/` is git-ignored
  (`MG/.gitignore`). Any change in mapgen output shows up as a diff in Battlehounds assets on the next bake.
  The baker has no staleness stamp, only a `bakedAt` timestamp (`MacroMapBaker.cs:200-201`).
- Sim benchmarks are calibrated against baked grids (`MACRO_SIM_SPEC.md:1196-1207`), so silent output drift
  moves benchmark results.

Recommendation: **land P9 before P3–P8, and include per-file hashes of all five concept maps** — they, not the
three example maps, are the bundles Battlehounds bakes and commits. The same hashes feed the manifest field
proposed under P1. **Evidence / Default.**

**Owner needed for P9: no.**

---

## Package order and dependencies

A.5 ranks by consumer value: analysis grids, `strategic.json`, route graph, type table, zones, cover, schema
and version, masks (`MACRO_SIM_ASSESSMENT.md:107-138`). The roadmap orders by build dependency, with the
manifest first.

Dependencies found:

- P3 alone fixes the consumer's one filed request (cost table and vanishing roads; `BH/.anthill/backlog/intake/macro-map-bake-use-mapgen-s-own-cost-table-and.md`),
  because the exported cost grid already contains mapgen's type costs. P2 is small and is needed alongside
  only for `los` and type keys.
- P5 needs P6 (route references) and benefits from P8 (zone references); P8 needs P5's chokepoint ids.
- P7 depends on the P4 decision (whether the consumer derives cover from `surface_type`) and on P3 (grid
  container).
- P1's goal of all machine data inside `export/` completes only with P3 and P5.

Recommended order (**Judgement**):

1. **P9** regression suite, with concept-map hashes.
2. **P1** kept thin: `export_version`, `meta.json.files[]` with per-file hash, schemas for the files that
   exist today.
3. **P2 + P3** together — the highest consumer value and the fix for the filed request.
4. **P6 + P5** together.
5. **P4 + P7** in one design session, since they are one decision about where semantic layers are derived.
6. **P8** last; it links everything before it.

## Consumer needs no package covers

| need | detail | suggested home |
|---|---|---|
| Bundle content identity | `spec_sha256` ignores `types.yaml` and code changes; the importer can report a stale scene as up to date | P1: per-file `sha256` in the manifest |
| Elevation and slope for the sim | no high-ground or slope term in the sim; A.5 #6 asks for it; mapgen already computes slope for cost | P3: add `height` and `slope` grids |
| `"inf"` as a string in the type table | breaks typed readers | P2 |
| Band crossing shape | already exported in `crossings.json`; discarded by the baker | Battlehounds work item, not a mapgen package; drop "crossings with their band polyline" from P5 or reduce it to a reference |
| `impassable` at mask resolution | currently the 10 m grid upsampled; drives NavMesh blocking | P4 |
| Overwatch / sightline spots | asked for in A.4; nothing computes them | none; needs an LOS model first |
| Three-team maps | the baker handles teams A and B only (`MACRO_SIM_SPEC.md:1214`) | Battlehounds |
| `treasure`, `spawn` POIs and camp `tier` dropped | no rule in the baker | Battlehounds |
| Stale-bake detection | baker records no source hash | Battlehounds, using the P1 manifest hash |

## Questions that need the owner

1. **P2:** confirm that mapgen's cost table (road 0.6, trail 0.8, bridge 0.6, ford 2.5, pass 1.5, ramp 1.2, plus
   slope cost) is the authority for the sim, and that the consumer's values were not intended tuning.
2. **P4:** which per-type masks remain once a `surface_type` raster exists; whether the bundle offers derived
   semantic layers.
3. **P7:** is cover a map property in mapgen or a sim tuning value in Battlehounds; the per-type cover
   weights, including hills.
4. **P8:** zones authored, generated, or generated with overrides; which strategic attributes exist and where
   their values live.
5. **P5/P6 (light):** should the route graph include alternative, non-shortest routes; the score floor that
   defines a chokepoint.
6. **Ordering:** accept the order above, in particular P2+P3 ahead of the rest and P4+P7 as one session.

Everything else in this report is a technical default.
