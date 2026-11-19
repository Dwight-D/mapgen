"""mapgen command line: build | validate | probe | diff | flowgraph | types | palette."""
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


def cmd_flowgraph_validate(args) -> int:
    from . import flowgraph

    try:
        g = flowgraph.load_flowgraph(args.graph)
    except FileNotFoundError:
        print("error: file not found: %s" % args.graph)
        return 1
    except schema.SpecError as e:
        print("%d error(s) in %s:" % (len(e.errors), args.graph))
        for line in e.errors:
            print("  " + line)
        return 1
    print("OK: %s (%d nodes, %d edges)" % (g.name, len(g.nodes), len(g.edges)))
    return 0


def cmd_flowgraph_check(args) -> int:
    from . import flowgraph, flowgraph_check

    g = None
    for path, loader in ((args.graph, flowgraph.load_flowgraph), (args.spec, schema.load_spec)):
        try:
            obj = loader(path)
        except FileNotFoundError:
            print("error: file not found: %s" % path)
            return 1
        except schema.SpecError as e:
            print("%d error(s) in %s:" % (len(e.errors), path))
            for line in e.errors:
                print("  " + line)
            return 1
        if g is None:
            g = obj
    res = flowgraph_check.check(g, obj, terrain=args.terrain)
    for line in res.lines(args.strict):
        print(line)
    return 1 if res.failed(args.strict) else 0


def cmd_flowgraph_scaffold(args) -> int:
    import os

    from . import flowgraph, flowgraph_scaffold

    try:
        g = flowgraph.load_flowgraph(args.graph)
    except FileNotFoundError:
        print("error: file not found: %s" % args.graph)
        return 1
    except schema.SpecError as e:
        print("%d error(s) in %s:" % (len(e.errors), args.graph))
        for line in e.errors:
            print("  " + line)
        return 1
    size = tuple(args.size) if args.size else flowgraph_scaffold.DEFAULT_SIZE
    try:
        text = flowgraph_scaffold.scaffold(g, size)
    except flowgraph_scaffold.ScaffoldError as e:
        print("%d error(s) in %s:" % (len(e.errors), args.graph))
        for line in e.errors:
            print("  " + line)
        return 1
    if not args.out:
        sys.stdout.buffer.write(text.encode("utf-8"))
        return 0
    if os.path.exists(args.out) and not args.force:
        print("error: %s exists (use --force to overwrite)" % args.out)
        return 1
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print("wrote %s" % args.out)
    return 0


def cmd_flowgraph_edit(args) -> int:
    import os

    from . import flowgraph, flowgraph_editor

    if args.new:
        if os.path.exists(args.graph):
            print("error: %s exists (--new starts a graph that does not exist yet)" % args.graph)
            return 1
        if not os.path.isdir(os.path.dirname(os.path.abspath(args.graph))):
            print("error: folder does not exist: %s" % os.path.dirname(os.path.abspath(args.graph)))
            return 1
    else:
        try:
            flowgraph.load_flowgraph(args.graph)
        except FileNotFoundError:
            print("error: file not found: %s (use --new to start a graph)" % args.graph)
            return 1
        except schema.SpecError as e:
            print("%d error(s) in %s:" % (len(e.errors), args.graph))
            for line in e.errors:
                print("  " + line)
            return 1
    return flowgraph_editor.serve(args.graph, args.port, not args.no_open)


def cmd_flowgraph_preview(args) -> int:
    import os

    from . import flowgraph, flowgraph_preview

    try:
        g = flowgraph.load_flowgraph(args.graph)
    except FileNotFoundError:
        print("error: file not found: %s" % args.graph)
        return 1
    except schema.SpecError as e:
        print("%d error(s) in %s:" % (len(e.errors), args.graph))
        for line in e.errors:
            print("  " + line)
        return 1
    default = not args.out
    out = args.out or os.path.join("out", "flowgraph_%s.html" % g.name)
    if os.path.exists(out) and not args.force and not default:
        print("error: %s exists (use --force to overwrite)" % out)
        return 1
    d = os.path.dirname(out)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(flowgraph_preview.render(g))
    print("wrote %s" % out)
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


def _positive_float(s: str) -> float:
    try:
        x = float(s)
    except ValueError:
        x = -1.0
    if not x > 0 or x == float("inf"):
        raise argparse.ArgumentTypeError("expected a number > 0, got %r" % s)
    return x


