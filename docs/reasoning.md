# Temporal reasoning, inference rules, decay (Phase 6)

Derive facts that aren't stated verbatim, normalize relative dates against a document date, and
let callers ask whether a time-sensitive fact is stale.

## Who makes the clinical judgment call

verichart ships **zero default inference rules, zero default decay windows, and zero default
indication data.** `ConceptTriggerRule` and `MedRtTriggerRule` are mechanisms; the *deploying
organization's clinical/informatics team* supplies and owns the actual rule content — which drug
implies which diagnosis, which lab needs what validity window — the same governance a hospital's
CDS committee applies before a drug-interaction rule goes live. This extends Phase 3's "no
vocabulary content shipped" to clinical *reasoning* content, not just terminology.

Full research behind the choices below (why `dateparser`, why no decay defaults, why two
inference-rule flavors) is in
[`research/temporal-reasoning-and-decay.md`](research/temporal-reasoning-and-decay.md).

## Inference rules

`ReasoningRule` is one protocol (`name`, `version`, `apply(facts) -> new_facts`);
`apply_rules(facts, rules)` runs all of them and appends what they derive. Pure — removing a
rule from the list changes exactly its contribution.

```python
from verichart.reasoning import apply_rules, ConceptTriggerRule

rule = ConceptTriggerRule(
    name="metformin_implies_t2dm", version="v1",       # you name and version it after review
    trigger_concept=("RxNorm", "6809"),
    infer=dict(label="PROBLEM", value="type 2 diabetes mellitus (inferred)",
              concept_system="SNOMED-CT", concept_code="44054006"),
    confidence=0.7,
)
facts = apply_rules(patient_facts, [rule])
```

Every derived fact has `provenance_type="inferred"` (never `"direct"` — nobody stated this),
`assertion_status="unknown"` by default, `span=None`, and `extraction_model` set to the rule's
version for audit. A shared guard (`_already_has_concept`) means no rule ever infers a concept
the patient already has — stated directly, or inferred earlier by another rule.

### Three flavors, not one hard-coded template

| | Mechanism | When to use | Ships default content? |
|---|---|---|---|
| `ConceptTriggerRule` | fixed `trigger → infer` mapping you write | you've already reviewed and approved this exact rule | no |
| `MedRtTriggerRule` | lookup against a **locally cached** MED-RT/RxClass `may_treat`/`may_prevent` export | drug→diagnosis, sourced from a maintained federal terminology | no (bring your own release) |
| `LlmInferenceRule` | a pinned LLM reasons over the patient's other facts | contextual disambiguation the KB lookup can't do | no |

**No published benchmark says either `MedRtTriggerRule` or `LlmInferenceRule` is more accurate
for drug→diagnosis inference** — both ship, neither is silently preferred.

### `MedRtTriggerRule` — never collapses ambiguity

Many drugs have more than one real indication (metformin: T2DM, PCOS, prediabetes; lisinopril:
hypertension, heart failure). Picking one silently would be a guess dressed up as a fact — a
drug with N candidate `may_treat` relations yields **up to N derived facts**, confidence split
evenly, each `note` naming the ambiguity.

Build the local DB once — never a live network call inside `.apply()`, consistent with Phase
3's terminology resolvers:

```python
from verichart.reasoning import load_indication_relations, MedRtTriggerRule

# rows sourced from the RxClass API (no UMLS license needed for the API call itself —
# only bulk file downloads require a free UMLS account):
#   GET https://rxnav.nlm.nih.gov/REST/rxclass/class/byRxcui
#       ?rxcui={rxcui}&relaSource=MEDRT&relas=may_treat
# or a bulk UMLS/MED-RT release.
load_indication_relations(rows, "indications.db")
rule = MedRtTriggerRule("indications.db", version="2026.07.06")  # the MED-RT release tag
```

`.version` pins both the release tag you pass and a content hash of the local DB, so two DBs
claiming the same release tag but holding different rows are distinguishable.

### `LlmInferenceRule` — proposes a name, never a code

