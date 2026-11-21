# Town organic shaping — warped outlines, open edges, mixed block fills

Status: design, nothing implemented; the warp stage is prototype-validated (§13). Self-contained apart from `TOWN_SPEC.md` (settlement model §3, params and seeds §4, graphs §5, checks §8,
emitters §9, preview §10, generators §7) and `TOWN_LAYERS_SPEC.md` (fill contract §4, `apply` §5, rules §6), which this file extends. Section 11
lists every API assumption verified against the code. A settlement generated without any parameter introduced here is unchanged byte for byte.

## 1. Purpose

Generated settlements read as plain: circle, rounded-rectangle or angular outlines, one orthogonal lattice, three straight spokes, one fill style per
town. Reference towns (Misthaven, Dusktown, a small village; `docs/missions/town-layout/refs/`) mix block shapes inside one town: irregular polygons,
rectangles, wedges and triangles, blocks that curve with a road, lattices at several angles; outlines follow terrain and water; a village has no
boundary at all and thins out into scattered buildings. This spec defines four capabilities, each usable alone:

1. **Shape field and outline** (§4): a scalar field whose contour is the settlement footprint (splotchy, tongues along roads, satellite lobes).
2. **Open edge** (§5): `edge: wall | open | fade`. `open` and `fade` towns have no wall; `fade` tapers into a fringe of scattered outskirt districts.
3. **Coordinate warp** (§6): one smooth, deterministic, topology-preserving displacement applied to the finished settlement. It curves every road,
   border, wall and outline, and gives blocks irregular edges.
4. **Mixed block fills** (§7): new layer fills `bsp`, `wedge`, `follow` and extensions of `grid` (`shear`, `fan`), plus per-district random rotation and
   a `layers=organic` preset, so one town holds many block shapes.

Out of scope: individual buildings, minor alleys, real terrain input (a hook is defined, §4.5), changes to the map spec schema or export bundle.

## 2. Prior art (summary; chosen approach in §3)

- **Watabou Medieval Fantasy City Generator** (Oleg Dolya): Voronoi "patches" around a centre, wall = ring of the patch union, streets = shortest paths
  gate to centre, wards of mixed kinds, lots by recursive cuts. Source of the patch/ward model that `voronoi` already follows; its outline is the patch
  union, which is why Voronoi edges look angular. Not adopted for outlines.
- **Parish and Mueller 2001 (CityEngine)**: L-system road growth, blocks as faces of the road graph, lots by recursive subdivision with an
  oriented-bounding-box cut perpendicular to the longest side and random ratio. Adopted for the `bsp` fill (§7.2).
- **Chen et al. 2008, tensor-field road networks**: roads as streamlines of a blended basis-field (grid, radial, boundary-following). Considered for roads;
  not adopted: it needs streamline tracing with separation control and does not give the exact shared-vertex face structure the checks and
  `layers` require. The coordinate warp (§6) gets the same visual result (bent grids) at a fraction of the cost.
- **Vanegas et al. 2012, inverse procedural modelling / Weber et al. interactive city growth**: lots as offset bands along a street and cross cuts. Adopted for
  the `follow` fill (§7.4).
- **Domain warping (Quilez)**: displace sample coordinates by low-frequency noise. Adopted as the warp stage (§6); the injectivity condition (§6.3) is the
  only added care.
- **Contour of a scalar field / metaballs**: standard for blob outlines with concavities; adopted for the footprint (§4).
Sources are cited from knowledge, not re-fetched in this session.

## 3. Architecture and decisions

Pipeline of `registry.generate` (today: generator, water, fort, layers) gains two points, marked `*`:

```
generator   radial | voronoi | grid | organic*      organic builds its own footprint from the shape field (§4, §8)
  -> water -> fort -> layers (fills: grid, radial, voronoi, bsp*, wedge*, follow*)
  -> warp*    densify every shared edge, displace every vertex by W, re-derive graphs, run the full `check` (§6)
```

Decisions (one per question):

| question | decision |
|---|---|
| Where does outline shape come from for new towns? | a scalar-field contour (§4), in the new `organic` generator; existing generators get their outline curved by the warp only |
| Existing Voronoi angular outlines | not patched; use `organic` + `voronoi` fills. `voronoi organic=true` is retired from showcases (Ashgrove failure) |
| How do roads and borders become curved? | one global warp `W` after layers, applied to every vertex of every geometry, never per feature |
| How is topology kept? | W is a pure function of coordinates (shared vertices stay shared), edges are densified canonically (same points both sides), and the result must pass the full `check` or the amplitude is reduced and retried |
| Block variety | more fills selected by weights per district (§7), not a meta-fill; Unfit falls through to the next weighted fill (§7.6) |
| Town with no boundary | `edge: open` (clean footprint, no wall, entries) and `edge: fade` (plus fringe districts of tapering density) (§5) |
| Real terrain | out of scope; the field takes an optional suitability callback (§4.5) |

New modules: `mapgen/town/shape.py` (field, contour, ribbons), `warp.py` (densify, displacement, apply), `gen_organic.py`, `fill_bsp.py`, `fill_wedge.py`,
`fill_follow.py`, `fringe.py`. Edited: `fill_grid.py` (new defaulted parameters), `layers.py` (fallback, `align random`), `layers_params.py` (`FILLS`, `USES`,
`organic` preset, widened `align_jitter`), `registry.py` (hook), `check.py` (§9), `graph.py` (open edges), `model.py` (§9.1), `emit_spec.py`, `emit_flow.py`,
`render.py`, `cli.py` (`town list`).

