import pytest

from veritract import Span
from verichart.facts import ClinicalFact, compute_fact_id


def _span(**over):
    base = dict(
        doc_id="note:1",
        source_type="clinical_note",
        char_start=10,
        char_end=20,
        text="metformin",
        provenance_type="direct",
    )
    base.update(over)
    return Span(**base)


# --- compute_fact_id ---


def test_compute_fact_id_is_deterministic():
    kw = dict(
        label="drug", value="metformin", concept_code=None, concept_system=None,
        span=_span(), manifest_id="m1",
    )
    assert compute_fact_id(**kw) == compute_fact_id(**kw)
    assert len(compute_fact_id(**kw)) == 64


def test_compute_fact_id_changes_with_value():
    base = dict(label="drug", concept_code=None, concept_system=None, span=_span(), manifest_id="m1")
    assert compute_fact_id(value="metformin", **base) != compute_fact_id(value="insulin", **base)


def test_compute_fact_id_changes_with_span_location():
    base = dict(
        label="drug", value="metformin", concept_code=None, concept_system=None, manifest_id="m1"
    )
    assert compute_fact_id(span=_span(char_start=10), **base) != compute_fact_id(
        span=_span(char_start=99), **base
    )


def test_compute_fact_id_changes_with_manifest():
    base = dict(label="drug", value="metformin", concept_code=None, concept_system=None, span=_span())
    assert compute_fact_id(manifest_id="m1", **base) != compute_fact_id(manifest_id="m2", **base)


def test_compute_fact_id_changes_with_concept_code():
    base = dict(label="drug", value="metformin", concept_system="RxNorm", span=_span(), manifest_id="m1")
    assert compute_fact_id(concept_code="6809", **base) != compute_fact_id(concept_code=None, **base)


def test_compute_fact_id_none_span_is_stable():
    kw = dict(
        label="drug", value="metformin", concept_code=None, concept_system=None,
        span=None, manifest_id=None,
    )
    assert compute_fact_id(**kw) == compute_fact_id(**kw)


# --- ClinicalFact shape ---


def test_clinical_fact_has_all_six_attribute_categories():
    keys = set(ClinicalFact.__annotations__)
    for expected in (
        # identity
        "fact_id", "patient_pseudonym", "label",
        # clinical content
        "concept_code", "concept_system", "concept_display",
        "value", "value_normalized", "effective_date", "assertion_status",
        # provenance
        "provenance_type", "span", "supporting_spans",
        "extraction_model", "extraction_model_digest", "extraction_confidence",
        # reconciliation
        "conflict_set_id", "resolution_method", "resolver_id", "rule_version",
        # versioning
        "manifest_id", "terminology_version",
        # annotation
        "note", "created_at",
    ):
        assert expected in keys, expected
