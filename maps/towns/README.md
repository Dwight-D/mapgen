# Town showcase maps

Four maps that embed generated settlements (`mapgen/town/`, `mapgen/TOWN_SPEC.md`) in hand-written terrain. They are
not in the regression suite and have no baselines.

| map | size | generator | seed | settlement params | settlement id |
|---|---|---|---|---|---|
| `hearthmoor` | 1000 | grid | 14 | extent_frac 0.58, aspect 1.2, gates 4, inner_wall false, citadel true, rotation 0 | `hearth` |
| `pentring` | 1200 | radial | 7 | radius_frac 0.6, spokes 5, rings 2, gates 5, citadel false | `pent` |
| `ashgrove` | 900 | voronoi | 21 | organic true, patches 16, radius_frac 0.6, gates 4, citadel false | `ash` |
| `marchfold` | 1500 | voronoi | 33 | radius_frac 0.5, patches 14, gates 4, fort corner, fort_outposts 2, citadel false | `march` |

Map seeds (terrain) are 5701-5704 in `src/<map>.base.yaml`.

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
`teams` with bases 120-200 m outside the two most opposed gates plus an `approach_a`/`approach_b` road from each base ending on
its gate point. The flowgraph is the movement-graph fragment plus spawn nodes `A`/`B` linked to those gates. Edit terrain
in `src/*.base.yaml`; the generated `<name>.yaml` files are overwritten by `build.py`.
