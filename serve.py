"""Local test UI for the DPDP SLM.  python serve.py  ->  http://localhost:8000

Standard library only, same as the rest of the pipeline. The corpus is loaded
once at startup; retrieval and generation reuse ask.py so the UI and the CLI
can never drift apart.
"""
import argparse
import hmac
import json
import os
from concurrent.futures import ThreadPoolExecutor
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import ask
import citecheck
import chat
from retrieve import load

HERE = Path(__file__).parent
INDEX = HERE / "ui.html"

META, INDEX_BM25, PENALTIES = None, None, None
# Set this before exposing the bench through a tunnel; see
# Handler._authorised. Unset means no auth, for local use.
TOKEN = os.environ.get("DPDP_TOKEN", "")


def hits_payload(hits):
    out = []
    for score, rec in hits:
        out.append({
            "id": rec["id"],
            "source": rec["source"],
            "citation": rec["citation"],
            "title": rec["title"],
            "score": round(score, 2),
            "in_force": rec.get("in_force"),
            "max_penalty_inr": rec.get("max_penalty_inr"),
            "tags": rec.get("tags", []),
            "text": rec["text"],
            "subclauses": list(rec.get("subclauses", {})),
        })
    return out


PROBE_TIMEOUT = 1.0


def _probe(url, pick):
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=PROBE_TIMEOUT) as resp:
            return True, sorted(pick(json.loads(resp.read())))
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError,
            KeyError, TypeError, OSError):
        return False, []


def model_status(host):
    """Ask Ollama what it has. Returns (reachable, [model names])."""
    return _probe(host.rstrip("/") + "/api/tags",
                  lambda d: [m["name"] for m in d.get("models", [])])


def openai_status(host):
    """Probe any OpenAI-compatible server (llama.cpp, LM Studio, Jan, vLLM)."""
    return _probe(host.rstrip("/") + "/v1/models",
                  lambda d: [m["id"] for m in d.get("data", [])])


def engine_report():
    """What can actually answer right now. Extractive always can.

    Probed in parallel: a dead port costs a full timeout each, and the UI
    blocks on this call.
    """
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_ollama = pool.submit(model_status, ask.HOST)
        f_openai = pool.submit(openai_status, ask.OPENAI_HOST)
        ollama_up, ollama_models = f_ollama.result()
        openai_up, openai_models = f_openai.result()
    return {
        "native": {"available": True, "models": [], "host": None},
        "ollama": {"available": ollama_up and bool(ollama_models),
                   "models": ollama_models, "host": ask.HOST},
        "openai": {"available": openai_up, "models": openai_models,
                   "host": ask.OPENAI_HOST},
    }


def retrieve(question, k, history=None):
    """Expand the query into statutory vocabulary, then narrow away the
    chapters the question is not about. Both endpoints go through here."""
    return chat.search(INDEX_BM25, question, history, k=k)


