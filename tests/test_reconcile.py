import pytest

from verichart.reconcile import ConflictSet, Resolution, concept_key, date_bucket


def _fact(**over):
    base = dict(
        fact_id="f1", patient_pseudonym="pt_1", label="MEDICATION",
        concept_code=None, concept_system=None, concept_display=None,
        value="metformin", value_normalized=None, effective_date=None,
        assertion_status="confirmed",
        provenance_type="direct", span=None, supporting_spans=[],
        extraction_model=None, extraction_model_digest=None, extraction_confidence=0.9,
        conflict_set_id=None, resolution_method="none", resolver_id=None, rule_version=None,
        manifest_id=None, terminology_version=None, note=None,
        created_at="2026-01-01T00:00:00+00:00",
    )
    base.update(over)
    return base


# --- concept_key ---


def test_concept_key_prefers_resolved_concept():
    f = _fact(concept_code="6809", concept_system="RxNorm", value="Metformin 500mg")
    assert concept_key(f) == "RxNorm:6809"


def test_concept_key_falls_back_to_normalized_label_value():
    a = _fact(label="MEDICATION", value="Metformin")
    b = _fact(label="MEDICATION", value="  metformin  ")
    assert concept_key(a) == concept_key(b) == "MEDICATION:metformin"


def test_concept_key_fallback_distinguishes_labels():
    a = _fact(label="MEDICATION", value="metformin")
    b = _fact(label="PROBLEM", value="metformin")
    assert concept_key(a) != concept_key(b)


# --- date_bucket ---


def test_date_bucket_truncates_to_day():
    assert date_bucket(_fact(effective_date="2026-03-14T09:00:00")) == "2026-03-14"


def test_date_bucket_none_stays_none():
    assert date_bucket(_fact(effective_date=None)) is None


# --- ConflictSet / Resolution shape ---


def test_conflict_set_shape():
    cs = ConflictSet(
        conflict_set_id="cs1", concept_key="RxNorm:6809", date_bucket=None,
        member_fact_ids=["f1", "f2"], kind="value_disagreement",
    )
    assert cs["kind"] == "value_disagreement"
    assert set(cs) == {"conflict_set_id", "concept_key", "date_bucket", "member_fact_ids", "kind"}


def test_resolution_shape():
    r = Resolution(
        conflict_set_id="cs1", winning_fact_id="f1", method="highest_confidence",
        resolver_id=None, rule_version="v1", rationale="highest confidence member",
        decided_at="2026-01-01T00:00:00+00:00",
    )
    assert r["winning_fact_id"] == "f1"


# --- group_facts + detect_conflicts ---


def test_solo_fact_is_not_a_conflict():
    from verichart.reconcile import detect_conflicts, group_facts

    groups = group_facts([_fact(fact_id="f1")])
    assert detect_conflicts(groups) == []


def test_identical_value_is_duplicate():
    from verichart.reconcile import detect_conflicts, group_facts

    facts = [_fact(fact_id="f1", value="metformin"), _fact(fact_id="f2", value="Metformin")]
    (cs,) = detect_conflicts(group_facts(facts))
    assert cs["kind"] == "duplicate"
    assert sorted(cs["member_fact_ids"]) == ["f1", "f2"]


def test_numeric_within_tolerance_is_duplicate_beyond_is_disagreement():
    from verichart.reconcile import detect_conflicts, group_facts

    facts = [_fact(fact_id="f1", label="MEDICATION", value="metformin 80 mg"),
             _fact(fact_id="f2", label="MEDICATION", value="metformin 81 mg")]
    # same concept_key requires identical fallback value text, so use resolved concept codes
    for f in facts:
        f["concept_code"], f["concept_system"] = "6809", "RxNorm"

    (cs,) = detect_conflicts(group_facts(facts), numeric_tolerance=1.0)
    assert cs["kind"] == "duplicate"
    (cs,) = detect_conflicts(group_facts(facts), numeric_tolerance=0.0)
    assert cs["kind"] == "value_disagreement"


