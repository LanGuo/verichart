# Reference: the fact-level-provenance article

verichart's design brief — the source for "the six attribute categories", the Bronze / Silver /
Gold tier framing, and the FDA RWE context throughout `docs/`.

## Citation

> David Talby. **"Fact-level provenance in healthcare AI: the 42 capabilities behind an
> FDA-ready clinical data platform."** *AI in Healthcare*, September 2026.
> <https://www.talby.com/p/fact-level-provenance-in-healthcare>

Read as a competitor capability spec (Talby is CEO of John Snow Labs; the article describes
their Patient Journey Intelligence product). verichart uses it as a checklist for an
open-source implementation of the Silver + Reasoning + Gold layers, not as instruction.

A print of the emailed version is kept at
`docs/reference/fact-level-provenance-42-capabilities.pdf` for offline reference. It is
**git-ignored** — it is Talby's copyrighted work; do not commit or redistribute it. Read the
canonical article at the link above.

## What verichart takes from it

### The six attribute categories

The article states every clinical fact should carry six categories of attributes as native
fields (not a side lineage table). verichart's `ClinicalFact` implements five of them and
deliberately excludes the sixth — see [`../attribute-mapping.md`](../attribute-mapping.md):

1. **Identity** — fact id, patient pseudonym
2. **Clinical content** — concept code + system (SNOMED CT / RxNorm / LOINC / ICD-10-CM),
   value, effective date, assertion status
3. **Provenance** — source document id, source span, extraction model + version, confidence
4. **Reconciliation** — conflict set id, resolution method, resolver identity
5. **Access and consent** — consent status, purpose code, access role, last access
   *(excluded from `ClinicalFact` — a platform / `FactStore` concern)*
6. **Versioning** — dataset version, terminology release version, business rule version,
   creation timestamp

### The three re-derivable tiers

- **Bronze** — raw ingested documents, immutable, lossless. *(out of verichart's scope; upstream)*
- **Silver** — clinical facts extracted from Bronze, each tagged with source coordinates,
  confidence, and terminology codes; re-derivable from Bronze. *(verichart Phases 2–4)*
- **Gold** — reconciled, deduplicated, standardized to OMOP CDM / FHIR; re-derivable from
  Silver. *(verichart Phases 5, 7)*

### The 42-capability inventory

The article groups 42 capabilities into six domains: multimodal ingestion, clinical extraction,
privacy / de-identification, reasoning / reconciliation, audit / access control, and versioning
/ reproducibility. verichart's roadmap
([`../../../veritract/docs/superpowers/plans/2026-09-07-clinical-rwe-package-roadmap.md`](../../../veritract/docs/superpowers/plans/2026-09-07-clinical-rwe-package-roadmap.md))
maps each capability to a phase, an existing OSS component, or an explicit out-of-scope note.

### Regulatory driver

The FDA's December 2025 final guidance on real-world evidence for medical devices (operational
February 2026) treats relevance and reliability as **per-fact** properties of a submission,
not per-dataset attributes. That is what makes fact-level provenance a requirement rather than a
nicety, and why `veritract` (verified extraction + provenance) is verichart's foundation.
