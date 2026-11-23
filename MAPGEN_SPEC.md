# mapgen — map spec to 2D map, gameplay analysis and terrain primitives

`mapgen` is an engine-agnostic command-line tool that turns a hand-written **map spec** (YAML) into:

1. a readable **2D map image** (regions, rivers, roads, points of interest, grid, hillshade, plus a side panel with
   title, legend, team list and scale bar) whose categories stay distinguishable for red-green colourblind viewers
   (§5.1),
2. **gameplay analysis** — per-team travel-time fields, territory, objective balance, fair zone,
   traffic/chokepoints, openness — as overlay images plus a machine-readable report,
3. **terrain primitives** — 16-bit heightmap, splatmaps, per-feature masks, POI and polyline JSON — that any
   level editor or engine can import.

The map spec is the single source of truth. Images and exports are derived and disposable. The intended loop is:
an agent (or a human) edits the YAML, runs `build`, looks at `map.png`, the overlays and `report.md`, and edits
again. Output must be deterministic: the same spec and seed produce byte-identical files.

It serves every game project under `E:\GameDev\Unity\` and has no Unity dependency.

---

## 1. Environment

- Location: `E:\GameDev\Unity\tools\mapgen\`.
- Interpreter: the venv at `tools/mapgen/.venv` (Python 3.8). Installed and verified:
  `numpy 1.24.4`, `scipy 1.10.1`, `shapely 2.0.7`, `Pillow 10.4.0`, `scikit-image 0.21.0`, `pyyaml`.
- `requirements.txt` pins every runtime package, transitive ones included, to an exact `==` version;
  `requirements-dev.txt` adds exact pins of pytest and its dependencies.
- Code must run on Python 3.8: no `match`, no `X | Y` type unions, no `list[int]` at runtime (use
  `from __future__ import annotations` or `typing.List`), no `str.removeprefix`.
- Invocation (from `tools/mapgen/`): `.venv/Scripts/python -m mapgen <command> ...`.
  `mapgen.ps1` and `mapgen.sh` wrappers forward all arguments to that command with the working directory set to
  `tools/mapgen/`, so they run from anywhere.

Verified library behaviour (relied on below):

- `skimage.graph.MCP_Geometric(costs, fully_connected=True)`: `find_costs([(row, col), ...])` returns
  `(cumulative, traceback)`; cells with cost `np.inf` or negative are impassable and their cumulative cost is `inf`;
  `traceback((row, col))` returns the path as a list of `(row, col)`. Cumulative cost is cost × step length in cells
  (diagonal steps × √2).
- `Image.fromarray(uint16_array, mode="I;16").save("x.png")` writes a 16-bit grayscale PNG that round-trips.
- `ImageFont.truetype("arial.ttf", size)` works on this machine; fall back to `ImageFont.load_default(size=size)`.

## 2. Package layout

```
tools/mapgen/
  MAPGEN_SPEC.md          this file
  README.md               short usage guide (commands, where outputs land, how to read the report)
  requirements.txt
  mapgen.ps1, mapgen.sh   wrappers
  .venv/                  local interpreter (not hand-edited)
  mapgen/
    __init__.py
    __main__.py           CLI (argparse): build, validate, probe, diff, spec, spec-edit, flowgraph, town, types, palette
    types.yaml            terrain/line/crossing/POI type table (data, editable; gameplay and splat data, no colours)
    palette.yaml          render colours, region patterns, default team colours (data, editable)
    palette_check.py      CIE L* legibility check of a palette (`palette` command)
    schema.py             load YAML, apply defaults, validate, return a MapSpec dataclass tree; load the palette
    specpatch.py          span-patch engine behind `spec set | add | delete | rename` and `spec-edit`: comment-preserving, verified text edits of a spec (§7)
    spec_model.py         editor view of a spec text: handles (id-addressed paths), referrers, resolved geometry JSON
    spec_editor.py        `spec-edit` server: text buffer, undo/redo, validation, atomic save, preview image (§7)
    spec_editor_page.py   the `spec-edit` page (vanilla JS, inline SVG over the preview image)
    editor_http.py        loopback server guards shared by the flowgraph and spec editors (token, Host/Origin, CSP, body cap)
    noise.py              deterministic value-noise fbm (2D, 2D on grid axes, 1D, periodic 1D)
    geometry.py           spec shapes -> shapely geometries (blob, poly, band, path smoothing, roughness, meander,
                          domain warp, fragmentation, variable-width lines, tributary joins, water union,
                          band crossings, automatic crossings, scatter)
    raster.py             Grid helper (world<->pixel), geometry -> masks, type-id grid, soft masks
    height.py             heightmap synthesis
    analysis.py           cost grid, travel times, territory, balance, fair zone, traffic, chokepoints, openness,
                          point probe
    render.py             map.png and overlay images (map area plus side panel)
    export.py             heightmap, splatmaps, masks, JSON exports, resolved spec, meta
    build.py              orchestrates the pipeline for one spec; writes the report
    town/                 settlement layout generators (districts, roads, wall, gates) that emit spec fragments: `mapgen/TOWN_SPEC.md`
  maps/                   map specs (*.yaml); examples live here
  out/<map-name>/         generated output (safe to delete)
```

## 3. Coordinates and grids

- World units are **metres**. Origin is the **south-west corner**. `x` grows east, `y` grows north. The map spans
  `[0, size_x] × [0, size_y]`.
- Exports for 3D engines name the horizontal axes `x` (east) and `z` (north); world `y` in the spec becomes `z` in
  exported JSON, and exported `y` is height in metres.
- `raster.Grid(size_x, size_y, cols, rows)` maps world to pixel. Row 0 is the **north** edge (image convention).
  - `col = x / size_x * cols`, `row = (size_y - y) / size_y * rows` (continuous pixel coords; cell centres at +0.5).
  - `to_pixel(x, y)`, `to_world(col, row)`, `cell_size_x`, `cell_size_y`, and `transform_geometry(geom)` returning
    the geometry in pixel space (shapely `affinity.affine_transform`).
- Every raster stage builds its own `Grid` at its resolution (render, analysis, heightmap, splat); all derive from the
  same shapely geometries, so they agree.

## 4. The map spec (YAML)

Example (the canonical example, saved as `maps/example_river_crossing.yaml`):

```yaml
format_version: 1                # spec format version; this mapgen reads 1
name: river_crossing            # output folder name; [a-z0-9_]+
brief: |                        # free text: concept, intended flow, what to test. Not parsed.
  Two teams on either side of a north-south river. One bridge in the centre held by a keep site,
  a ford in the south, a mountain pass in the north. Forests flank the bridge approaches.
size: [3000, 3000]              # metres [x, y]
seed: 42

terrain:                        # all optional; defaults shown
  height_max: 400               # metres represented by heightmap value 65535
  base_height: 40               # metres, mean ground level
  relief: 25                    # metres, amplitude of low-frequency base noise
  relief_scale: 1200            # metres, wavelength of base noise
  water_depth: 4                # water beds are carved 0.75 x this below the water surface
  detail: 6                     # metres, amplitude of mid-frequency ground undulation
  detail_scale: 250             # metres, wavelength of that undulation

teams:                          # 2 or 3 teams; ids are short strings
  A: { base: [300, 1500], color: "#2f6fdf", name: West }     # color optional: default from palette `teams`
  B: { base: [2700, 1500], color: "#f2a81d", name: East }

regions:                        # painted in list order; later entries win where they overlap
  - { id: region_0, seed: 0, type: forest,   shape: blob, at: [1150, 1750], r: 260, rough: 0.45 }
  - { id: region_1, seed: 1, type: forest,   shape: blob, at: [1850, 1250], r: 260, rough: 0.45 }
  - { id: region_2, seed: 2, type: mountain, shape: band, path: [[1250, 3000], [1500, 2500], [1750, 2050]], width: 450, height: 220 }
  - { id: region_3, seed: 3, type: town,     shape: poly, points: [[350, 1350], [600, 1350], [600, 1650], [350, 1650]], label: Westhold }
  - { id: region_4, seed: 4, type: marsh,    shape: blob, at: [1500, 650], r: 300, rough: 0.6 }

lines:
  - { id: line_0, seed: 0, type: river, path: [[1500, 0], [1480, 700], [1530, 1500], [1500, 2200], [1500, 3000]], width: 35, meander: 40 }
  - { id: line_1, seed: 1, type: road,  path: [[300, 1500], [900, 1500], [1500, 1500], [2100, 1500], [2700, 1500]], width: 10 }
  - { id: line_2, seed: 2, type: trail, path: [[300, 1500], [900, 2300], [1500, 2350], [2100, 2300], [2700, 1500]], width: 5 }

crossings:
  - { id: crossing_0, type: bridge, at: [1530, 1500] }
  - { id: crossing_1, type: ford,   at: [1480, 700], r: 45 }
  - { id: crossing_2, type: pass,   at: [1500, 2350], r: 330 }

pois:
  - { id: bridge_keep, type: keep_site,    at: [1600, 1550], label: Bridge Keep }
  - { id: camp_ne,     type: neutral_camp, at: [2200, 2600], tier: 2 }
  - { id: camp_sw,     type: neutral_camp, at: [800, 400],   tier: 2 }
  - { id: relic,       type: treasure,     at: [1500, 2550] }
  - { id: cap_s,       type: capture,      at: [1500, 900] }

analysis:                       # all optional; defaults shown
  cell: 10                      # metres per analysis cell
  speed: 5.0                    # m/s on cost-1.0 ground (infantry run)
  balance_tolerance: 0.15       # relative time difference that flags a neutral objective as imbalanced

output:                         # all optional; defaults shown
  render_px: 1536               # longest side of map.png and overlays
  heightmap_res: 1025           # square, 2^n+1
  splat_res: 1024               # square
  grid_step: 500                # metres between labelled grid lines on the render
  splat_blend: false            # soft splat edges + grass/dirt noise (§6.5): false/absent = off, true = the
                                # defaults below, or a mapping overriding any of them:
                                # {edge: 3, road_edge: 4, noise_scale: 24, patches: 0.6, patch_reach: 45,
                                #  tufts: 0.5, seed: <derived from the map seed>}
