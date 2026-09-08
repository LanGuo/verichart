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
| **Clinical content** | `concept_code` | Phase 3 (terminology resolution) — `None` in Phase 1 | **yes** |
| | `concept_system` | Phase 3 — `"SNOMED-CT"` / `"RxNorm"` / `"LOINC"` / `"ICD-10-CM"` | **yes** |
| | `concept_display` | Phase 3 | no |
| | `value` | `GroundedField["value"]` / `QuarantinedField["value"]` | **yes** |
| | `value_normalized` | Phase 4 / 6 (unit + format normalization) — `None` in Phase 1 | no |
| | `effective_date` | caller-supplied in Phase 1; Phase 6 extracts from text | no |
| | `assertion_status` | `"unknown"` in Phase 1; Phase 2 (negation / ConText) populates | no |
| **Provenance** | `provenance_type` | `Span["provenance_type"]` when grounded; `"inferred"` when grounded without a locatable span; `"unverified"` when quarantined | no |
| | `span` | `GroundedField["span"]` (a veritract `Span`) — `None` when unverified | **yes** (its `doc_id` + offsets) |
| | `supporting_spans` | `[span]` in Phase 1; Phase 5 dedup unions spans from merged facts | no |
| | `extraction_model` | `manifest["model_tag"]` when a manifest is passed | no |
| | `extraction_model_digest` | `manifest["model_digest"]` | no |
| | `extraction_confidence` | `GroundedField["confidence"] / 100` (renormalized 0–1); `0.0` when quarantined | no |
| **Reconciliation** | `conflict_set_id` | Phase 5 — `None` in Phase 1 | no |
| | `resolution_method` | Phase 5 — `"none"` in Phase 1 | no |
| | `resolver_id` | Phase 5 — `None` in Phase 1 | no |
| | `rule_version` | Phase 5 — `None` in Phase 1 | no |
| **Versioning** | `manifest_id` | `manifest["manifest_id"]` or `result.manifest_id` | **yes** |
| | `terminology_version` | Phase 3 — `None` in Phase 1 | no |
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
- **`note` added** — carries the quarantine `reason` in Phase 1; general annotation slot later.
- **`to_facts()` gained `manifest=` and `created_at=`** beyond the sketch's
  `patient_pseudonym` / `effective_date`, for model provenance and reproducible ids.

## Known issue for Phase 5

`fact_id` keys on `label`. A schema-extracted `"drug"` fact and a Phase 2 NER `"MEDICATION"`
fact for the same drug at the same span will get **different** `fact_id`s. Phase 5 deduplication
must therefore key on `concept_code` (available after Phase 3), not on `label`. Flagged in the
Phase 1 plan's self-review; to be addressed in the Phase 5 plan.

[article]: https://www.talby.com/p/fact-level-provenance-in-healthcare
[roadmap]: ../../veritract/docs/superpowers/plans/2026-09-07-clinical-rwe-package-roadmap.md
