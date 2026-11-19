# Flowgraph — topology precursor to the map spec

A **flowgraph** is a small hand-written YAML file describing a map's *topology*: which places exist and
which of them are directly traversable from which. It carries no geometry. A mapgen spec (the
topography: regions, lines, positions) is drafted from it and later checked against it.

Status: slices 1 (loader, validation, `flowgraph validate`), 2 (edge derivation, `flowgraph check`), 3 (`flowgraph scaffold`, layout), 4 (`flowgraph preview`) and the spatial hints + balanced layout of �10-11 (GDU-38) implemented; slices 5-6 and �12-14 are design. Implementation slices are backlog items in the `mapgen` workstream
(GDU-31 … GDU-36 = slices 1–6, §9). Everything below is the contract those items implement.

## 1. Concepts

- **Node** — a place of interest (spawn, objective, chokepoint, flank, control zone). Has an id, optional
  working `name`, free-text `desc`, a `kind`.
- **Edge** — an undirected, direct traversable connection between two nodes. Optional `name`, `desc`.
  "Direct" means a traveller can go from one node to the other along roads or trails without being
  considered to pass through a third node.
- **Working names and descriptions** (`name`, `desc`, `brief`) are qualitative hints for the designer or
  agent drawing the spec. They never affect validation or generation. They are copied into the scaffold
  as comments.
- Only **connectivity** is modelled. Distance, width, elevation, openness, directionality are out of scope.

## 2. File format — `flowgraphs/<name>.flow.yaml`

```yaml
format_version: 1
name: town_square              # [a-z0-9_]+, becomes the spec name in the scaffold
brief: |                       # free text, not parsed
  Two teams, a contested open town square with a chokepoint entrance per team, and two flank routes
  that each pass through a strategic control zone.

nodes:                         # map: node id -> properties ({} allowed)
  A:  { kind: spawn,     team: A, name: Team A spawn }
  B:  { kind: spawn,     team: B, name: Team B spawn }
  O:  { kind: objective, name: Town square, desc: open, many angles of fire }
  AO: { kind: choke,     name: Team A gate, desc: tight chokepoint into the square }
  BO: { kind: choke,     name: Team B gate }
  Fa: { kind: area,      name: Flank A, desc: alleyways }
  Fb: { kind: area,      name: Flank B, desc: attic }
  G:  { kind: zone,      name: Great Oak Hill, desc: strategic control zone towards Fa }
  F:  { kind: zone,      name: Forest Glen,    desc: strategic control zone towards Fb }

edges:                         # list; each item is "X - Y" or { link: "X - Y", name:, desc: }
  - A - AO
  - AO - O
  - B - BO
  - BO - O
  - Fa - O
  - Fb - O
  - G - Fa
  - F - Fb
  - A - G
  - B - G
  - A - F
  - B - F
```

Rules (all violations are collected and reported like spec errors: `path: message`, exit 1):

- Node id: `[A-Za-z][A-Za-z0-9_]*`, unique. Edge endpoints must be declared nodes. No self-edges.
  Edges are undirected: `X - Y` equals `Y - X`; a duplicate edge is an error (give name/desc inline in one entry).
- Unknown keys are errors (same strictness as the spec).
- `kind` ∈ `spawn`, `objective`, `zone`, `choke`, `area` (default `area`). Table in §3.
- `spawn` requires `team` (short string); at least two spawns with distinct teams. Other kinds forbid `team`.
- `bind` (optional): the spec feature this node corresponds to (§3). Default: `base_<team>` for a spawn,
  else the node id lowercased. Two nodes may not bind the same feature.
- The graph must be connected (one component); a disconnected graph is an error.
- Loaded with the repo's `_SpecLoader` (duplicate keys are errors). `format_version` (1) and `name` are required;
  `nodes` is required, `edges` optional. A `link` has exactly one `-`; the connectivity error is reported only when
  no other error was found (dropped edges would make it misleading).

## 3. Binding nodes to a spec — no spec schema change

The spec is not modified. A graph node binds to a spec **feature id** by convention:

