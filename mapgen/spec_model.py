"""Editor view of a spec text: the draggable handles (id- and path-addressed), the reference graph and the resolved
geometry as JSON. Pure functions of the text; no HTTP, no state. Design: docs/investigations/2026-11-19-map-editor-design.md 3, 5.

A handle is one spec value that the editor moves: `path` is its `specpatch` path (id-addressed, never a list position of
an id-carrying sequence), `at` its resolved coordinate, `ref` the point name when the written value is a reference.
Setting a reference handle moves the referenced definition (specpatch semantics); the definition's own handle is a
`base`, `poi` or `point`.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import schema, specpatch
from .flowgraph_check import raw_line_paths_doc

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
                          tok if isinstance(tok, str) else None, line=ln.id, index=j, count=len(ln.path))
        by_id = {r.id: r for r in spec.regions if r.scatter is None}
        for rr in raw.get("regions") or []:
            if not isinstance(rr, dict) or rr.get("id") not in by_id:
                continue
            reg = by_id[rr["id"]]
            if reg.shape == "blob" and reg.at is not None:
                tok = rr.get("at")
                self._add("center", "regions.%s.at" % reg.id, reg.id, reg.type, reg.at, tok if isinstance(tok, str) else None)
            else:
                key, pts = ("points", reg.points) if reg.shape == "poly" else ("path", reg.path)
                toks = rr.get(key) if isinstance(rr.get(key), list) else []
                for j, pt in enumerate(pts or []):
                    tok = toks[j] if j < len(toks) else None
                    self._add("vertex", "regions.%s.%s[%d]" % (reg.id, key, j), reg.id, reg.type, pt,
                              tok if isinstance(tok, str) else None, index=j)
        for cr in raw.get("crossings") or []:  # referrers only: crossings are not draggable in this slice
            if not isinstance(cr, dict):
                continue
            if isinstance(cr.get("at"), str):
                self.referrers.setdefault(cr["at"], []).append({"feature": str(cr.get("id")), "path": "crossings.%s.at" % cr.get("id")})
            for j, tok in enumerate(cr.get("path") if isinstance(cr.get("path"), list) else []):
                if isinstance(tok, str):
                    self.referrers.setdefault(tok, []).append({"feature": str(cr.get("id")), "path": "crossings.%s.path[%d]" % (cr.get("id"), j)})

    def state(self) -> Dict[str, Any]:
        return {"size": [self.spec.size[0], self.spec.size[1]], "handles": self.handles, "referrers": self.referrers}


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
