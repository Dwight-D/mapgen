# Town layers — nested district subdivision

Status: L1-L4 implemented (mission GDU-57); L5 showcase pending the owner's review. Section 16 lists where the implementation differs from the text below. Self-contained apart from `TOWN_SPEC.md` (the settlement model, §3, ids, graphs §5, checks §8,
emitters §9, preview §10), which this file extends. Nothing here changes the output of a settlement generated without `layers`.
All three fills are validated (§13, §16).

## 1. Purpose

A top-level generator (`radial`, `voronoi`, `grid`) builds a whole settlement. **Layers** subdivide chosen districts of that settlement
with another layout: radial wedges split into grids, grid blocks split into Voronoi cells, Voronoi cells split radially. The sub-layout
fills the district polygon only, adds its own districts and roads, and joins the parent road network. The choice of sub-generator is per
district (rule by kind, area, key, probability), seeded per district. Result: more detail and mixed street patterns in one settlement.
`Settlement.districts` still holds a flat list of districts that tile the footprint (the **leaves**); the hierarchy is extra data.

## 2. Concepts

- **Parent district**: a district being subdivided. It disappears from `districts` and is recorded as a **layer node** (§3).
- **Leaf**: a district not subdivided. Every entry of `Settlement.districts` is a leaf. Checks, graphs, emitters see leaves only.
- **Depth**: top-level districts are depth 0; children of a depth-0 parent are depth 1; and so on. `max_depth` (default 1, max 3) is the
  number of subdivision passes. Depth-d districts are subdivided in pass d+1.
- **Fill**: the polygon-fill mode of a generator (§4): `fill(P, ...)` tiles polygon `P` with faces and roads clipped to `P`.
- **Sub-road**: a road made by a fill. Class `artery` (main sub-road, few) or `street` (secondary, many). **Boundary street**: class `street`
  road laid along a parent boundary edge that carries no road (§5.2).
- **Frame**: the polygon's local layout frame: origin, rotation (alignment, §6.4), scale.

## 3. Model extensions (`model.py`; `format_version` stays 1, new fields omitted from JSON while default)

```python
District:   parent: str = ""    # id of the parent layer node ("" for depth 0)
            depth: int = 0
@dataclass LayerNode:           # one per subdivided district, any depth
    id: str                     # the id the district had (e.g. "town_d_r1s3")
    parent: str                 # layer node id or "" (depth 0 parent)
    depth: int                  # depth of this district (0 = top level)
    polygon: List[Point]        # CCW ring, same stitching rules as District.polygon
    use: str                    # grid | radial | voronoi
    rule: int                   # index of the matching rule (diagnostic only)
    params: Dict[str, Any]      # resolved sub-generator parameters (echo; regeneration reads them back)
    children: List[str]         # ids of child districts or layer nodes, sorted
Settlement.layers: List[LayerNode] = []     # sorted by (depth, id); key "layers" in JSON, omitted when empty
Road.cls gains `street`      # secondary road (width 4-6 m); movement GraphEdge.cls too
```

`Settlement.params["layers"]` echoes the resolved layer spec (§6.2) as a JSON-safe dict; `check_determinism` regenerates from it.
`District.kind` of a child is inherited from the parent unless the rule has `kinds` (§6.1). `District.inside_wall`, `waterfront`
inherit; `waterfront` is recomputed (child touches the shore). `landmarks` go to the child containing the landmark point.

## 4. Fill mode of a generator

Existing generators build a settlement (outline, plaza, walls, gates). A fill builds none of those: it is a pure function of a polygon.

```python
# mapgen/town/layerfill.py
@dataclass
class SubRoad: key: str; cls: str; polyline: List[Tuple[float, float]]      # cls artery | street; both ends on P.boundary or at a junction
@dataclass
class Fill:    faces: List[Tuple[str, Polygon]]; roads: List[SubRoad]       # faces tile P exactly; face keys unique within the fill
class Unfit(Exception): ...                                                  # this polygon cannot take this layout: caller leaves it a leaf
# each fill module exports SUB_DEFS: List[ParamDef] and
def fill(P: Polygon, rng: random.Random, p: Dict[str, Any], frame: Frame, min_area: float) -> Fill
```

