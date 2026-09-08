"""
Open-ended clinical entity extraction: GLiNER-BioMed finds entities, medspaCy ConText
classifies assertion / negation / temporality, verichart projects to ClinicalFact.

Needs the clinical extra:  pip install 'verichart[clinical]'
First run downloads the GLiNER-BioMed model (~400 MB).

    python examples/entities_from_note.py
"""
import sys

try:
    from verichart.clinical import (
        DEFAULT_GLINER_LABELS,
        GlinerBiomedRecognizer,
        MedspacyContextClassifier,
        extract_entities,
    )
except ImportError:
    sys.exit("this example needs: pip install 'verichart[clinical]'")

NOTE = (
    "ASSESSMENT: 62 y/o male with type 2 diabetes mellitus and hypertension, admitted "
    "for community-acquired pneumonia. Started on ceftriaxone 1g IV daily; home metformin "
    "held. Hemoglobin A1c was 8.2%. No evidence of myocardial infarction. "
    "Father with coronary artery disease. Return if you develop chest pain.\n"
)

recognizer = GlinerBiomedRecognizer()          # bi-base, threshold 0.35
classifier = MedspacyContextClassifier()

facts = extract_entities(
    NOTE,
    recognizer=recognizer,
    labels=list(DEFAULT_GLINER_LABELS.values()),
    assertion_classifier=classifier,
    doc_id="note:0042",
    source_type="clinical_note",
    patient_pseudonym="pt_0042",
)

print(f"{len(facts)} facts  (recognizer {recognizer.version})\n")
for f in facts:
    s = f["span"]
    print(f"  {f['label']:<11} {f['assertion_status']:<14} conf={f['extraction_confidence']:.2f}  "
          f"[{s['char_start']}:{s['char_end']}]  {f['value']!r}")
    if f["note"]:
        print(f"  {'':<11} {f['note']}")
