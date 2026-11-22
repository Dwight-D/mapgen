# Land cover as a nodachi layout (`mapgen export-landcover`)

`mapgen export-landcover <spec | build dir>` writes `<bundle>/<name>.landcover.nlayout.json`: a map's land-cover
areas (forest, field, hills, ...) and the areas that must stay free of scattered nature (roads, rivers, water, the
town, POIs) in nodachi's layout interchange format (`nodachi-layout` version 1, contract in the nodachi repo at
`Assets/Myrstack/Nodachi/Docs/LAYOUT-INTERCHANGE.md`). A nodachi nature graph selects cells by `landCover`, turns them
into fields (Region To Field) and subtracts holes and keep-outs from the cover it scatters into.

- Input: the export bundle's `meta.json`, `regions.json`, `lines.json`, `pois.json` (EXPORT_FORMAT.md §3). No
  settlement file is needed, so every map has one.
- Spec target: reads `<out>/<name>/export/` (build the map first; `--out <root>` picks the build root, default `out`).
- Build-dir target (`out/<name>` or `out/<name>/export`): reads that bundle and writes beside it. Both targets give
  byte-identical output.
- `--margin <m>` (default 2): keep-out distance beyond a line's half width, a POI's radius and the town outline.

## Frame and geometry

| nodachi | mapgen |
|---|---|
| `[x, 0, z]`, metres, `meta.frame: generator-local` | export `(x, z)`: x east, z north, origin the map's south-west corner; y = 0 (terrain supplies height). The same frame as `<name>.nlayout.json` (docs/nlayout.md), so both layouts overlay exactly |
| `meta.boundaryConvention: centreline` | the same value as the town layout. Cover cells are the region outlines; keep-out cells are already widened, so their outline is the keep-out boundary |
| outer loops counter-clockwise, simple | every cell; vertices rounded to 1 mm, no repeated closing vertex |
| no holes | a polygon hole is its own `hole` cell; a keep-out area with a hole is cut into hole-free pieces |
| `edges` | not written (cells have no road frontage) |

Ids are integers in file order. Cells: land cover in `regions.json` order (each polygon, then its holes), then
keep-outs in this order: lines in `lines.json` order, lakes, the town, POIs in `pois.json` order. Splines: roads,
trails and rivers in `lines.json` order. Points: POIs in `pois.json` order.

Cover cells are the regions as painted, not clipped against each other. Where two cover regions overlap, the later
one is painted on top in the terrain; `paintOrder` gives that order.

## Cells — land cover

One cell per polygon of every region whose type is not `town` or `lake` (`field`, `forest`, `marsh`, `hills`,
`mountain`, `plateau`, `ruins`, `clearing`). The cell is the polygon's outer ring.

| attribute | type | value |
|---|---|---|
| `landCover` | string | the region type |
| `regionId` | string | the region id in `regions.json` (a scatter patch: its scatter entry id) |
| `scatter` | bool | `true` for noise-generated background patches |
| `area` | float | m², the polygon net of its holes |
| `part` | int | index of the polygon in the region's `polygons` |
| `paintOrder` | int | index of the region in `regions.json` |
| `label` | string | the region's map name; absent when it has none |
| `height` | float | authored relief in metres (`hills`, `mountain`, `plateau`); absent otherwise |
| `impassable` | bool | `true` on impassable regions; absent otherwise |

## Cells — holes

One cell per hole of a cover polygon (a clearing in a forest), right after its polygon's cell. A consumer subtracts
hole cells from the cover of `parentRegion`.

| attribute | type | value |
|---|---|---|
| `landCover` | string | `hole` |
| `parentRegion` | string | the region id of the polygon holding it |
| `parentCover` | string | that region's type |
| `area` | float | m² |
| `part`, `hole` | int | the polygon's index in the region's `polygons`, the hole's index in that polygon's `holes` |
| `paintOrder` | int | the parent region's index in `regions.json` |

## Cells — keep-out

Ground where no nature is scattered. Each area is clipped to the map, simplified within 5 cm and cut into hole-free
pieces, one cell per piece. A closed ring road therefore gives two or more cells, and the ground it encloses stays
outside them. Keep-out cells overlap each other and the cover cells; a consumer merges them and subtracts.

| `source` | from | area |
|---|---|---|
| `road` | `road` and `trail` lines | centreline buffered by `width / 2 + margin`, round caps and joins |
| `river` | `river` lines | the same, at the nominal width |
| `cliff`, `wall` | `cliff` and `wall` lines | the same |
| `water` | `lake` regions | the lake polygons, islands left open, grown by `margin` |
| `town` | all `town` regions together | their union grown by `margin` (one area for the whole town) |
| `poi` | every POI, team bases included | a disc of radius `r + margin` (32-gon) |

| attribute | type | value |
|---|---|---|
| `landCover` | string | `keepout` |
| `source` | string | the table above |
| `sourceType` | string | the line, region or POI type (`trail` for a trail under `source: road`) |
| `sourceId` | string | the line, lake or POI id; absent for `town` |
| `width` | float | line keep-outs: the line's nominal width |
| `splineId` | int | road, trail and river keep-outs: the id of the line's spline |
| `radius` | float | POI keep-outs: the POI's footprint radius `r` |
| `margin` | float | the margin used |
| `area` | float | m² of this piece |

## Splines — roads, trails, rivers

`interpolation: linear`, the line's centreline from `lines.json` (10 m steps), y = 0. For edge effects (verges,
reeds along rivers).

| attribute | type | value |
|---|---|---|
| `lineType` | string | `road`, `trail` or `river` |
| `lineId` | string | the line id in `lines.json` |
| `width` | float | nominal width in metres |

## Points — POIs

| attribute | type | value |
|---|---|---|
| `poiType` | string | the POI type (`base`, `capture`, `landmark`, ...) |
| `poiId` | string | the POI id in `pois.json` |
| `radius` | float | footprint radius `r` |
| `team` | string | owning team; absent when neutral |
| `label` | string | display name; absent when it has none |

## Checking

nodachi's offline checker (build command in docs/nlayout.md § Checking) validates the file:
`dotnet <dir>/bin/Debug/net8.0/layout-check.dll out/<name>/export/<name>.landcover.nlayout.json`.
`tests/test_export_landcover.py` runs it when `NODACHI_LAYOUT_CHECK` names that `layout-check.dll`. A map passes with
0 errors. Its warnings are `EDGE-SHORT` on authored region outlines and buffer joins, and `CURVE-TURN` /
`CURVE-SEGMENT` on the line splines (10 m resampling); they add corners only.
