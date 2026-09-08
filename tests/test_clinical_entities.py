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


# --- derive_assertion_status ---


def _ar(**flags):
    base = dict(is_negated=False, is_historical=False, is_hypothetical=False,
                is_family=False, is_uncertain=False, modifiers=[])
    base.update(flags)
    return AssertionResult(**base)


@pytest.mark.parametrize("flags,expected", [
    ({}, "confirmed"),
    ({"is_negated": True}, "ruled_out"),
    ({"is_family": True}, "family_history"),
    ({"is_hypothetical": True}, "hypothetical"),
    ({"is_historical": True}, "historical"),
    ({"is_uncertain": True}, "uncertain"),
    ({"is_negated": True, "is_family": True}, "ruled_out"),          # negation wins
    ({"is_family": True, "is_historical": True}, "family_history"),  # family beats historical
    ({"is_hypothetical": True, "is_uncertain": True}, "hypothetical"),
])
def test_derive_assertion_status_precedence(flags, expected):
    from verichart.clinical.assertion import derive_assertion_status
    assert derive_assertion_status(_ar(**flags)) == expected


def test_uncertain_is_a_valid_assertion_status():
    from verichart.facts import AssertionStatus
    assert "uncertain" in AssertionStatus.__args__


# --- extract_entities ---

TEXT = "No evidence of pneumonia. Patient has type 2 diabetes and takes metformin."


def _rec():
    r = MockRecognizer(version="gliner-mock@1", digest="sha256:abc")
    r.register("pneumonia", label="PROBLEM", score=0.95)
    r.register("type 2 diabetes", label="PROBLEM", score=0.91)
    r.register("metformin", label="MEDICATION", score=0.88)
    return r


def test_extract_entities_projects_to_clinical_facts_in_doc_order():
    from verichart.clinical.entities import extract_entities

    facts = extract_entities(TEXT, recognizer=_rec(), labels=["PROBLEM", "MEDICATION"],
                             doc_id="note:7", patient_pseudonym="pt_7")
    assert [f["value"] for f in facts] == ["pneumonia", "type 2 diabetes", "metformin"]
    for f in facts:
        assert f["provenance_type"] == "direct"
        assert f["span"]["doc_id"] == "note:7"
        assert TEXT[f["span"]["char_start"]:f["span"]["char_end"]] == f["value"]
        assert f["extraction_model"] == "gliner-mock@1"
        assert f["extraction_model_digest"] == "sha256:abc"
        assert f["patient_pseudonym"] == "pt_7"
        assert f["concept_code"] is None
        assert 0.0 <= f["extraction_confidence"] <= 1.0


def test_extract_entities_without_classifier_status_is_unknown():
    from verichart.clinical.entities import extract_entities

    facts = extract_entities(TEXT, recognizer=_rec(), labels=["PROBLEM", "MEDICATION"])
    assert all(f["assertion_status"] == "unknown" for f in facts)


def test_extract_entities_with_classifier_sets_status():
    from verichart.clinical.entities import extract_entities

    clf = MockAssertionClassifier()
    clf.register("No evidence of", is_negated=True)  # clause governs pneumonia
    facts = extract_entities(TEXT, recognizer=_rec(), labels=["PROBLEM", "MEDICATION"],
                             assertion_classifier=clf)
    by_value = {f["value"]: f for f in facts}
    assert by_value["pneumonia"]["assertion_status"] == "ruled_out"
    assert by_value["type 2 diabetes"]["assertion_status"] == "confirmed"
    assert by_value["metformin"]["assertion_status"] == "confirmed"
    assert "No evidence of" in by_value["pneumonia"]["note"]


def test_extract_entities_min_score_filters():
    from verichart.clinical.entities import extract_entities

    facts = extract_entities(TEXT, recognizer=_rec(), labels=["PROBLEM", "MEDICATION"],
                             min_score=0.9)
    assert {f["value"] for f in facts} == {"pneumonia", "type 2 diabetes"}


def test_extract_entities_drops_mention_with_bad_offsets():
    from verichart.clinical.entities import extract_entities

    r = MockRecognizer()
    r.register_raw(text="ghost", char_start=1000, char_end=1005, label="PROBLEM", score=1.0)
    with pytest.warns(UserWarning, match="offset"):
        facts = extract_entities("short text", recognizer=r, labels=["PROBLEM"])
    assert facts == []


def test_extract_entities_fact_id_deterministic():
    from verichart.clinical.entities import extract_entities

    a = extract_entities(TEXT, recognizer=_rec(), labels=["PROBLEM", "MEDICATION"],
                         created_at="2026-01-01T00:00:00+00:00")
    b = extract_entities(TEXT, recognizer=_rec(), labels=["PROBLEM", "MEDICATION"],
                         created_at="2026-09-01T00:00:00+00:00")
    assert [f["fact_id"] for f in a] == [f["fact_id"] for f in b]


def test_extract_entities_manifest_id_stamped():
    from verichart.clinical.entities import extract_entities

    facts = extract_entities(TEXT, recognizer=_rec(), labels=["PROBLEM", "MEDICATION"],
                             manifest={"manifest_id": "m123"})
    assert all(f["manifest_id"] == "m123" for f in facts)


def test_extract_entities_raises_on_misaligned_classifier():
    from verichart.clinical.entities import extract_entities

    class BadClf:
        version = "bad"

        def classify(self, text, mentions):
            return []  # wrong length

    with pytest.raises(ValueError, match="one per mention"):
        extract_entities(TEXT, recognizer=_rec(), labels=["PROBLEM"], assertion_classifier=BadClf())
