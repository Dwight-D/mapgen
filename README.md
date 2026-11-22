# mapgen

Turns a hand-written map spec (YAML) into a 2D map image, gameplay analysis (overlays + report) and terrain
primitives (heightmap, splatmaps, masks, JSON). The spec is the source of truth; everything in `out/` is derived and
safe to delete. Full design: `MAPGEN_SPEC.md`. Export bundle contract for engine importers: `EXPORT_FORMAT.md`.
New map concepts are authored with the `map-concept` skill (`E:/GameDev/Unity/.claude/skills/map-concept/`).

## Setup

Python 3.8 venv at `tools/mapgen/.venv` (already created; runtime dependencies pinned in `requirements.txt`, development dependencies in
`requirements-dev.txt`: `.venv/Scripts/python -m pip install -r requirements-dev.txt`). Run from `tools/mapgen/`:

```
.venv/Scripts/python -m mapgen <command> ...
```

`mapgen.ps1` / `mapgen.sh` forward their arguments to the same command and work from any directory.

## Commands

```
python -m mapgen --version                                     # mapgen version, spec format versions it reads
python -m mapgen validate maps/example_river_crossing.yaml     # report every spec error, write nothing
python -m mapgen build    maps/example_river_crossing.yaml     # full build -> out/<name>/
python -m mapgen build    maps/                                # every *.yaml in the folder
python -m mapgen build    <spec> --only render|analyze|export  # one stage (analyze also writes the report)
python -m mapgen build    <spec> --out some/dir                # alternative output root
python -m mapgen probe    maps/concepts/thornwald.yaml 1500 1560 lodge   # times, spread, cost, features at points (no files)
python -m mapgen diff     out/a/thornwald out/b/thornwald      # what changed between two build folders: JSON (--text: summary)
python -m mapgen spec set <spec> pois.fa.at=[400,120] [<path>=<value> ...] [--create] [--dry-run] [--diff] [--detach] [--detach-refs]   # comment-preserving text edits of a spec, verified and atomic (see "Editing a spec as text")
python -m mapgen spec add <spec> regions '{type: forest, shape: blob, at: [300, 400], r: 80}' [--id forest_x]   # append a feature with a generated unique id (forest_1, river_2, poi_3, p4); creates a missing section
python -m mapgen spec delete <spec> lines.river_2 [regions.wood_1.rough ...] [--detach-refs]   # delete a feature (refused while referenced) or a key
python -m mapgen spec rename <spec> forest_1 north_wood        # rename an id and every reference to it
python -m mapgen town generate radial|voronoi|grid --seed 3 --size 1000 1000   # (any generator: --param water=river|bay|both for a river with bridges or a harbour bay; any generator: --param fort=corner|centre|peninsula|island [--param fort_outposts=2 --param fort_shape=star] for a fortress with its own wall and gates and outlying forts; any generator: --param "layers=0:grid@kind=residential;max_depth=1" subdivides districts with a grid, radial or voronoi fill, see mapgen/TOWN_LAYERS_SPEC.md) procedural settlement (districts, ring roads, wall, gates) -> out/town_town_radial_3/: settlement.json, preview.png, spec_fragment.yaml (paste into a spec), flow_fragment.yaml; also `town check <json>`, `town preview <json>... --out <png>`, `town list` (see mapgen/TOWN_SPEC.md)
python -m mapgen export-nlayout maps/towns/cindermarket.yaml   # (or a build dir: out/cindermarket) a town's settlement plan as a nodachi layout -> out/<name>/export/<name>.nlayout.json (see docs/nlayout.md). `build` writes export/settlements.json for a town map whose settlement file is <spec stem>.settlement.json beside the spec or out/<name>/settlement.json (EXPORT_FORMAT.md 3.10)
python -m mapgen export-landcover out/cindermarket   # (or a built spec) land cover, holes and keep-out areas of any built map as a nodachi layout -> out/<name>/export/<name>.landcover.nlayout.json (see docs/landcover.md)
python -m mapgen export-wall out/cindermarket   # (or a town spec) a town's walls (one linear spline per solid run, closed for a ring) and its wall gates as a nodachi layout -> out/<name>/export/<name>.wall.nlayout.json (see docs/wall.md)
python -m mapgen flowgraph validate flowgraphs/town_square.flow.yaml   # validate a topology graph (see mapgen/FLOWGRAPH_SPEC.md)
python -m mapgen flowgraph scaffold <graph> [--out <file> [--force]] [--size X Y]   # draft spec text from a graph (stdout by default; deterministic; node positions from a stress layout that balances edge lengths and honours the optional node key `place`: west/east/north/south/center/north-west/...)
python -m mapgen flowgraph preview <graph> [--out <html>] [--force]   # self-contained HTML/SVG graph view (default out/flowgraph_<name>.html, overwritten freely; --out refuses an existing file without --force)
python -m mapgen spec-edit <spec> [--port N] [--no-open] [--preview-px N] [--flowgraph GRAPH]   # browser map editor on a local 127.0.0.1-only server: full shape editing: drag/resize/add/delete regions (blob, band, poly), lines, crossings, POIs, `points`; property panel, rename, layers, undo/redo, explicit atomic Save (see "Editing a spec in the browser")
python -m mapgen flowgraph edit <graph> [--new] [--port N] [--no-open]   # browser editor on a local 127.0.0.1-only server: add/edit/delete nodes and edges, drag, undo/redo, Re-layout (Python layout incl. place hints), live validation; Save writes the canonical .flow.yaml (comments not preserved; refused while invalid); --new starts a two-spawn template written on first Save
python -m mapgen flowgraph check <graph> <spec> [--strict]       # spec vs graph: missing nodes/edges; --strict adds extras (no files)
python -m mapgen flowgraph check <graph> <spec> --terrain [--strict]   # also: walkable terrain must connect exactly the graph's nodes (terrain-missing / terrain-extra edges; ~7 s on town_square)
python -m mapgen types                                         # print the type table (types.yaml)
python -m mapgen build    <spec> --palette my_palette.yaml     # other render colours (omitted keys use palette.yaml)
python -m mapgen build    <spec> --splat-blend                 # soft splat edges + grass/dirt noise (defaults) when the spec has no output.splat_blend
python -m mapgen palette                                       # L* lightness matrix + greyscale legibility check
```

