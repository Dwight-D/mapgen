"""Fringe of an `edge=fade` organic town: the arteries run on past the footprint, un-walled outskirt clusters flank them and
side streets (hamlets), thinning with distance (design: mapgen/TOWN_ORGANIC_SPEC.md section 5.3).

`plan(...)` is pure and deterministic (stream `fringe` + attempt suffix). Its result lists the extended artery polylines
(truncated where the last cluster ends, so each tip entry lies on a fringe district), the outskirt districts with their
density, and the hamlet streets.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

from .model import r2
from .params import stage_rng

Pt = Tuple[float, float]

STEP = 10.0               # m between polyline vertices of an extension or street
MARGIN = 14.0             # m a fringe element keeps from the map border
FOOT_GAP = 3.0            # m between a cluster and the footprint
MIN_EXT = 40.0            # m: an extension shorter than this is dropped (the entry stays on the footprint ring)
AREA_SLACK = 1.12         # a cluster is at least this times min_district_area (the warp changes areas by up to 25 %)
DENS_CORE, DENS_RESIDENTIAL = 1.0, 0.85


def density(t: float) -> float:
    """Outskirt density at position t in [0, 1] along the fringe."""
    return r2(0.55 * (1.0 - min(1.0, max(0.0, t))) + 0.1)


class Cluster:
    def __init__(self, key: str, poly: Polygon, t: float, label: str, art: int = -1, side: float = 0.0) -> None:
        self.key, self.poly, self.t, self.label, self.art, self.side = key, poly, t, label, art, side


class Plan:
    """`arteries` (extended polylines, same order as the input), `clusters`, `streets` ((id, polyline, artery index, junction
    vertex index on the extended artery))."""

    def __init__(self) -> None:
        self.arteries: List[List[Pt]] = []
        self.clusters: List[Cluster] = []
        self.streets: List[Tuple[str, List[Pt], int, int]] = []


def _extension(start: Pt, heading: float, length: float, bend: float, wob: float, ph: float) -> List[Pt]:
    out = [start]
    x, y = start
    n = int(length // STEP)
    for i in range(1, n + 1):
        s = i * STEP
        th = heading + bend * (s / length) + wob * math.sin(2 * math.pi * s / 110.0 + ph)
        x += STEP * math.cos(th)
        y += STEP * math.sin(th)
        q = (r2(x), r2(y))
        if q != out[-1]:
            out.append(q)
    return out


def _normals(line: Sequence[Pt]) -> List[Pt]:
    out = []
    for i in range(len(line)):
        a, b = line[max(0, i - 1)], line[min(len(line) - 1, i + 1)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        h = math.hypot(dx, dy) or 1.0
        out.append((-dy / h, dx / h))
    return out


def _flank(line: Sequence[Pt], i0: int, i1: int, side: float, depth: float, rng) -> Optional[Polygon]:
    """Ragged polygon between the polyline vertices i0..i1 (its border on the road) and a staircase of lots of depth
    `depth` (each lot 2-3 steps wide, depth 0.65-1.25 times) on `side` (+1 left, -1 right)."""
    nrm = _normals(line)
    pts: List[Pt] = [line[i] for i in range(i0, i1 + 1)]
    lots: List[Tuple[int, int]] = []
    a = i0
    while a < i1:
        b = min(i1, a + rng.choice((2, 3)))
        lots.append((a, b))
        a = b
    back: List[Pt] = []
    for (a, b) in reversed(lots):
        d = depth * rng.uniform(0.65, 1.25)
        for i in (b, a):
            j = rng.uniform(-1.2, 1.2)
            back.append((r2(line[i][0] + side * nrm[i][0] * (d + j)), r2(line[i][1] + side * nrm[i][1] * (d + j))))
    poly = Polygon(pts + back)
    if not poly.is_valid or poly.is_empty:
        return None
    return orient(poly, 1.0)


def _largest(g) -> Optional[Polygon]:
    if g.is_empty:
        return None
    if isinstance(g, Polygon):
        return g
    parts = [q for q in getattr(g, "geoms", []) if isinstance(q, Polygon)]
    return max(parts, key=lambda q: (q.area, q.bounds)) if parts else None


def _finish(poly: Polygon, occ: List[Polygon], box: Polygon, foreign: List[LineString], min_area: float) -> Optional[Polygon]:
    g = poly.difference(unary_union(occ)).intersection(box) if occ else poly.intersection(box)
    q = _largest(g)
    if q is None or q.area < min_area:
        return None
    q = orient(Polygon([(r2(x), r2(y)) for x, y in q.exterior.coords[:-1]]), 1.0)
    if not q.is_valid or q.area < min_area:
        return None
    sh = q.buffer(-0.6)
    if not sh.is_empty and any(sh.intersects(f) for f in foreign):
        return None
    return q


def _walk(line: List[Pt], side: float, fade_len: float, t0: float, rng, occ: List[Polygon], box: Polygon,
          foreign: List[LineString], min_area: float, first_sure: bool, max_n: int, start_i: int,
          skip: Optional[Tuple[int, int]] = None) -> List[Tuple[int, int, Polygon, float]]:
    """Walk one side of `line` from vertex `start_i` on, placing clusters with the §5.3 probability. Returns
    (i0, i1, polygon, t) with t the fringe position of the cluster's middle; placed polygons are appended to `occ`."""
    out: List[Tuple[int, int, Polygon, float]] = []
    s = start_i * STEP + rng.uniform(0.0, 25.0)
    first = first_sure
    while len(out) < max_n:
        t = min(1.0, t0 + s / fade_len)
        if t >= 1.0 and t0 == 0.0:
            break
        t = min(t, 0.95 if t0 == 0.0 else 0.6)
        D = 60.0 * (1.0 - 0.6 * t)
        need = int(math.ceil(1.4 * min_area * AREA_SLACK / D / STEP)) + rng.randint(0, 3)
        i0 = max(1, int(round(s / STEP)))
        i1 = i0 + need
        if i1 >= len(line):
            break
        p = (1.0 - t) ** 1.5 * (0.6 + 0.4 * rng.random())
        if skip is not None and not (i1 < skip[0] or i0 > skip[1]):
            s = (skip[1] + 1) * STEP
            continue
        if first or rng.random() < p:
            poly = _flank(line, i0, i1, side, D, rng)
            q = _finish(poly, occ, box, foreign, min_area) if poly is not None else None
            if q is not None:
                out.append((i0, i1, q, min(1.0, t0 + (i0 + i1) * STEP / 2.0 / fade_len)))
                occ.append(q)
                first = False
                s = i1 * STEP + 20.0 + 60.0 * t + rng.uniform(0.0, 15.0)
                continue
        s += 20.0
    return out