Contract of every fill: `P` is a valid CCW polygon without holes, possibly non-convex. Faces are valid, hole-free polygons, each at least
`min_area` (smaller faces merge into the neighbour sharing the longest border), at least 2 faces (else `Unfit`), and their union equals `P` within
0.5 %. Every road lies inside `P` (clipped), runs along the border of at least one face (road pieces buried inside a merged face are cut away:
`piece ∖ union(face.buffer(-0.05))`), starts and ends on `P.boundary` or on another road of the same fill, and has no dead end (a sub-road stub
that ends on neither is dropped). Output is sorted by key; coordinates rounded with `model.r2` after all geometry. Deterministic given `rng`.

### 4.1 Grid fill (`fill_grid.py`) — prototyped

1. Frame: rotation from `align` (§6.4); origin `P.centroid` (or `representative_point()` if the centroid is outside); `P` is oriented CCW.
2. Spacing `block` (u) and `block * block_ratio` (v). Default block = `max(sqrt(1.3 * min_area), sqrt(area(P)) / 3)` (cells at least ~1.3x the minimum area,
   which keeps merging rare); lattice phase `rng.uniform(0, 0.5)` of a block. SUB_DEFS: `block` (25-250, drawn from the default +-25 %), `block_ratio`
   (0.6-1.6), `jitter` (0-0.25 of the spacing, per-line offset), `main_every` (int 0-6: every n-th line of each family is `artery`, 0 = none, default 3).
3. Lattice lines `u<k>`, `v<k>` (k signed, `n<k>` for negative, as `gen_grid`) are intersected with `P`; each connected piece of at least 8 m is a candidate
   road keyed `<line key>_<piece index>` (pieces ordered along the line).
4. Faces: `polygonize(unary_union([P.boundary] + pieces))`, keeping faces whose representative point lies in `P`. Face key `x<i>y<j>` from the cell
   index of the representative point in the lattice (suffix `a`, `b` for several faces in one cell: non-convex `P`), as `gen_grid`.
5. Merge faces below `min_area` (smallest first); drop buried road pieces; drop pieces shorter than 8 m.

### 4.2 Radial fill (`fill_radial.py`)

Hub = `shapely.ops.polylabel(P, tolerance=1.0)`. Valid only when every ray from the hub reaches `P.boundary` before leaving `P` (star-shaped about the hub, tested with
72 rays); else `Unfit`. SUB_DEFS: `spokes` (3-8, default `clamp(round(perimeter / 90), 3, 8)`), `rings` (1-2, default 1 when area < 20 000, else 2), `ring_frac`
(0.4-0.9, drawn uniformly in 0.52-0.78: radius of ring 1 as a fraction of the mean hub-to-boundary distance), `phase` (degrees). Spokes are rays clipped to `P` (class `artery`);
rings are `hub.buffer(r) ∩ P` boundary arcs (class `street`, closed rings may be clipped into several pieces). Faces: `polygonize` of
`P.boundary + spokes + rings`; the face containing the hub is key `core`, others `r<ring>s<spoke>` as `gen_radial`. No plaza landmark is made. The ring radius is tried at 1.0, 0.9, 0.8, 0.7, 0.6 times the drawn value until no ring runs within 2.5 m of the boundary over more than 12 m (a graze) and no face is a hollow wrap around the core (face area under 0.4 of its convex hull); `Unfit` when none fits.

### 4.3 Voronoi fill (`fill_voronoi.py`)

`n = clamp(round(area / (2.2 * min_area)), 3, 14)` seeds by rejection sampling inside `P` (minimum spacing `0.6 * sqrt(area / n)`) from `rng`, then `lloyd` (0-2, default 1)
relaxation passes; cells are `scipy.spatial.Voronoi` clipped to `P` (`gen_voronoi._cells` clipping reused, taking a polygon instead of the disc). Face keys `p<n>`
(seed index order after relaxation). Roads: the shared borders of cells form the border graph; arteries are `gen_voronoi._route` shortest paths between the 2-3 boundary
vertices farthest apart and the cell nearest the centroid; every other border chain is a `street` only when it joins two roads or a boundary (else it stays an unroaded
border). Cells below `min_area` merge as in §4.1.

