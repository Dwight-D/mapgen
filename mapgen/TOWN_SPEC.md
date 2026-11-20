# Town layout — procedural settlement generators

Status: slices 1-2 implemented (core model, JSON, checks, preview, spec and flowgraph emitters, `town` CLI, `radial` and
`voronoi` generators). The `wall` line type and `gate` crossing type are a prepared contract addition awaiting an approved re-baseline
(§9.1). Slices 2-6 (§14) are design; each is a backlog item in the `mapgen`
workstream (mission: `docs/missions/town-layout/MISSION.md`, evidence: `docs/investigations/2026-11-20-town-layout-*.md`).
This file is the contract those items implement. It is self-contained: a cold implementer needs nothing else except
`MAPGEN_SPEC.md` (spec schema) and `FLOWGRAPH_SPEC.md` (edge derivation).

## 1. Purpose and scope

A **settlement** is the high-level plan of a human habitat on a map of 600-1500 m per side: districts, major roads,
walls, gates, a few landmarks (squares, a citadel) and the connectivity between them. A generator produces one from
a seed and a few parameters, deterministically. The author pastes the result into a map spec and adds buildings, minor
roads and alleys by hand or with other tools.

Out of scope: buildings, minor roads, alleys, gameplay balance, symmetry, terrain fit (a later phase, §14).
A settlement spans 250-700 m; a district is at least 1 600 m2 (about 40 m across); 6-30 districts per settlement.

Dependencies are the pinned ones (`numpy`, `scipy`, `shapely`, `networkx`, `pillow`); no new dependency. Python 3.8.

## 2. Concepts

- **District**: a polygon of the settlement with a `kind` (core, market, residential, craft, harbour, citadel, temple,
  slum, outskirt). Districts do not overlap; together they tile the footprint.
- **Road**: a polyline with a class (artery, ring, boulevard, bridge, approach) and a width. Major roads run on district
  borders or through districts; ring roads are closed polylines (first point repeated).
- **Wall**: a closed polyline (outer, inner, citadel). `open_shore` lists vertex ranges that carry no wall (water side).
- **Gate**: a point on a wall where a road passes through it. A gate is where movement crosses a wall.
- **Landmark**: a square, market, citadel, fortress, temple or docks at a position with a radius.
- **Movement graph**: nodes are squares and citadels (`zone`), gates and road junctions (`choke`); edges are the road
  segments between consecutive nodes. It is the settlement's projection onto a flowgraph (§9.2).
- **District graph**: nodes are districts plus `outside`; edges are shared borders (§5).
- **Settlement id** (`sid`): `[a-z][a-z0-9_]*`, at most 16 characters, default `town`. Every feature id the settlement
  produces starts with `sid_`.

## 3. Core model (`mapgen/town/model.py`)

Pure data (dataclasses), JSON-serialisable, `format_version: 1`. All coordinates are metres in the map frame (origin
south-west, x east, y north), rounded to 0.01 m (`model.r2`, `model.pt`; never `-0.0`).

```
Settlement
  id, generator, seed, params (resolved, echoed), size [x, y], centre [x, y]
  footprint      polygon: outer boundary of the walled area (ring, first point not repeated)
  districts[]    id, key, kind, polygon, area, label, inside_wall, waterfront, landmarks[]
  roads[]        id, class, polyline, width, nodes[]
  walls[]        id, kind, polyline (closed), thickness, open_shore[[first, last], ...]
  gates[]        id, wall, at, normal, road, kind (gate | postern | water_gate)
  landmarks[]    id, kind, at, r, district, label
  water          {} (reserved for the water overlay, slice 5: river polylines, bay and island polygons)
  graph          nodes[] (id, kind, at, districts[]), edges[] (id, a, b, class, road, length, passable)
  district_graph nodes[] (id, kind: district | outside), edges[] (id, a, b, kind, length, passable, gates[], roads[])
```

