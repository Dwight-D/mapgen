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
```

`mapgen.ps1` / `mapgen.sh` forward to the same command from any directory. Other commands: `types`,
`palette`. Full usage: `README.md`.

## Rules

- **Python 3.8.** No `match`, no `X | Y` unions, no builtin generics at runtime, no `str.removeprefix`.
  Dependencies are pinned in `requirements.txt`.
- **Determinism.** The same spec and seed produce byte-identical files. Never introduce unseeded
  randomness, timestamps or unordered iteration into outputs.
- **Contract first.** A change to anything in `export/` updates `EXPORT_FORMAT.md` and `MAPGEN_SPEC.md`
  in the same commit, and is checked against the Battlehounds consumers (`docs/battlehounds-consumer.md`).
- **Data lives in data files.** Gameplay and splat values in `mapgen/types.yaml`, render colours in
  `mapgen/palette.yaml`.
- **Colour-blind safe.** The user is red-green colour-blind: every visual separates categories by
  lightness and pattern, not hue. `python -m mapgen palette` checks it.
- **Commits.** Commit freely in this repo.

## Verification

There is no test suite. Verify a change by building the three `maps/example_*.yaml` specs and one concept
map, confirming exit code 0 and no new report warnings, and inspecting the affected outputs. For changes
that must not alter output, compare file hashes of `out/<name>/` before and after.

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
| `docs/battlehounds-consumer.md` | what Battlehounds reads from the bundle and where that code and its specs live |
| `docs/backlog.md` | where work items for this repo are tracked and how to file one |
| `HANDOVER_UNITY_IMPORTER.md` | the prompt that started the Battlehounds importer session |

Map concepts in `maps/concepts/` are authored with the `map-concept` skill
(`E:\GameDev\Unity\.claude\skills\map-concept\`).
