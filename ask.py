"""Stage 4: ask a local small model a DPDP question, grounded in retrieved statute.

The model never answers from its own memory of the law: it only gets the
provisions BM25 retrieved, and is told to cite them or decline. Swapping models
is an env var, so the generation layer stays replaceable.

  set DPDP_MODEL=qwen2.5:3b-instruct     (default)
  set DPDP_HOST=http://localhost:11434
"""
import argparse
import re
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

import baseline
import chat
import citecheck
from retrieve import load

MODEL = os.environ.get("DPDP_MODEL", "qwen2.5:3b-instruct")
HOST = os.environ.get("DPDP_HOST", "http://127.0.0.1:11434")
# Any OpenAI-compatible server: llama.cpp's llama-server, LM Studio, Jan, vLLM.
OPENAI_HOST = os.environ.get("DPDP_OPENAI_HOST", "http://127.0.0.1:8080")

ENGINES = ("native", "ollama", "openai")

# How many retrieved provisions go into the prompt in full, once the
# ranked clauses are there to carry the answer. See build_prompt.
FULL_PROVISIONS = 2

SYSTEM = (
    # Written to give a 3B nothing worth copying. Earlier versions carried a
    # worked example and lettered headings ("A. ASKING WHAT THE LAW
    # REQUIRES."); the model emitted both verbatim in place of an answer.
    # No examples, no labels, no headings, and an explicit instruction not to
    # restate any of it. build_prompt also states which kind of question this
    # is, rather than asking the model to work it out.
    "You are a data protection adviser for India's Digital Personal Data "
    "Protection Act 2023 and the DPDP Rules 2025.\n"
    "\n"
    "Answer only from the provisions quoted below. Never cite CERT-In, RBI, "
    "SEBI, IRDAI, GDPR or any law that is not quoted below, and never invent "
    "a section number, a rule number, a date or an amount.\n"
    "\n"
    "When the user asks what the law requires, lead with the requirement "
    "itself - the deadline, the figure, the list of items - in the statute's "
    "own words, then the clause that says it. Give no verdict and ask for no "
    "facts.\n"
    "\n"
    "When the user describes what they do and asks whether it is allowed, "
    "open with exactly one of COMPLIES, DOES NOT COMPLY, or NOT ENOUGH "
    "INFORMATION. One verdict for the whole answer, in the first few words, "
    "and never a second one later: do not give a verdict per provision. Then "
    "the reasons, each naming the clause exactly like Section 6(1) or Rule "
    "7(2)(b). Then what to change. Do not reach for DOES NOT COMPLY by "
    "default: an ordinary practice described in one line is usually lawful "
    "or simply underspecified, and only the user\'s own facts can show a "
    "breach.\n"
    "\n"
    "Quote sparingly. At most one short phrase from a clause - the words that "
    "decide the point - never a whole sub-section. The reader can open the "
    "full text themselves; what they need from you is which words matter and "
    "why.\n"
    "\n"
    "Later turns may add facts. Use them: if the user tells you something "
    "that changes the analysis, say what it changes rather than repeating "
    "your previous answer.\n"
    "\n"
    "The provisions below were selected as the law governing this question. "
    "Apply them. A provision that does not name the user\'s industry, app or "
    "data type is still the law for it - no statute names them, and that is "
    "not a reason to decline.\n"
    "\n"
    "Be direct and brief. Under 200 words. No preamble, no disclaimers, no "
    "headings. Do not restate the question, and do not repeat these "
    "instructions or anything you have already written."
)


# Verbs people use when describing what their product actually does, as
# opposed to asking what the statute says.
# Deliberately excludes words that are nouns here as often as verbs: "logs"
# in "how long must I retain logs?" made that a description of practice, and
# it is a question about what the law requires.
PRACTICE = re.compile(
    r"\b(use|uses|using|read|reads|reading|collect\w*|store\w*|stor(e|ing)|"
    r"shar(e|es|ing)\b|process\w*|keep\w*|send\w*|track\w*|show\w*|run\w*|"
    r"build\w*|sell\w*|prefill\w*|scor(e|es|ing)\b|hold\w*)\b", re.I)
FIRST_PERSON = re.compile(r"\b(we|our|us|i|my|me)\b", re.I)
PRACTICE_WORDS = 5