JSON file `settlement.json` (`Settlement.to_json()`): keys sorted, 2-space indent, LF, trailing newline. `road.class`,
`graph.edges[].class` use the key `class` (the Python field is `cls`). `Settlement.from_json` reads it back; a
`format_version` other than 1 is an error. `size` and `centre` plus `params`, `seed`, `generator` and `id` are the complete
inputs: `generate(generator, seed, size, params, id, centre)` reproduces the file byte for byte (§8 determinism check).

Polygons are counter-clockwise rings, first point not repeated, `area` in m2 equals the polygon area within 0.5 m2.
Districts are listed plaza first, then by annulus and spoke; `roads` and `graph` nodes and edges are sorted by id.

### 3.1 Identity

Ids are structural, never list positions, so a parameter change keeps the ids of untouched features stable for
`mapgen diff` and the editor.

| feature | id | key |
|---|---|---|
| district | `<sid>_d_<key>` | radial: `plaza`, `r<annulus>s<first spoke>`; later generators: voronoi `p<n>`, grid `x<i>y<j>`, bsp the split path |
| spoke road | `<sid>_spoke_<s>` | |
| ring road | `<sid>_ring_<i>` (i from 1, innermost first) | |
| wall | `<sid>_wall_outer` (later `_wall_inner`, `_wall_citadel`) | |
| gate | `<sid>_gate_<s>` (s = the spoke it terminates) | also the movement-graph node id |
| junction | `<sid>_j_r<ring>s<spoke>` | movement-graph node id |
| landmark | `<sid>_plaza`, `<sid>_citadel`, ... | the plaza id is also its movement-graph node id |
| movement edge | `<a>~<b>` with the two node ids sorted | |
| district edge | `<a>~<b>` with the two ids sorted; `<district>~outside` for the wall | |

Generated ids never exceed 64 characters (`schema.ID_MAX_LEN`): `sid` is limited to 16 and keys to 16.

## 4. Parameters and seeds (`mapgen/town/params.py`)

A generator declares `DEFS: List[ParamDef]`. `ParamDef(name, type int|float|bool, lo, hi, doc, draw_lo, draw_hi, default)`:
`[lo, hi]` is the valid range of an explicit value; a parameter without `default` is **drawn** from the seed, uniformly in
`[draw_lo, draw_hi]` (default `[lo, hi]`; bool: `draw_hi` is the probability of true); a parameter with `default` is
never drawn. `resolve(defs, seed, sid, generator, overrides)` returns the full dict: override, else default, else draw.
Drawn parameters are drawn in `DEFS` order from the stream `stage_rng(sid, generator, seed, "params")` even when
overridden, so an override never shifts another draw. Unknown names, wrong types and out-of-range values are all
collected into one `ParamError(errors)`.

`stage_rng(sid, generator, seed, stage)` is `random.Random(int(sha256("<sid>|<generator>|<seed>|<stage>")[:8]))`. Every
generator stage (`params`, `spokes`, `gates`, `wall`, ...) owns its stream, so adding a draw in one stage leaves every
other stage untouched. Noise seeds come from a stage stream (`rng.randrange(1 << 30)`), never from list positions.

The echoed `Settlement.params` are the parameters after any fitting (§7.1), so regenerating from them is a fixed point.

## 5. Graphs (`mapgen/town/graph.py`)

Both graphs are derived from the geometry by shared functions; a generator never hand-writes edges.

**Movement graph.** The generator creates `GraphNode`s (`zone` for squares and citadels, `choke` for gates and junctions) at
road polyline vertices and lists each road's `nodes` in travel order (a closed ring repeats its first node at the end).
`movement_edges(roads, nodes)` makes one edge per consecutive pair along each road: `a`, `b`, `class` and `road` of the road,
`length` the polyline length between them (m), `passable: true`. A node must coincide with a polyline vertex (same
coordinates); a repeated node pair is an error. `attach_districts(nodes, districts)` fills `GraphNode.districts` with the
ids of districts within 1 m of the node.