## 4. Shape field and outline (`shape.py`)

### 4.1 Field

For a hub `h` (map centre plus `hub_offset`) and base radius `R = radius_frac * min(size) / 2`, the **shape field** is

```
F(p) = w_core * g(|p - h| / R)
     + sum_k w_rib * g(dist(p, A_k) / rho_k) * taper_k(p)          # ribbons: tongues along artery k
     + sum_j w_lobe * g(|p - c_j| / r_j)                           # satellite lobes
     + w_noise * fbm2(p / lam_n, seed_n, octaves 3)                # noise.fbm2, lam_n = 0.6 R
     + w_suit * (S(p) - 0.5)                                        # optional suitability (§4.5)
     - tau
g(t) = smoothstep(1, 0, t)    (1 at t=0, 0 for t>=1)
```

`taper_k` falls linearly from 1 at the hub-side start to 0 at the artery tip, so a tongue narrows. Defaults: `w_core 1.0`, `w_rib 0.7`, `w_lobe 0.6`, `tau 0.45`;
`w_noise = irregularity` (0-0.5, drawn 0.15-0.4). Ribbon axes `A_k` are the generator's curved arteries (§8.3), `rho_k = 0.28 R * rng.uniform(0.7, 1.2)`.
Lobes: `lobes` (0-6, drawn 0-3) centres at distance `R * rng.uniform(0.7, 1.15)` from the hub at seeded angles, radius `R * rng.uniform(0.22, 0.4)`.

### 4.2 Contour to polygon

Rasterise F on a grid with cell `max(4 m, R / 80)` over the bounding box of its support (`noise.fbm2_grid` for the noise term), contour at 0 with
`skimage.measure.find_contours` (scikit-image is a pinned dependency), convert to map coordinates. Keep the component containing the hub as the footprint `P0`;
other components of area at least `min_district_area` become **satellite parcels** (`edge: fade` only: hamlet seeds, §5.3; otherwise dropped). Then, in order:
close small bays and open small spurs (`P0.buffer(c).buffer(-c)`, then `.buffer(-o).buffer(o)`, `c = 0.04 R`, `o = 0.05 R`, round joins), drop holes,
two Chaikin passes, `simplify(1.0)`, orient CCW, round with `model.r2`. The footprint is rejected (attempt redrawn from stream `shape1`, `shape2`, ...; up to 8, then
`GenerationError`) unless valid, inside the map by 10 m, area within 0.6-1.6 of `pi * R**2 * 0.55`, **solidity** (area / convex hull area) at least `min_solidity`
(default 0.62; 0.5 for `edge: fade`) and **neck width** at least 40 m (`P0.buffer(-20).buffer(20)` retains at least 97 % of the area). These two rejections are the
Ashgrove guard: no pinched middle, no thin isthmus.

### 4.3 Seeds

Stage streams (`params.stage_rng(sid, "organic", seed, stage)`): `params`, `shape`, `lobes`, `arteries`, `arcs`, `fringe`, `kinds`, `gates`, `warp`. Each owns its draws.

### 4.4 Outline kinds produced

Blob with concavities and lobes (default); tongue-extended (ribbons); multi-lobed archipelago-like (high `lobes`); near-circular when `irregularity` and `lobes` are 0
(still warped by §6). The superellipse of `grid` and the wall ring of `radial` remain the outlines of those generators; the warp curves them.

### 4.5 Terrain hook

`shape.field(..., suitability=None)`: an optional `Callable[[ndarray, ndarray], ndarray]` returning 0-1 (1 = buildable). `None` uses no term (`w_suit = 0`).
A later phase binds it to a heightmap; the generator interface does not change.

## 5. Open edge (no wall)

`edge` is an `organic` parameter (`wall` default, `open`, `fade`). It does not apply to the other generators.

### 5.1 `wall`

Outer wall = the footprint ring (kind `outer`, thickness 8), gates at artery ends (`<sid>_gate_a<k>`), as the other generators.

### 5.2 `open`

No wall. The footprint is the town's urban polygon; districts tile it exactly as before. Each artery that reaches the footprint ends at an **entry**: a `Gate`
with `wall: ""`, `kind: "entry"`, `at` the artery end (on the footprint ring), `normal` the outward direction, `road` the artery. Entries are movement-graph
`choke` nodes with id `<sid>_entry_a<k>`. `graph.district_graph` joins every district whose border lies on the footprint ring to `outside` by an
`adjacent` passable edge (§9.2).

### 5.3 `fade`

`open` plus a **fringe** (`fringe.py`) outside the footprint along the arteries and around satellite parcels:

- Fringe length `fade_len` (80-400 m, drawn 120-300) past the footprint along each artery; the artery continues to a tip entry `<sid>_entry_a<k>`.
- Fringe districts are kind `outskirt`, `inside_wall: false`, ragged cluster polygons (union of 2-5 lot rectangles flanking the road, buffered 3 m and re-simplified),
  area at least `min_district_area`, one road side lying on the artery so the road runs on a district border. Position `t` in [0, 1] along the fringe: existence
  probability `p(t) = (1 - t) ** 1.5 * (0.6 + 0.4 * n(t))`, depth `45 * (1 - 0.6 t)` m, gap between clusters `20 + 60 t` m, all from stream `fringe`.
