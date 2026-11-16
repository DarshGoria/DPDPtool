"""Self-check: run `python test_dpdp.py` after build_corpus.py.

Guards the two things that would quietly poison everything downstream -
mis-parsed provisions and retrieval that returns the wrong statute.
"""
import json
from pathlib import Path

from retrieve import load

DATA = Path(__file__).parent / "data"


def check_structure(records):
    by_id = {r["id"]: r for r in records}
    sections = [r for r in records if r["id"].startswith("act-s") and r["id"] != "act-schedule"]
    rules = [r for r in records if r["id"].startswith("rules-r")]
    scheds = [r for r in records if r["id"].startswith("rules-sch")]
    assert len(sections) == 44, "expected 44 Act sections, got {}".format(len(sections))
    assert len(rules) == 23, "expected 23 Rules, got {}".format(len(rules))
    assert len(scheds) == 7, "expected 7 Rules schedules, got {}".format(len(scheds))
    assert "act-schedule" in by_id, "Act penalty Schedule missing"

    for r in records:
        assert r["text"].strip(), "{} has empty text".format(r["id"])
        # U+FFFD means a character was lost in extraction (pdftotext emitting
        # latin-1 while we read UTF-8). Silent corruption of statute text.
        assert "�" not in r["text"], (
            "{} contains replacement characters - re-extract with -enc UTF-8"
            .format(r["id"])
        )
        assert r["citation"], "{} has no citation".format(r["id"])
    for r in rules:
        assert r["title"], "{} has no title".format(r["id"])
    return by_id


def check_commencement(by_id):
    """Rule 1(2)-(4): immediate / 12 months / 18 months from 13 Nov 2025."""
    assert by_id["rules-r17"]["in_force"] == "2025-11-13"
    assert by_id["rules-r4"]["in_force"] == "2026-11-13"
    assert by_id["rules-r6"]["in_force"] == "2027-05-13"
    assert by_id["rules-r7"]["in_force"] == "2027-05-13"
    # Rule 1 itself is in the immediate set.
    assert by_id["rules-r1"]["in_force"] == "2025-11-13"


def check_subclauses(by_id):
    s8 = by_id["act-s8"]
    assert "8(5)" in s8["subclauses"], "section 8 sub-clauses not split"
    assert "security safeguards" in s8["subclauses"]["8(5)"]
    assert "8(11)" in s8["subclauses"], "double-digit sub-clause lost"

    r7 = by_id["rules-r7"]
    assert "seventy-two hours" in r7["text"], "72-hour Board report text missing"

    r6 = by_id["rules-r6"]
    assert "encryption" in r6["text"]
    # Rule 6(1)(e): one-year log retention.
    assert "period of one year" in r6["text"]

    # Rule 8(2): the 48-hour pre-erasure warning.
    assert "forty-eight hours" in by_id["rules-r8"]["text"]

    # First Schedule Part A item 7 is the MOA/AOA gate for Consent Managers.
    sch1 = by_id["rules-sch1"]
    assert "memorandum of association" in sch1["text"].lower()
    assert "two crore rupees" in sch1["text"]


def check_penalties(by_id):
    sched = by_id["act-schedule"]
    assert sched["max_penalty_inr"] == 2_500_000_000
    assert by_id["act-s8"]["max_penalty_inr"] == 2_500_000_000
    assert by_id["act-s15"]["max_penalty_inr"] == 10_000
    # The flag is derived by verify_penalties re-reading the Gazette text, so
    # a False here means the amounts and the statute have diverged.
    assert sched["verified_against_gazette"] is True, (
        "penalty table no longer matches the Gazette - run verify_penalties.py"
    )
    import build_corpus
    import verify_penalties
    act = (DATA / "act2023.txt").read_text(encoding="utf-8")
    ok, problems = verify_penalties.verify(act, build_corpus.PENALTIES)
    assert ok, "penalty verification failed: {}".format(problems)


