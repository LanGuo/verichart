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
