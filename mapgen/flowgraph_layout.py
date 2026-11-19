"""Deterministic flowgraph layout: node id -> (x, y) metres in the map rectangle (design: mapgen/FLOWGRAPH_SPEC.md sections 10-11)."""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .flowgraph import Flowgraph

GRID = 10.0          # coordinates are rounded to this many metres
MARGIN_FRAC = 0.1    # margin as a fraction of the shorter map side


def _round(v: float) -> float:
    return float(int(round(v / GRID)) * GRID)


def team_order(g: Flowgraph) -> List[str]:
    """Team ids in order of first spawn node."""
    out: List[str] = []
    for n in g.nodes:
        if n.kind == "spawn" and n.team not in out:
            out.append(n.team)
    return out


def _cross(p, q, r, t) -> bool:
    """True when segments pq and rt properly cross."""
    def side(a, b, c):
        v = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        return (v > 1e-9) - (v < -1e-9)
    return side(p, q, r) * side(p, q, t) < 0 and side(r, t, p) * side(r, t, q) < 0


def _bfs(adj: Dict[str, List[str]], sources: List[str]) -> Dict[str, int]:
    dist = {s: 0 for s in sources}
    queue = list(sources)
    for cur in queue:  # appending while iterating is the BFS queue
        for nb in adj[cur]:
            if nb not in dist:
                dist[nb] = dist[cur] + 1
                queue.append(nb)
    return dist