EXPECTED_TOP = [
    ("how long do I have to report a breach to the Board", "Rule 7"),
    ("reasonable security safeguards encryption access logs", "Rule 6"),
    ("verifiable consent of parent for a child", "Rule 10"),
    ("transfer personal data outside the territory of India", "Rule 15"),
    ("additional obligations of significant data fiduciary audit", "Rule 13"),
    ("rights of data principal grievance redressal ninety days", "Rule 14"),
    ("monetary penalty two hundred and fifty crore", "The Schedule"),
]


def check_retrieval(index):
    for question, want in EXPECTED_TOP:
        hits = index.search(question, k=3)
        assert hits, "no hits for {!r}".format(question)
        got = [r["citation"] for _s, r in hits]
        assert got[0] == want, (
            "{!r}\n  wanted top hit {}, got {}".format(question, want, got)
        )


def check_baseline(by_id, index):
    """The zero-model answerer: right clause, right citation."""
    import baseline

    # Schedule Parts restart numbering; a Part B item must never be cited as
    # Part A, which would be a confidently wrong citation.
    sch1 = dict(baseline.units(by_id["rules-sch1"]))
    net_worth = [c for c, t in sch1.items() if "net worth" in t.lower()]
    assert "First Schedule Part A item 4" in net_worth, (
        "net worth clause mis-cited: {}".format(net_worth)
    )
    assert any(c.startswith("First Schedule Part B") for c in sch1), (
        "Part B items were not detected"
    )

    # Sub-clause and lettered-item granularity on an ordinary rule.
    r6 = dict(baseline.units(by_id["rules-r6"]))
    assert "Rule 6(1)(e)" in r6, "lettered items not split: {}".format(list(r6)[:5])
    assert "period of one year" in r6["Rule 6(1)(e)"]

    expected_top = [
        ("how long must I retain logs", "Rule 6(1)(e)"),
        ("net worth to register as a consent manager", "First Schedule Part A item 4"),
    ]
    for question, want in expected_top:
        picked = baseline.rank(question, index.search(question, k=3), limit=3)
        assert picked, "baseline found nothing for {!r}".format(question)
        assert picked[0][1] == want, (
            "{!r}\n  wanted {}, got {}".format(question, want, [p[1] for p in picked])
        )

    # Fragments like "security of the State; and" must not be offered as answers.
    for _s, _c, text in baseline.rank("security", index.search("security", k=3)):
        assert len(text.split()) >= 4, "fragment surfaced: {!r}".format(text)

    assert baseline.answer("", []) == "NOT COVERED BY RETRIEVED PROVISIONS"


def check_chat(index):
    """The built-in conversational engine: right clause, honest when unsure."""
    import ask
    import chat

    penalties = ask.penalties_from(index)

    def reply(q, history=None):
        # The production path, so the tests exercise what users hit.
        hits, query = chat.search(index, q, history, k=4)
        return chat.respond(q, hits, history=history, penalties=penalties,
                            query=query)

    # Everyday words must reach the statute's vocabulary.
    assert "child" in chat.expand("is a 16 year old ok?", None)
    assert "erase" in chat.expand("can I delete it later", None)
    assert "Data Processor" in chat.expand("what about our cloud vendors", None)

    # Leads with the fact, from the clause that carries it.
    breach = reply("how fast must I report a breach to the Board?")
    assert breach.startswith("Within seventy-two hours"), breach[:80]
    assert "Rule 7(2)(b)" in breach

    logs = reply("how long must I retain logs?")
    assert logs.startswith("One year."), logs[:80]
    assert "Rule 6(1)(e)" in logs

    # The regression that mattered: "16 year old" once matched "one year after
    # the date of publication" and answered confidently from Rule 1(3).
    kid = reply("do I need parental consent for a 16 year old?")
    assert "Rule 1(3)" not in kid, "commencement rule cited for a children question"
    assert "Section 9" in kid, kid[:120]

    # Two separate scope requirements, see eval_paraphrase.py. Naming another
    # regime must be refused outright: answering a GDPR question with the
    # Indian Schedule's amounts actively misleads a lawyer.
    other = reply("what are the GDPR fines in Europe?")
    assert "do not cover" in other, other[:100]
    assert "crore" not in other, "answered a GDPR question from the Indian Act"

    # Nonsense only has to avoid asserting a verdict. Demanding a flat refusal
    # here is what drove the gate strict enough to refuse real questions.
    nonsense = reply("what is the capital of France?")
    assert not nonsense.lower().lstrip().startswith(("yes -", "no -")), nonsense[:80]

    # Weak retrieval hedges instead of asserting. This used to probe with
    # "what if we get hacked?", which now retrieves Rule 7 outright once
    # "hacked" was mapped to personal data breach - the old assertion was
    # pinning a weakness in place. Cyber insurance is genuinely absent from
    # both documents, so it is a real weak-retrieval case.
    assert "not confident" in reply("do we need cyber insurance?")
    # A breach question must not hedge any more.
    hacked = reply("what if we get hacked?")
    assert "Rule 7" in hacked, hacked[:120]

    # Conversational extras.
    assert reply("hi").startswith("Hello")
    assert "cannot do" in reply("what can you answer?")
    pen = reply("what is the penalty for a security failure?")
    assert "two hundred and fifty crore" in pen, pen[:120]

    # A follow-up inherits the previous topic.
    history = [{"role": "user", "content": "how fast must I report a breach to the Board?"},
               {"role": "assistant", "content": "Within seventy-two hours. (Rule 7(2)(b))"}]
    assert "breach" in chat.expand("and what about telling the users?", history).lower()


