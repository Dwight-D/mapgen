# Interactive map-spec editor: design

- Date: 2026-11-19
- Backlog item: GDU-49 (mapgen workstream). Design only; no code, spec or map was changed.
- Slice status: slice 1 (GDU-50) implemented: `mapgen/specpatch.py`, `schema.load_spec_text`, `mapgen spec set` (grammar in `MAPGEN_SPEC.md` section 7), `tests/test_specpatch.py`. `spec_model.py` is not part of the slice (the id and reference lookups live in `specpatch.py`). Slice 2 (GDU-51) implemented: `mapgen spec-edit` with `editor_http.py` (guards shared with the flowgraph editor), `spec_model.py`, `spec_editor.py`, `spec_editor_page.py`, `tests/test_spec_editor.py`, `tests/browser/spec_editor_drive.mjs`; handles for POIs, bases, `points`, line waypoints, blob centres and poly/band vertices, reference-aware drag and Alt-detach, waypoint insert/delete, undo/redo, atomic Save; preview image tier T2 (geometry JSON and image fetched separately, latest wins); T1 `/api/preview` during drags is client-side only. Slice 3 (GDU-52) implemented: the engine gains `Put` (new key), key and feature `Delete` (refused while referenced, or `detach_refs`), `Add` (generated unique id, flow and block style, missing section created) and `Rename` (every reference, seed pinned); `mapgen spec set --create`, `spec add | delete | rename`; the editor gains blob radius grips, band/line/crossing width grips, vertex insert (+ handle, double-click) and delete on every chain, add-feature tools with type pickers, delete with the referrer prompt, a schema-driven property panel, rename, layer show/lock, error-to-feature highlighting and click cycling of stacked handles; id generation uses one above the largest number in use for the base (not the smallest free number) and never reissues an id deleted in the session; `tests/test_specpatch_structure.py`, API and browser-drive extensions. Not in slice 3: scatter `within` vertex handles, snapping to existing features, painting (slice 4), flowgraph layer and `bind` rewrite on rename (slice 5). Slice 4 (GDU-53) implemented: `mapgen/paint.py` (brush polygon: buffer, simplify 1.5 m, integer metres, clip to the map, 400-vertex ring cap; merge, split, erase, hole and convert planning over the span-patch engine), `POST /api/paint` (modes paint, erase, convert; one undo step), the Paint, Erase and Scatter-area tools with brush cursor and stroke preview, scatter `within` vertex handles, click-inside-region selection, `tests/test_paint.py`, `tests/browser/paint_drive.mjs`. Decisions: merge and erase targets are all `shape: poly` regions with literal points (not only ids of the form `<type>_<n>`); holes (merge or erase) append a `clearing` poly (holes under 25 m2 are filled); a merge that a later painted `clearing` would hide paints the stroke on top as a new region; a blob, band or reference poly is never rewritten, erase reports it and "Convert to polygon" is the explicit action; painted regions carry no `seed`, no `rough`; send-backward is not built. Slice 5 (GDU-54) implemented: `mapgen spec-edit --flowgraph` (or the unambiguous `flowgraphs/<name>[_vN]` auto-detect; the editor only reads the graph, never writes it), `mapgen/spec_editor_flowgraph.py`, `POST /api/flowgraph` and `/api/flowgraph_terrain`, the flowgraph overlay layer (node discs, kind markers, solid/dashed/dotted edges with X and + boxes, unbound-node list), the live check badge with expandable findings, strict toggle and on-demand terrain check, the rename warning, `flowgraph_check.derive_edges`/`check` taking the buffer's raw line paths, `tests/test_spec_editor_flowgraph.py`, `tests/browser/flowgraph_drive.mjs`. Decisions: node disc radii are the radii the derivation uses (a `points` entry is 30 m, not the 25 m of the terrain check); extra edges and nodes count only in strict mode; the terrain findings are not part of the cached overlay. Slice 6 (GDU-55) implemented: disk watcher (`SpecEditor.check_disk`, watcher thread, `/api/poll`), `/api/resolve` (replay | discard | copy), the op log, the page banner, `tests/test_spec_editor_external.py`, `tests/browser/external_drive.mjs` (protocol in section 9.1; the uncommitted-changes notice is not implemented). Slice 7 open.
- Checked against: commit `aeb0b56`, `maps/iterations/town_square/v3_natural/town_square_v3.yaml`
- Statements marked **[measured]** come from running the tool on this machine; **[verified]** from reading the code.

## 1. What the editor is

