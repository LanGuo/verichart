"""LlmRelationExtractor against a real local Ollama model. Skips cleanly when Ollama
is unavailable. Mock-LLM wiring tests live in test_clinical_relations.py.
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


@pytest.fixture(scope="module")
def extractor():
    from veritract import LLMClient

    from verichart.clinical.relations import LlmRelationExtractor

    model = _model_or_skip()
    return LlmRelationExtractor(LLMClient(model=model, temperature=0.0, seed=42))


def _m(text, sub, label):
    from verichart.clinical.entities import EntityMention

    i = text.index(sub)
    return EntityMention(text=sub, char_start=i, char_end=i + len(sub),
                         label=label, raw_label=label, score=0.9, recognizer="test")


def test_extracts_grounded_med_attributes(extractor):
    text = "Plan: start metformin 500 mg by mouth twice daily."
    rels = extractor.extract(text, [_m(text, "metformin", "MEDICATION")])
    assert rels, "expected at least one relation"
    for r in rels:
        assert text[r["tail"]["char_start"]:r["tail"]["char_end"]] == r["tail"]["text"]
        assert r["method"] == "llm-grounded"
        assert r["head"]["text"] == "metformin"
    rel_types = {r["relation"] for r in rels}
    assert "HAS_STRENGTH" in rel_types or "HAS_DOSE" in rel_types


def test_absent_attribute_yields_no_relation(extractor):
    text = "Continue metformin."          # no dose/route/frequency stated
    rels = extractor.extract(text, [_m(text, "metformin", "MEDICATION")])
    assert all(r["tail"]["text"] in text for r in rels)   # nothing invented