| kind | scaffold emits | binds to (spec id) |
|---|---|---|
| `spawn` | a `teams` entry | `base_<team>` (the implicit base POI) |
| `objective` | POI type `capture` | POI id |
| `zone` | POI type `capture` | POI id |
| `area` | POI type `landmark` | POI id |
| `choke` | a `points` entry (waypoint) | `points` key |

A node is **present** in a spec when its bound id exists among POIs / `base_*` / `points`.
Kinds are scaffold hints only; `check` does not verify the POI type (kind-vs-type match is a later
extension).

## 4. Edge derivation from a spec

An edge X–Y is **present** when a traversable line connects X and Y directly.

1. Only lines of type `road` and `trail` count. `river`, `cliff` never do. Crossings are ignored.
2. For each such line, walk its `path` in order and resolve each vertex to a node or to nothing:
   - a **reference token** in the raw YAML path (`base_A`, `ao`, a `points` key) resolves to the node bound
     to that id;
   - a **literal coordinate** resolves to the nearest bound node whose point is within `max(poi.r, 30)` m
     (30 m for `points` waypoints), else to nothing.
3. Drop unresolved vertices, collapse consecutive repeats. Each consecutive pair (P, Q) of distinct nodes is
   a present edge P–Q. A line through A, C, B yields A–C and C–B, **not** A–B.
4. Implementation (`mapgen/flowgraph_check.py`) reads the raw YAML for reference tokens (`load_spec` resolves them
   to coordinates and drops the names) and `load_spec` for coordinates and line types, aligning raw and loaded path
   vertices by index. Only nodes whose bound feature exists in the spec take part; a reference token naming a
   feature no node binds resolves to nothing. A literal coordinate within reach of several bound nodes resolves to
   the nearest. Base POIs have r 120 m, so a coordinate within 120 m of a base resolves to the spawn node.

## 5. Commands — `python -m mapgen flowgraph <sub>`

```
flowgraph scaffold <graph> [--out <path> [--force]] [--size X Y]   # draft spec text -> stdout or file
flowgraph check    <graph> <spec> [--strict] [--terrain]  # validate a spec against the graph (�12 for --terrain)
flowgraph preview  <graph> [--out <html>] [--force]      # static HTML/SVG preview
```

### `scaffold`
Emits a draft mapgen spec as text (f-string template, fixed order; same graph ⇒ byte-identical output;
never round-trips YAML). Contents:
- `format_version`, `name`, `size` (default 3000×3000), `seed: 1`, a `brief` assembled from the graph brief and
  each node/edge name + desc (agents read this).
- `teams` from spawn nodes; `points`; `pois` per §3; one `roads`-type line per edge
  (`{ id: <a>_<b>, seed: n, type: road, path: [<ref_a>, <ref_b>], width: 8 }`), with the edge name/desc as a
  comment above; per-node comments carry name/desc. A `# TODO(agent):` banner states what is left: shape
  terrain with regions, route lines, tune positions/types, keep `flowgraph check --strict` passing.
- Placeholder coordinates: the deterministic stress layout of the graph in the map rectangle (see Layout below).
- The scaffold **must pass** `mapgen validate` and `flowgraph check --strict` against its own graph
  (tested).
- Details: the spawn of the first team sits on the left margin and of the second team on the right, at mid height; margin is 10% of the shorter side; coincident nodes are nudged 10 m steps apart. Team colours are
  the palette defaults (blue `#2f6fdf`, amber `#f2a81d`, purple `#6a2cc0`) in team order. Line ids are
  `road_<a>_<b>` (node ids lowercased; `road_<n>` if that is invalid or taken), seeds are edge indices.
  Name and desc become `label`/`name` values (JSON-quoted) and comments. `--out` refuses an existing file unless
  `--force`; the file is written with LF endings. Exit 1 on a graph load error, a node that cannot become a valid
  spec feature (`bind` not matching `[a-z][a-z0-9_]*`, a spawn `bind` other than `base_<team>`, a team id outside
  `[A-Za-z0-9_]+`, more than 3 teams), or an existing `--out`.

