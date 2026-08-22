"""Output-grounding check: does every number in the final answer trace back to a
tool result from this turn?

A second, independent line of defence on top of the system prompt's "never state
a number that didn't come from a tool" rule — a rule the model can still slip on.
No LLM call: it extracts numeric-looking claims from the answer and confirms each
appears in the tool results gathered this turn.

CALIBRATION IS THE HARD PART, and getting it wrong in either direction is
expensive. Too loose and it misses an invented figure. Too noisy and people stop
reading it, which is worse than not shipping it — a warning that fires on
correct answers trains the reader to dismiss the one that matters.

Two false-positive classes were found on real logged answers and are handled
below. Both are the model *presenting* a number rather than changing it:

  1. `95.0` from the database, written as `95%` by the model.
  2. `29974.8427` from the database, written as `29974.84` — correct rounding
     for currency.

Neither is a wrong number. What is still caught: a different number, and a
number that appears nowhere in the tool output at all (an invented dosage, for
instance).
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

# Currency (Rs./₹ NNN, NN,NNN), percentages, and bare numbers with 2+ digits.
# Single digits are skipped — too many false positives from ordinary prose
# ("1 sample", "2 brands").
_NUMBER_PATTERN = re.compile(
    r"(?:Rs\.?|₹)\s*[\d,]+(?:\.\d+)?"  # currency
    r"|\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b"  # comma-grouped thousands
    r"|\b\d+\.\d+\b"  # decimals
    r"|\b\d{2,}\b"  # bare integers, 2+ digits
    r"|\b\d+%",  # percentages
    re.IGNORECASE,
)

# How far to go when generating reduced-precision forms of a source number.
# Four decimals covers every column in this dataset.
_MAX_DECIMALS = 4


def _normalize_number(raw: str) -> str:
    """Canonical string form, so "Rs. 5,560" and "5560" compare equal.

    Keeps only digits, commas and periods (dropping letters, currency symbols
    and spaces), drops commas, then trims stray periods — otherwise the period
    in "Rs." survives as a leading decimal point. Finally drops a purely-zero
    fractional part, so "95.0" and "95" are the same number.
    """
    kept = re.sub(r"[^\d,.]", "", raw).replace(",", "").strip(".")
    return re.sub(r"\.0+$", "", kept)


def extract_numeric_claims(text: str) -> list[str]:
    return [_normalize_number(m) for m in _NUMBER_PATTERN.findall(text)]


def _presentation_variants(value: str) -> set[str]:
    """Every form the model could legitimately *display* `value` as.

    Rounded and truncated at each precision from 0 to _MAX_DECIMALS. Both,
    because models do both — and either way the number is unchanged, only its
    displayed precision is.
    """
    variants = {value}
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError):
        return variants

    for places in range(_MAX_DECIMALS + 1):
        quantum = Decimal(1).scaleb(-places)
        for rounding in ("ROUND_HALF_UP", "ROUND_DOWN"):
            try:
                variants.add(_normalize_number(str(number.quantize(quantum, rounding=rounding))))
            except InvalidOperation:
                continue
    return variants


def check_grounding(answer_text: str, tool_results_text: str) -> dict:
    """Returns {"grounded": bool, "unverified_claims": [...]}.

    `tool_results_text` should be the concatenation of every tool result
    produced during this turn.
    """
    claims = extract_numeric_claims(answer_text)
    if not claims:
        return {"grounded": True, "unverified_claims": []}

    source: set[str] = set()
    for number in extract_numeric_claims(tool_results_text):
        source |= _presentation_variants(number)

    unverified = [c for c in claims if c and c not in source]
    # Preserve order, drop duplicates — the same wrong number twice is one problem.
    seen: set[str] = set()
    deduped = [c for c in unverified if not (c in seen or seen.add(c))]
    return {"grounded": not deduped, "unverified_claims": deduped}


# ---------------------------------------------------------------------------
# internal-disclosure check
# ---------------------------------------------------------------------------

# Scoped query aliases (`my_doctors`) and any canonical column name containing an
# underscore (`mcr_coverage`, `chair_id`). Both are internal identifiers a rep
# should never see — the business term is "MCR coverage", not `mcr_coverage`.
#
# Single-word columns (`notes`, `year`, `specialty`) are deliberately NOT checked:
# they are ordinary English and would fire on every correct answer.
_IDENTIFIER = re.compile(r"\b(my_[a-z_]+|[a-z]+(?:_[a-z0-9]+)+)\b")


def check_internal_disclosure(answer_text: str, internal_names: set[str]) -> list[str]:
    """Internal identifiers that leaked into a rep-facing answer.

    Not user-facing. This is a regression signal: the model is instructed never
    to name tables or columns, and asking it to "list all the tables" used to
    produce a tidy bullet list of every scoped alias. A prompt rule can be talked
    around, so the outcome is measured rather than assumed — the hits land in the
    audit log and /api/metrics.

    `internal_names` should be the scoped aliases plus every canonical column.
    """
    found = {
        match.group(0)
        for match in _IDENTIFIER.finditer(answer_text.lower())
        if match.group(0) in internal_names
    }
    return sorted(found)


# ---------------------------------------------------------------------------
# citation check (retrieval)
# ---------------------------------------------------------------------------

# A retrieved passage is identified in an answer by its document title or its
# section number. Section numbers are the stronger signal — "4.5", "§4.2.1" —
# because a rep can look them up, which is the entire reason chunking is
# section-aware.
_SECTION_REF = re.compile(r"(?:§\s*)?\b(\d+(?:\.\d+){0,3})\b")

#: Answers that make no claim need no citation. A refusal is the important case:
#: "the approved literature does not cover X" is exactly the behaviour we want
#: from an absent answer, and demanding a citation for it would push the model
#: towards inventing one — the opposite of the intent.
_NO_CLAIM_MARKERS = (
    "does not cover",
    "doesn't cover",
    "not covered",
    "no information",
    "not in the approved",
    "not available in",
    "cannot find",
    "could not find",
    "can't find",
    "no relevant",
    "medical information request",
    "raise a medical information",
)


def check_citations(answer_text: str, retrieved: list[dict]) -> dict:
    """Returns {"cited": bool, "reason": str|None, "available": int}.

    Only meaningful when retrieval actually returned something: if the corpus had
    nothing, there is nothing to cite and the honest answer has no citation in it.

    `retrieved` is a list of {"document": ..., "section": ...} taken from
    search_literature results this turn.

    This is the enforcement behind "cite or refuse". The tool description asks
    for citations, but a prompt rule can be talked around, so the outcome is
    measured: hits are recorded to the audit log and /api/metrics next to the
    grounding check, rather than being asserted in a docstring.
    """
    if not retrieved:
        return {"cited": True, "reason": None, "available": 0}

    lowered = answer_text.lower()
    if any(marker in lowered for marker in _NO_CLAIM_MARKERS):
        return {"cited": True, "reason": None, "available": len(retrieved)}

    # A document is referenced if a distinctive word from its title appears, or
    # if its section number does. Titles carry strengths and forms ("Cardevia
    # (Cardevastatin) 10 mg / 20 mg …"), so matching whole titles would almost
    # never succeed; the brand-like leading token is what a citation actually uses.
    for item in retrieved:
        title = str(item.get("document") or "")
        head = re.split(r"[\s(]+", title.strip())[:1]
        if head and head[0] and len(head[0]) > 3 and head[0].lower() in lowered:
            return {"cited": True, "reason": None, "available": len(retrieved)}

        section = str(item.get("section") or "")
        numbers = _SECTION_REF.findall(section)
        if numbers and any(n in answer_text for n in numbers if len(n) > 2):
            return {"cited": True, "reason": None, "available": len(retrieved)}

    return {
        "cited": False,
        "reason": "answer makes claims from retrieved literature without citing a source",
        "available": len(retrieved),
    }


# ---------------------------------------------------------------------------
# outbound compliance check (text SENT to a prescriber)
# ---------------------------------------------------------------------------

# A DIFFERENT STANDARD FROM check_grounding, and the difference is the point.
#
# check_grounding governs what appears on the rep's SCREEN, where "the mail says
# 42%" is a true statement and the mail is a legitimate source. This governs what
# LEAVES THE COMPANY, where only the approved literature counts — a figure a
# doctor typed into an email is not an approved claim just because the assistant
# can see it. So a mail body entering `tool_results_text`, and therefore
# satisfying check_grounding, does not satisfy this.
#
# A PRE-FILTER, NOT THE WHOLE REVIEW. An obvious "better than" must not cost a
# model call, and a hard block should short-circuit before one. app/bot/compliance
# .py adds the LLM reviewer for what a regex cannot see: an implied comparison, an
# indication stated obliquely, a claim subtly wider than the passage it cites.
#
# Every rule below is lifted from the corpus's own wording. The sources are
# etl/literature/aids.py — the detailing guides' "What must not be said" and
# objection-handling's "Objections you must not answer yourself" — and the
# generated pharmacovigilance SOP. Nothing here is invented, because a compliance
# rule the company did not write is a rule nobody has to follow.

#: "No comparative superiority claim against any named competitor product."
#: (Detailing guide §4.) Plus objection handling: "Any comparative efficacy claim
#: not in approved material - Decline."
_SUPERIORITY = re.compile(
    r"\b(?:better|superior|stronger|safer|more effective|more potent|faster acting)\s+than\b"
    r"|\boutperform\w*\b"
    r"|\bbest[- ]in[- ]class\b"
    r"|\bmost effective\b"
    r"|\bthe only (?:statin|drug|product|treatment|option)\b"
    r"|\bunlike (?:the )?(?:other|older|competing)\b",
    re.IGNORECASE,
)

#: "No use in pregnancy, paediatrics, or any indication outside section 4.1 of
#: the SmPC." (Detailing guide §4.)
_OFF_LABEL = re.compile(
    r"\bpregnan\w*|\bbreast[- ]?feed\w*|\blactat\w*|\bpaediatric\w*|\bpediatric\w*"
    r"|\bchildren\b|\binfant\w*|\boff[- ]label\b|\bunlicensed\b",
    re.IGNORECASE,
)

#: "No discussion of unlicensed dosing above 40 mg daily." (Detailing guide §4.)
_DOSE = re.compile(r"\b(\d{1,4})\s?mg\b", re.IGNORECASE)
_LICENSED_MAX_MG = 40

#: SOP-PV-01 §2.3: "Do not assess whether the product caused the event." and
#: "Do not advise the prescriber or patient on management of the event."
_CAUSALITY = re.compile(
    r"\b(?:not|isn't|is not|wasn't|was not) (?:caused|related|linked|due) to\b"
    r"|\bunrelated to (?:the )?(?:drug|product|treatment|medication)\b"
    r"|\bnothing to do with\b"
    r"|\bcoincidental\b",
    re.IGNORECASE,
)
_MANAGEMENT_ADVICE = re.compile(
    r"\b(?:stop|discontinue|withdraw|halve|reduce|increase|switch)\s+(?:the\s+)?"
    r"(?:dose|drug|product|treatment|medication|therapy|patient)\b"
    r"|\bswitch (?:them|the patient|him|her) to\b",
    re.IGNORECASE,
)

#: Words in an inbound thread that mean a suspected adverse event may be in play.
#: THE ONE canonical list — imported by services/agenda.py for triage escalation
#: too, so the words that route a thread to pharmacovigilance and the words that
#: flag it on the rep's list can never drift apart (CLAUDE.md §1.4). `pregnan`
#: is included because exposure in pregnancy is itself a PV reporting trigger.
AE_TERMS = (
    "adverse", "side effect", "side-effect", "reaction", "rash", "hospitalis",
    "hospitaliz", "admitted", "toxicity", "overdose", "anaphyla", "jaundice",
    "liver injury", "died", "death", "pregnan",
)

#: SOP-PV-01 §3: "Maximum per prescriber per call - 6 units".
_SAMPLES = re.compile(r"\b(\d{1,3})\s+(?:sample|unit|pack|strip)s?\b", re.IGNORECASE)
_SAMPLE_LIMIT = 6

#: SOP-PV-01 §2.3: "Do not record the patient's name, address or contact details."
_MOBILE = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{9}(?!\d)")

#: Objection handling §1 step 3: "If you do not have an approved response, say so
#: and raise a Medical Information request." A promise beyond the label is the
#: opposite of that.
_OVERCOMMIT = re.compile(
    r"\bI can confirm\b|\bI guarantee\b|\bit is approved for\b|\byou can use it for\b"
    r"|\bthere are no (?:side effects|risks|interactions)\b",
    re.IGNORECASE,
)

#: A clinical claim needs BOTH a clinical term and a figure. Requiring both is
#: calibration, not laziness: "thank you for your time" contains no claim, and a
#: check that fires on it teaches the rep to click past the one that matters.
_CLINICAL_TERM = re.compile(
    r"\bindicat\w*|\befficac\w*|\bdos(?:e|ing|age)\b|\breduc\w*|\bincreas\w*"
    r"|\bLDL\b|\bHbA1c\b|\bblood pressure\b|\beGFR\b|\btitrat\w*|\binteraction\w*"
    r"|\bcontraindicat\w*|\badverse\b|\bundesirable\b|\bmortality\b|\boutcome\w*",
    re.IGNORECASE,
)


def _finding(rule: str, severity: str, quote: str, basis: str, guidance: str) -> dict:
    # `quote` is always an exact span of the draft, so the approval card can
    # highlight it and a reviewer's claim can be verified against the text.
    return {
        "rule": rule,
        "severity": severity,
        "quote": quote[:200],
        "basis": basis,
        "guidance": guidance,
    }


def check_outbound(draft: str, retrieved: list[dict], *, thread_text: str = "") -> dict:
    """Deterministic pre-filter for text about to be sent to a prescriber.

    Returns
        {"verdict": "block"|"warn"|"clear",
         "findings": [{"rule","severity","quote","basis","guidance"}],
         "requires_escalation": "pharmacovigilance"|"medical_information"|None,
         "reviewed_by": "rules",
         "cited": bool, "available": int}

    No model call, so this runs in the fast test job with no API key — which is
    also why the rules that must never regress live here rather than in the
    reviewer's prompt.
    """
    findings: list[dict] = []
    escalation: str | None = None

    if match := _SUPERIORITY.search(draft):
        findings.append(
            _finding(
                "comparative_superiority",
                "block",
                match.group(0),
                "Detailing guide - 4. What must not be said",
                "Drop the comparison. Use the approved response: total therapy value, "
                "dosing convenience, and the approved trial figures.",
            )
        )

    if match := _OFF_LABEL.search(draft):
        findings.append(
            _finding(
                "off_label",
                "block",
                match.group(0),
                "Detailing guide - 4. What must not be said",
                "Outside the approved indication. Say the approved literature does not "
                "cover it and raise a Medical Information request.",
            )
        )
        escalation = "medical_information"

    for match in _DOSE.finditer(draft):
        if int(match.group(1)) > _LICENSED_MAX_MG:
            findings.append(
                _finding(
                    "unlicensed_dosing",
                    "block",
                    match.group(0),
                    "Detailing guide - 4. What must not be said",
                    f"Above the licensed maximum of {_LICENSED_MAX_MG} mg daily. Do not "
                    f"discuss unlicensed dosing.",
                )
            )
            break

    thread_lower = (thread_text or "").lower()
    if any(term in thread_lower for term in AE_TERMS):
        escalation = "pharmacovigilance"
        if match := _CAUSALITY.search(draft):
            findings.append(
                _finding(
                    "adverse_event_routing",
                    "block",
                    match.group(0),
                    "SOP-PV-01 - 2.3 What you must not do",
                    "Do not assess whether the product caused the event. Report it to "
                    "pharmacovigilance within 24 hours instead.",
                )
            )
        if match := _MANAGEMENT_ADVICE.search(draft):
            findings.append(
                _finding(
                    "adverse_event_routing",
                    "block",
                    match.group(0),
                    "SOP-PV-01 - 2.3 What you must not do",
                    "Do not advise on management of the event. That is the prescriber's "
                    "decision.",
                )
            )

    for match in _SAMPLES.finditer(draft):
        if int(match.group(1)) > _SAMPLE_LIMIT:
            findings.append(
                _finding(
                    "sample_limit",
                    "block",
                    match.group(0),
                    "SOP-PV-01 - 3. Sample handling",
                    f"The maximum is {_SAMPLE_LIMIT} units per prescriber per call.",
                )
            )
            break

    if match := _MOBILE.search(draft):
        findings.append(
            _finding(
                "patient_identifiable",
                "block",
                match.group(0),
                "SOP-PV-01 - 2.3 What you must not do",
                "Remove the contact number. Patient-identifying details must not be "
                "recorded or sent.",
            )
        )

    if match := _OVERCOMMIT.search(draft):
        findings.append(
            _finding(
                "commitment_beyond_approval",
                "warn",
                match.group(0),
                "Objection handling - 1. The four-step method",
                "If there is no approved response, say so and raise a Medical "
                "Information request rather than committing.",
            )
        )

    # A clinical claim must trace to a passage retrieved THIS TURN. The matching
    # itself is check_citations', so the two can never disagree about what counts
    # as a citation — but its "nothing retrieved means nothing to cite" shortcut
    # is deliberately NOT reused here.
    #
    # That shortcut is right for an on-screen answer: if the corpus held nothing,
    # the honest answer has no citation in it. For outbound text it is exactly
    # backwards. A clinical claim with no retrieved passage at all is the
    # *invented* claim — the one case that most needs stopping — so an empty
    # `retrieved` makes a claim untraceable rather than exempt.
    citation = check_citations(draft, retrieved)
    traceable = bool(retrieved) and citation["cited"]
    # The refusal exemption has to be applied here rather than inherited, because
    # check_citations returns early on empty `retrieved` before it looks at the
    # markers. "The approved literature does not cover that" is the behaviour we
    # want from an absent answer, and demanding a citation for it would push the
    # model towards inventing one.
    refuses = any(marker in draft.lower() for marker in _NO_CLAIM_MARKERS)
    makes_clinical_claim = bool(_CLINICAL_TERM.search(draft) and _NUMBER_PATTERN.search(draft))
    if makes_clinical_claim and not refuses and not traceable:
        findings.append(
            _finding(
                "uncited_clinical_claim",
                "block",
                (_CLINICAL_TERM.search(draft) or _NUMBER_PATTERN.search(draft)).group(0),
                "Approved literature",
                "Cite the document and section the claim comes from, or remove it.",
            )
        )

    if any(f["severity"] == "block" for f in findings):
        verdict = "block"
    elif findings:
        verdict = "warn"
    else:
        verdict = "clear"

    return {
        "verdict": verdict,
        "findings": findings,
        "requires_escalation": escalation,
        "reviewed_by": "rules",
        "cited": traceable,
        "available": len(retrieved),
    }