```

The natural-shape keys (`warp`, `fragment`, `width_var`, `scatter`) are shown in `maps/example_natural.yaml`; the
authoring keys (`meander_scale`, `join`, band crossings, `ramp`, `edge`, `clearing`, scatter `on`, `label_at`,
`auto_crossings`) are used by the concept specs in `maps/concepts/`. Specs are read as YAML in which only
`true`/`false` are booleans, so a key named `on` stays a key.

### 4.1 Top-level keys

| key | required | meaning |
|---|---|---|
| `format_version` | yes | integer; the spec format version (§4.8). This mapgen reads 1 |
| `name` | yes | output folder name, `[a-z0-9_]+` |
| `brief` | no | free text, copied into the report |
| `size` | yes | `[x, y]` metres, each 200–20000 |
| `seed` | no (0) | integer; drives all noise |
| `terrain` | no | height synthesis parameters (§4 example shows all keys and defaults; `detail` and `detail_scale` shape §6.2 step 1) |
| `teams` | yes | 2 or 3 entries: `base` (point, required), `color` (hex, default: the palette `teams` entry at the team's position, §5.1), `name` (default the id) |
| `points` | no | mapping of name to point: named positions shared by several features (§4.9) |
| `regions`, `lines`, `crossings`, `pois`, `scatter` | no | lists, defined below |
| `auto_crossings` | no (`true`) | `true`/`false`: generate bridges and fords where roads and trails cross water (§4.7) |
| `analysis`, `output` | no | parameters (§4 example shows all keys and defaults) |

A point is `[x, y]` in metres; in the places listed in §4.9 it may also be a point reference. A path is a list of ≥ 2
points.

### 4.2 Regions

Every region has `id` (§4.8), `type` (a region type from `types.yaml`, §5) and `shape`, plus optional `seed` (§4.8),
`label` (drawn on the map), `label_at: [x, y]` (pins the label there), type-specific `height` (metres; used by `mountain`, `hills`,
`plateau`; default from `types.yaml`), `edge` (metres; only for the flattening types `plateau` and `town`, default
from `types.yaml`: 40 and 30) and the natural-shape keys `warp`, `warp_scale`, `fragment`, `fragment_scale` (below).
`edge` is the blend radius of the region's flatten step (§6.2): a larger `edge` turns the region's rim into a
walkable slope instead of a cliff.

`impassable` (optional boolean, default `false`; any other value is a validation error; only regions take it) makes
the region a wall: its cells get cost infinity in the movement cost grid (§6.3) whatever the region type, while line
of sight and heights follow the type (`los` and `height_mode` of `types.yaml`). Dense forest, rubble, hills or town
blocks can serve as walls this way. Render: the region keeps its type colour and outline and is overdrawn with the
strike-through hatch (§6.4, palette `impassable_pattern`); the legend lists "impassable". Export: `regions.json` and
`spec.resolved.json` carry `"impassable": true` on flagged regions only (the key is absent otherwise), and
`masks/impassable.png` includes the region's cells. A crossing still overrides passability (§6.3).

| shape | keys | geometry |
|---|---|---|
| `blob` | `at`, `r`, `rough` (0–1, default 0.3), `squash` (default 1.0), `angle` (deg, default 0) | circle of radius `r`; boundary radius at angle θ is `r · (1 + rough · 0.5 · n(θ))` with `n` 1D periodic fbm in [-1, 1] seeded by the region's feature seed (§4.8); then scaled by `squash` along local y and rotated by `angle`; 96 vertices |
| `poly` | `points` (≥ 3), `rough` (default 0) | polygon; if `rough > 0`, edges are densified to ≤ 20 m segments and each vertex displaced along its normal by `rough · 0.15 · sqrt(area) · n` (n from 1D fbm along the perimeter) |
| `band` | `path`, `width`, `rough` (default 0.2; 0.35 for type `mountain`) | path smoothed (Chaikin, 3 iterations), buffered by `width/2` with round caps; boundary roughened like `poly` |

A region's outline is built in this order: base outline (shape table, including `rough`) → `warp` → clip to the map
rectangle and make valid (`shapely.make_valid`, keep polygonal parts; parts smaller than 1 % of the largest are
dropped) → `fragment`. Noise seeds come from the region's feature seed (§4.8).

**Organic outlines: `warp`, `warp_scale`.** `warp` is a displacement amplitude in metres (default: blob `0.3·r`,
poly `0`, band `0.25·width`); `warp_scale` is the noise wavelength in metres (default: blob `r`, poly
`0.5·sqrt(area)`, band `width`). The outline is densified to ≤ 10 m segments and every vertex is moved by
`warp · (fbm2(x/warp_scale, y/warp_scale, s1), fbm2(x/warp_scale, y/warp_scale, s2))` (3 octaves), a 2D domain warp
with two independent seeds derived from `seed` and the region's feature seed. It bends the outline into lobes and bays.
`warp: 0` keeps the base outline.

**Fragmented interiors: `fragment`, `fragment_scale`.** `fragment` (0–1, default 0) is the approximate fraction of
the region's area removed; `fragment_scale` (metres, default 150) is the patch size. When `fragment > 0` the region
is broken into patches with clearings and ragged edges:

1. Rasterize the region on a local 5 m grid covering its bounds plus one `fragment_scale` margin.
2. Field `n = fbm2(x/fragment_scale, y/fragment_scale, seed', octaves=4)`, biased toward the edge:
   `n -= 0.6 · (1 − smoothstep(0, fragment_scale, d_inside))` where `d_inside` is the distance to the region boundary.
3. `q` is the `fragment` quantile of `n` over the region's cells; cells with `n ≥ q` are kept.
4. Vectorize: `skimage.measure.find_contours` of the continuous field `n − q` at level 0 (smooth edges), rings
   classified as shells or holes by containment parity, simplified 3 m, intersected with the region geometry.
   Shells smaller than 2 500 m² and holes smaller than 500 m² are dropped. The result replaces the region geometry.

**Fragment safety.** Fragmentation always keeps the largest patch: when every patch of the field is below the size
limits, the largest patch of the field is kept (the whole outline if the field is empty). When fragmentation removes
more than `fragment + 0.25` of the region's area, the build adds a warning naming the region (its list index, type
and label).

All geometries are clipped to the map rectangle and made valid.

### 4.3 Lines

Every line has `id` (required, §4.8), `type` (a line type), `path`, `width` (metres; default from `types.yaml`),
optional `seed` (§4.8), `meander` (metres, default 0), `meander_scale` (metres, default 300), `width_var` (0–1,
default 0), `label`,
`label_at: [x, y]` (pins the label), `join` and `join_end` (below), and the road shaping keys `wobble`, `wobble_scale`
and `smooth` (below).

- The path is smoothed with Chaikin (3 iterations). If `meander > 0`, it is densified to 10 m steps and each point
  displaced along the path normal by `meander · n(s)` (1D fbm over arc length `s`, wavelength ≈ `meander_scale`),
  with displacement tapered to 0 over the first and last 5 % so endpoints stay put.
- **Road shaping.** `wobble` (metres, default 0), `wobble_scale` (metres, default 200) and `smooth` (bool, default
  false) are for roads and trails. With `smooth: true` or `wobble > 0` the centreline is a centripetal Catmull-Rom
  spline through every waypoint, sampled about every 10 m (otherwise Chaikin, which cuts inside each waypoint). With
  `wobble > 0` each sample is also displaced along the path normal by `wobble · n(s)` (1D fbm over arc length,
  octaves 2, wavelength ≈ `wobble_scale`, seed `S + 9500 + K_l`), multiplied by a smoothstep weight that is 0 at the
  first and last point and at every path entry written as a point reference (POI id, `base_<team>`, `points` name)
  and reaches 1 at `wobble_scale / 2` metres from the nearest such point. Those points therefore stay exactly on the
  centreline, so flowgraph edge derivation (which reads the waypoints, not the centreline) and node discs are
  unaffected. `wobble` and `meander` may be combined; `meander` applies afterwards. Echoed into `spec.json` only
  when set. Natural values: 800 m map `wobble` 15-25 / `wobble_scale` 150-200; 3000 m map `wobble` 40-70 /
  `wobble_scale` 400-500; use 3-6 waypoints per road. `meander` displaces waypoints (references are not held) and
  tapers the first/last 5 %, which makes visible hooks at short roads; use it for rivers.
- **Tributaries.** `join: <line id>` makes the line flow into that line; `join_end: start|end` (default `end`) picks
  which endpoint. Lines are built parents first; the chosen endpoint is moved to the nearest point on the parent's
  final centreline before smoothing, and the meander taper keeps it fixed there. A `join` that names no line, a
  line that joins itself, a non-unique parent id or a cycle is a validation error.
- The resulting centreline is kept (exported to `lines.json`). With `width_var = 0` the area is the centreline
  buffered by `width/2`.
- With `width_var > 0` the width varies along the line: `width · max(0.4, 1 + width_var · 0.6 · fbm1(s / 250))`
  (`s` = arc length in metres). The area is the union of the centreline's 10 m segments, each buffered (round caps)
  by half the local width.
- **Water union.** The area of every water line (type with `height_mode: water`, i.e. rivers) is clipped to lie
  outside every lake region, so a line whose end runs into a lake leaves no stub inside it. Each end of the
  centreline that lies inside a lake ends at its **mouth**: walking from the shore crossing into the lake in 0.25 m
  steps, the first point where the end cap lies at least 0.5 m inside the lake, the cap being the centre point
  and the two corners at the line's widest half width (`width/2 · (1 + 0.6 · width_var)`) to either side across
  the local direction of the centreline. A strip of that half width along the centreline (and its `lines.json`
  points) therefore meets the lake water with no gap and overlaps it by about 0.5 m, however obliquely the line
  meets the shore. The walk stops at the line's own end or 30 m past the shore, keeping the deepest cap found
  when none qualifies. Per such end the line also keeps its **mouth channel**: the untrimmed centreline from the
  shore crossing up to 10 m into the lake, which carries the river bed into the lake bed (§6.2 step 9). A stretch
  inside a lake between two dry stretches stays in the centreline.