### `check`
Loads graph and spec (errors → exit 1, same format). Reports:
- **missing nodes** — bound id absent from spec;
- **missing edges** — graph edge not derived (§4);
- with `--strict`: **extra edges** (derived edge between two bound nodes, not in graph) and
  **extra nodes** (POIs of any type except `landmark` that are not bound by any graph node; `base_*` of teams
  with no spawn node). `points` entries never count as extra.
Output: one line per finding (`missing node ao (AO: Team A gate)`, `missing edge A - AO (name)`, `extra edge X - Y`,
`extra node id (poi type)`), then a summary line `OK|FAIL: N missing node(s), N missing edge(s)[, N extra edge(s), N extra node(s)]`.
Missing edges are reported for every graph edge not derived, including those of missing nodes. Load errors print
`N error(s) in <file>:` plus indented `path: message` lines, or `error: file not found: <file>`. Exit 0 = pass, 1 = any
missing (or extra under `--strict`) or load error. Default mode ignores extras. Nothing is written.
Derived edges touching an unbound node are ignored (not a graph concern).

### Layout (`mapgen/flowgraph_layout.py`, `layout(g, width, height)`)
Stress minimisation, fully deterministic (no randomness, pure Python); see �10 for the `place` hints and �11 for the
goal. Details:
- Target distance between two nodes = graph distance x one uniform edge length; pair weight 1/d^2, pairs joined by an
  edge weigh `EDGE_WEIGHT` (16) times more. Each node with a `place` (spawns default to west/east) is pulled by a spring
  toward its compass point in a frame of half-width diameter/2 around the centroid: spawns aim at the frame edge with
  spring weight `ANCHOR_WEIGHT` x `SPAWN_BOOST` (3 x 20), other nodes at half that distance with weight `ANCHOR_WEIGHT` (3).
- Start: the layered layout (kept as the private `_layered_layout`: layers by graph distance from the first team's spawns,
  barycentre ordering, hill-climbed rows). Then `ITERATIONS` (400) Gauss-Seidel stress-majorisation sweeps in declaration
  order; no convergence test.
- Output: the solution is stretched to fill the map rectangle inside a margin of 10 % of the shorter side (y points
  north), each anchored node is clamped into its zone (�10), coordinates are rounded to 10 m, coincident nodes are
  nudged apart.
- `solved_extent(g)` gives the solution's width and height in edge lengths (the preview sizes its canvas from it).

### `preview`
Writes one self-contained HTML file (`mapgen/flowgraph_preview.py`; inline SVG + vanilla JS, no external URLs, no
fonts fetched) drawing the graph, read-only:
- Page title `<name> - flowgraph preview`, the graph name as heading and the `brief` above the drawing, a legend.
- Node markers separate kinds by shape and fill lightness (hue is never the cue): spawn dark square, objective
  star, zone hexagon, choke diamond, area light dashed circle. Spawns show their team letter inside the marker and
  `team <t>` in the label. Label = id, then name, on the side of the marker (below, above, right, left, in that
  preference) with the fewest collisions with edges, other labels and markers.
- Edge labels from the edge `name`. Hover (SVG `<title>` tooltip plus a detail line under the drawing) shows node and
  edge `desc`; hovering a node highlights its edges.
- Layout from `layout()` on a canvas sized to the solution's extent (300 px per edge length, minimum 900x560 px), north
  up.
- Output is deterministic (LF endings). Default `--out` is `out/flowgraph_<name>.html` (git-ignored), overwritten
  without `--force`; an explicit `--out` that exists is refused without `--force`. Exit 1 on a load error or a refused
  overwrite.
Chosen as HTML so the later editing wave (click to add nodes/edges, write back the `.flow.yaml`) can extend it
without a separate GUI stack.
Chosen as HTML so the later editing wave (click to add nodes/edges, write back the `.flow.yaml`) can extend it
without a separate GUI stack.

## 6. Module layout & tests

