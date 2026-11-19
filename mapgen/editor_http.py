"""Shared HTTP guards of the local editors (flowgraph editor, spec editor): loopback bind, per-run token, Host and Origin
checks, CSP, body cap, JSON helpers. Design: docs/investigations/2026-11-19-map-editor-design.md section 3.

A server binds 127.0.0.1 only. Every request needs a loopback Host header; every POST also needs a same-origin (or absent)
Origin and the per-run token in header X-Token.
"""
from __future__ import annotations

import hmac
import json
import secrets
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional

HOST = "127.0.0.1"
MAX_BODY = 1 << 20


class LoopbackServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, handler, port: int = 0, token: Optional[str] = None):
        self.token = token or secrets.token_urlsafe(24)
        super().__init__((HOST, port), handler)
        self.allowed_hosts = {"%s:%d" % (h, self.server_address[1]) for h in ("127.0.0.1", "localhost")}

    def handle_error(self, request, client_address):
        if not isinstance(sys.exc_info()[1], ConnectionError):  # browsers drop keep-alive sockets; that is not an error
            super().handle_error(request, client_address)


class GuardedHandler(BaseHTTPRequestHandler):
    server: LoopbackServer
    protocol_version = "HTTP/1.1"
    img_src = ""   # extra CSP img-src sources, e.g. "blob:"

    def log_message(self, fmt, *args):  # quiet
        pass

    def csp(self) -> str:
        return ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                + ("img-src %s; " % self.img_src if self.img_src else "")
                + "connect-src 'self'; frame-ancestors 'none'")

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", self.csp())
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: Dict[str, Any]) -> None:
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8")

    def _host_ok(self) -> bool:
        return self.headers.get("Host", "") in self.server.allowed_hosts

    def guard_get(self) -> bool:
        """True when the request may proceed; otherwise the 403 is already sent."""
        if not self._host_ok():
            self._json(403, {"ok": False, "errors": ["forbidden host"]})
            return False
        return True

    def guard_post(self) -> bool:
        """Host, Origin and token checks, in that order. True when the request may proceed."""
        if not self.guard_get():
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin not in {"http://" + h for h in self.server.allowed_hosts}:
            self._json(403, {"ok": False, "errors": ["forbidden origin"]})
            return False
        if not hmac.compare_digest(self.headers.get("X-Token", "").encode("utf-8"), self.server.token.encode("utf-8")):
            self._json(403, {"ok": False, "errors": ["bad token"]})
            return False
        return True

    def read_json(self, bad_msg: str) -> Optional[Any]:
        """The JSON request body, or None after sending 413 (size) / 400 (not JSON)."""
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            n = -1
        if n < 0 or n > MAX_BODY:
            self._json(413, {"ok": False, "errors": ["bad body size"]})
            return None
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"ok": False, "errors": [bad_msg]})
            return None
