# DPDP SLM

A local, offline question-answering system over India's **Digital Personal Data
Protection Act 2023** and the **DPDP Rules 2025** (G.S.R. 846(E), Gazette
13 November 2025).

A structured statute corpus does the knowing. The built-in engine answers by
quoting it, and a small local model can write the prose instead — but it only
ever sees the provisions retrieved for the question, and every citation it
makes is checked against the statute before the answer is shown.

## Pipeline

```
PDFs --extract.py--> data/*.txt --build_corpus.py--> data/corpus.json
                                                          |
                              retrieve.py  BM25 over provisions
                              chat.py      synonyms, clause re-rank,
                                           cross-references, scope gate
                                                          |
                                           +--------------+--------------+
                                           |                             |
                              chat.respond (built-in)      ask.py -> llama.cpp
                              quotes only, cannot          writes prose, can
                              invent a citation            invent one
                                           |                             |
                                           +------> citecheck.py <-------+
                                                   every citation sorted:
                                                   grounded / off-context /
                                                   unknown (fabricated)
                                                          |
                              serve.py (bench)  api/index.py (Vercel)
                              dist/*.html (no server at all)
                                                          |
                                                 data/traces.jsonl
```

Zero third-party Python packages. Standard library only, browser included.
`architecture.svg` is the same picture, drawn.

## Run it

```bash
python serve.py          # test UI at http://localhost:8700
```

The UI opens in your browser and lets you compare retrieval against
generation: the answer with its citations highlighted on the left, the exact
provisions that were retrieved (score, in-force date, tags, full text) on the
right, and the verbatim prompt in the panel below. `Retrieve only` skips the
model. Ctrl+Enter sends. Good/Wrong writes a verdict into `data/traces.jsonl`.

Generation timings (first token, total, words/sec) are shown because they are
the thing you are really testing on a CPU-only box.

If port 8700 is busy it picks the next free one and prints the URL;
`--port N` overrides.

Command line, same engine:

```bash
python build_corpus.py                      # text -> structured corpus
python test_dpdp.py                         # self-check
python build_web.py                         # rebuild the self-contained dist/*.html
python retrieve.py "72 hour breach report"  # retrieval only, no model needed
python ask.py "how fast must I report a breach to the Board"
```

