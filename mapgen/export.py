"""Terrain primitive export (spec 6.5): heightmap, splatmaps, masks, JSON."""
from __future__ import annotations

import json
import os
from typing import Dict, List

import numpy as np
from PIL import Image
from scipy import ndimage
from shapely.geometry import MultiPolygon, Polygon

from . import __version__, export_settlements, raster
from . import noise as noisemod
from . import height as heightmod
from .analysis import AnalysisResult
from .geometry import MapGeometry, _resample, chaikin
from .height import Heightmap
from .raster import Grid
from .schema import MapSpec, SplatBlend

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


def _shaping(ln) -> dict:
    """Echo of the optional road shaping keys; present only when set, so unshaped specs export unchanged."""
    out = {}
    if ln.wobble > 0:
        out["wobble"] = ln.wobble
        out["wobble_scale"] = ln.wobble_scale
    if ln.smooth:
        out["smooth"] = True
    return out


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
    # water banks: 8 m around each water feature; land lines (trails, roads) crossing a bank win over it there,
    # while the water channel itself keeps the water layer
    bank = np.zeros(grid.shape, dtype=np.float32)
    core = np.zeros(grid.shape, dtype=np.float32)

    def paint_water(layer_name: str, geom) -> None:
        nonlocal bank, core
        m = raster.mask(geom.buffer(8.0), grid)
        paint(layer_name, m)
        bank = np.maximum(bank, m)
        core = np.maximum(core, raster.mask(geom, grid))

    for i in sorted(water_regions):
        paint_water(types.regions[spec.regions[i].type]["splat"], geoms.regions[i])
    for i, ln in enumerate(spec.lines):
        if geoms.lines[i].area.is_empty or types.lines[ln.type].get("height_mode") != "water":
            continue
        paint_water(types.lines[ln.type]["splat"], geoms.lines[i].area)
    bank_only = bank * (1.0 - core)
    for i, ln in enumerate(spec.lines):
        if geoms.lines[i].area.is_empty or types.lines[ln.type].get("height_mode") == "water":
            continue
        cov = raster.mask(geoms.lines[i].area, grid) * bank_only
        if cov.any():
            paint(types.lines[ln.type]["splat"], cov)
    if spec.output.splat_blend is not None:
        blend_splats(w, idx, spec.output.splat_blend, blend_seed(spec), grid)
    # slope toward rock
    if "rock" in idx:
        slope = heightmod.slope_grid(hm.h, hm.grid)
        slope = np.asarray(Image.fromarray(slope, mode="F").resize((grid.cols, grid.rows), Image.BILINEAR))
        t = _smoothstep(0.6, 1.0, slope).astype(np.float32)
        k = idx["rock"]
        w *= (1.0 - t)[None]
        w[k] += t
    return w


def blend_seed(spec: MapSpec) -> int:
    """Noise seed of output.splat_blend: its explicit seed, else derived from the map seed."""
    b = spec.output.splat_blend
    if b is not None and b.seed is not None:
        return int(b.seed)
    return (int(spec.seed) * 1000003 + 0x5B1E) & 0x7FFFFFFF


def _move(w: np.ndarray, src: int, dst: int, frac: np.ndarray) -> None:
    """Move the share `frac` of layer src's weight to layer dst (per pixel; the weight sum is unchanged)."""
    m = w[src] * frac
    w[src] -= m
    w[dst] += m


def blend_splats(w: np.ndarray, idx: Dict[str, int], b: SplatBlend, seed: int, grid: Grid) -> None:
    """In place: road shoulders to dirt, noise dirt patches in grass near town/road ground, noise grass tufts in
    dirt, then feather every layer edge. Runs on the hard-painted weights before the slope-to-rock pass."""
    cell = (grid.cell_size_y, grid.cell_size_x)
    has = lambda n: n in idx    # noqa: E731
    halo = np.zeros(grid.shape, dtype=np.float32)
    if has("road") and has("dirt") and b.road_edge > 0:
        road = w[idx["road"]] >= 0.5
        if road.any():
            d = ndimage.distance_transform_edt(~road, sampling=cell).astype(np.float32)
            halo = np.where(road, 0.0, 1.0 - _smoothstep(0.0, b.road_edge, d)).astype(np.float32)
            for n in ("grass", "field", "forest"):
                if has(n):
                    _move(w, idx[n], idx["dirt"], halo)
    if has("grass") and has("dirt") and (b.patches > 0 or b.tufts > 0):
        xs = (np.arange(grid.cols) + 0.5) * grid.cell_size_x
        ys = grid.size_y - (np.arange(grid.rows) + 0.5) * grid.cell_size_y
        g, dk = idx["grass"], idx["dirt"]
        if b.patches > 0:
            ground = w[dk] + (w[idx["road"]] if has("road") else 0.0) >= 0.5
            if ground.any():
                d = ndimage.distance_transform_edt(~ground, sampling=cell).astype(np.float32)
                near = 1.0 - _smoothstep(0.0, b.patch_reach, d)
                n1 = noisemod.fbm2_grid(xs / b.noise_scale, ys / b.noise_scale, seed, octaves=3)
                _move(w, g, dk, (b.patches * _smoothstep(0.05, 0.35, n1) * near).astype(np.float32))
        if b.tufts > 0:
            s2 = b.noise_scale * 0.6
            n2 = noisemod.fbm2_grid(xs / s2, ys / s2, seed + 7919, octaves=3)
            _move(w, dk, g, (b.tufts * _smoothstep(0.15, 0.45, n2) * (1.0 - halo)).astype(np.float32))
    if b.edge > 0:
        sigma = (b.edge / 2.0 / grid.cell_size_y, b.edge / 2.0 / grid.cell_size_x)
        for k in range(w.shape[0]):
            w[k] = ndimage.gaussian_filter(w[k], sigma=sigma, mode="nearest")
    np.clip(w, 0.0, None, out=w)


