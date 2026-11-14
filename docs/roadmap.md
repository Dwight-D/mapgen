# mapgen roadmap

## Direction

The export bundle is a machine-readable description of a map for game-side consumers. Every piece of
information a consumer needs is available as structured data (JSON or raster data with a documented
encoding), carries a version, and is listed in an index. Rendered images exist for human map authors
only.

Work is driven by what the Battlehounds consumers need (`battlehounds-consumer.md`). The detailed source
for everything below is section A of `E:\GameDev\Unity\battlehounds\docs\ai\MACRO_SIM_ASSESSMENT.md`
(what the consumer reads per file, its workarounds, and its recommendations in A.5). Read it before
designing any work package.

Work items are tracked in the parent-tier backlog (`backlog.md`); the package names below match the
item titles.

## Known gaps

| gap | detail |
|---|---|
| Analysis results exist only as rendered images | Traffic, territory, openness, travel time and fair zone are computed on a 10 m grid in `analysis.py` and written only as `overlay_*.png`. |
| Movement costs are not consumed | The cost table is in `mapgen/types.yaml` and in `spec.resolved.json.types`. The consumer hand-copied its own values and they differ (road 0.8 vs mapgen 0.6, trail 0.9 vs 0.8, ford 1.0 vs 2.5). |
| Thin linear features vanish at coarse resolution | The consumer re-rasterises masks into 16 m cells; a 10 m road averages away. Baked base-to-base travel on `thornwald` is 571 s against mapgen's 435 s, and every travel-based score inherits the error. |
| Cover is binary | `los` in `types.yaml` is yes/no per type. The consumer invents cover weights. |
| No route graph | Routes exist only implicitly in the travel-time fields; `lines.json` holds centrelines only. |
| Chokepoints are a top-5 list | Points in `report.json` with no extent, orientation or link to the routes that pass through; scores are comparable only within one map. |
| Masks overlap and lose paint order | A texel can be set in several type masks (forest under a later-painted field or clearing). Nothing states which type is on top. Edge texels take 5 levels (0, 64, 128, 191, 255) from 2×2 supersampling; this encoding is undocumented. |
| Locations are duplicated across files | The same place appears as a POI, a region around it, a crossing and a chokepoint, with nothing linking them. |
| Band crossings lose their shape downstream | `crossings.json` has the band path, but nothing strategic refers to it; the consumer collapses bands to a 30 m disc. |
| No schema, version or file index | Nothing states the export format version, the schema of each file, or which files are data and which are human renders. `report.json` sits outside `export/` and names the north axis `y` where the bundle uses `z`. |

## Work packages

Ordered so that each builds on the previous one. Package 1 is the foundation: every later package adds
files that are listed in the manifest and covered by a schema from the start.

### 1. Manifest, schema and export version

- `export_version` in `meta.json`, independent of `mapgen_version`.
- A file list in `meta.json` (or a manifest file): path, kind, schema reference, and a flag marking
  human renders.
- A JSON Schema for each JSON file in the bundle.
- All machine-readable analysis output lives inside `export/`, using the bundle's axis names
  (`x` east, `z` north, `y` height).

Open questions: one schema file or one per JSON file; whether `report.json` moves into `export/` or
stays as a human-facing file whose data is fully covered by the new export files.

### 2. Type table as data

The resolved type table (cost, `los`, splat layer, widths, crossing radii per region, line and crossing
type) is a documented, schema-covered part of the bundle, so a consumer never copies values by hand.
It is already present under `spec.resolved.json.types`; the package is to make it an explicit contract
(document it in `EXPORT_FORMAT.md`, or export a standalone `types.json`).

### 3. Analysis grids as data

`export/analysis/` holds one raw raster per analysis field on the analysis grid, plus `index.json`
describing each: cell size, width, height, origin, row order, dtype, endianness, units, nodata value.

Candidate fields: final travel cost (roads and crossings included), travel time per team in seconds,
territory (owning team index, with contested and unreachable values), traffic (path count),
constriction, openness, fair zone.

Open questions: raw binary (`.f32`, `.u8`, `.u16`) versus 16-bit PNG; row 0 = south like
`heightmap.r16`, or north like the images; whether to export at the analysis resolution only.

### 4. Masks and surface types (design session first)

Decide what masks exist, what each one means, and how overlaps resolve. This package starts with a
design discussion with the user before any implementation.

Current state:
- `export/masks/<type>.png`: 8-bit coverage, `splat_res²` (1024² default), row 0 = north. One file per
  region type, line type and crossing type present in the map. On `thornwald`: `bridge`, `clearing`,
  `field`, `ford`, `forest`, `hills`, `lake`, `river`, `road`, `ruins`, `town`, `trail`.
- Two derived masks: `water` (lakes and rivers together) and `impassable` (cells the analysis treats as
  impassable: mountains, water without crossings, cliffs, too-steep slopes; strictly binary).
- Type definitions with cost, `los` and splat layer: `mapgen/types.yaml`.
- The consumer thresholds masks at 128 and uses them for movement cost, cover and blocking.

Questions for the design session:
- Which masks are semantic categories a consumer reasons about (surface, cover, passability, water) and
  which are merely per-type coverage? Should the export offer derived semantic layers instead of, or in
  addition to, one file per type?
- A single `surface_type` index raster (top-most type per texel, paint order applied) so overlaps resolve
  without guesswork.
- Whether lines and crossings belong in the same layer as regions or in their own.
- How coverage at edges is encoded and documented.
- Whether mask resolution stays tied to `splat_res`.

### 5. `strategic.json`

- Chokepoints: id, extent (polygon, or centre and radius), orientation, width, the routes that pass
  through, raw score, and a score on a map-independent scale. More than the top five.
- Crossings with their band polyline.
- Objective balance: travel time per team, spread, flag (`IMBALANCED`, `UNREACHABLE`, `EXPOSED`).

Depends on package 6 for the route references; the two are designed together.

### 6. Route graph — `routes.json`

Nodes: bases, POIs, crossings, chokepoints, junctions. Edges: polyline, length, travel seconds per team,
and what the edge runs on (road, trail, off-road).

Open question: derive the graph from the line network (roads and trails with crossings as nodes), from
the shortest-path tracebacks the analysis already computes, or both.

### 7. Cover raster

A graded cover or line-of-sight layer as data, replacing the per-type boolean as the only source.
Candidates: export the openness field (covered by package 3), a per-type cover weight in `types.yaml`
rasterised to a `cover` grid, or a line-of-sight height layer. Cover weights are gameplay values; the
user decides them.

### 8. Zones — `zones.json`

One named zone per location: polygon, kind, parent POI, and strategic attributes, with POIs, regions,
crossings and chokepoints referring to their zone. Removes the duplication in the gap list.

Open question: authored in the map spec, generated from geometry, or generated with authored overrides.
Strategic attributes (value, base suitability) are gameplay values; the user decides them.

### 9. Regression suite

A small test suite covering spec validation, build exit codes and output determinism (file hashes) on
the example maps. Visual inspection covers the rest; new maps are generated rarely. Most useful when
landed before packages 3–8, which all touch the export stage.

## Consumer follow-up

Each package changes the contract Battlehounds reads. After a package lands, the Battlehounds side
switches from its workaround to the exported data (for example: read the cost and travel grids instead
of re-rasterising masks; read the type table instead of its copied costs). Those changes are Battlehounds
work items, filed in its backlog.
