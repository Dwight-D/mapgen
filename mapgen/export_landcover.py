"""`mapgen export-landcover`: a map's land cover and keep-out areas as a nodachi layout (docs/landcover.md).

Input is a build's export bundle (regions.json, lines.json, pois.json, meta.json); output is
`<name>.landcover.nlayout.json` in nodachi's layout interchange format (`nodachi-layout` version 1), in the same frame
and `boundaryConvention` as the town layout of `mapgen export-nlayout`. Cells: one per land-cover polygon, one per
polygon hole (nodachi layouts have no holes), and keep-out polygons around lines, water, the town and POIs. Splines:
roads, trails and rivers. Points: POIs. Works for every map; no settlement file is needed.
"""
from __future__ import annotations

import json
import math
import os
from typing import Any, Dict, List, Optional, Tuple

from shapely.geometry import LineString, Point, Polygon, box
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

from .export_nlayout import FORMAT, VERSION, dumps

MARGIN = 2.0                    # m: default keep-out margin beyond a line's half width, a POI radius, the town outline
NOT_COVER = ("town", "lake")    # region types that are keep-out, not land cover
LINE_SOURCE = {"road": "road", "trail": "road", "river": "river", "cliff": "cliff", "wall": "wall"}
SPLINE_TYPES = ("road", "trail", "river")
SIMPLIFY = 0.05                 # m: keep-out outlines drop vertices within this of the outline (sub-metre edges at joins)
MIN_AREA = 0.01                 # m²: split pieces smaller than this are dropped
LINE_QUAD_SEGS = 4              # buffer arc segments per quarter circle: lines (round caps and joins)
DISC_QUAD_SEGS = 8              # POI discs: a 32-gon


def _c(v: float) -> float:
    return round(float(v), 3) + 0.0


def _ring(coords) -> Optional[List[List[float]]]:
    """Counter-clockwise outer loop as nodachi [x, 0, z] vertices: mm-rounded, no repeated closing or consecutive
    duplicate vertices; None when fewer than 3 vertices remain."""
    pts: List[Tuple[float, float]] = []
    for x, z in list(coords)[:-1] if list(coords)[0] == list(coords)[-1] else list(coords):
        p = (_c(x), _c(z))
        if not pts or pts[-1] != p:
            pts.append(p)
    while len(pts) > 1 and pts[0] == pts[-1]:
        pts.pop()
    if len(pts) < 3:
        return None
    a2 = sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts)))
    if a2 == 0:
        return None
    if a2 < 0:
        pts = pts[::-1]
    return [[x, 0.0, z] for x, z in pts]


def _polys(g) -> List[Polygon]:
    if g.is_empty:
        return []
    if isinstance(g, Polygon):
        return [g] if g.area >= MIN_AREA else []
    out: List[Polygon] = []
    for part in getattr(g, "geoms", []):
        out.extend(_polys(part))
    return out


def hole_free(g) -> List[Polygon]:
    """The polygons of `g` cut into hole-free pieces covering exactly the same ground. Each hole is opened by a
    north-south cut through an interior point of it; pieces come west half first, then east, recursively."""
    out: List[Polygon] = []
    for p in _polys(g):
        if not p.interiors:
            out.append(orient(p, 1.0))
            continue
        x = Polygon(p.interiors[0]).representative_point().x
        minx, miny, maxx, maxy = p.bounds
        for half in (box(minx - 1, miny - 1, x, maxy + 1), box(x, miny - 1, maxx + 1, maxy + 1)):
            out.extend(hole_free(p.intersection(half)))
    return out


def _area(a: float) -> float:
    return round(float(a), 2) + 0.0