### 4.4 Crossings

`type` (a crossing type), `id` (required, §4.8) and one of two geometries:

- a **disc**: `at` and optional `r` (radius, metres; default from `types.yaml`);
- a **band**: `path` and `width` (metres): the Chaikin-smoothed path buffered by `width / 2` with round caps. Use it
  where a crossing must span a mountain band or follow a long causeway. A band's `r` is `width / 2`; its `at` is the
  middle of its path.

A crossing **overrides passability**: inside it the cost is the crossing's cost even where the region or line
beneath is impassable, and slope never makes a crossing cell impassable (§6.3). `bridge` and `ford` make rivers
crossable; `pass` makes a mountain crossable and lowers its height (§6.2); `ramp` relaxes the terrain so that a
plateau edge or cliff becomes a walkable slope (§6.2). Automatic crossings (§4.7)
have ids `<line id>-<water id>-<n>` (§4.8). `gate` is a small disc (cost 0.6, `r` 12) placed where a road passes through a `wall`
line: the wall is impassable (cost inf, blocks line of sight) and the gate crossing overrides it. The `town` generators
(`mapgen/TOWN_SPEC.md`) emit walls and gates.

### 4.5 POIs

`id` (required; §4.8), `type` (a POI type), `at`, optional `label`, `team` (a team id; the POI belongs to
that team), `tier` (int), `r` (metres, footprint radius for downstream tools; default from `types.yaml`).

Team bases are implicit POIs with id `base_<team>` and type `base`.

### 4.6 Scatter

Top-level `scatter:` list. Each entry generates many irregular patches of one region type across the map, so the
background is not empty plain:

```yaml
scatter:
  - { id: wild_forest, type: forest, coverage: 0.18, scale: 350, clump: 0.6, avoid: 30 }
  - { id: wild_field,  type: field,  coverage: 0.10, scale: 250, clump: 0.3, avoid: 20, within: [[0,0],[1500,0],[1500,3000],[0,3000]] }
```

| key | meaning |
|---|---|
| `id` | required; §4.8 |
| `seed` | optional; §4.8 |
| `type` | region type (required) |
| `coverage` | 0–1 (required): share of the entry's area (the map, cut to `within`) where the noise field lies above the threshold; eligible ground is covered at about this share where it spans many patch widths (`scale`); an entry whose eligible ground is a sliver can yield no patches |
| `scale` | metres, typical patch size (default 300) |
| `clump` | 0–1 (default 0.3): 0 = many small patches, 1 = few large merged ones |
| `avoid` | metres of clearance from every line, crossing, base and POI footprint (default 25) |
| `within` | optional polygon (≥ 3 points) restricting where the scatter may appear |
| `impassable` | optional boolean, default `false` (other values are errors): the entry's patches are impassable exactly as a region with `impassable: true` (§4.1): cost infinity, strike-through hatch, `"impassable": true` on its `regions.json` record, in `spec.resolved.json` on the region and in `scatter_params` (absent otherwise), cells in `masks/impassable.png`. Because a wall of cells must never close a road, an impassable entry keeps at least `2 · analysis.cell` metres (default 20) of clearance from every line area, crossing and POI/base footprint: its effective `avoid` is `max(avoid, 2 · analysis.cell)`. Line areas already span the half-width of the road, so the clearance is measured from the road's edge |
| `on` | optional list of region types: the scatter may also appear on explicit regions of those types (below) |

Eligible area: inside the map and `within`, outside every `water`, `ridge` and `flatten` region (lakes, mountains,
towns), and outside the clearance of every line area, crossing and POI/base footprint. The clearance is irregular:
it is the union of the plain `avoid` buffer (the guaranteed minimum) and a wider buffer (radius `1.5 · avoid`)
whose outline is domain-warped (amplitude `0.5 · avoid`, scale `3 · avoid`, the warp of §4.2), so the edge around a
POI or road is never a circle or a parallel offset. An entry with `on` additionally excludes every explicit region
whose type is not listed in `on`, so it covers open ground and the listed region types (e.g. woods on `hills`,
fields on `plateau`) and keeps clear of everything else (clearings, forests, ruins...). The noise field is
`fbm2(x/scale, y/scale, octaves=4) · (1 − 0.4·clump) + 1.2 · clump · fbm2(x/(4·scale), octaves=2)`, seeded by the
entry's feature seed (§4.8), sampled on a 5 m
grid (10 m when the map is larger than 4 km) and thresholded at the `1 − coverage` quantile of its values over the
entry's area: every grid cell inside the map and inside `within` (a cell is inside when at least half of it is
covered). The threshold depends on the field and that area only, never on what other features block; patches are the
eligible cells above it.
Patches are vectorized as in §4.2 fragmentation (continuous-field contours, simplified 4 m), then the blocked area
is subtracted exactly so the clearance holds; parts smaller than 1 500 m² and holes smaller than 300 m² are dropped.

**Locality.** An edit to another feature changes an entry's patches only where the edit changes the entry's blocked
area (for a line, crossing, POI or base: its footprint before and after the edit, widened by `2.25 · avoid`; for a
blocking region: its outline before and after the edit, plus the line areas and automatic crossings the edit changes, e.g. a
lake clipping a river; for an entry with `on`, every region type not listed in `on` blocks) and in the patches that come within 20 m of that area, which
may change along their whole outline or vanish below 1 500 m². Every other patch keeps its exact coordinates. Scatter
entries never affect each other.

