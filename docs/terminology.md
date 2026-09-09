# Terminology resolution (Phase 3)

Map a `ClinicalFact`'s `value` to a standard code — SNOMED CT, RxNorm, LOINC, ICD-10-CM, UMLS —
populating `concept_code` / `concept_system` / `concept_display` / `terminology_version`.

## The pipeline

Resolution runs **after** facts exist (from `to_facts` or `extract_entities`):

```python
from verichart import extract_entities, resolve_concepts
from verichart.clinical import SqliteLookupResolver, terminology_versions

facts = extract_entities(note, recognizer=rec, labels=[...], doc_id="note:1")

rxnorm = SqliteLookupResolver("rxnorm.db", system="RxNorm", version="2024AB")
loinc  = SqliteLookupResolver("loinc.db",  system="LOINC",  version="2.77")

resolved = resolve_concepts(facts, [rxnorm, loinc], documents={"note:1": note})
```

`resolve_concepts` is a pure projection — new fact dicts, no mutation, no network call of its
own. It routes each fact to resolvers by `label` (`DEFAULT_ROUTING`; a fact whose label is not
in the map tries every resolver in order), takes the first `ConceptMatch` at or above
`min_score` (default `0.0` — trust each resolver's own threshold).

`DEFAULT_ROUTING`:

| label | systems tried, in order |
|---|---|
| `PROBLEM` | SNOMED-CT, ICD-10-CM, UMLS |
| `MEDICATION` | RxNorm, UMLS |
| `LAB` | LOINC, UMLS |
| `PROCEDURE` | SNOMED-CT, UMLS |
| `VITAL` | LOINC, UMLS |

## `fact_id` changes on resolution

`compute_fact_id` keys on `concept_code` / `concept_system`, so a resolved fact gets a **new,
concept-anchored `fact_id`**. This is the point: two facts for the same drug from different
extraction paths, once resolved to the same code, become linkable by concept. `resolve_concepts`
recomputes `fact_id` on every match; `skip_resolved=True` (default) leaves an already-coded fact
untouched, so re-running is idempotent.

(Phase 5 deduplication keys on `concept_code` directly — `fact_id` still includes `label` and
`span`, so a schema-extracted `"drug"` fact and an NER `"MEDICATION"` fact converge in concept
but not yet in `fact_id`.)

## Manifest tie-in

```python
from veritract import build_manifest
manifest = build_manifest(llm, schema,
                          extra={"terminology_versions": terminology_versions([rxnorm, loinc])})
```

`terminology_versions([...])` → `{"RxNorm": "2024AB", "LOINC": "2.77"}`. Recording the release in
the manifest is what makes a point-in-time replay reproduce the same codes — SNOMED ships
biannually, RxNorm weekly, LOINC twice a year, ICD-10-CM annually.

## Resolvers

### `SqliteLookupResolver` — bring your own release (no extra, no download)

Deterministic lexical match over a local DB: exact / whitespace-normalized (score 1.0), then
rapidfuzz `token_sort_ratio` over candidates sharing the leading token, gated by
`fuzzy_threshold` (0–100, default 88). Ignores `context`.

**verichart ships no vocabulary content.** Build the DB from your own release:

| Vocabulary | License | Where |
|---|---|---|
| **UMLS Metathesaurus** (bundles SNOMED-US, RxNorm, LOINC, ICD-10-CM, …) | free UMLS licence | <https://uts.nlm.nih.gov> |
| SNOMED CT | free in Member countries (US via UMLS) | national release centre |
| RxNorm | mostly unrestricted | <https://www.nlm.nih.gov/research/umls/rxnorm> |
| LOINC | accept LOINC licence | <https://loinc.org/downloads> |
| ICD-10-CM | US public domain | <https://www.cms.gov/medicare/coding-billing/icd-10-codes> |
| CPT | proprietary, paid | AMA — bring your own |

DB schema — one row per `(code, synonym)`, `is_preferred=1` marks the display term:

```sql
CREATE TABLE concepts (code TEXT NOT NULL, term TEXT NOT NULL, is_preferred INTEGER DEFAULT 0);
```

Build it:

```python
from verichart.clinical import load_vocab_sqlite
load_vocab_sqlite(rows, "loinc.db")          # rows: iterable of (code, term, is_preferred)
```

or from a CSV/TSV:

```bash
python -m verichart.clinical.terminology load \
    --csv Loinc.csv --db loinc.db \
    --code-col LOINC_NUM --term-col LONG_COMMON_NAME --preferred-col ""
```

Common column names: **LOINC** `LOINC_NUM` / `LONG_COMMON_NAME`; **ICD-10-CM** `code` /
`description` (from the CMS order file, after reformatting); **RxNorm** `RXCUI` / `STR` with
`TTY` marking preferred terms (`--preferred-col` a column you derive).

### `ScispacyResolver` — scispaCy linker (needs `verichart[clinical]`, ~1 GB KB)

```python
from verichart.clinical import ScispacyResolver
umls = ScispacyResolver(linker_name="rxnorm", threshold=0.7)   # or "umls", "mesh", "go", "hpo"
```

scispaCy's `umls` / `rxnorm` / `mesh` linkers all identify concepts by **UMLS CUI** — the
`linker_name` only scopes which concepts are candidates (use `rxnorm` for drugs). So `.system`
is `"UMLS"` for all three. The KB is a frozen 2020AA snapshot; `.version` records the scispaCy
version + linker. `context` is accepted but unused.

For SNOMED / LOINC / ICD-10-CM *codes* (not CUIs), use `SqliteLookupResolver` over a
UMLS-derived table for that vocabulary.

### `MedcatResolver` — not yet

Deferred: MedCAT is a full NER+linker (re-segments the input rather than resolving a given
value), and its SNOMED model packs need a UMLS/NIH login. `ScispacyResolver` +
`SqliteLookupResolver` cover the need for now.