Asking an LLM to produce a SNOMED/RxNorm code directly is exactly the hallucination risk Phase 3
exists to avoid. `LlmInferenceRule` proposes a diagnosis **name** and rationale
(`mode="no-grounding"` — a judgment call, not a verbatim extraction, same pattern as
`VeritractLlmResolver`); the derived fact ships with `concept_code=None`. Run it through Phase
3's `resolve_concepts` afterward, the same as any other unresolved fact:

```python
from verichart import resolve_concepts
from verichart.reasoning import LlmInferenceRule

derived = LlmInferenceRule(pinned_llm).apply(patient_facts)
coded = resolve_concepts(derived, your_resolvers)   # fills concept_code if your vocab has it
```

## Absence-as-negative, scoped

```python
from verichart.reasoning import AbsenceRule

hiv_panel_absence = AbsenceRule(
    name="hiv_panel_absence", version="v1",
    concept=("LOINC", "5221-7"),                          # the screening/test concept
    applies_to_source_types={"hiv_screening_panel"},
    infer=dict(label="PROBLEM", value="HIV negative (inferred — absent from screening panel)",
              concept_system="SNOMED-CT", concept_code="165816005"),
    confidence=0.85,
)
```

Fires only for a document whose `source_type` is in `applies_to_source_types` **and** only when
the screening concept is absent **globally** across the patient's facts — if the real result was
recorded anywhere, even in a document outside that scope (e.g. forwarded into a consult note),
the absence in this one document is never used to guess over it. An unrelated document
(a cardiology note that also doesn't mention HIV) never fires — that's the whole point.

## Temporal normalization

A regex only *finds* candidate phrases (`_TEMPORAL_CUES`: absolute — ISO/slash dates, "Month D,
YYYY", "in YYYY"; relative — "N days/weeks/months/years ago", "last week/month/year",
"yesterday", "today"). [`dateparser`](https://dateparser.readthedocs.io/) (pure Python, BSD-3,
actively maintained) does the actual date arithmetic — never hand-rolled `timedelta` math.

```python
from verichart.reasoning import assign_effective_dates

facts = assign_effective_dates(patient_facts, documents, document_dates={"note:1": "2026-06-01"})
```

For each fact with no `effective_date` yet: find the nearest temporal cue in the **same
sentence**; an absolute cue resolves regardless of anchor; a relative cue needs
`document_dates[doc_id]` or (falling back) the first absolute cue found anywhere in that
document — and is left **unresolved, not guessed**, when neither exists. Never overwrites an
existing `effective_date`.

This is a cue-phrase floor, not a general temporal-relation system — no such system exists as
an installable Python library (see the research doc). Month/year arithmetic is
calendar-approximate.

## Decay: `is_stale`

```python
from verichart.reasoning import DecayRule, is_stale

# a caller-supplied, CITED window -- not a verichart default:
a1c_window = DecayRule(
    concept_key="LOINC:4548-4", max_age_days=365,
    source="NCQA HEDIS MY2024 Comprehensive Diabetes Care measurement-year convention "
           "(a quality-reporting window, not a clinical-safety threshold — set your own "
           "after clinical review)",
)
is_stale(fact, as_of="2026-09-17", table=[a1c_window])
```

`DEFAULT_DECAY_RULES` is **empty** — no universal "this concept is valid for N days" knowledge
base exists. `DecayRule.source` is a required field (a dataclass, not a `TypedDict`, so a
missing citation raises at construction, not just by convention) — even "internal clinical
review, 2026-09-17" is an acceptable citation; a bare number is not. `is_stale` with no table
configured, or a fact with no `effective_date`, is always `False` — never claim staleness a fact
can't support. Matching is `concept_key`-first (imported from `verichart.reconcile`, not
redefined), `label` as a fallback.

## Manifest tie-in

```python
from veritract import build_manifest
manifest = build_manifest(llm, schema, extra={
    "rule_versions": {**rule_versions(policy), **reasoning_versions(rules)},
})
```

`reasoning_versions(rules)` → `{"reasoning:<name>": version, ...}`, merging alongside Phase 5's
`rule_versions(policy)` in the same dict (`"reconciliation"` vs `"reasoning:<name>"` — no key
collision). Disabling a rule changes `manifest_id`.