Exit code 0 on success; 1 on spec errors, a missing file, invalid probe points
or a path that is not a build folder; 2 on usage errors. Builds are deterministic: same spec + seed gives byte-identical files.
A build takes roughly 10-18 s for a 3-4 km map at default settings (under 25 s with scatter).

`probe` runs geometry, heightmap and analysis only. Per point it prints each team's travel time (`m:ss`), the
spread `(max-min)/min`, the cost cell value (and `fair` inside the fair zone), the topmost region, and the lines and
crossings there. Arguments are `x y` pairs in metres; a POI id stands for its position. It is the quick way to ask
"how long does each team need to get here?" while moving an objective.

`diff` compares two build folders (`<out root>/<name>/`, e.g. two `--out` roots of the same map) and
prints JSON: changed, added and removed files; heightmap share of texels changed and maximum height
delta in metres; share, count and bounding box of changed texels per mask and raster; the changed JSON
paths of every differing JSON file (`--max-changes N` per file, default 100); per-objective travel-time
deltas per team; per-team, per-pair and map-wide deltas of the report values. `--text` prints a summary
for reading. It reads only the two folders and also works on `--only analyze` builds. Exit code 0 when
the report is printed, whether or not the builds differ (`"identical"` in the JSON). Details:
`mapgen/DIFF_SPEC.md`.

## Editing a spec in the browser (`spec-edit`)