To rebuild from the PDFs (needs poppler's `pdftotext` on PATH):

```bash
python extract.py <folder-containing-the-two-PDFs>
```

### Engines

Pick one in the UI, or `--engine` on the CLI. Nothing needs installing for the
default.

| Engine | Needs | Notes |
|---|---|---|
| `extractive` | **nothing** | Quotes the matching sub-clauses verbatim with citations. Generates nothing, so it cannot invent a section number. The default, and the baseline a real model must beat. |
| `ollama` | Ollama + a pulled model | `DPDP_HOST`, default `http://127.0.0.1:11434` |
| `openai` | any OpenAI-compatible server | llama.cpp's `llama-server`, LM Studio, Jan, vLLM. `DPDP_OPENAI_HOST`, default `http://127.0.0.1:8080` |

The `openai` engine exists so you are not tied to Ollama. llama.cpp is the
light route: its Windows CPU build is a ~20 MB zip against Ollama's ~700 MB
installer, because Ollama bundles CUDA and ROCm runtimes this machine has no
use for. Point it at any GGUF — a 0.5B–1.5B instruct model is 350 MB–1 GB and
runs fine on 12 CPU cores.

```bash
llama-server -m qwen2.5-1.5b-instruct-q4_k_m.gguf --port 8080
```

Then pick "OpenAI-compatible" in the UI. `--show-prompt` prints the grounded
prompt without calling anything.

## What's in the corpus

75 records: 44 Act sections, 23 Rules, the 7 Schedules to the Rules, and the
Act's penalty Schedule. Each record carries:

| Field | Purpose |
|---|---|
| `citation` | `Section 8`, `Rule 7`, `First Schedule` |
| `subclauses` | `{"8(5)": "..."}` — citation precision down to the sub-clause |
| `in_force` | per-rule commencement date, from Rule 1(2)–(4) |
| `max_penalty_inr` | from the Act's Schedule, for risk weighting |
| `tags` | `consent`, `breach`, `children`, `security`, `retention`, … |

### Commencement, straight from Rule 1

| Rules | In force |
|---|---|
| 1, 2, 17–21 | 2025-11-13 |
| 4 (Consent Manager registration) | 2026-11-13 |
| 3, 5–16, 22, 23 | **2027-05-13** |

## Measured accuracy

Six harnesses, because one of them measuring my own expectations is how a
perfect score and a reviewer's "30% accuracy, making facts as it goes" ended
up coexisting.

| harness | what it measures | result |
|---|---|---|
| `eval.py` | 23 hand-written cases, governing citation + required phrase | 23/23 |
| `eval_coverage.py` | all 75 provisions, queried with their own highest tf-idf terms - no hand-picked cases | 97% at rank 1, 100% in top 3 |
| `eval_paraphrase.py` | 65 questions in a founder's words, plus two scope requirements | 100% (**66% before tuning**) |
| `eval_holdout.py` | 25 cases written before tuning, failures not inspected during it | 100% (**44% before tuning**) |
| `eval_final.py` | 25 messy cases - typos, no punctuation, run-on sentences - written after all tuning | 100% (**92% on its first, untuned run**) |
| `eval_context.py` | multi-turn: a new subject must not inherit the last turn's provisions, a real follow-up must keep the topic | 5/5 shifts, 4/4 follow-ups |

The bold figures are the honest ones. The order matters:

1. `eval.py` scored 24/24 while a reviewer reported 30%. A set I wrote while
   tuning the retriever cannot measure the retriever.
2. `eval_paraphrase.py`, written in plain language, scored **66%** and
   `eval_holdout.py` **44%** on their first runs. That is the real starting
   point, and it is close to what the reviewer saw.
3. The 22 failures were nearly all vocabulary: "scramble" for encryption,
   "big operator" for Significant Data Fiduciary, "throw data away" for
   erase, "watchdog" for the Board. Fixing them took the held-out set from
   44% to 92% **before** it was ever used to drive a fix - so the synonym
   layer generalised rather than memorising phrasings.
4. `eval_final.py` is the last untuned number: **92%** on deliberately messy
   input. Its two failures were real defects (one typo returned zero
   provisions; "train a model" went to the retention rules) and were fixed,
   so it is now a regression check. **The next honest number needs a sixth
   set, written without looking at these.**

`eval_context.py` exists because the harnesses above all ask single
questions with no history, while the reported bug - "why am I getting the
same answers for 2 different questions" - was entirely in the conversational
path. Its seed questions are the ones the reviewer actually typed.

Citation integrity is measured separately, through the real HTTP endpoint,
so it exercises retrieval, prompt building, streaming and the citation frame
rather than calling the model directly:

```bash
python eval_llm.py --engine native      # 8/8 clean, 0 fabricated
python eval_llm.py --engine openai      # 8/8 clean, 0 fabricated, mean 34s
```

Both engines now come back clean on those eight questions. For the native
engine that is structural - it only quotes. For the 3B it is not a guarantee,
which is the point of checking rather than trusting: eight questions is a
small sample, and the model that produced "Rule 3(2)(ii)" is the same model.
What changed is that a fabricated citation is now caught and shown instead of
read as precision.

`citecheck.py` sorts every citation an answer makes into **grounded** (it was
retrieved), **off-context** (it exists but was not retrieved, so nothing
verified what the answer says about it) and **unknown** (no such provision in
our parse - fabricated). The native engine comes back clean because it only
quotes; this proves that per answer instead of asserting it. The UI shows a
warning strip above any answer with a bad citation.

`test_parity.py` checks the Python engine and its JavaScript port agree, by
running the same questions through both and diffing the retrieved citations.
Last run: 22/22, including multi-turn conversations. Without it the port drifts silently, and it had - it still
carried a `with_children` bug Python had fixed, and had no cross-references,
no spell fallback and the old inflated scope gate.

## Known limits

- **The penalty table is transcribed, then verified mechanically.** Every
  `pdftotext` mode scrambles the Schedule's columns, so the seven rows in
  `PENALTIES` (`build_corpus.py`) were typed in by hand. `verify_penalties.py`
  now re-reads the Gazette text and checks that table against it - the
  amounts in document order, the provision each row cites, and the hand
  table itself - and `build_corpus.py` derives `verified_against_gazette`
  from that check rather than hard-coding it. If re-extraction ever changes
  the text, the flag goes back to `false` on its own. Rows 6 and 7 are
  checked by presence only; the Gazette breaks them mid-phrase across the
  column gutter.
- **A list's last item used to vanish.** The cross-reference guard in
  `build_corpus._sequential` rejects a marker preceded by "and" or "or", for
  references like "clause (a) and (b) of section 7" - but a statute ends
  every list the same way, "...; and (c) give, the particular communication
  link...". That dropped the final item of 30 lists, Rule 3(c), section 7(i)
  (the employment ground) and section 11(1)(c) among them. `LIST_CONTINUES`
  now exempts a conjunction that follows a semicolon, colon or comma; citable
  units went from 385 to 425. Found because the local model cited Rule 3(c)
  correctly and `citecheck` called it fabricated.
- **Our parse stops one level down.** Lettered items inside a numbered
  sub-section - section 10(1)(f) inside 10(1) - are not units of their own,
  so 26 lists still carry their items as running text. `citecheck.deeper_item`
  recognises such a citation when its parent clause contains the marker,
  rather than reporting a correct citation as invented, which is the worse
  error of the two.
- **Act section titles are a static map**, because the Act's marginal notes
  can't be reliably tied back to their sections after extraction. The Rules'
  titles are parsed from the text and are trustworthy. Spot-check the Act map.
- **Act commencement dates are absent.** The Act commences by notification
  (G.S.R. 843(E)), which is not one of the two source documents, so `in_force`
  is `null` on every Act section rather than guessed.
- Retrieval is lexical (BM25), bridged to everyday language by a synonym
  layer (`chat.SYNONYMS`) and a spell fallback (`chat.spell_fix`). It is
  strongest on statutory vocabulary and still depends on that layer for
  paraphrase: a phrasing nobody has mapped, in words the statute never uses,
  can still miss. Every rule in that layer came from a question that actually
  failed - see `## Measured accuracy`.
- **The extractive baseline ranks, it does not reason.** It finds the clause
  that answers the question and quotes it; it will not combine two provisions
  or resolve a conflict between them. That is the job a real model has to earn.
- **Only these two documents.** No CERT-In, RBI, SEBI, IRDAI, or sectoral
  overlay. A question that names one of those regimes is now refused by name
  (`chat.OTHER_REGIME`) rather than answered from the wrong statute — asking
  about GDPR fines used to return the Indian Schedule's amounts.
- **Place names are a list, not knowledge.** "Can we store data in
  California?" is a section 16 / Rule 15 question, and nothing in either
  document names a country, so the common ones are enumerated in the transfer
  synonym with generic fallbacks (region, availability zone, data centre).
  An unlisted country in a sentence with no other transfer vocabulary can
  still miss. Indian cities are deliberately absent: hosting in Mumbai is not
  a transfer.
- **The small model copies whatever you show it.** A worked example written
  into the system prompt came back inside an answer; replacing it with
  lettered headings, the model emitted the heading instead. `ask.SYSTEM` now
  has no examples, no labels and no headings, and says not to restate itself.
  `test_dpdp.py` asserts both patterns stay out of it. If you edit that
  prompt, re-run `eval_llm.py --engine openai` afterwards: that harness checks
  the answer contains the required fact, not just that the citation is real.
- **Do not ask a 3B to classify the question.** Told to decide whether the
  user was asking what the law requires or describing a practice to judge, it
  answered "can I transfer personal data outside India?" — six words
  containing no facts — with DO NOT COMPLY. `ask.question_kind` makes that
  call from the question's own shape (first-person pronoun plus a verb of
  doing, past a word count) and `build_prompt` states the answer, so the
  model never has to.
