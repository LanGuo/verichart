import pytest

from verichart.facts import make_fact


def _fact(**over):
    base = dict(
        fact_id="f1", patient_pseudonym="pt_1", label="MEDICATION",
        concept_code=None, concept_system=None, concept_display=None,
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


# --- apply_rules / MockRule ---


def test_mock_rule_facts_are_appended():
    from verichart.reasoning import apply_rules

    class MockRule:
        name = "mock"
        version = "v1"

        def apply(self, facts):
            return [_fact(fact_id="derived", value="derived fact")]

    facts = [_fact(fact_id="f1")]
    out = apply_rules(facts, [MockRule()])
    assert {f["fact_id"] for f in out} == {"f1", "derived"}


def test_apply_rules_does_not_mutate_input():
    from verichart.reasoning import apply_rules

    class MockRule:
        name = "mock"
        version = "v1"

        def apply(self, facts):
            return [_fact(fact_id="derived")]

    facts = [_fact(fact_id="f1")]
    snapshot = [dict(f) for f in facts]
    apply_rules(facts, [MockRule()])
    assert [dict(f) for f in facts] == snapshot


def test_disabling_a_rule_removes_exactly_its_facts():
    from verichart.reasoning import apply_rules

    class RuleA:
        name, version = "a", "v1"
        def apply(self, facts):
            return [_fact(fact_id="from_a")]

    class RuleB:
        name, version = "b", "v1"
        def apply(self, facts):
            return [_fact(fact_id="from_b")]

    facts = [_fact(fact_id="f1")]
    with_both = {f["fact_id"] for f in apply_rules(facts, [RuleA(), RuleB()])}
    with_a_only = {f["fact_id"] for f in apply_rules(facts, [RuleA()])}
    assert with_both - with_a_only == {"from_b"}
    assert with_a_only == {"f1", "from_a"}


# --- ConceptTriggerRule ---


def _t2dm_rule(confidence=0.7):
    from verichart.reasoning import ConceptTriggerRule

    return ConceptTriggerRule(
        name="metformin_implies_t2dm",
        version="v1",
        trigger_concept=("RxNorm", "6809"),
        infer=dict(label="PROBLEM", value="type 2 diabetes mellitus (inferred)",
                  concept_system="SNOMED-CT", concept_code="44054006"),
        confidence=confidence,
    )


def test_concept_trigger_rule_fires_on_matching_concept():
    trigger = _fact(fact_id="med1", label="MEDICATION", value="metformin",
                    concept_code="6809", concept_system="RxNorm", patient_pseudonym="pt_1")
    (derived,) = _t2dm_rule().apply([trigger])
    assert derived["label"] == "PROBLEM"
    assert derived["concept_code"] == "44054006" and derived["concept_system"] == "SNOMED-CT"
    assert derived["provenance_type"] == "inferred"
    assert derived["span"] is None
    assert derived["extraction_confidence"] == 0.7
    assert derived["assertion_status"] == "unknown"
    assert derived["patient_pseudonym"] == "pt_1"
    assert derived["extraction_model"] == "v1"
    assert "med1" in derived["note"]


def test_concept_trigger_rule_no_double_inference():
    trigger = _fact(fact_id="med1", concept_code="6809", concept_system="RxNorm")
    already = _fact(fact_id="dx1", label="PROBLEM", concept_code="44054006",
                    concept_system="SNOMED-CT")
    assert _t2dm_rule().apply([trigger, already]) == []


def test_concept_trigger_rule_no_double_inference_against_prior_inference():
    trigger = _fact(fact_id="med1", concept_code="6809", concept_system="RxNorm")
    rule = _t2dm_rule()
    first_pass = rule.apply([trigger])
    assert len(first_pass) == 1
    second_pass = rule.apply([trigger, *first_pass])
    assert second_pass == []


def test_concept_trigger_rule_ignores_non_matching_facts():
    other = _fact(fact_id="med2", concept_code="99999", concept_system="RxNorm")
    assert _t2dm_rule().apply([other]) == []


def test_concept_trigger_rule_fact_id_deterministic():
    trigger = _fact(fact_id="med1", concept_code="6809", concept_system="RxNorm")
    a = _t2dm_rule().apply([trigger])
    b = _t2dm_rule().apply([trigger])
    assert a[0]["fact_id"] == b[0]["fact_id"]


# --- reasoning_versions ---


def test_reasoning_versions_feeds_manifest_and_changes_id():
    from veritract import MockLLM, build_manifest

    from verichart.reasoning import reasoning_versions

    class R:
        def __init__(self, version):
            self.name, self.version = "r", version

    llm = MockLLM()
    schema = {"type": "object", "properties": {"x": {"type": "string"}}}
    m1 = build_manifest(llm, schema, extra={"rule_versions": reasoning_versions([R("v1")])})
    m2 = build_manifest(llm, schema, extra={"rule_versions": reasoning_versions([R("v2")])})
    assert m1["manifest_id"] != m2["manifest_id"]
    assert m1["rule_versions"] == {"reasoning:r": "v1"}


# --- AbsenceRule ---


def _span(doc_id, source_type):
    return {"doc_id": doc_id, "source_type": source_type, "char_start": 0, "char_end": 1,
           "text": "x", "provenance_type": "direct"}


def _hiv_absence_rule(confidence=0.85):
    from verichart.reasoning import AbsenceRule

    return AbsenceRule(
        name="hiv_panel_absence",
        version="v1",
        concept=("LOINC", "5221-7"),
        applies_to_source_types={"hiv_screening_panel", "std_panel"},
        infer=dict(label="PROBLEM", value="HIV negative (inferred — absent from screening panel)",
                  concept_system="SNOMED-CT", concept_code="165816005"),
        confidence=confidence,
    )


def test_absence_rule_fires_in_scope_document():
    doc_facts = [_fact(fact_id="f1", label="LAB", value="glucose", concept_code="2345-7",
                       concept_system="LOINC", span=_span("panel1", "hiv_screening_panel"),
                       patient_pseudonym="pt_1")]
    (derived,) = _hiv_absence_rule().apply(doc_facts)
    assert derived["concept_code"] == "165816005"
    assert derived["provenance_type"] == "inferred"
    assert derived["patient_pseudonym"] == "pt_1"


def test_absence_rule_does_not_fire_outside_scope():
    doc_facts = [_fact(fact_id="f1", label="PROBLEM", value="chest pain",
                       span=_span("cardio1", "cardiology_note"))]
    assert _hiv_absence_rule().apply(doc_facts) == []


def test_absence_rule_does_not_fire_when_concept_present():
    doc_facts = [_fact(fact_id="f1", label="LAB", value="HIV Ab/Ag",
                       concept_code="5221-7", concept_system="LOINC",
                       span=_span("panel1", "hiv_screening_panel"))]
    assert _hiv_absence_rule().apply(doc_facts) == []


def test_absence_rule_scoped_and_unscoped_documents_together():
    facts = [
        _fact(fact_id="f1", label="LAB", value="glucose", concept_code="2345-7",
             concept_system="LOINC", span=_span("panel1", "hiv_screening_panel")),
        _fact(fact_id="f2", label="PROBLEM", value="chest pain",
             span=_span("cardio1", "cardiology_note")),
    ]
    derived = _hiv_absence_rule().apply(facts)
    assert len(derived) == 1


def test_absence_rule_does_not_contradict_a_result_recorded_elsewhere():
    # panel1 itself doesn't mention the HIV test, but the actual result was recorded in an
    # unrelated consult note -- the absence in panel1 must not be used to infer "negative."
    facts = [
        _fact(fact_id="f1", label="LAB", value="glucose", concept_code="2345-7",
             concept_system="LOINC", span=_span("panel1", "hiv_screening_panel")),
        _fact(fact_id="f2", label="LAB", value="HIV Ab/Ag reactive", concept_code="5221-7",
             concept_system="LOINC", span=_span("id_consult", "consult_note")),
    ]
    assert _hiv_absence_rule().apply(facts) == []


# --- assign_effective_dates ---


def _mention_fact(fact_id, value, doc_id, char_start, char_end):
    return _fact(
        fact_id=fact_id, value=value,
        span={"doc_id": doc_id, "source_type": "clinical_note", "char_start": char_start,
             "char_end": char_end, "text": value, "provenance_type": "direct"},
    )


def test_assign_effective_dates_relative_weeks():
    from verichart.reasoning import assign_effective_dates

    text = "Metformin started 3 weeks ago."
    fact = _mention_fact("f1", "metformin", "n1", text.index("Metformin"), len("Metformin"))
    out = assign_effective_dates([fact], {"n1": text}, document_dates={"n1": "2026-06-01"})
    assert out[0]["effective_date"] == "2026-05-11"
    assert "3 weeks ago" in out[0]["note"]


def test_assign_effective_dates_relative_last_year():
    from verichart.reasoning import assign_effective_dates

    text = "Diagnosed with hypertension last year."
    fact = _mention_fact("f1", "hypertension", "n1", text.index("hypertension"), len("hypertension"))
    out = assign_effective_dates([fact], {"n1": text}, document_dates={"n1": "2026-06-01"})
    assert out[0]["effective_date"] == "2025-06-01"


def test_assign_effective_dates_absolute_needs_no_document_date():
    from verichart.reasoning import assign_effective_dates

    text = "Labs drawn on 2026-01-15 showed elevated glucose."
    fact = _mention_fact("f1", "glucose", "n1", text.index("glucose"), len("glucose"))
    out = assign_effective_dates([fact], {"n1": text})
    assert out[0]["effective_date"] == "2026-01-15"


def test_assign_effective_dates_no_cue_in_sentence_stays_none():
    from verichart.reasoning import assign_effective_dates

    text = "Patient reports fatigue. No relevant dates here at all."
    fact = _mention_fact("f1", "fatigue", "n1", text.index("fatigue"), len("fatigue"))
    out = assign_effective_dates([fact], {"n1": text}, document_dates={"n1": "2026-06-01"})
    assert out[0]["effective_date"] is None


def test_assign_effective_dates_never_overwrites_existing():
    from verichart.reasoning import assign_effective_dates

    text = "Metformin started 3 weeks ago."
    fact = _mention_fact("f1", "metformin", "n1", text.index("Metformin"), len("Metformin"))
    fact["effective_date"] = "2020-01-01"
    out = assign_effective_dates([fact], {"n1": text}, document_dates={"n1": "2026-06-01"})
    assert out[0]["effective_date"] == "2020-01-01"


def test_assign_effective_dates_relative_without_anchor_stays_unresolved():
    from verichart.reasoning import assign_effective_dates

    text = "Metformin started 3 weeks ago, no absolute date anywhere in this note."
    fact = _mention_fact("f1", "metformin", "n1", text.index("Metformin"), len("Metformin"))
    out = assign_effective_dates([fact], {"n1": text})   # no document_dates, no absolute cue
    assert out[0]["effective_date"] is None


def test_assign_effective_dates_falls_back_to_absolute_cue_in_document():
    from verichart.reasoning import assign_effective_dates

    text = "Note dated 2026-06-01. Metformin started 3 weeks ago."
    fact = _mention_fact("f1", "metformin", "n1", text.index("Metformin"), len("Metformin"))
    out = assign_effective_dates([fact], {"n1": text})   # no document_dates given
    assert out[0]["effective_date"] == "2026-05-11"


def test_assign_effective_dates_does_not_mutate_input():
    from verichart.reasoning import assign_effective_dates

    text = "Metformin started 3 weeks ago."
    fact = _mention_fact("f1", "metformin", "n1", text.index("Metformin"), len("Metformin"))
    snapshot = dict(fact)
    assign_effective_dates([fact], {"n1": text}, document_dates={"n1": "2026-06-01"})
    assert fact == snapshot


# --- decay: DecayRule + is_stale ---


def test_default_decay_rules_is_empty():
    from verichart.reasoning import DEFAULT_DECAY_RULES

    assert DEFAULT_DECAY_RULES == []


def test_is_stale_with_no_table_is_always_false():
    from verichart.reasoning import is_stale

    old_fact = _fact(effective_date="2020-01-01", label="LAB")
    assert is_stale(old_fact, "2026-09-17") is False


def test_decay_rule_requires_source():
    from verichart.reasoning import DecayRule

    with pytest.raises(ValueError, match="source"):
        DecayRule(max_age_days=90, source="", label="LAB")
    with pytest.raises(TypeError):
        DecayRule(max_age_days=90, label="LAB")  # source omitted entirely


def test_decay_rule_requires_a_match_target():
    from verichart.reasoning import DecayRule

    with pytest.raises(ValueError, match="concept_key"):
        DecayRule(max_age_days=90, source="internal review")


def test_is_stale_concept_level_rule():
    from verichart.reasoning import DecayRule, is_stale

    fact = _fact(effective_date="2026-01-01", concept_code="4548-4", concept_system="LOINC")
    table = [DecayRule(concept_key="LOINC:4548-4", max_age_days=90, source="internal review")]
    assert is_stale(fact, "2026-06-01", table=table) is True     # ~150 days old
    assert is_stale(fact, "2026-02-01", table=table) is False    # ~31 days old


def test_is_stale_label_level_fallback():
    from verichart.reasoning import DecayRule, is_stale

    fact = _fact(effective_date="2026-01-01", label="VITAL")
    table = [DecayRule(label="VITAL", max_age_days=1, source="internal review")]
    assert is_stale(fact, "2026-01-05", table=table) is True


def test_is_stale_no_matching_rule_or_no_date():
    from verichart.reasoning import DecayRule, is_stale

    table = [DecayRule(label="LAB", max_age_days=90, source="internal review")]
    unmatched_label = _fact(effective_date="2020-01-01", label="PROBLEM")
    no_date = _fact(effective_date=None, label="LAB")
    assert is_stale(unmatched_label, "2026-09-17", table=table) is False
    assert is_stale(no_date, "2026-09-17", table=table) is False


# --- MedRtTriggerRule + load_indication_relations ---


@pytest.fixture
def indications_db(tmp_path):
    from verichart.reasoning import load_indication_relations

    p = tmp_path / "indications.db"
    rows = [
        ("RxNorm", "6809", "may_treat", "SNOMED-CT", "44054006", "Diabetes mellitus type 2"),
        # a deliberately ambiguous drug: two candidate indications
        ("RxNorm", "6373", "may_treat", "SNOMED-CT", "38341003", "Hypertension"),
        ("RxNorm", "6373", "may_treat", "SNOMED-CT", "84114007", "Heart failure"),
    ]
    n = load_indication_relations(rows, str(p))
    return str(p), n


def test_load_indication_relations_row_count(indications_db):
    _, n = indications_db
    assert n == 3


def test_medrt_rule_single_indication(indications_db):
    from verichart.reasoning import MedRtTriggerRule

    db_path, _ = indications_db
    rule = MedRtTriggerRule(db_path, version="2026.07.06", confidence=0.6)
    trigger = _fact(fact_id="med1", label="MEDICATION", concept_code="6809",
                    concept_system="RxNorm", patient_pseudonym="pt_1")
    (derived,) = rule.apply([trigger])
    assert derived["concept_code"] == "44054006"
    assert derived["extraction_confidence"] == 0.6
    assert derived["provenance_type"] == "inferred"


def test_medrt_rule_ambiguous_indications_split_confidence(indications_db):
    from verichart.reasoning import MedRtTriggerRule

    db_path, _ = indications_db
    rule = MedRtTriggerRule(db_path, version="2026.07.06", confidence=0.6)
    trigger = _fact(fact_id="med2", label="MEDICATION", concept_code="6373",
                    concept_system="RxNorm")
    derived = rule.apply([trigger])
    assert len(derived) == 2
    assert {d["concept_code"] for d in derived} == {"38341003", "84114007"}
    for d in derived:
        assert d["extraction_confidence"] == 0.3
        assert "ambiguous" in d["note"] and "2" in d["note"]


def test_medrt_rule_no_double_inference(indications_db):
    from verichart.reasoning import MedRtTriggerRule

    db_path, _ = indications_db
    rule = MedRtTriggerRule(db_path, version="2026.07.06")
    trigger = _fact(fact_id="med1", label="MEDICATION", concept_code="6809",
                    concept_system="RxNorm")
    already = _fact(fact_id="dx1", label="PROBLEM", concept_code="44054006",
                    concept_system="SNOMED-CT")
    assert rule.apply([trigger, already]) == []


def test_medrt_rule_skips_non_medication_or_unresolved(indications_db):
    from verichart.reasoning import MedRtTriggerRule

    db_path, _ = indications_db
    rule = MedRtTriggerRule(db_path, version="2026.07.06")
    not_med = _fact(fact_id="f1", label="PROBLEM", concept_code="6809", concept_system="RxNorm")
    unresolved = _fact(fact_id="f2", label="MEDICATION", concept_code=None)
    assert rule.apply([not_med]) == []
    assert rule.apply([unresolved]) == []


def test_medrt_rule_version_distinguishes_dbs(tmp_path):
    from verichart.reasoning import MedRtTriggerRule, load_indication_relations

    db_a = tmp_path / "a.db"
    db_b = tmp_path / "b.db"
    load_indication_relations([("RxNorm", "1", "may_treat", "SNOMED-CT", "2", "X")], str(db_a))
    load_indication_relations([("RxNorm", "1", "may_treat", "SNOMED-CT", "3", "Y")], str(db_b))
    a = MedRtTriggerRule(str(db_a), version="2026.07.06")
    b = MedRtTriggerRule(str(db_b), version="2026.07.06")
    assert a.version != b.version  # same release tag, different content -> distinguishable


def test_medrt_rule_does_not_mutate_input(indications_db):
    from verichart.reasoning import MedRtTriggerRule

    db_path, _ = indications_db
    rule = MedRtTriggerRule(db_path, version="2026.07.06")
    trigger = _fact(fact_id="med1", concept_code="6809", concept_system="RxNorm")
    snapshot = dict(trigger)
    rule.apply([trigger])
    assert trigger == snapshot


# --- LlmInferenceRule (mock LLM; real Ollama test in test_reasoning_llm.py) ---


def test_llm_inference_rule_proposes_unresolved_diagnosis():
    from veritract import MockLLM

    from verichart.reasoning import LlmInferenceRule

    llm = MockLLM()
    llm.register("Known facts", {
        "implied_diagnosis": "type 2 diabetes mellitus",
        "rationale": "on metformin, a first-line T2DM medication",
    })
    rule = LlmInferenceRule(llm, confidence=0.5)

    facts = [_fact(fact_id="med1", label="MEDICATION", value="metformin",
                  concept_code="6809", concept_system="RxNorm", patient_pseudonym="pt_1")]
    (derived,) = rule.apply(facts)
    assert derived["concept_code"] is None       # unresolved -- caller runs resolve_concepts
    assert derived["concept_system"] is None
    assert derived["value"] == "type 2 diabetes mellitus"
    assert derived["provenance_type"] == "inferred"
    assert derived["extraction_confidence"] == 0.5
    assert "not benchmarked" in derived["note"] or "unbenchmarked" in derived["note"]
    assert "metformin" in derived["note"] or "on metformin" in derived["note"]


def test_llm_inference_rule_no_diagnosis_implied():
    from veritract import MockLLM

    from verichart.reasoning import LlmInferenceRule

    llm = MockLLM()
    llm.register("Known facts", {"implied_diagnosis": "", "rationale": ""})
    rule = LlmInferenceRule(llm)
    facts = [_fact(fact_id="med1", label="MEDICATION", value="metformin",
                  concept_code="6809", concept_system="RxNorm", patient_pseudonym="pt_1")]
    assert rule.apply(facts) == []


def test_llm_inference_rule_skips_already_stated_diagnosis():
    from veritract import MockLLM

    from verichart.reasoning import LlmInferenceRule

    llm = MockLLM()
    llm.register("Known facts", {
        "implied_diagnosis": "type 2 diabetes mellitus", "rationale": "on metformin",
    })
    rule = LlmInferenceRule(llm)
    facts = [
        _fact(fact_id="med1", label="MEDICATION", value="metformin", concept_code="6809",
             concept_system="RxNorm", patient_pseudonym="pt_1"),
        _fact(fact_id="dx1", label="PROBLEM", value="Type 2 Diabetes Mellitus",
             patient_pseudonym="pt_1"),
    ]
    assert rule.apply(facts) == []


def test_llm_inference_rule_no_facts():
    from veritract import MockLLM

    from verichart.reasoning import LlmInferenceRule

    assert LlmInferenceRule(MockLLM()).apply([]) == []


def test_llm_inference_rule_does_not_mutate_input():
    from veritract import MockLLM

    from verichart.reasoning import LlmInferenceRule

    llm = MockLLM()
    llm.register("Known facts", {"implied_diagnosis": "type 2 diabetes mellitus", "rationale": "x"})
    facts = [_fact(fact_id="med1", label="MEDICATION", value="metformin", concept_code="6809",
                  concept_system="RxNorm", patient_pseudonym="pt_1")]
    snapshot = [dict(f) for f in facts]
    LlmInferenceRule(llm).apply(facts)
    assert [dict(f) for f in facts] == snapshot


def test_llm_inference_rule_output_resolves_through_phase3(tmp_path):
    """The two-step pattern: LlmInferenceRule proposes a name, resolve_concepts finds the code
    -- never asking the model for a code directly."""
    from veritract import MockLLM

    from verichart import resolve_concepts
    from verichart.clinical.terminology import SqliteLookupResolver, load_vocab_sqlite
    from verichart.reasoning import LlmInferenceRule

    llm = MockLLM()
    llm.register("Known facts", {
        "implied_diagnosis": "type 2 diabetes mellitus", "rationale": "on metformin",
    })
    facts = [_fact(fact_id="med1", label="MEDICATION", value="metformin", concept_code="6809",
                  concept_system="RxNorm", patient_pseudonym="pt_1")]
    (derived,) = LlmInferenceRule(llm).apply(facts)
    assert derived["concept_code"] is None

    db_path = str(tmp_path / "snomed.db")
    load_vocab_sqlite([("44054006", "type 2 diabetes mellitus", True)], db_path)
    resolver = SqliteLookupResolver(db_path, system="SNOMED-CT", version="2026-03")
    (resolved,) = resolve_concepts([derived], [resolver], routing={"PROBLEM": ("SNOMED-CT",)})
    assert resolved["concept_code"] == "44054006"
