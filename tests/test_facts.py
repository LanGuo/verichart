import pytest

from veritract import MockLLM, Span, build_manifest, extract
from verichart.facts import ClinicalFact, compute_fact_id


SOURCE = (
    "In a randomized trial, 248 patients with type 2 diabetes received "
    "metformin 500mg twice daily or placebo."
)
SCHEMA = {
    "type": "object",
    "properties": {
        "sample_size": {"type": "string"},
        "drug": {"type": "string"},
    },
    "required": ["sample_size", "drug"],
}


def _result(drug="metformin 500mg twice daily", **extract_kw):
    llm = MockLLM()
    llm.register("Extract", {"sample_size": "248 patients", "drug": drug})
    manifest = build_manifest(llm, SCHEMA)
    result = extract(
        SOURCE, SCHEMA, llm, doc_id="trial:1", source_type="text",
        manifest=manifest, **extract_kw,
    )
    return result, manifest


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


# --- to_facts: grounded fields ---


def test_to_facts_grounded_fields_carry_span_and_provenance():
    from verichart import to_facts

    result, manifest = _result()
    facts = to_facts(result, patient_pseudonym="pt_1", manifest=manifest)
    by_label = {f["label"]: f for f in facts}
    assert set(by_label) == {"sample_size", "drug"}
    for f in facts:
        assert f["span"] is not None
        assert f["provenance_type"] in ("direct", "paraphrased", "inferred")
        assert f["supporting_spans"] == [f["span"]]
        assert 0.0 <= f["extraction_confidence"] <= 1.0
        assert f["patient_pseudonym"] == "pt_1"
        assert f["assertion_status"] == "unknown"
        assert f["manifest_id"] == manifest["manifest_id"]
        assert f["extraction_model"] == manifest["model_tag"]
        assert f["extraction_model_digest"] == manifest["model_digest"]
        assert f["resolution_method"] == "none"
        assert f["concept_code"] is None
        assert f["note"] is None
        assert f["created_at"]


def test_to_facts_confidence_is_renormalized():
    from verichart import to_facts

    result, manifest = _result()
    facts = to_facts(result, manifest=manifest)
    assert any(f["extraction_confidence"] == 1.0 for f in facts)  # exact match: 100 -> 1.0
    assert all(f["extraction_confidence"] <= 1.0 for f in facts)


def test_to_facts_fact_id_is_deterministic_across_calls():
    from verichart import to_facts

    result, manifest = _result()
    a = to_facts(result, manifest=manifest, created_at="2026-01-01T00:00:00+00:00")
    b = to_facts(result, manifest=manifest, created_at="2026-06-01T00:00:00+00:00")
    assert [f["fact_id"] for f in a] == [f["fact_id"] for f in b]


def test_to_facts_uses_result_manifest_id_when_no_manifest_arg():
    from verichart import to_facts

    result, _ = _result()
    facts = to_facts(result)
    assert all(f["manifest_id"] == result.manifest_id for f in facts)
    assert all(f["extraction_model"] is None for f in facts)


def test_to_facts_does_not_mutate_result():
    from verichart import to_facts

    result, manifest = _result()
    before = (dict(result.extracted), list(result.quarantined))
    to_facts(result, manifest=manifest)
    assert (dict(result.extracted), list(result.quarantined)) == before


# --- to_facts: quarantined fields + edge cases ---


def test_to_facts_quarantined_field_is_unverified_not_dropped():
    from verichart import to_facts

    # "insulin glargine 10 units" is not in SOURCE -> quarantined after fuzzy grounding
    result, manifest = _result(drug="insulin glargine 10 units", mode="fuzzy")
    facts = to_facts(result, manifest=manifest)
    drug = next(f for f in facts if f["label"] == "drug")
    assert drug["provenance_type"] == "unverified"
    assert drug["span"] is None
    assert drug["supporting_spans"] == []
    assert drug["extraction_confidence"] == 0.0
    assert drug["note"]  # carries the quarantine reason
    assert drug["manifest_id"] == manifest["manifest_id"]


def test_to_facts_empty_result_returns_empty_list():
    from veritract import ExtractionResult

    from verichart import to_facts

    assert to_facts(ExtractionResult(extracted={}, quarantined=[])) == []


def test_to_facts_order_is_extracted_then_quarantined():
    from verichart import to_facts

    result, manifest = _result(drug="insulin glargine 10 units", mode="fuzzy")
    facts = to_facts(result, manifest=manifest)
    unverified_positions = [i for i, f in enumerate(facts) if f["provenance_type"] == "unverified"]
    grounded_positions = [i for i, f in enumerate(facts) if f["provenance_type"] != "unverified"]
    assert max(grounded_positions) < min(unverified_positions)


def test_to_facts_effective_date_passthrough():
    from verichart import to_facts

    result, manifest = _result()
    facts = to_facts(result, manifest=manifest, effective_date="2025-03-14")
    assert facts and all(f["effective_date"] == "2025-03-14" for f in facts)


def test_to_facts_no_grounding_mode_is_all_inferred():
    from verichart import to_facts

    result, manifest = _result(mode="no-grounding")
    facts = to_facts(result, manifest=manifest)
    assert facts and all(
        f["provenance_type"] == "inferred" and f["span"] is None for f in facts
    )


def test_to_facts_identical_fact_gets_same_id_across_results():
    """The dedup-key property: same (label, value, span, manifest) -> same fact_id."""
    from verichart import to_facts

    r1, m = _result(drug="metformin 500mg twice daily")
    r2, _ = _result(drug="insulin glargine 10 units", mode="fuzzy")  # only `drug` differs
    id1 = {f["label"]: f["fact_id"] for f in to_facts(r1, manifest=m)}
    id2 = {f["label"]: f["fact_id"] for f in to_facts(r2, manifest=m)}
    assert id1["sample_size"] == id2["sample_size"]  # unchanged field -> same id
    assert id1["drug"] != id2["drug"]                # changed value -> different id


def test_to_facts_skips_empty_value_fields():
    """veritract can promote an empty-string field to 'inferred'; it is not a fact."""
    from veritract import ExtractionResult, GroundedField

    from verichart import to_facts

    result = ExtractionResult(
        extracted={
            "real": GroundedField(value="metformin", span=None, confidence=80.0),
            "blank": GroundedField(value="   ", span=None, confidence=80.0),
        },
        quarantined=[],
    )
    labels = {f["label"] for f in to_facts(result)}
    assert labels == {"real"}


def test_public_api():
    from verichart import ClinicalFact, compute_fact_id, to_facts

    assert callable(to_facts) and callable(compute_fact_id)
    assert "fact_id" in ClinicalFact.__annotations__
