"""Parameter schema, seeded drawing and per-stage random streams (design: mapgen/TOWN_SPEC.md section 4)."""
from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


class ParamError(Exception):
    def __init__(self, errors: List[str]):
        Exception.__init__(self, "; ".join(errors))
        self.errors = errors


@dataclass(frozen=True)
class ParamDef:
    name: str
    type: str                       # int | float | bool
    lo: float                       # valid range of an explicit value: [lo, hi] (not checked for bool)
    hi: float
    doc: str = ""
    draw_lo: Optional[float] = None  # seeded draw range, uniform in [draw_lo, draw_hi] (default [lo, hi])
    draw_hi: Optional[float] = None  # bool: probability of true
    default: Any = None              # not None: never drawn, this value unless overridden


def stage_rng(settlement_id: str, generator: str, seed: int, stage: str) -> random.Random:
    """Independent stream per stage: adding a draw in one stage leaves the others untouched."""
    h = hashlib.sha256(("%s|%s|%d|%s" % (settlement_id, generator, seed, stage)).encode("utf-8")).digest()
    return random.Random(int.from_bytes(h[:8], "big"))


def _coerce(d: ParamDef, v: Any) -> Any:
    if d.type == "bool":
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            if v.lower() in ("true", "1", "yes"):
                return True
            if v.lower() in ("false", "0", "no"):
                return False
        raise ValueError("expected true or false")
    if isinstance(v, bool):
        raise ValueError("expected a number")
    x = float(v)
    if d.type == "int":
        if x != int(x):
            raise ValueError("expected an integer")
        return int(x)
    return x


def resolve(defs: List[ParamDef], seed: int, settlement_id: str, generator: str,
            overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Every parameter: the explicit override, else the default, else the seeded draw. Unknown names and values
    outside [lo, hi] are errors (all collected). Drawn parameters are drawn in the order of `defs` from one stream
    (stage `params`) even when overridden, so an override never shifts the other draws."""
    overrides = dict(overrides or {})
    errors: List[str] = []
    names = {d.name for d in defs}
    for k in sorted(overrides):
        if k not in names:
            errors.append("params.%s: unknown parameter (known: %s)" % (k, ", ".join(sorted(names))))
    rng = stage_rng(settlement_id, generator, seed, "params")
    out: Dict[str, Any] = {}
    for d in defs:
        draw = d.default
        if d.default is None:
            lo = d.lo if d.draw_lo is None else d.draw_lo
            hi = d.hi if d.draw_hi is None else d.draw_hi
            if d.type == "bool":
                draw = rng.random() < hi
            elif d.type == "int":
                draw = rng.randint(int(lo), int(hi))
            else:
                draw = round(rng.uniform(lo, hi), 4)
        if d.name in overrides:
            try:
                v = _coerce(d, overrides[d.name])
            except ValueError as e:
                errors.append("params.%s: %s (got %r)" % (d.name, e, overrides[d.name]))
                continue
            if d.type != "bool" and not (d.lo <= v <= d.hi):
                errors.append("params.%s: %r outside [%g, %g]" % (d.name, v, d.lo, d.hi))
                continue
            out[d.name] = v
        else:
            out[d.name] = draw
    if errors:
        raise ParamError(errors)
    return out
