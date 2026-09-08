"""Assertion classification: map ConText-style flags to a ClinicalFact assertion_status.

The medspaCy adapter (Task 5) lives here too. This module is import-safe without the
``verichart[clinical]`` extra — ``MedspacyContextClassifier`` imports ``medspacy`` lazily
in ``__init__`` and raises a clear error if it is missing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from verichart.clinical.entities import _empty_assertion
from verichart.facts import AssertionStatus

if TYPE_CHECKING:
    from verichart.clinical.entities import AssertionResult

# First match wins. Negation is the strongest signal; "uncertain" is the weakest
# non-default. "patient_reported" is not produced by stock ConText — it needs a
# custom modifier rule and is set by the classifier, not derived here.
_PRECEDENCE: list[tuple[str, AssertionStatus]] = [
    ("is_negated", "ruled_out"),
    ("is_family", "family_history"),
    ("is_hypothetical", "hypothetical"),
    ("is_historical", "historical"),
    ("is_uncertain", "uncertain"),
]


def derive_assertion_status(result: AssertionResult) -> AssertionStatus:
    """ConText flags -> assertion_status. No flag set -> 'confirmed' (an entity a
    classifier examined and found unmodified is asserted-present)."""
    for flag, status in _PRECEDENCE:
        if result.get(flag):
            return status
    return "confirmed"


class MedspacyContextClassifier:
    """AssertionClassifier backed by medspaCy's ConText algorithm.

    Needs the ``verichart[clinical]`` extra. Deterministic and rule-versioned:
    ``.version`` reflects the medspaCy version and, if custom rules are supplied,
    their content hash.
    """

    def __init__(self, rules: list | None = None):
        try:
            import medspacy  # noqa: F401
            import spacy
        except ImportError as e:  # pragma: no cover - exercised only without the extra
            raise ImportError(
                "MedspacyContextClassifier needs the clinical extra: pip install 'verichart[clinical]'"
            ) from e

        try:  # PyRuSH is chatty on import/use
            from loguru import logger

            logger.disable("PyRuSH")
        except Exception:  # pragma: no cover
            pass

        nlp = spacy.blank("en")
        nlp.add_pipe("sentencizer")
        context = nlp.add_pipe("medspacy_context")
        if rules:
            context.add(rules)

        self._nlp = nlp
        self._context = context

        import hashlib

        import medspacy as _medspacy

        rule_hash = (
            hashlib.sha256(repr(rules).encode()).hexdigest()[:12] if rules else "default"
        )
        self.version = f"medspacy-context@{_medspacy.__version__}+{rule_hash}"

    def classify(self, text: str, mentions: list) -> list:
        from spacy.util import filter_spans

        doc = self._nlp.make_doc(text)
        doc = self._nlp.get_pipe("sentencizer")(doc)

        spans = []
        span_to_idx: dict[tuple[int, int], int] = {}
        for i, m in enumerate(mentions):
            cs = doc.char_span(
                m["char_start"], m["char_end"],
                label=m["label"] or "ENTITY", alignment_mode="expand",
            )
            if cs is not None:
                spans.append(cs)
                span_to_idx[(cs.start, cs.end)] = i

        doc.ents = filter_spans(spans)
        doc = self._context(doc)

        results = [_empty_assertion() for _ in mentions]
        for ent in doc.ents:
            i = span_to_idx.get((ent.start, ent.end))
            if i is None:
                continue
            results[i] = _assertion_from_ent(ent)
        return results


def _assertion_from_ent(ent) -> "AssertionResult":
    from verichart.clinical.entities import AssertionResult

    modifiers: list[str] = []
    try:
        for mod in ent._.modifiers or ():
            category = getattr(mod, "category", None) or "MODIFIER"
            phrase = ""
            span = getattr(mod, "modifier_span", None)
            if span is not None:
                try:
                    phrase = ent.doc[span[0]:span[1]].text
                except Exception:  # pragma: no cover
                    phrase = ""
            modifiers.append(f"{category}: {phrase}".strip().rstrip(":").strip())
    except Exception:  # pragma: no cover
        pass

    return AssertionResult(
        is_negated=bool(ent._.is_negated),
        is_historical=bool(ent._.is_historical),
        is_hypothetical=bool(ent._.is_hypothetical),
        is_family=bool(ent._.is_family),
        is_uncertain=bool(ent._.is_uncertain),
        modifiers=modifiers,
    )