**District graph.** `district_graph(districts, roads, walls, gates)`: two districts are adjacent when their boundaries share
more than 1 m. The edge is `adjacent` and `passable: true`, or, when a wall polyline lies on the shared border, `wall` and
`passable: false`. `roads` lists the roads running along the border (overlap above 1 m); `gates` lists, for wall edges, the
gates within 1 m of the border. A district whose boundary lies on an `outer` wall is joined to the node `outside` the same
way (kind `wall`). A wall edge is crossable exactly when `gates` is not empty.

## 6. Generator interface and registry (`mapgen/town/registry.py`)

```python
generate(generator: str, seed: int, size=(1000, 1000), params=None, sid="town", centre=None) -> Settlement
```

Validates arguments (`ParamError` with every problem: unknown generator, seed outside 0-4294967295, `sid` form, `size` each
400-3000 m), resolves parameters, calls the generator. `centre` defaults to the map centre. The generator raises
`GenerationError` when no layout fits (for example a map too small for the settlement).

`GENERATORS: Dict[str, Generator]`, `Generator(name, defs, build, doc)` with
`build(params, seed, size, centre, sid) -> Settlement`. A new generator is a module `gen_<name>.py` exporting `DEFS` and
`generate`, plus one registry entry. The `town generate <name>` choices and `town list` come from the registry. A
generator returns a complete Settlement: districts, roads, walls, gates, landmarks, both graphs; `check` must pass on it.

## 7. Generators

### 7.1 `radial` (implemented; `mapgen/town/gen_radial.py`)

A polar lattice: spokes at angles cut the walled area into wedges, ring roads cut it into annuli.

Parameters (`python -m mapgen town list` prints the live table):

| name | type | valid | drawn | meaning |
|---|---|---|---|---|
| `radius_frac` | float | 0.3-0.95 | 0.45-0.75 | wall diameter as a fraction of the map's shorter side; clamped so the wall (with irregularity and elongation) stays 10 m inside the map |
| `spokes` | int | 4-14 | 5-10 | spoke count |
| `rings` | int | 1-4 | 1-3 | ring roads inside the wall |
| `ring_spacing` | float | 0.6-2.0 | 0.8-1.4 | exponent of the level radii; above 1 packs rings toward the centre |
| `plaza_frac` | float | 0.06-0.3 | 0.10-0.18 | plaza radius / wall radius (minimum 25 m) |
| `angle_jitter` | float | 0-0.3 | 0-0.25 | spoke angle jitter, fraction of the spoke spacing |
| `spoke_dropout` | float | 0-0.8 | 0-0.4 | probability that a non-core spoke starts at an outer ring |
| `wall_irregularity` | float | 0-0.2 | 0.04-0.16 | radial noise amplitude (fraction of radius) of rings and wall |
| `wall_freq` | int | 1-8 | 2-5 | noise cells around the wall |
| `elongation` | float | 1-1.6 | 1-1.3 | stretch along the rotated x axis, area preserving |
| `rotation` | float | 0-360 | 0-360 | rotation of the whole layout, degrees |
| `gates` | int | 2-12 | 3-6 | gates in the outer wall (at most `spokes`) |
| `citadel` | bool | | P(true) 0.5 | the outer wedge farthest from every gate becomes a citadel |
| `min_district_area` | float | 100-20000 | default 1600 | m2 |
| `min_ring_gap` | float | 20-200 | default 40 | smallest radial gap between plaza, rings and wall, m |

Construction:

1. Levels. With `L = rings + 1`, wall radius `R`, plaza radius `rp`: `r_i = rp + (R - rp) * (i / L) ** ring_spacing` for
   `i = 0..L` (level 0 is the plaza edge, level L the wall).
