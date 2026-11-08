"""Terrain primitive export (spec 6.5): heightmap, splatmaps, masks, JSON."""
from __future__ import annotations

import json
import os
from typing import Dict, List

import numpy as np
from PIL import Image
from shapely.geometry import MultiPolygon, Polygon

from . import __version__, raster
from . import height as heightmod
from .analysis import AnalysisResult
from .geometry import MapGeometry, _resample, chaikin
from .height import Heightmap
from .raster import Grid
from .schema import MapSpec

AXIS_TEXT = ("World units are metres. Origin is the south-west corner. Horizontal axes are x (east) and z (north); "
             "y is height in metres. heightmap.png is north-up (row 0 = north); heightmap.r16 is raw little-endian "
             "uint16 with row 0 = south. Spec coordinates (x, y) map to exported (x, z).")


def _r(v: float) -> float:
    return round(float(v), 2)


def _dump(path: str, data) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=1)
        f.write("\n")


def _save_u8(arr: np.ndarray, path: str, mode: str = "L") -> None:
    Image.fromarray(arr, mode).save(path)


def _smoothstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _quantize_weights(w: np.ndarray) -> np.ndarray:
    """(L, H, W) float weights -> int16 weights summing to exactly 255 per pixel."""
    s = w.sum(axis=0, keepdims=True)
    w = w / np.maximum(s, 1e-9)
    q = np.rint(w * 255.0).astype(np.int16)
    rem = 255 - q.sum(axis=0)
    idx = np.argmax(q, axis=0)
    np.put_along_axis(q, idx[None], np.take_along_axis(q, idx[None], axis=0) + rem[None], axis=0)
    return q


def splat_weights(spec: MapSpec, geoms: MapGeometry, hm: Heightmap, grid: Grid) -> np.ndarray:
    """(L, res, res) float32 splat weights before quantization."""
    layers = spec.types.splat_layers
    idx = {n: i for i, n in enumerate(layers)}
    w = np.zeros((len(layers),) + grid.shape, dtype=np.float32)
    w[idx["grass"] if "grass" in idx else 0] = 1.0

    def paint(layer_name: str, cov: np.ndarray) -> None:
        k = idx[layer_name]
        w[k] = np.maximum(w[k], cov)
        others = np.arange(len(layers)) != k
        w[others] *= (1.0 - cov)[None]

    types = spec.types
    water_regions = set()
    for i, reg in enumerate(spec.regions):
        if geoms.regions[i].is_empty:
            continue
        if types.regions[reg.type].get("height_mode") == "water":
            water_regions.add(i)
            continue
        paint(types.regions[reg.type]["splat"], raster.mask(geoms.regions[i], grid))
    for i, ln in enumerate(spec.lines):
        if geoms.lines[i].area.is_empty or types.lines[ln.type].get("height_mode") == "water":
            continue
        paint(types.lines[ln.type]["splat"], raster.mask(geoms.lines[i].area, grid))
    for i in sorted(water_regions):
        paint(types.regions[spec.regions[i].type]["splat"], raster.mask(geoms.regions[i].buffer(8.0), grid))
    for i, ln in enumerate(spec.lines):
        if geoms.lines[i].area.is_empty or types.lines[ln.type].get("height_mode") != "water":
            continue
        paint(types.lines[ln.type]["splat"], raster.mask(geoms.lines[i].area.buffer(8.0), grid))
    # slope toward rock
    if "rock" in idx:
        slope = heightmod.slope_grid(hm.h, hm.grid)
        slope = np.asarray(Image.fromarray(slope, mode="F").resize((grid.cols, grid.rows), Image.BILINEAR))
        t = _smoothstep(0.6, 1.0, slope).astype(np.float32)
        k = idx["rock"]
        w *= (1.0 - t)[None]
        w[k] += t
    return w


