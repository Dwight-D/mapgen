"""Load a map spec (YAML), apply defaults, validate, and return a MapSpec dataclass tree."""
from __future__ import annotations

import hashlib
import math
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import yaml

Point = Tuple[float, float]

TYPES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "types.yaml")
PALETTE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "palette.yaml")
BOUNDS_TOLERANCE = 1.0  # metres a point may lie outside the map rectangle


class SpecError(Exception):
    """Raised with every validation error found; each error is a string starting with its path."""

    def __init__(self, errors: List[str]):
        super().__init__("\n".join(errors))
        self.errors = list(errors)


# ----------------------------------------------------------------------------- type table


@dataclass
class TypeTable:
    regions: Dict[str, Dict[str, Any]]
    lines: Dict[str, Dict[str, Any]]
    crossings: Dict[str, Dict[str, Any]]
    pois: Dict[str, Dict[str, Any]]
    splat_layers: List[str]


_TYPES_CACHE: Dict[str, TypeTable] = {}


def _parse_cost(v: Any) -> float:
    if isinstance(v, str):
        s = v.strip().lower().lstrip("+")
        if s in ("inf", ".inf", "infinity"):
            return float("inf")
        return float(s)
    return float(v)


def load_types(path: Optional[str] = None) -> TypeTable:
    """Load and validate types.yaml (cached). Raises SpecError on a malformed table."""
    path = path or TYPES_PATH
    if path in _TYPES_CACHE:
        return _TYPES_CACHE[path]
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    errors: List[str] = []
    for sect in ("regions", "lines", "crossings", "pois"):
        if not isinstance(raw.get(sect), dict):
            errors.append("types.%s: missing or not a mapping" % sect)
    layers = raw.get("splat_layers")
    if not isinstance(layers, list) or not layers:
        errors.append("types.splat_layers: missing or empty")
        layers = []
    if errors:
        raise SpecError(errors)
    for sect in ("regions", "lines", "crossings"):
        for name, entry in raw[sect].items():
            if "cost" in entry:
                try:
                    entry["cost"] = _parse_cost(entry["cost"])
                except (TypeError, ValueError):
                    errors.append("types.%s.%s.cost: not a number" % (sect, name))
            if sect != "crossings" and entry.get("splat") not in layers:
                errors.append("types.%s.%s.splat: '%s' not in splat_layers" % (sect, name, entry.get("splat")))
    if errors:
        raise SpecError(errors)
    table = TypeTable(
        regions=raw["regions"],
        lines=raw["lines"],
        crossings=raw["crossings"],
        pois=raw["pois"],
        splat_layers=[str(x) for x in layers],
    )
    _TYPES_CACHE[path] = table
    return table


# ----------------------------------------------------------------------------- palette

PATTERN_NAMES = ("none", "stipple", "hatch", "crosshatch", "reeds", "dots", "grid", "hlines")


@dataclass
class Palette:
    base: str
    regions: Dict[str, str]
    lines: Dict[str, str]
    crossings: Dict[str, str]
    patterns: Dict[str, str]          # region type -> pattern name (missing = none)
    teams: List[str]
    overlay: Dict[str, Any]
    outline_darken: float


_PALETTE_CACHE: Dict[str, Palette] = {}


