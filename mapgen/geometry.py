"""Spec shapes -> shapely geometries in world metres (origin SW, x east, y north), clipped to the map."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import shapely
from shapely import affinity
from shapely.geometry import GeometryCollection, LineString, MultiPolygon, Point, Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.validation import make_valid
from skimage import measure

from . import noise, raster
from .schema import Crossing, Line, MapSpec, Region, Scatter

BLOB_VERTICES = 96
CHAIKIN_ITERATIONS = 3
ROUGH_SEGMENT = 20.0      # metres, edge densification for roughened polygons
MEANDER_STEP = 10.0       # metres, centreline resampling for meander
MEANDER_WAVELENGTH = 300.0  # metres, default `meander_scale`
WARP_SEGMENT = 10.0       # metres, outline densification before domain warp
WIDTH_VAR_STEP = 10.0     # metres, segment length of variable-width lines
WIDTH_VAR_WAVELENGTH = 250.0
FRAGMENT_CELL = 5.0       # metres, raster cell of fragmentation fields
FRAGMENT_MIN_AREA = 2500.0
SCATTER_MIN_AREA = 1500.0
AUTO_MARGIN = 15.0        # metres added to a river's half-width for an automatic crossing
AUTO_BAND_EXTEND = 18.0   # metres an automatic lake-ford band extends beyond the shore on each side
CLEARANCE_OUTER = 1.5     # scatter clearance: warped buffer radius as a multiple of `avoid`


@dataclass
class LineGeom:
    centreline: LineString
    area: BaseGeometry


@dataclass
class MapGeometry:
    bounds: Polygon
    regions: List[BaseGeometry]       # parallel to spec.regions (may be empty geometries)
    lines: List[LineGeom]             # parallel to spec.lines
    crossings: List[BaseGeometry]     # parallel to spec.crossings


# ----------------------------------------------------------------------------- helpers


def _key(reg: Region) -> int:
    """Noise seed offset of a region: its index in the spec's `regions` list (scatter regions have their own)."""
    return reg.seed_key if reg.seed_key >= 0 else reg.index


