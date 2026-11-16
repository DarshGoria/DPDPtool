"""Zero-download answerer: quotes the best-matching sub-clauses, cited.

It generates nothing, so it cannot hallucinate a section number - which makes
it both a usable answer on a machine with no model and the baseline any real
model has to beat. If an SLM scores worse than this, the SLM is not earning
its disk space.
"""
import re

from retrieve import Index, tokenize

# Lettered items inside a sub-clause: "(a) appropriate data security measures".
LETTER = re.compile(r"\(([a-z])\)\s")
ROMAN = re.compile(r"\(([ivx]{1,4})\)\s")
# Numbered items in a Schedule: "4. The net worth of the applicant...".
NUMBERED = re.compile(r"(?:^|\s)(\d{1,2})\.\s")

# Below this, a unit is a fragment like "security of the State; and" that can
# outscore real provisions on a single shared word.
MIN_TOKENS = 6


# "clause (b) of section 7" is a reference, not the start of item (b).
REFERENCE = re.compile(
    r"(sub-?section|sub-?rule|clause|section|rule|item|paragraph|schedule|"
    r"under|of|in|to|and|or|with)\s*$", re.I)
# ...but "...; and (c) give, the particular communication link..." is how a
# statute ends a list, and treating that "and" as a reference dropped the last
# item of 30 of them. Mirrors build_corpus.LIST_CONTINUES.
LIST_CONTINUES = re.compile(r"[;:,]\s*(and|or)\s*$", re.I)


def _split_sequential(text, pattern, first):
    """Split on markers that run in order from `first`, ignoring cross-references.

    An item runs to the next ACCEPTED marker, not to the next thing that looks
    like one: ending at the next raw match truncated clauses at the first
    cross-reference that followed them.
    """
    accepted, expect = [], first
    for m in pattern.finditer(text):
        if m.group(1) != expect:
            continue
        before = text[max(0, m.start() - 40):m.start()]
        if REFERENCE.search(before) and not LIST_CONTINUES.search(before):
            continue
        accepted.append((expect, m.start(), m.end()))
        expect = chr(ord(expect) + 1) if len(expect) == 1 else None
        if expect is None:
            break

    out = []
    for i, (key, _start, content_at) in enumerate(accepted):
        end = accepted[i + 1][1] if i + 1 < len(accepted) else len(text)
        out.append((key, text[content_at:end].strip()))
    return out


def _numbered_items(text, first=1):
    """Split a Schedule's '1. ... 2. ...' list, in order."""
    accepted, expect = [], first
    for m in NUMBERED.finditer(text):
        if int(m.group(1)) != expect:
            continue
        accepted.append((expect, m.start(), m.end()))
        expect += 1

    out = []
    for i, (key, _start, content_at) in enumerate(accepted):
        end = accepted[i + 1][1] if i + 1 < len(accepted) else len(text)
        out.append((key, text[content_at:end].strip()))
    return out


PART = re.compile(r"\bPART ([A-D])\b")


def _schedule_units(citation, flat):
    """Number a Schedule's items per Part.

    Parts restart at 1, so numbering straight through the whole Schedule
    silently relabels Part B items as Part A ones - a wrong citation, which is
    worse than no answer.
    """
    parts = list(PART.finditer(flat))
    if not parts:
        for n, chunk in _numbered_items(flat):
            yield "{} item {}".format(citation, n), chunk
        return
    if parts[0].start() > 0:
        for n, chunk in _numbered_items(flat[: parts[0].start()]):
            yield "{} item {}".format(citation, n), chunk
    for i, m in enumerate(parts):
        end = parts[i + 1].start() if i + 1 < len(parts) else len(flat)
        for n, chunk in _numbered_items(flat[m.end():end]):
            yield "{} Part {} item {}".format(citation, m.group(1), n), chunk


def units(record):
    """Yield (citation, text) at the finest citable granularity available."""
    subs = record.get("subclauses") or {}
    if not subs:
        flat = " ".join(record["text"].split())
        # Schedules carry their substance in numbered items, not sub-clauses.
        found = list(_schedule_units(record["citation"], flat))
        if len(found) >= 3:
            yield from found
            return
        yield record["citation"], flat
        return
    for label, body in subs.items():
        cite_base = record["citation"].split()[0] + " " + label
        letters = _split_sequential(body, LETTER, "a")
        if len(letters) >= 2:
            head = body[: body.index("(a) ")].strip() if "(a) " in body else ""
            if len(head.split()) > 4:
                yield cite_base, head
            for letter, chunk in letters:
                romans = _split_sequential(chunk, ROMAN, "i")
                if len(romans) >= 2:
                    for rn, rchunk in romans:
                        yield "{}({})({})".format(cite_base, letter, rn), rchunk
                else:
                    yield "{}({})".format(cite_base, letter), chunk
        else:
            yield cite_base, body


def rank(question, hits, limit=6, idf=None):
    """Rank the sub-clauses of the retrieved records against the question.

    Scored with the same BM25 as document retrieval, re-fitted over just these
    sub-clauses: its length normalisation is what stops a long omnibus item
    outranking the one short clause that actually answers the question, and
    re-fitting makes "net worth" rare (so decisive) among these candidates
    while "consent manager" is in every second line (so near worthless).
    """
    pool = []
    for _doc_score, record in hits:
        for cite, text in units(record):
            if len(tokenize(text)) >= MIN_TOKENS:
                pool.append({"citation": cite, "text": text})
    if not pool:
        return []

    index = Index(pool, text_fn=lambda r: r["text"])
    picked, seen = [], set()
    for score, rec in index.search(question, k=limit * 3):
        key = rec["text"][:60]
        if key in seen:
            continue
        seen.add(key)
        picked.append((score, rec["citation"], rec["text"]))
        if len(picked) >= limit:
            break
    return picked


def answer(question, hits, limit=6, idf=None):
    """Build the cited extractive answer as one string."""
    picked = rank(question, hits, limit=limit, idf=idf)
    if not picked:
        return "NOT COVERED BY RETRIEVED PROVISIONS"
    lines = ["Most relevant provisions, quoted verbatim:", ""]
    for _score, cite, text in picked:
        if len(text) > 420:
            text = text[:417].rsplit(" ", 1)[0] + "..."
        lines.append("{} - {}".format(cite, text))
        lines.append("")
    lines.append("(Extractive baseline: statute text only, nothing generated.)")
    return "\n".join(lines).strip()


def stream(question, hits, limit=6, idf=None):
    """Yield the answer in chunks so the UI streams it like a model."""
    for line in answer(question, hits, limit=limit, idf=idf).splitlines(keepends=True):
        yield line