def plan(p: Dict[str, Any], seed: int, size: Tuple[float, float], sid: str, suffix: str, P0: Polygon,
         arts: List[List[Pt]], occupied: List[Polygon]) -> Plan:
    """Extend every artery of `arts` (ending on the footprint ring) and grow the fringe. `occupied` are the polygons already
    taken (the districts of the footprint)."""
    rng = stage_rng(sid, "organic", seed, "fringe" + suffix)
    min_area = float(p["min_district_area"])
    fade_len = float(p["fade_len"])
    box = Polygon([(MARGIN, MARGIN), (size[0] - MARGIN, MARGIN), (size[0] - MARGIN, size[1] - MARGIN), (MARGIN, size[1] - MARGIN)])
    keep_out = P0.buffer(FOOT_GAP)
    occ: List[Polygon] = [keep_out]
    res = Plan()

    # extensions (full length first, truncated to the last cluster later)
    exts: List[List[Pt]] = []
    for a in arts:
        end, prev = a[-1], a[-2]
        heading = math.atan2(end[1] - prev[1], end[0] - prev[0])
        L = fade_len * rng.uniform(0.85, 1.0)
        e = _extension(end, heading, L, rng.uniform(-0.45, 0.45), rng.uniform(0.0, 0.12), rng.uniform(0, 6.28))
        cut = len(e)
        for i in range(1, len(e)):
            q = Point(e[i])
            if not box.contains(q) or P0.contains(q):
                cut = i
                break
        exts.append(e[:cut])
    # keep extensions of different arteries apart
    lines = [LineString(a) for a in arts]
    for k, e in enumerate(exts):
        others = [LineString(arts[j][:-1] + exts[j]) for j in range(len(arts)) if j != k and len(arts[j]) > 1]
        for i in range(1, len(e)):
            if any(o.distance(Point(e[i])) < 40.0 for o in others):
                exts[k] = e[:i]
                break
    full = [LineString(a[:-1] + e) if len(e) > 1 else LineString(a) for a, e in zip(arts, exts)]

    tips: List[int] = []
    found: List[List[Tuple[int, int, Polygon, float, float]]] = []
    for k, e in enumerate(exts):
        got: List[Tuple[int, int, Polygon, float, float]] = []
        if len(e) * STEP > MIN_EXT:
            foreign = [full[j] for j in range(len(arts)) if j != k]
            for side in (1.0, -1.0):
                for (i0, i1, q, t) in _walk(e, side, fade_len, 0.0, rng, occ, box, foreign, min_area * AREA_SLACK, False, 6, 1):
                    got.append((i0, i1, q, t, side))
        found.append(got)
        tips.append(max([g[1] for g in got] + [0]))
    new_arts: List[List[Pt]] = []
    for k, a in enumerate(arts):
        tip = tips[k]
        new_arts.append(list(a) + exts[k][1:tip + 1] if tip > 0 else list(a))
        found[k].sort(key=lambda g: (g[0], g[4]))
        for n, (i0, i1, q, t, side) in enumerate(found[k]):
            res.clusters.append(Cluster("f%d_%d" % (k, n), q, t, "Outskirts %d.%d" % (k + 1, n + 1), k, side))
    res.arteries = new_arts

    # hamlets: side streets with clusters on both sides
    hrng = rng
    eligible = [k for k in range(len(arts)) if tips[k] >= 6]
    nh = int(p["hamlets"])
    streets: List[LineString] = []
    m = 0
    for _ in range(nh):
        if not eligible:
            break
        k = eligible[hrng.randrange(len(eligible))]
        e = exts[k]
        nrm = _normals(e)
        sgn = hrng.choice((1.0, -1.0))
        ok_street = None
        for _try in range(16):
            gaps = [i for i in range(3, tips[k] + 1) if not any(c.art == k and c.side == sgn and c.poly.distance(Point(e[i])) < 12.0 for c in res.clusters)]
            if not gaps:
                break
            j = gaps[hrng.randrange(len(gaps))]
            ang = math.atan2(sgn * nrm[j][1], sgn * nrm[j][0]) + hrng.uniform(-0.5, 0.5)
            Ls = hrng.uniform(140.0, 240.0)
            st = _extension(e[j], ang, Ls, hrng.uniform(-0.5, 0.5), hrng.uniform(0.0, 0.1), hrng.uniform(0, 6.28))
            cut = len(st)
            for i in range(1, len(st)):
                q = Point(st[i])
                if not box.contains(q) or P0.buffer(10.0).contains(q):
                    cut = i
                    break
            st = st[:cut]
            if len(st) < 8:
                continue
            ln = LineString(st[2:])
            if any(LineString(st[3:]).distance(full[x]) < 35.0 for x in range(len(full)) if x != k):
                continue
            if LineString(st[3:]).distance(full[k]) < 18.0:
                continue
            if any(ln.distance(o) < 30.0 for o in streets):
                continue
            if any(LineString(st[2:]).intersects(c.poly.buffer(6.0)) for c in res.clusters):
                continue
            ok_street = (j, st)
            break
        if ok_street is None:
            continue
        j, st = ok_street
        sline = LineString(st)
        foreign = [full[x] for x in range(len(full)) if x != k] + [full[k]] + streets
        tj = min(1.0, j * STEP / fade_len)
        sub: List[Tuple[int, int, Polygon, float, float]] = []
        trial_occ = list(occ)
        for side in (1.0, -1.0):
            got = _walk(st, side, fade_len, min(0.5, 0.5 * tj + 0.1), hrng, trial_occ, box, foreign[:-1] + [full[k]], min_area * AREA_SLACK,
                        side == 1.0, 2, 3)
            for g in got:
                sub.append((g[0], g[1], g[2], g[3], side))
        if not sub:
            continue
        tip = max(g[1] for g in sub)
        st = st[:tip + 1]
        sline = LineString(st)
        occ[:] = trial_occ
        sid_ = "h%d" % m
        res.streets.append((sid_, st, k, j))
        streets.append(sline)
        sub.sort(key=lambda g: (g[0], g[4]))
        for n, (i0, i1, q, t, side) in enumerate(sub):
            res.clusters.append(Cluster("h%d_%d" % (m, n), q, t, "Hamlet %d.%d" % (m + 1, n + 1)))
        m += 1
    return res
