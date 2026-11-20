"""Painting geometry for the spec editor: brush strokes to integer-metre polygons and the span-patch ops that merge,
split, erase and convert `poly` regions. Pure functions of the parsed spec document; no HTTP, no state.
Design: docs/investigations/2026-11-19-map-editor-design.md 5.1 (slice 4: GDU-53).

Rules
- A stroke (polyline + radius in metres) becomes `LineString.buffer(radius)` with round caps, clipped to the map,
  simplified at STROKE_TOL, every vertex rounded to an integer metre. All arithmetic is plain float with that fixed
  rounding, so the same stroke yields the same YAML bytes.
- Merge/erase targets are regions authored as `shape: poly` whose `points` are literal coordinate pairs. Blob, band and
  reference-point polys are never rewritten; paint adds a new poly on top of them; erase reports them as `skipped`
  and `convert_ops` turns one into a poly in one explicit action.
- Paint over a same-type, same-`impassable` painted poly merges into it (the largest of several keeps its id, the others
  are deleted). A union that is several parts yields one region per part (the largest keeps the id). A ring with a hole
  keeps its simple outer ring and appends a `clearing` poly for the hole (design 5.1, owner choice 5).
- Paint of a non-`clearing` type that would merge under a later painted `clearing` poly (which would hide it) appends a
  new region on top instead of merging, so painted terrain is always visible.
- A ring never has more than MAX_RING vertices: the simplify tolerance doubles until it fits.
"""
from __future__ import annotations

import dataclasses
import math
from typing import Any, Dict, List, Optional, Tuple

from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

from . import geometry

STROKE_TOL = 1.5       # metres: simplification of the brush polygon
REWRITE_TOL = 0.5      # metres: simplification of a rewritten (merged / erased) ring
MAX_RING = 400         # vertices per ring cap (the tolerance doubles until a ring fits)
MIN_AREA = 4.0         # m2: smaller polygon parts are dropped
HOLE_MIN = 25.0        # m2: smaller holes are filled instead of becoming a clearing
TOUCH_MIN = 0.5        # m2: erase / carve overlaps below this are ignored
BRUSH_MIN, BRUSH_MAX = 1.0, 300.0
MAX_STROKE_POINTS = 5000
QUAD_SEGS = 8


class PaintError(Exception):
    """A paint request that cannot be planned (bad input); the message is for the author."""


# ----------------------------------------------------------------------------- geometry helpers

def _polygons_of(g) -> List[Polygon]:
    if g is None or g.is_empty:
        return []
    if g.geom_type == "Polygon":
        return [g]
    if hasattr(g, "geoms"):
        out = []  # type: List[Polygon]
        for p in g.geoms:
            out.extend(_polygons_of(p))
        return out
    return []


def _ring(coords) -> Optional[List[Tuple[int, int]]]:
    out = []  # type: List[Tuple[int, int]]
    for x, y in coords:
        q = (int(round(x)), int(round(y)))
        if not out or out[-1] != q:
            out.append(q)
    if len(out) > 1 and out[0] == out[-1]:
        out.pop()
    return out if len(out) >= 3 else None


def _round_polygon(p: Polygon) -> Optional[Polygon]:
    shell = _ring(p.exterior.coords)
    if shell is None:
        return None
    holes = [r for r in (_ring(i.coords) for i in p.interiors) if r]
    return Polygon(shell, holes)


def _valid_int_polys(g) -> List[Polygon]:
    out = []  # type: List[Polygon]
    stack = _polygons_of(g)
    for _ in range(4):
        nxt = []  # type: List[Polygon]
        for p in stack:
            q = _round_polygon(p) if not p.is_empty else None
            if q is None:
                continue
            if q.is_valid:
                out.append(q)
            else:
                nxt.extend(_polygons_of(q.buffer(0)))
        stack = nxt
        if not stack:
            break
    return out


