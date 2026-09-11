import pytest

from verichart.clinical.attributes import AttributeRecognizer


@pytest.fixture(scope="module")
def rec():
    return AttributeRecognizer()


def _by_label(mentions):
    out: dict[str, list[str]] = {}
    for m in mentions:
        out.setdefault(m["label"], []).append(m["text"])
    return out


def test_offsets_are_exact(rec):
    text = "metformin 500 mg PO twice daily for 3 months"
    for m in rec.recognize(text, list("STRENGTH DOSE ROUTE FREQUENCY DURATION".split())):
        assert text[m["char_start"]:m["char_end"]] == m["text"]
        assert m["score"] == 1.0
        assert m["recognizer"].startswith("attr-regex@")


def test_medication_sig_is_fully_parsed(rec):
    text = "metformin 500 mg PO twice daily for 3 months"
    got = _by_label(rec.recognize(text, ["STRENGTH", "ROUTE", "FREQUENCY", "DURATION"]))
    assert got["STRENGTH"] == ["500 mg"]
    assert got["ROUTE"] == ["PO"]
    assert got["FREQUENCY"] == ["twice daily"]
    assert got["DURATION"] == ["for 3 months"]


def test_lab_value_and_unit(rec):
    text = "Hemoglobin A1c 8.2 %"
    got = _by_label(rec.recognize(text, ["VALUE", "UNIT"]))
    assert got["VALUE"] == ["8.2"]
    assert got["UNIT"] == ["%"]


@pytest.mark.parametrize("phrase", ["q6h", "BID", "PRN", "every 8 hours", "once daily", "at bedtime"])
def test_frequency_lexicon(rec, phrase):
    text = f"take one {phrase} as needed"
    freqs = _by_label(rec.recognize(text, ["FREQUENCY"])).get("FREQUENCY", [])
    assert any(phrase.lower() in f.lower() for f in freqs)


def test_labels_filter(rec):
    text = "metformin 500 mg twice daily"
    got = _by_label(rec.recognize(text, ["FREQUENCY"]))
    assert set(got) == {"FREQUENCY"}


def test_combined_unit_is_one_span(rec):
    text = "amoxicillin 5 mg/5 mL suspension"
    strengths = _by_label(rec.recognize(text, ["STRENGTH"])).get("STRENGTH", [])
    assert strengths == ["5 mg/5 mL"]


def test_value_not_emitted_for_numbers_inside_a_strength(rec):
    text = "lisinopril 10 mg daily"
    got = _by_label(rec.recognize(text, ["STRENGTH", "VALUE"]))
    assert got.get("VALUE", []) == []          # "10" is part of "10 mg"
    assert got["STRENGTH"] == ["10 mg"]


def test_no_severity_or_stage_patterns(rec):
    text = "severe pneumonia, stage IV lung cancer"
    got = _by_label(rec.recognize(text, ["SEVERITY", "STAGE", "STRENGTH"]))
    assert "SEVERITY" not in got and "STAGE" not in got


def test_version_changes_with_lexicon(monkeypatch, rec):
    from verichart.clinical import attributes

    v1 = AttributeRecognizer().version
    monkeypatch.setitem(attributes._LEXICONS, "ROUTE", attributes._LEXICONS["ROUTE"] + ("nebulized",))
    v2 = AttributeRecognizer().version
    assert v1 != v2


def test_is_an_entity_recognizer(rec):
    from verichart.clinical.entities import EntityRecognizer

    assert isinstance(rec, EntityRecognizer)
