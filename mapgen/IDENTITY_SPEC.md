# Stable feature identity — implementation spec

This file is the complete brief for roadmap package 3, "Stable feature identity" (`docs/roadmap.md`,
backlog item `mapgen-stable-feature-identity`). An agent can execute any wave in section 15 by reading only
this file and the repo files it names. Repo root: `E:\GameDev\Unity\tools\mapgen` (paths below are relative
to it; every command runs from it). Interpreter: `.venv/Scripts/python` (Python 3.8).

## 1. Purpose

A map spec names every region, line, scatter entry and crossing with an `id`. The id is the feature's
identity:

- its noise seeds derive from the id (or from an explicit per-feature `seed`), so a feature's shape does not
  depend on where it stands in its list;
- it is the id under which the feature is exported;
- automatic crossings take ids built from the ids of the line and the water feature that meet.

List order of `regions` is paint order and nothing else. A spec states the format it is written in with a
top-level `format_version`, and mapgen states which format versions it reads.

The eight committed specs are migrated so that every heightmap, raster, render and analysis number stays
byte-identical; the only output differences are id values and the spec echo (section 9).

## 2. Decisions (owner-visible)

The roadmap fixes the design; these are the details this spec decides. Each is justified where it is
specified.

| # | decision | section |
|---|---|---|
| D1 | A feature's `seed` is an integer in `[0, 4294967295]`, the **feature seed** `K`. Every noise use of the feature adds `K` (scatter: `97·K`) to the spec seed and its fixed offset. Without `seed`, `K` = first 4 bytes of SHA-256 of the UTF-8 id, big-endian. | 4 |
| D2 | **Scatter entries get a required `id` and an optional `seed`**, like regions. Migration ids `scatter<j>`, seeds `j`. | 3.2, 4 |
| D3 | **Explicit crossings require an `id`.** Crossings have no noise, so they take no `seed`. Migration ids `crossing_<i>`. | 3.2 |
| D4 | Automatic crossing id = `<line id>-<water id>-<n>`; `n` counts that line's automatic crossings of that water feature in order along the line, from 1. The hyphen cannot occur in a spec id, so an automatic id never equals a spec id. | 6 |
| D5 | **One id namespace** for team bases (`base_<team>`), regions, lines, scatter entries, crossings and POIs. Ids match `[a-z][a-z0-9_]*`, at most 64 characters. **kingsjaw's crossing `eyrie` collides with POI `eyrie` and is renamed `eyrie_pass`.** | 3.2, 9.1 |
| D6 | Top-level `format_version: 1` (integer), required. `spec.resolved.json` echoes it; `meta.json` does not. `mapgen --version` prints the versions mapgen reads. | 3.1 |
| D7 | **`report.json` and `report.md` change in id strings** (automatic crossing ids, chokepoint `near` names). Every number in them stays byte-identical. This departs from the literal rule "analysis outputs byte-identical"; options in 9.4. Recommendation: accept as an id-only change. | 9.4 |
| D8 | `mapgen/__init__.py` `__version__` is `0.2.1`; this package does not bump it. Battlehounds' importer accepts only mapgen `0.2.*` (`ImportVersion.SupportedMapgen`); a minor bump would make it refuse every bundle. | 12 |
| D9 | Migrated ids are the ids exported today: `region_<k>` counts the scatter regions painted before the explicit regions, so thornwald's first explicit region is `region_1`. Battlehounds scenarios reference `region_2` and `region_3` of thornwald; those stay valid. | 9.1 |

## 3. Spec format rules

### 3.1 `format_version`

- Top-level key `format_version`, an integer. This format is version `1`.
- mapgen reads the versions in `mapgen.schema.SPEC_FORMAT_VERSIONS = (1,)`; `SPEC_FORMAT_VERSION = 1` is the
  version of this format. `python -m mapgen --version` prints
  `mapgen 0.2.1 (spec format versions: 1)`. README and `MAPGEN_SPEC.md` §4.1 state the same.
- A spec without the key is invalid: the error is reported and validation continues, so the output lists
  every other problem too (an unversioned spec typically also lacks ids).
- A spec whose `format_version` is present but not an integer in `SPEC_FORMAT_VERSIONS` (any other integer, a
  string, a float such as `1.0`, a boolean, null) is invalid, and that error is the only one reported: a spec of another format version
  follows other rules, so further messages would mislead.
- `spec.resolved.json` carries `"format_version": 1` as its first key. `meta.json` does not carry it: meta
  is the bundle's provenance for consumers, and the export format gets its own version (roadmap package 6).

### 3.2 Ids

| section | `id` | `seed` |
|---|---|---|
| `regions[]` | required | optional |
| `lines[]` | required | optional |
| `scatter[]` | required | optional |
| `crossings[]` | required | not allowed (unknown key) |
| `pois[]` | required | not allowed |
| team bases | implicit `base_<team id>` | — |

- **Charset:** an id is a string that matches `[a-z][a-z0-9_]*` in full (a trailing newline is rejected) and
  is at most 64 characters. A YAML number is
  not an id. All ids in the eight committed specs match.
- **One namespace.** All ids above, and every automatic crossing id, are distinct within a spec. A
  reference anywhere in a spec (`join`, and the point references of roadmap package 4 to POIs, bases and
  named `points` from line paths and region shapes) therefore names exactly one thing. Any spec section
  that declares named things joins this namespace. Justification: package 4 resolves a point reference by
  name without knowing the kind; zones and routes (packages 13, 15) list member ids of mixed kinds; the
  Battlehounds macro baker puts POI, crossing and region ids into one area table and suffixes clashes
  with `_2` (`MacroMapBaker.cs:464-468`), which the unmigrated kingsjaw pair `eyrie` / `eyrie` triggers.
- Team ids keep their own rules (any mapping key, typically `A`, `B`, `C`). The id `base_<team id>` is
  reserved: a spec id equal to it is an error. With upper-case team ids no spec id can equal it.
- `join` names a line id.
- **Renaming an id** changes the feature's id-derived seed and therefore its shape, unless the feature
  carries an explicit `seed`. Every feature of the eight migrated specs carries one, so renaming them is
  shape-safe.

### 3.3 `seed`

`seed` on a region, line or scatter entry is an integer `K` with `0 <= K <= 4294967295` (YAML booleans and
floats are rejected). It takes precedence over the id-derived value. Its meaning is fixed by the table in
section 4: `K` is the per-feature term in every lattice seed of that feature.

### 3.4 Validation messages

Every message starts with the key path. `<V>` below is `", ".join(str(v) for v in SPEC_FORMAT_VERSIONS)`
(`1`), `<x>` is Python `%r` of the offending value.

| situation | message |
|---|---|
| no `format_version` | `format_version: missing required key (this mapgen reads format versions: <V>)` |
| `format_version` not readable | `format_version: unsupported value <x> (this mapgen reads format versions: <V>)` (only error reported) |
| region / line / scatter / crossing without `id` | `regions[3].id: missing required key` (`lines[i]`, `scatter[i]`, `crossings[i]`; the generic missing-key message) |
| id not a valid id | `regions[3].id: must match [a-z][a-z0-9_]* and be at most 64 characters (got <x>)` (any section, POIs included) |
| id used twice | `lines[0].id: duplicate id 'wood' (first used at regions[0].id)` |
| id equal to a base id | `regions[0].id: 'base_a' is reserved for the implicit base of team a` |
| bad `seed` | `regions[3].seed: expected an integer in [0, 4294967295], got <x>` |
| `seed` on a crossing or POI | the generic unknown-key message, e.g. `crossings[0].seed: unknown key (allowed: type, at, r, path, width, id)` |