`mapgen spec-edit <spec.yaml>` starts a local web editor for one map spec. The browser shows the map and draws every
feature of the spec on top of it as a vector overlay: POIs, bases, `points`, line waypoints with the generated centreline,
region outlines, scatter areas, crossings. The author drags, adds and deletes handles and paints forest and mountain.
Every edit is written back into the YAML file as a minimal text patch that changes only the edited values; comments,
flow-style entries, point references, key order and line endings stay byte-identical. Every edit is validated by the real
loader (`load_spec`) before it can be saved. The spec stays the single source of truth; the editor is a view and an
input device for it.

Goals: hand-tweak LLM-generated drafts (town_square v1..v4) to an exact result; keep git diffs reviewable (a moved POI is
a one-line diff); keep the build deterministic and the committed baselines untouched.

## 2. Facts the design rests on

| fact | source |
|---|---|
| Specs are comment-rich, hand-written YAML with flow-style entries (`- { id: fa, ..., at: [395, 265] }`), point references (`path: [base_A, [180, 425], ao]`), a `points` mapping, `maps/**/*.yaml` stored LF | v3 spec, `.gitattributes` |
| PyYAML is the only parser; ruamel is not installed; dependencies are pinned exactly and output bytes depend on them | `requirements.txt`, `CLAUDE.md` |
| `load_spec(path)` reads bytes, parses with `_SpecLoader`, validates, hashes the file bytes into `spec_sha256`; errors are strings prefixed with a list-index path (`lines[3].path[1]: ...`) | `schema.py:994-1008`, `_V.err` |
| Features have ids and feature seeds derived from the id; nothing keys on list position; list order of `regions` is paint order only | `IDENTITY_SPEC.md` |
| A reference is a string where a point is expected: region `at`/`points`/`path`, line `path`, crossing `at`/`path`. POI `at`, team `base`, `points` values, `label_at` and scatter `within` are literal | `REFERENCES_SPEC.md` 3.2 |
| `poly` regions are one ring without holes; `impassable: true` is allowed on regions and scatter entries; later regions paint over earlier ones | `schema.py`, `MAPGEN_SPEC.md` 4 |
| Line shaping (`meander`, `wobble`, `smooth`) is generated from the waypoints; references stay exactly on the line (`Line.fixed`) | `geometry.py` `line_centreline`, `_wobbled` |
| Flowgraph editor (GDU-36): stdlib `ThreadingHTTPServer` on 127.0.0.1, per-run token header, Host and Origin checks, CSP, 1 MiB body cap, JSON API, vanilla JS and inline SVG page, atomic save, pytest HTTP tests plus a headless-Edge DevTools drive | `flowgraph_editor.py`, `tests/browser/` |

Timings, town_square_v3 (800 x 800 m, `analysis.cell` 5), **[measured]**:

| stage | full settings | preview settings |
|---|---|---|
| `geometry.build_geometries` | 0.34 s | 0.34 s (same code) |
| `height.synthesize` | 11.5 s at `heightmap_res` 1025 | 0.33 s at 257, 0.07 s at 129 |
| `analysis.analyze` | 0.18 s at cell 5 | 0.06 s at cell 10, 0.03 s at cell 20 |
| `render_core` (map image) | 14.3 s at `render_px` 1536 | 3.7 s at 768, 1.6 s at 512 |
| one overlay (`overlay_travel`) after the core | 0.35 s | 0.05-0.11 s |
| `build` all stages | 35 s (render 18 s, export 5 s) | not used |

Geometry is cheap; the heightmap at full resolution and the raster render are what make a build slow. This drives the
architecture: the true shapes of every feature are recomputed per edit by the real geometry code and drawn as vectors; the
raster is only a backdrop.

## 3. Architecture (question 1)

**Decision: a Python HTTP server in the flowgraph-editor pattern, a vector overlay drawn in the browser over a
server-rendered preview image, the YAML text buffer owned by the server.**

Components (new modules under `mapgen/`):

| module | role |
|---|---|
| `editor_http.py` | the guard code now in `flowgraph_editor.py` (loopback bind, token, Host/Origin check, CSP, body cap, JSON helpers, atomic write) extracted into a base class used by both editors; no behaviour change for the flowgraph editor |
| `specpatch.py` | the YAML span-patch engine (section 4); no HTTP, no browser; also exposed as `mapgen spec set` for agents |
| `spec_model.py` | feature index: id to node spans, kind, reference graph (who references which named point), id generator |
| `spec_editor.py` | server: buffer, undo stack, validation, preview, save, file watch |
| `spec_editor_page.py` | the page: vanilla JS, one inline SVG over an `<img>`; no external URLs |

The server holds `buffer` (current YAML text), `disk_hash` (hash of the file as last read or written) and `history`
(list of earlier buffers). The browser holds no spec state beyond the last server response, so a reload loses nothing.

