# Research: OMOP CDM v5.4 and FHIR R4 emission specifics

*Compiled 2026-09-18 for the Phase 7 plan
(`docs/superpowers/plans/2026-09-18-verichart-phase7-omop-fhir.md`). Scope: OMOP CDM v5.4's
actual required fields and concept-mapping requirement, whether OMOP already has a native
provenance mechanism worth reusing, and FHIR R4 tooling/provenance specifics.*

## Bottom line

- **FHIR is the lower-friction target.** A `CodeableConcept` carries `{system, code, display}`
  directly — no id-space translation. OMOP demands a genuinely separate mapping stage (source
  code → Athena `CONCEPT`/`CONCEPT_RELATIONSHIP` "Maps to" → standard `concept_id`) plus integer
  `person_id`/`visit_occurrence_id` identity verichart's pseudonymous, visit-agnostic fact model
  doesn't have natively. **Ship FHIR first**; treat OMOP as a follow-up gated on the vocabulary
  mapping being an explicit, documented prerequisite — not bundled.
- **OMOP already has native provenance columns** (`*_source_value`/`*_source_concept_id` on
  every clinical table) and a purpose-built table for NLP provenance specifically
  (`NOTE_NLP`) — no custom extension column should be invented.
- **`fhir.resources` dropped plain FHIR R4 at v7.0** (only R4B ships from then on) — pin
  `<7.0` for literal R4.
- **No character-offset concept exists anywhere in core FHIR R4** — a custom extension is
  required regardless of approach; no published implementation guide defines one.

## A — OMOP CDM v5.4

