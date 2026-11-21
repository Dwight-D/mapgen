"""Shape field and outline: the footprint of an organic settlement as the zero contour of a scalar field
(design: mapgen/TOWN_ORGANIC_SPEC.md section 4).

`footprint(size, seed, ...)` returns a `Shape` (footprint ring, satellite parcels, the field inputs and the echoed
parameters). Splotchy blobs, tongues along ribbon axes and satellite lobes come from the field; pinches, thin isthmuses
and self-intersection are removed by morphological cleanup and rejected by guards (the attempt is redrawn from the next
seed stream, up to `ATTEMPTS`, then `GenerationError`).
"""
from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy.spatial import cKDTree
from shapely.geometry import MultiPolygon, Point, Polygon
from shapely.geometry.polygon import orient
from skimage import measure

from .. import noise
from .gen_radial import GenerationError
from .model import r2
from .params import ParamError, stage_rng
from .water import _catmull

Pt = Tuple[float, float]

ATTEMPTS = 8
W_CORE, W_RIB, W_LOBE, TAU = 1.0, 0.95, 0.7, 0.32
NOISE_GAIN = 1.5          # noise weight = irregularity * NOISE_GAIN
AREA_REF = 0.55           # footprint area is compared with AREA_REF * pi * R^2
AREA_LO, AREA_HI = 0.6, 1.6
MARGIN = 10.0             # m the footprint keeps from the map border
NECK = 40.0               # m minimum neck width
NECK_KEEP = 0.97

# name -> (lo, hi, kind, default or None = drawn, draw range)
DEFS: Dict[str, Tuple[float, float, str, Any, Optional[Tuple[float, float]]]] = {
    "radius_frac": (0.3, 0.95, "float", None, (0.4, 0.65)),
    "irregularity": (0.0, 0.5, "float", None, (0.15, 0.4)),
    "lobes": (0, 6, "int", None, (0, 3)),
    "ribbons": (0, 4, "int", None, (0, 2)),
    "hub_offset": (0.0, 0.3, "float", None, (0.0, 0.15)),
    "min_solidity": (0.4, 0.9, "float", 0.62, None),
    "min_district_area": (100.0, 20000.0, "float", 1600.0, None),
}


class Shape:
    """Result of `footprint`: `ring` (CCW, rounded, no repeated end point), `satellites` (rings of other components of
    area >= min_district_area), `hub`, `R`, `axes` (ribbon polylines hub to tip), `lobes` ((cx, cy, r) tuples), `params`
    (echo of every resolved parameter), `attempt` (index of the accepted attempt), `solidity`, `area`."""

    def __init__(self, **kw: Any) -> None:
        self.__dict__.update(kw)


def resolve(params: Optional[Dict[str, Any]], rng) -> Dict[str, Any]:
    """Validate `params` and draw every absent key from `rng` in DEFS order (defaulted keys are never drawn)."""
    params = dict(params or {})
    bad = [k for k in params if k not in DEFS]
    errs = ["unknown shape parameter %r" % k for k in sorted(bad)]
    out: Dict[str, Any] = {}
    for name, (lo, hi, kind, default, drawn) in DEFS.items():
        if name in params:
            v = params[name]
            try:
                v = int(v) if kind == "int" else float(v)
            except (TypeError, ValueError):
                errs.append("%s: %r is not a number" % (name, v))
                continue
            if not (lo <= v <= hi):
                errs.append("%s: %s outside %s-%s" % (name, v, lo, hi))
                continue
        elif default is not None:
            v = default
        else:
            v = rng.randint(int(drawn[0]), int(drawn[1])) if kind == "int" else round(rng.uniform(*drawn), 3)
        out[name] = v
    if errs:
        raise ParamError(errs)
    return out


def _smooth(t: np.ndarray) -> np.ndarray:
    """g(t) = smoothstep(1, 0, t): 1 at t <= 0, 0 at t >= 1."""
    t = np.clip(t, 0.0, 1.0)
    return 1.0 - t * t * (3.0 - 2.0 * t)


