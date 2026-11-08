"""Pipeline orchestration for one spec (spec 6) and report writing (6.6)."""
from __future__ import annotations

import json
import math
import os
import time
from typing import Callable, Dict, List, Optional

from . import analysis, export, geometry, height, render, schema
from .analysis import AnalysisResult
from .render import fmt_time


def _pct(v: float) -> str:
    return "%.1f%%" % (100.0 * v)


def _clean(o):
    """JSON-safe copy: inf/nan -> None."""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    return o


def report_data(spec: schema.MapSpec, an: AnalysisResult, files: List[str]) -> dict:
    return _clean({
        "name": spec.name,
        "size": [spec.size[0], spec.size[1]],
        "seed": spec.seed,
        "brief": spec.brief.strip(),
        "warnings": list(an.warnings),
        "teams": {t.id: {"name": t.name, "base": [t.base[0], t.base[1]]} for t in spec.teams.values()},
        "base_times_s": dict(an.base_times),
        "area_share": dict(an.shares),
        "objectives": an.objectives,
        "balance_tolerance": spec.analysis.balance_tolerance,
        "chokepoints": [{"rank": c["rank"], "x": c["x"], "y": c["y"], "count": c["count"],
                         "constriction": c["constriction"], "score": c["score"], "near": c["near"]}
                        for c in an.chokepoints],
        "route_constriction": dict(an.route_constriction),
        "openness_along_routes": dict(an.path_openness),
        "openness_map_mean": an.openness_mean,
        "openness_by_team": dict(an.team_openness),
        "fair_zone_share": an.fair_share,
        "auto_crossings": [{"id": c.id, "type": c.type, "x": c.at[0], "y": c.at[1],
                            "r": None if c.path is not None else c.r, "width": c.width if c.path is not None else None,
                            "crosses": c.note} for c in spec.crossings if c.auto],
        "files": files,
    })


def report_markdown(spec: schema.MapSpec, an: AnalysisResult, files: List[str]) -> str:
    L: List[str] = []
    L.append("# %s" % spec.name)
    L.append("")
    L.append("- size: %g x %g m, seed %d, analysis cell %g m, speed %g m/s" % (
        spec.size[0], spec.size[1], spec.seed, spec.analysis.cell, spec.analysis.speed))
    if spec.brief.strip():
        L.append("")
        for line in spec.brief.strip().splitlines():
            L.append("> " + line)
    L.append("")
    L.append("## Warnings")
    L.append("")
    if an.warnings:
        for w in an.warnings:
            L.append("- " + w)
    else:
        L.append("None.")
    L.append("")
    L.append("## Teams")
    L.append("")
    L.append("| team | name | base (x, y) | area reached first |")
    L.append("|---|---|---|---|")
    for t in spec.teams.values():
        L.append("| %s | %s | (%g, %g) | %s |" % (t.id, t.name, t.base[0], t.base[1], _pct(an.shares[t.id])))
    L.append("")
    L.append("Contested: %s. Unreachable: %s." % (_pct(an.shares["contested"]), _pct(an.shares["unreachable"])))
    L.append("")
    L.append("Base-to-base travel time:")
    L.append("")
    for k, v in an.base_times.items():
        a, b = k.split("-")
        L.append("- %s to %s: %s" % (a, b, fmt_time(v) if v is not None else "unreachable"))
    L.append("")
    L.append("## Objectives")
    L.append("")
    head = "| id | type | owner | " + " | ".join("t(%s)" % t for t in an.teams) + " | spread | flag |"
    L.append(head)
    L.append("|" + "---|" * (5 + len(an.teams)))
    for o in an.objectives:
        times = " | ".join(fmt_time(o["times"][t]) for t in an.teams)
        spread = "%.0f%%" % (100 * o["spread"]) if o["spread"] is not None else "-"
        L.append("| %s | %s | %s | %s | %s | %s |" % (o["id"], o["type"], o["owner"] or "-", times, spread,
                                                     o["flag"] or "ok"))
    L.append("")
    L.append("Times are m:ss from each team base. Neutral objectives flag IMBALANCED when spread > %.0f%%." % (
        100 * spec.analysis.balance_tolerance))
    L.append("")
    L.append("Fair zone (reachable by all teams within that spread, `overlay_balance.png`): %s of the map." % _pct(
        an.fair_share))
    L.append("")
    L.append("## Chokepoints")
    L.append("")
    if an.chokepoints:
        L.append("| # | x | y | paths | constriction | score | nearest feature |")
        L.append("|---|---|---|---|---|---|---|")
        for c in an.chokepoints:
            L.append("| %d | %d | %d | %d | %.2f | %.1f | %s |" % (
                c["rank"], round(c["x"]), round(c["y"]), c["count"], c["constriction"], c["score"], c["near"]))
    else:
        L.append("Open ground - no chokepoint.")
    L.append("")
    L.append("Score = paths x constriction (fraction of cells within 120 m that are impassable or cost >= 2).")
    for k, v in an.route_constriction.items():
        if v <= 0:
            L.append("- %s: open ground - no chokepoint on the route" % k.replace("-", " to "))
    L.append("")
    autos = [c for c in spec.crossings if c.auto]
    if autos:
        L.append("## Automatic crossings")
        L.append("")
        L.append("| id | type | x | y | crosses |")
        L.append("|---|---|---|---|---|")
        for c in autos:
            L.append("| %s | %s%s | %d | %d | %s |" % (c.id, c.type, " (band)" if c.path is not None else "",
                                                       round(c.at[0]), round(c.at[1]), c.note))
        L.append("")
    L.append("## Openness")
    L.append("")
    L.append("1.0 = fully open, 0.0 = fully covered.")
    L.append("")
    L.append("- map-wide mean: %.2f" % an.openness_mean)
    for tid, v in an.team_openness.items():
        L.append("- territory of %s: %s" % (tid, "%.2f" % v if v == v else "-"))
    for k, v in an.path_openness.items():
        L.append("- route %s: %.2f" % (k.replace("-", " to "), v))
    L.append("")
    L.append("## Files")
    L.append("")
    for f in files:
        L.append("- " + f)
    L.append("")
    return "\n".join(L)


