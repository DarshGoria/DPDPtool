"""The untuned number. Written after all tuning, run once, not fixed against.

eval_paraphrase.py and eval_holdout.py both reached 100% - but both were used
to find and fix synonym gaps, so neither measures generalisation any more.
This file was written after the synonym layer was finished and is the only
remaining set that nothing was tuned against.

The phrasing is deliberately messier than the other two files: no
capitalisation, missing apostrophes, typos, Indian-English register, and the
run-on sentences people actually type into a box. If retrieval only works on
tidy questions it does not work.

FIRST RUN, BEFORE ANY FIX: 23/25 (92%) retrieval, 3/3 not asserted.
That 92% is the honest untuned figure. The two failures were then fixed,
because both were real defects rather than phrasing tricks:

  "is encrpytion mandatory"  - returned zero provisions. One typo in the only
    content word killed exact-match BM25 entirely. Fixed generally, with a
    spell fallback over the statute's own vocabulary (chat.spell_fix), not by
    adding that word to a list.
  "...use last years customer data to train a model..." - went to the
    retention and breach rules. The statute has no word for machine learning,
    and reusing held data for a new purpose is the most common real question
    there is. Fixed with two synonym rules.

So this file now reads 100% and is a REGRESSION check, not a measure of
generalisation. The next honest number needs a fourth set, written without
looking at these.

  python eval_final.py
  python eval_final.py -v
"""
import sys

import chat
from retrieve import load

CASES = [
    # terse, lowercase, no punctuation
    ("breach reporting timeline", ["Rule 7"]),
    ("parental consent age limit", ["Section 9", "Rule 10"]),
    ("max fine security lapse", ["The Schedule"]),
    ("data deletion timeline ecommerce", ["Rule 8", "Third Schedule"]),
    ("who is the board", ["Section 18", "Section 19", "Section 2"]),

    # typos and missing apostrophes
    ("do we need consnt to process salary slips", ["Section 4", "Section 6"]),
    ("whats the penalty if we dont report a leak", ["The Schedule", "Rule 7"]),
    ("can i keep data after user delets account", ["Rule 8", "Section 8"]),
    ("is encrpytion mandatory", ["Rule 6"]),
    ("childrens data rules for edtech", ["Rule 12", "Section 9", "Fourth Schedule"]),

    # run-on, how people actually type
    ("so we collect aadhaar and pan for kyc and keep it forever is that fine",
     ["Rule 8", "Section 8", "Section 5", "Third Schedule"]),
    ("our app asks for sms permission on install and we read bank alerts to "
     "decide the loan amount do we need anything special",
     ["Section 4", "Section 5", "Section 6"]),
    ("if a user emails us asking to delete everything how fast must we act",
     ["Section 12", "Rule 8", "Section 8"]),
    ("we want to use last years customer data to train a model is that allowed",
     ["Section 4", "Section 6", "Section 5", "Section 8"]),
    ("my cofounder says we dont need consent because we show a popup is he right",
     ["Section 6", "Section 5"]),

    # Indian-English register
    ("kindly tell what all we must inform the user at the time of signup",
     ["Section 5", "Rule 3"]),
    ("what is the time period for grievance closure", ["Rule 14"]),
    ("is it compulsory to appoint a data protection officer",
     ["Rule 9", "Section 10"]),
    ("whether consent once given can be taken back", ["Section 6"]),
    ("what all obligations come on a significant data fiduciary",
     ["Section 10", "Rule 13"]),

    # adversarial but legitimate
    ("we are a b2b saas we never touch consumer data does this still apply",
     ["Section 3", "Section 2"]),
    ("can we charge a fee for a data access request",
     ["Section 11", "Section 12", "Section 13"]),
    ("what if the parent is also a minor", ["Section 9", "Rule 10"]),
    ("our processor is in the philippines any issue",
     ["Section 16", "Rule 15", "Section 8"]),
    ("do we have to tell users when we change our privacy policy",
     ["Section 5", "Section 6", "Rule 3"]),

    # should be refused or hedged, never asserted
    ("what is the dpdp equivalent in japan", None),
    ("draft me a privacy policy", None),
    ("who won the world cup", None),
]
DECLINE = ("outside the dpdp act", "not covered", "nothing in the",
           "which i do not cover", "not confident")
ASSERTS = ("yes -", "no -")


def run(verbose=False):
    _meta, index = load()
    answerable = [c for c in CASES if c[1] is not None]
    refusable = [c for c in CASES if c[1] is None]

    passed, misses = 0, []
    for question, accept in answerable:
        hits, _query = chat.search(index, question, None, k=4)
        got = [r["citation"] for _s, r in hits]
        if any(a in got for a in accept):
            passed += 1
        else:
            misses.append((question, accept, got))

    import ask
    penalties = ask.penalties_from(index)
    held, bad = 0, []
    for question, _ in refusable:
        hits, query = chat.search(index, question, None, k=4)
        answer = chat.respond(question, hits, penalties=penalties, query=query)
        low = answer.lower().lstrip()
        if any(d in low for d in DECLINE) or not low.startswith(ASSERTS):
            held += 1
        else:
            bad.append((question, " ".join(answer.split())[:140]))

    if verbose:
        for question, accept, got in misses:
            print("-" * 72)
            print("MISS", question)
            print("     want any of:", accept)
            print("     got        :", got)
        for question, answer in bad:
            print("-" * 72)
            print("ASSERTED ON", question, "->", answer)

    n = len(answerable)
    print("\nuntuned retrieval  : {:>2}/{} ({:.0f}%)".format(
        passed, n, 100 * passed / n))
    print("not asserted       : {:>2}/{} ({:.0f}%)".format(
        held, len(refusable), 100 * held / len(refusable)))
    return passed, n


if __name__ == "__main__":
    run(verbose="-v" in sys.argv)