def test_article_dose_disagreement():
    from verichart.reconcile import detect_conflicts, group_facts

    facts = [
        _fact(fact_id="chart", value="80 mg", concept_code="6809", concept_system="RxNorm"),
        _fact(fact_id="pharmacy", value="40 mg", concept_code="6809", concept_system="RxNorm"),
    ]
    (cs,) = detect_conflicts(group_facts(facts))
    assert cs["kind"] == "value_disagreement"
    assert cs["concept_key"] == "RxNorm:6809"


def test_assertion_disagreement_beats_value_equality():
    from verichart.reconcile import detect_conflicts, group_facts

    facts = [
        _fact(fact_id="f1", value="diabetes", assertion_status="confirmed",
             concept_code="44054006", concept_system="SNOMED-CT"),
        _fact(fact_id="f2", value="diabetes", assertion_status="ruled_out",
             concept_code="44054006", concept_system="SNOMED-CT"),
    ]
    (cs,) = detect_conflicts(group_facts(facts))
    assert cs["kind"] == "assertion_disagreement"


def test_different_date_buckets_are_separate_groups():
    from verichart.reconcile import detect_conflicts, group_facts

    facts = [
        _fact(fact_id="f1", value="80 mg", concept_code="6809", concept_system="RxNorm",
             effective_date="2026-01-01"),
        _fact(fact_id="f2", value="40 mg", concept_code="6809", concept_system="RxNorm",
             effective_date="2026-06-01"),
    ]
    assert detect_conflicts(group_facts(facts)) == []


def test_unknown_dates_are_one_bucket():
    from verichart.reconcile import detect_conflicts, group_facts

    facts = [_fact(fact_id="f1", value="80 mg", concept_code="6809", concept_system="RxNorm"),
             _fact(fact_id="f2", value="40 mg", concept_code="6809", concept_system="RxNorm")]
    assert len(detect_conflicts(group_facts(facts))) == 1


def test_hierarchy_joins_descendant_into_ancestor_group():
    from verichart.reconcile import detect_conflicts, group_facts

    class Hierarchy:
        def is_descendant(self, code, ancestor, system):
            return system == "SNOMED-CT" and code == "44054006" and ancestor == "73211009"

    facts = [
        _fact(fact_id="f1", value="diabetes mellitus", concept_code="73211009",
             concept_system="SNOMED-CT"),
        _fact(fact_id="f2", value="type 2 diabetes", concept_code="44054006",
             concept_system="SNOMED-CT"),
    ]
    groups = group_facts(facts, hierarchy=Hierarchy())
    assert len(groups) == 1
    (cs,) = detect_conflicts(groups)
    assert sorted(cs["member_fact_ids"]) == ["f1", "f2"]


def test_conflict_set_id_order_independent():
    from verichart.reconcile import detect_conflicts, group_facts

    a = [_fact(fact_id="f1", value="80 mg", concept_code="6809", concept_system="RxNorm"),
         _fact(fact_id="f2", value="40 mg", concept_code="6809", concept_system="RxNorm")]
    b = list(reversed(a))
    (cs_a,) = detect_conflicts(group_facts(a))
    (cs_b,) = detect_conflicts(group_facts(b))
    assert cs_a["conflict_set_id"] == cs_b["conflict_set_id"]


# --- ResolutionPolicy.method_for ---


def _cs(kind="value_disagreement", concept_key="RxNorm:6809"):
    return ConflictSet(conflict_set_id="cs1", concept_key=concept_key, date_bucket=None,
                       member_fact_ids=["a", "b"], kind=kind)


def test_method_for_review_wins_over_everything():
    from verichart.reconcile import ResolutionPolicy

    policy = ResolutionPolicy(default="highest_confidence",
                              by_kind={"value_disagreement": "most_recent"},
                              route_to_review_when=lambda cs: True)
    assert policy.method_for(_cs()) == "human_review"