### 4.4 Non-convex and degenerate polygons

Grid fill supports non-convex `P` (validated on an L shape). Radial needs a star-shaped `P`. Any fill raising `Unfit`, producing fewer than 2 faces, or failing its
own contract is not an error: the district stays a leaf and is listed in `layers.skipped` of the echoed params (`{id: reason}`).

## 5. Applying layers (`layers.py`, `apply(s, spec) -> Settlement`)

### 5.1 Order and add-ons

`registry.generate`: top-level generator → water overlay → fort add-on → **layers**. Water and fort act on the top-level districts (whole-settlement features: river
cuts, bays, citadel); layers then subdivide the resulting districts, which may be irregular polygons. Default ineligible kinds: `core`, `citadel`, `harbour`
(`skip_kinds`, overridable per rule). A district whose interior is crossed by a road of the parent (length inside > 1 m) is ineligible (roads normally run on district
borders; none are interior in current generators). Walls, gates, towers, fortress, water polygons are never modified by layers.

Passes: for depth d in 0..max_depth-1, process the depth-d districts in sorted id order: choose rule, roll probability, choose sub-generator, fill, then commit. After each pass the
district count is checked against `max_districts` (default 400): a commit that would exceed it is skipped (and listed in `skipped`: `max_districts`).

### 5.2 Boundary, roads and junctions

For a subdivided district `P` each boundary edge segment is classified once, in this order:

1. **wall** (lies on a `Wall` polyline, within `graph.SNAP`) or **shore** (on the water boundary; `water.water_union`): never gets a road. A sub-road end that falls on such an
   edge is cut back to its previous junction (dropped if none); the cut line stays a district border. Gates stay the only crossings of walls.
2. **road** (parent road overlap above `graph.BORDER_MIN`): already a road; sub-roads that end there create a junction on it (§5.3).
3. **free** (shared with a sibling or neighbour district, or the footprint edge with no wall): becomes a **boundary street** when `boundary: streets` (default),
   so every sub-road ends on a road and the sub-network is connected by construction. A boundary street is one `Road` per maximal run of free edge shared with the same
   neighbour: id `<sid>_bs_<keyA>_x_<keyB>` (neighbour keys sorted; `<keyA>_x_edge` for the footprint edge; suffix `_2`, `_3` for further runs, ordered along the boundary).
   The same street serves both sides, so two subdivided neighbours share it. With `boundary: roads_only` no streets are added: sub-road pieces ending on a free edge are cut back
   as in rule 1, and if that leaves the sub-network (including the road-edge junctions) disconnected the district falls back to `streets`.

Sub-road ids: `<sid>_sr_<parentkey>_<subroad key>` (e.g. `town_sr_r1s3_u2_0`). Width: `artery` `max(7, 10 - 2 * d)` with d the parent's depth (10, 8, 7);
`street` 5 m (boundary streets 5 m).

### 5.3 Junction nodes and the movement graph

Every sub-road end on a road or boundary street, and every crossing of two sub-roads, is a vertex; vertices on existing roads are **inserted into the parent road polyline**.
Node ids: `<sid>_j_<a>_<b>` from the two road keys that meet (`<sid>_sr_...` keys without the `<sid>_sr_` prefix; parent road keys are their own ids without `<sid>_`);
when more than one node would get the same id (two roads meet twice) a suffix `_2`... orders them along the first road. After all districts commit, `layers.rebuild_nodes` rewrites
`Road.nodes` for every road (parents included) so nodes are listed in travel order at polyline vertices; the parent's movement edges split at the new junctions (new edge ids; edges
untouched by a junction keep theirs). A pair of nodes joined twice (a chord and the boundary street between the same two nodes) would be a duplicate edge: the later road by id gets a midpoint
node `<sid>_jm_<road id without sid>` at the midpoint vertex of its polyline.

