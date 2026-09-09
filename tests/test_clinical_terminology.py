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