def check_narrowing(index):
    """Real failures found while evaluating the model, kept from returning.

    Each of these once sent the model to the wrong chapter and produced a
    confident, wrongly-cited answer.
    """
    import chat

    def cites(question, history=None, k=4):
        hits, _query = chat.search(index, question, history, k=k)
        return [r["citation"] for _s, r in hits]

    # "credited amount" matched section 34, "Crediting sums realised by way of
    # penalties", and the model answered about penalties.
    got = cites("we read the user's SMS to get the credited amount and prefill it")
    assert "Section 6" in got and "Section 8" in got, got
    assert not {"Section 33", "Section 34", "Rule 22"} & set(got), (
        "enforcement machinery surfaced for a compliance question: {}".format(got)
    )

    # "gain"/"amount" pulled the definitions section as the lead authority.
    assert "Section 2" not in cites("is showing products by bank balance allowed?")

    # "they click allow button" retrieved the children's rules, and the model
    # answered about verifying a parent's age.
    history = [{"role": "user", "content": "we read SMS to prefill an amount"},
               {"role": "assistant", "content": "(previous answer)"}]
    got = cites("but they click allow button on the application", history)
    assert "Section 6" in got, got
    assert not {"Rule 10", "Rule 11", "Section 9"} & set(got), (
        "children's provisions surfaced with no child in the question: {}".format(got)
    )

    # The guards must not block the questions those chapters do answer.
    assert "The Schedule" in cites("what is the penalty for a security failure?", k=3)
    assert "Section 9" in cites("do I need parental consent for a 16 year old?", k=3)
    assert "Section 17" in cites("are startups exempt?", k=3)
    assert "First Schedule" in cites("net worth to register as a Consent Manager", k=3)
    assert "Section 2" in cites("what does Data Fiduciary mean?", k=3)


def check_traces(index):
    """log_trace -> set_review round-trip, against a scratch file."""
    import tempfile

    import ask

    real = ask.TRACES
    with tempfile.TemporaryDirectory() as tmp:
        ask.TRACES = Path(tmp) / "traces.jsonl"
        try:
            hits = index.search("breach reporting", k=2)
            first = ask.log_trace("q1", "test-model", hits, "PROMPT", "A1")
            second = ask.log_trace("q2", "test-model", hits, "PROMPT", "A2")
            assert first != second, "trace ids must be unique"

            assert ask.set_review(first, "good") is True
            assert ask.set_review("nope", "good") is False, "unknown id must not match"

            rows = [json.loads(l) for l in
                    ask.TRACES.read_text(encoding="utf-8").splitlines() if l.strip()]
            assert len(rows) == 2, "review rewrite lost rows"
            by_id = {r["id"]: r for r in rows}
            assert by_id[first]["reviewed"] == "good"
            assert by_id[second]["reviewed"] is None, "review leaked to the wrong row"
            assert by_id[second]["answer"] == "A2", "rewrite corrupted an untouched row"
        finally:
            ask.TRACES = real


