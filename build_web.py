"""Stage 5: emit a standalone page that runs the whole lookup in the browser.

Retrieval and the extractive answer need no model, so they can run client-side.
The sub-clause splitting (Schedule Parts, lettered and roman items) is computed
here by the tested Python and shipped as data - the browser only has to score,
so the citations it shows are the ones test_dpdp.py checks.
"""
import json
import re
from pathlib import Path

import baseline
import export_chat_tables
from retrieve import tokenize

HERE = Path(__file__).parent
DATA = HERE / "data"
DIST = HERE / "dist"
MIN_TOKENS = baseline.MIN_TOKENS


def payload():
    corpus = json.loads((DATA / "corpus.json").read_text(encoding="utf-8"))
    records, units = [], []
    for i, rec in enumerate(corpus["records"]):
        records.append({
            "i": i,
            # chat.narrow matches chapters by id, so the browser port needs it.
            "id": rec["id"],
            "src": "Rules 2025" if rec["source"].endswith("2025") else "Act 2023",
            "cite": rec["citation"],
            "title": rec["title"],
            "text": rec["text"],
            "inForce": rec.get("in_force"),
            "penalty": rec.get("max_penalty_inr"),
            "tags": rec.get("tags", []),
        })
        for cite, text in baseline.units(rec):
            if len(tokenize(text)) >= MIN_TOKENS:
                units.append({"r": i, "cite": cite, "text": text})
    return {
        "meta": {
            "commencement": corpus["meta"]["commencement"],
            "penaltiesVerified": corpus["meta"]["penalties_verified_against_gazette"],
        },
        "records": records,
        "units": units,
        # The Schedule's rows, for answering "what is the penalty for...".
        "penalties": next(
            (r.get("penalties") for r in corpus["records"]
             if r["id"] == "act-schedule"), None),
    }


PAGES = {
    "web/lookup.html": "dist/dpdp_lookup.html",
    "web/gapcheck.html": "dist/dpdp_gapcheck.html",
    "web/bench.html": "dist/dpdp_bench.html",
}


def main():
    data = payload()
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    # A literal </script> inside the JSON would close the tag early.
    blob = blob.replace("</", "<\\/")
    engine = (HERE / "web" / "engine.js").read_text(encoding="utf-8")
    chat_js = (HERE / "web" / "chat.js").read_text(encoding="utf-8").replace(
        "__CHAT_TABLES__",
        json.dumps(export_chat_tables.tables(), ensure_ascii=False))

    DIST.mkdir(exist_ok=True)
    for src, dest in PAGES.items():
        html = (HERE / src).read_text(encoding="utf-8")
        assert "__CORPUS__" in html, src + " lost its data placeholder"
        assert "__ENGINE_JS__" in html, src + " lost its engine placeholder"
        html = html.replace("__ENGINE_JS__", engine)
        if "__CHAT_JS__" in html:
            html = html.replace("__CHAT_JS__", chat_js)
        html = html.replace("__CORPUS__", blob)
        out = HERE / dest
        out.write_text(html, encoding="utf-8")
        print("{}: {} records, {} units, {:.0f} KB".format(
            out.name, len(data["records"]), len(data["units"]),
            out.stat().st_size / 1024))


if __name__ == "__main__":
    main()