def write_heightmap(spec: MapSpec, hm: Heightmap, out: str) -> List[str]:
    hmax = spec.terrain.height_max
    u16 = np.clip(np.rint(hm.h / hmax * 65535.0), 0, 65535).astype(np.uint16)
    Image.fromarray(u16, mode="I;16").save(os.path.join(out, "heightmap.png"))
    with open(os.path.join(out, "heightmap.r16"), "wb") as f:
        f.write(np.ascontiguousarray(u16[::-1]).astype("<u2").tobytes())
    return ["heightmap.png", "heightmap.r16"]


def write_splats(spec, geoms, hm, out) -> List[str]:
    res = spec.output.splat_res
    grid = Grid(spec.size[0], spec.size[1], res, res)
    q = _quantize_weights(splat_weights(spec, geoms, hm, grid))
    n = q.shape[0]
    files = []
    for k in range(int(np.ceil(n / 4.0))):
        rgba = np.zeros((res, res, 4), dtype=np.uint8)
        for c in range(4):
            if 4 * k + c < n:
                rgba[..., c] = q[4 * k + c].astype(np.uint8)
        name = "splat_%d.png" % k
        Image.fromarray(rgba, "RGBA").save(os.path.join(out, name))
        files.append(name)
    return files


def write_masks(spec, geoms, an: AnalysisResult, out) -> List[str]:
    res = spec.output.splat_res
    grid = Grid(spec.size[0], spec.size[1], res, res)
    mdir = os.path.join(out, "masks")
    os.makedirs(mdir, exist_ok=True)
    types = spec.types
    groups: Dict[str, np.ndarray] = {}

    def add(name: str, m: np.ndarray) -> None:
        groups[name] = np.maximum(groups[name], m) if name in groups else m

    water = np.zeros(grid.shape, np.float32)
    for i, reg in enumerate(spec.regions):
        if geoms.regions[i].is_empty:
            continue
        m = raster.mask(geoms.regions[i], grid)
        add(reg.type, m)
        if types.regions[reg.type].get("height_mode") == "water":
            water = np.maximum(water, m)
    for i, ln in enumerate(spec.lines):
        if geoms.lines[i].area.is_empty:
            continue
        m = raster.mask(geoms.lines[i].area, grid)
        add(ln.type, m)
        if types.lines[ln.type].get("height_mode") == "water":
            water = np.maximum(water, m)
    for i, c in enumerate(spec.crossings):
        if geoms.crossings[i].is_empty:
            continue
        add(c.type, raster.mask(geoms.crossings[i], grid))
    files = []
    for name in sorted(groups):
        _save_u8(np.rint(groups[name] * 255).astype(np.uint8), os.path.join(mdir, name + ".png"))
        files.append("masks/%s.png" % name)
    _save_u8(np.rint(water * 255).astype(np.uint8), os.path.join(mdir, "water.png"))
    files.append("masks/water.png")
    imp = Image.fromarray((~np.isfinite(an.cost)).astype(np.uint8) * 255, "L").resize((res, res), Image.NEAREST)
    imp.save(os.path.join(mdir, "impassable.png"))
    files.append("masks/impassable.png")
    return sorted(files)


def _polys(geom):
    if geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return list(geom.geoms)
    return [g for g in getattr(geom, "geoms", []) if isinstance(g, Polygon)]