def _non_negative_int(s: str) -> int:
    try:
        n = int(s)
    except ValueError:
        n = -1
    if n < 0:
        raise argparse.ArgumentTypeError("expected an integer >= 0, got %r" % s)
    return n


def cmd_diff(args) -> int:
    """Compare two build directories; JSON (or --text) on stdout. Writes no files."""
    from . import diff as diffmod

    try:
        r = diffmod.diff_builds(args.build_a, args.build_b, args.max_changes)
    except diffmod.DiffError as e:
        print(str(e))
        return 1
    _write_bytes(diffmod.format_text(r) if args.text else diffmod.format_json(r))
    return 0


def _write_bytes(text: str) -> None:
    """Write text to stdout as UTF-8 with LF line endings on every platform."""
    data = text.encode("utf-8")
    sys.stdout.flush()
    buf = getattr(sys.stdout, "buffer", None)
    if buf is None:
        sys.stdout.write(text)
        return
    buf.write(data)
    buf.flush()


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
    from . import __version__
    p.add_argument("--version", action="version", version="mapgen %s (spec format versions: %s)" % (
        __version__, ", ".join(str(v) for v in schema.SPEC_FORMAT_VERSIONS)))
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

    df = sub.add_parser("diff", help="compare two build directories (JSON on stdout; --text for a summary)")
    df.add_argument("build_a")
    df.add_argument("build_b")
    df.add_argument("--text", action="store_true", help="print a human-readable summary instead of JSON")
    df.add_argument("--max-changes", type=_non_negative_int, default=100,
                    help="change entries listed per JSON file (default 100)")
    df.set_defaults(func=cmd_diff)

    fg = sub.add_parser("flowgraph", help="flowgraph (topology precursor) commands")
    fgs = fg.add_subparsers(dest="flowgraph_command")
    fgs.required = True
    fgv = fgs.add_parser("validate", help="validate a *.flow.yaml graph and report every error")
    fgv.add_argument("graph")
    fgv.set_defaults(func=cmd_flowgraph_validate)
    fgsc = fgs.add_parser("scaffold", help="draft a mapgen spec from a graph (stdout, or --out <file>)")
    fgsc.add_argument("graph")
    fgsc.add_argument("--out", help="write the draft here instead of stdout")
    fgsc.add_argument("--size", nargs=2, type=_positive_float, metavar=("X", "Y"), help="map size in metres (default 3000 3000)")
    fgsc.add_argument("--force", action="store_true", help="overwrite an existing --out file")
    fgsc.set_defaults(func=cmd_flowgraph_scaffold)
    fgp = fgs.add_parser("preview", help="write a self-contained HTML/SVG preview of a graph (default out/flowgraph_<name>.html)")
    fgp.add_argument("graph")
    fgp.add_argument("--out", help="HTML file to write (default out/flowgraph_<name>.html, overwritten freely)")
    fgp.add_argument("--force", action="store_true", help="overwrite an existing --out file")
    fgp.set_defaults(func=cmd_flowgraph_preview)
    fge = fgs.add_parser("edit", help="edit a graph in the browser (local server on 127.0.0.1; Save writes canonical YAML)")
    fge.add_argument("graph")
    fge.add_argument("--new", action="store_true", help="start from a minimal two-spawn graph; <graph> is created on first Save")
    fge.add_argument("--port", type=int, default=0, help="port on 127.0.0.1 (default: any free port)")
    fge.add_argument("--no-open", action="store_true", help="do not open a browser")
    fge.set_defaults(func=cmd_flowgraph_edit)
    fgc = fgs.add_parser("check", help="check a spec against a graph: missing nodes/edges (--strict: extras too)")
    fgc.add_argument("graph")
    fgc.add_argument("spec")
    fgc.add_argument("--strict", action="store_true", help="also fail on extra edges and extra nodes")
    fgc.add_argument("--terrain", action="store_true",
                     help="also check the walkable terrain connects exactly the graph's nodes (terrain-missing/-extra edges)")
    fgc.set_defaults(func=cmd_flowgraph_check)

    t = sub.add_parser("types", help="print the type table")
    t.set_defaults(func=cmd_types)

    pc = sub.add_parser("palette", help="check the palette: region-fill lightness (L*) matrix, greyscale rule")
    pc.add_argument("--palette", help="palette file; keys it omits fall back to mapgen/palette.yaml")
    pc.set_defaults(func=cmd_palette)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