`mapgen spec-edit <spec> [--port N] [--no-open] [--preview-px N] [--flowgraph GRAPH | --no-flowgraph]` starts a local editor (stdlib server on 127.0.0.1 only,
per-run token, Host/Origin checks) and opens it in the browser. The page shows a reduced-resolution preview image of the map
(default 768 px, 257 heightmap; labelled "not a build", refreshed about 300 ms after each edit, never blocking the UI) with the
resolved region outlines and line centrelines and the spec's own values as handles: team bases (square), POIs (circle),
`points` entries (diamond), line waypoints (dot; a waypoint that is a point reference is a triangle), blob centres and
poly/band vertices, crossings (hexagon), radius grips (wide bar) and width grips (tall bar). Shape and lightness carry the
meaning, never hue.

- Drag a handle: it snaps to whole metres. Dragging a POI, base or `points` entry, or a reference triangle, moves the
  definition, so every referrer follows. Alt-drag a triangle detaches it to a literal `[x, y]` and leaves the definition.
  Several handles stacked at one point (a POI under four road-link triangles): the selected handle is the one that moves;
  clicking an already selected handle again cycles to the next one underneath.
- Shape editing. Blob: drag the centre, drag the radius grip (`r`), `rough`, `squash`, `angle`, `warp`... in the property
  panel (`rough` as slider and number). Band and line: drag vertices, a "+" at the middle of each segment of the selected shape
  (or a double-click on a segment) inserts a vertex, Delete (or the panel button) removes the selected vertex, the width grip
  sets `width`. Poly: the same, closed (the edge from the last to the first vertex has its "+" too); a poly keeps at least 3
  vertices, a band or line 2. Crossings: a disc (centre and radius grip) or a band (vertices and width grip).
- Tools (toolbar or key): Select (V), POI (P), Blob (B), Band (D), Poly (G), Line (L), Crossing (X), each with a type picker
  listing the names of `types.yaml` with their cost/width/radius. POI, blob and crossing: one click places it. Band, poly and
  line: click the vertices, Enter or a double-click finishes, Backspace drops the last vertex, Esc cancels. The new feature
  gets a generated id (`forest_3`, `river_2`, `poi_4`, `ford_1`; the number is one above the largest in use and an id deleted
  during the session is not issued again), is appended to its list (paint order: last paints on top) and is selected. A
  region or line gets no `seed`: its noise derives from its id.
- Panel for the selected feature: `id` with Rename (rewrites every reference: point references, `join`; a region, line or
  scatter entry without a `seed` gets `seed: <old id-derived seed>` so its shape does not change; refused when the new id is
  used, invalid, a team base, or the old id is ambiguous), `type` (the type table), every other key the schema allows for that
  kind of feature (read from `schema.py`, so it cannot drift: numbers with ranges, `impassable`/`smooth` checkboxes, `label`,
  `join`, `join_end`, `team`...), an x button that removes an optional key and an "Add property" menu for the absent ones,
  and Delete. Deleting a feature that others reference (a POI used as a waypoint or a region `at`, a `points` entry, a line
  that another `join`s) lists the referrers and offers "Detach references and delete" (point references become literal
  coordinates, a `join` is removed) or Cancel.
- Loader errors of a refused edit are mapped to the feature they name (`pois[3].at` is POI `g`): the first such feature is
  selected, its handles carry a dashed ring and a "!", its error rows and the invalid property field are marked.
- Layers (regions, lines, crossings, POIs and bases, named points, scatter areas): show and lock toggles, kept in the browser's local
  storage per spec path; a locked layer is drawn but cannot be hit.
- Keyboard: Tab through handles, arrows nudge 1 m (Shift 10 m), Ctrl+Z / Ctrl+Y, Ctrl+S, `+` `-` `0` zoom and fit, wheel and
  background drag zoom and pan.
- Every edit goes through the span-patch engine and the real loader (like `spec set`). An invalid result is refused with the
  loader's errors and the buffer is unchanged. The server keeps the text buffer and a whole-text undo stack; the file is
  written only by Save (no autosave), atomically and in place, only while the buffer is valid and the file on disk is the one
  that was read. A saved drag is a one-line diff; line endings are preserved.