def landcover_doc(name: str, size, regions: List[Dict[str, Any]], lines: List[Dict[str, Any]],
                  pois: List[Dict[str, Any]], margin: float = MARGIN) -> Dict[str, Any]:
    from . import __version__
    cells: List[Dict[str, Any]] = []
    splines: List[Dict[str, Any]] = []
    points: List[Dict[str, Any]] = []
    frame = box(0.0, 0.0, float(size[0]), float(size[1]))

    def cell(outer, attrs):
        if outer is not None:
            cells.append({"id": len(cells), "outer": outer, "attrs": attrs})

    for order, r in enumerate(regions):
        if r["type"] in NOT_COVER:
            continue
        for part, pg in enumerate(r["polygons"]):
            shell = Polygon(pg["outer"])
            holes = [Polygon(h) for h in pg["holes"]]
            attrs: Dict[str, Any] = {"landCover": r["type"], "regionId": r["id"], "scatter": bool(r["scatter"]),
                                     "area": _area(shell.area - sum(h.area for h in holes)), "part": part,
                                     "paintOrder": order}
            if r.get("label"):
                attrs["label"] = r["label"]
            if r.get("height") is not None:
                attrs["height"] = float(r["height"])
            if r.get("impassable"):
                attrs["impassable"] = True
            cell(_ring(pg["outer"]), attrs)
            for k, h in enumerate(holes):
                cell(_ring(pg["holes"][k]), {"landCover": "hole", "parentRegion": r["id"], "parentCover": r["type"],
                                             "area": _area(h.area), "part": part, "hole": k, "paintOrder": order})

    def keepout(geom, attrs):
        for piece in hole_free(geom.intersection(frame).simplify(SIMPLIFY, preserve_topology=True)):
            a = dict(attrs)
            a["area"] = _area(piece.area)
            cell(_ring(piece.exterior.coords), a)

    for ln in lines:
        src = LINE_SOURCE.get(ln["type"])
        sid = -1
        if ln["type"] in SPLINE_TYPES:
            sid = len(splines)
            splines.append({"id": sid, "points": [[_c(p[0]), 0.0, _c(p[1])] for p in ln["points"]],
                            "interpolation": "linear",
                            "attrs": {"lineType": ln["type"], "lineId": ln["id"], "width": float(ln["width"])}})
        if src is None or len(ln["points"]) < 2:
            continue
        half = float(ln["width"]) / 2.0 + margin
        geom = LineString([(p[0], p[1]) for p in ln["points"]]).buffer(half, quad_segs=LINE_QUAD_SEGS)
        attrs = {"landCover": "keepout", "source": src, "sourceType": ln["type"], "sourceId": ln["id"],
                 "width": float(ln["width"]), "margin": float(margin)}
        if sid >= 0:
            attrs["splineId"] = sid
        keepout(geom, attrs)
    for r in regions:
        if r["type"] == "lake":
            geom = unary_union([Polygon(pg["outer"], pg["holes"]) for pg in r["polygons"]]).buffer(
                margin, quad_segs=LINE_QUAD_SEGS)
            keepout(geom, {"landCover": "keepout", "source": "water", "sourceType": "lake", "sourceId": r["id"],
                           "margin": float(margin)})
    town = [Polygon(pg["outer"], pg["holes"]) for r in regions if r["type"] == "town" for pg in r["polygons"]]
    if town:
        keepout(unary_union(town).buffer(margin, quad_segs=LINE_QUAD_SEGS),
                {"landCover": "keepout", "source": "town", "sourceType": "town", "margin": float(margin)})
    for p in pois:
        geom = Point(p["x"], p["z"]).buffer(float(p["r"]) + margin, quad_segs=DISC_QUAD_SEGS)
        keepout(geom, {"landCover": "keepout", "source": "poi", "sourceType": p["type"], "sourceId": p["id"],
                       "radius": float(p["r"]), "margin": float(margin)})
        attrs = {"poiType": p["type"], "poiId": p["id"], "radius": float(p["r"])}
        if p.get("team"):
            attrs["team"] = p["team"]
        if p.get("label"):
            attrs["label"] = p["label"]
        points.append({"id": len(points), "position": [_c(p["x"]), 0.0, _c(p["z"])], "attrs": attrs})

    return {"format": FORMAT, "version": VERSION,
            "meta": {"units": "metres", "frame": "generator-local", "boundaryConvention": "centreline",
                     "generator": "mapgen %s export-landcover: map %s (margin %s m)"
                                  % (__version__, name, ("%g" % margin))},
            "cells": cells, "splines": splines, "points": points}


