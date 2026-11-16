"""A conversational layer over the clause ranker. No model, no network.

It will not discuss, speculate or draft - that needs a language model. What it
does do is work out what kind of answer a question wants, pick the clause that
carries it, and lead with the fact rather than the paragraph. Within DPDP it
beats a small model on the thing that matters most: it cannot invent a
citation, because every word it says is either a template or quoted statute.
"""
import re

import baseline
import citecheck
from retrieve import stem, tokenize

# --- what kind of answer the question wants ---------------------------------
# Order matters: the first match wins, so the specific patterns come first.
INTENTS = [
    ("penalty", r"\bpenalt|\bfine[sd]?\b|how much.*(fine|penalt)|what happens if|"
                r"consequence|liable|exposure"),
    ("duration", r"how long|how soon|how fast|how quickly|how many (days|hours|years|months)|"
                 r"\bdeadline|\bwhen (must|do|should|is|does)|time ?(period|limit)|"
                 r"how often|by when"),
    ("whether", r"^(do|does|can|may|must|am|are|is|should|will)\b|\bdo i (have|need)|"
                r"\bmust i\b|\bam i required\b|\bis it (mandatory|required|allowed|ok)"),
    ("who", r"^who\b|\bwho (is|must|has to|should|can)\b"),
    ("definition", r"what (is|are|does) (a |an |the )?[\w ]+ mean|^what is a\b|"
                   r"\bdefine\b|\bdefinition\b|\bmeaning of\b"),
    ("list", r"^(what|which)\b|\blist\b|\bwhat are\b"),
]

# Statutory durations are written as words, so digits alone would miss them.
NUMWORD = (r"(?:twenty-four|forty-eight|seventy-two|twenty|thirty|forty|fifty|sixty|"
           r"ninety|one|two|three|four|five|six|seven|eight|nine|ten|twelve|"
           r"fifteen|eighteen|\d{1,4})")
TIME = re.compile(
    r"(?:within|for a period of|for|of|least)?\s*" + NUMWORD +
    r"\s+(?:hours?|days?|weeks?|months?|years?)", re.I)
MONEY = re.compile(r"(?:\d[\d,]*\s*)?(?:crore|lakh|thousand)\s+rupees|rupees[\w\s]{0,20}", re.I)

FOLLOWUP = re.compile(
    r"^(and|but|ok|okay|so|then|also|what about|how about|and what|what if)\b"
    r"|\b(it|that|this|those|them|they|same|there)\b", re.I)