def _smoothstep(e0: float, e1: float, x):
    t = np.clip((np.asarray(x, dtype=np.float64) - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def parts(geom: BaseGeometry) -> List[Polygon]:
    """Polygon parts of a (multi)polygonal geometry."""
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    return [g for g in getattr(geom, "geoms", []) if isinstance(g, Polygon)]


def _multi(polys: List[Polygon]) -> BaseGeometry:
    if not polys:
        return Polygon()
    return polys[0] if len(polys) == 1 else MultiPolygon(polys)


def drop_small_parts(geom: BaseGeometry, fraction: float = 0.01, min_area: float = 0.0) -> BaseGeometry:
    """Drop parts smaller than `fraction` of the largest part (and than `min_area`)."""
    ps = parts(geom)
    if not ps:
        return Polygon()
    big = max(p.area for p in ps)
    return _multi([p for p in ps if p.area >= fraction * big and p.area >= min_area])


def polygonal(geom: BaseGeometry) -> BaseGeometry:
    """Keep only the polygonal parts of a geometry (Polygon or MultiPolygon; empty polygon if none)."""
    if geom is None or geom.is_empty:
        return Polygon()
    if isinstance(geom, (Polygon, MultiPolygon)):
        return geom
    if isinstance(geom, GeometryCollection) or hasattr(geom, "geoms"):
        parts: List[Polygon] = []
        for g in geom.geoms:
            p = polygonal(g)
            if p.is_empty:
                continue
            parts.extend(p.geoms if isinstance(p, MultiPolygon) else [p])
        if not parts:
            return Polygon()
        return parts[0] if len(parts) == 1 else MultiPolygon(parts)
    return Polygon()


def clean_polygon(geom: BaseGeometry, bounds: BaseGeometry) -> BaseGeometry:
    """Make valid, clip to the map rectangle, keep polygonal parts."""
    if geom.is_empty:
        return Polygon()
    if not geom.is_valid:
        geom = make_valid(geom)
    geom = polygonal(geom)
    if geom.is_empty:
        return geom
    clipped = geom.intersection(bounds)
    if not clipped.is_valid:
        clipped = make_valid(clipped)
    return polygonal(clipped)


def chaikin(points: Sequence[Tuple[float, float]], iterations: int = CHAIKIN_ITERATIONS) -> np.ndarray:
    """Chaikin corner cutting on an open path; endpoints are preserved. Returns an (n, 2) array."""
    pts = np.asarray(points, dtype=np.float64)
    for _ in range(iterations):
        if len(pts) < 3:
            break
        q = 0.75 * pts[:-1] + 0.25 * pts[1:]
        r = 0.25 * pts[:-1] + 0.75 * pts[1:]
        out = np.empty((2 * len(q), 2))
        out[0::2] = q
        out[1::2] = r
        pts = np.vstack([pts[:1], out[1:-1], pts[-1:]])
    return pts


def _resample(pts: np.ndarray, step: float) -> Tuple[np.ndarray, np.ndarray]:
    """Resample a polyline at roughly uniform arc-length steps. Returns (points, arc length s)."""
    seg = np.hypot(*np.diff(pts, axis=0).T)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    total = cum[-1]
    n = max(2, int(math.ceil(total / step)) + 1)
    s = np.linspace(0.0, total, n)
    return np.column_stack([np.interp(s, cum, pts[:, 0]), np.interp(s, cum, pts[:, 1])]), s


def _normals(pts: np.ndarray) -> np.ndarray:
    """Left-hand unit normals of a polyline (central differences)."""
    d = np.gradient(pts, axis=0)
    ln = np.hypot(d[:, 0], d[:, 1])
    ln[ln == 0] = 1.0
    d = d / ln[:, None]
    return np.column_stack([-d[:, 1], d[:, 0]])


def _roughen_polygon(poly: Polygon, rough: float, seed: int) -> Polygon:
    """Densify the exterior to <= 20 m segments and displace vertices along the outward normal."""
    if rough <= 0 or poly.is_empty or poly.area <= 0:
        return poly
    if not poly.is_valid:
        poly = polygonal(make_valid(poly))
        if isinstance(poly, MultiPolygon):
            poly = max(poly.geoms, key=lambda g: g.area)
        if poly.is_empty:
            return poly
    amp = rough * 0.15 * math.sqrt(poly.area)
    dense = shapely.segmentize(poly, ROUGH_SEGMENT)
    ring = shapely.geometry.polygon.orient(dense, 1.0).exterior  # CCW exterior -> outward normal is right-hand
    pts = np.asarray(ring.coords)[:-1]
    n = len(pts)
    if n < 4:
        return poly
    seg = np.hypot(*np.diff(np.vstack([pts, pts[:1]]), axis=0).T)
    cum = np.concatenate([[0.0], np.cumsum(seg)[:-1]])
    perim = float(seg.sum())
    cells = int(min(64, max(3, round(perim / (0.6 * math.sqrt(poly.area))))))
    u = cum / perim
    nz = noise.fbm1_periodic(u, cells, seed, octaves=3)
    d = np.roll(pts, -1, axis=0) - np.roll(pts, 1, axis=0)
    ln = np.hypot(d[:, 0], d[:, 1])
    ln[ln == 0] = 1.0
    d = d / ln[:, None]
    outward = np.column_stack([d[:, 1], -d[:, 0]])
    new = pts + outward * (amp * nz)[:, None]
    result = Polygon(new, [i.coords for i in dense.interiors])
    return result


# ----------------------------------------------------------------------------- per-feature builders


def blob_polygon(reg: Region, seed: int) -> Polygon:
    """Roughened circle: radius(theta) = r * (1 + rough * 0.5 * n(theta)), squash along local y, rotate, translate."""
    theta = np.arange(BLOB_VERTICES) * (2.0 * math.pi / BLOB_VERTICES)
    n = noise.fbm1_periodic(theta / (2.0 * math.pi), 6, seed + _key(reg), octaves=3)
    rad = reg.r * (1.0 + reg.rough * 0.5 * n)
    x = rad * np.cos(theta)
    y = rad * np.sin(theta) * reg.squash
    a = math.radians(reg.angle)
    ca, sa = math.cos(a), math.sin(a)
    px = reg.at[0] + x * ca - y * sa
    py = reg.at[1] + x * sa + y * ca
    return Polygon(np.column_stack([px, py]))


def band_polygon(reg: Region, seed: int) -> BaseGeometry:
    centre = LineString(chaikin(reg.path))
    poly = centre.buffer(reg.width / 2.0, quad_segs=16, cap_style="round", join_style="round")
    if isinstance(poly, Polygon):
        poly = _roughen_polygon(poly, reg.rough, seed + _key(reg))
    return poly


def warp_geometry(geom: BaseGeometry, warp: float, scale: float, seed: int) -> BaseGeometry:
    """2D domain warp: densify to <= WARP_SEGMENT and move every vertex by `warp` * (fbm2, fbm2) sampled at
    (x / scale, y / scale) with two independent seeds. The result may be invalid; callers clean it."""
    if warp <= 0 or geom.is_empty:
        return geom
    dense = shapely.segmentize(geom, WARP_SEGMENT)

    def fn(c: np.ndarray) -> np.ndarray:
        x, y = c[:, 0] / scale, c[:, 1] / scale
        return c + warp * np.column_stack([noise.fbm2(x, y, seed, octaves=3), noise.fbm2(x, y, seed + 7919, octaves=3)])

    return shapely.transform(dense, fn)


def _warp_params(reg: Region, g: BaseGeometry):
    if reg.shape == "blob":
        warp, scale = 0.3 * reg.r, reg.r
    elif reg.shape == "band":
        warp, scale = 0.25 * reg.width, reg.width
    else:
        warp, scale = 0.0, 0.5 * math.sqrt(max(g.area, 1.0))
    return (reg.warp if reg.warp is not None else warp), (reg.warp_scale if reg.warp_scale is not None else scale)


def field_to_polygons(f: np.ndarray, x0: float, y1: float, cs: float, min_area: float, simplify: float,
                      hole_min: float = 0.0) -> BaseGeometry:
    """Polygons of the region {f >= 0} of a raster field. Cell (r, c) has its centre at
    (x0 + (c + 0.5) * cs, y1 - (r + 0.5) * cs). Contours of the continuous field give smooth edges; the array is
    padded so every contour closes. Shells smaller than `min_area` and holes smaller than `hole_min` are dropped."""
    fp = np.pad(f.astype(np.float64), 1, mode="constant", constant_values=-10.0)
    rings: List[Polygon] = []
    for c in measure.find_contours(fp, 0.0):
        if len(c) < 4:
            continue
        xs = x0 + (c[:, 1] - 1 + 0.5) * cs
        ys = y1 - (c[:, 0] - 1 + 0.5) * cs
        poly = Polygon(np.column_stack([xs, ys]))
        if not poly.is_valid:
            poly = polygonal(make_valid(poly))
            if poly.is_empty:
                continue
            if not isinstance(poly, Polygon):
                poly = max(parts(poly), key=lambda g: g.area)
        if poly.area < min(min_area, hole_min if hole_min > 0 else min_area):
            continue
        rings.append(poly.simplify(simplify, preserve_topology=True) if simplify > 0 else poly)
    if not rings:
        return Polygon()
    arr = np.empty(len(rings), dtype=object)
    for i, rg in enumerate(rings):
        arr[i] = rg
    pts = shapely.points(np.array([r.exterior.coords[0] for r in rings]))
    inside = shapely.contains(arr[None, :], pts[:, None])      # inside[i, j]: ring j contains the start of ring i
    depth = inside.sum(axis=1)
    area = np.array([r.area for r in rings])
    shells = [i for i in range(len(rings)) if depth[i] % 2 == 0 and area[i] >= min_area]
    holes = {i: [] for i in shells}
    for i in range(len(rings)):
        if depth[i] % 2 == 1 and area[i] >= (hole_min if hole_min > 0 else min_area):
            cands = [j for j in shells if inside[i, j]]
            if cands:
                holes[min(cands, key=lambda j: area[j])].append(rings[i].exterior.coords)
    out = []
    for i in shells:
        out.append(Polygon(rings[i].exterior.coords, holes[i]))
    g = _multi(out)
    if not g.is_valid:
        g = polygonal(make_valid(g))
    return g


def fragment_geometry(reg: Region, g: BaseGeometry, seed: int, warnings: Optional[List[str]] = None) -> BaseGeometry:
    """Break a region into patches with clearings and ragged edges (`fragment` = fraction of area removed).
    The largest patch always survives; a warning is appended when far more than `fragment` of the area is lost."""
    if g.is_empty or reg.fragment <= 0:
        return g
    scale, cs = reg.fragment_scale, FRAGMENT_CELL
    minx, miny, maxx, maxy = g.bounds
    x0, y0 = minx - scale, miny - scale
    cols = int(math.ceil((maxx - minx + 2 * scale) / cs))
    rows = int(math.ceil((maxy - miny + 2 * scale) / cs))
    grid = raster.Grid(cols * cs, rows * cs, cols, rows)
    m = raster.hard(raster.mask(affinity.translate(g, -x0, -y0), grid))
    if not m.any():
        return g
    d = raster.edt_inside(m, grid)
    y1 = y0 + rows * cs
    xs = x0 + (np.arange(cols) + 0.5) * cs
    ys = y1 - (np.arange(rows) + 0.5) * cs
    n = noise.fbm2_grid(xs / scale, ys / scale, seed + 6000 + _key(reg), octaves=4)
    n = n - 0.6 * (1.0 - _smoothstep(0.0, scale, d))
    q = float(np.quantile(n[m], reg.fragment))
    out = field_to_polygons(n - q, x0, y1, cs, FRAGMENT_MIN_AREA, 3.0, hole_min=0.2 * FRAGMENT_MIN_AREA)
    out = polygonal(out.intersection(g)) if not out.is_empty else Polygon()
    out = drop_small_parts(out, 0.0, FRAGMENT_MIN_AREA)
    if out.is_empty:    # every patch is small: keep the largest one of the field
        small = field_to_polygons(n - q, x0, y1, cs, 1.0, 3.0)
        ps = parts(polygonal(small.intersection(g))) if not small.is_empty else []
        out = max(ps, key=lambda p: p.area) if ps else g
    if warnings is not None and g.area > 0:
        lost = 1.0 - out.area / g.area
        if lost > reg.fragment + 0.25:
            warnings.append("regions[%d] (%s%s): fragmentation removed %.0f%% of the area (fragment %g asks for %.0f%%)" % (
                reg.index, reg.type, " '%s'" % reg.label if reg.label else "", 100.0 * lost, reg.fragment,
                100.0 * reg.fragment))
    return out


def region_geometry(reg: Region, seed: int, bounds: BaseGeometry, warnings: Optional[List[str]] = None) -> BaseGeometry:
    if reg.shape == "blob":
        g = blob_polygon(reg, seed)
    elif reg.shape == "poly":
        g = _roughen_polygon(Polygon(reg.points), reg.rough, seed + _key(reg))
    else:
        g = band_polygon(reg, seed)
    warp, wscale = _warp_params(reg, g)
    if warp > 0:
        g = warp_geometry(g, warp, wscale, seed + 3000 + 2 * _key(reg))
    g = drop_small_parts(clean_polygon(g, bounds))
    return fragment_geometry(reg, g, seed, warnings)


def line_centreline(ln: Line, seed: int, join_to: Optional[LineString] = None) -> np.ndarray:
    """Chaikin-smoothed path with optional meander (endpoints fixed). With `join_to` (the parent's final centreline)
    the `join_end` endpoint is first moved to the nearest point on it. Returns an (n, 2) array."""
    path = [tuple(p) for p in ln.path]
    if join_to is not None and not join_to.is_empty:
        k = 0 if ln.join_end == "start" else len(path) - 1
        q = join_to.interpolate(join_to.project(Point(path[k])))
        path[k] = (q.x, q.y)
    pts = chaikin(path)
    if ln.meander > 0:
        pts, s = _resample(pts, MEANDER_STEP)
        total = s[-1]
        if total > 0:
            nz = noise.fbm1(s / ln.meander_scale, seed + ln.index, octaves=2)
            t = s / total
            edge = np.clip(np.minimum(t, 1.0 - t) / 0.05, 0.0, 1.0)
            taper = edge * edge * (3.0 - 2.0 * edge)
            pts = pts + _normals(pts) * (ln.meander * nz * taper)[:, None]
    return pts


def variable_width_area(ln: Line, pts: np.ndarray, seed: int) -> BaseGeometry:
    """Union of buffered WIDTH_VAR_STEP segments whose half-width follows
    width * (1 + width_var * 0.6 * fbm1(s / 250)), clamped to >= 0.4 * width."""
    rp, s = _resample(pts, WIDTH_VAR_STEP)
    nz = noise.fbm1(s / WIDTH_VAR_WAVELENGTH, seed + 5000 + ln.index, octaves=2)
    w = ln.width * np.maximum(1.0 + ln.width_var * 0.6 * nz, 0.4)
    segs = shapely.linestrings(np.stack([rp[:-1], rp[1:]], axis=1))
    half = 0.25 * (w[:-1] + w[1:])
    return shapely.union_all(shapely.buffer(segs, half, quad_segs=6))


def line_geometry(ln: Line, seed: int, bounds: BaseGeometry, join_to: Optional[LineString] = None) -> LineGeom:
    pts = line_centreline(ln, seed, join_to)
    centre = LineString(pts)
    if ln.width_var > 0:
        area = clean_polygon(variable_width_area(ln, pts, seed), bounds)
    else:
        area = clean_polygon(centre.buffer(ln.width / 2.0, quad_segs=8, cap_style="round", join_style="round"), bounds)
    clipped = centre.intersection(bounds)
    if clipped.is_empty:
        clipped = centre
    elif hasattr(clipped, "geoms"):
        lines = [g for g in clipped.geoms if isinstance(g, LineString)]
        clipped = max(lines, key=lambda g: g.length) if lines else centre
    if not isinstance(clipped, LineString):
        clipped = centre
    return LineGeom(centreline=clipped, area=area)


def crossing_geometry(c: Crossing, bounds: BaseGeometry) -> BaseGeometry:
    """A disc (`at`, `r`) or, for a band crossing, the smoothed `path` buffered by `width / 2`."""
    if c.path is not None:
        band = LineString(chaikin(c.path)).buffer(c.width / 2.0, quad_segs=16, cap_style="round", join_style="round")
        return clean_polygon(band, bounds)
    return clean_polygon(Point(c.at).buffer(c.r, quad_segs=16), bounds)


def line_order(lines: Sequence[Line]) -> List[int]:
    """Indices into `lines` with every parent (the line named by `join`) before its tributaries."""
    by_id = {ln.id: k for k, ln in enumerate(lines) if ln.id is not None}
    order: List[int] = []
    done = set()

    def visit(k: int) -> None:
        if k in done:
            return
        done.add(k)
        j = lines[k].join
        if j is not None and j in by_id and by_id[j] != k:
            visit(by_id[j])
        order.append(k)

    for k in range(len(lines)):
        visit(k)
    return order


def _line_pieces(g: BaseGeometry) -> List[LineString]:
    if g is None or g.is_empty:
        return []
    if isinstance(g, LineString):
        return [g]
    return [p for x in getattr(g, "geoms", []) for p in _line_pieces(x)]


def _extended(piece: LineString, by: float) -> List[Tuple[float, float]]:
    """Coordinates of a polyline extended by `by` metres past both ends along the end tangents."""
    c = np.asarray(piece.coords)
    d0 = c[0] - c[min(1, len(c) - 1)]
    d1 = c[-1] - c[max(-2, -len(c))]
    n0, n1 = float(np.hypot(*d0)), float(np.hypot(*d1))
    a = c[0] + d0 / n0 * by if n0 > 0 else c[0]
    b = c[-1] + d1 / n1 * by if n1 > 0 else c[-1]
    return [tuple(a)] + [tuple(p) for p in c] + [tuple(b)]


def auto_crossings(spec: MapSpec, regions: List[BaseGeometry], lines: List["LineGeom"],
                   crossings: List[BaseGeometry], bounds: BaseGeometry) -> List[Tuple[Crossing, BaseGeometry]]:
    """Crossings for every road x river and trail x river intersection no crossing covers (the line type's
    `crossing` entry in types.yaml picks the crossing type), centred on the intersection of the centrelines with
    r = river half-width + 15 m, and for every such line that enters and leaves a lake, a ford band along it inside
    the lake."""
    types = spec.types
    cov_parts = [g for g in crossings if not g.is_empty]
    cov = unary_union(cov_parts) if cov_parts else Polygon()
    rivers = [(k, ln) for k, ln in enumerate(spec.lines)
              if types.lines[ln.type].get("height_mode") == "water" and not lines[k].area.is_empty]
    lake_geoms = [regions[i] for i, r in enumerate(spec.regions)
                  if types.regions[r.type].get("height_mode") == "water" and not regions[i].is_empty]
    lakes = unary_union(lake_geoms) if lake_geoms else Polygon()
    out: List[Tuple[Crossing, BaseGeometry]] = []

    def add(ctype: str, at, r: float, path, width: float, note: str) -> None:
        nonlocal cov
        c = Crossing(index=len(spec.crossings) + len(out), type=ctype, at=at, r=r,
                     id="auto_%d" % (len(out) + 1), path=path, width=width, auto=True, note=note)
        g = crossing_geometry(c, bounds)
        out.append((c, g))
        if not g.is_empty:
            cov = unary_union([cov, g])

    def uncovered(g: BaseGeometry) -> bool:
        rest = g.difference(cov) if not cov.is_empty else g
        return rest.area > 4.0 and rest.area > 0.02 * g.area

    for k, ln in enumerate(spec.lines):
        entry = types.lines[ln.type]
        ctype = entry.get("crossing")
        lg = lines[k]
        if ctype is None or entry["cost"] == float("inf") or lg.area.is_empty:
            continue
        lname = ln.id or "line_%d" % k
        for rk, rl in rivers:
            inter = lg.area.intersection(lines[rk].area)
            for comp in parts(inter):
                if comp.area <= 4.0 or not uncovered(comp):
                    continue
                pt = lg.centreline.intersection(lines[rk].centreline)
                if pt.is_empty or pt.geom_type != "Point":
                    pc = _line_pieces(lg.centreline.intersection(comp))
                    pt = (max(pc, key=lambda p: p.length).interpolate(0.5, normalized=True) if pc
                          else comp.representative_point())
                at = (pt.x, pt.y)
                r = rl.width / 2.0 + AUTO_MARGIN
                for _ in range(10):   # oblique crossings need a larger disc to cover the whole overlap
                    if comp.difference(Point(at).buffer(r, quad_segs=16)).area <= 0.02 * comp.area:
                        break
                    r += 4.0
                add(ctype, at, r, None, 0.0, "%s x %s" % (lname, rl.id or "line_%d" % rk))
        if not lakes.is_empty:
            cl = lg.centreline
            ends = (Point(cl.coords[0]), Point(cl.coords[-1]))
            for piece in _line_pieces(cl.intersection(lakes)):
                if piece.length < 1.0 or piece.distance(ends[0]) < 2.0 or piece.distance(ends[1]) < 2.0:
                    continue
                rest = piece.difference(cov) if not cov.is_empty else piece
                if rest.length <= 2.0 or rest.length <= 0.1 * piece.length:
                    continue
                path = _extended(piece, AUTO_BAND_EXTEND)
                add("ford", _mid(path), 0.0, path, max(30.0, ln.width + 20.0), "%s x lake" % lname)
                out[-1][0].r = out[-1][0].width / 2.0
    return out


def _mid(path: List[Tuple[float, float]]) -> Tuple[float, float]:
    p = LineString(path).interpolate(0.5, normalized=True)
    return (p.x, p.y)


def scatter_geometry(sc: Scatter, spec: MapSpec, bounds: BaseGeometry, blockers: dict) -> BaseGeometry:
    """Irregular patches of one region type from thresholded noise over the eligible area (spec 4.6)."""
    cs = 5.0 if max(spec.size) <= 4000.0 else 10.0
    cols, rows = int(math.ceil(spec.size[0] / cs)), int(math.ceil(spec.size[1] / cs))
    grid = raster.Grid(spec.size[0], spec.size[1], cols, rows)
    a = sc.avoid
    sd = spec.seed + 8000 + 97 * sc.index
    blocked = [blockers["excluded_on"](sc.on) if sc.on else blockers["excluded"]]
    near = []
    if not blockers["lines"].is_empty:
        near.append(blockers["lines"])
    near.extend(blockers["crossings"])
    for (x, y), r in blockers["pois"]:
        near.append(Point(x, y).buffer(r, quad_segs=32))
    if near:
        nu = unary_union(near)
        if a > 0:
            # irregular clearance: the plain `avoid` buffer is the floor; a warped, wider buffer makes it uneven
            core = nu.buffer(a)
            wide = clean_polygon(warp_geometry(nu.buffer(CLEARANCE_OUTER * a), 0.5 * a, 3.0 * a, sd + 53), bounds)
            blocked.append(unary_union([core, wide]) if not wide.is_empty else core)
        else:
            blocked.append(nu)
    bl = [b for b in blocked if not b.is_empty]
    bm = raster.mask(unary_union(bl), grid) > 0.02 if bl else np.zeros(grid.shape, bool)
    ok = ~bm
    if sc.within is not None:
        ok &= raster.mask(Polygon(sc.within), grid) >= 0.5
    if sc.coverage <= 0 or not ok.any():
        return Polygon()
    xs = (np.arange(cols) + 0.5) * grid.cell_size_x
    ys = spec.size[1] - (np.arange(rows) + 0.5) * grid.cell_size_y
    n = noise.fbm2_grid(xs / sc.scale, ys / sc.scale, sd, octaves=4) * (1.0 - 0.4 * sc.clump)
    if sc.clump > 0:
        n = n + 1.2 * sc.clump * noise.fbm2_grid(xs / (4.0 * sc.scale), ys / (4.0 * sc.scale), sd + 31, octaves=2)
    q = float(np.quantile(n[ok], 1.0 - sc.coverage))
    f = np.where(ok, n - q, -2.0)
    g = field_to_polygons(f, 0.0, spec.size[1], grid.cell_size_x, SCATTER_MIN_AREA, 4.0,
                          hole_min=0.2 * SCATTER_MIN_AREA)
    g = clean_polygon(g, bounds)
    if bl and not g.is_empty:   # simplification may shave the clearance by a metre or two: cut it back exactly
        g = polygonal(g.difference(unary_union(bl)))
    return drop_small_parts(g, 0.0, SCATTER_MIN_AREA)


def build_geometries(spec: MapSpec) -> MapGeometry:
    """Build every region polygon, line (centreline, area) and crossing of a spec, clipped and valid.
    Order: regions; lines, parents before tributaries (a tributary end is snapped onto its parent's centreline);
    water lines clipped to lie outside lakes; crossings; automatic crossings (appended to `spec.crossings`);
    scatter entries, one region each: entries without `on` are inserted before the explicit regions, entries with
    `on` after them, in `spec.regions` (once)."""
    bounds = box(0.0, 0.0, spec.size[0], spec.size[1])
    types = spec.types
    spec.warnings = []
    regions = [region_geometry(r, spec.seed, bounds, spec.warnings) for r in spec.regions]
    lines: List[Optional[LineGeom]] = [None] * len(spec.lines)
    by_id = {ln.id: k for k, ln in enumerate(spec.lines) if ln.id is not None}
    for k in line_order(spec.lines):
        ln = spec.lines[k]
        parent = lines[by_id[ln.join]].centreline if ln.join is not None and ln.join in by_id else None
        lines[k] = line_geometry(ln, spec.seed, bounds, parent)
    lake_geoms = [regions[i] for i, r in enumerate(spec.regions)
                  if types.regions[r.type].get("height_mode") == "water" and not regions[i].is_empty]
    if lake_geoms:      # lakes own the water where a river meets them: no river stub inside a lake
        lakes = unary_union(lake_geoms)
        for k, ln in enumerate(spec.lines):
            if types.lines[ln.type].get("height_mode") == "water" and not lines[k].area.is_empty:
                lines[k].area = drop_small_parts(clean_polygon(lines[k].area.difference(lakes), bounds), 0.0, 1.0)
    crossings = [crossing_geometry(c, bounds) for c in spec.crossings]
    if spec.auto_crossings and not spec.auto_applied:
        for c, g in auto_crossings(spec, regions, lines, crossings, bounds):
            spec.crossings.append(c)
            crossings.append(g)
        spec.auto_applied = True
    if spec.scatter and not spec.scatter_applied:
        explicit = list(zip(spec.regions, regions))
        always = [g for r, g in explicit if types.regions[r.type].get("height_mode") in ("water", "ridge", "flatten")
                  and not g.is_empty]
        base_excl = unary_union(always) if always else Polygon()

        def excluded_on(on):
            more = [g for r, g in explicit if r.type not in on and not g.is_empty]
            return unary_union(more + ([base_excl] if not base_excl.is_empty else [])) if more else base_excl

        blockers = {
            "excluded": base_excl,
            "excluded_on": excluded_on,
            "lines": unary_union([l.area for l in lines if not l.area.is_empty]) if lines else Polygon(),
            "crossings": [g for g in crossings if not g.is_empty],
            "pois": [(p.at, p.r) for p in spec.all_pois()],
        }
        pre, post = [], []
        for sc in spec.scatter:
            reg = Region(index=-1, type=sc.type, shape="scatter", id="scatter%d" % sc.index,
                         height=types.regions[sc.type].get("height"), seed_key=1000 + sc.index, scatter=sc)
            (post if sc.on else pre).append((reg, scatter_geometry(sc, spec, bounds, blockers)))
        spec.regions = [r for r, _ in pre] + spec.regions + [r for r, _ in post]
        regions = [g for _, g in pre] + regions + [g for _, g in post]
        for i, r in enumerate(spec.regions):
            r.index = i
        spec.scatter_applied = True
    return MapGeometry(bounds=bounds, regions=regions, lines=lines, crossings=crossings)
