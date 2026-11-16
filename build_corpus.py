"""Stage 2: text -> corpus.json, one record per Section / Rule / Schedule.

Each record carries its own citation, sub-clause map, in-force date and max
penalty, so anything generated from it can be traced back to statute text.
"""
import json
import re
from pathlib import Path

HERE = Path(__file__).parent
DATA = HERE / "data"

# Marginal notes in the Act PDF land on their own lines in raw extraction mode.
# Matching them exactly both removes the furniture and supplies section titles.
ACT_SECTIONS = {
    1: "Short title and commencement",
    2: "Definitions",
    3: "Application of Act",
    4: "Grounds for processing personal data",
    5: "Notice",
    6: "Consent",
    7: "Certain legitimate uses",
    8: "General obligations of Data Fiduciary",
    9: "Processing of personal data of children",
    10: "Additional obligations of Significant Data Fiduciary",
    11: "Right to access information about personal data",
    12: "Right to correction and erasure of personal data",
    13: "Right of grievance redressal",
    14: "Right to nominate",
    15: "Duties of Data Principal",
    16: "Processing of personal data outside India",
    17: "Exemptions",
    18: "Establishment of Board",
    19: "Composition and qualifications for appointment of Chairperson and Members",
    20: "Salary, allowances and other terms and conditions of service of Chairperson and other Members",
    21: "Disqualifications for appointment and continuation as Chairperson and Members of Board",
    22: "Resignation by Members and filling of vacancy",
    23: "Proceedings of Board",
    24: "Officers and employees of Board",
    25: "Members and officers to be public servants",
    26: "Powers of Chairperson",
    27: "Powers and functions of Board",
    28: "Procedure to be followed by Board",
    29: "Appeal to Appellate Tribunal",
    30: "Orders passed by Appellate Tribunal to be executable as decree",
    31: "Alternate dispute resolution",
    32: "Voluntary undertaking",
    33: "Penalties",
    34: "Crediting sums realised by way of penalties to Consolidated Fund of India",
    35: "Protection of action taken in good faith",
    36: "Power to call for information",
    37: "Power of Central Government to issue directions",
    38: "Consistency with other laws",
    39: "Bar of jurisdiction",
    40: "Power to make rules",
    41: "Laying of rules and certain notifications before Parliament",
    42: "Power to amend Schedule",
    43: "Power to remove difficulties",
    44: "Amendments to certain enactments",
}

# Rule 1(2)-(4) of the DPDP Rules 2025, Gazette 13 November 2025 (G.S.R. 846(E)).
GAZETTE = "2025-11-13"
RULE_IN_FORCE = {}
for _n in (1, 2, 17, 18, 19, 20, 21):
    RULE_IN_FORCE[_n] = GAZETTE
RULE_IN_FORCE[4] = "2026-11-13"
for _n in (3, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 22, 23):
    RULE_IN_FORCE[_n] = "2027-05-13"

# The Schedule to the Act. Its penalty table is column-scrambled by every
# pdftotext mode, so it is transcribed here rather than parsed. VERIFY against
# the Gazette before treating these as authoritative scoring weights.
# Derived, not asserted: verify_penalties re-reads the Gazette text and
# checks this table against it. See that module for why rows 6-7 differ.
def penalties_verified():
    import verify_penalties
    act = (DATA / "act2023.txt").read_text(encoding="utf-8")
    ok, problems = verify_penalties.verify(act, PENALTIES)
    for p in problems:
        print("  penalty check:", p)
    return ok