# Everyday words mapped to the words the statute actually uses. Without this,
# lexical retrieval matches on the wrong sense: "16 year old" once hit
# "one year after the date of publication" and answered from the commencement
# rule, with a citation, confidently.
SYNONYMS = [
    (r"\b\d{1,2}[\s-]?year[\s-]?olds?\b|\bminors?\b|\bkids?\b|\bteen(ager)?s?\b"
     r"|\bunder ?-?18\b|\bunder the age of 18\b",
     "child children parent verifiable consent"),
    (r"\bparental\b|\bparents?\b|\bguardian\b", "parent verifiable consent child"),
    (r"\bdelete\b|\bdeletion\b|\bremove\b|\bwipe\b|\bpurge\b", "erase erasure"),
    (r"\bhack(ed|ing)?\b|\bleak(ed|age)?\b|\bincident\b|\bcompromis(e|ed)\b"
     # "spill" and "exfiltrate" are ordinary words for this and were missing.
     r"|\bspill(ed|age)?\b|\bexfiltrat\w+\b|\bransomware\b|\bstolen\b"
     r"|\bdata loss\b|\bexposed\b|\bbreach\w*\b",
     "personal data breach intimate the Board affected Data Principal"),
    # The Board is what people call the watchdog or the regulator.
    (r"\bwatchdog\b|\bregulator\b|\bauthorit(y|ies)\b|\bombudsman\b"
     r"|\bgovernment agency\b",
     "Data Protection Board of India"),
    (r"\bopt[\s-]?out\b|\bunsubscribe\b|\brevoke\b", "withdraw consent"),
    (r"\bvendors?\b|\bsub-?processors?\b|\bsuppliers?\b|\bthird[\s-]part(y|ies)\b"
     r"|\bcloud provider\b", "Data Processor contract"),
    (r"\boverseas\b|\babroad\b|\boffshore\b|\bforeign\b|\bus-?east\b|\bsingapore\b",
     "outside the territory of India transfer"),
    (r"\bdpo\b|\bdata protection officer\b|\bgrievance officer\b",
     "Data Protection Officer contact information grievance"),
    # "complain" without the t: "do we have to give customers somewhere to
    # complain?" matched nothing and returned the consent provisions.
    (r"\bcomplain(t|ts|ing|ed|s)?\b|\bescalat(e|ion)\b|\bgripe\b",
     "grievance redressal"),
    (r"\bencrypt(ion|ed)?\b|\bfirewall\b|\bmfa\b|\baccess control\b|\bsecurity\b",
     "reasonable security safeguards"),
    # "how much" alone is not a penalty question: "how much can they repay"
    # once dragged the penalty Schedule into a question about consent.
    (r"\bfines?\b|\bpenalt(y|ies)\b|how much.{0,25}(fine|penalt|liable|exposure)"
     # "what does it cost us if we get the children's rules wrong?" is a
     # penalty question that never says fine or penalty.
     r"|\bcost (us|me|you|the company)\b|\bwhat does it cost\b|\bexposure\b"
     r"|\bliab(le|ility)\b|\bon the hook for\b|\bhow bad is it if\b",
     "monetary penalty crore rupees Schedule"),
    (r"\bcookies?\b|\btrackers?\b|\banalytics\b", "consent notice purpose"),
    (r"\bdsar\b|\bsubject access\b|\baccess request\b", "right to access information"),
    # Reading a device's messages, mail or contacts is collection of personal
    # data, and nothing in the statute says "SMS".
    (r"\bsms\b|\bmessages?\b|\binbox\b|\bcontacts?\b|\bcall logs?\b|"
     r"\bgallery\b|\bphotos?\b|\bmicrophone\b|\bcamera\b|\blocation\b",
     "personal data collected notice specified purpose consent necessary"),
    # Profiling and personalisation are processing for a purpose, and a
    # decision that affects the data principal.
    (r"\bprofil(e|ing)\b|\bpersonalis|\bpersonaliz|\brecommend|\bprefill|"
     r"\bpre-?fill|\branking\b|\brank\b|\btarget(ed|ing)?\b|\bsegment|"
     r"\bscore|\bscoring\b|\bcreditworth|\beligib|\bunderwrit|\bshow products\b",
     "processing specified purpose decision that affects the Data Principal "
     "completeness accuracy consistency consent limited necessary"),
    (r"\bcredited?\b|\bbank\b|\bsalary\b|\btransaction|\bstatement|"
     r"\bfinancial\b|\bincome\b", "personal data processing specified purpose"),
    # How consent is actually collected in an app. Without this, "they click
    # allow" retrieved the children's rules and the model answered about
    # verifying a parent's age.
    (r"\ballow button\b|\ballow\b|\bpermission (pop-?up|dialog|prompt|screen)?\b|"
     r"\bcheck ?box\b|\btick\b|\baccept\b|\bagree\b|\bopt[\s-]?in\b|"
     r"\bterms and conditions\b|\bt&c\b|\bsign ?up\b|\bonboarding\b",
     "consent free specific informed unconditional unambiguous clear "
     "affirmative action notice request for consent"),
    # "what makes consent valid" reached section 8 instead of section 6.
    (r"\b(valid|lawful|proper|genuine|real)\b.{0,20}\bconsent\b|"
     r"\bconsent\b.{0,20}\b(valid|lawful|requirements?|rules?)\b",
     "consent free specific informed unconditional unambiguous clear "
     "affirmative action specified purpose necessary withdraw"),
    # "what rights does a data principal have" missed the access right.
    (r"\bwhat rights\b|\brights?\b.{0,25}data principal|"
     r"\bdata principal\b.{0,25}\brights?\b|\bmy rights\b",
     # Not "grievance": it dragged in section 15(d), a DUTY not to file
     # frivolous grievances, for a question about rights.
     "right to obtain access information summary correction erasure nominate"),
    (r"\baudit\b|\bdpia\b|\bimpact assessment\b",
     "Data Protection Impact Assessment Significant Data Fiduciary"),
    # Training a model on data collected for something else is a purpose
    # question, and the statute has no word for machine learning. This went
    # to the retention and breach rules before.
    (r"\btrain(ing|ed)?\b.{0,15}\b(model|ai|algorithm|system|data)\b"
     r"|\bmachine learning\b|\b(ml|ai) model\b|\bllm\b|\bneural\b"
     r"|\balgorithm\w*\b|\bdata ?set\b|\btrain on\b|\bfine[\s-]?tun\w+\b",
     "processed for the specified purpose for which consent has been given "
     "lawful purpose limited to such personal data as is necessary"),
    # Reusing data already held for a new purpose - the single most common
    # real compliance question and the one with no statutory vocabulary.
    (r"\bnew purpose\b|\bdifferent purpose\b|\bre-?use\b|\bre-?purpos\w+\b"
     r"|\bsecondary use\b|\balready (have|collected|hold)\b|\bexisting (data|customers?)\b"
     r"|\blast year'?s?\b|\bold (data|records?)\b|\bhistoric(al)? data\b",
     "specified purpose for which consent has been given notice consent "
     "limited to such personal data as is necessary lawful purpose"),

    # ----------------------------------------------------------------------
    # Added from the 22 misses eval_paraphrase.py found on its first run. Each
    # rule is a phrasing that a founder used and the statute does not, where
    # retrieval returned an unrelated provision rather than nothing.
    # ----------------------------------------------------------------------

    # Scope. "does this law cover us" shares no word with "shall apply to the
    # processing of digital personal data within the territory of India".
    (r"\bcover(s|ed)?\s+(us|we|me|my|our|a company|companies)\b|\bare we caught\b"
     r"|\b(caught|covered) by\b|\bappl(y|ies|icable)\s+to\s+(us|me|our|a|my)\b"
     r"|\bdoes (this|the)\s+(law|act|rules?)\b|\bin scope\b|\binside the law\b"
     r"|\bsubject to (this|the) (law|act|rules?)\b",
     "apply to the processing of digital personal data within the territory "
     "of India outside offering goods or services"),
    # Commencement. Nothing in Rule 1 says "start applying" or "ready".
    (r"\bcome into force\b|\bin force\b|\bcommence\b|\bstarts? apply\w*\b"
     r"|\bwhen does (this|the) (law|act|rules?)\b|\beffective date\b"
     r"|\bby when (must|do|should) we\b|\bready for\b|\bgrace period\b"
     r"|\btransition(al)? period\b|\bstaged\b|\bphased\b|\bdeadline to comply\b",
     "come into force on the date of their publication one year eighteen "
     "months commencement"),
    # Grounds for processing. "a reason to use" vs "for a lawful purpose".
    (r"\b(legal|lawful) basis\b|\ba reason to (use|process|collect)\b"
     r"|\bon what (basis|footing|grounds?)\b|\ballowed to (touch|use|process)\b"
     r"|\bentitled to (use|process)\b|\bgrounds? for processing\b",
     "process the personal data of a Data Principal only for a lawful "
     "purpose consent certain legitimate uses"),
    # Accuracy duty, section 8(3). "kept right", "make sure correct".
    # The window has to be wide enough for a real sentence: "make sure what we
    # hold about someone is correct" puts 30 characters between the two, and
    # a 20-character window meant this rule never fired at all.
    (r"\b(accurate|accuracy|inaccurate)\b|\bdata quality\b|\bout of date\b"
     r"|\bmake sure\b.{0,45}\b(correct|right|accurate|complete)\b"
     r"|\bkeep\w*\b.{0,30}\b(correct|right|accurate|up to date)\b"
     r"|\bstale (details|records|information)\b",
     "completeness accuracy and consistency of personal data reasonable "
     "effort to ensure duty of the Data Fiduciary"),
    # Processors, in words that are not "vendor" or "processor".
    (r"\boutside (company|firm|agency)\b|\bcrunch the data\b|\bsubcontract\w*\b"
     r"|\banother (firm|company|business) (to|handles?)\b|\bon our behalf\b"
     r"|\boutsourc\w+\b|\bbpo\b|\bservice provider\b",
     "Data Processor valid contract engage any Data Processor"),
    # Encryption, Rule 6(1)(a). "scramble", "lock the files".
    (r"\bscrambl\w+\b|\block (the )?(files?|data|records?)\b|\bunreadable\b"
     r"|\bat rest\b|\bin transit\b|\bhash\w*\b|\btokenis\w+\b|\btokeniz\w+\b"
     r"|\bmask\w*\b|\bciphe\w+\b",
     "encryption obfuscation masking the use of virtual tokens reasonable "
     "security safeguards"),
    # Logs, Rule 6(1)(b)-(d). "record of who looked at what".
    (r"\blogs?\b|\baudit trail\b|\brecord of (who|every|all)\b|\btrail\b"
     r"|\bwho (looked|accessed|viewed|saw)\b|\bevery (peek|access|view)\b"
     r"|\bmonitor\w*\b|\bdetect\w*\b",
     "logs monitoring and review of access detect unauthorised access "
     "retained for a period of one year"),
    # Erasure trigger, section 8(7) and Rule 8. "throw away", "in the bin".
    (r"\bthrow (it |them |data )?(away|out)\b|\bin the bin\b|\bbin it\b"
     r"|\bget rid of\b|\bdispose\b|\bstale\b|\bdormant\b|\binactive\b"
     r"|\bnot logged in\b|\bno longer (needed|required|using)\b",
     "erase personal data retention period specified purpose no longer "
     "being served Third Schedule"),
    # Children, in the words people use. The existing rule missed word
    # numbers ("fifteen year old") and plurals ("under eighteens").
    (r"\b(ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen)"
     r"[\s-]?year[\s-]?olds?\b|\bunder[\s-]?eighteens?\b|\bunder[\s-]?18s\b"
     r"|\bschool ?child(ren)?\b|\bpupils?\b|\bstudents?\b|\bunder ?age\b"
     r"|\badolescen\w+\b",
     "child children parent verifiable consent age"),
    # Rule 12 / Fourth Schedule: the children's rules relax for health and
    # education providers, who never call themselves that.
    (r"\bhospital\b|\bclinic\b|\bhealth ?care\b|\bdoctor\b|\bmedical\b"
     r"|\bschool\b|\bcollege\b|\beducational?\b|\bcreche\b|\bcrèche\b"
     r"|\bday ?care\b|\bchild ?care\b",
     "health care professional educational institution creche child day care "
     "centre exempted Fourth Schedule"),
    # Significant Data Fiduciary, in words nobody in business uses.
    (r"\bbig (operator|player|company|fiduciary)\b|\bheavyweight\b"
     r"|\blarge[\s-]?scale\b|\bclassified as\b|\blabell?ed\b|\bdesignated\b"
     r"|\bsignificant\b|\bsdf\b|\bhigh volume\b",
     "Significant Data Fiduciary notified by the Central Government "
     "additional obligations Data Protection Impact Assessment audit"),
    # Right to access, section 11. "what information we hold on them".
    (r"\bwhat (information|data|details) (we|you|they) (hold|have|keep)\b"
     r"|\bhold on (them|him|her|me)\b|\breadout\b|\bon file about\b"
     r"|\bcopy of (my|their|the) (data|information|records?)\b"
     r"|\bsubject access\b|\bwhat do (we|you) have (on|about)\b",
     "right to obtain a summary of personal data being processed and the "
     "processing activities undertaken identities of all other Data Fiduciaries"),
    # Right to correction, section 12. "address is wrong", "amend".
    (r"\b(wrong|incorrect|misspel\w+|typo)\b|\bfix(ed|ing)? (it|the|their|my)\b"
     r"|\bamend\w*\b|\bupdate (my|their|the) (details?|address|name|record)\b"
     r"|\bchange (my|their) (details?|address|name)\b|\brectif\w+\b",
     "right to correction completion updating and erasure of personal data"),
    # Nomination, section 14. "name somebody to act for them if they die".
    (r"\bnominat\w+\b|\bnominee\b|\bname (somebody|someone|a person)\b"
     r"|\bif (they|he|she|i) die\w*\b|\bon (my|their) behalf\b|\bstand[\s-]?in\b"
     r"|\bnext of kin\b|\bincapacit\w+\b|\bdeath\b",
     "right to nominate any other individual to exercise the rights in the "
     "event of death or incapacity"),
    # Transfer, section 16 and Rule 15. Existing rule had abroad/overseas but
    # not the places people actually name.
    # Places, because "can we store data in California?" is a section 16 and
    # Rule 15 question and nothing in either names a country. Not exhaustive -
    # world geography cannot be enumerated - so the generic triggers
    # (region, availability zone, data centre) carry the rest. Indian cities
    # are deliberately absent: hosting in Mumbai is not a transfer.
    (r"\bunited states\b|\bus[\s-]?(servers?|region|east|west)\b|\busa\b"
     r"|\bireland\b|\bfrankfurt\b|\beu\b|\banother country\b|\bcross[\s-]?border\b"
     r"|\bdata ?cent(re|er)\b|\bmachine abroad\b|\bexport the data\b"
     r"|\bcalifornia\b|\bvirginia\b|\boregon\b|\blondon\b|\bdublin\b|\btokyo\b"
     r"|\bsydney\b|\bnetherlands\b|\bgermany\b|\bcanada\b|\bjapan\b|\bbahrain\b"
     r"|\bavailability zone\b|\bregion\b|\bother jurisdiction\b",
     "transfer of personal data outside the territory of India restriction "
     "Central Government"),
    # Section 17 exemptions. "police", "state agencies let off".
    (r"\bpolice\b|\blaw enforcement\b|\bstate agenc\w+\b|\bgovernment (body|bodies|department)\b"
     r"|\bexempt\w*\b|\blet off\b|\bcarve[\s-]?out\b|\bdoes not apply to\b"
     r"|\bsecurity of the state\b|\bcourt\b|\btribunal\b",
     "shall not apply instrumentality of the State notified exempt "
     "prevention detection investigation prosecution"),
    # Appeal against a penalty.
    (r"\bappeal\w*\b|\bchallenge the (fine|penalty|order)\b|\btdsat\b"
     r"|\bappellate\b|\bjudicial review\b",
     "appeal to the Appellate Tribunal order of the Board"),
    # Bundled or pre-ticked consent, section 6(1).
    (r"\bbundl\w+\b|\ball at once\b|\bone click\b|\bpre[\s-]?tick\w*\b"
     r"|\bpre[\s-]?check\w*\b|\bdefault(ed)? on\b|\bblanket\b|\bcatch[\s-]?all\b"
     r"|\bten different things\b",
     "consent free specific informed unconditional unambiguous limited to "
     "such personal data as is necessary for the specified purpose"),
    # Languages, section 5(3) and Rule 3.
    (r"\blanguages?\b|\btongue\b|\bhindi\b|\bregional\b|\btranslat\w+\b"
     r"|\bvernacular\b|\benglish\b",
     "in English or any language specified in the Eighth Schedule to the "
     "Constitution notice request for consent"),
    # What has to be disclosed up front, section 5 and Rule 3. "do we have to
    # tell people how to complain when we sign them up" is a notice-content
    # question, but "complain" pulled it to the grievance provisions instead.
    # The leading lookahead matters: "what must I tell users after a data
    # breach?" is Rule 7, not section 5, and without the guard this rule
    # pulled the notice provisions above the breach rule.
    (r"^(?!.*\b(breach|leak|hack|incident|compromis)\w*\b).*"
     r"(\btell (people|users?|customers?|them|him|her|the user)\b"
     r"|\binform (people|users?|customers?|them)\b|\blet (them|users?) know\b"
     r"|\bdisclose to\b|\bup ?front\b|\bbefore (they|someone) (sign|agree|tap)\w*\b"
     r"|\bon the (screen|page|form)\b)",
     "notice given to the Data Principal shall inform itemised description "
     "of the personal data specified purpose manner in which she may make a "
     "complaint to the Board request for consent"),
    # Publishing a contact, Rule 9. "somewhere to complain", "who to ask".
    (r"\bsomewhere to complain\b|\bwho (do|should) (we|i) (tell|ask|contact)\b"
     r"|\bpoint of contact\b|\bcontact (details?|person|us)\b|\bhelpdesk\b"
     r"|\bsupport (email|channel)\b",
     "business contact information of the Data Protection Officer person "
     "able to answer questions prominently on its website grievance"),
]


