"""Accuracy harness: fixed questions with answers checked against the statute.

Run `python eval.py` for a score, `python eval.py -v` to see every failure.
Each case names the provision that governs it and a phrase the answer must
contain. The expected values were read off the two source documents, so a
failure here is a real defect, not a matter of taste.
"""
import sys

import ask
import chat
from retrieve import load

# (question, citation the answer must rest on, a phrase it must contain)
CASES = [
    # Breach
    ("how fast must I report a breach to the Board?", "Rule 7", "seventy-two hours"),
    ("what must I tell users after a data breach?", "Rule 7", "consequences"),
    ("who do I notify when there is a personal data breach?", "Rule 7", "Board"),
    # Security
    ("what security measures are mandatory at minimum?", "Rule 6", "encryption"),
    ("do I have to keep access logs?", "Rule 6", "logs"),
    ("how long must I retain logs?", "Rule 6", "one year"),
    # Notice and consent
    ("what must a notice to the data principal contain?", "Section 5", "purpose"),
    ("what makes consent valid under DPDP?", "Section 6", "free, specific, informed"),
    ("can consent be withdrawn?", "Section 6", "withdraw"),
    ("in what languages must a consent request be available?",
     "Section 6", "Eighth Schedule"),
    # Children
    ("do I need parental consent for a 16 year old?", "Section 9", "verifiable consent"),
    ("can I show targeted ads to children?", "Section 9", "advertising"),
    # Erasure and retention
    ("when must I erase personal data?", "Section 8", "erase"),
    ("must I warn a user before deleting their data?", "Rule 8", "forty-eight hours"),
    # Rights
    ("what rights does a data principal have?", "Section 11", "summary"),
    ("how long do I have to resolve a grievance?", "Rule 14", "ninety days"),
    ("must I publish a contact for data protection questions?", "Rule 9", "contact"),
    # Processors, transfer, SDF
    ("can I use a vendor to process personal data?", "Section 8", "contract"),
    ("can I store personal data outside India?", "Rule 15", "outside the territory"),
    ("what extra duties does a Significant Data Fiduciary have?",
     "Rule 13", "Data Protection Impact Assessment"),
    # Penalties
    ("what is the maximum penalty for a security failure?",
     "The Schedule", "two hundred and fifty crore"),
    # Scope lives in eval_paraphrase.py now, which separates the two real
    # requirements: naming another regime must be refused outright, while
    # nonsense only has to avoid asserting a verdict. Asserting a single
    # refusal phrase here was what made the scope gate too strict, because
    # tightening it until "capital of France" was refused also refused
    # "can consent be withdrawn?".
    # Permission is not obligation: this once answered "Yes - this is
    # required" for a question about whether something was allowed.
    ("can I keep data on a server in Singapore?", "Rule 15", "permitted"),
    ("do I need parental consent for a 16 year old?", "Section 9", "required"),
]


def run(verbose=False):
    _meta, index = load()
    penalties = ask.penalties_from(index)
    passed = cite_ok = phrase_ok = 0

    for question, want_cite, want_phrase in CASES:
        hits, query = chat.search(index, question, None, k=4)
        answer = chat.respond(question, hits, penalties=penalties, query=query)
        got_cites = [r["citation"] for _s, r in hits]

        c_ok = want_cite is None or want_cite in got_cites
        p_ok = want_phrase.lower() in answer.lower()
        cite_ok += c_ok
        phrase_ok += p_ok
        if c_ok and p_ok:
            passed += 1
        elif verbose:
            print("-" * 70)
            print("Q:", question)
            print("   want citation:", want_cite, "| got:", got_cites, "" if c_ok else "<-- MISS")
            print("   want phrase  :", repr(want_phrase), "" if p_ok else "<-- MISS")
            if not p_ok:
                print("   answer:", " ".join(answer.split())[:220])

    n = len(CASES)
    print("\ncitation found : {:>2}/{} ({:.0f}%)".format(cite_ok, n, 100 * cite_ok / n))
    print("phrase present : {:>2}/{} ({:.0f}%)".format(phrase_ok, n, 100 * phrase_ok / n))
    print("both           : {:>2}/{} ({:.0f}%)".format(passed, n, 100 * passed / n))
    return passed, n


if __name__ == "__main__":
    run(verbose="-v" in sys.argv)
