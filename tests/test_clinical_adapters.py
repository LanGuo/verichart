"""Adapter tests that need the verichart[clinical] extra.

Marked `clinical` (deselected by the default `-m 'not clinical'`). Run with:
    pytest -m clinical
"""
import os

import pytest

pytestmark = pytest.mark.clinical

from verichart.clinical.entities import EntityMention  # noqa: E402


def _mentions(text: str, terms: list[tuple[str, str]]) -> list[EntityMention]:
    out = []
    for term, label in terms:
        i = text.index(term)
        out.append(EntityMention(text=term, char_start=i, char_end=i + len(term),
                                 label=label, raw_label=label, score=0.9, recognizer="test"))
    return out


# --- MedspacyContextClassifier (Task 5) ---


@pytest.fixture(scope="module")
def context_clf():
    pytest.importorskip("medspacy")
    from verichart.clinical.assertion import MedspacyContextClassifier

    return MedspacyContextClassifier()


def test_context_classifier_version_reflects_medspacy(context_clf):
    assert context_clf.version.startswith("medspacy-context@")


def test_context_classifier_one_result_per_mention(context_clf):
    text = "Patient has diabetes and hypertension."
    ms = _mentions(text, [("diabetes", "PROBLEM"), ("hypertension", "PROBLEM")])
    results = context_clf.classify(text, ms)
    assert len(results) == 2


@pytest.mark.parametrize("sentence,term,flag", [
    ("There is no evidence of pneumonia.", "pneumonia", "is_negated"),
    ("Family history of breast cancer.", "breast cancer", "is_family"),
    ("History of myocardial infarction.", "myocardial infarction", "is_historical"),
    ("Return if you develop chest pain.", "chest pain", "is_hypothetical"),
])
def test_context_classifier_detects_modifiers(context_clf, sentence, term, flag):
    (result,) = context_clf.classify(sentence, _mentions(sentence, [(term, "PROBLEM")]))
    assert result[flag] is True


def test_context_classifier_unmodified_entity_has_no_flags(context_clf):
    text = "Patient has hypertension."
    (result,) = context_clf.classify(text, _mentions(text, [("hypertension", "PROBLEM")]))
    assert not any(result[k] for k in
                   ("is_negated", "is_historical", "is_hypothetical", "is_family", "is_uncertain"))


def test_context_classifier_feeds_derive_assertion_status(context_clf):
    from verichart.clinical.assertion import derive_assertion_status

    text = "No evidence of pneumonia. Patient has diabetes."
    ms = _mentions(text, [("pneumonia", "PROBLEM"), ("diabetes", "PROBLEM")])
    r_pneu, r_diab = context_clf.classify(text, ms)
    assert derive_assertion_status(r_pneu) == "ruled_out"
    assert derive_assertion_status(r_diab) == "confirmed"


# --- MedspacyRuleRecognizer (Task 6) ---


@pytest.fixture(scope="module")
def rule_rec():
    pytest.importorskip("medspacy")
    from verichart.clinical.ner import MedspacyRuleRecognizer

    return MedspacyRuleRecognizer([
        ("pneumonia", "PROBLEM"),
        ("type 2 diabetes", "PROBLEM"),
        ("metformin", "MEDICATION"),
    ])


def test_rule_recognizer_exact_offsets_and_labels(rule_rec):
    text = "History of type 2 diabetes; now with pneumonia. On metformin."
    ms = rule_rec.recognize(text, ["PROBLEM", "MEDICATION"])
    assert [(m["text"], m["label"]) for m in ms] == [
        ("type 2 diabetes", "PROBLEM"),
        ("pneumonia", "PROBLEM"),
        ("metformin", "MEDICATION"),
    ]
    for m in ms:
        assert text[m["char_start"]:m["char_end"]] == m["text"]
        assert m["score"] == 1.0
        assert m["recognizer"].startswith("medspacy-rules@")


def test_rule_recognizer_filters_to_requested_labels(rule_rec):
    text = "type 2 diabetes treated with metformin"
    ms = rule_rec.recognize(text, ["MEDICATION"])
    assert [m["text"] for m in ms] == ["metformin"]


def test_rule_recognizer_version_is_stable_for_same_rules():
    pytest.importorskip("medspacy")
    from verichart.clinical.ner import MedspacyRuleRecognizer

    a = MedspacyRuleRecognizer([("pneumonia", "PROBLEM")])
    b = MedspacyRuleRecognizer([("pneumonia", "PROBLEM")])
    c = MedspacyRuleRecognizer([("sepsis", "PROBLEM")])
    assert a.version == b.version
    assert a.version != c.version


