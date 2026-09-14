# Reconciliation (Phase 5)

Given `ClinicalFact[]` for one patient drawn from multiple documents or sources, deduplicate,
detect conflicts, and resolve them — recording resolver identity and rule version on the
surviving fact. The differentiated layer: the article's "nobody does this."

## Reconciliation builds on quarantine

veritract already preserves, annotates, and routes an unverifiable field instead of dropping or
silently accepting it (`result.quarantined`, honest uncertainty over silent failure). A
`ConflictSet` is the same move one level up:

- **quarantine** — "this one field failed verification, here's why"
- **conflict set** — "these N facts disagree, here's how, here's what backs each one"

Nothing is ever silently dropped: a resolved conflict's losing facts are removed from
`reconciled` but stay recoverable via `ConflictSet.member_fact_ids` (`fact_id`s that still
exist, unchanged, in the `facts` list you passed in) and the matching `Resolution`'s rationale.
A `human_review` outcome drops nothing at all — every member stays in `reconciled`, tagged.

## The pipeline

```python
from verichart import reconcile, ResolutionPolicy

policy = ResolutionPolicy(
    default="highest_confidence",
    by_concept_system={"RxNorm": "most_recent"},
    source_rank=["pharmacy_feed", "discharge_summary", "clinical_note"],
    named_rules={"lab_panel_supersede": my_callable},
    route_to_review_when=lambda cs: cs["kind"] == "assertion_disagreement",
    rule_version="v1",
)
reconciled, conflict_sets, resolutions = reconcile(patient_facts, policy)
```

1. **`group_facts`** — groups by `(concept_key, date_bucket)`, exactly.
2. **`detect_conflicts`** — one `ConflictSet` per group of ≥2 facts, classified deterministically.
3. **`resolve_conflict_set`** (called per conflict set inside `reconcile`) — routes through the
   policy, cheapest layer first.
4. **`reconcile`** assembles the merged fact list plus the full audit trail.

## Detection is deterministic

You want "these facts conflict" to be reproducible and explainable — never a model call.

- **`concept_key(fact)`** — `"<system>:<code>"` when Phase 3 resolved the fact, else
  `"<label>:<normalized value>"`. The fallback is exactly the gap flagged in Phases 1–2: without
  terminology resolution, two facts for the same drug only group when their surface text matches
  exactly. Resolve first (Phase 3) if you want cross-document dedup to actually work.
- **`date_bucket(fact)`** — `effective_date` truncated to the day. `None` is its **own** bucket
  ("unknown date"), not a wildcard — two undated facts are assumed the same episode; two facts
  with different explicit dates are assumed different episodes and never conflict. Phase 6's
  real temporal extraction will make this less blunt.
- **Pairwise classification**, in order: an incompatible `assertion_status` pair (`confirmed` vs
  `ruled_out`, `confirmed` vs `family_history`, `ruled_out` vs `family_history`) →
  `assertion_disagreement`; else both values numeric and within `numeric_tolerance` (default
  **`0.0`** — a dose difference matters, so no default fuzz) → `duplicate`, beyond it →
  `value_disagreement` (the article's "chart says 80mg, pharmacy feed says 40mg"); else
  normalized-value-equal → `duplicate`; else `value_disagreement`. A group's `kind` is the worst
  kind found across any pair.
- **`ConceptHierarchy`** — a hook for subsumption ("Type 2 diabetes" is-a "diabetes mellitus"),
  inert by default. verichart ships no hierarchy data (same rule as Phase 3's "no vocabulary
  content shipped") — supply your own SNOMED-derived implementation to activate it.

## Resolution is layered, cheapest first

Not "write 500 rules up front." `ResolutionPolicy.method_for` routes a `ConflictSet`:
`route_to_review_when` → `by_kind` → `by_concept_system` → `default`.

1. **Built-in policy rules** — `highest_confidence`, `most_recent` (`effective_date`, falling
   back to `created_at`), `source_rank` (by `span.source_type`, unranked sources lose to any
   ranked one). Deterministic ties break on the lowest `fact_id`. Dispatch the mechanical
   majority: dedup and obvious recency.
2. **`named_rules`** — `{"name": callable(conflict_set, members) -> fact_id | None}`. Arbitrary
   domain logic, still deterministic and inspectable; the surviving fact is stamped
   `resolution_method="named_rule"`, `resolver_id="<name>"`.
3. **`llm_assisted`** — `policy.llm_resolver` (an `LlmResolver`: `.version` +
   `.resolve(conflict_set, members, context) -> (fact_id | None, rationale)`) is asked "which
   value is more plausible." Auditable because the answer + rationale are logged as the
   `Resolution`; reproducible **only** because the model is pinned — the same argument as Phase
   1's manifest, and why a hosted API could not fill this slot. `VeritractLlmResolver` (below)
   is a ready adapter.
4. **`human_review`** — the escape hatch. Every member of the conflict set stays in
   `reconciled`, tagged `resolution_method="human_review"`; `Resolution.winning_fact_id is None`.
   Nothing is guessed.

This mirrors JSL's own framing (capability #41: *"a change to which source wins a medication
conflict creates a new version"*) — you version rules, not model vibes — while staying flexible.

### `VeritractLlmResolver`

```python
from verichart import ResolutionPolicy, reconcile
from verichart.reconcile import VeritractLlmResolver
from veritract import LLMClient

resolver = VeritractLlmResolver(LLMClient(model="gemma3:1b", temperature=0.0, seed=42))
policy = ResolutionPolicy(by_kind={"value_disagreement": "llm_assisted"}, llm_resolver=resolver)
reconciled, conflict_sets, resolutions = reconcile(facts, policy, documents={"note:1": note_text})
```

Builds a two-way choice schema from the conflict set's member values and the shared source text
(`documents`), asks the model to pick `"a"` or `"b"` and explain why, maps the answer back to a
`fact_id`. `.version` includes the model tag; pin the model (same argument as everywhere else in
verichart) if you need this decision to be replayable.

## Manifest tie-in

```python
from veritract import build_manifest
manifest = build_manifest(llm, schema, extra={"rule_versions": rule_versions(policy)})
```

`rule_versions(policy)` → `{"reconciliation": policy.rule_version}`. Bump `rule_version`
whenever the policy changes — a different `rule_version` string changes `manifest_id`, so a
point-in-time replay reproduces the reconciliation decisions that were in force at the time.

## What `reconcile()` does to the fact list

- Solo facts (no conflicting partner) pass through **unchanged**.
- A resolved `duplicate` group: one surviving fact, stamped, with every member's
  `supporting_spans` **unioned** onto it — they all support the same restated fact.
- A resolved `value_disagreement` / `assertion_disagreement` group: one surviving fact, stamped;
  the losers are dropped from `reconciled` (their content is *not* merged in — it would be
  misleading to claim a losing span supports the winning value) but remain fully recoverable.
- An unresolved (`human_review`) group: every member kept, every member tagged
  `resolution_method="human_review"`.
- `fact_id` is **not** recomputed — reconciliation doesn't touch `concept_code` / `value` / `span`.

## Not implemented

- Real SNOMED subsumption data (the `ConceptHierarchy` hook exists; no vocabulary ships).
- Temporal reasoning beyond the same-day/unknown bucket (Phase 6).
- OMOP/FHIR emission of the reconciled facts (Phase 7).
