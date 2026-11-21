# Town organic shaping — warped outlines, open edges, mixed block fills

Status: design; the warp stage (§6) is implemented (`mapgen/town/warp.py`, `tests/test_town_warp.py`), the shape field and outline (§4) are implemented (`mapgen/town/shape.py`, `tests/test_town_shape.py`, sheet `python -m mapgen.town.shape_sheet` or `town shape`), the `organic` generator with `edge=wall|open` (§5.1, §5.2, §8, §9; `gen_organic.py`, `tests/test_town_organic.py`) is implemented, the `layers=organic` preset and `params_random` (§7.6, `tests/test_town_organic_preset.py`) and the named presets with `parent_use` (§7.7, `tests/test_town_organic_combos.py`) are implemented, everything else (`edge=fade`, fringe) is design. Self-contained apart from `TOWN_SPEC.md` (settlement model §3, params and seeds §4, graphs §5, checks §8,
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
| Existing Voronoi angular outlines | not patched; use `organic` + `voronoi` fills. The `voronoi` generator has no organic regime |
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
     + w_noise * fbm2(p / lam_n, seed_n, octaves 3)                # noise.fbm2_grid, lam_n = 0.5 R
     + w_suit * (S(p) - 0.5)                                        # optional suitability (§4.5)
     - tau
g(t) = smoothstep(1, 0, t)    (1 at t=0, 0 for t>=1)
```

`taper_k` falls linearly from 1 at the hub-side start to 0 at the artery tip, so a tongue narrows. Defaults (tuned on a 30-seed sweep): `w_core 1.0`, `w_rib 0.95`, `w_lobe 0.7`, `tau 0.32`;
`w_noise = 1.5 * irregularity` (`irregularity` 0-0.5, drawn 0.15-0.4). Ribbon axes `A_k` are the generator's curved arteries (§8.3); standalone, `shape.footprint` draws bent axes of length `R * uniform(1.5, 2.1)`. `rho_k = 0.34 R * rng.uniform(0.7, 1.2)`.
Lobes: `lobes` (0-6, drawn 0-3) centres at distance `R * rng.uniform(0.7, 1.15)` from the hub at seeded angles, radius `R * rng.uniform(0.22, 0.4)`.
The ribbon distance term uses a KD-tree over samples of the axis every `rho/10`. Public API: `shape.footprint(size, seed, sid, params, edge, axes, suitability, generator) -> Shape` (`ring, satellites, hub, R, axes, lobes, params, attempt, solidity, area`), `shape.field_grid`, `shape.solidity`, `shape.neck_ok`.

### 4.2 Contour to polygon

Rasterise F on a grid with cell `max(4 m, R / 80)` over the bounding box of its support (`noise.fbm2_grid` for the noise term), contour at 0 with
`skimage.measure.find_contours` (scikit-image is a pinned dependency), convert to map coordinates. Keep the component containing the hub as the footprint `P0`;
other components of area at least `min_district_area` become **satellite parcels** (`edge: fade` only: hamlet seeds, §5.3; otherwise dropped). Then, in order:
close small bays and open small spurs (`P0.buffer(c).buffer(-c)`, then `.buffer(-o).buffer(o)`, `c = 0.04 R`, `o = 0.05 R`, round joins), drop holes,
two Chaikin passes, `simplify(1.0)`, orient CCW, round with `model.r2`. The footprint is rejected (attempt redrawn from stream `shape1`, `shape2`, ...; up to 8, then
`GenerationError`) unless valid, inside the map by 10 m, area within 0.6-1.6 of `pi * R**2 * 0.55` (the tuned field lands near the middle of that band), **solidity** (area / convex hull area) at least `min_solidity`
(default 0.62; 0.5 for `edge: fade`) and **neck width** at least 40 m (`P0.buffer(-20).buffer(20)` retains at least 97 % of the area and leaves at most one component of at least 5 % of the area; the second test catches a thin isthmus between two large bodies, which costs little area). These two rejections are the
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

`open` plus a **fringe** (`fringe.py`, `plan(...)`, stream `fringe`) outside the footprint. The parameters `fade_len` and `hamlets` exist only with `edge=fade`.

- Extension: each artery continues past the footprint ring along its end tangent as a gently bending polyline (10 m vertices, length `fade_len * U(0.85, 1)`), cut where it leaves the map
  (14 m margin) or comes back into the footprint, or within 40 m of another artery. The artery is then **truncated at the end of its farthest cluster** (or at the ring when it has no
  cluster), so the tip entry `<sid>_entry_a<k>` lies on a fringe district corner.
- Clusters (kind `outskirt`, `inside_wall: false`, keys `f<k>_<n>`): walking each road side from the ring outward, position `t = s / fade_len`, a cluster is placed with probability
  `p(t) = (1 - t) ** 1.5 * (0.6 + 0.4 u)`, `u ~ U(0, 1)`; depth `60 * (1 - 0.6 t)` m; gap to the next cluster `20 + 60 t` m (+ up to 15 m). A cluster is a ragged polygon: its border runs along the road
  polyline vertices and the other side is a staircase of 2-3-step lots of depth 0.65-1.25 times the nominal, each offset point jittered by 1.2 m; it is cut by the footprint (buffered 3 m), the map margin and
  earlier clusters, kept only when its largest part has area at least `1.12 * min_district_area` (the warp changes areas by up to 25 %) and no foreign road crosses it. A road runs on every cluster border.
- Hamlets (`hamlets`, 0-8): a side street (class `street`, width 8, id `<sid>_street_h<m>`, nodes `<sid>_j_a<k>_h<m>` on the artery and `<sid>_h<m>_end`) leaves an artery vertex that has no cluster on its side, at about
  90 degrees, 140-240 m long, at least 35 m from the other arteries and 10 m from the footprint, and is truncated at the end of its farthest cluster. It carries 1-2 clusters (keys `h<m>_<n>`) on its two sides, density from the junction's `t` (`0.5 t_j + 0.1` start).
  The satellite parcels of the shape (§4.2) are not used for hamlets.
- `District.density` (§9.1), set only with `edge=fade`: `core` and `market` 1.0, `craft` 0.95, `citadel` 0.9, `residential` 0.85, `outskirt` `0.55 * (1 - t) + 0.1` with `t` the cluster's position
  along the fringe in [0, 1] (clamped). It is data for the downstream filler (buildings cropping up at the edge); no building geometry is generated. The preview draws an outskirt lighter and with sparser dots the lower its density.
- Fringe districts are excluded from the footprint coverage check (only `inside_wall` districts count), are not subdivided by layers (`skip_kinds` gains `outskirt`), are never merged and are excluded from the
  "6 times the median" rule. They are connected only through the artery and hamlet streets; reachability is the ordinary movement and district graph checks.

### 5.4 Edge cases

An artery shorter than 60 m inside the footprint is dropped. `fade` with fewer than 2 entries is a `GenerationError` retry. `open`/`fade` with `fort` or `water`
add-ons work as on walled towns: the add-ons act on top-level districts and roads and never read the wall except through `s.walls` (absent = no wall work).

## 6. Coordinate warp (`warp.py`)

### 6.1 Parameter `warp`

Settlement-level parameter, split off in `registry.generate` like `layers` (`warp.is_warp_param`). Absent or `none`: nothing runs, `params` has no `warp` key.
Forms: a strength `warp=0.6` (0-1, preset below; 0 is none) or a dict / `k=v` list `{amp, wavelength, fine_amp, fine_wavelength, step, fort_damp}`
(missing keys take the strength-1 default). The dict also accepts the echoed `scale` and `seed` (below). Unknown keys and out-of-range values are `ParamError`s listing every problem.

| key | range | default at strength 1 | meaning |
|---|---|---|---|
| `amp` | 0-0.35 | 0.18 | large-scale displacement amplitude, fraction of the settlement radius `Rs` (footprint mean radius) |
| `wavelength` | 0.4-3 | 1.2 | large-scale feature size, multiples of `Rs` |
| `fine_amp` | 0-0.03 | 0.012 | small-scale amplitude, fraction of `Rs` (irregular block edges) |
| `fine_wavelength` | 0.05-0.4 | 0.15 | multiples of `Rs` |
| `step` | 4-30 m | 10 | densification step |
| `fort_damp` | 0-1 | 0.8 | fraction by which displacement is reduced inside and within 40 m of fort districts (districts of kind `citadel`) |

Strength `a` scales `amp` and `fine_amp` linearly from 0 (rounded to 6 decimals). The echoed dict in `params["warp"]` holds the six keys plus `scale` (the final amplitude
multiplier after retries, §6.3) and `seed` (`s1`, below). A dict that carries `scale` is applied at exactly that scale, without retries: this is what makes regeneration from the echo a fixed point.

**Outer boundary vs interior.** The `warp` displacement above is the *interior warp* (roads, block borders). For every generator except `organic` the outer
boundary (footprint, wall, towers, gates on it) is pinned against it: the interior displacement is multiplied by `smoothstep(dist(p, footprint boundary) / (0.18 * Rs))`, so the outline of a simple shape
is not wiggled at small scale. The `organic` generator, whose outline is already a shape-field contour, keeps the unmasked displacement. The separate parameter `edge_warp`
(split off like `warp`, `warp.merge_edge`) adds an *edge displacement* with a large wavelength: a strength 0-1 (`edge_amp = 0.25 * strength`, `edge_wavelength = 0.75`) or `amp=..,wavelength=..`
(`edge_amp` 0-0.3 of `Rs`, `edge_wavelength` 0.4-1.5 radii). `edge_warp=0` pins the boundary for any generator including `organic`. The edge displacement is `edge_amp * Rs * fbm2(p / (edge_wavelength * Rs), s1 + 101, 2)` (y with
offsets and seed `s2 + 101`), weighted by `smoothstep(1 - dist(p, boundary) / (0.6 * Rs))` so it fades into the interior. Without `warp` the parameter does nothing. The keys `edge_amp`, `edge_wavelength` are echoed in `params["warp"]` when `edge_warp` was given.
Radial seeds 3 and 7: `edge_warp=0.6` bends the outline into a plausible irregular wall; `1.0` is lumpy but still valid; `0` (default) leaves a clean ellipse.

### 6.2 Displacement

`W(p) = p + d(p)`, `d_x = amp * Rs * fbm2(p / (wavelength * Rs), s1, 3) + fine_amp * Rs * fbm2(p / (fine_wavelength * Rs), s1 + 11, 2)`; `d_y` the same with
offsets (+31.7, -12.3) and seed `s2`. `s1` is `seed`, drawn from stage `warp` (`rng.randrange(1 << 30)`) unless given; `s2 = (s1 * 2654435761 + 40503) mod 2^30`. `noise.fbm2` is hash-based and stateless (no global RNG), so
`W` is a deterministic pure function of `(x, y, params)`. The fort damping multiplies `d` by `1 - fort_damp * smoothstep(40, 0, dist(p, fort districts))`.

### 6.3 Procedure (`warp.apply(s, params) -> Settlement`)

1. **Weld, stitch, densify** every polyline and ring that must stay on shared borders: district polygons, layer-node polygons, footprint, road polylines, wall polylines (and their `open_shore` vertex
   index ranges, remapped), water polygons and river polylines. Generators leave vertices a few hundredths of a metre apart and borders whose neighbour carries an extra vertex, so first
   (a) **weld**: vertices within 0.03 m become one (the lexicographically smallest of the cluster; two graph-node positions are never merged), consecutive repeats dropped; then
   (b) **stitch**: every vertex of any geometry (and every graph node and gate position) lying within 0.02 m of an edge is inserted into that edge, so both sides of a border list the same vertices; then
   (c) **densify** (endpoints copied exactly, never recomputed; only interior points are computed). An edge `(a, b)` is split into `k = ceil(len / step)` equal parts computed from the **lexicographically smaller endpoint**, so the
   same border densified from either side yields the same points. Run after `layers._stitch_all`'s result (T-junctions already shared), i.e. on the final
   Settlement; every border then has identical vertex lists on both sides.
2. **Displace** every vertex by `W`, scaled by `scale`; round with `model.r2` once. Identical input coordinates give identical output (shared vertices stay shared).
   Gates, landmarks and graph nodes are displaced by the same `W`; a gate `normal` is recomputed as the outward normal of the displaced wall (or footprint) at the gate;
   landmark `r` is unchanged.
3. **Rebuild derived data**: `District.area` from the polygon; `Road.nodes` unchanged (nodes sit on polyline vertices, which survive); `graph_edges` via
   `graph.movement_edges(roads, nodes)` (lengths change); `district_nodes/edges` via `graph.district_graph(districts, roads, walls, gates)`;
   `graph.attach_districts`.
4. **Accept or retry**: a candidate is rejected when `check.check(s)` returns any error or raises a topology exception (invalid or overlapping polygon, area below minimum, footprint outside the map, a road leaving its border),
   or when a district adjacency longer than 3 m of the unwarped settlement is missing from the warped district graph. A rejection multiplies
   `scale` by 0.75 and repeats from the unwarped settlement, up to 6 times; then the settlement is returned unwarped with `scale: 0` echoed (never an error). Injectivity is
   not assumed, it is verified: any fold shows up as an invalid or overlapping polygon. The safe bound for no fold is `amp * 2*pi / wavelength < 0.8` (default 0.18 * 6.28 / 1.2 = 0.94 is
   deliberately at the edge; the retry handles the rest).
5. Echo `params["warp"]`; `check_determinism` regenerates from it (the final `scale` is a fixed point: regenerating with `scale` fixed gives the same file).

### 6.4 Invariants preserved

Districts still tile the footprint (W is a homeomorphism on the plane, verified by `check`); roads still lie on district borders; walls on borders; every gate within 1 m
of its wall and road; node ids, district ids, road ids and layer ids unchanged (identity never depends on coordinates); `graph.district_graph` loses no edge longer than 3 m (the stitch step can add adjacencies that the generators' unstitched borders hid).
Straight segments become polylines with vertices every `step` m; polylines of both a road and the border it runs on contain identical vertices.

### 6.5 Emission consequences

`emit_spec.road_path` writes literal vertices for polyline points that are not nodes; with densification that is a vertex every `step` m, and a literal coordinate
within 30 m of a foreign node would resolve to it in `flowgraph` edge derivation (`FLOWGRAPH_SPEC.md` §4). Therefore, for a settlement with `params["warp"]` only (every other settlement emits as before): each road is simplified between its nodes (or between its ends where it has none)
with Douglas-Peucker (tolerance 0.8 m), and a literal vertex whose nearest bound node within 30 m is not a node of the same road is dropped (only when dropping keeps the
simplified line within 3 m of the full one; otherwise the vertex is replaced by the nearest own-road node reference that preserves order, which cannot add an edge). The
region polygons emit the simplified rings (tolerance 0.5 m). `emit_spec.road_tokens(s, road)` returns the emitted tokens with the position each stands for; `road_path` is its token list and
`emit_flow` derives the layered edge list from it (a layered fragment lists the edges the emitted lines derive, so it must follow the simplified path, not the full polyline). Proof obligation: `flowgraph check --strict` passes on every showcase map (§12, W3).

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
  max_depth: 3
  min_split_area: 3000
  align: edge
  rules:
    - {depth: 0, kind: [market],                      use: {grid: 2, bsp: 1},                      align_jitter: 10}
    - {depth: 0, kind: [residential, craft, slum],    use: {bsp: 3, grid: 2, follow: 2, wedge: 2, voronoi: 1},
       params_random: true, align_jitter: 25, p: 0.97}      # grid shear/fan drawn per district (below)
    - {depth: [1, 2], area: [6500, 1000000],          use: {bsp: 3, wedge: 1, follow: 2, grid: 1}, params_random: true, align_jitter: 25}
    - {depth: 1, p: 0.35, use: {bsp: 2, voronoi: 1, wedge: 1}}
```

The area rule (no `p`) is the size cap: any district still above 6500 m2 after a pass is subdivided again (depth 1-2), so over 20 seeds no district exceeds 3 times the median (acceptance: 6 times).
`layers` as the string `organic` is expanded by `layers_params.preset`; the echoed `params["layers"]` is the resolved dict, which regenerates identically. `params_random` is a rule key (bool, default false).

`params_random: true` makes `shear` (drawn 0-30), `fan` (drawn 0-6) of a chosen `grid` (unless the rule's `params` set them) and the rotation (uniform 0-90 degrees, with probability 0.5 per district, replacing the `align` result) draw from the per-district stream `<district id>/params`; a rule without the flag draws nothing from it. Skip kinds gain `outskirt`.

### 7.7 Named presets and `parent_use`

Rule key `parent_use` (list of fill names, `radial/wedge` in the string form): the rule matches a depth >= 1 district only when the layer node that produced it used one of
those fills. This is how a level picks its layout by the parent's layout (radial x bsp, voronoi x grid, grid x radial, ...); without it a rule sees only kind, area, key, depth.
Resolved rules echo `parent_use` (sorted list or null).

`layers=<preset>` accepts these names (`layers_params.PRESETS`; `organic` is §7.6; `python -m mapgen town list` prints them). All have `max_depth: 3`, `min_split_area: 3000`,
`align: edge`, every depth-0 macro district (kinds market, residential, craft, slum) split with probability 1 and `params_random`, and two rules per `parent_use` parent fill at
depth 1: districts above 6500 m2 always split (weights of the table), smaller ones stay leaves with probability 0.1-0.3 (the weight `none`). A last depth 1-2 area rule
(above 6500 m2: bsp 2, voronoi 2, grid 2, radial 1, wedge 1) caps district size; the depth-0 and depth-1 uses fall back to the other weighted uses when `Unfit` (§7.6).

| preset | depth 0 | depth 1 by parent | depth 2 |
|---|---|---|---|
| `organic-radial` | radial 6, wedge 1, voronoi 1 | radial -> grid 3, bsp 2, voronoi 1, follow 1; wedge -> grid, voronoi, bsp; voronoi -> grid, radial, bsp | size cap |
| `organic-voronoi` | voronoi 6, wedge 1, radial 1 | voronoi -> grid 3, bsp, radial, wedge; radial -> voronoi, grid, bsp; bsp -> voronoi, grid | size cap |
| `organic-grid` | grid 6, radial 1, voronoi 1 | grid -> radial 3, voronoi 3, wedge 2, bsp 1; radial -> voronoi, grid, bsp; voronoi -> grid, bsp | size cap |
| `organic-cells` | voronoi 4, grid 1, radial 1 | voronoi -> radial 3, wedge 2, bsp, grid; grid -> voronoi, radial; radial -> voronoi, bsp | radial/wedge parents: grid or bsp (p 0.5), size cap |
| `organic-mixed` | radial 3, voronoi 3, grid 2, wedge 2, bsp 1 | one mix per parent fill (radial, voronoi, grid, wedge, bsp, follow), 3-level chains | any: bsp, grid, voronoi, radial (p 0.4), size cap |

Acceptance (`tests/test_town_organic_combos.py`, 20 seeds per preset, `warp=0.6`, `edge` alternating wall and open): `check` and `check_determinism` pass; the expected
(depth, fill) pairs occur (e.g. `organic-mixed`: all of radial, voronoi, grid, wedge at depth 0); at least 4 distinct (parent fill, child fill) pairs; leaves of bsp parents are at most
35 % of all depth >= 1 leaves; no district (kinds other than core, citadel, plaza) above 6 times the median area; `flowgraph check --strict` passes on seeds 1-6.
Contact sheets: `python -m mapgen.town.combo_sheet [preset ...]` writes `out/town_organic/combo_<preset>.png` (6 seeds, odd seeds walled, even open).

Non-convex organic districts: radial raises `Unfit` when not star-shaped about the hub, wedge and voronoi when they yield fewer than 2 faces; the fallback tries the other weighted uses
of the rule, and a district every use is unfit for stays a leaf listed in `layers.skipped`.

## 8. The `organic` generator (`gen_organic.py`)

A top-level generator (registry name `organic`) whose footprint is the shape field contour (§4) and whose macro districts are the faces of a few curved arteries and arcs.

### 8.1 Parameters (`python -m mapgen town list` prints the live table)

| name | type | valid | drawn | meaning |
|---|---|---|---|---|
| `radius_frac` | float | 0.3-0.95 | 0.4-0.65 | base radius `R` / half the shorter side |
| `edge` | str | wall, open, fade | default wall | §5; split off in `registry.generate`, echoed in `params["edge"]` |
| `irregularity` | float | 0-0.5 | 0.15-0.4 | noise weight of the field |
| `lobes` | int | 0-6 | 0-3 | satellite lobes |
| `ribbons` | int | 0-4 | 0-2 | arteries that grow a tongue |
| `arteries` | int | 3-7 | 4-6 | curved arteries hub to footprint |
| `curvature` | float | 0-1 | 0.55-1.0 | lateral bend of arteries (S-bends and sweeps; peak offset `curvature * L / 2.2 * U(0.35, 1)`) |
| `arcs` | int | 0-3 | 1-3 | partial cross arcs between adjacent arteries |
| `plaza_frac` | float | 0.05-0.2 | 0.07-0.12 | plaza radius / `R` (minimum 25 m) |
| `hub_offset` | float | 0-0.3 | 0-0.15 | hub distance from the map centre / `R` |
| `fade_len`, `hamlets` | float, int | 80-400, 0-8 | 200-400, 2-6 | §5.3; present only with `edge=fade` |
| `min_solidity` | float | 0.4-0.9 | default 0.62 | §4.2 |
| `min_district_area` | float | 100-20000 | default 1600 | m2 |
| `citadel` | bool | | P(true) 0.3 | the macro district farthest from the entries becomes a citadel (as `radial`) |

### 8.2 Hub and plaza

The plaza is a noise-perturbed 24-gon of radius `rp` at the hub (district key `plaza`, kind `core`, landmark `<sid>_plaza`, as `radial`).

### 8.3 Arteries

The `arteries` count includes the ribbon arteries. The first `ribbons` arteries are the shape's own axes (`Shape.axes`, hub-to-tip Catmull-Rom curves, extended along their tangent past the tip) so the footprint tongue and the road coincide. The rest take
angles `theta_i = base + i * 2 pi / n + jitter` (jitter ±0.3 of spacing, stage `arteries`), skipping any angle less than 38 degrees from an accepted one, each a 1-2 waypoint **Catmull-Rom** curve
(`water._catmull(ctrl, per=8)`) from the hub through waypoints displaced laterally by `curvature * L / 3 * rng.uniform(-1, 1)` to beyond the footprint. Every artery polyline starts at the hub (the plaza node), is clipped at its first exit from `P0`, and
is dropped when shorter than 60 m; the plaza ring crossing and the footprint end are inserted as vertices. Class `artery`, width 14; class `ring` for arcs (width 10): an arc at radius `R * rng.uniform(0.45, 0.7)` between two adjacent arteries
(angular gap at most 150 degrees) on a polar curve whose radius is interpolated between the two end radii plus a sine bend; it is kept only when simple, inside the footprint by 8 m, at least 3 m from every artery but its own two and every other arc, meeting each
of its two arteries exactly once, with no turn above 60 degrees. `edge=fade` (§5.3) additionally extends arteries `fade_len` beyond the footprint.

### 8.4 Districts, kinds, nodes

Faces: `polygonize(unary_union([P0 ring, plaza ring] + artery parts outside the plaza + arcs))`, face representative point inside `P0`; the faces must tile `P0` (0.5 %). A face under `min_district_area` is not merged (a merge would bury a road in a district interior): the layout drops arcs one at a time, then redraws the attempt. Keys `a<k>s<m>`: `k` the artery just clockwise of the face, `m` the rank of the face by distance from the hub within that sector; plaza `plaza`. Kinds: `market` touching the plaza, `craft` touching the footprint with
the largest area share of `P0`'s boundary, else `residential`. Nodes: plaza, junctions `<sid>_j_<a>_<b>` (artery or arc keys), gates or entries, arc ends; every polyline vertex of an artery/arc at a
junction is a node, so the spec fragment holds point references only at nodes (`graph.movement_edges` rule, `TOWN_SPEC.md` §5). Roads ids `<sid>_artery_<k>`, `<sid>_arc_<k>`. Walls and gates per §5.
A layout failing any check redraws from the next attempt stream (up to 8; the shape itself is drawn once by `shape.footprint`), then `GenerationError`. A gate is `<sid>_gate_a<k>` (`edge=wall`, wall `<sid>_wall_outer`, `Gate.kind` gate) or `<sid>_entry_a<k>` (`edge=open`); its graph node has the same id.

## 9. Model, checks, graphs

### 9.1 Model (`model.py`; `format_version` stays 1, new fields omitted from JSON while default)

`District.density: float = 1.0`. `Gate.kind` gains `entry` (`Gate.wall == ""`). `Road.cls` unchanged. `Settlement.params["warp"]`, `Settlement.params["edge"]` echo (§6, §8).

### 9.2 Graphs (`graph.py`)

`district_graph(districts, roads, walls, gates, footprint=None)`: when `walls` has no `outer` wall and `footprint` is given, every district whose boundary overlaps the footprint ring by more than
`BORDER_MIN` is joined to `outside` by an `adjacent` passable edge (`roads` lists roads running along it, `gates` lists entries within `SNAP`). With an `outer` wall nothing changes, and the open-edge branch runs only when some gate has `wall == ""` (so existing wall-less settlements are untouched). `footprint` defaults to the outline of the union of `inside_wall` districts, so callers that do not pass it (`layers._district_graph`, `water`, `warp._rebuild`) get the same result unless water carves the footprint. Callers in
`layers._district_graph` and `warp.apply` pass `s.footprint`. Fringe districts (`inside_wall` false): each is joined to `outside` by an `adjacent` passable edge whose border is its boundary not shared with another district (the whole boundary when that is at most `BORDER_MIN`);
the edge lists the roads on the district boundary and the entries within `SNAP` (a tip entry lies on its last cluster). They reach the core only through the artery road (movement graph).

### 9.3 Checks (`check.py`)

- `_walls_gates`: a gate with `wall == ""` must have `kind == "entry"`, lie within `SNAP` of its road and within 1 m of the footprint ring or (fade) within 1 m of the road end; a town with no `outer`
  wall has at least 2 entries; the "fewer than 2 gates" rule stays for walled towns.
- `_reachability`: unchanged logic; it now holds for wall-less towns because of §9.2.
- new `_organic` (runs when the settlement's generator is `organic`): footprint solidity at least the echoed `min_solidity` (default 0.5 when warped, 0.62 else);
  neck width 40 m (§4.2); no district larger than 6 times the median district area unless kind `plaza`/`citadel`; the district-area rule only without layers; every generator-made road polyline (layer sub-roads excluded) simple (no self-intersection), with turn angle at most 100 degrees per
  vertex (a fold detector); neck width 30 m instead of 40 when warped; every fringe district `inside_wall` false, kind `outskirt`, with a road on its border; `density` in (0, 1].
- `_geometry` minimum-area test: with `params["warp"]` present it compares against `min_district_area * 0.7` (the warp changes areas by up to about 25 %; §13 finding 2).
- Existing checks (§8 of `TOWN_SPEC.md`, `_layers`) run unchanged on the warped result; that is the acceptance criterion of the warp.

## 10. Showcase maps and tests

### 10.1 New and revised maps (`maps/towns/build.py` entries, `README.md` table)

| map | generator and params | demonstrates |
|---|---|---|
| `thornwick` | `organic`, `edge=fade` (built as `edge=open` until `fringe.py` lands), `lobes=3`, `ribbons=2`, `warp=0.7`, `layers=organic`, 1000 m | boundary-less sprawl: tapering fringe, hamlets, mixed fills, no wall |
| `mistmoor` | `organic`, `edge=open`, `warp=0.8`, `layers=organic`, `water=river` | un-walled, curved footprint cut by a river |
| `dusk` | `organic`, `edge=wall`, `lobes=2`, `ribbons=1`, `warp=0.6`, `layers=organic` | splotchy walled outline, blocks following roads |
| `wardring` (revised) | existing radial + layers, plus `warp=0.5` | the layered favourite, curved |
| `hearthmoor`, `swordmarch` (revised) | grid + `layers=organic` + `warp=0.6` (blocks re-filled at several angles; main lattice bent) | grid towns that are no longer uniform |
| `ashgrove` | `organic`, `edge=wall`, `warp=0.6`, `layers=organic`, seed 21, 900 m (no pinch) | splotchy walled outline |

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
- Shape (W1c, verified on real code): `noise.fbm2_grid(xs, ys, seed, octaves)` returns shape `(len(ys), len(xs))`; `skimage.measure.find_contours` returns `(row, col)` and needs a padded outside border to close contours; `GenerationError` lives in `gen_radial.py`; contour hole rings are discarded by keeping outermost components. Over 30 seeds on a 1000 m map every seed passes within 3 attempts (26 at attempt 0).
- Noise: `mapgen/noise.py` `fbm2`, `fbm2_grid`, `value_noise2` are stateless hash noise (numpy), deterministic, vectorised. `fbm1_periodic` exists for closed curves.
- Fill API: `layerfill.Fill/SubRoad/Frame/Unfit`, `finish(P, pieces, face_key, min_area)` (polygonize, merge small faces via private `_merge_small`, drop buried pieces and stubs, round), `FILLS` and `USES` in `layers_params.py`, `fill_grid.fill` structure (lines via `line_pieces`, key function, `finish`).
- Layer application: `layers._subdivide` per-district streams `/pick`, `/geom`, kinds; `Unfit` becomes `skipped`; `_ref_rot` reference edge; `_stitch_all` guarantees shared vertices; `_finish` rebuilds nodes and `_district_graph`.
- Derived-data functions are public in `graph.py`: `movement_edges(roads, nodes)`, `attach_districts(nodes, districts)`, `district_graph(districts, roads, walls, gates)`.
- Checks: `check._geometry` requires footprint valid, inside the map, union of `inside_wall` districts within 1 % of footprint (fringe districts with `inside_wall=False` are excluded), area at least `min_district_area` (all districts; hence fringe clusters are at least that); `_walls_gates` only demands an outer wall and 2 gates `if s.walls`; a gate's `wall` must be a known wall id (hence `entry` relaxation);
  `_reachability` seeds from the `outside` node, which has edges only through `graph.district_graph`'s wall edges today (a wall-less settlement fails it: §9.2 is required).
- Emission: `emit_spec.road_path` writes node ids where a polyline vertex is a node, else coordinates; `FLOWGRAPH_SPEC.md` §4: a literal coordinate resolves to the nearest bound node within 30 m (hence §6.5).
- Parameter names: the `voronoi` generator's smooth cell warp is the parameter `cell_warp`; `warp` is only the settlement-level parameter of §6 (a generator parameter of that name would be echoed into `params` and read as the settlement warp).
- Dependencies: `scikit-image==0.21.0` (`find_contours`), `shapely==2.0.7` (`offset_curve`, `polygonize`, `voronoi_diagram`), numpy, scipy are pinned in `requirements.txt`; Python 3.8 rules apply.
- `water._catmull(ctrl, per)` is a Catmull-Rom helper reusable for artery curves.
- W2a (verified on real code): `shape.footprint` takes no hub argument (the hub is drawn inside it), so the generator reads `Shape.hub` and `Shape.axes` from a standalone call and adopts the axes as its ribbon arteries; `shape.footprint(..., generator="organic")` keeps the generator's stream names. `params.ParamDef` has no string type, so `edge` is split off in `registry.generate` and appended to the resolved params. Faces: polygonize of rounded, pre-noded lines gives exact shared vertices (the only inserted vertices are the plaza ring crossings, footprint ends and arc ends, each inserted into every line through it). Arteries run through the plaza (hub to ring) as `radial` spokes do; only the part outside the plaza is polygonized, else the plaza splits into wedges. `emit_spec` writes no `gate` crossing for an entry (a point only); `flowgraph check --strict` passes on `edge=open` with two spawns linked to entries (the full W3b emission work, team bases in `build.py`, remains).
- Prototype (scratch script, not committed): see §13 (the warp is validated; shape field, generator and fills are not).

## 12. Implementation plan (waves; slices inside a wave are independent)

Each slice names its evidence. The full suite (`pytest -n 8`, about 9 minutes) runs between waves; nothing under `tests/baselines/` changes; showcase maps are not in the regression suite.

**Wave W0 (spec validation; done in this spec, §13).**

**Wave W1 (no mutual dependencies; parallel):**

| slice | content | files | evidence |
|---|---|---|---|
| W1a warp (done) | `warp.py` (§6), registry hook, `warp` param parse/validate/echo, `town list` entry, `check` area floor (§9.3), emission simplification (§6.5) | `warp.py`, `registry.py`, `cli.py`, `emit_spec.py`, `emit_flow.py`, `check.py`, `gen_voronoi.py` (`cell_warp`), `tests/test_town_warp.py` | tests of §10.2 Warp pass; `layers=grid warp=0.6` previews of radial, grid, voronoi in `out/town_organic/warp_*.png` show curved roads and borders with `check` OK; `flowgraph check --strict` on `hearthmoor` rebuilt with `warp=0.6` passes |
| W1b fills | `fill_bsp.py`, `fill_wedge.py`, `fill_follow.py`, grid `shear`/`fan`, `USES`/`FILLS`, `align random`, widened `align_jitter`, `Unfit` fallback | `fill_*.py`, `layers.py`, `layers_params.py`, `tests/test_town_fills_organic.py` | fill-contract tests on 30 convex plus L and wedge polygons; contact sheet `out/town_organic/fills.png` (each fill on 6 polygons) |
| W1c shape (done) | `shape.py` field, contour, rejection guards, `noise` usage, standalone `python -m mapgen.town.shape_sheet` script (a `town shape` CLI hook waits for W2a, which edits `cli.py`) | `shape.py`, `shape_sheet.py`, `tests/test_town_shape.py` | 30-seed sweep sheet `out/town_organic/outlines.png` (splotchy, lobed, tongued, no pinch); tests green |

**Wave W2 (after W1c and W1b):**

| slice | content | files | evidence |
|---|---|---|---|
| W2a generator (done) | `gen_organic.py` (§8), registry entry, `edge=wall`, model fields, `check` gate relaxation and `_organic`, `graph.district_graph` open edges | `gen_organic.py`, `model.py`, `check.py`, `graph.py`, `registry.py`, `tests/test_town_organic.py` | 20-seed sweeps `edge=wall` and `open`: `check`, `check_determinism`, fragment round trip, `flowgraph check --strict` |
| W2b preset (done) | `layers=organic` expansion, `params_random`, variety tuning (arteries 4-6, arcs 1-3, curvature 0.55-1, S-bend arteries, area-driven re-subdivision) | `layers_params.py`, `layers.py`, `gen_organic.py`, `tests/test_town_organic_preset.py` | identity-stability, all-fills-occur, variety-metric tests; contact sheet `out/town_organic/preset_sheet.png` |

**Wave W3 (after W2a):**

| slice | content | files | evidence |
|---|---|---|---|
| W3a fringe (done) | `fringe.py`, `edge=fade`, `District.density`, hamlets, preview of density | `fringe.py`, `gen_organic.py`, `render.py`, tests | fade sweeps; density test; preview `thornwick_town.png` showing tapering fringe |
| W3b emit and entries (done) | `emit_spec`/`emit_flow` entries (a `points` entry and a `choke` node named "Entry <k>"; no `gate` crossing, no wall), `build.py` team bases from `entry` gates (base at least 140 m out: a base's flowgraph reach is 120 m, nearer it binds the town's own road vertices; entries with no such room are not used), showcase `thornwick` (`edge=open`, warp 0.7, seed 3) | `emit_spec.py`, `emit_flow.py`, `maps/towns/build.py`, `maps/towns/src/thornwick.base.yaml`, `tests/test_town_entries_build.py` | `validate` and `flowgraph check --strict` on `thornwick` pass |

**Wave W4 (showcase and owner review):** add and revise the maps of §10.1 in `maps/towns/build.py`, `src/*.base.yaml` terrain, README table; render `maps/towns/preview/*.png` and `out/<map>/map.png`;
acceptance (§12.1); owner reviews the contact sheet. Nothing is baselined.

### 12.1 Acceptance (rendered)

Block shape classes are counted per district by corners (turning angle at least 35 degrees after `simplify(2.5)`): triangle 3, quad 4, 5+-gon; a class is present with at least 2 districts. Lattice rotation = a layer node's `rot` modulo 90. Over 20 `organic` seeds with `layers=organic warp=0.9` all 20 have 3 classes and 2 rotations 15 degrees apart. `thornwick`, `mistmoor`, `dusk` previews: no outline edge longer than 25 percent of the perimeter is straight; at least 3 distinct block shapes (quad, triangle, 5+-gon) and 2 lattice rotations at least
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
rebuilds of `hearthmoor`, `swordmarch`, `wardring` change those maps' generated files by design; the regression suite does not cover them. The `voronoi` generator's own smooth cell warp parameter is `cell_warp`, so `warp` names only the settlement-level stage (§6). The `voronoi` organic regime (`organic=true`) is removed; `ashgrove` is rebuilt from `organic`. `TOWN_SPEC.md` §7, §12 and §14 gain pointers to this file in the slice that lands each part.

## 15. Open decisions for the owner

1. Wall-less towns use `Gate.kind=entry` with no wall; `build.py` team bases attach to entries. DECIDED (owner): accepted, no `portals` list.
2. Fringe is `outskirt` districts with a `density` value, not individual plots. DECIDED (owner): outskirt districts have a minimum of 1 600 m2.
3. Warp strength defaults (`0.6-0.8` in showcases): review the contact sheet in W4.
4. `voronoi organic=true`. DECIDED (owner): removed (code, tests, docs); the `voronoi` generator itself stays. Backward compatibility is not kept.