- Hamlets: each satellite parcel (§4.2), up to `hamlets` (0-8), becomes 1-3 outskirt clusters at its position joined to the nearest artery by a `street` road.
- `District.density` (§9.1): core 1.0, `residential` 0.85, `outskirt` fringe `0.55 * (1 - t) + 0.1`. It is data for the downstream filler (buildings cropping up at
  the edge); no building geometry is generated.
- Fringe districts are excluded from the footprint coverage check (existing rule: only `inside_wall` districts), are not subdivided by layers (`skip_kinds` gains
  `outskirt`), and are never merged. They are connected only through the artery and hamlet streets; reachability is the ordinary movement and district graph checks.

### 5.4 Edge cases

An artery shorter than 60 m inside the footprint is dropped. `fade` with fewer than 2 entries is a `GenerationError` retry. `open`/`fade` with `fort` or `water`
add-ons work as on walled towns: the add-ons act on top-level districts and roads and never read the wall except through `s.walls` (absent = no wall work).

## 6. Coordinate warp (`warp.py`)

### 6.1 Parameter `warp`

Settlement-level parameter, split off in `registry.generate` like `layers` (`warp.is_warp_param`). Absent or `none`: nothing runs, `params` has no `warp` key.
Forms: a strength `warp=0.6` (0-1, preset below) or a dict/`k=v` list `{amp, wavelength, fine_amp, fine_wavelength, step, fort_damp}`.

| key | range | default at strength 1 | meaning |
|---|---|---|---|
| `amp` | 0-0.35 | 0.18 | large-scale displacement amplitude, fraction of the settlement radius `Rs` (footprint mean radius) |
| `wavelength` | 0.4-3 | 1.2 | large-scale feature size, multiples of `Rs` |
| `fine_amp` | 0-0.03 | 0.012 | small-scale amplitude, fraction of `Rs` (irregular block edges) |
| `fine_wavelength` | 0.05-0.4 | 0.15 | multiples of `Rs` |
| `step` | 4-30 m | 10 | densification step |
| `fort_damp` | 0-1 | 0.8 | fraction by which displacement is reduced inside and within 40 m of fort districts |

Strength `a` scales `amp` and `fine_amp` linearly from 0. Echoed resolved dict adds `scale` (the final amplitude multiplier after retries, §6.3) and `seed`.

### 6.2 Displacement

`W(p) = p + d(p)`, `d_x = amp * Rs * fbm2(p / (wavelength * Rs), s1, 3) + fine_amp * Rs * fbm2(p / (fine_wavelength * Rs), s1 + 11, 2)`; `d_y` the same with
offsets (+31.7, -12.3) and seed `s2`. `s1, s2` come from stage `warp` (`rng.randrange(1 << 30)`). `noise.fbm2` is hash-based and stateless (no global RNG), so
`W` is a deterministic pure function of `(x, y, params)`. The fort damping multiplies `d` by `1 - fort_damp * smoothstep(40, 0, dist(p, fort districts))`.

### 6.3 Procedure (`warp.apply(s, params) -> Settlement`)

1. **Densify** (endpoints copied exactly, never recomputed; only interior points are computed) every polyline and ring that must stay on shared borders: district polygons, layer-node polygons, footprint, road polylines, wall polylines, water
   polygons and river polylines. An edge `(a, b)` is split into `k = ceil(len / step)` equal parts computed from the **lexicographically smaller endpoint**, so the
   same border densified from either side yields the same points. Run after `layers._stitch_all`'s result (T-junctions already shared), i.e. on the final
   Settlement; every border then has identical vertex lists on both sides.
2. **Displace** every vertex by `W`, scaled by `scale`; round with `model.r2` once. Identical input coordinates give identical output (shared vertices stay shared).
   Gates, landmarks and graph nodes are displaced by the same `W`; a gate `normal` is recomputed as the outward normal of the displaced wall (or footprint) at the gate;
   landmark `r` is unchanged.
3. **Rebuild derived data**: `District.area` from the polygon; `Road.nodes` unchanged (nodes sit on polyline vertices, which survive); `graph_edges` via
   `graph.movement_edges(roads, nodes)` (lengths change); `district_nodes/edges` via `graph.district_graph(districts, roads, walls, gates)`;
   `graph.attach_districts`.
4. **Accept or retry**: run `check.check(s)`. Any error (invalid or overlapping polygon, area below minimum, footprint outside the map, a road leaving its border) multiplies
   `scale` by 0.75 and repeats from the unwarped settlement, up to 6 times; then the settlement is returned unwarped with `scale: 0` echoed (never an error). Injectivity is
   not assumed, it is verified: any fold shows up as an invalid or overlapping polygon. The safe bound for no fold is `amp * 2*pi / wavelength < 0.8` (default 0.18 * 6.28 / 1.2 = 0.94 is
   deliberately at the edge; the retry handles the rest).