def check_citations(index):
    """Every citation an answer makes must exist, and fabrications must show."""
    import ask
    import chat
    import citecheck

    known = ask.known_citations(index)
    assert "Rule 7(1)" in known and "Section 8(5)" in known
    assert "Rule 3(2)(ii)" not in known, "a citation with no statutory basis"

    # The native engine's whole claim is that it cannot invent a citation.
    # Assert it per answer rather than trusting the design.
    for question in ("how fast must I report a breach to the Board?",
                     "what security measures are mandatory at minimum?",
                     "do I need parental consent for a 16 year old?",
                     "what is the maximum penalty for a security failure?"):
        hits, query = chat.search(index, question, None, k=4)
        answer = chat.respond(question, hits, penalties=PENALTIES_FOR_TEST,
                              query=query)
        result = ask.check_citations(answer, hits, index)
        assert not result["unknown"], (question, result["unknown"])

    # And the checker must actually catch the real fabrication, or a clean
    # result above means nothing.
    caught = citecheck.check("Under Rule 3(2)(ii) notice is required.",
                             known, ["Rule 3"])
    assert caught["unknown"] == ["Rule 3(2)(ii)"], caught
    assert citecheck.warning(caught)
    # The Eighth Schedule is the Constitution's language list, not ours.
    external = citecheck.check("See the Eighth Schedule to the Constitution.",
                               known, ["Section 6"])
    assert not external["unknown"], external

    # A year is not a citation. "the DPDP Rules 2025" read as Rule 2025 and
    # was reported as fabricated, so the refusal message - which names the
    # Rules - told the reader to check its own citations.
    assert citecheck.citations("the DPDP Rules 2025 and the Act 2023") == []
    assert citecheck.citations("Rule 23 applies") == ["Rule 23"]
    for message in (chat.HELP, chat.OUT_OF_SCOPE, chat.NO_MATCH, chat.HELLO):
        result = citecheck.check(message, known)
        assert not result["unknown"], (message[:40], result["unknown"])

    # Citations inside quoted statute text are the statute's own
    # cross-references, not claims the answer is making: the Schedule lists
    # "Section 8(5): Failure to take reasonable security safeguards...", so
    # quoting it was reported as citing five provisions from memory.
    schedule = next(r for r in index.records if r["citation"] == "The Schedule")
    quoted = citecheck.check(
        "Maximum penalties: " + schedule["text"], known,
        ["The Schedule"], schedule["text"])
    assert not quoted["offcontext"], quoted["offcontext"]


def check_list_tails(by_id):
    """The last item of a list must survive parsing.

    The cross-reference guard rejects a marker preceded by "and" or "or", for
    references like "clause (a) and (b) of section 7". But a statute ends
    every list the same way - "...; and (c) give, the particular
    communication link..." - so the guard was silently dropping the final
    item of 30 lists. The model cited Rule 3(c) correctly and our corpus
    called it fabricated.
    """
    assert "3(c)" in by_id["rules-r3"]["subclauses"], \
        "Rule 3(c) lost: it follows '; and' and the reference guard ate it"
    # Section 7's legitimate uses run to (i), the employment ground.
    assert "7(i)" in by_id["act-s7"]["subclauses"], \
        "section 7(i) lost - the employment legitimate use"
    assert "15(e)" in by_id["act-s15"]["subclauses"]
    # And the guard must still do its job on an actual cross-reference.
    import build_corpus
    assert build_corpus.REFERENCE.search("under clause (a) of section ")
    assert not build_corpus.LIST_CONTINUES.search("under clause (a) of section ")
    assert build_corpus.LIST_CONTINUES.search("such processing; and ")