def statutory(question):
    """Append the statute's own vocabulary for the everyday words used."""
    extra = []
    for pattern, terms in SYNONYMS:
        if re.search(pattern, question, re.I):
            extra.append(terms)
    return question + (" " + " ".join(extra) if extra else "")

GREETING = re.compile(r"^(hi|hey|hello|yo|sup|namaste|good (morning|evening|afternoon))\b", re.I)
THANKS = re.compile(r"^(thanks|thank you|thx|ty|great|nice|cool|perfect)\b", re.I)
META = re.compile(r"what can you (do|answer|help)|who are you|what are you|help\b|"
                  r"what do you know", re.I)

HELP = (
    "I answer from the DPDP Act 2023 and the DPDP Rules 2025, and nothing else.\n\n"
    "Ask me things like:\n"
    "  - how fast must I report a breach to the Board?\n"
    "  - how long must I retain logs?\n"
    "  - do I need parental consent for a 16 year old?\n"
    "  - what is the penalty for a security failure?\n"
    "  - what must a notice say?\n\n"
    "Follow-ups work: ask \"and what about the data principal?\" and I will keep "
    "the topic. Every answer quotes the clause it rests on.\n\n"
    "What I cannot do: reason about your specific situation, weigh DPDP against "
    "RBI or CERT-In, or draft a policy. That needs a language model behind me."
)


