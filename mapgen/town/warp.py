"""Coordinate warp: one smooth displacement applied to a finished settlement (design: mapgen/TOWN_ORGANIC_SPEC.md section 6).

`apply(settlement, params)` stitches and densifies every shared border, displaces every vertex by the same pure function of
the coordinates, rebuilds the derived graph data and keeps the result only when the full `check` passes (otherwise the
amplitude is reduced and retried; after the last retry the settlement is returned unwarped with `scale: 0`).
"""
from __future__ import annotations

import copy
import json
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import shapely
from shapely.errors import GEOSException
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union
from shapely.strtree import STRtree

from .. import noise
from . import graph as G
from .model import DistrictEdge, Settlement, Wall, polyline_length, r2
from .params import ParamError, stage_rng
from .water import wall_runs

Pt = Tuple[float, float]

KEYS = ("amp", "wavelength", "fine_amp", "fine_wavelength", "step", "fort_damp")
FULL = {"amp": 0.18, "wavelength": 1.2, "fine_amp": 0.012, "fine_wavelength": 0.15, "step": 10.0, "fort_damp": 0.8}
RANGES = {"amp": (0.0, 0.35), "wavelength": (0.4, 3.0), "fine_amp": (0.0, 0.03), "fine_wavelength": (0.05, 0.4),
          "step": (4.0, 30.0), "fort_damp": (0.0, 1.0)}
RETRIES = 6
SHRINK = 0.75
STITCH_TOL = 0.02          # m: a vertex this close to an edge is inserted into it
DOC = {
    "warp": "curve every road, border and wall with one smooth displacement: a strength 0-1, or `amp=..,wavelength=..,fine_amp=..,"
            "fine_wavelength=..,step=..,fort_damp=..` (default none)",
    "amp": "large-scale amplitude, fraction of the settlement radius, 0-0.35 (0.18 at strength 1)",
    "wavelength": "large-scale feature size in settlement radii, 0.4-3 (1.2)",
    "fine_amp": "small-scale amplitude, fraction of the settlement radius, 0-0.03 (0.012 at strength 1)",
    "fine_wavelength": "small-scale feature size in settlement radii, 0.05-0.4 (0.15)",
    "step": "densification step in metres, 4-30 (10)",
    "fort_damp": "displacement reduction inside and within 40 m of fort districts, 0-1 (0.8)",
}


def is_warp_param(name: str) -> bool:
    return name == "warp"


# --- parameter parsing ------------------------------------------------------------------------------------------

def _from_strength(a: float) -> Dict[str, Any]:
    out = dict(FULL)
    out["amp"] = round(FULL["amp"] * a, 6)
    out["fine_amp"] = round(FULL["fine_amp"] * a, 6)
    return out


def resolve(raw: Any) -> Optional[Dict[str, Any]]:
    """Canonical dict (every key filled; `scale` and `seed` only when given), None for no warp. Raises ParamError listing every problem."""
    if raw is None or raw is False or (isinstance(raw, str) and raw.strip().lower() in ("", "none", "false")):
        return None
    if isinstance(raw, str):
        text = raw.strip()
        try:
            raw = float(text)
        except ValueError:
            try:
                raw = json.loads(text)
            except ValueError:
                raw = {}
                for part in text.split(","):
                    if "=" not in part:
                        raise ParamError(["warp: cannot parse %r (expected a strength 0-1 or k=v,k=v)" % text])
                    k, v = part.split("=", 1)
                    raw[k.strip()] = v.strip()
    if isinstance(raw, bool):
        raise ParamError(["warp: expected a strength 0-1 or a mapping, got %r" % (raw,)])
    if isinstance(raw, (int, float)):
        if not (0.0 <= float(raw) <= 1.0):
            raise ParamError(["warp: strength %r outside [0, 1]" % (raw,)])
        return None if float(raw) == 0.0 else _from_strength(float(raw))
    if not isinstance(raw, dict):
        raise ParamError(["warp: expected a strength 0-1 or a mapping, got %r" % (raw,)])
    errs: List[str] = []
    out = dict(FULL)
    for k in sorted(raw):
        v = raw[k]
        if k not in KEYS and k not in ("scale", "seed"):
            errs.append("warp: unknown key %r (known: %s, scale, seed)" % (k, ", ".join(KEYS)))
            continue
        try:
            if isinstance(v, bool):
                raise ValueError
            x = float(v)
        except (TypeError, ValueError):
            errs.append("warp.%s: expected a number, got %r" % (k, v))
            continue
        if k == "seed":
            if x != int(x) or not (0 <= x < (1 << 30)):
                errs.append("warp.seed: expected an integer 0-%d, got %r" % ((1 << 30) - 1, v))
            else:
                out[k] = int(x)
        elif k == "scale":
            if not (0.0 <= x <= 1.0):
                errs.append("warp.scale: %r outside [0, 1]" % (v,))
            else:
                out[k] = x
        else:
            lo, hi = RANGES[k]
            if not (lo <= x <= hi):
                errs.append("warp.%s: %r outside [%g, %g]" % (k, v, lo, hi))
            else:
                out[k] = x
    if errs:
        raise ParamError(errs)
    return out


