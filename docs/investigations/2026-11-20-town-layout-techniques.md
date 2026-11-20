# Town layout techniques for mapgen (GDU-57)

Date: 2026-11-20. Read-only investigation. Evidence base: the mission file and its seven reference maps
(`docs/missions/town-layout/`), `MAPGEN_SPEC.md` (§4.2 regions, §4.3 lines, §4.4 crossings, §4.5 POIs, §4.7
automatic crossings, §4.8 identity), `mapgen/FLOWGRAPH_SPEC.md` (§1-4 concepts, node binding, edge derivation),
`mapgen/geometry.py`, `types.yaml`, `requirements.txt`, and published techniques (Watabou's Medieval Fantasy City
Generator and its open-source TownGeneratorOS; Parish and Mueller 2001 "Procedural Modeling of Cities"; Chen et al.
2008 "Interactive Procedural Street Modeling" (tensor fields); Kelly and McCabe 2007 "Citygen"). The techniques are
described from those publications; no code was run.

## 1. What the settlement layout must be

A settlement is the high-level plan of a human habitat on a map of 600-1500 m per side: districts, major roads, walls,
gates, a few landmarks, and the connectivity between them. Out of scope: buildings, minor roads, alleys. A typical
settlement spans 250-700 m. At the analysis cell of 10 m (`analysis.cell`) and a major road width of 10-16 m, the
smallest meaningful district is about 40 m across, and 6-16 districts per settlement fit the scale.

### 1.1 Structure shared by the references

| ref | districts | major roads | wall and gates | landmarks and environment |
|---|---|---|---|---|
| 5 harbour city | irregular, named | organic, to docks | irregular wall | bay, docks, outlying villages |
| 6 walled bay city | ring of districts around a centre | radial | wall with citadel in the wall | bay, docks quarter |
| 7 Pentguard | radial wedges in rings, outskirts around | spokes plus 3-4 ring roads | irregular outer wall, gates on spokes | central plaza, fortress on a peninsula, fields |
| 8 Saltport | large wedge-like and block districts | a few straight arteries; roads run on district borders | wall with gates, no wall on the water | harbour, fortified islands |
| 9 Daboha | concentric rings | rings plus spokes | inner wall and outer wall | river with a bridge |
| 10 real-world city | numbered districts bounded by highways | orthogonal grid | none | none |
| 12 Zoradun | grid blocks cut by radial boulevards | 5 boulevards from one market square | outer polygon wall, inner circular wall, gates where boulevards cross | citadel in a wall corner, market square, river, island fort |

Three observations drive the design:

1. Districts and roads are one planar structure. In refs 8, 10 and 12 the district boundaries are major roads or
   walls; in refs 7 and 9 they are ring and spoke roads.
2. Gates are where an artery meets a wall. Every wall in the references has 4-9 gates.
3. Landmarks sit at structural positions: the hub of the arteries (market, plaza), a wall corner (citadel), the
   shore (docks), a peninsula or island (fort).

## 2. Available implementation substrate

`requirements.txt` already pins everything the techniques need: `numpy`, `scipy` (`scipy.spatial.Voronoi`,
`Delaunay`, `cKDTree`), `shapely` 2.0.7 (`voronoi_diagram`, `polygonize`, `split`, `unary_union`, `buffer`,
`intersection`, `difference`, `simplify`), `networkx` 3.1 (`astar_path`, `dijkstra_path`, planar-graph helpers),
`scikit-image` (`find_contours`, already used for fragment outlines). `geometry.py` supplies `chaikin`,
`catmull_rom`, `clean_polygon`, `polygonal`, `drop_small_parts`; `noise.py` supplies seeded `fbm` 1D and 2D. All
techniques below run in pure Python 3.8 on these. No new dependency is needed. Candidates considered and rejected:
`pyclipper` (shapely covers offsetting), `triangle` (scipy Delaunay covers it), `noise` (mapgen has fbm).