def last_question(history):
    for msg in reversed(history or []):
        if msg.get("role") == "user" and msg.get("content"):
            return msg["content"]
    return ""


def expand(question, history):
    """Fold the previous question in when this one cannot stand alone.

    "and what about erasing it?" retrieves nothing useful by itself; carrying
    the previous turn's words is what makes a follow-up work without a model.
    """
    prev = last_question(history)
    if not prev:
        return statutory(question)
    content = [t for t in tokenize(question)]
    if len(content) <= 6 or FOLLOWUP.search(question.strip()):
        return statutory(prev + " " + question)
    return statutory(question)


CM_ASKED = re.compile(r"consent manager|become a consent|register as", re.I)

# Chapters about the Board, appeals, penalties and miscellany. They answer
# "what happens when it goes wrong", never "is what I do allowed". Business
# words drag them in hard: "credited amount" matches section 34, "Crediting
# sums realised by way of penalties".
MACHINERY = ({"act-s" + str(n) for n in range(18, 45)} | {"rules-r" + str(n) for n in range(17, 24)})
ENFORCEMENT_ASKED = re.compile(
    r"\bpenalt|\bfine[sd]?\b|\bappeal|\btribunal\b|\binquir|\bprosecut|"
    r"\bcomplain|\bboard\b|\bliabl|\bexposure\b|what happens if", re.I)
# Definitions answer "what does X mean", not "is this allowed".
DEFINITIONS = {"act-s2", "rules-r2"}
DEFINITION_ASKED = re.compile(r"\bdefin|\bmean(s|ing)?\b|\bwhat is a\b|\bwho is a\b", re.I)

# Children's and guardianship provisions only bite when children are in play.
# They are dense with the word "consent", so they otherwise surface for any
# consent question - "they click allow" once drew an answer about verifying
# a parent's age.
# Retention-period table and the exemption regimes. Both are full of ordinary
# commercial words ("e-commerce", "products", "services") and surface for any
# business description, while answering a question nobody asked.
RETENTION_TABLE = {"rules-sch3"}
RETENTION_ASKED = re.compile(
    r"\bretain|\bretention|\berase|\berasure|\bdelete|\bhow long|\bkeep\b|"
    r"\bstorage\b|\bstore\b|\bdormant\b|\binactive\b", re.I)
EXEMPTIONS = {"act-s17", "rules-r16", "rules-sch2"}
EXEMPTION_ASKED = re.compile(
    r"\bexempt|\bexclud|\bdoes not apply|\bresearch\b|\barchiv|\bstatistic|"
    r"\bstartup\b|\bstate\b|\bgovernment\b|\bsubsidy\b|\bcarve[\s-]?out\b", re.I)

CHILDREN = {"act-s9", "rules-r10", "rules-r11", "rules-r12", "rules-sch4"}
CHILDREN_ASKED = re.compile(
    r"\bchild|\bkid|\bminor|\bparent|\bguardian|\bage\b|\bunder ?-?18\b|"
    r"\byear[\s-]?old\b|\bteen|\bschool\b|\bdisabilit|\bdisabled\b", re.I)


CROSSREF_LIMIT = 2
# Section 2 and the enforcement machinery are referenced all over the statute
# ("as defined in clause (x) of section 2", "proceedings under section 28").
# Following those would bury every answer in definitions and Board procedure,
# which narrow() has just finished demoting on purpose.
CROSSREF_SKIP = MACHINERY | DEFINITIONS