2. Angles. Spoke `s` has `theta_s = 2*pi*(s + j_s) / spokes`, `j_s` uniform in `±angle_jitter` (stage `spokes`; `j_0 >= 0`).
   A shared vertex grid is the spoke angles plus every 5 degrees, minus grid angles within 1.5 degrees of a spoke. Every
   point of the settlement is a function `P(level, grid angle)`, so polygons, roads and walls share identical vertices.
3. Point. `rel = (r_i - rp) / (R - rp)`; `rr = r_i * (1 + wall_irregularity * n(theta) * rel)` with `n` = periodic fbm
   (`noise.fbm1_periodic`, `wall_freq` cells, 2 octaves, stage `wall`); the point is `(rr cos, rr sin)` stretched by
   `(sqrt(e), 1/sqrt(e))`, rotated by `rotation`, translated to the centre, rounded (`rel = 0` keeps the plaza round).
4. Core spokes: three evenly spaced spokes (`round(k * spokes / 3)`) always reach the plaza. Every other spoke, with
   probability `spoke_dropout`, instead starts at ring `k` (uniform 1..rings): it is a district border and road only from
   that ring outward, so the wedges next to it merge in the inner annuli. Every annulus therefore has at least 3 borders.
5. Districts. The plaza is the polygon at level 0 (`kind core`, key `plaza`). Annulus `i` (between levels `i` and `i+1`) is cut
   by its present spokes into wedges, key `r<i>s<first spoke>`; kind `market` (annulus 0), `craft` (outermost), else
   `residential`. Labels are `<Kind name> <n>`, numbered per kind in list order.
6. Citadel (if `citadel`): among outermost-annulus wedges, the one whose mid-angle is farthest from every gate spoke (ties:
   smallest key); kind `citadel`, a `citadel` landmark (r 45) at its representative point.
7. Gates. `g = min(gates, spokes)`; spoke indices `int((j + off) * spokes / g)` for `j < g`, `off` uniform (stage `gates`), bumped
   to the next free spoke on collision. A gated spoke's road runs to the wall, a gate at its end (road width 14, normal =
   the outward radial direction); other spokes end at the outermost ring (width 10, no road when that is also their start).
8. Roads. Spoke polyline: the centre (when it starts at the plaza), then `P(level, theta_s)` from ring `max(start, 1)` to its
   end; nodes: the plaza, junctions `j_r<i>s<s>`, the gate. Ring `i` is a closed polyline over the grid, starting at its first
   junction; its nodes are the junctions of the spokes present at that ring, first node repeated.
9. Wall: ring at level L, thickness 8, closed. Footprint: the same ring. Landmarks: `<sid>_plaza` (square, radius `rp`, the
   plaza district) and the citadel.
10. Fit. If a ring gap is below `min_ring_gap`, or any district (the plaza included) is below `min_district_area`, drop one
    spoke (down to 4), then one ring (down to 1), and rebuild; stop with `GenerationError` when nothing remains to drop.

Expresses: concentric and wedge cities, walled cores with irregular walls, spoke-and-hub towns (refs 7, 9, 6 of the mission).
Does not express: orthogonal order, outskirts outside the wall, water.

### 7.2 `voronoi` (implemented; `mapgen/town/gen_voronoi.py`)

Voronoi-cell districts with an angular wall; `organic=true` is the high-jitter regime.

