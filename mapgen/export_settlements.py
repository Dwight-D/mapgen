"""settlements.json: the settlement plan behind a town map (EXPORT_FORMAT.md section 3.10).

A town map's `town` regions, `road` lines, `wall` lines and `gate` crossings are emitted from a settlement
(mapgen/town/model.py). The settlement itself is not part of the spec; `mapgen build` reads it from a settlement file
found next to the spec or in the build directory (`find_settlement`) and writes the placement data the bundle's other
files do not carry: district kind, density and layer tree, road class, the road each block edge faces, gate normals and
road links, wall towers and bridge spans. A map without a settlement file gets no settlements.json.
"""
from __future__ import annotations

import json
import math
import os
from typing import Any, Dict, List, Optional, Tuple

from shapely.geometry import LineString, Point

from .schema import MapSpec
from .town.model import Settlement
from .town.naming import district_names, top_level_of
from .town.water import wall_runs

FORMAT = "mapgen-settlements"
FORMAT_VERSION = 1
FILE = "settlements.json"
FACE_TOL = 0.5          # m: a block edge faces a road when both ends and its midpoint lie this close to the road's polyline
CLASS_RANK = {"artery": 0, "boulevard": 1, "ring": 2, "bridge": 3, "approach": 4, "street": 5}


def _r(v: float) -> float:
    return round(float(v), 2) + 0.0


def _xz(p) -> List[float]:
    return [_r(p[0]), _r(p[1])]


def find_settlement(spec: MapSpec, build_dir: str) -> Optional[str]:
    """The settlement file of a spec: `<spec dir>/<spec stem>.settlement.json`, else `<build dir>/settlement.json`
    (where `mapgen town generate --out <build dir>` writes it); None when neither exists."""
    cands = []
    if spec.spec_path:
        stem = os.path.splitext(spec.spec_path)[0]
        cands.append(stem + ".settlement.json")
    cands.append(os.path.join(build_dir, "settlement.json"))
    for c in cands:
        if os.path.isfile(c):
            return c
    return None


def load_settlement(path: str) -> Settlement:
    with open(path, "r", encoding="utf-8") as f:
        return Settlement.from_json(f.read())


def mismatches(spec: MapSpec, s: Settlement) -> List[str]:
    """Settlement features that the spec does not carry under the same id (a stale or foreign settlement file)."""
    regions = {r.id for r in spec.regions if r.type == "town"}
    roads = {ln.id for ln in spec.lines if ln.type == "road"}
    out = ["district %s is not a town region of the spec" % d.id for d in s.districts if d.id not in regions]
    out += ["road %s is not a road line of the spec" % r.id for r in s.roads if r.id not in roads]
    return out


