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
import warnings
from typing import Protocol, runtime_checkable

from typing_extensions import TypedDict

from veritract import Span

from verichart.facts import ClinicalFact, _now_iso, make_fact

CANONICAL_LABELS = ["PROBLEM", "MEDICATION", "LAB", "PROCEDURE", "VITAL"]

_LABEL_SYNONYMS = {
    "problem": "PROBLEM", "disease": "PROBLEM", "condition": "PROBLEM",
    "diagnosis": "PROBLEM", "symptom": "PROBLEM", "finding": "PROBLEM",
    "medication": "MEDICATION", "drug": "MEDICATION", "treatment": "MEDICATION",
    "lab": "LAB", "lab test": "LAB", "laboratory test": "LAB", "test": "LAB",
    "procedure": "PROCEDURE",
    "vital": "VITAL", "vital sign": "VITAL",
}


def normalize_label(raw: str) -> str:
    """Map a recognizer's label to a canonical one, else uppercase it unchanged."""
    return _LABEL_SYNONYMS.get(raw.strip().lower(), raw.strip().upper())


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


# ----------------------------------------------------------------- extract_entities


def extract_entities(
    text: str,
    *,
    recognizer: EntityRecognizer,
    labels: list[str],
    assertion_classifier: AssertionClassifier | None = None,
    doc_id: str | None = None,
    source_type: str = "clinical_note",
    patient_pseudonym: str | None = None,
    effective_date: str | None = None,
    manifest: dict | None = None,
    created_at: str | None = None,
    min_score: float = 0.0,
) -> list[ClinicalFact]:
    """Open-ended clinical entity extraction -> ``ClinicalFact`` records.

    ``recognizer`` finds entity mentions (exact character offsets); the optional
    ``assertion_classifier`` labels each with negation / temporality / subject.
    Offsets are authoritative — every fact gets ``provenance_type="direct"`` and no
    fuzzy grounding step. Mentions whose ``text`` does not match ``text[start:end]``
    are dropped with a warning (a recognizer returning bad offsets is a bug to surface).

    ``concept_code`` / ``concept_system`` stay ``None`` (Phase 3). To combine this with
    schema extraction over the same document, concatenate with ``to_facts(result)`` —
    deduping the two is Phase 5's job, not this function's.
    """
    from verichart.clinical.assertion import derive_assertion_status

    mentions = [m for m in recognizer.recognize(text, labels) if m["score"] >= min_score]

    aligned: list[EntityMention] = []
    for m in mentions:
        if text[m["char_start"]:m["char_end"]] == m["text"]:
            aligned.append(m)
        else:
            warnings.warn(
                f"dropping mention {m['text']!r}: offset "
                f"[{m['char_start']}:{m['char_end']}] does not match the source text",
                stacklevel=2,
            )
    mentions = aligned

    if assertion_classifier is not None:
        results: list[AssertionResult | None] = list(
            assertion_classifier.classify(text, mentions)
        )
        if len(results) != len(mentions):
            raise ValueError(
                f"assertion_classifier returned {len(results)} results for "
                f"{len(mentions)} mentions — must return one per mention, in order"
            )
    else:
        results = [None] * len(mentions)

    manifest_id = manifest["manifest_id"] if manifest else None
    digest = getattr(recognizer, "digest", None)
    stamp = created_at if created_at is not None else _now_iso()

    facts: list[ClinicalFact] = []
    for m, res in zip(mentions, results):
        span = Span(
            doc_id=doc_id,
            source_type=source_type,
            char_start=m["char_start"],
            char_end=m["char_end"],
            text=m["text"],
            provenance_type="direct",
        )
        status = derive_assertion_status(res) if res is not None else "unknown"

        note_bits: list[str] = []
        if res is not None and res["modifiers"]:
            note_bits.append("modifiers: " + ", ".join(res["modifiers"]))
        if m["raw_label"] != m["label"]:
            note_bits.append(f"raw_label: {m['raw_label']}")

        facts.append(make_fact(
            label=m["label"],
            value=m["text"],
            span=span,
            provenance_type="direct",
            confidence=min(1.0, max(0.0, m["score"])),
            note="; ".join(note_bits) or None,
            patient_pseudonym=patient_pseudonym,
            effective_date=effective_date,
            manifest_id=manifest_id,
            model_tag=m["recognizer"],
            model_digest=digest,
            created_at=stamp,
            assertion_status=status,
        ))

    facts.sort(key=lambda f: f["span"]["char_start"])
    return facts