5. Echo `params["warp"]`; `check_determinism` regenerates from it (the final `scale` is a fixed point: regenerating with `scale` fixed gives the same file).

### 6.4 Invariants preserved

Districts still tile the footprint (W is a homeomorphism on the plane, verified by `check`); roads still lie on district borders; walls on borders; every gate within 1 m
of its wall and road; node ids, district ids, road ids and layer ids unchanged (identity never depends on coordinates); `graph.district_graph` edges unchanged as a set.
Straight segments become polylines with vertices every `step` m; polylines of both a road and the border it runs on contain identical vertices.

### 6.5 Emission consequences

`emit_spec.road_path` writes literal vertices for polyline points that are not nodes; with densification that is a vertex every `step` m, and a literal coordinate
within 30 m of a foreign node would resolve to it in `flowgraph` edge derivation (`FLOWGRAPH_SPEC.md` §4). Therefore: before emission, each road is simplified between its nodes
with Douglas-Peucker (tolerance 0.8 m), and a literal vertex whose nearest bound node within 30 m is not a node of the same road is dropped (only when dropping keeps the
simplified line within 3 m of the full one; otherwise the vertex is replaced by the nearest own-road node reference that preserves order, which cannot add an edge). The
region polygons emit the simplified rings (tolerance 0.5 m). Proof obligation: `flowgraph check --strict` passes on every showcase map (§12, W3).

## 7. Mixed block fills

All fills follow the contract of `TOWN_LAYERS_SPEC.md` §4 (`fill(P, rng, p, frame, min_area) -> Fill`, `SUB_DEFS`, `Unfit`, shared tail `layerfill.finish`, faces tile `P`, roads on
face borders, no dead ends). New `USES` entries: `bsp`, `wedge`, `follow`. New parameters have `default` values that reproduce today's output, so existing `layers` specs are unchanged.

### 7.1 Grid extensions (`fill_grid.py`)

| param | range | default | meaning |
|---|---|---|---|
| `shear` | 0-40 deg | 0 | the second family's direction is rotated by `shear` degrees from perpendicular: parallelogram blocks, non-90-degree corners |
| `fan` | 0-12 deg per line | 0 | line `k` of the first family is additionally rotated about the lattice origin by `fan * k * rng.uniform(0.7, 1.3)` degrees, keeping lines non-crossing inside `P` (a fan of converging streets) |

At `shear = 0` and `fan = 0` no extra random draw happens and the lattice is bit-identical. Key function `x<i>y<j>` uses the sheared coordinates (inverse of the basis).

### 7.2 `bsp` fill (`fill_bsp.py`): irregular polygon blocks (Parish and Mueller)

`P` is split recursively: take the face's minimum-area oriented bounding box, cut with a line **perpendicular to its longest side** at ratio `rng.uniform(0.35, 0.65)` and tilted by
`rng.uniform(-skew, skew)` degrees; recurse until a face is below `target` (`target = max(min_area * 1.6, area(P) / cells)`) or deeper than `max_depth_bsp`. Cuts at depth
at most `road_depth` (default 1, range 0-3) are `street` roads (depth 0 may be `artery` when `main_road: true`); deeper cuts are unroaded borders. Parameters: `cells`
(3-24, drawn 4-10), `skew` (0-25 deg, drawn 3-14), `road_depth` (0-3, default 1), `ratio_jitter` (0-0.3, default 0.15, narrows the cut ratio), `main_road` (bool, default true).
Face key `b<path>` where path is the 0/1 split sequence (`b0`, `b01`, `b110`); stable under unrelated changes. Yields trapezoids, triangles near non-convex corners and varied sizes.
Roads: each road-depth cut ends on `P.boundary` or on a shallower cut, so no dead ends; `finish` enforces the contract.

### 7.3 `wedge` fill (`fill_wedge.py`): fans and triangles

Apex `a` is the boundary vertex of `P` with the smallest interior angle if below 100 degrees, else the point `0.35 * diameter` outside `P` on the perpendicular bisector of the
reference edge (frame rot), pushed until the rays reach `P`. `spokes` (2-9, drawn 3-6) rays from `a`, angles spread over the angular extent of `P` as seen from `a`, with jitter
`angle_jitter` (0-0.4 of spacing, drawn 0-0.25); class `street`, the central one `artery` when `spokes >= 4`. Optional cross streets: `cross` (0-2, default 1) arcs at fractions of the
ray length (`0.45`, `0.75`), each broken at random (p 0.25 per sector) so blocks differ. Faces `w<ray>c<band>`. Unlike `radial` the hub need not lie inside `P` and `P` need not be star-shaped
about it (`Unfit` only when fewer than 2 rays enter `P`).

### 7.4 `follow` fill (`fill_follow.py`): blocks along a curving road