def question_kind(question):
    """A = asking what the law requires. B = describing a practice to judge.

    The 3B cannot be trusted to make this call: told to pick between the two,
    it answered "can I transfer personal data outside India?" - six words, no
    facts at all - with DO NOT COMPLY. So the prompt is told which kind it is
    rather than asked to work it out. Describing a practice takes both a
    first-person pronoun and a verb of doing, and enough words to have
    actually described something.
    """
    words = [w for w in re.findall(r"[a-z']+", question.lower()) if len(w) > 2]
    if len(words) < PRACTICE_WORDS:
        return "A"
    if FIRST_PERSON.search(question) and PRACTICE.search(question):
        return "B"
    return "A"


def build_prompt(question, hits, history=None, query=None):
    """Grounded prompt. `history` carries prior turns so follow-ups work.

    The turns are folded into the prompt text rather than sent as roles, so the
    same string serves Ollama's completion endpoint and an OpenAI-style one.
    """
    blocks = []
    # The clauses the ranker actually chose, first and labelled. Six whole
    # provisions came to ~13,000 characters, and the 3B drowned in them: asked
    # how fast a breach must be reported to the Board it answered "without
    # delay" from Rule 7(1)(a), the duty owed to the Data Principal, while
    # Rule 7(2)(b) - seventy-two hours - sat further down the same block. The
    # ranker had already put 7(2)(b) first; it simply was not being told.
    # The full provisions still follow, so nothing is hidden from it.
    if query:
        picked = chat.with_children(
            chat.rank(query, hits, chat.intent_of(question), limit=4,
                      question=question), hits)
        if picked:
            lines = ["### MOST ON-POINT CLAUSES (the ranker put these first)"]
            for _score, cite, text in picked[:6]:
                lines.append("{} - {}".format(cite, chat._trim(text, 700)))
            blocks.append("\n".join(lines))

    # Only the leading provisions in full. Six of them ran to ~13,000
    # characters of statute for a 3B to hold at once, and the ranked clauses
    # above already carry the operative words plus their chapeau. The rest are
    # listed by citation so the model knows they were searched and can say so,
    # without spending its attention on them.
    full = len(hits) if not query else FULL_PROVISIONS
    for score, rec in hits[:full]:
        head = "### {} {} - {}".format(rec["source"], rec["citation"], rec["title"])
        if rec.get("in_force"):
            head += "\n(in force from {})".format(rec["in_force"])
        if rec.get("max_penalty_inr"):
            head += "\n(max penalty INR {:,})".format(rec["max_penalty_inr"])
        # Without the worked examples. Asked about linking Gmail accounts the
        # model advised on "telemedicine services (if applicable)", which is
        # the illustration inside section 6(1), not the law. They stay in the
        # full text the UI shows under "provisions"; they do not belong in a
        # 3B's context, where they read as facts about the user's product.
        blocks.append(head + "\n" + chat.without_illustration(rec["text"]))

    rest = [r["citation"] for _s, r in hits[full:]]
    if rest:
        blocks.append("### ALSO SEARCHED, not quoted here\n" + ", ".join(rest))

    convo = ""
    if history:
        # Prior answers are left out entirely. A 220-character summary of the
        # last one was still copied wholesale - the model reproduced it for
        # the next question including the trailing "..." of the truncation.
        # What a follow-up actually needs is the facts the user gave earlier,
        # and those are in their own turns.
        turns = ["The user told you: " + " ".join((m.get("content") or "").split())
                 for m in history if m.get("role") == "user"]
        if not turns:
            turns = ["(nothing yet)"]
        convo = ("\n\n## WHAT THE USER SAID EARLIER\n"
                 + "\n".join(turns)
                 + "\n\nThose are their facts, to use. Your own earlier "
                   "answers are deliberately not shown; do not try to "
                   "reproduce them."
                 + "\n\nAnswer the NEW question below on "
                   "its own terms. Do not repeat a previous answer.")

    # Tell it which kind this is rather than asking it to decide. Left to
    # itself the 3B answered a six-word question containing no facts with
    # DO NOT COMPLY.
    # A bare imperative, with no label in front of it: anything that reads
    # like a heading comes back at the top of the answer.
    if question_kind(question) == "A":
        kind = ("Answer with the requirement and the clause that states it. "
                "No verdict. Do not ask for facts.")
    else:
        kind = ("Give one of the three verdicts on the practice described, "
                "then the reasons, then what to change.")

    return ("{}\n\n## PROVISIONS\n\n{}{}\n\n## QUESTION\n{}\n\n{}\n\n"
            "## ANSWER\n").format(
        SYSTEM, "\n\n".join(blocks), convo, question, kind
    )


