"""Regenerate the town showcase specs: python maps/towns/build.py [name ...]  (run from tools/mapgen).

For each map: generate the settlement (mapgen.town.generate), merge its spec fragment into src/<name>.base.yaml, add two
team bases outside the two most opposed gates with approach roads ending on the gate points, and write
maps/towns/<name>.yaml, <name>.settlement.json (sidecar) and <name>.flow.yaml (flow fragment + two spawn nodes linked to those gates)."""
import math
import os
import random
import sys

sys.path.insert(0, os.getcwd())
from mapgen.town import check, emit_flow, emit_spec, generate, render_png  # noqa: E402
from mapgen.town.embed import merge  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
MAPS = {
    "hearthmoor": dict(gen="grid", seed=14, sid="hearth", size=1000, teams=("Dunmark", "Eastwold"),
                       params=dict(extent_frac=0.58, aspect=1.2, gates=4, inner_wall="false", citadel="true",
                                   rotation=0)),
    "pentring": dict(gen="radial", seed=7, sid="pent", size=1200, teams=("Northguard", "Southguard"),
                     params=dict(radius_frac=0.6, spokes=5, rings=2, gates=5, citadel="false")),
    "ashgrove": dict(gen="organic", seed=21, sid="ash", size=900, teams=("Westhold", "Easthold"),
                     params=dict(edge="wall", warp=0.6, layers="organic", radius_frac=0.6, citadel="false")),
    "marchfold": dict(gen="voronoi", seed=33, sid="march", size=1500, teams=("Marcher Lords", "Border Reivers"),
                      params=dict(radius_frac=0.5, patches=14, gates=4, fort="corner", fort_outposts=2,
                                  citadel="false")),
    "twinbridge": dict(gen="radial", seed=12, sid="twin", size=1200, teams=("Northbank", "Southbank"),
                       params=dict(radius_frac=0.55, spokes=6, rings=2, gates=6, citadel="false", water="river")),
    "saltport": dict(gen="voronoi", seed=7, sid="salt", size=1000, teams=("Landward Guard", "Sea Wardens"),
                     params=dict(radius_frac=0.5, patches=14, gates=4, citadel="false", water="bay",
                                 water_island="true")),
    "thornwick": dict(gen="organic", seed=3, sid="thorn", size=1000, teams=("Briarguard", "Fenwatch"),
                      params=dict(edge="open", warp=0.7, layers="organic", radius_frac=0.55, citadel="false")),
    "swordmarch": dict(gen="grid", seed=4, sid="sword", size=1500, teams=("Marchwardens", "Riverkin"),
                       params=dict(extent_frac=0.5, gates=4, inner_wall="false", citadel="false", rotation=0,
                                   water="river", fort="island")),
    "rivermere": dict(gen="organic", seed=4, sid="river", size=1000, teams=("Eastbank", "Westbank"),
                      params=dict(edge="wall", layers="organic-radial", warp=0.5, water="river", radius_frac=0.6,
                                  citadel="false")),
    "wyndale": dict(gen="organic", seed=13, sid="wynd", size=1000, teams=("Valewatch", "Hillmen"),
                    params=dict(edge="open", layers="organic-voronoi", warp=0.6, radius_frac=0.6, lobes=2, ribbons=2,
                                citadel="false")),
    "greywatch": dict(gen="organic", seed=12, sid="grey", size=1100, teams=("Wallguard", "Fieldhands"),
                      params=dict(edge="wall", layers="organic-grid", warp=0.5, fort="corner", radius_frac=0.55,
                                  citadel="false")),
    "driftholt": dict(gen="organic", seed=13, sid="drift", size=1100, teams=("Holtmen", "Moorfolk"),
                      params=dict(edge="fade", layers="organic-cells", warp=0.7, radius_frac=0.45, lobes=2, hamlets=5,
                                  fade_len=300, citadel="false")),
    "harrowmouth": dict(gen="organic", seed=14, sid="harrow", size=1100, teams=("Landward Watch", "Harbour Guard"),
                        params=dict(edge="wall", layers="organic-mixed", warp=0.5, water="bay", radius_frac=0.55,
                                    citadel="false")),
    "cindermarket": dict(gen="organic", seed=3, sid="cinder", size=900, teams=("Ashward", "Slagward"),
                         params=dict(edge="wall", layers="organic-mixed", warp=0.4, arteries=7, arcs=3, radius_frac=0.7,
                                     min_district_area=900, citadel="false")),
}


def rd(p):
    with open(os.path.join(HERE, p), encoding="utf-8") as f:
        return f.read()


def wr(p, t):
    with open(os.path.join(HERE, p), "w", encoding="utf-8", newline="\n") as f:
        f.write(t)


