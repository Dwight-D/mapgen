"""Deterministic flowgraph layout: node id -> (x, y) metres in the map rectangle (design: mapgen/FLOWGRAPH_SPEC.md section 5)."""
from __future__ import annotations

from typing import Dict, List, Tuple

import networkx as nx

from .flowgraph import Flowgraph

SEED = 1
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


def layout(g: Flowgraph, width: float, height: float) -> Dict[str, Tuple[float, float]]:
    """Positions for every node. The first two teams' spawns are pinned on the left and right margins at mid
    height; the rest come from a seeded spring layout rescaled into the rectangle. Same graph => same output."""
    margin = _round(MARGIN_FRAC * min(width, height))
    x0, x1, y0, y1 = margin, _round(width - margin), margin, _round(height - margin)
    teams = team_order(g)
    nx_g = nx.Graph()
    for n in g.nodes:
        nx_g.add_node(n.id)
    for e in g.edges:
        nx_g.add_edge(e.a, e.b)
    init: Dict[str, Tuple[float, float]] = {}
    for n in g.nodes:
        if n.kind == "spawn" and n.team in teams[:2] and n.id not in init:
            init[n.id] = (0.0, 0.5) if n.team == teams[0] else (1.0, 0.5)
    pos = nx.spring_layout(nx_g, pos=init, fixed=sorted(init), seed=SEED, iterations=200, scale=None, center=None) \
        if init else nx.spring_layout(nx_g, seed=SEED, iterations=200)
    xs = [float(p[0]) for p in pos.values()]
    ys = [float(p[1]) for p in pos.values()]
    lo_x, hi_x, lo_y, hi_y = min(xs), max(xs), min(ys), max(ys)

    def rescale(v: float, lo: float, hi: float, a: float, b: float) -> float:
        return (a + b) / 2.0 if hi - lo < 1e-9 else a + (v - lo) / (hi - lo) * (b - a)

    out: Dict[str, Tuple[float, float]] = {}
    used = set()
    for n in g.nodes:  # declaration order makes collision nudging deterministic
        px, py = float(pos[n.id][0]), float(pos[n.id][1])
        x = rescale(px, lo_x, hi_x, x0, x1)
        y = rescale(py, lo_y, hi_y, y0, y1)
        if n.id in init:
            x = x0 if init[n.id][0] == 0.0 else x1
        p = (min(max(_round(x), x0), x1), min(max(_round(y), y0), y1))
        base, k = p, 0
        while p in used:  # nudge coincident nodes apart so POIs never share a position
            k += 1
            cands = [(base[0], base[1] + GRID * k), (base[0], base[1] - GRID * k)]
            hit = [c for c in cands if y0 <= c[1] <= y1 and c not in used]
            if hit:
                p = hit[0]
            elif k > 1000:
                break
        used.add(p)
        out[n.id] = p
    return out