def write_json(spec: MapSpec, geoms: MapGeometry, hm: Heightmap, out: str) -> List[str]:
    pois = []
    for p in spec.all_pois():
        y = float(heightmod.sample(hm, p.at[0], p.at[1]))
        pois.append({"id": p.id, "type": p.type, "team": p.team, "tier": p.tier, "label": p.label, "r": _r(p.r),
                     "x": _r(p.at[0]), "z": _r(p.at[1]), "y": _r(y)})
    _dump(os.path.join(out, "pois.json"), pois)

    lines = []
    for i, ln in enumerate(spec.lines):
        pts, _ = _resample(np.asarray(geoms.lines[i].centreline.coords), 10.0)
        hy = heightmod.sample(hm, pts[:, 0], pts[:, 1])
        entry = {"id": ln.id or "line_%d" % i, "type": ln.type, "width": _r(ln.width),
                 "points": [[_r(x), _r(z), _r(y)] for (x, z), y in zip(pts, hy)], "water_surface": None}
        ws = hm.river_surface.get(ln.index)
        if ws is not None and len(ws) == len(pts):
            entry["water_surface"] = [_r(float(v)) for v in ws]
        lines.append(entry)
    _dump(os.path.join(out, "lines.json"), lines)

    regions = []
    for i, reg in enumerate(spec.regions):
        polys = [{"outer": [[_r(x), _r(y)] for x, y in p.exterior.coords],
                  "holes": [[[_r(x), _r(y)] for x, y in ring.coords] for ring in p.interiors]}
                 for p in _polys(geoms.regions[i])]
        ws = hm.lake_surface.get(reg.index)
        regions.append({"id": reg.id or "region_%d" % i, "type": reg.type, "label": reg.label,
                        "height": reg.height, "scatter": reg.scatter is not None,
                        "water_surface": None if ws is None else _r(ws), "polygons": polys})
    _dump(os.path.join(out, "regions.json"), regions)

    crossings = []
    for i, c in enumerate(spec.crossings):
        y = float(heightmod.sample(hm, c.at[0], c.at[1]))
        pts = None
        if c.path is not None:
            cl, _ = _resample(np.asarray(chaikin(c.path)), 10.0)
            hy = heightmod.sample(hm, cl[:, 0], cl[:, 1])
            pts = [[_r(px), _r(pz), _r(py)] for (px, pz), py in zip(cl, hy)]
        crossings.append({"id": c.id or "crossing_%d" % i, "type": c.type, "auto": c.auto,
                          "r": None if c.path is not None else _r(c.r),
                          "width": _r(c.width) if c.path is not None else None,
                          "x": _r(c.at[0]), "z": _r(c.at[1]), "y": _r(y), "points": pts})
    _dump(os.path.join(out, "crossings.json"), crossings)
    return ["pois.json", "lines.json", "regions.json", "crossings.json"]


def _inf(v):
    return "inf" if v == float("inf") else v