def _axes(hub: Pt, R: float, n: int, rng, curvature: float = 0.5) -> List[List[Pt]]:
    """Ribbon axes: gently bent Catmull-Rom curves from the hub outward, at least 60 degrees apart."""
    base = rng.uniform(0, 2 * math.pi)
    out: List[List[Pt]] = []
    for i in range(n):
        th = base + i * 2 * math.pi / max(n, 1) + rng.uniform(-0.25, 0.25) * 2 * math.pi / max(n, 1)
        L = R * rng.uniform(1.5, 2.1)
        u, nrm = (math.cos(th), math.sin(th)), (-math.sin(th), math.cos(th))
        lat = curvature * L / 3.0 * rng.uniform(-1, 1)
        ctrl = [hub,
                (hub[0] + u[0] * L * 0.5 + nrm[0] * lat, hub[1] + u[1] * L * 0.5 + nrm[1] * lat),
                (hub[0] + u[0] * L + nrm[0] * lat * 0.4, hub[1] + u[1] * L + nrm[1] * lat * 0.4)]
        out.append(_catmull(ctrl, per=8))
    return out


def _lobes(hub: Pt, R: float, n: int, rng) -> List[Tuple[float, float, float]]:
    base = rng.uniform(0, 2 * math.pi)
    out = []
    for i in range(n):
        th = base + i * 2 * math.pi / max(n, 1) + rng.uniform(-0.3, 0.3) * 2 * math.pi / max(n, 1)
        d = R * rng.uniform(0.7, 1.15)
        out.append((hub[0] + d * math.cos(th), hub[1] + d * math.sin(th), R * rng.uniform(0.22, 0.4)))
    return out


def _ribbon_term(X: np.ndarray, Y: np.ndarray, axis: Sequence[Pt], rho: float) -> np.ndarray:
    """g(dist to the axis / rho) * taper, taper falling linearly from 1 at the hub end to 0 at the tip."""
    a = np.asarray(axis, dtype=np.float64)
    seg = np.hypot(*(a[1:] - a[:-1]).T)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    # dense samples along the polyline so nearest-sample distance is accurate to ~rho/10
    step = rho / 10.0
    ts = np.arange(0.0, cum[-1] + step, step)
    ts = np.minimum(ts, cum[-1])
    sx = np.interp(ts, cum, a[:, 0])
    sy = np.interp(ts, cum, a[:, 1])
    tree = cKDTree(np.stack([sx, sy], axis=1))
    d, idx = tree.query(np.stack([X.ravel(), Y.ravel()], axis=1))
    taper = 1.0 - ts[idx] / cum[-1]
    return (_smooth(d / rho) * taper).reshape(X.shape)


def field_grid(xs: np.ndarray, ys: np.ndarray, hub: Pt, R: float, axes: Sequence[Sequence[Pt]], rhos: Sequence[float],
               lobes: Sequence[Tuple[float, float, float]], w_noise: float, noise_seed: int,
               suitability: Optional[Callable[[np.ndarray, np.ndarray], np.ndarray]] = None,
               tau: float = TAU) -> np.ndarray:
    """The shape field F over the grid of axes `xs` (columns) and `ys` (rows); result shape (len(ys), len(xs))."""
    X, Y = np.meshgrid(xs, ys)
    F = W_CORE * _smooth(np.hypot(X - hub[0], Y - hub[1]) / R)
    for ax, rho in zip(axes, rhos):
        F += W_RIB * _ribbon_term(X, Y, ax, rho)
    for cx, cy, r in lobes:
        F += W_LOBE * _smooth(np.hypot(X - cx, Y - cy) / r)
    if w_noise:
        lam = 0.5 * R
        F += w_noise * NOISE_GAIN * noise.fbm2_grid(xs / lam, ys / lam, noise_seed, 3)
    if suitability is not None:
        F += 1.0 * (np.asarray(suitability(X, Y), dtype=np.float64) - 0.5)
    return F - tau


