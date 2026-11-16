"""Run eval.py's cases against a deployed instance instead of the local code.

    python eval_remote.py https://dpdp-clause-tool.vercel.app

Proves the deployment serves the same answers the local tests check, rather
than an older build that happens to be live.
"""
import json
import sys
import urllib.request

from eval import CASES

sys.stdout.reconfigure(encoding="utf-8")


def ask(base, question):
    body = json.dumps({"question": question, "k": 4, "engine": "native",
                       "history": []}).encode()
    req = urllib.request.Request(base.rstrip("/") + "/api/ask", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read().decode("utf-8", "replace")
    answer, cites = "", []
    for frame in raw.split("\n\n"):
        if not frame.startswith("data: "):
            continue
        ev = json.loads(frame[6:])
        if ev["type"] == "retrieved":
            cites = [h["citation"] for h in ev["hits"]]
        elif ev["type"] == "answer_done":
            answer = ev["answer"]
    return cites, answer


def main(base):
    passed = 0
    for question, want_cite, want_phrase in CASES:
        try:
            cites, answer = ask(base, question)
        except Exception as exc:
            print("ERROR {}: {}".format(question[:40], exc))
            continue
        ok = (want_cite is None or want_cite in cites) and \
             want_phrase.lower() in answer.lower()
        passed += ok
        if not ok:
            print("FAIL  {}\n      want {} / {!r}\n      got  {}".format(
                question, want_cite, want_phrase, cites))
    print("\n{}: {}/{} ({:.0f}%)".format(base, passed, len(CASES),
                                         100 * passed / len(CASES)))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "https://dpdp-clause-tool.vercel.app")
