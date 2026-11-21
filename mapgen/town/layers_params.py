"""`layers` parameter: parsing of the canonical dict and the CLI string, validation, echo (design: mapgen/TOWN_LAYERS_SPEC.md section 6)."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from . import fill_bsp, fill_follow, fill_grid, fill_radial, fill_voronoi, fill_wedge
from .params import ParamError

FILLS = {"grid": fill_grid, "radial": fill_radial, "voronoi": fill_voronoi, "bsp": fill_bsp, "wedge": fill_wedge, "follow": fill_follow}
USES = tuple(sorted(FILLS)) + ("none",)
SKIP_KINDS = ["core", "citadel", "harbour", "outskirt"]
RULE_KEYS = ("depth", "kind", "area", "key", "p", "use", "params", "align", "kinds", "skip_kinds", "align_jitter", "params_random", "parent_use")
GLOBAL_KEYS = ("max_depth", "min_area", "min_split_area", "boundary", "graph", "max_districts", "align")
_ALIGN = re.compile(r"^(edge|parent|none|random|angle:-?\d+(\.\d+)?)$")
DOC = {
    "layers": "subdivide districts with another layout: JSON/YAML dict, or `[d:]use[@k=v,...];...;max_depth=2` (a preset name: organic | organic-radial | organic-voronoi | organic-grid | organic-cells | organic-mixed; use = grid | radial | voronoi | bsp | wedge | follow | a/b*2 | none)",
}


ORGANIC_PRESET: Dict[str, Any] = {
    "max_depth": 3, "min_split_area": 3000, "align": "edge",
    "rules": [
        {"depth": 0, "kind": ["market"], "use": {"grid": 2, "bsp": 1}, "align_jitter": 10},
        {"depth": 0, "kind": ["residential", "craft", "slum"], "use": {"bsp": 3, "grid": 2, "follow": 2, "wedge": 2, "voronoi": 1},
         "params_random": True, "align_jitter": 25, "p": 0.97},
        {"depth": [1, 2], "area": [6500, 1000000], "use": {"bsp": 3, "wedge": 1, "follow": 2, "grid": 1}, "params_random": True,
         "align_jitter": 25},
        {"depth": 1, "p": 0.35, "use": {"bsp": 2, "voronoi": 1, "wedge": 1}},
    ],
}


_MACRO = ["market", "residential", "craft", "slum"]
_CAP = {"depth": [1, 2], "area": [6500, 1000000], "use": {"bsp": 2, "voronoi": 2, "grid": 2, "radial": 1, "wedge": 1}, "params_random": True,
        "align_jitter": 25}


def _p(rules: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"max_depth": 3, "min_split_area": 3000, "align": "edge", "rules": rules}


def _top(use: Dict[str, float], p: float = 1.0, **kw: Any) -> List[Dict[str, Any]]:
    """The depth-0 rule: every macro district draws `use`."""
    r = {"depth": 0, "kind": list(_MACRO), "use": use, "params_random": True, "align_jitter": 20, "p": p}
    r.update(kw)
    return [r]


def _child(parent: str, use: Dict[str, float], p: float = 0.85) -> List[Dict[str, Any]]:
    """Depth-1 rules for children of a `parent` fill: districts above 6500 m2 always split, smaller ones stay leaves with probability 1 - p."""
    small = dict(use)
    small["none"] = sum(use.values()) * (1.0 - p) / p
    return [{"depth": 1, "parent_use": [parent], "area": [6500, 1000000], "use": dict(use), "params_random": True, "align_jitter": 20},
            {"depth": 1, "parent_use": [parent], "use": small, "params_random": True, "align_jitter": 20}]


# Named presets (design: TOWN_ORGANIC_SPEC.md section 7.7). Depth 0 fills the macro districts, `parent_use` rules pick the next level
# by the fill that made the parent, the final area rule caps every district at about 6500 m2.
PRESETS: Dict[str, Dict[str, Any]] = {
    "organic": ORGANIC_PRESET,
    "organic-radial": _p(_top({"radial": 6, "wedge": 1, "voronoi": 1}) + [
        *_child("radial", {"bsp": 2, "grid": 3, "voronoi": 1, "follow": 1}, 0.9),
        *_child("wedge", {"grid": 2, "voronoi": 2, "bsp": 1}, 0.7),
        *_child("voronoi", {"grid": 2, "radial": 1, "bsp": 1}, 0.7), _CAP]),
    "organic-voronoi": _p(_top({"voronoi": 6, "wedge": 1, "radial": 1}) + [
        *_child("voronoi", {"grid": 3, "bsp": 1, "radial": 1, "wedge": 1}, 0.9),
        *_child("radial", {"voronoi": 2, "grid": 1, "bsp": 1}, 0.7),
        *_child("bsp", {"voronoi": 2, "grid": 1}, 0.7), _CAP]),
    "organic-grid": _p(_top({"grid": 6, "radial": 1, "voronoi": 1}) + [
        *_child("grid", {"radial": 3, "voronoi": 3, "wedge": 2, "bsp": 1}, 0.8),
        *_child("radial", {"voronoi": 2, "grid": 1, "bsp": 1}, 0.7),
        *_child("voronoi", {"grid": 2, "bsp": 1, "radial": 1}, 0.7), _CAP]),
    "organic-cells": _p(_top({"voronoi": 4, "grid": 1, "radial": 1}) + [
        *_child("voronoi", {"radial": 3, "bsp": 1, "wedge": 2, "grid": 1}, 0.85),
        *_child("grid", {"voronoi": 2, "radial": 2}, 0.8),
        *_child("radial", {"voronoi": 1, "bsp": 1}, 0.7),
        {"depth": 2, "parent_use": ["radial", "wedge"], "use": {"grid": 2, "bsp": 2}, "p": 0.5, "params_random": True},
        _CAP]),
    "organic-mixed": _p(_top({"radial": 3, "voronoi": 3, "grid": 2, "wedge": 2, "bsp": 1}) + [
        *_child("radial", {"bsp": 2, "grid": 2, "voronoi": 1, "wedge": 1}, 0.85),
        *_child("voronoi", {"grid": 3, "bsp": 1, "radial": 2, "wedge": 1}, 0.85),
        *_child("grid", {"radial": 3, "voronoi": 2, "wedge": 1}, 0.85),
        *_child("wedge", {"voronoi": 3, "grid": 2, "radial": 1}, 0.85),
        *_child("bsp", {"voronoi": 2, "radial": 2, "grid": 1}, 0.7),
        *_child("follow", {"voronoi": 2, "bsp": 1}, 0.7),
        {"depth": 2, "p": 0.4, "use": {"bsp": 2, "grid": 2, "voronoi": 1, "radial": 1}, "params_random": True}, _CAP]),
}


def preset(name: str) -> Optional[Dict[str, Any]]:
    """The canonical (unresolved) dict of a named preset, None when `name` is not one."""
    if isinstance(name, str) and name.strip().lower() in PRESETS:
        return json.loads(json.dumps(PRESETS[name.strip().lower()]))
    return None


def is_layer_param(name: str) -> bool:
    return name == "layers"


def _num(v: Any, what: str, errs: List[str], lo: float, hi: float, integer: bool = False) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        errs.append("layers.%s: expected a number, got %r" % (what, v))
        return None
    try:
        x = float(v)
    except ValueError:
        errs.append("layers.%s: expected a number, got %r" % (what, v))
        return None
    if integer and x != int(x):
        errs.append("layers.%s: expected an integer, got %r" % (what, v))
        return None
    if not (lo <= x <= hi):
        errs.append("layers.%s: %r outside [%g, %g]" % (what, v, lo, hi))
        return None
    return int(x) if integer else x


def _scalar(v: str) -> Any:
    try:
        return json.loads(v)
    except ValueError:
        return v


def _range(v: Any, what: str, errs: List[str], lo: float, hi: float, integer: bool) -> Optional[List[float]]:
    if isinstance(v, str):
        m = re.match(r"^(-?[\d.]+)\s*-\s*(-?[\d.]+)$", v.strip())
        v = [m.group(1), m.group(2)] if m else [v]
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        v = [v, v]
    if not isinstance(v, (list, tuple)) or len(v) != 2:
        errs.append("layers.%s: expected a number or [lo, hi], got %r" % (what, v))
        return None
    a, b = _num(v[0], what, errs, lo, hi, integer), _num(v[1], what, errs, lo, hi, integer)
    if a is None or b is None:
        return None
    if a > b:
        errs.append("layers.%s: lo %r above hi %r" % (what, v[0], v[1]))
        return None
    return [a, b]


def _list(v: Any) -> List[str]:
    if isinstance(v, str):
        return [x for x in re.split(r"[/,]", v) if x]
    return list(v)


def _weights(v: Any, what: str, errs: List[str], allowed) -> Optional[Dict[str, float]]:
    if isinstance(v, str):
        d: Dict[str, Any] = {}
        for tok in v.split("/"):
            name, _, w = tok.partition("*")
            d[name.strip()] = float(w) if w else 1.0
        v = d
    if not isinstance(v, dict) or not v:
        errs.append("layers.%s: expected a name or a {name: weight} mapping, got %r" % (what, v))
        return None
    out: Dict[str, float] = {}
    for k in sorted(v):
        if allowed is not None and k not in allowed:
            errs.append("layers.%s: unknown name %r (known: %s)" % (what, k, ", ".join(sorted(allowed))))
            continue
        w = v[k]
        if isinstance(w, bool) or not isinstance(w, (int, float)) or w <= 0:
            errs.append("layers.%s.%s: weight must be a number above 0, got %r" % (what, k, w))
            continue
        out[k] = float(w)
    return out


def parse_string(s: str) -> Dict[str, Any]:
    """CLI string form (section 6.2) -> canonical dict (unvalidated)."""
    s = s.strip()
    if s[:1] in "{[":
        return json.loads(s)
    out: Dict[str, Any] = {}
    rules: List[Dict[str, Any]] = []
    for item in [x.strip() for x in s.split(";") if x.strip()]:
        m = re.match(r"^(\w+)=(.*)$", item)
        if m and m.group(1) in GLOBAL_KEYS and "@" not in m.group(1):
            out[m.group(1)] = _scalar(m.group(2))
            continue
        head, _, opts = item.partition("@")
        rule: Dict[str, Any] = {}
        dm = re.match(r"^(\d+(?:-\d+)?):(.*)$", head)
        if dm:
            lo, _, hi = dm.group(1).partition("-")
            rule["depth"] = [int(lo), int(hi or lo)]
            head = dm.group(2)
        rule["use"] = head.strip()
        sub: Dict[str, Any] = {}
        for kv in [x for x in opts.split(",") if x]:
            k, _, v = kv.partition("=")
            k = k.strip()
            if k.startswith("sub."):
                sub[k[4:]] = _scalar(v)
            else:
                rule[k] = v.strip() if k in ("kind", "area", "key", "kinds", "align", "skip_kinds", "parent_use") else _scalar(v)
        if sub:
            rule["params"] = sub
        rules.append(rule)
    out["rules"] = rules
    return out


def resolve(raw: Any, min_district_area: float) -> Optional[Dict[str, Any]]:
    """Canonical resolved dict (every default filled), None for no layers. Raises ParamError listing every problem."""
    if raw is None or raw is False or (isinstance(raw, str) and raw.strip().lower() in ("", "none", "false")):
        return None
    errs: List[str] = []
    if isinstance(raw, str) and preset(raw) is not None:
        raw = preset(raw)
    if isinstance(raw, str):
        try:
            raw = parse_string(raw)
        except (ValueError, TypeError) as e:
            raise ParamError(["layers: cannot parse %r (%s)" % (raw, e)])
    if not isinstance(raw, dict):
        raise ParamError(["layers: expected a mapping or a string, got %r" % (raw,)])
    raw = dict(raw)
    raw.pop("skipped", None)
    for k in sorted(raw):
        if k not in GLOBAL_KEYS and k != "rules":
            errs.append("layers: unknown key %r (known: %s, rules)" % (k, ", ".join(GLOBAL_KEYS)))
    md = _num(raw.get("max_depth", 1), "max_depth", errs, 1, 3, True) or 1
    min_area = _num(raw.get("min_area", max(400.0, float(min_district_area))), "min_area", errs, 100, 100000) or 1600.0
    min_split = _num(raw.get("min_split_area", max(3200.0, 4 * min_area)), "min_split_area", errs, 100, 1000000) or 6400.0
    boundary = raw.get("boundary", "streets")
    if boundary not in ("streets", "roads_only"):
        errs.append("layers.boundary: expected streets or roads_only, got %r" % (boundary,))
    graph = raw.get("graph", "major")
    if graph not in ("major", "all"):
        errs.append("layers.graph: expected major or all, got %r" % (graph,))
    maxd = _num(raw.get("max_districts", 400), "max_districts", errs, 20, 2000, True) or 400
    align = raw.get("align", "edge")
    if not isinstance(align, str) or not _ALIGN.match(align):
        errs.append("layers.align: expected edge, parent, none, random or angle:<deg>, got %r" % (align,))
    rules_in = raw.get("rules")
    if not isinstance(rules_in, list) or not rules_in:
        errs.append("layers.rules: expected a non-empty list")
        rules_in = []
    rules: List[Dict[str, Any]] = []
    for i, r in enumerate(rules_in):
        w = "rules[%d]" % i
        if not isinstance(r, dict):
            errs.append("layers.%s: expected a mapping, got %r" % (w, r))
            continue
        for k in sorted(r):
            if k not in RULE_KEYS:
                errs.append("layers.%s: unknown key %r (known: %s)" % (w, k, ", ".join(RULE_KEYS)))
        use = _weights(r.get("use", "none"), w + ".use", errs, USES)
        dep = _range(r.get("depth", [0, md - 1]), w + ".depth", errs, 0, 2, True)
        area = r.get("area")
        if area is not None:
            area = _range(area, w + ".area", errs, 0, 1e9, False)
        p = _num(r.get("p", 1.0), w + ".p", errs, 0, 1)
        ra = r.get("align", align)
        if not isinstance(ra, str) or not _ALIGN.match(ra):
            errs.append("layers.%s.align: expected edge, parent, none, random or angle:<deg>, got %r" % (w, ra))
        kinds = r.get("kinds")
        if kinds is not None:
            kinds = _weights(kinds, w + ".kinds", errs, None)
        rp = r.get("params", {})
        if not isinstance(rp, dict):
            errs.append("layers.%s.params: expected a mapping" % w)
            rp = {}
        if use:
            for name in sorted(use):
                if name == "none":
                    continue
                known = {d.name for d in FILLS[name].SUB_DEFS}
                for k in sorted(rp):
                    if k not in known:
                        errs.append("layers.%s.params.%s: unknown for %s (known: %s)" % (w, k, name, ", ".join(sorted(known))))
        pu = r.get("parent_use")
        if pu is not None:
            pu = _list(pu)
            for u in pu:
                if u not in FILLS:
                    errs.append("layers.%s.parent_use: unknown fill %r (known: %s)" % (w, u, ", ".join(sorted(FILLS))))
        pr = r.get("params_random", False)
        if not isinstance(pr, bool):
            errs.append("layers.%s.params_random: expected true or false, got %r" % (w, pr))
            pr = False
        aj = _num(r.get("align_jitter", 0.0), w + ".align_jitter", errs, 0, 45)
        rules.append({
            "depth": dep or [0, 0], "kind": _list(r["kind"]) if r.get("kind") is not None else None, "area": area,
            "key": r.get("key"), "p": p if p is not None else 1.0, "use": use or {}, "params": rp, "align": ra,
            "kinds": kinds, "skip_kinds": _list(r["skip_kinds"]) if r.get("skip_kinds") is not None else list(SKIP_KINDS),
            "align_jitter": aj or 0.0, "params_random": pr, "parent_use": sorted(pu) if pu else None,
        })
    if errs:
        raise ParamError(errs)
    return {"max_depth": md, "min_area": min_area, "min_split_area": min_split, "boundary": boundary, "graph": graph,
            "max_districts": maxd, "align": align, "rules": rules, "skipped": {}}
