# Town and city layout concepts (mission GDU-57)

Date: 2026-11-20. Read-only investigation. Inputs: `docs/missions/town-layout/MISSION.md`, the seven reference
images (`ref_5` to `ref_12`), `maps/iterations/town_square/v2_walled` (800 x 800 m, hand-built, flowgraph-checked),
`docs/battlehounds-consumer.md`.

## 1. Frame

### Scale and consumer facts
- Target maps are 600-1500 m per side. The hand-built town_square is 800 m and holds one market square, two gate
  streets, two side entrances and two outlying zones. Streets there are 20-40 m wide corridors between impassable blocks.
- Battlehounds reads `regions.json`, `pois.json`, `crossings.json`, masks and the top-5 chokepoints. It bakes 16 m
  cells. A district is therefore a region of at least a few cells (>= 48 m across); a gate is a crossing or POI with
  a 16-32 m opening; a major road is a line of 16 m width or more.
- A generated settlement outputs four things: district regions, major road lines, wall lines with gate openings,
  landmark POIs (square, citadel, harbour, bridge heads). It also outputs a connectivity graph (district and gate
  nodes, road and gate edges) in the flowgraph format.

### Gameplay vocabulary used below
- Chokepoint: a gate, bridge or single-road neck of 16-32 m.
- Route: a continuous major-road path between spawns or objectives.
- Flank: a path that bypasses a chokepoint (outer ring road, river ford, wall breach, coast strip).
- Spawns assumed: two teams (A, B), as in all current maps; larger maps may add a third objective zone.

### Axes of variation
Street pattern (grid, radial, Voronoi-angular, organic), water (none, river with bridges, bay with harbour, island),
fortification (single wall, double wall, citadel, detached fort), terrain fit (flat, hill, coast), and group form
(single settlement, settlement plus satellites).

## 2. Concepts

### C1. Hearthmoor - small walled market town
- Size: 600 x 600 m; settlement about 300 x 260 m.
- Pitch: a fortified crossroads market town on flat farmland. One rectangular wall, four gates, a central market
  square. The smallest, most legible settlement; the baseline type.
- Districts: Market (centre, open square 60 m), Guildrow (west blocks, craft), Chapel Close (north-east, walled
  precinct), Tanners (south-east, edge), Gatehouse Wards (four gate-side strips). Outside: Farmsteads (ring of fields).
- Major roads: two axes (east-west High Street, north-south Cart Road) crossing at the market; one back lane
  inside the wall parallel to the south wall.
- Wall and gates: rectangle with cut corners, towers at corners; West, East, North and South Gate; the south gate is
  the narrowest (16 m).
- Landmarks: market square, chapel, a gatehouse tower per gate.
- Terrain: flat, one stream along the south side outside the wall.
- Gameplay: four gates feed one centre; team A enters west, team B east, so the market is the objective and High
  Street is a straight 300 m kill lane. North and south gates are flanks; the back lane lets defenders rotate under
  the wall. The stream limits southern approaches to one ford.

```
   +--[N]--------+
   |Chapel| Mkt  |
 [W]=====( )=====[E]      ( ) market, == High Street
   |Guild |Tann. |
   +------[S]----+
        ~~ stream ~~
```

### C2. Pentring - radial ring city
- Size: 1200 x 1200 m; city diameter about 800 m.
- Pitch: a circular city of wedge districts around a central plaza, ring roads linking wedges, an outer irregular
  wall, fields around (ref_7). The reference radial type.
- Districts: Plaza Ward (centre), 8 wedges named by role (Temple, Scholars, Craft, Market, Barracks, Garden,
  Tavern, Dwarven), Outer Ward (belt between the second ring and the wall), Fields (outside).
- Major roads: 8 spokes from the plaza to the wall, 2 ring roads (r = 130 m and r = 270 m), a wall-walk road.
- Wall and gates: polygon wall of 16 to 20 vertices, jittered; 6 gates, not all spokes (2 spokes end at the wall
  unopened, giving blind spokes).
