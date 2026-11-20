"""Spec editor, slice 7: coarse analysis preview and the build job. Design: docs/investigations/2026-11-19-map-editor-design.md
sections 6 and 11 (slice 7).

Coarse analysis: the real `analysis.analyze` (the same cost, travel-time and territory code as `mapgen build`) run in-process on
the buffer text at a coarser analysis cell (`COARSE_CELL`, never finer than the spec's own cell) and a reduced heightmap. Nothing is
written to disk. One worker thread computes at most one text at a time; a request for a newer text supersedes older ones
(the older thread finishes into the cache but is never reported as current). Results are cached per text hash.

Build job: `mapgen build` run as a subprocess (the CLI itself, so the output is byte-identical to a CLI build of the same text) on a
temp snapshot of the buffer, into a staging folder; on exit code 0 the finished folder replaces `<scratch>/<name>/`. A cancelled or
failed build never touches the previous result, and the staging folder is removed. Only whitelisted files of the finished folder are
served.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional

from . import analysis, height, schema

COARSE_CELL = 20.0
PREVIEW_HEIGHTMAP_RES = 257
STAGES = ["geometry", "heightmap", "analysis", "render", "export", "report"]
SERVED = re.compile(r"^(map|overlay_[a-z0-9_]+)\.png$")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SCRATCH = os.path.join(ROOT, "out", "spec_edit")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _num(v: Optional[float]) -> Optional[float]:
    return None if v is None or not math.isfinite(v) else round(float(v), 2)


# -- coarse analysis ------------------------------------------------------------------------------------------------------

def coarse_analysis(spec: schema.MapSpec, geoms) -> Dict[str, Any]:
    """(result JSON, AnalysisResult) of the coarse preview for a loaded spec and its geometries."""
    pspec = copy.copy(spec)
    pspec.output = copy.copy(spec.output)
    pspec.output.heightmap_res = PREVIEW_HEIGHTMAP_RES
    hm = height.synthesize(pspec, geoms)
    cell = max(COARSE_CELL, spec.analysis.cell)
    t0 = time.time()
    an = analysis.analyze(spec, geoms, hm, cell)
    ms = int(round(1000 * (time.time() - t0)))
    g = an.grid
    code = []
    for r in range(g.rows):   # row 0 is north; one char per cell: team index, c contested, u unreachable
        row = []
        for c in range(g.cols):
            o = int(an.owner[r, c])
            row.append("c" if an.contested[r, c] and o >= 0 else ("u" if o < 0 else str(o)))
        code.append("".join(row))

    def pt(rc):
        return [round((rc[1] + 0.5) * g.cell_size_x, 1), round(g.size_y - (rc[0] + 0.5) * g.cell_size_y, 1)]

    def poly(p):
        return [pt(q) for q in p]

    routes = []
    for key, p in an.paths.items():
        routes.append({"kind": "base", "key": key, "team": None, "pts": poly(p), "time": _num(an.base_times.get(key))})
    pois = {p.id: p for p in spec.all_pois()}
    for o in an.objectives:
        cell_rc = analysis._cell(g, *pois[o["id"]].at)
        for t in an.teams:
            if o["times"][t] is None:
                continue
            p = an.mcps[t].traceback(cell_rc)
            routes.append({"kind": "objective", "key": o["id"], "team": t, "pts": poly(p), "time": _num(o["times"][t])})
    tol = spec.analysis.balance_tolerance
    objs = [{"id": o["id"], "type": o["type"], "owner": o["owner"], "times": {t: _num(v) for t, v in o["times"].items()},
             "spread": _num(o["spread"]), "flag": o["flag"] or "ok"} for o in an.objectives]
    neutral = [o for o in objs if o["owner"] is None]
    bad = [o for o in neutral if o["flag"] != "ok"]
    res = {"cell": cell, "rows": g.rows, "cols": g.cols, "size": [spec.size[0], spec.size[1]], "owner": code,
           "teams": [{"id": t.id, "name": t.name, "base": [t.base[0], t.base[1]]} for t in spec.teams.values()],
           "routes": routes, "objectives": objs, "tolerance": tol,
           "verdict": {"neutral": len(neutral), "flagged": len(bad), "ok": not bad,
                       "max_spread": max([o["spread"] for o in neutral if o["spread"] is not None] or [0.0])},
           "shares": {k: round(v, 4) for k, v in an.shares.items()},
           "base_times": {k: _num(v) for k, v in an.base_times.items()},
           "warnings": list(an.warnings), "ms": ms, "label": "coarse preview"}
    return res, an


class AnalysisPreview:
    """Cache + single worker for the coarse analysis of an editor's buffer. `derive(text, key)` -> (spec, geoms, ...) is the
    editor's cached geometry builder."""

    def __init__(self, derive):
        self._derive = derive
        self._cache = OrderedDict()   # key -> {"res", "an", "spec", "geoms"} | {"error": [...]}
        self._lock = threading.Lock()
        self._latest = None           # key of the newest request
        self._running = set()

    def request(self, text: str) -> Dict[str, Any]:
        """Status of the analysis of `text`: {status: ready, result} | {status: computing} | {status: error, errors}. Starts a
        worker when needed. Never blocks on the computation."""
        key = _sha(text)
        with self._lock:
            self._latest = key
            hit = self._cache.get(key)
            if hit is not None:
                self._cache.move_to_end(key)
                if "error" in hit:
                    return {"status": "error", "errors": hit["error"]}
                return {"status": "ready", "result": hit["res"]}
            if key not in self._running:
                self._running.add(key)
                threading.Thread(target=self._work, args=(text, key), daemon=True).start()
            return {"status": "computing"}

    def _work(self, text: str, key: str) -> None:
        try:
            spec, geoms = self._derive(text, key)[:2]
            res, an = coarse_analysis(spec, geoms)
            entry = {"res": res, "an": an, "spec": spec, "geoms": geoms}
        except Exception as e:   # the preview never takes the server down
            entry = {"error": ["%s: %s" % (type(e).__name__, e)]}
        with self._lock:
            self._cache[key] = entry
            while len(self._cache) > 3:
                self._cache.popitem(last=False)
            self._running.discard(key)

    def probe(self, text: str, x: float, y: float) -> Optional[Dict[str, Any]]:
        """`mapgen probe` semantics on the coarse result of `text` (None when it is not computed)."""
        with self._lock:
            hit = self._cache.get(_sha(text))
        if hit is None or "error" in hit:
            return None
        r = analysis.probe_point(hit["spec"], hit["geoms"], hit["an"], x, y)
        r["spread"] = _num(r["spread"])
        r["cost"] = None if not math.isfinite(r["cost"]) else round(r["cost"], 3)
        r["times"] = {t: _num(v) for t, v in r["times"].items()}
        r["tolerance"] = hit["spec"].analysis.balance_tolerance
        r["cell"] = hit["res"]["cell"]
        return r