| name | type | valid | drawn | meaning |
|---|---|---|---|---|
| `patches` | int | 8-30 | 12-24 | cells of the town before merging |
| `spiral_tightness` | float | 0.8-1.2 | 0.9-1.1 | seed angle step relative to the golden angle |
| `jitter` | float | 0-1 | 0.1-0.5 | seed displacement, fraction of the mean spacing |
| `lloyd` | int | 0-3 | 1-3 | Lloyd passes |
| `centre_bias` | float | 0.7-1.6 | 0.9-1.3 | above 1 packs smaller cells toward the centre |
| `anisotropy` | float | 1-1.6 | 1-1.3 | area-preserving stretch |
| `radius_frac` | float | 0.3-0.95 | 0.45-0.75 | mean town radius / half the shorter side, clamped to fit 10 m inside the map |
| `rotation` | float | 0-360 | 0-360 | degrees |
| `warp` | float | 0-0.2 | 0-0.06 | smooth sinusoidal domain warp amplitude, fraction of the town radius |
| `organic` | bool | | default false | effective jitter >= 0.9, lloyd <= 1, warp >= 0.12 (echoed params keep the raw values) |
| `gates` | int | 2-8 | 3-5 | gates in the wall |
| `plaza_frac` | float | 0.02-0.2 | 0.04-0.09 | minimum plaza area / town area |
| `merge_prob` | float | 0-0.5 | 0-0.25 | probability that a district merges with a neighbour |
| `citadel` | bool | | P(true) 0.5 | the wall district farthest from every gate becomes a citadel |
| `min_district_area` | float | 100-20000 | default 1600 | m2; smaller cells merge into the neighbour with the longest shared border |

Construction: seeds on a Vogel spiral (`r = ((i+0.5)/N)^(0.5*centre_bias)`, angle `i * golden * spiral_tightness`), jittered
(stage `seeds`; seed 0 stays on the origin), continued by ghost seeds and a far ring so every town cell is bounded, relaxed
by `lloyd` passes; the first `patches` cells are the town. Cells are normalised to unit mean radius, warped (stage `warp`),
rotated, scaled and rounded once per vertex so neighbours share bit-identical vertices. Cells that touch the plaza cell only at
a corner are dropped. Cell 0 absorbs neighbours until it reaches `plaza_frac`; cells under `min_district_area` merge; then each
district merges with a random neighbour with probability `merge_prob` (stage `merge`). District key `p<lowest cell index>`;
plaza `p0` (kind core). Kinds: nearest fifth market, wall districts of the outer quarter craft, citadel, else residential.
The wall is the exterior ring of the union, unsmoothed (so it lies exactly on district borders), thickness 8. Gates are wall
vertices nearest evenly spread angles (stage `gates`), at least 40 m apart, ids `<sid>_gate_<j>` by angle. Arteries are
Dijkstra routes over the district-border graph from each gate to the nearest vertex of the road network built so far, or, for
the first, to the plaza border followed by a straight link to the plaza centre; roads never overlap (`<sid>_artery_<j>`,
width 14, a gate whose vertex is already on the network gets no road of its own). Every road vertex is a movement-graph node
(`<sid>_j_p<a>p<b>p<c>` from the districts meeting there, numeric suffix on a collision), so flowgraph edges equal movement
edges. Arteries are not Chaikin-smoothed and there are no gate-to-gate arteries or landmark weights.
A layout that fails (unbounded cell, unmergeable sliver, holed or disconnected footprint) is redrawn from the next attempt
stream (`seeds1`, `merge1`, ...), up to 8 attempts, then `GenerationError`. A wall-less regime is not implemented.
Observed: the outermost cells are large and spiky at high jitter; `radial` is the tidier choice for compact walls.

### 7.3 Shared helpers for later generators

Slice 1 contains these in `radial` or `graph`; later slices move the generic parts out when a second user needs them:
`finalize(...)` (roads sorted, `movement_edges`, `attach_districts`, `district_graph`, build the Settlement), `planar.py`
(PSLG snapping, `shapely.polygonize` faces, ring simplification, closed-ring Chaikin), `outline.py` (footprint and wall outline
strategies: polar noise, hull, superellipse, union of patches, hand polygon), `gates.py` (gate selection with minimum angular and
metric separation, snapping to external approach points), `landmarks.py` (square, market, citadel placement rules).

## 8. Checks (`mapgen/town/check.py`)

`check(settlement) -> List[str]` (empty = pass), also run by `town generate` before it writes anything and by `town check`:

- every district polygon is valid; `area` matches the polygon; area at least `params.min_district_area`;
- districts do not overlap (shared area at most 1 m2) and their union equals the footprint within 1 %;
- the footprint is valid and lies inside the map;
- each wall is a closed simple ring; exactly one `outer` wall; at least 2 gates on it;
- each gate lies within 1 m of its wall and of its road, and has a unit normal;
- every district is reachable from `outside` in the district graph (wall edges crossable only with gates); every gate lies
  on a wall edge of that graph;
- the movement graph is connected, every gate is one of its nodes;
- feature ids are unique; graph node ids do not clash with district, road or wall ids; every road node exists.

`check_determinism(settlement)` regenerates from the settlement's own inputs and compares `to_json()` byte for byte, and checks the
JSON round trip. Tests run both over 20 seeds per generator (§13). Every later generator adds its invariants here (for water:
every wall opening is a gate or an `open_shore` range; every approach point lies within 30 m of a gate).

## 9. Mapping onto the map spec

### 9.1 Spec fragment (`emit_spec`, `town generate` writes `spec_fragment.yaml`)

Form A: an offline generator. The author pastes each list of the fragment under the same key of a spec. No spec schema
change. The text comes from a fixed template (numbers with at most 2 decimals), so the same settlement gives identical text.

| settlement | spec feature |
|---|---|
| district | `regions` entry `{ id: <sid>_d_<key>, seed, type: town, shape: poly, points, rough: 0, label }`; `rough: 0` and the default `warp: 0` keep the planned polygon |
| road | `lines` entry, type `road`, `smooth: true`, `width`; `path` holds point references for nodes and `[x, y]` for other vertices (ring roads) |
| wall | `lines` entry, type `wall` (`cliff` while `wall` is not in the type table), `width` = thickness, `path` = the closed polyline |
| gate | a `points` entry `<sid>_gate_<s>` and a `crossings` entry `{ id: <sid>_gate_<s>_x, type: gate, at: <sid>_gate_<s> }` (type `ramp` while `gate` is not in the type table); the crossing overrides the wall's impassable cost |
| junction | a `points` entry `<sid>_j_r<i>s<s>` |
| square, citadel, other landmarks | `pois` entry, type `landmark`, `r`, `label` (change to `keep_site` or `capture` to make an objective) |

`seed` of each region and line is `schema.feature_seed(id)`. Region and POI labels carry the district label, so the plaza
label appears twice on the map (district and landmark); authors drop one. District `kind` and `waterfront` travel in
`settlement.json` only; all districts use the `town` splat.

Contract additions this feature needs: line type `wall` (cost inf, `los: true`, splat `rock`, width 8; palette `#f2e6c8`, drawn as
a pale band on a dark casing with crenellation ticks) and crossing type `gate` (cost 0.6, `r` 12; palette `#ffd23f`, drawn with
an arch); both are separated from `cliff` and `ramp` by lightness and shape, and `python -m mapgen palette` passes with them.
`types.yaml` is echoed into every build's `export/spec.resolved.json` (`types`), so adding the two entries changes that file of
all eight committed maps: it is a contract change that needs an owner-approved re-baseline (`CLAUDE.md`: never re-baseline
without approval; show the diff report first). The complete prepared change (types, palette, `render.py` drawing of `wall` and
`gate`, doc rows) is `docs/missions/town-layout/wall-gate-types.patch`; apply it with `git apply`, update the hard-coded type
sets in `tests/test_spec_editor.py::test_schema_tables_are_read_from_schema_py_and_match_the_loader_messages`, build the eight
maps before and after for `mapgen diff`, and re-baseline on approval. Until then `emit_spec` falls back to `cliff` and `ramp`
(`emit_spec.wall_gate_types()` reads the type table), so fragments validate and build either way.

### 9.2 Flowgraph fragment (`emit_flow`, `flow_fragment.yaml`)