Each entry becomes one `Region` (shape `scatter`, the entry's `id`, multi-part geometry). Entries without `on` are
inserted **before** all explicit regions, so explicit regions paint over them; entries with `on` are inserted
**after** all explicit regions, so they paint over the regions they are allowed on. Scatter regions appear in
`regions.json` (flagged `"scatter": true`), masks, splat and analysis exactly like explicit regions. The render does
not label them; the legend lists their type.

### 4.7 Automatic crossings

With `auto_crossings: true` (the default), after the geometry of regions, lines and explicit crossings exists, every
intersection of a road or trail with a river (any line whose type has a `crossing` entry in `types.yaml` and a finite
cost) that no crossing covers gets a crossing of that entry's type (`road` → `bridge`, `trail` → `ford`): a disc
centred on the intersection of the two centrelines with `r = river half-width + 15 m` (widened in 4 m steps for
oblique intersections until it covers the overlap of the two line areas). A crossing covers an intersection when it
leaves at most 2 % of the overlap of the two line areas uncovered. Where such a line
enters and leaves a lake, a `ford` band follows the line's centreline inside the lake, extended 18 m past the shore
on each side, `width = max(30 m, line width + 20 m)`. Automatic crossings are appended after the explicit crossings
in generation order and take the ids of §4.8, take part in scatter clearance, analysis, render and export exactly like explicit
ones (the export flags them `"auto": true`), and are listed in the report. Lines are visited in spec order and
rivers in spec order, so the result is deterministic. A spec that wants a road to ford a river writes that `ford`
explicitly; an explicit crossing is never replaced. With `auto_crossings: false` nothing is generated and analysis
warns about uncovered road/trail × water intersections (§6.3).

### 4.8 Feature identity

**Format version.** `format_version` is a required top-level integer; this format is version `1`. mapgen reads the
versions listed in `mapgen.schema.SPEC_FORMAT_VERSIONS` (`(1,)`); `python -m mapgen --version` prints them. A spec
without the key is invalid and validation continues, so the output lists every other problem too. A spec whose
`format_version` is present but not an integer this mapgen reads (another integer, a string, a float such as `1.0`, a boolean, null) is invalid and that is the
only error reported, because a spec of another format version follows other rules. `spec.resolved.json` echoes
`format_version` as its first key; `meta.json` does not carry it. The format version changes when a spec that is valid
under the current version would become invalid or build differently; an addition after which every valid spec stays
valid and builds identically keeps the version.

**Ids.** The `id` is the feature's identity: its noise seeds derive from it, it is the id under which the feature is
exported, and automatic crossing ids are built from it. List order of `regions` is paint order; it has no effect on any
feature's shape.

| section | `id` | `seed` |
|---|---|---|
| `regions[]` | required | optional |
| `lines[]` | required | optional |
| `scatter[]` | required | optional |
| `crossings[]` | required | not allowed |
| `pois[]` | required | not allowed |
| team bases | implicit `base_<team id>` | — |

- An id is a string matching `[a-z][a-z0-9_]*` of at most 64 characters. A YAML number is not an id.
- **One namespace.** The ids of regions, lines, scatter entries, crossings and POIs, the names of `points`
  entries, the implicit base ids and every automatic crossing id are distinct within a spec, so a reference
  anywhere in a spec names exactly one thing. The id
  `base_<team id>` is reserved for the base of that team. `join` names a line id.
- Renaming an id changes the id-derived seed and therefore the feature's shape, unless the feature carries an explicit
  `seed`.

**Feature seed.** `seed` on a region, line or scatter entry is an integer `K` with `0 <= K <= 4294967295` (YAML
booleans and floats are rejected) and takes precedence over the id-derived value. Without `seed`, `K` is
`feature_seed(id)`, the first 4 bytes of the SHA-256 of the UTF-8 id read as a big-endian unsigned integer
(`feature_seed("mossbeck") == 1892764035`). The hash is platform- and process-independent; Python's `hash()` is not
used. `K` is the per-feature term of every noise seed of that feature:

`S` = spec `seed`; `o` = fbm octave index (each octave adds `1013·o`); `key` = `K` of a region, or `1000 + K` of the
scatter entry for a region generated from a scatter entry; `K_l` = `K` of a line; `K_s` = `K` of a scatter entry.

| use | lattice seed | sampled over |
|---|---|---|
| base relief | `S + 1013·o` | world x, y |
| terrain detail | `S + 500 + 1013·o` | world x, y |
| hills height | `S + 1000 + key + 1013·o` | world x, y |
| mountain ridges | `S + 2000 + key + 1013·o` | world x, y |
| blob, poly and band outline roughness | `S + key + 1013·o` | angle (blob), perimeter fraction (poly, band) |
| region warp, x / y | `S + 3000 + 2·key + 1013·o` / `... + 7919` | world x, y |
| region fragmentation | `S + 6000 + key + 1013·o` | world x, y |
| line meander | `S + K_l + 1013·o` | arc length |
| line width variation | `S + 5000 + K_l + 1013·o` | arc length |
| scatter field | `S + 8000 + 97·K_s + 1013·o` | world x, y |
| scatter clump field | `S + 8000 + 97·K_s + 31 + 1013·o` | world x, y |
| scatter clearance warp, x / y | `S + 8000 + 97·K_s + 53 + 1013·o` / `... + 7919` | world x, y |
| stipple pattern (render only) | `S + 4000`, `+1`, `+2` (no octaves) | image pixels |

**Automatic crossing ids.** After generation (§4.7) each automatic crossing gets the id `<line id>-<water id>-<n>`:
the line `L` that makes the crossing, the water feature `W` it crosses, and `n` counting `L`'s automatic crossings of
`W` in order along `L`, from 1 (ordered by the crossing's distance along the line's final centreline). `W` is the id
of the river line for a river crossing, and the id of the lake region with the longest intersection with the ford's
centreline piece for a lake ford (ties: the lexicographically smallest id). Examples: `kings_road-mossbeck-1`,
`zigzag-west_lake-1`. An automatic crossing's id depends only on its line, its water feature and its order along the
line: adding a road elsewhere, crossing another river or moving a feature in its list leaves it unchanged. Where two
lines would generate overlapping automatic crossings at the same water feature, the line listed first gets the
crossing and the later one is suppressed, so list order of lines decides which of the two crossings exists. The
hyphen cannot occur in a spec id, so an automatic id never equals a spec id.

**Validation messages** start with the key path: `regions[3].id: missing required key`, `regions[3].id: must match
[a-z][a-z0-9_]* and be at most 64 characters (got 'Big-Wood')`, `lines[0].id: duplicate id 'wood' (first used at
regions[0].id)`, `regions[0].id: 'base_a' is reserved for the implicit base of team a`, `regions[3].seed: expected an
integer in [0, 4294967295], got -1`, `format_version: missing required key (this mapgen reads format versions: 1)`.
The duplicate check visits bases, `points`, `regions`, `lines`, `scatter`, `crossings`, `pois` in that order, items in
list order; the first holder of an id is the one named.

### 4.9 Point references

A point reference is a name written where a point is expected. It names a POI (`id`), a spawn base
(`base_<team>`) or an entry of the top-level `points` mapping, and resolves to that position when the spec loads, to
exactly the coordinates of the target's literal. Moving a POI, a base or a `points` entry moves every feature that
refers to it.

Accepted in: region `at` (blob), `points` (poly) and `path` (band); line `path`; crossing `at` and `path`. A path may
mix references and literals. Not accepted in: team `base`, POI `at`, `points` values (the targets are literal; there
are no chains), `label_at`, scatter `within`.

`points` maps names to literal points, written after `teams`:

```yaml
points:
  lea_crossroads: [2366, 1906]
lines:
  - {id: b_track, type: road, path: [base_B, [2150, 2300], lea_crossroads]}
```

A name is an id (§4.8) and shares the one namespace. Use `points` for a position shared by several features that is
neither a POI nor a base, such as a road junction. An entry nobody references is valid.

A reference is a YAML string; quote it only when YAML would read it as another type (`null`, `true`, `false`).
`spec.resolved.json` contains the resolved coordinates; `points` is not exported.

Messages: `lines[0].path[1]: unknown reference 'base_c' (a point reference names a POI, a spawn base or a `points`
entry)`; `lines[0].path[1]: 'wood' is a region id; a point reference names a POI, a spawn base or a `points` entry`
(also `line`, `scatter entry`, `crossing`); a string in a place without references: `pois[0].at: expected a point
[x, y] of two numbers, got 'j' (point references are accepted only in region at/points/path, line path and crossing
at/path)`; `points: expected a mapping of name -> [x, y], got list`; `points.Junction: name must match
[a-z][a-z0-9_]* and be at most 64 characters (got 'Junction')`.

## 5. Type table — `mapgen/types.yaml`

Data, not code: adding a type or changing a cost needs no code change. `schema.py` loads it and validates spec types
against it. The table carries gameplay and splat data only; every render colour lives in the palette (§5.1).
Contents:

```yaml
regions:
  #          cost (multiplier on travel time; inf = impassable), los (blocks line of sight),
  #          splat (splat layer name), height (default metres), height_mode,
  #          edge (blend radius in metres of raise_flat / flatten regions)
  field:    { cost: 1.0, los: false, splat: field }
  forest:   { cost: 1.6, los: true,  splat: forest }
  marsh:    { cost: 2.5, los: false, splat: mud }
  hills:    { cost: 1.3, los: false, splat: grass, height: 50,  height_mode: add_noise }
  mountain: { cost: inf, los: true,  splat: rock,  height: 200, height_mode: ridge }
  plateau:  { cost: 1.0, los: false, splat: grass, height: 30,  height_mode: raise_flat, edge: 40 }
  town:     { cost: 1.1, los: true,  splat: dirt,  height_mode: flatten, edge: 30 }
  ruins:    { cost: 1.3, los: true,  splat: dirt }
  clearing: { cost: 1.0, los: false, splat: grass }
  meadow:   { cost: 1.0, los: false, splat: grass }
  lake:     { cost: inf, los: false, splat: sand,  height_mode: water }
lines:
  #          flatten (blend strength of flatten_line, default 1), crossing (type of the automatic crossing where the
  #          line crosses water, §4.7; none = no automatic crossing)
  river:    { cost: inf, los: false, splat: sand, width: 30, height_mode: water }
  road:     { cost: 0.6, los: false, splat: road, width: 10, height_mode: flatten_line, crossing: bridge }
  trail:    { cost: 0.8, los: false, splat: dirt, width: 5, height_mode: flatten_line, flatten: 0.5, crossing: ford }
  cliff:    { cost: inf, los: true,  splat: rock, width: 8 }
  wall:     { cost: inf, los: true,  splat: rock, width: 8 }
crossings:
  bridge:   { cost: 0.6, r: 25 }
  ford:     { cost: 2.5, r: 40 }
  pass:     { cost: 1.5, r: 80 }
  ramp:     { cost: 1.2, r: 40 }
  gate:     { cost: 0.6, r: 12 }
pois:
  #         glyph: circle|square|diamond|triangle|star|cross; r: footprint metres
  base:          { glyph: square,   r: 120, objective: false }
  capture:       { glyph: circle,   r: 40,  objective: true }
  keep_site:     { glyph: square,   r: 60,  objective: true }
  neutral_camp:  { glyph: triangle, r: 50,  objective: true }
  treasure:      { glyph: star,     r: 10,  objective: true }
  supply:        { glyph: diamond,  r: 30,  objective: true }
  spawn:         { glyph: circle,   r: 15,  objective: false }
  landmark:      { glyph: cross,    r: 20,  objective: false }
splat_layers: [grass, field, forest, mud, rock, sand, dirt, road]   # order = splat channel order; grass is the base
```

`inf` is parsed as `float("inf")` (YAML `.inf` is also accepted). Every `splat` value must be listed in
`splat_layers`.

### 5.1 Palette — `mapgen/palette.yaml`

All render colours live in one file, separate from the gameplay type table. `build --palette <path>` and
`validate --palette <path>` use another palette file; keys it omits fall back to the default palette. A palette must
give a colour for every region, line and crossing type of `types.yaml`.

Design rule: categories are told apart by **lightness and pattern first, hue second**, so the map reads for
deuteranopia/protanopia (red–green) viewers. Region fills sit on six CIE L* levels (92, 79, 66, 53, 40, 26), at
least 12 apart; types that share a level carry different patterns. Neighbouring categories differ strongly in
lightness; red/green pairs are never the only distinction. Team colours are blue, amber and purple, which stay
distinct under red–green colour deficiency, and teams are additionally told apart by letter and, in overlays, by
line style or texture.

```yaml
base: "#89ac4d"                 # open ground, L* 66
regions:
  field:    "#fbeb62"           # bright yellow, L* 92
  hills:    "#d2c39d"           # pale sand, L* 79 (hillshade carries the shape)
  marsh:    "#ea8929"           # orange, L* 66, reeds
  plateau:  "#957a4c"           # tan brown, L* 53
  town:     "#4283c0"           # steel blue, L* 53, street grid
  mountain: "#5c5c5c"           # mid grey, L* 39 (hillshade carries the shape)
  ruins:    "#9a3a8d"           # magenta, L* 41, crosshatch
  clearing: "#b3cc8a"           # light tint of the ground, L* 79, dots
  meadow:   "#a6c46e"           # lush light green, L* 75, hatch
  lake:     "#103a70"           # deep blue, L* 25
  forest:   "#194624"           # very dark green, L* 26, stipple
lines:
  river:    "#103a70"           # solid
  road:     "#ffffff"           # white with dark casing
  trail:    "#3d2a14"           # dark brown, dashed
  cliff:    "#111111"           # hatched
  wall:     "#f2e6c8"           # pale stone band on a dark casing with crenellation ticks (cliff is black)
crossings:
  bridge:   "#7a4a12"           # brown bar with white plank stripe
  ford:     "#8fd0ff"           # dashed ring
  pass:     "#ffffff"           # chevron
  ramp:     "#f0d9b5"           # dashed ring with step bars
  gate:     "#ffd23f"           # dashed ring with an arch (two posts and a lintel)
patterns:                       # region type -> texture drawn in the region's darkened colour
  forest: stipple
  marsh:  reeds
  town:   grid
  ruins:  crosshatch
  clearing: dots
  meadow: hatch
impassable_pattern: strike      # strike-through hatch (white stripe, dark casing) overdrawn on `impassable: true` regions
teams: ["#2f6fdf", "#f2a81d", "#6a2cc0"]   # default team colours in team order: blue, amber, purple
overlay:
  contested:   "#ffffff"        # white diagonal hatch with a dark edge stripe, not a tint
  unreachable: "#000000"        # black hatch
  traffic:     ["#fff7bc", "#fe9929", "#662506"]   # light-to-dark ramp (lightness carries the value)
  cover:       "#08306b"        # dark blue tint where covered
  fair:        "#1b7f79"        # teal tint with a white hatch where teams reach a cell within the balance tolerance
outline_darken: 0.45            # region outline = fill colour x (1 - this)
```

Pattern names: `none`, `stipple` (dots on a jittered grid), `hatch` (diagonal lines), `crosshatch`, `reeds` (short
horizontal dashes in staggered rows), `dots` (regular dots), `grid` (square lattice), `hlines` (horizontal lines).

`python -m mapgen palette [--palette path]` prints the L* of the ground and every region fill, the |ΔL*| matrix, the
minimum over all pairs, the minimum over pairs without a pattern difference, and the pairs separated by pattern only.
The check passes when every pair of fills differs by ≥ 12 in L* or by pattern (exit code 0), and fails otherwise
(exit code 1).

## 6. Pipeline

`build.build(spec_path, out_root, only=None)` runs:

1. **Load and validate** (`schema.py`) → `MapSpec`. On errors, print all of them and exit with code 1 before writing
   anything.
2. **Geometry** (`geometry.py`) → in this order: a shapely geometry per region; per line, parents before their
   tributaries (§4.3), the smoothed centreline `LineString` and the area; water lines clipped outside lakes (§4.3);
   crossing geometries (§4.4); automatic crossings (§4.7), appended to the crossing list; scatter entries (§4.6),
   generated from these geometries and inserted into the region list. Geometry warnings (§4.2 fragment safety) go to
   the warning list. Cached once; every later stage rasterizes from it.
3. **Heightmap** (`height.py`) at `heightmap_res` → `float32` metres, shape `(res, res)` covering the full map
   (non-square maps: the heightmap is square and stretched; `meta.json` records both sizes).
4. **Analysis** (`analysis.py`) on the analysis grid (`cell` metres).
5. **Render** (`render.py`).
6. **Export** (`export.py`).
7. **Report**: `report.md` and `report.json`.

`--only render|analyze|export` runs the needed earlier stages but writes only that stage's files (plus the report for
`analyze`).

