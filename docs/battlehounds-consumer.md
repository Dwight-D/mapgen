# Battlehounds as a consumer

Battlehounds (`E:\GameDev\Unity\battlehounds`) is an online first-person fantasy combined-arms war
sandbox (Unity 6, URP, Mirror). It is the only project that consumes mapgen bundles. Its own `CLAUDE.md`
and `.anthill/` bind any work done inside it.

## What it reads

Status as of 2026-11-14.

| file | read |
|---|---|
| `export/meta.json` | yes |
| `export/masks/*.png` | yes — treated as clean black/white data |
| `export/pois.json`, `regions.json`, `crossings.json` | yes |
| `report.json` | the top-5 chokepoints only |
| `map.png`, `overlay_*.png`, `report.md` | never |

Nothing is decoded from a rendered image. The map baker works on 16 m cells and keeps its own
hand-copied movement cost table and cover weights; the resulting discrepancies are listed in
`roadmap.md`.

## Where the consuming code and specs live

Paths inside the Battlehounds repo:

| path | content |
|---|---|
| `docs/specs/MAP_IMPORT_SPEC.md` | the Unity editor importer: export bundle to graybox map scene |
| `docs/specs/MACRO_SIM_SPEC.md`, `docs/ai/` | the macro simulation and AI work that bakes map data |
| `docs/DECISIONS.md` | settled decisions, including map and arena setup |
| `Assets/_Project/Scripts/Maps/`, `Assets/_Project/Editor/` | the map pipeline the importer plugs into |
| `.anthill/backlog/` | Battlehounds work items, including ones that request mapgen changes |

## Test bundles

The five concept maps in `maps/concepts/` (`kingsjaw`, `saltreach_delta`, `thornwald`, `gloamfen`,
`ashen_steppe`; 3.2–3.8 km, 2 teams) are the bundles Battlehounds imports. Build one with
`mapgen.ps1 build maps/concepts/<name>.yaml`; the bundle lands in `out/<name>/export/`.

## Changing the contract

1. Update `EXPORT_FORMAT.md` and `MAPGEN_SPEC.md`.
2. Implement and rebuild the concept maps.
3. Tell the Battlehounds side what changed (file an item in its backlog or hand over a prompt).