What the browser draws, back to front:
1. Preview image (server-rendered PNG, section 6), north-up, a mode switch: terrain map, travel overlay, territory, none.
2. Region and scatter-region outlines: resolved polygons from the real geometry (rough, warp, fragment applied), drawn
   as thin outlines tinted by type, plus the author's base shape (blob ellipse, band path) as a dashed guide.
3. Line centrelines (resolved meander/wobble result) and, in a stronger style, the author's waypoint polyline.
4. Handles: waypoints, POIs, bases, `points`, blob centres and radius grips, band path vertices and width grips, poly
   vertices, label pins, scatter `within` vertices.
5. Flowgraph layer (section 8) and validation markers (section 9).

Handles are the spec's own values, never generated ones: line waypoints (the meander and wobble curve follows), poly
vertices, blob `at`/`r`. Resolved geometry is drawn but not draggable.

Edit flow:
1. During a drag the client moves the handle and its dependants locally (rubber band on the waypoint polyline).
2. About 4 times per second the client posts `POST /api/preview {ops}` (a non-committing dry run on a copy of the buffer);
   the server patches, validates, runs geometry, returns resolved shapes as JSON (about 0.3-0.8 s; the client drops
   stale replies and keeps one request in flight).
3. On release the client posts `POST /api/apply {ops, buffer_version}`; the server patches the real buffer, pushes the
   previous text on `history`, and returns the new state (feature list, resolved shapes, errors).
4. `POST /api/undo`, `/api/redo`, `/api/save`, `/api/reload`, `/api/image` complete the API. All POST, JSON, token header,
   same guards as the flowgraph editor. The image comes back from `/api/image` as PNG bytes fetched with `fetch` and shown
   through a blob URL (the token cannot ride on an `<img src>`); the CSP gains `img-src blob:`.

An op is addressed by feature id and path, never by list position:
`{"op": "move_point", "ref": ["lines", "river_mere", "path", 2], "to": [604, 436]}`. Ops replay onto a changed buffer
(section 9).

## 4. YAML round-trip (question 2)

### 4.1 Options

| option | verdict |
|---|---|
| (a) add pinned `ruamel.yaml` | Round-trips comments, but it re-flows flow-style entries and indentation in places, adds a dependency to a build whose bytes depend on pinned versions, and still needs span knowledge to keep diffs tiny. Rejected as the primary mechanism. |
| (b) **targeted text patching of exact spans using `yaml.compose` marks** | Chosen. No new dependency; the parser that defines validity also locates the spans; untouched bytes are untouched by construction. |
| (c) canonical re-emit | Destroys comments (the v3 header, section comments and `brief` are the design record). Rejected. |
| (d) sidecar overlay applied at load | Splits the source of truth, makes `spec_sha256`, the flowgraph check (which reads the file) and every other tool see a different map than the file, and the owner never reviews the edit in the spec diff. Rejected. |

### 4.2 Mechanism

`yaml.compose(text, Loader=_SpecLoader)` returns the node graph. Every `ScalarNode` has `start_mark.index` and
`end_mark.index`, character offsets into the decoded text. An edit is a list of `(start, end, replacement)` spans applied
in descending offset order to the original string. The file is read as bytes, decoded as UTF-8 with no newline
translation, patched, encoded and written back, so CRLF or LF is preserved with no special handling. Inserted text uses
the file's dominant line ending when it contains a newline.

Operations and their spans:

| operation | span edited | notes |
|---|---|---|
| set scalar (number, `r`, `width`, `rough`, type, label) | the scalar's span | number formatting: integer text when the value is whole, otherwise the shortest of `%.1f`/`%.2f`; quoting kept as found |
| move literal point `[x, y]` | the two scalar spans | separators and spacing untouched |
| move a reference point | none at the vertex; the span edit is on the target (`points.<name>`, a POI `at`, a team `base`) | one edit moves every referrer; the UI shows "moves N features" |
| detach / attach a reference | replace the string scalar by `[x, y]`, or the `[x, y]` item by a name | the attach target is picked by snapping |
| insert vertex in a flow sequence | zero-width insertion `, [x, y]` after the previous item's `end_mark` (or before the first item) | one line grows |
| delete vertex in a flow sequence | the item plus one adjacent comma and the whitespace after it | |
| insert / delete in a block sequence | whole line(s) copied from the neighbouring item's indentation; a trailing comment on a deleted line goes with it | |
| add a feature | a new line appended after the last item of its section, matching the section's style (flow mapping when the section's items are flow mappings) | appended last so existing entries keep their place and paint order |
| delete a feature | the whole item's lines, plus a directly attached comment line only when it sits on the same line | section comment lines are never removed |
| reorder `regions` (paint order) | cut and paste of the item's lines | legal because nothing keys on position |

