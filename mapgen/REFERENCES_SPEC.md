# Feature references in the spec - implementation spec

This file is the complete brief for roadmap package 4, "Feature references in the spec" (`docs/roadmap.md`,
backlog item `mapgen-feature-references-in-the-spec`). An agent can execute any wave in section 14 by reading only
this file and the repo files it names. Repo root: `E:\GameDev\Unity\tools\mapgen` (paths below are relative to it;
every command runs from it). Interpreter: `.venv/Scripts/python` (Python 3.8). `PRE` is commit `d348b5c`.

## 1. Purpose

A map spec states each shared position once. Where a road ends at a spawn base, a trail passes a POI or several
roads meet at a junction, the features name that position instead of repeating its coordinates:

- a **point reference** is a bare name written where a point `[x, y]` is expected. It names a POI (its `id`), a
  spawn base (`base_<team>`) or an entry of the `points` section;
- the **`points` section** is a top-level mapping of names to coordinates for shared positions that are neither POIs
  nor spawn bases, such as road junctions;
- a reference resolves to the target's position when the spec loads. Moving a POI, a spawn base or a `points` entry
  moves every feature that refers to it;
- a reference to an unknown name is a validation error.

The eight committed specs are migrated so that every heightmap, raster, render and analysis output stays
byte-identical; the only output difference is `export/meta.json` `/spec_sha256` (section 9.3).

## 2. Decisions (owner-visible)

The roadmap fixes the design; these are the details this spec decides.

| # | decision | reason | section |
|---|---|---|---|
| R1 | **Syntax:** a reference is a YAML string (normally a bare plain scalar, e.g. `path: [base_B, [2150, 2300], lea_crossroads]`) in a place where a point is accepted. **Accepted places:** region `at` (blob), region `points` (poly), region `path` (band), line `path`, explicit crossing `at` and crossing `path`. | Crossings are features that sit on a line or POI by design: the migration's shared points include crossings (kingsjaw `gate` with `kings_road` and `region_11`; all three crossings of `example_river_crossing`; kingsjaw `needle`/`eyrie_pass` pass through POIs `needle_cap`/`eyrie`). Without crossings, moving those points would leave the crossing behind. | 3.2 |
| R2 | **Excluded places** (a string there is an error naming the accepted places): `label_at` (region, line), POI `at`, team `base`, `points` values, scatter `within`. `join` is unchanged (it names a line id). | POIs, bases and `points` entries are the targets and hold literal coordinates (no chains, R5). `label_at` is a render-only label pin. Scatter `within` polygons are shared whole between scatter entries (kingsjaw, saltreach_delta), which a per-vertex name does not express; they are a candidate for a later named-area feature. | 3.2, 3.4 |
| R3 | **`points` is a mapping** `name: [x, y]`, written after `teams`. **Its names join the single id namespace** (charset `[a-z][a-z0-9_]*`, at most 64 characters, distinct from every base, region, line, scatter, crossing and POI id). | One namespace makes every reference unambiguous without knowing its kind, matching `IDENTITY_SPEC.md` 3.2. A mapping keeps a name and its position on one line. | 3.1 |
| R4 | A reference to a region, line, scatter entry or crossing id is an error that says what kind of id it is; any other unknown string is an "unknown reference" error. Messages in 3.4. | The author learns why the name does not resolve. | 3.4 |
| R5 | **Resolution:** a reference takes the target's parsed position, the same `(float, float)` tuple a literal of the same numbers gives, so output is byte-identical. **No chains:** a `points` value, a POI `at` and a team `base` are literal points. | No observed need for chains; literal targets need no cycle detection and resolve in one pass. | 4 |
| R6 | **`spec.resolved.json` holds the resolved coordinates only; `points` is not echoed.** The file is byte-identical for every migrated spec (measured). | Every existing consumer keeps working: the file's shape and values do not change. Battlehounds reads only `brief` and `teams[].{id, name, color, base}` from it (`MapBundleReader.cs:179,273`). Named points carry no gameplay meaning. Alternatives in 5. | 5 |
| R7 | **`format_version` stays `1`.** Rule: the format version changes when a spec that is valid under the current version becomes invalid or builds differently; an addition after which every valid spec stays valid with identical output keeps the version. | Every valid version-1 spec stays valid and builds byte-identically (the migration input, measured). | 3.5 |
| R8 | `mapgen/__init__.py` `__version__` stays `0.2.1`. | Battlehounds' importer accepts only mapgen `0.2.*` (`IDENTITY_SPEC.md` D8); the bundle content is unchanged. | - |
| R9 | **Migration names** of the 22 `points` entries (section 9.1) are proposals for owner confirmation. Renaming one later is a text edit of the entry and its references and changes no output. | Names are an authoring choice. | 9.1 |
| R10 | **Near-coincident points** (distinct literals within 30 m that look like intended coincidences, 20 pairs) are listed in 9.2 and **not merged**. | Merging moves geometry and changes output; it is a later authoring pass. | 9.2 |

## 3. Spec format rules

### 3.1 `points`

```yaml
points:
  lea_crossroads: [2366, 1906]   # kings_road, b_track, lea_path, lodge_path
  west_fork: [1138, 904]
```

- Top-level key `points`, optional. Its value is a mapping (YAML `null` or an absent key means no entries).
- Each key is a name matching `[a-z][a-z0-9_]*` in full, at most 64 characters (the id rule of `IDENTITY_SPEC.md`
  3.2). Each value is a literal point `[x, y]` of two numbers inside the map (the bounds rule of every point,
  tolerance 1 m).
- Names are ids: they share the one namespace with bases (`base_<team>`), regions, lines, scatter entries, crossings
  and POIs. The namespace pass visits, in this order: bases, `points` (mapping order), `regions`, `lines`, `scatter`,
  `crossings`, `pois`; the first holder of an id is the one named in a duplicate message.
- An entry that no feature references is valid.
- In the top-level key list `points` stands directly after `teams`:
  `format_version, name, brief, size, seed, terrain, teams, points, regions, lines, crossings, pois, scatter,
  analysis, output, auto_crossings` (this is the text of the unknown-key message).

### 3.2 Point references

| place | literal | reference |
|---|---|---|
| `regions[i].at` (blob) | yes | yes |
| `regions[i].points[j]` (poly) | yes | yes |
| `regions[i].path[j]` (band) | yes | yes |
| `lines[i].path[j]` | yes | yes |
| `crossings[i].at` | yes | yes |
| `crossings[i].path[j]` | yes | yes |
| `teams.<t>.base`, `pois[i].at`, `points.<name>` | yes | no (these are the targets) |
| `regions[i].label_at`, `lines[i].label_at`, `scatter[i].within[j]` | yes | no |

- A reference is any YAML string in an accepted place. It resolves if it equals a target name:
  - `base_<team id>` for every key of `teams` (team ids keep their own rules, so `base_A` is a valid reference even
    though `A` is upper case);
  - every `points` name;
  - every POI `id`.
- A reference may be quoted (`'lea_crossroads'`); quoting is needed only for a name YAML reads as another type.
  With mapgen's YAML loader that applies to the names `null` and `true`/`false` (case variants included): unquoted they
  load as null or a boolean and are reported as a malformed point.
- Every place that takes a reference also takes a literal; a path may mix both.

### 3.3 Resolution

At load time each reference resolves to the target's position: the tuple `(float(x), float(y))` that parsing
the target's literal produced. The loaded `MapSpec` contains coordinates only; nothing after `schema.py` sees a
reference. A target whose own position is invalid (e.g. a POI outside the map) is reported once, at the target;
the referencing point resolves to nothing and adds no second message (the spec is invalid anyway).

### 3.4 Validation messages