Determinism (CLAUDE.md): each generator stage owns a `random.Random` seeded from
`sha256(settlement id, stage name, seed)`, so adding a draw in one stage leaves every other stage untouched. Qhull
(scipy Voronoi) and GEOS (shapely) are deterministic for pinned versions; every iteration over ridges, faces or
dict keys is sorted before use; output coordinates are rounded to 0.01 m before serialisation.

## 3. The techniques

Each entry gives: how it yields the five elements, seeded parameters, effort, expressive range, and the graph it
emits. Effort counts lines of Python and working sessions (one session = one focused agent run with tests).

### 3.1 Radial / wedge with ring roads (refs 7, 9, 8, 6)

- Districts: polar cells. Spokes at angles `theta_j` (k spokes, jittered) and rings at radii `r_i` (geometrically
  spaced) cut the footprint into cells `(i, j)`. A district is one cell or a merge of neighbouring cells (merge
  rule: wedge districts span several rings; ref 8). The centre cell becomes the plaza.
- Major roads: the spokes (polylines from the plaza to the wall) and the rings (closed or partial arcs). A spoke may
  stop at an inner ring (`spoke_dropout`) to make the outskirts less regular.
- Wall: a closed curve `r(theta) = R * (1 + irregularity * fbm(theta))`; an inner wall is the same curve at a smaller
  `R`. Ref 7's irregular outer wall and ref 9's inner wall are both this construction.
- Gates: the wall crossing of each spoke (or a chosen subset of 4-8).
- Landmarks: plaza at the centre; citadel at a wall corner or an outer cell; market on a ring.
- Seeded parameters: spoke count (5-12), ring count (1-4), ring spacing exponent, spoke angle jitter, spoke
  dropout, wall irregularity and wall frequency, elongation (affine squash plus rotation), centre offset from the
  footprint centre, inner-wall ring index, gate count, partial-ring probability.
- Effort: about 250 lines, 1 session. The simplest technique with an exact graph.
- Expresses: concentric and wedge cities, walled cores with outskirts, spoke-and-hub towns. Cannot express:
  orthogonal order, medieval irregularity inside districts (fixed by the polar lattice; elongation and fbm radial
  noise soften it).
- Graph: node per cell and per ring/spoke junction; the road graph is exact because junctions are the lattice
  points. District adjacency follows the lattice.

### 3.2 Voronoi patches (Watabou style; refs 5, 6, 8, 10)

- Districts: seed points on an outward spiral (dense at the centre, sparse outward), jittered, relaxed by 1-3
  Lloyd iterations; the Voronoi cells are the patches. The N patches nearest the centre form the town; each patch
  is a district (or a merge of neighbouring patches). Centre patch is the plaza. Cells are clipped to the
  footprint with shapely.
- Major roads: shortest paths over the graph of Voronoi edges (networkx `astar_path`), from every gate to the plaza
  and between gates; the path union is the artery set. Road polylines are Chaikin-smoothed (`geometry.chaikin`).
  Roads then run along district borders, as in ref 8.
- Wall: the exterior ring of the union of inner patches, simplified and smoothed. Adding patches changes the
  outline in whole-patch steps, so the outline is irregular without a noise function.
- Gates: wall vertices chosen at spread angles (greedy farthest-angle selection with minimum separation), the
  count seeded.
- Landmarks: plaza = centre patch; citadel = an inner patch touching the wall; market, temple, harbour assigned to
  patches by rules on distance to centre, wall adjacency and shore adjacency.
- Seeded parameters: patch count (8-30), spiral tightness, point jitter, Lloyd iterations, centre density bias,
  anisotropy (stretch plus rotation), wall on/off, gate count, plaza size, merge probability for small patches,
  landmark weights.
- Effort: about 500 lines, 2-3 sessions. Largest parts: the edge graph with A*, wall extraction, gate selection.
- Expresses: organic and angular medieval towns, harbour towns, mixes of large and small districts, irregular
  walls. Cannot express: regular lattices or perfect rings (the lattice is random); parallel avenues.
