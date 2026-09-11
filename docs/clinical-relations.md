# Clinical relation extraction (Phase 4)

Link an anchor entity (medication / lab / problem) to its attribute entities (dose, frequency,
route, value, unit, severity …) and emit **one composite `ClinicalFact`** per anchor —
"metformin 500 mg PO twice daily" instead of four disconnected rows — with `supporting_spans`
still tracing every piece to the source.

Design rationale and the SOTA review behind it: `docs/research/clinical-relation-extraction.md`.

## The pipeline

```
recognize attributes  ─┐
                        ├─►  RelationExtractor.extract(text, mentions)  ─►  [ClinicalRelation]
mentions_from_facts(anchor_facts) ─┘                                            │
                                                                               ▼
                                          relations_to_facts(text, relations, anchor_facts)
                                                        │
                                                        ▼
                                     [ClinicalFact]  (composites replace constituents)
```

```python
from verichart import extract_entities, extract_relations
from verichart.clinical import AttributeRecognizer, RuleRelationLinker

anchor_facts = extract_entities(note, recognizer=gliner, labels=["disease", "medication"], doc_id="n:1")

facts = extract_relations(
    note,
    recognizer=AttributeRecognizer(),            # regex attribute NER — no model
    anchor_facts=anchor_facts,
    attribute_labels=["STRENGTH", "ROUTE", "FREQUENCY", "FORM"],
    extractor=RuleRelationLinker(),              # deterministic
)
```

`extract_relations` is a convenience wrapper; the pieces (`mentions_from_facts`,
`extractor.extract`, `relations_to_facts`) are public for finer control — e.g. running both a
`RuleRelationLinker` and an `LlmRelationExtractor` and merging.

## Attributes are entities

Attribute spans come from the **Phase 2 `EntityRecognizer` protocol**, just with an attribute
label set — there is no bespoke medication parser. Two sources:

- **`AttributeRecognizer`** — regex + lexicons, no model. STRENGTH (`number + unit`, combined
  `5 mg/5 mL`, ranges), FREQUENCY / ROUTE / FORM (lexicons), DURATION, lab VALUE / UNIT.
  MedEx-semantic-tagger / `parsigs` lineage. Misses narrative phrasing with no number/keyword
  ("titrated up") — by design. No SEVERITY / STAGE / BODY_SITE (open-vocabulary).
- **GLiNER-BioMed** with `MEDICATION_ATTRIBUTE_LABELS.values()` etc. — for the spans regex misses.

## Two relation extractors

### `RuleRelationLinker` — deterministic (default path for the safe relations)

Ordered-directional, MedEx lineage — **not** naive nearest-neighbour. For each attribute:
link to the nearest anchor of the compatible family **in the same sentence**, biased by a
per-attribute-type directional prior; positional coordination
("… 500 mg and 10 mg respectively") is **declined, not guessed**.

`_ATTR_RULES` — family · prior · safe:

| attribute | anchor family | prior | emitted by default |
|---|---|---|---|
| STRENGTH, DOSE, FORM | MEDICATION | either | ✅ |
| ROUTE, FREQUENCY | MEDICATION | follows | ✅ |
| VALUE, UNIT | LAB | follows | ✅ |
| SEVERITY | PROBLEM | precedes | ✅ |
| LATERALITY | PROBLEM | either | ✅ |
| DURATION | MEDICATION | follows | ❌ (rule F1 ≈ 0.73) |
| BODY_SITE, STAGE | PROBLEM | follows | ❌ (rule F1 ≈ 0.78) |

The ❌ types are `LlmRelationExtractor`'s job — pass `emit_relations={...}` to override.
`scope="sentence"` (default; ~91 % of relations are intra-sentential) or `"clause"` (precision
knob for list-heavy notes). Every relation records `method` / `direction` / `token_gap` for audit.

Reason / indication / ADE relations are **never** emitted deterministically (rule F1 ≈ 0.4).

### `LlmRelationExtractor` — veritract-grounded (the hard cases)

Per anchor: a constrained-decoding schema of its attributes → `veritract.extract_raw` →
`ground(mode="fuzzy")`. An attribute the model returns that cannot be located in the source is
**dropped, never linked**. One LLM call per anchor — the cost. Use it for narrative phrasing,
coordinated lists, and the relation types the rule linker won't touch.

## The composite fact

`relations_to_facts` groups relations by anchor and, per group:

| field | value |
|---|---|
| `value` | assembled statement, ordered by `_ASSEMBLY` (strength → dose → form → route → frequency → duration) |
| `span` | covering span (anchor start … last attribute end) |
| `provenance_type` | `"direct"` when the whitespace-normalized covering text equals the assembled statement (contiguous — the common case), else `"inferred"` |
| `supporting_spans` | the anchor span + every attribute span — each traces exactly |
| `extraction_confidence` | `min` over the anchor, attributes, and relation scores (weakest link) |
| `extraction_model` | the relation extractor's `.version` |
| `note` | `"relations: HAS_STRENGTH='500 mg', …; <extractor>"` |
| `assertion_status`, `concept_code`, `patient_pseudonym`, `effective_date` | inherited from the matching anchor entity fact |

The composite **replaces** the bare anchor fact and the linked attribute facts. Anchors with no
links pass through unchanged. Unlinked attribute facts are dropped
(`keep_unlinked_attributes=True` to retain). `fact_id` is recomputed — its `value` differs from
the bare anchor's, so Phase 5 still sees them as the same concept via `concept_code`.

## Not implemented

- **Dependency-path linking** — Dligach 2014 measured only ~2–3 F1 over surface order; not
  worth a spaCy/Stanza model dependency.
- **Cross-sentence links** — ~9 % of relations; the LLM path or a later phase.
- Phase 9 must benchmark the linker **end to end** (recognizer + linker), not against gold
  entity spans — recognizer errors compound.