- External-change safety (GDU-55). The server watches the spec file (a thread hashes it every 0.5 s; the page also polls
  `/api/poll` every second) and compares its content hash with the baseline (the text loaded or last saved). Your own Save
  updates the baseline, so it never triggers the watcher. A clean buffer reloads automatically: a banner "file changed on
  disk - reloaded" appears and the undo history is cleared. With unsaved edits a banner offers: **Replay my edits** (the
  session's unsaved edits, recorded as id-addressed ops, are re-applied onto the new disk text; ops whose target is gone or
  that no longer apply are listed and skipped, the rest applied; the result is the buffer, unsaved, validated and one undo
  step), **Discard my edits** (load the disk text, history cleared), **Save as copy** (writes the buffer to
  `<name>.edited<N>.<ext>` next to the spec, first free N, never overwriting; you keep editing the original buffer) and
  **Cancel** (keep editing; the banner collapses to a strip and Save stays refused with HTTP 409 until resolved). A deleted
  or renamed file allows only Save as copy; a file replaced by another editor is detected by hash; a file that no longer
  loads shows the loader errors, the last valid buffer is kept and Replay and Discard stay disabled until the file loads
  again. Replay is also unavailable after an undo past your last Save (Discard or Save as copy instead). Line endings (CRLF or
  LF) follow the file. Op log format: design doc section 9.1.
- Flowgraph overlay and live check badge (GDU-54). `--flowgraph <graph.flow.yaml>` (or, without the flag, the one file
  `flowgraphs/<spec name>.flow.yaml` or `flowgraphs/<spec name minus _vN>.flow.yaml` in this repo when exactly one of the two
  exists; `--no-flowgraph` turns the detection off) adds a "flowgraph overlay" layer (show/lock like the others) and a
  "Flowgraph" box in the side panel. The editor only READS the graph (re-read whenever the file changes; it is never
  written, not even by Save; a graph that does not load shows its errors in the panel and the editor keeps working). Edit the
  graph with `mapgen flowgraph edit`. Overlay: each bound node as a dotted disc (the radius the check uses: POI `r` and 30 m
  minimum, base 120 m, `points` 30 m) with its kind marker (spawn square, objective star, zone hexagon, choke diamond, area
  dashed circle) and "id name" label; graph edges as lines between the node positions: solid when the road/trail
  derivation satisfies the edge, long dashes with an X box when missing, dotted with a + box for an extra derived edge
  (strict mode only). Nodes whose bound feature is absent from the spec (renamed or deleted) are listed under "Unbound nodes"
  with a hollow marker and the feature they need, not drawn. The badge reads "flowgraph: OK" or "N problems" (missing
  nodes and edges; with the strict box also extra edges and nodes) and is recomputed in-process on the buffer text about
  300 ms after each edit (no file is written). Click the badge for the finding list; clicking a finding highlights the edge
  and its nodes on the map (an extra node selects its feature). "Check terrain" adds the `--terrain` findings on demand
  (seconds, background request, latest wins, shown as "computing"; they go stale with the next edit). Renaming a feature
  that a node binds (`bind`, or the default bind) does not touch the graph: a warning says the node is now unbound and to
  update `bind` in `flowgraph edit`.