def crossrefs(index, hits, limit=CROSSREF_LIMIT):
    """Pull in provisions the retrieved clauses point at.

    Rule 6(1) requires safeguards "in respect of personal data ... under
    sub-section (5) of section 8"; section 8(5) is where the obligation
    itself lives. BM25 cannot find it, because the question's words are in
    the clause that refers to it, not in the clause referred to. So the top
    hits' own citations are resolved and appended.

    They are appended, not merged, and marked so clause ranking demotes them:
    a referenced provision is context for the answer, rarely the answer.
    """
    if not hits:
        return hits
    by_citation = {r["citation"]: r for r in index.records}
    have = {r["id"] for _s, r in hits}
    floor = min(s for s, _r in hits)

    added = []
    for _score, record in hits[:2]:
        for cite in citecheck.citations(record["text"]):
            root = citecheck._root(cite)
            target = by_citation.get(root)
            if target is None or target["id"] in have or target["id"] in CROSSREF_SKIP:
                continue
            have.add(target["id"])
            added.append((floor * 0.5, dict(target, crossref=True)))
            if len(added) >= limit:
                return list(hits) + added
    return list(hits) + added


def _pick(index, question, history, k=4, pool=8):
    """Retrieve for `question`, using the conversation only when it needs it.

    Merging the previous turn into every short question made a new subject
    inherit the old one's provisions: "i use users data to make them login"
    kept answering from the SMS question before it, because it is five words
    long and contains "them". So the standalone question is tried first, and
    the merged one only if that found nothing convincing.

    Returns (hits, query). The query comes back because clause ranking must
    use the same one: ranking with the merged query after choosing the
    standalone hits reintroduced the very context this rejected.
    """
    own_query = statutory(question)
    alone = narrow(question, index.search(own_query, k=pool))[:k]
    # A question that points back at the last turn - "what conditions apply to
    # that?" - must not take this short cut, however well it scores on its
    # own. That one scored 2.47 a word against a 2.2 bar on "conditions
    # apply", returned the Sixth Schedule, and lost Rule 15, which the merged
    # query found at three times the score. Falling through is safe: the
    # merged query still has to beat the standalone one by CONTEXT_MARGIN.
    if (alone and not FOLLOWUP.search(question.strip())
            and alone[0][0] / max(2, len(tokenize(question))) >= CONFIDENT_ALONE):
        return alone, own_query

    merged_query = expand(question, history)
    if merged_query == own_query:
        return alone, own_query
    merged = narrow(question, index.search(merged_query, k=pool))[:k]
    if not alone:
        return merged, merged_query
    if not merged:
        return alone, own_query

    # A question this short that also points back at the last turn has no
    # subject of its own to retrieve on. "what conditions apply to that?" is
    # two generic content words, which matched the Sixth Schedule at 2.47 a
    # word and buried Rule 15 - the provision the user was plainly asking
    # about. Comparing per word cannot rescue it, because the merged query is
    # seven words long and its much better hit scores lower once divided.
    # Five content words ("i use users data to make them login") is a new
    # subject and still goes through the comparison below.
    if len(tokenize(question)) <= SHORT_FOLLOWUP and FOLLOWUP.search(question.strip()):
        return merged, merged_query

    # Per word, or the merged query wins purely for being longer. The question
    # actually asked also gets the benefit of the doubt, so context has to be
    # clearly better before it overrides a new subject.
    def per_word(hits, text):
        return hits[0][0] / max(2, len(tokenize(text)))

    if per_word(merged, merged_query) > per_word(alone, question) * CONTEXT_MARGIN:
        return merged, merged_query
    return alone, own_query


def vocabulary(index):
    """Every word the statute uses, as retrieval sees it.

    Cached on the index itself rather than in a dict keyed by id(): that
    both leaks every index ever built and can hand back another index's
    vocabulary, because CPython reuses addresses after a collection.
    """
    cached = getattr(index, "_vocabulary", None)
    if cached is None:
        cached = sorted(index.idf)
        index._vocabulary = cached
    return cached


MIN_FIX_LEN = 5       # shorter words have too many near neighbours
EDIT_BUDGET_LONG = 8  # from here on, two edits are allowed instead of one


def edits_within(a, b, budget):
    """Levenshtein distance between a and b, or None if it exceeds budget.

    Hand-rolled rather than difflib because web/chat.js has to run exactly
    the same algorithm - mirroring difflib's ratio() in JavaScript would
    guarantee the two drift apart on some word nobody tests.
    """
    if abs(len(a) - len(b)) > budget:
        return None
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1,
                               previous[j - 1] + (ca != cb)))
        previous = current
        if min(previous) > budget:
            return None
    return previous[-1] if previous[-1] <= budget else None


def nearest(word, vocab):
    """The closest word in the statute's vocabulary, or None.

    Two edits for a long word covers the transposition that plain edit
    distance counts twice: "encrpytion" is two substitutions away from
    "encryption", not one.
    """
    budget = 2 if len(word) >= EDIT_BUDGET_LONG else 1
    best, best_distance = None, budget + 1
    for candidate in vocab:
        distance = edits_within(word, candidate, budget)
        if distance is not None and (distance < best_distance or
                                     (distance == best_distance and
                                      candidate < (best or "~"))):
            best, best_distance = candidate, distance
    return best


def spell_fix(index, question):
    """Map long unknown words to the nearest word the statute actually uses.

    BM25 is exact-match, so one typo in the only content word returns nothing
    at all: "is encrpytion mandatory" retrieved zero provisions. Short words
    and words the statute already knows are left alone, and the budget is
    tight enough that genuinely absent terms - aadhaar, kyc, saas, fintech -
    fail to match rather than being bent into an unrelated provision.
    """
    known = set(index.idf)
    vocab = vocabulary(index)
    out, changed = [], False
    for word in re.findall(r"[A-Za-z0-9']+|[^A-Za-z0-9']+", question):
        token = word.lower()
        root = stem(token)
        if not token.strip() or len(root) < MIN_FIX_LEN or root in known:
            out.append(word)
            continue
        near = nearest(root, vocab)
        # "handle" is absent only because the statute says "handling", whose
        # stem is "handl". Rewriting one to the other changes nothing for
        # retrieval and makes the query look mangled, so same-family matches
        # (one a prefix of the other) are left alone.
        if near and not (near.startswith(root) or root.startswith(near)):
            out.append(near)
            changed = True
        else:
            out.append(word)
    return "".join(out) if changed else question