# --- the walker: every geometry of a settlement goes through one `seq` / `pt` pair -------------------------------

class _Collect:
    def __init__(self) -> None:
        self.pts: List[Pt] = []

    def seq(self, pts, closed):
        self.pts.extend((p[0], p[1]) for p in pts)
        return pts

    def pt(self, p):
        self.pts.append((p[0], p[1]))
        return p


def _walk(s: Settlement, f, remap_open: bool = False) -> None:
    """Replace, in place, every polygon ring, polyline and point of `s` by what `f.seq(points, closed)` / `f.pt(point)` return."""
    s.footprint = f.seq(s.footprint, True)
    for d in s.districts:
        d.polygon = f.seq(d.polygon, True)
    for n in s.layers:
        n.polygon = f.seq(n.polygon, True)
    for r in s.roads:
        r.polyline = f.seq(r.polyline, False)
    for w in s.walls:
        w.polyline = f.seq(w.polyline, False)
        if remap_open and w.open_shore:
            off = f.offsets
            w.open_shore = [[off[a], off[b]] for a, b in w.open_shore]
        w.towers = [f.pt(p) for p in w.towers]
    for g in s.gates:
        g.at = f.pt(g.at)
    for l in s.landmarks:
        l.at = f.pt(l.at)
    for n in s.graph_nodes:
        n.at = f.pt(n.at)
    for key, closed_keys in (("bays", ("polygon",)), ("rivers", ("polygon",)), ("islands", ("polygon",))):
        for item in s.water.get(key, []):
            for k in closed_keys:
                item[k] = f.seq(item[k], True)
            if "polyline" in item:
                item["polyline"] = f.seq(item["polyline"], False)
            if "at" in item:
                item["at"] = f.pt(item["at"])


# --- welding ---------------------------------------------------------------------------------------------------

WELD_TOL = 0.03          # m: vertices this close are one vertex (generators leave 0.01 m twins at corners)


class _Weld:
    """Maps every vertex of a cluster of near-identical vertices to the cluster's smallest member and drops the repeats."""

    def __init__(self, points: Sequence[Pt], pinned: Sequence[Pt] = ()) -> None:
        from scipy.spatial import cKDTree
        V = sorted(set(points))
        index = {p: i for i, p in enumerate(V)}
        pin = {index[p]: p for p in pinned if p in index}     # graph-node positions: two of them are never merged
        parent = list(range(len(V)))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i
        if V:
            for i, j in cKDTree(np.array(V, dtype=np.float64)).query_pairs(WELD_TOL):
                a, b = find(i), find(j)
                if a != b:
                    lo, hi = min(a, b), max(a, b)
                    if lo in pin and hi in pin:
                        continue
                    parent[hi] = lo
                    if hi in pin:
                        pin[lo] = pin.pop(hi)
        self.rep = {V[i]: V[find(i)] for i in range(len(V))}
        self.offsets: List[int] = []

    def seq(self, pts, closed):
        out: List[Pt] = []
        self.offsets = []
        for p in pts:
            q = self.rep[(p[0], p[1])]
            if not out or out[-1] != q:
                out.append(q)
            self.offsets.append(len(out) - 1)
        if closed and len(out) > 1 and out[0] == out[-1]:
            out.pop()
            self.offsets = [min(o, len(out) - 1) for o in self.offsets]
        return [[p[0], p[1]] for p in out]

    def pt(self, p):
        q = self.rep[(p[0], p[1])]
        return [q[0], q[1]]


# --- densification ---------------------------------------------------------------------------------------------

