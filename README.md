# verichart

**Audit-grade clinical facts with fact-level provenance.**

verichart turns a [`veritract`](https://github.com/LanGuo/veritract) extraction into
`ClinicalFact` records — each carrying its source document, exact character span, extraction
model + config version, confidence, and (from the reconciliation layer) any detected conflicts
and how they were resolved. It is the open-source counterpart to the **Silver + Reasoning**
tiers of a regulatory-grade clinical data platform, targeting the FDA's December 2025 real-world
evidence guidance where relevance and reliability are *per-fact* properties.

> **Status: pre-alpha.** Phase 1 (the `ClinicalFact` record) is under construction. See
> `docs/superpowers/plans/` for the roadmap and per-phase plans.

## What it is / isn't

- **Is:** a library that produces provenance-carrying clinical facts and (later phases) resolves
  conflicts across documents and emits OMOP CDM / FHIR.
- **Isn't:** a hosted service, a warehouse, an ETL scheduler, a de-identification engine, an
  access-control plane, or a UI.

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

## License

MIT
