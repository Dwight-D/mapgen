"""Spec editor: local HTTP server (127.0.0.1 only) for `mapgen spec-edit <spec>`. Design:
docs/investigations/2026-11-19-map-editor-design.md (slice 2).

The server owns the YAML text buffer and a whole-text undo/redo stack. An edit is a list of ops addressed by feature id
(`specpatch.op_from_json`); it is applied through the span-patch engine and the real loader, and an invalid result is
refused with the loader's error list and leaves the buffer unchanged. The file is written only by an explicit Save, in
place and atomically, and only while the buffer is valid and the file on disk is the one that was read.

API (all POST, JSON in and out, header X-Token; see editor_http for the guards):
  /api/state                  -> {ok, state}
  /api/apply  {version, ops}  -> {ok, state} (state.results: per op `{id}` of an add, `{deleted, detached}`, `{renamed, references, seed_pinned}`)
                                 | 422 {ok: false, errors, error_refs, state} (refused, buffer unchanged; `error_refs[i]` is
                                   {feature, path, key} of errors[i]; a delete of a referenced feature adds `referenced` and
                                   `referrers`) | 409 (stale version)
                                 ops: set | put | insert | delete | add | rename (specpatch.op_from_json)
  /api/paint  {version, mode, ...} -> same as /api/apply, one undo step; state.results[0] = {paint: summary}.
                                 mode paint: {stroke: [[x, y], ...], radius, type, impassable} merges into / appends `poly` regions;
                                 mode erase: {stroke, radius, type | "any"} subtracts from painted polys (summary.skipped lists
                                 blob/band/reference polys under the brush); mode convert: {id} turns a blob or band into a poly (paint.py)
  /api/undo, /api/redo        -> {ok, state}
  /api/save                   -> {ok, path, bytes, state} | 409 (file changed on disk) | 422 (invalid)
  /api/geometry {version}     -> {ok, version, geometry, ms} | {ok: false, stale: true}: resolved centrelines and outlines
  /api/image {version}        -> image/png (reduced-resolution preview) with headers X-Version, X-Render-Ms
                                 | JSON {ok: false, stale: true} when the buffer moved on before rendering started
  /api/flowgraph {version}    -> {ok, version, flowgraph} | {ok: false, stale: true}: with a flowgraph configured (`--flowgraph` or
                                 auto-detected; read-only, never written): node discs, edge status, structured check findings and
                                 the unbound nodes, computed in-process on the buffer (spec_editor_flowgraph.overlay). `flowgraph`
                                 is {path, name, errors} only when the graph file does not load. {ok: false} without a graph.
  /api/flowgraph_terrain {version} -> {ok, version, findings, ms} | {ok: false, stale: true}: the `--terrain` findings (seconds).
`state` = {version, dirty, can_undo, can_redo, path, size, handles, referrers, features, flowgraph}; `flowgraph` is null without a
graph, else {path, name, errors}. An /api/apply that renames a feature a flowgraph node binds adds `flowgraph_warnings` (the graph is
not rewritten; the node is unbound).
"""
from __future__ import annotations

import copy
import hashlib
import io
import os
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

from . import geometry, height, paint, schema, specpatch, spec_editor_flowgraph as fgmod
from .editor_http import HOST, GuardedHandler, LoopbackServer
from .spec_editor_page import render_page
from .spec_model import Model, geometry_json, map_error, schema_json

HISTORY_LIMIT = 200
PREVIEW_PX = 768
PREVIEW_HEIGHTMAP_RES = 257