Reference curve `C`: the longest chain of `P`'s boundary edges lying on a road (`layers._ref_rot` already finds the reference edge; the chain extends it over collinear-adjacent
road edges); if no road touches `P`, the longest boundary chain. For `bands` (1-2, default 1) offsets `D_i = depth * i` (`depth` 25-60 m, drawn 30-48) take `LineString.offset_curve` of `C`
toward `P`'s interior (shapely 2.0.7, pinned; a left/right sign test picks the interior side), clip to `P`, and make each a `street` (only the innermost band edge; the road `C` is the parent's).
The strip between `C` and the first offset is cut by cross lines normal to `C` every `lot_w` (14-40 m, drawn 18-30, jitter 0.2) into lot faces `l<i>`; cross lines are borders only (alleys), not roads.
The remainder of `P` beyond the last offset is one face `core` (a later depth pass, or a rule, may subdivide it with `bsp` or `grid`). `Unfit` when the strip is shorter than 40 m or the offset
self-intersects (the curve is simplified with tolerance 2 m and retried once at `depth * 0.7`). Result: crescent and curved blocks following a road bend, as in the references.

### 7.5 Orientation: per-district rotation and skew

`align` gains `random`: rotation `rp.uniform(0, 90)` degrees from the `pick` stream (a district-local draw, independent of siblings). `align_jitter` range widens from 0-20 to 0-45 degrees. The
rule key `align: edge` keeps blocks flush with the parent border; `random` plus `shear` gives the several-angles-in-one-town look. Neighbouring districts of one parent get different rotations
because every district draws from its own `pick` stream.

### 7.6 Fallback and the `organic` preset

- `layers.py` `_subdivide`: when the chosen fill raises `Unfit`, try the remaining weighted uses in descending weight (ties by name) before recording `skipped`. Same seeds, so
  deterministic. Spec `use` weights unchanged in format.
- `layers=organic` (also `--param layers=organic`) expands to the canonical form:

```yaml
layers:
  max_depth: 2
  min_split_area: 3000
  align: edge
  rules:
    - {depth: 0, kind: [market],                      use: {grid: 2, bsp: 1},                      align_jitter: 10}
    - {depth: 0, kind: [residential, craft, slum],    use: {bsp: 3, grid: 2, follow: 2, wedge: 1, voronoi: 1},
       params_random: true, align_jitter: 25, p: 0.95}      # grid shear/fan drawn per district (below)
    - {depth: 1, p: 0.3, use: {bsp: 2, voronoi: 1}}
```

`params_random: true` makes `shear` (drawn 0-30), `fan` (drawn 0-6) of a chosen `grid` and `align: random` with probability 0.5 per district draw from stage `params`. Skip kinds gain `outskirt`.

## 8. The `organic` generator (`gen_organic.py`)

A top-level generator (registry name `organic`) whose footprint is the shape field contour (§4) and whose macro districts are the faces of a few curved arteries and arcs.

### 8.1 Parameters (`python -m mapgen town list` prints the live table)

| name | type | valid | drawn | meaning |
|---|---|---|---|---|
| `radius_frac` | float | 0.3-0.95 | 0.4-0.65 | base radius `R` / half the shorter side |
| `edge` | str | wall, open, fade | default wall | §5 |
| `irregularity` | float | 0-0.5 | 0.15-0.4 | noise weight of the field |
| `lobes` | int | 0-6 | 0-3 | satellite lobes |
| `ribbons` | int | 0-4 | 0-2 | arteries that grow a tongue |
| `arteries` | int | 3-7 | 3-5 | curved arteries hub to footprint |
| `curvature` | float | 0-1 | 0.3-0.8 | lateral bend of arteries (fraction of a third of the length) |
| `arcs` | int | 0-3 | 0-2 | partial cross arcs between adjacent arteries |
| `plaza_frac` | float | 0.05-0.2 | 0.07-0.12 | plaza radius / `R` (minimum 25 m) |
| `hub_offset` | float | 0-0.3 | 0-0.15 | hub distance from the map centre / `R` |
| `fade_len`, `hamlets` | float, int | 80-400, 0-8 | 120-300, 1-5 | §5.3 |
| `min_solidity` | float | 0.4-0.9 | default 0.62 | §4.2 |
| `min_district_area` | float | 100-20000 | default 1600 | m2 |
| `gates` | int | 2-8 | 3-5 | entries or gates, at most `arteries` |
| `citadel` | bool | | P(true) 0.3 | the macro district farthest from the entries becomes a citadel (as `radial`) |

### 8.2 Hub and plaza

The plaza is a noise-perturbed 24-gon of radius `rp` at the hub (district key `plaza`, kind `core`, landmark `<sid>_plaza`, as `radial`).

### 8.3 Arteries

`arteries` angles from `theta_i = base + i * 2 pi / n + jitter` (jitter ±0.3 of spacing, at least 38 degrees apart, stage `arteries`), plus a 1-2 waypoint **Catmull-Rom** curve
(`water._catmull(ctrl, per=10)`) from the plaza ring through waypoints displaced laterally by `curvature * L / 3 * rng.uniform(-1, 1)` to beyond the footprint. A `ribbon` artery
lengthens the tongue term of the field (§4.1) so the footprint reaches further along it. Arteries are clipped to `P0` (and, for `fade`, extended `fade_len` beyond). Class `artery`, width 14; class `ring` for
arcs (width 10): an arc at radius `R * rng.uniform(0.45, 0.7)` over a bend-noised polar curve between two adjacent arteries, ends on them.

### 8.4 Districts, kinds, nodes