def _bundle_dir(path: str) -> Optional[str]:
    for d in (path, os.path.join(path, "export")):
        if os.path.isfile(os.path.join(d, "regions.json")) and os.path.isfile(os.path.join(d, "meta.json")):
            return d
    return None


def resolve(target: str, out_root: str = "out") -> str:
    """The export bundle directory for a spec file (`<out>/<name>/export`, built beforehand) or a bundle directory."""
    from . import schema
    if os.path.isdir(target):
        d = _bundle_dir(target)
        if d is None:
            raise ValueError("%s has no export bundle (regions.json, meta.json)" % target)
        return d
    spec = schema.load_spec(target)
    d = _bundle_dir(os.path.join(out_root, spec.name))
    if d is None:
        raise ValueError("no export bundle for %s under %s (run mapgen build first)"
                         % (target, os.path.join(out_root, spec.name)))
    return d


def from_bundle(d: str, margin: float = MARGIN) -> Tuple[Dict[str, Any], str]:
    def load(f):
        with open(os.path.join(d, f), "r", encoding="utf-8") as fh:
            return json.load(fh)
    meta = load("meta.json")
    lines = load("lines.json") if os.path.isfile(os.path.join(d, "lines.json")) else []
    pois = load("pois.json") if os.path.isfile(os.path.join(d, "pois.json")) else []
    doc = landcover_doc(meta["name"], meta["size"], load("regions.json"), lines, pois, margin)
    return doc, os.path.join(d, "%s.landcover.nlayout.json" % meta["name"])


def summary(doc: Dict[str, Any]) -> Dict[str, int]:
    """Cell counts per landCover (keep-outs per source as `keepout:<source>`), sorted by key."""
    n: Dict[str, int] = {}
    for c in doc["cells"]:
        a = c["attrs"]
        k = a["landCover"] if a["landCover"] != "keepout" else "keepout:" + a["source"]
        n[k] = n.get(k, 0) + 1
    return dict(sorted(n.items()))


def cmd_export_landcover(args) -> int:
    from . import schema
    if not (args.margin >= 0 and math.isfinite(args.margin)):
        print("error: --margin must be a finite number >= 0")
        return 1
    try:
        doc, out = from_bundle(resolve(args.target, args.out), args.margin)
    except FileNotFoundError as e:
        print("error: file not found: %s" % (e.filename or e))
        return 1
    except schema.SpecError as e:
        print("%d error(s) in %s:" % (len(e.errors), args.target))
        for line in e.errors:
            print("  " + line)
        return 1
    except (ValueError, KeyError, TypeError) as e:
        print("error: %s" % e)
        return 1
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(dumps(doc))
    print("wrote %s: %d cells (%s), %d splines, %d points"
          % (out, len(doc["cells"]), ", ".join("%s %d" % kv for kv in summary(doc).items()), len(doc["splines"]),
             len(doc["points"])))
    return 0


def add_parser(sub) -> None:
    p = sub.add_parser("export-landcover",
                       help="write a map's land cover and keep-out areas as a nodachi layout (<name>.landcover.nlayout.json)")
    p.add_argument("target", help="a built map spec (reads <out>/<name>/export/), or a build/bundle directory")
    p.add_argument("--out", default="out", help="build root for a spec target (default out)")
    p.add_argument("--margin", type=float, default=MARGIN,
                   help="keep-out margin in metres beyond line half widths, POI radii and the town outline (default %g)"
                        % MARGIN)
    p.set_defaults(func=cmd_export_landcover)