`layers.graph: major` (default) puts into the movement graph only `artery` sub-roads, boundary streets and the junctions they form with parent roads; `street` sub-roads keep `nodes: []`
(they appear in the spec fragment as lines, not in the flowgraph). `all` adds every sub-road. Node `kind` is `choke`; `GraphNode.districts` is recomputed by `graph.attach_districts` over leaves.

### 5.4 Shared vertices (stitching)

After the last pass, one registry of every vertex (district polygons, layer-node polygons, road polylines, junctions, wall and gate points) stitches T-junctions: every leaf and
layer polygon and every road polyline receives each registry vertex lying within 0.02 m of one of its edges (the rule `gen_grid` already applies per polygon), inserted in order along the edge. Neighbour
districts that were not subdivided get the vertices of their subdivided neighbour; this is what keeps `graph.district_graph` borders exact. Walls are not modified.
After insertion each ring drops zero-width spikes (a vertex, then a neighbour vertex, then the first vertex again: `_despike`); a ring that is still self-intersecting falls back to the snapped ring without insertions, then to the unsnapped ring.

### 5.5 Rebuilding derived data

`district_nodes/edges` via `graph.district_graph(leaves, roads, walls, gates)`; `graph_edges` via `graph.movement_edges` (edge ids disambiguated only as in §5.3);
`graph_nodes` kept for gates and zones, plus junctions; `attach_districts`. District `area` recomputed. Child `label`: `<parent label> <n>` in key order (`Residential Ward 2` -> `Residential Ward 2.1`).

### 5.6 Identity (`TOWN_SPEC.md` §3.1 additions)

| feature | id |
|---|---|
| child district | `<parent id>_<subkey>` (so `town_d_r1s3_x1yn2`, depth 2: `town_d_r1s3_x1yn2_p4`) |
| layer node | the id the district had as a leaf |
| sub-road | `<sid>_sr_<parentkey>_<subroad key>` |
| boundary street | `<sid>_bs_<keyA>_x_<keyB>` |
| junction | `<sid>_j_<a>_<b>`, midpoint `<sid>_jm_<road>` |

Keys of layered districts may exceed 16 characters (the limit of top-level keys); every id stays within `schema.ID_MAX_LEN` (64): a rule whose child ids would be longer is skipped (`skipped: id_length`).
All ids derive from parent ids and fill-local keys, never from list positions, so adding a rule or changing a sibling leaves every other district's ids and geometry stable.

### 5.7 Seeds

Per-district streams `params.stage_rng(sid, "layers", seed, "<district id>/<stage>")`, stages `pick` (probability roll, then weighted sub-generator pick, then `kinds` draw), `params`
(sub-generator parameter draws, `params.resolve` over `SUB_DEFS` with the rule's explicit values as overrides), `geom` (the fill's `rng`). A district's result depends only on
the settlement seed, its own id, its polygon and the rules; a change to another district or an added rule on other districts changes nothing here.

## 6. Parameters

`layers` is a settlement-level parameter accepted by `generate(..., params={"layers": ...})` and `--param layers=...`; `registry.generate` splits it off like water and fort
(`layers.is_layer_param`: name `layers`). Absent or `none` = no layers (output identical to today).

### 6.1 Canonical form (dict; JSON or YAML-safe)

