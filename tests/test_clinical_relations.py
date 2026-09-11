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


# --- relations_to_facts ---

TEXT = "Patient on metformin 500 mg twice daily; also has hypertension."


def _med_facts(text=TEXT):
    from verichart import extract_entities
    from verichart.clinical.entities import MockRecognizer

    rec = MockRecognizer(version="rec@1")
    rec.register("metformin", label="MEDICATION", score=0.9)
    rec.register("hypertension", label="PROBLEM", score=0.85)
    return extract_entities(text, recognizer=rec, labels=["MEDICATION", "PROBLEM"],
                            doc_id="n:1", patient_pseudonym="pt_1")


def _rels(text=TEXT):
    ents = _ents(text, [("metformin", "MEDICATION"), ("500 mg", "STRENGTH"),
                        ("twice daily", "FREQUENCY")])
    return _linker().extract(text, ents)


def test_relations_to_facts_builds_one_composite_and_drops_bare_anchor():
    from verichart.clinical.relations import relations_to_facts

    facts = _med_facts()
    out = relations_to_facts(TEXT, _rels(), facts)
    meds = [f for f in out if f["label"] == "MEDICATION"]
    assert len(meds) == 1
    assert meds[0]["value"] == "metformin 500 mg twice daily"
    assert not any(f["value"] == "metformin" for f in out)          # bare anchor gone
    assert any(f["value"] == "hypertension" for f in out)           # unrelated fact passes through


def test_composite_carries_supporting_spans_and_provenance():
    from verichart.clinical.relations import relations_to_facts

    (comp,) = [f for f in relations_to_facts(TEXT, _rels(), _med_facts())
               if f["label"] == "MEDICATION"]
    assert comp["provenance_type"] == "direct"          # covering text == assembled
    assert len(comp["supporting_spans"]) == 3
    for s in comp["supporting_spans"]:
        assert TEXT[s["char_start"]:s["char_end"]] == s["text"]
    assert comp["span"]["char_start"] == TEXT.index("metformin")
    assert "HAS_STRENGTH" in comp["note"] and "HAS_FREQUENCY" in comp["note"]


def test_composite_inherits_patient_and_assertion_and_concept():
    from verichart import resolve_concepts
    from verichart.clinical.relations import relations_to_facts
    from verichart.clinical.terminology import MockResolver

    facts = _med_facts()
    rx = MockResolver(system="RxNorm", version="2024AB")
    rx.register("metformin", code="6809", display="Metformin")
    facts = resolve_concepts(facts, [rx])

    (comp,) = [f for f in relations_to_facts(TEXT, _rels(), facts)
               if f["label"] == "MEDICATION"]
    assert comp["patient_pseudonym"] == "pt_1"
    assert comp["assertion_status"] == facts[0]["assertion_status"]
    assert comp["concept_code"] == "6809"
    assert comp["concept_system"] == "RxNorm"


def test_composite_provenance_inferred_when_scattered():
    from verichart import extract_entities
    from verichart.clinical.entities import MockRecognizer
    from verichart.clinical.relations import relations_to_facts

    text = "metformin therapy was initiated at 500 mg administered twice daily"
    ents = _ents(text, [("metformin", "MEDICATION"), ("500 mg", "STRENGTH"),
                        ("twice daily", "FREQUENCY")])
    rels = _linker().extract(text, ents)
    assert rels, "expected the linker to link within one sentence"

    rec = MockRecognizer()
    rec.register("metformin", label="MEDICATION", score=0.9)
    facts = extract_entities(text, recognizer=rec, labels=["MEDICATION"], doc_id="n:2")

    comp = next(f for f in relations_to_facts(text, rels, facts) if f["label"] == "MEDICATION")
    assert comp["value"] == "metformin 500 mg twice daily"
    assert comp["provenance_type"] == "inferred"    # covering text has interleaved words


def test_composite_confidence_is_min_over_constituents():
    from verichart.clinical.relations import relations_to_facts

    (comp,) = [f for f in relations_to_facts(TEXT, _rels(), _med_facts())
               if f["label"] == "MEDICATION"]
    assert comp["extraction_confidence"] <= 0.9