Every message starts with the key path, in the style of the existing messages. `<x>` is Python `%r` of the
offending value. `<T>` is the fixed text `a point reference names a POI, a spawn base or a `points` entry`
(with the backticks). `<P>` is the fixed text
`point references are accepted only in region at/points/path, line path and crossing at/path`.

| situation | message |
|---|---|
| reference to an unknown name | `lines[0].path[1]: unknown reference 'base_c' (<T>)` |
| reference to a region id | `lines[0].path[1]: 'wood' is a region id; <T>` |
| ... a line id | `lines[0].path[1]: 'road' is a line id; <T>` |
| ... a scatter id | `lines[0].path[1]: 's0' is a scatter entry id; <T>` |
| ... a crossing id | `lines[0].path[1]: 'ford1' is a crossing id; <T>` |
| a string in an excluded place (POI `at`, team `base`, `label_at`, scatter `within`, a `points` value) | `pois[0].at: expected a point [x, y] of two numbers, got 'j' (<P>)` |
| `points` not a mapping | `points: expected a mapping of name -> [x, y], got list` (`type(v).__name__`) |
| `points` name not an id | `points.Junction: name must match [a-z][a-z0-9_]* and be at most 64 characters (got 'Junction')` |
| `points` value not a point / outside the map | the generic point messages with path `points.<name>`, e.g. `points.j: point [1500, 500] is outside the map [0..1000.0, 0..1000.0]` |
| a mapping key repeated within one mapping (any mapping of the spec) | `<yaml>: line 10: duplicate key 'j'` (the line of the repeated key; reported as a single YAML error) |
| `points` name used again | `pois[0].id: duplicate id 'j' (first used at points.j)`; a `points` name equal to a base id: `points.base_a: 'base_a' is reserved for the implicit base of team a` |

A non-string, non-point value in an accepted place keeps the generic message
`expected a point [x, y] of two numbers, got <x>` (e.g. an unquoted `null` gives `got None`).

Kind texts: `region`, `line`, `scatter entry`, `crossing`. An id held by both a position target and a feature
without a position (itself a duplicate-id error) resolves to the position target.

### 3.5 Format version

`format_version: 1`. `SPEC_FORMAT_VERSIONS` stays `(1,)`. The rule (R7) is stated in `MAPGEN_SPEC.md` 4.8 so later
packages apply it.

## 4. Resolution semantics (implementation contract)

1. `teams` is parsed (existing code).
2. `points` is parsed: each valid entry is checked with `_V.point` (literal only) and stored in `named` (dict,
   mapping order).
3. The reference table is built from the raw spec, without reporting errors:
   - targets: `base_<tid>` for each raw team key, each valid `points` name, each POI with a valid `id`, mapped to the
     position a literal parse of its raw value gives, or `None` when that literal is invalid. First holder wins
     (bases, points, POIs);
   - kinds: each valid id of `regions`, `lines`, `scatter`, `crossings` that is not a target, mapped to its kind text.
4. Regions, lines and crossings parse their accepted places with `ref=True`: a string goes through
   `_V.resolve(name, path)` (section 7.1); other values take the literal path.
5. POI parsing, scatter, the namespace pass and everything after are unchanged except that the namespace pass
   includes `points` names (section 3.1).

## 5. Export echo

`export/spec.resolved.json` is written by `export.write_resolved` from the loaded `MapSpec`, which holds resolved
coordinates. **No code change in `export.py`.** The file is byte-identical for all eight migrated specs (measured).

Options considered (JSON paths that change, for a spec with references):

| option | changed JSON paths | consumers |
|---|---|---|
| A (chosen) coordinates only, `points` not echoed | none | unchanged |
| B coordinates + `points` echoed as `{name: [x, y]}` after `teams` | `/points` added on every map (also maps without entries: `{}`) | additive; nothing reads it |
| C coordinates + the reference per point | e.g. `/lines/*/path_refs`, `/regions/*/at_ref`, `/regions/*/points_refs`, `/regions/*/path_refs`, `/crossings/*/at_ref`, `/crossings/*/path_refs` added, or every point becomes an object (breaks `[x, y]` readers) | additive or breaking |

Battlehounds (read-only check, `E:\GameDev\Unity\battlehounds\Assets\_Project\Editor\MapImport\Core\Bundle\MapBundleReader.cs`)
reads `spec.resolved.json` into `SpecDto { brief; teams }` with `TeamDto { id, name, color, base }` (lines 179-180,
273-280) and reads no point list from it. Option A needs no consumer follow-up.

## 6. Python 3.8 constraints

No `match`, no `X | Y` unions, no builtin generics at runtime (`list[str]`, `dict[...]`), no `str.removeprefix`, no
walrus operator. `schema.py` has `from __future__ import annotations`, so `Dict[...]`/`Tuple[...]` annotations are
fine. `re.fullmatch`, `dict` insertion order and `yaml.compose` marks are fine.

## 7. Code changes

### 7.1 `mapgen/schema.py` (LF)

The prototype (section 8) implemented exactly this; the code below is its tested form.

1. `MapSpec`: add the last field
   `points: Dict[str, Point] = field(default_factory=dict)  # named coordinates of the `points` section, spec order`
   and pass `points=named` in the `MapSpec(...)` call at the end of `_validate`.
2. Module constants directly above `class _V`:
   ```python
   REF_TARGETS = "a point reference names a POI, a spawn base or a `points` entry"
   REF_PLACES = "point references are accepted only in region at/points/path, line path and crossing at/path"
   ```
3. `_V.__init__`: add
   ```python
   self.refs: Dict[str, Optional[Point]] = {}  # reference name -> position (None: target's own position is invalid)
   self.ref_kinds: Dict[str, str] = {}         # id of a feature without a position -> its kind
   ```
4. `_V.point(self, v, path, size, ref: bool = False)`: at the top
   ```python
   if ref and isinstance(v, str):
       return self.resolve(v, path)
   if isinstance(v, str):
       self.err(path, "expected a point [x, y] of two numbers, got %r (%s)" % (v, REF_PLACES))
       return None
   ```
   then the existing body.
5. Add `_V.resolve`:
   ```python
   def resolve(self, name: str, path: str) -> Optional[Point]:
       """Position of the POI, spawn base or `points` entry `name`; None (and an error) if there is none."""
       if name in self.refs:
           return self.refs[name]
       kind = self.ref_kinds.get(name)
       if kind is not None:
           self.err(path, "'%s' is a %s id; %s" % (name, kind, REF_TARGETS))
       else:
           self.err(path, "unknown reference '%s' (%s)" % (name, REF_TARGETS))
       return None
   ```
6. `_V.points(..., what: str, ref: bool = False)` passes `ref` to each `self.point(item, ..., size, ref)`.
7. Module level, before `_validate`:
   ```python
   _NO_POSITION = (("regions", "region"), ("lines", "line"), ("scatter", "scatter entry"), ("crossings", "crossing"))


   def _valid_id(x: Any) -> bool:
       return isinstance(x, str) and bool(_ID.fullmatch(x)) and len(x) <= ID_MAX_LEN


   def _reference_table(raw: Dict[str, Any], size: Tuple[float, float]) -> Tuple[Dict[str, Optional[Point]], Dict[str, str]]:
       """Point-reference targets read from the raw spec: spawn bases, `points` entries and POIs, each mapped to its
       position (None when that position is itself invalid; its own error is reported where it is parsed), plus the
       ids of features that have no position, mapped to their kind. Targets are literal positions only: no chains."""
       quiet = _V()
       refs: Dict[str, Optional[Point]] = {}
       traw = raw.get("teams")
       if isinstance(traw, dict):
           for tid, tv in traw.items():
               refs.setdefault("base_" + str(tid), quiet.point(tv.get("base"), "", size) if isinstance(tv, dict) else None)
       praw = raw.get("points")
       if isinstance(praw, dict):
           for name, pv in praw.items():
               if _valid_id(name):
                   refs.setdefault(name, quiet.point(pv, "", size))
       poraw = raw.get("pois")
       if isinstance(poraw, list):
           for it in poraw:
               if isinstance(it, dict) and _valid_id(it.get("id")):
                   refs.setdefault(it["id"], quiet.point(it.get("at"), "", size))
       kinds: Dict[str, str] = {}
       for sect, kind in _NO_POSITION:
           items = raw.get(sect)
           if isinstance(items, list):
               for it in items:
                   if isinstance(it, dict) and _valid_id(it.get("id")) and it["id"] not in refs:
                       kinds.setdefault(it["id"], kind)
       return refs, kinds
   ```
   `_ident` and the namespace pass may use `_valid_id` for their charset test (same behaviour).
