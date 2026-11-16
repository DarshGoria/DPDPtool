// Browser port of chat.py. The tables below are generated from chat.py at
// build time (build_web.py), so the patterns cannot drift from the Python the
// tests exercise; only the control flow is hand-written here.
var CHAT = __CHAT_TABLES__;

function rx(pattern) { return new RegExp(pattern, "i"); }

var RE = {
  time: rx(CHAT.time), money: rx(CHAT.money), followup: rx(CHAT.followup),
  greeting: rx(CHAT.greeting), thanks: rx(CHAT.thanks), meta: rx(CHAT.meta),
  cmAsked: rx(CHAT.cm_asked), enforcement: rx(CHAT.enforcement_asked),
  definition: rx(CHAT.definition_asked), children: rx(CHAT.children_asked),
  retention: rx(CHAT.retention_asked), exemption: rx(CHAT.exemption_asked),
  who: rx(CHAT.who_boost), prohibited: rx(CHAT.prohibited), required: rx(CHAT.required),
  permissionAsked: rx(CHAT.permission_asked), permits: rx(CHAT.permits),
  // Clause hygiene, mirroring chat.py: Consent Manager clauses, the wh-word
  // test that stops a what-question being answered yes/no, and the Act's
  // worked examples.
  cmClause: rx(CHAT.cm_clause), notYesNo: rx(CHAT.not_yes_no),
  illustration: new RegExp(CHAT.illustration, "is"),
  withinLead: /^within\b/i,
  stripLead: /^(within|of|for a period of|for|at least|least)\s+/i
};
var SYN = CHAT.synonyms.map(function (p) { return [rx(p[0]), p[1]]; });
var INTENTS = CHAT.intents.map(function (p) { return [p[0], rx(p[1])]; });

function statutory(question) {
  var extra = [];
  for (var i = 0; i < SYN.length; i++) {
    if (SYN[i][0].test(question)) extra.push(SYN[i][1]);
  }
  return question + (extra.length ? " " + extra.join(" ") : "");
}

function lastQuestion(history) {
  for (var i = (history || []).length - 1; i >= 0; i--) {
    if (history[i].role === "user" && history[i].content) return history[i].content;
  }
  return "";
}

function expand(question, history) {
  var prev = lastQuestion(history);
  if (!prev) return statutory(question);
  if (tokenize(question).length <= 6 || RE.followup.test(question.trim())) {
    return statutory(prev + " " + question);
  }
  return statutory(question);
}

// Mirrors chat.chapter_weight: pulled out of narrow() so clause ranking can
// apply the same demotions. It could not before, so Rule 2(1)(c) - the
// definition of "user account" - ranked third for a company asking whether
// its terms-and-conditions consent was valid.
function chapterWeight(question, rec) {
  var id = rec ? rec.id : "";
  if (CHAT.about_cm.indexOf(id) >= 0 && !RE.cmAsked.test(question)) return 0.25;
  if (CHAT.machinery.indexOf(id) >= 0 && !RE.enforcement.test(question)) return 0.2;
  if (CHAT.definitions.indexOf(id) >= 0 && !RE.definition.test(question)) return 0.35;
  if (CHAT.children.indexOf(id) >= 0 && !RE.children.test(question)) return 0.3;
  if (CHAT.retention_table.indexOf(id) >= 0 && !RE.retention.test(question)) return 0.3;
  if (CHAT.exemptions.indexOf(id) >= 0 && !RE.exemption.test(question)) return 0.3;
  return 1;
}

function narrow(question, hits) {
  return hits.map(function (h) {
    return { score: h.score * chapterWeight(question, h.rec), rec: h.rec };
  }).sort(function (a, b) { return b.score - a.score; });
}

function intentOf(question) {
  // "so what exactly do i need to change" matched the whether rule on
  // "do i need" and was answered "Yes - this is required." See chat.intent_of.
  var notYesNo = RE.notYesNo.test(question);
  var q = question.trim().toLowerCase();
  for (var i = 0; i < INTENTS.length; i++) {
    if (INTENTS[i][0] === "whether" && notYesNo) continue;
    if (INTENTS[i][1].test(q)) return INTENTS[i][0];
  }
  return "general";
}

function boost(intent, text, rec) {
  if (intent === "duration" && RE.time.test(text)) return 1.8;
  if (intent === "penalty") {
    if (rec && rec.penalty) return 1.6;
    if (RE.money.test(text)) return 1.8;
  }
  if (intent === "who" && RE.who.test(text)) return 1.2;
  return 1;
}