def write_resolved(spec: MapSpec, out: str) -> List[str]:
    """spec.resolved.json: the spec with every default applied, scatter regions and automatic crossings expanded."""
    def pt(p):
        return [_r(p[0]), _r(p[1])] if p is not None else None

    t = spec.terrain
    regions = []
    for r in spec.regions:
        e = {"id": r.id or "region_%d" % r.index, "type": r.type, "shape": r.shape, "label": r.label,
             "label_at": pt(r.label_at), "height": r.height, "edge": r.edge, "scatter": r.scatter is not None}
        if r.scatter is not None:
            sc = r.scatter
            e["scatter_params"] = {"coverage": sc.coverage, "scale": sc.scale, "clump": sc.clump, "avoid": sc.avoid,
                                   "within": [pt(p) for p in sc.within] if sc.within else None, "on": list(sc.on)}
        else:
            e.update({"at": pt(r.at), "r": r.r if r.shape == "blob" else None,
                      "rough": r.rough, "squash": r.squash if r.shape == "blob" else None,
                      "angle": r.angle if r.shape == "blob" else None,
                      "points": [pt(p) for p in r.points] if r.points else None,
                      "path": [pt(p) for p in r.path] if r.path else None,
                      "width": r.width if r.shape == "band" else None,
                      "warp": r.warp, "warp_scale": r.warp_scale, "fragment": r.fragment,
                      "fragment_scale": r.fragment_scale})
        regions.append(e)
    data = {
        "name": spec.name, "brief": spec.brief.strip(), "size": [spec.size[0], spec.size[1]], "seed": spec.seed,
        "terrain": {"height_max": t.height_max, "base_height": t.base_height, "relief": t.relief,
                    "relief_scale": t.relief_scale, "water_depth": t.water_depth, "detail": t.detail,
                    "detail_scale": t.detail_scale},
        "teams": [{"id": tm.id, "name": tm.name, "color": tm.color, "base": pt(tm.base)} for tm in spec.teams.values()],
        "regions": regions,
        "lines": [{"id": ln.id or "line_%d" % i, "type": ln.type, "path": [pt(p) for p in ln.path], "width": ln.width,
                   "meander": ln.meander, "meander_scale": ln.meander_scale, "width_var": ln.width_var,
                   "join": ln.join, "join_end": ln.join_end if ln.join else None, "label": ln.label,
                   "label_at": pt(ln.label_at)} for i, ln in enumerate(spec.lines)],
        "crossings": [{"id": c.id or "crossing_%d" % i, "type": c.type, "auto": c.auto,
                       "at": pt(c.at), "r": None if c.path is not None else c.r,
                       "path": [pt(p) for p in c.path] if c.path is not None else None,
                       "width": c.width if c.path is not None else None, "crosses": c.note}
                      for i, c in enumerate(spec.crossings)],
        "pois": [{"id": p.id, "type": p.type, "at": pt(p.at), "r": p.r, "label": p.label, "team": p.team, "tier": p.tier}
                 for p in spec.all_pois()],
        "analysis": {"cell": spec.analysis.cell, "speed": spec.analysis.speed,
                     "balance_tolerance": spec.analysis.balance_tolerance},
        "output": {"render_px": spec.output.render_px, "heightmap_res": spec.output.heightmap_res,
                   "splat_res": spec.output.splat_res, "grid_step": spec.output.grid_step},
        "auto_crossings": spec.auto_crossings,
        "types": {
            "regions": {k: {kk: _inf(vv) for kk, vv in e.items()} for k, e in spec.types.regions.items()},
            "lines": {k: {kk: _inf(vv) for kk, vv in e.items()} for k, e in spec.types.lines.items()},
            "crossings": {k: {kk: _inf(vv) for kk, vv in e.items()} for k, e in spec.types.crossings.items()},
            "pois": spec.types.pois, "splat_layers": spec.types.splat_layers},
    }
    _dump(os.path.join(out, "spec.resolved.json"), data)
    return ["spec.resolved.json"]


def write_meta(spec: MapSpec, out: str) -> List[str]:
    layers = spec.types.splat_layers
    chan = {}
    for i, n in enumerate(layers):
        chan[n] = "splat_%d.%s" % (i // 4, "rgba"[i % 4])
    meta = {
        "name": spec.name,
        "size": [spec.size[0], spec.size[1]],
        "height_max": spec.terrain.height_max,
        "heightmap_res": spec.output.heightmap_res,
        "splat_res": spec.output.splat_res,
        "splat_layers": chan,
        "terrain_layers": [{"name": n, "channel": "rgba"[i % 4], "image": "splat_%d.png" % (i // 4)}
                           for i, n in enumerate(layers)],
        "water_level": None,
        "axes": AXIS_TEXT,
        "seed": spec.seed,
        "spec_sha256": spec.spec_sha256,
        "mapgen_version": __version__,
    }
    _dump(os.path.join(out, "meta.json"), meta)
    return ["meta.json"]


def export_all(spec: MapSpec, geoms: MapGeometry, hm: Heightmap, an: AnalysisResult, out_dir: str) -> List[str]:
    """Write everything under out_dir/export; returns paths relative to out_dir."""
    out = os.path.join(out_dir, "export")
    os.makedirs(out, exist_ok=True)
    files = []
    files += write_heightmap(spec, hm, out)
    files += write_splats(spec, geoms, hm, out)
    files += write_masks(spec, geoms, an, out)
    files += write_json(spec, geoms, hm, out)
    files += write_resolved(spec, out)
    files += write_meta(spec, out)
    return ["export/" + f for f in files]
