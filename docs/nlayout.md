# Town maps as nodachi layouts (`mapgen export-nlayout`)

`mapgen export-nlayout <spec | build dir>` writes `out/<name>/export/<name>.nlayout.json`: a town's settlement plan
in nodachi's layout interchange format (`nodachi-layout` version 1, contract in the nodachi repo at
`Assets/Myrstack/Nodachi/Docs/LAYOUT-INTERCHANGE.md`). Nodachi's settlement nodes (Road Classes, Town Layout, Alleys,
District Frontage) read it as it is and pack house lots along road frontage.

- Spec target: the settlement file is found as `mapgen build` finds it (EXPORT_FORMAT.md §3.10) or given with
  `--settlement <file>`; `--out <root>` picks the build root (default `out`).
- Build-dir target (`out/<name>` or `out/<name>/export`): reads `export/settlements.json` and writes beside it.
  Both targets give byte-identical output.

## Frame and geometry

| nodachi | mapgen |
|---|---|
| `[x, 0, z]`, metres, `meta.frame: generator-local` | export `(x, z)`: x east, z north, origin the map's south-west corner; y = 0 (terrain supplies height) |
| `meta.boundaryConvention: centreline` | block polygons run along road centrelines; District Frontage insets lots by half `roadWidth` plus the setback itself |
| outer loops counter-clockwise from above | district polygons as they are (counter-clockwise); a clockwise one is reversed with its edges |
| no holes | settlement districts have none, so `holes` is never written; a plaza is its own cell |

Ids are integers in file order: cells in district order, splines in road order, points gates first, then landmarks.

## Cells — one per leaf district (block)

| attribute | type | value | consumer |
|---|---|---|---|
| `districtType` | string | `market`, `residential` (also `slum`), `industrial` (`craft`, `harbour`), `civic` (`core`, `temple`, `citadel`), `rural` (`outskirt`) | District Frontage (optional filter, copied onto lots) |
| `mapgenKind` | string | the mapgen district kind | none (passed through to lots) |
| `districtId` | string | the `town` region id in `regions.json` | none (provenance) |
| `density` | float | relative built density 0–1 | none yet |
| `depth` | int | layer depth (0 = top level) | none |
| `districtName` | string | the top-level district's map name; absent when unnamed | none |
| `settlement` | string | settlement id | none |

`lotSetback`, floors and building styles are not written, so the nodes' own defaults apply.

**Edges** — one record per polygon edge (`edges[i]` = `outer[i] → outer[i+1]`):

- `splineRef`: the spline of the road the edge runs along, when the edge lies within 1 mm of that road's polyline
  (settlements.json `offset` ≤ 0.001); else -1. Town Layout copies the spline's `roadClass`, `roadWidth`,
  `roadMaterial` and `sidewalkWidth` onto the edge; District Frontage builds only on edges with a `roadWidth`.
- `neighbour`: the cell sharing exactly this edge in the opposite direction (across the road centreline), else -1.
- `attrs.frontagePriority` (int): 1 on `main` roads, 0 on others (corner ownership in District Frontage).
- `attrs.frontage: false` on every edge of a district holding a `square` landmark (the plaza stays open).

Wall, river and open-edge sides have `splineRef: -1` and get no frontage.

## Splines — one per road

`interpolation: linear`, the road's plan polyline (a ring road is `closed: true` without the repeated point).

| attribute | type | value | consumer |
|---|---|---|---|
| `roadClass` | string | `main` for `artery`, `boulevard`, `ring`, `bridge`; `street` for `street`, `approach` | Road Classes (fills `roadMaterial`, `sidewalkWidth` from its presets), Town Layout (`roadClasses` filter), Alleys |
| `roadWidth` | float | the mapgen road width in metres (overrides the preset width) | Town Layout → edges, District Frontage (half-width inset), Road |
| `mapgenClass` | string | the mapgen road class | none |
| `roadId` | string | the `road` line id in `lines.json` | none |
| `settlement` | string | settlement id | none |

Alleys are not exported: nodachi's Alleys node carves them from blocks deeper than its `maxBlockDepth`.

## Points — gates, then landmarks

| attribute | type | value |
|---|---|---|
| `landmarkType` | string | gate kind (`gate`, `postern`, `water_gate`, `entry`); landmark kind, `square` → `plaza` |
| `gateId`, `wallId`, `roadId` | string | gate, its wall and its road (gates) |
| `landmarkId`, `mapgenKind`, `label` | string | landmarks |
| `radius` | float | landmark footprint radius (landmarks) |

Gates carry `rotationY` = the outward normal as a yaw (degrees, +Z = 0, +X = 90). No nodachi node reads point
attributes yet; Town Layout passes points through.

## Checking

nodachi's offline checker validates a file without writing anything. Build it outside the nodachi repo:

```
dotnet build E:/GameDev/Unity/nodachi/tools/layout-check/LayoutCheck.csproj -p:BaseOutputPath=<dir>/bin/ -p:BaseIntermediateOutputPath=<dir>/obj/
dotnet <dir>/bin/Debug/net8.0/layout-check.dll out/<name>/export/<name>.nlayout.json
```

`tests/test_export_nlayout.py` runs it when `NODACHI_LAYOUT_CHECK` names that `layout-check.dll`. A town plan passes
with 0 errors. Its warnings come from the plan's own vertex density: `EDGE-SHORT` (edges under 1 m where densified
curves meet straight cuts) and `CURVE-TURN` / `CURVE-SEGMENT` (warped roads turning more than 10° per vertex or with
segments under 2 m). They add corners only; `--strict` reports them as failures.