- Graph: Voronoi ridges give exact district adjacency; road graph nodes are ridge vertices.

### 3.3 Grid / Manhattan (refs 10, 12)

- Districts: a rotated rectangular lattice with major avenues every `n` cells and per-axis spacing jitter. Cells
  between avenues are districts (super-blocks); adjacent cells merge by a seeded rule. The Manhattan-distance
  variant assigns each point to its nearest of several centres under the L1 metric: boundaries are axis-parallel
  or 45-degree segments. L1 cells come from a raster of nearest-centre labels vectorised with `find_contours`
  and simplified, the same pipeline `fragment_geometry` uses (about 80 extra lines).
- Major roads: the avenues (straight polylines), plus optional diagonal boulevards radiating from a market square
  (ref 12: five boulevards from one square cut the grid).
- Wall: any closed polygon around the lattice: noise-warped superellipse, octagon, convex hull of the lattice
  points, or a hand-listed polygon. The lattice is clipped to it. An inner circular wall (ref 12) is a second
  closed curve.
- Gates: avenue and boulevard crossings of the wall.
- Landmarks: market square at the boulevard hub, citadel in a clipped corner, temple on a block.
- Seeded parameters: rotation, avenue spacing and its jitter, spacing ramp (denser at the centre), merge
  probability, boulevard count and hub position, outline kind and its roundness, inner-wall radius, L1 centre
  count.
- Effort: lattice and boulevards about 200 lines, 1 session; the L1 variant adds 1 session.
- Expresses: planned towns, colonial or imperial cities, ref 10 and ref 12 structure. Cannot express: organic
  growth or curved roads.
- Graph: exact; lattice nodes are avenue crossings.

### 3.4 Binary space partition (BSP; refs 8, 10)

- Districts: recursive splitting of the footprint polygon by lines. Each split picks an axis (the longer extent,
  with an angle jitter of 0-25 degrees) and a ratio in `[ratio_min, 1 - ratio_min]`. Leaves at depth `d` (or below an
  area limit) are districts.
- Major roads: the split lines at depth `<= d_road`; deeper split lines are district borders without roads. The
  artery hierarchy is the split tree: depth-0 cut is the main avenue.
- Wall: any outline (see 3.3), the polygon being split. Gates: the endpoints where artery cuts meet the outline.
- Landmarks: the leaf containing the centroid becomes the plaza or market; a reserved leaf becomes the citadel.
- Seeded parameters: depth, minimum ratio, angle jitter, axis bias (alternating versus longest side), minimum
  district area, artery depth, outline kind, reserved-leaf count.
- Effort: about 150 lines, 1 session (shapely `split` or half-plane intersection; no new code for the outline).
- Expresses: angular districts with T-junction arteries (Saltport-like partitions), cheap and robust. Cannot
  express: rings, curved arteries, organic fabric; arteries always end at T-junctions.
- Graph: split tree gives adjacency; the road graph is the cut segments split at T-junctions.

### 3.5 Organic growth (road-first) and tensor fields

Two real techniques exist and a cheap approximation.

- Road-first hub graph (Citygen). Pick hub points (plaza, gates, landmark sites), connect them with weighted
  shortest paths over a cost grid (slope, water, existing roads cheaper), and polygonise the road graph: the faces
  are districts. Roads exist before districts and respond to terrain, so this technique carries the later phase
  "nestle against a mountain, wrap a bay". Effort: about 350 lines using the mapgen cost grid (`analysis.py`) and
  `shapely.polygonize`, 2-3 sessions.
- Tensor fields (Chen et al.). Basis fields (grid, radial) blended by distance; major hyperstreamlines are traced
  from seeds with a separation test; blocks come from `polygonize`. It yields hybrid grid-radial towns with curved
  arteries. Effort: about 700 lines (integration, seeding, separation, graph cleaning), 4-6 sessions, with
  non-trivial tuning. The value is real and the first-phase goal (learn what is cheap) argues for deferring it.
