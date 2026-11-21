"""`mapgen town` commands (design: mapgen/TOWN_SPEC.md section 11)."""
from __future__ import annotations

import os
from typing import Any, Dict, List

from . import (GENERATORS, GenerationError, ParamError, Settlement, check, check_determinism, emit_flow, emit_spec,
               generate, render_png, render_sheet)
from . import fort, layers_params, warp, water
from .registry import DEFAULT_SIZE


def _write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _parse_params(items: List[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for it in items or []:
        if "=" not in it:
            raise ParamError(["--param %r: expected name=value" % it])
        k, v = it.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _load(path: str) -> Settlement:
    with open(path, "r", encoding="utf-8") as f:
        return Settlement.from_json(f.read())


def cmd_generate(args) -> int:
    try:
        s = generate(args.generator, args.seed, args.size or DEFAULT_SIZE, _parse_params(args.param), args.id)
    except ParamError as e:
        print("%d error(s):" % len(e.errors))
        for line in e.errors:
            print("  " + line)
        return 1
    except GenerationError as e:
        print("error: %s" % e)
        return 1
    errs = check(s)
    if errs:
        print("%d check failure(s):" % len(errs))
        for line in errs:
            print("  " + line)
        return 1
    out = args.out or os.path.join("out", "town_%s_%s_%d" % (s.id, s.generator, s.seed))
    os.makedirs(out, exist_ok=True)
    _write(os.path.join(out, "settlement.json"), s.to_json())
    _write(os.path.join(out, "spec_fragment.yaml"), emit_spec(s))
    _write(os.path.join(out, "flow_fragment.yaml"), emit_flow(s))
    render_png(s).save(os.path.join(out, "preview.png"))
    print("OK: %s %s seed %d: %d districts, %d roads, %d gates, %d movement nodes -> %s"
          % (s.generator, s.id, s.seed, len(s.districts), len(s.roads), len(s.gates), len(s.graph_nodes), out))
    return 0


def cmd_check(args) -> int:
    rc = 0
    for path in args.settlement:
        try:
            s = _load(path)
        except FileNotFoundError:
            print("error: file not found: %s" % path)
            rc = 1
            continue
        except (ValueError, KeyError, TypeError) as e:
            print("error: %s is not a settlement file: %s" % (path, e))
            rc = 1
            continue
        try:
            errs = check(s) + check_determinism(s)
        except (ParamError, GenerationError) as e:
            errs = ["regeneration failed: %s" % e]
        if errs:
            print("FAIL: %s: %d failure(s)" % (path, len(errs)))
            for line in errs:
                print("  " + line)
            rc = 1
        else:
            print("OK: %s (%d districts, %d gates)" % (path, len(s.districts), len(s.gates)))
    return rc


def cmd_preview(args) -> int:
    try:
        items = [_load(p) for p in args.settlement]
    except FileNotFoundError as e:
        print("error: file not found: %s" % e.filename)
        return 1
    except (ValueError, KeyError, TypeError) as e:
        print("error: not a settlement file: %s" % e)
        return 1
    if os.path.exists(args.out) and not args.force:
        print("error: %s exists (use --force to overwrite)" % args.out)
        return 1
    d = os.path.dirname(args.out)
    if d:
        os.makedirs(d, exist_ok=True)
    (render_png(items[0]) if len(items) == 1 else render_sheet(items)).save(args.out)
    print("wrote %s" % args.out)
    return 0


def cmd_list(args) -> int:
    for name in sorted(GENERATORS):
        g = GENERATORS[name]
        print("%s: %s" % (name, g.doc))
        for d in g.defs:
            rng = "default %g" % d.default if d.default is not None else "drawn %g..%g" % (
                d.lo if d.draw_lo is None else d.draw_lo, d.hi if d.draw_hi is None else d.draw_hi)
            print("  %-18s %-5s valid %g..%g, %s: %s" % (d.name, d.type, d.lo, d.hi, rng, d.doc))
    print("fort add-on (any generator; --param name=value):")
    for name, doc in fort.DOC.items():
        print("  %-14s %s" % (name, doc))
    print("water overlay (any generator; --param name=value):")
    for name, doc in water.DOC.items():
        print("  %-16s %s" % (name, doc))
    print("warp (any generator; --param warp=<strength 0-1 | k=v,...>): %s" % warp.DOC["warp"])
    for name in warp.KEYS:
        print("  %-16s %s" % (name, warp.DOC[name]))
    print("layers (any generator; --param layers=<spec>): %s" % layers_params.DOC["layers"])
    print("  rule keys: %s; global keys: %s" % (", ".join(layers_params.RULE_KEYS), ", ".join(layers_params.GLOBAL_KEYS)))
    for name in sorted(layers_params.FILLS):
        print("  sub-generator %s (rule params as sub.<name>=<v>):" % name)
        for d in layers_params.FILLS[name].SUB_DEFS:
            rng = "default %g" % d.default if d.default is not None else "drawn %g..%g" % (
                d.lo if d.draw_lo is None else d.draw_lo, d.hi if d.draw_hi is None else d.draw_hi)
            print("    %-14s %-5s valid %g..%g, %s: %s" % (d.name, d.type, d.lo, d.hi, rng, d.doc))
    return 0


def cmd_shape(args) -> int:
    from . import shape_sheet
    argv = ["--seeds", str(args.seeds), "--size", str(args.size), "--edge", args.edge, "--out", args.out]
    for it in args.param or []:
        argv += ["--param", it]
    return shape_sheet.main(argv)


def add_parser(sub, positive_float) -> None:
    t = sub.add_parser("town", help="procedural settlement layouts: districts, roads, wall, gates, landmarks")
    ts = t.add_subparsers(dest="town_command")
    ts.required = True
    g = ts.add_parser("generate", help="generate a settlement: settlement.json, preview.png, spec and flowgraph fragments")
    g.add_argument("generator", choices=sorted(GENERATORS))
    g.add_argument("--seed", type=int, default=1)
    g.add_argument("--size", nargs=2, type=positive_float, metavar=("X", "Y"), help="map size in metres (default 1000 1000)")
    g.add_argument("--id", default="town", help="settlement id, the prefix of every emitted feature id (default town)")
    g.add_argument("--param", action="append", metavar="NAME=VALUE", help="override a generator parameter (see `town list`)")
    g.add_argument("--out", help="output directory (default out/town_<id>_<generator>_<seed>; files are overwritten)")
    g.set_defaults(func=cmd_generate)
    c = ts.add_parser("check", help="check settlement.json files: geometry, walls, gates, reachability, determinism")
    c.add_argument("settlement", nargs="+")
    c.set_defaults(func=cmd_check)
    p = ts.add_parser("preview", help="render settlement.json file(s) to one PNG (several are placed side by side)")
    p.add_argument("settlement", nargs="+")
    p.add_argument("--out", required=True)
    p.add_argument("--force", action="store_true", help="overwrite an existing --out file")
    p.set_defaults(func=cmd_preview)
    sh = ts.add_parser("shape", help="contact sheet of shape-field outlines (the organic generator's footprints)")
    sh.add_argument("--seeds", type=int, default=30)
    sh.add_argument("--size", type=positive_float, default=1000.0, help="map side in metres (default 1000)")
    sh.add_argument("--edge", default="wall", choices=["wall", "open", "fade"])
    sh.add_argument("--param", action="append", metavar="NAME=VALUE", help="shape parameter (irregularity, lobes, ribbons, ...)")
    sh.add_argument("--out", default=os.path.join("out", "town_organic", "outlines.png"))
    sh.set_defaults(func=cmd_shape)
    l = ts.add_parser("list", help="list generators and their parameters")
    l.set_defaults(func=cmd_list)
