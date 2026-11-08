# mapgen export format — contract for engine importers

`mapgen` (this folder) turns a YAML **map spec** into a 2D map, a gameplay report and an **export
bundle**: plain files describing terrain height, surface layers, features and points of
interest. Engine importers (Unity terrain first; other level tools later) read only the export
bundle. This document is the importer's contract. `MAPGEN_SPEC.md` has the full generation
semantics; `README.md` has the authoring guide.

Version: `mapgen_version` 0.2.1 (in `meta.json`). An importer should check the major.minor and
refuse unknown versions.

## 1. Where it lives and how it is produced

```
tools/mapgen/
  maps/**/<name>.yaml          map specs (source of truth; authored by hand or by the map-concept skill)
  out/<name>/                  build output for one spec
    map.png, overlay_*.png      2D renders (for humans, not importers)
    report.md, report.json      gameplay analysis
    export/                     ← the bundle this document describes
```

Build: from `tools/mapgen/`, `.venv/Scripts/python -m mapgen build maps/concepts/<name>.yaml`
(or `mapgen.ps1 build ...` from anywhere). A 3 km map builds in ~15 s.

**Determinism:** the same spec file produces byte-identical exports. `meta.json.spec_sha256`
identifies the source spec, so an importer can detect "unchanged since last import" and
re-imports are reproducible. Concepts available now: `ashen_steppe`, `gloamfen`, `kingsjaw`,
`saltreach_delta`, `thornwald` (in `maps/concepts/`), plus examples `natural`, `river_crossing`,
`three_team` (in `maps/`).

## 2. Coordinates and units

- Units are **metres**. Origin is the **south-west corner** of the map.
- Exported horizontal axes are **x (east)** and **z (north)**; **y is height**. (Map specs use
  `(x, y)` for the ground plane; exports rename spec `y` to `z`.)
- The map spans `x ∈ [0, size[0]]`, `z ∈ [0, size[1]]`; heights are in `[0, height_max]`.
- Every point in the JSON files is in this world space, ready to use as a Unity position
  `(x, y, z)` relative to the terrain's origin corner.

## 3. Files in `export/`

### 3.1 `meta.json`

| key | meaning |
|---|---|
| `name` | map name (`[a-z0-9_]+`) |
| `size` | `[size_x, size_z]` metres; may be non-square |
| `height_max` | metres represented by heightmap value 65535 |
| `heightmap_res` | heightmap side in samples (2ⁿ+1, default 1025); always square, stretched over `size` |
| `splat_res` | splat/mask side in pixels (default 1024); square, stretched over `size` |
| `splat_layers` | `{layer: "splat_<k>.<channel>"}` |
| `terrain_layers` | `[{name, channel, image}]` in channel order — one entry per surface layer |
| `water_level` | always `null`: there is no global sea level; water surfaces are per feature (§3.6, §3.7) |
| `axes` | the convention of §2 as text |
| `seed`, `spec_sha256`, `mapgen_version` | provenance |

### 3.2 Heightmap — `heightmap.r16` and `heightmap.png`

Same data in two encodings; height in metres = `value / 65535 · height_max`.

- `heightmap.r16`: raw little-endian uint16, `heightmap_res × heightmap_res`, **row 0 = south
  (z = 0)**, column 0 = west (x = 0). This is Unity's RAW import convention ("Windows" byte
  order), so it maps directly onto `TerrainData` with size `(size_x, height_max, size_z)`.
- `heightmap.png`: 16-bit greyscale, **row 0 = north** (image convention). Flip vertically
  relative to the RAW.

The heightmap already contains every gameplay-relevant shape: mountains (impassable by slope),
passes cut through them, plateaus and town pads flattened, roads and trails flattened into the
terrain, ramps relaxed to walkable slopes, river and lake beds carved.

### 3.3 Splatmaps — `splat_0.png`, `splat_1.png`

RGBA 8-bit, `splat_res²`, **row 0 = north** (flip vertically for Unity alphamaps, whose first
index is z). Channels follow `terrain_layers`; default layers in order:

| layer | image.channel | what it is |
|---|---|---|
| grass | splat_0.r | open ground (base) |
| field | splat_0.g | farmland |
| forest | splat_0.b | forest floor |
| mud | splat_0.a | marsh |
| rock | splat_1.r | mountains and steep slopes |
| sand | splat_1.g | river/lake banks and beds |
| dirt | splat_1.b | towns, ruins, trails |
| road | splat_1.a | roads |

Per pixel the weights sum to exactly 255 across both images, so they load directly as normalized
alphamap weights.

### 3.4 Masks — `masks/*.png`

8-bit greyscale coverage (0–255), `splat_res²`, row 0 = north. One file per region type, line
type and crossing type present in the map (e.g. `forest.png`, `road.png`, `bridge.png`,
`pass.png`), plus:

- `water.png` — rivers and lakes together,
- `impassable.png` — cells the analysis treats as impassable (mountains, water without
  crossings, cliffs, too-steep slopes); useful for blocking volumes or nav validation.

Masks are for scattering (trees in `forest.png`), blocking, or tool-agnostic import. Exact
geometry is in the JSON files.

### 3.5 `pois.json` — points of interest

`[{id, type, team, tier, label, r, x, z, y}]`. `y` is terrain height at the point; `r` is the
footprint radius in metres (space reserved for whatever stands there). Team bases are included
as `type: "base"` with ids `base_<team>`.