8. `_validate`: add `"points"` after `"teams"` in `top`. Between the `# teams` block and `# regions`:
   ```python
   # points: named coordinates, the targets of point references besides POIs and spawn bases
   named: Dict[str, Point] = {}
   ptraw = raw.get("points")
   if ptraw is not None:
       if not isinstance(ptraw, dict):
           v.err("points", "expected a mapping of name -> [x, y], got %s" % type(ptraw).__name__)
       else:
           for pname, pv in ptraw.items():
               pp = "points.%s" % pname
               if not _valid_id(pname):
                   v.err(pp, "name must match [a-z][a-z0-9_]* and be at most %d characters (got %r)" % (ID_MAX_LEN, pname))
                   continue
               pt = v.point(pv, pp, size)
               if pt is not None:
                   named[pname] = pt
   v.refs, v.ref_kinds = _reference_table(raw, size)
   ```
9. Pass `ref=True` in exactly these six calls: region `at` (`v.point(r["at"], p + ".at", size, ref=True)`), region
   `points` and `path` (`v.points(..., ref=True)`), line `path`, crossing `path`, crossing `at`. All other
   `v.point`/`v.points` calls (team base, `label_at`, POI `at`, scatter `within`, `points` values) stay without it.
10. Namespace pass: comment `# one id namespace over bases, points, regions, lines, scatter entries, crossings and
    POIs`; build the visit list with `points` names first (path `points.<name>`), then the sections as today:
    ```python
    holders: List[Tuple[str, Any]] = [("points.%s" % k, k) for k in (ptraw if isinstance(ptraw, dict) else {})]
    for sect in ("regions", "lines", "scatter", "crossings", "pois"):
        items = raw.get(sect)
        if isinstance(items, list):
            holders += [("%s[%d].id" % (sect, i), it.get("id") if isinstance(it, dict) else None)
                        for i, it in enumerate(items)]
    for p, fid in holders:
        if not _valid_id(fid):
            continue
        # existing body: first-holder record, reserved-base message, duplicate message
    ```

### 7.2 Other modules

No change in `export.py`, `geometry.py`, `height.py`, `analysis.py`, `render.py`, `build.py`, `diff.py`,
`__main__.py` or `__init__.py`. `probe <poi id>` keeps taking POI ids and coordinates only.

## 8. Prototype (measured, 2026-11-14)

