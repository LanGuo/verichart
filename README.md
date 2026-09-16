# verichart

**Audit-grade clinical facts with fact-level provenance.**

verichart turns a [`veritract`](https://github.com/LanGuo/veritract) extraction into
`ClinicalFact` records — each carrying its source document, exact character span, extraction
model + config version, confidence, and (from the reconciliation layer) any detected conflicts
and how they were resolved. It is the open-source counterpart to the **Silver + Reasoning**
tiers of a regulatory-grade clinical data platform, targeting the FDA's December 2025 real-world
evidence guidance where relevance and reliability are *per-fact* properties.

> **Status: pre-alpha, v0.5.0.** Phases 1–5 are done: the `ClinicalFact` record, clinical NER +
> assertion (GLiNER-BioMed + medspaCy), terminology coding (SNOMED/RxNorm/LOINC/UMLS), relation
> extraction (medication/lab/problem attributes → one composite fact), and multi-document
> reconciliation (dedup, conflict detection, layered resolution policies). See
> [`docs/roadmap.md`](docs/roadmap.md) for what's done, what's next, and the doc for each phase.

## What it is / isn't

- **Is:** a library that turns clinical text into provenance-carrying `ClinicalFact` records,
  codes them to standard terminologies, links their attributes, and resolves conflicts across
  documents/sources — with every step traceable back to a source span.
- **Isn't:** a hosted service, a warehouse, an ETL scheduler, a de-identification engine, an
  access-control plane, or a UI. Not yet: OMOP/FHIR emission, temporal reasoning, or a
  persistence/audit-log layer (Phases 6–9, see the roadmap).

## Install (development)

`veritract` is not on PyPI yet, so install it from source first:

```bash
git clone https://github.com/LanGuo/veritract.git
git clone https://github.com/LanGuo/verichart.git
cd verichart
python3.12 -m venv venv && source venv/bin/activate
pip install -e ../veritract          # provides veritract>=0.2
pip install -e '.[dev]'
pytest -q
```

The `[clinical]` extra (`pip install -e '.[dev,clinical]'`) adds `medspacy`, `gliner`, and
`scispacy` for the real NER/terminology adapters (~2 GB); the base install runs on mocks and
regex-only components (`AttributeRecognizer`, `SqliteLookupResolver`, `RuleRelationLinker`).

## Examples

Runnable, no external service or download unless noted:

| File | Shows |
|---|---|
| [`examples/facts_from_note.py`](examples/facts_from_note.py) | schema extraction → `ClinicalFact` (needs Ollama) |
| [`examples/entities_from_note.py`](examples/entities_from_note.py) | open-ended NER + assertion (needs `[clinical]`, downloads GLiNER-BioMed) |
| [`examples/resolve_note_concepts.py`](examples/resolve_note_concepts.py) | terminology coding, no download |
| [`examples/medication_statements.py`](examples/medication_statements.py) | relation extraction → composite facts, no download |
| [`examples/reconcile_two_sources.py`](examples/reconcile_two_sources.py) | the article's 80mg/40mg dose conflict, resolved three ways |

## Docs

- [`docs/roadmap.md`](docs/roadmap.md) — phase-by-phase status and what's next
- [`docs/attribute-mapping.md`](docs/attribute-mapping.md) — the `ClinicalFact` record, field by field
- [`docs/clinical-ner.md`](docs/clinical-ner.md) · [`docs/terminology.md`](docs/terminology.md) · [`docs/clinical-relations.md`](docs/clinical-relations.md) · [`docs/reconciliation.md`](docs/reconciliation.md) — one doc per phase
- [`docs/research/clinical-relation-extraction.md`](docs/research/clinical-relation-extraction.md) — the SOTA review behind Phase 4's design
- [`docs/reference/README.md`](docs/reference/README.md) — the source article this roadmap is a checklist against

## License

MIT