PENALTIES = [
    {
        "provision": "Section 8(5)",
        "max_words": "two hundred and fifty crore rupees",
        "breach": "Failure to take reasonable security safeguards to prevent personal data breach",
        "max_inr": 2_500_000_000,
    },
    {
        "provision": "Section 8(6)",
        "max_words": "two hundred crore rupees",
        "breach": "Failure to give the Board or affected Data Principal notice of a personal data breach",
        "max_inr": 2_000_000_000,
    },
    {
        "provision": "Section 9",
        "max_words": "two hundred crore rupees",
        "breach": "Breach of additional obligations in relation to children",
        "max_inr": 2_000_000_000,
    },
    {
        "provision": "Section 10",
        "max_words": "one hundred and fifty crore rupees",
        "breach": "Breach of additional obligations of Significant Data Fiduciary",
        "max_inr": 1_500_000_000,
    },
    {
        "provision": "Section 15",
        "max_words": "ten thousand rupees",
        "breach": "Breach of the duties of a Data Principal",
        "max_inr": 10_000,
    },
    {
        "provision": "Section 32",
        "max_words": "up to the extent applicable for the underlying breach",
        "breach": "Breach of any term of a voluntary undertaking accepted by the Board",
        "max_inr": None,
    },
    {
        "provision": "residual",
        "max_words": "fifty crore rupees",
        "breach": "Breach of any other provision of the Act or the rules",
        "max_inr": 500_000_000,
    },
]
SECTION_PENALTY = {8: 2_500_000_000, 9: 2_000_000_000, 10: 1_500_000_000, 15: 10_000}

TAG_PATTERNS = {
    "consent": r"\bconsent\b",
    "consent-manager": r"Consent Manager",
    "notice": r"\bnotice\b",
    "breach": r"personal data breach",
    "security": r"security safeguards|encryption|obfuscation|masking|virtual token",
    "children": r"\bchild\b|\bchildren\b|\bparent\b",
    "disability": r"person with disability|lawful guardian",
    "retention": r"\berase\b|\berasure\b|retain|retention",
    "cross-border": r"outside the territory of India|outside India|foreign State",
    "rights": r"right to|rights of Data Principal|grievance",
    "sdf": r"Significant Data Fiduciary",
    "processor": r"Data Processor",
    "logs": r"\blogs\b",
    "penalty": r"monetary penalty|penalt",
    "exemption": r"shall not apply|exempt",
    "state": r"State and its instrumentalities|subsidy, benefit",
}

FURNITURE = re.compile(
    r"GAZETTE OF INDIA"
    r"|^\[?\s*PART II"
    r"|^SEC\.\s"
    r"|^\[\s*II"
    # Bilingual Hindi running heads; the English text carries no Devanagari.
    r"|[ऀ-ॿ]{2,}"
    r"|^\s*\d{1,3}\s*$"
    r"|UPLOADED BY THE MANAGER"
    r"|PUBLISHED BY THE CONTROLLER"
    r"|^MGIPMRND"
    r"|Digitally signed"
    r"|^\s*Kshitiz"
    r"|^\s*Mohan\b"
    r"|REETA VASISHTA"
    r"|Secretary to the Govt"
)

DASH = r"(?:--|[‒–—]+)"

SUBCLAUSE = re.compile(r"\((\d{1,2})\)\s")


def clean(raw, drop_exact):
    out = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or FURNITURE.search(stripped):
            continue
        if stripped.rstrip(".") in drop_exact:
            continue
        out.append(line.rstrip())
    return out


LETTERED = re.compile(r"\(([a-z])\)\s")


# "...rights under sub-section (2) of section 6" is a cross-reference, not the
# start of sub-section (2). Taking it as one truncated section 5(1) mid-sentence
# and gave the remainder the wrong citation.
REFERENCE = re.compile(
    r"(sub-?section|sub-?rule|clause|section|rule|item|paragraph|schedule|"
    r"under|of|in|to|and|or|with)\s*$", re.I)

# "and" and "or" in REFERENCE are there for cross-references like "clause (a)
# and (b) of section 7". But a statute ends every list the same way - "...; and
# (c) give, the particular communication link..." - so that guard was dropping
# the LAST item of 30 lists, Rule 3(c), section 7(i) and section 11(1)(c)
# among them. A conjunction after a semicolon, colon or comma is list
# punctuation, not a reference.
LIST_CONTINUES = re.compile(r"[;:,]\s*(and|or)\s*$", re.I)


