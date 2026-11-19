"""Deterministic flowgraph layout: node id -> (x, y) metres in the map rectangle (design: mapgen/FLOWGRAPH_SPEC.md section 5)."""
from __future__ import annotations

from typing import Dict, List, Tuple

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


def layout(g: Flowgraph, width: float, height: float) -> Dict[str, Tuple[float, float]]:
    """Positions for every node, layered left to right. A node's layer is its graph distance from the first team's
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
