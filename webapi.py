"""Shared logic for the Vercel functions in api/.

Kept out of api/ so Vercel does not expose it as a route, and out of serve.py
so the local bench stays a single process. The engine is the built-in one:
a serverless function cannot host a 2 GB model.
"""
import json
from pathlib import Path

import ask
import chat
import citecheck
from retrieve import Index

ROOT = Path(__file__).resolve().parent

_CORPUS = json.loads((ROOT / "data" / "corpus.json").read_text(encoding="utf-8"))
META = _CORPUS["meta"]
INDEX = Index(_CORPUS["records"])
PENALTIES = next(
    (r.get("penalties") for r in _CORPUS["records"] if r["id"] == "act-schedule"),
    None,
)


def retrieve(question, k, history=None):
    return chat.search(INDEX, question, history, k=k)


def hits_payload(hits):
    return [{
        "id": r["id"], "source": r["source"], "citation": r["citation"],
        "title": r["title"], "score": round(s, 2),
        "in_force": r.get("in_force"), "max_penalty_inr": r.get("max_penalty_inr"),
        "tags": r.get("tags", []), "text": r["text"],
        "subclauses": list(r.get("subclauses", {})),
    } for s, r in hits]


def health():
    return {
        # No model can run in a serverless function; the UI hides the rest.
        "engines": {
            "native": {"available": True, "models": [], "host": None},
            "ollama": {"available": False, "models": [], "host": None},
            "openai": {"available": False, "models": [], "host": None},
        },
        "reachable": False,
        "default_model": "built-in",
        "records": len(INDEX.records),
        "sources": META["sources"],
        "commencement": META["commencement"],
        "penalties_verified": META["penalties_verified_against_gazette"],
        "hosted": True,
    }


def clean_history(raw):
    return [m for m in (raw or [])
            if m.get("role") in ("user", "assistant") and m.get("content")][-8:]


def answer_frames(question, k, history):
    """The same SSE frames the local bench sends, so one UI serves both."""
    hits, query = retrieve(question, k, history)
    answer = chat.respond(question, hits, history=history, penalties=PENALTIES,
                          query=query)
    # Same check the bench runs on the LLM engines. Here it should always come
    # back clean, which is the point: it proves the quote-only claim per answer
    # instead of asking anyone to take it on trust.
    cites = ask.check_citations(answer, hits, INDEX)
    # Same shape checks the bench runs; the built-in engine never trips them,
    # which is the point of running them here too.
    cites["issues"] = ask.answer_problems(answer)
    return [
        {"type": "retrieved", "hits": hits_payload(hits)},
        {"type": "prompt",
         "prompt": "(built-in engine: composed from statute text directly, "
                   "no prompt is sent to any model)",
         "chars": 0},
        {"type": "token", "t": answer},
        {"type": "citations", **cites,
         "warning": " | ".join([w for w in [citecheck.warning(cites)] if w]
                               + cites["issues"]) or None},
        {"type": "answer_done", "answer": answer, "id": None},
        {"type": "done"},
    ]


def sse(frames):
    return "".join("data: " + json.dumps(f) + "\n\n" for f in frames)


__all__ = ["ask", "chat", "health", "retrieve", "hits_payload",
           "clean_history", "answer_frames", "sse"]