def test_method_for_by_kind_then_by_concept_system_then_default():
    from verichart.reconcile import ResolutionPolicy

    policy = ResolutionPolicy(default="highest_confidence",
                              by_concept_system={"RxNorm": "source_rank"})
    assert policy.method_for(_cs()) == "source_rank"          # by_concept_system

    policy2 = ResolutionPolicy(default="highest_confidence",
                               by_kind={"value_disagreement": "most_recent"},
                               by_concept_system={"RxNorm": "source_rank"})
    assert policy2.method_for(_cs()) == "most_recent"          # by_kind beats by_concept_system

    policy3 = ResolutionPolicy(default="highest_confidence")
    assert policy3.method_for(_cs()) == "highest_confidence"   # default


# --- resolve_conflict_set: built-in dispatch ---


def test_resolve_highest_confidence_ties_broken_by_fact_id():
    from verichart.reconcile import ResolutionPolicy, resolve_conflict_set

    members = [_fact(fact_id="f2", extraction_confidence=0.9),
               _fact(fact_id="f1", extraction_confidence=0.9),
               _fact(fact_id="f3", extraction_confidence=0.5)]
    res = resolve_conflict_set(_cs(), members, ResolutionPolicy())
    assert res["winning_fact_id"] == "f1"
    assert res["method"] == "highest_confidence"


def test_resolve_most_recent_prefers_effective_date_over_created_at():
    from verichart.reconcile import ResolutionPolicy, resolve_conflict_set

    members = [_fact(fact_id="f1", effective_date="2026-01-01", created_at="2026-09-01T00:00:00+00:00"),
               _fact(fact_id="f2", effective_date="2026-06-01", created_at="2026-01-01T00:00:00+00:00")]
    res = resolve_conflict_set(_cs(), members, ResolutionPolicy(default="most_recent"))
    assert res["winning_fact_id"] == "f2"


def test_resolve_source_rank_prefers_ranked_over_unranked():
    from verichart.reconcile import ResolutionPolicy, resolve_conflict_set

    def _f(fact_id, source_type):
        f = _fact(fact_id=fact_id)
        f["span"] = {"doc_id": "d", "source_type": source_type, "char_start": 0, "char_end": 1,
                    "text": "x", "provenance_type": "direct"}
        return f

    members = [_f("pharmacy", "pharmacy_feed"), _f("note", "clinical_note")]
    policy = ResolutionPolicy(default="source_rank",
                              source_rank=["pharmacy_feed", "discharge_summary"])
    res = resolve_conflict_set(_cs(), members, policy)
    assert res["winning_fact_id"] == "pharmacy"


def test_resolve_named_rule_called_with_conflict_set_and_members():
    from verichart.reconcile import ResolutionPolicy, resolve_conflict_set

    seen = {}

    def my_rule(cs, members):
        seen["cs"] = cs
        seen["members"] = members
        return members[-1]["fact_id"]

    members = [_fact(fact_id="f1"), _fact(fact_id="f2")]
    policy = ResolutionPolicy(by_kind={"value_disagreement": "prefer_last"},
                              named_rules={"prefer_last": my_rule})
    res = resolve_conflict_set(_cs(), members, policy)
    assert res["winning_fact_id"] == "f2"
    assert res["method"] == "named_rule"
    assert res["resolver_id"] == "prefer_last"
    assert seen["members"] == members


def test_resolve_human_review_defers():
    from verichart.reconcile import ResolutionPolicy, resolve_conflict_set

    members = [_fact(fact_id="f1"), _fact(fact_id="f2")]
    policy = ResolutionPolicy(route_to_review_when=lambda cs: True)
    res = resolve_conflict_set(_cs(), members, policy)
    assert res["winning_fact_id"] is None
    assert res["method"] == "human_review"


def test_resolve_unknown_method_raises():
    from verichart.reconcile import ResolutionPolicy, resolve_conflict_set

    policy = ResolutionPolicy(default="made_up_method")
    with pytest.raises(ValueError, match="made_up_method"):
        resolve_conflict_set(_cs(), [_fact(fact_id="f1"), _fact(fact_id="f2")], policy)


