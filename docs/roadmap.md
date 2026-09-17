# Roadmap

verichart depends on [`veritract`](https://github.com/LanGuo/veritract) (verified LLM
extraction + provenance + point-in-time replay) and adds the healthcare-specific layers on top.
The original phase-by-phase plans (one detailed TDD doc per phase) live locally under
`docs/superpowers/plans/` in each repo — that directory is intentionally **not** committed (it's
working scratch), so this page is the durable, tracked record of what each phase delivered and
what's left. Design decisions that matter beyond their own phase are promoted into the docs
linked below as they land.

## Milestones

| Milestone | Phases | Outcome | Status |
|---|---|---|---|
| **M1 — Auditable clinical facts** | veritract 0.2 + verichart Phase 1 | Reproducible, manifest-stamped `ClinicalFact` records from any veritract extraction | ✅ done |
| **M2 — Clinical Silver** | Phases 2, 3, 4 | Entities, assertion, terminology codes, relations — all grounded | ✅ done |
| **M3 — Reasoning tier** | Phases 5, 6 | Multi-document reconciliation with versioned policies + inference rules — the differentiated layer | ✅ done |
| **M4 — Gold + persistence** | Phases 7, 8 | OMOP CDM / FHIR emission with raw-byte traceability; reference hash-chained fact store | ⬜ not started |
| **M5 — Evidence** | Phase 9 | Published benchmarks against cloud clinical-NLP APIs | ⬜ not started |

## Phase status

| Phase | What | Status | Docs |
|---|---|---|---|
| **veritract — Pipeline manifest** | Content-addressed `PipelineManifest`, `replay()` — the reproducibility a hosted LLM API structurally cannot offer | ✅ v0.2.0 | [reproducibility.md](https://github.com/LanGuo/veritract/blob/main/docs/reproducibility.md) (veritract repo) |
| **1 — `ClinicalFact` record** | The record shape (6 attribute categories), `to_facts()` projection from schema extraction | ✅ v0.1.0 | [attribute-mapping.md](attribute-mapping.md) |
| **2 — Clinical NER** | Open-ended entity extraction (GLiNER-BioMed) + assertion/negation (medspaCy ConText) | ✅ v0.2.0 | [clinical-ner.md](clinical-ner.md) |
| **3 — Terminology resolution** | SNOMED CT / RxNorm / LOINC / ICD-10-CM / UMLS coding — `SqliteLookupResolver` (bring your own release) + `ScispacyResolver` | ✅ v0.3.0 | [terminology.md](terminology.md) |
| **4 — Relation extraction** | Link a medication/lab/problem to its attributes into one composite fact; deterministic `RuleRelationLinker` (ordered-directional, MedEx lineage) + `LlmRelationExtractor` | ✅ v0.4.0 | [clinical-relations.md](clinical-relations.md), [research/clinical-relation-extraction.md](research/clinical-relation-extraction.md) |
| **5 — Reconciliation** | Dedup, conflict detection, and layered policy resolution across documents/sources | ✅ v0.5.0 | [reconciliation.md](reconciliation.md) |
| **6 — Temporal reasoning** | `assign_effective_dates` (dateparser-backed); inference rules with explicit confidence (`ConceptTriggerRule`, `MedRtTriggerRule` KB lookup, `LlmInferenceRule`); scoped absence-as-negative (`AbsenceRule`); decay (`is_stale`, zero default windows) | ✅ v0.6.0 | [reasoning.md](reasoning.md), [research/temporal-reasoning-and-decay.md](research/temporal-reasoning-and-decay.md) |
| **7 — OMOP / FHIR emission** | Materialize reconciled facts to OMOP CDM v5.4 / FHIR R4 with native (not side-table) provenance | ⬜ not started | — |
| **8 — `FactStore`** | Persistence + hash-chained audit log protocol, one reference implementation | ⬜ not started | — |
| **9 — Benchmarks** | Reproduce the kind of head-to-head the source article cites, against OSS + veritract; also a grounded eval-battery mode (invented-fact / omission / dose-error / unsafe-disposition checks against generated clinical text, reusing Phase 5 reconciliation for span-level grounding) — see `docs/superpowers/comparables/2026-09-17-typesafe-jev-system-one.md` (untracked) | ⬜ not started | — |

## Scope boundary (kept out on purpose)

Bronze ingestion (PDF/OCR — `veritract.extract_pdf`; FHIR/HL7v2/DICOM readers), de-identification,
access control / consent enforcement, and the agent layer (MCP tools over the OMOP/FHIR output)
are explicitly not verichart's job — see the "What it is / isn't" section of the
[top-level README](../README.md).

## Source

Read as a checklist, not instruction: David Talby, *"Fact-level provenance in healthcare AI: the
42 capabilities behind an FDA-ready clinical data platform"* (Sept 2026) — see
[docs/reference/README.md](reference/README.md) for the full citation and how each of the
article's 42 capabilities maps to a phase above.