def test_rule_recognizer_feeds_extract_entities(rule_rec, context_clf):
    from verichart.clinical.entities import extract_entities

    text = "No evidence of pneumonia. Patient takes metformin."
    facts = extract_entities(text, recognizer=rule_rec, labels=["PROBLEM", "MEDICATION"],
                             assertion_classifier=context_clf)
    by_value = {f["value"]: f for f in facts}
    assert by_value["pneumonia"]["assertion_status"] == "ruled_out"
    assert by_value["metformin"]["assertion_status"] == "confirmed"
    assert by_value["pneumonia"]["extraction_model"].startswith("medspacy-rules@")


# --- GlinerBiomedRecognizer (Task 7) ---
#
# Runs real GLiNER-BioMed inference — opt-in via RUN_GLINER_TESTS=1 (the model is a
# ~400 MB download). The composition logic is covered by mocks in test_clinical_entities.py.

_RUN_GLINER = os.environ.get("RUN_GLINER_TESTS") == "1"
_gliner_reason = "set RUN_GLINER_TESTS=1 to run GLiNER-BioMed inference tests"


@pytest.fixture(scope="module")
def gliner_rec():
    if not _RUN_GLINER:
        pytest.skip(_gliner_reason)
    pytest.importorskip("gliner")
    from verichart.clinical.ner import GlinerBiomedRecognizer

    return GlinerBiomedRecognizer()


@pytest.mark.skipif(not _RUN_GLINER, reason=_gliner_reason)
def test_gliner_recognizes_with_exact_offsets(gliner_rec):
    from verichart.clinical.entities import DEFAULT_GLINER_LABELS

    text = ("62 y/o male with type 2 diabetes mellitus and hypertension, admitted for "
            "community-acquired pneumonia. Started on ceftriaxone. Hemoglobin A1c was 8.2%.")
    ms = gliner_rec.recognize(text, list(DEFAULT_GLINER_LABELS.values()))
    assert ms, "expected at least one entity"
    for m in ms:
        assert text[m["char_start"]:m["char_end"]] == m["text"]
        assert 0.0 <= m["score"] <= 1.0
        assert m["recognizer"].startswith("Ihor/gliner-biomed-bi-base-v1.0@")
    values = {m["value"] if "value" in m else m["text"] for m in ms}
    assert any("diabetes" in v for v in values)


@pytest.mark.skipif(not _RUN_GLINER, reason=_gliner_reason)
def test_gliner_digest_is_hf_revision(gliner_rec):
    assert gliner_rec.digest is None or gliner_rec.digest.startswith("hf:")


@pytest.mark.skipif(not _RUN_GLINER, reason=_gliner_reason)
def test_gliner_feeds_extract_entities_with_context(gliner_rec, context_clf):
    from verichart.clinical.entities import DEFAULT_GLINER_LABELS, extract_entities

    text = "No evidence of pneumonia. Patient has diabetes and takes metformin."
    facts = extract_entities(text, recognizer=gliner_rec,
                             labels=list(DEFAULT_GLINER_LABELS.values()),
                             assertion_classifier=context_clf, doc_id="note:9")
    by_value = {f["value"]: f for f in facts}
    assert any(f["assertion_status"] == "ruled_out" for f in facts)  # "no evidence of pneumonia"


def test_extract_entities_end_to_end_with_context(context_clf):
    from verichart.clinical.entities import MockRecognizer, extract_entities

    text = "No evidence of pneumonia. History of MI. Patient takes metformin."
    rec = MockRecognizer(version="mock@1")
    rec.register("pneumonia", label="PROBLEM", score=0.9)
    rec.register("MI", label="PROBLEM", score=0.9)
    rec.register("metformin", label="MEDICATION", score=0.9)

    facts = extract_entities(text, recognizer=rec, labels=["PROBLEM", "MEDICATION"],
                             assertion_classifier=context_clf, doc_id="note:1")
    by_value = {f["value"]: f for f in facts}
    assert by_value["pneumonia"]["assertion_status"] == "ruled_out"
    assert by_value["MI"]["assertion_status"] == "historical"
    assert by_value["metformin"]["assertion_status"] == "confirmed"
    assert all(f["provenance_type"] == "direct" for f in facts)
