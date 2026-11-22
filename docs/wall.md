# Town walls as a nodachi layout (`mapgen export-wall`)

`mapgen export-wall <spec | build dir>` writes `out/<name>/export/<name>.wall.nlayout.json`: a town's walls and the
gates in them, in nodachi's layout interchange format (`nodachi-layout` version 1). A wall graph (nodachi
`Pack Along Splines`, `Cut Spline`) reads it through a Layout Input; its `Roads` output carries the wall splines and its
`Points` output the gates. Targets, `--out` and `--settlement` work as for `mapgen export-nlayout` (`docs/nlayout.md`);
both target forms give byte-identical output.

## Frame

Same as `export-nlayout`: `[x, 0, z]` in metres, export `(x, z)` (x east, z north, origin the map's south-west corner),
y = 0 (terrain supplies height), values rounded to 1 mm. `cells` is empty.

## Splines — one per solid wall run

| field / attribute | value |
|---|---|
| `points` | the wall polyline at its full vertex count (a ring's repeated first point is dropped) |
| `interpolation` | `linear` |
| `closed` | `true` for a ring wall with no `open_shore`; absent for each run of a wall left open to water |
| `kind` | `wall` |
| `wallId` | the wall id in `settlements.json` |
| `wallKind` | `outer`, `inner` or `citadel` |
| `wallWidth` | wall thickness in metres |
| `runIndex` | 0-based solid run index within the wall (always 0 for a ring) |
| `settlement` | settlement id |

Solid runs are the wall's edges outside its `open_shore` ranges, in ring order; a run that wraps past the ring start is
joined into one.

## Points — one per gate that sits in a wall

Gates whose `wall` is null (`entry` gates of a wall-less town) are not written.

| field / attribute | value |
|---|---|
| `position` | the gate position on the wall line |
| `rotationY` | degrees, yaw of the gate normal (pointing out of the town): `atan2(nx, nz)` |
| `gateId` | the gate id after its last `_gate_` (`cinder_gate_a4` → `a4`); the whole id when it has no `_gate_` |
| `gateRef` | the full gate id |
| `kind` | `gate`, `postern` or `water_gate` |
| `wallId` | the wall the gate sits in |
| `roadId` | the road through the gate (when it has one) |
| `roadWidth`, `width` | that road's width in metres (when it has a road) |
| `settlement` | settlement id |

Ids are integers in file order: splines in wall order then run order, points in gate order.