class _RoadIndex:
    """Road polylines with padded bounding boxes, for the block-edge test."""

    def __init__(self, roads):
        self.items = []
        for r in roads:
            ls = LineString([tuple(p) for p in r.polyline])
            x0, y0, x1, y1 = ls.bounds
            self.items.append((r, ls, (x0 - FACE_TOL, y0 - FACE_TOL, x1 + FACE_TOL, y1 + FACE_TOL)))

    def facing(self, a, b) -> Optional[Tuple[Any, float]]:
        """(road, offset) of the road the edge a-b runs along, offset = the largest distance of a, b and the midpoint
        to the road polyline; None when no road is within FACE_TOL at all three. Ties: smallest offset, then the higher
        road class (artery first), then id."""
        pts = [Point(a[0], a[1]), Point(b[0], b[1]), Point((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)]
        lo_x, hi_x = min(a[0], b[0]), max(a[0], b[0])
        lo_y, hi_y = min(a[1], b[1]), max(a[1], b[1])
        best = None
        for r, ls, (x0, y0, x1, y1) in self.items:
            if hi_x < x0 or lo_x > x1 or hi_y < y0 or lo_y > y1:
                continue
            off = 0.0
            for p in pts:
                off = max(off, ls.distance(p))
                if off > FACE_TOL:
                    break
            if off > FACE_TOL:
                continue
            key = (round(off, 3), CLASS_RANK.get(r.cls, 9), r.id)
            if best is None or key < best[0]:
                best = (key, r, off)
        return None if best is None else (best[1], best[2])


def _span(polyline) -> List[float]:
    """Unit direction of a bridge road at its middle vertex (the direction the bridge spans)."""
    n = len(polyline)
    i = n // 2
    a = polyline[max(0, i - 1)]
    b = polyline[min(n - 1, i + 1)] if n > 2 else polyline[-1]
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = math.hypot(dx, dy) or 1.0
    return [round(dx / L, 4) + 0.0, round(dy / L, 4) + 0.0]


def settlement_record(spec: MapSpec, s: Settlement) -> Dict[str, Any]:
    line_ids = {ln.id for ln in spec.lines}
    names = district_names(s)
    top = top_level_of(s)
    group_name = {top[did]: name for did, name in names.items()}
    idx = _RoadIndex(s.roads)

    districts = []
    for d in s.districts:
        ring = d.polygon
        n = len(ring)
        edges = []
        for i in range(n):
            hit = idx.facing(ring[i], ring[(i + 1) % n])
            if hit is None:
                edges.append(None)
            else:
                r, off = hit
                edges.append({"road": r.id, "class": r.cls, "width": _r(r.width), "offset": round(off, 3) + 0.0})
        districts.append({
            "id": d.id, "key": d.key, "kind": d.kind, "density": _r(d.density), "area": _r(d.area),
            "depth": d.depth, "parent": d.parent or None, "top": top[d.id], "top_label": group_name.get(top[d.id]),
            "label": names.get(d.id), "inside_wall": d.inside_wall, "waterfront": d.waterfront,
            "landmarks": list(d.landmarks), "polygon": [_xz(p) for p in ring], "edges": edges})

    roads = []
    for r in s.roads:
        closed = len(r.polyline) > 2 and list(r.polyline[0]) == list(r.polyline[-1])
        roads.append({"id": r.id, "class": r.cls, "width": _r(r.width), "closed": closed,
                      "polyline": [_xz(p) for p in r.polyline], "nodes": list(r.nodes)})

    walls = []
    for w in s.walls:
        runs = []
        for k, _ in enumerate(wall_runs(w)):
            rid = w.id if k == 0 else "%s_%d" % (w.id, k + 1)
            if rid in line_ids:
                runs.append(rid)
        walls.append({"id": w.id, "kind": w.kind, "thickness": _r(w.thickness), "polyline": [_xz(p) for p in w.polyline],
                      "lines": runs, "open_shore": [list(x) for x in w.open_shore], "towers": [_xz(p) for p in w.towers]})

    gates = [{"id": g.id, "kind": g.kind, "x": _r(g.at[0]), "z": _r(g.at[1]),
              "normal": [round(g.normal[0], 4) + 0.0, round(g.normal[1], 4) + 0.0],
              "wall": g.wall or None, "road": g.road or None} for g in s.gates]

    bridges = []
    for r in s.roads:
        if r.cls == "bridge":
            mid = r.polyline[len(r.polyline) // 2]
            bridges.append({"id": r.id + "_x", "road": r.id, "x": _r(mid[0]), "z": _r(mid[1]), "span": _span(r.polyline)})

    landmarks = [{"id": l.id, "kind": l.kind, "x": _r(l.at[0]), "z": _r(l.at[1]), "r": _r(l.r),
                  "district": l.district, "label": l.label or None} for l in s.landmarks]

    layers = [{"id": n.id, "parent": n.parent or None, "depth": n.depth, "use": n.use, "children": list(n.children)}
              for n in s.layers]

    return {"id": s.id, "generator": s.generator, "seed": s.seed, "centre": _xz(s.centre),
            "footprint": [_xz(p) for p in s.footprint], "districts": districts, "layers": layers, "roads": roads,
            "walls": walls, "gates": gates, "bridges": bridges, "landmarks": landmarks}


def settlements_doc(spec: MapSpec, settlements: List[Settlement]) -> Dict[str, Any]:
    from . import __version__
    return {"format": FORMAT, "format_version": FORMAT_VERSION, "mapgen_version": __version__, "name": spec.name,
            "settlements": [settlement_record(spec, s) for s in settlements]}


def dump(path: str, doc: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, indent=1)
        f.write("\n")


def write_settlements(spec: MapSpec, export_dir: str, build_dir: str, warnings: List[str]) -> List[str]:
    """Write export_dir/settlements.json when the spec has a settlement file whose ids match the spec; else nothing
    (a mismatch is reported through `warnings`). Returns the written file names."""
    path = find_settlement(spec, build_dir)
    if path is None:
        return []
    try:
        s = load_settlement(path)
    except (ValueError, KeyError, TypeError) as e:
        warnings.append("settlements.json not written: %s is not a settlement file (%s)" % (path, e))
        return []
    bad = mismatches(spec, s)
    if bad:
        warnings.append("settlements.json not written: %s does not match the spec (%d mismatches, first: %s)"
                        % (path, len(bad), bad[0]))
        return []
    dump(os.path.join(export_dir, FILE), settlements_doc(spec, [s]))
    return [FILE]
