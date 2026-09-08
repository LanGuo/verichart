"""
Project a veritract extraction of a clinical note into verichart ClinicalFact records.

Run with Ollama up and a small model pulled (e.g. `ollama pull gemma3:1b`):

    python examples/facts_from_note.py

Each fact traces to a source span (provenance_type direct/paraphrased/inferred),
or — if the model returns something the source does not support — is preserved
with provenance_type="unverified" rather than dropped.
"""
from veritract import LLMClient, build_manifest, extract

from verichart import to_facts

MODEL = "gemma3:1b"

NOTE = (
    "ASSESSMENT AND PLAN\n"
    "62 y/o male with type 2 diabetes mellitus and hypertension, admitted for "
    "community-acquired pneumonia. Started on ceftriaxone 1g IV daily. "
    "Home metformin held during admission. No history of MI. "
    "Father with coronary artery disease.\n"
)

SCHEMA = {
    "type": "object",
    "properties": {
        "primary_diagnosis": {"type": "string"},
        "antibiotic": {"type": "string"},
        "held_medication": {"type": "string"},
        "cardiac_history": {"type": "string"},
    },
    "required": ["primary_diagnosis", "antibiotic", "held_medication", "cardiac_history"],
}

llm = LLMClient(model=MODEL, temperature=0.0, seed=42)
manifest = build_manifest(llm, SCHEMA)

result = extract(NOTE, SCHEMA, llm, doc_id="note:0042", source_type="clinical_note", manifest=manifest)
facts = to_facts(result, patient_pseudonym="pt_0042", effective_date="2026-02-11", manifest=manifest)

print(f"{len(facts)} facts  (manifest {manifest['manifest_id'][:12]})\n")
for f in facts:
    loc = (
        f"{f['span']['doc_id']}[{f['span']['char_start']}:{f['span']['char_end']}]"
        if f["span"]
        else "(no span)"
    )
    print(f"  {f['label']:<18} {f['provenance_type']:<11} conf={f['extraction_confidence']:.2f}  {loc}")
    print(f"  {'':<18} value: {f['value']!r}")
    if f["note"]:
        print(f"  {'':<18} note:  {f['note']}")
    print()
