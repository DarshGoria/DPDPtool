"""Does retrieval work when nobody uses statutory vocabulary?

eval_coverage.py queries each provision with its own distinctive words and
scores 97% at rank 1. That is the easy case. Real users write "we had a leak,
who do we tell", not "intimation of personal data breach". This file is 60
questions in the words a founder or a product manager would actually use,
deliberately avoiding the statute's own phrasing wherever an everyday
synonym exists.

Each case lists every provision that could fairly govern it, and passes if
any one of them is retrieved. Several questions genuinely engage more than
one provision - "can we store data abroad" is section 16 and Rule 15 - and
marking one of those wrong would be scoring my own opinion, not the law.

The last group is deliberately out of scope: a compliance tool that answers
confidently about things the Act does not cover is worse than one that says
so. Those cases pass when nothing is retrieved or the answer declines.

  python eval_paraphrase.py        score
  python eval_paraphrase.py -v     every miss, with what came back instead
"""
import sys

import ask
import chat
from retrieve import load

# (question, provisions any one of which is a fair answer)
CASES = [
    # --- scope -------------------------------------------------------------
    ("we run a lending app in Mumbai, does this law cover us?", ["Section 3"]),
    ("we are based in Dubai but all our users are Indian, are we caught?",
     ["Section 3"]),
    ("does this apply to data we only keep on paper?", ["Section 3", "Section 2"]),
    ("when does this law actually start applying to us?", ["Rule 1"]),
    ("is the whole thing in force already or is it staged?", ["Rule 1"]),

    # --- basis for processing ---------------------------------------------
    ("do we need a reason to use someone's information?", ["Section 4"]),
    ("what are the only two ways we are allowed to process data?",
     ["Section 4"]),
    ("can we use data without asking if it is for a government subsidy?",
     ["Section 7"]),
    ("do we need permission to use data in a medical emergency?",
     ["Section 7"]),
    ("can we process our own employees' data without asking them?",
     ["Section 7"]),

    # --- telling people ---------------------------------------------------
    ("what do we have to tell a customer when we collect their details?",
     ["Section 5", "Rule 3"]),
    ("does the disclosure have to be written in plain language?",
     ["Section 5", "Rule 3"]),
    ("must the request be separate from our terms and conditions?",
     ["Rule 3", "Section 5", "Section 6"]),
    ("do we have to tell people how to complain when we sign them up?",
     ["Section 5", "Rule 3"]),

    # --- permission --------------------------------------------------------
    ("what makes permission from a user actually valid?", ["Section 6"]),
    ("can a customer take back their permission later?", ["Section 6"]),
    ("if someone changes their mind, what do we do with what we already hold?",
     ["Section 6", "Section 8"]),
    ("which languages do we have to offer the sign-up request in?",
     ["Section 6", "Rule 3"]),
    ("is a pre-ticked box good enough to get agreement?", ["Section 6"]),
    ("can we bundle agreement for ten different things into one click?",
     ["Section 6"]),

    # --- fiduciary duties --------------------------------------------------
    ("our vendor actually handles the data, who is on the hook?",
     ["Section 8"]),
    ("do we have to make sure what we hold about someone is correct?",
     ["Section 8"]),
    ("can we hire an outside company to crunch the data for us?",
     ["Section 8"]),
    ("what security are we legally required to have in place?",
     ["Rule 6", "Section 8"]),
    ("do we have to scramble the data we store?", ["Rule 6"]),
    ("are we required to keep a record of who looked at what?",
     ["Rule 6"]),
    # Was "how far back do we have to keep those records?" - a defective case:
    # "those records" pointed at a previous turn this harness never supplies,
    # so in isolation it is genuinely ambiguous between Rule 6 (logs, one
    # year) and Rule 8 (personal data). Reworded to stand alone, still
    # without using the statute's word "logs".
    ("how long must we keep the record of who accessed what?", ["Rule 6"]),
    ("what do we need in our contract with a cloud provider?",
     ["Rule 6", "Section 8"]),

    # --- breach ------------------------------------------------------------
    ("we had a leak, who do we have to tell?", ["Rule 7"]),
    ("how fast do we have to report a leak to the regulator?", ["Rule 7"]),
    ("what details go into the report we file about a leak?", ["Rule 7"]),
    ("do we have to tell the affected customers themselves?", ["Rule 7"]),

    # --- retention and deletion -------------------------------------------
    ("how long are we allowed to keep customer information?",
     ["Rule 8", "Third Schedule", "Section 8"]),
    ("do we have to warn someone before we wipe their account?", ["Rule 8"]),
    ("when are we obliged to throw data away?",
     ["Section 8", "Rule 8", "Third Schedule"]),
    ("a user has not logged in for two years, what then?",
     ["Rule 8", "Third Schedule"]),

    # --- children ----------------------------------------------------------
    ("our app has sixteen year old users, what changes for us?",
     ["Section 9", "Rule 10"]),
    ("how do we check that someone really is the parent?", ["Rule 10"]),
    ("can we show targeted ads to under eighteens?", ["Section 9"]),
    ("are we allowed to track a minor's location?", ["Section 9"]),
    ("does the under-eighteen rule apply to a hospital's app?",
     ["Rule 12", "Fourth Schedule"]),
    ("what if our user is an adult who has a court appointed guardian?",
     ["Rule 11", "Section 9"]),

    # --- large operators ---------------------------------------------------
    ("what happens if we get labelled a big operator?",
     ["Section 10", "Rule 13"]),
    ("do we have to run an impact assessment?", ["Rule 13", "Section 10"]),
    ("are we going to need an independent audit?", ["Rule 13", "Section 10"]),
    ("do we have to appoint someone in India to answer for this?",
     ["Section 10", "Rule 9"]),

    # --- user rights -------------------------------------------------------
    ("a customer has asked what information we hold on them",
     ["Section 11"]),
    ("a customer says their address is wrong and wants it fixed",
     ["Section 12"]),
    ("someone wants us to erase everything about them", ["Section 12"]),
    ("do we have to give customers somewhere to complain?",
     ["Section 13", "Rule 14"]),
    ("how many days do we get to settle a complaint?", ["Rule 14"]),
    ("can a user name somebody to act for them if they die?",
     ["Section 14"]),
    ("a user keeps filing fake complaints, is that on them?",
     ["Section 15"]),
    ("do we need to publish a contact for data questions?",
     ["Rule 9", "Section 5"]),

    # --- transfers and exemptions -----------------------------------------
    ("can we keep our database on servers in the United States?",
     ["Section 16", "Rule 15"]),
    ("can the government block us from sending data overseas?",
     ["Section 16", "Rule 15"]),
    ("do any of these rules apply to the police?", ["Section 17"]),
    ("does any of this apply to academic research?",
     ["Rule 16", "Second Schedule", "Section 17"]),

    # --- money -------------------------------------------------------------
    ("how much can we be fined for a leak?", ["The Schedule"]),
    ("what is the largest fine under this law?", ["The Schedule"]),
    ("what does it cost us if we get the children's rules wrong?",
     ["The Schedule"]),
    ("can we appeal if we are fined?", ["Section 29", "Section 28"]),

    # --- the user's own domain --------------------------------------------
    ("we read users' text messages to work out their credit limit, what do we need?",
     ["Section 4", "Section 5", "Section 6"]),
    ("we prefill a loan amount from bank SMS, is that allowed?",
     ["Section 4", "Section 5", "Section 6", "Section 7"]),
    ("can we pass customer details to a credit bureau?",
     ["Section 4", "Section 5", "Section 8"]),
]