function headline(intent, cite, text, question) {
  if (intent === "duration") {
    var m = RE.time.exec(text);
    if (m) {
      var phrase = m[0].split(/\s+/).join(" ").trim();
      var lead = RE.withinLead.test(phrase) ? "Within " : "";
      phrase = phrase.replace(RE.stripLead, "");
      var out = (lead + phrase).trim();
      return out.charAt(0).toUpperCase() + out.slice(1) + ". (" + cite + ")";
    }
  }
  if (intent === "whether") {
    if (RE.prohibited.test(text)) return "No - this is prohibited. (" + cite + ")";
    // Permission is not obligation: "can I store data in Singapore?" once
    // came back "Yes - this is required", confusing may with shall.
    if (RE.permissionAsked.test(question || "")) {
      if (RE.permits.test(text)) {
        return "Yes - permitted, subject to the conditions below. (" + cite + ")";
      }
      return null;
    }
    if (RE.required.test(text)) return "Yes - this is required. (" + cite + ")";
  }
  return null;
}

// Mirrors chat.without_illustration. The Act's worked examples are part of
// the statute but they are examples: Section 6(1)'s is 300 characters about a
// telemedicine app. They stay in the full provision, not in the quoted answer.
function withoutIllustration(text) {
  var stripped = String(text).replace(RE.illustration, "").trim();
  return stripped.length >= 60 ? stripped : text;
}

function trim(text, cap) {
  cap = cap || CHAT.trim_cap;
  text = withoutIllustration(text).split(/\s+/).join(" ");
  if (text.length <= cap) return text;
  return text.slice(0, cap).replace(/\s+\S*$/, "") + "...";
}

// Mirrors chat.rank: baseline clause ranking, re-scored for the intent, with
// a bonus for sitting inside the provision record retrieval ranked best.
function rankClauses(query, hits, intent, limit, question) {
  var byCite = {}, rankOf = {};
  hits.forEach(function (h, position) {
    unitsOf(h.rec).forEach(function (u) {
      byCite[u.cite] = h.rec; rankOf[u.cite] = position;
    });
  });
  var picked = baselineRank(query, hits, limit * 3).map(function (p) {
    var pos = rankOf[p.cite];
    var parent = (pos !== undefined && pos < CHAT.parent_bonus.length)
      ? CHAT.parent_bonus[pos] : 1;
    var rec = byCite[p.cite];
    // A provision we only reached by following a reference is background; it
    // should colour the answer, not headline it. See chat.rank.
    if (rec && rec.crossref) parent *= CHAT.crossref_penalty;
    if (question) {
      // The same chapter demotions narrow() applies to records. See chat.rank.
      parent *= chapterWeight(question, rec);
      if (RE.cmClause.test(p.text) && !RE.cmAsked.test(question)) {
        parent *= CHAT.cm_clause_weight;
      }
    }
    return { score: p.score * boost(intent, p.text, rec) * parent,
             cite: p.cite, text: p.text };
  });
  picked.sort(function (a, b) { return b.score - a.score; });
  return picked.slice(0, limit);
}

// Mirrors chat.with_children: a chapeau alone says a duty exists but not what
// it is, so its own lettered items follow it.
function withChildren(picked, hits, limit) {
  if (!picked.length) return picked;
  var all = {};
  hits.forEach(function (h) {
    unitsOf(h.rec).forEach(function (u) { all[u.cite] = u.text; });
  });
  var letters = "abcdefghijklmnop", children = [];
  for (var i = 0; i < letters.length; i++) {
    var key = picked[0].cite + "(" + letters[i] + ")";
    if (!(key in all)) break;
    children.push({ score: 0, cite: key, text: all[key] });
  }
  if (!children.length) return picked;
  // One seen-set, not two filters. Filtering children by "already picked" and
  // then picked by "is a child" dropped any clause that was both, losing
  // Rule 7(1)(b) when clause ranking had already found it. See chat.py.
  var merged = [picked[0]], seen = {};
  seen[picked[0].cite] = 1;
  children.concat(picked.slice(1)).forEach(function (c) {
    if (!seen[c.cite]) { seen[c.cite] = 1; merged.push(c); }
  });
  return merged.slice(0, limit || 8);
}

