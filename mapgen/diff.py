"""Build-to-build diff (mapgen diff): compare two build directories and report what changed.
Design: mapgen/DIFF_SPEC.md."""
from __future__ import annotations

import hashlib
import json
import math
import os
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

DIFF_FORMAT = 1
DEFAULT_MAX_CHANGES = 100

HEIGHTMAP_PATH = "export/heightmap.r16"
META_PATH = "export/meta.json"
REPORT_PATH = "report.json"

TEAM = "<team>"
PAIR = "<pair>"

# (metric name, report.json path); "<team>" / "<pair>" are substituted per key.
TEAM_METRICS = [
    ("area_share", ("area_share", TEAM)),
    ("openness", ("openness_by_team", TEAM)),
]
PAIR_METRICS = [
    ("base_time_s", ("base_times_s", PAIR)),
    ("route_constriction", ("route_constriction", PAIR)),
    ("route_openness", ("openness_along_routes", PAIR)),
]
MAP_METRICS = [
    ("contested_share", ("area_share", "contested")),
    ("unreachable_share", ("area_share", "unreachable")),
    ("fair_zone_share", ("fair_zone_share",)),
    ("openness_mean", ("openness_map_mean",)),
]


class DiffError(Exception):
    """Invalid build directory; str(e) is the exact one-line message of DIFF_SPEC section 4."""


# --------------------------------------------------------------------------- files

def check_build_dir(path: str) -> None:
    if not os.path.exists(path):
        raise DiffError("error: directory not found: %s" % path)
    if not os.path.isdir(path):
        raise DiffError("error: not a directory: %s" % path)
    if not (os.path.isfile(os.path.join(path, REPORT_PATH))
            or os.path.isfile(os.path.join(path, "export", "meta.json"))):
        raise DiffError("error: not a mapgen build directory "
                        "(no report.json or export/meta.json): %s" % path)


def list_files(root: str) -> Dict[str, str]:
    """Relative path ('/' separators) -> absolute path, in sorted key order."""
    found = {}  # type: Dict[str, str]
    for d, _dirs, names in os.walk(root):
        for n in names:
            full = os.path.join(d, n)
            found[os.path.relpath(full, root).replace(os.sep, "/")] = full
    return {k: found[k] for k in sorted(found)}


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def file_kind(rel: str) -> str:
    if rel == HEIGHTMAP_PATH:
        return "heightmap"
    if rel.startswith("export/") and rel.endswith(".png"):
        return "raster"
    if rel.endswith(".json"):
        return "json"
    if rel.endswith(".png"):
        return "render"
    return "other"


# --------------------------------------------------------------------------- JSON diff

def _esc(key: str) -> str:
    return key.replace("~", "~0").replace("/", "~1")


def json_same(x: Any, y: Any) -> bool:
    """Strict equality, recursive: same JSON type and value at every level (1 vs 1.0 and true vs 1
    differ; NaN equals NaN; lists compare by position, objects by key)."""
    if type(x) is not type(y):
        return False
    if isinstance(x, dict):
        return set(x) == set(y) and all(json_same(x[k], y[k]) for k in x)
    if isinstance(x, list):
        return len(x) == len(y) and all(json_same(p, q) for p, q in zip(x, y))
    if isinstance(x, float) and math.isnan(x) and math.isnan(y):
        return True
    return x == y


def diff_json(a: Any, b: Any, max_changes: int) -> dict:
    changes = []  # type: List[dict]
    counts = {}  # type: Dict[str, int]

    def emit(change: dict, pat: str) -> None:
        counts[pat] = counts.get(pat, 0) + 1
        changes.append(change)

    def rec(x: Any, y: Any, path: str, pat: str) -> None:
        if isinstance(x, dict) and isinstance(y, dict):
            for k in sorted(set(x) | set(y)):
                p, q = path + "/" + _esc(k), pat + "/" + _esc(k)
                if k not in y:
                    emit({"op": "removed", "path": p, "a": x[k]}, q)
                elif k not in x:
                    emit({"op": "added", "path": p, "b": y[k]}, q)
                else:
                    rec(x[k], y[k], p, q)
        elif isinstance(x, list) and isinstance(y, list):
            n = min(len(x), len(y))
            for i in range(n):
                rec(x[i], y[i], path + "/%d" % i, pat + "/*")
            for i in range(n, len(x)):
                emit({"op": "removed", "path": path + "/%d" % i, "a": x[i]}, pat + "/*")
            for i in range(n, len(y)):
                emit({"op": "added", "path": path + "/%d" % i, "b": y[i]}, pat + "/*")
        elif not json_same(x, y):
            emit({"op": "changed", "path": path, "a": x, "b": y}, pat)

    rec(a, b, "", "")
    return {
        "change_count": len(changes),
        "truncated": len(changes) > max_changes,
        "patterns": [{"pattern": p, "count": counts[p]} for p in sorted(counts)],
        "changes": changes[:max_changes],
        "error": None,
    }