- `mapgen/flowgraph.py` — load/validate graph, layout, scaffold text, edge derivation, check; `mapgen/flowgraph_layout.py` (layout); `mapgen/flowgraph_preview.py` (preview HTML).
- `__main__.py` — nested `add_subparsers`; reuse the error-print helper.
- Python 3.8, pinned deps only (`networkx` already pinned), deterministic output.
- No schema/export/`maps/` change, so regression baselines stay untouched. Fixtures under `tests/` use `tmp_path`.
- Tests: `tests/test_flowgraph.py` (graph validation cases in the `_case` style; derivation incl. A–C–B rule,
  coordinate proximity, rivers ignored; check missing/extra/strict; scaffold determinism and self-pass) and CLI
  exit-code cases in `tests/test_cli_exit_codes.py` style.
- Docs: add a row to `CLAUDE.md` knowledge table and a section in `README.md` commands.

## 7. PoC map (after waves 1–2)

Commit the example graph above as `flowgraphs/town_square.flow.yaml`. Scaffold it, then an agent (via the
`map-concept` skill) fills the draft into `maps/concepts/town_square.yaml`: terrain regions, routed lines,
balanced analysis. Done when `build` exits 0, `flowgraph check --strict` passes, and the report shows no new
imbalance warnings. Adding the map adds a regression baseline (owner-approved rebaseline).

## 8. Decisions taken (revisit on request)

- Binding by id convention, no spec schema change; optional `bind` for overrides.
- Edge = direct consecutive road/trail adjacency; passing through a node splits the edge.
- Undirected, connectivity only; `kind` is a scaffold hint, not validated.
- Preview = static HTML/SVG generated by the CLI (not a Unity UI).
- Graph typos in the request (`A0`, `0`) read as `AO`, `O`.

## 9. Implementation slices (backlog items, `mapgen` workstream)

| # | slice | needs | evidence |
|---|---|---|---|
| 1 | graph loader + validation (`flowgraph.py`, §2) | — | tests/test_flowgraph.py graph cases pass; `flowgraphs/town_square.flow.yaml` loads |
| 2 | edge derivation + `flowgraph check [--strict]` (§3–§5) | 1 | derivation and check/strict tests pass; exit codes |
| 3 | `flowgraph scaffold` + layout (§5) | 1 (2 for self-pass test) | determinism test; scaffold of town_square passes `validate` and `check --strict` |
| 4 | `flowgraph preview` HTML (§5) | 1, 3 layout | file written, opens; labels/desc present; no CDN; tests/test_flowgraph_preview.py. DONE |
| 5 | PoC map `town_square` (§7) | 1–4 | build exit 0, check --strict pass, report clean, rebaseline approved |
| 6 | GUI editing — DEFERRED until 5 proves the workflow | 4, 5 | — |

Slices 1→2→3 are sequential (one module); 4 can start once 1 and the layout function exist.
Each slice: add docs (README commands, CLAUDE.md table row), run the full pytest suite, commit in the mapgen repo.

## 10. Spatial hints — `place`

A node may carry `place`, a soft anchor for layout and scaffold coordinates. Values: `west`, `east`, `north`,
`south`, `center`, and the four diagonals (`north-west`, `north-east`, `south-west`, `south-east`). It is a hint
only: `check` ignores it. Spawns without `place` default to `west` (first team) and `east` (second team).
The layout (hence scaffold and preview) places an anchored node in its zone of the map rectangle (edge zones within the outer 20 % band,
`center` within the middle 30 %); layout (§11) uses the anchor as a spring toward that point. Unknown values are
format errors (`nodes.X.place: ...`).

## 11. Layout — balanced edge lengths

`layout(graph, width, height)` minimises stress: the squared difference between each drawn node distance and the
graph-theoretic distance times one uniform edge length, so as many edges as possible come out the same length and
nodes that connect to both sides sit between them (flank nodes between the two spawns, not beside one). Anchors
from `place` add a weak spring toward the anchor point. Deterministic: initialised from the layered layout,
fixed iteration count, no randomness, coordinates rounded to 10 m. Consumed by `scaffold` and `preview`/editor.
Quality bar (tested): on town_square both flank nodes sit within 15 % of the layout width of the objective's x, and the
standard deviation of edge lengths (absolute and relative to the mean) is lower than the layered layout's.

