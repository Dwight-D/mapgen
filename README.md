# mapgen

Turns a hand-written map spec (YAML) into a 2D map image, gameplay analysis (overlays + report) and terrain
primitives (heightmap, splatmaps, masks, JSON). The spec is the source of truth; everything in `out/` is derived and
safe to delete. Full design: `MAPGEN_SPEC.md`. Export bundle contract for engine importers: `EXPORT_FORMAT.md`.
New map concepts are authored with the `map-concept` skill (`E:/GameDev/Unity/.claude/skills/map-concept/`).

## Setup

Python 3.8 venv at `tools/mapgen/.venv` (already created; deps in `requirements.txt`). Run from `tools/mapgen/`:

```
.venv/Scripts/python -m mapgen <command> ...
```

`mapgen.ps1` / `mapgen.sh` forward their arguments to the same command and work from any directory.

## Commands

```
python -m mapgen validate maps/example_river_crossing.yaml     # report every spec error, write nothing
python -m mapgen build    maps/example_river_crossing.yaml     # full build -> out/<name>/
python -m mapgen build    maps/                                # every *.yaml in the folder
python -m mapgen build    <spec> --only render|analyze|export  # one stage (analyze also writes the report)
python -m mapgen build    <spec> --out some/dir                # alternative output root
python -m mapgen probe    maps/concepts/thornwald.yaml 1500 1560 lodge   # times, spread, cost, features at points (no files)
python -m mapgen types                                         # print the type table (types.yaml)
python -m mapgen build    <spec> --palette my_palette.yaml     # other render colours (omitted keys use palette.yaml)
python -m mapgen palette                                       # L* lightness matrix + greyscale legibility check
```

Exit code 0 on success, 1 on spec errors. Builds are deterministic: same spec + seed gives byte-identical files.
A build takes roughly 10-18 s for a 3-4 km map at default settings (under 25 s with scatter).

`probe` runs geometry, heightmap and analysis only. Per point it prints each team's travel time (`m:ss`), the
spread `(max-min)/min`, the cost cell value (and `fair` inside the fair zone), the topmost region, and the lines and
crossings there. Arguments are `x y` pairs in metres; a POI id stands for its position. It is the quick way to ask
"how long does each team need to get here?" while moving an objective.

## Edit loop

1. Edit the YAML in `maps/` (see the examples; `maps/example_natural.yaml` uses every natural-shape key; `types.yaml`
   lists the available types and costs).
2. `build`, then look at `out/<name>/map.png`, the `overlay_*.png` files and `report.md`.
3. Edit again.

## Outputs (`out/<name>/`)

| file | content |
|---|---|
| `map.png` | hillshaded map (regions outlined where the type changes, patterns, rivers, roads, crossings, POIs with labels, labelled grid) plus a side panel on the right with title, legend, team list and scale bar; nothing but map content is drawn over the map |
| `overlay_travel.png` | per-team isochrones every 30 s (labelled `<team> m:ss` every 60 s; solid / dashed / dotted per team); white hatch = contested |
| `overlay_territory.png` | cell owner (fastest team; team 2 dotted, team 3 lined, big team labels), white hatch = contested, black hatch = unreachable |
| `overlay_traffic.png` | heat of shortest paths from bases to bases and objectives (light to dark brown = more paths); numbered circles = report chokepoints |
| `overlay_openness.png` | dark blue = covered (LOS-blocking terrain nearby); white lines = base-to-base routes |
| `overlay_balance.png` | fair zone: teal hatch where every team arrives within `analysis.balance_tolerance` of the others; neutral objectives are labelled with their spread |
| `overlay_cost.png` | travel cost grid (light = cheap, dark = costly, black = impassable); for debugging the spec |
| `report.md`, `report.json` | the analysis in text (same data); every overlay carries the same side panel with its own legend |
| `export/heightmap.png` | 16-bit, north-up, value = height / `height_max` * 65535 |
| `export/heightmap.r16` | same data, raw little-endian uint16, row 0 = south (Unity/RAW convention) |
| `export/splat_0.png`, `splat_1.png` | RGBA weights for the 8 `splat_layers` (channel map in `meta.json`); sum to 255 |
| `export/masks/*.png` | 8-bit coverage per region/line/crossing type, plus `water.png`, `impassable.png` |
| `export/pois.json`, `lines.json`, `regions.json`, `crossings.json` | placement data with heights; region polygons carry their holes, lakes and rivers carry `water_surface` heights; `crossings.json` lists every crossing (explicit and automatic, `auto: true`; discs have `r`, bands have `width` and `points`); scatter regions are flagged `"scatter": true` in `regions.json` |
| `export/spec.resolved.json` | the spec with every default applied, scatter regions and automatic crossings expanded; engine importers read this instead of the YAML |
| `export/meta.json` | sizes, seed, spec hash, mapgen version, `terrain_layers` (`{name, channel, image}` per splat layer), `water_level` (`null`: lakes and rivers have per-feature surfaces) |

## Natural shapes, scatter, palette

New optional keys (full definitions in `MAPGEN_SPEC.md` §4.2, §4.3, §4.6, §5.1, §6.2):