# --- reconcile() ---


def test_reconcile_article_dose_disagreement():
    from verichart.reconcile import ResolutionPolicy, reconcile

    facts = [
        _fact(fact_id="chart", value="80 mg", concept_code="6809", concept_system="RxNorm",
             extraction_confidence=0.7),
        _fact(fact_id="pharmacy", value="40 mg", concept_code="6809", concept_system="RxNorm",
             extraction_confidence=0.95),
    ]
    reconciled, conflict_sets, resolutions = reconcile(facts, ResolutionPolicy())

    assert len(conflict_sets) == 1 and conflict_sets[0]["kind"] == "value_disagreement"
    assert len(resolutions) == 1
    assert resolutions[0]["method"] == "highest_confidence"
    assert resolutions[0]["rule_version"] == "v1"

    survivors = {f["fact_id"] for f in reconciled}
    assert survivors == {"pharmacy"}                       # higher confidence wins
    winner = reconciled[0]
    assert winner["conflict_set_id"] == conflict_sets[0]["conflict_set_id"]
    assert winner["resolver_id"] is None
    assert "chart" in conflict_sets[0]["member_fact_ids"]   # loser recoverable via the audit trail


def test_reconcile_switching_policy_changes_winner():
    from verichart.reconcile import ResolutionPolicy, reconcile

    # same date bucket (both None) so they conflict; confidence and recency disagree
    facts = [
        _fact(fact_id="chart", value="80 mg", concept_code="6809", concept_system="RxNorm",
             extraction_confidence=0.7, created_at="2026-09-01T00:00:00+00:00"),
        _fact(fact_id="pharmacy", value="40 mg", concept_code="6809", concept_system="RxNorm",
             extraction_confidence=0.95, created_at="2026-01-01T00:00:00+00:00"),
    ]
    by_confidence, _, _ = reconcile(facts, ResolutionPolicy(default="highest_confidence"))
    assert {f["fact_id"] for f in by_confidence} == {"pharmacy"}

    by_recency, _, _ = reconcile(facts, ResolutionPolicy(default="most_recent"))
    assert {f["fact_id"] for f in by_recency} == {"chart"}   # most recent, not highest confidence


def test_reconcile_duplicate_unions_supporting_spans():
    from verichart.reconcile import ResolutionPolicy, reconcile

    def _span(cs, ce):
        return {"doc_id": "d1", "source_type": "text", "char_start": cs, "char_end": ce,
               "text": "x", "provenance_type": "direct"}

    a = _fact(fact_id="a", value="metformin", concept_code="6809", concept_system="RxNorm",
             extraction_confidence=0.8)
    a["supporting_spans"] = [_span(0, 9)]
    b = _fact(fact_id="b", value="metformin", concept_code="6809", concept_system="RxNorm",
             extraction_confidence=0.6)
    b["supporting_spans"] = [_span(50, 59)]

    reconciled, conflict_sets, _ = reconcile([a, b], ResolutionPolicy())
    assert conflict_sets[0]["kind"] == "duplicate"
    (winner,) = reconciled
    assert winner["fact_id"] == "a"
    assert len(winner["supporting_spans"]) == 2


def test_reconcile_human_review_keeps_every_member():
    from verichart.reconcile import ResolutionPolicy, reconcile

    facts = [_fact(fact_id="a", value="80 mg", concept_code="6809", concept_system="RxNorm"),
             _fact(fact_id="b", value="40 mg", concept_code="6809", concept_system="RxNorm")]
    reconciled, conflict_sets, resolutions = reconcile(
        facts, ResolutionPolicy(route_to_review_when=lambda cs: True)
    )
    assert {f["fact_id"] for f in reconciled} == {"a", "b"}
    assert all(f["resolution_method"] == "human_review" for f in reconciled)
    assert resolutions[0]["winning_fact_id"] is None


