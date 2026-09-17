"""
Phase 6: derive facts that aren't stated verbatim.

(a) ConceptTriggerRule  -- a deployer-reviewed fixed rule: metformin implies likely T2DM
(b) AbsenceRule         -- an HIV screening panel with no HIV mention -> scoped negative;
                           an unrelated note correctly does NOT fire
(c) MedRtTriggerRule    -- a KB lookup over a tiny local demo table, including a
                           deliberately ambiguous drug (never silently picks one)
(d) is_stale            -- a 6-month-old vital, checked against a caller-supplied, cited
                           DecayRule (verichart ships no default windows)

No external service or download.

    python examples/inference_rules.py
"""
import tempfile
from pathlib import Path

from verichart.facts import make_fact
from verichart.reasoning import (
    AbsenceRule,
    ConceptTriggerRule,
    DecayRule,
    MedRtTriggerRule,
    apply_rules,
    is_stale,
    load_indication_relations,
)


def _fact(**over):
    base = dict(
        fact_id=over.get("fact_id", "f"), patient_pseudonym="pt_1", label="MEDICATION",
        concept_code=None, concept_system=None, concept_display=None,
        value="x", value_normalized=None, effective_date=None, assertion_status="confirmed",
        provenance_type="direct", span=None, supporting_spans=[],
        extraction_model=None, extraction_model_digest=None, extraction_confidence=0.9,
        conflict_set_id=None, resolution_method="none", resolver_id=None, rule_version=None,
        manifest_id=None, terminology_version=None, note=None,
        created_at="2026-01-01T00:00:00+00:00",
    )
    base.update(over)
    return base


def _span(doc_id, source_type):
    return {"doc_id": doc_id, "source_type": source_type, "char_start": 0, "char_end": 1,
           "text": "x", "provenance_type": "direct"}


# --- (a) ConceptTriggerRule ---

metformin_implies_t2dm = ConceptTriggerRule(
    name="metformin_implies_t2dm", version="v1",             # reviewed and named by YOU
    trigger_concept=("RxNorm", "6809"),
    infer=dict(label="PROBLEM", value="type 2 diabetes mellitus (inferred)",
              concept_system="SNOMED-CT", concept_code="44054006"),
    confidence=0.7,
)

# --- (b) AbsenceRule ---

hiv_panel_absence = AbsenceRule(
    name="hiv_panel_absence", version="v1",
    concept=("LOINC", "5221-7"),
    applies_to_source_types={"hiv_screening_panel"},
    infer=dict(label="PROBLEM", value="HIV negative (inferred — absent from screening panel)",
              concept_system="SNOMED-CT", concept_code="165816005"),
    confidence=0.85,
)

facts = [
    _fact(fact_id="med1", label="MEDICATION", value="metformin",
         concept_code="6809", concept_system="RxNorm"),
    # this panel doesn't mention HIV -> absence fires
    _fact(fact_id="lab1", label="LAB", value="glucose", concept_code="2345-7",
         concept_system="LOINC", span=_span("panel1", "hiv_screening_panel")),
    # an unrelated note also doesn't mention HIV -> must NOT fire
    _fact(fact_id="prob1", label="PROBLEM", value="chest pain",
         span=_span("cardio1", "cardiology_note")),
]

after_rules = apply_rules(facts, [metformin_implies_t2dm, hiv_panel_absence])
print(f"(a)+(b) {len(after_rules)} facts after inference rules "
     f"(started with {len(facts)}):\n")
for f in after_rules:
    tag = "inferred" if f["provenance_type"] == "inferred" else "stated"
    print(f"  [{tag:<8}] {f['label']:<11} {f['value']!r}")
    if f["note"]:
        print(f"  {'':<10} {f['note']}")

# --- (c) MedRtTriggerRule: never collapses ambiguity ---

db = Path(tempfile.gettempdir()) / "verichart_demo_indications.db"
load_indication_relations([
    ("RxNorm", "6809", "may_treat", "SNOMED-CT", "44054006", "Diabetes mellitus type 2"),
    # a deliberately ambiguous drug: two real indications
    ("RxNorm", "6373", "may_treat", "SNOMED-CT", "38341003", "Hypertension"),
    ("RxNorm", "6373", "may_treat", "SNOMED-CT", "84114007", "Heart failure"),
], str(db))

medrt_rule = MedRtTriggerRule(str(db), version="2026.07.06", confidence=0.6)
ambiguous_trigger = _fact(fact_id="med2", label="MEDICATION", value="lisinopril",
                          concept_code="6373", concept_system="RxNorm")
candidates = medrt_rule.apply([ambiguous_trigger])

print(f"\n(c) MED-RT lookup on an ambiguous drug -> {len(candidates)} candidate facts "
     f"(never silently picks one):\n")
for f in candidates:
    print(f"  {f['value']!r}  confidence={f['extraction_confidence']:.2f}")
    print(f"  {f['note']}")

# --- (d) is_stale: verichart ships no default windows ---

# A caller-supplied, cited window -- NOT a verichart default (see docs/reasoning.md).
weight_decay = DecayRule(
    concept_key="LOINC:29463-7",  # body weight
    max_age_days=90,
    source="internal clinical review, 2026-09-17 — illustrative, not a universal standard",
)
old_weight = _fact(fact_id="w1", label="VITAL", value="82 kg",
                   concept_code="29463-7", concept_system="LOINC",
                   effective_date="2026-03-01")

print(f"\n(d) is_stale (checked as of 2026-09-17, ~200 days after the reading):")
print(f"  no table configured -> {is_stale(old_weight, '2026-09-17')} (verichart's safe default)")
print(f"  with a caller-supplied, cited DecayRule -> "
     f"{is_stale(old_weight, '2026-09-17', table=[weight_decay])}")