- The spec must load when the editor starts (otherwise it prints the errors and exits 1).
- Painting (Paint F, Erase E; GDU-53). Pick a region type from the type table, a brush radius (slider, `[` and `]`, 1-120 m)
  and optionally `impassable`, then drag strokes on the map: a brush cursor ring (dashed for paint, dotted with a cross for
  erase) shows the radius and a translucent hatched (paint) or dotted (erase) preview follows the drag. On mouse-up the server
  turns the stroke into a polygon (`LineString.buffer(radius)`, round caps, clipped to the map, simplified at 1.5 m, vertices
  rounded to whole metres) and commits ONE undo step:
  - over empty ground or other terrain: a new `{ id: forest_3, type: forest, shape: poly, points: [[x, y], ...] }` region
    (a flow list; `impassable: true` when the toggle is on; no `seed`, no `rough`: the outline is exactly the brush) appended
    last, so it paints on top;
  - touching or overlapping a `poly` region of the same type and `impassable` flag: merged into it (its `points` are
    rewritten; id, key order, comments stay). A stroke that bridges several keeps the id of the largest and deletes the others.
    A union of several parts gives one region per part. A ring that encloses ground (a loop stroke) keeps its simple outer
    ring and appends a `clearing` poly for the hole (holes under 25 m2 are filled). A merge that a later painted `clearing`
    would hide is not done: the stroke is painted on top as a new region;
  - Erase subtracts from painted `poly` regions of the chosen type (or "any painted polygon"). A region reduced to nothing
    is deleted (its id is not reissued in the session); a split keeps the original id on the largest piece and gives the
    others generated ids; a hole becomes a `clearing` poly.
  - Only regions authored as `shape: poly` with literal points are merged or erased. Blob, band and point-reference regions
    are never rewritten silently: paint adds a new poly on top; erase reports them ("not erased: wood is a blob") and the panel
    of a selected blob or band has "Convert to polygon" (one explicit action: the base outline without rough and warp noise).
  - A ring has at most 400 vertices (the simplify tolerance doubles until it fits); a 250 m stroke gives about 15-30.
  The same strokes give the same YAML bytes.
- Scatter area tool (S): click polygon vertices, Enter; the polygon becomes the `within` of the entry chosen in the picker
  ("within of <id>") or of a new scatter entry of the chosen type (`coverage 0.4, scale 120, clump 0.4, avoid 20` and the
  `impassable` toggle, editable in the toolbar; the defaults of `SCATTER_SPEC.md`). The vertices of every `within` polygon are
  handles (drag, insert, delete, minimum 3); scatter entries keep their procedural texture inside the polygon.
- Select by click: with the Select tool a click inside a region (blob, band, poly or scatter patch, from the resolved
  geometry) selects it; clicking again cycles through the regions stacked there. Delete removes the selected region.
- Analyze panel (coarse preview): the Analyze button (or the Probe tool, key Q) computes, in a background thread on the buffer
  text and without writing any file, the real `analysis.analyze` at a coarse cell (20 m, never finer than the spec's own
  `analysis.cell`) on a 257-sample heightmap: 0.04 s on `town_square_v4`, about 0.1 s on the 3 km concept maps. Everything is
  labelled "coarse preview"; the numbers need not equal a full build's. Results are cached per buffer text, a newer buffer
  supersedes an older request, and the panel says `computing...` or `outdated` while the shown result is not the buffer's.
  Layers "analysis: territory" (who arrives first: team 1 solid grey, team 2 dots, team 3 diagonal lines, team 4 vertical
  lines, contested crosshatch, unreachable horizontal lines; the legend swatches use the same patterns) and "analysis: routes"
  (base-to-base routes thick and solid; each team's route to each objective thin, with the team's own dash pattern). The table
  gives each team's time to every objective, the spread, the flag and the verdict against `balance_tolerance`, the area shares
  (first / contested / unreachable) and the base-to-base time. The Probe tool shows, for a clicked point, each team's travel
  time, the spread, the cost, the topmost region, lines and crossings (the semantics of `mapgen probe`).
- Build button: runs the real `mapgen build` (a subprocess of the same CLI) on a snapshot of the buffer, unsaved edits
  included, into the scratch folder `out/spec_edit/<name>/` (git-ignored; the regular `out/<name>/` is never written). Stages
  (geometry, heightmap, analysis, render, export, report) stream to the panel with their seconds; Cancel kills the build and
  keeps the previous result; one build runs at a time (a second request is refused). Editing and the file watcher continue
  during a build. On success the panel shows the exit code, the report warnings, the balance table and the area shares, and
  buttons open `map.png` and every `overlay_*.png` in a full-page viewer (Esc closes); only these file names are served, never
  a path. A build is byte-identical to `mapgen build` of the same text.
- Not in the editor: `label_at` (edit in the file); the flowgraph is shown read-only (`--flowgraph`), never written.