### 6.1 Rasterization (`raster.py`)

- `mask(geom, grid, supersample=2) -> float32 [0,1]`: draw the pixel-space polygon(s) (exterior filled, holes
  cleared) with `PIL.ImageDraw` at `supersample×` resolution, then box-downsample. Gives anti-aliased coverage.
- `hard(mask) = mask >= 0.5`.
- `soft(mask, radius_m, grid)`: Gaussian blur (`scipy.ndimage.gaussian_filter`, sigma = radius in pixels / 2) for
  smooth falloffs.
- `edt_inside(mask_bool, grid)`: `scipy.ndimage.distance_transform_edt` in metres (distance from each inside cell to
  the boundary).
- `type_grid(spec, grid) -> int16`: region index of the topmost region at each cell (−1 for none), using hard masks in
  list order.

### 6.2 Heightmap (`height.py`)

All heights in metres; steps applied in order:

1. **Base:** `base_height + relief · fbm(x / relief_scale, y / relief_scale, octaves=5) +
   detail · fbm(x / detail_scale, y / detail_scale, octaves=4)` (fbm in [-1, 1]); `detail` is a mid-frequency layer
   that gives plains gentle undulation.
2. **Hills** (`add_noise`): `+ height · soft_mask · (0.5 + 0.5 · fbm(x/300, y/300, octaves=4))`, with
   `soft_mask = soft(mask, 120 m)`.
3. **Mountains** (`ridge`): each mountain region contributes separately and the accumulated mountain layer is the
   per-cell **maximum** of the contributions, so overlapping mountains do not stack. For one region:
   `d = edt_inside(hard mask)`; `t = smoothstep(0, falloff, d) ^ 0.8` with `falloff = 0.6 · max d`;
   `height · t · (0.55 + 0.45 · ridged_fbm(x/250, y/250, octaves=5))`, plus a **foothill apron**
   `0.15 · height · soft(mask, 180 m) · (1 − t)`: a soft skirt that surrounds the mask and fades out toward the
   summit, so the mountain rises from a gradient. `ridged_fbm` = `1 - |fbm|` normalized to [0, 1].
4. **Passes:** inside each `pass` crossing (disc or band), scale the accumulated mountain layer (apron included) by
   `1 - 0.85 · soft(crossing, r)` so the pass is a saddle. The ground then receives the mountain layer.
5. **Plateaus** (`raise_flat`): target = median height inside the region + `height`; blend
   `h = lerp(h, target, soft(mask, edge))` (`edge` default 40 m).
6. **Towns** (`flatten`): target = median height inside the region; same blend with `soft(mask, edge)` (default 30 m).
7. **Ramps:** inside each `ramp` crossing (disc or band) relax the ground toward a strong blur:
   `h = lerp(h, gaussian_filter(h, sigma = r), soft(crossing, r))`, so a plateau edge or cliff becomes a walkable
   slope.
8. **Roads and trails** (`flatten_line`): `h_smooth = gaussian_filter(h, sigma = 60 m)`; blend toward `h_smooth` with
   `strength · soft(mask, width)`, where `strength` is the type's `flatten` entry (1 for roads, 0.5 for trails).
9. **Water** (rivers and lakes, `water`). Water follows the terrain: every water surface sits 0.8 m (freeboard)
   below the lowest nearby ground on its banks, so it never floats above the ground and a river may run uphill where
   the ground rises. All levels are read from the ground before any water is carved, blurred with sigma 4 m
   (`ground`). The bank probe distance `p` is 2 m, at least 2 heightmap cells.
   - **Lake:** one flat surface = min of `ground` over the ring of width `p` just outside the lake (island shores
     included) − freeboard. Touching or overlapping lakes are one water body and share the lowest of their
     levels.
   - **River:** sampled along the centreline every 10 m (the `lines.json` points); at each sample the surface =
     min of `ground` at the centreline and at `p` beyond each bank edge (the widest edge, `width/2 · (1 + 0.6 ·
     width_var)`) − freeboard. A sample within `p` of a lake (or inside it) carries the lake's surface.
   - **Junctions** (a river entering a lake, a tributary joining its parent): both water bodies meet at the lower of
     the two levels. A lake drops to the lowest surface of an entering river within 10 m of its shore; a tributary's
     joined end and the parent samples within 10 m of the junction take the lower of the two. Around a junction a
     river surface is lowered to the junction level within 10 m and blends back to its own level over the next
     30 m; surfaces are only ever lowered.
   - **Water cells and surfaces:** the water cells are the hard masks of the lakes and of the river areas. A lake
     cell's surface is the lake level; a river cell takes the surface (linear interpolation between the 10 m samples) at
     the arc length of its projection onto the centreline: the arc length of the nearest point of the centreline
     resampled every 0.5 m, plus the cell's offset along the local direction, held within 0.25 m of that point, so
     the surface is continuous along the river; where water bodies overlap, the lower surface wins.
   - **Shore distance:** distances to a shoreline come from the signed distance to the water mask's cell edge
     (negative under water); beyond the shoreline it blends (smoothstep over 0–3 m of distance) to its gaussian blur
     with sigma 1.5 m, so the raster staircase leaves no facets while the waterline stays exact.
   - **Shelving bed:** the full depth is `D = 0.75 · water_depth`. A water cell at shore distance `d` from the
     union of all water gets `h = min(h, surface − max(0.1 m, D · smoothstep(0, S, d)))`: a shallow margin at
     the shoreline that deepens smoothly to the full depth at the shelf width `S` = 8 m for a lake and
     `0.7 · width/2` for a river. Ground already below the shelf profile stays.
   - **Mouth channel:** a river ending in a lake carries its bed on into the lake bed along its mouth channel
     (§4.3). A lake cell within `width/2` of the channel, at distance `a` from it and arc length `t` along it (of
     channel length `L`), gets the depth `max(lake shelf depth, D · smoothstep(0, 1, (width/2 − a) / (0.7 ·
     width/2)) · (1 − smoothstep(0, L, t)))`: the river's own cross-section, fading out over the channel. The bed
     runs from the river into the lake with no bar at the mouth, and the river's last stretch (at the lake level
     within 10 m of the shore, see Junctions) meets the lake water at the same level.
   - **Shore profile:** above water every bank (lake shore, island, river bank) is capped by the profile
     `cap(d)`, `d` = distance from the body's nearest water cell: it rises 0.35 m per metre (19°) up to 3 m above
     the water, then its slope blends (smoothstep) to 1.0 per metre (45°) over the next 0.6 × that distance.
     Each dry cell is capped at `min` over all water bodies (each lake, each river) of `surface + cap(d)`, where
     `surface` is the body's surface at its nearest water cell; for a river (whose surface slopes) that nearest-cell
     surface is smoothed with a gaussian of sigma 2 m, because the nearest cell of a slanted raster shoreline jumps
     several cells at once and would terrace the bank in steps along the river. The cap is joined
     to the ground through a smooth minimum of width 0.5 m; ground below the cap is untouched. So a lake shore
     shelves from the surrounding ground down to the waterline and on under the water, and an island rises from
     the water as a beach.
   - **Bank floor:** within `p` of a water body, the ground never drops below that body's surface (for a river, the
     smoothed nearest-cell surface of the shore profile)
     `+ min(0.3 m, cap(d))` (`d` from the nearest water of any body), unless it was already lower; this keeps
     the land between two channels at different levels above the higher water.
10. **Finish:** gaussian blur sigma 1 pixel; then every water cell's ground is held at least 0.1 m below its
    surface and every bank cell within 4 cells of the water at its step 9 bank floor; clamp to
    `[0, height_max]`.

