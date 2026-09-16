"""
End-to-end: two documents for one patient, through all five phases.

  Phase 2  extract_entities     -> entities + assertion, per document
  Phase 3  resolve_concepts     -> SNOMED / RxNorm codes
  Phase 4  extract_relations    -> medication attributes assembled into one statement
  Phase 5  reconcile            -> the two documents' metformin doses disagree; resolved

Runs with no external service or download: MockRecognizer stands in for GLiNER-BioMed,
AttributeRecognizer and SqliteLookupResolver are regex/lexicon and a throwaway local DB.

    python examples/end_to_end_pipeline.py
"""
import tempfile
from pathlib import Path

from verichart import extract_entities, extract_relations, reconcile, resolve_concepts
from verichart.clinical import AttributeRecognizer, MockRecognizer, RuleRelationLinker
from verichart.clinical.terminology import SqliteLookupResolver, load_vocab_sqlite
from verichart.reconcile import ResolutionPolicy

# --- two documents, same patient, disagreeing on the metformin dose ---

CLINIC_NOTE = (
    "Assessment: type 2 diabetes, currently controlled. "
    "Continue metformin 500 mg PO twice daily. Blood pressure well managed."
)
PHARMACY_FEED = "Dispensed: metformin 1000 mg PO once daily, 90-day supply."

DOCS = {"clinic_note": CLINIC_NOTE, "pharmacy_feed": PHARMACY_FEED}


def _recognizer():
    rec = MockRecognizer(version="mock-anchor@1")
    rec.register("type 2 diabetes", label="PROBLEM", score=0.93)
    rec.register("metformin", label="MEDICATION", score=0.95)
    return rec


# --- a throwaway SNOMED-ish / RxNorm-ish DB — real use: your own licensed release ---

_tmp = Path(tempfile.gettempdir())
snomed_db = _tmp / "e2e_snomed.db"
load_vocab_sqlite([("44054006", "Diabetes mellitus type 2", True),
                   ("44054006", "type 2 diabetes", False)], str(snomed_db))
rxnorm_db = _tmp / "e2e_rxnorm.db"
load_vocab_sqlite([("6809", "Metformin", True)], str(rxnorm_db))

resolvers = [
    SqliteLookupResolver(str(snomed_db), system="SNOMED-CT", version="2026-03"),
    SqliteLookupResolver(str(rxnorm_db), system="RxNorm", version="2024AB"),
]

# --- Phases 2-4, per document ---

all_facts = []
for doc_id, text in DOCS.items():
    entities = extract_entities(
        text, recognizer=_recognizer(), labels=["PROBLEM", "MEDICATION"],
        doc_id=doc_id, source_type=doc_id, patient_pseudonym="pt_1",
    )
    coded = resolve_concepts(entities, resolvers, documents={doc_id: text})
    with_relations = extract_relations(
        text, recognizer=AttributeRecognizer(), anchor_facts=coded,
        attribute_labels=["STRENGTH", "ROUTE", "FREQUENCY"], extractor=RuleRelationLinker(),
    )
    all_facts.extend(with_relations)

print(f"Phase 2-4: {len(all_facts)} facts across {len(DOCS)} documents\n")
for f in all_facts:
    code = f"{f['concept_system']}:{f['concept_code']}" if f["concept_code"] else "unresolved"
    print(f"  [{f['span']['source_type']:<13}] {f['label']:<11} {f['value']!r:<32} {code}")

# --- Phase 5: reconcile across both documents ---

policy = ResolutionPolicy(
    default="highest_confidence",
    by_concept_system={"RxNorm": "source_rank"},   # meds: trust the pharmacy feed over dictation
    source_rank=["pharmacy_feed", "clinic_note"],
    rule_version="v1",
)
reconciled, conflict_sets, resolutions = reconcile(all_facts, policy, documents=DOCS)

print(f"\nPhase 5: {len(conflict_sets)} conflict(s) found")
for cs, res in zip(conflict_sets, resolutions):
    print(f"  {cs['kind']} among {cs['member_fact_ids']}")
    print(f"  -> {res['method']} picked {res['winning_fact_id']!r}: {res['rationale']}")

print(f"\n{len(reconciled)} facts survive reconciliation:")
for f in reconciled:
    print(f"  {f['label']:<11} {f['value']!r:<32} "
          f"conflict_set={'yes' if f['conflict_set_id'] else 'no ':<3} "
          f"resolution={f['resolution_method']}")