def _sequential(body, pattern, first, nxt, cast=str):
    """Split on markers running in order; out-of-order ones are references.

    A sub-clause runs to the next ACCEPTED marker, not to the next thing that
    merely looks like one. Ending at the next raw match stopped section 5(1)
    at "...under sub-section (4) of section 6", losing the rest.
    """
    accepted, expect = [], first
    for m in pattern.finditer(body):
        if cast(m.group(1)) != expect:
            continue
        before = body[max(0, m.start() - 40):m.start()]
        if REFERENCE.search(before) and not LIST_CONTINUES.search(before):
            continue
        accepted.append((expect, m.start(), m.end()))
        expect = nxt(expect)

    found = {}
    for i, (key, _start, content_at) in enumerate(accepted):
        end = accepted[i + 1][1] if i + 1 < len(accepted) else len(body)
        found[key] = " ".join(body[content_at:end].split())
    return found


def subclauses(label, body):
    """Split a provision into its sub-clauses for precise citation.

    Most provisions number their sub-sections (1), (2); but some - section 7's
    legitimate uses, section 15's duties - go straight to lettered clauses.
    Those used to yield nothing, so a four-thousand-character section became a
    single unquotable blob that answers truncated at 380 characters.
    """
    numbered = _sequential(body, SUBCLAUSE, 1, lambda n: n + 1, int)
    if numbered:
        return {"{}({})".format(label, n): t for n, t in numbered.items()}

    lettered = _sequential(body, LETTERED, "a", lambda c: chr(ord(c) + 1))
    if not lettered:
        return {}
    out = {}
    # The chapeau carries the operative verb - "may process ... for the
    # following uses" - so a lettered clause quoted without it says nothing.
    head = body[: body.index("(a) ")].strip() if "(a) " in body else ""
    head = " ".join(head.split())
    if len(head.split()) > 4:
        out[label] = head
    out.update({"{}({})".format(label, c): t for c, t in lettered.items()})
    return out


def tags_for(text):
    return sorted(t for t, p in TAG_PATTERNS.items() if re.search(p, text, re.I))


def split_sequential(lines, last, pattern):
    """Carve text into numbered provisions by expecting 1, 2, 3... in order.

    Matching every 'N.' at a line start would also catch list items and
    cross-references; expecting only the next number is simpler and right.
    """
    starts = {}
    expected = 1
    for i, line in enumerate(lines):
        if expected > last:
            break
        if re.match(pattern.format(n=expected), line.strip()):
            starts[expected] = i
            expected += 1
    bounds = sorted(starts.items())
    out = {}
    for idx, (num, start) in enumerate(bounds):
        end = bounds[idx + 1][1] if idx + 1 < len(bounds) else len(lines)
        out[num] = "\n".join(lines[start:end]).strip()
    return out


def build_act():
    raw = (DATA / "act2023.txt").read_text(encoding="utf-8")
    lines = clean(raw, set(ACT_SECTIONS.values()))
    bodies = split_sequential(lines, 44, r"^{n}\.\s")
    records = []
    for num, body in sorted(bodies.items()):
        records.append({
            "id": "act-s" + str(num),
            "source": "DPDP Act 2023",
            "citation": "Section " + str(num),
            "title": ACT_SECTIONS[num],
            "text": body,
            "subclauses": subclauses(str(num), body),
            "in_force": None,
            "in_force_note": (
                "Act commencement is by notification (G.S.R. 843(E)); that "
                "notification is not among these source documents."
            ),
            "max_penalty_inr": SECTION_PENALTY.get(num),
            "tags": tags_for(body),
        })
    return records


ORDINALS = ["FIRST", "SECOND", "THIRD", "FOURTH", "FIFTH", "SIXTH", "SEVENTH"]


