# Town showcase maps

Fourteen maps that embed generated settlements (`mapgen/town/`, `mapgen/TOWN_SPEC.md`) in hand-written terrain. They are
not in the regression suite and have no baselines.

| map | size | generator | seed | settlement params | settlement id |
|---|---|---|---|---|---|
| `hearthmoor` | 1000 | grid | 14 | extent_frac 0.58, aspect 1.2, gates 4, inner_wall false, citadel true, rotation 0 | `hearth` |
| `pentring` | 1200 | radial | 7 | radius_frac 0.6, spokes 5, rings 2, gates 5, citadel false | `pent` |
| `ashgrove` | 900 | organic | 21 | edge wall, warp 0.6, layers organic, radius_frac 0.6, citadel false | `ash` |
| `marchfold` | 1500 | voronoi | 33 | radius_frac 0.5, patches 14, gates 4, fort corner, fort_outposts 2, citadel false | `march` |
| `twinbridge` | 1200 | radial | 12 | radius_frac 0.55, spokes 6, rings 2, gates 6, water river, citadel false | `twin` |
| `saltport` | 1000 | voronoi | 7 | radius_frac 0.5, patches 14, gates 4, water bay, water_island true, citadel false | `salt` |
| `thornwick` | 1000 | organic | 3 | edge open, warp 0.7, layers organic, radius_frac 0.55, citadel false | `thorn` |
| `swordmarch` | 1500 | grid | 4 | extent_frac 0.5, gates 4, inner_wall false, water river, fort island, rotation 0, citadel false | `sword` |
| `rivermere` | 1000 | organic | 4 | edge wall, layers organic-radial, warp 0.5, water river, radius_frac 0.6, citadel false | `river` |
| `wyndale` | 1000 | organic | 13 | edge open, layers organic-voronoi, warp 0.6, radius_frac 0.6, lobes 2, ribbons 2, citadel false | `wynd` |
| `greywatch` | 1100 | organic | 12 | edge wall, layers organic-grid, warp 0.5, fort corner, radius_frac 0.55, citadel false | `grey` |
| `driftholt` | 1100 | organic | 13 | edge fade, layers organic-cells, warp 0.7, radius_frac 0.45, lobes 2, hamlets 5, fade_len 300, citadel false | `drift` |
| `harrowmouth` | 1100 | organic | 14 | edge wall, layers organic-mixed, warp 0.5, water bay, radius_frac 0.55, citadel false | `harrow` |
| `cindermarket` | 900 | organic | 3 | edge wall, layers organic-mixed, warp 0.4, arteries 7, arcs 3, radius_frac 0.7, min_district_area 900, citadel false | `cinder` |

Map seeds (terrain) are 5701-5708 and 5709-5714 (the six layered organic maps) in `src/<map>.base.yaml`.

## Regenerate

From `tools/mapgen`:

```
.venv/Scripts/python maps/towns/build.py [name ...]      # rewrites maps/towns/<name>.yaml, <name>.flow.yaml, preview/<name>_town.png
.venv/Scripts/python -m mapgen validate maps/towns/<name>.yaml
.venv/Scripts/python -m mapgen build maps/towns/<name>.yaml                 # -> out/<name>/map.png
.venv/Scripts/python -m mapgen flowgraph check maps/towns/<name>.flow.yaml maps/towns/<name>.yaml --strict
```

`build.py` generates the settlement (`mapgen.town.generate`), merges its spec fragment into `src/<name>.base.yaml` with
`mapgen/town/embed.py` (`python -m mapgen.town.embed BASE FRAGMENT... --out OUT` does the same for hand use), and adds
`teams` with bases 120-200 m outside the two most opposed gates plus an `approach_a`/`approach_b` road (seeded side waypoints and `wobble`, so it curves) from each base ending on
its gate point. The flowgraph is the movement-graph fragment plus spawn nodes `A`/`B` linked to those gates. Edit terrain
in `src/*.base.yaml`; the generated `<name>.yaml` files are overwritten by `build.py`.

Water maps: `twinbridge` (river, 8 bridge crossings), `saltport` (bay with harbour, docks and a reserved island site drawn as a
`clearing`; the island carries no fort), `swordmarch` (river plus a detached `fort=island` fort). Settlement seeds were picked by
scanning for a clean `flowgraph check --strict` (twinbridge seeds 11 fails: two bridges within 30 m; saltport seed 5 builds with
"no bridge or ford" warnings from a smoothed road clipping the bay; swordmarch needs `fort=island`, see the findings file).

Layered organic maps: `rivermere` (organic-radial, river with 3 bridges), `wyndale` (organic-voronoi, open), `greywatch` (organic-grid, corner fort), `driftholt` (organic-cells, `edge=fade` sprawl with hamlets), `harrowmouth` (organic-mixed, bay with harbour; the bay disc is clipped by the map edge for some seeds, which fails `validate`: seeds 1, 3, 4, 9, 11, 13, 16-19 of the sweep, so seed 14 was used), `cindermarket` (organic-mixed, 7 arteries, 3 arcs). The settlement id is part of the random streams: sweep seeds with the real id. Block fill kinds mix inside every town (grid, radial, voronoi, bsp, wedge, follow).

Open-edge towns (`thornwick`, `wyndale`, `driftholt`) have no wall: the team approach roads end on `<sid>_entry_a<k>` points at the footprint edge, bases sit at least 140 m outside, and no `gate` crossings are emitted.