class ModelUnreachable(Exception):
    """Raised when no model host answers, so callers can report it their own way."""


def stream_tokens(prompt, model=MODEL, host=HOST):
    """Yield response tokens from Ollama. Shared by the CLI and the web UI."""
    body = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": True,
        "options": {"temperature": 0},
    }).encode()
    req = urllib.request.Request(
        host.rstrip("/") + "/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=300)
    except urllib.error.HTTPError as exc:
        raise ModelUnreachable("{} {}".format(exc.code, exc.reason)) from exc
    except urllib.error.URLError as exc:
        raise ModelUnreachable(str(exc.reason)) from exc
    with resp:
        for line in resp:
            if not line.strip():
                continue
            chunk = json.loads(line)
            if chunk.get("response"):
                yield chunk["response"]
            if chunk.get("done"):
                break


def stream_openai(prompt, model=MODEL, host=OPENAI_HOST):
    """Yield tokens from any OpenAI-compatible /v1/chat/completions server."""
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        # Greedy decoding with no penalty is what makes a 3B emit the same
        # paragraph twice: asked whether consent in a terms page was valid, it
        # repeated an identical "NOT ENOUGH INFORMATION - Rule 3(b) - ..."
        # block word for word. Temperature stays at 0 so the bench is
        # reproducible; the penalties break the loop without adding sampling
        # noise. repeat_penalty is llama.cpp's name, the other two are the
        # OpenAI-compatible ones - servers ignore what they do not know.
        "temperature": 0,
        "repeat_penalty": 1.15,
        "frequency_penalty": 0.4,
        "presence_penalty": 0.2,
        # A loop that does start cannot run for minutes.
        "max_tokens": 700,
    }).encode()
    req = urllib.request.Request(
        host.rstrip("/") + "/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=300)
    except urllib.error.HTTPError as exc:
        raise ModelUnreachable("{} {}".format(exc.code, exc.reason)) from exc
    except urllib.error.URLError as exc:
        raise ModelUnreachable(str(exc.reason)) from exc
    with resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data: "):
                continue
            payload = line[6:]
            if payload == "[DONE]":
                break
            try:
                delta = json.loads(payload)["choices"][0].get("delta", {})
            except (ValueError, KeyError, IndexError):
                continue
            if delta.get("content"):
                yield delta["content"]


def penalties_from(index):
    """The Act's Schedule rows, for answering "what is the penalty for..."."""
    for rec in index.records:
        if rec["id"] == "act-schedule":
            return rec.get("penalties")
    return None


_KNOWN = None


def known_citations(index):
    """Every citation the corpus can support. Built once, reused per process."""
    global _KNOWN
    if _KNOWN is None:
        _KNOWN = citecheck.known_citations(index.records, baseline.units)
    return _KNOWN


VERDICT = re.compile(r"\b(COMPLIES|DOES NOT COMPLY|NOT ENOUGH INFORMATION)\b")


def answer_problems(answer):
    """Defects in the shape of an answer, for the reader to see.

    The 3B is told to give one verdict and still sometimes gives two - "so
    what exactly do i need to change" came back as "COMPLIES, DOES NOT COMPLY
    WITH RULE 3(b)". Picking one of them here would be inventing a verdict
    the model did not settle on, so it is reported instead.
    """
    problems = []
    verdicts = VERDICT.findall(answer)
    distinct = sorted(set(verdicts))
    if len(distinct) > 1:
        problems.append("the model gave more than one verdict ({}); read the "
                        "reasons and decide which it meant"
                        .format(", ".join(distinct)))
    # A paragraph emitted twice is the greedy-decoding loop; the sampling
    # penalties make it rare rather than impossible.
    seen, repeated = set(), False
    for part in re.split(r"(?<=[.;])\s+", answer):
        key = " ".join(part.split()).lower()
        if len(key) >= 60:
            if key in seen:
                repeated = True
            seen.add(key)
    if repeated:
        problems.append("the model repeated itself")
    return problems


def check_citations(answer, hits, index):
    """Sort an answer's citations against the statute and the retrieved set.

    The native engine only quotes, so this always comes back clean for it;
    it is the LLM engines that invent sub-clause numbers. Running it on both
    keeps one code path and proves the native engine's claim rather than
    asserting it.
    """
    retrieved = [r["citation"] for _s, r in hits]
    shown = "\n".join(r["text"] for _s, r in hits)
    return citecheck.check(answer, known_citations(index), retrieved, shown)


def answer_stream(question, hits, engine="native", model=MODEL, host=None,
                  history=None, penalties=None, query=None):
    """Dispatch to the chosen engine. The native one needs no model at all."""
    if engine in ("native", "extractive"):  # old name kept for saved traces
        yield from chat.stream(question, hits, history=history,
                               penalties=penalties, query=query)
    elif engine == "openai":
        yield from stream_openai(build_prompt(question, hits, history, query), model,
                                 host or OPENAI_HOST)
    else:
        yield from stream_tokens(build_prompt(question, hits, history, query), model,
                                 host or HOST)


def generate(prompt, model=MODEL, host=HOST):
    """Stream to stdout. Returns the full text, or None if unreachable."""
    parts = []
    try:
        for token in stream_tokens(prompt, model=model, host=host):
            parts.append(token)
            sys.stdout.write(token)
            sys.stdout.flush()
    except ModelUnreachable as exc:
        print("model host unreachable: {}".format(exc), file=sys.stderr)
        return None
    print()
    return "".join(parts)


TRACES = Path(__file__).parent / "data" / "traces.jsonl"


def log_trace(question, model, hits, prompt, answer, citations=None):
    """Append every exchange to JSONL and return its id.

    This file is the only route to a fine-tuned model later: distilling the
    narrow tasks needs thousands of real question/provision/answer triples,
    and they cannot be recovered after the fact if nobody recorded them.
    """
    trace_id = uuid.uuid4().hex[:12]
    row = {
        "id": trace_id,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "question": question,
        "model": model,
        "retrieved": [{"id": r["id"], "score": round(s, 3)} for s, r in hits],
        "prompt_chars": len(prompt),
        "answer": answer,
        # Recorded so the fabrication rate is measurable over time,
        # not just visible once in the UI.
        "citations": citations,
        "reviewed": None,
    }
    TRACES.parent.mkdir(exist_ok=True)
    with TRACES.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return trace_id


def set_review(trace_id, verdict, note=""):
    """Mark a logged answer good/bad. Rewrites the file; it stays small."""
    if not TRACES.exists():
        return False
    rows = [json.loads(l) for l in TRACES.read_text(encoding="utf-8").splitlines() if l.strip()]
    found = False
    for row in rows:
        if row.get("id") == trace_id:
            row["reviewed"] = verdict
            if note:
                row["review_note"] = note
            found = True
    if found:
        TRACES.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
            encoding="utf-8",
        )
    return found