The duplicate check runs once over the whole spec after every section is parsed, visiting sections in the
order bases, `regions`, `lines`, `scatter`, `crossings`, `pois` and items in list order; the first holder
of an id is the one named. Items whose id is missing or invalid are skipped (their own error is already
reported). Its errors follow the per-section errors in the message list.

Prototype output for the unmigrated thornwald (15 errors: `format_version`, 12 regions, 2 scatter entries):

```
15 error(s) in maps/concepts/thornwald.yaml:
  format_version: missing required key (this mapgen reads format versions: 1)
  regions[0].id: missing required key
  ...
```

## 4. Feature seeds

### 4.1 Rule

```python
def feature_seed(feature_id: str) -> int:
    """Id-derived feature seed: first 4 bytes of SHA-256 of the UTF-8 id, big-endian unsigned."""
    return int.from_bytes(hashlib.sha256(feature_id.encode("utf-8")).digest()[:4], "big")
```

- Feature seed `K` of a region, line or scatter entry = its `seed` if given, else `feature_seed(id)`.
- SHA-256 gives the same value on every platform and in every process. Python's `hash()` is salted per
  process (`PYTHONHASHSEED`) and is never used. Golden values (prototype, verified with
  `PYTHONHASHSEED=123`): `feature_seed("mossbeck") == 1892764035` (`0x70d14983`),
  `feature_seed("region_6") == 2640903791`, `feature_seed("a") == 3398926610`.
- The lattice hash (`noise.hash_lattice`) uses `(int(seed) * 2246822519 + 3266489917) & 0xFFFFFFFF`, computed
  on Python integers, so any non-negative seed sum is valid; no numpy integer overflow is possible.
- Region **noise key** `key(r)`: `K` of the region; for a region generated from a scatter entry,
  `1000 + K` of that entry. (`geometry.region_key`.)
- Justification of the offsets and multipliers: a migrated feature's explicit `seed` is its list index,
  and with `K` = list index every lattice seed equals the one its baseline was built with, so the migration
  is byte-identical (measured, section 9). An id-derived `K` is a 32-bit value, so features of a spec
  written with ids alone almost never share a lattice seed (small integer keys do: `K = 0` puts a region
  outline on `S`, the base relief seed).

### 4.2 Every noise use

`S` = spec `seed`; `o` = fbm octave index (each fbm octave adds `1013·o` to the seed; `noise.py:63, 96,
112, 144`); `K_l` = line feature seed; `K_s` = scatter feature seed. Line numbers are current at commit
`83b69a7` (verified).

| use | lattice seed | sampled over | code |
|---|---|---|---|
| base relief | `S + 1013·o` | world x, y | `height.py:72` |
| terrain detail | `S + 500 + 1013·o` | world x, y | `height.py:74` |
| hills height (`add_noise`) | `S + 1000 + key(r) + 1013·o` | world x, y | `height.py:90` |
| mountain ridges (`ridge`) | `S + 2000 + key(r) + 1013·o` | world x, y | `height.py:105` |
| blob outline roughness | `S + key(r) + 1013·o` | angle | `geometry.py:194` |
| poly outline roughness | `S + key(r) + 1013·o` | perimeter fraction | `geometry.py:325` → `177` |
| band outline roughness | `S + key(r) + 1013·o` | perimeter fraction | `geometry.py:209` → `177` |
| region warp, x / y | `S + 3000 + 2·key(r) + 1013·o` / `... + 7919` | world x, y | `geometry.py:330` → `222` |
| region fragmentation | `S + 6000 + key(r) + 1013·o` | world x, y | `geometry.py:302` |
| line meander | `S + K_l + 1013·o` | arc length | `geometry.py:348` |
| line width variation | `S + 5000 + K_l + 1013·o` | arc length | `geometry.py:360` |
| scatter field | `S + 8000 + 97·K_s + 1013·o` | world x, y | `geometry.py:511, 537` |
| scatter clump field | `S + 8000 + 97·K_s + 31 + 1013·o` | world x, y | `geometry.py:539` |
| scatter clearance warp, x / y | `S + 8000 + 97·K_s + 53 + 1013·o` / `... + 7919` | world x, y | `geometry.py:524` → `222` |
| stipple pattern (render only) | `S + 4000`, `+1`, `+2` (no octaves) | image pixels | `render.py:509` → `357-359` |

Regions generated from scatter entries never run outline, warp or fragmentation; their `key(r)` matters
only when the scatter type has `height_mode` `add_noise` or `ridge` (none of the eight specs).
Nothing else in the package draws noise (no `random`, no `numpy.random`).

## 5. Export ids

Every exported `id` is the spec id; the code has no positional fallback.

| file | entries | id |
|---|---|---|
| `regions.json`, `spec.resolved.json` `regions` | explicit and scatter regions in paint order | the region's or scatter entry's `id` |
| `lines.json`, `spec.resolved.json` `lines` | lines in spec order | the line `id` |
| `crossings.json`, `spec.resolved.json` `crossings` | explicit crossings in spec order, then automatic ones | explicit: the spec `id`; automatic: section 6 |
| `pois.json`, `spec.resolved.json` `pois` | bases, then POIs | `base_<team>`, POI `id` |
| `report.json` `auto_crossings`, `report.md` table | automatic crossings | section 6 |
| `report.json` / `report.md` chokepoint `near` | nearest named feature | crossings appear as `<type> <id>` |

`spec.resolved.json` additionally carries `"format_version"` (first key) and `"seed"` (the resolved feature
seed, right after `"id"`) in every `regions` and `lines` entry; for a scatter region it is the scatter
entry's `K`.

## 6. Automatic crossing ids

Automatic crossings are generated as specified in `MAPGEN_SPEC.md` §4.7 (lines in spec order, rivers in
spec order, lake fords per line). Ids do not influence generation order, geometry or list position in
`spec.crossings`. After generation each automatic crossing gets its id:

1. Record, per generated crossing: the line id `L`; the water id `W`; the position `s` =
   `line.centreline.project(Point(crossing.at))` (metres along that line's final centreline).
   - River crossing: `W` = the river's line id.
   - Lake ford band: `W` = the id of the lake region whose geometry has the longest intersection with the
     ford's centreline piece; ties go to the lexicographically smallest id. (Lakes are unioned before
     piece extraction, so a piece can touch several lakes.)
2. Group by `(L, W)`. Within a group sort by `(s, generation index)`; number from 1.
3. id = `"%s-%s-%d" % (L, W, n)`, e.g. `kings_road-mossbeck-1`, `zigzag-west_lake-1`.

Properties: an automatic crossing's id depends only on its line, its water feature and its order along that
line. Adding a road elsewhere, a crossing of another river, or moving a feature in its list leaves the id
unchanged; a second crossing of the same river further along the same line gets `-2`. List order does decide
which crossings exist where two lines would generate overlapping automatic crossings at the same water
feature: the line listed first gets its crossing and the later line's overlapping crossing is suppressed
(generation covers the area already taken by earlier crossings, `MAPGEN_SPEC.md` §4.7). Spec ids cannot
contain `-`, so automatic ids never collide with spec ids, and two automatic ids differ because
`(L, W, n)` differs. The `crosses` note of an automatic crossing reads `<line> x <river>` or `<line> x lake`.

Prototype (spec in section 10.4: a trail zig-zagging over a straight river four times and
through a lake): `zigzag-snake-1` at (1000, 374), `-2` (1000, 700), `-3` (1000, 1100), `-4` (1000, 1364),
`zigzag-west_lake-1` (364, 1489).

## 7. Code changes

Two files are stored with CRLF line endings and must keep them: `mapgen/export.py`, `mapgen/height.py`
(also `MAPGEN_SPEC.md`, `README.md`). All other touched files are LF. Code style: `%` formatting, `typing`
hints, as in the surrounding code.

### 7.1 `mapgen/schema.py`