Noise (`noise.py`): 2D value noise on an integer lattice with smoothstep interpolation, hashed from
`(ix, iy, seed)` with a fixed integer hash (no global RNG state); fbm sums octaves with lacunarity 2, gain 0.5,
normalized to [-1, 1]. 1D periodic noise for blob outlines uses the same lattice along one axis, wrapping at a
period. All vectorized over numpy arrays.

### 6.3 Analysis (`analysis.py`)

Analysis grid: `cols = ceil(size_x / cell)`, `rows = ceil(size_y / cell)`.

**Cost grid** (multiplier on travel time per metre):

1. Start at `1.0`.
2. Regions in list order: where the hard mask is set, cost = region cost (later wins).
3. Rivers and cliffs: where set, `inf`.
4. Roads and trails, in list order: where set **and** the current cost is finite, cost = line cost.
5. Crossings: where the disc is set, cost = crossing cost (overrides `inf`).
6. Slope: multiply by `1 + 2 · max(0, slope − 0.15)` where slope is rise/run from the heightmap resampled to the
   analysis grid; cells with slope > 0.7 become `inf`. Inside any crossing the slope term uses `min(slope, 0.7)` and
   never makes a cell impassable.

**Travel-time fields:** for each team, `MCP_Geometric(cost, fully_connected=True).find_costs([base_cell])`;
seconds = cumulative × `cell` / `speed`. `inf` = unreachable.

**Territory:** each cell's team = the team with the smallest time; contested (front) cells are where the best two
teams' times differ by less than `max(20 s, 5 %)` of the best.

**Objective balance:** for every POI with `objective: true`: each team's time to reach it, in seconds.
- Neutral POI (no `team`): `spread = (max − min) / min` over teams; flag `IMBALANCED` if
  `spread > balance_tolerance`; flag `UNREACHABLE` if any team can't reach it.
- Owned POI: flag `EXPOSED` if any enemy team reaches it faster than its owner.

**Traffic and chokepoints:** traceback the shortest path from every team base to every other base and to every
objective POI; accumulate a hit count per cell, then dilate by 1 cell. A cell's **constriction** is the fraction of
cells within 120 m (a disc) that are impassable or have cost ≥ 2. A cell's chokepoint **score** is its path count ×
its constriction; cells within `max(250 m, 0.15 · d)` of a base (`d` = the largest straight-line base-to-base
distance) are ignored. The report lists the top 5 chokepoints with score > 0: local maxima of the score grid at
least 150 m apart (ties broken by the Gaussian-smoothed score), with world coordinates, path count, constriction,
score, and the nearest named feature (closest crossing/POI/region with a label, within 200 m). When no cell scores
above 0 the report states "open ground - no chokepoint"; a base-to-base route whose maximum constriction (outside
the ignored zones) is 0 is reported as open ground too.

**Fair zone:** the cells reachable by every team whose relative time spread `(max − min) / min` over the teams is
at most `balance_tolerance`; this is where a neutral objective would be balanced. Its share of the map is reported.

**Openness (cover):** `los` mask = cells of LOS-blocking regions/lines; `openness = 1 − box_filter(los, 100 m)`.
The report gives the map-wide mean openness, the mean openness over each team's territory (cells it reaches first,
contested cells excluded) and the mean along each base-to-base shortest path.

**Probe:** `probe_point(x, y)` returns, for a world point, each team's travel time, the spread, the cost cell value,
whether the cell is in the fair zone, the topmost region, and the lines and crossings whose area contains the point.
It uses the same cell lookup as the objective table, so times at an objective's coordinates equal the report's.

**Validation inside analysis** (reported as warnings, not errors; the geometry warnings of §4.2 come first): a POI
or base on an impassable cell; a base that can't reach another base; a road or trail whose area crosses a river or
lake with no crossing over the intersection (with `auto_crossings: true` every such intersection is covered).

### 6.4 Render (`render.py`)

All colours, patterns and outline strength come from the palette (§5.1). Every image is the **map area** (longest
side `render_px`, aspect matching the map) plus a **side panel** to its right, so the panel's width is 22 % of the
image width. The panel holds the title (map `name`), a subtitle line (size and seed, and for overlays the overlay's
name), the legend, the team list and, at the bottom, the scale bar and grid step. Nothing but map content is drawn
over the map area. Panel rows shrink to fit the image height.

`map.png`:

1. Fill the palette `base`; paint region colours in list order, then water lines (rivers, each at least 2.4 px wide
   and overlapping lakes by a pixel), with anti-aliased masks (alpha = mask coverage; masks are drawn at 2×
   resolution).
2. Multiply by a **hillshade** of the heightmap (light from NW at 45°, strength so flat ground is unchanged and steep
   faces darken by up to 40 %), except over water, which stays flat.