def check_answer_hygiene(index):
    """Three defects a reviewer's transcript showed, all now guarded.

    The question was whether consent taken on a terms-and-conditions page is
    valid. What came back was a definition of "user account", the Consent
    Manager registration sub-section, 300 characters of worked example about
    a telemedicine app, and - when the user asked what to change - the words
    "Yes - this is required."
    """
    import chat

    question = ("Hey I have a ai company we use users data like gmail and we "
                "link it with their account with their consent over a terms "
                "and conditions page. Is it following dpdp laws")
    hits, query = chat.search(index, question, None, k=4)
    picked = chat.rank(query, hits, chat.intent_of(question), limit=6,
                       question=question)
    cites = [c for _s, c, _t in picked]
    # Definitions and Consent Manager registration are not the answer to a
    # company's question about its own consent flow. The record-level
    # demotions missed these because both clauses sit inside records that
    # genuinely are relevant.
    assert "Rule 2(1)(c)" not in cites, cites
    assert not any(c.startswith("Section 6(9)") for c in cites), cites
    assert "Section 6(1)" in cites, cites

    # A worked example is not the operative text. It stays in the full
    # provision; it does not belong in the quoted answer.
    section6 = next(r for r in index.records if r["citation"] == "Section 6")
    raw = section6["subclauses"]["6(1)"]
    assert "Illustration" in raw, "fixture changed; 6(1) has no illustration"
    quoted = chat._trim(raw)
    assert "Illustration" not in quoted, quoted[-120:]
    assert "telemedicine" not in quoted.lower()
    assert "free, specific, informed" in quoted
    # A clause that is almost all illustration is kept whole rather than
    # reduced to a fragment.
    assert chat.without_illustration("Illustration. X does a thing.").strip()

    # "what do I need to change" is not a yes/no question, however it goes on.
    assert chat.intent_of("so what exactly do i need to change") != "whether"
    assert chat.intent_of("what do we need to fix") != "whether"
    assert chat.intent_of("do i need parental consent for a 16 year old?") == "whether"
    assert chat.intent_of("can I transfer data outside India?") == "whether"


def check_answer_problems():
    """Two verdicts, or a repeated paragraph, must be reported not hidden."""
    import ask

    doubled = ask.answer_problems("COMPLIES, DOES NOT COMPLY WITH RULE 3(b) "
                                  "- the notice must be itemised.")
    assert doubled and "more than one verdict" in doubled[0], doubled
    assert not ask.answer_problems(
        "DOES NOT COMPLY - Section 6(1) requires specific consent.")
    # The same verdict named twice is emphasis, not a contradiction.
    assert not ask.answer_problems(
        "DOES NOT COMPLY. Section 6(1) is the reason it DOES NOT COMPLY.")
    sentence = ("The Data Fiduciary shall give an itemised description of "
                "the personal data being processed and the purpose. ")
    assert ask.answer_problems(sentence + sentence)


def check_question_kind():
    """Asking what the law says, vs describing a practice to be judged.

    The 3B cannot make this call: told to choose, it answered "can I transfer
    personal data outside India?" - six words, no facts - with DO NOT COMPLY.
    So build_prompt decides and states it, and this guards that decision.
    """
    import ask

    asking = [
        "how fast must I report a breach to the Board?",
        "can I transfer personal data outside India?",
        "what security measures are mandatory at minimum?",
        "do I need parental consent for a 16 year old?",
        # "logs" is a noun here; treating it as a verb of doing made this a
        # description of practice.
        "how long must I retain logs?",
        "how long do I have to resolve a grievance?",
        "what is the maximum penalty for a security failure?",
    ]
    describing = [
        "we read users SMS to work out their credit limit, do we need anything special?",
        "we use user data to make them login, is that allowed?",
        "our app collects contacts and location and we keep it for three years",
        "we do a normal login process through gmail",
        "we store customer records on a server in Singapore",
    ]
    for question in asking:
        assert ask.question_kind(question) == "A", question
    for question in describing:
        assert ask.question_kind(question) == "B", question

    # And the prompt must carry the answer, not just the provisions: the
    # ranked clauses go in first precisely so the model is shown it.
    import chat
    from retrieve import load as _load
    _m, idx = _load()
    for question, phrase in (
            ("how fast must I report a breach to the Board?", "seventy-two"),
            ("how long do I have to resolve a grievance?", "ninety days"),
            ("how long must I retain logs?", "one year")):
        hits, query = chat.search(idx, question, None, k=4)
        prompt = ask.build_prompt(question, hits, None, query)
        at = prompt.find("MOST ON-POINT")
        assert at >= 0, question
        ranked = prompt[at:prompt.find("### ", at + 5)]
        assert phrase.lower() in ranked.lower(), (question, phrase)

    # Nothing in the system prompt that a small model can lift wholesale.
    import re as _re
    assert not _re.findall(r"^[AB]\. [A-Z]", ask.SYSTEM, _re.M), \
        "lettered heading in SYSTEM - the model emits it as the answer"
    assert not _re.findall(r'"[^"]{15,}\?"', ask.SYSTEM), \
        "example question in SYSTEM - the model echoes it instead of answering"


