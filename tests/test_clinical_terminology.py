import pytest

from verichart.clinical.terminology import (
    ConceptMatch,
    DEFAULT_ROUTING,
    MockResolver,
    terminology_versions,
)


# --- MockResolver + types + routing (Task 1) ---


def test_mock_resolver_returns_registered_match():
    r = MockResolver(system="RxNorm", version="2024AB")
    r.register("metformin", code="6809", display="Metformin", score=0.99)
    m = r.resolve("metformin")
    assert m == ConceptMatch(
        code="6809", display="Metformin", system="RxNorm", version="2024AB", score=0.99
    )


def test_mock_resolver_case_insensitive_and_miss_returns_none():
    r = MockResolver(system="RxNorm", version="v")
    r.register("Metformin", code="6809", display="Metformin")
    assert r.resolve("METFORMIN") is not None
    assert r.resolve("aspirin") is None


def test_mock_resolver_default_score_is_one():
    r = MockResolver(system="LOINC", version="2.77")
    r.register("TSH", code="3016-3", display="Thyrotropin")
    assert r.resolve("tsh")["score"] == 1.0


def test_default_routing_shape():
    assert DEFAULT_ROUTING["MEDICATION"] == ("RxNorm",)
    assert "SNOMED-CT" in DEFAULT_ROUTING["PROBLEM"]
    assert DEFAULT_ROUTING["LAB"] == ("LOINC",)


def test_terminology_versions_maps_system_to_version():
    rs = [
        MockResolver(system="RxNorm", version="2024AB"),
        MockResolver(system="LOINC", version="2.77"),
    ]
    assert terminology_versions(rs) == {"RxNorm": "2024AB", "LOINC": "2.77"}


# --- resolve_concepts (Task 2) ---

TEXT = "Patient has type 2 diabetes and takes metformin. TSH was elevated."


def _facts():
    from verichart.clinical.entities import MockRecognizer, extract_entities

    rec = MockRecognizer(version="mock@1")
    rec.register("type 2 diabetes", label="PROBLEM", score=0.9)
    rec.register("metformin", label="MEDICATION", score=0.9)
    rec.register("TSH", label="LAB", score=0.9)
    return extract_entities(
        TEXT, recognizer=rec, labels=["PROBLEM", "MEDICATION", "LAB"], doc_id="note:1"
    )


def _resolvers():
    sno = MockResolver(system="SNOMED-CT", version="2026-03")
    sno.register("type 2 diabetes", code="44054006",
                 display="Diabetes mellitus type 2", score=0.95)
    rx = MockResolver(system="RxNorm", version="2024AB")
    rx.register("metformin", code="6809", display="Metformin", score=0.99)
    loinc = MockResolver(system="LOINC", version="2.77")
    loinc.register("TSH", code="3016-3", display="Thyrotropin [Units/volume]", score=0.9)
    return [sno, rx, loinc]


def test_resolve_concepts_routes_by_label():
    from verichart.clinical.terminology import resolve_concepts

    out = resolve_concepts(_facts(), _resolvers())
    by_label = {f["label"]: f for f in out}
    assert by_label["PROBLEM"]["concept_system"] == "SNOMED-CT"
    assert by_label["PROBLEM"]["concept_code"] == "44054006"
    assert by_label["PROBLEM"]["concept_display"] == "Diabetes mellitus type 2"
    assert by_label["MEDICATION"]["concept_system"] == "RxNorm"
    assert by_label["LAB"]["concept_code"] == "3016-3"
    for f in out:
        assert f["terminology_version"]


def test_resolve_concepts_recomputes_fact_id_deterministically():
    from verichart.clinical.terminology import resolve_concepts

    before = _facts()
    after = resolve_concepts(before, _resolvers())
    assert [f["fact_id"] for f in after] != [f["fact_id"] for f in before]
    assert [f["fact_id"] for f in after] == [
        f["fact_id"] for f in resolve_concepts(_facts(), _resolvers())
    ]


def test_resolve_concepts_is_idempotent():
    from verichart.clinical.terminology import resolve_concepts

    once = resolve_concepts(_facts(), _resolvers())
    twice = resolve_concepts(once, _resolvers())
    assert [f["fact_id"] for f in once] == [f["fact_id"] for f in twice]
    assert [f["concept_code"] for f in once] == [f["concept_code"] for f in twice]


def test_resolve_concepts_unmatched_fact_keeps_none_and_id():
    from verichart.clinical.entities import MockRecognizer, extract_entities
    from verichart.clinical.terminology import resolve_concepts

    rec = MockRecognizer()
    rec.register("hypertension", label="PROBLEM", score=0.9)
    facts = extract_entities("has hypertension", recognizer=rec, labels=["PROBLEM"])
    out = resolve_concepts(facts, [MockResolver(system="SNOMED-CT", version="v")])
    assert out[0]["concept_code"] is None
    assert out[0]["fact_id"] == facts[0]["fact_id"]


def test_resolve_concepts_does_not_mutate_input():
    from verichart.clinical.terminology import resolve_concepts

    facts = _facts()
    snapshot = [dict(f) for f in facts]
    resolve_concepts(facts, _resolvers())
    assert [dict(f) for f in facts] == snapshot


def test_resolve_concepts_passes_context_window():
    from verichart.clinical.terminology import resolve_concepts

    seen = {}

    class Spy(MockResolver):
        def resolve(self, mention, context=None):
            seen["context"] = context
            return super().resolve(mention, context)

    spy = Spy(system="RxNorm", version="v")
    spy.register("metformin", code="6809", display="Metformin")
    resolve_concepts(_facts(), [spy], documents={"note:1": TEXT}, context_chars=20)
    assert seen["context"] and "metformin" in seen["context"]
    assert len(seen["context"]) < len(TEXT)


def test_resolve_concepts_min_score_gate():
    from verichart.clinical.terminology import resolve_concepts

    rx = MockResolver(system="RxNorm", version="v")
    rx.register("metformin", code="6809", display="Metformin", score=0.4)
    out = resolve_concepts(_facts(), [rx], min_score=0.8)
    med = next(f for f in out if f["label"] == "MEDICATION")
    assert med["concept_code"] is None


def test_resolve_concepts_unknown_label_tries_all_resolvers():
    from verichart.clinical.entities import MockRecognizer, extract_entities
    from verichart.clinical.terminology import resolve_concepts

    rec = MockRecognizer()
    rec.register("aspirin", label="ALLERGY", score=0.9)  # not in DEFAULT_ROUTING
    facts = extract_entities("allergic to aspirin", recognizer=rec, labels=["ALLERGY"])
    rx = MockResolver(system="RxNorm", version="v")
    rx.register("aspirin", code="1191", display="Aspirin")
    out = resolve_concepts(facts, [rx])
    assert out[0]["concept_code"] == "1191"
