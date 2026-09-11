"""Relation extraction (Phase 4): link an anchor entity to its attribute entities and
emit one composite ``ClinicalFact`` ("metformin 500 mg PO twice daily") instead of three.

Design rationale — including why the rule linker is ordered-directional, not naive
proximity, and which relation types a deterministic linker should not touch — is in
``docs/research/clinical-relation-extraction.md``.
"""

from __future__ import annotations

import re
from typing import Protocol, runtime_checkable

from typing_extensions import TypedDict

from verichart.clinical.entities import EntityMention
from verichart.facts import ClinicalFact

# attribute label -> relation name
_ATTRS = [
    "STRENGTH", "DOSE", "FORM", "ROUTE", "FREQUENCY", "DURATION",  # medication
    "VALUE", "UNIT",                                               # lab
    "SEVERITY", "LATERALITY", "BODY_SITE", "STAGE",                # problem
]
_RELATION_FOR_ATTR: dict[str, str] = {a: f"HAS_{a}" for a in _ATTRS}
_ATTR_FOR_RELATION: dict[str, str] = {v: k for k, v in _RELATION_FOR_ATTR.items()}

# anchor label -> relation types in the order the composite statement assembles
_ASSEMBLY: dict[str, list[str]] = {
    "MEDICATION": ["HAS_STRENGTH", "HAS_DOSE", "HAS_FORM", "HAS_ROUTE", "HAS_FREQUENCY", "HAS_DURATION"],
    "LAB": ["HAS_VALUE", "HAS_UNIT"],
    "PROBLEM": ["HAS_SEVERITY", "HAS_LATERALITY", "HAS_BODY_SITE", "HAS_STAGE"],
}


class ClinicalRelation(TypedDict):
    relation: str            # "HAS_DOSE" | "HAS_FREQUENCY" | ...
    head: EntityMention      # the anchor (MEDICATION / LAB / PROBLEM)
    tail: EntityMention      # the attribute
    score: float             # 0-1
    extractor: str           # extractor.version -> composite fact's extraction_model
    method: str              # audit: which rule fired ("rule:left-prior", "llm-grounded", ...)
    direction: str           # "left" | "right" — tail position relative to head
    token_gap: int           # whitespace tokens strictly between head and tail; -1 if unknown


@runtime_checkable
class RelationExtractor(Protocol):
    version: str

    def extract(self, text: str, entities: list[EntityMention]) -> list[ClinicalRelation]: ...


# --------------------------------------------------------------------- helpers


def _token_gap(text: str, a: EntityMention, b: EntityMention) -> int:
    """Whitespace-delimited tokens strictly between spans ``a`` and ``b``."""
    lo, hi = sorted((a, b), key=lambda m: m["char_start"])
    between = text[lo["char_end"]:hi["char_start"]]
    return len(between.split())


def _direction(head: EntityMention, tail: EntityMention) -> str:
    return "left" if tail["char_end"] <= head["char_start"] else "right"


# --------------------------------------------------------------------- mentions_from_facts


def mentions_from_facts(facts: list[ClinicalFact]) -> list[EntityMention]:
    """Reconstruct ``EntityMention``s from Phase 2/3 ``ClinicalFact``s (their spans).

    Skips facts with ``span is None``. ``raw_label`` is set to ``label`` (the original
    recognizer label is not retained on the fact and no linker needs it).
    """
    out: list[EntityMention] = []
    for f in facts:
        span = f["span"]
        if span is None:
            continue
        out.append(EntityMention(
            text=span["text"],
            char_start=span["char_start"],
            char_end=span["char_end"],
            label=f["label"],
            raw_label=f["label"],
            score=f["extraction_confidence"],
            recognizer=f["extraction_model"] or "unknown",
        ))
    return out


# --------------------------------------------------------------------- MockRelationExtractor


class MockRelationExtractor:
    """Deterministic relation stub for tests. Registers links by head/tail substrings."""

    def __init__(self, *, version: str = "mock-relations"):
        self.version = version
        self._rules: list[tuple[str, str, str]] = []  # (head_substr, relation, tail_substr)

    def register(self, *, head: str, relation: str, tail: str) -> None:
        self._rules.append((head, relation, tail))

    def extract(self, text: str, entities: list[EntityMention]) -> list[ClinicalRelation]:
        rels: list[ClinicalRelation] = []
        for head_s, relation, tail_s in self._rules:
            heads = [e for e in entities if head_s in e["text"]]
            tails = [e for e in entities if tail_s in e["text"]]
            for h in heads:
                for t in tails:
                    if (h["char_start"], h["char_end"]) == (t["char_start"], t["char_end"]):
                        continue
                    rels.append(ClinicalRelation(
                        relation=relation, head=h, tail=t, score=1.0,
                        extractor=self.version, method="mock",
                        direction=_direction(h, t), token_gap=_token_gap(text, h, t),
                    ))
        return rels