- Constants after `BOUNDS_TOLERANCE`: `SPEC_FORMAT_VERSION = 1`, `SPEC_FORMAT_VERSIONS = (1,)`,
  `ID_MAX_LEN = 64`, `SEED_MAX = 4294967295`; function `feature_seed` (section 4.1); regex
  `_ID = re.compile(r"^[a-z][a-z0-9_]*$")` next to `_NAME`.
- Dataclasses:
  - `Region`: `id: str = ""`; `seed: int = 0` (feature seed; for a scatter region the scatter entry's
    seed). `Region` has no other noise-key field.
  - `Line`: `id: str = ""`; field `seed: int = 0` appended.
  - `Scatter`: fields `id: str = ""`, `seed: int = 0` appended.
  - `Crossing`: `id: str = ""`.
  - `MapSpec`: field `format_version: int = SPEC_FORMAT_VERSION` (place it before `scatter_applied`).
- Helpers (module level, after `_ID`):

```python
def _ident(v: "_V", d: Dict[str, Any], path: str) -> Optional[str]:
    """The item's id, or None (missing: reported by keys(); invalid: reported here)."""
    if "id" not in d:
        return None
    x = d["id"]
    if not isinstance(x, str) or not _ID.match(x) or len(x) > ID_MAX_LEN:
        v.err(path + ".id", "must match [a-z][a-z0-9_]* and be at most %d characters (got %r)" % (ID_MAX_LEN, x))
        return None
    return x


def _feature_seed_of(v: "_V", d: Dict[str, Any], path: str, fid: Optional[str]) -> int:
    if "seed" in d:
        x = d["seed"]
        if isinstance(x, bool) or not isinstance(x, int) or not 0 <= x <= SEED_MAX:
            v.err(path + ".seed", "expected an integer in [0, %d], got %r" % (SEED_MAX, x))
            return 0
        return x
    return feature_seed(fid) if fid is not None else 0
```

- `_validate`, at the top (before `v.keys(raw, "", top, ...)`):

```python
    if isinstance(raw, dict):
        versions = ", ".join(str(x) for x in SPEC_FORMAT_VERSIONS)
        if "format_version" not in raw:
            v.err("format_version", "missing required key (this mapgen reads format versions: %s)" % versions)
        elif isinstance(raw["format_version"], bool) or raw["format_version"] not in SPEC_FORMAT_VERSIONS:
            v.err("format_version", "unsupported value %r (this mapgen reads format versions: %s)"
                  % (raw["format_version"], versions))
            return None, v.errors
```

- `top = ["format_version", "name", "brief", "size", "seed", "terrain", "teams", "regions", "lines",
  "crossings", "pois", "scatter", "analysis", "output", "auto_crossings"]`; required `["name", "size",
  "teams"]` (`format_version` has its own message above).
- Regions: `_REGION_COMMON = ["type", "shape", "label", "label_at", "id", "seed", "height", "edge", "warp",
  "warp_scale", "fragment", "fragment_scale"]`; both `v.keys` calls require `["id", "type", "shape"]`
  (+ the shape's required keys). `rid = _ident(v, r, p)`; `Region(index=i, type=..., shape=shape,
  seed=_feature_seed_of(v, r, p, rid))`; `reg.id = rid or ""`.
- Lines: allowed `["type", "path", "width", "meander", "meander_scale", "width_var", "id", "seed", "label",
  "label_at", "join", "join_end"]`, required `["id", "type", "path"]`; `lid = _ident(...)`,
  `Line(..., id=lid or "", seed=_feature_seed_of(v, ln, p, lid))`.
- Crossings: allowed `["type", "at", "r", "path", "width", "id"]`; required
  `["id", "type"]`; `id=_ident(v, c, p) or ""` in both constructors.
- POIs: `pid = _ident(v, pv, p)`. Uniqueness and the reserved base ids are checked only by the namespace
  pass below.
- Scatter: allowed `["id", "seed", "type", "coverage", "scale", "clump", "avoid", "within", "on"]`,
  required `["id", "type", "coverage"]`; `Scatter(..., id=sid or "", seed=_feature_seed_of(v, sc, p, sid))`.
- Namespace pass, after the scatter section and before `# analysis / output`:

```python
    first: Dict[str, str] = {}
    for tid in teams:
        first["base_" + tid] = "teams.%s" % tid
    for sect in ("regions", "lines", "scatter", "crossings", "pois"):
        items = raw.get(sect)
        if not isinstance(items, list):
            continue
        for i, it in enumerate(items):
            fid = it.get("id") if isinstance(it, dict) else None
            if not isinstance(fid, str) or not _ID.match(fid) or len(fid) > ID_MAX_LEN:
                continue
            p = "%s[%d].id" % (sect, i)
            if fid not in first:
                first[fid] = p
            elif first[fid].startswith("teams."):
                v.err(p, "'%s' is reserved for the implicit base of team %s" % (fid, fid[5:]))
            else:
                v.err(p, "duplicate id '%s' (first used at %s)" % (fid, first[fid]))
```

- `MapSpec(...)` gets `format_version=raw["format_version"]`.

### 7.2 `mapgen/geometry.py`

- `region_key(reg: Region) -> int`: `1000 + reg.seed if reg.scatter is not None else reg.seed`, the only
  region noise-key function, used at `geometry.py:194, 209, 302, 325, 330` and by `height.py`.
- `line_centreline` meander seed `seed + ln.seed` (`:348`); `variable_width_area` seed
  `seed + 5000 + ln.seed` (`:360`).
- `scatter_geometry`: `sd = spec.seed + 8000 + 97 * sc.seed` (`:511`).
- `build_geometries` scatter regions (`:598-599`): `Region(index=-1, type=sc.type, shape="scatter",
  id=sc.id, height=..., seed=sc.seed, scatter=sc)`.
- `auto_crossings` (`:432-497`), section 6:
  - build `lake_list = [(r.id, regions[i]) for i, r in enumerate(spec.regions) if <water region, non-empty>]`
    and derive `lake_geoms` from it;
  - `keys: List[Tuple[str, str, float]] = []`; after each river-crossing `add(...)`:
    `keys.append((ln.id, rl.id, lg.centreline.project(Point(at))))`; after each lake-ford `add(...)` and
    its `r` fix-up: `best = max(sorted(lake_list, key=lambda e: e[0]), key=lambda e:
    e[1].intersection(piece).length)` then `keys.append((ln.id, best[0], lg.centreline.project(Point(out[-1][0].at))))`;
  - `add` creates the crossing with `id=""`; before `return out`, group and number as in section 6 and set
    `out[k][0].id`;
  - `lname = ln.id` and `rl.id` without fallbacks.
- `Dict` is already imported from `typing`.

### 7.3 `mapgen/height.py`

`from .geometry import MapGeometry, _resample, region_key`; hills `spec.seed + 1000 + region_key(r)`
(`:90`), ridges `spec.seed + 2000 + region_key(r)` (`:105`). Keep CRLF.

### 7.4 `mapgen/export.py` (keep CRLF)

- `write_json`: ids without fallback: `"id": ln.id` (`:188`), `"id": reg.id` (`:202`), `"id": c.id`
  (`:215`).
- `write_resolved`: region entry starts `{"id": r.id, "seed": r.seed, "type": ...}` (remaining keys in their order);
  line entry `{"id": ln.id, "seed": ln.seed, "type": ...}`; crossing entry `"id": c.id`; `data` starts with
  `"format_version": spec.format_version` before `"name"`.
- `write_meta`: no edit.

### 7.5 `mapgen/analysis.py`, `mapgen/build.py`, `mapgen/render.py`

