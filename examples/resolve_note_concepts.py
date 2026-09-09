"""
Phase 3: resolve clinical facts to terminology codes.

Uses MockRecognizer + a tiny in-process SqliteLookupResolver so it runs with no
model download and no vocabulary license. Swap in GlinerBiomedRecognizer and a
real vocab DB (see docs/terminology.md) for actual use.

    python examples/resolve_note_concepts.py
"""
import tempfile
from pathlib import Path

from verichart import extract_entities, resolve_concepts
from verichart.clinical import MockRecognizer, SqliteLookupResolver, load_vocab_sqlite, terminology_versions

NOTE = "Assessment: type 2 diabetes and hypertension. Continue metformin. TSH pending."

# --- a throwaway "RxNorm-ish" + "LOINC-ish" DB (real use: load your own release) ---
db = Path(tempfile.gettempdir()) / "verichart_demo_vocab.db"
load_vocab_sqlite(
    [
        ("44054006", "Diabetes mellitus type 2", True),
        ("44054006", "type 2 diabetes", False),
        ("38341003", "Hypertension", True),
        ("38341003", "high blood pressure", False),
    ],
    str(db),
)
snomed = SqliteLookupResolver(str(db), system="SNOMED-CT", version="2026-03")

rx_db = Path(tempfile.gettempdir()) / "verichart_demo_rx.db"
load_vocab_sqlite([("6809", "Metformin", True), ("6809", "Glucophage", False)], str(rx_db))
rxnorm = SqliteLookupResolver(str(rx_db), system="RxNorm", version="2024AB")

# --- extract, then resolve ---
rec = MockRecognizer(version="mock@1")
rec.register("type 2 diabetes", label="PROBLEM", score=0.92)
rec.register("hypertension", label="PROBLEM", score=0.9)
rec.register("metformin", label="MEDICATION", score=0.95)
rec.register("TSH", label="LAB", score=0.88)

facts = extract_entities(NOTE, recognizer=rec, labels=["PROBLEM", "MEDICATION", "LAB"],
                         doc_id="note:1", patient_pseudonym="pt_1")

resolvers = [snomed, rxnorm]
resolved = resolve_concepts(facts, resolvers, documents={"note:1": NOTE})

print(f"terminology_versions -> {terminology_versions(resolvers)}\n")
for f in resolved:
    code = (f"{f['concept_system']}:{f['concept_code']} ({f['concept_display']})"
            if f["concept_code"] else "— unresolved —")
    print(f"  {f['label']:<11} {f['value']!r:<22} {code}")
    if f["terminology_version"]:
        print(f"  {'':<11} version: {f['terminology_version']}   fact_id: {f['fact_id'][:12]}")
