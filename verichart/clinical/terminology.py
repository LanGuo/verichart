"""Terminology resolution: map a ClinicalFact's ``value`` to a standard code.

Phase 3 of verichart. A ``ConceptResolver`` runs *after* facts exist (from ``to_facts``
or ``extract_entities``): ``resolve_concepts`` routes each fact to resolvers by its
``label``, sets ``concept_code`` / ``concept_system`` / ``concept_display`` /
``terminology_version``, and recomputes ``fact_id`` — a resolved fact has a
concept-anchored identity, which is what makes Phase 5 cross-source dedup possible.

verichart ships resolver *adapters* and a loader, never vocabulary content. Bring your
own release (see ``docs/terminology.md``).
"""

from __future__ import annotations

from typing import Iterable, Protocol, runtime_checkable

from typing_extensions import TypedDict


class ConceptMatch(TypedDict):
    code: str
    display: str
    system: str  # "SNOMED-CT" | "RxNorm" | "LOINC" | "ICD-10-CM" | "UMLS" | "MeSH" | ...
    version: str  # release identifier, e.g. "2024AB" / "2.77" / "scispacy-umls-2020AA"
    score: float  # 0-1


@runtime_checkable
class ConceptResolver(Protocol):
    system: str
    version: str

    def resolve(self, mention: str, context: str | None = None) -> ConceptMatch | None: ...


# label -> ordered systems to try. First match at/above min_score wins.
DEFAULT_ROUTING: dict[str, tuple[str, ...]] = {
    "PROBLEM": ("SNOMED-CT", "ICD-10-CM", "UMLS"),
    "MEDICATION": ("RxNorm",),
    "LAB": ("LOINC",),
    "PROCEDURE": ("SNOMED-CT",),
    "VITAL": ("LOINC",),
}


def terminology_versions(resolvers: list[ConceptResolver]) -> dict[str, str]:
    """``{system: version}`` for every resolver.

    Pass to ``veritract.build_manifest(extra={"terminology_versions": ...})`` so a
    point-in-time replay reproduces the same codes.
    """
    return {r.system: r.version for r in resolvers}


class MockResolver:
    """Deterministic resolver stub for tests. Registers terms by lowercased key."""

    def __init__(self, *, system: str, version: str):
        self.system = system
        self.version = version
        self._by_term: dict[str, tuple[str, str, float]] = {}  # term -> (code, display, score)

    def register(self, term: str, *, code: str, display: str, score: float = 1.0) -> None:
        self._by_term[term.strip().lower()] = (code, display, score)

    def resolve(self, mention: str, context: str | None = None) -> ConceptMatch | None:
        hit = self._by_term.get(mention.strip().lower())
        if hit is None:
            return None
        code, display, score = hit
        return ConceptMatch(
            code=code, display=display, system=self.system,
            version=self.version, score=score,
        )
