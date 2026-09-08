"""Clinical entity extraction: types, adapter protocols, mock adapters, and the
``extract_entities`` composition.

Phase 2 of verichart. Entity *detection* and assertion *classification* are separate
protocols — the best OSS tools split them (GLiNER-BioMed finds entities with exact
offsets but no negation; medspaCy ConText classifies negation/temporality but needs
entities handed to it). ``extract_entities`` composes: recognize -> classify -> ground
-> project to ``ClinicalFact``.
"""

from __future__ import annotations

import re
from typing import Protocol, runtime_checkable

from typing_extensions import TypedDict


class EntityMention(TypedDict):
    text: str  # verbatim span text
    char_start: int
    char_end: int
    label: str  # normalized: PROBLEM | MEDICATION | LAB | PROCEDURE | VITAL | <uppercased raw>
    raw_label: str  # the recognizer's own label, before normalization
    score: float  # 0-1
    recognizer: str  # recognizer.version — flows to ClinicalFact.extraction_model


class AssertionResult(TypedDict):
    is_negated: bool
    is_historical: bool
    is_hypothetical: bool
    is_family: bool
    is_uncertain: bool
    modifiers: list[str]  # the modifier phrases that fired (kept in the fact's note)


@runtime_checkable
class EntityRecognizer(Protocol):
    version: str
    digest: str | None

    def recognize(self, text: str, labels: list[str]) -> list[EntityMention]: ...


@runtime_checkable
class AssertionClassifier(Protocol):
    version: str

    def classify(self, text: str, mentions: list[EntityMention]) -> list[AssertionResult]: ...


def _empty_assertion() -> AssertionResult:
    return AssertionResult(
        is_negated=False,
        is_historical=False,
        is_hypothetical=False,
        is_family=False,
        is_uncertain=False,
        modifiers=[],
    )


# --------------------------------------------------------------------------- mocks


class MockRecognizer:
    """Deterministic recognizer stub for tests. Registers terms by substring."""

    def __init__(self, *, version: str = "mock", digest: str | None = None):
        self.version = version
        self.digest = digest
        self._terms: list[tuple[str, str, float]] = []  # (term, label, score)
        self._raw: list[EntityMention] = []

    def register(self, term: str, *, label: str, score: float = 1.0) -> None:
        self._terms.append((term, label, score))

    def register_raw(
        self, *, text: str, char_start: int, char_end: int, label: str, score: float = 1.0
    ) -> None:
        self._raw.append(EntityMention(
            text=text, char_start=char_start, char_end=char_end,
            label=label, raw_label=label, score=score, recognizer=self.version,
        ))

    def recognize(self, text: str, labels: list[str]) -> list[EntityMention]:
        wanted = set(labels) if labels else None
        out: list[EntityMention] = []
        for term, label, score in self._terms:
            start = 0
            while (idx := text.find(term, start)) != -1:
                out.append(EntityMention(
                    text=term, char_start=idx, char_end=idx + len(term),
                    label=label, raw_label=label, score=score, recognizer=self.version,
                ))
                start = idx + len(term)
        out.extend(self._raw)
        out = [m for m in out if wanted is None or m["label"] in wanted or m["raw_label"] in wanted]
        out.sort(key=lambda m: m["char_start"])
        return out


class MockAssertionClassifier:
    """Deterministic assertion stub. A registered phrase applies to a mention when
    both fall in the same clause (text split on ``.`` / ``;`` / newline)."""

    def __init__(self, *, version: str = "mock-assertion"):
        self.version = version
        self._rules: list[tuple[str, dict]] = []  # (phrase, flags)

    def register(self, phrase: str, **flags: bool) -> None:
        self._rules.append((phrase, flags))

    def _clauses(self, text: str) -> list[tuple[int, int]]:
        spans: list[tuple[int, int]] = []
        pos = 0
        for part in re.split(r"([.;\n])", text):
            if part in (".", ";", "\n"):
                pos += len(part)
                continue
            spans.append((pos, pos + len(part)))
            pos += len(part)
        return spans

    def classify(self, text: str, mentions: list[EntityMention]) -> list[AssertionResult]:
        clauses = self._clauses(text)
        results: list[AssertionResult] = []
        for m in mentions:
            res = _empty_assertion()
            clause = next(
                (text[s:e] for s, e in clauses if s <= m["char_start"] < e), ""
            )
            for phrase, flags in self._rules:
                if phrase in clause:
                    for k, v in flags.items():
                        res[k] = v  # type: ignore[literal-required]
                    res["modifiers"].append(phrase)
            results.append(res)
        return results
