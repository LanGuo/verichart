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


# --- RuleRelationLinker ---


def _ents(text, specs):
    """specs: list of (substring, label). Offsets from text.find."""
    return [_m(s, text.index(s), lbl) for s, lbl in specs]


def _linker(**kw):
    from verichart.clinical.relations import RuleRelationLinker
    return RuleRelationLinker(**kw)


def test_rule_linker_contiguous_sig():
    text = "metformin 500 mg twice daily"
    ents = _ents(text, [("metformin", "MEDICATION"), ("500 mg", "STRENGTH"),
                        ("twice daily", "FREQUENCY")])
    rels = _linker().extract(text, ents)
    assert {r["relation"] for r in rels} == {"HAS_STRENGTH", "HAS_FREQUENCY"}
    for r in rels:
        assert r["head"]["text"] == "metformin"
        assert r["direction"] == "right"
        assert r["method"] == "rule:only-anchor"
        assert r["token_gap"] >= 0


def test_rule_linker_pre_posed_strength():
    text = "500 mg metformin daily"
    ents = _ents(text, [("500 mg", "STRENGTH"), ("metformin", "MEDICATION")])
    (r,) = [x for x in _linker().extract(text, ents) if x["relation"] == "HAS_STRENGTH"]
    assert r["head"]["text"] == "metformin"
    assert r["direction"] == "left"


def test_rule_linker_two_meds_no_crossing():
    text = "lisinopril 10 mg and metformin 500 mg"
    ents = _ents(text, [("lisinopril", "MEDICATION"), ("10 mg", "STRENGTH"),
                        ("metformin", "MEDICATION"), ("500 mg", "STRENGTH")])
    rels = _linker().extract(text, ents)
    got = {r["tail"]["text"]: r["head"]["text"] for r in rels}
    assert got == {"10 mg": "lisinopril", "500 mg": "metformin"}


def test_rule_linker_declines_positional_coordination():
    text = "metformin and lisinopril 500 mg and 10 mg respectively"
    ents = _ents(text, [("metformin", "MEDICATION"), ("lisinopril", "MEDICATION"),
                        ("500 mg", "STRENGTH"), ("10 mg", "STRENGTH")])
    rels = _linker().extract(text, ents)
    assert not any(r["tail"]["text"] == "500 mg" and r["head"]["text"] == "lisinopril"
                   for r in rels)
    # the ambiguous coordination is declined, not guessed
    assert rels == []


def test_rule_linker_scope_is_sentence():
    text = "Continue metformin daily. Dose is 500 mg."
    ents = _ents(text, [("metformin", "MEDICATION"), ("500 mg", "STRENGTH")])
    assert _linker().extract(text, ents) == []


def test_rule_linker_family_compatibility():
    text = "metformin level was 8.2"
    ents = _ents(text, [("metformin", "MEDICATION"), ("8.2", "VALUE")])
    assert _linker().extract(text, ents) == []          # VALUE needs a LAB anchor


def test_rule_linker_lab_value_unit():
    text = "Hemoglobin A1c 8.2 %"
    ents = _ents(text, [("Hemoglobin A1c", "LAB"), ("8.2", "VALUE"), ("%", "UNIT")])
    rels = _linker().extract(text, ents)
    assert {r["relation"] for r in rels} == {"HAS_VALUE", "HAS_UNIT"}
    assert all(r["head"]["text"] == "Hemoglobin A1c" for r in rels)


def test_rule_linker_unsafe_relations_gated():
    text = "metformin 500 mg for 3 months"
    ents = _ents(text, [("metformin", "MEDICATION"), ("500 mg", "STRENGTH"),
                        ("for 3 months", "DURATION")])
    default = _linker().extract(text, ents)
    assert "HAS_DURATION" not in {r["relation"] for r in default}

    with_dur = _linker(emit_relations={"HAS_STRENGTH", "HAS_DURATION"}).extract(text, ents)
    assert {r["relation"] for r in with_dur} == {"HAS_STRENGTH", "HAS_DURATION"}


def test_rule_linker_version_stable_and_scope_validated():
    a, b = _linker().version, _linker().version
    assert a == b and a.endswith(":sentence")
    assert _linker(scope="clause").version.endswith(":clause")
    with pytest.raises(ValueError, match="scope"):
        _linker(scope="paragraph")


def test_rule_linker_does_not_mutate_entities():
    text = "metformin 500 mg twice daily"
    ents = _ents(text, [("metformin", "MEDICATION"), ("500 mg", "STRENGTH"),
                        ("twice daily", "FREQUENCY")])
    snapshot = [dict(e) for e in ents]
    _linker().extract(text, ents)
    assert [dict(e) for e in ents] == snapshot
