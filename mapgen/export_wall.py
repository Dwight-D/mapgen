"""`mapgen export-wall`: a town's walls and wall gates as a nodachi layout file (docs/wall.md).

Input is the settlements.json document (export_settlements.py); output is `<name>.wall.nlayout.json` in nodachi's layout
interchange format (`nodachi-layout` version 1): one linear spline per solid wall run (a ring wall with no open shore is
one closed spline at its full vertex count; a wall left open to water gives one open spline per solid run) and one point
per gate that sits in a wall. Gates without a wall (`entry` gates of a wall-less town) are not written. The file holds no
cells, so a graph reading it sees only the wall. Frame and rounding match `export-nlayout`.
"""
from __future__ import annotations

import math
import os
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from .export_nlayout import FORMAT, VERSION, _c, _v3, dumps, resolve


def short_gate_id(gid: str) -> str:
    """`cinder_gate_a4` -> `a4`: the part after the last `_gate_`; an id without that marker is kept whole."""
    k = gid.rfind("_gate_")
    return gid[k + len("_gate_"):] if k >= 0 else gid


def _runs(w: Dict[str, Any]) -> List[List[List[float]]]:
    from .town.water import wall_runs
    return wall_runs(SimpleNamespace(polyline=[list(p) for p in w["polyline"]],
                                     open_shore=[tuple(x) for x in w["open_shore"]]))


def wall_doc(sdoc: Dict[str, Any]) -> Dict[str, Any]:
    from . import __version__
    splines: List[Dict[str, Any]] = []
    points: List[Dict[str, Any]] = []
    for st in sdoc["settlements"]:
        road_width = {r["id"]: float(r["width"]) for r in st["roads"]}
        walls = {w["id"]: w for w in st["walls"]}
        for w in st["walls"]:
            runs = _runs(w)
            for k, run in enumerate(runs):
                closed = not w["open_shore"] and len(run) > 2 and list(run[0]) == list(run[-1])
                pts = run[:-1] if closed else run
                attrs = {"kind": "wall", "wallId": w["id"], "wallKind": w["kind"], "wallWidth": float(w["thickness"]),
                         "runIndex": k, "settlement": st["id"]}
                sp = {"id": len(splines), "points": [_v3(p) for p in pts], "interpolation": "linear", "attrs": attrs}
                if closed:
                    sp["closed"] = True
                splines.append(sp)
        for g in st["gates"]:
            if not g["wall"] or g["wall"] not in walls:
                continue
            nx, nz = g["normal"]
            attrs = {"gateId": short_gate_id(g["id"]), "gateRef": g["id"], "kind": g["kind"], "wallId": g["wall"],
                     "settlement": st["id"]}
            if g["road"]:
                attrs["roadId"] = g["road"]
                if g["road"] in road_width:
                    attrs["roadWidth"] = road_width[g["road"]]
                    attrs["width"] = road_width[g["road"]]
            points.append({"id": len(points), "position": _v3((g["x"], g["z"])),
                           "rotationY": _c(math.degrees(math.atan2(nx, nz))), "attrs": attrs})
    seeds = ", ".join("%s seed %d" % (st["id"], st["seed"]) for st in sdoc["settlements"])
    return {"format": FORMAT, "version": VERSION,
            "meta": {"units": "metres", "frame": "generator-local", "boundaryConvention": "centreline",
                     "generator": "mapgen %s export-wall: map %s (%s)" % (__version__, sdoc["name"], seeds)},
            "cells": [], "splines": splines, "points": points}


def wall_path(nlayout_path: str) -> str:
    d, base = os.path.split(nlayout_path)
    return os.path.join(d, base[:-len(".nlayout.json")] + ".wall.nlayout.json")


def cmd_export_wall(args) -> int:
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
    doc = wall_doc(sdoc)
    out = wall_path(out)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(dumps(doc))
    closed = sum(1 for s in doc["splines"] if s.get("closed"))
    verts = sum(len(s["points"]) for s in doc["splines"])
    print("wrote %s: %d wall splines (%d closed, %d vertices), %d gates"
          % (out, len(doc["splines"]), closed, verts, len(doc["points"])))
    return 0


def add_parser(sub) -> None:
    p = sub.add_parser("export-wall", help="write a town's walls and wall gates as a nodachi layout (<name>.wall.nlayout.json)")
    p.add_argument("target", help="a town map spec, or a build/bundle directory holding export/settlements.json")
    p.add_argument("--out", default="out", help="build root for a spec target (default out): writes <out>/<name>/export/")
    p.add_argument("--settlement", help="settlement file for a spec target (default: found as mapgen build finds it)")
    p.set_defaults(func=cmd_export_wall)