# Two different requirements, which the first version of this file conflated.
#
# Naming another legal regime must never produce DPDP content: a lawyer who
# asks about GDPR fines and is shown the Indian Schedule's amounts has been
# actively misled. These must refuse and say which regime they are about.
OTHER_LAW = [
    "what are the GDPR fines in Europe?",
    "how much income tax do I owe this year?",
    "what does CERT-In require after an incident?",
    "what are RBI's data localisation rules?",
]
# Nonsense only has to avoid asserting a verdict. A hedge ("I am not confident
# I found the right provision - these are the closest") is a fine answer to a
# question about CSS, and demanding a flat refusal here is what pushed the
# scope gate into refusing real questions like "can consent be withdrawn?".
NONSENSE = [
    "what is the capital of France?",
    "write me a poem about dogs",
    "how do I centre a div in css?",
    "what is the weather tomorrow?",
]
DECLINE = ("outside the dpdp act", "not covered", "nothing in the",
           "which i do not cover")
# A headline verdict. Anything starting like this is an assertion, not a hedge.
ASSERTS = ("yes -", "no -", "yes,", "no,")


def run(verbose=False):
    _meta, index = load()
    penalties = ask.penalties_from(index)
    passed = 0
    misses = []

    for question, accept in CASES:
        hits, query = chat.search(index, question, None, k=4)
        got = [r["citation"] for _s, r in hits]
        if any(a in got for a in accept):
            passed += 1
        else:
            misses.append((question, accept, got))

    declined = 0
    scope_misses = []
    for question in OTHER_LAW:
        hits, query = chat.search(index, question, None, k=4)
        answer = chat.respond(question, hits, penalties=penalties, query=query)
        if any(d in answer.lower() for d in DECLINE):
            declined += 1
        else:
            scope_misses.append((question, " ".join(answer.split())[:150]))

    hedged = 0
    for question in NONSENSE:
        hits, query = chat.search(index, question, None, k=4)
        answer = chat.respond(question, hits, penalties=penalties, query=query)
        low = answer.lower().lstrip()
        if any(d in low for d in DECLINE) or not low.startswith(ASSERTS):
            hedged += 1
        else:
            scope_misses.append((question, " ".join(answer.split())[:150]))

    if verbose:
        for question, accept, got in misses:
            print("-" * 72)
            print("MISS", question)
            print("     want any of:", accept)
            print("     got        :", got)
        for question, answer in scope_misses:
            print("-" * 72)
            print("ANSWERED OUT OF SCOPE:", question)
            print("     ->", answer)

    n = len(CASES)
    print("\nplain-language retrieval : {:>2}/{} ({:.0f}%)".format(
        passed, n, 100 * passed / n))
    print("other regimes refused    : {:>2}/{} ({:.0f}%)".format(
        declined, len(OTHER_LAW), 100 * declined / len(OTHER_LAW)))
    print("nonsense not asserted    : {:>2}/{} ({:.0f}%)".format(
        hedged, len(NONSENSE), 100 * hedged / len(NONSENSE)))
    return passed, n


if __name__ == "__main__":
    run(verbose="-v" in sys.argv)
