import pytest

from verichart.facts import make_fact


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


# --- apply_rules / MockRule ---


def test_mock_rule_facts_are_appended():
    from verichart.reasoning import apply_rules

    class MockRule:
        name = "mock"
        version = "v1"

        def apply(self, facts):
            return [_fact(fact_id="derived", value="derived fact")]

    facts = [_fact(fact_id="f1")]
    out = apply_rules(facts, [MockRule()])
    assert {f["fact_id"] for f in out} == {"f1", "derived"}


def test_apply_rules_does_not_mutate_input():
    from verichart.reasoning import apply_rules

    class MockRule:
        name = "mock"
        version = "v1"

        def apply(self, facts):
            return [_fact(fact_id="derived")]

    facts = [_fact(fact_id="f1")]
    snapshot = [dict(f) for f in facts]
    apply_rules(facts, [MockRule()])
    assert [dict(f) for f in facts] == snapshot


def test_disabling_a_rule_removes_exactly_its_facts():
    from verichart.reasoning import apply_rules

    class RuleA:
        name, version = "a", "v1"
        def apply(self, facts):
            return [_fact(fact_id="from_a")]

    class RuleB:
        name, version = "b", "v1"
        def apply(self, facts):
            return [_fact(fact_id="from_b")]

    facts = [_fact(fact_id="f1")]
    with_both = {f["fact_id"] for f in apply_rules(facts, [RuleA(), RuleB()])}
    with_a_only = {f["fact_id"] for f in apply_rules(facts, [RuleA()])}
    assert with_both - with_a_only == {"from_b"}
    assert with_a_only == {"f1", "from_a"}


# --- ConceptTriggerRule ---


def _t2dm_rule(confidence=0.7):
    from verichart.reasoning import ConceptTriggerRule

    return ConceptTriggerRule(
        name="metformin_implies_t2dm",
        version="v1",
        trigger_concept=("RxNorm", "6809"),
        infer=dict(label="PROBLEM", value="type 2 diabetes mellitus (inferred)",
                  concept_system="SNOMED-CT", concept_code="44054006"),
        confidence=confidence,
    )


def test_concept_trigger_rule_fires_on_matching_concept():
    trigger = _fact(fact_id="med1", label="MEDICATION", value="metformin",
                    concept_code="6809", concept_system="RxNorm", patient_pseudonym="pt_1")
    (derived,) = _t2dm_rule().apply([trigger])
    assert derived["label"] == "PROBLEM"
    assert derived["concept_code"] == "44054006" and derived["concept_system"] == "SNOMED-CT"
    assert derived["provenance_type"] == "inferred"
    assert derived["span"] is None
    assert derived["extraction_confidence"] == 0.7
    assert derived["assertion_status"] == "unknown"
    assert derived["patient_pseudonym"] == "pt_1"
    assert derived["extraction_model"] == "v1"
    assert "med1" in derived["note"]


def test_concept_trigger_rule_no_double_inference():
    trigger = _fact(fact_id="med1", concept_code="6809", concept_system="RxNorm")
    already = _fact(fact_id="dx1", label="PROBLEM", concept_code="44054006",
                    concept_system="SNOMED-CT")
    assert _t2dm_rule().apply([trigger, already]) == []


def test_concept_trigger_rule_no_double_inference_against_prior_inference():
    trigger = _fact(fact_id="med1", concept_code="6809", concept_system="RxNorm")
    rule = _t2dm_rule()
    first_pass = rule.apply([trigger])
    assert len(first_pass) == 1
    second_pass = rule.apply([trigger, *first_pass])
    assert second_pass == []


def test_concept_trigger_rule_ignores_non_matching_facts():
    other = _fact(fact_id="med2", concept_code="99999", concept_system="RxNorm")
    assert _t2dm_rule().apply([other]) == []


def test_concept_trigger_rule_fact_id_deterministic():
    trigger = _fact(fact_id="med1", concept_code="6809", concept_system="RxNorm")
    a = _t2dm_rule().apply([trigger])
    b = _t2dm_rule().apply([trigger])
    assert a[0]["fact_id"] == b[0]["fact_id"]


# --- reasoning_versions ---


def test_reasoning_versions_feeds_manifest_and_changes_id():
    from veritract import MockLLM, build_manifest

    from verichart.reasoning import reasoning_versions

    class R:
        def __init__(self, version):
            self.name, self.version = "r", version

    llm = MockLLM()
    schema = {"type": "object", "properties": {"x": {"type": "string"}}}
    m1 = build_manifest(llm, schema, extra={"rule_versions": reasoning_versions([R("v1")])})
    m2 = build_manifest(llm, schema, extra={"rule_versions": reasoning_versions([R("v2")])})
    assert m1["manifest_id"] != m2["manifest_id"]
    assert m1["rule_versions"] == {"reasoning:r": "v1"}