- Landmarks: central plaza with a domed hall, a gate square at each gate.
- Terrain: flat or gentle bowl; one road runs outward from every gate.
- Gameplay: spokes converge on the plaza, so the centre is a strong shared objective; rings are the flank
  routes, and each ring-spoke junction is a small chokepoint. Attackers choose one of 6 gates; the first ring
  road lets defenders rotate between wedges. Blind spokes make dead-end ambush streets.

```
        .-- wall --.
      /   \  |  /    \
     |  ---( P )---   |      P plaza, spokes, ring r1 r2
      \   /  |  \    /
        '-- gate ---'
```

### C3. Swordmarch - Manhattan city with boulevards, river and island fort
- Size: 1400 x 1000 m; city about 900 x 700 m.
- Pitch: a rectilinear block city cut by radial boulevards through an inner old town, with an inner wall, a citadel,
  a market square, a river on the north-east flank and a fort on an island (ref_12). The large mixed city.
- Districts: Old Town (inner walled, radial), Citadel Heights (walled enclave on the river), Seaside/River Quarter,
  three outer grid districts (Barracks, Temple, Craft), Island Fort (detached).
- Major roads: 4 radial boulevards from the market square through the inner wall; 2 cross avenues in the outer
  grid; a river quay road.
- Wall and gates: outer polygon wall, 7 gates; an inner arc wall with 4 gates; the citadel has one gate to the
  market square and one to the river.
- Landmarks: marble market square, citadel, high temple, barracks, island fort reached by ferry or a causeway.
- Terrain: river along the north-east, the city lies on the south-west bank; the island fort lies mid-river.
- Gameplay: double wall gives layered defence (outer gates then inner gates); boulevards are fast approach lanes
  to the market; the island fort commands the river and the quay road; the river bank is a one-sided flank.

```
 ~~~river~~~~~~~~~~~~~~
   [Cit]|======       (Fort)
 +--inner arc--+  \
 |  Old ( M )===+===+
 +--------------+
 outer wall with 7 gates
```

### C4. Twinbridge - concentric river city
- Size: 1000 x 1000 m; city about 700 m across.
- Pitch: a walled core with concentric ring roads, split in two by a river with a bridge across it, plus an outer
  wall with gates (ref_9). The reference river-and-bridges type.
- Districts: Core (walled), East Bank, West Bank, Bridge Market (both bank heads), Outer Ring Quarter on each bank.
- Major roads: 2 concentric rings, 4 spokes per bank, 3 bridge roads (main bridge, upstream footbridge,
  downstream ford road).
- Wall and gates: round outer wall with 5 gates; core wall with 3 gates; river gate (water grating, not
  passable) at the up- and downstream wall crossings.
- Landmarks: main bridge, bridge market, core keep.
- Terrain: river runs north-south through the middle, 30-50 m wide, impassable except at crossings.
- Gameplay: the river divides the city; every cross-river move uses one of 3 bridges, so the main bridge is the
  decisive chokepoint with two bridgeheads. Team A holds the west bank, team B the east; the footbridge and the
  ford are flank routes with long approach roads.

```
   wall -----.   |~|   .----- wall
   West      \   |~|   /   East
   Bank   ===(Core)===B====(Core)=== Bank      B main bridge
   Ring      /   |~|   \   Ring
```

### C5. Saltport - bay harbour city
- Size: 1500 x 1200 m; city about 800 x 700 m, bay about 500 m wide.
- Pitch: a wedge-district city wrapping a bay: harbour quarter on the shore, a wall with gates on the land sides,
  small fortified islands guarding the bay mouth (ref_5, ref_6, ref_8).
- Districts: Docks (shore), Fish Market, Harbour Wards (2 wedges), Upper Town (hill side), Citadel on the wall,
  Outer Villages (2-3 satellites on the coast road). Islands: Lighthouse Fort, Mole Fort.
- Major roads: shore road, 3 wedge-dividing roads from the harbour inland, a ring road behind the upper town,
  a coast road to the villages.
- Wall and gates: irregular wall from shore to shore (landward arc), 4 gates; a chain wall along the quay (no
  gates, water side open by boat).