Faces: `polygonize(unary_union([P0.boundary] + arteries + arcs + plaza ring))`, face representative point inside `P0`; faces under `min_district_area` merge into the neighbour with the longest border
(`layerfill._merge_small` promoted to public `merge_small`). Keys `a<first artery index>s<sector>` (like `radial`), plaza `plaza`. Kinds: `market` touching the plaza, `craft` touching the footprint with
the largest area share of `P0`'s boundary, else `residential`. Nodes: plaza, junctions `<sid>_j_<a>_<b>` (artery or arc keys), gates or entries, arc ends; every polyline vertex of an artery/arc at a
junction is a node, so the spec fragment holds point references only at nodes (`graph.movement_edges` rule, `TOWN_SPEC.md` §5). Roads ids `<sid>_artery_<k>`, `<sid>_arc_<k>`. Walls and gates per §5.
A layout failing any check redraws from the next attempt stream (up to 8), then `GenerationError`.

## 9. Model, checks, graphs

### 9.1 Model (`model.py`; `format_version` stays 1, new fields omitted from JSON while default)

`District.density: float = 1.0`. `Gate.kind` gains `entry` (`Gate.wall == ""`). `Road.cls` unchanged. `Settlement.params["warp"]`, `Settlement.params["edge"]` echo (§6, §8).

### 9.2 Graphs (`graph.py`)

`district_graph(districts, roads, walls, gates, footprint=None)`: when `walls` has no `outer` wall and `footprint` is given, every district whose boundary overlaps the footprint ring by more than
`BORDER_MIN` is joined to `outside` by an `adjacent` passable edge (`roads` lists roads running along it, `gates` lists entries within `SNAP`). With an `outer` wall nothing changes. Callers in
`layers._district_graph` and `warp.apply` pass `s.footprint`. Fringe districts: they lie outside the footprint, so they reach `outside` by an `adjacent` edge when they share more than `BORDER_MIN`
with no other district (always), and to the core districts through the artery road only (movement graph).

### 9.3 Checks (`check.py`)

- `_walls_gates`: a gate with `wall == ""` must have `kind == "entry"`, lie within `SNAP` of its road and within 1 m of the footprint ring or (fade) within 1 m of the road end; a town with no `outer`
  wall has at least 2 entries; the "fewer than 2 gates" rule stays for walled towns.
- `_reachability`: unchanged logic; it now holds for wall-less towns because of §9.2.
- new `_organic` (runs when `params.get("generator") == organic` or `params["warp"]` present): footprint solidity at least the echoed `min_solidity` (default 0.5 when warped, 0.62 else);
  neck width 40 m (§4.2); no district larger than 6 times the median district area unless kind `plaza`/`citadel`; every road polyline simple (no self-intersection), with turn angle at most 100 degrees per
  vertex at `step` spacing (a fold detector); every fringe district `inside_wall` false, kind `outskirt`, with a road on its border; `density` in (0, 1].
- `_geometry` minimum-area test: with `params["warp"]` present it compares against `min_district_area * 0.7` (the warp changes areas by up to about 25 %; §13 finding 2).
- Existing checks (§8 of `TOWN_SPEC.md`, `_layers`) run unchanged on the warped result; that is the acceptance criterion of the warp.

## 10. Showcase maps and tests

### 10.1 New and revised maps (`maps/towns/build.py` entries, `README.md` table)

| map | generator and params | demonstrates |
|---|---|---|
| `thornwick` | `organic`, `edge=fade`, `lobes=3`, `ribbons=2`, `warp=0.7`, `layers=organic`, 1000 m | boundary-less sprawl: tapering fringe, hamlets, mixed fills, no wall |
| `mistmoor` | `organic`, `edge=open`, `warp=0.8`, `layers=organic`, `water=river` | un-walled, curved footprint cut by a river |
| `dusk` | `organic`, `edge=wall`, `lobes=2`, `ribbons=1`, `warp=0.6`, `layers=organic` | splotchy walled outline, blocks following roads |
| `wardring` (revised) | existing radial + layers, plus `warp=0.5` | the layered favourite, curved |
| `hearthmoor`, `swordmarch` (revised) | grid + `layers=organic` + `warp=0.6` (blocks re-filled at several angles; main lattice bent) | grid towns that are no longer uniform |
| `ashgrove` | replaced by `organic` seed chosen by scan (no pinch) | the failure case fixed |

Seeds are chosen by scanning (as the README records for twinbridge): the first seed passing `check`, `flowgraph check --strict` and the visual criteria of §12.

### 10.2 Tests (`tests/test_town_organic_*.py`, shared helpers in `tests/layers_helpers.py`)

- Shape: 30 seeds: footprint valid, inside the map, solidity and neck width hold; determinism; `lobes=0, irregularity=0` is near-circular (solidity above 0.95).
- Warp: invariants of §6.4 over `radial`, `grid`, `voronoi` x `layers` x `water` x `fort`, 20 seeds each: `check` and `check_determinism` pass; the districts' ids, road ids, node ids equal the unwarped ones;
  tiling error under 1 %; every shared border has identical vertex lists in both districts; `scale` echoed; with `warp` absent output byte-identical to today.
- Fills: each new fill on `layers_helpers.polygons()` (30 convex, L shape, wedge): `check_fill_contract`, determinism, rounded coordinates; `bsp` yields at least 3 distinct face vertex counts over the set; `wedge` and
  `follow` raise `Unfit` (not errors) on unsuitable shapes; grid `shear=0, fan=0` equals the current fill byte for byte; `shear=30` gives non-90-degree corners (assert max interior-angle deviation).