def finalize(geom, tol: float) -> List[Polygon]:
    """Simplify, round to integer metres, repair and sort (largest first) the polygon parts of `geom`; parts under
    MIN_AREA are dropped; the tolerance doubles until every exterior ring has at most MAX_RING vertices."""
    t = tol
    polys = []  # type: List[Polygon]
    while True:
        simp = geom.simplify(t) if t > 0 else geom
        polys = [p for p in _valid_int_polys(simp) if p.area >= MIN_AREA]
        if all(len(p.exterior.coords) - 1 <= MAX_RING for p in polys) or t > 400:
            break
        t = t * 2 if t > 0 else 1.0
    polys.sort(key=lambda p: (-p.area, p.bounds))
    return polys


def ring_points(p: Polygon) -> List[List[int]]:
    """Exterior ring as [[x, y], ...]: counter-clockwise, starting at the lowest (x, y) vertex, not closed."""
    c = [(int(x), int(y)) for x, y in list(orient(p, 1.0).exterior.coords)[:-1]]
    i = min(range(len(c)), key=lambda k: c[k])
    c = c[i:] + c[:i]
    return [[x, y] for x, y in c]


def brush_polygon(points, radius: float, size) -> Optional[Polygon]:
    """The brush area of a stroke as one valid integer polygon clipped to the map, or None when it is empty."""
    if not isinstance(points, (list, tuple)) or not points:
        raise PaintError("a stroke needs at least one point")
    if len(points) > MAX_STROKE_POINTS:
        raise PaintError("a stroke has at most %d points" % MAX_STROKE_POINTS)
    if isinstance(radius, bool) or not isinstance(radius, (int, float)) or not math.isfinite(radius) \
            or not BRUSH_MIN <= radius <= BRUSH_MAX:
        raise PaintError("the brush radius must be between %g and %g metres" % (BRUSH_MIN, BRUSH_MAX))
    pts = []  # type: List[Tuple[float, float]]
    for p in points:
        if not (isinstance(p, (list, tuple)) and len(p) == 2) or any(isinstance(v, bool) or not isinstance(v, (int, float))
                                                                       or not math.isfinite(v) for v in p):
            raise PaintError("a stroke point is [x, y] in metres")
        q = (float(p[0]), float(p[1]))
        if not pts or pts[-1] != q:
            pts.append(q)
    r = float(radius)
    g = Point(pts[0]).buffer(r, quad_segs=QUAD_SEGS) if len(pts) == 1 else \
        LineString(pts).buffer(r, quad_segs=QUAD_SEGS, cap_style="round", join_style="round")
    g = g.intersection(box(0.0, 0.0, float(size[0]), float(size[1])))
    parts = finalize(g, min(STROKE_TOL, max(0.5, 0.08 * r)))   # small brushes keep their roundness
    if not parts:
        return None
    u = unary_union(parts)
    return u if u.geom_type == "Polygon" else MultiPolygon(_polygons_of(u))


# ----------------------------------------------------------------------------- the spec's painted regions

class _Target:
    def __init__(self, idx: int, raw: Dict[str, Any], poly):
        self.idx, self.raw, self.poly = idx, raw, poly
        self.id, self.type = raw["id"], raw.get("type")
        self.impassable = raw.get("impassable") is True


def _literal_points(v) -> bool:
    return (isinstance(v, list) and len(v) >= 3 and all(
        isinstance(p, list) and len(p) == 2 and all(isinstance(c, (int, float)) and not isinstance(c, bool) for c in p) for p in v))


def painted_polys(py: Any) -> List[_Target]:
    """Regions authored as `poly` with literal points: the only ones paint and erase rewrite."""
    out = []  # type: List[_Target]
    regs = py.get("regions") if isinstance(py, dict) else None
    for i, r in enumerate(regs or []):
        if not (isinstance(r, dict) and isinstance(r.get("id"), str) and r.get("shape") == "poly" and _literal_points(r.get("points"))):
            continue
        g = Polygon([(float(x), float(y)) for x, y in r["points"]])
        if not g.is_valid:
            g = g.buffer(0)
        if g.is_empty or g.area <= 0:
            continue
        out.append(_Target(i, r, g))
    return out