def search(index, question, history, k=4, pool=8):
    """Pick the provisions, then follow what they point at.

    Kept separate from _pick so the cross-reference step runs once, on
    whichever of the standalone or context-merged results won.
    """
    hits, query = _pick(index, question, history, k=k, pool=pool)
    if not hits:
        # Only as a fallback: a mis-correction can then only improve on
        # having found nothing, never displace a real match.
        fixed = spell_fix(index, question)
        if fixed != question:
            hits, query = _pick(index, fixed, history, k=k, pool=pool)
    return crossrefs(index, hits), query


def narrow(question, hits):
    """Demote Consent Manager material unless the question is about one.

    Rule 4 and the First Schedule are thick with the word "consent", so they
    outrank the consent rules themselves for any company asking about its own
    consent flow - while being about registering as a Consent Manager, which
    almost no one is.
    """
    rescored = [(score * chapter_weight(question, rec), rec) for score, rec in hits]
    rescored.sort(key=lambda p: -p[0])
    return rescored


# By id, not by tag: Section 6 merely mentions a Consent Manager, and
# demoting it dropped the consent rule itself out of a consent question.
ABOUT_CM = {"rules-r4", "rules-sch1"}
# Clauses that are about registering as a Consent Manager rather than about
# the user's own consent flow. Section 6(9) - "Every Consent Manager shall be
# registered with the Board" - was ranking fourth for a company asking whether
# its terms-and-conditions consent was valid.
CM_CLAUSE = re.compile(r"\bConsent Manager\b", re.I)
CM_CLAUSE_WEIGHT = 0.4


def chapter_weight(question, record):
    """How much this provision's chapter is worth for this question.

    Extracted so clause ranking can apply the same demotions. It could not
    before: the weights were computed inside narrow() and lost by the time
    baseline.rank re-fitted BM25 over sub-clauses, so a definition clause
    could outrank the operative one even though its record had been demoted.
    """
    rid = record["id"] if record else ""
    if rid in ABOUT_CM and not CM_ASKED.search(question):
        return 0.25
    if rid in MACHINERY and not ENFORCEMENT_ASKED.search(question):
        return 0.2
    if rid in DEFINITIONS and not DEFINITION_ASKED.search(question):
        return 0.35
    if rid in CHILDREN and not CHILDREN_ASKED.search(question):
        return 0.3
    if rid in RETENTION_TABLE and not RETENTION_ASKED.search(question):
        return 0.3
    if rid in EXEMPTIONS and not EXEMPTION_ASKED.search(question):
        return 0.3
    return 1.0


# A wh-word anywhere in the question means it is not a yes/no, however it
# goes on. "so what exactly do i need to change" matched the "whether" rule
# on "do i need" and was answered "Yes - this is required."
NOT_YES_NO = re.compile(r"\b(what|which|whose|where)\b", re.I)


def intent_of(question):
    q = question.strip().lower()
    for name, pattern in INTENTS:
        if name == "whether" and NOT_YES_NO.search(q):
            continue
        if re.search(pattern, q):
            return name
    return "general"


def _boost(intent, text, record):
    """Nudge clauses that actually carry the kind of answer being asked for."""
    if intent == "duration" and TIME.search(text):
        return 1.8
    if intent == "penalty":
        if record and record.get("max_penalty_inr"):
            return 1.6
        if MONEY.search(text):
            return 1.8
    if intent == "who" and WHO_BOOST.search(text):
        return 1.2
    return 1.0


# "Can I store data in Singapore?" asks about permission; answering "Yes -
# this is required" confuses may with shall. Rule 15 permits that transfer
# subject to conditions, it does not demand it.
PERMISSION_ASKED = re.compile(
    r"^\s*(can|may|could)\b|\bcan (i|we|you|they)\b|\bmay (i|we)\b|"
    r"\bis it (allowed|permitted|ok|okay|legal|lawful|fine)\b|"
    r"\b(am|are) (i|we) allowed\b", re.I)
PERMITS = re.compile(r"\bmay\b|\bis permitted\b|\bentitled\b", re.I)


def _headline(intent, cite, text, question=""):
    """Lead with the fact, not the paragraph."""
    if intent == "duration":
        m = TIME.search(text)
        if m:
            phrase = " ".join(m.group().split()).strip()
            lead = "Within " if re.match(r"within\b", phrase, re.I) else ""
            phrase = re.sub(r"^(within|of|for a period of|for|at least|least)\s+",
                            "", phrase, flags=re.I)
            out = (lead + phrase).strip()
            return out[0].upper() + out[1:] + ". (" + cite + ")"
    if intent == "whether":
        if re.search(r"\bshall not\b|\bshall no\b|\bno .{0,30}shall\b", text):
            return "No - this is prohibited. (" + cite + ")"
        if PERMISSION_ASKED.search(question):
            if PERMITS.search(text):
                return ("Yes - permitted, subject to the conditions below. ("
                        + cite + ")")
            # A permission question answered from a clause that only imposes a
            # duty says nothing useful; let the provisions speak instead.
            return None
        if re.search(r"\bshall\b|\bmust\b|\brequired\b", text):
            return "Yes - this is required. (" + cite + ")"
    return None


# A headline asserts a fact, so it is only allowed when retrieval was strong.
# Without this gate a stray word match produces a confident, cited, wrong
# answer - the worst output this tool can give. The retrieval score separates
# far better than counting shared words, because the question's everyday
# phrasing ("16 year old") never matches the statute's ("child") directly.
WHO_BOOST = re.compile(r"Data Fiduciary|Data Principal|Board|Consent Manager"
                       r"|Data Processor")
PROHIBITED = re.compile(r"\bshall not\b|\bshall no\b|\bno .{0,30}shall\b", re.I)
REQUIRED = re.compile(r"\bshall\b|\bmust\b|\brequired\b", re.I)

HELLO = ("Hello. Ask me what the DPDP Act 2023 or the Rules 2025 require, "
         "and I will answer with the clause.\n\n"
         'Try: "how fast must I report a breach to the Board?"')
OUT_OF_SCOPE = ("That looks like it is outside the DPDP Act 2023 and the Rules "
                "2025, which are the only things I know.\n\n"
                "Ask me about notice, consent, children's data, security "
                "safeguards, breach reporting, retention and erasure, data "
                "principal rights, processors, cross-border transfer or penalties.")