- Layers: `organic` preset 20 seeds x 3 generators: all five fills occur across the sweep; fallback on `Unfit` is deterministic; identity stability (adding a rule for another kind changes nothing else).
- Open edge: `organic` `edge=open|fade` 20 seeds: no wall, at least 2 entries, `check` passes, every district reachable from `outside`, fringe districts satisfy §5.3, density decreases with distance
  (monotone mean per distance bin).
- Emission: warped and wall-less settlements round-trip `schema.load_spec_text`; with two spawns linked to entries `flowgraph check --strict` passes; emitter determinism.
- Preview: renders; `edge=fade` fringe drawn (lighter grey with density-scaled pattern, never hue).
- CLI: `town generate organic --param edge=fade --param warp=0.7 --param layers=organic` exit 0; bad `edge`/`warp` values exit 1 with every error.

## 11. Verified against the code

- Pipeline order and param split: `registry.generate` (water, fort, layers separated by `is_water_param`, `is_fort_param`, `layers_params.is_layer_param`; layers last: `layers.apply(s, spec)`). A `warp` hook is one more split plus a call after `layers.apply`.
- Seeds: `params.stage_rng(sid, generator, seed, stage)` (sha256 stream per stage); `params.resolve` draws defaulted-less params in `DEFS` order; params with `default` are never drawn, so new defaulted parameters shift no draw.
- Noise: `mapgen/noise.py` `fbm2`, `fbm2_grid`, `value_noise2` are stateless hash noise (numpy), deterministic, vectorised. `fbm1_periodic` exists for closed curves.
- Fill API: `layerfill.Fill/SubRoad/Frame/Unfit`, `finish(P, pieces, face_key, min_area)` (polygonize, merge small faces via private `_merge_small`, drop buried pieces and stubs, round), `FILLS` and `USES` in `layers_params.py`, `fill_grid.fill` structure (lines via `line_pieces`, key function, `finish`).
- Layer application: `layers._subdivide` per-district streams `/pick`, `/geom`, kinds; `Unfit` becomes `skipped`; `_ref_rot` reference edge; `_stitch_all` guarantees shared vertices; `_finish` rebuilds nodes and `_district_graph`.
- Derived-data functions are public in `graph.py`: `movement_edges(roads, nodes)`, `attach_districts(nodes, districts)`, `district_graph(districts, roads, walls, gates)`.
- Checks: `check._geometry` requires footprint valid, inside the map, union of `inside_wall` districts within 1 % of footprint (fringe districts with `inside_wall=False` are excluded), area at least `min_district_area` (all districts; hence fringe clusters are at least that); `_walls_gates` only demands an outer wall and 2 gates `if s.walls`; a gate's `wall` must be a known wall id (hence `entry` relaxation);
  `_reachability` seeds from the `outside` node, which has edges only through `graph.district_graph`'s wall edges today (a wall-less settlement fails it: §9.2 is required).
- Emission: `emit_spec.road_path` writes node ids where a polyline vertex is a node, else coordinates; `FLOWGRAPH_SPEC.md` §4: a literal coordinate resolves to the nearest bound node within 30 m (hence §6.5).
- Dependencies: `scikit-image==0.21.0` (`find_contours`), `shapely==2.0.7` (`offset_curve`, `polygonize`, `voronoi_diagram`), numpy, scipy are pinned in `requirements.txt`; Python 3.8 rules apply.
- `water._catmull(ctrl, per)` is a Catmull-Rom helper reusable for artery curves.
- Prototype (scratch script, not committed): see §13 (the warp is validated; shape field, generator and fills are not).

## 12. Implementation plan (waves; slices inside a wave are independent)

Each slice names its evidence. The full suite (`pytest -n 8`, about 9 minutes) runs between waves; nothing under `tests/baselines/` changes; showcase maps are not in the regression suite.

**Wave W0 (spec validation; done in this spec, §13).**

**Wave W1 (no mutual dependencies; parallel):**

| slice | content | files | evidence |
|---|---|---|---|
| W1a warp | `warp.py` (§6), registry hook, `warp` param parse/validate/echo, `town list` entry, `check` unchanged, emission simplification (§6.5) | `warp.py`, `registry.py`, `cli.py`, `emit_spec.py`, `tests/test_town_warp.py` | tests of §10.2 Warp pass; `layers=grid warp=0.6` previews of radial, grid, voronoi in `out/town_organic/warp_*.png` show curved roads and borders with `check` OK; `flowgraph check --strict` on `hearthmoor` rebuilt with `warp=0.6` passes |
| W1b fills | `fill_bsp.py`, `fill_wedge.py`, `fill_follow.py`, grid `shear`/`fan`, `USES`/`FILLS`, `align random`, widened `align_jitter`, `Unfit` fallback | `fill_*.py`, `layers.py`, `layers_params.py`, `tests/test_town_fills_organic.py` | fill-contract tests on 30 convex plus L and wedge polygons; contact sheet `out/town_organic/fills.png` (each fill on 6 polygons) |
| W1c shape | `shape.py` field, contour, rejection guards, `noise` usage, standalone `town shape` debug command or script | `shape.py`, `tests/test_town_shape.py` | 30-seed sweep sheet `out/town_organic/outlines.png` (splotchy, lobed, tongued, no pinch); tests green |