def _add_op(rtype: str, ring: List[List[int]], impassable: bool, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    val = {"type": rtype, "shape": "poly"}  # type: Dict[str, Any]
    if extra:
        val.update(extra)
    val["points"] = ring
    if impassable and "impassable" not in val:
        val["impassable"] = True
    return {"op": "add", "section": "regions", "value": val}


class Plan:
    def __init__(self, mode: str):
        self.mode = mode
        self.ops = []  # type: List[Dict[str, Any]]
        self.modified = []  # type: List[str]
        self.deleted = []  # type: List[str]
        self.created = 0
        self.holes = 0
        self.vertices = []  # type: List[int]
        self.skipped = []  # type: List[Dict[str, str]]
        self.notes = []  # type: List[str]

    def summary(self) -> Dict[str, Any]:
        return {"mode": self.mode, "modified": self.modified, "deleted": self.deleted, "created": self.created, "holes": self.holes,
                "vertices": self.vertices, "skipped": self.skipped, "notes": self.notes}


def _holes_to_clearings(plan: Plan, poly: Polygon, rtype: str, clearings: List[_Target]) -> None:
    if rtype == "clearing":
        return   # a hole in a clearing is just filled
    for ring in poly.interiors:
        hp = Polygon(ring)
        if hp.area < HOLE_MIN:
            continue
        if any(c.poly.buffer(0.5).contains(hp) or c.poly.contains(hp) for c in clearings):
            continue
        parts = finalize(hp, REWRITE_TOL)
        for q in parts:
            plan.ops.append(_add_op("clearing", ring_points(q), False))
            plan.created += 1
            plan.vertices.append(len(q.exterior.coords) - 1)
        plan.holes += 1


def plan_paint(py: Any, size, brush, rtype: str, impassable: bool) -> Plan:
    """Ops that paint `brush` (a polygon) as region type `rtype`."""
    plan = Plan("paint")
    painted = painted_polys(py)
    clearings = [t for t in painted if t.type == "clearing"]
    same = [t for t in painted if t.type == rtype and t.impassable == bool(impassable) and t.poly.intersects(brush)]
    if same and rtype != "clearing":
        first = min(t.idx for t in same)
        hide = [c for c in clearings if c.idx > first and c.poly.intersection(brush).area > TOUCH_MIN]
        if hide:
            plan.notes.append("painted on top as a new region: %s would hide a merge" % ", ".join(c.id for c in hide))
            same = []
    if not same:
        for p in finalize(brush, 0.0):
            plan.ops.append(_add_op(rtype, ring_points(p), impassable))
            plan.created += 1
            plan.vertices.append(len(p.exterior.coords) - 1)
            _holes_to_clearings(plan, p, rtype, clearings)
        return plan
    union = unary_union([brush] + [t.poly for t in same])
    parts = finalize(union, REWRITE_TOL)
    keep = sorted(same, key=lambda t: (-t.poly.area, t.idx))[0]
    for i, p in enumerate(parts):
        if i == 0:
            plan.ops.append({"op": "set", "path": "regions.%s.points" % keep.id, "value": ring_points(p)})
            plan.modified.append(keep.id)
        else:
            plan.ops.append(_add_op(rtype, ring_points(p), impassable))
            plan.created += 1
        plan.vertices.append(len(p.exterior.coords) - 1)
        _holes_to_clearings(plan, p, rtype, clearings)
    for t in same:
        if t is not keep:
            plan.ops.append({"op": "delete", "path": "regions.%s" % t.id})
            plan.deleted.append(t.id)
    if len(same) > 1:
        plan.notes.append("merged %d regions into %s" % (len(same), keep.id))
    return plan


def _outline(reg) -> Optional[Any]:
    """The base outline of a loaded region (no rough, no warp), for overlap reports and conversion."""
    try:
        if reg.shape == "blob":
            return geometry.blob_polygon(dataclasses.replace(reg, rough=0.0), 0)
        if reg.shape == "band":
            return geometry.band_polygon(dataclasses.replace(reg, rough=0.0), 0)
        if reg.shape == "poly" and reg.points:
            g = Polygon(reg.points)
            return g if g.is_valid else g.buffer(0)
    except Exception:
        return None
    return None


def plan_erase(py: Any, spec, brush, rtype: Optional[str]) -> Plan:
    """Ops that subtract `brush` from the painted polys of `rtype` (None: any type)."""
    plan = Plan("erase")
    painted = painted_polys(py)
    clearings = [t for t in painted if t.type == "clearing"]
    hit_ids = set()
    for t in painted:
        if rtype is not None and t.type != rtype:
            continue
        if not t.poly.intersects(brush) or t.poly.intersection(brush).area < TOUCH_MIN:
            continue
        hit_ids.add(t.id)
        parts = finalize(t.poly.difference(brush), REWRITE_TOL)
        if not parts:
            plan.ops.append({"op": "delete", "path": "regions.%s" % t.id})
            plan.deleted.append(t.id)
            continue
        extra = {k: v for k, v in t.raw.items() if k not in ("id", "type", "shape", "points", "seed", "label", "label_at")}
        for i, p in enumerate(parts):
            if i == 0:
                plan.ops.append({"op": "set", "path": "regions.%s.points" % t.id, "value": ring_points(p)})
                plan.modified.append(t.id)
            else:
                plan.ops.append(_add_op(t.type, ring_points(p), False, dict(extra)))
                plan.created += 1
            plan.vertices.append(len(p.exterior.coords) - 1)
            _holes_to_clearings(plan, p, t.type, clearings)
    for reg in spec.regions:
        if reg.id in hit_ids or (rtype is not None and reg.type != rtype):
            continue
        o = _outline(reg)
        if o is not None and not o.is_empty and o.intersection(brush).area >= TOUCH_MIN:
            why = "uses point references" if reg.shape == "poly" else "is a %s" % reg.shape
            plan.skipped.append({"id": reg.id, "shape": reg.shape, "why": why})
    return plan


def plan_convert(py: Any, spec, size, rid: str) -> Plan:
    """Ops that turn the blob or band region `rid` into a `poly` of its base outline (rough and warp noise dropped)."""
    plan = Plan("convert")
    reg = None
    for r in spec.regions:
        if r.id == rid:
            reg = r
    if reg is None or reg.scatter is not None:
        raise PaintError("no region %r" % rid)
    if reg.shape not in ("blob", "band"):
        raise PaintError("%s is already a %s; only a blob or band converts" % (rid, reg.shape))
    o = _outline(reg)
    parts = finalize(o.intersection(box(0.0, 0.0, float(size[0]), float(size[1]))), STROKE_TOL) if o is not None else []
    if not parts:
        raise PaintError("%s has no area inside the map to convert" % rid)
    raw = None
    for r in (py.get("regions") or []):
        if isinstance(r, dict) and r.get("id") == rid:
            raw = r
    if raw is None:
        raise PaintError("no region %r" % rid)
    base = "regions.%s" % rid
    plan.ops.append({"op": "set", "path": base + ".shape", "value": "poly"})
    for k in ("at", "r", "squash", "angle", "path", "width", "rough", "warp", "warp_scale"):
        if k in raw:
            plan.ops.append({"op": "delete", "path": "%s.%s" % (base, k)})
    plan.ops.append({"op": "put", "path": base + ".points", "value": ring_points(parts[0]), "after": "shape"})
    plan.modified.append(rid)
    plan.vertices.append(len(parts[0].exterior.coords) - 1)
    plan.notes.append("converted %s to a poly of its base outline (rough and warp noise dropped)" % rid)
    return plan