# --------------------------------------------------------------------------- rasters

def _bbox(mask: Any, size: Optional[list], south_first: bool) -> Tuple[dict, Optional[dict]]:
    """Pixel and metre bounding box of the true texels of a 2-D boolean mask."""
    rows = np.flatnonzero(mask.any(axis=1))
    cols = np.flatnonzero(mask.any(axis=0))
    r0, r1, c0, c1 = int(rows[0]), int(rows[-1]), int(cols[0]), int(cols[-1])
    px = {"row_min": r0, "row_max": r1, "col_min": c0, "col_max": c1}
    if size is None:
        return px, None
    h, w = mask.shape
    cx, cz = float(size[0]) / w, float(size[1]) / h
    if south_first:
        z0, z1 = r0 * cz, (r1 + 1) * cz
    else:
        z0, z1 = size[1] - (r1 + 1) * cz, size[1] - r0 * cz
    metres = {"x_min": round(c0 * cx, 2), "x_max": round((c1 + 1) * cx, 2),
              "z_min": round(z0, 2), "z_max": round(z1, 2)}
    return px, metres


def _png_info(path: str) -> dict:
    with Image.open(path) as im:
        return {"mode": im.mode, "width": im.size[0], "height": im.size[1]}


def _valid_res(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v > 0


def _valid_height_max(v: Any) -> bool:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return False
    try:
        return 0.0 < float(v) < float("inf")
    except OverflowError:
        return False


def diff_heightmap(path_a: str, path_b: str, same_hash: bool, size: Optional[list],
                   meta_a: Optional[dict], meta_b: Optional[dict]) -> dict:
    e = {"path": HEIGHTMAP_PATH, "a": None, "b": None, "comparable": True, "reason": None,
         "changed_texels": None, "total_texels": None, "changed_share": None,
         "max_delta_m": None, "bbox_px": None, "bbox_m": None}  # type: Dict[str, Any]
    infos = []  # type: List[dict]
    for side, p, meta in (("a", path_a, meta_a), ("b", path_b, meta_b)):
        res = meta.get("heightmap_res") if isinstance(meta, dict) else None
        hmax = meta.get("height_max") if isinstance(meta, dict) else None
        if not _valid_res(res) or not _valid_height_max(hmax):
            e["comparable"] = False
            e["reason"] = "%s: export/meta.json lacks heightmap_res or height_max" % side
            return e
        info = {"res": res, "height_max": hmax, "bytes": os.path.getsize(p)}
        e[side] = info
        infos.append(info)
        if info["bytes"] != res * res * 2:
            e["comparable"] = False
            e["reason"] = "%s: file has %d bytes, heightmap_res %d needs %d" % (
                side, info["bytes"], res, res * res * 2)
            return e
    ia, ib = infos
    if ia["res"] != ib["res"]:
        e["comparable"] = False
        e["reason"] = "heightmap_res differs: %d vs %d" % (ia["res"], ib["res"])
        return e
    res = ia["res"]
    e["total_texels"] = res * res
    if same_hash and ia["height_max"] == ib["height_max"]:
        e.update(changed_texels=0, changed_share=0.0, max_delta_m=0.0)
        return e
    ha = np.fromfile(path_a, dtype="<u2").reshape(res, res).astype(np.float64) / 65535.0 * ia["height_max"]
    hb = np.fromfile(path_b, dtype="<u2").reshape(res, res).astype(np.float64) / 65535.0 * ib["height_max"]
    changed = ha != hb
    n = int(changed.sum())
    e.update(changed_texels=n, changed_share=n / float(res * res),
             max_delta_m=float(np.abs(hb - ha).max()))
    if n:
        e["bbox_px"], e["bbox_m"] = _bbox(changed, size, True)
    return e


def _relative_message(msg: str, path: str, rel: str) -> str:
    """Replace every spelling of `path` in `msg` by the build-relative path `rel`, longest first.

    Spellings: the path as given, normalised, absolute, each with '/' separators, and each as embedded
    in a repr (Pillow and the OS embed the path escaped once or twice)."""
    forms = set()  # type: set
    for p in (path, os.path.normpath(path), os.path.abspath(path)):
        for q in (p, p.replace("\\", "/")):
            once = repr(q)[1:-1]
            forms.update((q, once, repr(once)[1:-1]))
    for cand in sorted((f for f in forms if f), key=lambda f: (-len(f), f)):
        msg = msg.replace(cand, rel)
    return msg


def _read_error(side: str, ex: Exception, path: str, rel: str) -> str:
    """'<side>: cannot read: <message>' with the path replaced by the build-relative one."""
    return "%s: cannot read: %s" % (side, _relative_message(str(ex), path, rel))


# Modes whose pixel arrays compare numerically as they are; 'P' is converted to RGBA first.
_NUMERIC_MODES = ("1", "L", "LA", "RGB", "RGBA", "I", "I;16")


def diff_raster(rel: str, path_a: str, path_b: str, same_hash: bool, size: Optional[list]) -> dict:
    e = {"path": rel, "a": None, "b": None, "comparable": True, "reason": None,
         "changed_texels": None, "total_texels": None, "changed_share": None,
         "max_abs_delta": None, "pixels_identical": None, "bbox_px": None, "bbox_m": None}  # type: Dict[str, Any]
    for side, p in (("a", path_a), ("b", path_b)):
        try:
            e[side] = _png_info(p)
        except Exception as ex:  # Pillow raises several types for unreadable files
            e["comparable"] = False
            e["reason"] = _read_error(side, ex, p, rel)
            return e
    ia, ib = e["a"], e["b"]
    if ia != ib:
        e["comparable"] = False
        e["reason"] = "format differs: %s %dx%d vs %s %dx%d" % (
            ia["mode"], ia["width"], ia["height"], ib["mode"], ib["width"], ib["height"])
        return e
    total = ia["width"] * ia["height"]
    if same_hash:
        e.update(total_texels=total, changed_texels=0, changed_share=0.0, max_abs_delta=0,
                 pixels_identical=True)
        return e
    if ia["mode"] not in _NUMERIC_MODES and ia["mode"] != "P":
        e["comparable"] = False
        e["reason"] = "mode %s cannot be compared" % ia["mode"]
        return e
    e["total_texels"] = total
    arrays = []
    for side, p in (("a", path_a), ("b", path_b)):
        try:
            with Image.open(p) as im:
                im.load()
                arrays.append(np.asarray(im.convert("RGBA") if im.mode == "P" else im))
        except Exception as ex:
            e["comparable"] = False
            e["reason"] = _read_error(side, ex, p, rel)
            return e
    x, y = arrays
    d = x != y
    if d.ndim == 3:
        d = d.any(axis=2)
    n = int(d.sum())
    e.update(changed_texels=n, changed_share=n / float(total),
             max_abs_delta=int(np.abs(x.astype(np.int64) - y.astype(np.int64)).max()),
             pixels_identical=(n == 0))
    if n:
        e["bbox_px"], e["bbox_m"] = _bbox(d, size, False)
    return e


# --------------------------------------------------------------------------- analysis

def _get(doc: Any, path: Tuple[str, ...]) -> Any:
    for k in path:
        if not isinstance(doc, dict) or k not in doc:
            return None
        doc = doc[k]
    return doc


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _record(a: Any, b: Any) -> dict:
    return {"a": a, "b": b, "delta": (b - a) if _is_num(a) and _is_num(b) else None,
            "changed": not json_same(a, b)}


def _subst(path: Tuple[str, ...], key: str) -> Tuple[str, ...]:
    return tuple(key if p in (TEAM, PAIR) else p for p in path)


def _keys(doc: Any) -> List[str]:
    return list(doc) if isinstance(doc, dict) else []


def _objective_list(report: dict) -> List[dict]:
    objs = report.get("objectives")
    return ([o for o in objs if isinstance(o, dict) and isinstance(o.get("id"), str)]
            if isinstance(objs, list) else [])


def _objectives_by_id(report: dict, side: str, notes: List[str]) -> Tuple[Dict[str, dict], List[str]]:
    """First entry per id and the ids in first-appearance order; one note per repeated id."""
    by = {}  # type: Dict[str, dict]
    repeated = []  # type: List[str]
    for o in _objective_list(report):
        if o["id"] not in by:
            by[o["id"]] = o
        elif o["id"] not in repeated:
            repeated.append(o["id"])
    for oid in repeated:
        notes.append("analysis: objective id %s occurs more than once in %s; first entry used" % (oid, side))
    return by, list(by)


def diff_analysis(report_a: dict, report_b: dict, notes: Optional[List[str]] = None) -> dict:
    teams = sorted(set(_keys(report_a.get("teams"))) | set(_keys(report_b.get("teams"))))
    pairs = sorted(set(_keys(report_a.get("base_times_s"))) | set(_keys(report_b.get("base_times_s"))))

    notes = notes if notes is not None else []
    by_a, order_a = _objectives_by_id(report_a, "a", notes)
    by_b, order_b = _objectives_by_id(report_b, "b", notes)
    ids = order_a + [i for i in order_b if i not in by_a]

    objectives = []
    for oid in ids:
        xa, xb = by_a.get(oid), by_b.get(oid)
        src = xb if xb is not None else xa
        times_a = xa.get("times") if xa is not None else None
        times_b = xb.get("times") if xb is not None else None
        delta = {}  # type: Dict[str, Optional[float]]
        for t in sorted(set(_keys(times_a)) | set(_keys(times_b))):
            va = times_a.get(t) if isinstance(times_a, dict) else None
            vb = times_b.get(t) if isinstance(times_b, dict) else None
            delta[t] = (vb - va) if _is_num(va) and _is_num(vb) else None
        spread_a = xa.get("spread") if xa is not None else None
        spread_b = xb.get("spread") if xb is not None else None
        flag_a = xa.get("flag") if xa is not None else None
        flag_b = xb.get("flag") if xb is not None else None
        objectives.append({
            "id": oid, "type": src.get("type"), "owner": src.get("owner"),
            "in_a": xa is not None, "in_b": xb is not None,
            "times_a": times_a, "times_b": times_b, "delta_s": delta,
            "spread": _record(spread_a, spread_b),
            "flag_a": flag_a, "flag_b": flag_b,
            "changed": (xa is None or xb is None or not json_same(times_a, times_b)
                        or not json_same(spread_a, spread_b) or not json_same(flag_a, flag_b)),
        })

    per_team = {t: {name: _record(_get(report_a, _subst(p, t)), _get(report_b, _subst(p, t)))
                    for name, p in TEAM_METRICS} for t in teams}
    per_pair = {k: {name: _record(_get(report_a, _subst(p, k)), _get(report_b, _subst(p, k)))
                    for name, p in PAIR_METRICS} for k in pairs}
    map_vals = {name: _record(_get(report_a, p), _get(report_b, p)) for name, p in MAP_METRICS}

    warn_a = report_a.get("warnings") if isinstance(report_a.get("warnings"), list) else []
    warn_b = report_b.get("warnings") if isinstance(report_b.get("warnings"), list) else []
    map_vals["warning_count"] = _record(len(warn_a), len(warn_b))
    return {
        "teams": teams,
        "objectives": objectives,
        "per_team": per_team,
        "per_pair": per_pair,
        "map": map_vals,
        "warnings": {"added": [w for w in warn_b if w not in warn_a],
                     "removed": [w for w in warn_a if w not in warn_b]},
    }


# --------------------------------------------------------------------------- whole report

class _JsonCache:
    """Parses each JSON file of each build at most once; remembers load errors."""

    def __init__(self, files_a: Dict[str, str], files_b: Dict[str, str]) -> None:
        self._files = {"a": files_a, "b": files_b}
        self._cache = {}  # type: Dict[Tuple[str, str], Tuple[Any, Optional[str]]]

    def load(self, side: str, rel: str) -> Tuple[Any, Optional[str]]:
        key = (side, rel)
        if key not in self._cache:
            path = self._files[side].get(rel)
            if path is None:
                self._cache[key] = (None, "missing")
            else:
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        self._cache[key] = (json.load(f), None)
                except (ValueError, OSError) as ex:
                    self._cache[key] = (None, _relative_message(str(ex), path, rel))
        return self._cache[key]

    def doc(self, side: str, rel: str) -> Any:
        value, err = self.load(side, rel)
        return None if err else value


def _map_name(meta: Any, report: Any) -> Optional[str]:
    for src in (report, meta):
        if isinstance(src, dict) and isinstance(src.get("name"), str):
            return src["name"]
    return None


def _map_size(meta: Any, report: Any) -> Optional[list]:
    for src in (meta, report):
        s = src.get("size") if isinstance(src, dict) else None
        if isinstance(s, list) and len(s) == 2 and all(_is_num(v) for v in s):
            return s
    return None


def diff_builds(a: str, b: str, max_changes: int = DEFAULT_MAX_CHANGES) -> dict:
    check_build_dir(a)
    check_build_dir(b)
    files_a, files_b = list_files(a), list_files(b)
    both = [k for k in files_a if k in files_b]
    hash_a = {k: sha256_file(files_a[k]) for k in both}
    hash_b = {k: sha256_file(files_b[k]) for k in both}
    changed = [k for k in both if hash_a[k] != hash_b[k]]
    added = [k for k in files_b if k not in files_a]
    removed = [k for k in files_a if k not in files_b]

    cache = _JsonCache(files_a, files_b)
    meta_a, meta_b = cache.doc("a", META_PATH), cache.doc("b", META_PATH)
    rep_a, rep_b = cache.doc("a", REPORT_PATH), cache.doc("b", REPORT_PATH)
    size = _map_size(meta_b, rep_b)

    notes = []  # type: List[str]
    heightmap = None
    if HEIGHTMAP_PATH in both:
        heightmap = diff_heightmap(files_a[HEIGHTMAP_PATH], files_b[HEIGHTMAP_PATH],
                                   hash_a[HEIGHTMAP_PATH] == hash_b[HEIGHTMAP_PATH], size,
                                   meta_a if isinstance(meta_a, dict) else None,
                                   meta_b if isinstance(meta_b, dict) else None)
    else:
        notes.append("heightmap: export/heightmap.r16 is not in both builds")

    rasters = [diff_raster(k, files_a[k], files_b[k], hash_a[k] == hash_b[k], size)
               for k in both if file_kind(k) == "raster"]

    json_changes = {}  # type: Dict[str, dict]
    for k in changed:
        if file_kind(k) != "json":
            continue
        va, ea = cache.load("a", k)
        vb, eb = cache.load("b", k)
        if ea is not None or eb is not None:
            err = "a: %s" % ea if ea is not None else "b: %s" % eb
            json_changes[k] = {"change_count": None, "truncated": False, "patterns": [],
                               "changes": [], "error": err}
        else:
            json_changes[k] = diff_json(va, vb, max_changes)

    analysis = None
    if isinstance(rep_a, dict) and isinstance(rep_b, dict):
        analysis = diff_analysis(rep_a, rep_b, notes)
    else:
        notes.append("analysis: report.json is not readable in both builds")

    return {
        "diff_format": DIFF_FORMAT,
        "a": a,
        "b": b,
        "name_a": _map_name(meta_a, rep_a),
        "name_b": _map_name(meta_b, rep_b),
        "identical": not (added or removed or changed),
        "files": {"added": added, "removed": removed, "changed": changed,
                  "unchanged_count": len(both) - len(changed)},
        "heightmap": heightmap,
        "rasters": rasters,
        "json": json_changes,
        "analysis": analysis,
        "notes": notes,
    }


def _finite(v: Any) -> Any:
    """Replace non-finite floats by the strings 'NaN', 'Infinity', '-Infinity'."""
    if isinstance(v, float) and not math.isfinite(v):
        return "NaN" if math.isnan(v) else ("Infinity" if v > 0 else "-Infinity")
    if isinstance(v, dict):
        return {k: _finite(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_finite(x) for x in v]
    return v


def format_json(report: dict) -> str:
    return json.dumps(_finite(report), indent=1, sort_keys=True, ensure_ascii=True,
                      allow_nan=False) + "\n"


# --------------------------------------------------------------------------- text output

def _fmt(fmt: str, v: Any) -> str:
    """`fmt % v`, except that a value printing as zero prints as positive zero."""
    s = fmt % v
    return fmt % 0.0 if float(s) == 0.0 else s


def _v(v: Any) -> str:
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return "%d" % v
    if isinstance(v, float):
        return _fmt("%.6g", v)
    if isinstance(v, str):
        return v
    return json.dumps(v, sort_keys=True)


def _rec_text(name: str, r: dict) -> str:
    s = "%s %s -> %s" % (name, _v(r["a"]), _v(r["b"]))
    if r["delta"] is not None:
        s += " (%s)" % _fmt("%+.6g", r["delta"])
    return s


def _box_text(box: Optional[dict]) -> str:
    if not box:
        return ""
    return ", x %s..%s z %s..%s" % tuple(_fmt("%g", box[k]) for k in ("x_min", "x_max", "z_min", "z_max"))


def _pct(share: float) -> str:
    return "%.4f%%" % (100.0 * share)


def format_text(report: dict) -> str:
    out = []  # type: List[str]
    out.append("mapgen diff")
    out.append("  a: %s" % report["a"])
    out.append("  b: %s" % report["b"])
    out.append("identical: %s" % ("yes" if report["identical"] else "no"))
    f = report["files"]
    out.append("files: %d changed, %d added, %d removed, %d unchanged" % (
        len(f["changed"]), len(f["added"]), len(f["removed"]), f["unchanged_count"]))
    for key in ("changed", "added", "removed"):
        for p in f[key]:
            out.append("  %-8s %s" % (key, p))

    h = report["heightmap"]
    if h is None:
        out.append("heightmap: not in both builds")
    elif not h["comparable"]:
        out.append("heightmap: not comparable: %s" % h["reason"])
    else:
        out.append("heightmap: %s of %d texels changed, max delta %.2f m%s" % (
            _pct(h["changed_share"]), h["total_texels"], h["max_delta_m"], _box_text(h["bbox_m"])))

    rasters = report["rasters"]
    file_changed = set(f["changed"])
    listed = [r for r in rasters
              if not r["comparable"] or r["changed_texels"] or r["path"] in file_changed]
    out.append("rasters: %d of %d changed" % (len(listed), len(rasters)))
    for r in listed:
        if not r["comparable"]:
            out.append("  %s  not comparable: %s" % (r["path"], r["reason"]))
        elif not r["changed_texels"]:
            out.append("  %s  pixels identical, file bytes differ" % r["path"])
        else:
            out.append("  %s  %s (%d texels), max delta %d%s" % (
                r["path"], _pct(r["changed_share"]), r["changed_texels"], r["max_abs_delta"],
                _box_text(r["bbox_m"])))

    js = report["json"]
    out.append("json: %d file(s) changed" % len(js))
    for path in sorted(js):
        e = js[path]
        if e["error"] is not None:
            out.append("  %s: unreadable: %s" % (path, e["error"]))
            continue
        out.append("  %s: %d change(s)" % (path, e["change_count"]))
        for pt in e["patterns"][:10]:
            out.append("    %s  x%d" % (pt["pattern"], pt["count"]))
        if len(e["patterns"]) > 10:
            out.append("    ... %d more pattern(s)" % (len(e["patterns"]) - 10))

    an = report["analysis"]
    if an is not None:
        objs = an["objectives"]
        changed = [o for o in objs if o["changed"]]
        out.append("objectives: %d of %d changed (time delta b - a, seconds)" % (len(changed), len(objs)))
        for o in changed:
            if not o["in_a"] or not o["in_b"]:
                out.append("  %s  only in %s" % (o["id"], "a" if o["in_a"] else "b"))
                continue
            parts = [o["id"]]
            for t in sorted(o["delta_s"]):
                d = o["delta_s"][t]
                parts.append("%s %s" % (t, "-" if d is None else _fmt("%+.1f", d)))
            parts.append(_rec_text("spread", o["spread"]))
            if not json_same(o["flag_a"], o["flag_b"]):
                parts.append("flag %s -> %s" % (o["flag_a"] or "ok", o["flag_b"] or "ok"))
            out.append("  " + "  ".join(parts))
        out.append("per team:")
        for t in an["teams"]:
            m = an["per_team"][t]
            out.append("  " + "  ".join([t] + [_rec_text(k, m[k]) for k in sorted(m)]))
        out.append("per pair:")
        for k in sorted(an["per_pair"]):
            m = an["per_pair"][k]
            out.append("  " + "  ".join([k] + [_rec_text(n, m[n]) for n in sorted(m)]))
        m = an["map"]
        out.append("map: " + "  ".join(_rec_text(n, m[n]) for n in sorted(m)))
        for w in an["warnings"]["added"]:
            out.append("warning added: %s" % w)
        for w in an["warnings"]["removed"]:
            out.append("warning removed: %s" % w)
    for n in report["notes"]:
        out.append("note: %s" % n)
    return ("\n".join(out) + "\n").encode("ascii", "backslashreplace").decode("ascii")