def _list_files(out_dir: str) -> List[str]:
    res = []
    for root, _, names in os.walk(out_dir):
        for n in names:
            res.append(os.path.relpath(os.path.join(root, n), out_dir).replace(os.sep, "/"))
    return sorted(res)


def prepare(spec_path: str, palette_path: Optional[str] = None):
    """Load a spec and run geometry, heightmap and analysis (no files written). Returns (spec, geoms, hm, an)."""
    spec = schema.load_spec(spec_path, palette_path)
    geoms = geometry.build_geometries(spec)
    hm = height.synthesize(spec, geoms)
    an = analysis.analyze(spec, geoms, hm)
    return spec, geoms, hm, an


def build(spec_path: str, out_root: str = "out", only: Optional[str] = None,
          log: Callable[[str], None] = print, palette_path: Optional[str] = None) -> str:
    """Build one spec. Raises schema.SpecError on invalid specs (before writing anything). Returns the out dir."""
    spec = schema.load_spec(spec_path, palette_path)
    out_dir = os.path.join(out_root, spec.name)
    os.makedirs(out_dir, exist_ok=True)
    t_all = time.time()

    def stage(name, fn):
        t0 = time.time()
        r = fn()
        log("  %-9s %5.1fs" % (name, time.time() - t0))
        return r

    log("%s" % spec.name)
    geoms = stage("geometry", lambda: geometry.build_geometries(spec))
    hm = stage("heightmap", lambda: height.synthesize(spec, geoms))
    an = stage("analysis", lambda: analysis.analyze(spec, geoms, hm))
    if only in (None, "render"):
        stage("render", lambda: render.render_all(spec, geoms, hm, an, out_dir))
    if only in (None, "export"):
        stage("export", lambda: export.export_all(spec, geoms, hm, an, out_dir))
    if only in (None, "analyze"):
        def write_report():
            files = _list_files(out_dir)
            for f in ("report.md", "report.json"):
                if f not in files:
                    files.append(f)
            files = sorted(files)
            with open(os.path.join(out_dir, "report.md"), "w", encoding="utf-8", newline="\n") as f:
                f.write(report_markdown(spec, an, files))
            with open(os.path.join(out_dir, "report.json"), "w", encoding="utf-8", newline="\n") as f:
                json.dump(report_data(spec, an, files), f, indent=1)
                f.write("\n")
        stage("report", write_report)
    for w in an.warnings:
        log("  warning: " + w)
    log("  done in %.1fs -> %s" % (time.time() - t_all, out_dir))
    return out_dir