```yaml
layers:
  max_depth: 2          # 1-3, default 1
  min_area: 1600        # smallest leaf, m2 (default: the settlement's min_district_area, never below 400)
  min_split_area: 6000  # a district below this is not subdivided (default 4 * min_area, min 3200)
  boundary: streets     # streets | roads_only
  graph: major          # major | all
  max_districts: 400    # 20-2000
  align: edge           # default for rules: edge | parent | none | angle:<deg>
  rules:                # per depth, first matching rule wins; no match = leaf
    - depth: 0          # int or [lo, hi]; default [0, max_depth-1]
      kind: [residential, craft]     # district kinds, default any not in skip_kinds
      area: [6000, 40000]            # m2 range of the district, default any
      key: "r*"                      # fnmatch on the district key (not the id), default any
      p: 0.8                         # probability the rule fires on a matching district, default 1
      use: {grid: 2, voronoi: 1}     # name, or weights (>0, any number of entries); `none` makes a matching district an explicit leaf
      params: {block: 45}            # explicit sub-generator parameters (validated against SUB_DEFS of the chosen generator, else an error)
      align: edge                    # overrides the default
      kinds: {residential: 3, craft: 1}   # optional: each child draws its kind from these weights
      skip_kinds: [core, citadel, harbour]
      parent_use: [radial, wedge]    # depth >= 1 only: the parent layer node used one of these fills (TOWN_ORGANIC_SPEC.md 7.7)
```

Validation (all collected into one `ParamError`): unknown keys, `use` not in {grid, radial, voronoi, bsp, wedge, follow, none} (the last three: TOWN_ORGANIC_SPEC.md), ranges, `p` in [0, 1], `params` names not belonging to every
generator in `use` (a name must exist in each weighted generator, or the generator be selected by one rule per generator). The resolved spec echoes into `Settlement.params["layers"]`
with all defaults filled in and `skipped` appended.

### 6.2 CLI string form

`--param layers=<s>` where `<s>` is `;`-separated items. A rule: `[<depth>:]<use>[@<k>=<v>[,<k>=<v>]...]` with `<depth>` `d` or `lo-hi`, `<use>` `grid`, `grid*2/voronoi` (weights) or `none`, `<k>` any
rule key (`kind=residential/craft`, `area=6000-40000`, `key=r*`, `p=0.8`, `align=edge`, `kinds=residential*3/craft`) or a sub-generator parameter prefixed `sub.` (`sub.block=45`). A global item: `max_depth=2`,
`min_area=...`, `boundary=...`, `graph=...`, `max_districts=...`. A value starting `{` or `[` is parsed as JSON for the whole parameter (the canonical form). Examples:

```
--param layers=grid                                              # every eligible district -> grid, depth 1
--param "layers=0:grid@kind=residential,p=0.7;0:radial@kind=market;max_depth=1"
--param "layers=0:grid*2/voronoi@area=8000-60000;1:radial;max_depth=2"
```

### 6.3 Resolution order

Rules resolve per district: first rule (list order) whose depth, kind, area, key match; then `p`; then `use`. Provenance: `LayerNode.rule` is that rule's index.

### 6.4 Alignment to the parent

`align: edge` rotates the frame so the lattice axis (grid), the first spoke (radial: `phase` relative to it) or the cell elongation (voronoi: seed anisotropy) lies along the district's **reference edge**: the longest
boundary edge lying on a road if any, else the longest boundary edge (ties: lowest start vertex). `parent`: use the rotation of the parent's own fill frame (top-level rotation for depth 0).
`none`: rotation 0. `angle:<deg>`: fixed. A drawn rotation jitter `align_jitter` (0-20 degrees, default 0) may be added per rule.

## 7. Checks (`check.py`, new `_layers`; applies only when `s.layers` or leaf `depth > 0`)

- Existing checks run on leaves unchanged; the minimum-area test uses `params["layers"]["min_area"]` for leaves of depth > 0.
- **Tiling**: each layer node's children (districts and nodes) are valid, overlap at most 1 m2 and cover the node polygon within 1 %; the depth-0 nodes plus depth-0 leaves tile the footprint (the existing coverage check on leaves implies it).
- **Ids**: child ids are `<parent>_...`; `District.parent` names an existing layer node; `depth` equals parent's depth + 1; depth at most `max_depth`; uniqueness is covered by `_identity`.
- **Roads**: every road of depth >= 1 lies within 0.5 m of leaf borders (no road through a leaf interior); no road crosses a wall except at a gate; road geometry is connected (graph of polyline endpoints and vertices joined within 0.05 m is one
  component over all roads, bridges included).
