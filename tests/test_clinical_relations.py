import pytest

from verichart.clinical.entities import EntityMention, normalize_label
from verichart.clinical.relations import (
    ClinicalRelation,
    MockRelationExtractor,
    mentions_from_facts,
)


def _m(text, start, label, score=0.9):
    return EntityMention(
        text=text, char_start=start, char_end=start + len(text),
        label=label, raw_label=label, score=score, recognizer="test",
    )


# --- attribute label constants + normalization ---


def test_attribute_label_constants():
    from verichart.clinical.entities import (
        LAB_ATTRIBUTE_LABELS,
        MEDICATION_ATTRIBUTE_LABELS,
        PROBLEM_ATTRIBUTE_LABELS,
    )

    assert set(MEDICATION_ATTRIBUTE_LABELS) == {
        "STRENGTH", "DOSE", "FREQUENCY", "ROUTE", "FORM", "DURATION"
    }
    assert LAB_ATTRIBUTE_LABELS == {"VALUE": "lab value", "UNIT": "lab unit"}
    assert set(PROBLEM_ATTRIBUTE_LABELS) == {"SEVERITY", "STAGE", "BODY_SITE", "LATERALITY"}


@pytest.mark.parametrize("raw,expected", [
    ("drug dose", "DOSE"),
    ("dosage", "DOSE"),
    ("drug frequency", "FREQUENCY"),
    ("drug route", "ROUTE"),
    ("lab value", "VALUE"),
    ("lab unit", "UNIT"),
    ("severity", "SEVERITY"),
])
def test_normalize_label_covers_attributes(raw, expected):
    assert normalize_label(raw) == expected


# --- mentions_from_facts ---


def test_mentions_from_facts_round_trips_span_and_fields():
    from verichart import extract_entities
    from verichart.clinical.entities import MockRecognizer

    text = "patient takes metformin and lisinopril"
    rec = MockRecognizer(version="rec@1")
    rec.register("metformin", label="MEDICATION", score=0.88)
    rec.register("lisinopril", label="MEDICATION", score=0.91)
    facts = extract_entities(text, recognizer=rec, labels=["MEDICATION"], doc_id="n:1")

    ms = mentions_from_facts(facts)
    assert [m["text"] for m in ms] == ["metformin", "lisinopril"]
    for f, m in zip(facts, ms):
        assert (m["char_start"], m["char_end"]) == (f["span"]["char_start"], f["span"]["char_end"])
        assert text[m["char_start"]:m["char_end"]] == m["text"]
        assert m["label"] == f["label"]
        assert m["score"] == f["extraction_confidence"]
        assert m["recognizer"] == f["extraction_model"] or m["recognizer"] == "rec@1"


def test_mentions_from_facts_skips_spanless_facts():
    from verichart.facts import make_fact

    spanless = make_fact(
        label="MEDICATION", value="aspirin", span=None, provenance_type="unverified",
        confidence=0.0, note="q", patient_pseudonym=None, effective_date=None,
        manifest_id=None, model_tag=None, model_digest=None, created_at="2026-01-01T00:00:00+00:00",
    )
    assert mentions_from_facts([spanless]) == []


# --- MockRelationExtractor ---


def test_mock_relation_extractor_registered_links():
    text = "metformin 500 mg twice daily"
    ents = [_m("metformin", 0, "MEDICATION"), _m("500 mg", 10, "DOSE"),
            _m("twice daily", 17, "FREQUENCY")]
    ext = MockRelationExtractor(version="mock@1")
    ext.register(head="metformin", relation="HAS_DOSE", tail="500 mg")
    ext.register(head="metformin", relation="HAS_FREQUENCY", tail="twice daily")

    rels = ext.extract(text, ents)
    assert {r["relation"] for r in rels} == {"HAS_DOSE", "HAS_FREQUENCY"}
    for r in rels:
        assert r["head"]["text"] == "metformin"
        assert r["extractor"] == "mock@1"
        assert r["direction"] == "right"        # attribute follows the drug
        assert r["token_gap"] >= 0
        assert set(r) == {"relation", "head", "tail", "score", "extractor",
                          "method", "direction", "token_gap"}


def test_mock_relation_extractor_no_rule_no_relation():
    ext = MockRelationExtractor()
    assert ext.extract("metformin 500 mg", [_m("metformin", 0, "MEDICATION")]) == []