class Refused(Exception):
    """An edit or save that was refused; `errors` is the message list, `code` the HTTP status."""

    def __init__(self, errors: List[str], code: int = 422, **extra: Any):
        super().__init__("; ".join(errors))
        self.errors, self.code, self.extra = errors, code, extra


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class SpecEditor:
    """Buffer, history, validation and preview computation for one spec file. No HTTP."""

    def __init__(self, path: str, preview_px: int = PREVIEW_PX, palette_path: Optional[str] = None,
                 flowgraph_path: Optional[str] = None):
        self.path = path
        self.fg = fgmod.FlowgraphSource(flowgraph_path) if flowgraph_path else None
        self._terrain = OrderedDict()  # text hash -> (findings, ms)
        self._terr_lock = threading.Lock()
        self.preview_px = preview_px
        self.palette_path = palette_path
        self.lock = threading.RLock()
        with open(path, "rb") as f:
            data = f.read()
        self.text = data.decode("utf-8")
        self.disk_text = self.text
        self.disk_hash = _sha(data)
        self.version = 1
        self.undo_stack = []  # type: List[str]
        self.redo_stack = []  # type: List[str]
        self.retired = set()  # type: set  # ids deleted during this session: a generated id never reuses one
        self.model = self._load(self.text)  # raises schema.SpecError when the file is invalid
        self._geom_lock = threading.Lock()
        self._img_lock = threading.Lock()
        self._geoms = OrderedDict()  # text hash -> (spec, geoms, geometry json, ms)
        self._images = OrderedDict()  # text hash -> (png bytes, ms)

    # -- buffer -----------------------------------------------------------------------------------------------------
    def _load(self, text: str) -> Model:
        return Model(text, schema.load_spec_text(text.encode("utf-8"), self.path, self.palette_path))

    def state(self) -> Dict[str, Any]:
        with self.lock:
            st = {"version": self.version, "dirty": self.text != self.disk_text, "can_undo": bool(self.undo_stack),
                  "can_redo": bool(self.redo_stack), "path": os.path.abspath(self.path)}
            st.update(self.model.state())
            st["flowgraph"] = self.fg.info() if self.fg else None
            return st

    def apply(self, version: Any, ops_json: Any) -> Dict[str, Any]:
        """Apply the ops to the buffer or raise Refused (buffer unchanged). One call is one undo step."""
        with self.lock:
            if version != self.version:
                raise Refused(["the buffer changed (version %s, yours %s); state refreshed" % (self.version, version)], 409)
            if not isinstance(ops_json, list) or not ops_json:
                raise Refused(["ops must be a non-empty list"], 400)
            new = None
            try:
                ops = [specpatch.op_from_json(o) for o in ops_json]
                for op in ops:
                    if isinstance(op, (specpatch.Add, specpatch.Rename)):
                        op.avoid = set(self.retired)
                new = specpatch.apply_ops(self.text, ops)
                model = self._load(new)
            except specpatch.ReferencedError as e:
                raise Refused(["%s: %s" % (type(e).__name__, e)], referenced=e.name, referrers=e.referrers)
            except specpatch.PatchError as e:
                raise Refused(["%s: %s" % (type(e).__name__, e)])
            except schema.SpecError as e:
                refs = []
                try:
                    raw = specpatch.parsed_document(new) if new is not None else {}
                    refs = [map_error(raw, m) for m in e.errors]
                except specpatch.PatchError:
                    pass
                raise Refused(list(e.errors), error_refs=refs)
            results = [dict(op.result) for op in ops if getattr(op, "result", None)]
            for r in results:
                if "deleted" in r:
                    self.retired.add(r["deleted"])
                if "renamed" in r:
                    self.retired.add(r["renamed"][0])
            if new != self.text:
                self._push(self.undo_stack, self.text)
                self.redo_stack = []
                self.text, self.model = new, model
                self.version += 1
            st = self.state()
            st["results"] = results
            if self.fg is not None:
                renames = [r for r in results if "renamed" in r]
                st["flowgraph_warnings"] = fgmod.rename_warnings(self.fg.graph, renames, self.fg.path) if renames else []
            return st

    def paint(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """Plan a paint / erase / convert request (paint.py) and apply it as one edit (one undo step)."""
        with self.lock:
            if body.get("version") != self.version:
                raise Refused(["the buffer changed (version %s, yours %s); state refreshed" % (self.version, body.get("version"))], 409)
            mode, spec = body.get("mode"), self.model.spec
            types = spec.types.regions
            try:
                py = specpatch.parsed_document(self.text)
                if mode == "convert":
                    plan = paint.plan_convert(py, spec, spec.size, body.get("id"))
                elif mode in ("paint", "erase"):
                    rtype = body.get("type")
                    if mode == "paint" and rtype not in types:
                        raise paint.PaintError("unknown region type %r (known: %s)" % (rtype, ", ".join(types)))
                    if mode == "erase" and rtype not in (None, "any") and rtype not in types:
                        raise paint.PaintError("unknown region type %r" % (rtype,))
                    if body.get("impassable") not in (None, True, False):
                        raise paint.PaintError("impassable is true or false")
                    brush = paint.brush_polygon(body.get("stroke"), body.get("radius"), spec.size)
                    if brush is None:
                        raise paint.PaintError("the stroke is outside the map")
                    plan = (paint.plan_paint(py, spec.size, brush, rtype, body.get("impassable") is True) if mode == "paint"
                            else paint.plan_erase(py, spec, brush, None if rtype in (None, "any") else rtype))
                else:
                    raise paint.PaintError("unknown paint mode %r (paint | erase | convert)" % (mode,))
            except paint.PaintError as e:
                raise Refused([str(e)], 400)
            summary = plan.summary()
            if not plan.ops:
                st = self.state()
                summary["notes"] = summary["notes"] + ["nothing changed"]
                st["results"] = [{"paint": summary}]
                return st
            st = self.apply(body.get("version"), plan.ops)
            summary["created_ids"] = [r["id"] for r in st["results"] if "id" in r]
            st["results"].insert(0, {"paint": summary})
            return st

    @staticmethod
    def _push(stack: List[str], text: str) -> None:
        stack.append(text)
        if len(stack) > HISTORY_LIMIT:
            del stack[0]

    def _step(self, src: List[str], dst: List[str]) -> Dict[str, Any]:
        with self.lock:
            if not src:
                raise Refused(["nothing to %s" % ("undo" if src is self.undo_stack else "redo")], 409)
            prev = src[-1]
            model = self._load(prev)   # every stored buffer was valid; a failure here leaves everything unchanged
            src.pop()
            self._push(dst, self.text)
            self.text, self.model = prev, model
            self.version += 1
            return self.state()

    def undo(self) -> Dict[str, Any]:
        return self._step(self.undo_stack, self.redo_stack)

    def redo(self) -> Dict[str, Any]:
        return self._step(self.redo_stack, self.undo_stack)

    def save(self) -> Tuple[int, Dict[str, Any]]:
        """Write the buffer over the file atomically. Refused when the buffer is invalid or the file changed on disk."""
        with self.lock:
            try:
                self._load(self.text)
            except schema.SpecError as e:
                raise Refused(list(e.errors))
            try:
                with open(self.path, "rb") as f:
                    on_disk = _sha(f.read())
            except OSError as e:
                raise Refused(["cannot read %s: %s" % (self.path, e)], 500)
            if on_disk != self.disk_hash:
                raise Refused(["%s changed on disk since it was loaded; not overwritten" % self.path], 409)
            if self.text == self.disk_text:
                return 0, self.state()
            data = self.text.encode("utf-8")
            try:
                specpatch.write_atomic(self.path, self.text)
            except OSError as e:
                raise Refused(["write failed: %s" % e], 500)
            self.disk_text, self.disk_hash = self.text, _sha(data)
            return len(data), self.state()

    # -- preview ----------------------------------------------------------------------------------------------------
    def _current(self, version: Any) -> Optional[Tuple[str, str]]:
        with self.lock:
            if version != self.version:
                return None
            return self.text, _sha(self.text.encode("utf-8"))

    def _derived(self, text: str, key: str):
        with self._geom_lock:
            hit = self._geoms.get(key)
            if hit is None:
                t0 = time.time()
                spec = schema.load_spec_text(text.encode("utf-8"), self.path, self.palette_path)
                geoms = geometry.build_geometries(spec)
                gj = geometry_json(spec, geoms)
                hit = (spec, geoms, gj, int(round(1000 * (time.time() - t0))))
                self._geoms[key] = hit
                while len(self._geoms) > 4:
                    self._geoms.popitem(last=False)
            return hit

    def geometry(self, version: Any) -> Optional[Dict[str, Any]]:
        """Resolved shapes of the buffer at `version` (load + build_geometries), cached per text; None when stale."""
        cur = self._current(version)
        if cur is None:
            return None
        hit = self._derived(*cur)
        return {"ok": True, "version": version, "geometry": hit[2], "ms": hit[3]}

    def flowgraph(self, version: Any) -> Optional[Dict[str, Any]]:
        """Overlay + check of the buffer at `version` against the flowgraph (in-process; the graph file is only read);
        None when stale. Without a graph: {"ok": False}."""
        if self.fg is None:
            return {"ok": False, "errors": ["no flowgraph is configured"]}
        with self.lock:
            if version != self.version:
                return None
            model = self.model
        info = self.fg.info()
        if self.fg.graph is None:
            return {"ok": True, "version": version, "flowgraph": {"path": info["path"], "name": None, "errors": info["errors"]}}
        t0 = time.time()
        data = fgmod.overlay(self.fg.graph, model.spec, model.raw)
        data.update(path=info["path"], errors=[], ms=int(round(1000 * (time.time() - t0))))
        return {"ok": True, "version": version, "flowgraph": data}

    def flowgraph_terrain(self, version: Any) -> Optional[Dict[str, Any]]:
        """`--terrain` findings for the buffer at `version`, cached per text; latest wins (a request that waited behind a
        running computation re-checks the version); None when stale."""
        if self.fg is None:
            return {"ok": False, "errors": ["no flowgraph is configured"]}
        self.fg.info()
        if self.fg.graph is None:
            return {"ok": False, "errors": ["no valid flowgraph is loaded"]}
        cur = self._current(version)
        if cur is None:
            return None
        graph = self.fg.graph
        key = cur[1] + ":" + str(id(graph))
        with self._terr_lock:
            if self._current(version) is None:
                return None
            hit = self._terrain.get(key)
            if hit is None:
                t0 = time.time()
                spec = schema.load_spec_text(cur[0].encode("utf-8"), self.path, self.palette_path)
                hit = (fgmod.terrain(graph, spec), int(round(1000 * (time.time() - t0))))
                self._terrain[key] = hit
                while len(self._terrain) > 4:
                    self._terrain.popitem(last=False)
            return {"ok": True, "version": version, "findings": hit[0], "ms": hit[1]}

    def image(self, version: Any) -> Optional[Tuple[bytes, int]]:
        """PNG of the buffer at `version` at preview settings, cached per text; None when stale. Never writes files."""
        cur = self._current(version)
        if cur is None:
            return None
        text, key = cur
        with self._img_lock:
            if self._current(version) is None:   # latest wins: a request that waited behind a render re-checks
                return None
            hit = self._images.get(key)
            if hit is None:
                spec, geoms = self._derived(text, key)[:2]
                t0 = time.time()
                from . import render
                pspec = copy.copy(spec)
                pspec.output = copy.copy(spec.output)
                pspec.output.heightmap_res = PREVIEW_HEIGHTMAP_RES
                pspec.output.render_px = self.preview_px
                hm = height.synthesize(pspec, geoms)
                img = render.render_core(pspec, geoms, hm, render.View(pspec, self.preview_px))
                buf = io.BytesIO()
                img.convert("RGB").save(buf, "PNG", compress_level=1)
                hit = (buf.getvalue(), int(round(1000 * (time.time() - t0))))
                self._images[key] = hit
                while len(self._images) > 4:
                    self._images.popitem(last=False)
            return hit


class SpecEditorServer(LoopbackServer):
    def __init__(self, path: str, port: int = 0, token: Optional[str] = None, preview_px: int = PREVIEW_PX,
                 flowgraph_path: Optional[str] = None):
        self.ed = SpecEditor(path, preview_px, flowgraph_path=flowgraph_path)
        super().__init__(_Handler, port, token)

    def page(self) -> str:
        boot = {"token": self.token, "state": self.ed.state(), "preview_px": self.ed.preview_px,
                "schema": schema_json(schema.load_types())}
        return render_page(boot, os.path.basename(self.ed.path))


class _Handler(GuardedHandler):
    server: SpecEditorServer
    img_src = "blob:"

    def do_GET(self):
        if not self.guard_get():
            return
        if self.path.split("?")[0] in ("/", "/index.html"):
            return self._send(200, self.server.page().encode("utf-8"), "text/html; charset=utf-8")
        return self._json(404, {"ok": False, "errors": ["not found"]})

    def do_POST(self):
        if not self.guard_post():
            return
        routes = {"/api/state": self._state, "/api/apply": self._apply, "/api/undo": self._undo, "/api/redo": self._redo,
                  "/api/save": self._save, "/api/paint": self._paint, "/api/geometry": self._geometry, "/api/image": self._image,
                  "/api/flowgraph": self._flowgraph, "/api/flowgraph_terrain": self._flowgraph_terrain}
        route = routes.get(self.path)
        if route is None:
            return self._json(404, {"ok": False, "errors": ["not found"]})
        body = self.read_json("expected a JSON object")
        if body is None:
            return
        if not isinstance(body, dict):
            return self._json(400, {"ok": False, "errors": ["expected a JSON object"]})
        try:
            route(body)
        except Refused as e:
            body = {"ok": False, "errors": e.errors, "state": self.server.ed.state()}
            body.update(e.extra)
            self._json(e.code, body)

    def _state(self, body):
        self._json(200, {"ok": True, "state": self.server.ed.state()})

    def _apply(self, body):
        self._json(200, {"ok": True, "state": self.server.ed.apply(body.get("version"), body.get("ops"))})

    def _paint(self, body):
        self._json(200, {"ok": True, "state": self.server.ed.paint(body)})

    def _undo(self, body):
        self._json(200, {"ok": True, "state": self.server.ed.undo()})

    def _redo(self, body):
        self._json(200, {"ok": True, "state": self.server.ed.redo()})

    def _save(self, body):
        n, st = self.server.ed.save()
        self._json(200, {"ok": True, "path": st["path"], "bytes": n, "state": st})

    def _geometry(self, body):
        r = self.server.ed.geometry(body.get("version"))
        self._json(200, r if r is not None else {"ok": False, "stale": True, "errors": []})

    def _flowgraph(self, body):
        r = self.server.ed.flowgraph(body.get("version"))
        self._json(200, r if r is not None else {"ok": False, "stale": True, "errors": []})

    def _flowgraph_terrain(self, body):
        r = self.server.ed.flowgraph_terrain(body.get("version"))
        self._json(200, r if r is not None else {"ok": False, "stale": True, "errors": []})

    def _image(self, body):
        v = body.get("version")
        r = self.server.ed.image(v)
        if r is None:
            return self._json(200, {"ok": False, "stale": True, "errors": []})
        png, ms = r
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(png)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", self.csp())
        self.send_header("X-Version", str(v))
        self.send_header("X-Render-Ms", str(ms))
        self.end_headers()
        self.wfile.write(png)


def serve(path: str, port: int = 0, open_browser: bool = True, preview_px: int = PREVIEW_PX,
          flowgraph_path: Optional[str] = None) -> int:
    """Run the editor until interrupted. Returns a process exit code."""
    try:
        srv = SpecEditorServer(path, port, preview_px=preview_px, flowgraph_path=flowgraph_path)
    except OSError as e:
        print("error: cannot listen on %s:%d: %s" % (HOST, port, e))
        return 1
    url = "http://127.0.0.1:%d/" % srv.server_address[1]
    print("spec editor for %s at %s (Ctrl+C to stop)" % (path, url))
    if flowgraph_path:
        print("flowgraph (read-only): %s" % flowgraph_path)
    if open_browser:
        import webbrowser
        threading.Timer(0.3, webbrowser.open, (url,)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0