## Editing a spec as text (`spec set`)

`mapgen spec set <spec> <path>=<value> [...]` changes values in the spec file as exact text patches: comments, quoting,
flow/block style, indentation, key order and line endings (LF or CRLF) outside the edited values stay byte-identical, so a
moved POI is a one-line git diff. All edits apply in order and atomically: the patched text is re-parsed and must equal the
old document plus exactly the requested change, then the real loader (`validate`) must accept it; otherwise nothing is
written (exit 1, same error format as `validate`). The file is replaced through a temp file and rename. `--diff` prints a
unified diff, `--dry-run` prints it and writes nothing. Exit codes: 0 ok, 1 errors, 2 usage.

```
python -m mapgen spec set town.yaml pois.fa.at=[400,120]                  # POI position
python -m mapgen spec set town.yaml points.ao=[260.5,412] teams.A.base=[110,420]
python -m mapgen spec set town.yaml 'lines.river_main.path[2]=[300,500]'   # waypoint 2 (zero-based) of a line
python -m mapgen spec set town.yaml 'lines.river_main.path[+2]=[320,480]'  # insert before position 2 ([+] appends)
python -m mapgen spec set town.yaml 'lines.river_main.path[2]=@delete'     # delete a waypoint / vertex
python -m mapgen spec set town.yaml lines.river_main.width=22 pois.fa.label="Alley"
```

Structure edits (same safety net, same style rules):

```
python -m mapgen spec add    town.yaml regions '{type: forest, shape: blob, at: [300, 400], r: 80, rough: 0.4}'
python -m mapgen spec add    town.yaml lines '{type: river, path: [[10, 10], [200, 300], [400, 350]]}'    # prints "added: river_1"
python -m mapgen spec add    town.yaml points '[260, 410]' --id ao2                                       # a named point
python -m mapgen spec set    town.yaml regions.forest_1.warp=30 regions.forest_1.label="North wood" --create   # new keys
python -m mapgen spec set    town.yaml regions.forest_1.warp=@delete                                       # delete a key
python -m mapgen spec delete town.yaml lines.river_1                      # delete a feature (the whole item)
python -m mapgen spec delete town.yaml pois.o --detach-refs               # referenced: references become literal [x, y]
python -m mapgen spec rename town.yaml forest_1 north_wood                # id and every reference
```

- `spec add <spec> <section> <value> [--id ID]`: sections `regions`, `lines`, `crossings`, `pois`, `scatter`, `points`. The
  value is a YAML flow mapping (for `points`: a pair). The id is `--id`, else the value's own `id`, else generated:
  `<type>_<n>` (`poi_<n>` for POIs, `p<n>` for points), `n` one above the largest in use for that base, never an id in use.
  The entry is appended after the last item of its section in that section's style (a flow mapping line like its neighbours;
  block lines when the section is written as block mappings) with the keys ordered `id`, `type`, `shape`, then as given. A
  missing section is created before `analysis`/`output` (or at the end of the file) after a blank line. No `seed` is written:
  the feature's noise derives from its id (`mapgen/IDENTITY_SPEC.md`).
- `spec delete <spec> <path>...` removes a feature (`pois.fa`, `lines.river_2`, `points.ao`) with its whole line(s) and
  trailing comment, or a key (`regions.wood_1.rough`). A feature that others reference is refused and the referrers are
  listed; `--detach-refs` replaces each point reference by the literal coordinates and removes a `join` (and `join_end`).
  Deleting the only item of a section removes the section key as well. Team bases cannot be deleted.
- `spec rename <spec> <old> <new>` rewrites the definition (a feature `id` or a `points` key) and every reference (region
  `at`/`points`/`path`, line `path`/`join`, crossing `at`/`path`). A region, line or scatter entry without `seed` gets
  `seed: <feature_seed(old)>` so its shape is unchanged; refused when `<new>` is in use or invalid, the old id is defined twice,
  or it is a team base. Flowgraph `bind` values live in `.flow.yaml` files and are not rewritten.
