"""AttributeRecognizer — regex + lexicon attribute NER for the deterministic relation tier.

An ``EntityRecognizer`` (Phase 2 protocol) that finds medication/lab attribute spans
without a model: strength, frequency, route, form, duration, lab value, lab unit.
MedEx-semantic-tagger / ``parsigs`` lineage. Covers the templated cases the research
(``docs/research/clinical-relation-extraction.md``) marks safe; narrative phrasing with
no number/keyword is missed by design — use GLiNER-BioMed or the LLM path for those.

Deliberately no SEVERITY / STAGE / BODY_SITE patterns (open-vocabulary).
"""

from __future__ import annotations

import hashlib
import re

from verichart.clinical.entities import EntityMention

# --- lexicons (word-ish alternations, matched case-insensitively with boundaries) ---

_LEXICONS: dict[str, tuple[str, ...]] = {
    "FREQUENCY": (
        "q\\.?d\\.?", "b\\.?i\\.?d\\.?", "t\\.?i\\.?d\\.?", "q\\.?i\\.?d\\.?",
        "q\\.?h\\.?s\\.?", "q\\.?a\\.?m\\.?", "q\\.?p\\.?m\\.?", "p\\.?r\\.?n\\.?",
        "q\\d+\\s?h", "q\\s?\\d+\\s?hours?", "every\\s+\\d+\\s+hours?", "every\\s+\\d+\\s+days?",
        "once\\s+(?:a\\s+|per\\s+)?(?:day|daily|week|weekly)", "twice\\s+(?:a\\s+|per\\s+)?(?:day|daily|week)",
        "three\\s+times\\s+(?:a\\s+|per\\s+)?(?:day|week)", "twice\\s+daily", "once\\s+daily",
        "daily", "nightly", "weekly", "hourly", "at\\s+bedtime", "with\\s+meals",
        "every\\s+morning", "every\\s+evening", "as\\s+needed",
    ),
    "ROUTE": (
        "p\\.?o\\.?", "by\\s+mouth", "orally", "i\\.?v\\.?", "intravenous(?:ly)?",
        "i\\.?m\\.?", "intramuscular(?:ly)?", "sub\\s?q", "subcut(?:aneous(?:ly)?)?", "s\\.?c\\.?",
        "s\\.?l\\.?", "sublingual(?:ly)?", "p\\.?r\\.?", "rectal(?:ly)?", "topical(?:ly)?",
        "inhaled", "inhalation", "nasal(?:ly)?", "ophthalmic", "otic", "transdermal", "per\\s+ng\\s?tube",
    ),
    "FORM": (
        "tablets?", "tabs?", "capsules?", "caps?", "solution", "suspension", "syrup",
        "patch(?:es)?", "inhaler", "creams?", "ointments?", "gel", "drops?", "sprays?",
        "lozenges?", "suppositor(?:y|ies)", "elixir", "powder", "lotion",
    ),
    "UNIT": (
        "%", "mg/dl", "mg/dL", "mmol/l", "mmol/L", "meq/l", "mEq/L", "g/dl", "g/dL",
        "iu/l", "IU/L", "ng/ml", "ng/mL", "u/l", "U/L", "cells/[uµ]l", "mm\\s?hg", "mmol/mol",
        "10\\^\\d+/l", "/[uµ]l", "beats/min", "bpm",
    ),
}

_STRENGTH_UNITS = r"(?:mg|mcg|[uµ]g|g|ml|units?|iu|meq|mmol|tabs?|tablets?|caps?|capsules?|drops?|puffs?|sprays?)"
# a numeric quantity + unit, optionally combined ("5 mg/5 mL"), optionally a range ("5-10 mg").
# The `(?!\s?/\s?[A-Za-z])` lookahead keeps lab concentration units ("1.1 mg/dL") out — those
# are VALUE + UNIT, not a drug strength.
_STRENGTH = re.compile(
    rf"\b\d+(?:\.\d+)?(?:\s?[-–]\s?\d+(?:\.\d+)?)?\s?{_STRENGTH_UNITS}"
    rf"(?:\s?/\s?\d+(?:\.\d+)?\s?{_STRENGTH_UNITS})?\b(?!\s?/\s?[A-Za-z])",
    re.IGNORECASE,
)
_DURATION = re.compile(
    r"\bfor\s+\d+\s+(?:days?|weeks?|months?|years?)\b"
    r"|\b(?:x|for)\s?\d+\s?(?:days?|weeks?|months?|d|wk|mo)\b"
    r"|\bover\s+the\s+next\s+\d+\s+(?:days?|weeks?|months?)\b",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"\b\d+(?:\.\d+)?\b")


def _lex_pattern(alts: tuple[str, ...]) -> re.Pattern:
    # sort longest-first so the alternation is greedy toward longer phrases
    body = "|".join(sorted(alts, key=len, reverse=True))
    return re.compile(rf"(?<![A-Za-z0-9])(?:{body})(?![A-Za-z0-9])", re.IGNORECASE)


_LEX_PATTERNS: dict[str, re.Pattern] = {k: _lex_pattern(v) for k, v in _LEXICONS.items()}


def _lexicon_hash() -> str:
    blob = repr(sorted((k, tuple(v)) for k, v in _LEXICONS.items())) + _STRENGTH.pattern + _DURATION.pattern
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


class AttributeRecognizer:
    """Regex/lexicon attribute NER. ``families`` selects which attribute groups to run."""

    digest = None

    def __init__(self, *, families: set[str] | None = None):
        self.families = families or {"MEDICATION", "LAB"}
        self.version = f"attr-regex@{_lexicon_hash()}"

    def recognize(self, text: str, labels: list[str]) -> list[EntityMention]:
        wanted = set(labels) if labels else None
        raw: list[tuple[str, int, int]] = []  # (label, start, end)

        if "MEDICATION" in self.families:
            for m in _STRENGTH.finditer(text):
                raw.append(("STRENGTH", *m.span()))
            for m in _DURATION.finditer(text):
                raw.append(("DURATION", *m.span()))
            for label in ("FREQUENCY", "ROUTE", "FORM"):
                for m in _LEX_PATTERNS[label].finditer(text):
                    raw.append((label, *m.span()))

        if "LAB" in self.families:
            for m in _LEX_PATTERNS["UNIT"].finditer(text):
                raw.append(("UNIT", *m.span()))

        # resolve overlaps: longest span wins, earlier on a tie
        raw.sort(key=lambda t: (t[1], -(t[2] - t[1])))
        kept: list[tuple[str, int, int]] = []
        occupied_end = -1
        for label, s, e in sorted(raw, key=lambda t: (t[1], -(t[2] - t[1]))):
            if s >= occupied_end:
                kept.append((label, s, e))
                occupied_end = e

        # VALUE: bare numbers not already consumed by a strength/unit/duration span
        if "LAB" in self.families:
            consumed = [(s, e) for _, s, e in kept]
            for m in _NUMBER.finditer(text):
                ns, ne = m.span()
                if not any(s <= ns < e for s, e in consumed):
                    kept.append(("VALUE", ns, ne))

        kept.sort(key=lambda t: t[1])
        out: list[EntityMention] = []
        for label, s, e in kept:
            if wanted is not None and label not in wanted:
                continue
            out.append(EntityMention(
                text=text[s:e], char_start=s, char_end=e,
                label=label, raw_label=label, score=1.0, recognizer=self.version,
            ))
        return out
