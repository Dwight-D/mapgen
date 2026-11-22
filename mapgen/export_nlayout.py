"""`mapgen export-nlayout`: a town's settlement plan as a nodachi layout file (docs/nlayout.md).

Input is the settlements.json document (export_settlements.py); output is `<name>.nlayout.json` in nodachi's layout
interchange format (nodachi Docs/LAYOUT-INTERCHANGE.md, format `nodachi-layout` version 1): one cell per leaf district
(block), one linear spline per road, one point per gate and landmark. Cells are bounded by road centrelines
(`boundaryConvention: centreline`); nodachi's frontage packer insets lots by half the road width itself.
"""
from __future__ import annotations

import json
import math
import os
from typing import Any, Dict, List, Optional, Tuple

FORMAT = "nodachi-layout"
VERSION = 1
ON_SPLINE = 0.001       # m: an edge is given a splineRef only when it lies this close to the road (half nodachi's 2 mm check tolerance)

ROAD_CLASS = {"artery": "main", "boulevard": "main", "ring": "main", "bridge": "main", "approach": "street",
              "street": "street"}           # mapgen road class -> nodachi Road Classes preset
DISTRICT_TYPE = {"market": "market", "residential": "residential", "slum": "residential", "craft": "industrial",
                 "harbour": "industrial", "core": "civic", "temple": "civic", "citadel": "civic", "outskirt": "rural"}
LANDMARK_TYPE = {"square": "plaza"}         # other landmark kinds keep their mapgen name
OPEN_LANDMARKS = ("square", "market")       # plaza landmark kinds: the holding district is open ground (no frontage)
PLAZA_KINDS = ("plaza",)                    # district kinds that are a plaza without a landmark


def _c(v: float) -> float:
    return round(float(v), 3) + 0.0


def _v3(p) -> List[float]:
    return [_c(p[0]), 0.0, _c(p[1])]


def _area2(ring) -> float:
    return sum(ring[i][0] * ring[(i + 1) % len(ring)][1] - ring[(i + 1) % len(ring)][0] * ring[i][1]
               for i in range(len(ring)))


def is_plaza_landmark(lm: Dict[str, Any], generator: str) -> bool:
    """A town square: `square` from any generator, or the grid generator's central `market` ("Market Square")."""
    return lm["kind"] == "square" or (lm["kind"] == "market" and generator == "grid")


def nlayout_doc(sdoc: Dict[str, Any]) -> Dict[str, Any]:
    from . import __version__
    cells: List[Dict[str, Any]] = []
    splines: List[Dict[str, Any]] = []
    points: List[Dict[str, Any]] = []
    for st in sdoc["settlements"]:
        spline_of: Dict[str, int] = {}
        for r in st["roads"]:
            pts = r["polyline"][:-1] if r["closed"] else r["polyline"]
            sp = {"id": len(splines), "points": [_v3(p) for p in pts], "interpolation": "linear",
                  "attrs": {"roadClass": ROAD_CLASS.get(r["class"], "street"), "roadWidth": float(r["width"]),
                            "mapgenClass": r["class"], "roadId": r["id"], "settlement": st["id"]}}
            if r["closed"]:
                sp["closed"] = True
            spline_of[r["id"]] = sp["id"]
            splines.append(sp)
        plaza_of: Dict[str, Dict[str, Any]] = {}      # district id -> its first plaza landmark
        for l in st["landmarks"]:
            if l["kind"] in OPEN_LANDMARKS and is_plaza_landmark(l, st["generator"]):
                plaza_of.setdefault(l["district"], l)
        open_d = set(plaza_of) | {d["id"] for d in st["districts"] if d["kind"] in PLAZA_KINDS}
        first = len(cells)
        for d in st["districts"]:
            ring = [list(p) for p in d["polygon"]]
            faces = list(d["edges"])
            if _area2(ring) < 0:      # nodachi wants counter-clockwise outer loops (seen from above, x east, z north)
                ring = ring[::-1]
                faces = faces[::-1][1:] + faces[::-1][:1]
            edges = []
            for f in faces:
                e: Dict[str, Any] = {"splineRef": -1, "neighbour": -1}
                attrs: Dict[str, Any] = {}
                if f is not None and f["offset"] <= ON_SPLINE:
                    e["splineRef"] = spline_of[f["road"]]
                    attrs["frontagePriority"] = 1 if ROAD_CLASS.get(f["class"]) == "main" else 0
                if d["id"] in open_d:
                    attrs["frontage"] = False
                if attrs:
                    e["attrs"] = attrs
                edges.append(e)
            attrs = {"districtType": DISTRICT_TYPE.get(d["kind"], "residential"), "mapgenKind": d["kind"],
                     "districtId": d["id"], "density": float(d["density"]), "depth": int(d["depth"]),
                     "settlement": st["id"]}
            if d["id"] in open_d:
                attrs["districtType"] = "plaza"
                if d["id"] in plaza_of:
                    attrs["plazaRadius"] = float(plaza_of[d["id"]]["r"])
                    attrs["landmarkId"] = plaza_of[d["id"]]["id"]
            if d["top_label"]:
                attrs["districtName"] = d["top_label"]
            cells.append({"id": len(cells), "outer": [_v3(p) for p in ring], "attrs": attrs, "edges": edges})
        _link_neighbours(cells[first:])
        for g in st["gates"]:
            nx, nz = g["normal"]
            yaw = math.degrees(math.atan2(nx, nz))
            attrs = {"landmarkType": g["kind"], "gateId": g["id"], "settlement": st["id"]}
            if g["wall"]:
                attrs["wallId"] = g["wall"]
            if g["road"]:
                attrs["roadId"] = g["road"]
            points.append({"id": len(points), "position": _v3((g["x"], g["z"])), "rotationY": _c(yaw), "attrs": attrs})
        for l in st["landmarks"]:
            ltype = "plaza" if is_plaza_landmark(l, st["generator"]) else LANDMARK_TYPE.get(l["kind"], l["kind"])
            attrs = {"landmarkType": ltype, "landmarkId": l["id"], "mapgenKind": l["kind"],
                     "radius": float(l["r"]), "settlement": st["id"]}
            if l["label"]:
                attrs["label"] = l["label"]
            points.append({"id": len(points), "position": _v3((l["x"], l["z"])), "attrs": attrs})
    seeds = ", ".join("%s seed %d" % (st["id"], st["seed"]) for st in sdoc["settlements"])
    return {"format": FORMAT, "version": VERSION,
            "meta": {"units": "metres", "frame": "generator-local", "boundaryConvention": "centreline",
                     "generator": "mapgen %s export-nlayout: map %s (%s)" % (__version__, sdoc["name"], seeds)},
            "cells": cells, "splines": splines, "points": points}


