"""Measure what the local model gets wrong, through the real HTTP endpoint.

The native engine quotes and cannot invent a citation. The 3B behind the
openai engine can and does - it has produced "Rule 3(2)(ii)" where Rule 3 has
clauses (a) to (c) and no sub-rule (2) at all. That was reported as the model
"making facts as it goes", and until now there was no number attached to it.

This drives the bench's own /api/ask over HTTP, so it exercises the whole
chain - retrieval, prompt building, streaming, the citation frame - rather
than calling the model directly. It reports three things per question:

  fabricated  citations that do not exist in our parse of the statute
  offcontext  citations that exist but were not retrieved, so nothing in
              front of the model verified what it said about them
  grounded    citations the model was actually shown

A 3B on CPU takes a while per answer, so this is slow by nature. Run it in
the background and read the summary.

  python eval_llm.py                          native engine (fast, baseline)
  python eval_llm.py --engine openai          the local model
  python eval_llm.py --engine openai --n 4    fewer questions
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request

BENCH = "http://127.0.0.1:8700"

# (question, a phrase the answer must contain to have actually answered it)
#
# Chosen so a fabricated sub-clause number is tempting: each has a real answer
# at sub-clause level, which is exactly where the model invents. The phrase
# matters as much as the citation - the model once answered "how fast must I
# report a breach?" with "DOES NOT COMPLY ... the user's facts do not provide
# information about the time taken", citing a real provision and never saying
# seventy-two hours. Clean citations on a non-answer is still a failure.
QUESTIONS = [
    ("how fast must I report a breach to the Board?", "seventy-two"),
    ("what security measures are mandatory at minimum?", "encryption"),
    ("do I need parental consent for a 16 year old?", "verifiable consent"),
    ("what must a notice to the data principal contain?", "purpose"),
    ("how long must I retain logs?", "one year"),
    # Alternatives, because the first version demanded the statute's exact
    # words: the model answered "allow the transfer of personal data outside
    # India", which is correct, and was scored a failure for saying India
    # instead of the territory of India. The check is whether the answer
    # conveys the requirement, not whether it matches a phrasebook.
    ("can I transfer personal data outside India?",
     ("outside the territory", "outside india")),
    ("what extra duties does a Significant Data Fiduciary have?",
     "Impact Assessment"),
    ("how long do I have to resolve a grievance?", "ninety days"),
]


def ask(bench, question, engine, token=""):
    """POST to /api/ask and collect the SSE frames. Returns a dict or None."""
    url = bench.rstrip("/") + "/api/ask" + ("?k=" + token if token else "")
    body = json.dumps({"question": question, "engine": engine, "k": 4}).encode()
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"})
    out = {"answer": "", "cites": None, "hits": [], "error": None}
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data: "):
                    continue
                frame = json.loads(line[6:])
                kind = frame.get("type")
                if kind == "retrieved":
                    out["hits"] = [h["citation"] for h in frame["hits"]]
                elif kind == "citations":
                    out["cites"] = frame
                elif kind == "answer_done":
                    out["answer"] = frame["answer"]
                elif kind == "error":
                    out["error"] = frame.get("message")
    except (urllib.error.URLError, TimeoutError) as exc:
        out["error"] = str(exc)
    return out


def run(bench, engine, questions, token="", verbose=False):
    fabricated = offcontext = clean = failed = answered = 0
    rows = []
    for question, want in questions:
        started = time.time()
        result = ask(bench, question, engine, token)
        took = time.time() - started
        if result["error"]:
            failed += 1
            print("FAILED  {:<52} {}".format(question[:52], result["error"]))
            continue
        cites = result["cites"] or {}
        bad = cites.get("unknown") or []
        off = cites.get("offcontext") or []
        fabricated += len(bad)
        offcontext += len(off)
        if not bad and not off:
            clean += 1
        wants = (want,) if isinstance(want, str) else want
        low = result["answer"].lower()
        said = any(w.lower() in low for w in wants)
        answered += said
        rows.append((question, bad, off, cites.get("grounded") or [], took))
        flag = "FABRICATED" if bad else ("off-context" if off else "clean")
        print("{:<11} {:<9} {:>6.1f}s  {:<42} {}".format(
            flag, "answered" if said else "NO ANSWER", took, question[:42],
            ", ".join(bad) if bad else (", ".join(off) if off else "")))
        if verbose and (bad or off or not said):
            print("   wanted:", repr(want))
            print("   answer:", " ".join(result["answer"].split())[:400])
            print("   shown :", result["hits"])

    n = len(questions)
    print("\nengine            : {}".format(engine))
    print("questions         : {} ({} failed to answer)".format(n, failed))
    print("clean citations   : {}/{}".format(clean, n - failed if n > failed else n))
    print("actually answered : {}/{}".format(answered, n))
    print("fabricated cites  : {}".format(fabricated))
    print("off-context cites : {}".format(offcontext))
    if rows:
        print("mean time         : {:.1f}s".format(
            sum(r[4] for r in rows) / len(rows)))
    return fabricated, failed


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bench", default=BENCH)
    parser.add_argument("--engine", default="native")
    parser.add_argument("--token", default="")
    parser.add_argument("--n", type=int, default=len(QUESTIONS))
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    bad, failed = run(args.bench, args.engine, QUESTIONS[:args.n],
                      args.token, args.verbose)
    sys.exit(1 if failed else 0)