def _contours(F: np.ndarray, xs: np.ndarray, ys: np.ndarray) -> List[Polygon]:
    """Closed zero contours of F as valid polygons (outer rings; hole contours are filled by the later hole drop)."""
    P = np.pad(F, 1, mode="constant", constant_values=-1.0)          # a border of outside closes every contour
    dx, dy = xs[1] - xs[0], ys[1] - ys[0]
    polys: List[Polygon] = []
    for c in measure.find_contours(P, 0.0):
        if len(c) < 4:
            continue
        pts = [(xs[0] + (col - 1) * dx, ys[0] + (row - 1) * dy) for row, col in c]
        pg = Polygon(pts)
        if not pg.is_valid:
            pg = pg.buffer(0)
        if pg.is_empty:
            continue
        polys.extend(pg.geoms if isinstance(pg, MultiPolygon) else [pg])
    # keep outermost contours only: a contour inside another is a hole (the field dips below tau inside a blob)
    polys.sort(key=lambda p: -p.area)
    outer: List[Polygon] = []
    for p in polys:
        q = p.representative_point()
        if not any(o.contains(q) for o in outer):
            outer.append(p)
    return outer


def _clean(P: Polygon, R: float) -> Optional[Polygon]:
    """Close small bays, open small spurs, drop holes, smooth, simplify, orient CCW, round."""
    c, o = 0.04 * R, 0.05 * R
    P = P.buffer(c, join_style=1).buffer(-c, join_style=1)
    P = P.buffer(-o, join_style=1).buffer(o, join_style=1)
    if P.is_empty:
        return None
    if P.geom_type == "MultiPolygon":
        P = max(P.geoms, key=lambda g: g.area)
    P = Polygon(P.exterior)
    pts = list(P.exterior.coords)[:-1]
    for _ in range(2):
        nxt: List[Pt] = []
        for i in range(len(pts)):
            a, b = pts[i], pts[(i + 1) % len(pts)]
            nxt.append((0.75 * a[0] + 0.25 * b[0], 0.75 * a[1] + 0.25 * b[1]))
            nxt.append((0.25 * a[0] + 0.75 * b[0], 0.25 * a[1] + 0.75 * b[1]))
        pts = nxt
    P = Polygon(pts).simplify(1.0)
    if P.is_empty or P.geom_type != "Polygon":
        return None
    P = orient(P, 1.0)
    ring = [(r2(x), r2(y)) for x, y in list(P.exterior.coords)[:-1]]
    out: List[Pt] = []
    for p in ring:
        if not out or p != out[-1]:
            out.append(p)
    if len(out) > 1 and out[0] == out[-1]:
        out.pop()
    return Polygon(out) if len(out) >= 3 else None


def solidity(P: Polygon) -> float:
    return P.area / P.convex_hull.area


def neck_ok(P: Polygon, width: float = NECK, keep: float = NECK_KEEP) -> bool:
    h = width / 2.0
    opened = P.buffer(-h, join_style=1).buffer(h, join_style=1)
    if opened.area < keep * P.area:
        return False
    parts = list(opened.geoms) if opened.geom_type == "MultiPolygon" else [opened]
    return sum(1 for g in parts if g.area >= 0.05 * P.area) <= 1     # a thin isthmus between two bodies splits them


def _guards(P: Optional[Polygon], size: Tuple[float, float], R: float, min_solidity: float) -> Optional[str]:
    """None when the footprint passes every guard, else the reason."""
    if P is None or not P.is_valid or P.is_empty:
        return "invalid"
    if not P.exterior.is_simple:
        return "self-intersecting"
    x0, y0, x1, y1 = P.bounds
    if x0 < MARGIN or y0 < MARGIN or x1 > size[0] - MARGIN or y1 > size[1] - MARGIN:
        return "outside the map"
    ref = AREA_REF * math.pi * R * R
    if not (AREA_LO * ref <= P.area <= AREA_HI * ref):
        return "area %.0f outside %.0f-%.0f" % (P.area, AREA_LO * ref, AREA_HI * ref)
    if solidity(P) < min_solidity:
        return "solidity %.2f below %.2f" % (solidity(P), min_solidity)
    if not neck_ok(P):
        return "neck narrower than %g m" % NECK
    return None