| key | where | effect |
|---|---|---|
| `warp`, `warp_scale` | region | metres; 2D domain warp of the outline (lobes and bays). Defaults: blob `0.3*r` / `r`, band `0.25*width` / `width`, poly `0` |
| `fragment`, `fragment_scale` | region | `fragment` 0-1 = fraction of area removed as clearings and ragged edges; `fragment_scale` = patch size in metres (150) |
| `width_var` | line | 0-1; width varies along the line (`width * (1 + width_var * 0.6 * noise)`) |
| `scatter` | top level | list of `{type, coverage, scale, clump, avoid, within}`: noise patches of one region type across the map, clear of lines, crossings, POIs, lakes, mountains and towns by `avoid` metres; painted under the explicit regions |
| `terrain.detail`, `terrain.detail_scale` | terrain | metres; mid-frequency ground undulation (6 m / 250 m) |

Mountains get a foothill apron; `band` mountains default to `rough: 0.35`; overlapping mountains combine by maximum,
not sum.

## Authoring keys

| key | where | effect |
|---|---|---|
| `meander`, `meander_scale` | line | `meander` = sideways wiggle amplitude (m); `meander_scale` = wavelength (m, default 300). Use a coarse path plus these instead of hand-zigzagged control points |
| `join: <line id>`, `join_end: start\|end` | line | tributary: that end of the line is moved onto the nearest point of the parent's centreline (parents are built first; cycles are errors) |
| (automatic) | river x lake | rivers are clipped outside lakes, so a river ending inside a lake leaves no stub; lakes and rivers render as one water body |
| `auto_crossings` | top level | default `true`: every road x river gets a `bridge`, every trail x river a `ford` (disc, `r` = river half-width + 15 m, id `auto_<n>`), and roads/trails that enter and leave a lake get a ford band. An explicit crossing over an intersection is kept (write one to ford a road). `false` turns it off and analysis warns about uncovered intersections |
| `path` + `width` | crossing | band crossing instead of `at` + `r`: the buffered path. For passes through a long mountain or long fords |
| `ramp` | crossing type | cost 1.2; relaxes the terrain inside it (blur sigma = `r`) so a plateau edge or cliff becomes a walkable slope. Disc or band |
| `edge` | region (`plateau`, `town`) | blend radius (m) of the flatten step, default 40 / 30; larger = gentler rim |
| `clearing` | region type | light-green glade (cost 1.0, dotted); paint it after forests to cut a glade. Use `field` for real farmland |
| `on: [types]` | scatter | the scatter may also cover explicit regions of those types (woods on `hills`, fields on `plateau`) and is painted after all explicit regions; it keeps clear of every other explicit region |
| `label_at: [x, y]` | region, line | pins the label there; other labels are placed automatically around glyphs, other labels and the map edge |

Scatter clearance (`avoid`) is irregular, never a circle. Fragmentation (`fragment`) always keeps the largest patch and
warns when it removes much more than asked. Trails flatten the terrain half as strongly as roads.

Render colours live in `mapgen/palette.yaml`, not in `types.yaml`. Categories differ by lightness and pattern first
(forest stipple, marsh reeds, town grid, ruins crosshatch), so the map reads with red-green colour blindness; teams
default to blue, amber, purple and also carry their letter. `python -m mapgen palette` checks the greyscale rule.

## Reading the report

- **Warnings**: spec problems found by geometry and analysis. Fix these first. Typical: a POI on impassable ground, a
  base that cannot reach another base, a fragmented region that lost most of its area, and (only with
  `auto_crossings: false`) a road crossing water with no bridge/ford.
- **Teams**: base-to-base travel time (m:ss) and the share of the map each team reaches first. Large asymmetry in
  shares or base distances means an unfair layout. Contested = cells where the best two teams are within
  max(20 s, 5 %).
- **Objectives**: time from each team base to each objective. Neutral objectives show `spread = (max-min)/min` and
  are flagged `IMBALANCED` above `analysis.balance_tolerance`, `UNREACHABLE` if any team cannot get there. Owned
  objectives are flagged `EXPOSED` if an enemy gets there before the owner.
- **Fair zone**: share of the map every team reaches within the balance tolerance of the others (`overlay_balance.png`).
  A neutral objective belongs inside it.
- **Chokepoints**: the 5 best spots by score = number of shortest paths x constriction (fraction of cells within
  120 m that are impassable or cost >= 2). Cells near bases are ignored. Open ground with no constriction yields "open
  ground - no chokepoint". The nearest named feature is listed. Numbers match the circles in `overlay_traffic.png`.
- **Automatic crossings**: the bridges and fords `auto_crossings` added (id, type, position, what they cross).
- **Openness**: map-wide mean, each team's territory mean, and the mean along each base-to-base route. 1.0 = open
  ground, 0.0 = fully covered.

## Coordinate conventions

- Units are metres. Origin is the south-west corner; `x` grows east, `y` grows north. The map spans
  `[0, size_x] x [0, size_y]`. The grid labels on `map.png` are these coordinates.
- Images are north-up (row 0 = north). `heightmap.r16` is the exception: row 0 = south.
- Exported JSON uses `x` (east), `z` (north = spec `y`) and `y` = height in metres.
- Points are `[x, y]`; paths are lists of points.
