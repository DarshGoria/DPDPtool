"""Check every citation an answer makes against the statute we actually parsed.

The local 3B model's worst habit is inventing sub-clause numbers - it has
produced "Rule 3(2)(ii)" where Rule 3 has clauses (a) to (c) and no sub-rule
(2) at all. A wrong number is worse than a vague answer, because it reads as
precision. The native engine cannot do this (it only ever quotes), so this
module exists to hold the LLM to the same standard.

Each citation lands in one of three buckets:

  grounded   - it is one of the provisions that was retrieved and put in the
               prompt, so the model is citing what it was shown.
  offcontext - it exists in the Act or the Rules, but was not retrieved. The
               model is citing from memory; the text was never in front of
               it, so nothing here verified what it says about it.
  unknown    - no such provision exists in our parse. Fabricated, or a real
               sub-clause our parser missed; either way it is unverifiable.

`unknown` is reported as "not in our parse" rather than "does not exist"
because citable coverage is 96%, not 100%.
"""
import re

# Sub-clause depth the Gazette actually uses, so a citation can be tested for
# shape before it is looked up.
# At most three digits, and not followed by another: "the DPDP Rules 2025"
# was being read as a citation to Rule 2025 and then reported as fabricated,
# so the refusal message - which names the Rules - carried a warning telling
# the reader to check its own citations. The Act has 44 sections and the
# Rules 23, so no real citation comes anywhere near four digits.
SECTION = re.compile(
    r"\b(?:[Ss]ections?|[Ss]ecs?\.)\s*(\d{1,3})(?!\d)"
    r"((?:\s*\(\s*[0-9a-zA-Z]{1,4}\s*\))*)")
RULE = re.compile(
    r"\b(?:[Rr]ules?)\s*(\d{1,3})(?!\d)"
    r"((?:\s*\(\s*[0-9a-zA-Z]{1,4}\s*\))*)")
SCHEDULE = re.compile(
    r"\b(First|Second|Third|Fourth|Fifth|Sixth|Seventh|Eighth|Ninth|Tenth)"
    r"\s+Schedule((?:\s+Part\s+[A-D])?(?:\s+item\s+\d+)?)", re.I)
BARE_SCHEDULE = re.compile(r"\bThe\s+Schedule\b")

# The Act and the Rules cite other instruments. The Eighth Schedule is the
# Constitution's list of languages (section 6(3), Rule 3); flagging it as
# fabricated would be our bug, not the model's.
EXTERNAL = {"Eighth Schedule", "Ninth Schedule", "Tenth Schedule"}

ORDINALS = {o.lower(): o for o in
            ("First", "Second", "Third", "Fourth", "Fifth", "Sixth",
             "Seventh", "Eighth", "Ninth", "Tenth")}


def _parens(tail):
    """"( 2 )( b )" -> "(2)(b)" so a citation can be compared as a string."""
    return "".join("(" + p.strip() + ")"
                   for p in re.findall(r"\(\s*([0-9a-zA-Z]{1,4})\s*\)", tail or ""))


def citations(text):
    """Every citation in `text`, canonicalised, in order, without duplicates."""
    found = []
    for pattern, word in ((SECTION, "Section"), (RULE, "Rule")):
        for m in pattern.finditer(text):
            found.append((m.start(), word + " " + m.group(1) + _parens(m.group(2))))
    for m in SCHEDULE.finditer(text):
        ordinal = ORDINALS[m.group(1).lower()]
        tail = " ".join((m.group(2) or "").split())
        # Part letters are upper case in the Gazette ("Part A", "item 3").
        tail = re.sub(r"Part\s+([a-d])", lambda x: "Part " + x.group(1).upper(), tail, flags=re.I)
        tail = re.sub(r"Item\s+", "item ", tail)
        found.append((m.start(), (ordinal + " Schedule " + tail).strip()))
    for m in BARE_SCHEDULE.finditer(text):
        found.append((m.start(), "The Schedule"))

    out, seen = [], set()
    for _at, cite in sorted(found):
        if cite not in seen:
            seen.add(cite)
            out.append(cite)
    return out