def approach_path(name, team, base, gate, avoid):
    """`path` of a team approach road: the base, 2-3 seeded waypoints that swing alternately to either side of the base-gate line
    (about 10 % of its length), the last one straight out of the gate along its normal, then the gate. With `wobble` on the line the
    result meanders instead of running straight. Deterministic per (map, team)."""
    rng = random.Random("%s/%s" % (name, team))
    dx, dy = gate[0] - base[0], gate[1] - base[1]
    L = math.hypot(dx, dy)
    px, py = -dy / L, dx / L
    side = rng.choice((-1.0, 1.0))
    out = ["base_%s" % team]
    for t in (0.3, 0.55, 0.78):
        if (1.0 - t) * L < 55.0:
            continue
        off = side * L * rng.uniform(0.07, 0.13)
        side = -side
        for _ in range(3):
            x, y = round(base[0] + dx * t + px * off), round(base[1] + dy * t + py * off)
            if all(math.hypot(x - a[0], y - a[1]) > 40.0 for a in avoid):
                out.append("[%d, %d]" % (x, y))
                break
            off *= 0.5
    return out


def build(name, c):
    size = c["size"]
    s = generate(c["gen"], c["seed"], (size, size), {k: str(v) for k, v in c["params"].items()}, c["sid"])
    errs = check(s)
    assert not errs, errs
    # wall towns: `<sid>_gate_<k>` gates; open-edge towns: `<sid>_entry_a<k>` entries (points in the footprint ring, no wall to cross)
    kind = "entry" if c["params"].get("edge") in ("open", "fade") else "gate"
    gates = [g for g in s.gates if g.kind == kind and g.id.startswith("%s_%s_" % (c["sid"], kind))
             and "fort" not in g.id and not g.id.split("_%s_" % kind)[1].startswith("o")]
    # a gate that starts an outpost approach road is skipped: the team road would resolve onto the outpost's nodes
    used = set(r.nodes[0] for r in s.roads if "_apr_o" in r.id)
    gates = [g for g in gates if g.id not in used]
    margin = 70.0
    # a literal road vertex within 120 m of a base resolves to its spawn node in flowgraph derivation: open-edge bases keep clear of every town road
    road_vertices = [v for r in s.roads for v in r.polyline]

    def base_dist(g):
        """Largest distance <= 0.2 * size along the gate normal whose base stays inside the margin; 0 when none reaches 140 m (entry towns;
        walled towns go down to 40 m). A base's flowgraph reach is 120 m: nearer, it would bind the town's own road vertices."""
        for t in range(int(0.3 * size) if kind == "entry" else int(0.2 * size), 135 if kind == "entry" else 40, -5):
            x, y = g.at[0] + g.normal[0] * t, g.at[1] + g.normal[1] * t
            if margin <= x <= size - margin and margin <= y <= size - margin and (
                    kind != "entry" or min(math.hypot(x - v[0], y - v[1]) for v in road_vertices) > 125.0):
                return t
        return 0
    if kind == "entry":
        gates = [g for g in gates if base_dist(g)]
    best = max(((a, b) for a in gates for b in gates if a.id < b.id),
               key=lambda ab: -(ab[0].normal[0] * ab[1].normal[0] + ab[0].normal[1] * ab[1].normal[1]))
    pts, lines, teams, edges, nodes = [], [], [], [], []
    for team, g, tname in zip("AB", best, c["teams"]):
        d = base_dist(g)
        bx, by = g.at[0] + g.normal[0] * d, g.at[1] + g.normal[1] * d
        path = approach_path(name, team, (bx, by), g.at, [n.at for n in s.graph_nodes]) + [g.id]
        teams.append("  %s: { base: [%.0f, %.0f], name: %s }" % (team, bx, by, tname))
        lines.append("  - { id: approach_%s, type: road, path: [%s], width: 10, smooth: true, wobble: 14, wobble_scale: 140 }"
                     % (team.lower(), ", ".join(path)))
        nodes.append("  %s: { kind: spawn, team: %s, name: %s spawn }" % (team, team, tname))
        edges.append("  - %s - %s" % (team, g.id))
    spec = merge(rd("src/%s.base.yaml" % name),
                 ["teams:\n" + "\n".join(teams) + "\n", "lines:\n" + "\n".join(lines) + "\n", emit_spec(s)])
    wr("%s.yaml" % name, spec)
    wr("%s.settlement.json" % name, s.to_json())  # sidecar read by `mapgen build` (export_settlements.find_settlement)
    flow = ("format_version: 1\nname: %s\nbrief: |\n  Settlement movement graph of %s (generator %s, seed %d) with two spawns "
            "linked to gates %s and %s.\n\nnodes:\n%s\n\nedges:\n%s\n"
            % (name, name, c["gen"], c["seed"], best[0].id, best[1].id, "\n".join(nodes), "\n".join(edges)))
    wr("%s.flow.yaml" % name, merge(flow, [emit_flow(s)]))
    os.makedirs(os.path.join(HERE, "preview"), exist_ok=True)
    render_png(s).save(os.path.join(HERE, "preview", "%s_town.png" % name))
    print("%s: %d gates (%s), spawns at %s / %s" % (name, len(gates), ", ".join(g.id for g in gates), best[0].id, best[1].id))


if __name__ == "__main__":
    for n in (sys.argv[1:] or MAPS):
        build(n, MAPS[n])