// Mirrors citecheck.citations / citecheck._root. Used here to find what a
// clause points at; the page itself cannot fabricate a citation, because it
// only ever quotes, but cross-references need the same parse.
var CITE_SECTION = new RegExp(CHAT.cite_section, "g");
var CITE_RULE = new RegExp(CHAT.cite_rule, "g");
var CITE_SCHEDULE = new RegExp(CHAT.cite_schedule, "gi");
var CITE_BARE = new RegExp(CHAT.cite_bare_schedule, "g");
var ORDINALS = ["First", "Second", "Third", "Fourth", "Fifth", "Sixth",
                "Seventh", "Eighth", "Ninth", "Tenth"];

function parens(tail) {
  var found = String(tail || "").match(/\(\s*[0-9a-zA-Z]{1,4}\s*\)/g) || [];
  return found.map(function (p) {
    return "(" + p.replace(/[()\s]/g, "") + ")";
  }).join("");
}

function citationsIn(text) {
  var found = [], m;
  [[CITE_SECTION, "Section"], [CITE_RULE, "Rule"]].forEach(function (pair) {
    pair[0].lastIndex = 0;
    while ((m = pair[0].exec(text)) !== null) {
      found.push([m.index, pair[1] + " " + m[1] + parens(m[2])]);
    }
  });
  CITE_SCHEDULE.lastIndex = 0;
  while ((m = CITE_SCHEDULE.exec(text)) !== null) {
    var ordinal = ORDINALS.filter(function (o) {
      return o.toLowerCase() === m[1].toLowerCase();
    })[0] || m[1];
    var tail = (m[2] || "").split(/\s+/).join(" ").trim()
      .replace(/Part\s+([a-d])/i, function (_x, g) { return "Part " + g.toUpperCase(); })
      .replace(/Item\s+/i, "item ");
    found.push([m.index, (ordinal + " Schedule " + tail).trim()]);
  }
  CITE_BARE.lastIndex = 0;
  while ((m = CITE_BARE.exec(text)) !== null) found.push([m.index, "The Schedule"]);

  found.sort(function (a, b) { return a[0] - b[0]; });
  var out = [], seen = {};
  found.forEach(function (f) {
    if (!seen[f[1]]) { seen[f[1]] = 1; out.push(f[1]); }
  });
  return out;
}

function citeRoot(cite) {
  var base = cite.split("(")[0].trim();
  var m = base.match(new RegExp("^(" + ORDINALS.join("|") + ")\\s+Schedule"));
  return m ? m[1] + " Schedule" : base;
}

// Mirrors chat.crossrefs: pull in provisions the retrieved clauses point at.
// Rule 6(1) requires safeguards for data processed "under sub-section (5) of
// section 8"; BM25 cannot find section 8 because the question's words are in
// the clause that refers to it, not the clause referred to.
var CROSSREF_SKIP = {};
CHAT.crossref_skip.forEach(function (id) { CROSSREF_SKIP[id] = 1; });

function crossrefs(hits) {
  if (!hits.length) return hits;
  var byCitation = {}, have = {}, floor = hits[0].score;
  RECORDS.forEach(function (r) { byCitation[r.cite] = r; });
  hits.forEach(function (h) {
    have[h.rec.id] = 1;
    if (h.score < floor) floor = h.score;
  });
  var added = [];
  for (var i = 0; i < Math.min(2, hits.length); i++) {
    var found = citationsIn(hits[i].rec.text);
    for (var j = 0; j < found.length; j++) {
      var target = byCitation[citeRoot(found[j])];
      if (!target || have[target.id] || CROSSREF_SKIP[target.id]) continue;
      have[target.id] = 1;
      // Object.assign keeps `i`, which unitsOf() filters units by.
      added.push({ score: floor * 0.5,
                   rec: Object.assign({}, target, { crossref: true }) });
      if (added.length >= CHAT.crossref_limit) return hits.concat(added);
    }
  }
  return hits.concat(added);
}

