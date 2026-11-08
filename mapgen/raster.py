"""Grid helper (world <-> pixel) and geometry -> raster masks. Row 0 is the north edge."""
from __future__ import annotations

from typing import TYPE_CHECKING, List, Tuple

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage
from shapely import affinity
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

from .schema import MapSpec

if TYPE_CHECKING:
    from .geometry import MapGeometry


class Grid:
    """Maps the world rectangle [0,size_x] x [0,size_y] (metres, origin SW) onto cols x rows pixels (row 0 = north)."""

    def __init__(self, size_x: float, size_y: float, cols: int, rows: int):
        self.size_x = float(size_x)
        self.size_y = float(size_y)
        self.cols = int(cols)
        self.rows = int(rows)

    @property
    def shape(self) -> Tuple[int, int]:
        return (self.rows, self.cols)

    @property
    def cell_size_x(self) -> float:
        return self.size_x / self.cols

    @property
    def cell_size_y(self) -> float:
        return self.size_y / self.rows

    def to_pixel(self, x, y):
        """World metres -> continuous pixel coords (col, row); cell centres are at +0.5."""
        x = np.asarray(x, dtype=np.float64) if not np.isscalar(x) else x
        y = np.asarray(y, dtype=np.float64) if not np.isscalar(y) else y
        return x / self.size_x * self.cols, (self.size_y - y) / self.size_y * self.rows

    def to_world(self, col, row):
        """Continuous pixel coords -> world metres (x, y)."""
        col = np.asarray(col, dtype=np.float64) if not np.isscalar(col) else col
        row = np.asarray(row, dtype=np.float64) if not np.isscalar(row) else row
        return col / self.cols * self.size_x, self.size_y - row / self.rows * self.size_y

    def transform_geometry(self, geom: BaseGeometry) -> BaseGeometry:
        """World-space geometry -> pixel-space geometry."""
        return affinity.affine_transform(
            geom, [self.cols / self.size_x, 0.0, 0.0, -self.rows / self.size_y, 0.0, float(self.rows)])


def _polygons(geom: BaseGeometry) -> List[Polygon]:
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if hasattr(geom, "geoms"):
        out: List[Polygon] = []
        for g in geom.geoms:
            out.extend(_polygons(g))
        return out
    return []


def mask(geom: BaseGeometry, grid: Grid, supersample: int = 2) -> np.ndarray:
    """Anti-aliased coverage in [0,1] (float32, shape (rows, cols)) of a polygonal geometry."""
    ss = int(supersample)
    w, h = grid.cols * ss, grid.rows * ss
    img = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(img)
    pix = grid.transform_geometry(geom)
    # PIL addresses pixel i at coordinate i, so shift continuous coordinates by half a (super)pixel.
    def conv(coords) -> List[Tuple[float, float]]:
        return [(x * ss - 0.5, y * ss - 0.5) for x, y in coords]

    for poly in _polygons(pix):
        draw.polygon(conv(poly.exterior.coords), fill=255)
        for hole in poly.interiors:
            draw.polygon(conv(hole.coords), fill=0)
    arr = np.asarray(img, dtype=np.float32) / 255.0
    return arr.reshape(grid.rows, ss, grid.cols, ss).mean(axis=(1, 3)).astype(np.float32)


def hard(m: np.ndarray) -> np.ndarray:
    return m >= 0.5


def soft(m: np.ndarray, radius_m: float, grid: Grid) -> np.ndarray:
    """Gaussian-blurred mask (sigma = radius in pixels / 2) for smooth falloffs."""
    sigma = max(radius_m / grid.cell_size_x / 2.0, 1e-6)
    return ndimage.gaussian_filter(m.astype(np.float32), sigma=sigma).astype(np.float32)


def edt_inside(mask_bool: np.ndarray, grid: Grid) -> np.ndarray:
    """Distance in metres from each inside cell to the nearest outside cell (0 outside)."""
    return ndimage.distance_transform_edt(mask_bool, sampling=(grid.cell_size_y, grid.cell_size_x)).astype(np.float32)


def type_grid(spec: MapSpec, geoms: MapGeometry, grid: Grid) -> np.ndarray:
    """int16 grid of the topmost region index per cell (-1 for none), using hard masks in list order."""
    out = np.full(grid.shape, -1, dtype=np.int16)
    for i, g in enumerate(geoms.regions):
        if g.is_empty:
            continue
        out[hard(mask(g, grid))] = i
    return out