def _merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_palette(path: Optional[str] = None) -> Palette:
    """Load a palette file merged over the default palette (keys it omits fall back to the default).
    Raises SpecError on a malformed palette or when a type of types.yaml has no colour."""
    path = os.path.abspath(path) if path else PALETTE_PATH
    if path in _PALETTE_CACHE:
        return _PALETTE_CACHE[path]
    with open(PALETTE_PATH, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if path != PALETTE_PATH:
        with open(path, "r", encoding="utf-8") as f:
            extra = yaml.safe_load(f) or {}
        if not isinstance(extra, dict):
            raise SpecError(["palette: expected a mapping"])
        raw = _merge(raw, extra)
    errors: List[str] = []

    def hexc(v: Any, where: str) -> str:
        if not isinstance(v, str) or not _HEX.match(v):
            errors.append("palette.%s: expected a hex colour like \"#3b6fd4\" (got %r)" % (where, v))
            return "#ff00ff"
        return v

    types = load_types()
    sections = {"regions": types.regions, "lines": types.lines, "crossings": types.crossings}
    cols: Dict[str, Dict[str, str]] = {}
    for sect, table in sections.items():
        have = raw.get(sect) or {}
        cols[sect] = {}
        for name in table:
            if name not in have:
                errors.append("palette.%s.%s: missing colour" % (sect, name))
            else:
                cols[sect][name] = hexc(have[name], "%s.%s" % (sect, name))
    pats = {}
    for name, pat in (raw.get("patterns") or {}).items():
        if name not in types.regions:
            errors.append("palette.patterns.%s: unknown region type" % name)
        elif pat not in PATTERN_NAMES:
            errors.append("palette.patterns.%s: unknown pattern %r (known: %s)" % (name, pat, ", ".join(PATTERN_NAMES)))
        else:
            pats[name] = pat
    teams = raw.get("teams")
    if not isinstance(teams, list) or len(teams) < 3:
        errors.append("palette.teams: expected a list of at least 3 colours")
        teams = []
    ov = raw.get("overlay") or {}
    overlay: Dict[str, Any] = {}
    for key in ("contested", "unreachable", "cover", "fair"):
        overlay[key] = hexc(ov.get(key), "overlay." + key)
    ramp = ov.get("traffic")
    if not isinstance(ramp, list) or len(ramp) < 2:
        errors.append("palette.overlay.traffic: expected a list of at least 2 colours")
        ramp = []
    overlay["traffic"] = [hexc(c, "overlay.traffic[%d]" % i) for i, c in enumerate(ramp)]
    od = raw.get("outline_darken", 0.45)
    if not _V.is_num(od) or not 0 <= od <= 1:
        errors.append("palette.outline_darken: expected a number in [0, 1]")
        od = 0.45
    pal = Palette(base=hexc(raw.get("base"), "base"), regions=cols["regions"], lines=cols["lines"],
                  crossings=cols["crossings"], patterns=pats,
                  teams=[hexc(c, "teams[%d]" % i) for i, c in enumerate(teams)], overlay=overlay,
                  outline_darken=float(od))
    if errors:
        raise SpecError(errors)
    _PALETTE_CACHE[path] = pal
    return pal


# ----------------------------------------------------------------------------- spec dataclasses


@dataclass
class Terrain:
    height_max: float = 400.0
    base_height: float = 40.0
    relief: float = 25.0
    relief_scale: float = 1200.0
    water_depth: float = 4.0
    detail: float = 6.0
    detail_scale: float = 250.0


@dataclass
class Team:
    id: str
    base: Point
    color: str
    name: str


@dataclass
class Region:
    index: int
    type: str
    shape: str
    id: Optional[str] = None
    label: Optional[str] = None
    height: Optional[float] = None  # resolved: explicit or types.yaml default (None if the type has none)
    at: Optional[Point] = None
    r: float = 0.0
    rough: float = 0.0
    squash: float = 1.0
    angle: float = 0.0
    points: Optional[List[Point]] = None
    path: Optional[List[Point]] = None
    width: float = 0.0
    warp: Optional[float] = None         # None = shape default (resolved in geometry)
    warp_scale: Optional[float] = None
    fragment: float = 0.0
    fragment_scale: float = 150.0
    seed_key: int = -1                   # noise seed offset: index in the spec's `regions` list
    scatter: Optional["Scatter"] = None  # set on regions generated from a `scatter` entry
    label_at: Optional[Point] = None     # pinned label position
    edge: Optional[float] = None         # flatten/raise_flat blend radius, metres (None for other types)


@dataclass
class Line:
    index: int
    type: str
    path: List[Point]
    width: float
    meander: float = 0.0
    id: Optional[str] = None
    label: Optional[str] = None
    width_var: float = 0.0
    meander_scale: float = 300.0
    join: Optional[str] = None           # id of the line this one flows into
    join_end: str = "end"                # which end of this line is moved onto the parent: start | end
    label_at: Optional[Point] = None


@dataclass
class Scatter:
    index: int
    type: str
    coverage: float
    scale: float
    clump: float = 0.3
    avoid: float = 25.0
    within: Optional[List[Point]] = None
    on: List[str] = field(default_factory=list)   # explicit region types the scatter may also appear on


@dataclass
class Crossing:
    index: int
    type: str
    at: Point                            # disc centre; for a band, the middle of its path
    r: float                             # disc radius; for a band, width / 2
    id: Optional[str] = None
    path: Optional[List[Point]] = None   # band crossing: centreline (the crossing is the buffered, smoothed path)
    width: float = 0.0                   # band width, metres
    auto: bool = False                   # generated by `auto_crossings`
    note: Optional[str] = None           # what an auto crossing crosses


@dataclass
class Poi:
    id: str
    type: str
    at: Point
    r: float
    label: Optional[str] = None
    team: Optional[str] = None
    tier: Optional[int] = None


@dataclass
class Analysis:
    cell: float = 10.0
    speed: float = 5.0
    balance_tolerance: float = 0.15


@dataclass
class Output:
    render_px: int = 1536
    heightmap_res: int = 1025
    splat_res: int = 1024
    grid_step: float = 500.0


@dataclass
class MapSpec:
    name: str
    brief: str
    size: Tuple[float, float]
    seed: int
    terrain: Terrain
    teams: Dict[str, Team]  # insertion-ordered
    regions: List[Region]
    lines: List[Line]
    crossings: List[Crossing]
    pois: List[Poi]  # explicit POIs only
    analysis: Analysis
    output: Output
    types: TypeTable
    palette: Palette
    scatter: List[Scatter] = field(default_factory=list)
    spec_path: str = ""
    spec_sha256: str = ""
    scatter_applied: bool = False  # set by geometry.build_geometries once scatter regions are in `regions`
    auto_crossings: bool = True
    auto_applied: bool = False     # set by geometry.build_geometries once auto crossings are in `crossings`
    warnings: List[str] = field(default_factory=list)   # spec/geometry warnings, merged into the report

    def all_pois(self) -> List[Poi]:
        """Team bases (id `base_<team>`, type `base`) followed by the explicit POIs."""
        bases = [
            Poi(id="base_" + t.id, type="base", at=t.base, r=self.types.pois["base"]["r"], label=t.name, team=t.id)
            for t in self.teams.values()
        ]
        return bases + list(self.pois)


# ----------------------------------------------------------------------------- validation helpers


class _V:
    def __init__(self) -> None:
        self.errors: List[str] = []

    def err(self, path: str, msg: str) -> None:
        self.errors.append("%s: %s" % (path, msg))

    def keys(self, d: Any, path: str, allowed: List[str], required: List[str]) -> bool:
        if not isinstance(d, dict):
            self.err(path, "expected a mapping, got %s" % type(d).__name__)
            return False
        for k in d:
            if k not in allowed:
                self.err("%s.%s" % (path, k) if path else str(k), "unknown key (allowed: %s)" % ", ".join(allowed))
        for k in required:
            if k not in d:
                self.err("%s.%s" % (path, k) if path else k, "missing required key")
        return True

    @staticmethod
    def is_num(v: Any) -> bool:
        return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)

    def num(self, d: Dict[str, Any], key: str, path: str, default: float, lo: Optional[float] = None,
            hi: Optional[float] = None, positive: bool = False, nonneg: bool = False) -> float:
        if key not in d:
            return default
        v = d[key]
        p = "%s.%s" % (path, key)
        if not self.is_num(v):
            self.err(p, "expected a number, got %r" % (v,))
            return default
        if positive and v <= 0:
            self.err(p, "must be > 0 (got %s)" % v)
        elif nonneg and v < 0:
            self.err(p, "must be >= 0 (got %s)" % v)
        elif lo is not None and v < lo or hi is not None and v > hi:
            self.err(p, "must be in [%s, %s] (got %s)" % (lo, hi, v))
        return float(v)

    def integer(self, d: Dict[str, Any], key: str, path: str, default: int, minimum: int = 1) -> int:
        if key not in d:
            return default
        v = d[key]
        p = "%s.%s" % (path, key)
        if isinstance(v, bool) or not isinstance(v, int):
            self.err(p, "expected an integer, got %r" % (v,))
            return default
        if v < minimum:
            self.err(p, "must be >= %d (got %d)" % (minimum, v))
        return v

    def text(self, d: Dict[str, Any], key: str, path: str) -> Optional[str]:
        if key not in d or d[key] is None:
            return None
        v = d[key]
        if not isinstance(v, (str, int, float)) or isinstance(v, bool):
            self.err("%s.%s" % (path, key), "expected text, got %r" % (v,))
            return None
        return str(v)

    def point(self, v: Any, path: str, size: Tuple[float, float]) -> Optional[Point]:
        if not isinstance(v, (list, tuple)) or len(v) != 2 or not all(self.is_num(c) for c in v):
            self.err(path, "expected a point [x, y] of two numbers, got %r" % (v,))
            return None
        x, y = float(v[0]), float(v[1])
        if x < -BOUNDS_TOLERANCE or x > size[0] + BOUNDS_TOLERANCE or y < -BOUNDS_TOLERANCE or y > size[1] + BOUNDS_TOLERANCE:
            self.err(path, "point [%s, %s] is outside the map [0..%s, 0..%s]" % (v[0], v[1], size[0], size[1]))
            return None
        return (x, y)

    def points(self, v: Any, path: str, size: Tuple[float, float], minimum: int, what: str) -> Optional[List[Point]]:
        if not isinstance(v, (list, tuple)):
            self.err(path, "expected a list of points, got %r" % (v,))
            return None
        if len(v) < minimum:
            self.err(path, "%s needs at least %d points (got %d)" % (what, minimum, len(v)))
        out: List[Point] = []
        ok = True
        for i, item in enumerate(v):
            pt = self.point(item, "%s[%d]" % (path, i), size)
            if pt is None:
                ok = False
            else:
                out.append(pt)
        return out if ok and len(v) >= minimum else None

    def type_name(self, d: Dict[str, Any], path: str, table: Dict[str, Dict[str, Any]], kind: str) -> Optional[str]:
        t = d.get("type")
        if t is None:
            return None  # missing-key error already reported by keys()
        if not isinstance(t, str) or t not in table:
            self.err("%s.type" % path, "unknown %s type %r (known: %s)" % (kind, t, ", ".join(table)))
            return None
        return t