Ids are always set, so every id expression uses the id directly (output identical): `_nearest_feature` crossing name
`"%s %s" % (c.type, c.id)` (`:106`); line fallback `ln.label or ln.id` (`:129`); warnings `"river %s" %
ln.id` (`:301`), `"lake %s" % reg.id` (`:304`), `ln.id` (`:318`); `probe_point` region `reg.label or
reg.id` (`:343`), lines `ln.id` (`:345`), crossings `cr.id` (`:347`). `build.py` and `render.py` need no
change (render draws no region, line or crossing ids).

### 7.6 `mapgen/__main__.py`

In `main()`, before `sub = p.add_subparsers(...)`:

```python
    from . import __version__
    p.add_argument("--version", action="version", version="mapgen %s (spec format versions: %s)" % (
        __version__, ", ".join(str(v) for v in schema.SPEC_FORMAT_VERSIONS)))
```

argparse prints to stdout and exits 0 before the required-subcommand check (verified on the prototype).
The `validate` output format stays as in `tests/REGRESSION_SPEC.md` §2.4.

## 8. Migration tool (scratch, not committed)

`out/identity-review/migrate.py` (`out/` is git-ignored). Text-level edits only, so comments, key order,
alignment and LF line endings survive. Usage: `migrate.py <spec in> <spec out> [--rename
<section>:<index>:<id> ...]`. Algorithm:

1. Read the file as text (`newline=""`); refuse CR characters. `root = yaml.compose(text)` (node marks
   give line and column of every mapping).
2. `npre` = number of `scatter` entries without an `on` key (compare key node `.value == "on"`; do not use
   `yaml.safe_load`, which turns the key `on` into `True`).
3. For every list item, the item must be a flow mapping (`node.flow_style`); stop with an error otherwise
   (all eight specs are flow style). Planned insertions:
   - `scatter[j]` without `id`: insert `id: scatter<j>, seed: <j>, ` after the `{` and the spaces after it.
   - `regions[i]` without `id`: insert `id: region_<i + npre>, seed: <i>, ` likewise.
   - `lines[i]` without `id`: insert `id: line_<i>, seed: <i>, `. With `id`: insert `, seed: <i>` right after
     the id value's end mark (`{ id: mossbeck, seed: 0, type: river, ...`).
   - regions or scatter entries that already have an `id` get `, seed: <index>` the same way (none today).
   - `crossings[i]` without `id`: insert `id: crossing_<i>, `. A `--rename crossings:<i>:<id>` replaces the
     id value's text between its start and end marks.
   - Refuse an item that already has `seed`.
4. Apply insertions per line from the rightmost column leftwards.
5. Insert the line `format_version: 1` before the line of the top-level `name` key (line 1 in all eight).
6. Check the namespace: collect all ids after renaming (+ POI ids) and print every collision; the result must
   validate with `python -m mapgen validate`.
7. Self-check: deleting the inserted text from the output reproduces the input byte for byte.

## 9. Migration

### 9.1 Per-spec edits

`npre` = scatter entries without `on` (painted before explicit regions, so they take the first export
indices).

| spec file | `name` | scatter (`npre`) | region ids added | line ids added / seeds added | crossing ids added | other |
|---|---|---|---|---|---|---|
| `maps/example_natural.yaml` | natural | 2 (2) | `region_2`…`region_10` | 3 / 3 | 3 (`crossing_0`…`_2`) | — |
| `maps/example_river_crossing.yaml` | river_crossing | 0 | `region_0`…`region_4` | 3 / 3 | 3 | — |
| `maps/example_three_team.yaml` | three_team | 0 | `region_0`…`region_14` | 6 / 6 | 0 | — |
| `maps/concepts/ashen_steppe.yaml` | ashen_steppe | 3 (3) | `region_3`…`region_14` | 0 / 7 | 0 | — |
| `maps/concepts/gloamfen.yaml` | gloamfen | 3 (3) | `region_3`…`region_18` | 0 / 11 | 0 | — |
| `maps/concepts/kingsjaw.yaml` | kingsjaw | 4 (3) | `region_3`…`region_16` | 0 / 5 | 0 | `crossings[2]` `eyrie` → `eyrie_pass` |
| `maps/concepts/saltreach_delta.yaml` | saltreach_delta | 6 (5) | `region_5`…`region_19` | 0 / 15 | 0 | — |
| `maps/concepts/thornwald.yaml` | thornwald | 2 (1) | `region_1`…`region_12` | 0 / 7 | 0 | — |

Every region gets `seed: <its index in regions>`, every line `seed: <its index in lines>`, every scatter
entry `id: scatter<j>, seed: <j>`, and every spec `format_version: 1` as line 1. Example (thornwald):

```yaml
format_version: 1
name: thornwald
...
scatter:
  - { id: scatter0, seed: 0, type: forest, coverage: 0.58, scale: 420, clump: 0.6, avoid: 8 }
  - { id: scatter1, seed: 1, type: forest, coverage: 0.42, scale: 110, clump: 0.1, avoid: 8, on: [hills] }

regions:
  - { id: region_1, seed: 0, type: town,   shape: poly, points: [[250, 350], ...
  - { id: region_2, seed: 1, type: hills,  shape: blob, at: [1900, 2650], ...
...
lines:
  - { id: mossbeck, seed: 0, type: river, path: [[650, 2800], ...
```

The single namespace collision in the eight specs is kingsjaw `crossings[2].id: eyrie` (band pass) vs
`pois[2].id: eyrie` (Eyrie Roost camp). The crossing is renamed: the POI id is an objective id used in the
report, the diff and Battlehounds scenarios; the crossing id appears only in `crossings.json` and one
chokepoint name. Alternative the owner can choose instead: two namespaces (points: POIs, bases, package-4
`points`; features: regions, lines, scatter, crossings) with no rename.

### 9.2 Measured result (prototype, all eight maps)

Prototype: the repo's `mapgen/` and `maps/` copied to scratch, sections 7.1–7.6 applied, the eight specs
migrated with the section 8 algorithm, each built into an empty directory. Pre-migration builds of the repo
code were first checked identical to `tests/baselines/*.json` (0 differences on all eight). Then every
migrated build was compared with its baseline and with `mapgen diff`.

**Byte-identical on every map:** `export/heightmap.r16`, `export/heightmap.png`, every `export/masks/*.png`,
`export/splat_*.png`, `export/pois.json`, `export/lines.json`, `export/regions.json`, `map.png`, every
`overlay_*.png`. Every analysis number in `report.json` (objective times, spreads, flags, shares, chokepoint
positions, counts, constrictions, scores, openness, warnings) is unchanged; `mapgen diff` reports 0 changed
objectives and every metric delta `+0`.

