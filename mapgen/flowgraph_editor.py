"""Flowgraph editor: local HTTP server (127.0.0.1 only) serving the preview page upgraded to an editor (design: mapgen/FLOWGRAPH_SPEC.md section 14).

API (all POST, JSON in and out; every request needs the per-run token in header X-Token and a loopback Host):
  /api/validate  {graph}  -> {ok, errors}                      same loader/validator as `flowgraph validate`
  /api/layout    {graph}  -> {ok, errors, pos, w, h}           Python layout (place hints included) of a valid graph
  /api/save      {graph}  -> {ok, errors, path, bytes}         canonical `.flow.yaml`, atomic; refused when invalid
`graph` is the wire format of `flowgraph.graph_to_dict`.
"""
from __future__ import annotations

import os
import re
import threading
from typing import Optional

from . import flowgraph, flowgraph_preview
from .editor_http import HOST, MAX_BODY, GuardedHandler, LoopbackServer
from .flowgraph import Flowgraph, SpecError
from .flowgraph_editor_page import render_page


def template_graph(path: str) -> Flowgraph:
    """The minimal valid starting graph for `edit --new`: two spawns joined by one edge, named after the file."""
    base = os.path.basename(path)
    stem = base[:-len(".flow.yaml")] if base.endswith(".flow.yaml") else os.path.splitext(base)[0]
    name = re.sub(r"[^a-z0-9_]", "_", stem.lower()) or "new_graph"
    return flowgraph.load_flowgraph_dict({
        "format_version": 1, "name": name, "brief": "",
        "nodes": {"A": {"kind": "spawn", "team": "A", "name": "Team A spawn"},
                  "B": {"kind": "spawn", "team": "B", "name": "Team B spawn"}},
        "edges": ["A - B"]})


class EditorServer(LoopbackServer):
    def __init__(self, path: str, port: int = 0, token: Optional[str] = None):
        self.path = path
        super().__init__(_Handler, port, token)

    def current_graph(self) -> Flowgraph:
        """The graph on disk, or the template while a --new file has not been saved yet."""
        if os.path.exists(self.path):
            return flowgraph.load_flowgraph(self.path)
        return template_graph(self.path)

    def page(self) -> str:
        g = self.current_graph()
        w, h, pos = flowgraph_preview._canvas(g)
        boot = {"token": self.token, "graph": flowgraph.graph_to_dict(g), "pos": {k: [x, y] for k, (x, y) in pos.items()},
                "w": w, "h": h, "file": os.path.basename(self.path), "exists": os.path.exists(self.path),
                "kinds": list(flowgraph.KINDS), "places": list(flowgraph.PLACES),
                "style": {k: list(v) for k, v in flowgraph_preview.STYLE.items()}, "labels": flowgraph_preview.LABELS}
        return render_page(boot)


class _Handler(GuardedHandler):
    server: EditorServer

    def do_GET(self):
        if not self.guard_get():
            return
        if self.path.split("?")[0] in ("/", "/index.html"):
            try:
                return self._send(200, self.server.page().encode("utf-8"), "text/html; charset=utf-8")
            except SpecError as e:
                return self._send(500, ("graph on disk is invalid:\n" + "\n".join(e.errors)).encode("utf-8"), "text/plain; charset=utf-8")
        return self._json(404, {"ok": False, "errors": ["not found"]})

    def do_POST(self):
        if not self.guard_post():
            return
        route = {"/api/validate": self._validate, "/api/layout": self._layout, "/api/save": self._save}.get(self.path)
        if route is None:
            return self._json(404, {"ok": False, "errors": ["not found"]})
        body = self.read_json("expected a JSON object with a graph")
        if body is None:
            return
        try:
            graph = body["graph"]
        except (KeyError, TypeError):
            return self._json(400, {"ok": False, "errors": ["expected a JSON object with a graph"]})
        try:
            g = flowgraph.load_flowgraph_wire(graph)
        except SpecError as e:
            return self._json(200 if route == self._validate else 422, {"ok": False, "errors": e.errors})
        route(g)

    def _validate(self, g: Flowgraph) -> None:
        self._json(200, {"ok": True, "errors": []})

    def _layout(self, g: Flowgraph) -> None:
        w, h, pos = flowgraph_preview._canvas(g)
        self._json(200, {"ok": True, "errors": [], "w": w, "h": h, "pos": {k: [x, y] for k, (x, y) in pos.items()}})

    def _save(self, g: Flowgraph) -> None:
        try:
            n = flowgraph.save_canonical(g, self.server.path)
        except OSError as e:
            return self._json(500, {"ok": False, "errors": ["write failed: %s" % e]})
        self._json(200, {"ok": True, "errors": [], "path": self.server.path, "bytes": n})


def serve(path: str, port: int = 0, open_browser: bool = True) -> int:
    """Run the editor until interrupted. Returns a process exit code."""
    try:
        srv = EditorServer(path, port)
    except OSError as e:
        print("error: cannot listen on %s:%d: %s" % (HOST, port, e))
        return 1
    url = "http://127.0.0.1:%d/" % srv.server_address[1]
    print("flowgraph editor for %s at %s (Ctrl+C to stop)" % (path, url))
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