- **Junctions**: every road end of a sub-road coincides with a vertex of another road or the boundary street (no dangling end), except ends cut at a wall or shore.
- **Graph**: movement graph connected with junction nodes (existing check); no duplicate movement edge ids; district graph: every leaf reachable (existing check) and no leaf pair shares a border without a stitched shared vertex pair at its ends.
- **Count**: leaves at most `max_districts`.
- `check_determinism` already covers regeneration via the echoed `layers`.

## 8. Wall and gates

Layers never add or move walls or gates. The outer wall edge of a district is a `wall` edge (§5.2) with no road. The district graph's `wall` edges keep their gates because `graph.district_graph` is re-derived from leaves whose borders carry the same wall polylines.
Gate roads belong to the parent and stay on district borders, so they are `road` edges for the children beside them.

## 9. Fort and water

Both run first (§5.1) on the top level. Consequences: a river cuts districts before they are subdivided (children never cross water; `shore` edges get no road); bridges and approach roads are parent roads; `harbour`, `citadel` and `core` are not subdivided by default; the fortress
polygon, outlying forts and citadel wall are untouched; `water.check_water` and fort checks see leaves and must pass unchanged (they read `s.districts`, `s.walls`, `s.water`).

## 10. Emission

`emit_spec`: regions = leaves only (`type: town`), ids and `label` as in §3; lines = parent roads (with the inserted junction vertices, so `path` references the new nodes), sub-roads and boundary streets (`type: road`, width from §5.2); no layer-node regions (the hierarchy is `settlement.json` data;
a comment line `# layers: <n> subdivided districts` and, per leaf, nothing extra). `ROAD_TYPE` gains `street: road`. Points: junction nodes in the movement graph (`choke`) as `points:` entries. `emit_flow`: nodes and edges of the movement graph (`major` by default keeps the flowgraph small: top-level
roughly 40-100 nodes grow by the junctions of arteries and boundary streets only). District count growth: 6-30 top-level districts become up to `max_districts` (typically 3-12 children per subdivided district).
`flowgraph check --strict` passes on fragments plus spawn nodes (tested like the existing generators).

## 11. Preview (`render.py`; colour-blind safe: lightness and pattern, never hue)

- Leaf fill: base grey and pattern of the `kind` style as today; siblings of one parent alternate +-10 grey levels by child index parity (clamped 60-250) so adjacent children separate by lightness; the pattern is kept.
- Boundaries by hierarchy: a border between two leaves is drawn by the depth of their lowest common ancestor: different top-level districts 3 px dark (as today), different depth-1 parents 2 px mid-grey (90), siblings 1 px light-grey (150) dashed. Layer node outlines are not filled.
- Roads: `artery` sub-roads white on a dark casing narrower than parent roads; `street` thinner (min 2 px), casing only; boundary streets as `street`.
- Labels: leaf key text at 70 % size for depth >= 1; parent key at the parent centroid, upper case, only when at most 20 layer nodes.
- Title: `... (N districts, M subdivided, depth D)`.

## 12. CLI and documentation

`town generate <generator> --param layers=...`; `town list` adds a `layers` section (the sub-generators with their SUB_DEFS, the rule keys, the string grammar); `town check` unchanged. `TOWN_SPEC.md` gets a pointer paragraph in §14 and a `layers.py`/`layerfill.py`/`fill_*.py` entry in §12 in the slice that lands them. No change to the map spec schema or export bundle.

## 13. Prototype result (grid fill, scratch script, not committed)