- Cheap approximation: the Voronoi technique (3.2) with high jitter, one Lloyd pass and a domain warp
  (`geometry.warp_geometry` machinery) gives "organic and chaotic with little apparent plan" at no extra
  implementation cost. Organic is a parameter regime of 3.2.
- Expresses (road-first): any terrain-conforming layout, bays, slopes. Cannot express without extra work: strict
  symmetry.
- Graph: planar graph from polygonize is exact; faces are districts.

### 3.6 Hybrids

Hybrids are compositions of the generators above through three interchangeable stages: footprint (outline
strategy), partition (polar, Voronoi, grid, BSP) and arteries (spokes, avenues, shortest paths on partition
edges). Two composition forms cover the references:

- Core plus outskirts: partition A inside a core footprint (disc or polygon), partition B in the annulus between
  the core and the outer wall. Ref 7: polar core, Voronoi outskirts. Ref 12: grid outskirts with a circular
  inner wall around a polar-like old town. Stitching: outer-cell seeds sit on the core boundary; core spokes
  continue as arteries through the annulus (shortest path to the outer wall).
- Overlay: boulevards or a second wall drawn over a base partition, with the cut cells split along them (ref 12
  boulevards over a grid).
- Effort: about 120 lines of stitching once two generators exist, 1 session.

### 3.7 Environment: rivers, bays, islands, fortresses, walls and gates

These are modules acting on any generator's output, not generators.

- Land mask. The generator receives the footprint as a polygon with water holes and shore lines. From a map spec,
  the mask is the complement of lake regions and `river`-line areas (`geometry.build_geometries` output). For
  stand-alone use, the settlement carries its own water: a river as a Catmull-Rom line through the town, a bay as a
  blob polygon on one side.
- River. Districts are clipped by `difference` with the river area; slivers under 600 m2 are dropped; every road
  that crosses the river is emitted as a `road` line crossing a `river` line, which mapgen's automatic crossings
  (§4.7) turn into bridges. Bridge count is the number of artery crossings, chosen by the generator
  (`bridge_count`, spaced by minimum distance).
- Bay and harbour. Districts touching the water polygon get `waterfront: true`; the one nearest the bay hub
  becomes the harbour district (dock landmark). The wall follows the land side only: the outline segments within a
  buffer of the water are marked `open_shore` and omitted from the wall polyline, optionally with a water gate.
- Islands. A second, small settlement is generated inside the island polygon (3.1 with a handful of cells, or a
  single fortress landmark). A `bridge` or `ferry` edge joins it to the mainland graph; with a bridge the
  connection is a `road` crossing a water line, with a ferry it is a graph-only edge (kind `ferry`).
- Fortress and citadel. A reserved polygon: convex or star-shaped bastion fort (n-point star with radius ratio
  parameter), a rectangle with corner towers, or a wall-corner citadel. Placement modes: `corner` (touching the
  wall, ref 12), `centre`, `peninsula` (a land tongue of the footprint, ref 7, connected by one causeway, the single
  gate), `island`. The reserved polygon is subtracted from the district partition. The citadel has its own wall
  segment and gates (inward and outward).
- Walls. Outline strategies: polar noise, union of patches, hull, superellipse, hand polygon. Multiple walls are
  allowed (outer, inner, citadel); each wall is a closed polyline with `open_shore` segments excluded.
- Gates. Selection: arteries crossing a wall, thinned to a seeded count with a minimum angular and metric
  separation. Gates snap to external connections: the settlement parameters accept a list of point references
  (map-spec roads that arrive at the town); the nearest artery is steered so one gate lies where each external road
  meets the wall.
- Terrain fit (later phase). Inputs are the analysis grid's slope and height. Footprint = cells with slope under a
  limit, closed by morphology and vectorised with `find_contours`; the lattice rotation aligns with the local
  contour direction (grid) or hubs and costs come from slope (road-first, 3.5). The mountain-nestle case is a
  footprint clipped to a half-plane with the wall on the mountain side marked `natural` (no wall required).