Scratch copy of `PRE`'s `mapgen/`, `maps/` and `tests/` at
`C:\Users\thewa\AppData\Local\Temp\claude\1a0ae9e118628337\08238eb1-06ce-46bb-b56f-e251c09dd869\scratchpad\refs\`
(`proto/`, patch script `patch_schema.py`, migration `migrate.py`, builds `pre/`, `post2/`, diffs `diff/`, move-base
demo `demo/`, validation cases `val_cases.py` / `val_cases_out.txt`, notes `NOTES.md`). Results:

- All eight migrated specs validate; `load_spec` gives point fields (regions `at`/`points`/`path`/`label_at`, lines
  `path`/`label_at`, crossings `at`/`path`, POIs incl. bases, scatter `within`) equal to the pre-migration spec's.
- Builds of `PRE` equal the committed baselines on all eight maps; builds of the migrated specs differ from the
  baselines only in `export/meta.json` (table 9.3).
- Full `pytest` with `ALLOWED` updated: 195 passed, 8 failed (only `test_build_matches_baseline[*]`, each naming
  only `export/meta.json`).
- Move-the-base (migrated thornwald, `teams.B.base` `[2050, 2560]` -> `[2100, 2600]`): `export/lines.json` `b_track`
  and `abbey_path` start at `[2100.0, 2600.0, 82.6]` (were `[2050.0, 2560.0, 71.88]`, order `[x, z, y]`);
  `mapgen diff` shows `export/spec.resolved.json` patterns `/lines/*/path/*/*` x4, `/pois/*/at/*` x2,
  `/teams/*/base/*` x2 (two lines x two coordinates, base POI, team base).
- Unknown reference (`b_track` `base_B` -> `base_b`): `mapgen validate` exits 1 with
  `lines[2].path[0]: unknown reference 'base_b' (a point reference names a POI, a spawn base or a `points` entry)`.

## 9. Migration

### 9.1 Algorithm and per-spec edits

The migration tool is `out/references-review/migrate.py` (full text in Appendix A; run
`.venv/Scripts/python out/references-review/migrate.py . <spec>...`, `--check` prints the plan without writing). It
works on text: only the character spans of migrated point literals change (located via `yaml.compose` node marks),
plus one inserted `points:` block; comments, key order, flow style, spacing and LF line endings are preserved.

1. **Anchors.** Every team `base` (as `base_<team>`) and every POI `at` that is a literal point, keyed by
   `(float(x), float(y))`; bases first, then POIs in list order; the first holder of a position wins (no two anchors
   share a position in the eight specs).
2. **Places.** Every literal point in an accepted place (3.2), in file order: regions `at`/`points`/`path`, lines
   `path`, crossings `at`/`path`.
3. **References to anchors.** A literal whose `(float(x), float(y))` equals an anchor position becomes that anchor's
   id (exact equality, no tolerance).
4. **Shared points.** The remaining literals are grouped by exact position. A group whose points lie in two or more
   distinct features (a feature is one list item of `regions`, `lines` or `crossings`) becomes a `points` entry; each
   of its literals becomes the entry's name. A position repeated only inside one feature stays literal.
5. **Names** come from the `NAMES` table (R9); a shared point without a name stops the tool with an error.
6. **`points` block.** Inserted after the line holding the last `teams` entry, as a blank line, `points:` and one
   line per entry in order of first occurrence: `  <name>: [x, y]` padded to a common width, two spaces,
   `# <ids of the features that meet there>`. The existing blank line before the next section stays.

Edits (`->` is the reference written in place of the literal):

**example_natural** (7 references, 1 entry): `points.river_road_meet: [1250, 1300]` (`line_0`, `line_1`).
`line_0.path[3]` -> `river_road_meet`; `line_1.path`: `[0]` -> `base_A`, `[2]` -> `river_road_meet`, `[3]` -> `cap_e`,
`[5]` -> `base_B`; `line_2.path`: `[0]` -> `base_B`, `[5]` -> `base_A`.

**example_river_crossing** (10 references, 3 entries): `ford_site: [1480, 700]` (`line_0`, `crossing_1`),
`bridge_site: [1530, 1500]` (`line_0`, `crossing_0`), `pass_site: [1500, 2350]` (`line_2`, `crossing_2`).
`line_0.path`: `[1]` -> `ford_site`, `[2]` -> `bridge_site`; `line_1.path`: `[0]` -> `base_A`, `[4]` -> `base_B`;
`line_2.path`: `[0]` -> `base_A`, `[2]` -> `pass_site`, `[4]` -> `base_B`; `crossing_0.at` -> `bridge_site`;
`crossing_1.at` -> `ford_site`; `crossing_2.at` -> `pass_site`.

**example_three_team** (15 references, no entries): `line_0.path` `[0]` `base_A`, `[1]` `fwd_north`, `[2]`
`island_keep`; `line_1.path` `[0]` `base_B`, `[1]` `fwd_sw`, `[2]` `island_keep`; `line_2.path` `[0]` `base_C`, `[1]`
`fwd_se`, `[2]` `island_keep`; `line_3.path` `[0]` `base_A`, `[3]` `base_C`; `line_4.path` `[0]` `base_B`, `[3]`
`base_A`; `line_5.path` `[0]` `base_C`, `[3]` `base_B`.

**ashen_steppe** (20 references, 4 entries): `north_fork: [3000, 2000]` (`imperial_road`, `north_road`),
`west_merge_north: [1000, 2100]` and `west_merge_south: [700, 1800]` (`north_road`, `steppe_track_w`),
`bend_fork: [2000, 450]` (`steppe_track_e`, `bend_track`). `region_5.at` -> `hillfort`; `region_7.at` -> `kurgan`;
`region_10.at` -> `bandits`; `region_11.at` -> `wolves`; `imperial_road.path` `[0]` `base_B`, `[2]` `north_fork`, `[7]`
`hillfort`; `old_road.path` `[0]` `hillfort`, `[6]` `base_A`; `north_road.path` `[0]` `north_fork`, `[6]`
`west_merge_north`, `[7]` `west_merge_south`; `steppe_track_w.path` `[0]` `base_A`, `[3]` `west_merge_south`, `[4]`
`west_merge_north`; `steppe_track_e.path` `[0]` `base_A`, `[2]` `bend_fork`, `[6]` `base_B`; `bend_track.path` `[0]`
`bend_fork`, `[3]` `hillfort`.

**gloamfen** (10 references, 2 entries): `causeway_meet: [1900, 1550]` (`west_causeway`, `north_causeway`),
`north_path_join: [2150, 2400]` (`north_causeway`, `north_path`). `region_17.at` -> `abbey`; `west_causeway.path`
`[0]` `base_A`, `[6]` `causeway_meet`; `north_causeway.path` `[0]` `base_B`, `[5]` `north_path_join`, `[9]`
`causeway_meet`; `south_path.path[0]` `base_A`; `east_path.path[0]` `base_B`; `north_path.path` `[0]` `base_A`, `[6]`
`north_path_join`.

**kingsjaw** (21 references, 3 entries): `gate_site: [1450, 680]` (`region_11`, `kings_road`, `gate`),
`farm_lane_west: [740, 1431]` and `farm_lane_east: [1200, 900]` (`kings_road`, `farm_lane`). `region_11.at` ->
`gate_site`; `region_12.at` -> `eyrie`; `region_14.path` `[4]` `needle_cap`, `[9]` `eyrie`; `region_16.at` -> `hermit`;
`kings_road.path` `[0]` `base_A`, `[3]` `farm_lane_west`, `[6]` `farm_lane_east`, `[7]` `gate_site`, `[13]` `base_B`;
`needle_trail.path` `[0]` `base_A`, `[6]` `needle_cap`, `[11]` `base_B`; `eyrie_trail.path` `[0]` `base_A`, `[7]`
`eyrie`, `[13]` `base_B`; `farm_lane.path` `[0]` `farm_lane_west`, `[4]` `farm_lane_east`; `gate.at` -> `gate_site`;
`needle.path[1]` -> `needle_cap`; `eyrie_pass.path[1]` -> `eyrie`.

**saltreach_delta** (25 references, 5 entries): `delta_fork: [2100, 2050]` (`trunk`, `arm_w`, `arm_e`),
`bluff_crossroads: [1250, 2400]` (`high_road`, `causeway`, `stair_path`), `coast_fen_junction: [1350, 520]`
(`coast_road`, `fen_s`), `wold_goat_junction: [1990, 2700]` (`wold_trail`, `goat_stair`),
`stair_goat_junction: [1600, 2560]` (`stair_path`, `goat_stair`). `region_14.at` -> `saltkeep`; `trunk.path[4]`,
`arm_w.path[0]`, `arm_e.path[0]` -> `delta_fork`; `high_road.path` `[0]` `base_A`, `[2]` `bluff_crossroads`, `[8]`
`base_B`; `causeway.path` `[0]` `bluff_crossroads`, `[5]` `saltkeep`; `coast_road.path` `[0]` `base_A`, `[7]`
`coast_fen_junction`; `fen_n.path` `[0]` `base_B`, `[5]` `saltkeep`; `fen_s.path` `[0]` `base_B`, `[9]`
`coast_fen_junction`; `wold_trail.path` `[0]` `base_B`, `[6]` `wold_goat_junction`; `stair_path.path` `[0]`
`stair_goat_junction`, `[2]` `bluff_crossroads`; `goat_stair.path` `[0]` `wold_goat_junction`, `[4]`
`stair_goat_junction`; `reed_path.path[0]` `saltkeep`; `isle_track.path` `[0]` `saltkeep`, `[2]` `heron_mound`, `[4]`
`shrine`.

**thornwald** (20 references, 4 entries): `lodge_glade: [2050, 1250]` (`region_5`, `lodge_path`),
`abbey_junction: [658, 718]` (`kings_road`, `abbey_path`), `west_fork: [1138, 904]` (`kings_road`, `lea_path`,
`lodge_path`), `lea_crossroads: [2366, 1906]` (`kings_road`, `b_track`, `lea_path`, `lodge_path`). `region_4.at` ->
`lea_keep`; `region_5.at` -> `lodge_glade`; `region_9.at` -> `abbey_camp`; `region_10.at` -> `barrow`; `region_11.at`
-> `thornhold`; `kings_road.path` `[3]` `abbey_junction`, `[5]` `west_fork`, `[11]` `lea_crossroads`; `b_track.path`
`[0]` `base_B`, `[3]` `lea_crossroads`; `abbey_path.path` `[0]` `base_B`, `[6]` `abbey_junction`; `lea_path.path` `[0]`
`lea_crossroads`, `[5]` `barrow`, `[9]` `west_fork`; `tarn_path.path` `[0]` `charcoal`, `[5]` `supply_b`;
`lodge_path.path` `[0]` `west_fork`, `[3]` `lodge_glade`, `[6]` `lea_crossroads`. `teams.B.base` keeps the only
literal of B's base position.

Totals: 128 references, 22 `points` entries. Migrated thornwald `points` block:

```yaml
points:
  lodge_glade: [2050, 1250]     # region_5, lodge_path
  abbey_junction: [658, 718]    # kings_road, abbey_path
  west_fork: [1138, 904]        # kings_road, lea_path, lodge_path
  lea_crossroads: [2366, 1906]  # kings_road, b_track, lea_path, lodge_path
```

and e.g. `- { id: b_track, seed: 2, type: trail, path: [base_B, [2150, 2300], [2300, 2050], lea_crossroads], width: 5 }`.

### 9.2 Near-coincident points (owner list, not merged)

Distinct literals in accepted places, or a literal and an anchor, within 30 m of each other (measured on the
pre-migration specs; scatter `within` excluded). Each may be an intended coincidence; aligning one is an authoring
edit that changes output.

| spec | a | b | distance |
|---|---|---|---|
| ashen_steppe | POI `hillfort` [1880, 1660] | `region_3.at` [1900, 1650] | 22.4 m |
| kingsjaw | base_B [2800, 1080] | `region_7.at` [2800, 1100] | 20.0 m |
| kingsjaw | POI `gate_keep` [1430, 670] | `gate_site` [1450, 680] (`region_11`, `kings_road`, `gate`) | 22.4 m |
| kingsjaw | POI `mill` [1180, 1200] | `region_8.at` [1180, 1210] | 10.0 m |
| kingsjaw | POI `supply_a` [800, 1900] | `region_9.at` [790, 1900] | 10.0 m |
| kingsjaw | `needle_trail.path[7]` [2010, 1440] | crossing `needle.path[2]` [2020, 1435] | 11.2 m |
| kingsjaw | `eyrie_trail.path[6]` [1854, 2485] | crossing `eyrie_pass.path[0]` [1830, 2485] | 24.0 m |
| saltreach_delta | `arm_w.path[1]` [1743, 1692] | `causeway.path[3]` [1750, 1700] | 10.6 m |
| saltreach_delta | `goat_stair.path[2]` [1780, 2790] | crossing `goat_ramp.path[0]` [1800, 2780] | 22.4 m |
| saltreach_delta | `goat_stair.path[3]` [1680, 2700] | crossing `goat_ramp.path[1]` [1700, 2720] | 28.3 m |
| thornwald | base_A [430, 470] | `kings_road.path[2]` [450, 480] | 22.4 m |
| thornwald | POI `lodge` [2050, 1230] | `lodge_glade` [2050, 1250] (`region_5`, `lodge_path`) | 20.0 m |
| thornwald | POI `charcoal` [520, 2420] | `region_6.at` [540, 2400] | 28.3 m |
| thornwald | POI `supply_a` [700, 650] | `region_7.at` [720, 660] | 22.4 m |
| thornwald | POI `great_oak` [1300, 2550] | `region_8.at` [1290, 2560] | 14.1 m |
| thornwald | `region_1.points[0]` [250, 350] | `kings_road.path[1]` [238, 352] | 12.2 m |
| example_natural | `line_2.path[2]` [1700, 2350] | `crossing_2.at` [1720, 2350] | 20.0 m |
| example_river_crossing | `bridge_site` [1530, 1500] (`line_0`, `crossing_0`) | `line_1.path[2]` [1500, 1500] | 30.0 m |
| example_three_team | base_B [504, 925] | `region_13.points[0]` [523, 915] | 21.5 m |
| example_three_team | base_C [2496, 925] | `region_14.points[1]` [2477, 915] | 21.5 m |

Also for a later pass: scatter `within` polygons repeated whole by two scatter entries (kingsjaw `scatter0`/`scatter1`
7 vertices, `scatter2`/`scatter3` 7 vertices; saltreach_delta `scatter0`/`scatter1` 3 vertices, `scatter3`/`scatter4`
2 vertices).

### 9.3 Measured result (prototype, all eight maps)

Migrated builds compared with the committed baselines (`tests/regress.py` `hash_tree` + `load_baseline` + `compare`)
and with `PRE` builds via `mapgen diff`:

| map | files changed / unchanged | changed file | JSON patterns | heightmap, rasters, objectives |
|---|---|---|---|---|
| natural | 1 / 33 | `export/meta.json` | `/spec_sha256` x1 | 0 texels, 0 rasters, 0 objectives changed |
| river_crossing | 1 / 30 | `export/meta.json` | `/spec_sha256` x1 | same |
| three_team | 1 / 27 | `export/meta.json` | `/spec_sha256` x1 | same |
| ashen_steppe | 1 / 32 | `export/meta.json` | `/spec_sha256` x1 | same |
| gloamfen | 1 / 32 | `export/meta.json` | `/spec_sha256` x1 | same |
| kingsjaw | 1 / 32 | `export/meta.json` | `/spec_sha256` x1 | same |
| saltreach_delta | 1 / 34 | `export/meta.json` | `/spec_sha256` x1 | same |
| thornwald | 1 / 32 | `export/meta.json` | `/spec_sha256` x1 | same |

`export/spec.resolved.json`, `report.json`, `report.md` and every render are byte-identical. The allowed set for the
wave C check is exactly: changed files subset of {`export/meta.json`}, its patterns subset of {`/spec_sha256`}, no
file added or removed.

## 10. Tests

### 10.1 `tests/test_validation.py`

- `ALLOWED` becomes `"format_version, name, brief, size, seed, terrain, teams, points, regions, lines, crossings,
  pois, scatter, analysis, output, auto_crossings"` (keep the existing two-string concatenation layout).
- Add these cases to `CASES` (text = `BASE +` the YAML shown; `T` = `REF_TARGETS` text, `PL` = `REF_PLACES` text, as
  module constants in the test file; expected = the exact list):

| id | YAML appended to `BASE` (or edit) | expected |
|---|---|---|
| `ref_unknown` | `lines:\n  - {id: road, type: road, path: [base_A, base_c]}` | `lines[0].path[1]: unknown reference 'base_c' (T)` |
| `ref_region_id` | `regions:\n  - {id: wood, type: forest, shape: blob, at: [500, 500], r: 100}\nlines:\n  - {id: road, type: road, path: [base_A, wood]}` | `lines[0].path[1]: 'wood' is a region id; T` |
| `ref_line_id` | `lines:\n  - {id: road, type: road, path: [base_A, road]}` | `lines[0].path[1]: 'road' is a line id; T` |
| `ref_scatter_id` | `scatter:\n  - {id: s0, type: forest, coverage: 0.2}\nlines:\n  - {id: road, type: road, path: [base_A, s0]}` | `lines[0].path[1]: 's0' is a scatter entry id; T` |
| `ref_crossing_id` | `crossings:\n  - {id: ford1, type: ford, at: [500, 500]}\nlines:\n  - {id: road, type: road, path: [base_A, ford1]}` | `lines[0].path[1]: 'ford1' is a crossing id; T` |
| `ref_in_poi_at` | `points:\n  j: [500, 500]\npois:\n  - {id: c, type: capture, at: j}` | `pois[0].at: expected a point [x, y] of two numbers, got 'j' (PL)` |
| `ref_in_base` | edit `{base: [900, 900]}` -> `{base: j}`, append `points:\n  j: [500, 500]` | `teams.B.base: expected a point [x, y] of two numbers, got 'j' (PL)` |
| `ref_in_label_at` | `points:\n  j: [500, 500]\nlines:\n  - {id: road, type: road, path: [base_A, j], label_at: j}` | `lines[0].label_at: expected a point [x, y] of two numbers, got 'j' (PL)` |
| `ref_in_within` | `points:\n  j: [500, 500]\nscatter:\n  - {id: s0, type: forest, coverage: 0.2, within: [j, [0, 0], [0, 900]]}` | `scatter[0].within[0]: expected a point [x, y] of two numbers, got 'j' (PL)` |
| `points_chain` | `points:\n  j: base_A` | `points.j: expected a point [x, y] of two numbers, got 'base_A' (PL)` |
| `points_not_mapping` | `points: [[1, 2]]` | `points: expected a mapping of name -> [x, y], got list` |
| `points_bad_name` | `points:\n  Junction: [500, 500]` | `points.Junction: name must match [a-z][a-z0-9_]* and be at most 64 characters (got 'Junction')` |
| `points_outside_map` | `points:\n  j: [1500, 500]` | `points.j: point [1500, 500] is outside the map [0..1000.0, 0..1000.0]` |
| `points_duplicate_poi` | `points:\n  j: [500, 500]\npois:\n  - {id: j, type: capture, at: [400, 400]}` | `pois[0].id: duplicate id 'j' (first used at points.j)` |
| `points_duplicate_region` | `points:\n  j: [500, 500]\nregions:\n  - {id: j, type: forest, shape: blob, at: j, r: 50}` | `regions[0].id: duplicate id 'j' (first used at points.j)` |
| `points_reserved_base` | `BASE.replace("A:", "a:").replace("B:", "b:")` + `points:\n  base_a: [500, 500]` | `points.base_a: 'base_a' is reserved for the implicit base of team a` |
| `ref_to_invalid_poi` | `pois:\n  - {id: c, type: capture, at: [1500, 400]}\nlines:\n  - {id: road, type: road, path: [base_A, c]}` | `pois[0].at: point [1500, 400] is outside the map [0..1000.0, 0..1000.0]` (only message) |

All of these were run on the prototype and gave exactly these messages (`val_cases_out.txt`).

### 10.2 `tests/test_references.py` (in-process; a file this package adds)

Use `mapgen.schema.load_spec`, `mapgen.build.build(..., only="export", log=lambda s: None)` and `tests/regress.py`
`REPO_ROOT`. Write edited spec copies with `newline="\n"` into `tmp_path`.

1. `test_all_places_resolve`: `BASE +` this text loads; assert the resolved values:
   ```yaml
   points:
     j: [500, 500]
   pois:
     - {id: c, type: capture, at: [400, 600]}
   regions:
     - {id: r1, type: forest, shape: blob, at: c, r: 50}
     - {id: r2, type: field, shape: poly, points: [j, c, base_B]}
     - {id: r3, type: hills, shape: band, path: [base_A, j], width: 60}
   lines:
     - {id: road, type: road, path: [base_A, j, c, base_B]}
   crossings:
     - {id: x1, type: ford, at: j}
     - {id: x2, type: bridge, path: [j, c], width: 20}
   ```
   `spec.points == {"j": (500.0, 500.0)}`; `r1.at == (400.0, 600.0)`; `r2.points == [(500.0, 500.0), (400.0, 600.0),
   (900.0, 900.0)]`; `r3.path == [(100.0, 100.0), (500.0, 500.0)]`; road path `[(100.0, 100.0), (500.0, 500.0),
   (400.0, 600.0), (900.0, 900.0)]`; `x1.at == (500.0, 500.0)`; `x2.path == [(500.0, 500.0), (400.0, 600.0)]` and
   `x2.at == (450.0, 550.0)`. A quoted reference (`at: 'c'`) gives the same result.
2. `test_move_base_moves_references` (backlog verify clause): read `maps/concepts/thornwald.yaml`; assert
   `"B: { base: [2050, 2560]"` occurs once and `"[2050, 2560]"` occurs once in the file; replace it by
   `"B: { base: [2100, 2600]"`; build with `only="export"`; in `export/lines.json` the entries `b_track` and
   `abbey_path` have `points[0][:2] == [2100.0, 2600.0]`; in `export/spec.resolved.json` `lines[*].path[0]` of both is
   `[2100.0, 2600.0]`. One build, module-scoped fixture.
3. `test_move_named_point`: replace `lea_crossroads: [2366, 1906]` by `lea_crossroads: [2400, 1950]` in thornwald
   (assert one occurrence); `load_spec` only. `kings_road.path[11]`, `b_track.path[3]`, `lea_path.path[0]`,
   `lodge_path.path[6]` all equal `(2400.0, 1950.0)`; every other point of every line, region and crossing equals the
   unedited spec's.
4. `test_migrated_specs_resolve_to_literals`: for each spec of `regress.discover_maps()`, load the raw YAML with
   `mapgen.schema._SpecLoader`, build the literal form by replacing every string in an accepted place (3.2) with the
   raw coordinates of its target (`teams[t].base` for `base_<t>`, `points[name]`, the POI's `at`), delete `points`,
   dump with `yaml.safe_dump(..., sort_keys=False)` to `tmp_path`, `load_spec` both, and assert equal: per region
   `(id, at, points, path, label_at)`, per line `(id, path, label_at)`, per crossing `(id, at, path)`, per POI of
   `all_pois()` `(id, at)`, per scatter `(id, within)`.
5. `test_unknown_reference_cli`: write migrated thornwald with `path: [base_B, [2150, 2300]` replaced by
   `path: [base_b, [2150, 2300]` (one occurrence); `mapgen.__main__.main(["validate", path])` returns 1 and stderr or
   stdout contains `lines[2].path[0]: unknown reference 'base_b' (a point reference names a POI, a spawn base or a
   `points` entry)`. Match how `tests/test_validation.py` `test_invalid_spec` captures output.
6. `test_thornwald_states_base_b_once`: the text of `maps/concepts/thornwald.yaml` contains `[2050, 2560]` exactly
   once (in `teams`).

### 10.3 Existing fixtures

Checked against the migrated specs in the prototype; no change needed besides `ALLOWED`:
`tests/test_identity.py` edits thornwald at `"\nregions:\n"` and `"{ id: region_6, seed: 5, "` (both still unique);
`tests/test_diff.py` `river` fixture edits `example_river_crossing` at `"at: [1500, 900] }"` (POI `cap_s`, not
referenced) and `"width: 450, height: 220 }"` (both still unique). Baselines change only by re-baselining in wave D.

## 11. Documentation updates

Durable text states what things are; no contrast with earlier behaviour.

### 11.1 `MAPGEN_SPEC.md` (CRLF; keep CRLF)

- 4.1 table, add a row after `teams`:
  `| `points` | no | mapping of name to point: named positions shared by several features (§4.9) |`
- 4.1, the sentence `A point is `[x, y]` in metres. A path is a list of ≥ 2 points.` becomes
  `A point is `[x, y]` in metres; in the places listed in §4.9 it may also be a point reference. A path is a list of
  ≥ 2 points.`
- 4.8 **Format version** paragraph, append: `The format version changes when a spec that is valid under the current
  version would become invalid or build differently; an addition after which every valid spec stays valid and builds
  identically keeps the version.`
- 4.8 **One namespace** bullet: the list becomes `The ids of regions, lines, scatter entries, crossings and POIs, the
  names of `points` entries, the implicit base ids and every automatic crossing id ...`.
- 4.8 **Validation messages**: `The duplicate check visits bases, `points`, `regions`, `lines`, `scatter`,
  `crossings`, `pois` in that order ...`.
- Add section **4.9 Point references** (after 4.8):

  > A point reference is a name written where a point is expected. It names a POI (`id`), a spawn base
  > (`base_<team>`) or an entry of the top-level `points` mapping, and resolves to that position when the spec loads,
  > to exactly the coordinates of the target's literal. Moving a POI, a base or a `points` entry moves every feature
  > that refers to it.
  >
  > Accepted in: region `at` (blob), `points` (poly) and `path` (band); line `path`; crossing `at` and `path`. A path
  > may mix references and literals. Not accepted in: team `base`, POI `at`, `points` values (the targets are
  > literal; there are no chains), `label_at`, scatter `within`.
  >
  > `points` maps names to literal points: `points: { lea_crossroads: [2366, 1906] }`. A name is an id (§4.8): it
  > shares the one namespace. Use it for a position shared by several features that is neither a POI nor a base, such
  > as a road junction. An entry nobody references is valid.
  >
  > A reference is a YAML string; quote it only when YAML would read it as another type (`null`, `true`, `false`).
  > `spec.resolved.json` contains the resolved coordinates; `points` is not exported.
  >
  > Messages: `lines[0].path[1]: unknown reference 'base_c' (a point reference names a POI, a spawn base or a `points`
  > entry)`; `lines[0].path[1]: 'wood' is a region id; a point reference names a POI, a spawn base or a `points`
  > entry` (also `line`, `scatter entry`, `crossing`); a string in a place without references:
  > `pois[0].at: expected a point [x, y] of two numbers, got 'j' (point references are accepted only in region
  > at/points/path, line path and crossing at/path)`; `points: expected a mapping of name -> [x, y], got list`;
  > `points.Junction: name must match [a-z][a-z0-9_]* and be at most 64 characters (got 'Junction')`.
- 6.5 table row `spec.resolved.json`: append `; point references appear as their resolved coordinates`.
- 8 Validation list: add after the id items: `a point reference naming no POI, base or `points` entry, or naming a
  region, line, scatter entry or crossing; a string in a point place that takes no reference; `points` not a mapping,
  a `points` name that is not an id, a `points` value that is not a literal point inside the map;`.

### 11.2 `README.md` (CRLF; keep CRLF)

- Authoring keys table, two rows after `seed`:
  - `| `points: {name: [x, y]}` | top level | named positions shared by several features that are neither POIs nor
    bases (road junctions, a crossing on a river point). A name is an id: `[a-z][a-z0-9_]*`, unique across the spec |`
  - `| point reference | region `at`/`points`/`path`, line `path`, crossing `at`/`path` | a name in place of
    `[x, y]`: a POI id, `base_<team>` or a `points` name, e.g. `path: [base_B, [2150, 2300], lea_crossroads]`. It
    resolves to that position when the spec loads, so moving the POI, base or named point moves every feature that
    refers to it. An unknown name is a validation error |`
- Outputs table row `export/spec.resolved.json`: append `; point references appear as coordinates`.

### 11.3 `EXPORT_FORMAT.md` (LF)

3.9, after the sentence ending `... use `regions.json` / `lines.json` / `crossings.json` for final geometry.`, add:
`Point references in the spec (MAPGEN_SPEC.md §4.9) appear here as their resolved [x, y] coordinates; the spec's
`points` section is not part of this file.` The bundle content does not change, so `docs/battlehounds-consumer.md`
needs no edit.

### 11.4 `docs/roadmap.md` (LF)

- Delete the gap row `| Coordinates are repeated by hand | ... |` (the gap is closed).
- Package 4 section: add the line `Implementation spec: `mapgen/REFERENCES_SPEC.md`.` after the four bullets.

### 11.5 Map-concept skill `E:\GameDev\Unity\.claude\skills\map-concept\SKILL.md` (parent tier, no git)

- Process step 3, append: `State each shared position once. Where a line, region or crossing meets a POI or a base,
  write the POI id or `base_<team>` in place of the point (`path: [base_A, [900, 800], mill]`). A junction or other
  point that several features share and that is neither goes into the top-level `points` mapping, named after the
  features that meet there (`lea_crossroads: [2366, 1906]`), and the features name it. References work in region
  `at`/`points`/`path`, line `path` and crossing `at`/`path`.`
- Pitfalls, add the bullet: `Moving a POI, a base or a `points` entry moves every feature that names it; a feature that
  repeats the coordinates instead stays where it was.`

`CLAUDE.md` needs no change.

## 12. Acceptance checks

1. `python -m pytest tests/test_validation.py tests/test_references.py tests/test_identity.py
   tests/test_cli_exit_codes.py tests/test_diff.py tests/test_diff_cli.py tests/test_regress_helpers.py` passes.
2. `python -m mapgen validate` passes on all eight migrated specs and on the eight `PRE` specs.
3. Wave C check passes: on all eight maps only `export/meta.json` differs, with only `/spec_sha256`.
4. Backlog verify clause: regression suite green: all 8 maps byte-identical except the spec-echo files, re-baselined
   after owner approval of the diff; thornwald states the base position of team B once (`test_thornwald_states_base_b_once`);
   moving that base in a scratch copy moves `b_track` and `abbey_path` with it (`test_move_base_moves_references`); a
   reference to an unknown id is a validation error (`ref_unknown`, `test_unknown_reference_cli`).
5. Determinism: two builds of migrated thornwald are byte-identical.
6. Documentation of section 11 is in the same commit as the code (the skill edit lands in the parent tier).

## 13. Consumer: Battlehounds

No follow-up item: the bundle is byte-identical apart from `meta.json` `spec_sha256`, which changes whenever a spec
file changes. Battlehounds-committed bundles re-baked from the migrated specs differ only in that value.

## 14. Implementation waves

Contrast with the pre-package behaviour is allowed in this section only. Nothing is committed until wave D.

### Wave 0 (coordinator)

- Working tree clean on `main` at `PRE` (`d348b5c`); `python -m pytest` green.

### Wave A (one agent): code

Section 7.1 in `mapgen/schema.py` only (the existing namespace loop over sections becomes the `holders` loop; the
charset test there and in `_ident` may call `_valid_id`). Then: all eight unmigrated specs still validate
(`python -m mapgen validate <spec>`, exit 0); `python -m pytest tests/test_validation.py` fails in exactly
`test_invalid_spec[unknown_top_key]`, `test_invalid_spec[two_errors]` and `test_load_spec_raises_spec_error`, all
through the `ALLOWED` text (fixed in B2; measured on the prototype). Do not touch `maps/`, `tests/`, docs.

### Wave B (parallel, after A)

- **B1 migration:** create `out/references-review/migrate.py` with the text of Appendix A; run
  `--check` on the eight specs and compare the printed plan with section 9.1 (128 references, 22 entries, the listed
  names); then run without `--check`. `python -m mapgen validate` all eight. `git diff --stat maps/` shows only the
  eight specs; `git diff maps/concepts/thornwald.yaml` matches 9.1. Confirm LF endings are kept (`git diff` shows no
  whole-file change) and no other text changed.
- **B2 tests:** section 10 (`ALLOWED`, the new `CASES`, `tests/test_references.py`). Run them after B1.
- **B3 docs:** section 11 (keep CRLF in `MAPGEN_SPEC.md` and `README.md`: edit with a tool that preserves line endings
  and confirm with `git diff --stat` that only the intended lines change).

### Wave C (one agent, after B): review material, then STOP

1. Pre-migration source: `mkdir -p out/references-review/pre-src && git archive d348b5c mapgen maps | tar -x -C out/references-review/pre-src`.
2. Pre builds: from `out/references-review/pre-src`, for each of the eight specs
   `../../../.venv/Scripts/python -m mapgen build <spec> --out ../pre`; confirm with `tests/regress.py` (`hash_tree` +
   `load_baseline` + `compare`, imported from the repo) that every pre build equals its baseline.
3. Post builds: from the repo root, `python -m mapgen build <spec> --out out/references-review/post` for the eight
   migrated specs (empty directory first).
4. Reports: for each map `<name>`:
   `python -m mapgen diff --text out/references-review/pre/<name> out/references-review/post/<name> > out/references-review/diff/<name>.txt`
   and the same without `--text` into `out/references-review/diff/<name>.json`.
5. Check script `out/references-review/check_review.py`: per map, `mapgen.diff.diff_builds(pre, post, 100000)`; fail
   unless no file is added or removed, the changed files are a subset of {`export/meta.json`} and the JSON patterns
   of `export/meta.json` are a subset of {`/spec_sha256`}; print `<name>: OK (<n> files changed)`. Expected: all eight
   `OK (1 files changed)`.
6. `out/references-review/SUMMARY.txt`: per map the `files:`, `heightmap:`, `rasters:`, `json:` and `objectives:`
   lines of `diff/<name>.txt`, the check output, and the pytest result of step 7.
7. `python -m pytest`: everything passes except `test_build_matches_baseline`, which fails on all eight maps naming
   only `export/meta.json`.
8. **STOP.** Do not run `tests/rebaseline.py`. Do not commit. Report the paths of `out/references-review/diff/`,
   `SUMMARY.txt` and the check output to the coordinator, who shows the eight diff reports to the owner together with
   decisions R1-R10, the names of 9.1 and the list of 9.2.

### Wave D (coordinator, only after the owner approves the diffs)

1. Apply any owner renames of `points` entries (text edit of the entry and its references; no output change), then
   `python tests/rebaseline.py --all`; then `python -m pytest` fully green.
2. One commit: `mapgen/schema.py`, the eight migrated specs, tests, re-baselined `tests/baselines/*.json`, docs
   (section 11.1-11.4), this file. The skill edit (11.5) lands in the parent tier (no git there).

## Appendix A. `out/references-review/migrate.py`

```python
"""Migrate map specs to point references (roadmap package 4). Text-level: only the spans of migrated point
literals change, plus one inserted `points:` block; comments, key order, formatting and LF are preserved.

Usage: python migrate.py <mapgen source root> <spec.yaml>... [--check]
  Rewrites each spec in place. --check: report planned edits, write nothing.
"""
import sys
from collections import OrderedDict

import yaml

# Proposed `points` names (owner to confirm). Key: (spec name, x, y).
NAMES = {
    ("ashen_steppe", 3000, 2000): "north_fork",
    ("ashen_steppe", 1000, 2100): "west_merge_north",
    ("ashen_steppe", 700, 1800): "west_merge_south",
    ("ashen_steppe", 2000, 450): "bend_fork",
    ("gloamfen", 1900, 1550): "causeway_meet",
    ("gloamfen", 2150, 2400): "north_path_join",
    ("kingsjaw", 1450, 680): "gate_site",
    ("kingsjaw", 740, 1431): "farm_lane_west",
    ("kingsjaw", 1200, 900): "farm_lane_east",
    ("saltreach_delta", 2100, 2050): "delta_fork",
    ("saltreach_delta", 1250, 2400): "bluff_crossroads",
    ("saltreach_delta", 1350, 520): "coast_fen_junction",
    ("saltreach_delta", 1990, 2700): "wold_goat_junction",
    ("saltreach_delta", 1600, 2560): "stair_goat_junction",
    ("thornwald", 2366, 1906): "lea_crossroads",
    ("thornwald", 1138, 904): "west_fork",
    ("thornwald", 658, 718): "abbey_junction",
    ("thornwald", 2050, 1250): "lodge_glade",
    ("natural", 1250, 1300): "river_road_meet",
    ("river_crossing", 1530, 1500): "bridge_site",
    ("river_crossing", 1480, 700): "ford_site",
    ("river_crossing", 1500, 2350): "pass_site",
}


def _get(mapping_node, key):
    for k, v in mapping_node.value:
        if k.value == key:
            return v
    return None


def _num(scalar):
    return yaml.safe_load(scalar.value)


def _as_point(node):
    """(x, y) of a literal point node, else None."""
    if isinstance(node, yaml.SequenceNode) and len(node.value) == 2 and all(
            isinstance(c, yaml.ScalarNode) for c in node.value):
        x, y = _num(node.value[0]), _num(node.value[1])
        if all(isinstance(c, (int, float)) and not isinstance(c, bool) for c in (x, y)):
            return (x, y)
    return None


def places(root):
    """Yield (feature key, place path, node) for every point literal in an accepted place."""
    for sect, keys in (("regions", ("at", "points", "path")), ("lines", ("path",)), ("crossings", ("at", "path"))):
        seq = _get(root, sect)
        if not isinstance(seq, yaml.SequenceNode):
            continue
        for i, item in enumerate(seq.value):
            if not isinstance(item, yaml.MappingNode):
                continue
            fid = _get(item, "id")
            feat = "%s[%d:%s]" % (sect, i, fid.value if fid is not None else "?")
            for key in keys:
                n = _get(item, key)
                if n is None:
                    continue
                if key == "at":
                    if _as_point(n) is not None:
                        yield feat, "%s.at" % feat, n
                elif isinstance(n, yaml.SequenceNode):
                    for j, q in enumerate(n.value):
                        if _as_point(q) is not None:
                            yield feat, "%s.%s[%d]" % (feat, key, j), q


def plan(text):
    from mapgen.schema import _SpecLoader
    root = yaml.compose(text, Loader=_SpecLoader)
    name = _get(root, "name").value
    anchors = OrderedDict()  # position -> id (bases first, then POIs; first holder wins)
    teams = _get(root, "teams")
    for k, tv in teams.value:
        b = _as_point(_get(tv, "base"))
        if b is not None:
            anchors.setdefault((float(b[0]), float(b[1])), "base_" + k.value)
    pois = _get(root, "pois")
    for it in (pois.value if pois is not None else []):
        a = _as_point(_get(it, "at"))
        if a is not None:
            anchors.setdefault((float(a[0]), float(a[1])), _get(it, "id").value)
    edits = []  # (start, end, replacement, place)
    rest = OrderedDict()  # position -> [(feat, place, node, literal)]
    for feat, place, node in places(root):
        x, y = _as_point(node)
        key = (float(x), float(y))
        if key in anchors:
            edits.append((node.start_mark.index, node.end_mark.index, anchors[key], place))
        else:
            rest.setdefault(key, []).append((feat, place, node, (x, y)))
    entries = []
    for key, hits in rest.items():
        feats = []
        for h in hits:
            if h[0] not in feats:
                feats.append(h[0])
        if len(feats) < 2:
            continue
        lit = hits[0][3]
        nm = NAMES.get((name, lit[0], lit[1]))
        if nm is None:
            raise SystemExit("%s: no name for shared point [%s, %s] (%s)" % (name, lit[0], lit[1], ", ".join(feats)))
        entries.append((nm, lit, [f.split(":")[1].rstrip("]") for f in feats]))
        for feat, place, node, _ in hits:
            edits.append((node.start_mark.index, node.end_mark.index, nm, place))
    # insertion point: end of the line holding the last value of `teams`
    last = teams.value[-1][1]
    ins = text.index("\n", last.end_mark.index) + 1
    return name, edits, entries, ins


def apply(text, edits, entries, ins):
    out = text
    block = ""
    if entries:
        width = max(len("  %s: [%s, %s]" % (n, l[0], l[1])) for n, l, _ in entries)
        rows = ["%-*s  # %s" % (width, "  %s: [%s, %s]" % (n, l[0], l[1]), ", ".join(f)) for n, l, f in entries]
        block = "\npoints:\n" + "\n".join(rows) + "\n"
    spans = sorted(edits + ([(ins, ins, block, "points")] if block else []), key=lambda e: e[0], reverse=True)
    for s, e, rep, _ in spans:
        out = out[:s] + rep + out[e:]
    return out


def main(argv):
    sys.path.insert(0, argv[1])
    check = "--check" in argv
    for path in [a for a in argv[2:] if a != "--check"]:
        with open(path, "r", encoding="utf-8", newline="") as f:
            text = f.read()
        name, edits, entries, ins = plan(text)
        print("== %s: %d references, %d points entries" % (name, len(edits), len(entries)))
        for s, e, rep, place in sorted(edits, key=lambda x: x[0]):
            print("  %-40s %-14s -> %s" % (place, text[s:e], rep))
        for n, l, f in entries:
            print("  points.%s: [%s, %s]  (%s)" % (n, l[0], l[1], ", ".join(f)))
        if not check:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(apply(text, edits, entries, ins))


if __name__ == "__main__":
    main(sys.argv)
```

The plan must be computed on the unmigrated specs: run the tool once. On a migrated spec it finds no literals to
migrate (`0 references, 0 points entries`) and writes the file unchanged.
