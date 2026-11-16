// Shared retrieval engine for the DPDP pages. Generated into each page by
// build_web.py, so a fix here reaches every page.
// Mirrors retrieve.py: same stopwords, same crude stemmer, same BM25.

// -- same tokeniser as retrieve.py, so scores match the Python pipeline ----
var STOP = new Set(("a an and any are as at be by can do does for from has have how i if in is it its " +
  "may must my need of on or our shall should such that the their them there these this to under was we " +
  "what when which who will with").split(" "));
var SUFFIXES = ["ations", "ation", "ingly", "ities", "ting", "ing", "ies", "ers", "ed", "es", "s"];

function stem(w) {
  for (var i = 0; i < SUFFIXES.length; i++) {
    var s = SUFFIXES[i];
    if (w.length > s.length + 3 && w.slice(-s.length) === s) return w.slice(0, -s.length);
  }
  return w;
}
function tokenize(text) {
  var out = [], m = String(text).toLowerCase().match(/[a-z0-9]+/g) || [];
  for (var i = 0; i < m.length; i++) {
    if (!STOP.has(m[i]) && m[i].length > 1) out.push(stem(m[i]));
  }
  return out;
}

// -- BM25, k1=1.5 b=0.75, matching retrieve.py -----------------------------
function Index(docs) {
  this.freqs = []; this.lengths = []; this.idf = {};
  var df = {}, total = 0, i, j;
  for (i = 0; i < docs.length; i++) {
    var toks = tokenize(docs[i]), tf = {};
    for (j = 0; j < toks.length; j++) tf[toks[j]] = (tf[toks[j]] || 0) + 1;
    this.freqs.push(tf); this.lengths.push(toks.length); total += toks.length;
    for (var t in tf) df[t] = (df[t] || 0) + 1;
  }
  this.avg = docs.length ? total / docs.length : 0;
  for (var term in df) {
    this.idf[term] = Math.log(1 + (docs.length - df[term] + 0.5) / (df[term] + 0.5));
  }
}
Index.prototype.search = function (query, k) {
  var q = tokenize(query), out = [], K1 = 1.5, B = 0.75;
  if (!q.length) return out;
  for (var i = 0; i < this.freqs.length; i++) {
    var score = 0;
    for (var j = 0; j < q.length; j++) {
      var f = this.freqs[i][q[j]];
      if (!f) continue;
      var denom = f + K1 * (1 - B + B * this.lengths[i] / (this.avg || 1));
      score += (this.idf[q[j]] || 0) * f * (K1 + 1) / denom;
    }
    if (score > 0) out.push({ i: i, score: score });
  }
  out.sort(function (a, b) { return b.score - a.score || a.i - b.i; });
  return out.slice(0, k);
};

// Record index: citation and title weighted, as in retrieve.Index._doc_text.
var recordIndex = new Index(RECORDS.map(function (r) {
  return [r.cite, r.cite, r.title, r.title, r.tags.join(" "), r.text].join(" ");
}));

function esc(s) {
  return String(s).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
  });
}
function crore(n) { return "₹" + (n / 1e7).toLocaleString("en-IN") + " cr"; }

function answerFor(question, k) {
  var top = recordIndex.search(question, k || 4);
  if (!top.length) return { clauses: [], records: [] };
  var keep = {}, recs = [];
  top.forEach(function (h) { keep[RECORDS[h.i].i] = true; recs.push(RECORDS[h.i]); });

  var pool = UNITS.filter(function (u) { return keep[u.r]; });
  if (!pool.length) return { clauses: [], records: recs };

  // Re-fit BM25 over just these sub-clauses: length normalisation stops a
  // long omnibus item outranking the short clause that answers the question.
  var unitIndex = new Index(pool.map(function (u) { return u.text; }));
  var picked = [], seen = {};
  unitIndex.search(question, 18).forEach(function (h) {
    var u = pool[h.i], key = u.text.slice(0, 60);
    if (seen[key] || picked.length >= 5) return;
    seen[key] = true; picked.push(u);
  });
  return { clauses: picked, records: recs };
}

// --- helpers for the browser chat port (web/chat.js) -----------------------
// Hit shape here is {score, rec}, so the port reads like chat.py's (score, rec).

function searchRecords(query, k) {
  return recordIndex.search(query, k).map(function (h) {
    return { score: h.score, rec: RECORDS[h.i] };
  });
}

function unitsOf(rec) {
  return UNITS.filter(function (u) { return u.r === rec.i; });
}

// Mirrors baseline.rank: re-fit BM25 over just these sub-clauses, so length
// normalisation stops a long omnibus item outranking the short clause.
function baselineRank(query, hits, limit) {
  var pool = [];
  hits.forEach(function (h) {
    unitsOf(h.rec).forEach(function (u) { pool.push(u); });
  });
  if (!pool.length) return [];
  var unitIndex = new Index(pool.map(function (u) { return u.text; }));
  var out = [], seen = {};
  unitIndex.search(query, limit * 3).forEach(function (h) {
    var u = pool[h.i], key = u.text.slice(0, 60);
    if (seen[key] || out.length >= limit) return;
    seen[key] = true;
    out.push({ score: h.score, cite: u.cite, text: u.text });
  });
  return out;
}