// Mirrors chat.edits_within / chat.nearest / chat.spell_fix. Plain
// Levenshtein rather than a ratio, because this has to agree exactly with
// the Python; two edits for a long word covers the transposition that edit
// distance counts twice ("encrpytion" -> "encryption").
function editsWithin(a, b, budget) {
  if (Math.abs(a.length - b.length) > budget) return null;
  var previous = [], current, i, j;
  for (j = 0; j <= b.length; j++) previous.push(j);
  for (i = 1; i <= a.length; i++) {
    current = [i];
    var lowest = i;
    for (j = 1; j <= b.length; j++) {
      var cost = a.charAt(i - 1) === b.charAt(j - 1) ? 0 : 1;
      var v = Math.min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost);
      current.push(v);
      if (v < lowest) lowest = v;
    }
    previous = current;
    if (lowest > budget) return null;
  }
  return previous[b.length] <= budget ? previous[b.length] : null;
}

var VOCAB = null;

function vocabulary() {
  if (VOCAB === null) VOCAB = Object.keys(recordIndex.idf).sort();
  return VOCAB;
}

function nearestWord(word) {
  var budget = word.length >= CHAT.edit_budget_long ? 2 : 1;
  var vocab = vocabulary(), best = null, bestDistance = budget + 1;
  for (var i = 0; i < vocab.length; i++) {
    var d = editsWithin(word, vocab[i], budget);
    if (d === null) continue;
    if (d < bestDistance || (d === bestDistance && vocab[i] < (best || "~"))) {
      best = vocab[i]; bestDistance = d;
    }
  }
  return best;
}

function spellFix(question) {
  var parts = question.match(/[A-Za-z0-9']+|[^A-Za-z0-9']+/g) || [];
  var out = [], changed = false;
  for (var i = 0; i < parts.length; i++) {
    var token = parts[i].toLowerCase(), root = stem(token);
    if (!token.trim() || root.length < CHAT.min_fix_len ||
        recordIndex.idf[root] !== undefined) {
      out.push(parts[i]);
      continue;
    }
    var near = nearestWord(root);
    // Same-family matches ("handle" vs the statute's "handling" -> "handl")
    // change nothing for retrieval and make the query look mangled.
    if (near && near.indexOf(root) !== 0 && root.indexOf(near) !== 0) {
      out.push(near); changed = true;
    } else {
      out.push(parts[i]);
    }
  }
  return changed ? out.join("") : question;
}

// Mirrors chat.search: try the question alone, fall back to the conversation
// only when that finds nothing convincing, and compare per word so a longer
// merged query does not win for being longer.
function searchWithContext(question, history, k) {
  var found = pickRecords(question, history, k);
  if (!found.hits.length) {
    // Only as a fallback, so a mis-correction can improve on nothing found
    // but never displace a real match. See chat.search.
    var fixed = spellFix(question);
    if (fixed !== question) found = pickRecords(fixed, history, k);
  }
  return { hits: crossrefs(found.hits), query: found.query };
}

function pickRecords(question, history, k) {
  var ownQuery = statutory(question);
  var alone = narrow(question, searchRecords(ownQuery, (k || 4) + 4)).slice(0, k || 4);
  // A question pointing back at the last turn must not take this short cut,
  // however well it scores alone. See chat._pick.
  if (alone.length && !RE.followup.test(question.trim()) &&
      alone[0].score / Math.max(2, tokenize(question).length) >= CHAT.confident_alone) {
    return { hits: alone, query: ownQuery };
  }
  var mergedQuery = expand(question, history);
  if (mergedQuery === ownQuery) return { hits: alone, query: ownQuery };
  var merged = narrow(question, searchRecords(mergedQuery, (k || 4) + 4)).slice(0, k || 4);
  if (!alone.length) return { hits: merged, query: mergedQuery };
  if (!merged.length) return { hits: alone, query: ownQuery };

  // Too short to have a subject of its own, and explicitly pointing back:
  // "what conditions apply to that?" is two generic content words. Comparing
  // per word cannot rescue it, because the merged query is longer and its
  // much better hit scores lower once divided. See chat._pick.
  if (tokenize(question).length <= CHAT.short_followup &&
      RE.followup.test(question.trim())) {
    return { hits: merged, query: mergedQuery };
  }
  var perWord = function (hits, text) {
    return hits[0].score / Math.max(2, tokenize(text).length);
  };
  if (perWord(merged, mergedQuery) > perWord(alone, question) * CHAT.context_margin) {
    return { hits: merged, query: mergedQuery };
  }
  return { hits: alone, query: ownQuery };
}

// Mirrors chat.other_regime: naming another regime is the one case where a
// confident answer is certainly wrong. "What are the GDPR fines in Europe?"
// returned the Indian Schedule's amounts.
var OTHER_REGIME = CHAT.other_regime.map(function (p) { return [rx(p[0]), p[1]]; });