def footprint(size: Tuple[float, float], seed: int, sid: str = "town", params: Optional[Dict[str, Any]] = None,
              edge: str = "wall", axes: Optional[Sequence[Sequence[Pt]]] = None,
              suitability: Optional[Callable[[np.ndarray, np.ndarray], np.ndarray]] = None,
              generator: str = "organic") -> Shape:
    """Build the footprint. `params` holds any of DEFS (absent keys are drawn from stream `params`). `axes` are the
    generator's arteries (hub-to-tip polylines) whose first `ribbons` grow tongues; None draws standalone axes.
    `edge="fade"` relaxes `min_solidity` to 0.5 when it is not given and keeps satellite parcels (else dropped)."""
    given = dict(params or {})
    if edge == "fade" and "min_solidity" not in given:
        given["min_solidity"] = 0.5
    p = resolve(given, stage_rng(sid, generator, seed, "params"))
    R = p["radius_frac"] * min(size) / 2.0
    cx, cy = size[0] / 2.0, size[1] / 2.0
    why: List[str] = []
    for attempt in range(ATTEMPTS):
        rng = stage_rng(sid, generator, seed, "shape" if attempt == 0 else "shape%d" % attempt)
        ha = rng.uniform(0, 2 * math.pi)
        hub = (cx + p["hub_offset"] * R * math.cos(ha), cy + p["hub_offset"] * R * math.sin(ha))
        lobes = _lobes(hub, R, p["lobes"], stage_rng(sid, generator, seed, "lobes" if attempt == 0 else "lobes%d" % attempt))
        ax_all = [list(a) for a in axes] if axes is not None else _axes(hub, R, p["ribbons"], rng)
        ax = ax_all[:p["ribbons"]]
        rhos = [0.34 * R * rng.uniform(0.7, 1.2) for _ in ax]
        noise_seed = rng.randrange(1 << 30)
        pad = 1.3 * R
        pts = [hub] + [(a, b) for a in ax for (a, b) in a] + [(l[0], l[1]) for l in lobes]
        reach = max([R] + [math.hypot(q[0] - hub[0], q[1] - hub[1]) + 0.6 * R for q in pts])
        x0, x1 = max(0.0, hub[0] - max(reach, pad)), min(size[0], hub[0] + max(reach, pad))
        y0, y1 = max(0.0, hub[1] - max(reach, pad)), min(size[1], hub[1] + max(reach, pad))
        cell = max(4.0, R / 80.0)
        xs = np.arange(x0, x1 + cell, cell)
        ys = np.arange(y0, y1 + cell, cell)
        F = field_grid(xs, ys, hub, R, ax, rhos, lobes, p["irregularity"], noise_seed, suitability)
        comps = _contours(F, xs, ys)
        main = None
        for c in comps:
            if c.contains(Point(hub)):
                main = c
                break
        if main is None:
            why.append("no component at the hub")
            continue
        P0 = _clean(main, R)
        reason = _guards(P0, size, R, p["min_solidity"])
        if reason:
            why.append(reason)
            continue
        sats: List[List[Pt]] = []
        if edge == "fade":
            for c in comps:
                if c is main:
                    continue
                q = _clean(c, R)
                if q is not None and q.area >= p["min_district_area"] and q.is_valid and not q.intersects(P0):
                    sats.append([tuple(t) for t in list(q.exterior.coords)[:-1]])
        ring = [tuple(t) for t in list(P0.exterior.coords)[:-1]]
        return Shape(ring=ring, satellites=sats, hub=hub, R=R, axes=ax, lobes=lobes, params=dict(p), attempt=attempt,
                     solidity=solidity(P0), area=P0.area, noise_seed=noise_seed, rhos=rhos)
    raise GenerationError("shape: no acceptable footprint in %d attempts (%s)" % (ATTEMPTS, "; ".join(why)))