## 4. Comparison

| technique | effort (sessions) | refs covered | exact graph | organic | water and fortress | main limit |
|---|---|---|---|---|---|---|
| radial | 1 | 7, 9, 8, 6 | yes | low | via modules | regular lattice |
| Voronoi | 2-3 | 5, 6, 8, 10 | yes | high | via modules, natural fit | no rings or grids |
| grid and L1 | 1 (+1) | 10, 12 | yes | none | via modules | no curves |
| BSP | 1 | 8, 10 | yes | low | via modules | T-junctions only |
| road-first hub graph | 2-3 | 5, 6, 7 | yes | high | best terrain fit | needs cost grid |
| tensor field | 4-6 | 7, 12 | yes | high | hard | tuning effort |
| hybrid composition | 1 on top | 7, 12 | yes | mixed | via modules | needs two generators |

## 5. Mapping onto the mapgen spec

### 5.1 Elements

| settlement element | spec feature | detail |
|---|---|---|
| district | `region`, type `town`, shape `poly`, `label` | one region per district; `rough: 0` keeps the planned polygon; `edge` 30 m default flattens terrain at the rim; ids `<settlement>.d_<key>` |
| major road, ring road, avenue | `line`, type `road`, `smooth: true` | width 10-16 m; ring roads emitted as a closed point list (first point repeated); `wobble: 0` keeps designed geometry |
| bridge | `road` line crossing a `river` line | automatic crossing (§4.7) yields id `<road>-<river>-<n>` |
| wall | `line`, type `cliff` today (impassable, line of sight true, width 8) | a dedicated line type `wall` with its own palette entry is a contract addition (§6) |
| gate | `crossing` disc at the gate point | `ramp` (cost 1.2, r 40) is available today; a dedicated crossing type `gate` (cost 0.6, r 12) is the intended form; a crossing overrides the impassable wall cost (§4.4) |
| square, plaza, market | `poi`, type `landmark`, `r` = square radius | optionally `capture` when the town is an objective |
| citadel, fortress | `region` type `town` plus `poi` type `keep_site` | `keep_site` (r 60) exists |
| gate point, junction | `points` entry (waypoint) | referenced by road paths, so the flowgraph edge derivation resolves it (§5.3) |
| external approach | existing spec `road` line | the settlement parameter lists its end points as references |

Identity (§4.8): district keys are structural, never list positions: polar `r{ring}s{spoke}`, grid `x{i}y{j}`,
Voronoi `p{seed index}` assigned by generation order from the spiral, BSP the split path (`LRL`). Gate ids derive
from the road id and wall id (`gate_<wall>_<road>`). A parameter change therefore keeps ids of untouched features
stable for `mapgen diff` and the editor.

Existing region types have no per-district flavour. District kind (market, residential, harbour) travels in the
sidecar `settlement.json` and in the region `label`; a `town` splat is the same for all kinds in the terrain export.
Per-kind splats are a later extension.

### 5.2 Integration forms

- Form A (first): an offline generator. `python -m mapgen town generate <settlement.yaml> --out fragment.yaml`
  writes regions, lines, crossings, pois and points as a YAML fragment, plus `settlement.json` (the core model) and
  an optional `.flow.yaml`. The author pastes the fragment into a map spec, as `flowgraph scaffold` does for
  topology. This form changes no spec schema and no baseline.
- Form B (later): a top-level `settlements:` spec section, expanded at load time into the same features with derived
  ids. It needs a `MAPGEN_SPEC.md` and `EXPORT_FORMAT.md` change, schema validation, and re-baselining approval.

### 5.3 Connectivity graph emission

The core model carries two graphs derived from the geometry.

Movement graph (the flowgraph projection):

- Nodes: gates (kind `choke`), squares and citadels (kind `zone`), major junctions (kind `choke`), bridge heads
  (kind `choke`). Each is bound to a `points` entry or POI.