def build_rule_schedules(lines, start):
    marks = []
    for i in range(start, len(lines)):
        s = lines[i].strip()
        for o in ORDINALS:
            if s == o + " SCHEDULE":
                marks.append((o, i))
    records = []
    for idx, (ordinal, at) in enumerate(marks):
        end = marks[idx + 1][1] if idx + 1 < len(marks) else len(lines)
        body = "\n".join(lines[at:end]).strip()
        records.append({
            "id": "rules-sch" + str(idx + 1),
            "source": "DPDP Rules 2025",
            "citation": ordinal.title() + " Schedule",
            "title": ordinal.title() + " Schedule to the DPDP Rules 2025",
            "text": body,
            "subclauses": {},
            "in_force": None,
            "max_penalty_inr": None,
            "tags": tags_for(body),
        })
    return records


def build_rules():
    raw = (DATA / "rules2025.txt").read_text(encoding="utf-8")
    lines = clean(raw, set())
    # Rule bodies run up to the First Schedule; schedules are carved separately.
    first_schedule = len(lines)
    for i, line in enumerate(lines):
        if line.strip() == "FIRST SCHEDULE":
            first_schedule = i
            break
    bodies = split_sequential(lines[:first_schedule], 23, r"^{n}\.\s*\S")
    records = []
    for num, body in sorted(bodies.items()):
        # Titles wrap across lines in the layout-mode text, so match on one
        # line. The Gazette separates title from body with an em-dash.
        m = re.match(r"^\d{1,2}\.\s*(.+?)\.\s*" + DASH, " ".join(body.split()))
        records.append({
            "id": "rules-r" + str(num),
            "source": "DPDP Rules 2025",
            "citation": "Rule " + str(num),
            "title": m.group(1).strip() if m else "",
            "text": body,
            "subclauses": subclauses(str(num), body),
            "in_force": RULE_IN_FORCE.get(num),
            "max_penalty_inr": None,
            "tags": tags_for(body),
        })
    records.extend(build_rule_schedules(lines, first_schedule))
    return records


def build_act_schedule(verified):
    lines = []
    for p in PENALTIES:
        cap = p["max_words"]
        if p["max_inr"] is not None:
            cap += " (INR {:,})".format(p["max_inr"])
        lines.append(
            p["provision"] + ": " + p["breach"] + " - penalty may extend to " + cap
        )
    return {
        "id": "act-schedule",
        "source": "DPDP Act 2023",
        "citation": "The Schedule",
        "title": "The Schedule (monetary penalties), see section 33(1)",
        "text": "\n".join(lines),
        "subclauses": {},
        "in_force": None,
        "max_penalty_inr": max(p["max_inr"] for p in PENALTIES if p["max_inr"]),
        "tags": ["penalty"],
        "transcribed_not_parsed": True,
        "verified_against_gazette": verified,
        "penalties": PENALTIES,
    }


def main():
    verified = penalties_verified()
    records = build_act() + build_rules() + [build_act_schedule(verified)]
    corpus = {
        "meta": {
            "sources": [
                {"name": "DPDP Act 2023", "citation": "Act 22 of 2023"},
                {
                    "name": "DPDP Rules 2025",
                    "citation": "G.S.R. 846(E), Gazette 13 November 2025",
                },
            ],
            "commencement": {
                "immediate_2025_11_13": [1, 2, 17, 18, 19, 20, 21],
                "one_year_2026_11_13": [4],
                "eighteen_months_2027_05_13": [
                    3, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 22, 23
                ],
            },
            "penalties_verified_against_gazette": verified,
        },
        "records": records,
    }
    (DATA / "corpus.json").write_text(
        json.dumps(corpus, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    sections = sum(1 for r in records if r["id"].startswith("act-s") and r["id"] != "act-schedule")
    rules = sum(1 for r in records if r["id"].startswith("rules-r"))
    scheds = sum(1 for r in records if "sch" in r["id"])
    print("corpus.json: {} records ({} sections, {} rules, {} schedules)".format(
        len(records), sections, rules, scheds))


if __name__ == "__main__":
    main()
