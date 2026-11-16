"""Mechanical retrieval coverage over every provision, with no hand-picked cases.

eval.py has 24 cases that I wrote while tuning the retriever, so a perfect
score there partly measures how well the retriever matches my expectations.
This file removes the author from the loop: for each of the 75 provisions it
builds a query out of that provision's own most distinctive vocabulary -
highest tf-idf terms, chosen by the index, not by me - and asks whether the
provision comes back.

A failure means retrieval cannot find a provision even when asked using the
words that provision alone uses. That is a real defect regardless of taste.

It is a floor, not a ceiling: real users do not type statutory vocabulary,
which is what eval_paraphrase.py measures. Run both.

  python eval_coverage.py        score
  python eval_coverage.py -v     every miss, with what came back instead
"""
import sys

from retrieve import load

TERMS = 5  # query length; enough to be specific, short enough to be a question
TOP = 3


def distinctive(index, position, n=TERMS):
    """The terms this provision uses and the rest of the statute mostly does not.

    Uses the index's own term frequencies and idf rather than recomputing
    them, which also means the query is scored on exactly the vocabulary
    retrieval sees - no second tokenizer to drift.
    """
    scored = [(tf * index.idf.get(term, 0.0), term)
              for term, tf in index.freqs[position].items() if len(term) >= 3]
    scored.sort(key=lambda r: (-r[0], r[1]))
    return [t for _s, t in scored[:n]]


def run(verbose=False):
    _meta, index = load()
    hit1 = hitk = 0
    misses = []

    for position, record in enumerate(index.records):
        terms = distinctive(index, position)
        if not terms:
            continue
        query = " ".join(terms)
        results = index.search(query, k=TOP)
        got = [r["citation"] for _s, r in results]
        if got and got[0] == record["citation"]:
            hit1 += 1
            hitk += 1
        elif record["citation"] in got:
            hitk += 1
        else:
            misses.append((record["citation"], record["title"][:48], query, got))

    n = len(index.records)
    if verbose:
        for cite, title, query, got in misses:
            print("-" * 72)
            print("MISS", cite, "-", title)
            print("     query:", query)
            print("     got  :", got)
    print("\nprovisions           : {}".format(n))
    print("retrieved at rank 1  : {:>2}/{} ({:.0f}%)".format(hit1, n, 100 * hit1 / n))
    print("retrieved in top {}   : {:>2}/{} ({:.0f}%)".format(TOP, hitk, n, 100 * hitk / n))
    return hitk, n


if __name__ == "__main__":
    run(verbose="-v" in sys.argv)