class _Densify:
    """Inserts every foreign vertex lying on an edge, then equal sub-steps computed from the lexicographically smaller endpoint,
    so a border has the same vertex list from either side. Original vertices are copied exactly."""

    def __init__(self, points: Sequence[Pt], step: float) -> None:
        self.step = step
        self.V = sorted(set(points))
        self.tree = STRtree(shapely.points(np.array(self.V, dtype=np.float64))) if self.V else None
        self.cache: Dict[Tuple[Pt, Pt], List[Pt]] = {}
        self.offsets: List[int] = []

    def _stitch(self, lo: Pt, hi: Pt) -> List[Pt]:
        if self.tree is None:
            return []
        t = STITCH_TOL
        box = shapely.box(min(lo[0], hi[0]) - t, min(lo[1], hi[1]) - t, max(lo[0], hi[0]) + t, max(lo[1], hi[1]) + t)
        dx, dy = hi[0] - lo[0], hi[1] - lo[1]
        L2 = dx * dx + dy * dy
        found = []
        for i in self.tree.query(box):
            p = self.V[int(i)]
            if p == lo or p == hi:
                continue
            u = ((p[0] - lo[0]) * dx + (p[1] - lo[1]) * dy) / L2
            if not (0.0 < u < 1.0):
                continue
            if math.hypot(p[0] - (lo[0] + u * dx), p[1] - (lo[1] + u * dy)) <= t:
                found.append((u, p))
        found.sort()
        return [p for _, p in found]

    def _edge(self, lo: Pt, hi: Pt) -> List[Pt]:
        """Interior points of the edge lo -> hi (lo < hi lexicographically), in order."""
        key = (lo, hi)
        got = self.cache.get(key)
        if got is not None:
            return got
        chain = [lo] + self._stitch(lo, hi) + [hi]
        out: List[Pt] = []
        for a, b in zip(chain, chain[1:]):
            k = max(1, int(math.ceil(math.hypot(b[0] - a[0], b[1] - a[1]) / self.step - 1e-9)))
            for i in range(1, k):
                out.append((r2(a[0] + (b[0] - a[0]) * i / k), r2(a[1] + (b[1] - a[1]) * i / k)))
            if b is not hi:
                out.append(b)
        self.cache[key] = out
        return out

    def _between(self, a: Pt, b: Pt) -> List[Pt]:
        if a == b:
            return []
        if a < b:
            return self._edge(a, b)
        return self._edge(b, a)[::-1]

    def seq(self, pts, closed):
        P = [(p[0], p[1]) for p in pts]
        n = len(P)
        out: List[Pt] = []
        self.offsets = []
        for i in range(n):
            self.offsets.append(len(out))
            out.append(P[i])
            if i + 1 < n:
                out.extend(self._between(P[i], P[i + 1]))
            elif closed and n > 1:
                out.extend(self._between(P[i], P[0]))
        return [[p[0], p[1]] for p in out]

    def pt(self, p):
        return p


# --- displacement ----------------------------------------------------------------------------------------------

class _Field:
    def __init__(self, s: Settlement, wp: Dict[str, Any], seed: int, scale: float) -> None:
        self.Rs = math.sqrt(Polygon(s.footprint).area / math.pi)
        self.wp = wp
        self.s1 = seed
        self.s2 = (seed * 2654435761 + 40503) % (1 << 30)
        self.scale = scale
        forts = [Polygon(d.polygon) for d in s.districts if d.kind == "citadel"]
        self.fort = unary_union(forts) if forts and wp["fort_damp"] > 0 else None
        self.cache: Dict[Pt, Pt] = {}
        self.offsets: List[int] = []

    def raw(self, xy: np.ndarray) -> np.ndarray:
        """Unrounded displaced positions of an (n, 2) array."""
        wp, Rs = self.wp, self.Rs
        x, y = xy[:, 0], xy[:, 1]
        L, F = wp["wavelength"] * Rs, wp["fine_wavelength"] * Rs
        a, fa = wp["amp"] * Rs, wp["fine_amp"] * Rs
        dx = a * noise.fbm2(x / L, y / L, self.s1, 3) + fa * noise.fbm2(x / F, y / F, self.s1 + 11, 2)
        dy = (a * noise.fbm2(x / L + 31.7, y / L - 12.3, self.s2, 3)
              + fa * noise.fbm2(x / F + 31.7, y / F - 12.3, self.s2 + 11, 2))
        k = np.full(x.shape, self.scale, dtype=np.float64)
        if self.fort is not None:
            dist = shapely.distance(shapely.points(xy), self.fort)
            t = np.clip(dist / 40.0, 0.0, 1.0)
            k = k * (1.0 - wp["fort_damp"] * (1.0 - t * t * (3.0 - 2.0 * t)))
        return np.stack([x + k * dx, y + k * dy], axis=1)

    def _batch(self, pts: List[Pt]) -> None:
        miss = sorted({p for p in pts if p not in self.cache})
        if not miss:
            return
        out = self.raw(np.array(miss, dtype=np.float64))
        for p, q in zip(miss, out):
            self.cache[p] = (r2(q[0]), r2(q[1]))

    def seq(self, pts, closed):
        P = [(p[0], p[1]) for p in pts]
        self._batch(P)
        return [[self.cache[p][0], self.cache[p][1]] for p in P]

    def pt(self, p):
        self._batch([(p[0], p[1])])
        q = self.cache[(p[0], p[1])]
        return [q[0], q[1]]

    def normal(self, at, n) -> List[float]:
        """The outward normal pushed through the displacement: perpendicular of the displaced tangent."""
        tx, ty = -n[1], n[0]
        pts = np.array([[at[0] + 0.5 * tx, at[1] + 0.5 * ty], [at[0] - 0.5 * tx, at[1] - 0.5 * ty]], dtype=np.float64)
        q = self.raw(pts)
        ux, uy = q[0][0] - q[1][0], q[0][1] - q[1][1]
        h = math.hypot(ux, uy)
        if h < 1e-9:
            return list(n)
        return [round(uy / h, 4) + 0.0, round(-ux / h, 4) + 0.0]