- Edges: consecutive nodes along a major road polyline, one edge per road segment, with `road_id`, `length`,
  `class` (artery, ring, boulevard, bridge). A ring road contributes one edge per arc between junctions.
- Rule: the flowgraph edge derivation (§4) pairs consecutive resolved vertices of each `road` line, so a road
  emitted with reference tokens for its gate, junction and square points yields exactly these edges. The emitted
  paths therefore use reference tokens, never bare coordinates.

District graph (inspectable, in `settlement.json`):

- Nodes: districts and landmarks. Edges: `adjacent` for shared border segments with `passable: true` (open border)
  or `false` (wall between), each carrying the gate ids and road ids that cross it; `waterfront` and `shore`
  attributes on districts touching water.
- Rule: two districts are adjacent when their polygons share a boundary of positive length; the border is open when
  no wall segment lies on it, otherwise it is passable only through gates on that segment.

Flowgraph nodes for districts: the edge derivation reads lines, so a district node has edges only when a line
passes through its bound feature. Arteries run on district borders, not through district centres, hence districts
appear in the flowgraph only as attributes of road nodes (a `desc` listing member districts). Adding district nodes
needs a `check` extension ("a district node is adjacent to the road nodes on its boundary"); that extension is
a candidate follow-up, kept out of the first build.

Checks run on every generated settlement (`town check`): district polygons are valid, non-overlapping, and their
union covers the footprint within 1 %; each district area is at least the minimum (default 1 600 m2); the wall is a
closed simple ring; each gate lies on a wall within 1 m and on a road; every district is reachable from every
gate through the district graph; the movement graph is connected; every wall opening is a gate or an `open_shore`
segment; every spec approach point is within 30 m of a gate.

## 6. Core data model

`Settlement` (pure data, JSON-serialisable, `format_version: 1`, all coordinates metres in the map frame):

```
Settlement
  id, generator, seed, params (resolved, echoed), bounds (x0,y0,x1,y1), centre
  footprint      polygon (outer boundary of the built area, water removed)
  districts[]    id, key, kind (core|market|residential|craft|harbour|citadel|temple|slum|outskirt),
                 polygon, area, label, inside_wall (bool), waterfront (bool), landmarks[] (ids)
  roads[]        id, class (artery|ring|boulevard|bridge|approach), polyline, width, nodes[] (node ids)
  walls[]        id, kind (outer|inner|citadel), polyline (closed), thickness, open_shore[] (index ranges)
  gates[]        id, wall, at, normal, road, kind (gate|postern|water_gate)
  landmarks[]    id, kind (square|market|citadel|fortress|temple|docks), at, r, polygon (optional), district
  water          optional echo of input: river polylines, bay polygons, island polygons
  graph          nodes[] (id, kind, at, district), edges[] (id, a, b, kind, road, length, passable)
```

Build-time helper, not part of the data: a planar street graph (PSLG) whose edges carry a class (road, wall,
border, shore). Districts are its faces (`shapely.polygonize`); the checks in §5.3 compare the generator's
districts with the faces. The helper also makes "road-first" generators (3.5) and generator stitching (3.6)
direct: their output is a PSLG, and the model derives from it.

Contract additions required for form B or full rendering (each follows CLAUDE.md contract-first): line type
`wall` (cost inf, los true, width 8, palette entry separated by lightness and a hatch pattern); crossing type
`gate` (cost 0.6, r 12); POI types `square` and `citadel` (or `landmark` with a `kind` attribute).
`python -m mapgen palette` verifies colour-blind separation after the additions.

## 7. Recommendation

### 7.1 Generators to build, ordered by value over effort

1. Core model, checks, spec and flow emitters, SVG preview, plus `radial` (one slice). The polar lattice has
   exact adjacency and covers refs 7, 9, 8 and 6; it exercises walls, gates, plaza, citadel and rings, so the
   model and emitters are validated end to end on the simplest input.