A 30-line grid fill (frame from the longest edge, lattice lines clipped to `P`, `polygonize` of boundary plus lines, merge of faces under 1 600 m2, T-junction stitching) ran on 33 random convex polygons (3-9 vertices, 8 000-150 000 m2), an L shape and a thin wedge:
all faces valid, union equals `P` (within 0.5 %), zero overlap, every face at least `min_area`, every road end (100 %) on `P.boundary`. Findings applied above: (1) merging small faces buries road pieces inside leaves (about 79 % of lattice lines were partly
buried) -> pieces are trimmed against the leaves (§4) and the default block is sized from `min_area` to reduce merging (§4.1); (2) the L shape needs the face-key suffix for several faces in one lattice cell (§4.1 step 4); (3) `polygonize` plus the stitching pass is enough to share every
vertex exactly; no shapely snapping needed. Radial and voronoi fills are not prototyped; their first task is to validate §4.2 and §4.3 on random convex and L polygons and amend the spec.

## 14. Tests (`tests/test_town_layers.py`, plus a section in each existing town test where noted)

- Fill unit tests per generator: 30 random convex polygons and 5 non-convex: the §4 contract (tiling, min area, road ends, no buried road, determinism, rounded coordinates).
- `generate` with `layers=grid` over each of the three generators and 20 seeds each: `check` and `check_determinism` pass; leaf count > top-level count; every leaf id starts with a parent id.
- Mixed rules: `0:grid@kind=residential;0:radial@kind=market;1:voronoi`: all three fills occur; skipped districts are listed, never errors.
- Identity stability: adding a rule for kind X leaves every leaf of kinds not X byte-identical (ids and polygons); changing the seed changes the layout; two districts' results are independent of each other.
- Absent `layers`: `settlement.json` and fragments byte-identical to the current output (existing 20-seed sweeps unchanged; the existing tests pass untouched).
- With water and fort (`water=river`, `fort=...`): checks pass; no road crosses a wall except at gates; shore edges carry no street.
- Boundary: `boundary=roads_only` connectedness and fallback; `graph=all` vs `major` counts; `max_districts` cap; `max_depth=3` id length rule.
- Parameters: canonical form and the CLI string give the same echoed dict; every malformed input gives a `ParamError` listing all problems; JSON round trip with `layers`/`parent`/`depth` fields; old JSON (no layers) loads.
- Emission: fragment loads with `schema.load_spec_text`; with two spawn nodes it passes `flowgraph check --strict`; emitter determinism; the movement edge ids are unique.
- Preview: sizes, runs with layers; a pixel test that sibling districts of one parent differ in mean grey.
- CLI: `town generate grid --param layers=grid` exit code 0, writes files; bad `layers` exits 1 with all errors.

Nothing under `maps/` or `tests/baselines/` changes.

## 15. Slices (waves; later ones depend on the earlier ones as stated)

| slice | content | files |
|---|---|---|
| L1 | Core, grid fill: model fields, `LayerNode`, parse/validate/echo (§6.1-6.3), eligibility, per-district seeding, boundary classification, junction insertion, stitching, node rebuild, `apply`, registry hook, `_layers` checks, grid fill | `model.py` (fields, `street`), `layers.py`, `layerfill.py` (`Fill`, `SubRoad`, `Unfit`, `Frame`, shared helpers), `fill_grid.py`, `registry.py`, `check.py`, `__init__.py`, `tests/test_town_layers.py` |
| L2 | Radial fill (parallel with L3, after L1) | `fill_radial.py`, `layers.py` (register), `tests/test_town_layers_radial.py` |
| L3 | Voronoi fill (extracts the clipping function from `gen_voronoi`; the existing outputs must stay byte-identical) | `fill_voronoi.py`, `gen_voronoi.py` (refactor only), `layers.py` (register), `tests/test_town_layers_voronoi.py` |
| L4 | CLI string form, `town list` section, preview hierarchy, emit changes (§6.2, §10-12), flowgraph round trip; `TOWN_SPEC.md` pointers | `layers.py` (string parser), `cli.py`, `render.py`, `emit_spec.py`, `emit_flow.py`, `TOWN_SPEC.md`, `tests/test_town_layers_emit.py` |
| L5 | Showcase and tuning: three layered settlements in `maps/towns/` (e.g. radial+grid, grid+voronoi, voronoi+radial) with previews; owner reviews; baselines only with approval | `maps/towns/build.py`, `maps/towns/*.yaml` |

