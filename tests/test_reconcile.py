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