The movement graph as `nodes:` and `edges:` entries (node ids equal the spec feature ids, which is the default `bind`; `zone`
for squares and citadels, `choke` for gates and junctions; each edge's `name` is its road id). A flowgraph needs two spawn
nodes, which a settlement does not have, so this is a fragment: merge it into a `*.flow.yaml` that adds spawns and links
them to gates. Rule: the flowgraph edge derivation pairs consecutive resolved vertices of each `road` line (`FLOWGRAPH_SPEC.md`
§4); the emitted paths use point references for every node, so the derived edges are exactly the movement edges. Literal ring
vertices resolve to the nearest node within 30 m, which `min_ring_gap` (40 m) keeps from reaching a different ring. Citadels are
`landmark` POIs and so never count as extra nodes under `flowgraph check --strict`.

District nodes are not emitted (an artery runs on district borders, not through district centres); they stay in
`settlement.json`. A `flowgraph check` extension for district nodes is a candidate follow-up.

## 10. Preview (`mapgen/town/render.py`)

`render_png(settlement, px=900) -> PIL.Image`, `render_sheet([settlements], px=700)` (side by side). Colour-blind safe:
district kinds differ by grey level and pattern (core lightest; market dots; residential plain; craft hatch; harbour grid;
citadel dark crosshatch), roads are white on a dark casing, the wall is a dark band with a light core, gates are white
diamonds, the plaza a ring with a cross, each district shows its key. View: the settlement's footprint plus 12 % margin.

## 11. CLI (`mapgen/town/cli.py`, wired in `__main__.py`)

```
town generate <generator> [--seed N=1] [--size X Y=1000 1000] [--id SID=town] [--param NAME=VALUE ...] [--out DIR]
town check    <settlement.json>...
town preview  <settlement.json>... --out <png> [--force]
town list
```

`generate` resolves and builds, runs `check`, and writes `settlement.json`, `preview.png`, `spec_fragment.yaml` and
`flow_fragment.yaml` into `--out` (default `out/town_<id>_<generator>_<seed>`, files overwritten: `out/` is derived). It prints
`OK: ...` and exits 0. Exit 1: argument or parameter errors (all listed), `GenerationError`, a failing check. `check` runs
`check` plus `check_determinism` per file (`OK:` or `FAIL:` with the failures; exit 1 on any failure, a missing file or a
file that is not a settlement). `preview` refuses an existing `--out` without `--force`. `list` prints every generator with its
parameter table.

## 12. Module layout (`mapgen/town/`)

```
__init__.py     public API: generate, check, check_determinism, emit_spec, emit_flow, render_png, render_sheet, ...
model.py        dataclasses, to_dict/from_dict/to_json/from_json, rounding helpers
params.py       ParamDef, resolve, stage_rng, ParamError
graph.py        movement_edges, attach_districts, district_graph
check.py        check, check_determinism
registry.py     GENERATORS, generate
gen_radial.py   slice 1
gen_voronoi.py  slice 2
emit_spec.py    Settlement -> spec YAML fragment
emit_flow.py    Settlement -> flowgraph fragment
render.py       preview PNG
cli.py          `town` command group
(later)         planar.py, outline.py, gates.py, landmarks.py, env.py (water, fort), gen_grid.py, gen_bsp.py, gen_hybrid.py
```

## 13. Tests (`tests/test_town_*.py`)

Slice 1 (`tests/test_town_radial.py`): checks and determinism over 20 seeds; identical JSON for the same seed; different seeds
differ; parameter resolution, overrides do not shift other draws, bad arguments; JSON round trip; structure counts; each
check catches a deliberately broken settlement; the emitted fragment inside a full spec loads (`schema.load_spec_text`) and,
with two spawn nodes linked to gates, passes `flowgraph check --strict`; emitter determinism; preview sizes; CLI exit codes and
byte-identical output for the same seed. Every later generator repeats the 20-seed sweep, the determinism test and the
fragment round trip. Nothing under `maps/` or `tests/baselines/` changes; fragments become committed maps only after the owner
approves a baseline.

## 14. Slices (waves for the remaining generators)

Each slice is independently mergeable and adds a generator to the registry plus its tests; slices 2-4 depend only on slice 1.

2. **`voronoi`** (implemented, section 7.2; original design: Watabou style, also the organic regime). Seeds on an outward spiral, jittered, 1-3 Lloyd iterations
   (`scipy.spatial.Voronoi`, clipped with shapely); the N inner cells form the town, each cell (or a merge of small ones) a
   district `p<n>`; arteries are shortest paths (`networkx.astar_path`) over the Voronoi edge graph from each gate to the plaza and
   between gates, Chaikin-smoothed; the wall is the simplified, smoothed exterior ring of the union; gates are wall vertices at
   spread angles. Parameters: patch count 8-30, spiral tightness, jitter, Lloyd iterations, centre bias, anisotropy, wall on/off,
   gate count, plaza size, merge probability, landmark weights; "chaotic" = high jitter, one Lloyd pass, domain warp. Extracts
   `planar.py`, `outline.py`, `gates.py`, `landmarks.py` and `finalize` from `gen_radial`. Roads here run on district borders, so
   junction nodes sit at Voronoi vertices (ids `j_p<a>p<b>p<c>`).
3. **`grid`** (Manhattan). Rotated lattice with avenues every n cells and spacing jitter, super-block districts `x<i>y<j>`,
   optional diagonal boulevards from a market square, outline (superellipse, octagon, hull, polygon) clipping the lattice, an
   optional inner circular wall; gates at avenue and boulevard crossings. The L1 variant (nearest-centre labels under the L1 metric,
   vectorised with `skimage.measure.find_contours`) is a second step. Optional `bsp` generator (recursive splits, split depth
   decides road versus border) shares the outline code.
4. **Fort add-on** (`env.py`, acts on any generator's output): a reserved polygon (star bastion, rectangle with corner towers, wall-corner
   citadel) with placement modes `corner`, `centre`, `peninsula`, `island`; subtracted from the districts, its own `citadel` wall
   with an inward and an outward gate; kind `citadel` district and a `fortress` landmark. The `radial` citadel wedge becomes one
   placement.
5. **Water overlay** (`env.py`): a land mask (footprint with water holes and shore lines); river as a Catmull-Rom line through the
   town: districts clipped, slivers under 600 m2 dropped, each artery crossing emitted as a `road` crossing a `river` line (the
   automatic bridge of `MAPGEN_SPEC.md` §4.7), bridge nodes and `bridge` edges in the graphs; bay as a blob: `waterfront` districts, a
   harbour district and `docks` landmark, wall omitted on the shore (`open_shore`, optional `water_gate`); islands: a small
   settlement or fortress joined by a bridge edge or a graph-only `ferry` edge. Checks extend as in §8; `water` is echoed in the JSON.
6. **Organic and hybrid**. A road-first generator (hub points connected by weighted shortest paths over a cost grid, faces by
   `shapely.polygonize`) and composition (core partition A inside a disc, outskirts partition B in the annulus, core arteries
   continued to the outer wall; overlay of boulevards or a second wall). Terrain fit (nestle against a mountain, wrap a bay: footprint
   from slope, lattice aligned with contours) and a spec `settlements:` section (form B: expanded at load time, needs `MAPGEN_SPEC.md`
   and `EXPORT_FORMAT.md` changes and a re-baseline) come after.

## 15. Implementation notes

- Shared vertices: every coordinate is rounded at the point function, so adjacent districts, ring roads and the wall
  contain bit-identical vertices; adjacency, `roads` along borders and gate-on-wall tests rely on it.
- The elongated plaza polygon is an ellipse while the plaza landmark radius is `rp`; the landmark is a position and radius for
  downstream tools, not an outline.
- A spoke that starts at an outer ring (`spoke_dropout`) still owns junctions on every ring it crosses; an ungated spoke whose start
  equals the outermost ring has junction nodes but no spoke road.
