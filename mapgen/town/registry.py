"""Generator registry and the public `generate` entry point (design: mapgen/TOWN_SPEC.md section 6)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import fort, gen_grid, gen_organic, gen_radial, gen_voronoi, layers, layers_params, warp, water
from .model import Settlement
from .params import ParamDef, ParamError, resolve

DEFAULT_SIZE = (1000.0, 1000.0)
MIN_SIZE, MAX_SIZE = 400.0, 3000.0
_SID = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")
SID_MAX_LEN = 16


@dataclass(frozen=True)
class Generator:
    name: str
    defs: List[ParamDef]
    build: Callable[[Dict[str, Any], int, Tuple[float, float], Tuple[float, float], str], Settlement]
    doc: str


GENERATORS: Dict[str, Generator] = {
    "radial": Generator("radial", gen_radial.DEFS, gen_radial.generate,
                        "ring roads, wedge districts, central plaza, irregular wall with gates on spokes"),
    "voronoi": Generator("voronoi", gen_voronoi.DEFS, gen_voronoi.generate,
                         "Voronoi-cell districts on a seed spiral, border arteries to a plaza, angular wall; organic=true for the chaotic regime"),
    "organic": Generator("organic", gen_organic.DEFS, gen_organic.generate,
                         "splotchy shape-field footprint, curved arteries and arcs, perturbed plaza; edge=%s" % gen_organic.EDGE_DOC),
    "grid": Generator("grid", gen_grid.DEFS, gen_grid.generate,
                      "rotated street lattice in a superellipse wall, market square with radial boulevards, optional inner wall"),
}


def generate(generator: str, seed: int, size: Sequence[float] = DEFAULT_SIZE, params: Optional[Dict[str, Any]] = None,
             sid: str = "town", centre: Optional[Sequence[float]] = None) -> Settlement:
    """Deterministic: the same arguments give the same Settlement. Raises ParamError (bad arguments, `.errors` lists
    every one) or the generator's GenerationError (no layout fits)."""
    errors: List[str] = []
    if generator not in GENERATORS:
        raise ParamError(["generator: unknown %r (known: %s)" % (generator, ", ".join(sorted(GENERATORS)))])
    if not isinstance(seed, int) or isinstance(seed, bool) or not (0 <= seed <= 4294967295):
        errors.append("seed: expected an integer 0-4294967295, got %r" % (seed,))
    if not _SID.match(sid or "") or len(sid) > SID_MAX_LEN:
        errors.append("id: must match [a-z][a-z0-9_]* (no double or trailing underscore), at most %d characters (got %r)"
                      % (SID_MAX_LEN, sid))
    if len(size) != 2 or not all(MIN_SIZE <= float(v) <= MAX_SIZE for v in size):
        errors.append("size: expected [x, y] metres, each %g-%g, got %r" % (MIN_SIZE, MAX_SIZE, list(size)))
    if errors:
        raise ParamError(errors)
    sz = (float(size[0]), float(size[1]))
    c = (sz[0] / 2.0, sz[1] / 2.0) if centre is None else (float(centre[0]), float(centre[1]))
    gen = GENERATORS[generator]
    wp = water.resolve({k: v for k, v in (params or {}).items() if water.is_water_param(k)})
    base_params = {k: v for k, v in (params or {}).items() if not fort.is_fort_param(k) and not water.is_water_param(k)
                   and not layers_params.is_layer_param(k) and not warp.is_warp_param(k)}
    wr = warp.resolve((params or {}).get("warp"))
    fp = fort.resolve({k: v for k, v in (params or {}).items() if fort.is_fort_param(k)})
    edge = None
    if generator == "organic":
        edge = base_params.pop("edge", "wall")
        if edge not in gen_organic.EDGES:
            raise ParamError(["params.edge: %r is not one of %s" % (edge, ", ".join(gen_organic.EDGES))])
        if edge == "open" and fort.active(fp):
            raise ParamError(["params.fort: the fort add-on needs an outer wall (edge=wall)"])
    defs = gen.defs + gen_organic.FADE_DEFS if edge == "fade" else gen.defs
    p = resolve(defs, seed, sid, generator, base_params)
    if edge is not None:
        p["edge"] = edge
    s = gen.build(p, seed, sz, c, sid)
    if water.active(wp):
        s = water.apply(s, wp)
    if fort.active(fp):
        s = fort.apply(s, fp)
    spec = layers_params.resolve((params or {}).get("layers"), p.get("min_district_area", 1600.0))
    if spec:
        s = layers.apply(s, spec)
    return warp.apply(s, wr) if wr else s
