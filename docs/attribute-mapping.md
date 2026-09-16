# ClinicalFact attribute mapping

The FDA's December 2025 real-world-evidence guidance treats relevance and reliability as
*per-fact* properties. The [source article][article] frames this as six attribute categories
that must be native fields on every clinical fact (see [`reference/README.md`](reference/README.md)
for the citation and what verichart takes from it). This table maps each to a `ClinicalFact`
field, says where the value comes from, and marks whether it feeds `fact_id`.

`fact_id = sha256(label, value, concept_code, concept_system, span.doc_id, span.char_start,
span.char_end, manifest_id)` — the stable identity a fact keeps across pipeline runs and the key
Phase 5 dedup builds on. `created_at`, `extraction_confidence`, and everything a later phase
mutates are deliberately **not** in it.

| Article category | `ClinicalFact` field | Source | In `fact_id`? |
|---|---|---|---|
| **Identity** | `fact_id` | `compute_fact_id(...)` | — (it *is* the id) |
| | `patient_pseudonym` | caller-supplied to `to_facts()` | no |
| | `label` | schema field name (Phase 1) / entity label (Phase 2) | **yes** |
| **Clinical content** | `concept_code` | Phase 3 — `resolve_concepts` sets it from a `ConceptResolver` match; `None` until then. **Recomputes `fact_id`.** | **yes** |
| | `concept_system` | Phase 3 — `"SNOMED-CT"` / `"RxNorm"` / `"LOINC"` / `"ICD-10-CM"` / `"UMLS"` | **yes** |
| | `concept_display` | Phase 3 — the resolver's canonical/preferred term | no |
| | `value` | `GroundedField["value"]` / `QuarantinedField["value"]` | **yes** |
| | `value_normalized` | Phase 6 (unit + format normalization, e.g. "twice daily" → "BID") — `None` before | no |
| | `effective_date` | caller-supplied in Phase 1; Phase 6 extracts from text | no |
| | `assertion_status` | `"unknown"` in Phase 1; Phase 2 sets it via `derive_assertion_status` from a `MedspacyContextClassifier` (ConText) — `confirmed` / `ruled_out` / `family_history` / `historical` / `hypothetical` / `uncertain` | no |
| **Provenance** | `provenance_type` | `Span["provenance_type"]` when grounded; `"inferred"` when grounded without a locatable span **or** for a Phase 4 relation-assembled composite whose covering text ≠ the assembled statement; `"unverified"` when quarantined | no |
| | `span` | `GroundedField["span"]` (a veritract `Span`) — `None` when unverified | **yes** (its `doc_id` + offsets) |
| | `supporting_spans` | `[span]` for a plain fact; a Phase 4 composite carries the anchor span + every attribute span; Phase 5 dedup unions spans from merged facts | no |
| | `extraction_model` | `manifest["model_tag"]` when a manifest is passed | no |
| | `extraction_model_digest` | `manifest["model_digest"]` | no |
| | `extraction_confidence` | `GroundedField["confidence"] / 100` (renormalized 0–1); `0.0` when quarantined | no |
| **Reconciliation** | `conflict_set_id` | `reconcile()` — set on any fact that was in a group of ≥2 (`None` for a solo fact) | no |
| | `resolution_method` | `reconcile()` — the winning method (`"highest_confidence"` / `"most_recent"` / `"source_rank"` / `"named_rule"` / `"llm_assisted"` / `"human_review"`); `"none"` for a solo fact | no |
| | `resolver_id` | `reconcile()` — the named-rule name, the LLM resolver's model tag, or `None` for a built-in method | no |
| | `rule_version` | `reconcile()` — `policy.rule_version`; feeds `rule_versions(policy)` into the manifest | no |
| **Versioning** | `manifest_id` | `manifest["manifest_id"]` or `result.manifest_id` | **yes** |
| | `terminology_version` | Phase 3 — the resolved code's release (`resolver.version`); `terminology_versions(resolvers)` feeds the manifest | no |
| **(annotation)** | `note` | quarantine reason in Phase 1; free-form later | no |
| **(bookkeeping)** | `created_at` | `to_facts()` call time, or the `created_at=` argument | no |

## Access & consent — deliberately absent

The article's sixth category (consent status at query time, purpose code, access role, last
access timestamp) has **no fields in `ClinicalFact`**. Access control, purpose limitation, and
consent enforcement are the caller's / platform's responsibility — see "what stays out" in the
[roadmap][roadmap]. verichart's `FactStore` protocol (Phase 8) provides the seam for a store
that enforces them; the fact record itself stays free of access state.

## Deliberate deviations from the roadmap sketch

The [roadmap][roadmap] Phase 1 sketch of `ClinicalFact` was refined during implementation:

- **`label` added** — a pre-terminology identifier (the schema field name, or a Phase 2 entity
  type). Needed because `fact_id` must be computable before terminology resolution exists.
- **`provenance_type` hoisted to the fact** with a fourth value `"unverified"`. The roadmap left
  the unverified case implicit in `span is None`, but a grounded-then-LLM-inferred fact can also
  have `span is None` — so the distinction needs its own field.
- **`note` added** — carries the quarantine `reason` in Phase 1; the fired ConText modifiers in
  Phase 2; general annotation slot later.
- **`to_facts()` gained `manifest=` and `created_at=`** beyond the sketch's
  `patient_pseudonym` / `effective_date`, for model provenance and reproducible ids.
- **`AssertionStatus` gained `"uncertain"`** (Phase 2). The article's list is *confirmed,
  ruled-out, family history, patient-reported, historical*; ConText also produces uncertainty
  and hypotheticality — both clinically load-bearing — so verichart keeps `"hypothetical"` and
  adds `"uncertain"`. See [`clinical-ner.md`](clinical-ner.md).
- **Phase 2 split `EntityRecognizer` from `AssertionClassifier`** — the roadmap sketched one
  recognizer returning entities that already carried `assertion_status`. GLiNER-BioMed (finds
  entities, no negation) + medspaCy ConText (classifies, doesn't find) compose better as two
  protocols.

## Resolved in Phase 5

`fact_id` keys on `label`, so a schema-extracted `"drug"` fact and a Phase 2 NER `"MEDICATION"`
fact for the same drug at the same span still get **different** `fact_id`s — that part is
unchanged. Flagged in the Phase 1 plan's self-review as a problem for cross-document dedup;
Phase 5's `concept_key()` fixes it at the reconciliation layer by grouping on `concept_code`
(available after Phase 3) instead, with a `label:value` fallback for unresolved facts. See
[`reconciliation.md`](reconciliation.md).

[article]: https://www.talby.com/p/fact-level-provenance-in-healthcare
[roadmap]: roadmap.md