| type | meaning in battlehounds | objective |
|---|---|---|
| `base` | team main base / spawn (has `team`) | no |
| `spawn` | extra spawn point | no |
| `capture` | capture point | yes |
| `keep_site` | buildable fortification site | yes |
| `neutral_camp` | PvE monster camp (`tier` = difficulty) | yes |
| `treasure` | relic / loot | yes |
| `supply` | supply depot (usually owned by a team) | yes |
| `landmark` | named feature, no mechanic | no |

`team` is a team id (`"A"`, `"B"`, …) or `null` for neutral. `label` is the display name or
`null` (fall back to `id`).

### 3.6 `regions.json` — areas

`[{id, type, label, height, scatter, water_surface, polygons}]` in **paint order** (later
regions are on top of earlier ones where they overlap).

- `polygons`: `[{outer: [[x, z], ...], holes: [[[x, z], ...], ...]}]` — one entry per
  disconnected part; `holes` are clearings in forests, islands in lakes, etc. Rings are closed
  (first point = last point).
- `scatter: true` marks background patches generated by noise (`scatter<i>` ids); they are one
  multi-part region each and have no label.
- `height`: metres for `hills` / `mountain` / `plateau` (the authored relief), else `null`.
- `water_surface`: for `lake`, the flat water-plane height in metres; else `null`.

| type | gameplay meaning (from `types.yaml`) | graybox suggestion |
|---|---|---|
| `field` | open, normal speed | nothing / low fences |
| `clearing` | open glade inside forest | nothing |
| `forest` | slow (1.6×), blocks line of sight | tree placeholders scattered inside, honouring holes |
| `marsh` | very slow (2.5×) | marsh decal / reed placeholders |
| `hills` | slightly slow (1.3×), raised terrain | already in heightmap |
| `mountain` | impassable, blocks sight | already in heightmap; optional rock blockouts on the outline |
| `plateau` | raised flat ground | already in heightmap |
| `town` | slightly slow (1.1×), blocks sight; flattened pad | building blockouts on the pad |
| `ruins` | slow (1.3×), blocks sight | broken-wall blockouts |
| `lake` | impassable water | water plane at `water_surface` |

Movement cost and line-of-sight per type are also in `spec.resolved.json.types.regions`
(`cost`, `los`).

### 3.7 `lines.json` — rivers, roads, trails, cliffs

`[{id, type, width, points: [[x, z, y], ...], water_surface}]`. `points` is the final
(smoothed, meandered) centreline resampled every 10 m; `y` is terrain height on the centreline;
`width` is the nominal width in metres (rivers with `width_var` vary around it).
`water_surface` is, for rivers, the water-plane height at each point (same length as `points`,
never increasing downstream); `null` for other types.

| type | meaning | graybox suggestion |
|---|---|---|
| `river` | impassable water except at crossings | water strip mesh / spline at `water_surface` |
| `road` | fast (0.6×), flattened into terrain | spline road / decal |
| `trail` | faster (0.8×), lightly flattened | spline path / decal |
| `cliff` | impassable, blocks sight | wall/rock blockout along the line |

### 3.8 `crossings.json` — bridges, fords, passes, ramps

`[{id, type, auto, r, width, x, z, y, points}]`. A crossing makes otherwise impassable ground
passable.

- Disc crossing: `r` set; `width` and `points` `null`; `(x, z)` is the centre.
- Band crossing: `width` set, `r` `null`; `points` = centreline `[[x, z, y], ...]` (10 m
  steps); `(x, z)` is the band middle.
- `auto: true` = generated where a road (→ `bridge`) or trail (→ `ford`) meets a river or lake.
- `y` is terrain height at `(x, z)`.

| type | meaning | graybox suggestion |
|---|---|---|
| `bridge` | road crossing over a river | bridge blockout spanning the river along the road direction |
| `ford` | shallow wading crossing (slow) | shallow-water marker; the bed is already walkable |
| `pass` | gap through a mountain | already carved into the heightmap |
| `ramp` | walkable slope up a plateau edge or cliff | already relaxed in the heightmap |

For a bridge's direction, use the road in `lines.json` that passes through the disc.

### 3.9 `spec.resolved.json` — the whole map as data

The spec with every default applied, scatter regions and automatic crossings expanded in paint
order, so an importer never needs the YAML or mapgen's defaults. Keys: `name`, `brief`, `size`,
`seed`, `terrain`, `teams` (`[{id, name, color, base}]`), `regions`, `lines`, `crossings`,
`pois` (bases included), `analysis`, `output`, `auto_crossings`, `types` (the type table:
costs, line-of-sight, splat layer, default widths/footprints per type). Coordinates here use spec
naming (`[x, y]` ground plane = export `[x, z]`). Geometry in this file is the authored input
(control points, radii), not the final shapes — use `regions.json` / `lines.json` /
`crossings.json` for final geometry.

## 4. What the bundle does not contain

- No meshes, prefabs, materials or textures — only layer names; the importer maps layer and
  type names to its own assets.
- No buildings, trees or props — only the areas and points where they belong.
- No navmesh — `masks/impassable.png` and the type costs describe intent.
- No team/match rules beyond team ids, names, colours and base positions.

## 5. Engine mapping notes (Unity)

- `TerrainData.heightmapResolution = heightmap_res`; `TerrainData.size = (size_x, height_max,
  size_z)`; heights from `heightmap.r16` as `value / 65535f` into `SetHeights(0, 0, float[z, x])`
  (row index = z).
- `alphamapResolution = splat_res`; one `TerrainLayer` per `terrain_layers` entry in order;
  alphamap `[z, x, layer]` = splat channel / 255 with the PNG row flipped (`z = splat_res − 1 −
  row`).
- World position of export point `(x, z, y)` = terrain transform position + `(x, y, z)`.
- Water: one plane per lake at `water_surface`; river strips along `points` at `water_surface`.