- Landmarks: harbour mole, citadel, lighthouse, fish market square.
- Terrain: bay on the west, bluffs on the north, land rising to the east.
- Gameplay: sea is impassable terrain for ground units, so the harbour is a flat edge; attackers use land gates or
  a beach landing at the village strip. The coast road is a long flank route; islands are tertiary objectives only
  reached by causeway or boat, so a sniper fort overlooks the docks.

```
 ~~~~~~~~  Fort(i)
 ~~bay~~~ Docks|Fish|
 ~~~~~~~~ |wedge|wedge|=== gate
 ~~~~~~~~ Citadel/Upper Town
     villages o  o  (coast road)
```

### C6. Highcrag - fortress on a hill with a terraced town
- Size: 900 x 900 m; town about 400 m.
- Pitch: a castle on a rocky rise, a terraced town below it on one slope, a single wall around the town, a switchback
  road to the keep. The terrain-fit type.
- Districts: Keep Hill (castle, ward, barbican), Upper Terrace (gentry), Lower Terrace (commons), Gate Street,
  Hillfoot Hamlet (outside).
- Major roads: switchback keep road (2 hairpins), terrace road (contour-following, curved), gate road to the plain.
- Wall and gates: wall along the slope with two gates (lower gate to the plain, postern on the far side of the
  keep); castle curtain wall with one gate.
- Landmarks: keep, barbican, well square on the terrace.
- Terrain: a hill of 40-60 m relief; the cliff on the north is impassable.
- Gameplay: attackers must climb; defenders hold the high ground. The switchback road is a long exposed
  chokepoint; the postern is a hard flank behind the keep; contour road gives lateral movement for defenders.

```
   /\ keep       cliff
  /  \=barbican   ####
 |Upper terr.|====   ####
 |Lower terr.|=== postern
 [Gate]====== plain
```

### C7. Marchfold - fortress with villages, no city wall
- Size: 1500 x 1500 m; four settlements of 100-200 m each.
- Pitch: a border fortress at a crossroads with three unwalled villages linked by roads, a mill and farm fields. The
  loosest, most organic type, and the largest map with the least wall.
- Districts: Fortress (square enclosure with bastions), Fortress Yard (market outside the gate), Millbrook, Oakhame,
  Ferry Cross (villages), Fields (belts).
- Major roads: a crossroads (north-south Drover's Road, east-west King's Road), three village spurs (each 250-400 m).
- Wall and gates: fortress only, two gates (road side and river side); villages have none, only a lane choke at a
  bridge or a ford.
- Landmarks: fortress, mill, ferry landing, crossroads inn.
- Terrain: a stream curving through the map; small hills.
- Gameplay: three small capture points around a central fortress; roads are the fast routes between them; fields
  are open ground between villages, so combined arms (vehicles) have room. Fortress is a hard point that must be
  taken or bypassed along the village chain.

```
  Millbrook o
        \
   Oakhame o---[FORT]---o Ferry Cross
        /  King's Rd ~stream~
```

### C8. Shardhold - angular Voronoi walled town
- Size: 800 x 800 m; town about 450 m.
- Pitch: a bastioned town with a polygonal star-shaped wall, districts as irregular angular cells, roads running
  between cell boundaries; a compact fortress town. The Voronoi type.
- Districts: 7 to 9 cells grouped around a Central Cell (market) with names: Armoury, Granary, Scribes, Stables,
  Smiths, Cistern, Watch; an Outer Glacis (open ring).
- Major roads: roads follow cell edges; the central cell has 5 exits; a perimeter road inside the wall.
- Wall and gates: 6 to 8-sided star with a bastion at each vertex; 3 gates on alternating faces.
- Landmarks: central market, granary tower, cistern.
- Terrain: flat, open glacis around it.
- Gameplay: angular bastions give crossfire on the three gates; the open glacis removes cover for attackers;
  inside, the irregular cells produce unequal routes so no two paths are the same length.

```
     /\__/\
    / cells \__
   <  Mkt+cells >   gates on 3 faces
    \_cells__/
      \/  \/
```

## 3. Capability matrix

Legend: P = primary (the concept is defined by it), S = secondary (needed but simple), blank = not needed.