3. Region **patterns** (palette `patterns`): each pattern is drawn in the region's fill colour darkened by 60 %, only
   where that region is the topmost one — stipple for forests, reeds for marsh, grid for towns, crosshatch for ruins,
   dots for clearings.
   **Impassable strike-through** (palette `impassable_pattern`, pattern `strike`): a region with `impassable: true` is
   additionally overdrawn, where it is the topmost region, with a diagonal (`\`) hatch of 14 px period: a 2.4 px white
   core stripe between 1.3 px dark casings, so it separates by pattern and lightness on every fill and differs from every
   type pattern. Type colour and outline stay.
4. **Outlines** are drawn where the painted **type** changes, found by edge detection on the 2×-resolution type grid
   (topmost region per pixel; lakes and rivers form one `water` type), 1.5 px wide in the darkened fill colour of the
   topmost side. Overlapping or touching regions of one type read as one area, lake and river water reads as one
   body, and nothing is outlined along the map edge.
5. Lines: roads cased (dark 1 px casing, white fill); trails dashed dark brown on a light halo; cliffs as a dark
   hatched line; walls a pale band on a dark casing with a crenellation tick every ~10 px on both sides. A line is never narrower than 2.4 px.
6. Crossings: bridge = brown bar with dark casing and a white plank stripe across the river; ford = dashed light
   ring with a dark casing; pass = dashed ring with a chevron drawn in white over dark; ramp = dashed ring with three
   step bars; gate = dashed ring with an arch (two posts and a lintel). A band crossing is drawn as the dashed outline of its band with its symbol at the middle.
7. POIs: glyph (§5) in white with black outline, sized ~1.2 % of the image; team-owned POIs filled with the team
   colour and carrying the team id letter in black or white (whichever contrasts); label (or id, with `[team]` for
   owned POIs) next to it in black text with a white halo. Bases: a larger team-coloured square with the team letter
   and the team name.
8. Grid: thin translucent lines every `grid_step` metres, coordinate labels along the left and bottom edges
   (e.g. `1500`). Agents use these to read off coordinates when editing the spec.
9. **Labels.** Glyphs, crossing symbols and grid labels reserve their boxes first. Pinned labels (`label_at`) are drawn
   at their position. Then region labels (anchor: the geometry's representative point), line labels (anchor: the
   centreline's midpoint) and POI labels (candidates beside the glyph) are placed in that order: candidate positions
   around the anchor are tried at growing distances and the first one that lies inside the map area and overlaps no
   reserved box is taken; when none is free the first candidate is used.
10. The panel legend lists only the types present in this map (regions including scatter types, lines, crossings,
    POI types). Every swatch is drawn in the same style as on the map: region fill with its outline and pattern, line
    style (casing, dash, hatch), crossing symbol, POI glyph; the team list shows each team's glyph with letter, name
    and base. When any region is impassable, the legend adds an "impassable" row showing the strike-through hatch.

Overlays (same layout; the map area is a desaturated 50 %-faded copy of the map without panel, and the panel shows
that overlay's legend, the marker legend and the team list):

- `overlay_travel.png`: per team, isochrone contour lines every 30 s over a white light-edged halo, labelled
  `<team> m:ss` every 60 s; team lines differ by style (team 1 solid, team 2 dashed, team 3 dotted) as well as colour;
  contested cells are a white diagonal hatch with dark edge stripes.
- `overlay_territory.png`: cells tinted by owning team (42 % alpha) with a texture per team (team 1 plain, team 2
  dots, team 3 horizontal lines) and a large `<team> <name>` label at the deepest point of the territory; contested
  cells are the white/dark diagonal hatch, unreachable cells a black diagonal hatch in the opposite direction.
- `overlay_traffic.png`: traffic heat as an opaque light-to-dark ramp (palette `overlay.traffic`; lightness carries
  the count), top-5 chokepoints as white circles with black outline and black number matching the report.
- `overlay_openness.png`: covered cells tinted with palette `overlay.cover` (alpha ∝ cover); base-to-base paths drawn
  as white lines with a black casing.
- `overlay_balance.png`: the fair zone (§6.3) tinted with palette `overlay.fair` and covered by a white diagonal
  hatch; each neutral objective is labelled `<id> <spread %>`.
- `overlay_cost.png`: the cost grid (log-scaled greyscale, impassable black) — for debugging the spec.

Contours: `skimage.measure.find_contours` on the time field.

### 6.5 Export (`export.py`)

Into `out/<name>/export/`:

| file | content |
|---|---|
| `heightmap.png` | 16-bit grayscale PNG, `heightmap_res`², north-up (row 0 = north); value = h / `height_max` · 65535 |
| `heightmap.r16` | same data, raw little-endian uint16, **row 0 = south** (the RAW convention Unity and most engines expect) |
| `splat_0.png`, `splat_1.png` | RGBA 8-bit, `splat_res`², north-up; channels = `splat_layers` in order (8 layers → 2 images); weights per pixel sum to 255 |
| `masks/<type>.png` | 8-bit grayscale coverage per region type, line type and crossing type present (automatic crossings included), plus `water.png` (rivers + lakes) and `impassable.png` (analysis grid cost = inf, upsampled to `splat_res` nearest-neighbour). The build first deletes every `*.png` already in `masks/`, so the folder holds only the current spec's types |
| `pois.json` | `[{id, type, team, tier, label, r, x, z, y}]` — `y` = terrain height in metres sampled at the point; bases included |
| `lines.json` | `[{id, type, width, points: [[x, z, y], ...], water_surface}]` — smoothed, meandered centrelines resampled at 10 m; `y` = terrain height at the centreline; `water_surface` = for rivers, the water-plane height in metres at each point (same length as `points`; follows the terrain and may rise downstream), else `null` |
| `regions.json` | `[{id, type, label, height, scatter, water_surface, polygons: [{outer: [[x, z], ...], holes: [[[x, z], ...], ...]}, ...]}]` — final region geometry in paint order, each part an outer ring plus its holes (clearings in fragmented forests, islands in lakes); `scatter` is `true` for regions generated from a `scatter` entry, which carry that entry's `id`; `water_surface` = for lakes, the flat water-plane height in metres, else `null` |
| `crossings.json` | `[{id, type, auto, r, width, x, z, y, points}]` — every crossing, explicit and automatic (`auto: true`); a disc has `r` and `width: null, points: null`; a band has `width`, `r: null` and `points` = its smoothed centreline `[[x, z, y], ...]` resampled at 10 m; `x, z` = disc centre or band middle, `y` = terrain height there |
| `spec.resolved.json` | the fully resolved spec: every default applied, scatter regions and automatic crossings expanded (in paint order), team colours, POI footprints (bases included), analysis and output parameters, the type table in use; point references appear as their resolved coordinates; importers read this instead of YAML |
| `settlements.json` | town maps with a settlement file only (`<spec stem>.settlement.json` beside the spec, else `out/<name>/settlement.json`; written by `export_settlements.py` when its district and road ids match the spec): per settlement the leaf districts (kind, density, layer depth/parent, top-level name, polygon, per-edge facing road `{road, class, width, offset}` or `null`), layer nodes, roads (class, width, plan polyline), walls (polyline, `wall` line ids, towers), gates (position, outward normal, kind, wall, road), bridges (position, road, span direction), landmarks; own `format: mapgen-settlements`, `format_version: 1`; contract in EXPORT_FORMAT.md §3.10 |
| `meta.json` | `size`, `height_max`, `heightmap_res`, `splat_res`, `splat_layers` with channel assignment (`{"grass": "splat_0.r", ...}`), `terrain_layers` (`[{name, channel, image}]` per splat layer, e.g. `{"name": "grass", "channel": "r", "image": "splat_0.png"}`), `water_level` (`null`: there is no global sea level; lakes and rivers have per-feature surfaces), axis convention text, `seed`, sha256 of the spec file, mapgen version |

**Water surfaces:** each exported water surface sits 0.8 m below the lowest nearby ground on its banks, and the
heightmap carves its bed `0.75 · water_depth` below it (§6.2 step 9).

**Splat weights:** start with `grass = 1`. For each region in list order, `w_layer = max(w_layer, coverage)` and
scale the other layers by `1 − coverage`; then lines (roads/trails/rivers' banks: river and lake masks dilated by 8 m
write `sand`); then each non-water line repaints its own layer over the bank ring (its coverage × bank coverage ×
(1 − water-channel coverage)), so trails and roads stay continuous up to the water's edge; then slope: where slope > 0.6 blend toward `rock` by `smoothstep(0.6, 1.0, slope)`. Finally normalize
so channels sum to 1 and quantize so they sum to exactly 255 (put the rounding remainder in the largest channel).

**Splat blend** (`output.splat_blend`, opt-in; `build --splat-blend` turns it on with the defaults for a spec that
leaves it off). It runs on the painted weights after the water banks and before the slope step; every step moves weight
between layers, so the per-pixel sum is unchanged. Distances are metres; `smoothstep` as above.
1. Road shoulders: `d` = distance to the nearest pixel with `road ≥ 0.5`; outside the road, the share
   `1 − smoothstep(0, road_edge, d)` (the halo `h`) of `grass`, `field` and `forest` moves to `dirt`.
2. Dirt patches: `near = 1 − smoothstep(0, patch_reach, d_g)` with `d_g` the distance to ground where
   `dirt + road ≥ 0.5`; noise `n1 = fbm2(x / noise_scale, y / noise_scale, seed, 3 octaves)`; the share
   `patches · smoothstep(0.05, 0.35, n1) · near` of `grass` moves to `dirt`.
3. Grass tufts: `n2` = the same fbm at scale `0.6 · noise_scale` with seed `seed + 7919`; the share
   `tufts · smoothstep(0.15, 0.45, n2) · (1 − h)` of `dirt` moves to `grass`.
4. Feather: every layer is gaussian-blurred with sigma `edge / 2` (edge-clamped), softening all region and line edges.
`seed` defaults to `(map seed · 1000003 + 0x5B1E) mod 2^31`. `patches: 0`, `tufts: 0`, `road_edge: 0` or `edge: 0`
switch the single step off. `spec.resolved.json` echoes the settings as `output.splat_blend` when on.

### 6.6 Report

`out/<name>/report.md` (for humans and agents) and `report.json` (same data). Sections:

1. Header: name, size, seed, brief.
2. Warnings (geometry warnings of §4.2, validation of §6.3).
3. Team summary: base-to-base travel time for each pair; share of map area each team reaches first; contested area
   share.
4. Objective table: one row per objective POI — id, type, owner, time from each team (`m:ss`), spread %, flag; below
   it the fair-zone share of the map.
5. Chokepoints: top 5 with coordinates, path count, constriction, score, nearest feature (or "open ground - no
   chokepoint"), and the routes that are open ground.
6. Automatic crossings (when any): id, type, position, what they cross.
7. Openness: map-wide mean, each team's territory mean, each base-to-base route.
8. File list.

## 7. CLI (`__main__.py`)

```
python -m mapgen build <spec.yaml|maps-dir> [--out out] [--only render|analyze|export] [--palette palette.yaml]
python -m mapgen validate <spec.yaml> [--palette palette.yaml]
python -m mapgen probe <spec.yaml> x y [x y ...]   # per point: team times, spread, cost, region/line (no files)
python -m mapgen diff <build a> <build b> [--text] [--max-changes N]   # delta of two build folders (no files)
python -m mapgen spec set <spec.yaml> <path>=<value> [...] [--create] [--dry-run] [--diff] [--detach] [--detach-refs] [--palette palette.yaml]
python -m mapgen spec add <spec.yaml> <section> <value> [--id ID] [--dry-run] [--diff]   # append a feature, generated unique id
python -m mapgen spec delete <spec.yaml> <path> [...] [--detach-refs] [--dry-run] [--diff]   # delete a feature or a key
python -m mapgen spec rename <spec.yaml> <old> <new> [--dry-run] [--diff]   # an id and every reference to it
python -m mapgen spec-edit <spec.yaml> [--port N] [--no-open] [--preview-px N]   # browser editor (127.0.0.1 only)
python -m mapgen --version              # mapgen version and the spec format versions it reads
python -m mapgen town generate <radial> [--seed N] [--size X Y] [--id SID] [--param NAME=VALUE ...] [--out DIR]   # settlement layout -> settlement.json, preview.png, spec and flowgraph fragments (`town check | preview | list` too; `mapgen/TOWN_SPEC.md`)
python -m mapgen types                  # print the type table
python -m mapgen palette [--palette palette.yaml]   # L* legibility matrix of the palette (§5.1)
```

- `build` on a directory builds every `*.yaml` in it.
- `probe` runs geometry, heightmap and analysis (no render, export or report) and writes no files. For each point it
  prints each team's travel time (`m:ss`), the spread `(max − min) / min`, the cost-grid value of the cell (and `fair`
  when the cell is in the fair zone), and the topmost region, the lines and the crossings at the point. A POI id may
  stand in for an `x y` pair.
- `diff` reads two build directories, never the spec, and prints a JSON report (default) or a text summary
  (`--text`): changed/added/removed files, heightmap and raster texel changes, JSON path changes, objective time
  deltas and report-value deltas. Exit code 0 when the report is printed; 1 when a path is missing or not a build
  directory. Full design: `mapgen/DIFF_SPEC.md`.
- `spec set` edits the spec text in place (`mapgen/specpatch.py`). Each argument is `<path>=<value>`; the edits apply in
  order, all or nothing. Path grammar: keys joined by `.`; the items of a feature list (`pois`, `regions`, `lines`,
  `scatter`, `crossings`) are addressed by `id` (`pois.fa.at`, `lines.river_main.path[2]`); `[n]` is a zero-based
  position in a sequence without ids (waypoints, vertices); a trailing `[+n]` / `[+]` inserts before position `n` / appends;
  the value `@delete` with a trailing `[n]` deletes the item. A value is JSON or YAML flow syntax. Whole numbers are
  written as integers, others as the shortest decimal. Only the value spans change: comments, quoting, flow/block style,
  key order and line endings stay byte-identical. A point reference set to `[x, y]` moves the referenced `points` entry,
  POI `at` or team `base` (`--detach`: writes the literal at the vertex). Safety net per patch: the patched text is
  re-parsed and compared with the old document plus exactly the requested change (a mismatch is a `VerifyError`), then
  `schema.load_spec_text` validates it; a loader failure prints the loader's error list and writes nothing. Refused
  (`PatchError` subclasses, `error: <Type>: message`, exit 1): anchors, aliases, merge keys, duplicate keys, block or
  multi-line scalars, several documents, a path that addresses nothing, positional addressing of id-keyed lists, a
  comment in the way of a flow-sequence deletion. `--diff` prints a unified diff; `--dry-run` also writes nothing. The
  file is replaced atomically (temp file in the same folder, `os.replace`). Exit 0 ok (also "no change"), 1 errors.
  Structure edits (`mapgen/specpatch.py`, same safety net): `--create` adds a key the mapping lacks (`Put`: appended to a
  flow mapping as `, key: value`, a new line at the mapping's indent in a block mapping); `path=@delete` with a key or a
  feature id as the last token deletes the key or the feature. `spec add` (`Add`) appends a feature to `regions`, `lines`,
  `crossings`, `pois`, `scatter` or `points` in the section's style, creating the section when absent (before the first of
  `analysis`, `output`, `auto_crossings`, else at the end of the file); the id is `--id`, the value's `id`, or `<type>_<n>` /
  `poi_<n>` / `p<n>` with `n` one above the largest in use for the base, never an id in the single id namespace (§4.8) and
  never an id the caller names as retired; no `seed` is written (the id derives it). `spec delete` (`Delete`) removes the
  feature's lines or the key; a feature referenced by others (`regions.at`, region/line/crossing `path`/`points` items, a
  line `join`) raises `ReferencedError` listing the referrers unless `--detach-refs`, which writes the literal coordinates in
  place of each reference and removes a `join` and its `join_end`; a section left without items is removed with its key; team
  bases are not deletable. `spec rename` (`Rename`) rewrites the definition and every reference, and pins
  `seed: <feature_seed(old)>` on a region, line or scatter entry that has none; refused when the new id is invalid or in use,
  the old id is defined twice or is a team base. Exit codes as for `spec set`.
  `schema.load_spec_text(data, path_hint, palette_path)` is the byte-neutral text form of `load_spec`.
- `spec-edit` serves a browser editor for one spec on 127.0.0.1 (`mapgen/spec_editor.py`; guards in `editor_http.py`: loopback
  bind, per-run token header on every POST, Host and Origin checks, CSP, 1 MiB body cap). It exits 1 (printing the loader's
  errors) when the spec does not load. The server owns the YAML text buffer and a whole-text undo/redo stack. API, all POST
  JSON: `/api/state`; `/api/apply {version, ops}` (ops are `specpatch.op_from_json` objects: `set` with `path`, `value`,
  optional `detach`; `insert` with `path`, `index`, `value`; `delete` with `path`; addressed by feature id; one call is
  one undo step; every op is verified by the patch engine and the buffer is loaded with `schema.load_spec_text`; a refusal
  is HTTP 422 with the error list and leaves the buffer unchanged, a stale `version` is 409); `/api/undo`, `/api/redo`;
  `/api/save` (atomic in-place write of the buffer, only when it is valid and the file's hash still equals the one read;
  409 otherwise; a clean buffer writes nothing); `/api/geometry` (resolved centrelines and region outlines from
  `geometry.build_geometries`) and `/api/image` (PNG at preview settings: `heightmap_res` 257, `render_px` 768 or
  `--preview-px`, `render_core` only) both keyed by buffer version, cached per buffer text, answering `stale` for an
  outdated version. The state lists handles (kind base/poi/point/waypoint/center/vertex/crossing/radius/width, id-addressed `path`,
  resolved `at`, `ref` when the written value is a point reference; vertex handles carry `chain`, `count`, `minimum`,
  `closed`; radius handles the `center`, width handles the `anchor` and `value`), the features (id, kind, section, type,
  shape, raw keys, first handle) and the referrers of every named point, joins included. Ops also include `put`, `add`,
  `rename` and `delete` of a key or feature (`detach_refs`); an apply returns `state.results` (the generated id of an add,
  `{deleted, detached}`, `{renamed, references, seed_pinned}`); a refusal carries `error_refs` (the feature, handle path and
  property each loader error is about) and, for a referenced feature, `referenced` and `referrers` (HTTP 422). The page gets the
  allowed and required keys per feature kind from `schema.allowed_keys` and the type tables from `types.yaml`. The editor never
  writes `out/`, never autosaves and never touches the spec except through Save. Design:
  `docs/investigations/2026-11-19-map-editor-design.md` (slice 2).
- Prints one line per stage with elapsed time and the output folder at the end. Exit codes: 0 on success; 1 on spec
  errors, a missing file, invalid probe points or a path that is not a build directory; 2 on usage errors.
- Target runtime for a 3 km map: under 20 s for the examples, under 25 s with scatter.

## 8. Validation (`schema.py`)

Errors (abort): missing required keys; unknown keys (catch typos — report the key path); unknown types; wrong
shapes (point not 2 numbers, path < 2 points, poly < 3 points); non-positive `r`/`width`; team count not 2 or 3;
missing or unreadable `format_version`; a region, line, scatter entry or
crossing without `id`; an id that is not `[a-z][a-z0-9_]*` (≤ 64 characters) or used twice in the spec; a point
reference naming no POI, base or `points` entry, or naming a region, line, scatter entry or crossing; a string in a
point place that takes no reference; `points` not a mapping, a `points` name that is not an id, a `points` value that
is not a literal point inside the map; a key repeated within one mapping (reported as
`<yaml>: line N: duplicate key 'k'`); `seed` not an
integer in [0, 4294967295]; any point outside the map rectangle by more than 1 m; `heightmap_res` not 2^n+1; `seed` not an int;
out-of-range natural-shape keys (`warp`, `warp_scale`, `fragment_scale`, `terrain.detail_scale`: ≥ 0 / > 0;
`fragment`, `width_var`, scatter `coverage` and `clump`: 0–1; scatter `within` < 3 points; `meander_scale` and `edge`:
> 0); `edge` on a type that is not `raise_flat`/`flatten`; `join` naming no line, a non-unique id, the line itself or
closing a cycle, `join_end` not `start`/`end` or given without `join`; a crossing with both `at`/`r` and `path`, with
`path` but no `width`, with `width` but no `path`, or with neither `at` nor `path`; scatter `on` empty or naming an
unknown region type; `auto_crossings` not a boolean; `label_at` not a point; a palette that lacks a type or holds a
non-hex colour.
Every error message carries a path like `regions[3].r`.

## 9. Determinism

No `random` or `np.random` global state. All noise is hashed from coordinates and `seed` + a fixed per-use offset + the feature seed (§4.8). Output PNGs are written with Pillow defaults (no timestamps in metadata).

## 10. Acceptance

- `validate` and `build` succeed on `maps/example_river_crossing.yaml`; running `build` twice yields byte-identical
  files.
- `map.png` visibly shows: river with meander, bridge/ford/pass markers, two forests with stipple, the mountain band
  with hillshade, the town, the marsh, roads, labelled POIs, grid with coordinates, legend, scale bar.
- Report: base-to-base time is finite; `bridge_keep` and `cap_s` have times for both teams; the bridge or the ford
  appears among the top chokepoints; there are no warnings for the example.
- With `auto_crossings: false`, removing the `bridge` and `ford` crossings from a copy of the example: bases are
  still connected only through the pass, and analysis warns about the uncovered road × river intersection; removing
  the `pass` too produces the warning "base A cannot reach base B". With `auto_crossings: true` and no crossings at
  all, the build adds an automatic bridge and ford (ids `<line>-<river>-1`) and the bases are connected.
- `heightmap.r16` is `heightmap_res² · 2` bytes; its first row equals the last row of `heightmap.png`.
- Splat pixel weights sum to 255 everywhere.
- A second example, `maps/example_three_team.yaml` (3 teams on a 3000 × 3000 map around a central lake with three
  land bridges, hills, ruins), builds without warnings.
- A third example, `maps/example_natural.yaml` (2 teams, asymmetric, 3000 × 3000), uses `warp`, `fragment`,
  `width_var`, `scatter` (forest and field), `terrain.detail`, a mountain band with a pass, a ford and a bridge. It
  builds without warnings, deterministically (byte-identical output twice), in under 25 s.
- On the natural example's render: fragmented forests show clearings and ragged edges; scatter patches keep their
  `avoid` clearance from roads, rivers, crossings and POI footprints (checked geometrically: minimum distance ≥
  `avoid` minus polygon-approximation slack of 0.5 m); the mountain has a foothill gradient on the hillshade
  (sampled heights fall off gradually outside the mask); no region outline is a circle.
- Every legend entry is distinguishable when the image is converted to greyscale: `python -m mapgen palette` reports
  every pair of region fills (ground included) differing by ≥ 12 L* or by pattern; the minimum over pairs without a
  pattern difference is ≥ 12.
- Overlays read in greyscale: contested and unreachable cells are hatches, traffic is a lightness ramp, team lines and
  territories differ by style or texture as well as colour.
- All examples and every `maps/concepts/*.yaml` build without warnings, with every neutral objective's spread within the
  spec's `balance_tolerance` (the one documented exception: kingsjaw's two deliberately imbalanced home objectives).
- No legend or scale bar over map content; label placement leaves no overlap on the five concepts (checked by viewing
  and by reserved-box overlap tests); no stub where a river enters a lake; rivers and lakes read as one water body.
- `probe` at an objective's coordinates reproduces the report's times (± 1 s).
- A tributary's joined endpoint lies on the parent's centreline (distance 0); the area of every river is disjoint from
  every lake.
- Byte-identical double builds hold for every example and concept.

## 11. Implementation waves

| wave | files | depends on |
|---|---|---|
| 1 | `requirements.txt`, wrappers, `types.yaml`, `schema.py`, `noise.py`, `geometry.py`, `raster.py`, `__main__.py` (`validate`, `types`), example spec | — |
| 2 | `height.py`, `analysis.py`, `render.py`, `export.py`, `build.py`, CLI `build`, README, three-team example | wave 1 |
| 3 | run acceptance (§10), view the images, fix | wave 2 |
| 4 | `palette.yaml`, `palette_check.py`, schema/geometry/height/render changes (§4.2, §4.3, §4.6, §5.1, §6.2, §6.4), `maps/example_natural.yaml` | wave 3 |
| 5 | five two-team concept maps in `maps/concepts/` | wave 4 |
| 6 | `schema.py`, `geometry.py`, `height.py`, `analysis.py`, `render.py`, `export.py`, `build.py`, `__main__.py`, `types.yaml`, `palette.yaml` (authoring features: side panel and type-edge outlines, label placement, tributary joins, water union, band crossings, ramps, edge softness, clearings, automatic crossings, `on` scatter, constriction-scored chokepoints, fair zone, `probe`, export additions); concept specs rewritten to use them | wave 5 |