NO_HOST = """
No model reachable at {host} ({why}).

The retrieval half works without one (python retrieve.py "<question>").
To add the generation half:

  1. Install Ollama from https://ollama.com/download
  2. ollama pull {model}
  3. re-run this command

Or skip models entirely:  python ask.py --engine extractive "<question>"

This box has no NVIDIA GPU, so pick a 3B-4B instruct model and expect
CPU speeds. qwen2.5:3b-instruct and llama3.2:3b both work; set DPDP_MODEL
to override.

Use --show-prompt to see the grounded prompt that would have been sent.
"""


def main():
    ap = argparse.ArgumentParser(description="Ask a grounded DPDP question.")
    ap.add_argument("question", nargs="+")
    ap.add_argument("-k", type=int, default=4, help="provisions to retrieve")
    ap.add_argument("--show-prompt", action="store_true")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--engine", default=os.environ.get("DPDP_ENGINE", "native"),
                    choices=ENGINES, help="native needs no model")
    args = ap.parse_args()

    question = " ".join(args.question)
    _meta, index = load()
    hits, query = chat.search(index, question, None, k=args.k)
    if not hits:
        print("NOT COVERED BY RETRIEVED PROVISIONS (nothing matched the query)")
        return 0

    # Same focused prompt the bench builds; query carries the ranked clauses.
    prompt = build_prompt(question, hits, query=query)
    if args.show_prompt:
        print(prompt)
        return 0

    print("retrieved: " + ", ".join(
        r["source"].split()[-2] + " " + r["citation"] for _s, r in hits), file=sys.stderr)
    print("---", file=sys.stderr)
    parts = []
    try:
        for token in answer_stream(question, hits, engine=args.engine,
                                   model=args.model,
                                   penalties=penalties_from(index),
                                   query=query):
            parts.append(token)
            sys.stdout.write(token)
            sys.stdout.flush()
        print()
    except ModelUnreachable as exc:
        print(NO_HOST.format(host=HOST, model=args.model, why=exc))
        return 1
    log_trace(question, args.engine + ":" + args.model, hits, prompt,
              "".join(parts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