2. `voronoi` (Watabou style). Highest variety per unit effort: organic, harbour, angular-medieval looks, plus the
   "chaotic" regime by parameters. Covers refs 5, 6, 8.
3. `grid` with boulevards, optional L1 variant. Planned and imperial towns; covers refs 10 and 12.
4. `bsp`. Cheapest, robust, angular districts with T-junction arteries; also the best generator for the district
   layout of a fortress interior and for the island settlements.
5. `hybrid` composition (polar core plus Voronoi or grid outskirts) and the environment modules (water mask,
   bridges, bay and shore walls, citadel placement modes, island satellites). Covers the full set of references 7
   and 12.

Deferred: road-first hub graph and terrain fit (later phase: nestle, wrap), tensor fields.

### 7.2 Module layout under `tools/mapgen/mapgen/`

The package is `mapgen/town/`; 15+ modules would crowd the flat directory (the flowgraph feature already uses
eight `flowgraph_*.py` files).

```
town/__init__.py        public API: generate(params) -> Settlement
town/model.py           dataclasses, to_dict/from_dict, format_version
town/params.py          parameter schema, defaults, validation, seed derivation (sha256 per stage)
town/planar.py          PSLG helpers: snapping, polygonize faces, ring simplify, closed-ring Chaikin
town/outline.py         footprint and wall outline strategies (polar noise, hull, superellipse, patches, polygon)
town/gates.py           gate selection, spacing, snapping to external approaches
town/landmarks.py       square, market, citadel placement rules
town/env.py             water mask, river bridges, bay and shore walls, islands, fortress polygons
town/graph.py           movement graph and district graph derivation
town/check.py           invariants of §5.3
town/gen_radial.py      generator 1
town/gen_voronoi.py     generator 2
town/gen_grid.py        generator 3
town/gen_bsp.py         generator 4
town/gen_hybrid.py      generator 5 (composition of the above)
town/registry.py        name -> generator function(params, rngs, env) -> Settlement
town/emit_spec.py       Settlement -> spec YAML fragment (regions, lines, crossings, pois, points)
town/emit_flow.py       Settlement -> .flow.yaml
town/render.py          preview SVG: districts by lightness plus hatch, roads, wall, gates, landmarks
```

Wiring: a `town` command group in `__main__.py` (`generate`, `check`, `preview`); a `mapgen/TOWN_SPEC.md` holding the
contract of this section and of `settlement.json`; tests `tests/test_town_*.py` covering determinism (same seed and
parameters give identical `settlement.json` bytes), the §5.3 invariants over a sweep of 50 seeds per generator, and
emission round trips (emitted fragment passes `validate`; `flowgraph check` against the emitted flow file passes).
Generated fragments live under `maps/` or `maps/concepts/` only after the owner approves a baseline.

### 7.3 Conclusion (10 lines)

1. All techniques run on the pinned dependencies (numpy, scipy, shapely, networkx); no new dependency.
2. Districts, roads, walls and shore form one planar graph; districts are its faces; the core model derives from it.
3. Core model: Settlement with districts, roads, walls, gates, landmarks and two graphs (movement, district).
4. Build order: radial plus core, Voronoi, grid with boulevards, BSP, hybrid plus environment modules.
5. Radial first: exact graph, three references, simplest end-to-end validation of walls, gates and rings.
6. Voronoi, with high jitter, also yields the organic regime; a dedicated organic generator is unnecessary at first.
7. Rivers, bays, islands and fortresses are modules acting on any generator's output via a land mask.
8. Spec mapping needs no schema change for form A (generated YAML fragment); walls reuse `cliff`, gates reuse `ramp`.
9. Flowgraph projection uses gates, squares and junctions as nodes with reference-token road paths; district nodes
   need a later `check` extension.
10. Deferred: road-first terrain fit and tensor fields (the later nestle and wrap phase); new `wall` line type and
    `gate` crossing type are the contract additions to approve first.
