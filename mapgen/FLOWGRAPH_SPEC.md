# Flowgraph — topology precursor to the map spec

A **flowgraph** is a small hand-written YAML file describing a map's *topology*: which places exist and
which of them are directly traversable from which. It carries no geometry. A mapgen spec (the
topography: regions, lines, positions) is drafted from it and later checked against it.

Status: slice 1 (loader, validation, `flowgraph validate`) implemented; slices 2-6 are design. Implementation slices are backlog items in the `mapgen` workstream
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
4. Implementation reads the raw YAML for reference tokens (`load_spec` resolves them to coordinates and drops
   the names) and `load_spec` for coordinates.

## 5. Commands — `python -m mapgen flowgraph <sub>`

```
flowgraph scaffold <graph> [--out <path>] [--size X Y]   # draft spec text -> stdout or file
flowgraph check    <graph> <spec> [--strict]             # validate a spec against the graph
flowgraph preview  <graph> [--out <html>]                # wave 2: static HTML/SVG preview
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
- Placeholder coordinates: deterministic layout of the graph in the map rectangle (spawns on opposite sides,
  remainder by a seeded `networkx` spring layout, seed fixed, rounded to 10 m).
- The scaffold **must pass** `mapgen validate` and `flowgraph check --strict` against its own graph
  (tested).

### `check`
Loads graph and spec (errors → exit 1, same format). Reports:
- **missing nodes** — bound id absent from spec;
- **missing edges** — graph edge not derived (§4);
- with `--strict`: **extra edges** (derived edge between two bound nodes, not in graph) and
  **extra nodes** (POIs of any type except `landmark` that are not bound by any graph node; `base_*` of teams
  with no spawn node). `points` entries never count as extra.
Output: one line per finding, `missing node ao (AO: Team A gate)`, then a summary. Exit 0 = pass, 1 = any
missing (or extra under `--strict`) or load error. Default mode ignores extras. Nothing is written.
Derived edges touching an unbound node are ignored (not a graph concern).

### `preview` (wave 2)
Writes one self-contained HTML file (inline SVG + vanilla JS, no CDN) drawing the graph: node shapes/colours by
kind (lightness + shape, colour-blind safe per mapgen rule), labels (id + name), hover shows `desc`, edge labels
from `name`. Layout from the same deterministic layout as the scaffold; spawns pinned left/right. Read-only.
Chosen as HTML so the later editing wave (click to add nodes/edges, write back the `.flow.yaml`) can extend it
without a separate GUI stack.

## 6. Module layout & tests

- `mapgen/flowgraph.py` — load/validate graph, layout, scaffold text, edge derivation, check; `mapgen/flowgraph_preview.py` (wave 2).
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
| 4 | `flowgraph preview` HTML (§5) | 1, 3 layout | file written, opens; labels/desc present; no CDN |
| 5 | PoC map `town_square` (§7) | 1–4 | build exit 0, check --strict pass, report clean, rebaseline approved |
| 6 | GUI editing — DEFERRED until 5 proves the workflow | 4, 5 | — |

Slices 1→2→3 are sequential (one module); 4 can start once 1 and the layout function exist.
Each slice: add docs (README commands, CLAUDE.md table row), run the full pytest suite, commit in the mapgen repo.
