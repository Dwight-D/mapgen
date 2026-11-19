# Map iterations

Frozen generations of a map, kept side by side for comparison. This folder is outside the
regression suite (which only globs `maps/example_*.yaml` and `maps/concepts/*.yaml`); nothing here
has a baseline.

## Layout

`<map>/vN_<tag>/<map>_vN.yaml`, e.g. `town_square/v2_walled/town_square_v2.yaml`.
The spec's `name:` must be unique per generation (`<map>_vN`) so build outputs do not collide.
Each file starts with a comment giving what the generation is and its source commit.

## Build and compare

```
.venv/Scripts/python -m mapgen build maps/iterations/town_square/v1_first/town_square_v1.yaml --out out/iterations
.venv/Scripts/python -m mapgen build maps/iterations/town_square/v2_walled/town_square_v2.yaml --out out/iterations
```

View `out/iterations/<name>/map.png` for each generation. Optionally check against the flowgraph:

```
.venv/Scripts/python -m mapgen flowgraph check flowgraphs/town_square.flow.yaml maps/iterations/town_square/v2_walled/town_square_v2.yaml --strict --terrain
```
