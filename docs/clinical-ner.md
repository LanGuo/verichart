# Clinical entity extraction (Phase 2)

Open-ended clinical NER: find every problem / medication / lab / procedure / vital in a
document, classify each for negation and temporality, and project to `ClinicalFact`.

## Two extraction modes

| | Schema extraction (Phase 1) | Open-ended NER (Phase 2) |
|---|---|---|
| entry point | `to_facts(veritract.extract(...))` | `verichart.clinical.extract_entities(...)` |
| you specify | named fields (`{"drug": ...}`) | entity **types** (`["disease", "medication"]`) |
| output | one fact per field | variable-length list |
| mechanism | LLM + constrained decoding | span model (+ optional assertion classifier) |

Both produce `ClinicalFact` records with the same shape. To run both over one document,
concatenate the two lists — deduping them is Phase 5's job, not `extract_entities`'.

## The pipeline

```
recognizer.recognize(text, labels)        -> [EntityMention]   (exact char offsets)
  -> assertion_classifier.classify(...)    -> [AssertionResult] (negation / temporality / subject)
    -> ground: offsets become a veritract Span (provenance_type="direct", no fuzzy step)
      -> project: verichart.facts.make_fact(...) -> [ClinicalFact]
```

```python
from verichart.clinical import (
    GlinerBiomedRecognizer, MedspacyContextClassifier,
    extract_entities, DEFAULT_GLINER_LABELS,
)

recognizer = GlinerBiomedRecognizer()                    # needs verichart[clinical]
classifier = MedspacyContextClassifier()

facts = extract_entities(
    note_text,
    recognizer=recognizer,
    labels=list(DEFAULT_GLINER_LABELS.values()),
    assertion_classifier=classifier,
    doc_id="note:1",
    patient_pseudonym="pt_1",
)
```

`extract_entities` drops any mention whose `text` does not equal `text[char_start:char_end]`
(with a warning) — a recognizer returning bad offsets is a bug to surface, not paper over.

## Adapters

Both slots are `Protocol`s — plug in a fine-tuned model, a cloud clinical-NLP API, or an LLM.

### `EntityRecognizer`

| Adapter | Backend | Notes |
|---|---|---|
| `GlinerBiomedRecognizer` | GLiNER-BioMed (Apache-2.0) | zero-shot from a label list; default `Ihor/gliner-biomed-bi-base-v1.0`, `threshold=0.35`. `bi-base` is conservative with terse labels — use `DEFAULT_GLINER_LABELS` (single-word where possible) and tune `threshold` down for recall. `bi-large` for higher F1. |
| `MedspacyRuleRecognizer` | medspaCy `TargetRule` | deterministic, no model download; for a curated term list. `score=1.0`. The default when `gliner` is not installed. |

GLiNER has an input length limit — `GlinerBiomedRecognizer` chunks on a fixed character window
(`chunk_chars=1800`, `chunk_overlap=200`) and de-duplicates by `(start, end, label)`. An entity
straddling a chunk boundary can still be missed; widen the overlap for dense text.

### `AssertionClassifier`

`MedspacyContextClassifier` wraps medspaCy's ConText algorithm (MIT). It classifies entities it
is *handed* — it does not find them. Returns one `AssertionResult` per mention, in order.

### `derive_assertion_status`

`AssertionResult` flags → `ClinicalFact.assertion_status`, first match wins:

| flag | status |
|---|---|
| `is_negated` | `ruled_out` |
| `is_family` | `family_history` |
| `is_hypothetical` | `hypothetical` |
| `is_historical` | `historical` |
| `is_uncertain` | `uncertain` |
| (none) | `confirmed` |

No classifier passed → every fact is `assertion_status="unknown"` (we did not check).

`"patient_reported"` is not produced by stock ConText — add a custom modifier rule if you need it.

## Reproducibility

Phase 2 does not go through `veritract.extract()`, so there is no `PipelineManifest` from
`build_manifest` (that needs an LLM). Instead each adapter carries its own version:

- `recognizer.version` → `ClinicalFact.extraction_model`
  (e.g. `"Ihor/gliner-biomed-bi-base-v1.0@75ae331f66c7"` or `"medspacy-rules@<hash>"`)
- `recognizer.digest` → `ClinicalFact.extraction_model_digest`
  (`"hf:<repo-revision-sha>"` for GLiNER; `None` for the rule recognizer)
- `MedspacyContextClassifier.version` pins the medspaCy version + a hash of any custom rules

If a caller runs a combined pipeline (schema extraction *and* NER over one document), build a
manifest for the extraction side and pass it to `extract_entities(manifest=...)` so its
`manifest_id` is stamped on the NER facts too; put the recognizer/classifier versions in that
manifest's `build_manifest(extra={"rule_versions": {...}})`.

## Deviation from `attribute-mapping.md`

`AssertionStatus` gained `"uncertain"` in Phase 2. The source article's list is *confirmed,
ruled-out, family history, patient-reported, historical*; ConText also produces uncertainty and
hypotheticality, both clinically load-bearing, so verichart keeps `"hypothetical"` and adds
`"uncertain"`.