| map | files differing from baseline | JSON path patterns (`mapgen diff`) | class |
|---|---|---|---|
| ashen_steppe | 5 | `crossings.json`: `/*/id` ×4 · `meta.json`: `/spec_sha256` · `spec.resolved.json`: `/crossings/*/id` ×4, `/format_version`, `/lines/*/seed` ×7, `/regions/*/seed` ×15 · `report.json`: `/auto_crossings/*/id` ×4, `/chokepoints/*/near` ×1 | id + echo |
| gloamfen | 5 | `/*/id` ×5 · `/spec_sha256` · resolved `/crossings/*/id` ×5, `/format_version`, `/lines/*/seed` ×11, `/regions/*/seed` ×19 · report `/auto_crossings/*/id` ×5, `/chokepoints/*/near` ×1 | id + echo |
| kingsjaw | 5 | `/*/id` ×1 (`eyrie` → `eyrie_pass`) · `/spec_sha256` · resolved `/crossings/*/id` ×1, `/format_version`, `/lines/*/seed` ×5, `/regions/*/seed` ×18 · report `/chokepoints/*/near` ×1 (`pass eyrie` → `pass eyrie_pass`) | id + echo |
| natural | 5 | `/*/id` ×1 · `/spec_sha256` · resolved `/crossings/*/id` ×1, `/format_version`, `/lines/*/seed` ×3, `/regions/*/seed` ×11 · report `/auto_crossings/*/id` ×1, `/chokepoints/*/near` ×1 (`bridge (1193, 1271)` → `bridge crossing_0`) | id + echo |
| river_crossing | 5 | `/*/id` ×1 · `/spec_sha256` · resolved `/crossings/*/id` ×1, `/format_version`, `/lines/*/seed` ×3, `/regions/*/seed` ×5 · report `/auto_crossings/*/id` ×1, `/chokepoints/*/near` ×4 (`pass (1500, 2350)` → `pass crossing_2` ×3, `bridge auto_1` → `bridge line_1-line_0-1`) | id + echo |
| saltreach_delta | 5 | `/*/id` ×10 · `/spec_sha256` · resolved `/crossings/*/id` ×10, `/format_version`, `/lines/*/seed` ×15, `/regions/*/seed` ×21 · report `/auto_crossings/*/id` ×10, `/chokepoints/*/near` ×1 | id + echo |
| thornwald | 5 | `/*/id` ×5 · `/spec_sha256` · resolved `/crossings/*/id` ×5, `/format_version`, `/lines/*/seed` ×7, `/regions/*/seed` ×14 · report `/auto_crossings/*/id` ×5, `/chokepoints/*/near` ×2 | id + echo |
| three_team | 2 | `meta.json`: `/spec_sha256` · resolved `/format_version`, `/lines/*/seed` ×6, `/regions/*/seed` ×15 | echo |

The five differing files per map: `export/crossings.json` (id), `export/meta.json` (spec hash: the spec
bytes differ), `export/spec.resolved.json` (echo: `format_version` and `seed` keys added; crossing ids),
`report.json` and `report.md` (id strings: the automatic-crossing list and table, chokepoint `near`
names). `/format_version` and the `seed` paths are `added` operations; all others are `changed`.
Verified mechanically: substituting every pre-migration automatic id by its post-migration id, every
`<type> (<x>, <y>)` chokepoint name by `<type> <id>`, and `pass eyrie` by `pass eyrie_pass` turns the
pre-migration `report.json` and `report.md` into the post-migration files byte for byte on all eight maps.

Automatic id mapping (thornwald): `auto_1` → `kings_road-mossbeck-1`, `auto_2` → `abbey_path-mossbeck-1`,
`auto_3` → `lea_path-mossbeck-1`, `auto_4` → `tarn_path-mossbeck-1`, `auto_5` → `lodge_path-mossbeck-1`.
saltreach_delta: `high_road-trunk-1`, `causeway-arm_w-1`, `coast_road-arm_w-1`, `fen_n-arm_e-1`,
`fen_s-arm_mid-1`, `fen_s-arm_e-1`, `fen_s-arm_sub-1`, `wold_trail-trunk-1`, `reed_path-arm_w-1`,
`reed_path-arm_mid-1`. Migrated example maps have positional-looking ids (`line_2-line_0-1`) because their
lines take the ids they are exported under today. None of the eight maps has a lake ford.

### 9.3 Locality (measured on migrated thornwald)

| edit | heightmap | rasters changed | features changed (id-keyed, `regions.json` / `lines.json` / `crossings.json` / `pois.json`) |
|---|---|---|---|
| insert `{ id: glade_x, type: clearing, shape: blob, at: [2750, 1950], r: 150, rough: 0.6, warp: 60, warp_scale: 80 }` at `regions[0]` | identical | `clearing.png` 0.73 % inside x 2594–2900, z 1794–2105; `forest.png` 0.48 % map-wide; `impassable.png` 9 texels at the Black Tarn shore; `splat_0.png` | `glade_x` added; `scatter1` changed (scatter drift); every other region, line, crossing and POI identical; `spec.resolved.json` identical except the added entry |
| same insert, unmigrated spec and code (for comparison) | 11.4 % changed, max 38 m | 13 of 17 | 13 regions, 5 lines, 4 POIs changed; 28 of 33 files differ |
| insert `{ id: new_hill, type: hills, shape: blob, at: [2800, 1500], r: 200, rough: 0.4, height: 40 }` | 6.0 %, x 2176–3200, z 1063–1907 (hill + road/river blends) | `hills.png`, splats | `new_hill` added; `mossbeck`, `lea_path` heights and water surface, one crossing's `y`; no region changed |
| delete `seed: 5, ` from `region_6` (id-derived seed takes effect) | identical | `clearing.png` 0.18 % inside x 403–650, z 2294–2496; `forest.png` 0.045 %; `splat_0.png` | `region_6` reshaped, `scatter1` drift; resolved `seed` = 2640903791 = `feature_seed("region_6")`; nothing else |
| delete `seed: 0, ` from `mossbeck` | 3.9 % along the river | river, crossing, water masks, forest | `mossbeck` reshaped; its 5 automatic crossings move with it; other lines keep their x/z points (heights sampled on them change); scatter drift |

The forest mask drifts map-wide on every placement edit (roadmap package 5). `impassable.png` changes where
drifted `on` scatter (painted after explicit regions) covers analysis cells at a lake shore. `mapgen diff`
compares JSON lists by position, so an inserted region shows as changes at every later index of
`regions.json`; feature identity across two builds is read by matching entries on `id` (the tests in
section 10.2 do this).

### 9.4 Byte-identity rule and the report files (owner decision D7)

The owner's acceptance rule names "the heightmap, every raster and the analysis outputs" as byte-identical.
Heightmap, rasters and renders are. `report.json` and `report.md` are not: they print automatic crossing ids
(`auto_crossings` list and table) and chokepoint names that include crossing ids (`near`). Every number in
them is identical. Options:

1. **Accept (recommended):** classify `report.json` / `report.md` as id-carrying files, shown with
   `mapgen diff` and re-baselined with the others. Cost: none beyond the review.
2. Keep `auto_<n>` ids: contradicts the decided design (ids from line and water feature) and keeps the
   renumbering problem the package exists to remove.
3. Name crossings by type and position in `near`: still changes `report.md` (automatic-crossing table) and
   makes `near` an unstable string.

## 10. Tests

### 10.1 `tests/test_validation.py` — final content of the case table

`BASE` starts with `format_version: 1\n` followed by the current five lines. `ALLOWED` starts with
`"format_version, name, brief, size, ..."`. Every inline spec that reaches validation carries
`format_version: 1` and an `id` on each region, line, scatter entry and crossing (`r0`, `l0`, `s0`, `c0`).
Cases and expected lines:

| case | spec (on `BASE` unless stated) | expected |
|---|---|---|
| every other case of the file | its spec, carrying `format_version: 1` and ids | its message in the file |
| `duplicate_poi_id` | two POIs `a` | `pois[1].id: duplicate id 'a' (first used at pois[0].id)` |
| `poi_reserved_base_id` | `teams` with lower-case team `a`, POI id `base_a` | `pois[0].id: 'base_a' is reserved for the implicit base of team a` |
| `poi_id_charset` | POI id `base_A` | `pois[0].id: must match [a-z][a-z0-9_]* and be at most 64 characters (got 'base_A')` |
| `missing_format_version` | `BASE` without its first line | `format_version: missing required key (this mapgen reads format versions: 1)` |
| `unversioned_without_ids` | as above + one region without `id` | that line, then `regions[0].id: missing required key` |
| `unsupported_format_version` | `format_version: 2` + `colour: red` | `format_version: unsupported value 2 (this mapgen reads format versions: 1)` only |
| `format_version_string` | `format_version: '1'` | `format_version: unsupported value '1' (this mapgen reads format versions: 1)` |
| `format_version_float` / `format_version_bool` | `format_version: 1.0` / `true` | `format_version: unsupported value 1.0 (...)` (resp. `True`) |
| `id_trailing_newline` | region id `"wood
"` (YAML double-quoted escape) | `regions[0].id: must match [a-z][a-z0-9_]* and be at most 64 characters (got 'wood
')` |
| `name_trailing_newline` | `name: "tiny
"` | `name: must match [a-z0-9_]+ (got 'tiny
')` |
| `region_without_id` / `line_without_id` / `scatter_without_id` / `crossing_without_id` | one item without `id` | `regions[0].id: missing required key` (resp. `lines[0]`, `scatter[0]`, `crossings[0]`) |
| `bad_id` | region id `Big-Wood` | `regions[0].id: must match [a-z][a-z0-9_]* and be at most 64 characters (got 'Big-Wood')` |
| `id_too_long` | region id `"a" * 65` | same message with that value |
| `duplicate_id_across_kinds` | region `wood`, line `wood` | `lines[0].id: duplicate id 'wood' (first used at regions[0].id)` |
| `crossing_id_equals_poi_id` | crossing `eyrie`, POI `eyrie` | `pois[0].id: duplicate id 'eyrie' (first used at crossings[0].id)` |
| `region_id_reserved` | lower-case team `a`, region id `base_a` | `regions[0].id: 'base_a' is reserved for the implicit base of team a` |
| `seed_negative` / `seed_float` / `seed_too_big` / `seed_bool` | region `seed` -1 / 1.5 / 4294967296 / true | `regions[0].seed: expected an integer in [0, 4294967295], got -1` (resp. `1.5`, `4294967296`, `True`) |
| `crossing_seed` | crossing with `seed: 3` | `crossings[0].seed: unknown key (allowed: type, at, r, path, width, id)` |

`test_valid_base_spec`, `test_load_spec_raises_spec_error`, `test_invalid_build_writes_nothing` keep their
assertions.

### 10.2 `tests/test_identity.py` (in-process)

- `test_feature_seed_golden`: the three golden values of section 4.1.
- `test_explicit_seed_equals_derived`: a small spec (`BASE` imported from `test_validation` + one blob forest `w` with `warp` and
  `fragment`, one river line `r` with `meander` and `width_var`), built through
  `geometry.build_geometries` twice: without `seed`, and with `seed: feature_seed(id)` on both; region
  and line geometries have equal `.wkb`.
- `test_auto_crossing_ids`: the spec of section 10.4 through
  `schema.load_spec` + `geometry.build_geometries`; the automatic crossings are
  `["zigzag-snake-1", "zigzag-snake-2", "zigzag-snake-3", "zigzag-snake-4", "zigzag-west_lake-1"]` with
  `round(at)` `(1000, 374)`, `(1000, 700)`, `(1000, 1100)`, `(1000, 1364)`, `(364, 1489)`.