- All four print `added:`, `detached:`, `references rewritten:`, `seed pinned:` lines; `--diff` and `--dry-run` as for `spec set`.

Path grammar: keys joined by `.`; a feature list (`pois`, `regions`, `lines`, `scatter`, `crossings`) is addressed by the
feature's `id` (`pois.fa.at`), never by position; `[n]` is a zero-based position inside a sequence without ids (waypoints,
vertices); `[+n]` / `[+]` as the last token inserts. Values are JSON, or YAML flow syntax (`[base_A, [1,2]]`, bare words).
Numbers are written as integers when whole, otherwise as the shortest decimal. Quote the argument in a shell when it
contains `[` or spaces.

A point reference (a name where a point is expected, e.g. `path[3]` holding `j`) set to `[x, y]` moves the referenced
definition (`points.<name>`, a POI `at`, a team `base`), so every referrer follows; `--detach` writes the literal at the
vertex instead. Setting a literal pair to a name attaches it. A key the mapping lacks is added with `--create` (appended to a flow mapping, a new line in a block mapping), removed with
`path=@delete`. Refused with a typed error: anchors, aliases, merge keys, duplicate keys, block or multi-line scalars,
several YAML documents, and comments in the way of a deletion. Engine: `mapgen/specpatch.py`; design:
`docs/investigations/2026-11-19-map-editor-design.md` section 4.

## Edit loop

1. Edit the YAML in `maps/` (see the examples; `maps/example_natural.yaml` uses every natural-shape key; `types.yaml`
   lists the available types and costs). Every region, line, scatter entry and crossing needs an `id`; insert a feature
   anywhere in its list, list order of `regions` is only paint order.
2. `build`, then look at `out/<name>/map.png`, the `overlay_*.png` files and `report.md`.
3. Edit again.

## Tests

`.venv/Scripts/python -m pytest` runs the regression suite (about 9 minutes with `-n 8`; 1464 tests, 524 s): every committed map is
built and compared file by file with its baseline in `tests/baselines/`, plus spec-validation and
exit-code tests. `-k <map name>` runs one map. After the owner has approved the
`mapgen diff` report of an output change,
`.venv/Scripts/python tests/rebaseline.py <map>... | --all` rewrites the baselines (`--dry-run` lists
the differing files without writing). Details: `tests/REGRESSION_SPEC.md`.

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
| `export/spec.resolved.json` | the spec with every default applied, scatter regions and automatic crossings expanded; engine importers read this instead of the YAML; point references appear as coordinates |
| `export/meta.json` | sizes, seed, spec hash, mapgen version, `terrain_layers` (`{name, channel, image}` per splat layer), `water_level` (`null`: lakes and rivers have per-feature surfaces) |

## Natural shapes, scatter, palette

Optional keys (full definitions in `MAPGEN_SPEC.md` §4.2, §4.3, §4.6, §5.1, §6.2):

| key | where | effect |
|---|---|---|
| `warp`, `warp_scale` | region | metres; 2D domain warp of the outline (lobes and bays). Defaults: blob `0.3*r` / `r`, band `0.25*width` / `width`, poly `0` |
| `fragment`, `fragment_scale` | region | `fragment` 0-1 = fraction of area removed as clearings and ragged edges; `fragment_scale` = patch size in metres (150) |
| `impassable` | region | `true` makes the region a wall: cost infinity whatever the type (line of sight and height follow the type). Drawn with its type colour plus a diagonal strike-through hatch, legend entry "impassable"; `regions.json` carries `"impassable": true` on such regions only. Default `false`; non-boolean values are errors |
| `width_var` | line | 0-1; width varies along the line (`width * (1 + width_var * 0.6 * noise)`) |
| `scatter` | top level | list of `{id, type, coverage, scale, clump, avoid, within, on, impassable}`: noise patches of one region type across the map, clear of lines, crossings, POIs, lakes, mountains and towns by `avoid` metres; painted under the explicit regions; `coverage` is the share of the entry's area (map cut to `within`) above its noise threshold, so eligible ground is covered at about that share where it spans many patch widths (`scale`) and an entry whose eligible ground is a sliver can yield no patches, and an edit to another feature changes only the patches near that feature; `impassable: true` (default `false`, bool only) makes the patches walls like an impassable region (cost infinity, strike-through hatch, same export field) and widens `avoid` to at least two analysis cells so roads, trails and crossings stay walkable |
| `terrain.detail`, `terrain.detail_scale` | terrain | metres; mid-frequency ground undulation (6 m / 250 m) |

