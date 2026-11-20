"""`voronoi` generator: Voronoi-cell districts on an outward spiral of seeds, major roads on district borders from gates
to a plaza, an angular wall around the union; `organic` switches to a high-jitter, warped regime
(design: mapgen/TOWN_SPEC.md section 7.2)."""
from __future__ import annotations

import heapq
import math
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np
from scipy.spatial import Voronoi
from shapely.geometry import LineString, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

from . import graph as G
from .gen_radial import GenerationError
from .model import District, Gate, GraphNode, Landmark, Road, Settlement, Wall, pt, r2
from .params import ParamDef, stage_rng

NAME = "voronoi"
GOLDEN = 2.399963229728653          # golden angle, radians
ATTEMPTS = 8
ORGANIC_JITTER, ORGANIC_WARP = 0.9, 0.12

DEFS = [
    ParamDef("patches", "int", 8, 30, "Voronoi cells of the town (before merging)", draw_lo=12, draw_hi=24),
    ParamDef("spiral_tightness", "float", 0.8, 1.2, "seed angle step relative to the golden angle (1 = even packing)",
             draw_lo=0.9, draw_hi=1.1),
    ParamDef("jitter", "float", 0.0, 1.0, "seed displacement, fraction of the mean cell spacing", draw_lo=0.1, draw_hi=0.5),
    ParamDef("lloyd", "int", 0, 3, "Lloyd relaxation passes (0 keeps the raw cells)", draw_lo=1, draw_hi=3),
    ParamDef("centre_bias", "float", 0.7, 1.6, "above 1 packs smaller cells toward the centre", draw_lo=0.9, draw_hi=1.3),
    ParamDef("anisotropy", "float", 1.0, 1.6, "stretch along the rotated x axis, area preserving", draw_lo=1.0, draw_hi=1.3),
    ParamDef("radius_frac", "float", 0.3, 0.95, "mean radius of the town as a fraction of half the map's shorter side",
             draw_lo=0.45, draw_hi=0.75),
    ParamDef("rotation", "float", 0.0, 360.0, "rotation of the whole layout, degrees"),
    ParamDef("warp", "float", 0.0, 0.2, "smooth domain warp amplitude, fraction of the town radius", draw_lo=0.0, draw_hi=0.06),
    ParamDef("organic", "bool", 0, 1, "organic regime: jitter at least 0.9, one Lloyd pass, warp at least 0.12", default=False),
    ParamDef("gates", "int", 2, 8, "gates in the wall", draw_lo=3, draw_hi=5),
    ParamDef("plaza_frac", "float", 0.02, 0.2, "minimum plaza district area as a fraction of the town", draw_lo=0.04, draw_hi=0.09),
    ParamDef("merge_prob", "float", 0.0, 0.5, "probability that a district merges with a neighbour", draw_lo=0.0, draw_hi=0.25),
    ParamDef("citadel", "bool", 0, 1, "the wall district farthest from every gate becomes a citadel", draw_hi=0.5),
    ParamDef("min_district_area", "float", 100.0, 20000.0, "m2; smaller cells merge into a neighbour", default=1600.0),
]

KIND_NAMES = {"market": "Market Ward", "craft": "Craft Quarter", "residential": "Residential Ward", "citadel": "Citadel Ward"}


class _Retry(Exception):
    """This seed attempt gave an unusable layout; draw again from the next attempt stream."""


def _stage(attempt: int, name: str) -> str:
    return name if attempt == 0 else "%s%d" % (name, attempt)


def _cells(pts: np.ndarray, n: int) -> Tuple[Voronoi, List[List[int]]]:
    vor = Voronoi(pts)
    cells = []
    for i in range(n):
        reg = vor.regions[vor.point_region[i]]
        if not reg or -1 in reg:
            raise _Retry()
        reg = sorted(reg, key=lambda v: math.atan2(vor.vertices[v][1] - pts[i][1], vor.vertices[v][0] - pts[i][0]))
        cells.append(reg)
    return vor, cells


