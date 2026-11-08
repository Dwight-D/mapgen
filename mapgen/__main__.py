"""mapgen command line: build | validate | probe | types | palette."""
from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from . import schema


def _fmt_cost(c) -> str:
    return "inf" if c == float("inf") else "%g" % c


def cmd_validate(args) -> int:
    try:
        spec = schema.load_spec(args.spec, getattr(args, "palette", None))
    except FileNotFoundError:
        print("error: file not found: %s" % args.spec)
        return 1
    except schema.SpecError as e:
        print("%d error(s) in %s:" % (len(e.errors), args.spec))
        for line in e.errors:
            print("  " + line)
        return 1
    print("OK: %s (%gx%g m, %d teams, %d regions, %d lines, %d crossings, %d pois)" % (
        spec.name, spec.size[0], spec.size[1], len(spec.teams), len(spec.regions), len(spec.lines),
        len(spec.crossings), len(spec.pois)))
    return 0


def cmd_types(args) -> int:
    t = schema.load_types()
    print("regions:")
    print("  %-10s %5s  %-5s %-8s %6s  %-11s %s" % ("type", "cost", "los", "splat", "height", "height_mode", "edge"))
    for k, e in t.regions.items():
        print("  %-10s %5s  %-5s %-8s %6s  %-11s %s" % (
            k, _fmt_cost(e["cost"]), str(e.get("los", False)).lower(), e["splat"],
            e.get("height", "-"), e.get("height_mode", "-"), e.get("edge", "-")))
    print("lines:")
    print("  %-10s %5s  %-5s %-8s %6s  %-13s %-8s %s" % ("type", "cost", "los", "splat", "width", "height_mode", "flatten", "crossing"))
    for k, e in t.lines.items():
        print("  %-10s %5s  %-5s %-8s %6s  %-13s %-8s %s" % (
            k, _fmt_cost(e["cost"]), str(e.get("los", False)).lower(), e["splat"], e["width"],
            e.get("height_mode", "-"), e.get("flatten", "-"), e.get("crossing", "-")))
    print("crossings:")
    print("  %-10s %5s  %6s" % ("type", "cost", "r"))
    for k, e in t.crossings.items():
        print("  %-10s %5s  %6s" % (k, _fmt_cost(e["cost"]), e["r"]))
    print("pois:")
    print("  %-14s %-9s %5s  %s" % ("type", "glyph", "r", "objective"))
    for k, e in t.pois.items():
        print("  %-14s %-9s %5s  %s" % (k, e["glyph"], e["r"], str(e["objective"]).lower()))
    print("splat_layers: %s" % ", ".join(t.splat_layers))
    return 0


def cmd_palette(args) -> int:
    """Print the region-fill lightness matrix (CIE L*) and check the greyscale legibility rule."""
    from . import palette_check

    try:
        pal = schema.load_palette(args.palette)
    except FileNotFoundError:
        print("error: file not found: %s" % args.palette)
        return 1
    except schema.SpecError as e:
        for line in e.errors:
            print(line)
        return 1
    return palette_check.report(pal)


def cmd_probe(args) -> int:
    """Per point: each team's travel time, the spread, the cost cell and what lies there. Writes no files."""
    from . import build as buildmod
    from .analysis import probe_point
    from .render import fmt_time

    pts = []
    toks = list(args.points)
    try:
        spec, geoms, hm, an = buildmod.prepare(args.spec)
    except FileNotFoundError:
        print("error: file not found: %s" % args.spec)
        return 1
    except schema.SpecError as e:
        print("%d error(s) in %s:" % (len(e.errors), args.spec))
        for line in e.errors:
            print("  " + line)
        return 1
    poi_at = {p.id: p.at for p in spec.all_pois()}
    i = 0
    while i < len(toks):
        t = toks[i]
        if t in poi_at:
            pts.append(poi_at[t])
            i += 1
            continue
        try:
            pts.append((float(t), float(toks[i + 1])))
        except (ValueError, IndexError):
            print("error: expected x y pairs or POI ids, got %r" % " ".join(toks[i:i + 2]))
            return 1
        i += 2
    for x, y in pts:
        r = probe_point(spec, geoms, an, x, y)
        times = "  ".join("%s %s" % (t, fmt_time(r["times"][t])) for t in an.teams)
        spread = "%.0f%%" % (100 * r["spread"]) if r["spread"] is not None else "-"
        cost = "inf" if r["cost"] == float("inf") else "%.2f" % r["cost"]
        print("(%g, %g)  %s  spread %s  cost %s%s" % (x, y, times, spread, cost, "  fair" if r["fair"] else ""))
        print("    region: %s | lines: %s | crossings: %s" % (
            r["region"] or "-", ", ".join(r["lines"]) or "-", ", ".join(r["crossings"]) or "-"))
    return 0


def cmd_build(args) -> int:
    import glob
    import os

    from . import build as buildmod

    if os.path.isdir(args.spec):
        specs = sorted(glob.glob(os.path.join(args.spec, "*.yaml")))
        if not specs:
            print("error: no *.yaml files in %s" % args.spec)
            return 1
    else:
        specs = [args.spec]
    rc = 0
    for path in specs:
        try:
            buildmod.build(path, args.out, args.only, palette_path=args.palette)
        except FileNotFoundError:
            print("error: file not found: %s" % path)
            rc = 1
        except schema.SpecError as e:
            print("%d error(s) in %s:" % (len(e.errors), path))
            for line in e.errors:
                print("  " + line)
            rc = 1
    return rc


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="mapgen", description="Map spec -> 2D map, analysis and terrain primitives.")
    sub = p.add_subparsers(dest="command")
    sub.required = True

    b = sub.add_parser("build", help="build a spec (or every *.yaml in a directory)")
    b.add_argument("spec")
    b.add_argument("--out", default="out")
    b.add_argument("--only", choices=["render", "analyze", "export"])
    b.add_argument("--palette", help="palette file; keys it omits fall back to mapgen/palette.yaml")
    b.set_defaults(func=cmd_build)

    v = sub.add_parser("validate", help="validate a spec and report every error")
    v.add_argument("spec")
    v.add_argument("--palette", help="palette file to validate against")
    v.set_defaults(func=cmd_validate)

    pr = sub.add_parser("probe", help="travel times, spread, cost and features at points (writes no files)")
    pr.add_argument("spec")
    pr.add_argument("points", nargs="+", help="x y pairs in metres; a POI id may stand for its position")
    pr.set_defaults(func=cmd_probe)

    t = sub.add_parser("types", help="print the type table")
    t.set_defaults(func=cmd_types)

    pc = sub.add_parser("palette", help="check the palette: region-fill lightness (L*) matrix, greyscale rule")
    pc.add_argument("--palette", help="palette file; keys it omits fall back to mapgen/palette.yaml")
    pc.set_defaults(func=cmd_palette)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