Refusals (the editor shows "edit this by hand" and never guesses): the target node has an anchor, alias or merge key
(composed aliases share one node object, so a span would change several places; detected by node identity), is a block
scalar, a multi-line flow scalar, or the document has several YAML documents. None of these occur in the committed specs.

### 4.3 Safety net on every patch

After computing the new text the engine (1) re-parses it with `_SpecLoader` and compares the resulting Python value with the
old one: only the intended paths may differ, with exactly the intended values; (2) runs the real validator; (3) for the
tests, asserts that every comment line is byte-identical. A mismatch refuses the edit and leaves the buffer unchanged.

### 4.4 Prototype result

A throwaway script (scratchpad, not committed) moved `pois.fa.at` ([395, 265] to [401.5, 270]), `river_mere` waypoint 2
([598, 430] to [604, 436]) and `points.ao` ([255, 410] to [260.5, 412]) in the real v3 spec by span patching. Result
**[measured]**:
- the unified diff has exactly 3 changed lines (the `ao` line, the `fa` line, the `river_mere` line), each changing only the
  edited numbers; the 3 long comment lines of the header and every section comment are unchanged;
- a deep comparison of the old and new loaded values shows exactly the 6 intended number changes;
- the patched file passes `schema.load_spec`;
- the same edits on a CRLF copy keep every line ending;
- composed aliases (`&x`/`*x`) share one node object, so the refusal rule is implementable.
Insertion and deletion in flow and block sequences were not prototyped; they use the same marks and are covered by the
property tests of slice 1.

### 4.5 Consequences for review in git

A drag is a one-line diff. A saved session is the set of lines whose coordinates moved. `spec_sha256` in `meta.json`
changes with any edit, as it does for any text change today. An editor session that moves nothing writes nothing (save is
a no-op when the buffer equals the disk text).

## 5. Editing primitives (question 3)

Id and reference behaviour apply to every row. Snap options: grid (1, 5, 10, 25 m; default 1 m so values are integers, the
style of the committed specs), to existing POI/base/`points`/waypoint (within 8 screen px; attaching offered, not
automatic), to a line segment (for crossings), angle lock with Shift.

| feature | handles | spec keys changed | reference behaviour |
|---|---|---|---|
| POI | disc | `pois[id].at` | moves every referrer; label pin `label_at` is literal and offered as "move with" |
| base | disc | `teams.<t>.base` | same |
| `points` entry | diamond | `points.<name>` | same; deleting is refused while referenced (the UI lists referrers) |
| line (river, road, trail, cliff) | waypoints, midpoint "+" handles | `lines[id].path[j]` | a reference waypoint drags its target (default) or, with Alt, is detached to a literal copy; waypoint insertion adds a literal; a tributary's `join` end is shown locked (the build snaps it) |
| line shaping | slider in the inspector | `width`, `meander`, `meander_scale`, `wobble`, `wobble_scale`, `smooth`, `width_var` | none |
| blob region | centre, radius grip, squash/angle grip | `at`, `r`, `squash`, `angle` (`rough`, `warp`, `height` in the inspector) | a reference `at` drags its target; the shape then follows the POI |
| band region | path vertices, width grips | `path[j]`, `width` | as lines |
| poly region | vertices, edge midpoints | `points[j]` | as lines |
| scatter entry | `within` polygon vertices (literal only) | `within[j]` | none; `coverage`, `scale`, `clump`, `avoid`, `impassable` in the inspector |
| crossing | disc or band vertices | `at`, `r`, or `path[j]`, `width` | as above |
| new feature | tool palette (POI, line of a type, blob, poly, crossing) | appends an entry | id from the generator |

Rules:
- **Id generation:** `<type>_<n>` for regions and lines (`forest_3`, `road_2`), `poi_<n>` for POIs, `p<n>` for points; `n` is
  one above the largest number in use for that base (so a gap left by a deletion is not refilled) and the id is unused in the single id namespace (bases, `points`, regions, lines,
  scatter, crossings, POIs; charset `[a-z][a-z0-9_]*`, at most 64 characters). The feature seed is not written: it derives
  from the id (`IDENTITY_SPEC.md` D1), so a new feature's noise is independent of everything else. The inspector renames an
  id and rewrites every reference to it (references, `join`, `join_end` users, the flowgraph `bind` when a flowgraph is
  loaded); a rename is refused when a reference lives in a place the engine cannot patch.
- **Delete:** a feature referenced by others (a POI used as a waypoint, a line used by `join`, a crossing used by a flowgraph
  bind) shows its referrers and requires a choice: detach referrers to literal coordinates, or cancel.
