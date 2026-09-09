"""ScispacyResolver tests — need the clinical extra AND a ~1 GB KB download.

Opt in with RUN_SCISPACY_TESTS=1. The lexical SqliteLookupResolver is covered
(no download) in test_clinical_terminology.py.
"""
import os

import pytest

pytestmark = pytest.mark.clinical

_RUN = os.environ.get("RUN_SCISPACY_TESTS") == "1"
_reason = "set RUN_SCISPACY_TESTS=1 to run scispaCy linker tests (~1 GB KB download)"


@pytest.fixture(scope="module")
def rxnorm_resolver():
    if not _RUN:
        pytest.skip(_reason)
    pytest.importorskip("scispacy")
    from verichart.clinical.terminology import ScispacyResolver

    return ScispacyResolver(linker_name="rxnorm", threshold=0.5)


@pytest.mark.skipif(not _RUN, reason=_reason)
def test_scispacy_resolver_rxnorm_metformin(rxnorm_resolver):
    m = rxnorm_resolver.resolve("metformin")
    assert m is not None
    assert m["system"] == "UMLS"          # scispaCy identifies by CUI even for the rxnorm linker
    assert m["code"].startswith("C")       # UMLS CUI
    assert 0.0 <= m["score"] <= 1.0
    assert m["version"].startswith("scispacy")
    assert "metformin" in m["display"].lower()


@pytest.mark.skipif(not _RUN, reason=_reason)
def test_scispacy_resolver_miss_returns_none(rxnorm_resolver):
    assert rxnorm_resolver.resolve("qwertyuiop zxcvbnm") is None


@pytest.mark.skipif(not _RUN, reason=_reason)
def test_scispacy_resolver_feeds_resolve_concepts(rxnorm_resolver):
    from verichart.clinical.entities import MockRecognizer, extract_entities
    from verichart.clinical.terminology import resolve_concepts

    rec = MockRecognizer()
    rec.register("metformin", label="MEDICATION", score=0.9)
    facts = extract_entities("takes metformin", recognizer=rec, labels=["MEDICATION"])
    out = resolve_concepts(facts, [rxnorm_resolver])
    assert out[0]["concept_system"] == "UMLS"
    assert out[0]["concept_code"]
    assert out[0]["terminology_version"].startswith("scispacy")


def test_scispacy_resolver_rejects_bad_linker_name():
    pytest.importorskip("scispacy")
    from verichart.clinical.terminology import ScispacyResolver

    with pytest.raises(ValueError, match="linker_name"):
        ScispacyResolver(linker_name="snomed")