def _seeds(p: Dict[str, Any], seed: int, sid: str, attempt: int, jitter: float, lloyd: int):
    N = int(p["patches"])
    rng = stage_rng(sid, NAME, seed, _stage(attempt, "seeds"))
    M = N + int(math.ceil(2.5 * math.sqrt(N))) + 4
    sp = math.sqrt(math.pi / N)
    ang = GOLDEN * p["spiral_tightness"]
    sa = math.sqrt(p["anisotropy"])
    out = []
    for i in range(M):
        r = ((i + 0.5) / N) ** (0.5 * p["centre_bias"])
        jr, ja = sp * jitter * math.sqrt(rng.random()), 2 * math.pi * rng.random()
        if i == 0:
            jr = 0.0
        x = r * math.cos(i * ang) + jr * math.cos(ja)
        y = r * math.sin(i * ang) + jr * math.sin(ja)
        out.append((x * sa, y / sa))
    far = 3.0 * max(math.hypot(x, y) for x, y in out)
    for k in range(20):
        out.append((far * math.cos(2 * math.pi * k / 20), far * math.sin(2 * math.pi * k / 20)))
    pts = np.array(out, dtype=float)
    for _ in range(lloyd):
        vor, cells = _cells(pts, N)
        for i in range(N):
            poly = Polygon([vor.vertices[v] for v in cells[i]])
            if not poly.is_valid or poly.area <= 0:
                raise _Retry()
            c = poly.centroid
            pts[i] = (c.x, c.y)
    return _cells(pts, N)


def _neighbours(polys: Dict[int, Polygon], k: int) -> List[Tuple[float, int]]:
    out = []
    for j in sorted(polys):
        if j != k and polys[k].envelope.intersects(polys[j].envelope):
            ln = polys[k].boundary.intersection(polys[j].boundary).length
            if ln > G.BORDER_MIN:
                out.append((ln, j))
    return out


def _merge(polys: Dict[int, Polygon], a: int, b: int) -> bool:
    u = unary_union([polys[a], polys[b]])
    if u.geom_type != "Polygon" or u.interiors or not u.is_valid:
        return False
    polys[min(a, b)] = orient(u, 1.0)
    del polys[max(a, b)]
    return True


def _ring(poly: Polygon) -> List[List[float]]:
    return [[x, y] for x, y in list(poly.exterior.coords)[:-1]]


def _dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _route(adj: Dict[tuple, List[Tuple[tuple, float]]], src: tuple, targets: Set[tuple]) -> List[tuple]:
    """Dijkstra from src to the nearest of `targets` (ties broken by coordinates, so deterministic)."""
    dist = {src: 0.0}
    prev: Dict[tuple, tuple] = {}
    heap = [(0.0, src)]
    done: Set[tuple] = set()
    while heap:
        d, u = heapq.heappop(heap)
        if u in done:
            continue
        done.add(u)
        if u in targets:
            path = [u]
            while path[-1] in prev:
                path.append(prev[path[-1]])
            return path[::-1]
        for v, w in adj[u]:
            nd = d + w
            if nd < dist.get(v, 1e18):
                dist[v] = nd
                prev[v] = u
                heapq.heappush(heap, (nd, v))
    raise _Retry()


