"""Adapter tests that need the verichart[clinical] extra.

Marked `clinical` (deselected by the default `-m 'not clinical'`). Run with:
    pytest -m clinical
"""
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
