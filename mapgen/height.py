"""Heightmap synthesis (spec 6.2). All heights in metres, float32, row 0 = north."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree

from . import noise, raster
from .geometry import MapGeometry, _resample, region_key
from .raster import Grid
from .schema import MapSpec


# Water follows the terrain. A water surface sits WATER_FREEBOARD m below the lowest local ground (the ground blurred
# WATER_LOCAL_SIGMA m) on its banks: for a lake, the ground in a ring of the bank probe width just outside the lake
# (island shores included); for a river, per centreline sample, the ground at the centreline and the bank probe
# distance outside each (widest) bank edge. The bank probe is WATER_BANK_PROBE m, at least 2 heightmap cells.
# The bed is carved WATER_FILL * water_depth below the surface.
WATER_FILL = 0.75
WATER_LOCAL_SIGMA = 4.0
WATER_BANK_PROBE = 2.0
WATER_FREEBOARD = 0.8
# Shore shaping. Under water the bed shelves from WATER_EDGE_DEPTH at the shoreline to the full depth along a
# smoothstep over the shelf width: LAKE_SHELF m for a lake, RIVER_SHELF x the half width for a river. A river ending in a
# lake carries its bed on into the lake bed along its mouth channel (geometry.mouth_channels): the river's cross-section
# depth, fading to nothing over the channel's length, wherever it is deeper than the lake's own shelf. Above water every bank
# (lake shore, island, river bank) is capped by the shore profile `shore_cap`: it rises SHORE_SLOPE per metre from the
# waterline up to SHORE_RISE m above the water, then steepens smoothly to SHORE_STEEP per metre; the cap meets the
# untouched ground through a smooth minimum of width SHORE_BLEND m.
LAKE_SHELF = 8.0
RIVER_SHELF = 0.7
SHORE_SLOPE = 0.35
SHORE_STEEP = 1.0
SHORE_RISE = 3.0
SHORE_BLEND = 0.5
# The signed shoreline distances behind the shelf and the shore cap are blurred with this sigma (m).
SHORE_SMOOTH = 1.5
# Bank ground never drops below the surface + min(WATER_BANK_LIP, shore_cap) (unless the ground was already lower).
WATER_BANK_LIP = 0.3
# The final blur never leaves a water cell's ground less than this far below its surface.
WATER_EDGE_DEPTH = 0.1
# Junctions (a tributary joining its parent, a river entering a lake): both surfaces sit at the lower of the two
# levels within RIVER_POOL m of the junction and blend back to their own level over the next RIVER_JOIN_RAMP m.
RIVER_POOL = 10.0
RIVER_JOIN_RAMP = 30.0
# Carving interpolates the river surface along the centreline at this step (m), so the bed and banks have no terraces.
RIVER_CARVE_STEP = 0.5


@dataclass
class Heightmap:
    h: np.ndarray      # float32 metres, shape (res, res)
    grid: Grid
    # Water surface heights in metres: region index -> lake surface; line index -> per-point
    # surface along the line's centreline resampled at 10 m (same points as lines.json).
    lake_surface: Dict[int, float] = field(default_factory=dict)
    river_surface: Dict[int, np.ndarray] = field(default_factory=dict)


def smoothstep(e0: float, e1: float, x):
    t = np.clip((np.asarray(x, dtype=np.float64) - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def shore_cap(d):
    """Height (m) above the water that the shore profile allows at distance `d` (m) from the waterline: slope
    SHORE_SLOPE up to SHORE_RISE (at d0 = SHORE_RISE / SHORE_SLOPE), then the slope blends (smoothstep) to
    SHORE_STEEP over d0..1.6 d0."""
    d = np.maximum(np.asarray(d, dtype=np.float64), 0.0)
    d0 = SHORE_RISE / SHORE_SLOPE
    a, b = d0, 1.6 * d0
    t = np.clip((d - a) / (b - a), 0.0, 1.0)
    extra = np.where(d > b, 0.5 * (b - a) + (d - b), (b - a) * (t ** 3 - 0.5 * t ** 4))
    return SHORE_SLOPE * d + (SHORE_STEEP - SHORE_SLOPE) * extra


def _smin(a, b, k: float):
    """Polynomial smooth minimum of width k (equals min(a, b) where |a - b| >= k)."""
    t = np.clip(0.5 + 0.5 * (b - a) / k, 0.0, 1.0)
    return b + (a - b) * t - k * t * (1.0 - t)


def sample(hm: Heightmap, x, y) -> np.ndarray:
    """Bilinear height (metres) at world coordinates (arrays or scalars)."""
    col, row = hm.grid.to_pixel(x, y)
    col = np.asarray(col, dtype=np.float64) - 0.5
    row = np.asarray(row, dtype=np.float64) - 0.5
    shape = col.shape
    out = ndimage.map_coordinates(hm.h, [row.reshape(-1), col.reshape(-1)], order=1, mode="nearest")
    return out.reshape(shape)


def slope_grid(h: np.ndarray, grid: Grid) -> np.ndarray:
    """Rise over run (dimensionless) from a height array on `grid`."""
    gy, gx = np.gradient(h.astype(np.float64), grid.cell_size_y, grid.cell_size_x)
    return np.hypot(gx, gy).astype(np.float32)


def _sigma(metres: float, grid: Grid) -> float:
    return max(metres / grid.cell_size_x, 1e-6)


def _coords(grid: Grid):
    cols = (np.arange(grid.cols) + 0.5) * grid.cell_size_x
    ys = grid.size_y - (np.arange(grid.rows) + 0.5) * grid.cell_size_y
    return np.meshgrid(cols, ys)


def _local_bank(h_local: np.ndarray, grid: Grid, pts: np.ndarray, reach: float) -> np.ndarray:
    """Per centreline point: the lowest of h_local at the point and `reach` m to either side across the line."""
    tx = np.gradient(pts[:, 0])
    ty = np.gradient(pts[:, 1])
    n = np.hypot(tx, ty)
    n[n == 0] = 1.0
    nx, ny = -ty / n, tx / n
    out = None
    for s in (-reach, 0.0, reach):
        col, row = grid.to_pixel(pts[:, 0] + nx * s, pts[:, 1] + ny * s)
        v = ndimage.map_coordinates(h_local, [row - 0.5, col - 0.5], order=1, mode="nearest")
        out = v if out is None else np.minimum(out, v)
    return out


def _arc_distance_to(s: np.ndarray, sel: np.ndarray) -> np.ndarray:
    """Per sample: arc-length distance to the nearest sample where `sel` is true."""
    return np.abs(s[:, None] - s[sel][None, :]).min(axis=1)


def _lower_to(ws: np.ndarray, level: float, d: np.ndarray) -> np.ndarray:
    """Lower a river surface to `level` within RIVER_POOL m of a junction (`d` = distance to it per sample),
    blending back to the surface's own level over the next RIVER_JOIN_RAMP m. Never raises it."""
    t = np.clip((d - RIVER_POOL) / RIVER_JOIN_RAMP, 0.0, 1.0)
    return np.where(ws > level, level + (ws - level) * t, ws)


