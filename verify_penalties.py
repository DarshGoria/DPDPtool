"""Check the hand-transcribed penalty table against the Gazette text itself.

The Act's Schedule is a three-column table. pdftotext returns the columns
interleaved - all five descriptions run together as one paragraph, then all
five amounts as separate lines - so the table cannot be parsed row by row and
the amounts in build_corpus.PENALTIES were transcribed by hand.

Transcribed money figures in a compliance tool need to be earned, not
asserted, so build_corpus derives its `verified_against_gazette` flag from
this check instead of hard-coding it. If re-extraction ever changes the text,
the flag goes back to False on its own.

Rows 1-5 have a clean amount each and are checked by position. Rows 6 and 7
are checked by presence only: the Gazette breaks them mid-phrase across the
column gutter ("May extend to fifty" ... "crore rupees"), so their words do
not appear contiguously and there is nothing left to confuse them with.
"""
import re

HEAD = "THE SCHEDULE [See section 33"

# The five clean rows, in the order the Gazette prints them.
ORDERED = [
    ("sub-section (5) of section 8", "two hundred and fifty crore rupees"),
    ("sub-section (6) of section 8", "two hundred crore rupees"),
    ("section 9", "two hundred crore rupees"),
    ("section 10", "one hundred and fifty crore rupees"),
    ("section 15", "ten thousand rupees"),
]
# Rows 6 and 7: fragments that must all be present after the clean block.
TAIL = [
    "voluntary undertaking",
    "section 32",
    "any other provision",
    "May extend to fifty",
    "crore rupees",
]
AMOUNT = re.compile(r"May extend to ([a-z ]+?(?:crore|thousand) rupees)\.")


def region(act_text):
    """The Schedule, from its heading to the Gazette's closing rule."""
    at = act_text.find(HEAD)
    if at < 0:
        return ""
    body = act_text[at:]
    end = body.find("————")  # the four-em-dash sign-off
    return " ".join((body[:end] if end > 0 else body).split())


def verify(act_text, penalties):
    """Return (ok, problems). `penalties` is build_corpus.PENALTIES."""
    bad = []
    text = region(act_text)
    if not text:
        return False, ["penalty Schedule not found in the extracted Act text"]

    # The amount column, in document order. Rows 6-7 are split across the
    # gutter so they do not match; that is what TAIL is for.
    found = AMOUNT.findall(text)
    want = [amount for _ref, amount in ORDERED]
    if found[: len(want)] != want:
        bad.append("amount column reads {} , expected {}".format(found[:len(want)], want))

    # Every description must cite its provision, and in the same order, or a
    # row's amount belongs to a different section than we think.
    at = 0
    for ref, _amount in ORDERED:
        found_at = text.find(ref, at)
        if found_at < 0:
            bad.append("row for {!r} not found in document order".format(ref))
            break
        at = found_at + len(ref)

    for fragment in TAIL:
        if fragment not in text:
            bad.append("row 6/7 fragment {!r} missing".format(fragment))

    # Finally the hand table itself must say what the Gazette says.
    table = [(p["provision"], p["max_words"]) for p in penalties]
    expect = [
        ("Section 8(5)", "two hundred and fifty crore rupees"),
        ("Section 8(6)", "two hundred crore rupees"),
        ("Section 9", "two hundred crore rupees"),
        ("Section 10", "one hundred and fifty crore rupees"),
        ("Section 15", "ten thousand rupees"),
        ("Section 32", "up to the extent applicable for the underlying breach"),
        ("residual", "fifty crore rupees"),
    ]
    if table != expect:
        bad.append("PENALTIES no longer matches the verified reading: {}".format(table))

    return not bad, bad


if __name__ == "__main__":
    from pathlib import Path

    import build_corpus

    act = (Path(__file__).parent / "data" / "act2023.txt").read_text(encoding="utf-8")
    ok, problems = verify(act, build_corpus.PENALTIES)
    print("verified against Gazette:", ok)
    for p in problems:
        print("  -", p)