def _layered_layout(g: Flowgraph, width: float, height: float) -> Dict[str, Tuple[float, float]]:
    """Layered initial layout of the stress layout. Positions for every node, layered left to right. A node's layer is its graph distance from the first team's
    spawns; the second team's spawns are pinned to the last layer, the first team's to layer 0 (both on the margins).
    Within a layer nodes are ordered by barycentre sweeps (ties by declaration order), then spread evenly around
    mid height on a shared row pitch. Same graph => same output."""
    margin = _round(MARGIN_FRAC * min(width, height))
    x0, x1, y0, y1 = margin, _round(width - margin), margin, _round(height - margin)
    teams = team_order(g)
    order = {n.id: i for i, n in enumerate(g.nodes)}
    adj: Dict[str, List[str]] = {n.id: [] for n in g.nodes}
    for e in g.edges:
        adj[e.a].append(e.b)
        adj[e.b].append(e.a)
    left = [n.id for n in g.nodes if n.kind == "spawn" and n.team == teams[0]]
    right = [n.id for n in g.nodes if n.kind == "spawn" and n.team == teams[1]]
    dist = _bfs(adj, left)
    layer = {nid: dist.get(nid, 0) for nid in order}
    last = max([layer[n] for n in order if n not in right] + [0]) + 1
    for nid in right:
        layer[nid] = last
    for nid in left:
        layer[nid] = 0
    cols: List[List[str]] = [[] for _ in range(last + 1)]
    for n in g.nodes:
        cols[layer[n.id]].append(n.id)
    cols = [c for c in cols if c]
    for i, c in enumerate(cols):
        for nid in c:
            layer[nid] = i
    rank = {nid: float(j) for c in cols for j, nid in enumerate(c)}

    def sweep(seq):
        for c in seq:
            if len(c) < 2:
                continue
            def key(nid):
                nb = [rank[m] - (len(cols[layer[m]]) - 1) / 2.0 for m in adj[nid]]
                bary = sum(nb) / len(nb) if nb else rank[nid] - (len(c) - 1) / 2.0
                return (bary, order[nid])
            c.sort(key=key)
            for j, nid in enumerate(c):
                rank[nid] = float(j)
    for _ in range(8):
        sweep(cols[1:])
        sweep(reversed(cols[:-1]))
    nrows = max(max(len(c) for c in cols), 3)  # spare rows let a lone node step off a straight line
    pitch = (y1 - y0) / (nrows - 1)
    gap = (x1 - x0) / max(len(cols) - 1, 1)
    clear = 0.3 * min(pitch, gap)  # an edge closer than this to another node counts as a hit
    col_of = {nid: i for i, c in enumerate(cols) for nid in c}
    row = {nid: (nrows - len(c)) / 2.0 + j for c in cols for j, nid in enumerate(c)}
    pinned = set(left) | set(right)
    xy = {nid: (x0 + gap * col_of[nid], y0 + row[nid] * pitch) for nid in row}
    segs = [(e.a, e.b) for e in g.edges]

    def cost() -> float:
        """Edges passing through nodes (heavy), edge crossings (medium), total edge slope (light)."""
        hits = crossings = 0
        slope = 0.0
        for a, b in segs:
            (ax, ay), (bx, by) = xy[a], xy[b]
            slope += abs(ay - by)
            dx, dy = bx - ax, by - ay
            ln = dx * dx + dy * dy
            for nid, (px, py) in xy.items():
                if nid == a or nid == b or ln == 0:
                    continue
                t = ((px - ax) * dx + (py - ay) * dy) / ln
                t = min(max(t, 0.0), 1.0)
                if (px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2 < clear * clear:
                    hits += 1
        for i in range(len(segs)):
            for k in range(i + 1, len(segs)):
                a, b = segs[i]
                c, d = segs[k]
                if len({a, b, c, d}) < 4:
                    continue
                if _cross(xy[a], xy[b], xy[c], xy[d]):
                    crossings += 1
        return hits * 1e6 + crossings * 1e4 + slope

    def put(nid: str, r: float) -> None:
        row[nid] = r
        xy[nid] = (xy[nid][0], y0 + r * pitch)

    best = cost()
    for _ in range(40):  # first-improvement moves in a fixed scan order: swap row-neighbours, shift to a free row
        improved = False
        for c in cols:
            c.sort(key=lambda nid: (row[nid], order[nid]))
            for j in range(len(c) - 1):
                u, v = c[j], c[j + 1]
                ru, rv = row[u], row[v]
                put(u, rv)
                put(v, ru)
                now = cost()
                if now < best - 1e-9:
                    best, improved = now, True
                    c[j], c[j + 1] = v, u
                else:
                    put(u, ru)
                    put(v, rv)
            for nid in list(c):
                if nid in pinned:
                    continue
                for d in (-1.0, 1.0):
                    r0, r1 = row[nid], row[nid] + d
                    if r1 < 0 or r1 > nrows - 1 or any(abs(row[o] - r1) < 0.5 for o in c if o != nid):
                        continue
                    put(nid, r1)
                    now = cost()
                    if now < best - 1e-9:
                        best, improved = now, True
                        break
                    put(nid, r0)
        if not improved:
            break
    out: Dict[str, Tuple[float, float]] = {}
    used = set()
    for i, c in enumerate(cols):
        x = x0 if len(cols) == 1 else x0 + (x1 - x0) * i / (len(cols) - 1)
        for j, nid in enumerate(c):
            y = xy[nid][1]
            p = (min(max(_round(x), x0), x1), min(max(_round(y), y0), y1))
            k = 0
            while p in used and k < 1000:  # nudge coincident nodes apart so POIs never share a position
                k += 1
                p = (p[0], min(p[1] + GRID, y1) if p[1] + GRID <= y1 else max(p[1] - GRID * k, y0))
            used.add(p)
            out[nid] = p
    return out


# ---- stress layout (spec section 11) ---------------------------------------------------------------------------

ITERATIONS = 400        # fixed Gauss-Seidel sweeps: no convergence test, so the result never depends on a tolerance
ANCHOR_WEIGHT = 12.0    # spring weight toward a `place` anchor, against pairwise stress weights of 1/d^2
SPAWN_REACH = 1.0       # a spawn anchor aims at this fraction of the frame half-width
SPAWN_BOOST = 20.0      # spawn anchors are this many times stiffer than other anchors
NODE_REACH = 0.5        # a non-spawn anchor aims at this fraction of the frame half-width (spawns aim at the frame edge)
EDGE_WEIGHT = 16.0      # extra weight on pairs joined by an edge (d = 1): edge lengths matter more than far pairs
MIN_SEP = 0.7           # nodes closer than this many edge lengths push each other apart
SEED_BLEND = 0.1        # share of the layered layout mixed into the relaxed start to break ties between parallel branches
ZONE_EDGE = 0.35        # an edge zone (west/east/north/south) is the outer 35 % of the map rectangle
ZONE_CENTER = (0.4, 0.6)     # `center` is the middle 20 % of each axis
AUTOMORPHISM_BUDGET = 20000  # search steps for graph symmetries; large asymmetric graphs simply get none
_DIRS = {"west": (-1, 0), "east": (1, 0), "north": (0, 1), "south": (0, -1), "center": (0, 0),
         "north-west": (-1, 1), "north-east": (1, 1), "south-west": (-1, -1), "south-east": (1, -1)}


def _distances(g: Flowgraph) -> Dict[str, Dict[str, int]]:
    adj: Dict[str, List[str]] = {n.id: [] for n in g.nodes}
    for e in g.edges:
        adj[e.a].append(e.b)
        adj[e.b].append(e.a)
    return {n.id: _bfs(adj, [n.id]) for n in g.nodes}


def _anchors(g: Flowgraph) -> Dict[str, Tuple[int, int]]:
    """node id -> compass direction (dx, dy), y north. Spawns without `place` default to west (first team) / east (second team)."""
    teams = team_order(g)
    out: Dict[str, Tuple[int, int]] = {}
    for n in g.nodes:
        place = n.place
        if not place and n.kind == "spawn" and n.team in teams[:2]:
            place = "west" if n.team == teams[0] else "east"
        if place:
            out[n.id] = _DIRS[place]
    return out


def _involutions(g: Flowgraph) -> List[Dict[str, str]]:
    """Non-trivial graph automorphisms of order 2 (kind-preserving), found by backtracking in declaration order, so the list
    is deterministic. Bounded by AUTOMORPHISM_BUDGET steps."""
    ids = [n.id for n in g.nodes]
    kind = {n.id: n.kind for n in g.nodes}
    adj = {i: set() for i in ids}
    for e in g.edges:
        adj[e.a].add(e.b)
        adj[e.b].add(e.a)
    out: List[Dict[str, str]] = []
    steps = [0]
    m: Dict[str, str] = {}

    def rec(k: int) -> None:
        if steps[0] > AUTOMORPHISM_BUDGET:
            return
        while k < len(ids) and ids[k] in m:
            k += 1
        if k == len(ids):
            if any(a != b for a, b in m.items()):
                out.append(dict(m))
            return
        u = ids[k]
        for v in ids[k:]:  # an involution maps u to itself or to a later, still unmapped node
            steps[0] += 1
            if v in m or kind[v] != kind[u] or len(adj[v]) != len(adj[u]):
                continue
            m[u] = v
            m[v] = u
            ok = all(m[w] in adj[v] for w in adj[u] if w in m) and all(m[w] in adj[u] for w in adj[v] if w in m)
            if ok:
                rec(k + 1)
            del m[u]
            m.pop(v, None)
    rec(0)
    return out


def _mirrors(g: Flowgraph, anchors: Dict[str, Tuple[int, int]]) -> Tuple[Optional[Dict[str, str]], Optional[Dict[str, str]]]:
    """(left-right mirror, top-bottom mirror) as node permutations, or None. The left-right mirror swaps the two teams' spawns;
    the top-bottom mirror fixes every spawn. Both must agree with the `place` hints (an anchored node maps to a node whose
    anchor is the reflected one, or to an unanchored node); among the candidates the one moving the most nodes wins."""
    spawns = [n.id for n in g.nodes if n.kind == "spawn"]
    teams = team_order(g)
    team = {n.id: n.team for n in g.nodes}
    best: List[Optional[Dict[str, str]]] = [None, None]
    for m in _involutions(g):
        if all(m[s] == s for s in spawns):
            axis = 1
        elif len(teams) == 2 and all(team[m[s]] != team[s] for s in spawns):
            axis = 0
        else:
            continue
        ok = True
        for u, v in m.items():
            if u in anchors and v in anchors:
                dx, dy = anchors[u]
                ok = ok and anchors[v] == ((-dx, dy) if axis == 0 else (dx, -dy))
            elif u in anchors or v in anchors:
                ok = False
        if ok and (best[axis] is None or sum(a != b for a, b in m.items()) > sum(a != b for a, b in best[axis].items())):
            best[axis] = m
    return best[0], best[1]


def _symmetrise(pos: Dict[str, List[float]], mx: Optional[Dict[str, str]], my: Optional[Dict[str, str]]) -> None:
    """Project positions onto the mirror-symmetric set (axes through the origin)."""
    if mx:
        old = {k: list(v) for k, v in pos.items()}
        for u, v in mx.items():
            pos[u] = [(old[u][0] - old[v][0]) / 2.0, (old[u][1] + old[v][1]) / 2.0]
    if my:
        old = {k: list(v) for k, v in pos.items()}
        for u, v in my.items():
            pos[u] = [(old[u][0] + old[v][0]) / 2.0, (old[u][1] - old[v][1]) / 2.0]


def _solve(g: Flowgraph) -> Dict[str, List[float]]:
    """Stress-minimising positions in abstract units (one edge = 1.0), y north, centred on the origin. Target distance
    = graph distance; weight 1/d^2. An anchor is a stiff spring toward its compass point on both axes (a one-axis anchor
    such as `west` aims at the middle of the other axis), and the layout is mirror-symmetric when the graph has the
    symmetry (see _mirrors). Starts from a relaxation that puts anchored nodes at their targets and every other node at
    the mean of its neighbours, plus a small share of the layered layout; overlapping nodes repel; fixed sweep count;
    no randomness."""
    dist = _distances(g)
    ids = [n.id for n in g.nodes]
    diam = max(max(d.values()) for d in dist.values())
    half = max(diam / 2.0, 1.0)
    spawn = {n.id for n in g.nodes if n.kind == "spawn"}
    anchors = _anchors(g)
    target = {nid: (dx * half * (SPAWN_REACH if nid in spawn else NODE_REACH), dy * half * (SPAWN_REACH if nid in spawn else NODE_REACH))
              for nid, (dx, dy) in anchors.items()}
    mx, my = _mirrors(g, anchors)
    adj: Dict[str, List[str]] = {i: [] for i in ids}
    for e in g.edges:
        adj[e.a].append(e.b)
        adj[e.b].append(e.a)
    lay = _layered_layout(g, 1000.0, 1000.0)
    scale = max(max(abs(lay[i][0] - 500.0), abs(lay[i][1] - 500.0)) for i in ids) or 1.0
    pos = {i: list(target[i]) if i in target else [0.0, 0.0] for i in ids}
    for _ in range(200):  # relax the free nodes onto the mean of their neighbours
        for i in ids:
            if i not in target and adj[i]:
                pos[i] = [sum(pos[j][0] for j in adj[i]) / len(adj[i]), sum(pos[j][1] for j in adj[i]) / len(adj[i])]
    for i in ids:
        pos[i][0] += SEED_BLEND * half * (lay[i][0] - 500.0) / scale
        pos[i][1] += SEED_BLEND * half * (500.0 - lay[i][1]) / scale
    _symmetrise(pos, mx, my)
    for _ in range(ITERATIONS):
        for i in ids:
            xi, yi = pos[i]
            sx = sy = sw = 0.0
            for j in ids:
                if j == i:
                    continue
                d = float(dist[i][j])
                w = (EDGE_WEIGHT if d == 1.0 else 1.0) / (d * d)
                xj, yj = pos[j]
                r = ((xi - xj) ** 2 + (yi - yj) ** 2) ** 0.5
                ux, uy = ((xi - xj) / r, (yi - yj) / r) if r > 1e-9 else (1.0, 0.0)
                if r < MIN_SEP and d > 1.0:  # overlap repulsion: never pull a close pair toward a short target
                    d, w = MIN_SEP, 1.0
                sx += w * (xj + d * ux)
                sy += w * (yj + d * uy)
                sw += w
            if i in target:
                a = ANCHOR_WEIGHT * (SPAWN_BOOST if i in spawn else 1.0)
                sx += a * target[i][0]
                sy += a * target[i][1]
                sw += a
            pos[i] = [sx / sw, sy / sw]
        _symmetrise(pos, mx, my)
    return pos


def solved_extent(g: Flowgraph) -> Tuple[float, float]:
    """Width and height of the stress solution in edge lengths (the preview sizes its canvas from it)."""
    pos = _solve(g)
    xs = [p[0] for p in pos.values()]
    ys = [p[1] for p in pos.values()]
    return max(xs) - min(xs), max(ys) - min(ys)


def _fit(lo: float, hi: float, a0: float, a1: float, v: float) -> float:
    return (a0 + a1) / 2.0 if hi - lo < 1e-9 else a0 + (v - lo) / (hi - lo) * (a1 - a0)


def layout(g: Flowgraph, width: float, height: float) -> Dict[str, Tuple[float, float]]:
    """Positions for every node in the width x height rectangle (spec sections 10-11): the stress solution stretched to
    fill the rectangle inside a margin of 10 % of the shorter side, anchored nodes clamped into their zone, rounded to
    10 m, coincident nodes nudged apart. Same graph and size => same output."""
    margin = _round(MARGIN_FRAC * min(width, height))
    x0, x1, y0, y1 = margin, _round(width - margin), margin, _round(height - margin)
    sol = _solve(g)
    xs = [p[0] for p in sol.values()]
    ys = [p[1] for p in sol.values()]
    lo_x, hi_x, lo_y, hi_y = min(xs), max(xs), min(ys), max(ys)
    anchors = _anchors(g)
    raw: Dict[str, Tuple[float, float]] = {}
    for n in g.nodes:
        x = _fit(lo_x, hi_x, x0, x1, sol[n.id][0])
        y = _fit(lo_y, hi_y, y0, y1, sol[n.id][1])  # y north: larger y is further north
        if n.id in anchors:
            dx, dy = anchors[n.id]
            if dx < 0:
                x = min(x, ZONE_EDGE * width)
            elif dx > 0:
                x = max(x, (1 - ZONE_EDGE) * width)
            elif dy == 0:
                x = min(max(x, ZONE_CENTER[0] * width), ZONE_CENTER[1] * width)
            if dy < 0:
                y = min(y, ZONE_EDGE * height)
            elif dy > 0:
                y = max(y, (1 - ZONE_EDGE) * height)
            elif dx == 0:
                y = min(max(y, ZONE_CENTER[0] * height), ZONE_CENTER[1] * height)
        raw[n.id] = (x, y)
    out: Dict[str, Tuple[float, float]] = {}
    used = set()
    for n in g.nodes:
        x, y = raw[n.id]
        p = (min(max(_round(x), x0), x1), min(max(_round(y), y0), y1))
        k = 0
        while p in used and k < 1000:  # nudge coincident nodes apart so POIs never share a position
            k += 1
            p = (p[0], min(p[1] + GRID, y1) if p[1] + GRID <= y1 else max(p[1] - GRID * k, y0))
        used.add(p)
        out[n.id] = p
    return out
