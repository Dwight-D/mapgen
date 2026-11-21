# mapgen — agent instructions

`mapgen` is an engine-agnostic Python CLI that turns a hand-written YAML map spec into a 2D map image,
gameplay analysis (overlays + report) and a terrain export bundle (heightmap, splatmaps, masks, JSON).
The spec is the single source of truth; everything in `out/` is derived, git-ignored and safe to delete.

## Scope and priorities

- The only consumer is **Battlehounds** (`E:\GameDev\Unity\battlehounds`). Design for it; generalize to
  other games only when one needs it.
- The export bundle is the product. Consumers read data files, never rendered images. Priority is
  structured, machine-parsable, versioned output with more gameplay data in it. See `docs/roadmap.md`.
- Rendered images (`map.png`, `overlay_*.png`) and `report.md` are for humans authoring maps.

## Run

```
.venv/Scripts/python -m mapgen build maps/concepts/thornwald.yaml    # -> out/thornwald/
.venv/Scripts/python -m mapgen validate <spec>
.venv/Scripts/python -m mapgen probe <spec> <x> <y> | <poi id>
.venv/Scripts/python -m mapgen diff <build a> <build b> [--text]   # delta of two build folders
```

`mapgen.ps1` / `mapgen.sh` forward to the same command from any directory. Other commands: `types`,
`palette`. Full usage: `README.md`.

## Rules

- **Python 3.8.** No `match`, no `X | Y` unions, no builtin generics at runtime, no `str.removeprefix`.
  Dependencies are pinned exactly: runtime in requirements.txt, development (pytest) in
  requirements-dev.txt.
- **Determinism.** The same spec and seed produce byte-identical files. Never introduce unseeded
  randomness, timestamps or unordered iteration into outputs.
- **Feature identity.** Every region, line, scatter entry and crossing has an `id`; noise seeds and exported ids
  derive from it (or from an explicit `seed`). Nothing keys on list position. Design: `mapgen/IDENTITY_SPEC.md`.
- **Contract first.** A change to anything in `export/` updates `EXPORT_FORMAT.md` and `MAPGEN_SPEC.md`
  in the same commit, and is checked against the Battlehounds consumers (`docs/battlehounds-consumer.md`).
- **Data lives in data files.** Gameplay and splat values in `mapgen/types.yaml`, render colours in
  `mapgen/palette.yaml`.
- **Colour-blind safe.** The user is red-green colour-blind: every visual separates categories by
  lightness and pattern, not hue. `python -m mapgen palette` checks it.
- **Commits.** Commit freely in this repo.

## Verification

Run the regression suite: `.venv/Scripts/python -m pytest` (about 9 minutes with `-n 8`; 1464 tests, 524 s). It builds the three
`maps/example_*.yaml` maps and the five `maps/concepts/*.yaml` maps into temporary directories and
compares the SHA-256 of every output file with the committed baseline in `tests/baselines/<map>.json`.
It also covers spec validation errors and command exit codes. Run one map with `-k <map name>`.

A change that must not alter output passes the suite unchanged. A change that alters output fails it,
naming each map and file. Show the owner the diff report for the change: build each affected map before
and after the change (e.g. the previous commit from a `git worktree`), each into its own empty `--out`
root, and run `.venv/Scripts/python -m mapgen diff <before>/<map> <after>/<map> --text` (agents read the
JSON form, without `--text`); once the owner approves it, re-baseline with
`.venv/Scripts/python tests/rebaseline.py <map>... | --all` and commit the new baselines with the change.
Never re-baseline without that approval. Design: `tests/REGRESSION_SPEC.md`.

## Conventions

Metres; origin south-west; `x` east, `y` north. Images are north-up; `heightmap.r16` has row 0 = south.
Exported JSON uses `x` (east), `z` (north), `y` (height).

## Where knowledge lives

| file | content |
|---|---|
| `README.md` | usage: commands, outputs, authoring keys, how to read the report |
| `MAPGEN_SPEC.md` | full design: spec schema, type table, pipeline stages, validation, acceptance |
| `EXPORT_FORMAT.md` | the export bundle contract for engine importers |
| `docs/roadmap.md` | direction: machine-parsable export, known gaps, planned additions |
| `docs/investigations/` | dated read-only investigation reports with the evidence behind the roadmap: map editability, concept-map balance, export questions |
| `docs/battlehounds-consumer.md` | what Battlehounds reads from the bundle and where that code and its specs live |
| `docs/backlog.md` | where work items for this repo are tracked and how to file one |
| `tests/REGRESSION_SPEC.md` | the regression suite: baselines, helper API, test cases, re-baselining |
| `mapgen/REFERENCES_SPEC.md` | point references and the `points` section: format rules, resolution, migration of the committed maps, tests, docs |
| `mapgen/SCATTER_SPEC.md` | the scatter threshold over the entry's own area: locality property, `coverage` meaning, migration of the scatter maps, tests |
| `mapgen/DIFF_SPEC.md` | the diff command: report format, algorithms, tests |
| `mapgen/FLOWGRAPH_SPEC.md` | the flowgraph feature: topology graph format and rules, spec binding, edge derivation, `flowgraph` commands, slices |
| `mapgen/TOWN_SPEC.md` | the town-layout generators in `mapgen/town/` (radial, voronoi, grid), the fort and water add-ons, spec emission |
| `mapgen/TOWN_LAYERS_SPEC.md` | layered subdivision: nested district fills (grid, radial, voronoi), boundary streets, junction nodes, layer checks |
| `maps/towns/` | generated town maps and their flow graphs; `README.md` there describes the set |
| `HANDOVER_UNITY_IMPORTER.md` | the prompt that started the Battlehounds importer session |

Map concepts in `maps/concepts/` are authored with the `map-concept` skill
(`E:\GameDev\Unity\.claude\skills\map-concept\`).