def check_crossrefs(index):
    """A clause that points at another provision should pull it in."""
    import chat

    hits, _query = chat.search(index, "what security must we have in place?",
                               None, k=4)
    assert any(r.get("crossref") for _s, r in hits), (
        "no cross-referenced provision followed")
    # Cross-refs are context: they must not take the headline citation.
    plain, _q = chat._pick(index, "how fast must I report a breach to the Board?",
                           None, k=4)
    full, q2 = chat.search(index, "how fast must I report a breach to the Board?",
                           None, k=4)
    assert plain[0][1]["citation"] == full[0][1]["citation"], (
        "cross-reference displaced the governing provision")


def check_scope(index):
    """Typos recover; other regimes are named; nonsense is not asserted."""
    import chat

    assert chat.spell_fix(index, "is encrpytion mandatory") == \
        "is encryption mandatory"
    # Genuinely absent terms must survive untouched rather than being bent
    # into some unrelated provision.
    for word in ("aadhaar", "kyc", "saas", "fintech"):
        assert chat.spell_fix(index, "we handle " + word) == "we handle " + word, word
    hits, _q = chat.search(index, "is encrpytion mandatory", None, k=4)
    assert hits and "Rule 6" in [r["citation"] for _s, r in hits], \
        "typo still returns nothing"

    assert chat.other_regime("what are the GDPR fines in Europe?")
    assert chat.other_regime("how much income tax do I owe?")
    assert chat.other_regime("what does CERT-In require?")
    assert not chat.other_regime("how fast must I report a breach?")

    # Naming a place is not naming another regime. A bare "california"
    # refused "can we store data in California?", which is an ordinary
    # cross-border question under section 16 and Rule 15.
    assert not chat.other_regime("can we store data in California?")
    assert chat.other_regime("what are the CCPA requirements?")
    for question in ("can we store data in California?",
                     "can we host in a Frankfurt region?"):
        hits, _q = chat.search(index, question, None, k=4)
        got = [r["citation"] for _s, r in hits]
        assert "Rule 15" in got or "Section 16" in got, (question, got)
    # ...and hosting inside India is not a transfer at all.
    hits, _q = chat.search(index, "can we keep servers in Mumbai?", None, k=4)
    got = [r["citation"] for _s, r in hits]
    assert "Rule 15" not in got, ("Indian city read as a transfer", got)


PENALTIES_FOR_TEST = None


def main():
    global PENALTIES_FOR_TEST
    records = json.loads((DATA / "corpus.json").read_text(encoding="utf-8"))["records"]
    by_id = check_structure(records)
    check_commencement(by_id)
    check_subclauses(by_id)
    check_list_tails(by_id)
    check_penalties(by_id)
    _meta, index = load()
    check_retrieval(index)
    check_baseline(by_id, index)
    import ask
    PENALTIES_FOR_TEST = ask.penalties_from(index)
    globals()["PENALTIES_FOR_TEST"] = PENALTIES_FOR_TEST
    check_chat(index)
    check_narrowing(index)
    check_citations(index)
    check_crossrefs(index)
    check_question_kind()
    check_answer_problems()
    check_answer_hygiene(index)
    check_scope(index)
    check_traces(index)
    print("all checks passed: {} records, {} retrieval probes, baseline "
          "citations, chat engine, narrowing, citation integrity, "
          "cross-references, scope gate, question kinds, list tails, answer hygiene, "
          "trace round-trip".format(
              len(records), len(EXPECTED_TOP)))


if __name__ == "__main__":
    main()
