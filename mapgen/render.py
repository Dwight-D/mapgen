"""2D rendering (spec 6.4): map.png and the analysis overlays."""
from __future__ import annotations

import math
import os
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage
from shapely.geometry import LineString, Point
from shapely.ops import unary_union
from skimage import measure

from . import noise, raster
from .analysis import AnalysisResult
from .geometry import MapGeometry
from .palette_check import lightness
from .height import Heightmap
from .raster import Grid
from .schema import MapSpec

SS = 2  # supersampling of the annotation layer

_FONTS: Dict[Tuple[int, bool], ImageFont.ImageFont] = {}


def font(size: int, bold: bool = False):
    key = (size, bold)
    if key not in _FONTS:
        f = None
        for name in (("arialbd.ttf", "DejaVuSans-Bold.ttf") if bold else ("arial.ttf", "DejaVuSans.ttf")):
            try:
                f = ImageFont.truetype(name, size)
                break
            except (OSError, IOError):
                continue
        if f is None:
            f = ImageFont.load_default(size=size)
        _FONTS[key] = f
    return _FONTS[key]


def rgb(hexstr: str) -> Tuple[int, int, int]:
    s = hexstr.lstrip("#")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


def fmt_time(sec: Optional[float]) -> str:
    if sec is None or not math.isfinite(sec):
        return "--"
    s = int(round(sec))
    return "%d:%02d" % (s // 60, s % 60)


# ----------------------------------------------------------------------------- view + annotation layer


class View:
    """Render raster: longest side `px` pixels, aspect of the map."""

    def __init__(self, spec: MapSpec, px: int):
        sx, sy = spec.size
        if sx >= sy:
            cols, rows = px, max(1, int(round(px * sy / sx)))
        else:
            rows, cols = px, max(1, int(round(px * sx / sy)))
        self.grid = Grid(sx, sy, cols, rows)
        self.W, self.H = cols, rows
        self.px = px
        self.m_per_px = sx / cols

    def pt(self, x: float, y: float) -> Tuple[float, float]:
        c, r = self.grid.to_pixel(x, y)
        return float(c), float(r)


class Layer:
    """RGBA drawing layer at SS x resolution; text/glyph placement with simple collision avoidance."""

    def __init__(self, view: "View", w: Optional[int] = None, h: Optional[int] = None):
        self.view = view
        self.W = int(w if w is not None else view.W)
        self.H = int(h if h is not None else view.H)
        self.img = Image.new("RGBA", (self.W * SS, self.H * SS), (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.img)
        self.boxes: List[Tuple[float, float, float, float]] = []

    def s(self, p):
        return (p[0] * SS, p[1] * SS)

    def line(self, pts, color, width=1.0):
        pts = [self.s(p) for p in pts]
        self.d.line(pts, fill=color, width=max(1, int(round(width * SS))), joint="curve")

    def reserve(self, cx, cy, hw, hh):
        self.boxes.append((cx - hw, cy - hh, cx + hw, cy + hh))

    def reserve_box(self, x0, y0, x1, y1):
        self.boxes.append((x0, y0, x1, y1))

    def _free(self, box) -> bool:
        if box[0] < 3 or box[1] < 3 or box[2] > self.W - 3 or box[3] > self.H - 3:
            return False
        for b in self.boxes:
            if box[0] < b[2] and box[2] > b[0] and box[1] < b[3] and box[3] > b[1]:
                return False
        return True

    def text_size(self, txt, size, bold=False):
        bb = self.d.textbbox((0, 0), txt, font=font(size * SS, bold), anchor="lt")
        return (bb[2] - bb[0]) / SS, (bb[3] - bb[1]) / SS

    def text(self, xy, txt, size, fill=(20, 20, 20, 255), halo=(255, 255, 255, 235), bold=False, anchor="mm",
             place=False, offset=0.0, centered=False):
        """Draw text with a halo. xy in 1x pixel coords. With `place`, try positions around the anchor point and take
        the first that stays inside the image and overlaps no earlier label, glyph or symbol: next to the anchor
        (`offset` px away) or, with `centered`, on the anchor first and then at growing distances around it. When
        none is free the first candidate is used."""
        f = font(size * SS, bold)
        tw, th = self.text_size(txt, size, bold)
        x, y = xy
        if place:
            cands = []
            if centered:
                cands.append((x, y))
                for k in range(1, 8):
                    d = k * (th * 0.9)
                    dx = tw * 0.5 + d * 0.3
                    cands += [(x, y - d), (x, y + d), (x + dx, y), (x - dx, y), (x + tw * 0.35, y - d),
                              (x - tw * 0.35, y + d), (x - tw * 0.35, y - d), (x + tw * 0.35, y + d)]
            else:
                for k in range(5):
                    o = offset + k * (th + 2)
                    cands += [(x + o + tw / 2, y), (x - o - tw / 2, y), (x, y - o - th / 2), (x, y + o + th / 2),
                              (x + o + tw / 2, y - o), (x + o + tw / 2, y + o), (x - o - tw / 2, y - o),
                              (x - o - tw / 2, y + o)]
            chosen = None
            for cx, cy in cands:
                box = (cx - tw / 2 - 2, cy - th / 2 - 2, cx + tw / 2 + 2, cy + th / 2 + 2)
                if self._free(box):
                    chosen = (cx, cy)
                    break
            if chosen is None:
                chosen = cands[0]
            x, y = chosen
            anchor = "mm"
        self.d.text((x * SS, y * SS), txt, font=f, fill=fill, anchor=anchor, stroke_width=SS * 2 // 1,
                    stroke_fill=halo)
        self.reserve(x, y, tw / 2 + 2, th / 2 + 2)

    def composite_on(self, base: Image.Image) -> Image.Image:
        small = self.img.resize((self.W, self.H), Image.BOX)
        out = base.convert("RGBA")
        out.alpha_composite(small)
        return out.convert("RGB")


def _glyph_points(glyph: str, cx: float, cy: float, r: float):
    if glyph == "square":
        return [(cx - r, cy - r), (cx + r, cy - r), (cx + r, cy + r), (cx - r, cy + r)]
    if glyph == "diamond":
        r *= 1.25
        return [(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)]
    if glyph == "triangle":
        r *= 1.2
        return [(cx, cy - r), (cx + r, cy + 0.8 * r), (cx - r, cy + 0.8 * r)]
    if glyph == "star":
        r *= 1.3
        pts = []
        for i in range(10):
            a = -math.pi / 2 + i * math.pi / 5
            rr = r if i % 2 == 0 else r * 0.45
            pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
        return pts
    if glyph == "cross":
        r *= 1.2
        t = r * 0.38
        return [(cx - t, cy - r), (cx + t, cy - r), (cx + t, cy - t), (cx + r, cy - t), (cx + r, cy + t),
                (cx + t, cy + t), (cx + t, cy + r), (cx - t, cy + r), (cx - t, cy + t), (cx - r, cy + t),
                (cx - r, cy - t), (cx - t, cy - t)]
    return None  # circle


def draw_glyph(layer: Layer, glyph: str, cx: float, cy: float, r: float, fill, outline=(0, 0, 0, 255)):
    """Glyph with a black outline; (cx, cy, r) in 1x pixels."""
    pts = _glyph_points(glyph, cx, cy, r)
    d = layer.d
    o = 1.4
    if pts is None:
        d.ellipse([(cx - r - o) * SS, (cy - r - o) * SS, (cx + r + o) * SS, (cy + r + o) * SS], fill=outline)
        d.ellipse([(cx - r) * SS, (cy - r) * SS, (cx + r) * SS, (cy + r) * SS], fill=fill)
    else:
        big = _glyph_points(glyph, cx, cy, r + o)
        d.polygon([(x * SS, y * SS) for x, y in big], fill=outline)
        d.polygon([(x * SS, y * SS) for x, y in pts], fill=fill)


# ----------------------------------------------------------------------------- terrain


def _resize_f(arr: np.ndarray, W: int, H: int, resample=Image.BILINEAR) -> np.ndarray:
    return np.asarray(Image.fromarray(arr.astype(np.float32), mode="F").resize((W, H), resample), dtype=np.float32)


def hillshade(hm: Heightmap, view: View) -> np.ndarray:
    """Multiplicative shade (1 = flat) from a NW light at 45 degrees, steep faces darken up to 40 %."""
    h = _resize_f(hm.h, view.W, view.H)
    h = h * 2.0  # vertical exaggeration so gentle relief still reads
    gy, gx = np.gradient(h.astype(np.float64), view.grid.cell_size_y, view.grid.cell_size_x)
    # gy is d(h)/d(row) with rows growing southwards: north-ward slope is -gy
    dzdx, dzdy = gx, -gy
    slope = np.arctan(np.hypot(dzdx, dzdy))
    aspect = np.arctan2(dzdy, -dzdx)  # direction the surface faces (downhill), math convention from +x east
    az = math.radians(315.0)          # light from NW
    zen = math.radians(45.0)
    # compass azimuth of light -> math angle
    lam = math.pi / 2 - az
    shade = math.cos(zen) * np.cos(slope) + math.sin(zen) * np.sin(slope) * np.cos(lam - aspect)
    s = shade / math.cos(zen) - 1.0
    return np.clip(1.0 + 0.55 * s, 0.6, 1.18).astype(np.float32)


def _blend(img: np.ndarray, color, alpha: np.ndarray) -> None:
    a = alpha[..., None]
    img *= (1.0 - a)
    img += a * np.asarray(color, dtype=np.float32)


def _water_axis(spec: MapSpec, geoms: MapGeometry, p: Tuple[float, float]):
    """Tangent of the nearest water line centreline to p (unit vector) and that line's width, or None."""
    best = None
    for i, ln in enumerate(spec.lines):
        if spec.types.lines[ln.type].get("height_mode") != "water":
            continue
        cl = geoms.lines[i].centreline
        d = cl.distance(Point(p))
        if best is None or d < best[0]:
            best = (d, cl, ln.width)
    if best is None or best[0] > best[2] + 60:
        return None
    cl = best[1]
    s = cl.project(Point(p))
    a = cl.interpolate(max(0.0, s - 8.0))
    b = cl.interpolate(min(cl.length, s + 8.0))
    v = np.array([b.x - a.x, b.y - a.y])
    n = np.hypot(*v)
    if n == 0:
        return None
    return v / n, best[2]


def _ring(layer: Layer, cx: float, cy: float, rpx: float, col, n: int, casing, w_case: float, w_fill: float) -> None:
    """Dashed ring of n segments (every other one drawn) with a dark casing."""
    for k in range(0, n, 2):
        a0, a1 = 2 * math.pi * k / n, 2 * math.pi * (k + 1) / n
        pts = [(cx + rpx * math.cos(a0 + (a1 - a0) * j / 4), cy + rpx * math.sin(a0 + (a1 - a0) * j / 4))
               for j in range(5)]
        layer.line(pts, casing, w_case)
        layer.line(pts, col + (255,), w_fill)


def _chevron(layer: Layer, cx: float, cy: float, col, sz: float = 9.0) -> None:
    pts = [(cx - sz, cy + sz * 0.55), (cx, cy - sz * 0.55), (cx + sz, cy + sz * 0.55)]
    layer.line(pts, (20, 20, 20, 255), 6.0)
    layer.line(pts, col + (255,), 3.2)


def _steps(layer: Layer, cx: float, cy: float, col, sz: float = 9.0) -> None:
    """Three stacked bars of growing length: a ramp."""
    for k, half in enumerate((sz * 0.45, sz * 0.8, sz * 1.15)):
        y = cy + (k - 1) * sz * 0.62
        layer.line([(cx - half, y), (cx + half, y)], (20, 20, 20, 255), 5.0)
        layer.line([(cx - half, y), (cx + half, y)], col + (255,), 2.6)


def draw_crossings(spec: MapSpec, geoms: MapGeometry, view: View, layer: Layer) -> None:
    pal = spec.palette
    for k, c in enumerate(spec.crossings):
        col = rgb(pal.crossings[c.type])
        cx, cy = view.pt(*c.at)
        rpx = c.r / view.m_per_px
        g = geoms.crossings[k]
        if c.path is not None:      # band: dashed outline of the band, symbol at its middle
            if not g.is_empty:
                polys = [g] if g.geom_type == "Polygon" else list(getattr(g, "geoms", []))
                for poly in polys:
                    if poly.geom_type != "Polygon":
                        continue
                    pts = np.array([view.pt(x, y) for x, y in poly.exterior.coords])
                    for run in dashed_runs(pts, 9.0, 6.0):
                        layer.line(run.tolist(), (20, 20, 20, 235), 3.6)
                        layer.line(run.tolist(), col + (255,), 2.0)
            if c.type == "pass":
                _chevron(layer, cx, cy, col)
            elif c.type == "ramp":
                _steps(layer, cx, cy, col)
            layer.reserve(cx, cy, 10, 10)
            continue
        if c.type == "bridge":
            ax = _water_axis(spec, geoms, c.at)
            if ax is None:
                t, wid = np.array([0.0, 1.0]), 30.0
            else:
                t, wid = ax
            nrm = np.array([-t[1], t[0]])  # perpendicular to the river = direction of travel across it
            half = (wid / 2.0 + 22.0)
            p1 = view.pt(c.at[0] - nrm[0] * half, c.at[1] - nrm[1] * half)
            p2 = view.pt(c.at[0] + nrm[0] * half, c.at[1] + nrm[1] * half)
            w = max(5.0, 16.0 / view.m_per_px)
            layer.line([p1, p2], (30, 20, 12, 255), w + 3.0)
            layer.line([p1, p2], col + (255,), w)
            layer.line([p1, p2], (255, 255, 255, 255), max(1.6, w * 0.22))   # plank stripe: lightness on dark water
        elif c.type == "ford":
            _ring(layer, cx, cy, rpx, col, 24, (20, 40, 70, 255), 4.0, 2.2)
        elif c.type == "ramp":
            _ring(layer, cx, cy, rpx, col, 36, (20, 20, 20, 230), 3.0, 1.6)
            _steps(layer, cx, cy, col)
        else:  # pass and any other crossing: dashed ring + chevron
            _ring(layer, cx, cy, rpx, col, 36, (20, 20, 20, 230), 3.0, 1.6)
            _chevron(layer, cx, cy, col)
        layer.reserve(cx, cy, 10, 10)


def darken(col, f: float) -> Tuple[int, int, int]:
    return tuple(int(round(c * (1.0 - f))) for c in col[:3])


def ink_for(col) -> Tuple[int, int, int, int]:
    """Black or white, whichever reads better on `col`."""
    return (0, 0, 0, 255) if lightness("#%02x%02x%02x" % tuple(col[:3])) > 55 else (255, 255, 255, 255)


CASING = (35, 30, 26)
LINE_STYLE = {"trail": "dashed", "cliff": "hatched"}   # every other non-water line is drawn cased (fill + dark casing)
PATTERN_SS = 2
STIPPLE_SPACING = 6.5


# ----------------------------------------------------------------------------- patterns


def _down(m: np.ndarray) -> np.ndarray:
    h, w = m.shape[0] // PATTERN_SS, m.shape[1] // PATTERN_SS
    return m.reshape(h, PATTERN_SS, w, PATTERN_SS).mean(axis=(1, 3)).astype(np.float32)


def pattern_alpha(name: str, w: int, h: int, ox: float = 0.0, oy: float = 0.0, seed: int = 0) -> np.ndarray:
    """Anti-aliased ink coverage (float32 in [0, 1], shape (h, w)) of a texture over the window of 1x pixels
    [ox, ox + w) x [oy, oy + h). Textures are functions of absolute pixel position, so windows tile seamlessly."""
    if name == "none":
        return np.zeros((h, w), np.float32)
    if name == "stipple":
        sp = STIPPLE_SPACING
        gi, gj = np.meshgrid(np.arange(int(math.floor(ox / sp)) - 1, int(math.ceil((ox + w) / sp)) + 2),
                             np.arange(int(math.floor(oy / sp)) - 1, int(math.ceil((oy + h) / sp)) + 2))
        jx = noise.hash_lattice(gi, gj, seed)
        jy = noise.hash_lattice(gi, gj, seed + 1)
        keep = noise.hash_lattice(gi, gj, seed + 2) > -0.35
        px = (gi + 0.5 + 0.45 * jx) * sp - ox
        py = (gj + 0.5 + 0.45 * jy) * sp - oy
        ok = keep & (px > -3) & (px < w + 3) & (py > -3) & (py < h + 3)
        img = Image.new("L", (w * PATTERN_SS, h * PATTERN_SS), 0)
        d = ImageDraw.Draw(img)
        for x, y in zip(px[ok].tolist(), py[ok].tolist()):
            d.ellipse([(x - 1.5) * PATTERN_SS, (y - 1.5) * PATTERN_SS, (x + 1.5) * PATTERN_SS,
                       (y + 1.5) * PATTERN_SS], fill=255)
        return _down(np.asarray(img) > 127)
    x = (ox + (np.arange(w * PATTERN_SS) + 0.5) / PATTERN_SS)[None, :]
    y = (oy + (np.arange(h * PATTERN_SS) + 0.5) / PATTERN_SS)[:, None]
    if name == "hatch":
        m = ((x + y) % 7.0) < 1.1
    elif name == "crosshatch":
        m = (((x + y) % 7.0) < 1.0) | (((x - y) % 7.0) < 1.0)
    elif name == "reeds":
        shift = (np.floor(y / 6.0) * 5.0) % 11.0
        m = (np.abs((y % 6.0) - 3.0) < 0.6) & (((x + shift) % 11.0) < 4.5)
    elif name == "dots":
        m = ((x % 8.0 - 4.0) ** 2 + (y % 8.0 - 4.0) ** 2) < 1.7
    elif name == "grid":
        m = ((x % 9.0) < 0.9) | ((y % 9.0) < 0.9)
    elif name == "hlines":
        m = (y % 6.0) < 1.0
    else:
        raise ValueError("unknown pattern %r" % name)
    return _down(np.broadcast_to(m, (h * PATTERN_SS, w * PATTERN_SS)))


def stripes(view: "View", kind: str) -> Tuple[np.ndarray, np.ndarray]:
    """(dark, light) boolean stripe masks of a diagonal hatch over the whole view. kind: '/' or '\\'."""
    yy, xx = np.mgrid[0:view.H, 0:view.W]
    k = (xx + yy) % 10 if kind == "/" else (xx - yy) % 10
    return k < 2, (k >= 3) & (k < 6)


def dashed_runs(pts: np.ndarray, dash: float, gap: float):
    """Split a pixel polyline into dash runs (arrays of points)."""
    seg = np.hypot(*np.diff(pts, axis=0).T)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    if cum[-1] <= 0:
        return []
    s = np.arange(0.0, cum[-1], 1.0)
    P = np.column_stack([np.interp(s, cum, pts[:, 0]), np.interp(s, cum, pts[:, 1])])
    on = (s % (dash + gap)) < dash
    edges = np.flatnonzero(np.diff(on.astype(np.int8)))
    bounds = np.concatenate([[0], edges + 1, [len(s)]])
    return [P[a:b] for a, b in zip(bounds[:-1], bounds[1:]) if on[a] and b - a >= 2]


def team_letter(layer: "Layer", cx: float, cy: float, r: float, tid: str, color) -> None:
    layer.d.text((cx * SS, cy * SS), tid, font=font(max(8, int(r * 1.5)) * SS, True), fill=ink_for(color), anchor="mm")


def _min_width_buffer(area, width_m: float, view: View, min_px: float = 2.4):
    need = min_px * view.m_per_px
    if width_m < need:
        return area.buffer((need - width_m) / 2.0)
    return area


def _down2(a: np.ndarray, H: int, W: int) -> np.ndarray:
    """Box-downsample a 2x array (2H, 2W) to (H, W) float32."""
    return a.reshape(H, 2, W, 2).mean(axis=(1, 3)).astype(np.float32)


def _paint_entries(spec: MapSpec, geoms: MapGeometry, view: View):
    """Everything painted as ground cover, in paint order: regions in list order, then water lines (rivers).
    Returns a list of (paint id, geometry, fill colour, edge group); ids are region indices and, for lines,
    len(regions) + line index. The edge group is shared by lakes and rivers, so water reads as one body."""
    types = spec.types
    pal = spec.palette
    out = []
    for i, reg in enumerate(spec.regions):
        if geoms.regions[i].is_empty:
            continue
        mode = types.regions[reg.type].get("height_mode")
        out.append((i, geoms.regions[i], rgb(pal.regions[reg.type]), "water" if mode == "water" else "r:" + reg.type))
    for k, ln in enumerate(spec.lines):
        if types.lines[ln.type].get("height_mode") != "water" or geoms.lines[k].area.is_empty:
            continue
        # a river is never thinner than 2.4 px and overlaps lakes by a pixel so no seam shows where they meet
        area = _min_width_buffer(geoms.lines[k].area, ln.width, view).buffer(1.2 * view.m_per_px)
        out.append((len(spec.regions) + k, area, rgb(pal.lines[ln.type]), "water"))
    return out


def _type_outlines(top2: np.ndarray, entries, n_ids: int, spec: MapSpec, H: int, W: int):
    """Outline of the edges where the painted type group changes (2x raster `top2` of paint ids, -1 = ground).
    Returns (alpha, premultiplied rgb) at 1x. The colour of an edge is the darkened fill of the topmost side."""
    groups = {}
    tab_grp = np.zeros(n_ids + 1, np.int16)
    tab_col = np.zeros((n_ids + 1, 3), np.float32)
    for pid, _, col, gkey in entries:
        tab_grp[pid + 1] = groups.setdefault(gkey, len(groups) + 1)
        tab_col[pid + 1] = darken(col, spec.palette.outline_darken)
    g2 = tab_grp[top2.astype(np.int32) + 1]
    eid = np.full(top2.shape, -1, np.int16)
    for sl_a, sl_b in (((slice(None), slice(0, -1)), (slice(None), slice(1, None))),
                       ((slice(0, -1), slice(None)), (slice(1, None), slice(None)))):
        a, b = top2[sl_a], top2[sl_b]
        diff = g2[sl_a] != g2[sl_b]
        win = np.maximum(a, b)
        left = np.where(diff & (a >= b), win, -1).astype(np.int16)
        right = np.where(diff & (b > a), win, -1).astype(np.int16)
        eid[sl_a] = np.maximum(eid[sl_a], left)
        eid[sl_b] = np.maximum(eid[sl_b], right)
    eid = ndimage.grey_dilation(eid, size=(3, 3))
    on = eid >= 0
    alpha = _down2(on.astype(np.float32), H, W)
    pm = np.zeros((H, W, 3), np.float32)
    idx = eid.astype(np.int32) + 1
    for ch in range(3):
        pm[..., ch] = _down2(tab_col[idx, ch] * on, H, W)
    return alpha, pm


def render_core(spec: MapSpec, geoms: MapGeometry, hm: Heightmap, view: View) -> Image.Image:
    """Terrain, hillshade, region patterns and type-edge outlines, lines, crossings, grid, POIs and labels (no panel)."""
    types = spec.types
    pal = spec.palette
    W, H = view.W, view.H
    grid2 = Grid(view.grid.size_x, view.grid.size_y, 2 * W, 2 * H)
    img = np.empty((H, W, 3), dtype=np.float32)
    img[:] = np.asarray(rgb(pal.base), dtype=np.float32)
    top2 = np.full((2 * H, 2 * W), -1, dtype=np.int16)
    entries = _paint_entries(spec, geoms, view)
    water = np.zeros((H, W), np.float32)
    for pid, g, col, gkey in entries:
        m2 = raster.mask(g, grid2, supersample=1) >= 0.5
        m1 = _down2(m2.astype(np.float32), H, W)
        _blend(img, col, m1)
        if gkey == "water":
            water = np.maximum(water, m1)
        top2[m2] = pid
    shade = hillshade(hm, view)
    img *= (1.0 + (shade - 1.0) * (1.0 - water))[..., None]    # water is flat: no hillshade
    img = np.clip(img, 0, 255)

    # region textures (ink = darkened fill), only where the region is the topmost
    cache: Dict[str, np.ndarray] = {}
    for typ, pat in pal.patterns.items():
        idx = [i for i, r in enumerate(spec.regions) if r.type == typ]
        if pat == "none" or not idx:
            continue
        sel = _down2(np.isin(top2, idx).astype(np.float32), H, W)
        if not sel.any():
            continue
        if pat not in cache:
            cache[pat] = pattern_alpha(pat, W, H, seed=spec.seed + 4000)
        _blend(img, darken(rgb(pal.regions[typ]), 0.6), cache[pat] * sel * 0.9)
    cache.clear()

    # outlines where the region type (water: lakes and rivers together) changes
    alpha, pm = _type_outlines(top2, entries, len(spec.regions) + len(spec.lines), spec, H, W)
    del top2
    img = img * (1.0 - alpha[..., None]) + pm

    # lines: casing first, then fill (rivers are painted with the ground; trails and cliffs are on the annotation layer)
    for i, ln in enumerate(spec.lines):
        entry = types.lines[ln.type]
        area = geoms.lines[i].area
        if area.is_empty or ln.type in LINE_STYLE or entry.get("height_mode") == "water":
            continue
        area = _min_width_buffer(area, ln.width, view)
        _blend(img, CASING, raster.mask(area.buffer(1.0 * view.m_per_px), view.grid))
        _blend(img, rgb(pal.lines[ln.type]), raster.mask(area, view.grid))
    pil = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGB")

    layer = Layer(view)
    for i, ln in enumerate(spec.lines):
        style = LINE_STYLE.get(ln.type)
        cl = geoms.lines[i].centreline
        if style is None or cl.is_empty:
            continue
        col = rgb(pal.lines[ln.type]) + (255,)
        pts = np.array([view.pt(x, y) for x, y in cl.coords])
        if style == "dashed":    # dark dashes on a light halo: reads on forest and on field
            wpx = max(2.6, ln.width / view.m_per_px)
            for run in dashed_runs(pts, 8.0, 5.0):
                layer.line(run.tolist(), (255, 255, 255, 190), wpx + 2.4)
                layer.line(run.tolist(), col, wpx)
        else:    # hatched: dark line with ticks
            layer.line(pts.tolist(), col, 3.0)
            n = int(cl.length / (9.0 * view.m_per_px))
            for k in range(n + 1):
                sdist = cl.length * k / max(n, 1)
                a, b = cl.interpolate(max(0, sdist - 3)), cl.interpolate(min(cl.length, sdist + 3))
                v = np.array([b.x - a.x, b.y - a.y])
                if np.hypot(*v) == 0:
                    continue
                v = v / np.hypot(*v)
                q = cl.interpolate(sdist)
                layer.line([view.pt(q.x, q.y), view.pt(q.x + -v[1] * 12, q.y + v[0] * 12)], col, 1.4)

    # grid lines
    step = spec.output.grid_step
    gcol = (30, 30, 30, 70)
    x = step
    while x < spec.size[0]:
        layer.line([view.pt(x, 0), view.pt(x, spec.size[1])], gcol, 1.0)
        x += step
    y = step
    while y < spec.size[1]:
        layer.line([view.pt(0, y), view.pt(spec.size[0], y)], gcol, 1.0)
        y += step

    draw_crossings(spec, geoms, view, layer)
    _grid_labels(spec, view, layer)

    # POIs (glyphs first so labels avoid them)
    gsz = max(7.0, 0.006 * view.px)
    poi_items = []
    for poi in spec.all_pois():
        entry = types.pois[poi.type]
        cx, cy = view.pt(*poi.at)
        r = gsz * 1.7 if poi.type == "base" else gsz
        if poi.team in spec.teams:
            col = rgb(spec.teams[poi.team].color)
            draw_glyph(layer, entry["glyph"], cx, cy, r, col + (255,))
            team_letter(layer, cx, cy, r, poi.team, col)
        else:
            draw_glyph(layer, entry["glyph"], cx, cy, r, (255, 255, 255, 255))
        layer.reserve(cx, cy, r + 3, r + 3)
        poi_items.append((poi, cx, cy, r))

    # labels: pinned ones first, then regions, lines and POIs; each avoids what is already placed
    fs = max(12, int(0.0105 * view.px))
    reg_ink, line_ink = (35, 28, 20, 255), (25, 40, 70, 255)
    for i, reg in enumerate(spec.regions):
        if reg.label and reg.label_at is not None and not geoms.regions[i].is_empty:
            layer.text(view.pt(*reg.label_at), reg.label, fs + 2, fill=reg_ink, bold=True)
    for i, ln in enumerate(spec.lines):
        if ln.label and ln.label_at is not None and not geoms.lines[i].centreline.is_empty:
            layer.text(view.pt(*ln.label_at), ln.label, fs, fill=line_ink, bold=True)
    for i, reg in enumerate(spec.regions):
        if not reg.label or reg.label_at is not None or geoms.regions[i].is_empty:
            continue
        pt = geoms.regions[i].representative_point()
        layer.text(view.pt(pt.x, pt.y), reg.label, fs + 2, fill=reg_ink, bold=True, place=True, centered=True)
    for i, ln in enumerate(spec.lines):
        if not ln.label or ln.label_at is not None or geoms.lines[i].centreline.is_empty:
            continue
        pt = geoms.lines[i].centreline.interpolate(0.5, normalized=True)
        layer.text(view.pt(pt.x, pt.y), ln.label, fs, fill=line_ink, bold=True, place=True, centered=True)
    for poi, cx, cy, r in poi_items:
        txt = poi.label or poi.id
        if poi.type == "base":
            txt = "%s base" % (poi.label or poi.team)
            layer.text((cx, cy), txt, fs + 1, bold=True, place=True, offset=r + 3)
        else:
            if poi.team in spec.teams:
                txt += " [%s]" % poi.team
            layer.text((cx, cy), txt, fs, place=True, offset=r + 3)
    return layer.composite_on(pil)


def _grid_labels(spec: MapSpec, view: View, layer: Layer) -> None:
    step = spec.output.grid_step
    fs = max(10, int(0.0085 * view.px))
    col = (20, 20, 20, 255)
    halo = (255, 255, 255, 220)
    f = font(fs * SS)
    x = 0.0
    while x < spec.size[0]:
        px, py = view.pt(x, 0)
        t = "%d" % x
        layer.d.text(((px + 3) * SS, (view.H - 3) * SS), t, font=f, fill=col, anchor="lb", stroke_width=SS,
                     stroke_fill=halo)
        tw, th = layer.text_size(t, fs)
        layer.reserve_box(px + 1, view.H - 5 - th, px + 6 + tw, view.H)
        x += step
    y = step
    while y < spec.size[1]:
        px, py = view.pt(0, y)
        t = "%d" % y
        layer.d.text((3 * SS, (py - 2) * SS), t, font=f, fill=col, anchor="lb", stroke_width=SS, stroke_fill=halo)
        tw, th = layer.text_size(t, fs)
        layer.reserve_box(0, py - 4 - th, 6 + tw, py + 1)
        y += step


# ----------------------------------------------------------------------------- side panel

PANEL_FRACTION = 0.22      # panel width as a fraction of the whole image width
PANEL_BG = (246, 244, 238)
PANEL_INK = (20, 20, 20, 255)


def panel_width(view: View) -> int:
    return int(round(view.W * PANEL_FRACTION / (1.0 - PANEL_FRACTION)))


def pretty_name(name: str) -> str:
    return " ".join(w.capitalize() for w in name.split("_"))


class Panel:
    """Side panel (title, legend rows, team list, scale bar), laid out top to bottom to fit the map height."""

    def __init__(self, spec: MapSpec, view: View, subtitle: Optional[str] = None):
        self.spec = spec
        self.view = view
        self.w = panel_width(view)
        self.h = view.H
        self.layer = Layer(view, self.w, self.h)
        self.fs = max(11, int(0.0095 * view.px))
        self.rows: List[tuple] = []     # ("section", text) | ("row", draw_fn, label) | ("note", text)
        self.subtitle = subtitle

    def section(self, text: str) -> None:
        self.rows.append(("section", text))

    def row(self, draw_fn, label: str) -> None:
        self.rows.append(("row", draw_fn, label))

    def note(self, text: str) -> None:
        self.rows.append(("note", text))

    def _fit(self, txt: str, size: int, maxw: float, bold: bool = False) -> int:
        while size > 8 and self.layer.text_size(txt, size, bold)[0] > maxw:
            size -= 1
        return size

    def finish(self) -> Image.Image:
        L, d, fs, w = self.layer, self.layer.d, self.fs, self.w
        pad = 16
        y = 18
        title_fs = self._fit(pretty_name(self.spec.name), int(fs * 1.9), w - 2 * pad, True)
        d.text((pad * SS, y * SS), pretty_name(self.spec.name), font=font(title_fs * SS, True), fill=PANEL_INK, anchor="lt")
        y += title_fs + 8
        sub = "%g x %g m   seed %d" % (self.spec.size[0], self.spec.size[1], self.spec.seed)
        d.text((pad * SS, y * SS), sub, font=font(int(fs * 0.9) * SS), fill=(70, 70, 70, 255), anchor="lt")
        y += int(fs * 0.9) + 8
        if self.subtitle:
            sfs = self._fit(self.subtitle, int(fs * 1.25), w - 2 * pad, True)
            d.text((pad * SS, y * SS), self.subtitle, font=font(sfs * SS, True), fill=(40, 40, 40, 255), anchor="lt")
            y += sfs + 8
        d.line([(pad * SS, (y + 2) * SS), ((w - pad) * SS, (y + 2) * SS)], fill=(120, 120, 120, 255), width=SS)
        y += 12
        bar_h = 56
        avail = self.h - y - bar_h - 10
        n_rows = sum(1 for r in self.rows if r[0] == "row")
        n_sec = sum(1 for r in self.rows if r[0] == "section")
        n_note = sum(1 for r in self.rows if r[0] == "note")
        sec_h, note_h = fs + 14, fs + 4
        row_h = min(fs + 11.0, (avail - n_sec * sec_h - n_note * note_h) / max(n_rows, 1))
        row_h = max(row_h, fs * 1.15)
        sw = min(fs * 1.2, row_h * 0.8)
        for r in self.rows:
            if r[0] == "section":
                y += 4
                d.text((pad * SS, (y + 2) * SS), r[1].upper(), font=font(int(fs * 0.85) * SS, True),
                       fill=(90, 90, 90, 255), anchor="lt")
                y += sec_h - 4
            elif r[0] == "note":
                d.text((pad * SS, y * SS), r[1], font=font(int(fs * 0.9) * SS), fill=(60, 60, 60, 255), anchor="lt")
                y += note_h
            else:
                cy = y + row_h / 2
                r[1](L, pad, cy, sw)
                lab = r[2]
                lfs = self._fit(lab, fs, w - pad * 2 - sw * 1.6 - 14)
                d.text(((pad + sw * 1.6 + 10) * SS, cy * SS), lab, font=font(lfs * SS), fill=PANEL_INK, anchor="lm")
                y += row_h
        self._scale_bar(L, w, self.h - bar_h, pad)
        small = L.img.resize((self.w, self.h), Image.BOX)
        out = Image.new("RGBA", (self.w, self.h), PANEL_BG + (255,))
        out.alpha_composite(small)
        return out.convert("RGB")

    def _scale_bar(self, L: Layer, w: int, y0: float, pad: int) -> None:
        view, spec = self.view, self.spec
        target = spec.size[0] * 0.2
        nice = [10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 2500, 5000]
        length = max([n for n in nice if n <= target] or [nice[0]])
        wpx = length / view.m_per_px
        x0, x1, y = pad, pad + wpx, y0 + 34
        half = wpx / 2
        L.d.rectangle([x0 * SS, (y - 4) * SS, (x0 + half) * SS, (y + 4) * SS], fill=(20, 20, 20, 255), outline=(20, 20, 20, 255))
        L.d.rectangle([(x0 + half) * SS, (y - 4) * SS, x1 * SS, (y + 4) * SS], fill=(255, 255, 255, 255), outline=(20, 20, 20, 255))
        txt = "%d m" % length if length < 1000 else "%g km" % (length / 1000.0)
        L.d.text((x0 * SS, (y - 8) * SS), txt, font=font(self.fs * SS), fill=PANEL_INK, anchor="lb")
        L.d.text((x1 * SS + 10 * SS, (y + 1) * SS), "grid %g m" % spec.output.grid_step, font=font(int(self.fs * 0.9) * SS),
                 fill=(70, 70, 70, 255), anchor="lm")


def compose(core: Image.Image, panel: Image.Image) -> Image.Image:
    """Map area on the left, panel on the right, a thin rule between them."""
    out = Image.new("RGB", (core.width + panel.width, core.height), PANEL_BG)
    out.paste(core, (0, 0))
    out.paste(panel, (core.width, 0))
    ImageDraw.Draw(out).line([(core.width, 0), (core.width, core.height)], fill=(60, 60, 60), width=2)
    return out


def _legend_region(layer: Layer, spec: MapSpec, typ: str, x: float, cy: float, w: float, h: float) -> None:
    pal = spec.palette
    fill = rgb(pal.regions[typ])
    wi, hi = int(round(w)), int(round(h))
    tile = np.empty((hi, wi, 3), np.float32)
    tile[:] = np.asarray(fill, np.float32)
    pat = pal.patterns.get(typ, "none")
    if pat != "none":
        _blend(tile, darken(fill, 0.6), pattern_alpha(pat, wi, hi, seed=spec.seed + 4000) * 0.9)
    im = Image.fromarray(tile.astype(np.uint8), "RGB").convert("RGBA").resize((wi * SS, hi * SS), Image.NEAREST)
    y0 = int((cy - hi / 2.0) * SS)
    layer.img.paste(im, (int(x * SS), y0))
    layer.d.rectangle([int(x * SS), y0, int(x * SS) + wi * SS, y0 + hi * SS],
                      outline=darken(fill, pal.outline_darken) + (255,), width=int(1.5 * SS))


def _legend_line(layer: Layer, spec: MapSpec, typ: str, x: float, cy: float, w: float) -> None:
    pal = spec.palette
    col = rgb(pal.lines[typ]) + (255,)
    style = LINE_STYLE.get(typ)
    pts = np.array([[x, cy], [x + w, cy]])
    if style == "dashed":
        for run in dashed_runs(pts, 6.0, 4.0):
            layer.line(run.tolist(), (255, 255, 255, 190), 5.0)
            layer.line(run.tolist(), col, 2.6)
    elif style == "hatched":
        layer.line(pts.tolist(), col, 3.0)
        for k in range(4):
            xx = x + 2 + k * w / 4.0
            layer.line([(xx, cy), (xx, cy + 5)], col, 1.4)
    elif spec.types.lines[typ].get("height_mode") == "water":
        layer.line(pts.tolist(), darken(col, 0.3) + (255,), 6.0)
        layer.line(pts.tolist(), col, 4.4)
    else:
        layer.line(pts.tolist(), CASING + (255,), 6.0)
        layer.line(pts.tolist(), col, 4.0)


def _legend_cross(layer: Layer, spec: MapSpec, key: str, cx: float, cy: float, sw: float) -> None:
    col = rgb(spec.palette.crossings[key])
    w = sw * 1.6
    if key == "bridge":
        layer.line([(cx + 2, cy), (cx + w - 2, cy)], (30, 20, 12, 255), 8.0)
        layer.line([(cx + 3, cy), (cx + w - 3, cy)], col + (255,), 5.0)
        layer.line([(cx + 3, cy), (cx + w - 3, cy)], (255, 255, 255, 255), 1.4)
    elif key == "ford":
        for a in (0, 8, 16):
            layer.line([(cx + 2 + a, cy), (cx + 8 + a, cy)], (20, 40, 70, 255), 4.0)
            layer.line([(cx + 2 + a, cy), (cx + 8 + a, cy)], col + (255,), 2.2)
    elif key == "ramp":
        _steps(layer, cx + w / 2, cy, col, sz=sw * 0.45)
    else:
        _chevron(layer, cx + w / 2, cy, col, sz=sw * 0.4)


def _legend_team(layer: Layer, spec: MapSpec, tid: str, cx: float, cy: float, sw: float) -> None:
    col = rgb(spec.teams[tid].color)
    draw_glyph(layer, "square", cx + sw * 0.8, cy, sw * 0.5, col + (255,))
    team_letter(layer, cx + sw * 0.8, cy, sw * 0.5, tid, col)


def map_legend(panel: Panel) -> None:
    """Legend rows for the types present in this map (swatches use the map's own styles), then the team list."""
    spec = panel.spec
    types, pal = spec.types, spec.palette
    panel.section("Legend")
    seen = set()
    for r in spec.regions:
        if r.type not in seen:
            seen.add(r.type)
            panel.row(lambda L, x, cy, sw, t=r.type: _legend_region(L, spec, t, x, cy, sw * 1.6, sw), r.type)
    seen = set()
    for ln in spec.lines:
        if ln.type not in seen:
            seen.add(ln.type)
            panel.row(lambda L, x, cy, sw, t=ln.type: _legend_line(L, spec, t, x, cy, sw * 1.6), ln.type)
    seen = set()
    for c in spec.crossings:
        if c.type not in seen:
            seen.add(c.type)
            panel.row(lambda L, x, cy, sw, t=c.type: _legend_cross(L, spec, t, x, cy, sw), c.type)
    seen = set()
    for poi in spec.pois:
        if poi.type not in seen:
            seen.add(poi.type)
            g = types.pois[poi.type]["glyph"]
            panel.row(lambda L, x, cy, sw, g=g: draw_glyph(L, g, x + sw * 0.8, cy, sw * 0.5, (255, 255, 255, 255)), poi.type)
    team_rows(panel)


def team_rows(panel: Panel) -> None:
    spec = panel.spec
    panel.section("Teams")
    for t in spec.teams.values():
        panel.row(lambda L, x, cy, sw, tid=t.id: _legend_team(L, spec, tid, x, cy, sw),
                  "%s (%s)  base %d, %d" % (t.name, t.id, round(t.base[0]), round(t.base[1])))


# ----------------------------------------------------------------------------- overlays


def _faded(core: Image.Image) -> Image.Image:
    gray = core.convert("L").convert("RGB")
    x = Image.blend(core, gray, 0.5)
    return Image.blend(x, Image.new("RGB", core.size, (255, 255, 255)), 0.5)


def _up(arr: np.ndarray, view: View, resample=Image.BILINEAR) -> np.ndarray:
    return _resize_f(arr, view.W, view.H, resample)


def _an_to_view(an: AnalysisResult, view: View, rows, cols):
    """Analysis-grid pixel coords (row, col) -> render pixel coords (x, y)."""
    return ((np.asarray(cols) + 0.5) * view.W / an.grid.cols, (np.asarray(rows) + 0.5) * view.H / an.grid.rows)


def _poi_markers(spec: MapSpec, view: View, layer: Layer, labels: bool = True) -> None:
    gsz = max(7.0, 0.006 * view.px) * 0.85
    fs = max(11, int(0.0095 * view.px))
    items = []
    for poi in spec.all_pois():
        cx, cy = view.pt(*poi.at)
        r = gsz * (1.6 if poi.type == "base" else 1.0)
        glyph = spec.types.pois[poi.type]["glyph"]
        if poi.team in spec.teams:
            col = rgb(spec.teams[poi.team].color)
            draw_glyph(layer, glyph, cx, cy, r, col + (255,))
            team_letter(layer, cx, cy, r, poi.team, col)
        else:
            draw_glyph(layer, glyph, cx, cy, r, (255, 255, 255, 255))
        layer.reserve(cx, cy, r + 3, r + 3)
        items.append((poi, cx, cy, r))
    if labels:
        for poi, cx, cy, r in items:
            if poi.type == "base":
                continue
            layer.text((cx, cy), poi.id, fs, place=True, offset=r + 3)


def _contested_hatch(view: View, mask: np.ndarray) -> np.ndarray:
    """RGBA (float32) white diagonal hatch with dark edge stripes over a boolean mask (visible on any background)."""
    dark, light = stripes(view, "/")
    rgba = np.zeros((view.H, view.W, 4), np.float32)
    rgba[mask & light] = (255, 255, 255, 235)
    rgba[mask & dark] = (25, 25, 25, 235)
    return rgba


def _tint(base: Image.Image, rgba: np.ndarray) -> Image.Image:
    """Alpha-composite an (H, W, 4) float32 RGBA (0..255) array onto base."""
    over = Image.fromarray(np.clip(rgba, 0, 255).astype(np.uint8), "RGBA")
    out = base.convert("RGBA")
    out.alpha_composite(over)
    return out.convert("RGB")


def overlay_travel(spec, an, view, core) -> Image.Image:
    img = _faded(core)
    cont = _up(an.contested.astype(np.float32), view) >= 0.5
    img = _tint(img, _contested_hatch(view, cont))
    layer = Layer(view)
    styles = [None, (9.0, 5.0), (2.0, 5.0)]   # solid, dashed, dotted: team lines differ by style as well as colour
    for k, tid in enumerate(an.teams):
        t = an.times[tid]
        # Each team's isochrones are drawn only where it arrives first (or contested), so the
        # overlay reads as each side's reach instead of two crossing line sets.
        own = ndimage.binary_dilation((an.owner == k) | an.contested, iterations=2)
        fin = np.isfinite(t) & own
        if not fin.any():
            continue
        tmax = float(t[fin].max())
        tt = np.where(fin, t, 0.0)
        col = rgb(spec.teams[tid].color)
        style = styles[k % len(styles)]
        lvl = 30.0
        while lvl < tmax:
            major = int(round(lvl)) % 60 == 0
            segs = measure.find_contours(tt, lvl, mask=fin)
            longest = None
            for seg in segs:
                if len(seg) < 4:
                    continue
                x, y = _an_to_view(an, view, seg[:, 0], seg[:, 1])
                arr = np.column_stack([x, y])
                wpx = 2.4 if major else 1.4
                runs = [arr] if style is None else dashed_runs(arr, *style)
                for run in runs:
                    layer.line(run.tolist(), (255, 255, 255, 215), wpx + 2.2)
                for run in runs:
                    layer.line(run.tolist(), col + (255,), wpx)
                if longest is None or len(seg) > len(longest):
                    longest = arr.tolist()
            if major and longest is not None:
                p = longest[len(longest) // 2]
                layer.text(p, "%s %s" % (tid, fmt_time(lvl)), max(10, int(0.008 * view.px)), fill=col + (255,),
                           bold=True)
            lvl += 30.0
    _poi_markers(spec, view, layer, labels=False)
    return layer.composite_on(img)


def overlay_territory(spec, an, view, core) -> Image.Image:
    img = _faded(core)
    n = len(an.teams)
    cats = np.zeros((n + 2, view.H, view.W), np.float32)
    for k in range(n):
        cats[k] = _up(((an.owner == k) & ~an.contested).astype(np.float32), view)
    cats[n] = _up(an.contested.astype(np.float32), view)
    cats[n + 1] = _up((an.owner < 0).astype(np.float32), view)
    lab = np.argmax(cats, axis=0)
    rgba = np.zeros((view.H, view.W, 4), np.float32)
    team_pat = ["none", "dots", "hlines"]
    for k, tid in enumerate(an.teams):
        c = rgb(spec.teams[tid].color)
        m = lab == k
        rgba[m, 0], rgba[m, 1], rgba[m, 2], rgba[m, 3] = c[0], c[1], c[2], 255 * 0.42
        pat = team_pat[k % len(team_pat)]
        if pat != "none":     # team textures: owners differ by pattern as well as colour
            a = pattern_alpha(pat, view.W, view.H) * m
            ink = darken(c, 0.45)
            rgba[a > 0.3, 0], rgba[a > 0.3, 1], rgba[a > 0.3, 2], rgba[a > 0.3, 3] = ink[0], ink[1], ink[2], 255 * 0.9
    img = _tint(img, rgba)
    dark, light = stripes(view, "\\")
    un = lab == n + 1
    hatch = np.zeros((view.H, view.W, 4), np.float32)
    hatch[un] = (0, 0, 0, 55)
    hatch[un & dark] = (0, 0, 0, 255)
    img = _tint(img, hatch)
    img = _tint(img, _contested_hatch(view, lab == n))
    layer = Layer(view)
    _poi_markers(spec, view, layer, labels=False)
    # big team letter at the deepest point of each team's territory
    for k, tid in enumerate(an.teams):
        m = (an.owner == k) & ~an.contested
        if not m.any():
            continue
        dist = ndimage.distance_transform_edt(np.pad(m, 1, mode="constant"))[1:-1, 1:-1]
        r, c = np.unravel_index(int(np.argmax(dist)), dist.shape)
        x, y = _an_to_view(an, view, r, c)
        col = rgb(spec.teams[tid].color)
        layer.text((float(x), float(y)), "%s  %s" % (tid, spec.teams[tid].name), max(16, int(0.02 * view.px)),
                   fill=darken(col, 0.35) + (255,), bold=True)
    return layer.composite_on(img)


def overlay_traffic(spec, an, view, core) -> Image.Image:
    img = _faded(core)
    tr = an.traffic.astype(np.float32)
    mx = float(tr.max()) if tr.max() > 0 else 1.0
    v = np.clip(np.sqrt(_up(tr / mx, view)), 0, 1)
    ramp = np.array([rgb(c) for c in spec.palette.overlay["traffic"]], np.float32)
    xp = np.linspace(0.0, 1.0, len(ramp))
    tpos = 0.3 + 0.7 * v       # the ramp starts above its palest end so the faintest traffic still shows
    rgba = np.zeros((view.H, view.W, 4), np.float32)
    for ch in range(3):
        rgba[..., ch] = np.interp(tpos, xp, ramp[:, ch])
    rgba[..., 3] = np.where(v > 0.01, 235, 0)    # opaque ramp: lightness carries the value
    img = _tint(img, rgba)
    layer = Layer(view)
    _poi_markers(spec, view, layer, labels=False)
    fs = max(12, int(0.011 * view.px))
    for c in an.chokepoints:
        cx, cy = view.pt(c["x"], c["y"])
        r = fs * 1.3
        layer.d.ellipse([(cx - r) * SS, (cy - r) * SS, (cx + r) * SS, (cy + r) * SS], fill=(255, 255, 255, 245),
                        outline=(0, 0, 0, 255), width=3 * SS)
        layer.d.text((cx * SS, cy * SS), str(c["rank"]), font=font(fs * SS, True), fill=(0, 0, 0, 255), anchor="mm")
    return layer.composite_on(img)


def overlay_openness(spec, an, view, core) -> Image.Image:
    img = _faded(core)
    cover = np.clip(1.0 - _up(an.openness, view), 0, 1)
    rgba = np.zeros((view.H, view.W, 4), np.float32)
    c = rgb(spec.palette.overlay["cover"])
    rgba[..., 0], rgba[..., 1], rgba[..., 2] = c
    rgba[..., 3] = cover * 190
    img = _tint(img, rgba)
    layer = Layer(view)
    for key, p in an.paths.items():
        x, y = _an_to_view(an, view, p[:, 0], p[:, 1])
        pts = list(zip(x.tolist(), y.tolist()))
        layer.line(pts, (0, 0, 0, 230), 4.4)
        layer.line(pts, (255, 255, 255, 255), 2.4)
    _poi_markers(spec, view, layer, labels=False)
    return layer.composite_on(img)


def overlay_cost(spec, an, view, core) -> Image.Image:
    c = an.cost
    fin = np.isfinite(c)
    v = np.zeros(c.shape, np.float32)
    lg = np.log(np.where(fin, c, 1.0))
    norm = np.clip((lg - math.log(0.5)) / (math.log(8.0) - math.log(0.5)), 0, 1)
    v = np.where(fin, 235.0 - 185.0 * norm, 0.0).astype(np.float32)
    g = _resize_f(v, view.W, view.H, Image.NEAREST)
    img = Image.fromarray(np.clip(g, 0, 255).astype(np.uint8), "L").convert("RGB")
    layer = Layer(view)
    _poi_markers(spec, view, layer, labels=False)
    return layer.composite_on(img)


def overlay_balance(spec, an, view, core) -> Image.Image:
    """Fair zone: cells every team reaches within `balance_tolerance` of each other (teal tint, white hatch)."""
    img = _faded(core)
    fair = _up(an.fair.astype(np.float32), view) >= 0.5
    c = rgb(spec.palette.overlay["fair"])
    rgba = np.zeros((view.H, view.W, 4), np.float32)
    rgba[fair] = c + (95,)
    _, light = stripes(view, "\\")
    rgba[fair & light] = (255, 255, 255, 235)
    img = _tint(img, rgba)
    layer = Layer(view)
    _poi_markers(spec, view, layer, labels=False)
    fs = max(11, int(0.0095 * view.px))
    pos = {p.id: p for p in spec.all_pois()}
    for o in an.objectives:
        if o["spread"] is None or o["id"] not in pos:
            continue
        cx, cy = view.pt(*pos[o["id"]].at)
        layer.text((cx, cy), "%s %.0f%%" % (o["id"], 100.0 * o["spread"]), fs, place=True,
                   offset=max(7.0, 0.006 * view.px) * 0.85 + 3)
    return layer.composite_on(img)


# ----------------------------------------------------------------------------- overlay panels


def _hatch_tile(kind: str, colors, bg=None):
    """Callable drawing a swatch of a diagonal hatch (as on the overlays) into a panel layer."""
    def draw(L: Layer, x: float, cy: float, sw: float) -> None:
        wi, hi = int(round(sw * 1.6)), int(round(sw))
        fake = type("V", (), {"W": wi, "H": hi})()
        dark, light = stripes(fake, kind)
        tile = np.zeros((hi, wi, 4), np.uint8)
        if bg is not None:
            tile[...] = bg
        if "light" in colors:
            tile[light] = colors["light"]
        if "dark" in colors:
            tile[dark] = colors["dark"]
        im = Image.fromarray(tile, "RGBA").resize((wi * SS, hi * SS), Image.NEAREST)
        y0 = int((cy - hi / 2.0) * SS)
        L.img.paste(im, (int(x * SS), y0))
        L.d.rectangle([int(x * SS), y0, int(x * SS) + wi * SS, y0 + hi * SS], outline=(40, 40, 40, 255), width=SS)
    return draw


def _swatch(color, pattern: str = "none", alpha: float = 0.42):
    def draw(L: Layer, x: float, cy: float, sw: float) -> None:
        wi, hi = int(round(sw * 1.6)), int(round(sw))
        tile = np.full((hi, wi, 3), 250.0, np.float32)
        _blend(tile, color, np.full((hi, wi), alpha, np.float32))
        if pattern != "none":
            _blend(tile, darken(color, 0.45), pattern_alpha(pattern, wi, hi) * 0.9)
        im = Image.fromarray(np.clip(tile, 0, 255).astype(np.uint8), "RGB").convert("RGBA").resize((wi * SS, hi * SS), Image.NEAREST)
        y0 = int((cy - hi / 2.0) * SS)
        L.img.paste(im, (int(x * SS), y0))
        L.d.rectangle([int(x * SS), y0, int(x * SS) + wi * SS, y0 + hi * SS], outline=(40, 40, 40, 255), width=SS)
    return draw


def _ramp_swatch(colors, label_lo=None):
    def draw(L: Layer, x: float, cy: float, sw: float) -> None:
        wi, hi = int(round(sw * 1.6)), int(round(sw))
        xp = np.linspace(0.0, 1.0, len(colors))
        t = np.linspace(0.0, 1.0, wi)
        tile = np.zeros((hi, wi, 3), np.uint8)
        for ch in range(3):
            tile[..., ch] = np.interp(t, xp, [c[ch] for c in colors])[None, :]
        im = Image.fromarray(tile, "RGB").convert("RGBA").resize((wi * SS, hi * SS), Image.NEAREST)
        y0 = int((cy - hi / 2.0) * SS)
        L.img.paste(im, (int(x * SS), y0))
        L.d.rectangle([int(x * SS), y0, int(x * SS) + wi * SS, y0 + hi * SS], outline=(40, 40, 40, 255), width=SS)
    return draw


def _team_line(spec: MapSpec, tid: str, k: int):
    col = rgb(spec.teams[tid].color)
    style = [None, (9.0, 5.0), (2.0, 5.0)][k % 3]

    def draw(L: Layer, x: float, cy: float, sw: float) -> None:
        pts = np.array([[x, cy], [x + sw * 1.6, cy]])
        runs = [pts] if style is None else dashed_runs(pts, *style)
        for run in runs:
            L.line(run.tolist(), (255, 255, 255, 215), 4.4)
        for run in runs:
            L.line(run.tolist(), col + (255,), 2.4)
    return draw


def panel_for(spec: MapSpec, view: View, which: str, an: Optional[AnalysisResult] = None) -> Image.Image:
    titles = {"map": None, "travel": "Travel time", "territory": "Territory", "traffic": "Traffic",
              "openness": "Openness", "cost": "Travel cost", "balance": "Fair zone"}
    p = Panel(spec, view, titles[which])
    teams = list(spec.teams.values())
    pal = spec.palette
    if which == "map":
        map_legend(p)
        return p.finish()
    if which == "travel":
        p.section("Isochrones")
        for k, t in enumerate(teams):
            p.row(_team_line(spec, t.id, k), "%s: every 30 s, labelled each minute" % t.id)
        p.row(_hatch_tile("/", {"light": (255, 255, 255, 235), "dark": (25, 25, 25, 235)}), "contested")
    elif which == "territory":
        p.section("Owner (fastest team)")
        for k, t in enumerate(teams):
            p.row(_swatch(rgb(t.color), ["none", "dots", "hlines"][k % 3]), "%s %s" % (t.id, t.name))
        p.row(_hatch_tile("/", {"light": (255, 255, 255, 235), "dark": (25, 25, 25, 235)}), "contested")
        p.row(_hatch_tile("\\", {"dark": (0, 0, 0, 255)}, bg=(0, 0, 0, 55)), "unreachable")
    elif which == "traffic":
        p.section("Shortest-path traffic")
        ramp = [rgb(c) for c in pal.overlay["traffic"]]
        p.row(_ramp_swatch(ramp), "few paths to many paths")

        def num(L, x, cy, sw):
            r = sw * 0.5
            L.d.ellipse([(x + sw * 0.3 - r) * SS, (cy - r) * SS, (x + sw * 0.3 + r) * SS, (cy + r) * SS],
                        fill=(255, 255, 255, 245), outline=(0, 0, 0, 255), width=2 * SS)
            L.d.text(((x + sw * 0.3) * SS, cy * SS), "1", font=font(int(sw * 0.8) * SS, True), fill=(0, 0, 0, 255), anchor="mm")
        p.row(num, "chokepoint (report rank)")
    elif which == "openness":
        p.section("Cover")
        c = rgb(pal.overlay["cover"])
        p.row(_ramp_swatch([(250, 250, 250), c]), "open to covered")

        def route(L, x, cy, sw):
            L.line([(x, cy), (x + sw * 1.6, cy)], (0, 0, 0, 230), 4.4)
            L.line([(x, cy), (x + sw * 1.6, cy)], (255, 255, 255, 255), 2.4)
        p.row(route, "base-to-base route")
    elif which == "cost":
        p.section("Cost per metre")
        p.row(_ramp_swatch([(235, 235, 235), (50, 50, 50)]), "cheap to costly")
        p.row(_swatch((0, 0, 0), alpha=1.0), "impassable")
    elif which == "balance":
        p.section("Time spread between teams")
        c = rgb(pal.overlay["fair"])
        tol = 100.0 * spec.analysis.balance_tolerance
        share = 100.0 * (an.fair_share if an is not None else 0.0)
        p.row(_hatch_tile("\\", {"light": (255, 255, 255, 235)}, bg=c + (110,)), "within %.0f%% (%.0f%% of the map)" % (tol, share))
        p.note("Objective labels show their spread.")
    p.section("Markers")
    p.row(lambda L, x, cy, sw: draw_glyph(L, "circle", x + sw * 0.8, cy, sw * 0.5, (255, 255, 255, 255)), "objective / landmark")
    team_rows(p)
    return p.finish()


OVERLAYS = [
    ("overlay_travel.png", overlay_travel, "travel"),
    ("overlay_territory.png", overlay_territory, "territory"),
    ("overlay_traffic.png", overlay_traffic, "traffic"),
    ("overlay_openness.png", overlay_openness, "openness"),
    ("overlay_balance.png", overlay_balance, "balance"),
    ("overlay_cost.png", overlay_cost, "cost"),
]


def render_all(spec: MapSpec, geoms: MapGeometry, hm: Heightmap, an: AnalysisResult, out_dir: str) -> List[str]:
    """Write map.png and overlay_*.png into out_dir (map area plus side panel); returns the file names written."""
    view = View(spec, spec.output.render_px)
    core = render_core(spec, geoms, hm, view)
    written = []
    compose(core, panel_for(spec, view, "map")).save(os.path.join(out_dir, "map.png"))
    written.append("map.png")
    for name, fn, which in OVERLAYS:
        compose(fn(spec, an, view, core), panel_for(spec, view, which, an)).save(os.path.join(out_dir, name))
        written.append(name)
    return written
