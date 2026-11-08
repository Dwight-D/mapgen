"""Heightmap synthesis (spec 6.2). All heights in metres, float32, row 0 = north."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree

from . import noise, raster
from .geometry import MapGeometry, _resample
from .raster import Grid
from .schema import MapSpec


# Water planes sit this fraction of water_depth above the carved bed (bed = bank - water_depth),
# leaving a small freeboard below the banks.
WATER_FILL = 0.75


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
        n = noise.fbm2_grid(xs / 300.0, ys / 300.0, spec.seed + 1000 + r.seed_key, octaves=4)
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
        rg = noise.ridged_fbm2_grid(xs / 250.0, ys / 250.0, spec.seed + 2000 + r.seed_key, octaves=5)
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
        bank = ndimage.gaussian_filter(h, sigma=_sigma(80.0, grid))
        depth = t.water_depth
        for r in water_regions:
            m = rmask(r.index)
            hard = raster.hard(m)
            if not hard.any():
                continue
            # lakes have a flat surface just below the low shoreline
            surface = float(np.percentile(bank[hard], 10)) - depth
            lake_surface[r.index] = surface + WATER_FILL * depth
            w = raster.soft(m, 20.0, grid)
            h = h + (surface - h) * w
            h = np.where(hard, np.minimum(h, surface), h)
        for ln in rivers:
            m = raster.mask(geoms.lines[ln.index].area, grid)
            hard = raster.hard(m)
            w = raster.soft(m, ln.width / 2.0 + 10.0, grid)
            pts, _ = _resample(np.asarray(geoms.lines[ln.index].centreline.coords), 10.0)
            col, row = grid.to_pixel(pts[:, 0], pts[:, 1])
            b = ndimage.map_coordinates(bank, [row - 0.5, col - 0.5], order=1, mode="nearest")
            if b[0] < b[-1]:
                prof = np.minimum.accumulate(b[::-1])[::-1]
            else:
                prof = np.minimum.accumulate(b)
            prof = prof - depth
            river_surface[ln.index] = (prof + WATER_FILL * depth).astype(np.float32)
            sel = w > 1e-3
            surface = np.zeros_like(h)
            tree = cKDTree(pts)
            _, idx = tree.query(np.column_stack([X[sel], Y[sel]]))
            surface[sel] = prof[idx]
            h = np.where(sel, h + (surface - h) * w, h)
            h = np.where(hard, np.minimum(h, surface), h)

    # 9. finish
    h = ndimage.gaussian_filter(h, sigma=1.0)
    h = np.clip(h, 0.0, t.height_max).astype(np.float32)
    return Heightmap(h=h, grid=grid, lake_surface=lake_surface, river_surface=river_surface)