## 12. Terrain-aware check — `flowgraph check --terrain`

`--terrain` adds a check against the terrain, not just the roads: the map's walkable cells must connect exactly
the graph's nodes. The road-path findings (�5 `check`) are still reported.

Procedure (`mapgen/flowgraph_terrain.py`): compute the movement-cost grid as the analysis stage does (geometry,
heightmap, `analysis.build_cost` on the analysis grid, `analysis.cell` metres per cell; the analysis passes and all
file output are skipped); a cell is walkable when its cost is finite (so `impassable: true` regions, rivers, cliffs,
mountains and steep slopes are walls; crossings open them). Each bound node present in the spec owns a disc: POI `r`
(minimum 30 m), spawn bases 120 m, `points` chokepoints 25 m. For every node X, label the 8-connected walkable cells
with all *other* nodes' discs treated as walls; the region containing X's disc (the disc cells themselves are always
passable for X) is X's flood. The other discs whose walkable cells touch the flood (8-neighbourhood) are X's **terrain
neighbours**. An edge X-Y is terrain-present when Y is a terrain neighbour of X or X of Y.

Findings:
- `terrain-missing edge X - Y` � graph edge with no walkable way that avoids the other nodes;
- `terrain-extra edge X - Y` � walkable bypass between nodes the graph does not connect (leaky wall, open ground
  around a chokepoint);
- `note: discs of X and Y overlap (treated as adjacent)` � overlapping discs count as adjacent (never an error by
  itself; the pair is a terrain neighbour pair, so it is terrain-extra when the graph has no such edge).

Both terrain findings fail the check whether or not `--strict` is given (`--strict` adds the road-path extras of �5).
Output lines come after the road-path lines and before the summary, which gains `, N terrain-missing edge(s), N
terrain-extra edge(s)`. Exit codes as `check`. Nodes missing from the spec are skipped (already reported as missing
nodes).

Cost: no flooding on a coarser grid is needed. On town_square (analysis grid 240 x 300 cells) the command takes ~7 s,
of which the flood is ~0.03 s; the rest is geometry and heightmap synthesis. Tests: `tests/test_flowgraph_terrain.py`.

## 13. Impassable regions

Any region may carry `impassable: true`. Its cells get cost infinity (like mountain) whatever the region type, line
of sight and height behave as for the type. This lets dense forest, rubble, hills or town blocks serve as walls.
Render: the region keeps its type colour and outline and is overdrawn with a diagonal strike-through hatch (a
fixed pattern independent of the type's own pattern, separable by pattern and lightness, colour-blind safe), and
the legend shows "impassable" with the hatch. The export region records carry `impassable` only for regions that set
it. Specs without the key produce byte-identical output (baselines unchanged). Documented in `MAPGEN_SPEC.md` §4.2,
`README.md` and `EXPORT_FORMAT.md` if the export gains the field.

## 14. Editor — `flowgraph edit`

`python -m mapgen flowgraph edit <graph> [--port N]` serves the preview page from a local stdlib HTTP server
(127.0.0.1 only) and lets the author edit the graph in the browser: drag nodes (positions are layout state only,
not saved), add a node (click empty space, choose kind, id, name, desc, place, team), add an edge (select two
nodes), delete, edit name/desc/kind/place, undo/redo. Every change is validated by the same loader (§2); an invalid
edit is refused with the error shown and never written. Save writes the `.flow.yaml` in canonical form (fixed key
order, comments not preserved; the format is small) and is the only write. Without the server, `preview` stays the
read-only static page.

## 15. Next slices (2026-11-19)

Impassable regions (§13), spatial hints + balanced layout (§10, §11), terrain check (§12), editor (§14), and the
reworked town_square map (800 × 800 m: river with bridges funnelling traffic through the graph's edges, dense/impassable
forest, cliffs and mountains blocking the rest; Attic = multi-storey north side of the square, Alleys = the
south entrance to the square, both inside the city centre; the two control zones on the town outskirts at either
side). Tracked as backlog items; PoC baseline stays pending until the map rework passes `check --strict --terrain`.
