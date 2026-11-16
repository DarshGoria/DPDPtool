"""A held-out set, written before the synonym layer was tuned and not looked at.

eval_paraphrase.py scored 66% the first time it ran. Fixing those 22 misses
means tuning against that file, after which its score measures the tuning,
not the tool. So these 25 cases were written in the same sitting, before any
fix, and the per-case failures were deliberately not inspected while tuning -
only the final aggregate.

The gap between paraphrase and holdout is the honest estimate of how much of
the paraphrase score is real and how much is fitted. A big gap means the
synonym layer memorised phrasings instead of generalising.

Do not tune against this file. If it needs to be used for tuning, write a
new holdout first.

  python eval_holdout.py
"""
import sys

import chat
from retrieve import load

CASES = [
    ("is a company in Bengaluru selling only to Indians inside the law?",
     ["Section 3"]),
    ("by when must we be ready for the bits about security?", ["Rule 1"]),
    ("on what footing are we even allowed to touch user records?",
     ["Section 4", "Section 7"]),
    ("what goes on the screen before somebody taps agree?",
     ["Section 5", "Rule 3"]),
    ("must the ask be in a tongue the user actually reads?",
     ["Section 6", "Rule 3"]),
    ("if a customer revokes their okay, what follows?",
     ["Section 6", "Section 8"]),
    ("are we free to reuse the records for a brand new purpose?",
     ["Section 4", "Section 6", "Section 5"]),
    ("who answers for it when the firm we subcontract to loses the files?",
     ["Section 8"]),
    ("must the figures we store about a person be kept right?",
     ["Section 8"]),
    ("are we obliged to lock the files so outsiders cannot read them?",
     ["Rule 6"]),
    ("must we keep a trail of every peek at a record?", ["Rule 6"]),
    ("after a spill, how long before the watchdog must know?", ["Rule 7"]),
    ("must the people whose records spilled hear about it from us?",
     ["Rule 7"]),
    ("when does stale information have to go in the bin?",
     ["Rule 8", "Third Schedule", "Section 8"]),
    ("must we give notice before we wipe a dormant account?", ["Rule 8"]),
    ("a fifteen year old signed up, whose yes do we need?",
     ["Section 9", "Rule 10"]),
    ("may we aim promotions at schoolchildren?", ["Section 9"]),
    ("if the regulator calls us a heavyweight, what lands on us?",
     ["Section 10", "Rule 13"]),
    ("somebody wants a readout of everything on file about them",
     ["Section 11"]),
    ("a user says the spelling of their name is wrong, must we amend it?",
     ["Section 12"]),
    ("within what window must a gripe be settled?", ["Rule 14"]),
    ("may a person appoint a stand-in to use their entitlements?",
     ["Section 14"]),
    ("are we permitted to park the records on a machine abroad?",
     ["Section 16", "Rule 15"]),
    ("are state agencies let off any of this?", ["Section 17"]),
    ("what is the ceiling on a fine for sloppy security?",
     ["The Schedule"]),
]


def run(verbose=False):
    _meta, index = load()
    passed, misses = 0, []
    for question, accept in CASES:
        hits, _query = chat.search(index, question, None, k=4)
        got = [r["citation"] for _s, r in hits]
        if any(a in got for a in accept):
            passed += 1
        else:
            misses.append((question, accept, got))
    if verbose:
        for question, accept, got in misses:
            print("-" * 72)
            print("MISS", question)
            print("     want any of:", accept)
            print("     got        :", got)
    n = len(CASES)
    print("\nheld-out retrieval : {:>2}/{} ({:.0f}%)".format(passed, n, 100 * passed / n))
    return passed, n


if __name__ == "__main__":
    run(verbose="-v" in sys.argv)