def _blend_echo(b: SplatBlend) -> dict:
    return {"edge": b.edge, "road_edge": b.road_edge, "noise_scale": b.noise_scale, "patches": b.patches,
            "patch_reach": b.patch_reach, "tufts": b.tufts, "seed": b.seed}


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
    for f in os.listdir(mdir):   # masks of types an earlier build of this map had and this spec no longer does
        if f.endswith(".png"):
            os.remove(os.path.join(mdir, f))
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
        entry = {"id": ln.id, "type": ln.type, "width": _r(ln.width),
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
        regions.append({"id": reg.id, "type": reg.type, "label": reg.label,
                        "height": reg.height, "scatter": reg.scatter is not None,
                        "water_surface": None if ws is None else _r(ws), "polygons": polys})
        if reg.impassable:
            regions[-1]["impassable"] = True
    _dump(os.path.join(out, "regions.json"), regions)

    crossings = []
    for i, c in enumerate(spec.crossings):
        y = float(heightmod.sample(hm, c.at[0], c.at[1]))
        pts = None
        if c.path is not None:
            cl, _ = _resample(np.asarray(chaikin(c.path)), 10.0)
            hy = heightmod.sample(hm, cl[:, 0], cl[:, 1])
            pts = [[_r(px), _r(pz), _r(py)] for (px, pz), py in zip(cl, hy)]
        crossings.append({"id": c.id, "type": c.type, "auto": c.auto,
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
        e = {"id": r.id, "seed": r.seed, "type": r.type, "shape": r.shape, "label": r.label,
             "label_at": pt(r.label_at), "height": r.height, "edge": r.edge, "scatter": r.scatter is not None}
        if r.impassable:
            e["impassable"] = True
        if r.scatter is not None:
            sc = r.scatter
            e["scatter_params"] = {"coverage": sc.coverage, "scale": sc.scale, "clump": sc.clump, "avoid": sc.avoid,
                                   "within": [pt(p) for p in sc.within] if sc.within else None, "on": list(sc.on)}
            if sc.impassable:
                e["scatter_params"]["impassable"] = True
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
        "format_version": spec.format_version,
        "name": spec.name, "brief": spec.brief.strip(), "size": [spec.size[0], spec.size[1]], "seed": spec.seed,
        "terrain": {"height_max": t.height_max, "base_height": t.base_height, "relief": t.relief,
                    "relief_scale": t.relief_scale, "water_depth": t.water_depth, "detail": t.detail,
                    "detail_scale": t.detail_scale},
        "teams": [{"id": tm.id, "name": tm.name, "color": tm.color, "base": pt(tm.base)} for tm in spec.teams.values()],
        "regions": regions,
        "lines": [dict({"id": ln.id, "seed": ln.seed, "type": ln.type, "path": [pt(p) for p in ln.path],
                        "width": ln.width, "meander": ln.meander, "meander_scale": ln.meander_scale,
                        "width_var": ln.width_var, "join": ln.join, "join_end": ln.join_end if ln.join else None,
                        "label": ln.label, "label_at": pt(ln.label_at)},
                       **_shaping(ln)) for i, ln in enumerate(spec.lines)],
        "crossings": [{"id": c.id, "type": c.type, "auto": c.auto,
                       "at": pt(c.at), "r": None if c.path is not None else c.r,
                       "path": [pt(p) for p in c.path] if c.path is not None else None,
                       "width": c.width if c.path is not None else None, "crosses": c.note}
                      for i, c in enumerate(spec.crossings)],
        "pois": [{"id": p.id, "type": p.type, "at": pt(p.at), "r": p.r, "label": p.label, "team": p.team, "tier": p.tier}
                 for p in spec.all_pois()],
        "analysis": {"cell": spec.analysis.cell, "speed": spec.analysis.speed,
                     "balance_tolerance": spec.analysis.balance_tolerance},
        "output": dict({"render_px": spec.output.render_px, "heightmap_res": spec.output.heightmap_res,
                        "splat_res": spec.output.splat_res, "grid_step": spec.output.grid_step},
                       **({"splat_blend": _blend_echo(spec.output.splat_blend)}
                          if spec.output.splat_blend is not None else {})),   # key present only when on
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
    files += export_settlements.write_settlements(spec, out, out_dir, an.warnings)   # town maps with a settlement file only
    files += write_meta(spec, out)
    return ["export/" + f for f in files]
