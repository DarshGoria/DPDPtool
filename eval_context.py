"""Does the conversation help when it should, and stay out of the way otherwise?

The bug behind "why am I getting the same answers for 2 different questions"
was here: every short question was merged with the previous turn, so a new
subject inherited the old one's provisions. "i use users data to make them
login" kept answering from the SMS question before it, because it is five
words long and contains "them".

chat.search handles this by trying the question alone first and falling back
to the merged query only when the standalone one finds nothing convincing
(CONFIDENT_ALONE), and then only if the merged one is clearly better per word
(CONTEXT_MARGIN). None of the other harnesses exercise any of that - they all
ask single questions with no history.

Two things are checked:

  SHIFTS   a new subject must NOT come back with the previous turn's
           provisions. The seed questions are the ones a reviewer actually
           typed, in the order they typed them.
  FOLLOWS  a real follow-up ("and what about the Board?") must keep the
           topic, or the conversation is useless.

  python eval_context.py
  python eval_context.py -v
"""
import sys

import chat
from retrieve import load


def cites(index, question, history):
    hits, _query = chat.search(index, question, history, k=4)
    return [r["citation"] for _s, r in hits]


def turn(history, question, answer):
    return history + [{"role": "user", "content": question},
                      {"role": "assistant", "content": answer}]


# Each: (opening question, next question, what must NOT carry over).
# The first is the exchange the reviewer actually had.
SHIFTS = [
    ("we use sms of users to read the credited amount and show prefills",
     "i use users data to make them login"),
    ("i use users data to make them login",
     "how fast must I report a breach to the Board?"),
    ("how long must I retain logs?",
     "do I need parental consent for a 16 year old?"),
    ("what is the penalty for a security failure?",
     "can I transfer personal data outside India?"),
    ("what must a notice contain?",
     "how long do I have to resolve a grievance?"),
]

# Each: (opening, follow-up, a citation the follow-up must still reach).
FOLLOWS = [
    ("how fast must I report a breach to the Board?",
     "and what must I tell the affected users?", "Rule 7"),
    ("do I need parental consent for a 16 year old?",
     "how do I verify that person is really the parent?", "Rule 10"),
    ("what security measures are mandatory?",
     "and how long do those records have to be kept?", "Rule 6"),
    ("can I transfer personal data outside India?",
     "what conditions apply to that?", "Rule 15"),
]


def run(verbose=False):
    _meta, index = load()
    shifted, kept = 0, 0
    problems = []

    for first, second in SHIFTS:
        first_cites = cites(index, first, [])
        history = turn([], first, "(answer)")
        with_context = cites(index, second, history)
        alone = cites(index, second, [])
        # The test is not that context is ignored - it is that a new subject
        # does not simply inherit the old one's provisions. Identical results
        # to the previous turn's is the failure the reviewer saw.
        if with_context and with_context != first_cites:
            shifted += 1
        else:
            problems.append(("SUBJECT DID NOT SHIFT", second,
                             "previous turn: {}".format(first_cites),
                             "with context: {}".format(with_context),
                             "alone       : {}".format(alone)))

    for first, second, want in FOLLOWS:
        history = turn([], first, "(answer)")
        got = cites(index, second, history)
        if want in got:
            kept += 1
        else:
            problems.append(("TOPIC LOST", second,
                             "want: {}".format(want),
                             "got : {}".format(got), ""))

    if verbose:
        for label, question, a, b, c in problems:
            print("-" * 72)
            print(label, "|", question)
            for line in (a, b, c):
                if line:
                    print("   ", line)

    print("\nsubject shifts respected : {:>2}/{} ({:.0f}%)".format(
        shifted, len(SHIFTS), 100 * shifted / len(SHIFTS)))
    print("follow-ups keep topic    : {:>2}/{} ({:.0f}%)".format(
        kept, len(FOLLOWS), 100 * kept / len(FOLLOWS)))
    return shifted + kept, len(SHIFTS) + len(FOLLOWS)


if __name__ == "__main__":
    run(verbose="-v" in sys.argv)
