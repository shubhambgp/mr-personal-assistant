"""Detailing aids and one SOP — the non-monograph half of the corpus.

Two reasons these exist rather than only monographs:

  * They are what an objection-handling question actually retrieves from, and
    they fuse with the structured data ("he is a P1 loyalist whose Cardevia RCPA
    fell 12%" is SQL; "the approved response to a price objection" is this).
  * They are Word documents in real life, so some are here, which is what makes
    the DOCX branch of the parser exercised rather than notional.

ENTIRELY FICTIONAL — see etl/literature/brands.py.
"""

from __future__ import annotations

DETAILING_AIDS: list[dict] = [
    {
        "slug": "cardevia-detailing-guide",
        "title": "Cardevia Detailing Guide — Cardiology",
        "brand": "Cardevia",
        "molecule": "Cardevastatin",
        "format": "docx",
        "body": """
## 1. Key messages

- Cardevia reduces LDL cholesterol by a mean of 42% at 20 mg in the QORVEX-1 study.
- Once-daily evening dosing supports adherence in patients already on multiple therapies.
- A 10 mg starting dose is appropriate for patients over 65 or on ciclosporin.

## 2. Approved responses to common objections

| Objection | Approved response |
|---|---|
| "The competitor product is cheaper." | Acknowledge cost, then move to total therapy value: Cardevia's once-daily evening dose and 4-weekly titration schedule mean fewer review visits. Do not make comparative price claims that are not in the approved materials. |
| "I am not convinced about efficacy in elderly patients." | The QORVEX-1 subgroup of patients over 65 showed a mean LDL reduction of 38% at 20 mg. Start at 10 mg and monitor renal function at 8 weeks, as in section 4.2 of the SmPC. |
| "I have seen muscle pain with statins." | Myalgia is a common undesirable effect. Advise the patient to report unexplained muscle pain, and check creatine kinase. Do not co-prescribe gemfibrozil. |
| "My patients are already stable on something else." | Do not ask for a switch of stable patients. Position Cardevia for new initiations and for patients not at target after 12 weeks. |

## 3. Segment guidance

- **P1 loyalists:** lead with continuity of supply and the patient support programme, not price.
- **High-volume, low-loyalty prescribers:** lead with the titration schedule and review burden.
- **Digitally engaged prescribers:** offer the dosing reference card by email rather than in print.

## 4. What must not be said

- No comparative superiority claim against any named competitor product.
- No use in pregnancy, paediatrics, or any indication outside section 4.1 of the SmPC.
- No discussion of unlicensed dosing above 40 mg daily.
""",
    },
    {
        "slug": "hepatoval-detailing-guide",
        "title": "Hepatoval Detailing Guide — Hepatology",
        "brand": "Hepatoval",
        "molecule": "Hepatovaline",
        "format": "docx",
        "body": """
## 1. Key messages

- Hepatoval is positioned as adjunctive therapy in non-alcoholic fatty liver disease.
- Twice-daily dosing before meals; the enteric coating must not be broken.
- Dose is halved in Child-Pugh B, with weekly ammonia monitoring in the first month.

## 2. Approved responses to common objections

| Objection | Approved response |
|---|---|
| "There is no outcome data." | Be direct: the licensed indication is adjunctive and symptomatic. Do not imply a hard outcome benefit. Offer the QORVEX-LIVER surrogate-endpoint summary. |
| "My patients take paracetamol regularly." | Additive glutathione depletion above 2 g/day of paracetamol. Advise the prescriber to counsel patients to limit paracetamol; this is in section 4.5. |
| "Twice daily is a problem for adherence." | Once-daily 20 mg is the licensed elderly regimen, and may be considered where adherence is the limiting factor. |

## 3. Segment guidance

- **Gastroenterologists:** lead with the Child-Pugh dosing table; they will ask for it.
- **General physicians:** lead with the paracetamol counselling point, which is practical and memorable.

## 4. What must not be said

- No claim of cirrhosis prevention or mortality benefit.
- Not for use in decompensated cirrhosis (Child-Pugh C) under any circumstances.
""",
    },
    {
        "slug": "objection-handling-general",
        "title": "Field Objection Handling — General Principles",
        "brand": None,
        "molecule": None,
        "format": "docx",
        "body": """
## 1. The four-step method

1. **Acknowledge** the objection in the prescriber's own words. Do not defend immediately.
2. **Clarify** what is actually being asked. "Expensive" may mean cost to patient, cost to the practice, or perceived value.
3. **Respond** using only approved material. If you do not have an approved response, say so and raise a Medical Information request.
4. **Confirm** agreement on a next step before leaving.

## 2. Objections you must not answer yourself

| Objection type | Required action |
|---|---|
| Any question about unlicensed use or dosing | Raise a Medical Information request. Do not answer, even if you know the answer. |
| Any question about a specific patient's management | Decline. Clinical management is the prescriber's decision. |
| Any comparative efficacy claim not in approved material | Decline and offer the approved comparative summary if one exists. |
| Any report of a suspected adverse event | Follow the pharmacovigilance SOP. Report within 24 hours. Do not assess causality. |

## 3. Segment-based framing

- **Loyal, high-value prescribers:** continuity, supply reliability, patient support. Price last.
- **Declining prescribers:** ask what changed before presenting anything. A declining RCPA usually has a reason the prescriber will tell you if asked.
- **New prescribers:** one message, one product, one next step.
""",
    },
    {
        "slug": "pharmacovigilance-sop",
        "title": "SOP-PV-01: Adverse Event and Product Complaint Reporting",
        "brand": None,
        "molecule": None,
        "format": "pdf",
        "body": """
## 1. Purpose and scope

This procedure applies to every field-force employee of Qorvexa Healthcare. It
covers suspected adverse events, product quality complaints and special
situations, for all Qorvexa products.

## 2.1 What must be reported

Any suspected adverse event of which you become aware, whether or not you
believe the product caused it. Causality is not your assessment to make.

| Situation | Report within | Route |
|---|---|---|
| Suspected adverse event | 24 hours | Pharmacovigilance mailbox and your line manager |
| Pregnancy exposure | 24 hours | Pharmacovigilance mailbox |
| Suspected product quality defect | 3 working days | Quality Assurance |
| Off-label use mentioned by a prescriber | 5 working days | Medical Information |

## 2.2 What to record

- Product name and strength, and batch number if available.
- Patient identifier as given by the reporter, age band and sex. Do not collect
  or write down any additional identifying information.
- Description of the event in the reporter's words.
- Reporter name and contact details, and whether they consent to follow-up.

## 2.3 What you must not do

- Do not assess whether the product caused the event.
- Do not advise the prescriber or patient on management of the event.
- Do not delay reporting to gather more information. Report what you have.
- Do not record the patient's name, address or contact details.

## 3. Sample distribution

| Rule | Detail |
|---|---|
| Maximum per prescriber per call | 6 units |
| Record required | Product, strength, quantity, date, prescriber signature |
| Retention | Sample records are retained for 5 years |
| Prohibited | Samples must never be supplied to a person who is not a qualified prescriber |
""",
    },
]
