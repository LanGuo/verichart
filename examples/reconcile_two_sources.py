"""
Phase 5: the article's own example — two sources disagree on a medication dose.

"Chart says 80mg, pharmacy feed says 40mg" — reconcile() detects the disagreement,
routes it through a source-rank policy (pharmacy feed outranks the dictated note),
and returns one surviving fact plus the full audit trail.

    python examples/reconcile_two_sources.py
"""
from verichart import ResolutionPolicy, reconcile
from verichart.facts import make_fact


def _fact(fact_id, value, source_type, confidence):
    span = {"doc_id": f"doc:{source_type}", "source_type": source_type,
           "char_start": 0, "char_end": len(value), "text": value, "provenance_type": "direct"}
    f = make_fact(
        label="MEDICATION", value=value, span=span, provenance_type="direct",
        confidence=confidence, note=None, patient_pseudonym="pt_1", effective_date=None,
        manifest_id=None, model_tag="mock", model_digest=None,
        created_at="2026-01-01T00:00:00+00:00",
    )
    f["fact_id"] = fact_id                       # stable ids for this demo, easier to read
    f["concept_code"], f["concept_system"] = "6809", "RxNorm"   # both sources resolved to metformin
    return f


chart_note = _fact("chart_note", "80 mg", "clinical_note", confidence=0.75)
pharmacy_feed = _fact("pharmacy_feed", "40 mg", "pharmacy_feed", confidence=0.9)

policy = ResolutionPolicy(
    default="source_rank",
    source_rank=["pharmacy_feed", "discharge_summary", "clinical_note"],
    rule_version="v1",
)

reconciled, conflict_sets, resolutions = reconcile([chart_note, pharmacy_feed], policy)

print(f"conflict_sets: {len(conflict_sets)}, resolutions: {len(resolutions)}\n")
for cs, res in zip(conflict_sets, resolutions):
    print(f"  kind={cs['kind']}  members={cs['member_fact_ids']}")
    print(f"  method={res['method']}  winner={res['winning_fact_id']}  rule_version={res['rule_version']}")
    print(f"  rationale: {res['rationale']}\n")

print("surviving facts:")
for f in reconciled:
    print(f"  {f['fact_id']:<14} value={f['value']!r:<8} "
          f"resolution_method={f['resolution_method']}  conflict_set_id={f['conflict_set_id'][:12]}...")

print("\nswitch to 'most_recent' or 'highest_confidence' and the winner changes:")
for default in ("highest_confidence", "most_recent"):
    r2, _, res2 = reconcile([chart_note, pharmacy_feed], ResolutionPolicy(default=default))
    print(f"  default={default:<18} -> winner={res2[0]['winning_fact_id']}")
