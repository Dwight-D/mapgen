# Handover prompt — mapgen → Unity terrain importer

Paste the block below into a fresh session started in `E:\GameDev\Unity\battlehounds`.

---

We're building a Unity editor importer that turns a **mapgen export bundle** into a graybox map
scene for Battlehounds, as deterministically as possible — the more scaffolding it bootstraps
from the data, the better. This session is for **designing** the importer (spec first, per my
global workflow), then implementing it in waves.

**What already exists**
- `E:\GameDev\Unity\tools\mapgen\` — a Python tool that turns a YAML map spec into a 2D map,
  gameplay analysis and an export bundle (heightmap, splatmaps, masks, JSON for regions, lines,
  crossings, POIs, plus a fully resolved spec). Read **`EXPORT_FORMAT.md` first** — it is the
  importer's contract (coordinates, every file, type meanings, Unity mapping notes). `README.md`
  and `MAPGEN_SPEC.md` cover the generator if needed; don't change mapgen unless the importer
  needs a contract change (then update `MAPGEN_SPEC.md` + `EXPORT_FORMAT.md` first).
- Built bundles to test with: `tools/mapgen/out/<name>/export/` for the concepts
  `kingsjaw`, `saltreach_delta`, `thornwald`, `gloamfen`, `ashen_steppe` (3.2–3.8 km, 2 teams).
  Look at `out/<name>/map.png` to see what each should become. Rebuild with
  `tools/mapgen/mapgen.ps1 build maps/concepts/<name>.yaml`.
- Battlehounds already has a map pipeline to plug into — survey before designing:
  `Assets/_Project/Scripts/Maps/` (`MapCatalog`, `ArenaLayout`), `Assets/_Project/Editor/`
  (`ArenaSceneBuilder`, `MapCatalogEditor`, `MapValidator`, `BuildAll`, `BuilderUtil`, the
  `*PrefabBuilder`s), and gameplay components the POIs map onto: `Net/TeamSpawnPoint`,
  `Objectives/CaptureZone`, `AI/Neutral/NeutralCamp`, keep sites (`Building/`), supply
  (`Supply/`). Decisions in `docs/DECISIONS.md` (Arena layouts, Maps and map selection,
  CoastalMap setup, Keep NavMesh cost). Packages present: `com.unity.terrain-tools`,
  `com.unity.ai.navigation`; no `com.unity.splines`.

**Goal for v1 (Unity terrain only; other level tools are a later extension)**
From one export folder, an editor command (menu + batchmode-callable) creates or updates a map
scene with, at minimum:
1. Terrain: `TerrainData` from `heightmap.r16`, size from `meta.json`, terrain layers per
   `terrain_layers` with placeholder materials, alphamaps from the splat PNGs.
2. Water planes for lakes and river strips at their `water_surface` heights.
3. POIs → the matching gameplay prefabs/components where they exist (team spawns, capture
   zones, neutral camps, keep sites, supply), placeholder markers otherwise.
4. Graybox scaffolding: tree placeholders in forest regions (honouring holes), building
   blockouts on town pads, bridge blockouts at bridge crossings aligned to the road, road/trail
   markers along lines, blocking where `masks/impassable.png` says so.
5. NavMesh bake, MapCatalog entry, and passing `MapValidator`.
Re-import of an unchanged bundle must be idempotent (use `meta.json.spec_sha256`); generated
content should live under one root object so re-import can replace it without touching
hand-placed work.

**Decisions to make with me early**
- Where the importer lives: battlehounds `Assets/_Project/Editor/` (simplest, project-coupled)
  vs. a game-agnostic core shareable with kaiju-calamity / scrapyard-siege later. My lean: a
  game-agnostic core (terrain, water, regions → placeholders) plus a thin battlehounds binding
  (POI type → gameplay prefab).
- Scale/perf: the concepts are ~3.5 km — terrain tiling, tree counts, NavMesh bake time.
- Which placeholder art/prefabs to use.

**Rules**: battlehounds `CLAUDE.md` binds (project code in `Assets/_Project/`, never edit
Mirror/third-party, gameplay numbers in ScriptableObjects, tuning values are my call, settled
questions go to `docs/DECISIONS.md`). The working tree has a lot of uncommitted work — don't
commit or discard anything that isn't yours. Spec goes in `docs/specs/` (e.g.
`MAP_IMPORT_SPEC.md`); validate its Unity API assumptions on a small slice (heightmap +
alphamap import of one bundle) before delegating implementation waves. I'm red–green
colourblind: any debug visualisation separates categories by lightness/pattern, not hue.

Start by reading `EXPORT_FORMAT.md` and surveying the battlehounds map pipeline, then come
back with a short design proposal and the open decisions.