# --- procedure -------------------------------------------------------------------------------------------------

def _rebuild(s: Settlement) -> None:
    from shapely.geometry import Polygon as _P
    for d in s.districts:
        d.area = r2(_P(d.polygon).area)
    nodes = {n.id: n for n in s.graph_nodes}
    s.graph_edges = G.movement_edges(s.roads, nodes)
    G.attach_districts(s.graph_nodes, s.districts)
    pseudo: List[Wall] = []
    for w in s.walls:
        for k, run in enumerate(wall_runs(w)):
            pseudo.append(Wall(id=w.id if k == 0 else "%s_%d" % (w.id, k + 1), kind=w.kind, polyline=run, thickness=w.thickness))
    s.district_nodes, edges = G.district_graph(s.districts, s.roads, pseudo, s.gates)
    byid = {e.id: e for e in edges}
    polys = [(d.id, Polygon(d.polygon)) for d in s.districts]
    for br in [r for r in s.roads if r.cls == "bridge"]:
        ends = [Point(br.polyline[0]), Point(br.polyline[-1])]
        sides = [sorted(did for did, pg in polys if pg.distance(p) <= G.SNAP) for p in ends]
        for x in sides[0]:
            for y in sides[1]:
                if x == y:
                    continue
                a, b = sorted((x, y))
                eid = "%s~%s" % (a, b)
                if eid in byid:
                    if br.id not in byid[eid].roads:
                        byid[eid].roads.append(br.id)
                        byid[eid].roads.sort()
                else:
                    byid[eid] = DistrictEdge(id=eid, a=a, b=b, kind="bridge", length=r2(polyline_length(br.polyline)),
                                             passable=True, gates=[], roads=[br.id])
    s.district_edges = sorted(byid.values(), key=lambda e: e.id)


def _displace(sd: Settlement, field: _Field) -> Settlement:
    out = copy.deepcopy(sd)
    _walk(out, field)
    for g, g0 in zip(out.gates, sd.gates):
        g.normal = field.normal(g0.at, g0.normal)
    _rebuild(out)
    return out


def densify(s: Settlement, step: float) -> Settlement:
    """Copy of `s` with near-identical vertices welded and every polyline and ring stitched (foreign vertices on an edge inserted) and densified."""
    sd = copy.deepcopy(s)
    col = _Collect()
    _walk(copy.deepcopy(s), col)
    _walk(sd, _Weld(col.pts, [(n.at[0], n.at[1]) for n in s.graph_nodes]), remap_open=True)
    col = _Collect()
    _walk(copy.deepcopy(sd), col)
    _walk(sd, _Densify(col.pts, step), remap_open=True)
    return sd


def apply(s: Settlement, wp: Dict[str, Any]) -> Settlement:
    """Warp `s`. `wp` is the resolved parameter dict; a `scale` in it is used as given (no retries), otherwise 1, 0.75, ... are tried
    in turn. Never raises: when no scale passes the checks the settlement is returned unwarped with `scale: 0`."""
    from .check import check
    wp = dict(wp)
    seed = wp.get("seed")
    if seed is None:
        seed = stage_rng(s.id, s.generator, s.seed, "warp").randrange(1 << 30)
    if "scale" in wp:
        scales = [round(float(wp["scale"]), 6)]
    else:
        scales = [round(SHRINK ** k, 6) for k in range(RETRIES + 1)]
    base_edges = {e.id for e in s.district_edges if e.length > 3.0 * G.BORDER_MIN}   # shorter ones are borderline adjacencies
    result = None
    sd = None
    for sc in scales:
        if sc <= 0.0:
            break
        if sd is None:
            sd = densify(s, wp["step"])
        try:
            cand = _displace(sd, _Field(s, wp, seed, sc))
        except ValueError:
            continue
        cand.params = dict(s.params)
        cand.params["warp"] = dict(_echo(wp, sc, seed))
        if not base_edges <= {e.id for e in cand.district_edges}:      # an adjacency was lost
            continue
        try:
            if check(cand):
                continue
        except GEOSException:      # a fold made a polygon invalid
            continue
        result = cand
        break
    if result is None:
        result = copy.deepcopy(s)
        result.params = dict(s.params)
        result.params["warp"] = dict(_echo(wp, 0.0, seed))
    return result


def _echo(wp: Dict[str, Any], scale: float, seed: int) -> Dict[str, Any]:
    out = {k: wp[k] for k in KEYS}
    out["scale"] = scale
    out["seed"] = seed
    return out
