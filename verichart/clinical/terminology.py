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

from typing import TYPE_CHECKING, Iterable, Protocol, runtime_checkable

from typing_extensions import TypedDict

from verichart.facts import compute_fact_id

if TYPE_CHECKING:
    from verichart.facts import ClinicalFact


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


# ----------------------------------------------------------------- resolve_concepts


def resolve_concepts(
    facts: list["ClinicalFact"],
    resolvers: list[ConceptResolver],
    *,
    routing: dict[str, tuple[str, ...]] | None = None,
    documents: dict[str, str] | None = None,
    context_chars: int = 120,
    min_score: float = 0.0,
    skip_resolved: bool = True,
) -> list["ClinicalFact"]:
    """Resolve each fact's ``value`` to a terminology code.

    Pure projection — returns new fact dicts, does not mutate ``facts``, makes no
    network call of its own.

    Routing: for a fact with ``label`` L, try resolvers whose ``.system`` is in
    ``routing.get(L)`` (default ``DEFAULT_ROUTING``), in that order; the first
    ``ConceptMatch`` with ``score >= min_score`` wins. A fact whose ``label`` is not
    in the routing map falls back to trying every resolver in the order passed.

    On a match: sets ``concept_code`` / ``concept_system`` / ``concept_display`` /
    ``terminology_version`` and **recomputes ``fact_id``** (it keys on the concept
    fields). ``skip_resolved`` leaves an already-coded fact untouched (idempotent).

    ``documents`` (``{doc_id: full text}``) enables a ±``context_chars`` window around
    each fact's span to be passed to ``resolver.resolve(value, context)``.
    """
    routing = DEFAULT_ROUTING if routing is None else routing
    by_system: dict[str, list[ConceptResolver]] = {}
    for r in resolvers:
        by_system.setdefault(r.system, []).append(r)

    out: list["ClinicalFact"] = []
    for src in facts:
        fact = dict(src)  # shallow copy
        if not (skip_resolved and fact.get("concept_code")):
            match = _resolve_one(fact, resolvers, by_system, routing, documents,
                                 context_chars, min_score)
            if match is not None:
                fact["concept_code"] = match["code"]
                fact["concept_system"] = match["system"]
                fact["concept_display"] = match["display"]
                fact["terminology_version"] = match["version"]
                fact["fact_id"] = compute_fact_id(
                    label=fact["label"],
                    value=fact["value"],
                    concept_code=fact["concept_code"],
                    concept_system=fact["concept_system"],
                    span=fact["span"],
                    manifest_id=fact["manifest_id"],
                )
        out.append(fact)  # type: ignore[arg-type]
    return out


def _resolve_one(fact, resolvers, by_system, routing, documents, context_chars, min_score):
    context = None
    span = fact.get("span")
    if documents is not None and span is not None:
        doc = documents.get(span["doc_id"])
        if doc is not None:
            lo = max(0, span["char_start"] - context_chars)
            hi = min(len(doc), span["char_end"] + context_chars)
            context = doc[lo:hi]

    systems = routing.get(fact["label"])
    ordered: list[ConceptResolver]
    if systems:
        ordered = [r for s in systems for r in by_system.get(s, [])]
    else:
        ordered = list(resolvers)  # unknown label: try everything, in order

    for resolver in ordered:
        m = resolver.resolve(fact["value"], context)
        if m is not None and m["score"] >= min_score:
            return m
    return None