def synthesize(spec: MapSpec, geoms: MapGeometry) -> Heightmap:
    res = spec.output.heightmap_res
    grid = Grid(spec.size[0], spec.size[1], res, res)
    t = spec.terrain
    X, Y = _coords(grid)
    xs, ys = X[0], Y[:, 0]
    types = spec.types

    # 1. base relief
    h = t.base_height + t.relief * noise.fbm2_grid(xs / t.relief_scale, ys / t.relief_scale, spec.seed, octaves=5)
    if t.detail > 0:
        h = h + t.detail * noise.fbm2_grid(xs / t.detail_scale, ys / t.detail_scale, spec.seed + 500, octaves=4)

    masks = {}

    def rmask(i):
        if i not in masks:
            masks[i] = raster.mask(geoms.regions[i], grid)
        return masks[i]

    def modes(mode):
        return [r for r in spec.regions
                if types.regions[r.type].get("height_mode") == mode and not geoms.regions[r.index].is_empty]

    # 2. hills
    for r in modes("add_noise"):
        sm = raster.soft(rmask(r.index), 120.0, grid)
        n = noise.fbm2_grid(xs / 300.0, ys / 300.0, spec.seed + 1000 + region_key(r), octaves=4)
        h = h + (r.height or 0.0) * sm * (0.5 + 0.5 * n)

    # 3. mountains (ridge) combine by per-cell maximum, kept separate from the ground so passes can carve saddles
    mt = np.zeros_like(h)
    for r in modes("ridge"):
        hard = raster.hard(rmask(r.index))
        if not hard.any():
            continue
        d = raster.edt_inside(hard, grid)
        dmax = float(d.max())
        if dmax <= 0:
            continue
        falloff = 0.6 * dmax
        tt = smoothstep(0.0, falloff, d) ** 0.8
        rg = noise.ridged_fbm2_grid(xs / 250.0, ys / 250.0, spec.seed + 2000 + region_key(r), octaves=5)
        hgt = r.height or 0.0
        # foothill apron: a soft skirt around the mask that fades out toward the summit so the profile is continuous
        apron = 0.15 * hgt * raster.soft(rmask(r.index), 180.0, grid) * (1.0 - tt)
        mt = np.maximum(mt, hgt * tt * (0.55 + 0.45 * rg) + apron)
    # 4. passes
    for c in spec.crossings:
        if c.type != "pass" or geoms.crossings[c.index].is_empty:
            continue
        sm = raster.soft(raster.mask(geoms.crossings[c.index], grid), c.r, grid)
        mt = mt * (1.0 - 0.85 * np.clip(sm, 0.0, 1.0))
    h = h + mt

    # 5. plateaus
    for r in modes("raise_flat"):
        hard = raster.hard(rmask(r.index))
        if not hard.any():
            continue
        target = float(np.median(h[hard])) + (r.height or 0.0)
        w = raster.soft(rmask(r.index), r.edge or 40.0, grid)
        h = h + (target - h) * w
    # 6. towns
    for r in modes("flatten"):
        hard = raster.hard(rmask(r.index))
        if not hard.any():
            continue
        target = float(np.median(h[hard]))
        w = raster.soft(rmask(r.index), r.edge or 30.0, grid)
        h = h + (target - h) * w
    # 6b. ramps: relax the ground toward a strong blur so a plateau edge or cliff becomes a walkable slope
    for c in spec.crossings:
        if c.type != "ramp" or geoms.crossings[c.index].is_empty:
            continue
        w = np.clip(raster.soft(raster.mask(geoms.crossings[c.index], grid), c.r, grid), 0.0, 1.0)
        h = h + (ndimage.gaussian_filter(h, sigma=_sigma(c.r, grid)) - h) * w

    # 7. roads
    roads = [ln for ln in spec.lines if types.lines[ln.type].get("height_mode") == "flatten_line"
             and not geoms.lines[ln.index].area.is_empty]
    if roads:
        hs = ndimage.gaussian_filter(h, sigma=_sigma(60.0, grid))
        for ln in roads:
            w = raster.soft(raster.mask(geoms.lines[ln.index].area, grid), ln.width, grid)
            w = w * float(types.lines[ln.type].get("flatten", 1.0))
            h = h + (hs - h) * w

    # 8. water
    water_regions = modes("water")
    rivers = [ln for ln in spec.lines if types.lines[ln.type].get("height_mode") == "water"
              and not geoms.lines[ln.index].area.is_empty]
    lake_surface: Dict[int, float] = {}
    river_surface: Dict[int, np.ndarray] = {}
    if water_regions or rivers:
        depth = t.water_depth
        # all water levels come from the ground before any water is carved
        h_local = ndimage.gaussian_filter(h, sigma=_sigma(WATER_LOCAL_SIGMA, grid))
        probe_m = max(WATER_BANK_PROBE, 2.0 * grid.cell_size_x)
        probe_px = max(1, int(round(probe_m / grid.cell_size_x)))
        lake_hard: Dict[int, np.ndarray] = {}
        for r in water_regions:
            hard = raster.hard(rmask(r.index))
            if not hard.any():
                continue
            # a lake has a flat surface below the lowest ground on its shore (island shores included)
            ring = ndimage.binary_dilation(hard, iterations=probe_px) & ~hard
            if not ring.any():
                ring = hard
            lake_surface[r.index] = float(h_local[ring].min()) - WATER_FREEBOARD
            lake_hard[r.index] = hard
        # touching or overlapping lakes are one water body: they share the lowest of their levels
        lake_ids = sorted(lake_hard)
        grown = {li: ndimage.binary_dilation(lake_hard[li], iterations=probe_px) for li in lake_ids}
        changed = True
        while changed:
            changed = False
            for i, li in enumerate(lake_ids):
                for lj in lake_ids[i + 1:]:
                    if lake_surface[li] != lake_surface[lj] and (grown[li] & lake_hard[lj]).any():
                        lake_surface[li] = lake_surface[lj] = min(lake_surface[li], lake_surface[lj])
                        changed = True
        # rivers: per centreline sample, below the lowest local ground on both banks; the surface follows the terrain
        # and may run uphill
        samples: Dict[int, list] = {}
        for ln in rivers:
            pts, s = _resample(np.asarray(geoms.lines[ln.index].centreline.coords), 10.0)
            half = ln.width / 2.0 * (1.0 + 0.6 * max(ln.width_var, 0.0))
            ws = _local_bank(h_local, grid, pts, half + probe_m) - WATER_FREEBOARD
            col, row = grid.to_pixel(pts[:, 0], pts[:, 1])
            ci = np.clip(np.floor(col).astype(int), 0, grid.cols - 1)
            ri = np.clip(np.floor(row).astype(int), 0, grid.rows - 1)
            # a sample within the bank probe of a lake is at the lake (a centreline ends just past the shore)
            inside = {li: lg[ri, ci] for li, lg in grown.items() if lg[ri, ci].any()}
            samples[ln.index] = [pts, s, ws, inside]
        # river mouths: a lake whose shore is higher than an entering river's water near the mouth drops to it
        for ln in rivers:
            pts, s, ws, inside = samples[ln.index]
            for li, ins in inside.items():
                d = _arc_distance_to(s, ins)
                near = ~ins & (d <= RIVER_POOL)
                if near.any():
                    lake_surface[li] = min(lake_surface[li], float(ws[near].min()))
        for ln in rivers:
            pts, s, ws, inside = samples[ln.index]
            for li, ins in inside.items():
                d = _arc_distance_to(s, ins)
                ws = np.where(ins, lake_surface[li], _lower_to(ws, lake_surface[li], d))
            samples[ln.index][2] = ws
        # confluences: the tributary's joined end and the parent meet at the lower of the two surfaces
        by_id = {ln.id: ln for ln in rivers}
        for ln in rivers:
            parent = by_id.get(ln.join) if ln.join is not None else None
            if parent is None:
                continue
            pts, s, ws, _ = samples[ln.index]
            ppts, _, pws, _ = samples[parent.index]
            k = 0 if ln.join_end == "start" else len(pts) - 1
            dp = np.hypot(ppts[:, 0] - pts[k, 0], ppts[:, 1] - pts[k, 1])
            near = dp <= RIVER_POOL
            if not near.any():
                near = dp == dp.min()
            level = min(float(ws[k]), float(pws[near].min()))
            dt = np.abs(s - s[k])
            samples[ln.index][2] = _lower_to(ws, level, dt)
            samples[parent.index][2] = _lower_to(pws, level, dp)

        cs = (grid.cell_size_y, grid.cell_size_x)
        wet = np.zeros(h.shape, dtype=bool)
        water_top = np.full(h.shape, np.inf, dtype=np.float64)  # water surface over each water cell
        shelf = np.full(h.shape, np.inf, dtype=np.float64)      # shelf width (m) of each water cell
        bodies = []  # (hard mask, surface over it) per lake and per river
        for li in lake_ids:
            hard = lake_hard[li]
            water_top = np.where(hard, np.minimum(water_top, lake_surface[li]), water_top)
            shelf = np.where(hard, np.minimum(shelf, LAKE_SHELF), shelf)
            wet |= hard
            bodies.append((hard, np.where(hard, lake_surface[li], np.inf)))
        lake_wet = wet.copy()
        for ln in rivers:
            pts, s, ws, _ = samples[ln.index]
            river_surface[ln.index] = ws.astype(np.float32)
            hard = raster.hard(raster.mask(geoms.lines[ln.index].area, grid))
            if not hard.any():
                continue
            fpts, fs = _resample(np.asarray(geoms.lines[ln.index].centreline.coords), RIVER_CARVE_STEP)
            fws = np.interp(fs, s, ws)
            _, idx = cKDTree(fpts).query(np.column_stack([X[hard], Y[hard]]))
            water = np.full(h.shape, np.inf, dtype=np.float64)
            water[hard] = fws[idx]
            water_top = np.minimum(water_top, water)
            shelf = np.where(hard, np.minimum(shelf, RIVER_SHELF * ln.width / 2.0), shelf)
            wet |= hard
            bodies.append((hard, water))

        bank_floor = np.full(h.shape, -np.inf, dtype=np.float64)  # lowest allowed ground beside the water
        if wet.any():
            def signed(hard):
                # signed distance (m) to the shoreline of a water mask, negative under water; away from the shoreline
                # it blends (smoothstep over 0..2 SHORE_SMOOTH) to its blur with sigma SHORE_SMOOTH, so the raster
                # staircase leaves no facets while the waterline itself stays exact
                half_cell = 0.5 * grid.cell_size_x
                sd = np.where(hard, half_cell - ndimage.distance_transform_edt(hard, sampling=cs),
                              ndimage.distance_transform_edt(~hard, sampling=cs) - half_cell)
                blur = ndimage.gaussian_filter(sd, sigma=_sigma(SHORE_SMOOTH, grid))
                w = smoothstep(0.0, 2.0 * SHORE_SMOOTH, np.abs(sd))
                return sd + (blur - sd) * w

            # under water: the bed shelves from the shoreline to the full depth; nothing is raised
            sd_wet = signed(wet)
            d_in = np.maximum(-sd_wet, 0.0)
            d_out = np.maximum(sd_wet, 0.0)
            ramp = smoothstep(0.0, 1.0, d_in / np.where(wet, shelf, 1.0))
            dep = np.maximum(WATER_EDGE_DEPTH, WATER_FILL * depth * ramp)
            # mouth channels: the river's bed runs on into the lake bed, so the bed has no bar at the mouth
            for ln in rivers:
                half = ln.width / 2.0
                for mc in geoms.lines[ln.index].mouths:
                    near = lake_wet & raster.hard(raster.mask(mc.buffer(half), grid))
                    if not near.any():
                        continue
                    mpts, ms = _resample(np.asarray(mc.coords), RIVER_CARVE_STEP)
                    dist, idx = cKDTree(mpts).query(np.column_stack([X[near], Y[near]]))
                    across = smoothstep(0.0, 1.0, (half - dist) / (RIVER_SHELF * half))
                    along = 1.0 - smoothstep(0.0, float(ms[-1]), ms[idx])
                    dep[near] = np.maximum(dep[near], WATER_FILL * depth * across * along)
            # above water: each dry cell is capped by the lowest shore profile over all water bodies, each measured
            # from the body's nearest water cell (a min of continuous caps, so no step where two bodies' reaches meet);
            # every body's own banks (within the bank probe) keep the ground at least at its surface + the lip, the lip
            # following the shore profile from the nearest water of any body
            lip = np.minimum(WATER_BANK_LIP, shore_cap(d_out))
            top_cap = np.full(h.shape, np.inf, dtype=np.float64)
            top_lip = np.full(h.shape, -np.inf, dtype=np.float64)
            for hard, top in bodies:
                _, (ir, ic) = ndimage.distance_transform_edt(~hard, return_indices=True)
                d_k = np.maximum(signed(hard), 0.0)
                top_cap = np.minimum(top_cap, top[ir, ic] + shore_cap(d_k))
                top_lip = np.maximum(top_lip, np.where(d_k <= probe_m, top[ir, ic] + lip, -np.inf))
            floor = np.minimum(h, top_lip)
            dry = np.maximum(_smin(h, top_cap, SHORE_BLEND), floor)
            h = np.where(wet, np.minimum(h, water_top - dep), dry)
            # the finishing blur holds banks next to the water at their floor
            bank_floor = np.where(~wet & (d_out <= 4.0 * grid.cell_size_x), floor, -np.inf)

    # 9. finish
    h = ndimage.gaussian_filter(h, sigma=1.0)
    if water_regions or rivers:
        # the blur must neither lift a water cell's ground out of the water next to a high bank nor sink a bank
        # below the water next to a deep bed
        h = np.where(wet, np.minimum(h, water_top - WATER_EDGE_DEPTH), np.maximum(h, bank_floor))
    h = np.clip(h, 0.0, t.height_max).astype(np.float32)
    return Heightmap(h=h, grid=grid, lake_surface=lake_surface, river_surface=river_surface)
