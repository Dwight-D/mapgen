# mapgen roadmap

## Direction

The export bundle is a machine-readable description of a map for game-side consumers. Every piece of
information a consumer needs is available as structured data (JSON or raster data with a documented
encoding), carries a version, and is listed in an index. Rendered images exist for human map authors
only.

Work is driven by what the Battlehounds consumers need (`battlehounds-consumer.md`).

## Known gaps

Reported by the Battlehounds map baker on 2026-11-14.

| gap | detail |
|---|---|
| Analysis results exist only as rendered images | Traffic, territory and openness are available as `overlay_*.png` only. Consumers cannot read them. |
| Movement costs are not consumable | The cost table lives in `mapgen/types.yaml`; the consumer hand-copied its own values and they differ (road 0.8 vs mapgen 0.6, trail 0.9 vs 0.8, ford 1.0 vs 2.5). |
| Thin linear features vanish at coarse resolution | Roads mostly disappear in the consumer's 16 m cells. Baked base-to-base travel on `thornwald` is 571 s against mapgen's 435 s, and every travel-based score inherits the error. |
| Cover is binary | `los` in `types.yaml` is yes/no. The consumer invents cover weights. |
| No route graph | Routes exist only implicitly in the travel-time fields. |
| Chokepoints are a top-5 list | Points in `report.json`, without extents. |
| No schema, version or file index | Nothing in the bundle states the export version, the schema of each file, or which files are data and which are human renders. |

## Planned additions

In order of value to the consumer:

1. **Analysis grids as data.** Export travel time per team, territory, traffic, openness and cost as
   raster data with an index describing each grid (resolution, encoding, units, nodata value).
2. **`strategic.json`.** Chokepoints with extents, crossing bands, objective balance.
3. **Route graph.** Nodes and edges for the main routes between bases, objectives and crossings, with
   travel times.
4. **Schema, export version and file list.** A schema per JSON file, an export format version in
   `meta.json`, and a manifest that lists every file and marks human renders.

Further candidates from the gap list: exporting the movement cost table as data, graded cover values,
and a representation of roads that survives downsampling (vector lines with cost, or a
conservative coarse cost grid).

## Regression safety

There is no test suite. A small suite covering spec validation, build exit codes and output determinism
on the example maps is a candidate. Visual inspection covers the rest; new maps are generated rarely.
