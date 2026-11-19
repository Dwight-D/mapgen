"""Terrain-aware flowgraph check (design: mapgen/FLOWGRAPH_SPEC.md section 12).

Computes the movement-cost grid the way the analysis stage does (geometry, heightmap, `analysis.build_cost`; no
analysis passes, no files written) and floods walkable (finite-cost) cells from each node's disc with every other
node's disc as a wall. The discs the flood touches are the node's terrain neighbours.
"""
from __future__ import annotations

import math
from typing import Dict, List, Tuple

import numpy as np
from scipy import ndimage

from . import analysis, geometry, height
from .flowgraph import Flowgraph
from .flowgraph_check import MIN_R, spec_features
from .raster import Grid
from .schema import MapSpec

POINT_R = 25.0
_STRUCT = np.ones((3, 3), dtype=bool)


def walkable_grid(spec: MapSpec) -> Tuple[Grid, np.ndarray]:
    """Walkable mask (finite movement cost) on the analysis grid (spec.analysis.cell metres per cell)."""
    geoms = geometry.build_geometries(spec)
    hm = height.synthesize(spec, geoms)
    a = spec.analysis
    grid = Grid(spec.size[0], spec.size[1], int(math.ceil(spec.size[0] / a.cell)), int(math.ceil(spec.size[1] / a.cell)))
    return grid, np.isfinite(analysis.build_cost(spec, geoms, hm, grid))


def _disc(grid: Grid, at: Tuple[float, float], r: float) -> np.ndarray:
    xs = (np.arange(grid.cols) + 0.5) * grid.cell_size_x
    ys = grid.size_y - (np.arange(grid.rows) + 0.5) * grid.cell_size_y
    X, Y = np.meshgrid(xs, ys)
    m = (X - at[0]) ** 2 + (Y - at[1]) ** 2 <= r * r
    if not m.any():  # disc smaller than a cell: take the cell containing the centre
        c, rr = grid.to_pixel(at[0], at[1])
        m[min(max(int(rr), 0), grid.rows - 1), min(max(int(c), 0), grid.cols - 1)] = True
    return m


def node_discs(g: Flowgraph, spec: MapSpec, grid: Grid) -> Dict[str, np.ndarray]:
    """Disc mask per bound node present in the spec: POI r (min 30 m; bases 120 m), `points` 25 m."""
    feats = spec_features(spec)
    out: Dict[str, np.ndarray] = {}
    for n in g.nodes:
        f = feats.get(n.bind)
        if f is None:
            continue
        r = POINT_R if f.poi_type is None else max(f.r, MIN_R)
        out[n.id] = _disc(grid, f.at, r)
    return out


def terrain_neighbours(walk: np.ndarray, discs: Dict[str, np.ndarray]) -> Tuple[Dict[str, set], List[Tuple[str, str]]]:
    """(node id -> set of terrain-neighbour ids, overlapping disc pairs). Overlapping discs count as adjacent."""
    nbrs: Dict[str, set] = {k: set() for k in discs}
    overlaps: List[Tuple[str, str]] = []
    ids = list(discs)
    for i, x in enumerate(ids):
        for y in ids[i + 1:]:
            if (discs[x] & discs[y]).any():
                overlaps.append((x, y))
                nbrs[x].add(y)
                nbrs[y].add(x)
    for x in ids:
        walls = np.zeros(walk.shape, bool)
        for y in ids:
            if y != x:
                walls |= discs[y]
        passable = (walk & ~walls) | discs[x]
        # a disc seed on impassable ground still floods out through walkable neighbours only
        labels, _ = ndimage.label(passable, structure=_STRUCT)
        seeds = np.unique(labels[discs[x] & passable])
        seeds = seeds[seeds > 0]
        if seeds.size == 0:
            continue
        region = np.isin(labels, seeds)
        reach = ndimage.binary_dilation(region, structure=_STRUCT)
        for y in ids:
            if y != x and (reach & discs[y] & walk).any():
                nbrs[x].add(y)
                nbrs[y].add(x)
    return nbrs, overlaps


def terrain_findings(g: Flowgraph, spec: MapSpec) -> Tuple[List[str], List[str], List[str]]:
    """(terrain-missing lines, terrain-extra lines, notes)."""
    grid, walk = walkable_grid(spec)
    discs = node_discs(g, spec, grid)
    nbrs, overlaps = terrain_neighbours(walk, discs)
    order = {n.id: i for i, n in enumerate(g.nodes)}
    wanted = set()
    missing: List[str] = []
    for e in g.edges:
        wanted.add(frozenset((e.a, e.b)))
        if e.a in discs and e.b in discs and e.b not in nbrs[e.a]:
            missing.append("terrain-missing edge %s - %s%s" % (e.a, e.b, " (%s)" % e.name if e.name else ""))
    pairs = sorted({frozenset((a, b)) for a in nbrs for b in nbrs[a]} - wanted,
                   key=lambda k: sorted(order[x] for x in k))
    extra = []
    for k in pairs:
        a, b = sorted(k, key=lambda x: order[x])
        extra.append("terrain-extra edge %s - %s" % (a, b))
    notes = ["note: discs of %s and %s overlap (treated as adjacent)" % tuple(sorted(p, key=lambda x: order[x]))
             for p in overlaps]
    return missing, extra, notes
