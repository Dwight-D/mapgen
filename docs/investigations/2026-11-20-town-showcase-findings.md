# Town showcase maps: findings (GDU-57 integration)

Maps: `maps/towns/{hearthmoor,pentring,ashgrove,marchfold,twinbridge,saltport,swordmarch}.yaml` (1000/1200/900/1500/1200/1000/1500 m). All pass `mapgen validate`, `mapgen build`
(`out/<map>/map.png`) and `flowgraph check --strict` (0 missing/extra nodes and edges). Regeneration: `maps/towns/README.md`.

## What worked

- Form A (paste fragments) is automatable with a text merge: `mapgen/town/embed.py` appends each fragment list to the same key
  of a base spec and keeps base comments. No spec schema change was needed; `wall` lines, `gate` crossings and point references
  all resolved first time.
- The flow fragment plus two spawns linked to gates passes `flowgraph check --strict` on all four maps (87, 18, 11, 25 nodes).
- Reports: no warnings; unreachable 1.4-2.9 %; contested 15-26 %; base-to-base 1:18 (ashgrove) to 2:26 (marchfold).

## Hand work still needed

- Terrain is hand-written per map; the generator only supplies the settlement. Spawns, bases and approach roads are
  computed by `build.py` (not by the town module): there is no "spawn" concept in a settlement.
- Spawn gates must avoid gates that start an outpost `approach` road (marchfold): the base road then resolves onto the outpost's
  nodes and `flowgraph check` reports a missing edge. `build.py` skips those gates.
- Region/POI labels duplicate (plaza appears as district and landmark); the grid map is cluttered with "Residential Ward N"
  labels (40-55 districts). Authors should drop most district labels.
- `town generate` has no `--centre`; the API `generate(..., centre=)` does. Settlements are always centred, so terrain
  features must be placed around a known centre.

## Defects and limits seen in the renders

- ashgrove (organic voronoi): `Craft Quarter 2` is one district of about 300 x 400 m with no road inside it (the arteries
  only reach the plaza side), and town regions poke out past the wall as triangular corners (the pre-wall cell polygons are wider
  than the wall in two places). Organic regime produces a very uneven district size spread.
- ashgrove: three gates cluster on the north-west side, two on the east, none south; the large unroaded district is reachable
  only through the wall's gate-less side, so it is effectively dead space.
- hearthmoor: with `inner_wall` random the generator produced 14 gates; fixed to false. Citadel sits inside the grid, not on
  the wall. Both team roads end at west/east gates; the two southern gates are unused.
- marchfold: voronoi `fort=corner` puts the star fortress against the north-east wall with its own gates; the blob
  footprint is irregular, and road A->gate_1 crosses the field edge. Only 2 of 4 gates were usable for spawns.
- Town regions are 1.1-cost `town` terrain with `flatten`: the interior is flat dirt with no cover variation; `los: true` on
  town is the only occlusion, so interior fighting is not differentiated by district kind (kind travels in settlement.json only).

## Gameplay-flow observations (reports)

- hearthmoor: single chokepoint score 0 (all dense town; the wall is impassable except gates, yet the chokepoint metric sees
  the whole town as one open block). Route openness A to B 0.25: the town is the main cover.
- pentring: radial symmetry is broken by wall irregularity and gate offsets; spoke roads make the plaza a mandatory mid-point
  only if teams use the gates facing each other; the ring roads are not gates-to-gates shortcuts.
- marchfold: the fortress is not on the shortest A-B route (it hangs off the east side), so the "mandatory pass" test fails;
  the outpost on gate_0/gate_3 approaches is a cheap capture pair.
- Objectives: none added (settlement landmarks are `landmark` POIs, not objectives), so the report objective tables are empty;
  balance analysis needs landmarks converted to `capture`/`keep_site` by hand.

## Water and fort showcase maps (final integration)

- twinbridge (radial, river, seed 12), saltport (voronoi, bay + island site, seed 7), swordmarch (grid, river, `fort=island`, seed 4):
  validate OK, build with 0 warnings, `flowgraph check --strict` 0 missing/extra. Built with the unchanged `build.py` flow plus
  `water*`/`fort` params; terrain bases are hand-written around the centred settlement (rivers/bays are emitted by the generator and
  run to the map edge, so the base only needs meadows, woods and fen).
- Bridge/bank-node <30 m limit: hit with twinbridge seed 11 (extra edges between `twin_bridge_1` and `_2` nodes). Avoided by seed choice
  (12); no change to `water.py` (a spacing fix would alter generation for the suite's town tests).
- saltport seed 5 builds with two warnings ("road crosses lake with no bridge or ford"): a smoothed road next to the shore node dips
  into the bay, and the bridge crossing `salt_bridge_1_x` sits on a bank node rather than the bridge midpoint. Seed 7 has no bridge.
  Both are generator/emitter issues (`water.py`, emit), not fixed here.
- Defect found: an inner fort (`fort=corner` or `centre`) combined with `water=river` raises `shapely GEOSException: side location
  conflict` in `check_water` on `grid`, `radial` and `voronoi` (seeds 4, 5 tried); `fort=peninsula` fits nowhere with the river.
  The fort add-on yields an invalid district polygon once water clipping runs. Swordmarch therefore uses `fort=island` (detached fort
  with an approach road). Needs a fix in `fort.py`/`water.py` ordering; not in this territory.
- Island site: reserved only; the fort add-on does not use it (`water_island=true` plus `fort=island` was not attempted together).

## Ashgrove defects: not fixed

Both defects originate in the voronoi generator and `emit_spec`, not in `embed.py`/`build.py`: the unroaded district has no road in the
Settlement itself, and the regions past the wall are the pre-wall district polygons (visible on saltport too: town cells poke out past
the wall at several corners). A build-side patch would require clipping region polygons or inventing roads that are absent from the
settlement graph, which breaks the `flowgraph check --strict` agreement. Fix belongs in `mapgen/town/voronoi.py` (road coverage) and
`emit_spec` (clip district regions to the wall ring).
