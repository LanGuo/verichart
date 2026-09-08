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


class GlinerBiomedRecognizer:
    """Zero-shot clinical span NER via GLiNER-BioMed.

    Default model ``Ihor/gliner-biomed-bi-base-v1.0`` (Apache-2.0, bi-encoder, CPU-tolerable).
    ``.digest`` is the resolved Hugging Face repo revision sha; ``.version`` embeds it.
    Long documents are chunked on a fixed character window with overlap and de-duplicated.
    """

    def __init__(
        self,
        model_id: str = "Ihor/gliner-biomed-bi-base-v1.0",
        *,
        revision: str | None = None,
        threshold: float = 0.35,
        device: str | None = None,
        chunk_chars: int = 1800,
        chunk_overlap: int = 200,
    ):
        try:
            from gliner import GLiNER
        except ImportError as e:  # pragma: no cover
            raise ImportError(
                "GlinerBiomedRecognizer needs the clinical extra: pip install 'verichart[clinical]'"
            ) from e

        self.model = GLiNER.from_pretrained(model_id, revision=revision)
        if device:
            self.model = self.model.to(device)
        self.threshold = threshold
        self._chunk_chars = chunk_chars
        self._chunk_overlap = chunk_overlap

        sha = None
        try:
            from huggingface_hub import model_info

            sha = model_info(model_id, revision=revision).sha
        except Exception:  # pragma: no cover - offline / private
            pass
        self.digest = f"hf:{sha}" if sha else None
        self.version = f"{model_id}@{(sha or 'unknown')[:12]}"

    def _chunks(self, text: str):
        if len(text) <= self._chunk_chars:
            yield 0, text
            return
        step = self._chunk_chars - self._chunk_overlap
        for start in range(0, len(text), step):
            yield start, text[start:start + self._chunk_chars]
            if start + self._chunk_chars >= len(text):
                break

    def recognize(self, text: str, labels: list[str]) -> list[EntityMention]:
        seen: set[tuple[int, int, str]] = set()
        out: list[EntityMention] = []
        for offset, chunk in self._chunks(text):
            for e in self.model.predict_entities(chunk, labels, threshold=self.threshold):
                cs, ce = offset + e["start"], offset + e["end"]
                key = (cs, ce, e["label"])
                if key in seen:
                    continue
                seen.add(key)
                out.append(EntityMention(
                    text=e["text"],
                    char_start=cs,
                    char_end=ce,
                    label=normalize_label(e["label"]),
                    raw_label=e["label"],
                    score=float(e["score"]),
                    recognizer=self.version,
                ))
        out.sort(key=lambda m: (m["char_start"], m["char_end"]))
        return out
