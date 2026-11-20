"""Editor view of a spec text: the draggable handles (id- and path-addressed), the reference graph and the resolved
geometry as JSON. Pure functions of the text; no HTTP, no state. Design: docs/investigations/2026-11-19-map-editor-design.md 3, 5.

A handle is one spec value that the editor moves: `path` is its `specpatch` path (id-addressed, never a list position of
an id-carrying sequence), `at` its resolved coordinate, `ref` the point name when the written value is a reference.
Setting a reference handle moves the referenced definition (specpatch semantics); the definition's own handle is a
`base`, `poi` or `point`.
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional

from . import schema, specpatch
from .flowgraph_check import raw_line_paths_doc

SECTION_KIND = {"regions": "region", "lines": "line", "crossings": "crossing", "pois": "poi", "scatter": "scatter"}
SIMPLIFY_LINE_M = 1.5
SIMPLIFY_REGION_M = 3.0


def _xy(p) -> List[float]:
    return [float(p[0]), float(p[1])]


class Model:
    """Everything the page draws from a spec that loaded: handles and referrers. `spec` is the loaded MapSpec."""

    def __init__(self, text: str, spec: schema.MapSpec):
        self.spec = spec
        raw = specpatch.parsed_document(text)
        self.raw = raw if isinstance(raw, dict) else {}
        self.handles = []  # type: List[Dict[str, Any]]
        self.referrers = {}  # type: Dict[str, List[Dict[str, str]]]
        self._build()

    # -- handles ----------------------------------------------------------------------------------------------------
    def _add(self, kind: str, path: str, feature: str, ftype: str, at, ref: Optional[str] = None, **extra) -> None:
        h = {"kind": kind, "path": path, "feature": feature, "ftype": ftype, "at": _xy(at), "ref": ref}
        h.update(extra)
        self.handles.append(h)
        if ref is not None:
            self.referrers.setdefault(ref, []).append({"feature": feature, "path": path})

    def _build(self) -> None:
        spec, raw = self.spec, self.raw
        for t in spec.teams.values():
            self._add("base", "teams.%s.base" % t.id, "base_" + t.id, "base", t.base, label=t.name, team=t.id)
        for p in spec.pois:
            self._add("poi", "pois.%s.at" % p.id, p.id, p.type, p.at, label=p.label)
        for name, pt in spec.points.items():
            self._add("point", "points.%s" % name, name, "point", pt)
        raw_paths = raw_line_paths_doc(raw)
        for ln in spec.lines:
            rp = raw_paths.get(ln.index, [])
            for j, pt in enumerate(ln.path):
                tok = rp[j] if j < len(rp) else None
                self._add("waypoint", "lines.%s.path[%d]" % (ln.id, j), ln.id, ln.type, pt,
                          tok if isinstance(tok, str) else None, line=ln.id, index=j, count=len(ln.path),
                          chain="lines.%s.path" % ln.id, closed=False, minimum=2)
            self._width_handle("lines.%s.width" % ln.id, ln.id, ln.type, ln.path, ln.width, "lines.%s.path" % ln.id)
        by_id = {r.id: r for r in spec.regions if r.scatter is None}
        for rr in raw.get("regions") or []:
            if not isinstance(rr, dict) or rr.get("id") not in by_id:
                continue
            reg = by_id[rr["id"]]
            if reg.shape == "blob" and reg.at is not None:
                tok = rr.get("at")
                self._add("center", "regions.%s.at" % reg.id, reg.id, reg.type, reg.at, tok if isinstance(tok, str) else None)
                self._add("radius", "regions.%s.r" % reg.id, reg.id, reg.type, (reg.at[0] + reg.r, reg.at[1]), center=_xy(reg.at))
            else:
                key, pts = ("points", reg.points) if reg.shape == "poly" else ("path", reg.path)
                toks = rr.get(key) if isinstance(rr.get(key), list) else []
                for j, pt in enumerate(pts or []):
                    tok = toks[j] if j < len(toks) else None
                    self._add("vertex", "regions.%s.%s[%d]" % (reg.id, key, j), reg.id, reg.type, pt,
                              tok if isinstance(tok, str) else None, index=j, count=len(pts), chain="regions.%s.%s" % (reg.id, key),
                              closed=reg.shape == "poly", minimum=3 if reg.shape == "poly" else 2)
                if reg.shape == "band" and reg.path:
                    self._width_handle("regions.%s.width" % reg.id, reg.id, reg.type, reg.path, reg.width, "regions.%s.path" % reg.id)
        sc_by_id = {s.id: s for s in spec.scatter}
        for it in raw.get("scatter") or []:   # a scatter entry's `within` polygon (literal points only)
            sc = sc_by_id.get(it.get("id")) if isinstance(it, dict) else None
            if sc is None or not isinstance(it.get("within"), list):
                continue
            for j, pt in enumerate(it["within"]):
                if isinstance(pt, list) and len(pt) == 2:
                    self._add("vertex", "scatter.%s.within[%d]" % (sc.id, j), sc.id, sc.type, pt, None, index=j,
                              count=len(it["within"]), chain="scatter.%s.within" % sc.id, closed=True, minimum=3)
        explicit = {c.id: c for c in spec.crossings if not c.auto}
        for cr in raw.get("crossings") or []:
            if not isinstance(cr, dict):
                continue
            cid = cr.get("id")
            sc = explicit.get(cid)
            if isinstance(cr.get("at"), str):
                self.referrers.setdefault(cr["at"], []).append({"feature": str(cid), "path": "crossings.%s.at" % cid})
            toks = cr.get("path") if isinstance(cr.get("path"), list) else []
            for j, tok in enumerate(toks):
                if isinstance(tok, str):
                    self.referrers.setdefault(tok, []).append({"feature": str(cid), "path": "crossings.%s.path[%d]" % (cid, j)})
            if sc is None:
                continue
            if sc.path:
                for j, pt in enumerate(sc.path):
                    tok = toks[j] if j < len(toks) else None
                    self._add("vertex", "crossings.%s.path[%d]" % (cid, j), cid, sc.type, pt, tok if isinstance(tok, str) else None,
                              index=j, count=len(sc.path), chain="crossings.%s.path" % cid, closed=False, minimum=2)
                self._width_handle("crossings.%s.width" % cid, cid, sc.type, sc.path, sc.width, "crossings.%s.path" % cid)
            else:
                tok = cr.get("at")
                self._add("crossing", "crossings.%s.at" % cid, cid, sc.type, sc.at, tok if isinstance(tok, str) else None)
                self._add("radius", "crossings.%s.r" % cid, cid, sc.type, (sc.at[0] + sc.r, sc.at[1]), center=_xy(sc.at))
        for ln in raw.get("lines") or []:   # a `join` is a reference to a line
            if isinstance(ln, dict) and isinstance(ln.get("join"), str):
                self.referrers.setdefault(ln["join"], []).append({"feature": str(ln.get("id")), "path": "lines.%s.join" % ln.get("id")})
        self._features()

    def _width_handle(self, path: str, feature: str, ftype: str, pts, width: float, chain: str) -> None:
        """A handle at the middle of the polyline, `width / 2` to its left; dragging it sets the width."""
        if not pts or len(pts) < 2:
            return
        seg = [math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]) for i in range(len(pts) - 1)]
        half, acc = sum(seg) / 2.0, 0.0
        for i, L in enumerate(seg):
            if acc + L >= half and L > 0:
                u = (half - acc) / L
                x, y = pts[i][0] + u * (pts[i + 1][0] - pts[i][0]), pts[i][1] + u * (pts[i + 1][1] - pts[i][1])
                nx, ny = -(pts[i + 1][1] - pts[i][1]) / L, (pts[i + 1][0] - pts[i][0]) / L
                self._add("width", path, feature, ftype, (x + nx * width / 2.0, y + ny * width / 2.0), chain=chain,
                          anchor=[x, y], value=width)
                return
            acc += L

    def _features(self) -> None:
        """One record per selectable feature, per section in file order: id, kind, section, type, shape, raw keys, first handle."""
        raw = self.raw
        first = {}  # type: Dict[str, str]
        for h in self.handles:
            first.setdefault(h["feature"], h["path"])
        out = []  # type: List[Dict[str, Any]]
        rteams = raw.get("teams") if isinstance(raw.get("teams"), dict) else {}
        for tid in self.spec.teams:
            tv = rteams.get(tid)
            out.append({"id": "base_" + tid, "kind": "base", "section": "teams", "type": "base", "shape": None,
                        "props": {"base": tv.get("base") if isinstance(tv, dict) else None}})
        for sect in specpatch.SEQ_SECTIONS:
            for it in raw.get(sect) or []:
                if isinstance(it, dict) and isinstance(it.get("id"), str):
                    out.append({"id": it["id"], "kind": SECTION_KIND[sect], "section": sect, "type": it.get("type"),
                                "shape": it.get("shape"), "props": it})
        if isinstance(raw.get("points"), dict):
            for name, v in raw["points"].items():
                out.append({"id": name, "kind": "point", "section": "points", "type": "point", "shape": None, "props": {"at": v}})
        for f in out:
            f["handle"] = first.get(f["id"])
        self.features = out

    def state(self) -> Dict[str, Any]:
        return {"size": [self.spec.size[0], self.spec.size[1]], "handles": self.handles, "referrers": self.referrers,
                "features": self.features}


def geometry_json(spec: schema.MapSpec, geoms) -> Dict[str, Any]:
    """Resolved centrelines and region outlines (after `geometry.build_geometries(spec)`), simplified for drawing."""
    lines = []
    for ln, lg in zip(spec.lines, geoms.lines):
        c = lg.centreline.simplify(SIMPLIFY_LINE_M) if not lg.centreline.is_empty else lg.centreline
        lines.append({"id": ln.id, "type": ln.type, "width": ln.width,
                      "pts": [[round(x, 1), round(y, 1)] for x, y in c.coords]})
    regions = []
    for reg, g in zip(spec.regions, geoms.regions):
        if g.is_empty:
            continue
        rings = []
        for part in getattr(g, "geoms", [g]):
            if part.geom_type != "Polygon" or part.is_empty:
                continue
            s = part.simplify(SIMPLIFY_REGION_M)
            if not s.is_empty and s.geom_type == "Polygon":
                rings.append([[round(x, 1), round(y, 1)] for x, y in s.exterior.coords])
        if rings:
            regions.append({"id": reg.id, "type": reg.type, "scatter": reg.scatter is not None, "rings": rings})
    return {"lines": lines, "regions": regions}


# ----------------------------------------------------------------------------- loader errors -> features

_ERR_PATH = re.compile(r"^([A-Za-z_]+)(?:\[(\d+)\])?((?:\.[A-Za-z0-9_-]+|\[\d+\])*)")


def map_error(raw: Any, message: str) -> Dict[str, Any]:
    """Map a loader message (`pois[3].at: ...`, `lines[1].path[2]: ...`, `points.ao: ...`) onto the feature it is about:
    {"feature": id | None, "path": handle path | None, "key": the property name | None}."""
    out = {"feature": None, "path": None, "key": None}  # type: Dict[str, Any]
    m = _ERR_PATH.match(message)
    if not m or not isinstance(raw, dict):
        return out
    sect, idx, rest = m.group(1), m.group(2), m.group(3)
    if sect in specpatch.SEQ_SECTIONS and idx is not None:
        items = raw.get(sect)
        if not isinstance(items, list) or int(idx) >= len(items) or not isinstance(items[int(idx)], dict):
            return out
        fid = items[int(idx)].get("id")
        if not isinstance(fid, str):
            return out
        km = re.match(r"\.([A-Za-z_]+)", rest)
        key = km.group(1) if km else None
        out.update(feature=fid, key=key, path="%s.%s%s" % (sect, fid, rest))
    elif sect == "points" and rest.startswith("."):
        name = rest[1:].split(".")[0].split("[")[0]
        out.update(feature=name, path="points.%s" % name, key=None)
    elif sect == "teams" and rest.startswith("."):
        parts = rest[1:].split(".")
        out.update(feature="base_" + parts[0], path="teams.%s.base" % parts[0], key=parts[1] if len(parts) > 1 else None)
    return out


# editing hints per property key: t = num | int | bool | text | enum | line | team; default = value written when the key is added
PROPS = {
    "r": {"t": "num", "min": 1, "step": 1, "default": 40},
    "rough": {"t": "num", "min": 0, "max": 1, "step": 0.05, "default": 0.3},
    "squash": {"t": "num", "min": 0.1, "step": 0.05, "default": 0.7},
    "angle": {"t": "num", "step": 5, "default": 0},
    "width": {"t": "num", "min": 1, "step": 1, "default": 10},
    "warp": {"t": "num", "min": 0, "step": 1, "default": 20},
    "warp_scale": {"t": "num", "min": 1, "step": 5, "default": 80},
    "fragment": {"t": "num", "min": 0, "max": 1, "step": 0.05, "default": 0.3},
    "fragment_scale": {"t": "num", "min": 1, "step": 5, "default": 150},
    "height": {"t": "num", "min": 0, "step": 1, "default": 50},
    "edge": {"t": "num", "min": 1, "step": 1, "default": 30},
    "meander": {"t": "num", "min": 0, "step": 1, "default": 40},
    "meander_scale": {"t": "num", "min": 1, "step": 10, "default": 300},
    "width_var": {"t": "num", "min": 0, "max": 1, "step": 0.05, "default": 0.3},
    "wobble": {"t": "num", "min": 0, "step": 1, "default": 15},
    "wobble_scale": {"t": "num", "min": 1, "step": 10, "default": 150},
    "coverage": {"t": "num", "min": 0, "max": 1, "step": 0.05, "default": 0.4},
    "scale": {"t": "num", "min": 1, "step": 5, "default": 100},
    "clump": {"t": "num", "min": 0, "max": 1, "step": 0.05, "default": 0.3},
    "avoid": {"t": "num", "min": 0, "step": 1, "default": 20},
    "tier": {"t": "int", "step": 1, "default": 1},
    "seed": {"t": "int", "min": 0, "max": 4294967295, "step": 1, "default": 1},
    "impassable": {"t": "bool", "default": True},
    "smooth": {"t": "bool", "default": True},
    "label": {"t": "text", "default": "New label"},
    "join_end": {"t": "enum", "values": ["start", "end"], "default": "end"},
    "join": {"t": "line", "default": None},
    "team": {"t": "team", "default": None},
}
# keys the generic property editor leaves to the geometry tools, the type picker or the rename operation
STRUCTURAL = ("id", "type", "shape", "at", "points", "path", "within", "label_at", "on")


def schema_json(types: "schema.TypeTable") -> Dict[str, Any]:
    """What the page needs for its property editor and type pickers: allowed and required keys per feature kind (read
    from schema.py, so they cannot drift), the type tables of types.yaml and the editing hints of each key."""
    def cell(v):
        return "inf" if isinstance(v, float) and math.isinf(v) else v
    keys = {}
    for kind in ("line", "crossing", "poi", "scatter"):
        a, r = schema.allowed_keys(kind)
        keys[kind] = {"allowed": a, "required": r}
    for shape in ("blob", "poly", "band"):
        a, r = schema.allowed_keys("region", shape)
        keys["region_" + shape] = {"allowed": a, "required": r}
    tables = (("region", types.regions), ("line", types.lines), ("crossing", types.crossings), ("poi", types.pois))
    return {"keys": keys, "structural": list(STRUCTURAL), "props": PROPS,
            "types": {k: {n: {kk: cell(vv) for kk, vv in row.items()} for n, row in tbl.items()} for k, tbl in tables}}
