"""Gameplay analysis (spec 6.3): cost grid, travel times, territory, objective balance, traffic, openness."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy import ndimage
from shapely.geometry import Point
from shapely.ops import unary_union
from skimage.graph import MCP_Geometric

from . import height as heightmod
from . import raster
from .geometry import MapGeometry
from .height import Heightmap
from .raster import Grid
from .schema import MapSpec

INF = float("inf")
CHOKE_SEPARATION = 150.0    # metres between reported chokepoints
CHOKE_BASE_EXCLUDE = 250.0  # metres around bases ignored when ranking chokepoints (minimum)
CHOKE_BASE_FRACTION = 0.15  # ... or this fraction of the largest base-to-base distance
CHOKE_RADIUS = 120.0        # metres: neighbourhood of the constriction measure
CHOKE_COST = 2.0            # cells at or above this cost (or impassable) count as constricting
CHOKE_NAME_RADIUS = 200.0


@dataclass
class AnalysisResult:
    grid: Grid
    cost: np.ndarray                         # multiplier on travel time per metre (inf = impassable)
    teams: List[str]                         # team ids in spec order
    times: Dict[str, np.ndarray]             # seconds from each team base (inf = unreachable)
    owner: np.ndarray                        # int8 team index of fastest team, -1 unreachable
    contested: np.ndarray                    # bool
    traffic: np.ndarray                      # int32 path hit counts (dilated)
    chokepoints: List[dict]                  # {rank, x, y, count, constriction, score, near, row, col}
    openness: np.ndarray                     # float32 in [0,1]
    paths: Dict[str, np.ndarray]             # "A-B" -> (n,2) array of (row, col) base-to-base path
    path_openness: Dict[str, float]
    base_times: Dict[str, Optional[float]]   # "A-B" -> seconds (None = unreachable)
    shares: Dict[str, float]                 # team id / "contested" / "unreachable" -> fraction of map area
    objectives: List[dict]
    warnings: List[str] = field(default_factory=list)
    fair: Optional[np.ndarray] = None        # bool: reachable by all teams with time spread <= balance_tolerance
    fair_share: float = 0.0                  # fraction of the map area that is fair
    openness_mean: float = 0.0               # map-wide mean openness
    team_openness: Dict[str, float] = field(default_factory=dict)   # mean openness over each team's territory
    route_constriction: Dict[str, float] = field(default_factory=dict)  # "A-B" -> max constriction along the route


def _cell(grid: Grid, x: float, y: float) -> Tuple[int, int]:
    c, r = grid.to_pixel(x, y)
    return (int(min(max(math.floor(r), 0), grid.rows - 1)), int(min(max(math.floor(c), 0), grid.cols - 1)))


def build_cost(spec: MapSpec, geoms: MapGeometry, hm: Heightmap, grid: Grid) -> np.ndarray:
    types = spec.types
    cost = np.ones(grid.shape, dtype=np.float64)
    for i, reg in enumerate(spec.regions):
        if geoms.regions[i].is_empty:
            continue
        cost[raster.hard(raster.mask(geoms.regions[i], grid))] = types.regions[reg.type]["cost"]
    line_masks = []
    for i, ln in enumerate(spec.lines):
        if geoms.lines[i].area.is_empty:
            line_masks.append(np.zeros(grid.shape, bool))
        else:
            line_masks.append(raster.hard(raster.mask(geoms.lines[i].area, grid)))
    for i, ln in enumerate(spec.lines):
        if types.lines[ln.type]["cost"] == INF:
            cost[line_masks[i]] = INF
    for i, ln in enumerate(spec.lines):
        c = types.lines[ln.type]["cost"]
        if c != INF:
            cost[line_masks[i] & np.isfinite(cost)] = c
    in_crossing = np.zeros(grid.shape, bool)
    for i, cr in enumerate(spec.crossings):
        if geoms.crossings[i].is_empty:
            continue
        m = raster.hard(raster.mask(geoms.crossings[i], grid))
        cost[m] = types.crossings[cr.type]["cost"]
        in_crossing |= m
    # slope penalty
    cols = (np.arange(grid.cols) + 0.5) * grid.cell_size_x
    ys = grid.size_y - (np.arange(grid.rows) + 0.5) * grid.cell_size_y
    X, Y = np.meshgrid(cols, ys)
    hs = heightmod.sample(hm, X, Y)
    slope = heightmod.slope_grid(hs, grid)
    # crossings override passability, so inside a crossing disc steep slopes only add the capped penalty
    eff = np.where(in_crossing, np.minimum(slope, 0.7), slope)
    cost = cost * (1.0 + 2.0 * np.maximum(0.0, eff - 0.15))
    cost[slope > 0.7] = np.where(in_crossing[slope > 0.7], cost[slope > 0.7], INF)
    return cost


def _nearest_feature(spec: MapSpec, geoms: MapGeometry, x: float, y: float) -> str:
    """Closest named crossing / POI / labelled region or line within CHOKE_NAME_RADIUS of (x, y)."""
    p = Point(x, y)
    best, best_d = None, CHOKE_NAME_RADIUS
    for k, c in enumerate(spec.crossings):
        d = geoms.crossings[k].distance(p) if not geoms.crossings[k].is_empty else INF
        if d <= best_d:
            best, best_d = "%s %s" % (c.type, c.id or "(%d, %d)" % (c.at[0], c.at[1])), d
    for poi in spec.all_pois():
        d = max(0.0, p.distance(Point(poi.at)) - poi.r)
        if d < best_d:
            best, best_d = "%s %s" % (poi.type, poi.id), d
    for i, reg in enumerate(spec.regions):
        if not reg.label or geoms.regions[i].is_empty:
            continue
        d = geoms.regions[i].distance(p)
        if d < best_d:
            best, best_d = "%s %s" % (reg.type, reg.label), d
    for i, ln in enumerate(spec.lines):
        if not ln.label or geoms.lines[i].area.is_empty:
            continue
        d = geoms.lines[i].area.distance(p)
        if d < best_d:
            best, best_d = "%s %s" % (ln.type, ln.label), d
    if best is None:  # no named feature nearby: fall back to the line (road, trail, river...) the point lies on
        for i, ln in enumerate(spec.lines):
            if geoms.lines[i].area.is_empty:
                continue
            d = geoms.lines[i].area.distance(p)
            if d < 1.0 and (best is None or d < best_d):
                best, best_d = "%s %s" % (ln.type, ln.label or ln.id or "#%d" % i), d
    return best or "-"


def analyze(spec: MapSpec, geoms: MapGeometry, hm: Heightmap) -> AnalysisResult:
    a = spec.analysis
    grid = Grid(spec.size[0], spec.size[1], int(math.ceil(spec.size[0] / a.cell)),
                int(math.ceil(spec.size[1] / a.cell)))
    cell_m = 0.5 * (grid.cell_size_x + grid.cell_size_y)
    types = spec.types
    warnings: List[str] = list(spec.warnings)
    cost = build_cost(spec, geoms, hm, grid)
    finite = np.isfinite(cost)
    team_ids = list(spec.teams.keys())
    pois = spec.all_pois()

    # validation: POIs on impassable cells
    for poi in pois:
        r, c = _cell(grid, *poi.at)
        if not finite[r, c]:
            warnings.append("%s '%s' at (%g, %g) is on an impassable cell" % (
                "base" if poi.type == "base" else "POI", poi.id, poi.at[0], poi.at[1]))

    # travel time fields
    near_idx = None
    mcps: Dict[str, MCP_Geometric] = {}
    times: Dict[str, np.ndarray] = {}
    for tid, team in spec.teams.items():
        s = _cell(grid, *team.base)
        if not finite[s]:
            if near_idx is None:
                near_idx = ndimage.distance_transform_edt(~finite, return_distances=False, return_indices=True)
            s = (int(near_idx[0][s]), int(near_idx[1][s]))
        mcp = MCP_Geometric(cost, fully_connected=True)
        cum, _ = mcp.find_costs([s])
        mcps[tid] = mcp
        times[tid] = (cum * cell_m / a.speed).astype(np.float64)

    # territory
    stack = np.stack([times[t] for t in team_ids])
    order = np.argsort(stack, axis=0, kind="stable")
    best = np.take_along_axis(stack, order[:1], axis=0)[0]
    reach = np.isfinite(best)
    owner = np.where(reach, order[0], -1).astype(np.int8)
    if len(team_ids) > 1:
        second = np.take_along_axis(stack, order[1:2], axis=0)[0]
        with np.errstate(invalid="ignore"):
            contested = np.isfinite(second) & reach & ((second - best) < np.maximum(20.0, 0.05 * best))
    else:
        contested = np.zeros(grid.shape, bool)
    total = float(grid.rows * grid.cols)
    shares: Dict[str, float] = {}
    for k, tid in enumerate(team_ids):
        shares[tid] = float(((owner == k) & ~contested).sum()) / total
    shares["contested"] = float(contested.sum()) / total
    shares["unreachable"] = float((~reach).sum()) / total

    # fair zone: reachable by every team with a relative time spread within the tolerance
    tmin, tmax = stack.min(axis=0), stack.max(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        fair = np.isfinite(stack).all(axis=0) & (tmin > 0) & ((tmax - tmin) <= a.balance_tolerance * tmin)

    # base to base
    base_times: Dict[str, Optional[float]] = {}
    paths: Dict[str, np.ndarray] = {}
    traffic = np.zeros(grid.shape, dtype=np.int32)
    base_cell = {t: _cell(grid, *spec.teams[t].base) for t in team_ids}
    for i, ta in enumerate(team_ids):
        for tb in team_ids[i + 1:]:
            tt = float(times[ta][base_cell[tb]])
            key = "%s-%s" % (ta, tb)
            if not math.isfinite(tt):
                base_times[key] = None
                warnings.append("base %s cannot reach base %s" % (ta, tb))
                continue
            base_times[key] = tt
            p = np.asarray(mcps[ta].traceback(base_cell[tb]), dtype=np.int64)
            paths[key] = p
            traffic[p[:, 0], p[:, 1]] += 1

    # objectives
    objectives: List[dict] = []
    for poi in pois:
        if not types.pois[poi.type].get("objective"):
            continue
        cell = _cell(grid, *poi.at)
        tm = {t: float(times[t][cell]) for t in team_ids}
        row = {"id": poi.id, "type": poi.type, "owner": poi.team,
               "times": {t: (v if math.isfinite(v) else None) for t, v in tm.items()},
               "spread": None, "flag": ""}
        flags = []
        vals = list(tm.values())
        if poi.team is None:
            if any(not math.isfinite(v) for v in vals):
                flags.append("UNREACHABLE")
            else:
                lo, hi = min(vals), max(vals)
                spread = (hi - lo) / lo if lo > 0 else (0.0 if hi == 0 else INF)
                row["spread"] = spread
                if spread > a.balance_tolerance:
                    flags.append("IMBALANCED")
        else:
            mine = tm[poi.team]
            if not math.isfinite(mine):
                flags.append("UNREACHABLE")
            if any(v < mine for t, v in tm.items() if t != poi.team):
                flags.append("EXPOSED")
        row["flag"] = ", ".join(flags)
        objectives.append(row)
        for t in team_ids:
            if math.isfinite(tm[t]):
                p = np.asarray(mcps[t].traceback(cell), dtype=np.int64)
                traffic[p[:, 0], p[:, 1]] += 1

    traffic = ndimage.grey_dilation(traffic, size=(3, 3))

    # chokepoints: path count x constriction, ignoring the neighbourhood of bases
    xs = (np.arange(grid.cols) + 0.5) * grid.cell_size_x
    ys = grid.size_y - (np.arange(grid.rows) + 0.5) * grid.cell_size_y
    X, Y = np.meshgrid(xs, ys)
    bad = ((~finite) | (cost >= CHOKE_COST)).astype(np.float64)
    rc = max(1, int(round(CHOKE_RADIUS / cell_m)))
    yy, xx = np.ogrid[-rc:rc + 1, -rc:rc + 1]
    disk = ((xx * xx + yy * yy) <= rc * rc).astype(np.float64)
    constriction = ndimage.convolve(bad, disk / disk.sum(), mode="nearest")
    bases = [spec.teams[t].base for t in team_ids]
    span = max([math.hypot(p[0] - q[0], p[1] - q[1]) for i, p in enumerate(bases) for q in bases[i + 1:]] or [0.0])
    excl_r = max(CHOKE_BASE_EXCLUDE, CHOKE_BASE_FRACTION * span)
    near_base = np.zeros(grid.shape, bool)
    for bx, by in bases:
        near_base |= np.hypot(X - bx, Y - by) < excl_r
    key = np.where(near_base, 0.0, traffic.astype(np.float64) * constriction)
    smooth = ndimage.gaussian_filter(key, sigma=3.0)
    alive = key > 0
    chokes: List[dict] = []
    while len(chokes) < 5 and alive.any():
        cand = np.flatnonzero(alive.ravel())
        o = np.lexsort((-smooth.ravel()[cand], -key.ravel()[cand]))
        k = cand[o[0]]
        r, c = divmod(int(k), grid.cols)
        x, y = float(X[r, c]), float(Y[r, c])
        chokes.append({"rank": len(chokes) + 1, "x": x, "y": y, "count": int(traffic[r, c]),
                       "constriction": float(constriction[r, c]), "score": float(key[r, c]), "row": r, "col": c,
                       "near": _nearest_feature(spec, geoms, x, y)})
        alive &= np.hypot(X - x, Y - y) >= CHOKE_SEPARATION
    route_constriction = {}
    for k, p in paths.items():
        cv = constriction[p[:, 0], p[:, 1]][~near_base[p[:, 0], p[:, 1]]]
        route_constriction[k] = float(cv.max()) if len(cv) else 0.0

    # openness
    los = np.zeros(grid.shape, bool)
    for i, reg in enumerate(spec.regions):
        if types.regions[reg.type].get("los") and not geoms.regions[i].is_empty:
            los |= raster.hard(raster.mask(geoms.regions[i], grid))
    for i, ln in enumerate(spec.lines):
        if types.lines[ln.type].get("los") and not geoms.lines[i].area.is_empty:
            los |= raster.hard(raster.mask(geoms.lines[i].area, grid))
    size = max(1, int(round(100.0 / cell_m)))
    openness = (1.0 - ndimage.uniform_filter(los.astype(np.float32), size=size, mode="nearest")).astype(np.float32)
    path_open = {k: float(openness[p[:, 0], p[:, 1]].mean()) for k, p in paths.items()}
    team_open: Dict[str, float] = {}
    for k, tid in enumerate(team_ids):
        own = (owner == k) & ~contested
        team_open[tid] = float(openness[own].mean()) if own.any() else float("nan")

    # validation: roads/trails crossing water without a bridge/ford (automatic crossings cover them when enabled)
    discs = [g for g in geoms.crossings if not g.is_empty]
    covered = unary_union(discs) if discs else None
    water = []
    for i, ln in enumerate(spec.lines):
        if types.lines[ln.type]["cost"] == INF and types.lines[ln.type].get("height_mode") == "water":
            water.append(("river %s" % (ln.id or "#%d" % i), geoms.lines[i].area))
    for i, reg in enumerate(spec.regions):
        if types.regions[reg.type].get("height_mode") == "water":
            water.append(("lake %s" % (reg.id or reg.label or "#%d" % i), geoms.regions[i]))
    for i, ln in enumerate(spec.lines):
        if types.lines[ln.type]["cost"] == INF:
            continue
        for wname, wgeom in water:
            if wgeom.is_empty or geoms.lines[i].area.is_empty:
                continue
            inter = geoms.lines[i].area.intersection(wgeom)
            if inter.is_empty or inter.area < 1.0:
                continue
            rest = inter.difference(covered) if covered is not None else inter
            if rest.area > 0.1 * inter.area and rest.area > 4.0:
                c = rest.representative_point()
                warnings.append("%s '%s' crosses %s near (%d, %d) with no bridge or ford" % (
                    ln.type, ln.id or "#%d" % i, wname, c.x, c.y))

    return AnalysisResult(grid=grid, cost=cost, teams=team_ids, times=times, owner=owner, contested=contested,
                          traffic=traffic, chokepoints=chokes, openness=openness, paths=paths,
                          path_openness=path_open, base_times=base_times, shares=shares, objectives=objectives,
                          warnings=warnings, fair=fair, fair_share=float(fair.sum()) / total,
                          openness_mean=float(openness.mean()), team_openness=team_open,
                          route_constriction=route_constriction)


def probe_point(spec: MapSpec, geoms: MapGeometry, an: AnalysisResult, x: float, y: float) -> dict:
    """Everything the analysis knows at (x, y): each team's time, the spread, the cost cell, what lies there."""
    r, c = _cell(an.grid, x, y)
    times = {t: float(an.times[t][r, c]) for t in an.teams}
    vals = list(times.values())
    spread = None
    if all(math.isfinite(v) for v in vals):
        lo, hi = min(vals), max(vals)
        spread = (hi - lo) / lo if lo > 0 else (0.0 if hi == 0 else INF)
    p = Point(x, y)
    region = None
    for i in range(len(spec.regions) - 1, -1, -1):
        g = geoms.regions[i]
        if not g.is_empty and g.contains(p):
            reg = spec.regions[i]
            region = "%s %s" % (reg.type, reg.label or reg.id or "#%d" % i)
            break
    on_lines = ["%s %s" % (ln.type, ln.id or "#%d" % i) for i, ln in enumerate(spec.lines)
                if not geoms.lines[i].area.is_empty and geoms.lines[i].area.contains(p)]
    on_crossings = ["%s %s" % (cr.type, cr.id or "#%d" % i) for i, cr in enumerate(spec.crossings)
                    if not geoms.crossings[i].is_empty and geoms.crossings[i].contains(p)]
    return {"x": x, "y": y, "row": r, "col": c, "times": {t: (v if math.isfinite(v) else None) for t, v in times.items()},
            "spread": spread, "cost": float(an.cost[r, c]), "fair": bool(an.fair[r, c]), "region": region,
            "lines": on_lines, "crossings": on_crossings}