NO_MATCH = ("Nothing in the Act or the Rules matches that.\n\n"
            "Try the words the statute uses - erase, consent, breach, retain, "
            'safeguards, notice, grievance - or ask "what can you answer?".')

CONFIDENT = 7.0

# Per content word. Above this the question stands on its own and the
# conversation is not folded in; below it, context is worth trying.
CONFIDENT_ALONE = 2.2

# How much better, per word, the context-merged query must score before it
# overrides the question the user actually typed.
# Measured: a genuine follow-up ("and what about telling the users?") scores
# well standalone now that the synonym layer does the work, while a new
# subject beat it only marginally when merged. A wide margin keeps the
# question the user typed in charge.
CONTEXT_MARGIN = 1.4
# Content words, after stopwords. At or below this a question that also
# points back at the previous turn is treated as part of it; see _pick.
SHORT_FOLLOWUP = 3

# Measured separation on this corpus: real DPDP questions score ~7 and up,
# while off-topic ones sit near 1-5. Deliberately low, so a genuine question
# is never turned away - a weak-but-plausible match gets the hedge instead.
# Per content word, not absolute: "can consent be withdrawn?" has two content
# words and scored 2.89, so an absolute floor refused a squarely in-scope
# question, while "capital of France" scores 0.39 over the same two words.
# Measured per content word: real questions bottom out near 0.86 ("i use
# users data to make them login") while nonsense sits at 0.0-0.2. One
# outlier, "write me a poem about dogs", reaches 1.09 by matching "in
# writing", so no threshold separates everything. Set below the real
# questions: refusing a genuine one is far worse than hedging a silly one,
# which the confidence gate already handles.
IN_SCOPE_PER_WORD = 0.45


# How much a clause gains from sitting in the best-matching provision.
# Record retrieval already judged which provision governs; without this the
# clause scoring could ignore it and answer "what rights do I have" from
# section 15, the Data Principal's *duties*.
PARENT_BONUS = [1.5, 1.15, 1.0]
# Cross-referenced provisions compete in clause ranking but should not win
# outright: the question matched the referring clause, not this one.
CROSSREF_PENALTY = 0.55


def rank(query, hits, intent, limit=4, question=None):
    """baseline.rank, re-scored for what the question is actually asking."""
    by_cite_record, by_cite_rank = {}, {}
    for position, (_score, record) in enumerate(hits):
        for cite, _text in baseline.units(record):
            by_cite_record[cite] = record
            by_cite_rank[cite] = position

    scored = []
    for score, cite, text in baseline.rank(query, hits, limit=limit * 3):
        position = by_cite_rank.get(cite, len(PARENT_BONUS))
        parent = PARENT_BONUS[position] if position < len(PARENT_BONUS) else 1.0
        record = by_cite_record.get(cite)
        # A provision we only reached by following a reference is background;
        # it should colour the answer, not headline it.
        if record is not None and record.get("crossref"):
            parent *= CROSSREF_PENALTY
        if question:
            # The same chapter demotions narrow() applies to records. Without
            # them here, Rule 2(1)(c) - the definition of "user account" -
            # ranked third for a company asking whether its consent was valid,
            # because its record had been demoted but its clause had not.
            parent *= chapter_weight(question, record)
            if CM_CLAUSE.search(text) and not CM_ASKED.search(question):
                parent *= CM_CLAUSE_WEIGHT
        scored.append((score * _boost(intent, text, record) * parent,
                       cite, text))
    scored.sort(key=lambda r: -r[0])
    return scored[:limit]


CHILD_OF = "{}({})"


def with_children(picked, hits, limit=8):
    """Follow a chapeau down to the items it introduces.

    Rule 7(1) says a Data Fiduciary shall intimate the data principal "--";
    what must actually be told is in (a) to (e). Quoting only the chapeau
    answers "must I tell them" but not "what must I tell them", which is what
    a reader asked. So when the leading clause is a chapeau, its own lettered
    children follow it.
    """
    if not picked:
        return picked
    all_units = {}
    for _score, record in hits:
        for cite, text in baseline.units(record):
            all_units[cite] = text

    top_cite = picked[0][1]
    children = []
    for letter in "abcdefghijklmnop":
        key = CHILD_OF.format(top_cite, letter)
        if key not in all_units:
            break
        children.append((0.0, key, all_units[key]))
    if not children:
        return picked

    # One seen-set, not two filters. Filtering children by "already picked"
    # and then picked by "is a child" dropped any clause that was both, so a
    # clause ranking that found Rule 7(1)(b) on its own lost it here.
    merged, seen = [picked[0]], {picked[0][1]}
    for clause in children + list(picked[1:]):
        if clause[1] not in seen:
            seen.add(clause[1])
            merged.append(clause)
    return merged[:limit]


# Naming another regime is the one case where a confident answer is certainly
# wrong: "what are the GDPR fines in Europe?" returned the Indian Schedule's
# amounts, because the synonym layer turned "fines" into "monetary penalty
# crore rupees" and the scope gate then saw a strong match. Saying what we do
# not cover is more useful than a near-miss from the wrong statute.
OTHER_REGIME = [
    (r"\bgdpr\b|\beu\b.{0,15}\b(law|regulation)|\beuropean union\b|"
     r"\bdata protection act\b.{0,10}\b(uk|1998|2018)\b",
     "the EU/UK regime (GDPR)"),
    # Not a bare "california": "can we store data in California?" is an
    # ordinary cross-border question under section 16 and Rule 15, and
    # refusing it as a CCPA question answers the wrong thing. The place names
    # elsewhere in OTHER_REGIME are guarded the same way.
    (r"\bccpa\b|\bcpra\b|\bcalifornia\s+(privacy|consumer|law|act)\b",
     "California privacy law"),
    (r"\bhipaa\b", "HIPAA, a US health privacy law"),
    (r"\bpdpa\b|\bsingapore\b.{0,20}\b(act|law)\b", "Singapore's PDPA"),
    (r"\bcert-?in\b", "the CERT-In directions, a separate instrument"),
    (r"\b(rbi|reserve bank)\b", "RBI regulation, a separate instrument"),
    (r"\b(sebi|irdai)\b", "a sectoral regulator's rules, separate from the Act"),
    (r"\bincome ?tax\b|\bgst\b|\btds\b|\bitr\b", "tax law"),
    (r"\b(it act|information technology act)\b.{0,10}2000|\bsection 43a\b",
     "the IT Act 2000, which the DPDP Act displaces in part"),
    (r"\b(companies act|labour law|trademark|patent|gratuity)\b",
     "a different area of law"),
]


