# Concept map balance and the balance-tuning process

- Date: 2026-11-14
- Checked against: mapgen commit `77e278b` (clean working tree), mapgen 0.2.1, Python 3.8 venv
- Scope: the five specs in `maps/concepts/` (`thornwald`, `gloamfen`, `ashen_steppe`, `saltreach_delta`,
  `kingsjaw`), with `thornwald` as the case study
- Status: investigation only. No spec or code was changed. Candidate edits were evaluated on temporary copies
  outside the repo.

## 1. Question and short answer

Playtest feedback says `maps/concepts/thornwald.yaml` heavily favours team B. This document records what mapgen
measures about that, what it does not measure, and how well the current tooling supports tuning balance by editing
a spec.

Short answer:

1. **By mapgen's own report, thornwald is balanced.** No warnings, no `IMBALANCED` flag, every neutral objective
   within the 20 % tolerance. B has a mild edge (first to 4 of 7 neutral objectives, 142 s of summed lead against
   A's 74 s); A reaches more of the map first (46.8 % against 43.8 %).
2. **That balance depends on mapgen's movement costs, and the game does not use them.** B is geometrically closer
   to every neutral objective. A is compensated only by the King's Road at cost 0.6 and by B's fords at cost 2.5.
   `docs/roadmap.md` records that Battlehounds uses road 0.8, trail 0.9 and ford 1.0. Re-running the analysis with
   those three values makes B first to all 7 neutral objectives (293 s summed lead, two objectives `IMBALANCED`,
   B 49.2 % of the map against A 41.3 %). This is the most likely cause of the playtest result (inference, section 5).
3. **B also holds advantages the analysis does not score at all**: a base 24 m higher than A's on a hill crest,
   open sightlines around its base (A's base sits inside LOS-blocking town), and a river between its base and
   every attacker.
4. **The tuning loop is workable but manual.** One analysis pass takes 4 s, but there is no per-team summary score,
   no comparison between two builds, no threshold beyond the per-objective spread, no way to analyse with the
   consumer's costs, and spec coordinates are duplicated by hand across coupled features. Section 8 lists the gaps.

## 2. How the numbers were produced

- `python -m mapgen build maps/concepts/<name>.yaml` for all five maps; all exit 0 with "Warnings: None".
- `report.md` / `report.json` and `export/pois.json` in `out/<name>/`.
- `python -m mapgen probe` for spot checks.
- Supplemental numbers (marked *supplemental*) come from throwaway scripts that call `mapgen.build.prepare()` and
  read the in-memory `AnalysisResult` (cost grid, per-team time fields, tracebacks, openness, sampled heights).
  These numbers are not in any mapgen output file; that absence is itself a finding (section 8).
- Conventions: metres, origin south-west, `x` east, `y` north. Times are `m:ss` at 5 m/s on cost-1.0 ground.
  `spread = (max - min) / min` over the teams' times.

## 3. Thornwald: measured per-team numbers

Map: 3200 x 2800 m, seed 4410, 2 teams. A "Wardens" at (430, 470), B "Charcoalers" at (2050, 2560).

### 3.1 From `report.md` / `report.json`

| metric | A | B | note |
|---|---|---|---|
| area reached first | 46.8 % | 43.8 % | contested 8.2 %, unreachable 1.2 % |
| openness of own territory (1 = open) | 0.21 | 0.40 | map mean 0.30 |
| own supply, owner's time | 0:53 | 1:03 | |
| enemy time to this team's supply | 6:42 (B to `supply_a`) | 7:13 (A to `supply_b`) | B raids 31 s faster; neither `EXPOSED` |

Base to base: 7:15. Route openness A-B 0.69, route constriction 0.10. Fair zone 22.7 % of the map.

| objective | type | t(A) | t(B) | spread | first | lead |
|---|---|---|---|---|---|---|
| `thornhold` | keep_site | 3:46 | 4:19 | 15 % | A | 33 s |
| `lea_keep` | keep_site | 6:59 | 6:00 | 16 % | B | 59 s |
| `abbey_camp` | neutral_camp (tier 2) | 5:05 | 4:55 | 3 % | B | 10 s |
| `barrow` | neutral_camp (tier 3) | 6:50 | 7:10 | 5 % | A | 20 s |
| `barrow_relic` | treasure | 7:07 | 7:27 | 5 % | A | 21 s |
| `lodge` | capture | 4:44 | 4:35 | 3 % | B | 9 s |
| `charcoal` | neutral_camp (tier 1) | 6:44 | 5:40 | 19 % | B | 64 s |

All flags `ok`. The "first" and "lead" columns are derived here; the report prints only the two times and the spread.

Chokepoints (top 5, score = paths x constriction): ford `auto_3` 0.4, Kingsmead 0.4, ford `auto_4` 0.4, Mossbeck
0.2, King's Road 0.2. Constriction is at most 0.18 everywhere, so the report sees no strong chokepoint on this map.
Chokepoints are not attributed to a team.

Automatic crossings on the Mossbeck: one bridge (`auto_1`, King's Road, at Kingsmead) and four fords
(`auto_2` to `auto_5`).

### 3.2 Supplemental (not in any output file except heights in `pois.json`)

| metric | A | B |
|---|---|---|
| neutral objectives reached first | 3 | 4 |
| sum of leads on those | 74 s | 142 s |
| sum of times to all 7 neutral objectives | 2475 s | 2408 s |
| straight-line distance to each neutral objective | longer on all 7 | shorter on all 7 (by 147 to 459 m) |
| shortest-path length to each neutral objective | longer on all 7 | shorter on all 7 (by 160 to 438 m) |
| river crossings on the path to each neutral objective | 0 | 1 on every one |
| river crossings on the path to the enemy base / enemy supply | 1 | 1 |
| base height (`pois.json` `y`) | 47.9 m | 71.9 m |
| base height minus mean height within 150 m | 0.0 m | +6.4 m |
| mean height within 400 m of base | 48.7 m | 55.8 m |
| openness within 150 m of base | 0.03 | 0.79 |
| mean openness along paths to neutral objectives | 0.33 to 0.54 | 0.51 to 0.79 |

All seven neutral objectives lie on A's side of the Mossbeck. The river is therefore a barrier in front of B's base
and B's supply only; nothing comparable shields A's base.

Objective sites themselves are level: every objective is within 2.4 m of the mean height of its 150 m
surroundings, so there is no elevation advantage at the objectives.

### 3.3 Sensitivity to movement costs (supplemental)

The same analysis with different cost tables, everything else unchanged:

| cost table | first to neutral objectives (A / B) | summed lead (A / B) | area first (A / B) | flags |
|---|---|---|---|---|
| mapgen `types.yaml` (road 0.6, trail 0.8, ford 2.5) | 3 / 4 | 74 s / 142 s | 46.8 % / 43.8 % | none |
| consumer values from `docs/roadmap.md` (road 0.8, trail 0.9, ford 1.0) | 0 / 7 | 0 s / 293 s | 41.3 % / 49.2 % | `lea_keep` 24 %, `charcoal` 22 % `IMBALANCED`; `lodge` 20 % |
| uniform cost 1.0 on all passable cells | 1 / 6 | 2 s / 311 s | 46.7 % / 52.0 % | `lodge` 42 % |

Under the consumer values base-to-base is 535 s (mapgen: 435 s; `docs/roadmap.md` reports 571 s from the consumer's
own 16 m bake).

### 3.4 Probe spot checks

`python -m mapgen probe maps/concepts/thornwald.yaml ...`:

| point | A | B | spread | ground |
|---|---|---|---|---|
| `charcoal` (520, 2420) | 6:44 | 5:40 | 19 % | clearing, on `tarn_path` |
| (450, 2250) | 6:28 | 6:21 | 2 % | forest |
| (600, 2300) | 6:16 | 5:39 | 11 % | forest |
| `lea_keep` (2480, 820) | 6:59 | 6:00 | 16 % | field, on `lea_path` |
| (2400, 760) | 6:51 | 6:16 | 9 % | field |
| (2350, 700) | 6:30 | 6:38 | 2 % | forest |
| `thornhold` (1520, 1560) | 3:46 | 4:19 | 15 % | ruins |
| (1600, 1650) | 3:55 | 4:02 | 3 % | Kingsmead field |
| (1650, 1700) | 4:14 | 3:49 | 11 % | Kingsmead field |

The equal-time line runs through Kingsmead about 120 m north-east of Thornhold; 190 m from Thornhold the lead has
flipped to B.

## 4. Thornwald: authored causes in the spec

Line numbers refer to `maps/concepts/thornwald.yaml` at `77e278b`.

### 4.1 Intent

The map is deliberately asymmetric. Nothing in it is mirrored or rotated.

- The `brief` (lines 2-13) states: A holds Southwatch "and own[s] the road"; B comes "down from the Ravenscar
  hills", is "cut off from the road by the Mossbeck stream and forced through four fords". The brief describes A's
  asset and B's handicap. It does not name any B advantage or say what compensates what.
- The authoring skill (`E:\GameDev\Unity\.claude\skills\map-concept\SKILL.md`) requires "Asymmetric but fair":
  "no mirrored layouts", bases with different characters, the brief stating "what compensates what", neutral
  objectives within `balance_tolerance` ("use 0.2").
- `MAPGEN_SPEC.md` section 10 accepts a concept when it builds without warnings and every neutral objective is
  within the spec's tolerance. Thornwald meets that.

### 4.2 Entries that differ between the sides

| line | entry | effect | size |
|---|---|---|---|
| 25-26 | `teams.A.base: [430, 470]`, `teams.B.base: [2050, 2560]` | B sits closer to the map centre (1600, 1400): 1244 m against A's 1495 m | B 251 m (17 %) closer; closer to all 7 neutral objectives |
| 53 | `kings_road` (road, cost 0.6) | Starts at A's base, passes Thornhold, never reaches B's base (nearest point 730 m away). A's only compensation for distance | A's paths to objectives run 58-88 % on road/trail |
| 54 | `b_track` (trail from B's base to the road at (2366, 1906)) | Gives B the road. Without it B's `thornhold` time goes 4:19 to 5:01, `lodge` 4:35 to 5:22 | one line, worth 40-50 s to B on four objectives |
| 55 | `abbey_path` (trail from B's base past `abbey_camp` to (658, 718)) | A trail from B's base that ends 80 m from `supply_a` (700, 650). A has no trail towards `supply_b` | B raids 31 s faster than A |
| 58 | `tarn_path` (trail `charcoal` to `supply_b`) | Connects B's supply directly to a neutral camp | B leads `charcoal` by 64 s (19 %) |
| 56 | `lea_path` (trail from the road junction near B to `lea_keep` and `barrow`) | B's fast route to the east meadow | B leads `lea_keep` by 59 s (16 %) |
| 34 | `hills` Ravenscar, `height: 70`, centred (1900, 2650) under B's base | High ground and open view for B's base | B base +24 m over A's base |
| 33 | `town` Southwatch around A's base | Flat, LOS-blocking ground for A's base | openness 0.03 against B's 0.79 |
| 52 | `mossbeck` river | Separates B's base and supply from the whole rest of the map; 5 crossings | every attacker of B crosses once; B crosses once to reach anything |
| 74-75 | `analysis.balance_tolerance: 0.2` | With the schema default 0.15, `lea_keep` (16.4 %) and `charcoal` (18.7 %) would flag `IMBALANCED`; `thornhold` (14.8 %) would not | three of seven objectives sit at 15-19 % |

Objective weight is not modelled: the two objectives B leads by about a minute are a keep site and a tier-1 camp;
the one A leads by half a minute is the other keep site.

## 5. Facts and inference

Measured:

- mapgen reports thornwald as balanced within tolerance, with a mild B edge on neutral objectives and an A edge on
  area.
- B is closer to every neutral objective in straight-line and path distance.
- With road 0.8 / trail 0.9 / ford 1.0, B is first to every neutral objective and two objectives exceed tolerance.
- B's base is 24 m higher and far more open than A's; the river lies in front of B only.

Inference (not verified):

- The playtest imbalance is mainly the cost mismatch between mapgen's analysis and the game. The consumer values
  were taken from `docs/roadmap.md`; the Battlehounds code and its actual unit speeds were not inspected. If the
  game applies no road bonus to infantry at all, the uniform-cost row applies and the effect is larger.
- High ground, base sightlines and the river moat add to B's advantage in play. mapgen has no number for their
  gameplay value.
- The brief's framing (A advantaged by the road, B handicapped by fords) led the author to compensate B with
  proximity and trails; with the game's weaker road and free fords only the compensation remains.

## 6. What the analysis measures, flags and misses

Code references are to `mapgen/analysis.py` and `mapgen/build.py` at `77e278b`.

### 6.1 Measured and flagged

| item | where | how it surfaces |
|---|---|---|
| neutral objective spread above tolerance | `analysis.py:221-229` | `IMBALANCED` in the objective table's flag column |
| objective unreachable for a team | `analysis.py:222-223`, `232-233` | `UNREACHABLE` flag |
| owned objective reached sooner by an enemy | `analysis.py:234-235` | `EXPOSED` flag |
| base cannot reach base, POI on impassable cell, uncovered water crossing | `analysis.py:146-150`, `202`, `295-318` | entries in `warnings` |

Flags are not warnings. `IMBALANCED` does not appear in the Warnings section, is not printed by the build
(`build.py:204-205` prints warnings only) and does not change the exit code (kingsjaw builds with two
`IMBALANCED` flags, "Warnings: None" and exit 0).

### 6.2 Measured and reported, never flagged

| item | where | in `report.json` |
|---|---|---|
| area reached first per team, contested, unreachable | `analysis.py:167-184` | `area_share` |
| base-to-base time | `analysis.py:191-207` | `base_times_s` |
| per-team time to every objective | `analysis.py:209-219` | `objectives[].times` (seconds) |
| enemy time to an owned objective (raid time) | same | `objectives[].times`; no per-side comparison |
| fair-zone share | `analysis.py:186-189` | `fair_zone_share` |
| openness per team territory and per route | `analysis.py:279-293` | `openness_by_team`, `openness_along_routes` |
| top-5 chokepoints, route constriction | `analysis.py:245-277` | `chokepoints`, `route_constriction`; no team attribution |

No threshold exists for any of these. Examples that pass silently: saltreach_delta's area share 48.3 % against
33.7 %; thornwald's territory openness 0.21 against 0.40; kingsjaw's 0.90 against 0.40; ashen_steppe's own-supply
time 0:50 against 1:41.

### 6.3 Not measured

| factor | state |
|---|---|
| aggregate per-team balance (who is first where, summed or weighted lead) | not computed; objective `tier` and type are not used by the analysis |
| elevation advantage (base or objective height relative to surroundings, uphill approaches) | height enters only as a slope cost multiplier (`analysis.py:86-95`); absolute heights appear only as `y` in `export/pois.json` |
| line of sight / visibility | none. "Openness" is a 100 m box filter over a binary LOS-blocking mask (`analysis.py:280-288`), with no rays and no height |
| defensibility of a base or objective (barriers, crossings an attacker must take) | none; chokepoint ranking excludes the area around bases (`analysis.py:254-260`) |
| number of distinct approaches to a base or objective | none; only the single shortest path per team is traced (`analysis.py:205`, `240`) |
| route redundancy (second-best route, cost of losing a crossing) | none |
| path length in metres, terrain mix along a path, crossings on a path | not reported |
| cover at an objective and along approach paths per team | only the base-to-base route gets an openness mean |
| travel under other movement profiles (cavalry, the consumer's costs) | one cost table (`mapgen/types.yaml`) and one speed; no override on the command line |
| resource totals per side | supplies are listed, not counted or compared (saltreach_delta: A owns 2, B owns 1) |

## 7. The other concept maps

mapgen costs; "consumer costs" is the section 3.3 re-run.

| map | area first A / B | first to neutrals A / B | summed lead A / B | largest spread | other deltas | verdict (mapgen costs) | consumer costs |
|---|---|---|---|---|---|---|---|
| thornwald | 46.8 / 43.8 % | 3 / 4 | 74 / 142 s | 19 % `charcoal` (B) | openness 0.21 / 0.40; B base +24 m | within tolerance, mild B edge | B 7 / 0, 2 flags |
| gloamfen | 42.3 / 43.4 % | 4 / 2 | 73 / 76 s | 16 % `heron` (B) | raid time on enemy supply: B 6:49, A 8:06 | balanced; B raids 77 s faster | B 4 / 2, 3 flags |
| ashen_steppe | 44.6 / 49.0 % | 3 / 4 | 122 / 75 s | 17 % `wolves` (A) | own supply 0:50 / 1:41; raid A 7:13, B 8:10 | balanced on neutrals; supply favours A | A 5 / 2, 387 / 40 s, 2 flags |
| saltreach_delta | 48.3 / 33.7 % | 3 / 4 | 70 / 69 s | 16 % `old_bridge` (B) | A owns 2 supplies, B 1; raid B 5:07, A 6:33 | neutrals balanced; 14.6-point area gap unflagged | B 7 / 0, 390 s, 2 flags |
| kingsjaw | 45.4 / 43.0 % | 3 / 2 | 210 / 285 s | 151 % `wolf_den` (B), 143 % `mill` (A) | openness 0.90 / 0.40; fair zone 1.1 % | balanced by design: the two flags are the paired home objectives documented in `MAPGEN_SPEC.md` section 10; the other three are within 11 % | unchanged pattern |

None of the other four shows a one-sided result under mapgen's costs. Under the consumer's costs saltreach_delta
shows the same pattern as thornwald (B first everywhere) and ashen_steppe the reverse (A dominant), so the cost
mismatch is a cross-map problem, not a thornwald problem.

## 8. The tuning process, tested on thornwald

Four candidate edits were applied to temporary copies of the spec and analysed. They are probes of the editing
process, not recommended fixes.

### 8.1 Loop cost

| step | time |
|---|---|
| `probe <spec> <points...>` | 4.0 s |
| `build <spec> --only analyze` (writes `report.md`, `report.json`) | 4.0 s |
| full `build` | 16 s (render 10.7 s) |

A variant needs its own `name:` or `--out`, because the output directory is `out/<name>/`.

### 8.2 Candidate edits

**E1. Move one objective: `charcoal` to (450, 2250).**

- Expression: the POI's `at` (line 68). Coupled by hand-copied coordinates: the clearing around it (line 40,
  `[540, 2400]`) and the first vertex of `tarn_path` (line 58, `[520, 2420]`). Three lines; coordinates chosen by
  probing, then typed three times.
- Result: `charcoal` spread 18.7 % to 4.9 % (A 6:15, B 5:57). Moving the POI alone gives 2.7 % (A 6:22, B 6:12)
  but leaves the clearing and the trail end 170 m away from the camp.
- Side effects: `abbey_camp` A time 5:05 to 5:01; nothing else moves.
- Probe accuracy: the probe predicted A 6:28 / B 6:21 for the new spot; the edited spec gives 6:15 / 5:57. Probe
  evaluates the point on the current map and cannot include the features that move with the POI.
- Confirming metric: `objectives[id=charcoal].spread` and `.times` in `report.json`.
- Confidence that the loop works for this kind of edit: high. Local, cheap, readable.

**E2. Remove B's link to the road: delete `b_track`.**

- Expression: one line (54).
- Result: B loses 40-70 s on `thornhold`, `lodge`, `barrow`, `barrow_relic`, `lea_keep`; `thornhold` becomes
  33.6 % `IMBALANCED` in A's favour; area share 52.6 % / 40.6 %; base-to-base 7:15 to 7:57.
- Side effects: global. One line changes five objective rows, the area share and the base-to-base time.
- Confirming metric: all of `objectives[]`, `area_share`, `base_times_s`; the agent has to compare seven rows by
  hand against the previous build.
- Confidence: high that it shifts balance, low that it lands on target; the step size of a whole line is too
  coarse and there is no intermediate (the trail type's cost lives in `types.yaml`, not per line).

**E3. Move B's base: `teams.B.base` to (2200, 2640).**

- Expression: line 26, plus the first vertex of `b_track` and `abbey_path` (lines 54, 55), which repeat
  `[2050, 2560]`. Not followed in the test: the hill centre (line 34) and `supply_b` (line 71) with its
  `tarn_path` end (line 58).
- Result with the trails moved: first-to count 5 / 2 for A, `thornhold` 19.2 %, B's own supply 1:03 to 1:43,
  B base height 71 m to 90 m (the base lands higher on the hill).
- Result with only line 26 changed (trails left behind): `thornhold` 33.2 % `IMBALANCED`, area 55.6 % / 35.3 %.
  The spec stays valid and builds without warnings although the base no longer sits on its trails.
- Confirming metric: `objectives[]`, `area_share`; the height change is visible only in `export/pois.json`
  (requires a full build, not `--only analyze`).
- Confidence: medium. Effective, but it is the edit most exposed to hand-copied coordinates; a forgotten vertex
  silently changes the result by a factor of two.

**E4. Lower B's hill: `height: 70` to `40` on Ravenscar (line 34).**

- Expression: one key on one line.
- Result: B base 71 m to 60 m. No reported metric describes high ground, so nothing in the report confirms the
  intended effect.
- Side effects: slope cost falls around B's base, B gets 4-10 s faster to everything, and `charcoal` goes from
  18.7 % to 22.1 %, which is `IMBALANCED`. An edit meant to weaken B strengthens it in the only metric available.
- Confirming metric: none for elevation. `y` of `base_B` in `export/pois.json` is the nearest thing.
- Confidence that the current loop can tune this factor: low.

### 8.3 Observations on the spec as an editing surface

- **Coordinates are literals, repeated wherever features meet.** In thornwald: `[2050, 2560]` (B base) on lines
  26, 54, 55; `[2366, 1906]` (road junction) on 53, 54, 56, 59; `[1138, 904]` on 53, 56, 59; `[658, 718]` on 53,
  55; `[2680, 560]` on 45, 56, 65; `[1520, 1560]` on 47, 62; `[2480, 820]` on 37, 63; `[520, 2420]` on 58, 68;
  `[1800, 2450]` on 58, 71. Only river tributaries can reference another feature (`join`).
- **Nothing links a POI to the region painted around it or to the trail that ends at it**, and no validation
  notices when they drift apart.
- **One objective per line and flow-style entries make edits small and greppable.** That part works well.
- **The analysis responds smoothly and deterministically**, so bisecting a coordinate is feasible; what is missing
  is the read-out, not the response.
- **Terrain edits leak into travel times** through the slope cost, so "cosmetic" height changes move objective
  spreads.

## 9. Tooling gaps for balance tuning

Ordered by how much each would shorten the loop.

1. **Analysis under the consumer's movement costs.** No way to run the analysis with another cost table or
   movement profile (`types.yaml` is fixed; `analysis` accepts only `cell`, `speed`, `balance_tolerance`). Until
   mapgen and Battlehounds share one cost table, every balance number here can disagree with the game. This is the
   existing roadmap gap "Movement costs are not consumed", and it dominates everything else.
2. **A per-team balance summary.** No single block states, per team: neutral objectives reached first, summed and
   weighted lead, area share, own-supply time, raid time on enemy supply, territory openness, base height. An
   agent derives these by hand from `objectives[]`. Objective weights (type, `tier`) are unused.
3. **Machine-readable delta between two builds.** No `diff`/compare command and no stored baseline. Every tuning
   step is a manual comparison of two objective tables.
4. **Thresholds beyond the per-objective spread.** `balance_tolerance` applies to single neutral objectives only.
   Area share, aggregate lead, supply and raid times, openness and base height have no tolerance and never flag.
5. **Flags that reach the build result.** `IMBALANCED` / `EXPOSED` live in a table column only: not in
   `warnings`, not on stdout, not in the exit code. There is no "deliberate imbalance" marker in the spec, so
   kingsjaw's intended flags and an accidental one look the same.
6. **Elevation and visibility metrics.** No high-ground measure for bases or objectives, no line-of-sight
   analysis, no cover measure at objectives or along per-team approach paths.
7. **Defensibility and approach metrics.** No count of distinct approaches, no barrier/crossing count per path,
   no second-best route, no chokepoint attribution to a team; chokepoints near bases are excluded by construction.
8. **Feature references in the spec.** No anchors or references (`at: charcoal`, a path vertex naming a POI or a
   junction), so moving anything means retyping coordinates on several lines, and a missed one fails silently.
9. **What-if probing.** `probe` cannot evaluate a moved POI together with its clearing and trail, nor a moved
   base; the full edit must be written to a spec copy first. A spec override on the command line
   (`--set pois.charcoal.at=450,2250`) or a "move with attachments" helper is missing.
10. **Path details in the report.** Path length, share on road/trail, crossings used and openness per team per
    objective are computed implicitly by the tracebacks and thrown away. They explain *why* a time differs and
    which line to edit.
11. **Fair-zone search.** The fair zone exists as an image only (`overlay_balance.png`). There is no query such as
    "nearest fair cell to this POI" or "cells where spread is under 5 %", so candidate positions are found by
    guessing points for `probe`.
12. **A symmetry helper.** No mirror or rotate operation for features. The authoring rules forbid mirrored
    layouts, so this matters least for the concept maps; it would matter for a deliberately symmetric competitive
    map.

Relation to `docs/roadmap.md` (package headings only were compared, not their full text): item 1 relates to
"Type table as data", items 10 and 11 to "Analysis grids as data", "`strategic.json`" and "Route graph", item 6 to
"Cover raster". Items 2-5, 8, 9 and 12 have no matching package heading.