def test_reconcile_solo_fact_passes_through_unchanged():
    from verichart.reconcile import ResolutionPolicy, reconcile

    solo = _fact(fact_id="only")
    reconciled, conflict_sets, resolutions = reconcile([solo], ResolutionPolicy())
    assert reconciled == [solo]
    assert conflict_sets == [] and resolutions == []


def test_reconcile_does_not_mutate_input():
    from verichart.reconcile import ResolutionPolicy, reconcile

    facts = [_fact(fact_id="a", value="80 mg", concept_code="6809", concept_system="RxNorm"),
             _fact(fact_id="b", value="40 mg", concept_code="6809", concept_system="RxNorm")]
    snapshot = [dict(f) for f in facts]
    reconcile(facts, ResolutionPolicy())
    assert [dict(f) for f in facts] == snapshot


def test_rule_versions_feeds_manifest_and_changes_id():
    from veritract import MockLLM, build_manifest

    from verichart.reconcile import ResolutionPolicy, rule_versions

    llm = MockLLM()
    schema = {"type": "object", "properties": {"x": {"type": "string"}}}
    m1 = build_manifest(llm, schema, extra={"rule_versions": rule_versions(ResolutionPolicy(rule_version="v1"))})
    m2 = build_manifest(llm, schema, extra={"rule_versions": rule_versions(ResolutionPolicy(rule_version="v2"))})
    assert m1["manifest_id"] != m2["manifest_id"]
    assert m1["rule_versions"] == {"reconciliation": "v1"}


def test_public_api():
    from verichart import ConflictSet as TopConflictSet
    from verichart import Resolution as TopResolution
    from verichart import ResolutionPolicy as TopPolicy
    from verichart import concept_key as top_concept_key
    from verichart import reconcile as top_reconcile
    from verichart import rule_versions as top_rule_versions

    assert callable(top_reconcile) and callable(top_concept_key) and callable(top_rule_versions)
    assert isinstance(TopPolicy(), TopPolicy)
    assert set(TopConflictSet.__annotations__) == {
        "conflict_set_id", "concept_key", "date_bucket", "member_fact_ids", "kind"
    }
    assert "winning_fact_id" in TopResolution.__annotations__


# --- VeritractLlmResolver (mock LLM; real Ollama test in test_reconcile_llm.py) ---


def test_veritract_llm_resolver_with_mock_llm():
    from veritract import MockLLM

    from verichart.reconcile import ResolutionPolicy, VeritractLlmResolver, reconcile

    llm = MockLLM()
    llm.register("disagree", {"winner": "option_b", "rationale": "pharmacy feed is more reliable"})
    resolver = VeritractLlmResolver(llm)

    facts = [_fact(fact_id="a", value="80 mg", concept_code="6809", concept_system="RxNorm"),
             _fact(fact_id="b", value="40 mg", concept_code="6809", concept_system="RxNorm")]
    policy = ResolutionPolicy(default="llm_assisted", llm_resolver=resolver)
    reconciled, conflict_sets, resolutions = reconcile(facts, policy)

    assert resolutions[0]["method"] == "llm_assisted"
    assert resolutions[0]["winning_fact_id"] == "b"
    assert resolutions[0]["resolver_id"] == resolver.version
    assert resolutions[0]["rationale"] == "pharmacy feed is more reliable"
    assert {f["fact_id"] for f in reconciled} == {"b"}


def test_veritract_llm_resolver_requires_two_members():
    from veritract import MockLLM

    from verichart.reconcile import VeritractLlmResolver

    resolver = VeritractLlmResolver(MockLLM())
    winner, rationale = resolver.resolve(
        _cs(), [_fact(fact_id="a"), _fact(fact_id="b"), _fact(fact_id="c")], None
    )
    assert winner is None and "2-way" in rationale


def test_resolve_conflict_set_llm_assisted_requires_resolver():
    from verichart.reconcile import ResolutionPolicy, resolve_conflict_set

    policy = ResolutionPolicy(default="llm_assisted")
    with pytest.raises(ValueError, match="llm_resolver"):
        resolve_conflict_set(_cs(), [_fact(fact_id="a"), _fact(fact_id="b")], policy)
