# Mission: high-level town and city layout generation

Status: queued (starts after the map-editor mission, backlog GDU-53..GDU-56, finishes). Owner request 2026-11-20.

## Problem

mapgen is good at coarse terrain and weak at detail in built-up areas. Hand-designing towns and cities in
the map spec costs a lot of effort. The owner already has a separate tool that fills a zone with detail
(buildings inside a district, roads along a spline or point series, objects along roads). What is missing is
the step before it: the high-level layout of a settlement that such a tool fills in.

## Goal

A procedural generator for the high-level layout of a human settlement on a map: **districts, major roads,
walls and gates (openings)**, plus a few key landmarks (squares, optionally a fortress or castle). It does
not place individual buildings, minor roads or alleys; those are added by hand afterwards for gameplay.

The district adjacency/road structure is a **connectivity graph**, in the sense of the flowgraphs
(`mapgen/FLOWGRAPH_SPEC.md`): major roads and gates determine how players move through the town, so the
topology has direct gameplay consequences and must be explicit, inspectable and checkable.

## Approach

- Start with one simple generator and add features progressively.
- Procedural, seeded and deterministic (same seed + parameters => same layout), with a few parameters that
  change the look and feel.
- Techniques to explore: Voronoi diagrams, BSP, grids (Manhattan-style), radial/wedge layouts with ring
  roads, organic growth, hybrids of these.
- Target 3-5 settlement types with distinct feel, for example:
  grid-like; circular with wedges and ring roads; angular Voronoi-style; Manhattan-distance style;
  organic and chaotic with little apparent plan; mixed; settlements that incorporate rivers, bays,
  islands connected by bridges, or large buildings such as fortresses and castles.
  Not all are required.
- A later phase makes a settlement fit existing geometry: nestle against a mountain, wrap a bay, and so on.

## Scope boundaries

- In: districts, major roads, walls, gates, a few landmarks (squares), the connectivity graph.
- Out: individual buildings, minor roads, alleys, piers and other hand-drawn detail, gameplay balance,
  symmetry, whether the overall map is good.
- The goal of the first phase is learning which human-habitat areas can be modelled with modest effort.

## First actions (subagents)

1. Several investigations of procedural city-generation techniques (Voronoi, BSP, grid/Manhattan,
   radial/wedge, organic growth, hybrids): how each yields districts, major roads, walls and gates; seeded
   parameters; effort; what it can and cannot express; how it maps to the mapgen spec (regions, lines,
   crossings, POIs, flowgraph nodes/edges).
2. One investigation producing a handful of fantasy-style town and city concepts for small- to
   medium-sized maps (600-1500 m per side). They need not be fully urban: a bigger city, a small town or a
   combination; each map includes some kind of towns or buildings. Purpose: find which habitat types we can
   model.

## Reference art

Provided by the owner (`references/`). Not to be replicated in detail; they show the kinds of structure
wanted (districts, major roads, walls, gates, a river or bay, islands).

| file | what it shows |
|---|---|
| `ref_5.png` | Harbour city on a bay: irregular wall, coloured districts with names, docks, outlying villages |
| `ref_6.png` | Walled bay city: districts around a central district, a docks quarter on the bay, a citadel on the wall |
| `ref_7.png` | Pentguard City: radial wedge districts and ring roads around a central plaza, outer irregular wall, a coastal fortress on a peninsula, surrounding fields |
| `ref_8.png` | Saltport: district wedges split by a few major roads, a wall with gates, a harbour, small fortified islands |
| `ref_9.png` | Daboha: concentric ring roads and a walled core, a river splitting the city with a bridge, an outer wall with gates |
| `ref_10.png` | A real-world city divided into numbered districts by highways: grid-like road network, district boundaries along major roads |
| `ref_12.png` | Zoradun: Manhattan-style blocks with radial boulevards, an inner wall, a citadel, a marketplace square, a wall with gates, a river and an island fort |

## Notes

- The tool owner's existing detail-filling tool consumes the districts and roads produced here.
- Work lands in the `mapgen` workstream; follow `tools/mapgen/CLAUDE.md` (determinism, feature identity,
  contract-first docs, colour-blind-safe visuals).