- `test_insert_region_is_local` (module-scoped fixture builds migrated `maps/concepts/thornwald.yaml` with
  `build.build(spec, out, only="export", log=lambda s: None)`; about 5 s per build): insert the line
  `  - { id: glade_x, type: clearing, shape: blob, at: [2750, 1950], r: 150, rough: 0.6, warp: 60, warp_scale: 80 }`
  directly after `regions:` (assert the anchor `"\nregions:\n"` occurs once), build into a second empty dir,
  `r = mapgen.diff.diff_builds(a, b)`, then assert:
  1. `r["heightmap"]["changed_texels"] == 0`;
  2. every raster whose `pixels_identical` is false is one of `export/masks/clearing.png`,
     `export/masks/forest.png`, `export/masks/impassable.png`, `export/splat_0.png`, `export/splat_1.png`;
  3. `clearing.png` `bbox_m` lies inside x 2495–3005, z 1695–2205 (the blob's reach `r·(1 + rough/2) +
     warp` around `at`); `forest.png` `changed_share <= 0.01`; `impassable.png` `changed_share <= 0.001`;
  4. `export/lines.json`, `export/crossings.json`, `export/pois.json` are not in `files.changed`;
  5. `regions.json` matched by `id`: the ids of b minus `glade_x` equal the ids of a in the same order;
     every entry whose id is not a scatter id (`scatter0`, `scatter1`) is equal in a and b;
  6. `spec.resolved.json`: `regions` matched by id are equal for every id of a; `lines`, `crossings`, `pois`,
     `teams` lists are equal.
  Measured: all hold (`clearing.png` x 2594–2900, z 1794–2105; forest 0.0048; impassable 0.0000086; only
  `scatter1` differs).
- `test_id_derived_seed_takes_effect`: delete `seed: 5, ` from `{ id: region_6, seed: 5, ` (assert it occurs
  once), build export-only, assert heightmap unchanged; changed rasters ⊆ {`clearing.png`, `forest.png`,
  `impassable.png`, `splat_0.png`, `splat_1.png`}; `clearing.png` `bbox_m` inside x 331–749, z 2191–2609;
  `regions.json` by id: `region_6` differs, every non-scatter other region equal; `spec.resolved.json`
  `region_6.seed == feature_seed("region_6") == 2640903791`, every other region entry equal.

- Seeds do not come from list position. Each test builds small specs (`BASE`, one `hills` blob region, a
  filler line, and the feature under test) through `schema.load_spec` + `geometry.build_geometries` and
  compares `.wkb`:
  - `test_line_seed_is_id_derived_not_positional`: a river with `meander` and `width_var`: the centreline and
    area built with no `seed` equal those built with `seed: feature_seed("r")` and differ (centreline and area
    separately: meander and width variation) from those built with `seed: 0`, the line's list index.
  - `test_line_geometry_independent_of_list_position`: inserting another line before the river leaves its
    centreline and area identical.
  - `test_scatter_seed_is_id_derived_not_positional`: a scatter entry (its scatter field and the region it
    creates; the region's `seed` equals the entry's): no `seed` equals `seed: feature_seed("s")` and differs
    from `seed: 0`.
  - `test_scatter_geometry_independent_of_list_position`: inserting another scatter entry before it leaves its
    region geometry identical.
  A mutant that derives line seeds (`seed + ln.seed`, `5000 + ln.seed`) and scatter seeds (`97 * sc.seed`)
  from the list index fails all four tests; `test_explicit_seed_equals_derived` alone does not detect it.

### 10.3 Other test files

- `tests/test_cli_exit_codes.py`: `test_version`: `run("--version")` gives exit 0 and stdout
  `"mapgen %s (spec format versions: 1)\n" % mapgen.__version__`. The other tests use `BASE` of
  `test_validation.py`.
- `tests/test_diff.py`: no edit; the `river` fixture's text anchors (`at: [1500, 900] }`,
  `width: 450, height: 220 }`) remain unique in the migrated example and `/regions/2/height` stays the
  mountain.
- `tests/test_stability.py`, `tests/regress.py`, `tests/rebaseline.py`, `tests/baselines/*.json`:
  untouched by the implementation. Until the owner-approved re-baseline, `test_build_matches_baseline`
  fails on all eight maps, each naming exactly the files of the section 9.2 table (5 per map, 2 for
  three_team). That failure is expected and is the input to the review.

### 10.4 Synthetic spec for `test_auto_crossing_ids`

```yaml
format_version: 1
name: synthetic_auto
size: [2000, 2000]
seed: 3
teams:
  A: { base: [100, 100] }
  B: { base: [1900, 1900] }
regions:
  - { id: west_lake, type: lake, shape: blob, at: [500, 1500], r: 200, rough: 0.2 }
lines:
  - { id: snake, type: river, path: [[1000, 0], [1000, 2000]], width: 20 }
  - { id: zigzag, type: trail, path: [[100, 100], [1500, 500], [500, 900], [1500, 1300], [100, 1500], [900, 1500]], width: 5 }
```

## 11. Documentation updates

Contract-first rule: `EXPORT_FORMAT.md` and `MAPGEN_SPEC.md` change in the same commit as the code.

### 11.1 `MAPGEN_SPEC.md` (CRLF)

- §4 example: the migrated `maps/example_river_crossing.yaml` verbatim (it is "the canonical example"),
  starting `format_version: 1                # spec format version; this mapgen reads 1` (align the comment
  with the others).
- §4.1 table, first row: `| format_version | yes | integer; the spec format version (§4.8). This mapgen reads 1 |`.
- §4.2 first sentence: "Every region has `id` (§4.8), `type` ... and `shape`, plus optional `seed` (§4.8),
  `label` ...". Blob row: "seeded by the region's feature seed (§4.8)". Replace "The region index in the noise
  seeds is the entry's index in the spec's `regions` list." with "Noise seeds come from the region's feature
  seed (§4.8)." Warp paragraph: "two independent seeds derived from `seed` and the region's feature seed".
- §4.3: "`id` (required, §4.8)", "optional `seed` (§4.8)".
- §4.4: "`type`, `id` (required, §4.8) and one of two geometries"; last sentence: "Automatic crossings (§4.7)
  have ids `<line id>-<water id>-<n>` (§4.8)."
- §4.5: "`id` (required; §4.8)".
- §4.6: keys table gains rows `id` (required, §4.8) and `seed` (optional, §4.8); "Each entry becomes one
  `Region` (shape `scatter`, the entry's `id`, multi-part geometry)"; noise text "seeded by the entry's
  feature seed (§4.8)".
- §4.7: "Automatic crossings are appended after the explicit crossings in generation order and take the ids
  of §4.8".
- §4.8 "Feature identity" (section to add): sections 3.2, 3.3, 4.1, 4.2 and 6 of this file condensed: id rules
  and namespace, feature seed and `feature_seed`, the noise table, automatic crossing ids. State: "List order
  of `regions` is paint order; it has no effect on any feature's shape."
- §6.5 export table: `regions.json` "`scatter` is `true` for regions generated from a `scatter` entry, which
  carry that entry's `id`".
- §8: add to the error list "missing or unreadable `format_version`; a region, line, scatter entry or
  crossing without `id`; an id that is not `[a-z][a-z0-9_]*` (≤ 64 characters) or used twice in the spec;
  `seed` not an integer in [0, 4294967295]"; "duplicate POI ids" becomes "duplicate ids".
- §9: "All noise is hashed from coordinates and `seed` + a fixed per-use offset + the feature seed (§4.8)."
- §10: "the build adds an automatic bridge and ford (ids `<line>-<river>-1`)".
- §7: add `python -m mapgen --version   # mapgen version and the spec format versions it reads`.

### 11.2 `README.md` (CRLF)

- Commands block: `python -m mapgen --version                                     # mapgen version, spec format versions it reads`.
- Authoring keys table, first rows:
  - `` `format_version` | top level | required; `1`. mapgen reads the versions `--version` prints ``
  - `` `id` | region, line, scatter, crossing, POI | required; `[a-z][a-z0-9_]*`, unique across the whole spec (bases are `base_<team>`). The feature's identity: noise seeds and exported ids come from it. Renaming an id reshapes the feature unless it has a `seed` ``
  - `` `seed` | region, line, scatter | optional integer 0–4294967295; fixes the feature's noise independently of its id. The committed maps carry one on every feature ``
- `auto_crossings` row: "(disc, `r` = river half-width + 15 m, id `<line id>-<river id>-<n>`)".
- Edit loop item 1: "Every region, line, scatter entry and crossing needs an `id`; insert a feature anywhere
  in its list, list order of `regions` is only paint order."

### 11.3 `EXPORT_FORMAT.md`

- §1 or §2 gains a paragraph "**Ids.** Every entry of `regions.json`, `lines.json`, `crossings.json` and
  `pois.json` has an `id` that is unique across the bundle and stable across rebuilds: it is the id written
  in the spec. Automatic crossings have ids `<line id>-<water id>-<n>` (the road or trail, the river or lake
  it crosses, and the crossing's order along that line); they contain `-`, which spec ids never do."
- §3.6: "`scatter: true` marks background patches generated by noise; their ids are the spec's scatter
  entry ids".
- §3.8: "`auto: true` = generated where a road (→ `bridge`) or trail (→ `ford`) meets a river or lake; the id
  is `<line id>-<water id>-<n>`."
- §3.9 keys: "`format_version` (the spec format version), `name`, ..."; "every `regions` and `lines` entry
  carries its feature `seed`."

### 11.4 Other files

- `CLAUDE.md`, Rules, one bullet after Determinism: "**Feature identity.** Every region, line, scatter entry
  and crossing has an `id`; noise seeds and exported ids derive from it (or from an explicit `seed`). Nothing
  keys on list position. Design: `mapgen/IDENTITY_SPEC.md`."
- `docs/roadmap.md`: delete the gap rows "No spec format version" and "List position is feature identity"
  (Editability table); package 3 section gains the line "Implementation spec: `mapgen/IDENTITY_SPEC.md`."
- `mapgen/DIFF_SPEC.md` §6.6, the bullet "Lists are compared by position. Position is the identity mapgen
  exports use ...": "Lists are compared by position (list order is paint order and POI order). An inserted
  or removed entry therefore shows as changes at every later index; a feature's identity across two builds
  is its `id` value."
- `docs/battlehounds-consumer.md`: no change.
- `E:\GameDev\Unity\.claude\skills\map-concept\SKILL.md` (parent tier, agent-owned): Process step 3 gains
  "Start the spec with `format_version: 1`. Give every region, line, scatter entry and crossing an `id`
  (`[a-z][a-z0-9_]*`, unique across the spec including POI ids; name it after the place, e.g. `kingsmead`,
  `mossbeck`). Do not write `seed` on features." Pitfalls gain "Renaming an id reshapes that feature (its
  noise derives from the id) unless it carries `seed`. Automatic crossing ids are `<line>-<water>-<n>`."

## 12. Consumer: Battlehounds

Read-only check of `E:\GameDev\Unity\battlehounds` (paths relative to it):

- `Assets/_Project/Editor/MacroSim/MacroMapBaker.cs:520` labels crossings by stripping the `auto_` prefix
  (`auto_5` → "Ford 5"); other ids fall back to `Pretty(id)`. `:538, 621-625` label unlabelled regions
  `"{Type} {suffix after last _}"` (`region_5` → "Clearing 5"). `:489, 520, 538` persist POI, crossing and
  region ids as macro area ids; clashes get `_2` (`:464-468`, hit by kingsjaw `eyrie` today).
- `Assets/_Project/Editor/MacroSim/MacroAssetBuilder.Scenarios.cs:303-304, 326, 337, 344` and
  `Assets/_Project/Config/AI/Sim/Scenarios/*.asset` reference thornwald `region_2` and `region_3` literally;
  the committed `Assets/_Project/Config/AI/Macro/Maps/MacroMap_*.asset` store `region_<n>`, `auto_<n>` ids.
- `Assets/_Project/Editor/MapImport/Core/`: ids are opaque, but seed the importer RNG per region
  (`Determinism.cs:27`, tree scatter, town lots, ruins) and name meshes and objects (`lake_region_12`,
  `NB_bridge_<id>`). `Bundle/MapBundleReader.cs:46-97` accepts `mapgen_version` `0.2.*` only.
- `report.json`: only `chokepoints[].near` as display text (`"Choke by {near}"`); `auto_crossings` unread.
  `spec.resolved.json`: only `brief` and `teams`.
- Tests and docs cite `auto_<n>`: `Assets/_Project/Tests/EditMode/MacroMapBakeTests.cs:43-53, 163-174`,
  `docs/specs/MACRO_SIM_SPEC.md:1200`, `docs/specs/MAP_IMPORT_SPEC.md:150-151, 1147-1150, 1229`.

Effect of this package: region ids are unchanged (D9), so scenarios, area ids of regions and the importer's
region-seeded placement stay as they are. Automatic crossing ids and kingsjaw's `eyrie` change, so on
re-bake those macro areas get other ids and labels (`Pretty("kings_road-mossbeck-1")`), and bridge objects
other names. `meta.json` keeps `mapgen_version` 0.2.1 (D8).

Follow-up item for `E:\GameDev\Unity\battlehounds\.anthill\backlog\intake\` (filed by the coordinator when
the package lands; YAML frontmatter `title`, `value`, `source`, `status: idea`):

- title: `Map bake: adopt mapgen stable crossing ids (<line>-<water>-<n>)`
- value: `mapgen now names automatic crossings <line id>-<water id>-<n> instead of auto_<n> (kingsjaw's pass
  eyrie becomes eyrie_pass). MacroMapBaker's auto_ prefix label rule stops matching and labels fall back to
  Pretty(id); re-baked MacroMap assets, NB_bridge_<id> object names, MacroMapBakeTests fixtures and the
  auto_<n> citations in MACRO_SIM_SPEC/MAP_IMPORT_SPEC need updating. Region ids are unchanged. Ids are now
  stable across spec edits, so scenarios may reference crossings by id.`
- source: `mapgen IDENTITY_SPEC.md`

## 13. Python 3.8 constraints

No `match`, no `X | Y` unions, no builtin generics at runtime (`List[...]`, `Dict[...]` from `typing`), no
`str.removeprefix`, no walrus operator. `int.from_bytes`, `hashlib.sha256` and `str.encode` are fine.

## 14. Acceptance checks

1. `python -m pytest tests/test_validation.py tests/test_identity.py tests/test_cli_exit_codes.py
   tests/test_diff.py tests/test_diff_cli.py tests/test_regress_helpers.py` passes.
2. `python -m mapgen validate` passes on all eight migrated specs; each unmigrated spec (from the
   pre-migration commit) fails, its first error naming `format_version`.
3. Review check (section 15, wave C) passes: on all eight maps only the files of the 9.2 table differ, with
   only the listed JSON patterns, and the id substitution reproduces `report.json` / `report.md`.
4. Backlog verify clause: regression suite green after the owner-approved re-baseline: on all eight maps the
   heightmap, every raster and the analysis outputs are byte-identical to their baselines; `meta.json` and
   `spec.resolved.json` echo the spec, and the JSON files carrying feature or crossing ids differ only in
   id values; these files are re-baselined after owner approval of the diff; inserting a region at the front
   of thornwald's regions leaves every other feature unchanged per `test_insert_region_is_local`; a spec
   without ids or format version fails validation with a message naming the key.
5. Determinism: two builds of migrated thornwald are byte-identical.
6. Documentation of section 11 is in the same commit as the code.

## 15. Implementation waves

Contrast with the pre-package behaviour is allowed in this section only. Nothing is committed until wave D.

### Wave 0 (coordinator)

- Working tree clean on `main`; `python -m pytest` green. Record `PRE = git rev-parse HEAD`.

### Wave A (one agent): code

Sections 7.1–7.6. Transition edits this implies: delete `Region.seed_key` and `geometry._key` (all
callers use `region_key`), the `seen_ids` duplicate and reserved-base checks in the POI section (the
namespace pass covers them), the positional id fallbacks (`or "region_%d"`, `or "line_%d"`,
`or "crossing_%d"`, `or "#%d"`, `"(%d, %d)"`), `id="auto_%d"` and `id="scatter%d"`; replace
`seed + ln.index` / `5000 + ln.index` / `97 * sc.index` / `seed_key=i` / `seed_key=1000 + sc.index` by
the feature-seed expressions. Then: every unmigrated spec fails `validate` with the `format_version` error first;
`python -m mapgen --version` prints `mapgen 0.2.1 (spec format versions: 1)`. Do not touch `maps/`,
`tests/`, docs.

### Wave B (parallel, after A)

- **B1 migration:** write `out/identity-review/migrate.py` (section 8), migrate the eight specs in place
  with `--rename crossings:2:eyrie_pass` for kingsjaw, run its self-check, `validate` all eight, confirm
  `git diff --stat maps/` shows only those eight files and `git diff` only insertions (plus the one rename).
- **B2 tests:** section 10 (`tests/test_validation.py` edits: `BASE` and `ALLOWED`, `missing_teams` and
  `one_team` texts gain `format_version: 1`, region/line/crossing cases gain `id`, `duplicate_poi_id` and
  `poi_reserved_base_id` change as in 10.1; `tests/test_identity.py`; `test_version`). Run them after B1.
- **B3 docs:** section 11.

### Wave C (one agent, after B): review material, then STOP

1. Pre-migration source: `mkdir -p out/identity-review/pre-src && git archive PRE mapgen maps | tar -x -C out/identity-review/pre-src`.
2. Pre builds: from `out/identity-review/pre-src`, for each of the eight specs
   `../../../.venv/Scripts/python -m mapgen build <spec> --out ../pre`; confirm with `tests/regress.py`
   (`hash_tree` + `load_baseline` + `compare`) that every pre build equals its baseline.
3. Post builds: from the repo root, `python -m mapgen build <spec> --out out/identity-review/post` for the
   eight migrated specs (empty directory first).
4. Reports: for each map `<name>`:
   `python -m mapgen diff --text out/identity-review/pre/<name> out/identity-review/post/<name> > out/identity-review/diff/<name>.txt`
   and the same without `--text` into `out/identity-review/diff/<name>.json`.
5. Review check script `out/identity-review/check_review.py`: per map, `mapgen.diff.diff_builds(pre, post,
   100000)`; fail unless no file is added or removed, every changed file is one of `export/meta.json`
   (patterns ⊆ {`/spec_sha256`}), `export/spec.resolved.json` (⊆ {`/format_version`, `/regions/*/seed`,
   `/lines/*/seed`, `/crossings/*/id`}), `export/crossings.json` (⊆ {`/*/id`}), `report.json`
   (⊆ {`/auto_crossings/*/id`, `/chokepoints/*/near`}), `report.md`; and unless the pre `report.json` and
   `report.md`, after substituting (a) each changed automatic crossing id token (regex
   `(?<![\w-])<old>(?![\w-])`) by its post id, matched by index in `crossings.json`, (b) for each changed
   explicit crossing id the string `<type> <old>` by `<type> <new>`, (c) for every crossing the string
   `<type> (<int x>, <int y>)` (from pre `spec.resolved.json` `crossings[i].at`) by `<type> <post id>`,
   equal the post files byte for byte. Expected output: 7 maps `OK (5 files changed)`, three_team
   `OK (2 files changed)`.
6. `python -m pytest`: everything passes except `test_build_matches_baseline`, which fails on all eight maps
   listing exactly the 9.2 files.
7. **STOP.** Do not run `tests/rebaseline.py`. Do not commit. Report the paths of `out/identity-review/diff/`
   and the check output to the coordinator, who shows the eight diff reports to the owner together with
   decisions D1–D9.

### Wave D (coordinator, only after the owner approves the diffs)

1. `python tests/rebaseline.py --all`; then `python -m pytest` fully green.
2. One commit: code, migrated specs, tests, re-baselined `tests/baselines/*.json`, docs (section 11).
   The parent-tier skill edit lands in the parent tier (no git there).
3. File the Battlehounds follow-up item of section 12.