def known_citations(records, units_fn):
    """Every citation the corpus can support, at record and sub-clause level."""
    known = set()
    for r in records:
        known.add(r["citation"])
        for cite, _text in units_fn(r):
            known.add(cite)
    return known


DEEPER = re.compile(r"\(([0-9a-zA-Z]{1,4})\)$")


def deeper_item(cite, texts):
    """True if `cite` is a real item one level below a clause we do have.

    Our parse stops at one level of lettering, so Rule 3(b)(i) is not a unit
    even though Rule 3(b) plainly contains "(i) an itemised description of
    such personal data". Reporting that as fabricated is the worst kind of
    wrong: it tells a reader an accurate citation was invented. So before
    calling anything unknown, the parent is looked up and its text checked
    for the marker.
    """
    match = DEEPER.search(cite)
    if not match:
        return False
    parent = cite[: match.start()]
    parent_text = texts.get(parent)
    return bool(parent_text) and "({})".format(match.group(1)) in parent_text


def check(answer, known, retrieved=(), shown_text="", texts=None):
    """Sort the answer's citations into grounded / offcontext / unknown.

    `retrieved` is the citations of the provisions that were put in the
    prompt. A citation counts as grounded if it is one of them or sits
    underneath one - a model shown Rule 7 may properly cite Rule 7(2).

    `shown_text` is the text of those provisions. Citations occurring inside
    it are the statute's own cross-references, quoted along with the clause,
    not claims the answer is making: the Act's Schedule lists "Section 8(5):
    Failure to take reasonable security safeguards...", so quoting the
    Schedule was being reported as citing five provisions from memory.
    Passing it in is what makes the off-context count mean something.

    It never excuses an `unknown`: a fabricated sub-clause number does not
    appear in the retrieved text either.
    """
    in_context = set(retrieved)
    roots = {_root(c) for c in in_context}
    quoted = set(citations(shown_text)) if shown_text else set()
    grounded, offcontext, unknown = [], [], []
    for cite in citations(answer):
        if cite in EXTERNAL:
            continue
        if cite not in known:
            if deeper_item(cite, texts or {}):
                grounded.append(cite)
            else:
                unknown.append(cite)
        elif cite in in_context or _root(cite) in roots or cite in quoted:
            grounded.append(cite)
        else:
            offcontext.append(cite)
    return {"grounded": grounded, "offcontext": offcontext, "unknown": unknown}


def _root(cite):
    """"Rule 7(2)(b)" -> "Rule 7"; "Third Schedule item 2" -> "Third Schedule"."""
    cite = cite.split("(")[0].strip()
    m = re.match(r"((?:First|Second|Third|Fourth|Fifth|Sixth|Seventh|Eighth|Ninth|Tenth)"
                 r"\s+Schedule)", cite)
    return m.group(1) if m else cite


def warning(result):
    """One line for the UI, or None when every citation checks out."""
    bits = []
    if result["unknown"]:
        bits.append("not in the Act or the Rules as we parsed them: "
                    + ", ".join(result["unknown"]))
    if result["offcontext"]:
        bits.append("cited without being retrieved, so unverified here: "
                    + ", ".join(result["offcontext"]))
    return " | ".join(bits) if bits else None


if __name__ == "__main__":
    import json
    from pathlib import Path

    import baseline

    data = json.loads((Path(__file__).parent / "data" / "corpus.json")
                      .read_text(encoding="utf-8"))
    known = known_citations(data["records"], baseline.units)
    print(len(known), "citable provisions")

    samples = [
        ("Under Rule 3(2)(ii) you must give notice.", ["Rule 3"]),
        ("Section 8(5) requires reasonable security safeguards.", ["Section 8"]),
        ("See Rule 7(1) and also section 9 and the Third Schedule item 2.", ["Rule 7"]),
        ("Available in the Eighth Schedule to the Constitution.", ["Section 6"]),
        ("Penalties are in The Schedule.", ["The Schedule"]),
    ]
    for text, retrieved in samples:
        r = check(text, known, retrieved)
        print("\n", text)
        print("   ", r)
        print("    warn:", warning(r))