def _build(p: Dict[str, Any], seed: int, size: Tuple[float, float], centre: Tuple[float, float], sid: str,
           attempt: int) -> Settlement:
    N = int(p["patches"])
    org = bool(p["organic"])
    jitter = max(p["jitter"], ORGANIC_JITTER) if org else p["jitter"]
    lloyd = min(int(p["lloyd"]), 1) if org else int(p["lloyd"])
    warp = max(p["warp"], ORGANIC_WARP) if org else p["warp"]
    vor, cells = _seeds(p, seed, sid, attempt, jitter, lloyd)

    # normalise: mean radius of the town 1, centred on the union's centroid, then warp and rotate
    raw = [Polygon([vor.vertices[v] for v in cells[i]]) for i in range(N)]
    union_raw = unary_union(raw)
    rho = math.sqrt(union_raw.area / math.pi)
    c0 = union_raw.centroid
    wrng = stage_rng(sid, NAME, seed, "warp")
    ph = [2 * math.pi * wrng.random() for _ in range(2)]
    kk = [0.4 + 0.3 * wrng.random() for _ in range(2)]
    rot = math.radians(p["rotation"])
    cr, sr = math.cos(rot), math.sin(rot)

    def to_norm(v) -> Tuple[float, float]:
        x, y = (v[0] - c0.x) / rho, (v[1] - c0.y) / rho
        wx = x + warp * math.sin(2 * math.pi * kk[0] * y + ph[0])
        wy = y + warp * math.sin(2 * math.pi * kk[1] * x + ph[1])
        return wx * cr - wy * sr, wx * sr + wy * cr

    used = sorted({v for c in cells for v in c})
    nu = {v: to_norm(vor.vertices[v]) for v in used}
    ex = max(abs(q[0]) for q in nu.values())
    ey = max(abs(q[1]) for q in nu.values())
    f_fit = min((min(centre[0], size[0] - centre[0]) - 10.0) / ex, (min(centre[1], size[1] - centre[1]) - 10.0) / ey)
    f = min(p["radius_frac"] * min(size) / 2.0, f_fit)
    if f < 60.0:
        raise GenerationError("map %gx%g is too small for a settlement (town radius %.1f m < 60 m)" % (size[0], size[1], f))
    mp = {v: pt(centre[0] + f * q[0], centre[1] + f * q[1]) for v, q in nu.items()}

    polys: Dict[int, Polygon] = {}
    for i in range(N):
        ring: List[List[float]] = []
        for v in cells[i]:
            if not ring or ring[-1] != mp[v]:
                ring.append(mp[v])
        if len(ring) > 1 and ring[0] == ring[-1]:
            ring.pop()
        poly = Polygon(ring) if len(ring) >= 3 else None
        if poly is None or not poly.is_valid or poly.area <= 0:
            raise _Retry()
        polys[i] = orient(poly, 1.0)
    # cells joined to the plaza cell by a border of at least BORDER_MIN; corner-touching strays are dropped
    reach, todo = {0}, [0]
    while todo:
        for _, j in _neighbours(polys, todo.pop()):
            if j not in reach:
                reach.add(j)
                todo.append(j)
    polys = {k: q for k, q in polys.items() if k in reach}
    total = sum(q.area for q in polys.values())
    min_area = float(p["min_district_area"])

    # plaza: cell 0 grows to plaza_frac of the town; small cells merge into their longest-bordered neighbour
    target = max(p["plaza_frac"] * total, 1.1 * min_area)
    while polys[0].area < target:
        c = polys[0].centroid
        cand = sorted((_dist((polys[j].centroid.x, polys[j].centroid.y), (c.x, c.y)), j) for _, j in _neighbours(polys, 0))
        if not any(_merge(polys, 0, j) for _, j in cand):
            break
    for _ in range(4 * N):
        small = sorted((q.area, k) for k, q in polys.items() if k != 0 and q.area < min_area)
        if not small:
            break
        k = small[0][1]
        nb = sorted(_neighbours(polys, k), key=lambda t: (t[1] == 0, -t[0], t[1]))
        if not any(_merge(polys, k, j) for _, j in nb):
            raise _Retry()
    mrng = stage_rng(sid, NAME, seed, _stage(attempt, "merge"))
    for k in sorted(polys):
        u, pick = mrng.random(), mrng.random()
        if k == 0 or k not in polys or u >= p["merge_prob"]:
            continue
        nb = [j for _, j in _neighbours(polys, k) if j != 0]
        if nb:
            _merge(polys, k, nb[int(pick * len(nb)) % len(nb)])
    if any(q.area < min_area for q in polys.values()) or len(polys) < 4:
        raise _Retry()

    foot = unary_union(list(polys.values()))
    if foot.geom_type != "Polygon" or foot.interiors or not foot.is_valid:
        raise _Retry()
    footprint = _ring(orient(foot, 1.0))
    wall_line = LineString(footprint + [footprint[0]])
    cpt = pt(centre[0], centre[1])

    keys = sorted(polys)
    rings = {k: _ring(polys[k]) for k in keys}
    areas = {k: r2(polys[k].area) for k in keys}
    cen = {k: (polys[k].centroid.x, polys[k].centroid.y) for k in keys}
    plaza_poly = polys[0]
    pc = plaza_poly.centroid if plaza_poly.contains(plaza_poly.centroid) else plaza_poly.representative_point()
    plaza_at = pt(pc.x, pc.y)
    plaza_id = "%s_plaza" % sid

    # kinds: nearest fifth market, wall districts of the outer quarter craft, the rest residential
    dist0 = {k: _dist(cen[k], plaza_at) for k in keys if k != 0}
    order = sorted(dist0, key=lambda k: (dist0[k], k))
    kind = {0: "core"}
    for rank, k in enumerate(order):
        kind[k] = "market" if rank < max(1, int(round(0.2 * len(order)))) else "residential"
    touch = {k: polys[k].boundary.intersection(wall_line).length > G.BORDER_MIN for k in keys}
    for k in order[int(0.75 * len(order)):]:
        if touch[k] and kind[k] == "residential":
            kind[k] = "craft"

    # planar graph of the district borders
    adj_set: Dict[tuple, Dict[tuple, float]] = {}
    inc: Dict[tuple, Set[int]] = {}
    for k in keys:
        r = rings[k]
        for i in range(len(r)):
            a, b = tuple(r[i]), tuple(r[(i + 1) % len(r)])
            inc.setdefault(a, set()).add(k)
            d = _dist(a, b)
            adj_set.setdefault(a, {})[b] = d
            adj_set.setdefault(b, {})[a] = d
    adj = {u: sorted(nb.items()) for u, nb in adj_set.items()}

    # gates: wall vertices nearest to evenly spread angles, at least 40 m apart
    grng = stage_rng(sid, NAME, seed, "gates")
    off = grng.random()
    g = int(p["gates"])
    wv = [tuple(v) for v in footprint if tuple(v) in adj]

    def ang(v) -> float:
        return math.atan2(v[1] - cpt[1], v[0] - cpt[0]) % (2 * math.pi)

    chosen: List[tuple] = []
    for j in range(g):
        tgt = (j + off) / g * 2 * math.pi
        for v in sorted(wv, key=lambda v: (min(abs(ang(v) - tgt), 2 * math.pi - abs(ang(v) - tgt)), v)):
            if all(_dist(v, c) > 40.0 for c in chosen):
                chosen.append(v)
                break
    if len(chosen) < 2:
        raise _Retry()
    chosen.sort(key=lambda v: (ang(v), v))
    gate_id = {v: "%s_gate_%d" % (sid, j) for j, v in enumerate(chosen)}

    landmarks: List[Landmark] = [Landmark(id=plaza_id, kind="square", at=plaza_at,
                                          r=r2(0.55 * math.sqrt(areas[0] / math.pi)), district="%s_d_p0" % sid,
                                          label="Central Plaza")]
    if p["citadel"]:
        best, cit = -1.0, None
        for k in order:
            if touch[k]:
                gd = min(_dist(cen[k], v) for v in chosen)
                if gd > best + 1e-9:
                    best, cit = gd, k
        if cit is not None:
            kind[cit] = "citadel"
            rp = polys[cit].representative_point()
            landmarks.append(Landmark(id="%s_citadel" % sid, kind="citadel", at=pt(rp.x, rp.y),
                                      r=r2(min(45.0, 0.5 * math.sqrt(areas[cit] / math.pi))),
                                      district="%s_d_p%d" % (sid, cit), label="Citadel"))

    # arteries on district borders: gate -> nearest vertex of the network (the plaza border at first), never overlapping
    plaza_verts = {tuple(v) for v in rings[0]}
    network: Set[tuple] = set()
    paths: List[Tuple[int, List[tuple], bool]] = []
    for j, v in enumerate(chosen):
        if v in network:
            continue
        path = _route(adj, v, network | plaza_verts)
        paths.append((j, path, path[-1] not in network))
        network.update(path)

    # every road vertex is a movement-graph node; junctions are named after the districts that meet there
    names: Dict[tuple, str] = {}
    used_ids: Set[str] = set()
    for v in sorted(network):
        if v in gate_id:
            names[v] = gate_id[v]
            continue
        base = "%s_j_%s" % (sid, "".join("p%d" % k for k in sorted(inc[v])[:3]))
        nid, n = base, 1
        while nid in used_ids:
            n += 1
            nid = "%s_%d" % (base, n)
        used_ids.add(nid)
        names[v] = nid
    nodes: Dict[str, GraphNode] = {plaza_id: GraphNode(id=plaza_id, kind="zone", at=plaza_at)}
    for v in network:
        nodes[names[v]] = GraphNode(id=names[v], kind="choke", at=[v[0], v[1]])
    roads: List[Road] = []
    for j, path, link in paths:
        poly = [[v[0], v[1]] for v in path]
        ids = [names[v] for v in path]
        if link:
            poly.append(plaza_at)
            ids.append(plaza_id)
        roads.append(Road(id="%s_artery_%d" % (sid, j), cls="artery", polyline=poly,
                          width=14.0 if path[0] in gate_id else 10.0, nodes=ids))
    roads.sort(key=lambda r: r.id)

    wall_id = "%s_wall_outer" % sid
    walls = [Wall(id=wall_id, kind="outer", polyline=footprint + [footprint[0]], thickness=8.0)]
    gates: List[Gate] = []
    n = len(footprint)
    for v in chosen:
        i = footprint.index([v[0], v[1]])
        a, b = footprint[(i - 1) % n], footprint[(i + 1) % n]
        tx, ty = b[0] - a[0], b[1] - a[1]
        h = math.hypot(tx, ty) or 1.0
        rid = next(r.id for r in roads if [v[0], v[1]] in r.polyline)
        gates.append(Gate(id=gate_id[v], wall=wall_id, at=[v[0], v[1]], normal=[r2(ty / h), r2(-tx / h)], road=rid))
    gates.sort(key=lambda x: x.id)

    districts: List[District] = []
    counters: Dict[str, int] = {}
    for k in keys:
        did = "%s_d_p%d" % (sid, k)
        if k == 0:
            label = "Central Plaza"
        else:
            counters[kind[k]] = counters.get(kind[k], 0) + 1
            label = "%s %d" % (KIND_NAMES[kind[k]], counters[kind[k]])
        districts.append(District(id=did, key="p%d" % k, kind=kind[k], polygon=rings[k], area=areas[k], label=label,
                                  landmarks=[l.id for l in landmarks if l.district == did]))
    gnodes = [nodes[k] for k in sorted(nodes)]
    G.attach_districts(gnodes, districts)
    edges = G.movement_edges(roads, nodes)
    dn, de = G.district_graph(districts, roads, walls, gates)
    return Settlement(id=sid, generator=NAME, seed=seed, params=dict(p), size=(size[0], size[1]), centre=cpt,
                      footprint=footprint, districts=districts, roads=roads, walls=walls, gates=gates,
                      landmarks=landmarks, graph_nodes=gnodes, graph_edges=edges, district_nodes=dn, district_edges=de)


def generate(p: Dict[str, Any], seed: int, size: Tuple[float, float], centre: Tuple[float, float], sid: str) -> Settlement:
    """Build from resolved parameters; an unusable seed layout is redrawn from the next attempt stream."""
    for attempt in range(ATTEMPTS):
        try:
            return _build(dict(p), seed, size, centre, sid, attempt)
        except _Retry:
            continue
    raise GenerationError("no usable voronoi layout in %d attempts; lower min_district_area or merge_prob, or raise patches"
                          % ATTEMPTS)