L1 is the only slice that edits `model.py`, `check.py`, `registry.py`; L2 and L3 touch only their own files plus one registration line each. The full suite (`pytest`, about 3 minutes) runs between waves; no baseline changes without owner approval.

## 16. Implemented form (deviations from sections 4-12)

Module layout: `layers.py` (apply), `layers_params.py` (parse, validate, echo, CLI string form), `layerfill.py` (contract and shared tail `finish`), `fill_grid.py`, `fill_radial.py`, `fill_voronoi.py`. Validation on 30 random convex polygons and non-convex shapes: the contracts hold; a fill that cannot comply raises `Unfit`.

- **Fill tail (`layerfill.finish`)**: lines are extended 0.3 m past any end on `P.boundary` and clipped back so noding is exact; road ends within 0.06 m of `P.boundary` or another road are snapped onto it; stubs are dropped in a loop.
- **Grid**: `block` default 0 means derived (`max(sqrt(1.3 * min_area), sqrt(area) / 3)` drawn +-25 % from the geometry stream), since a ParamDef range cannot depend on the polygon.
- **Radial**: spokes start at the first ring (0.8 of its radius; the part inside the core is cut away); the disc inside ring 1 is face `core`; `rings` 1-2 are the concentric streets; `spokes` and `rings` 0 mean derived. Faces under `min_area` merge, so small districts keep few roads.
- **Voronoi**: cells come from `shapely.ops.voronoi_diagram` clipped to `P` (`gen_voronoi` is not touched); seeds by rejection sampling, `lloyd` 0-2, `cells` 0 = derived; arteries are `gen_voronoi._route` paths between the two farthest boundary vertices and from the vertex nearest the centroid to the boundary; every other border chain is a `street`. There is no `anisotropy` parameter.
- **Boundary streets** are made after stitching, edge by edge (wall or shore within 1 m: none; road within 0.3 m: none; otherwise the run shared with the nearest neighbour of depth at most the node's, shallower first). Ids over 64 characters become `<sid>_bs_<12 hex>`. A junction id uses the first two road ids (sorted) meeting at the vertex; a clash appends `_2`, `_3` in coordinate order.
- **Stitching** first merges vertices within 0.015 m onto the earlier one (existing road, gate and node coordinates never move), then inserts into each polygon and polyline every foreign vertex within 0.02 m of an edge (never one within 0.05 m of an end of that edge, never one of the sequence's own vertices). The district graph derives from walls stitched the same way.
- **Pruning**: after stitching, a sub-road end that touches no other road is cut back to its nearest shared vertex (dropped if none); sub-roads and boundary streets not connected to a parent road are dropped. `graph: major` promotes street sub-roads into the movement graph when an artery component would otherwise be cut off from the parent network. A bridge joins nodes only at its ends.
- **`roads_only`** is applied first; the settlement is rebuilt with `streets` when the movement graph is disconnected.
- **Alignment**: `align_jitter` is a rule key; `parent` uses the parent node's stored `rot` (degrees, in `LayerNode.params`).
- **Check `_layers`**: the wall test is "at most 5 m of a layer road within 0.5 m of a wall"; the connectivity test is "every layer road lies in the component of a parent road".
- **Flow fragment**: `emit_flow` of a layered settlement lists the edges `flowgraph check` derives from the emitted lines (a literal vertex resolves to the nearest node within 30 m, and junction nodes are denser than that): the movement graph plus the connections the lines imply. `emit_spec` adds the comment `# layers: N subdivided districts, M districts in all`.
- **Preview**: leaf outlines 1 px light grey (depth > 0); subdivided-district outlines 3 px (depth 0) or 2 px; street sub-roads narrower; siblings are not dashed.
- **Eligibility**: a district under `min_split_area` is skipped silently; kinds `core`, `citadel`, `harbour` are skipped by default; `skipped` lists `road_inside`, `unfit: <reason>`, `max_districts`, `id_length`. Grid blocks of the top-level `grid` generator are about 2 000-5 000 m2, so pass `min_split_area=2500` to subdivide them.
