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
