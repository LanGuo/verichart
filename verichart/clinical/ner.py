"""Entity recognizers — the ``EntityRecognizer`` slot of the Phase 2 pipeline.

- ``MedspacyRuleRecognizer`` — deterministic, no model download; for callers with a
  curated term list. Also the sensible default when ``gliner`` is not installed.
- ``GlinerBiomedRecognizer`` — GLiNER-BioMed zero-shot span NER (Task 7).

Both need the ``verichart[clinical]`` extra and import their backend lazily.
"""

from __future__ import annotations

import hashlib

from verichart.clinical.entities import EntityMention, normalize_label


class MedspacyRuleRecognizer:
    """Rule-based clinical NER via medspaCy ``TargetRule`` / ``medspacy_target_matcher``.

    ``rules`` is a list of ``(literal, label)`` tuples or medspaCy ``TargetRule`` objects.
    Matches are exact character spans with ``score=1.0``.
    """

    digest = None

    def __init__(self, rules: list):
        try:
            import spacy
            from medspacy.ner import TargetRule
        except ImportError as e:  # pragma: no cover
            raise ImportError(
                "MedspacyRuleRecognizer needs the clinical extra: pip install 'verichart[clinical]'"
            ) from e

        try:
            from loguru import logger

            logger.disable("PyRuSH")
        except Exception:  # pragma: no cover
            pass

        target_rules = []
        for r in rules:
            if isinstance(r, tuple):
                target_rules.append(TargetRule(literal=r[0], category=r[1]))
            else:
                target_rules.append(r)

        nlp = spacy.blank("en")
        nlp.add_pipe("sentencizer")
        matcher = nlp.add_pipe("medspacy_target_matcher")
        matcher.add(target_rules)

        self._nlp = nlp
        digest = hashlib.sha256(
            repr([(getattr(r, "literal", None) or getattr(r, "category", None), getattr(r, "category", None))
                  if not isinstance(r, tuple) else r for r in rules]).encode()
        ).hexdigest()[:12]
        self.version = f"medspacy-rules@{digest}"

    def recognize(self, text: str, labels: list[str]) -> list[EntityMention]:
        wanted = set(labels) if labels else None
        doc = self._nlp(text)
        out: list[EntityMention] = []
        for ent in doc.ents:
            raw = ent.label_
            norm = normalize_label(raw)
            if wanted is not None and raw not in wanted and norm not in wanted:
                continue
            out.append(EntityMention(
                text=ent.text,
                char_start=ent.start_char,
                char_end=ent.end_char,
                label=norm,
                raw_label=raw,
                score=1.0,
                recognizer=self.version,
            ))
        out.sort(key=lambda m: m["char_start"])
        return out
