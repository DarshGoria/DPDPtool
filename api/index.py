"""Vercel entrypoint: the whole bench, minus the model.

A serverless function cannot host a 2 GB GGUF, so only the built-in engine
runs here - the half worth putting in front of a lawyer anyway, since it
quotes statute verbatim and answers in milliseconds.

Routing matches the LAST path segment: Vercel may hand the function either the
original path or a rewritten one, and the first attempt served the UI for
/api/health because it compared the whole path.
"""
import json
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import webapi  # noqa: E402

import os

_UI = (ROOT / "ui.html").read_text(encoding="utf-8")


def ui_page():
    """Serve the page, naming the backend when one is configured.

    Set DPDP_BACKEND in Vercel to the public URL of the machine running the
    model; the browser then calls it directly, so a ninety-second answer is
    not bound by this function's timeout.
    """
    backend = os.environ.get("DPDP_BACKEND", "").strip().rstrip("/")
    if not backend:
        return _UI
    inject = "<script>window.DPDP_BACKEND={};</script>".format(json.dumps(backend))
    return _UI.replace("<script>", inject + "\n<script>", 1)


def route_of(path):
    return urlparse(path).path.rstrip("/").rsplit("/", 1)[-1].lower()


class handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body)
        body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        if route_of(self.path) == "health":
            self._send(200, webapi.health())
        else:
            self._send(200, ui_page(), "text/html; charset=utf-8")

    def do_POST(self):
        route = route_of(self.path)
        try:
            req = self._body()
        except ValueError:
            self._send(400, {"error": "bad json"})
            return

        if route == "retrieve":
            hits, _query = webapi.retrieve(
                req.get("question", ""), int(req.get("k", 4)),
                webapi.clean_history(req.get("history")))
            self._send(200, {"hits": webapi.hits_payload(hits)})
        elif route == "ask":
            question = (req.get("question") or "").strip()
            if not question:
                self._send(400, {"error": "empty question"})
                return
            frames = webapi.answer_frames(
                question, int(req.get("k", 4)),
                webapi.clean_history(req.get("history")))
            self._send(200, webapi.sse(frames), "text/event-stream")
        elif route == "review":
            # A serverless filesystem does not persist, so verdicts are dropped.
            self._send(200, {"ok": False, "reason": "read-only deployment"})
        else:
            self._send(404, {"error": "not found"})