def test_anchor_with_no_relations_passes_through():
    from verichart.clinical.relations import relations_to_facts

    facts = _med_facts()
    out = relations_to_facts(TEXT, [], facts)
    assert {f["value"] for f in out} == {"metformin", "hypertension"}


def test_fact_id_deterministic_and_differs_from_bare():
    from verichart.clinical.relations import relations_to_facts

    facts = _med_facts()
    a = relations_to_facts(TEXT, _rels(), facts, created_at="2026-01-01T00:00:00+00:00")
    b = relations_to_facts(TEXT, _rels(), facts, created_at="2026-09-01T00:00:00+00:00")
    assert [f["fact_id"] for f in a] == [f["fact_id"] for f in b]
    comp_id = next(f["fact_id"] for f in a if f["label"] == "MEDICATION")
    bare_id = next(f["fact_id"] for f in facts if f["value"] == "metformin")
    assert comp_id != bare_id


def test_relations_to_facts_unmatched_head_uses_mention_fields():
    from verichart.clinical.relations import relations_to_facts

    out = relations_to_facts(TEXT, _rels(), [])   # no entity_facts at all
    (comp,) = [f for f in out if f["label"] == "MEDICATION"]
    assert comp["value"] == "metformin 500 mg twice daily"
    assert comp["patient_pseudonym"] is None
    assert comp["assertion_status"] == "unknown"


def test_relations_to_facts_does_not_mutate_input():
    from verichart.clinical.relations import relations_to_facts

    facts = _med_facts()
    snap = [dict(f) for f in facts]
    relations_to_facts(TEXT, _rels(), facts)
    assert [dict(f) for f in facts] == snap


# --- extract_relations wrapper ---


def test_extract_relations_end_to_end():
    from verichart.clinical.attributes import AttributeRecognizer
    from verichart.clinical.relations import RuleRelationLinker, extract_relations

    anchors = _med_facts()
    out = extract_relations(
        TEXT,
        recognizer=AttributeRecognizer(),
        anchor_facts=anchors,
        attribute_labels=["STRENGTH", "FREQUENCY", "ROUTE"],
        extractor=RuleRelationLinker(),
    )
    (comp,) = [f for f in out if f["label"] == "MEDICATION"]
    assert comp["value"] == "metformin 500 mg twice daily"
    assert any(f["value"] == "hypertension" for f in out)


# --- LlmRelationExtractor (mock LLM; real Ollama test in test_clinical_relations_llm.py) ---


def test_llm_relation_extractor_with_mock_llm():
    from veritract import MockLLM

    from verichart.clinical.relations import LlmRelationExtractor

    text = "Start metformin 500 mg PO twice daily."
    llm = MockLLM()
    llm.register("metformin", {
        "strength": "500 mg", "dose": "", "form": "", "route": "PO",
        "frequency": "twice daily", "duration": "",
    })
    ext = LlmRelationExtractor(llm)
    rels = ext.extract(text, [_m("metformin", text.index("metformin"), "MEDICATION")])

    got = {r["relation"]: r["tail"]["text"] for r in rels}
    assert got == {"HAS_STRENGTH": "500 mg", "HAS_ROUTE": "PO", "HAS_FREQUENCY": "twice daily"}
    for r in rels:
        assert text[r["tail"]["char_start"]:r["tail"]["char_end"]] == r["tail"]["text"]
        assert r["method"] == "llm-grounded"
        assert r["extractor"].startswith("llm-relations@")


def test_llm_relation_extractor_drops_ungroundable_value():
    from veritract import MockLLM

    from verichart.clinical.relations import LlmRelationExtractor

    text = "Start metformin 500 mg."
    llm = MockLLM()
    llm.register("metformin", {
        "strength": "500 mg", "dose": "", "form": "",
        "route": "intravenous", "frequency": "", "duration": "",  # "intravenous" not in text
    })
    rels = LlmRelationExtractor(llm).extract(
        text, [_m("metformin", text.index("metformin"), "MEDICATION")])
    assert {r["relation"] for r in rels} == {"HAS_STRENGTH"}     # route dropped by grounding


def test_llm_relation_extractor_no_anchors():
    from veritract import MockLLM

    from verichart.clinical.relations import LlmRelationExtractor

    assert LlmRelationExtractor(MockLLM()).extract("some text", []) == []