def _link_neighbours(cells: List[Dict[str, Any]]) -> None:
    """neighbour = the cell whose edge runs over exactly the same two vertices in the opposite direction."""
    at: Dict[Tuple, Tuple[int, int]] = {}
    for c in cells:
        o = c["outer"]
        for i in range(len(o)):
            at[(tuple(o[i]), tuple(o[(i + 1) % len(o)]))] = (c["id"], i)
    for c in cells:
        o = c["outer"]
        for i in range(len(o)):
            hit = at.get((tuple(o[(i + 1) % len(o)]), tuple(o[i])))
            if hit is not None and hit[0] != c["id"]:
                c["edges"][i]["neighbour"] = hit[0]


def dumps(doc: Dict[str, Any]) -> str:
    return json.dumps(doc, indent=1) + "\n"


def _bundle_dir(path: str) -> Optional[str]:
    for d in (path, os.path.join(path, "export")):
        if os.path.isfile(os.path.join(d, "settlements.json")):
            return d
    return None


def resolve(target: str, out_root: str = "out", settlement: Optional[str] = None) -> Tuple[Dict[str, Any], str]:
    """(settlements document, output path) for a spec file or a bundle directory (`out/<name>` or its `export/`)."""
    from . import export_settlements as es
    from . import schema
    if os.path.isdir(target):
        d = _bundle_dir(target)
        if d is None:
            raise ValueError("%s has no settlements.json (build a town map with a settlement file first)" % target)
        with open(os.path.join(d, "settlements.json"), "r", encoding="utf-8") as f:
            sdoc = json.load(f)
        if sdoc.get("format") != es.FORMAT or sdoc.get("format_version") != es.FORMAT_VERSION:
            raise ValueError("%s: unsupported settlements format %r version %r"
                             % (d, sdoc.get("format"), sdoc.get("format_version")))
        return sdoc, os.path.join(d, "%s.nlayout.json" % sdoc["name"])
    spec = schema.load_spec(target)
    build_dir = os.path.join(out_root, spec.name)
    path = settlement or es.find_settlement(spec, build_dir)
    if path is None:
        raise ValueError("no settlement file for %s (looked for %s.settlement.json and %s)"
                         % (target, os.path.splitext(target)[0], os.path.join(build_dir, "settlement.json")))
    s = es.load_settlement(path)
    bad = es.mismatches(spec, s)
    if bad:
        raise ValueError("%s does not match %s: %s" % (path, target, "; ".join(bad[:5])))
    return es.settlements_doc(spec, [s]), os.path.join(build_dir, "export", "%s.nlayout.json" % spec.name)


def cmd_export_nlayout(args) -> int:
    from . import schema
    try:
        sdoc, out = resolve(args.target, args.out, args.settlement)
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
    doc = nlayout_doc(sdoc)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(dumps(doc))
    front = sum(1 for c in doc["cells"] for e in c["edges"] if e["splineRef"] >= 0)
    total = sum(len(c["edges"]) for c in doc["cells"])
    print("wrote %s: %d cells (%d of %d edges on a road), %d splines, %d points"
          % (out, len(doc["cells"]), front, total, len(doc["splines"]), len(doc["points"])))
    return 0


def add_parser(sub) -> None:
    p = sub.add_parser("export-nlayout", help="write a town's settlement plan as a nodachi layout (<name>.nlayout.json)")
    p.add_argument("target", help="a town map spec, or a build/bundle directory holding export/settlements.json")
    p.add_argument("--out", default="out", help="build root for a spec target (default out): writes <out>/<name>/export/")
    p.add_argument("--settlement", help="settlement file for a spec target (default: found as mapgen build finds it)")
    p.set_defaults(func=cmd_export_nlayout)