| capability | C1 | C2 | C3 | C4 | C5 | C6 | C7 | C8 |
|---|---|---|---|---|---|---|---|---|
| grid / block streets | P | | P | | | | | |
| radial wedges and rings | | P | S | P | S | | | |
| Voronoi-angular cells | | | | | | | | P |
| organic growth | | | | | S | S | P | |
| water (river, bridge) | S | | P | P | | | S | |
| water (bay, harbour, islands) | | | S | | P | | | |
| fortress / citadel | | | P | S | S | P | P | S |
| terrain-fit (hill, coast) | | | | | P | P | S | |
| wall with gates | P | P | P | P | P | P | S | P |
| satellite villages | S | S | | | P | S | P | |
| double wall | | | P | P | | | | |

All eight concepts need a wall-with-gates primitive and a connectivity graph over districts and gates; those two are
core, not a settlement type.

## 4. Settlement types and ranking

A settlement type is a generator mode that outputs districts, roads and walls. The table counts concepts a type serves
(P or S) and rates effort in rough relative units (1 low to 5 high).

| rank | type | serves | effort | reasoning |
|---|---|---|---|---|
| 1 | Walled grid town (rectilinear blocks, axes, wall with gates) | C1, C3, C8 partly | 1 | lowest effort: rectangles and axis roads; matches the hand-built town_square topology; also the outer grid of C3 |
| 2 | Radial ring city (wedges, rings, polygon wall) | C2, C3, C4, C5 | 2 | one polar construction yields four concepts; wedge/ring is parametric (spokes, rings, jitter) |
| 3 | Water overlay (river with bridges, bay) applied to any base type | C3, C4, C5, C7 | 2 | cut an existing layout by a river line; bridges become crossings; bay is a water region the wall terminates on |
| 4 | Citadel/fort add-on (walled enclave, island or hill fort) | C3, C4, C5, C6, C7, C8 | 2 | a small fixed template: enclosure, gate, landmark, placed on a chosen site |
| 5 | Voronoi/organic cluster (angular cells, satellite villages) | C5, C6, C7, C8 | 3 | cells from seeded points; roads along cell edges; villages are small clusters; looser and harder to keep inspectable |

Terrain fit (C5, C6) is later phase work; it needs queries on the heightmap and is the highest effort (4). It is not
in the ranking, as the mission schedules it after the first types.

Recommended order of build: type 1, type 2, type 4, type 3, then type 5; this covers six of eight concepts with
the first four types and all eight with five.

## 5. Mapping to the mapgen spec

| output | spec element |
|---|---|
| district | region (poly or blob), id `district_<name>` |
| major road | line (type road), id `road_<a>_<b>` |
| wall | line (type wall, impassable) with gate openings |
| gate | crossing, id `gate_<name>`; also a flowgraph node |
| square, citadel, fort | POI, region (plaza), id `landmark_<name>` |
| river and bridge | line (river) and crossing (bridge) |
| bay, island | region (water) and region (island) |
| district adjacency, road connectivity | flowgraph nodes and edges (district, gate) |

## 6. Conclusion

1. Eight concepts, 600-1500 m: small town, radial city, grid-and-boulevard city with island fort, river city,
   bay harbour, hill fortress, fortress with villages, angular Voronoi fort town.
2. Every concept needs a wall-with-gates primitive and a district/gate connectivity graph; build those first.
3. Walled grid town is cheapest (effort 1) and matches the hand-built town_square.
4. Radial ring city covers four concepts with one polar construction (effort 2).
5. A water overlay (river with bridges, bay) covers four concepts as a cut of an existing layout (effort 2).
6. A citadel/fort add-on covers six concepts as a small template (effort 2).
7. Voronoi/organic cluster covers four concepts and has the highest effort of the five types (effort 3).
8. Terrain fit (hill, coast) is the hardest capability (effort 4) and stays a later phase.
9. Build order: grid, radial, fort add-on, water overlay, Voronoi/organic.
10. Gameplay leverage: gates, bridges and switchbacks are the chokepoints; all appear as crossings and flowgraph nodes.