Mountains get a foothill apron; `band` mountains default to `rough: 0.35`; overlapping mountains combine by maximum,
not sum.

## Authoring keys

| key | where | effect |
|---|---|---|
| `format_version` | top level | required; `1`. mapgen reads the versions `--version` prints |
| `id` | region, line, scatter, crossing, POI | required; `[a-z][a-z0-9_]*`, unique across the whole spec (bases are `base_<team>`). The feature's identity: noise seeds and exported ids come from it. Renaming an id reshapes the feature unless it has a `seed` |
| `points: {name: [x, y]}` | top level | named positions shared by several features that are neither POIs nor bases (road junctions, a crossing on a river point). A name is an id: `[a-z][a-z0-9_]*`, unique across the spec |
| point reference | region `at`/`points`/`path`, line `path`, crossing `at`/`path` | a name in place of `[x, y]`: a POI id, `base_<team>` or a `points` name, e.g. `path: [base_B, [2150, 2300], lea_crossroads]`. It resolves to that position when the spec loads, so moving the POI, base or named point moves every feature that refers to it. An unknown name is a validation error |
| `seed` | region, line, scatter | optional integer 0-4294967295; fixes the feature's noise independently of its id. The committed maps carry one on every feature |
| `meander`, `meander_scale` | line | `meander` = sideways wiggle amplitude (m); `meander_scale` = wavelength (m, default 300). Use a coarse path plus these instead of hand-zigzagged control points |
| `wobble`, `wobble_scale`, `smooth` | line | road/trail shaping. `wobble` = sideways amplitude (m, default 0 = off), `wobble_scale` = wavelength (m, default 200): seeded low-frequency noise on a smooth spline that passes exactly through every waypoint; the first/last point and every point reference (POI id, `base_<team>`, `points` name) stay exactly on the line, the wobble fades to zero around them. `smooth: true` alone gives the spline without noise (default: Chaikin corner cutting, which cuts inside every waypoint). Values: 800 m map `wobble` 15-25, `wobble_scale` 150-200 (trails 20-30); 3000 m map `wobble` 40-70, `wobble_scale` 400-500. Use 3-6 waypoints per road, not a hand-drawn ring; `meander` (rivers) displaces waypoints and tapers the ends, so prefer `wobble` for roads and trails |
| `join: <line id>`, `join_end: start\|end` | line | tributary: that end of the line is moved onto the nearest point of the parent's centreline (parents are built first; cycles are errors) |
| (automatic) | river x lake | rivers are clipped outside lakes, so a river ending inside a lake leaves no stub; lakes and rivers render as one water body |
| `auto_crossings` | top level | default `true`: every road x river gets a `bridge`, every trail x river a `ford` (disc, `r` = river half-width + 15 m, id `<line id>-<river id>-<n>`), and roads/trails that enter and leave a lake get a ford band. An explicit crossing over an intersection is kept (write one to ford a road). `false` turns it off and analysis warns about uncovered intersections |
| `path` + `width` | crossing | band crossing instead of `at` + `r`: the buffered path. For passes through a long mountain or long fords |
| `wall` | line type | cost inf, blocks line of sight, width 8; drawn as a pale band on a dark casing with crenellation ticks. Emitted by `town generate` around a settlement; a `gate` crossing opens it |
| `gate` | crossing type | cost 0.6, `r` 12; overrides the impassable `wall` under it; drawn as an arch. One per wall opening |
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
