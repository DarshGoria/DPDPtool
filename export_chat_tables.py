"""Export chat.py's patterns and strings for the browser port.

The JS in web/chat.js re-implements the control flow by hand, but every
pattern, id set and canned string comes from here, so the two cannot drift
apart silently. Python and JS regex syntax agree on everything used.
"""
import json

import chat
import citecheck


def tables():
    return {
        "synonyms": [[p, t] for p, t in chat.SYNONYMS],
        "intents": [[n, p] for n, p in chat.INTENTS],
        "time": chat.TIME.pattern,
        "money": chat.MONEY.pattern,
        "followup": chat.FOLLOWUP.pattern,
        "greeting": "^(?:" + chat.GREETING.pattern.lstrip("^") + ")",
        "thanks": "^(?:" + chat.THANKS.pattern.lstrip("^") + ")",
        "meta": chat.META.pattern,
        "cm_asked": chat.CM_ASKED.pattern,
        "enforcement_asked": chat.ENFORCEMENT_ASKED.pattern,
        "definition_asked": chat.DEFINITION_ASKED.pattern,
        "children_asked": chat.CHILDREN_ASKED.pattern,
        "retention_asked": chat.RETENTION_ASKED.pattern,
        "exemption_asked": chat.EXEMPTION_ASKED.pattern,
        "who_boost": chat.WHO_BOOST.pattern,
        "prohibited": chat.PROHIBITED.pattern,
        "permission_asked": chat.PERMISSION_ASKED.pattern,
        "permits": chat.PERMITS.pattern,
        "required": chat.REQUIRED.pattern,
        "about_cm": ["rules-r4", "rules-sch1"],
        "machinery": sorted(chat.MACHINERY),
        "definitions": sorted(chat.DEFINITIONS),
        "children": sorted(chat.CHILDREN),
        "retention_table": sorted(chat.RETENTION_TABLE),
        "exemptions": sorted(chat.EXEMPTIONS),
        "help": chat.HELP,
        "hello": chat.HELLO,
        "out_of_scope": chat.OUT_OF_SCOPE,
        "no_match": chat.NO_MATCH,
        "confident": chat.CONFIDENT,
        "in_scope_per_word": chat.IN_SCOPE_PER_WORD,
        "parent_bonus": chat.PARENT_BONUS,
        "confident_alone": chat.CONFIDENT_ALONE,
        "context_margin": chat.CONTEXT_MARGIN,
        "short_followup": chat.SHORT_FOLLOWUP,
        # Clause-level hygiene, added after a reviewer transcript showed
        # definitions, Consent Manager registration and a worked example
        # about telemedicine in the answer to a consent question.
        "cm_clause": chat.CM_CLAUSE.pattern,
        "cm_clause_weight": chat.CM_CLAUSE_WEIGHT,
        "not_yes_no": chat.NOT_YES_NO.pattern,
        "illustration": chat.ILLUSTRATION.pattern,
        "trim_cap": 900,
        # Scope gate and cross-references, added with those features so the
        # browser port cannot keep the old thresholds.
        "other_regime": [[p, n] for p, n in chat.OTHER_REGIME],
        "grounding_min": chat.GROUNDING_MIN,
        "crossref_penalty": chat.CROSSREF_PENALTY,
        "crossref_limit": chat.CROSSREF_LIMIT,
        "crossref_skip": sorted(chat.CROSSREF_SKIP),
        "min_fix_len": chat.MIN_FIX_LEN,
        "edit_budget_long": chat.EDIT_BUDGET_LONG,
        "cite_section": citecheck.SECTION.pattern,
        "cite_rule": citecheck.RULE.pattern,
        "cite_schedule": citecheck.SCHEDULE.pattern,
        "cite_bare_schedule": citecheck.BARE_SCHEDULE.pattern,
        "cite_external": sorted(citecheck.EXTERNAL),
    }


if __name__ == "__main__":
    print(json.dumps(tables(), indent=2)[:400])