function otherRegime(question) {
  for (var i = 0; i < OTHER_REGIME.length; i++) {
    if (OTHER_REGIME[i][0].test(question)) return OTHER_REGIME[i][1];
  }
  return null;
}

// Mirrors chat.in_scope. The score must be divided by the length of the query
// that produced it, not the question: dividing by the question let every
// synonym rule inflate the in-scope signal, because BM25 rises with query
// terms while the denominator never moved.
function grounding(question, rec) {
  var words = tokenize(question);
  if (!words.length) return [0, 0];
  var text = {};
  tokenize(rec.text + " " + (rec.title || "")).forEach(function (t) { text[t] = 1; });
  var unique = {}, matched = 0, total = 0;
  words.forEach(function (w) {
    if (unique[w]) return;
    unique[w] = 1; total += 1;
    if (text[w]) matched += 1;
  });
  return [matched, total ? matched / total : 0];
}

function inScope(question, hits, query) {
  if (!hits.length) return false;
  var terms = tokenize(query || question).length;
  if (hits[0].score / Math.max(2, terms) >= CHAT.in_scope_per_word) return true;
  // One matched word is enough when it is the statute's own term of art:
  // "how long do I have to resolve a grievance?" shares exactly "grievance"
  // with Rule 14. Requiring two refused it.
  var g = grounding(question, hits[0].rec);
  return g[0] >= 1 && g[1] >= CHAT.grounding_min;
}

function respond(question, hits, history, penalties, query) {
  var raw = question.trim();
  if (RE.meta.test(raw)) return CHAT.help;
  if (RE.greeting.test(raw) && tokenize(raw).length <= 2) return CHAT.hello;
  if (RE.thanks.test(raw) && tokenize(raw).length <= 3) return "Happy to help. Ask another.";

  var other = otherRegime(raw);
  if (other) {
    return "That is " + other + ", which I do not cover. I only know the " +
      "Digital Personal Data Protection Act 2023 and the DPDP Rules 2025.\n\n" +
      "If the question also has an Indian DPDP angle, ask it that way and I " +
      "will answer from the Act or the Rules.";
  }

  var intent = intentOf(raw);
  if (hits.length && !inScope(raw, hits, query)) return CHAT.out_of_scope;

  var picked = withChildren(
    rankClauses(query || expand(question, history), hits, intent, 4, raw), hits);
  if (!picked.length) return CHAT.no_match;

  var lines = [];
  var sure = hits.length > 0 && hits[0].score >= CHAT.confident;
  var head = sure ? headline(intent, picked[0].cite, picked[0].text, raw) : null;
  if (head) {
    lines.push(head, "");
  } else if (!sure) {
    lines.push("I am not confident I found the right provision for that - " +
               "these are the closest, judge them yourself:", "");
  }

  if (intent === "penalty" && penalties) {
    var rows = penalties.filter(function (p) { return p.max_inr; });
    if (rows.length) {
      lines.push("Maximum penalties under the Act's Schedule:");
      rows.slice(0, 4).forEach(function (p) {
        lines.push("  " + p.provision + " - " + p.max_words);
      });
      lines.push("");
    }
  }

  lines.push(head ? "From the statute:" : "Most relevant provisions:", "");
  var show = picked.slice(0, head ? 6 : 7);
  show.forEach(function (p) { lines.push(p.cite + " - " + trim(p.text), ""); });

  var extra = picked.slice(head ? 6 : 7).map(function (p) { return p.cite; });
  if (extra.length) lines.push("Also relevant: " + extra.join(", "));

  return lines.join("\n").trim();
}

// Test hook. The port re-implements chat.py's control flow by hand, so the
// only way to show the two agree is to run the same questions through both;
// without this the port is unverifiable and drifts on the next change.
// test_parity.py drives it. Harmless in production: it exposes no state.
if (typeof window !== "undefined") {
  window.DPDP_PORT = {
    search: searchWithContext,
    respond: respond,
    spellFix: spellFix,
    otherRegime: otherRegime,
    inScope: inScope,
    citationsIn: citationsIn,
    // A function, not a value: PENALTIES is assigned by the host page after
    // this file is spliced in, so reading it here would capture undefined.
    penalties: function () { return PENALTIES; }
  };
}
