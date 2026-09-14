"""VeritractLlmResolver against a real local Ollama model. Skips cleanly when Ollama is
unavailable. Mock-LLM wiring test lives in test_reconcile.py.
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


def test_veritract_llm_resolver_picks_a_side():
    from veritract import LLMClient

    from verichart.reconcile import VeritractLlmResolver

    model = _model_or_skip()
    resolver = VeritractLlmResolver(LLMClient(model=model, temperature=0.0, seed=42))

    context = (
        "Pharmacy dispensing record: metformin 500 mg tablets, verified against the "
        "prescription on file. An earlier dictated note said 5000 mg, which is an implausible "
        "dose and is presumed a transcription error."
    )
    a = _fact(fact_id="a", value="5000 mg")
    b = _fact(fact_id="b", value="500 mg")
    from verichart.reconcile import ConflictSet

    cs = ConflictSet(conflict_set_id="cs1", concept_key="RxNorm:6809", date_bucket=None,
                     member_fact_ids=["a", "b"], kind="value_disagreement")

    winner, rationale = resolver.resolve(cs, [a, b], context)
    assert winner in ("a", "b")
    assert rationale