**A.1 Required fields.** Checked against the actual v5.4.0 DDL
([OHDSI/CommonDataModel](https://github.com/OHDSI/CommonDataModel/tree/v5.4.0/inst/ddl/5.4),
[cdm54.html](https://ohdsi.github.io/CommonDataModel/cdm54.html)). For `condition_occurrence`,
`drug_exposure`, `measurement`, `procedure_occurrence`, `observation`, the NOT NULL set is
uniformly small: the row id, **`person_id`** (integer FK), the domain `*_concept_id`, the
`*_type_concept_id`, and one date-only occurrence/start column. `drug_exposure` additionally
requires `drug_exposure_end_date` NOT NULL. **`visit_occurrence_id` is nullable in every one of
these tables** — not a hard constraint. There is no documented canonical "no-visit" placeholder
value anywhere in the CDM spec, the Book of OHDSI ETL chapter, or OHDSI's Themis ETL-convention
project — it's left to each implementer; leaving it `NULL` is spec-legal.

**A.2 Concept mapping is real and separate.** `condition_concept_id` etc. must be an OMOP
**Standard Concept** — an integer in OHDSI's own id space, distinct from the source code
string. The path is [Athena](https://athena.ohdsi.org/vocabulary/list) (free, requires a login
account), joining source `CONCEPT` (by code + vocabulary_id) → `CONCEPT_RELATIONSHIP` where
`relationship_id = 'Maps to'` → target standard `CONCEPT`. **CPT4 is licensed separately under
an [AMA EULA](https://www.ohdsi.org/cpt-eula/)** that prohibits redistribution, derivative
works, or use outside OHDSI-administered healthcare programs — verichart cannot bundle CPT4
mappings under any circumstance; a user's own Athena bundle must include it if they need
procedure-code mapping from CPT.

**A.3 Native provenance — confirmed.** Every listed table ships `*_source_value` (raw string)
and `*_source_concept_id` (source vocabulary's own OMOP-space concept) natively.
[**`NOTE_NLP`**](https://www.ohdsi.org/web/wiki/doku.php?id=documentation:cdm:note_nlp) is
OHDSI's own table for NLP-derived facts: `note_nlp_id`, `note_id`, `section_concept_id`,
`snippet`, `offset`, `lexical_variant`, `note_nlp_concept_id`, `note_nlp_source_concept_id`,
`nlp_system`, `nlp_date`/`nlp_date_time`, `term_exists`, `term_temporal`, `term_modifiers`. Its
FK to `NOTE` was only added in the **v5.4.1 patch** (a detail a slightly older memory would
miss). It has **no standard FK to the clinical event tables** (`condition_occurrence` etc.) — a
parallel staging/traceability table by design, not one OHDSI wires in for you.

**A.4 Python tooling.** No first-party OHDSI Python ORM. Community packages exist —
[`omop-cdm`](https://pypi.org/project/omop-cdm), [`sqlalchemy-omopcdm`](https://pypi.org/project/sqlalchemy-omopcdm),
[`pyomop`](https://github.com/dermatologist/pyomop) (SQLAlchemy, v5.4/v6 support) — but license
compatibility with an MIT project needs checking case by case (at least one carries a GPL-family
license per dependency scanning). The trustworthy path is OHDSI's own published DDL directly.

**A.5 Validation.** [Achilles](https://github.com/OHDSI/Achilles) (1.7.2, Jan 2025) and the
[Data Quality Dashboard](https://github.com/OHDSI/DataQualityDashboard) are both alive, R-based,
and run ~3,000 checks across CDM versions including v5.4. No maintained lightweight Python
equivalent exists.

**Flag:** **OMOP CDM v5.5 released 2026-08-28**
([forum announcement](https://forums.ohdsi.org/t/omop-cdm-v5-5-release/25881)) — a non-breaking
superset of v5.4 (new fields are all optional), so v5.4-shaped output stays valid under v5.5.

## B — FHIR R4

**B.1** [`fhir.resources`](https://pypi.org/project/fhir.resources/) is at v8.3.0 (July 2026),
BSD-licensed, pydantic v2. **As of v7.0.0, plain R4 was dropped** — only R4B (4.3.0) ships
alongside default R5 and STU3. Pin `fhir.resources>=6.5,<7` for literal R4.

**B.2/B.3** Per the [HL7 Provenance spec](http://hl7.org/fhir/provenance.html):
`Provenance.entity.what` is a `Reference(Any)` — resource-level only, no character-offset
concept anywhere in core R4. One unofficial 2018 SMART-on-FHIR wiki sketch of an offset
extension never became a published/balloted IG. A custom extension is required either way; the
idiomatic shape keeps offsets on `Provenance.entity` (not the clinical resource itself),
matching FHIR's separation of "what happened" from "how we know."

## Sources

- OHDSI CommonDataModel v5.4.0 DDL: <https://github.com/OHDSI/CommonDataModel/tree/v5.4.0/inst/ddl/5.4>
- CDM v5.4 field reference: <https://ohdsi.github.io/CommonDataModel/cdm54.html>
- Book of OHDSI, ETL chapter: <https://ohdsi.github.io/TheBookOfOhdsi/ExtractTransformLoad.html>
- Themis ETL conventions: <https://github.com/OHDSI/Themis>
- Athena vocabulary browser: <https://athena.ohdsi.org/vocabulary/list>
- OHDSI CPT4 EULA: <https://www.ohdsi.org/cpt-eula/>
- NOTE_NLP table documentation: <https://www.ohdsi.org/web/wiki/doku.php?id=documentation:cdm:note_nlp>
- `omop-cdm` (PyPI): <https://pypi.org/project/omop-cdm> · `pyomop`: <https://github.com/dermatologist/pyomop>
- Achilles: <https://github.com/OHDSI/Achilles> · Data Quality Dashboard: <https://github.com/OHDSI/DataQualityDashboard>
- OMOP CDM v5.5 release announcement: <https://forums.ohdsi.org/t/omop-cdm-v5-5-release/25881>
- `fhir.resources` (PyPI): <https://pypi.org/project/fhir.resources/>
- FHIR R4 Provenance resource: <http://hl7.org/fhir/provenance.html>
- SMART-on-FHIR offset-extension sketch (unofficial, never balloted): <https://github.com/smart-on-fhir/smart-on-fhir.github.io/wiki/Extensions-and-Provenance-for-Derived-Data>