def other_regime(question):
    """Name the regime the question is really about, if it is not ours."""
    for pattern, name in OTHER_REGIME:
        if re.search(pattern, question, re.I):
            return name
    return None


def grounding(question, record):
    """(matched, fraction) for the user's own words against the provision.

    A sanity check independent of BM25: if almost nothing the user typed is
    in the text, a high score came from somewhere other than their question.
    The count matters as well as the fraction - "what is the capital of
    France?" is two content words, and "capital" occurs in the Act, so a
    fraction alone called it a 50% match and let it through.
    """
    words = set(tokenize(question))
    if not words:
        return 0, 0.0
    text = set(tokenize(record["text"] + " " + record.get("title", "")))
    matched = len(words & text)
    return matched, matched / len(words)


GROUNDING_MIN = 0.34
# One matched term of art is enough; see in_scope.


def in_scope(question, hits, query=None):
    """Is this a DPDP question at all?

    The score has to be divided by the length of the query that produced it,
    not the length of the question. Dividing by the question let every synonym
    rule inflate the in-scope signal: the expansion adds query terms, BM25
    scores rise with query terms, and the denominator never moved. That is how
    a question about European fines cleared the gate.

    A question whose own words do appear in the provision is in scope even if
    the arithmetic is marginal, which keeps short questions ("can consent be
    withdrawn?") from being refused.
    """
    if not hits:
        return False
    terms = len(tokenize(query)) if query else len(tokenize(question))
    per_word = hits[0][0] / max(2, terms)
    if per_word >= IN_SCOPE_PER_WORD:
        return True
    # One matched word is enough when it is the statute's own term of art:
    # "how long do I have to resolve a grievance?" shares exactly "grievance"
    # with Rule 14 and is plainly in scope. Requiring two matches refused it,
    # and a false refusal costs far more than a hedge on nonsense - which is
    # all an unmatched question gets, since _headline needs CONFIDENT.
    matched, fraction = grounding(question, hits[0][1])
    return matched >= 1 and fraction >= GROUNDING_MIN


# The Act sets out worked examples inside the provisions themselves. They are
# part of the statute, but they are examples: Section 6(1)'s is 300 characters
# about a telemedicine app and a phone contact list, which is most of what got
# quoted at a company asking about linking Gmail accounts. They stay in the
# full text under "provisions"; they are dropped from the quoted answer.
ILLUSTRATION = re.compile(r"\s*Illustrations?\.\s.*$", re.S)


def without_illustration(text):
    """The operative words of a clause, without its worked example."""
    stripped = ILLUSTRATION.sub("", text).strip()
    # Some clauses are almost entirely illustration; keep those whole rather
    # than quoting a fragment.
    return stripped if len(stripped) >= 60 else text


def _trim(text, cap=900):
    text = " ".join(without_illustration(text).split())
    if len(text) <= cap:
        return text
    return text[:cap].rsplit(" ", 1)[0] + "..."


def respond(question, hits, history=None, penalties=None, query=None):
    """Build a reply. Returns plain text; the caller streams it."""
    raw = question.strip()
    if META.search(raw):
        return HELP
    if GREETING.match(raw) and len(tokenize(raw)) <= 2:
        return ("Hello. Ask me what the DPDP Act 2023 or the Rules 2025 require, "
                "and I will answer with the clause.\n\nTry: \"how fast must I "
                "report a breach to the Board?\"")
    if THANKS.match(raw) and len(tokenize(raw)) <= 3:
        return "Happy to help. Ask another."

    other = other_regime(raw)
    if other:
        return ("That is {}, which I do not cover. I only know the Digital "
                "Personal Data Protection Act 2023 and the DPDP Rules 2025.\n\n"
                "If the question also has an Indian DPDP angle, ask it that way "
                "and I will answer from the Act or the Rules."
                .format(other))

    intent = intent_of(raw)
    if hits and not in_scope(raw, hits, query):
        return ("That looks like it is outside the DPDP Act 2023 and the Rules "
                "2025, which are the only things I know.\n\n"
                "Ask me about notice, consent, children's data, security "
                "safeguards, breach reporting, retention and erasure, data "
                "principal rights, processors, cross-border transfer or penalties.")
    # Rank clauses on the same expanded query the records were found with,
    # or the statute's vocabulary never reaches the clause-level scoring.
    # The same query that produced these hits: ranking clauses with the merged
    # query after search() chose the standalone one put the context back.
    ranking_query = query or expand(question, history)
    picked = with_children(
        rank(ranking_query, hits, intent, limit=4, question=raw), hits)
    if not picked:
        return ("Nothing in the Act or the Rules matches that.\n\n"
                "Try the words the statute uses - erase, consent, breach, retain, "
                "safeguards, notice, grievance - or ask \"what can you answer?\".")

    top_cite, top_text = picked[0][1], picked[0][2]
    lines = []

    sure = bool(hits) and hits[0][0] >= CONFIDENT
    head = _headline(intent, top_cite, top_text, raw) if sure else None
    if head:
        lines.append(head)
        lines.append("")
    elif not sure:
        lines.append("I am not confident I found the right provision for that - "
                     "these are the closest, judge them yourself:")
        lines.append("")

    if intent == "penalty" and penalties:
        rows = [p for p in penalties if p.get("max_inr")]
        if rows:
            lines.append("Maximum penalties under the Act's Schedule:")
            for p in rows[:4]:
                lines.append("  " + p["provision"] + " - " + p["max_words"])
            lines.append("")

    lines.append("From the statute:" if head else "Most relevant provisions:")
    lines.append("")
    for _s, cite, text in picked[: 6 if head else 7]:
        lines.append(cite + " - " + _trim(text))
        lines.append("")

    extra = [c for _s, c, _t in picked[6 if head else 7:]]
    if extra:
        lines.append("Also relevant: " + ", ".join(extra))

    return "\n".join(lines).strip()


def stream(question, hits, history=None, penalties=None, query=None):
    text = respond(question, hits, history=history, penalties=penalties,
                   query=query)
    for line in text.splitlines(keepends=True):
        yield line