- **Undo/redo:** server-side stack of whole-buffer texts (specs are tens of kilobytes). Undo restores a previous exact text, so
  it needs no inverse operations. One drag, one paint stroke or one inspector edit is one undo step. The stack is cleared on
  save only when the user chooses; by default it survives saves.
- **Layer lock and visibility:** per kind (regions by type, lines by type, POIs, points, crossings, scatter, flowgraph,
  guides, image) with eye and lock toggles; locked layers are drawn but not hit-testable. Stored in `localStorage` per
  spec path, not in the spec.
- **Selection and inspector:** click selects by id; the inspector lists the feature's keys with the schema's constraints and
  edits them through the same ops; the error list highlights the selected feature's errors.

### 5.1 Painting forest, mountain and other regions

The spec is vector-based, so a stroke becomes a vector region. Decision: **a brush stroke creates or extends a `poly` region
of the brush's type**.

- Brush: type (forest, mountain, hills, field, marsh, lake, town, ruins, clearing), radius in metres, optional
  `impassable` flag (for stands of woods that must block movement, as v3's `scatter` stands do), `rough`/`warp`/`fragment`
  defaults from the type's usual values.
- On stroke end the client sends the stroke polyline and radius. The server builds the area with Shapely
  (`LineString.buffer`), simplifies it (tolerance 3 m), rounds vertices to integer metres, and:
  - **merge:** if the area touches or overlaps an existing editor-painted region of the same type and `impassable` flag,
    it is unioned into it and that region's `points` are rewritten (the union is one ring);
  - otherwise it appends a new region `{ id: forest_4, type: forest, shape: poly, points: [...], rough: 0.25 }` after the
    last region. Where the union would be several parts or a ring with a hole, one region per part is emitted and a hole is
    split into a ring through a bridge cut or a second `clearing` region, whichever the owner's preference setting names
    (default: `clearing` poly appended, which keeps the forest ring simple).
- **Editor-painted regions** are those whose id matches `<type>_<n>` as generated. Hand-authored regions are never merged
  into or erased from silently.
- **Erase:** subtracts the brush area from the painted `poly` regions of the chosen type that it touches (a region may split
  into several; a region that vanishes is deleted). Erasing over blob, band or scatter areas offers "convert to poly" (the
  base outline without its rough/warp noise, an explicit visual change) or appends a `clearing` poly of the stroke.
- The paint order of the new region is the end of the list so it paints over earlier regions; "send backward" moves the
  entry earlier in the list.
- **Scatter areas** are edited, not painted: drawing a polygon sets or edits a scatter entry's `within`, and the stand keeps
  its procedural texture. The brush tool is the choice for exact control; the scatter polygon tool for texture.

Why not the alternatives: paint-to-raster would need a new spec key and a new pipeline stage; `scatter` per stroke would give
procedural coverage that does not follow the brush exactly, which the owner's "hard to steer to an exact result" rules out.

## 6. Fast preview (question 4)

Tiers, each a server computation on an in-memory copy of the spec (the preview never writes `out/` and never touches the
file):

| tier | trigger | content | cost |
|---|---|---|---|
| T0 | during a drag | client-only rubber band of the waypoint polyline and handles | 0 |
| T1 | drag at about 4 Hz, on release, every apply | `load_spec_text` + `build_geometries`: every region and scatter polygon, line centrelines, crossings (auto crossings included), warnings | 0.3-0.8 s **[measured]** |
| T2 | 300 ms after an apply, debounced, newest wins | preview image: `heightmap_res` 257, `render_px` 768 (or 512), `render_core` | about 4 s at 768, 1.7 s at 512 **[measured]** |
| T3 | button | coarse analysis: `analysis.cell` 20 (about 0.1 s) giving travel times, territory, base-to-base time and objective table as a side panel and as overlay modes (`overlay_travel` etc. cost 0.05-0.11 s after the core) | under 1 s on top of T2 |
| T4 | button, explicit | real `mapgen build` into `out/<name>/` with the spec's own settings (35 s on v3), reported as progress; the result is the only authority for balance numbers and exports | 10-35 s |

Rules:
- The preview image is labelled with its settings; a diagonal "preview" watermark is drawn so it is never mistaken for a
  build. The unchanged last T2 image stays under the vector overlay while a new one renders, so the overlay never lags the
  image by more than the render time and shapes are always current because they come from T1.
- Preview settings are applied to a deep copy of the loaded `MapSpec` (`output.heightmap_res`, `output.render_px`,
  `analysis.cell`); the committed spec is not changed. Terrain from a coarser heightmap differs in detail from a full
  build; balance numbers from T3 are indicative and T4 is authoritative.
- The geometry cache key is `(sha256 of buffer text)`; identical buffers (undo to a previous state) hit a small LRU. No
  finer incremental recompute: geometry is fast enough, and per-feature invalidation would need the scatter quantile and
  line-join couplings (`map-editability.md` 3.2) modelled in the editor, which is a cost with no measured benefit.
- Determinism: preview code is the build code with different resolution parameters. It adds no randomness, no timestamp and no
  unordered iteration to anything that is written to disk; the only files the editor writes are the spec itself and, on
  request, `out/` through the normal build.

## 7. Spec loading from text

`schema.load_spec(path)` is split into `load_spec_text(data: bytes, path_hint)` and a one-line wrapper. The split is
byte-neutral: the regression suite passes unchanged and a test asserts that `load_spec_text(bytes)` and `load_spec(path)`
give equal `spec.resolved.json` for the five concepts and three examples. `flowgraph_check._raw_line_paths` reads the
spec file again; it gets a text variant so a live check runs on the buffer, not the disk.

## 8. Flowgraph integration (question 5)

The editor takes `--flowgraph <file>`; without it, it looks for `flowgraphs/<spec name without a trailing _v<N>>.flow.yaml`
and the flowgraph named in the spec's brief, and runs without the layer when none is found.

- **Nodes:** each node is drawn as a disc at the bound feature's position (`flowgraph_check.spec_features`) with the node id
  and kind glyph; nodes whose `bind` is missing from the spec are listed in the badge and marked on the node list.
- **Edges:** the graph's edges as dashed straight links between node discs, coloured by status from `check()`:
  present, `missing edge` (in the graph but no road/trail derives it; pattern-coded, not hue-coded: the owner is red-green
  colour-blind), `extra edge` (derived from the spec but not in the graph). Derived edges are also drawn along the road/trail
  waypoint chain that creates them.
- **Live check:** after every T1 the server runs `flowgraph_check.check(graph, spec)` on the buffer (no `--terrain`; that
  option is a button) and returns `{ok, missing_nodes, missing_edges, extra_edges, extra_nodes}`. The status badge shows
  `OK`, or `FAIL: 1 missing edge`, using the strictness chosen in a toggle (`--strict` semantics). Clicking a finding selects
  and pans to the failing edge.
- **Direction:** the editor reads the flowgraph and never writes it in these slices; the flowgraph editor (GDU-36) owns
  `.flow.yaml`. Dragging a bound POI moves the node disc automatically because it follows the feature.

## 9. Validation and safety (question 6)

- **Live validation:** every preview and apply runs the real loader on the patched text. The response carries `errors`, each
  with the parsed path (`lines[3].path[1]`) mapped to a feature id and handle through the compose index, so the error list
  highlights the feature and the inspector field. Warnings from geometry and analysis (POI on impassable cell, road over water
  without a crossing) are shown separately in the same list.
- **Invalid states:** a drag that would give an invalid spec (a point out of bounds, a duplicate id) is shown red and its
  `apply` is refused with the loader's message; the buffer never holds an invalid spec through the editor. A spec that is
  invalid on disk at start opens read-only with its errors listed until it loads.
- **Save:** `POST /api/save` refuses when the buffer is invalid or the disk hash differs from `disk_hash`; otherwise it writes
  atomically (temp file in the same folder, `os.replace`) preserving the file's line endings and updates `disk_hash`. Save is
  explicit (button, Ctrl+S); an unsaved-changes marker and a "buffer differs from disk" diff view (the span list, which is
  tiny) are always available. No autosave to the spec.
- **Undo and backup:** the undo stack covers the session; git covers everything else. The editor never runs git and never
  creates backup files in the repo. At start, if the spec has uncommitted changes (`git status --porcelain <file>`, read-only),
  the page shows "spec has uncommitted changes" so the owner can commit first and get a clean undo point.
- **File changed on disk (slice 6, GDU-55, implemented):** see section 9.1.
- **Network:** unchanged from the flowgraph editor: loopback only, token, Host and Origin checks, CSP, body cap.

### 9.1 External-change protocol and the op log (slice 6)

**Baseline.** `disk_hash` is the SHA-256 of the file as loaded or last saved by this session. A watcher thread (every 0.5 s)
and `POST /api/poll` (the page calls it every second) re-read the file and compare the hash; mtime and size are not trusted, so
a rename-over by another editor and a same-size rewrite are caught, and a rewrite with identical bytes is not a change.
Save refuses with 409 whenever the file's hash differs from the baseline or the file is missing. A successful Save sets the
baseline, so the session's own write is never reported.

**States** (`state.external`, null when the file equals the baseline):

| disk | buffer | behaviour |
|---|---|---|
| changed, loads | clean | reload at once; history and op log cleared; `state.notice` = reloaded (the page shows the banner) |
| changed, loads | dirty | `external.kind = changed`: Replay / Discard / Save as copy / Cancel |
| missing (deleted, renamed away) | any | `kind = deleted`: Save as copy only; the file returning with the baseline bytes clears it |
| changed, does not load or not UTF-8 | any | `kind = invalid` with the loader errors; buffer kept; Replay and Discard refused until the file loads (then it becomes `changed`, or reloads when clean) |

`POST /api/resolve {action, version}`: `replay` | `discard` | `copy`. Copy writes the buffer with an exclusive create to
`<name>.edited<N>.<ext>` (N = 1, 2, ... first free) and changes nothing else; the pending state stays. Replay and discard
adopt the disk text as the new baseline.

**Op log.** `SpecEditor.oplog` holds one entry per committed edit, parallel to the undo stack: `{"ops": [...], "saved": bool}`.
The ops are the wire ops of `specpatch.op_from_json` (`set`, `put`, `insert`, `delete`, `add`, `rename`), exactly what
`/api/apply` received (paint, erase, convert and scatter-within are planned by the server into the same ops, so they are
logged identically). Every op is addressed by feature id (`pois.fa.at`, `lines.river.path[2]`, `regions.r1.points`,
`scatter.wild_wood.within`); only vertex ops also carry a sequence position inside the feature. An `add` is logged with the id
it received (a generated id is written into the entry) so later ops of the session that name it replay onto the same id.
Paint rewrites of a polygon are `set regions.<id>.points`; they apply when the region still exists, otherwise they are
skipped. Undo moves the last entry to `redo_oplog`, redo moves it back, a new edit drops the redo entries, Save marks all
entries `saved` (they are in the file, Replay does not re-apply them). If an undo pops a `saved` entry the buffer differs
from the file by a revert the log cannot express: Replay is refused (`can_replay` false, `replay_blocked` says why) and
Discard or Save as copy remain.

**Replay.** Starting from the new disk text, each unsaved entry is applied as a unit (engine patch, then the real loader). A
unit that fails is retried op by op; ops that still fail are skipped and reported as `{op, text, reason}` (target id gone, a
`PatchError`, or a result the loader rejects). The remaining ops stay applied. The result is the buffer (dirty against the new
baseline, loader-valid), the undo stack is `[disk text]` (one undo step back to the disk text) and the op log is the applied
ops as one entry.


## 10. Testing (question 7)

| layer | tests |
|---|---|
| `specpatch` unit and property tests (no server) | for every committed spec (8 maps, the iterations, flowgraph fixtures): identity edit of every point leaves the bytes identical; random single-value moves change exactly the intended span and no comment line; every operation of 4.2 on flow and block style, on LF and CRLF copies; refusal cases (alias, anchor, block scalar); reference-aware move changes one span and the loader shows all referrers moved; insert/delete round trips (insert then delete restores the original bytes) |
| `spec_model` | id generator is collision-free across the namespace; rename rewrites all references; referrers lists |
| HTTP API (`tests/test_spec_editor.py`, pattern of `test_flowgraph_editor.py`) | token, Host, Origin and body-size guards; `apply` then `save` then `load_spec` equals the expected spec; invalid apply refused and buffer unchanged; save refused on disk-hash mismatch; replay after an external edit; preview does not touch the file or `out/`; paint and erase produce valid specs; preview geometry equals `build_geometries` of the saved file (same polygons) |
| browser drive (`tests/browser/spec_editor_drive.mjs`, run by `run_drive.py`-style runner, skipped with exit 77 without node or Edge) | real DevTools mouse events: drag a POI and assert the one-line file diff after Save; drag a referenced waypoint and assert the referrers moved; paint a stroke and assert a poly region appeared; undo; screenshot for the owner |
| determinism and baselines | the editor code is not on the build path except the `load_spec_text` split; the full regression suite must pass unchanged (committed baselines untouched, no re-baseline); a test asserts that an editor session with no net edit leaves the spec file byte-identical and that building the file before and after an edit-and-undo gives identical outputs |

## 11. Slices (question 8)

Ordered by value. Each slice ships on its own and includes its own tests, browser-drive extension and README/docs update. Effort: S under half a day, M about a day, L several days of agent work.

| # | slice | delivers | effort | depends on |
|---|---|---|---|---|
| 1 | **Span-patch engine and `mapgen spec set`** (`specpatch.py`, `spec_model.py`, `load_spec_text` split, property tests) | move a point or waypoint, set a scalar, insert/delete flow-sequence items, with id addressing and reference awareness; usable by agents from the CLI with comment-preserving edits | M | none |
| 2 | **Viewer with drag-to-move** (`spec-edit`, shared `editor_http.py`, page, T1 vector overlay, T2 preview image, drag POIs, bases, `points`, line waypoints, ref-aware, validation, atomic save with disk-hash check, undo/redo) | the minimal editor the owner can use today: see the map, drag the river and POIs, save a one-line diff | L | 1 |
| 3 | **Structure and shapes:** add/delete/insert vertices, blob `at`/`r`/`squash`/`angle`, band path and width, poly vertices, crossings, inspector with key editing, id generation, new-feature tools, delete with referrer choice, rename, layer lock and visibility, snapping | complete geometry editing of every feature kind | L | 2 |
| 4 | **Painting:** brush, erase, merge into poly regions, `clearing` holes, send backward, scatter `within` polygon tool | the forest and mountain painting the owner asked for | L | 3 |
| 5 | **Flowgraph layer:** nodes, edges, live `check` badge, failing-edge highlighting, click to select | design intent visible while editing | S | 2 |
| 6 | **External-change safety (implemented, GDU-55):** file watcher, replay/discard/save-as-copy, uncommitted-changes notice | concurrent editing in another editor is safe | S | 2 |
| 7 | **Analysis preview:** T3 coarse travel/territory overlays and a side panel of base-to-base time, objective spreads and warnings; T4 build button with progress | balance feedback while moving things | M | 2 |

Minimal first slice that already helps: slice 1 (agents and the owner's scripts get comment-safe edits immediately), then
slice 2. If only one deliverable is funded, fund 1 and 2 together: slice 2 with dragging of POIs, bases, `points` and
waypoints is a read-and-drag viewer with ref-aware patching, which is the feature the owner described first ("dragging
river nodes around").

Risks:
- *Patching edge cases in block sequences and trailing comments* (slice 1, 3): mitigated by the verify-by-reparse net and
  property tests over every committed spec; fallback is refusal.
- *Geometry latency on large maps*: thornwald-size maps measured 0.8 s for geometry; at 4 Hz requests are dropped, never
  queued, so the UI stays live. Fallback is update on release only.
- *Preview terrain differs from the real build* (coarser heightmap): the preview is labelled; balance numbers come from T4.
- *Painted polygon quality*: ring simplification at 3 m and the type's `rough`/`warp` can give outlines that differ from the
  brush path; the overlay shows the true result immediately and the owner adjusts.
- *Scope creep into a general GIS tool*: held by the rule that the editor only writes keys the schema defines.

What to cut if time is short: the inspector's rarely used keys (`fragment_scale`, `width_var`), snapping to segments, rename,
the `clearing`-hole choice, layer lock (keep visibility), T4, `scatter within` tool, and the browser drive beyond the
drag test.

## 12. Open questions for the owner

| # | question | recommendation |
|---|---|---|
| 1 | Span patching (chosen) or add `ruamel.yaml` | span patching; no new dependency, smallest diffs |
| 2 | Painting representation | poly regions with ids for exact control, scatter `within` polygons for procedural texture |
| 3 | Where saves go | in place, explicit Save, git as the undo; no sidecar or draft copy |
| 4 | Default coordinate snap | 1 m integers (the style of the committed specs); finer only on zoom |
| 5 | Holes in painted forest | append a `clearing` poly (keeps rings simple and the result visible as its own feature) |
| 6 | Should the editor also write the flowgraph | no, the GDU-36 editor owns it; the editor only reads it |
| 7 | Browser support in tests | the existing Edge/Chrome DevTools drive; skipped when missing |

## 13. Rejected alternatives

- **Fully server-rendered tiles:** every drag would wait for a 1.6-14 s raster render; handles and hit-testing would still
  need vector data. The vector overlay needs the geometry JSON anyway.
- **Client-side reimplementation of the geometry** (meander, wobble, Chaikin, noise warp): a second implementation of
  seeded noise in JavaScript would drift from Python and cannot match byte-exact shapes; the server's 0.3 s geometry call is
  authoritative.
- **Electron/Qt desktop app, or a framework build (React, bundler):** adds a toolchain and dependencies for a single-user
  local tool; the flowgraph editor shows vanilla JS and inline SVG suffice.
- **Editing through `ruamel.yaml`, canonical re-emit, or a sidecar overlay:** see 4.1.
- **Autosave to the spec, or backup files in the repo:** an unreviewed write to a tracked file; git provides history.
- **Incremental per-feature recompute:** see section 6.