**Wave W2 (after W1c and W1b):**

| slice | content | files | evidence |
|---|---|---|---|
| W2a generator | `gen_organic.py` (§8), registry entry, `edge=wall`, model fields, `check` gate relaxation and `_organic`, `graph.district_graph` open edges | `gen_organic.py`, `model.py`, `check.py`, `graph.py`, `registry.py`, `tests/test_town_organic.py` | 20-seed sweeps `edge=wall` and `open`: `check`, `check_determinism`, fragment round trip, `flowgraph check --strict` |
| W2b preset | `layers=organic` expansion, `params_random` | `layers_params.py`, tests | identity-stability and all-fills-occur tests |

**Wave W3 (after W2a):**

| slice | content | files | evidence |
|---|---|---|---|
| W3a fringe | `fringe.py`, `edge=fade`, `District.density`, hamlets, preview of density | `fringe.py`, `gen_organic.py`, `render.py`, tests | fade sweeps; density test; preview `thornwick_town.png` showing tapering fringe |
| W3b emit and entries | `emit_spec`/`emit_flow` entries (points, no gate crossing), `build.py` team bases from `entry` gates | `emit_spec.py`, `emit_flow.py`, `maps/towns/build.py` | `flowgraph check --strict` on a wall-less map |

**Wave W4 (showcase and owner review):** add and revise the maps of §10.1 in `maps/towns/build.py`, `src/*.base.yaml` terrain, README table; render `maps/towns/preview/*.png` and `out/<map>/map.png`;
acceptance (§12.1); owner reviews the contact sheet. Nothing is baselined.

### 12.1 Acceptance (rendered)

`thornwick`, `mistmoor`, `dusk` previews: no outline edge longer than 25 percent of the perimeter is straight; at least 3 distinct block shapes (quad, triangle, 5+-gon) and 2 lattice rotations at least
15 degrees apart present; `thornwick` has no wall and at least 8 fringe clusters; no district larger than 6 times the median; every map passes `town check`, `validate`, `flowgraph check --strict`.

## 13. Prototype result (scratch, not committed)

A scratch script (a 40-line `densify + fbm2 displacement` of a finished Settlement, then `graph.movement_edges`, `graph.district_graph`, `check`) ran on `radial`, `grid`, `voronoi` with
`layers=grid`, seeds 1-3, amplitudes 12 m / wavelength 120 m, 30 / 250 and 45 / 250 (27 runs, 1000 m map). Findings applied above:

1. No topology failure in any run: no invalid or overlapping district, no footprint-coverage error, no road or reachability error. Shared vertices, tiling and the district graph survive the warp.
2. The only recurring failure (26 of 27 runs, 1-6 districts each) was `area below min_district_area`: the warp shrinks small districts (1600-1700 m2 before) by 2-25 %. Retrying with a smaller amplitude does not
   fix districts that start near the minimum, so the checks apply an **area floor**: with `params["warp"]` present the minimum-area test uses `min_district_area * 0.7` (§9.3). The retry loop (§6.3) still guards
   everything else.
3. Voronoi runs 1 and 3 failed `gate town_gate_0 lies on no district-graph wall edge`. Cause: the scratch densifier recomputed segment endpoints in floating point (`p + (q - p) * k / k`), so a gate
   vertex could move by an ulp and, after rounding, off the wall vertex. The spec therefore requires densification to emit the **original endpoints exactly** and interior points only (§6.3 step 1), and a
   regression test that every gate `at` equals a wall vertex after the warp (§10.2).
4. Not prototyped: the shape field, contour and `organic` generator, the new fills, the wall-less checks. Their first tasks (W1b, W1c, W2a) are to validate §4, §7 and §8 on random polygons and amend this file.

## 14. Migration

Nothing existing changes unless a new parameter is given. Edits to existing files add defaulted fields and branches: `District.density` and `Gate.kind entry` (omitted/unused when absent), `fill_grid` params
`shear`, `fan` (default 0, no extra draws), `align_jitter` range, `Unfit` fallback (a district that was a leaf because its only fill was `Unfit` stays a leaf when its `use` has one entry). The showcase
rebuilds of `hearthmoor`, `swordmarch`, `wardring` change those maps' generated files by design; the regression suite does not cover them. `voronoi organic=true` remains available and documented as the
chaotic regime with large outer cells; `ashgrove` is rebuilt from `organic`. `TOWN_SPEC.md` §7, §12 and §14 gain pointers to this file in the slice that lands each part.

## 15. Open decisions for the owner

1. Wall-less towns use `Gate.kind=entry` with no wall; `build.py` team bases attach to entries. Accept, or prefer a separate `portals` list.
2. Fringe is `outskirt` districts (at least 1 600 m2 each) with a `density` value, not individual plots. Accept, or allow a smaller minimum area for outskirt districts (relaxes the area check for that kind).
3. Warp strength defaults (`0.6-0.8` in showcases): review the contact sheet in W4.
4. Whether to retire `voronoi organic=true` and the `voronoi` generator for new towns, keeping it for compatibility.