# -- build job ------------------------------------------------------------------------------------------------------------

class BuildRefused(Exception):
    def __init__(self, errors: List[str], code: int = 422):
        super().__init__("; ".join(errors))
        self.errors, self.code = errors, code


class BuildManager:
    """One build at a time of a snapshot of the buffer. `snapshot_name` is the spec file's base name."""

    def __init__(self, scratch_root: str, snapshot_name: str, palette_path: Optional[str] = None):
        self.root = os.path.abspath(scratch_root)
        self.snapshot_name = snapshot_name
        self.palette_path = palette_path
        self._lock = threading.Lock()
        self._seq = 0
        self._job = None      # type: Optional[Dict[str, Any]]
        self._proc = None
        self._last = None     # type: Optional[Dict[str, Any]]  # summary of the last successful build

    # -- API -------------------------------------------------------------------------------------------------------------
    def start(self, text: str, version: Any = None) -> Dict[str, Any]:
        try:
            spec = schema.load_spec_text(text.encode("utf-8"), None, self.palette_path)
        except schema.SpecError as e:
            raise BuildRefused(["the spec does not load, so it cannot be built:"] + list(e.errors))
        with self._lock:
            if self._job is not None and self._job["status"] == "running":
                raise BuildRefused(["a build is already running (job %d); wait for it or cancel it" % self._job["id"]], 409)
            self._seq += 1
            jid = self._seq
            work = os.path.join(self.root, ".work", "job%d_%d" % (os.getpid(), jid))
            shutil.rmtree(work, ignore_errors=True)
            os.makedirs(work)
            snap = os.path.join(work, self.snapshot_name)
            with open(snap, "wb") as f:
                f.write(text.encode("utf-8"))
            self._job = {"id": jid, "status": "running", "name": spec.name, "version": version, "stages": [
                {"name": n, "state": "pending", "s": None} for n in STAGES], "t0": time.time(), "t1": None,
                "exit_code": None, "log": [], "files": [], "report": None, "errors": [], "cancel": False}
            self._job["stages"][0]["state"] = "running"
            cmd = [sys.executable, "-u", "-m", "mapgen", "build", snap, "--out", os.path.join(work, "out")]
            if self.palette_path:
                cmd += ["--palette", self.palette_path]
            env = dict(os.environ)
            env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
            env["PYTHONIOENCODING"] = "utf-8"
            self._proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
            threading.Thread(target=self._run, args=(self._job, self._proc, work, spec.name), daemon=True).start()
            return self._view(self._job)

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {"job": self._view(self._job) if self._job else None, "last": self._last}

    def cancel(self) -> Dict[str, Any]:
        with self._lock:
            j = self._job
            if j is None or j["status"] != "running":
                raise BuildRefused(["no build is running"], 409)
            j["cancel"] = True
            try:
                self._proc.kill()
            except OSError:
                pass
        return self.status()

    def close(self) -> None:
        with self._lock:
            if self._job is not None and self._job["status"] == "running":
                self._job["cancel"] = True
                try:
                    self._proc.kill()
                except OSError:
                    pass

    def wait(self, timeout: float = 120.0) -> Dict[str, Any]:
        t0 = time.time()
        while time.time() - t0 < timeout:
            st = self.status()
            if st["job"] is None or st["job"]["status"] != "running":
                return st
            time.sleep(0.05)
        return self.status()

    def file_path(self, name: Any) -> Optional[str]:
        """Absolute path of a served file of the last successful build, or None. Only `map.png` / `overlay_*.png` names
        (no separators), resolved inside the build folder."""
        if not isinstance(name, str) or not SERVED.match(name):
            return None
        with self._lock:
            last = self._last
        if last is None:
            return None
        base = os.path.realpath(last["dir"])
        p = os.path.realpath(os.path.join(base, name))
        if os.path.dirname(p) != base or not os.path.isfile(p):
            return None
        return p

    # -- internals -------------------------------------------------------------------------------------------------------
    def _view(self, j: Dict[str, Any]) -> Dict[str, Any]:
        end = j["t1"] if j["t1"] else time.time()
        out = {k: copy.deepcopy(v) for k, v in j.items() if k not in ("t0", "t1", "cancel")}
        out["elapsed"] = round(end - j["t0"], 1)
        return out

    def _run(self, job: Dict[str, Any], proc, work: str, name: str) -> None:
        stage_re = re.compile(r"^\s+(geometry|heightmap|analysis|render|export|report)\s+([0-9.]+)s\s*$")
        try:
            for raw in iter(proc.stdout.readline, b""):
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                with self._lock:
                    job["log"].append(line)
                    del job["log"][:-200]
                    m = stage_re.match(line)
                    if m:
                        names = [s["name"] for s in job["stages"]]
                        i = names.index(m.group(1))
                        for k, s in enumerate(job["stages"]):
                            if k < i and s["state"] != "done":
                                s["state"], s["s"] = "skipped", None
                        job["stages"][i]["state"], job["stages"][i]["s"] = "done", float(m.group(2))
                        if i + 1 < len(names):
                            job["stages"][i + 1]["state"] = "running"
            code = proc.wait()
        except Exception as e:   # reader failure: treat as a failed build
            code = -1
            with self._lock:
                job["errors"].append("%s: %s" % (type(e).__name__, e))
        finished = None
        with self._lock:
            cancelled = job["cancel"]
        if code == 0 and not cancelled:
            try:
                finished = self._publish(work, name)
            except OSError as e:
                code = -2
                with self._lock:
                    job["errors"].append("could not install the build: %s" % e)
        shutil.rmtree(work, ignore_errors=True)
        with self._lock:
            job["exit_code"] = code
            job["t1"] = time.time()
            if cancelled:
                job["status"] = "cancelled"
            elif code == 0 and finished is not None:
                job["status"] = "done"
                job["files"], job["report"] = finished["files"], finished["report"]
                for s in job["stages"]:
                    if s["state"] != "done":
                        s["state"] = "skipped"
                self._last = {"dir": finished["dir"], "name": name, "job": job["id"], "files": finished["files"],
                              "report": finished["report"]}
            else:
                job["status"] = "failed"
                if not job["errors"]:
                    job["errors"] = [l for l in job["log"] if l.strip()][-8:]
                for s in job["stages"]:
                    if s["state"] == "running":
                        s["state"] = "failed"

    def _publish(self, work: str, name: str) -> Dict[str, Any]:
        src = os.path.join(work, "out", name)
        dst = os.path.join(self.root, name)
        with self._lock:   # a reader never sees a half-replaced folder: swap under the lock
            if os.path.isdir(dst):
                shutil.rmtree(dst)
            os.replace(src, dst)
        with open(os.path.join(dst, "report.json"), "rb") as f:
            rep = json.loads(f.read().decode("utf-8"))
        files = sorted(n for n in os.listdir(dst) if SERVED.match(n))
        order = ["map.png"] + [n for n in files if n != "map.png"]
        keep = ("name", "size", "seed", "warnings", "teams", "base_times_s", "area_share", "objectives", "balance_tolerance",
                "fair_zone_share")
        return {"dir": dst, "files": order, "report": {k: rep.get(k) for k in keep}}
