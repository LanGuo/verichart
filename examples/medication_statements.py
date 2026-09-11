"""
Phase 4: link medication attributes into one composite ClinicalFact per drug.

Deterministic path — no model, no download:
  MockRecognizer (anchors)  +  AttributeRecognizer (regex attributes)  +  RuleRelationLinker

    python examples/medication_statements.py
"""
from verichart import extract_entities, extract_relations
from verichart.clinical import AttributeRecognizer, MockRecognizer, RuleRelationLinker

NOTE = (
    "MEDICATIONS: Start metformin 500 mg PO twice daily. "
    "Continue lisinopril 10 mg daily. "
    "Hold home aspirin. Patient also has hypertension.\n"
)

# anchors — in real use, GLiNER-BioMed; here a mock so the example needs nothing installed
anchors_rec = MockRecognizer(version="mock@1")
for drug in ("metformin", "lisinopril", "aspirin"):
    anchors_rec.register(drug, label="MEDICATION", score=0.9)
anchors_rec.register("hypertension", label="PROBLEM", score=0.85)

anchor_facts = extract_entities(
    NOTE, recognizer=anchors_rec, labels=["MEDICATION", "PROBLEM"],
    doc_id="note:1", patient_pseudonym="pt_1",
)

facts = extract_relations(
    NOTE,
    recognizer=AttributeRecognizer(),
    anchor_facts=anchor_facts,
    attribute_labels=["STRENGTH", "ROUTE", "FREQUENCY", "FORM"],
    extractor=RuleRelationLinker(),
)

print(f"{len(facts)} facts\n")
for f in facts:
    tag = "composite" if len(f["supporting_spans"]) > 1 else "entity"
    print(f"  [{tag:<9}] {f['label']:<11} {f['value']!r}")
    if len(f["supporting_spans"]) > 1:
        pieces = ", ".join(f"{NOTE[s['char_start']:s['char_end']]!r}" for s in f["supporting_spans"])
        print(f"  {'':<13} provenance={f['provenance_type']}  spans: {pieces}")
        print(f"  {'':<13} {f['note']}")