_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_NAME = re.compile(r"^[a-z0-9_]+$")

_REGION_COMMON = ["type", "shape", "label", "label_at", "id", "height", "edge", "warp", "warp_scale", "fragment",
                  "fragment_scale"]
_REGION_SHAPE_KEYS = {
    "blob": (["at", "r", "rough", "squash", "angle"], ["at", "r"]),
    "poly": (["points", "rough"], ["points"]),
    "band": (["path", "width", "rough"], ["path", "width"]),
}


def _path_middle(path: List[Point]) -> Point:
    """Point halfway along a polyline (by length)."""
    seg = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(path[:-1], path[1:])]
    half = 0.5 * sum(seg)
    acc = 0.0
    for (a, b), d in zip(zip(path[:-1], path[1:]), seg):
        if acc + d >= half and d > 0:
            t = (half - acc) / d
            return (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
        acc += d
    return path[0]


def _check_joins(lines: List[Line], v: "_V") -> None:
    """`join` must name another line (by id) and the join graph must have no cycle."""
    by_id: Dict[str, List[Line]] = {}
    for ln in lines:
        if ln.id is not None:
            by_id.setdefault(ln.id, []).append(ln)
    parent: Dict[int, int] = {}
    for ln in lines:
        if ln.join is None:
            continue
        p = "lines[%d].join" % ln.index
        cands = by_id.get(ln.join, [])
        if not cands:
            v.err(p, "no line with id '%s'" % ln.join)
        elif len(cands) > 1:
            v.err(p, "line id '%s' is not unique" % ln.join)
        elif cands[0] is ln:
            v.err(p, "a line cannot join itself")
        else:
            parent[ln.index] = cands[0].index
    for start in parent:
        seen = {start}
        cur = parent[start]
        while cur in parent:
            if cur in seen:
                v.err("lines[%d].join" % start, "join cycle through lines[%d]" % cur)
                break
            seen.add(cur)
            cur = parent[cur]


def _validate(raw: Any, types: TypeTable, palette: Palette) -> Tuple[Optional[MapSpec], List[str]]:
    v = _V()
    top = ["name", "brief", "size", "seed", "terrain", "teams", "regions", "lines", "crossings", "pois",
           "scatter", "analysis", "output", "auto_crossings"]
    if not v.keys(raw, "", top, ["name", "size", "teams"]):
        return None, v.errors

    name = raw.get("name")
    if "name" in raw and (not isinstance(name, str) or not _NAME.match(name)):
        v.err("name", "must match [a-z0-9_]+ (got %r)" % (name,))
        name = "invalid"
    brief = raw.get("brief")
    brief = "" if brief is None else str(brief)
    auto_crossings = True
    if "auto_crossings" in raw:
        if not isinstance(raw["auto_crossings"], bool):
            v.err("auto_crossings", "expected true or false, got %r" % (raw["auto_crossings"],))
        else:
            auto_crossings = raw["auto_crossings"]

    size = (3000.0, 3000.0)
    sz = raw.get("size")
    if "size" in raw:
        if not isinstance(sz, (list, tuple)) or len(sz) != 2 or not all(v.is_num(c) for c in sz):
            v.err("size", "expected [x, y] of two numbers, got %r" % (sz,))
        elif not all(200 <= c <= 20000 for c in sz):
            v.err("size", "each dimension must be 200..20000 metres (got %r)" % (sz,))
        else:
            size = (float(sz[0]), float(sz[1]))

    seed = 0
    if "seed" in raw:
        if isinstance(raw["seed"], bool) or not isinstance(raw["seed"], int):
            v.err("seed", "expected an integer, got %r" % (raw["seed"],))
        else:
            seed = raw["seed"]

    # terrain
    terrain = Terrain()
    t = raw.get("terrain")
    if t is not None and v.keys(t, "terrain", ["height_max", "base_height", "relief", "relief_scale", "water_depth", "detail",
                                           "detail_scale"], []):
        terrain = Terrain(
            height_max=v.num(t, "height_max", "terrain", 400.0, positive=True),
            base_height=v.num(t, "base_height", "terrain", 40.0),
            relief=v.num(t, "relief", "terrain", 25.0, nonneg=True),
            relief_scale=v.num(t, "relief_scale", "terrain", 1200.0, positive=True),
            water_depth=v.num(t, "water_depth", "terrain", 4.0, nonneg=True),
            detail=v.num(t, "detail", "terrain", 6.0, nonneg=True),
            detail_scale=v.num(t, "detail_scale", "terrain", 250.0, positive=True),
        )

    # teams
    teams: Dict[str, Team] = {}
    traw = raw.get("teams")
    if "teams" in raw:
        if not isinstance(traw, dict):
            v.err("teams", "expected a mapping of team id -> team")
        else:
            if len(traw) not in (2, 3):
                v.err("teams", "needs 2 or 3 teams (got %d)" % len(traw))
            for i, (tid, tv) in enumerate(traw.items()):
                tid = str(tid)
                p = "teams.%s" % tid
                if not v.keys(tv, p, ["base", "color", "name"], ["base"]):
                    continue
                base = v.point(tv["base"], p + ".base", size) if "base" in tv else None
                color = palette.teams[i % len(palette.teams)]
                if "color" in tv:
                    if isinstance(tv["color"], str) and _HEX.match(tv["color"]):
                        color = tv["color"]
                    else:
                        v.err(p + ".color", "expected a hex colour like \"#3b6fd4\" (got %r)" % (tv["color"],))
                tname = v.text(tv, "name", p) or tid
                if base is not None:
                    teams[tid] = Team(id=tid, base=base, color=color, name=tname)

    # regions
    regions: List[Region] = []
    rraw = raw.get("regions")
    if rraw is not None:
        if not isinstance(rraw, list):
            v.err("regions", "expected a list")
            rraw = []
        for i, r in enumerate(rraw):
            p = "regions[%d]" % i
            shape = r.get("shape") if isinstance(r, dict) else None
            if isinstance(r, dict) and shape in _REGION_SHAPE_KEYS:
                extra, req = _REGION_SHAPE_KEYS[shape]
                v.keys(r, p, _REGION_COMMON + extra, ["type", "shape"] + req)
            else:
                v.keys(r, p, _REGION_COMMON + sorted({k for s in _REGION_SHAPE_KEYS.values() for k in s[0]}), ["type", "shape"])
                if isinstance(r, dict):
                    if "shape" in r:
                        v.err(p + ".shape", "unknown shape %r (known: blob, poly, band)" % (shape,))
                continue
            rtype = v.type_name(r, p, types.regions, "region")
            reg = Region(index=i, type=rtype or "", shape=shape, seed_key=i)
            if "warp" in r:
                reg.warp = v.num(r, "warp", p, 0.0, nonneg=True)
            if "warp_scale" in r:
                reg.warp_scale = v.num(r, "warp_scale", p, 1.0, positive=True)
            reg.fragment = v.num(r, "fragment", p, 0.0, lo=0.0, hi=1.0)
            reg.fragment_scale = v.num(r, "fragment_scale", p, 150.0, positive=True)
            reg.id = v.text(r, "id", p)
            reg.label = v.text(r, "label", p)
            if "label_at" in r:
                reg.label_at = v.point(r["label_at"], p + ".label_at", size)
            if rtype is not None and types.regions[rtype].get("height_mode") in ("raise_flat", "flatten"):
                reg.edge = v.num(r, "edge", p, float(types.regions[rtype].get("edge", 30.0)), positive=True)
            elif "edge" in r:
                v.err(p + ".edge", "region type '%s' does not take an edge (only raise_flat and flatten types do)" % rtype)
            if "height" in r:
                h = v.num(r, "height", p, 0.0, nonneg=True)
                if rtype is not None and "height" not in types.regions[rtype]:
                    v.err(p + ".height", "region type '%s' does not take a height" % rtype)
                reg.height = h
            elif rtype is not None:
                reg.height = types.regions[rtype].get("height")
            if shape == "blob":
                reg.at = v.point(r["at"], p + ".at", size) if "at" in r else None
                reg.r = v.num(r, "r", p, 0.0, positive=True)
                reg.rough = v.num(r, "rough", p, 0.3, lo=0.0, hi=1.0)
                reg.squash = v.num(r, "squash", p, 1.0, positive=True)
                reg.angle = v.num(r, "angle", p, 0.0)
            elif shape == "poly":
                reg.points = v.points(r["points"], p + ".points", size, 3, "polygon") if "points" in r else None
                reg.rough = v.num(r, "rough", p, 0.0, lo=0.0, hi=1.0)
            else:
                reg.path = v.points(r["path"], p + ".path", size, 2, "path") if "path" in r else None
                reg.width = v.num(r, "width", p, 0.0, positive=True)
                reg.rough = v.num(r, "rough", p, 0.35 if rtype == "mountain" else 0.2, lo=0.0, hi=1.0)
            regions.append(reg)

    # lines
    lines: List[Line] = []
    lraw = raw.get("lines")
    if lraw is not None:
        if not isinstance(lraw, list):
            v.err("lines", "expected a list")
            lraw = []
        for i, ln in enumerate(lraw):
            p = "lines[%d]" % i
            if not v.keys(ln, p, ["type", "path", "width", "meander", "meander_scale", "width_var", "id", "label",
                                  "label_at", "join", "join_end"], ["type", "path"]):
                continue
            ltype = v.type_name(ln, p, types.lines, "line")
            path = v.points(ln["path"], p + ".path", size, 2, "path") if "path" in ln else None
            default_w = float(types.lines[ltype]["width"]) if ltype else 10.0
            width = v.num(ln, "width", p, default_w, positive=True)
            meander = v.num(ln, "meander", p, 0.0, nonneg=True)
            width_var = v.num(ln, "width_var", p, 0.0, lo=0.0, hi=1.0)
            meander_scale = v.num(ln, "meander_scale", p, 300.0, positive=True)
            join = v.text(ln, "join", p)
            join_end = ln.get("join_end", "end")
            if join_end not in ("start", "end"):
                v.err(p + ".join_end", "must be 'start' or 'end' (got %r)" % (join_end,))
                join_end = "end"
            if "join_end" in ln and "join" not in ln:
                v.err(p + ".join_end", "needs `join`")
            label_at = v.point(ln["label_at"], p + ".label_at", size) if "label_at" in ln else None
            if ltype is not None and path is not None:
                lines.append(Line(index=i, type=ltype, path=path, width=width, meander=meander,
                                  width_var=width_var, id=v.text(ln, "id", p), label=v.text(ln, "label", p),
                                  meander_scale=meander_scale, join=join, join_end=join_end, label_at=label_at))
    _check_joins(lines, v)

    # crossings
    crossings: List[Crossing] = []
    craw = raw.get("crossings")
    if craw is not None:
        if not isinstance(craw, list):
            v.err("crossings", "expected a list")
            craw = []
        for i, c in enumerate(craw):
            p = "crossings[%d]" % i
            if not v.keys(c, p, ["type", "at", "r", "path", "width", "id"], ["type"]):
                continue
            ctype = v.type_name(c, p, types.crossings, "crossing")
            default_r = float(types.crossings[ctype]["r"]) if ctype else 25.0
            if "path" in c:
                for bad in ("at", "r"):
                    if bad in c:
                        v.err("%s.%s" % (p, bad), "a band crossing (path + width) takes no `%s`" % bad)
                if "width" not in c:
                    v.err(p + ".width", "missing required key (band crossing with `path`)")
                cpath = v.points(c["path"], p + ".path", size, 2, "path")
                cwidth = v.num(c, "width", p, 2.0 * default_r, positive=True)
                if ctype is not None and cpath is not None:
                    crossings.append(Crossing(index=i, type=ctype, at=_path_middle(cpath), r=cwidth / 2.0,
                                              id=v.text(c, "id", p), path=cpath, width=cwidth))
                continue
            if "width" in c:
                v.err(p + ".width", "`width` needs `path` (a disc crossing takes `at` and `r`)")
            if "at" not in c:
                v.err(p + ".at", "missing required key (give `at` for a disc or `path` + `width` for a band)")
            at = v.point(c["at"], p + ".at", size) if "at" in c else None
            r = v.num(c, "r", p, default_r, positive=True)
            if ctype is not None and at is not None:
                crossings.append(Crossing(index=i, type=ctype, at=at, r=r, id=v.text(c, "id", p)))

    # pois
    pois: List[Poi] = []
    praw = raw.get("pois")
    seen_ids: Dict[str, int] = {}
    if praw is not None:
        if not isinstance(praw, list):
            v.err("pois", "expected a list")
            praw = []
        for i, pv in enumerate(praw):
            p = "pois[%d]" % i
            if not v.keys(pv, p, ["id", "type", "at", "label", "team", "tier", "r"], ["id", "type", "at"]):
                continue
            pid = v.text(pv, "id", p)
            if pid is not None:
                if pid in seen_ids:
                    v.err(p + ".id", "duplicate POI id '%s' (first used at pois[%d])" % (pid, seen_ids[pid]))
                else:
                    seen_ids[pid] = i
                if pid.startswith("base_") and pid[5:] in teams:
                    v.err(p + ".id", "'%s' is reserved for the implicit base of team %s" % (pid, pid[5:]))
            ptype = v.type_name(pv, p, types.pois, "POI")
            at = v.point(pv["at"], p + ".at", size) if "at" in pv else None
            default_r = float(types.pois[ptype]["r"]) if ptype else 20.0
            r = v.num(pv, "r", p, default_r, positive=True)
            team = pv.get("team")
            if team is not None:
                team = str(team)
                if team not in teams:
                    v.err(p + ".team", "unknown team '%s' (teams: %s)" % (team, ", ".join(teams)))
            tier = pv.get("tier")
            if tier is not None and (isinstance(tier, bool) or not isinstance(tier, int)):
                v.err(p + ".tier", "expected an integer, got %r" % (tier,))
                tier = None
            if pid is not None and ptype is not None and at is not None:
                pois.append(Poi(id=pid, type=ptype, at=at, r=r, label=v.text(pv, "label", p), team=team, tier=tier))

    # scatter
    scatter: List[Scatter] = []
    sraw = raw.get("scatter")
    if sraw is not None:
        if not isinstance(sraw, list):
            v.err("scatter", "expected a list")
            sraw = []
        for i, sc in enumerate(sraw):
            p = "scatter[%d]" % i
            if not v.keys(sc, p, ["type", "coverage", "scale", "clump", "avoid", "within", "on"], ["type", "coverage"]):
                continue
            stype = v.type_name(sc, p, types.regions, "region")
            coverage = v.num(sc, "coverage", p, 0.0, lo=0.0, hi=1.0)
            within = v.points(sc["within"], p + ".within", size, 3, "polygon") if "within" in sc else None
            on: List[str] = []
            if "on" in sc:
                if not isinstance(sc["on"], list) or not sc["on"]:
                    v.err(p + ".on", "expected a non-empty list of region types")
                else:
                    for k, t_on in enumerate(sc["on"]):
                        if not isinstance(t_on, str) or t_on not in types.regions:
                            v.err("%s.on[%d]" % (p, k), "unknown region type %r (known: %s)" % (t_on, ", ".join(types.regions)))
                        else:
                            on.append(t_on)
            if stype is not None and (within is not None or "within" not in sc):
                scatter.append(Scatter(index=i, type=stype, coverage=coverage,
                                       scale=v.num(sc, "scale", p, 300.0, positive=True),
                                       clump=v.num(sc, "clump", p, 0.3, lo=0.0, hi=1.0),
                                       avoid=v.num(sc, "avoid", p, 25.0, nonneg=True), within=within, on=on))

    # analysis / output
    analysis = Analysis()
    a = raw.get("analysis")
    if a is not None and v.keys(a, "analysis", ["cell", "speed", "balance_tolerance"], []):
        analysis = Analysis(
            cell=v.num(a, "cell", "analysis", 10.0, positive=True),
            speed=v.num(a, "speed", "analysis", 5.0, positive=True),
            balance_tolerance=v.num(a, "balance_tolerance", "analysis", 0.15, nonneg=True),
        )
    output = Output()
    o = raw.get("output")
    if o is not None and v.keys(o, "output", ["render_px", "heightmap_res", "splat_res", "grid_step"], []):
        hres = v.integer(o, "heightmap_res", "output", 1025, 3)
        if "heightmap_res" in o and isinstance(o["heightmap_res"], int) and not isinstance(o["heightmap_res"], bool):
            n = o["heightmap_res"] - 1
            if n < 2 or (n & (n - 1)) != 0:
                v.err("output.heightmap_res", "must be 2^n+1, e.g. 513, 1025, 2049 (got %d)" % o["heightmap_res"])
        output = Output(
            render_px=v.integer(o, "render_px", "output", 1536, 64),
            heightmap_res=hres,
            splat_res=v.integer(o, "splat_res", "output", 1024, 2),
            grid_step=v.num(o, "grid_step", "output", 500.0, positive=True),
        )

    if v.errors:
        return None, v.errors
    spec = MapSpec(name=name, brief=brief, size=size, seed=seed, terrain=terrain, teams=teams, regions=regions,
                   lines=lines, crossings=crossings, pois=pois, analysis=analysis, output=output, types=types,
                   palette=palette, scatter=scatter, auto_crossings=auto_crossings)
    return spec, []


class _SpecLoader(yaml.SafeLoader):
    """YAML where only true/false are booleans, so a key named `on` (scatter) stays the string "on"."""


_SpecLoader.yaml_implicit_resolvers = {
    k: [(tag, rx) for tag, rx in v if tag != "tag:yaml.org,2002:bool"]
    for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()}
_SpecLoader.add_implicit_resolver("tag:yaml.org,2002:bool", re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
                                  list("tTfF"))


def load_spec(path: str, palette_path: Optional[str] = None) -> MapSpec:
    """Load and validate a map spec file. Raises SpecError listing every problem found.
    `palette_path` selects a palette file (omitted keys fall back to the default palette)."""
    with open(path, "rb") as f:
        data = f.read()
    try:
        raw = yaml.load(data.decode("utf-8"), Loader=_SpecLoader)
    except yaml.YAMLError as e:
        raise SpecError(["<yaml>: %s" % str(e).replace("\n", " ")])
    spec, errors = _validate(raw, load_types(), load_palette(palette_path))
    if errors:
        raise SpecError(errors)
    spec.spec_path = os.path.abspath(path)
    spec.spec_sha256 = hashlib.sha256(data).hexdigest()
    return spec
