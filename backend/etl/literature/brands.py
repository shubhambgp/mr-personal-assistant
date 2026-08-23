"""Per-brand facts for the synthetic literature corpus.

ENTIRELY FICTIONAL. Qorvexa Healthcare does not exist, none of these brands or
molecules exist, and every clinical statement here is invented. Real prescribing
information is not used, for two reasons: it is somebody's copyright, and an
assistant answering dosing questions out of a demo corpus must never be
mistakable for a real medical tool. Every rendered page carries that notice.

The facts are deliberately *differentiated* rather than templated. A corpus where
every monograph says the same thing with a different name makes retrieval
evaluation meaningless — every query would match everything equally, and
recall@5 would measure nothing. So each brand has its own molecule, class,
renal threshold, interaction set and adverse-event profile.

Two omissions are deliberate and load-bearing:
  * `pregnancy` is absent for several brands. That is what makes the "the
    approved literature does not cover this — I cannot infer it" behaviour
    testable with a question that genuinely has no answer in the corpus.
  * `paediatric` is absent for most. Same reason.
"""

from __future__ import annotations

BRANDS: dict[str, dict] = {
    "Cardevia": {
        "molecule": "Cardevastatin",
        "strengths": ["10 mg", "20 mg", "40 mg"],
        "form": "film-coated tablet",
        "class": "HMG-CoA reductase inhibitor",
        "area": "Cardiology",
        "indications": [
            "Primary hypercholesterolaemia in adults inadequately controlled by diet alone.",
            "Reduction of cardiovascular risk in adults with established atherosclerotic disease.",
        ],
        "dosing": [
            ("Adults, initial", "10 mg once daily", "Evening dose, with or without food"),
            ("Adults, maintenance", "20–40 mg once daily", "Titrate at 4-week intervals"),
            ("Elderly (>65 years)", "10 mg once daily", "Monitor renal function at 8 weeks"),
            ("With ciclosporin", "10 mg once daily", "Do not exceed 10 mg"),
        ],
        "renal": "eGFR 30–59 mL/min: maximum 20 mg once daily. eGFR below 30 mL/min: "
                 "maximum 10 mg once daily and monitor creatine kinase every 12 weeks.",
        "hepatic": "Contraindicated in active hepatic disease or persistent transaminase "
                   "elevation above three times the upper limit of normal.",
        "interactions": [
            ("Ciclosporin", "Marked increase in Cardevastatin exposure. Restrict to 10 mg daily."),
            ("Gemfibrozil", "Increased myopathy risk. Concomitant use not recommended."),
            ("Clarithromycin", "Suspend Cardevia for the duration of the antibiotic course."),
            ("Metformin", "No clinically significant interaction has been observed. "
                          "Monitor renal function in patients over 65 years."),
            ("Warfarin", "Monitor INR when initiating or changing dose."),
        ],
        "contraindications": [
            "Hypersensitivity to Cardevastatin or any excipient.",
            "Active hepatic disease.",
            "Concomitant use of gemfibrozil.",
        ],
        "adverse": [
            ("Common (1–10%)", "Headache, myalgia, nausea, elevated transaminases"),
            ("Uncommon (0.1–1%)", "Rash, dizziness, insomnia"),
            ("Rare (<0.1%)", "Rhabdomyolysis, immune-mediated necrotising myopathy"),
        ],
        "storage": "Store below 25 °C in the original blister. Do not refrigerate.",
        # pregnancy: deliberately omitted — see the module docstring.
    },
    "Hepatoval": {
        "molecule": "Hepatovaline",
        "strengths": ["10 mg", "20 mg"],
        "form": "enteric-coated tablet",
        "class": "hepatoprotective aminothiol",
        "area": "Hepatology",
        "indications": [
            "Adjunctive treatment of non-alcoholic fatty liver disease in adults.",
            "Supportive therapy during hepatotoxic chemotherapy regimens.",
        ],
        "dosing": [
            ("Adults", "20 mg twice daily", "Before meals; swallow whole"),
            ("Elderly", "20 mg once daily", "Increase only if tolerated at 6 weeks"),
            ("Child-Pugh A", "20 mg once daily", "No further adjustment"),
            ("Child-Pugh B", "10 mg once daily", "Monitor ammonia weekly"),
        ],
        "renal": "No dose adjustment required down to eGFR 30 mL/min. Below 30 mL/min, "
                 "safety has not been established.",
        "hepatic": "Child-Pugh C: not recommended. Child-Pugh B: halve the dose and "
                   "monitor serum ammonia weekly for the first month.",
        "interactions": [
            ("Paracetamol", "Additive glutathione depletion at doses above 2 g/day. "
                            "Advise patients to limit paracetamol."),
            ("Rifampicin", "Reduces Hepatovaline exposure by approximately 40%."),
            ("Oral contraceptives", "No interaction observed."),
        ],
        "contraindications": [
            "Hypersensitivity to Hepatovaline.",
            "Decompensated cirrhosis (Child-Pugh C).",
        ],
        "adverse": [
            ("Common (1–10%)", "Nausea, abdominal discomfort, transient pruritus"),
            ("Uncommon (0.1–1%)", "Taste disturbance, mild neutropenia"),
        ],
        "storage": "Store below 30 °C. Protect from moisture.",
        "pregnancy": "Not recommended during pregnancy. Animal studies showed no "
                     "teratogenicity, but human data are insufficient. If treatment is "
                     "essential, discuss with a hepatologist before continuing.",
    },
    "Thyrolen": {
        "molecule": "Thyrolenamide",
        "strengths": ["20 mg", "40 mg"],
        "form": "scored tablet",
        "class": "thyroid peroxidase modulator",
        "area": "Endocrinology",
        "indications": ["Adjunct management of subclinical hypothyroidism in adults."],
        "dosing": [
            ("Adults, initial", "20 mg once daily", "On an empty stomach, 30 min before breakfast"),
            ("Adults, titration", "40 mg once daily", "After 8 weeks if TSH remains elevated"),
            ("Elderly", "20 mg once daily", "Do not titrate before 12 weeks"),
        ],
        "renal": "No adjustment required at any degree of renal impairment.",
        "hepatic": "Reduce to alternate-day dosing in moderate impairment.",
        "interactions": [
            ("Calcium carbonate", "Separate administration by at least 4 hours; "
                                  "absorption is reduced by up to 30%."),
            ("Ferrous sulfate", "Separate administration by at least 4 hours."),
            ("Levothyroxine", "Do not co-administer; effects are not additive and "
                              "over-replacement may result."),
        ],
        "contraindications": [
            "Untreated thyrotoxicosis.",
            "Hypersensitivity to Thyrolenamide.",
        ],
        "adverse": [
            ("Common (1–10%)", "Palpitations, heat intolerance, mild weight loss"),
            ("Uncommon (0.1–1%)", "Anxiety, tremor"),
        ],
        "storage": "Store below 25 °C, protected from light.",
    },
    "Nephrocine": {
        "molecule": "Nephrocitine",
        "strengths": ["10 mg", "20 mg"],
        "form": "prolonged-release tablet",
        "class": "renal tubular protectant",
        "area": "Nephrology",
        "indications": [
            "Slowing of progression in adults with chronic kidney disease stages 2–3.",
        ],
        "dosing": [
            ("eGFR ≥ 60 mL/min", "20 mg once daily", "Morning, with food"),
            ("eGFR 45–59 mL/min", "10 mg once daily", "Reassess at 12 weeks"),
            ("eGFR 30–44 mL/min", "10 mg alternate days", "Monitor potassium fortnightly"),
            ("eGFR < 30 mL/min", "Not recommended", "Insufficient data"),
        ],
        "renal": "Dosing is defined by eGFR band; see section 4.2. Potassium must be "
                 "checked before initiation and at 2, 4 and 12 weeks.",
        "hepatic": "No adjustment required.",
        "interactions": [
            ("ACE inhibitors", "Additive hyperkalaemia risk. Check potassium at 2 weeks."),
            ("NSAIDs", "May blunt the renoprotective effect. Avoid chronic use."),
            ("Metformin", "No pharmacokinetic interaction. Follow standard metformin "
                          "renal thresholds independently."),
        ],
        "contraindications": [
            "Serum potassium above 5.5 mmol/L at baseline.",
            "Acute kidney injury.",
        ],
        "adverse": [
            ("Common (1–10%)", "Hyperkalaemia, fatigue, mild hypotension"),
            ("Uncommon (0.1–1%)", "Metallic taste, peripheral oedema"),
        ],
        "storage": "Store below 30 °C in the original container.",
    },
    "Dermaxen": {
        "molecule": "Dermaxenol",
        "strengths": ["5 mg", "10 mg"],
        "form": "tablet",
        "class": "selective histamine H1 antagonist",
        "area": "Dermatology",
        "indications": [
            "Symptomatic relief of chronic spontaneous urticaria in adults and "
            "adolescents over 12 years.",
        ],
        "dosing": [
            ("Adults", "10 mg once daily", "Any time of day"),
            ("Adolescents 12–17 years", "5 mg once daily", "May increase to 10 mg after 4 weeks"),
            ("Elderly", "5 mg once daily", "Sedation risk is dose-related"),
        ],
        "renal": "eGFR below 30 mL/min: 5 mg once daily.",
        "hepatic": "No adjustment required in mild impairment.",
        "interactions": [
            ("Alcohol", "Additive sedation. Advise patients accordingly."),
            ("Ketoconazole", "Increases Dermaxenol exposure; halve the dose."),
        ],
        "contraindications": ["Hypersensitivity to Dermaxenol."],
        "adverse": [
            ("Common (1–10%)", "Somnolence, dry mouth, headache"),
            ("Uncommon (0.1–1%)", "Rash, tachycardia"),
        ],
        "storage": "Store below 25 °C.",
        "paediatric": "Not recommended below 12 years of age; efficacy has not been "
                      "established in this group.",
    },
    "Neurotane": {
        "molecule": "Neurotanide", "strengths": ["25 mg", "50 mg"], "form": "capsule",
        "class": "voltage-gated sodium channel modulator", "area": "Neurology",
        "indications": ["Adjunctive therapy for focal seizures in adults."],
        "dosing": [
            ("Adults, initial", "25 mg twice daily", "Titrate weekly"),
            ("Adults, maintenance", "50 mg twice daily", "Maximum 100 mg/day"),
        ],
        "renal": "eGFR below 45 mL/min: reduce the daily dose by half.",
        "hepatic": "Avoid in moderate to severe impairment.",
        "interactions": [
            ("Carbamazepine", "Mutual induction; monitor both levels."),
            ("Oral contraceptives", "Reduced contraceptive efficacy. Advise additional precautions."),
        ],
        "contraindications": ["Second- or third-degree heart block."],
        "adverse": [("Common (1–10%)", "Dizziness, diplopia, ataxia"),
                    ("Rare (<0.1%)", "Severe cutaneous reactions")],
        "storage": "Store below 25 °C.",
    },
    "Pulmoclear": {
        "molecule": "Pulmoclearine", "strengths": ["100 mcg", "200 mcg"],
        "form": "inhalation powder", "class": "long-acting beta-2 agonist",
        "area": "Respiratory",
        "indications": ["Maintenance bronchodilation in adults with COPD."],
        "dosing": [
            ("Adults", "200 mcg once daily", "Same time each day, by inhalation"),
            ("Elderly", "200 mcg once daily", "No adjustment"),
        ],
        "renal": "No adjustment required.",
        "hepatic": "No adjustment required.",
        "interactions": [
            ("Beta-blockers", "May antagonise the bronchodilator effect. "
                              "Use cardioselective agents where beta-blockade is required."),
            ("Diuretics", "Additive hypokalaemia risk."),
        ],
        "contraindications": ["Hypersensitivity to milk proteins (lactose carrier)."],
        "adverse": [("Common (1–10%)", "Tremor, headache, oropharyngeal candidiasis"),
                    ("Uncommon (0.1–1%)", "Palpitations, muscle cramps")],
        "storage": "Store below 25 °C. Discard 6 weeks after opening the foil.",
    },
    "Rheumafix": {
        "molecule": "Rheumafixib", "strengths": ["50 mg", "100 mg"], "form": "tablet",
        "class": "selective COX-2 inhibitor", "area": "Rheumatology",
        "indications": ["Symptomatic relief of osteoarthritis and rheumatoid arthritis in adults."],
        "dosing": [
            ("Osteoarthritis", "50 mg once daily", "Lowest effective dose, shortest duration"),
            ("Rheumatoid arthritis", "100 mg once daily", "Review at 12 weeks"),
        ],
        "renal": "eGFR below 30 mL/min: contraindicated.",
        "hepatic": "Child-Pugh B: halve the dose. Child-Pugh C: contraindicated.",
        "interactions": [
            ("Warfarin", "Increased bleeding risk. Monitor INR closely."),
            ("ACE inhibitors", "Reduced antihypertensive effect and increased renal risk."),
            ("Low-dose aspirin", "Gastrointestinal risk is additive; consider gastroprotection."),
        ],
        "contraindications": ["Established ischaemic heart disease.",
                              "Active gastrointestinal bleeding.", "eGFR below 30 mL/min."],
        "adverse": [("Common (1–10%)", "Dyspepsia, oedema, hypertension"),
                    ("Rare (<0.1%)", "Myocardial infarction, gastrointestinal perforation")],
        "storage": "Store below 30 °C.",
    },
    "Osteovim": {
        "molecule": "Osteovimide", "strengths": ["35 mg", "70 mg"], "form": "tablet",
        "class": "bisphosphonate", "area": "Orthopaedics",
        "indications": ["Treatment of postmenopausal osteoporosis to reduce fracture risk."],
        "dosing": [
            ("Adults", "70 mg once weekly", "On rising, with 200 mL plain water; "
                                            "remain upright for 30 minutes"),
            ("Alternative", "35 mg twice weekly", "Where weekly dosing is not tolerated"),
        ],
        "renal": "eGFR below 35 mL/min: not recommended.",
        "hepatic": "No adjustment required.",
        "interactions": [
            ("Calcium supplements", "Separate by at least 2 hours; absorption is "
                                    "substantially reduced."),
            ("Antacids", "Separate by at least 2 hours."),
        ],
        "contraindications": ["Oesophageal stricture or achalasia.",
                              "Inability to remain upright for 30 minutes.",
                              "Hypocalcaemia."],
        "adverse": [("Common (1–10%)", "Oesophagitis, abdominal pain, musculoskeletal pain"),
                    ("Rare (<0.1%)", "Osteonecrosis of the jaw, atypical femoral fracture")],
        "storage": "Store below 25 °C in the original blister.",
    },
    "Gastroliv": {
        "molecule": "Gastrolivone", "strengths": ["20 mg", "40 mg"],
        "form": "gastro-resistant capsule", "class": "proton pump inhibitor",
        "area": "Gastroenterology",
        "indications": ["Gastro-oesophageal reflux disease in adults.",
                        "Prevention of NSAID-associated gastric ulcer."],
        "dosing": [
            ("GORD", "20 mg once daily", "Before breakfast, for 4–8 weeks"),
            ("Ulcer prophylaxis", "20 mg once daily", "For the duration of NSAID therapy"),
            ("Severe oesophagitis", "40 mg once daily", "Review at 8 weeks"),
        ],
        "renal": "No adjustment required.",
        "hepatic": "Severe impairment: maximum 20 mg daily.",
        "interactions": [
            ("Clopidogrel", "Reduced antiplatelet activity. Prefer an alternative acid "
                            "suppressant in patients on clopidogrel."),
            ("Methotrexate", "Increased methotrexate levels at high doses."),
        ],
        "contraindications": ["Hypersensitivity to substituted benzimidazoles."],
        "adverse": [("Common (1–10%)", "Headache, diarrhoea, flatulence"),
                    ("Uncommon (0.1–1%)", "Hypomagnesaemia on prolonged use")],
        "storage": "Store below 30 °C.",
    },
    "Betaprol": {
        "molecule": "Betaprolol", "strengths": ["2.5 mg", "5 mg", "10 mg"], "form": "tablet",
        "class": "cardioselective beta-blocker", "area": "Cardiology",
        "indications": ["Essential hypertension in adults.",
                        "Stable chronic heart failure as adjunct to standard therapy."],
        "dosing": [
            ("Hypertension", "5 mg once daily", "May increase to 10 mg after 2 weeks"),
            ("Heart failure, initial", "2.5 mg once daily", "Double at 2-week intervals if tolerated"),
            ("Heart failure, target", "10 mg once daily", "Do not up-titrate during decompensation"),
        ],
        "renal": "eGFR below 20 mL/min: maximum 5 mg daily.",
        "hepatic": "Severe impairment: maximum 5 mg daily.",
        "interactions": [
            ("Verapamil", "Risk of severe bradycardia and AV block. Avoid combination."),
            ("Insulin", "May mask hypoglycaemic warning signs. Counsel diabetic patients."),
        ],
        "contraindications": ["Cardiogenic shock.", "Second- or third-degree AV block.",
                              "Severe asthma."],
        "adverse": [("Common (1–10%)", "Bradycardia, fatigue, cold extremities"),
                    ("Uncommon (0.1–1%)", "Sleep disturbance, worsening claudication")],
        "storage": "Store below 25 °C.",
    },
    "Immunoza": {
        "molecule": "Immunozalin", "strengths": ["5 ml suspension"], "form": "oral suspension",
        "class": "immunomodulator", "area": "Paediatrics",
        "indications": ["Adjunct in recurrent upper respiratory tract infection in "
                        "children aged 2–12 years."],
        "dosing": [
            ("2–5 years", "2.5 mL once daily", "For 10 consecutive days per month"),
            ("6–12 years", "5 mL once daily", "For 10 consecutive days per month"),
        ],
        "renal": "Not studied; avoid in known renal impairment.",
        "hepatic": "Not studied; avoid in known hepatic impairment.",
        "interactions": [("Live vaccines", "Separate administration by 4 weeks.")],
        "contraindications": ["Autoimmune disease.", "Age under 2 years."],
        "adverse": [("Common (1–10%)", "Mild gastrointestinal upset, transient rash")],
        "storage": "Store below 25 °C. Use within 28 days of opening.",
        "paediatric": "Licensed from 2 years of age. Dose by age band as in section 4.2. "
                      "Shake well before each administration.",
    },
    "Painzeal": {
        "molecule": "Painzealide", "strengths": ["50 mg", "100 mg"], "form": "tablet",
        "class": "centrally acting analgesic", "area": "Pain management",
        "indications": ["Moderate acute pain in adults where NSAIDs are unsuitable."],
        "dosing": [("Adults", "50 mg every 6 hours as required", "Maximum 300 mg in 24 hours"),
                   ("Elderly", "50 mg every 8 hours", "Maximum 200 mg in 24 hours")],
        "renal": "eGFR below 30 mL/min: maximum 100 mg in 24 hours.",
        "hepatic": "Moderate impairment: maximum 100 mg in 24 hours.",
        "interactions": [
            ("SSRIs", "Serotonin syndrome risk. Avoid where possible."),
            ("Alcohol", "Additive CNS depression."),
        ],
        "contraindications": ["Concomitant MAO inhibitor use.", "Uncontrolled epilepsy."],
        "adverse": [("Common (1–10%)", "Nausea, dizziness, constipation"),
                    ("Rare (<0.1%)", "Seizure, serotonin syndrome")],
        "storage": "Store below 25 °C.",
    },
    "Vitalflow": {
        "molecule": "Vitalflozin", "strengths": ["5 mg", "10 mg"], "form": "tablet",
        "class": "SGLT2 inhibitor", "area": "Diabetology",
        "indications": ["Type 2 diabetes mellitus in adults as adjunct to diet and exercise."],
        "dosing": [("Adults, initial", "5 mg once daily", "Morning, with or without food"),
                   ("Adults, maintenance", "10 mg once daily", "If tolerated after 4 weeks")],
        "renal": "eGFR 45–59 mL/min: do not initiate; may continue at 5 mg. "
                 "eGFR below 45 mL/min: discontinue.",
        "hepatic": "Severe impairment: not recommended.",
        "interactions": [
            ("Metformin", "No dose adjustment required for either agent. The combination "
                          "is a standard regimen; monitor eGFR at least annually."),
            ("Loop diuretics", "Additive volume depletion. Review diuretic dose at initiation."),
            ("Insulin", "Increased hypoglycaemia risk; consider reducing the insulin dose."),
        ],
        "contraindications": ["Type 1 diabetes mellitus.", "History of diabetic ketoacidosis."],
        "adverse": [("Common (1–10%)", "Genital mycotic infection, polyuria, thirst"),
                    ("Rare (<0.1%)", "Euglycaemic diabetic ketoacidosis, Fournier's gangrene")],
        "storage": "Store below 30 °C.",
    },
    "Zentabiox": {
        "molecule": "Zentabioxacin", "strengths": ["250 mg", "500 mg"],
        "form": "film-coated tablet", "class": "fourth-generation cephalosporin analogue",
        "area": "Infectious disease",
        "indications": ["Community-acquired respiratory tract infection in adults.",
                        "Uncomplicated urinary tract infection in adults."],
        "dosing": [("Respiratory infection", "500 mg twice daily", "For 7 days"),
                   ("Urinary infection", "250 mg twice daily", "For 5 days"),
                   ("eGFR 30–50 mL/min", "250 mg twice daily", "Regardless of indication")],
        "renal": "eGFR 30–50 mL/min: 250 mg twice daily. Below 30 mL/min: 250 mg once daily.",
        "hepatic": "No adjustment required.",
        "interactions": [
            ("Warfarin", "Enhanced anticoagulation. Monitor INR during and after the course."),
            ("Oral contraceptives", "No clinically significant interaction."),
        ],
        "contraindications": ["Immediate hypersensitivity to beta-lactams."],
        "adverse": [("Common (1–10%)", "Diarrhoea, nausea, rash"),
                    ("Uncommon (0.1–1%)", "Clostridioides difficile colitis")],
        "storage": "Store below 25 °C.",
    },
}
