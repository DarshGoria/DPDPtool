"""Stage 3: BM25 retrieval over corpus.json. Standard library only.

Statutory queries reuse statutory vocabulary ("reasonable security safeguards",
"verifiable consent"), so lexical scoring is a good fit here and avoids pulling
in an embedding model that a CPU-only box would rather not load.
"""
import json
import math
import re
from pathlib import Path

CORPUS = Path(__file__).parent / "data" / "corpus.json"

STOP = {
    "a", "an", "and", "any", "are", "as", "at", "be", "by", "can", "do", "does",
    "for", "from", "has", "have", "how", "i", "if", "in", "is", "it", "its",
    "may", "must", "my", "need", "of", "on", "or", "our", "shall", "should",
    "such", "that", "the", "their", "them", "there", "these", "this", "to",
    "under", "was", "we", "what", "when", "which", "who", "will", "with",
}

# Crude suffix stripping: enough to tie erase/erasure/erased and
# retain/retention together without a stemmer dependency.
SUFFIXES = ("ations", "ation", "ingly", "ities", "ting", "ing", "ies", "ers",
            "ed", "es", "s")

K1 = 1.5
B = 0.75


def stem(word):
    for suf in SUFFIXES:
        if len(word) > len(suf) + 3 and word.endswith(suf):
            return word[: -len(suf)]
    return word


def tokenize(text):
    words = re.findall(r"[a-z0-9]+", text.lower())
    return [stem(w) for w in words if w not in STOP and len(w) > 1]


class Index:
    def __init__(self, records, text_fn=None):
        self.records = records
        make_text = text_fn or self._doc_text
        self.docs = [tokenize(make_text(r)) for r in records]
        self.lengths = [len(d) for d in self.docs]
        self.avg_len = sum(self.lengths) / len(self.docs) if self.docs else 0.0
        self.freqs = []
        doc_count = {}
        for tokens in self.docs:
            tf = {}
            for t in tokens:
                tf[t] = tf.get(t, 0) + 1
            self.freqs.append(tf)
            for t in tf:
                doc_count[t] = doc_count.get(t, 0) + 1
        n = len(self.docs)
        self.idf = {
            t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in doc_count.items()
        }

    @staticmethod
    def _doc_text(record):
        # Title and citation repeated so a direct "Rule 7" style query lands.
        return " ".join([
            record["citation"], record["citation"],
            record["title"], record["title"],
            " ".join(record.get("tags", [])),
            record["text"],
        ])

    def search(self, query, k=5):
        q = tokenize(query)
        scored = []
        for i, tf in enumerate(self.freqs):
            score = 0.0
            for term in q:
                f = tf.get(term)
                if not f:
                    continue
                denom = f + K1 * (1 - B + B * self.lengths[i] / self.avg_len)
                score += self.idf.get(term, 0.0) * f * (K1 + 1) / denom
            if score > 0:
                scored.append((score, i))
        scored.sort(key=lambda p: (-p[0], p[1]))
        return [(s, self.records[i]) for s, i in scored[:k]]


def load(path=CORPUS):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data["meta"], Index(data["records"])


if __name__ == "__main__":
    import sys

    meta, index = load()
    question = " ".join(sys.argv[1:]) or "how long do I have to report a breach"
    for score, rec in index.search(question):
        print("{:6.2f}  {:<22} {}".format(
            score, rec["source"] + " " + rec["citation"], rec["title"][:60]))
