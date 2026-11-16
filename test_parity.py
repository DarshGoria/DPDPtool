"""Do the Python engine and its JavaScript port actually agree?

web/chat.js re-implements chat.py's control flow by hand so the static pages
in dist/ can answer with no server at all. Every pattern and threshold is
generated from chat.py by export_chat_tables.py, which stops the *tables*
drifting - but nothing stopped the *logic* drifting, and it had: the port
still carried the two-filter bug in withChildren, had no cross-references, no
spell fallback and the old inflated scope gate.

This runs the same questions through both and diffs the retrieved citations.
It needs the built page served over HTTP, because the port reads its corpus
from a <script> tag:

    python build_web.py
    python -m http.server 8701 --bind 127.0.0.1 --directory dist
    python test_parity.py                 # uses http://127.0.0.1:8701

Exits non-zero on any disagreement. A disagreement is a real defect: the two
surfaces would answer the same question differently.
"""
import json
import sys
import urllib.error
import urllib.request

import chat
from retrieve import load

URL = "http://127.0.0.1:8701/dpdp_bench.html"

# Spread across every feature the port had to be taught, not just the easy
# lexical cases: cross-references, the spell fallback, the scope gate, the
# other-regime refusal, and the withChildren fix.
QUESTIONS = [
    "how fast must I report a breach to the Board?",
    "what must I tell users after a data breach?",
    "what security measures are mandatory at minimum?",
    "do I need parental consent for a 16 year old?",
    "what is the maximum penalty for a security failure?",
    "can consent be withdrawn?",
    "how long do I have to resolve a grievance?",
    "can I store personal data outside India?",
    "is encrpytion mandatory",
    "we want to use last years customer data to train a model is that allowed",
    "what are the GDPR fines in Europe?",
    "how much income tax do I owe this year?",
    "what is the capital of France?",
    "do we need cyber insurance?",
    "our app has sixteen year old users, what changes for us?",
    "a customer has asked what information we hold on them",
    "what does it cost us if we get the children's rules wrong?",
    "after a spill, how long before the watchdog must know?",
]


# (previous question, this question). The port has its own copy of the
# standalone-vs-merged logic, and nothing checked it until the conversational
# fixes landed.
CONVERSATIONS = [
    ("can I transfer personal data outside India?", "what conditions apply to that?"),
    ("how fast must I report a breach to the Board?",
     "and what must I tell the affected users?"),
    ("we use sms of users to read the credited amount and show prefills",
     "i use users data to make them login"),
    ("how long must I retain logs?", "do I need parental consent for a 16 year old?"),
]


def python_side(index, penalties, question, history=None):
    hits, query = chat.search(index, question, history, k=4)
    answer = chat.respond(question, hits, penalties=penalties, query=query)
    return {
        "cites": [r["citation"] for _s, r in hits],
        "crossref": [r["citation"] for _s, r in hits if r.get("crossref")],
        "head": " ".join(answer.split())[:60],
    }


def compare(mine, theirs):
    """Report the differences between one question's two results."""
    problems = []
    if mine["cites"] != theirs.get("cites"):
        problems.append("citations: python={} js={}".format(
            mine["cites"], theirs.get("cites")))
    if mine["crossref"] != theirs.get("crossref"):
        problems.append("cross-refs: python={} js={}".format(
            mine["crossref"], theirs.get("crossref")))
    # Only the opening clause of the answer, because the port wraps lines
    # differently; the verdict and the leading citation are what matter.
    if mine["head"][:40] != (theirs.get("head") or "")[:40]:
        problems.append("answer opens differently:\n      python: {}\n      js:     {}"
                        .format(mine["head"], theirs.get("head")))
    return problems


def main(js_results):
    _meta, index = load()
    import ask
    penalties = ask.penalties_from(index)

    failures = 0
    for question in QUESTIONS:
        mine = python_side(index, penalties, question)
        theirs = js_results.get(question)
        if theirs is None:
            print("NO JS RESULT for", question)
            failures += 1
            continue
        problems = compare(mine, theirs)
        if problems:
            failures += 1
            print("-" * 72)
            print("DISAGREE:", question)
            for p in problems:
                print("   ", p)

    print("\nparity: {}/{} questions agree".format(
        len(QUESTIONS) - failures, len(QUESTIONS)))
    return failures


EXPECT = "dist/py_expect.json"
# The diff runs in the page rather than here, so the browser only has to
# return the disagreements instead of every result.
BROWSER_SNIPPET = """
fetch('py_expect.json').then(r => r.json()).then(expect => {
  const bad = [];
  for (const [q, want] of Object.entries(expect)) {
    const f = window.DPDP_PORT.search(q, [], 4);
    const a = window.DPDP_PORT.respond(q, f.hits, [], window.DPDP_PORT.penalties(), f.query);
    const got = {
      cites: f.hits.map(h => h.rec.cite),
      crossref: f.hits.filter(h => h.rec.crossref).map(h => h.rec.cite),
      head: a.split(/\\s+/).join(' ').slice(0, 60)
    };
    const diff = {};
    if (JSON.stringify(got.cites) !== JSON.stringify(want.cites)) diff.cites = [want.cites, got.cites];
    if (JSON.stringify(got.crossref) !== JSON.stringify(want.crossref)) diff.crossref = [want.crossref, got.crossref];
    if (got.head.slice(0, 40) !== want.head.slice(0, 40)) diff.head = [want.head, got.head];
    if (Object.keys(diff).length) bad.push([q, diff]);
  }
  return JSON.stringify({ total: Object.keys(expect).length, disagree: bad }, null, 1);
})
"""


def emit():
    """Write what Python answers, for the page to diff itself against."""
    _meta, index = load()
    import ask
    penalties = ask.penalties_from(index)
    out = {q: python_side(index, penalties, q) for q in QUESTIONS}
    for previous, follow in CONVERSATIONS:
        history = [{"role": "user", "content": previous},
                   {"role": "assistant", "content": "(answer)"}]
        out["AFTER<" + previous + ">" + follow] = python_side(
            index, penalties, follow, history)
    with open(EXPECT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, ensure_ascii=False)
    print("wrote {} with {} questions".format(EXPECT, len(out)))
    print("\nNow serve dist/ and run this in the page's console:")
    print(BROWSER_SNIPPET)


if __name__ == "__main__":
    if "--emit" in sys.argv or len(sys.argv) == 1:
        emit()
    else:
        results = json.loads(open(sys.argv[1], encoding="utf-8").read())
        sys.exit(1 if main(results) else 0)
