"""Text a rep SENDS to a prescriber, which is regulated in a way an answer is not.

check_grounding accepts a number a doctor wrote in an email, because on the rep's
own screen the mail is a legitimate source. A promotional claim does not get to
be true because a stranger typed it, so outbound text is held to the approved
literature instead. That difference is the whole reason this check exists
separately, and it is what the first test here pins down.

Every rule is quoted from the corpus, so each test names its source. If a test
here disagrees with etl/literature/aids.py or the pharmacovigilance SOP, the
corpus wins.
"""

from __future__ import annotations

from app.bot.guardrails import check_outbound

# A retrieved passage, in the shape search_literature returns.
CARDEVIA = [{"document": "Cardevia (Cardevastatin) 10 mg / 20 mg tablet", "section": "4.2 Posology"}]


def _rules(verdict: dict) -> set[str]:
    return {f["rule"] for f in verdict["findings"]}


def test_a_clean_cited_draft_comes_back_clear():
    """THE CALIBRATION TEST, and the most important one in this file.

    check_grounding's docstring already learned this the hard way: a guardrail
    that fires on a correct draft is worse than not shipping it, because it
    teaches the reader to dismiss the one that matters. Every rule added below
    has to keep this passing.
    """
    draft = (
        "Dear Dr Sharma, thank you for your time today. As promised, the renal "
        "guidance: for eGFR 30-59 mL/min the maximum is 20 mg once daily "
        "[Cardevia, 4.2 Posology]. Happy to bring the dosing card next visit."
    )
    verdict = check_outbound(draft, CARDEVIA)
    assert verdict["verdict"] == "clear", verdict["findings"]
    assert verdict["findings"] == []


def test_a_comparative_superiority_claim_is_blocked():
    """Detailing guide, 4. What must not be said: "No comparative superiority
    claim against any named competitor product." """
    verdict = check_outbound(
        "Cardevia is more effective than the alternative you are using [Cardevia, 4.2].",
        CARDEVIA,
    )
    assert verdict["verdict"] == "block"
    assert "comparative_superiority" in _rules(verdict)


def test_an_implied_superiority_claim_with_no_competitor_named_is_caught():
    """A comparison does not have to name a product to be one."""
    verdict = check_outbound("It is the only statin with this titration schedule.", CARDEVIA)
    assert "comparative_superiority" in _rules(verdict)


def test_a_pregnancy_claim_is_blocked_and_routed_to_medical_information():
    """Detailing guide: "No use in pregnancy, paediatrics, or any indication
    outside section 4.1 of the SmPC."

    This is also the case that fuses the feature with the corpus's deliberate
    absences: 14 of the 15 monographs have no pregnancy section at all, so there
    is nothing to cite even if it were permitted.
    """
    verdict = check_outbound("It can be continued safely in pregnancy.", CARDEVIA)
    assert verdict["verdict"] == "block"
    assert "off_label" in _rules(verdict)
    assert verdict["requires_escalation"] == "medical_information"


def test_dosing_above_the_licensed_maximum_is_blocked():
    """Detailing guide: "No discussion of unlicensed dosing above 40 mg daily." """
    verdict = check_outbound("Some prescribers go to 80 mg daily [Cardevia, 4.2].", CARDEVIA)
    assert "unlicensed_dosing" in _rules(verdict)


def test_a_dose_inside_the_licence_is_not_flagged():
    """The other half of the dosing rule. 20 mg is licensed; flagging it would
    make the check useless for the drug it is meant to protect."""
    verdict = check_outbound("The maximum is 20 mg once daily [Cardevia, 4.2 Posology].", CARDEVIA)
    assert "unlicensed_dosing" not in _rules(verdict)


def test_assessing_causality_on_an_adverse_event_thread_is_blocked():
    """SOP-PV-01, 2.3: "Do not assess whether the product caused the event." """
    verdict = check_outbound(
        "The rash is unrelated to the drug, so please continue.",
        CARDEVIA,
        thread_text="My patient developed a rash after starting it.",
    )
    assert verdict["verdict"] == "block"
    assert "adverse_event_routing" in _rules(verdict)
    assert verdict["requires_escalation"] == "pharmacovigilance"


def test_advising_on_management_of_an_adverse_event_is_blocked():
    """SOP-PV-01, 2.3: "Do not advise the prescriber or patient on management." """
    verdict = check_outbound(
        "Please reduce the dose to 10 mg and see if it settles.",
        CARDEVIA,
        thread_text="The patient reports a reaction.",
    )
    assert "adverse_event_routing" in _rules(verdict)


def test_an_adverse_event_thread_answered_correctly_still_escalates_but_does_not_block():
    """The behaviour we actually want, and it must not be punished.

    Reporting rather than assessing is the correct response, so this draft has to
    survive the check while still carrying the escalation route.
    """
    verdict = check_outbound(
        "Thank you for telling me. I must report this to pharmacovigilance within "
        "24 hours and cannot comment on the cause or on management.",
        CARDEVIA,
        thread_text="My patient had a reaction.",
    )
    assert verdict["verdict"] == "clear", verdict["findings"]
    assert verdict["requires_escalation"] == "pharmacovigilance"


def test_more_than_six_samples_is_blocked():
    """SOP-PV-01, 3: "Maximum per prescriber per call - 6 units." """
    verdict = check_outbound("I will drop 12 samples with your receptionist.", CARDEVIA)
    assert "sample_limit" in _rules(verdict)


def test_a_patient_contact_number_is_blocked():
    """SOP-PV-01, 2.3: "Do not record the patient's name, address or contact
    details." """
    verdict = check_outbound("The patient can be reached on 9876543210.", CARDEVIA)
    assert "patient_identifiable" in _rules(verdict)


def test_a_clinical_claim_with_no_retrieved_passage_is_uncited():
    """The outbound half of "cite or refuse"."""
    verdict = check_outbound("Cardevia reduces LDL by 38% in the elderly.", [])
    assert "uncited_clinical_claim" in _rules(verdict)
    assert verdict["verdict"] == "block"


def test_an_honest_refusal_is_not_punished_for_lacking_a_citation():
    """Reuses check_citations' _NO_CLAIM_MARKERS unchanged.

    Demanding a citation for "the approved literature does not cover this" would
    push the model towards inventing one — the exact opposite of the intent.
    """
    verdict = check_outbound(
        "The approved literature does not cover that, so I will raise a Medical "
        "Information request and come back to you.",
        CARDEVIA,
    )
    assert "uncited_clinical_claim" not in _rules(verdict)


def test_every_finding_quotes_words_that_are_actually_in_the_draft():
    """A finding the card cannot highlight is a finding the rep cannot check.

    Also the property the LLM reviewer's output is filtered against, so it is
    worth holding the deterministic half to it too.
    """
    draft = (
        "Cardevia is better than the alternative, is safe in pregnancy, and I will "
        "leave 12 samples. Reach the patient on 9876543210."
    )
    verdict = check_outbound(draft, CARDEVIA, thread_text="")
    assert verdict["findings"]
    for finding in verdict["findings"]:
        assert finding["quote"] in draft, finding
        assert finding["basis"], finding
        assert finding["guidance"], finding


def test_a_refusal_is_clear_even_when_nothing_was_retrieved_at_all():
    """The pair to the test above.

    An empty `retrieved` makes a CLAIM untraceable, but it must not make a
    refusal wrong — otherwise the only safe move left is to invent a citation.
    """
    verdict = check_outbound(
        "I could not find that in the approved literature, so I will raise a "
        "Medical Information request rather than guess at 38% or any other figure.",
        [],
    )
    assert "uncited_clinical_claim" not in _rules(verdict)