- **The prompt is a budget, not a container.** Six whole provisions came to
  ~13,000 characters and the 3B picked the wrong sub-clause out of them: asked
  how fast a breach must reach the Board it answered "without delay" from
  Rule 7(1)(a), the duty owed to the Data Principal, while Rule 7(2)(b) —
  seventy-two hours — sat further down the same block. The ranker had already
  put 7(2)(b) first. `build_prompt` now leads with those ranked clauses and
  quotes only the top `FULL_PROVISIONS` records in full, listing the rest by
  citation. Across the eight `eval_llm.py` questions that cut the prompts by
  35% (91,674 to 59,740 characters) and the required answer now appears in
  the ranked block for all eight. Retrieval being right is not the same as
  the model being told.
- **Worked examples are not the law.** The Act sets out illustrations inside
  the provisions: section 6(1)'s is 300 characters about a telemedicine app.
  Asked about linking Gmail accounts, the model advised on "telemedicine
  services (if applicable)". `chat.without_illustration` strips them from
  quoted answers and from the model's prompt; they stay in the full text the
  UI shows under **provisions**, where they belong.
- **Chapter demotions have to reach clause level.** `narrow()` demotes
  definitions, enforcement machinery and Consent Manager material per record,
  but `baseline.rank` then re-fits BM25 over sub-clauses and the demotion was
  lost. A company asking whether its terms-and-conditions consent was valid
  got the definition of "user account" third and Consent Manager registration
  fourth. `chapter_weight` is now applied in both places.
- **A greedy 3B repeats itself.** With `temperature: 0` and no penalty it
  emitted an identical "NOT ENOUGH INFORMATION - Rule 3(b) - ..." block twice
  in one answer. `stream_openai` now sends `repeat_penalty`, frequency and
  presence penalties and a `max_tokens` cap; temperature stays at 0 so the
  bench is reproducible.
- **Never show a small model its own previous answer.** A 220-character
  summary of the last answer was still copied wholesale on the next turn,
  trailing "..." included. `build_prompt` now passes only what the *user*
  said earlier - which is where a follow-up's facts actually are.
- **The 3B is the weak link, not retrieval.** The built-in engine answers
  every one of the eight `eval_llm.py` questions correctly and cites nothing
  it was not shown. Where the two disagree, prefer the built-in one — it is
  also what the hosted deployment serves.

## On fine-tuning

Not yet, and not on these documents. The two Acts are about 150 KB of text —
several orders of magnitude too little to train or meaningfully fine-tune a
language model, and training facts into weights is the wrong mechanism for
law anyway: a model that half-remembers a section number is worse than one
that reads it.

The route to an actual fine-tune runs through `data/traces.jsonl`, which
records every question, the provisions retrieved, and the answer given. Once
there are a few thousand rows with the `reviewed` field filled in, the narrow
high-volume tasks are worth distilling into a small model — clause → obligation
classification, control present/absent detection, multilingual notice drafting.
The reasoning and drafting stay on a larger model. That is why the logging
exists from the first commit; those triples cannot be recovered later.

## Not legal advice

A readiness and research aid. Every answer must be traceable to a provision,
and a lawyer still has to sign anything that leaves the building.