class Handler(BaseHTTPRequestHandler):
    server_version = "dpdp-slm"

    def log_message(self, fmt, *args):  # quieter console
        pass

    # -- helpers ---------------------------------------------------------
    def _cors(self):
        # The page may be served from Vercel while answers come from here.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def _authorised(self):
        """Opt-in shared secret, for when this is reachable off the laptop.

        A quick tunnel is public and unauthenticated, and data/traces.jsonl
        records every question asked through it - so a forwarded link means
        strangers' questions land in the training log. Set DPDP_TOKEN before
        exposing it and the link becomes ?k=<token>.

        Not enforced when unset, so the local bench keeps working with no
        setup; --lan prints a warning instead. Compared with compare_digest
        because a plain == leaks the prefix length through timing.
        """
        if not TOKEN:
            return True
        given = (self.headers.get("X-DPDP-Token")
                 or parse_qs(urlparse(self.path).query).get("k", [""])[0])
        return hmac.compare_digest(given, TOKEN)

    def _deny(self):
        self._send(401, {"error": "this instance needs ?k=<token>"})

    def _sse_open(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self._cors()
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

    def _sse(self, event):
        self.wfile.write(("data: " + json.dumps(event) + "\n\n").encode())
        self.wfile.flush()

    # -- routes ----------------------------------------------------------
    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send(200, INDEX.read_text(encoding="utf-8"), "text/html; charset=utf-8")
        elif path == "/api/health":
            engines = engine_report()
            self._send(200, {
                "engines": engines,
                "model_host": ask.HOST,
                "reachable": any(e["available"] for n, e in engines.items()
                                 if n != "native"),
                "default_model": ask.MODEL,
                "records": len(INDEX_BM25.records),
                "sources": META["sources"],
                "commencement": META["commencement"],
                "penalties_verified": META["penalties_verified_against_gazette"],
            })
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        # Every POST either spends model time or writes to the trace log.
        if not self._authorised():
            self._deny()
            return
        if path == "/api/retrieve":
            req = self._body()
            # Same pipeline as /api/ask, or "Retrieve only" would show
            # different provisions from the ones an answer is built on.
            self._send(200, {"hits": hits_payload(retrieve(
                req.get("question", ""),
                int(req.get("k", 4)),
                req.get("history") or [])[0])})
        elif path == "/api/ask":
            self.handle_ask()
        elif path == "/api/review":
            req = self._body()
            ok = ask.set_review(
                req.get("id", ""), req.get("verdict"), req.get("note", "")
            )
            self._send(200 if ok else 404, {"ok": ok})
        else:
            self._send(404, {"error": "not found"})

    def handle_ask(self):
        if not self._authorised():
            self._deny()
            return
        req = self._body()
        question = (req.get("question") or "").strip()
        k = int(req.get("k", 4))
        model = req.get("model") or ask.MODEL
        if not question:
            self._send(400, {"error": "empty question"})
            return

        engine = req.get("engine") or "native"
        # Last few turns only: a long bench session would otherwise crowd the
        # provisions out of a small model's context.
        history = [m for m in (req.get("history") or [])
                   if m.get("role") in ("user", "assistant") and m.get("content")][-8:]
        hits, query = retrieve(question, k, history)
        self._sse_open()
        self._sse({"type": "retrieved", "hits": hits_payload(hits)})
        if not hits:
            self._sse({"type": "answer_done",
                       "answer": "NOT COVERED BY RETRIEVED PROVISIONS "
                                 "(nothing in the corpus matched this query)",
                       "id": None})
            self._sse({"type": "done"})
            return

        if engine == "extractive":
            prompt = "(extractive baseline: statute text is quoted directly, " \
                     "no prompt is sent to any model)"
        else:
            prompt = ask.build_prompt(question, hits, history, query)
        self._sse({"type": "prompt", "prompt": prompt, "chars": len(prompt)})

        parts = []
        try:
            for token in ask.answer_stream(question, hits, engine=engine,
                                           model=model, history=history,
                                           penalties=PENALTIES, query=query):
                parts.append(token)
                self._sse({"type": "token", "t": token})
        except ask.ModelUnreachable as exc:
            self._sse({"type": "error", "message": str(exc), "engine": engine,
                       "host": ask.HOST if engine == "ollama" else ask.OPENAI_HOST,
                       "model": model})
            self._sse({"type": "done"})
            return
        except (BrokenPipeError, ConnectionResetError):
            return  # browser navigated away mid-stream

        answer = "".join(parts)
        # Hold the LLM to the native engine's standard: every section number
        # it writes must exist, and ideally must be one we actually retrieved.
        cites = ask.check_citations(answer, hits, INDEX_BM25)
        # Shape problems the citation check cannot see: two verdicts, or a
        # repeated paragraph. Reported, never silently corrected.
        cites["issues"] = ask.answer_problems(answer)
        trace_id = ask.log_trace(question, engine + ":" + model, hits, prompt,
                                 answer, citations=cites)
        parts = [w for w in (citecheck.warning(cites),) if w]
        parts.extend(cites["issues"])
        self._sse({"type": "citations", **cites,
                   "warning": " | ".join(parts) if parts else None})
        self._sse({"type": "answer_done", "answer": answer, "id": trace_id})
        self._sse({"type": "done"})


def bind(port, host="127.0.0.1", tries=25):
    """Bind the first free port at or after `port`.

    Windows both occupies ports with other services and reserves whole ranges
    (WinError 10013), so a fixed port fails often enough to be worth avoiding.
    """
    last = None
    for candidate in range(port, port + tries):
        try:
            return ThreadingHTTPServer((host, candidate), Handler)
        except OSError as exc:
            last = exc
    raise SystemExit("no free port in {}-{}: {}".format(port, port + tries - 1, last))


def lan_ip():
    """This machine's address on the local network, for another device to open."""
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # no packets sent; just picks the route
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def main():
    global META, INDEX_BM25, PENALTIES
    ap = argparse.ArgumentParser(description="Serve the DPDP SLM test UI.")
    ap.add_argument("--port", type=int, default=8700)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--lan", action="store_true",
                    help="serve on the local network so another device can open it")
    args = ap.parse_args()

    META, INDEX_BM25 = load()
    PENALTIES = ask.penalties_from(INDEX_BM25)
    engines = engine_report()
    httpd = bind(args.port, "0.0.0.0" if args.lan else "127.0.0.1")
    port = httpd.server_address[1]
    url = "http://localhost:{}".format(port)

    print("corpus : {} records".format(len(INDEX_BM25.records)))
    for name in ("native", "ollama", "openai"):
        e = engines[name]
        mark = "ready" if e["available"] else "not running"
        extra = ""
        if name == "native":
            extra = " (ours, no model needed - default)"
        elif e["models"]:
            extra = ": " + ", ".join(e["models"][:4])
        elif e["host"]:
            extra = " at " + e["host"]
        print("engine : {:<11} {}{}".format(name, mark, extra))
    print("serving: {}  (ctrl-c to stop)".format(url))
    if args.lan:
        ip = lan_ip()
        if ip:
            print("    LAN: http://{}:{}  <- open this from another device"
                  .format(ip, port))
        print("    NOTE --lan exposes the UI to everyone on this network.")
        if TOKEN:
            print("    auth: on - append ?k=<DPDP_TOKEN> to the link.")
        else:
            print("    auth: OFF. Anyone who reaches this can ask questions,")
            print("          and data/traces.jsonl records every one of them.")
            print("          Before putting it behind a public tunnel, set")
            print("          DPDP_TOKEN and share the link as ?k=<token>.")
    else:
        print("    local only. Another device needs --lan (same network),"
              " or use the published page.")

    if not args.no_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
