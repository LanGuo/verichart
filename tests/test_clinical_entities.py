import pytest

from verichart.clinical.entities import (
    AssertionResult,
    EntityMention,
    MockAssertionClassifier,
    MockRecognizer,
)


# --- MockRecognizer ---


def test_mock_recognizer_returns_registered_mentions():
    rec = MockRecognizer(version="mock@1")
    rec.register("metformin", label="MEDICATION", score=0.9)
    ms = rec.recognize("patient takes metformin daily", ["MEDICATION"])
    assert len(ms) == 1
    m = ms[0]
    assert m["text"] == "metformin"
    assert (m["char_start"], m["char_end"]) == (14, 23)
    assert m["label"] == "MEDICATION"
    assert m["raw_label"] == "MEDICATION"
    assert m["score"] == 0.9
    assert m["recognizer"] == "mock@1"


def test_mock_recognizer_reports_all_occurrences_in_order():
    rec = MockRecognizer()
    rec.register("pain", label="PROBLEM")
    ms = rec.recognize("chest pain and leg pain", ["PROBLEM"])
    assert [(m["char_start"], m["char_end"]) for m in ms] == [(6, 10), (19, 23)]


def test_mock_recognizer_filters_to_requested_labels():
    rec = MockRecognizer()
    rec.register("metformin", label="MEDICATION")
    rec.register("diabetes", label="PROBLEM")
    ms = rec.recognize("diabetes treated with metformin", ["PROBLEM"])
    assert [m["text"] for m in ms] == ["diabetes"]


def test_mock_recognizer_register_raw_allows_bad_offsets():
    rec = MockRecognizer()
    rec.register_raw(text="ghost", char_start=1000, char_end=1005, label="PROBLEM", score=1.0)
    ms = rec.recognize("short text", ["PROBLEM"])
    assert ms[0]["char_start"] == 1000


# --- MockAssertionClassifier ---


def test_mock_assertion_classifier_one_result_per_mention_aligned():
    clf = MockAssertionClassifier()
    clf.register("no evidence of", is_negated=True)
    text = "no evidence of pneumonia; has diabetes"
    mentions = [
        EntityMention(text="pneumonia", char_start=15, char_end=24, label="PROBLEM",
                      raw_label="Disease", score=0.9, recognizer="m"),
        EntityMention(text="diabetes", char_start=29, char_end=37, label="PROBLEM",
                      raw_label="Disease", score=0.9, recognizer="m"),
    ]
    results = clf.classify(text, mentions)
    assert len(results) == 2
    assert results[0]["is_negated"] is True
    assert results[1]["is_negated"] is False
    assert all(set(r) >= {"is_negated", "is_historical", "is_hypothetical",
                          "is_family", "is_uncertain", "modifiers"} for r in results)


def test_mock_assertion_classifier_multiple_flags():
    clf = MockAssertionClassifier()
    clf.register("family history of", is_family=True)
    text = "family history of breast cancer"
    m = EntityMention(text="breast cancer", char_start=18, char_end=31, label="PROBLEM",
                      raw_label="Disease", score=0.9, recognizer="m")
    (r,) = clf.classify(text, [m])
    assert r["is_family"] is True
    assert "family history of" in r["modifiers"]


def test_mock_assertion_classifier_empty_mentions():
    clf = MockAssertionClassifier()
    assert clf.classify("anything", []) == []
