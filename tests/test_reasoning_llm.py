"""LlmInferenceRule against a real local Ollama model. Skips cleanly when Ollama is
unavailable. Mock-LLM wiring tests live in test_reasoning.py.
"""
import pytest


def _model_or_skip():
    ollama = pytest.importorskip("ollama")
    try:
        listing = ollama.list()
    except Exception:
        pytest.skip("Ollama daemon not reachable")
    models = getattr(listing, "models", None) or (
        listing.get("models", []) if hasattr(listing, "get") else []
    )
    tags = {
        (m.get("model") if hasattr(m, "get") else None) or getattr(m, "model", None)
        for m in models
    }
    present = next((t for t in ("gemma3:1b", "gemma4:e4b", "gemma4:12b") if t in tags), None)
    if present is None:
        pytest.skip("no known model pulled")
    return present


def _fact(**over):
    base = dict(
        fact_id="f1", patient_pseudonym="pt_1", label="MEDICATION",
        concept_code="6809", concept_system="RxNorm", concept_display=None,
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


@pytest.fixture(scope="module")
def rule():
    from veritract import LLMClient

    from verichart.reasoning import LlmInferenceRule

    model = _model_or_skip()
    return LlmInferenceRule(LLMClient(model=model, temperature=0.0, seed=42))


def test_proposes_a_name_not_a_code(rule):
    facts = [_fact(fact_id="med1", label="MEDICATION", value="metformin",
                  concept_code="6809", concept_system="RxNorm", patient_pseudonym="pt_1")]
    derived = rule.apply(facts)
    for d in derived:
        assert d["concept_code"] is None       # never asked the model for a code
        assert d["value"]                       # a non-empty proposed name
        assert d["provenance_type"] == "inferred"


def test_does_not_repeat_an_already_stated_diagnosis(rule):
    facts = [
        _fact(fact_id="med1", label="MEDICATION", value="metformin",
             concept_code="6809", concept_system="RxNorm", patient_pseudonym="pt_1"),
        _fact(fact_id="dx1", label="PROBLEM", value="type 2 diabetes mellitus",
             concept_code=None, concept_system=None, patient_pseudonym="pt_1"),
    ]
    derived = rule.apply(facts)
    assert not any(d["value"].strip().lower() == "type 2 diabetes mellitus" for d in derived)
