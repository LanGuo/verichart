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

from veritract import Span

from verichart.clinical.entities import EntityMention
from verichart.facts import ClinicalFact, _now_iso, make_fact

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


# --------------------------------------------------------------------- RuleRelationLinker

_ANCHOR_FAMILIES = {"MEDICATION", "LAB", "PROBLEM"}

# attribute -> (anchor family, directional prior, safe for a deterministic linker)
#   "follows"  = attribute normally follows the anchor  ("metformin ... PO BID")
#   "precedes" = attribute normally precedes the anchor ("severe pneumonia")
#   "either"   = no strong prior
_ATTR_RULES: dict[str, tuple[str, str, bool]] = {
    "STRENGTH":   ("MEDICATION", "either",   True),
    "DOSE":       ("MEDICATION", "either",   True),
    "FORM":       ("MEDICATION", "either",   True),
    "ROUTE":      ("MEDICATION", "follows",  True),
    "FREQUENCY":  ("MEDICATION", "follows",  True),
    "DURATION":   ("MEDICATION", "follows",  False),
    "VALUE":      ("LAB",        "follows",  True),
    "UNIT":       ("LAB",        "follows",  True),
    "SEVERITY":   ("PROBLEM",    "precedes", True),
    "LATERALITY": ("PROBLEM",    "either",   True),
    "BODY_SITE":  ("PROBLEM",    "follows",  False),
    "STAGE":      ("PROBLEM",    "follows",  False),
}

_SAFE_RELATIONS = frozenset(
    _RELATION_FOR_ATTR[a] for a, (_, _, safe) in _ATTR_RULES.items() if safe
)

# split on sentence/clause punctuation but not inside a decimal ("8.2") or a grouped
# number ("1,000"); newlines always split.
_SCOPE_SPLIT = {
    "sentence": re.compile(r"(?<!\d)[.;!?](?!\d)|\n"),
    "clause": re.compile(r"(?<!\d)[.;!?,](?!\d)|\n"),
}
_COORD_CUE = re.compile(r"\brespectively\b", re.IGNORECASE)


class RuleRelationLinker:
    """Ordered-directional linker (MedEx lineage) — deterministic, no model.

    For each attribute mention, link to the nearest anchor of the compatible family in
    the same scope unit, biased by a per-attribute-type directional prior. Positional
    coordination ("... 500 mg and 10 mg respectively") is declined, not guessed.

    Only the research-"safe" relation types are emitted by default
    (``docs/research/clinical-relation-extraction.md``); pass ``emit_relations`` to add
    duration / body-site / stage etc. (route those to ``LlmRelationExtractor`` instead).
    """

    digest = None

    def __init__(self, *, emit_relations: set[str] | None = None, scope: str = "sentence"):
        if scope not in _SCOPE_SPLIT:
            raise ValueError(f"scope must be one of {sorted(_SCOPE_SPLIT)}")
        self.emit_relations = frozenset(emit_relations) if emit_relations else _SAFE_RELATIONS
        self.scope = scope
        import hashlib

        tag = hashlib.sha256(
            repr((sorted(self.emit_relations), scope)).encode()
        ).hexdigest()[:8]
        self.version = f"rule-relations@{tag}:{scope}"

    def _scope_of(self, pos: int, bounds: list[tuple[int, int]]) -> int:
        for i, (s, e) in enumerate(bounds):
            if s <= pos < e:
                return i
        return -1

    def extract(self, text: str, entities: list[EntityMention]) -> list[ClinicalRelation]:
        # scope-unit boundaries
        bounds: list[tuple[int, int]] = []
        pos = 0
        for piece in _SCOPE_SPLIT[self.scope].split(text):
            bounds.append((pos, pos + len(piece)))
            pos += len(piece) + 1

        anchors = [e for e in entities if e["label"] in _ANCHOR_FAMILIES]
        attrs = [e for e in entities if e["label"] in _ATTR_RULES]
        scope_of = {id(a): self._scope_of(a["char_start"], bounds) for a in anchors + attrs}

        rels: list[ClinicalRelation] = []
        for t in sorted(attrs, key=lambda m: m["char_start"]):
            family, prior, _safe = _ATTR_RULES[t["label"]]
            relation = _RELATION_FOR_ATTR[t["label"]]
            if relation not in self.emit_relations:
                continue

            sc = scope_of[id(t)]
            scope_text = text[bounds[sc][0]:bounds[sc][1]] if 0 <= sc < len(bounds) else ""
            cands = [a for a in anchors if a["label"] == family and scope_of[id(a)] == sc]
            if not cands:
                continue

            n_same_type = sum(
                1 for x in attrs if x["label"] == t["label"] and scope_of[id(x)] == sc
            )
            if len(cands) >= 2 and n_same_type >= 2 and _COORD_CUE.search(scope_text):
                continue  # positional coordination — LLM territory

            before = [a for a in cands if a["char_end"] <= t["char_start"]]
            after = [a for a in cands if a["char_start"] >= t["char_end"]]
            nearest_before = max(before, key=lambda a: a["char_end"], default=None)
            nearest_after = min(after, key=lambda a: a["char_start"], default=None)

            head, method, score = self._choose(
                prior, nearest_before, nearest_after, only=len(cands) == 1
            )
            if head is None:
                continue

            rels.append(ClinicalRelation(
                relation=relation, head=head, tail=t, score=score,
                extractor=self.version, method=method,
                direction=_direction(head, t), token_gap=_token_gap(text, head, t),
            ))
        return rels

    @staticmethod
    def _choose(prior, nearest_before, nearest_after, *, only):
        if only:
            head = nearest_before or nearest_after
            return head, "rule:only-anchor", 1.0
        if prior == "follows":
            if nearest_before is not None:
                return nearest_before, "rule:follows-prior", 1.0
            return nearest_after, "rule:follows-fallback", 0.7
        if prior == "precedes":
            if nearest_after is not None:
                return nearest_after, "rule:precedes-prior", 1.0
            return nearest_before, "rule:precedes-fallback", 0.7
        # "either" — nearest overall
        if nearest_before is None:
            return nearest_after, "rule:either-prior", 1.0
        if nearest_after is None:
            return nearest_before, "rule:either-prior", 1.0
        return (nearest_before, "rule:either-prior", 1.0)  # tie: prefer the left anchor


# --------------------------------------------------------------------- relations_to_facts

_WS = re.compile(r"\s+")


def _ws(s: str) -> str:
    return _WS.sub(" ", s).strip()


def _span_from_mention(m: EntityMention, *, doc_id, source_type) -> Span:
    return Span(
        doc_id=doc_id, source_type=source_type,
        char_start=m["char_start"], char_end=m["char_end"],
        text=m["text"], provenance_type="direct",
    )


def _attr_of(relation: str) -> str:
    return _ATTR_FOR_RELATION.get(relation, relation.removeprefix("HAS_"))


def relations_to_facts(
    text: str,
    relations: list[ClinicalRelation],
    entity_facts: list[ClinicalFact],
    *,
    manifest: dict | None = None,
    created_at: str | None = None,
    keep_unlinked_attributes: bool = False,
) -> list[ClinicalFact]:
    """Merge relations into ``entity_facts``: one composite ``ClinicalFact`` per anchor
    with links, replacing the bare anchor fact and any linked-attribute facts. Anchors
    with no links pass through unchanged.

    Pure — returns a new list, does not mutate ``entity_facts``.
    """
    stamp = created_at if created_at is not None else _now_iso()
    manifest_id = manifest["manifest_id"] if manifest else None

    facts_by_span: dict[tuple[int, int], ClinicalFact] = {
        (f["span"]["char_start"], f["span"]["char_end"]): f
        for f in entity_facts if f["span"] is not None
    }

    groups: dict[tuple[int, int], list[ClinicalRelation]] = {}
    for r in relations:
        key = (r["head"]["char_start"], r["head"]["char_end"])
        groups.setdefault(key, []).append(r)

    consumed: set[tuple[int, int]] = set()
    composites: list[ClinicalFact] = []

    for head_key, rels in groups.items():
        head = rels[0]["head"]
        base = facts_by_span.get(head_key)
        consumed.add(head_key)

        doc_id = base["span"]["doc_id"] if base else None
        source_type = base["span"]["source_type"] if base else "clinical_note"

        order = _ASSEMBLY.get(head["label"], [])
        rank = {rel: i for i, rel in enumerate(order)}
        rels_sorted = sorted(
            rels, key=lambda r: (rank.get(r["relation"], 99), r["tail"]["char_start"])
        )
        for r in rels_sorted:
            consumed.add((r["tail"]["char_start"], r["tail"]["char_end"]))

        tails = [r["tail"] for r in rels_sorted]
        assembled = " ".join([head["text"], *[t["text"] for t in tails]])

        spans = [head, *tails]
        cover_start = min(s["char_start"] for s in spans)
        cover_end = max(s["char_end"] for s in spans)
        cover_text = text[cover_start:cover_end]
        provenance = "direct" if _ws(cover_text) == _ws(assembled) else "inferred"

        supporting = [_span_from_mention(m, doc_id=doc_id, source_type=source_type) for m in spans]
        cover_span = Span(
            doc_id=doc_id, source_type=source_type,
            char_start=cover_start, char_end=cover_end,
            text=cover_text, provenance_type=provenance,
        )

        confidence = min(
            [head["score"], *[t["score"] for t in tails], *[r["score"] for r in rels_sorted]]
        )
        rel_note = ", ".join(f"{r['relation']}={r['tail']['text']!r}" for r in rels_sorted)
        note = f"relations: {rel_note}; {rels_sorted[0]['extractor']}"

        fact = make_fact(
            label=head["label"],
            value=assembled,
            span=cover_span,
            provenance_type=provenance,
            confidence=confidence,
            note=note,
            patient_pseudonym=base["patient_pseudonym"] if base else None,
            effective_date=base["effective_date"] if base else None,
            manifest_id=(base["manifest_id"] if base else None) or manifest_id,
            model_tag=rels_sorted[0]["extractor"],
            model_digest=None,
            created_at=stamp,
            assertion_status=base["assertion_status"] if base else "unknown",
            supporting_spans=supporting,
        )
        if base:  # carry a resolved concept through
            for k in ("concept_code", "concept_system", "concept_display", "terminology_version"):
                fact[k] = base[k]  # type: ignore[literal-required]
            fact["fact_id"] = _refresh_fact_id(fact)
        composites.append(fact)

    _ATTR_LABELS = set(_ATTR_RULES)
    survivors: list[ClinicalFact] = []
    for f in entity_facts:
        key = (f["span"]["char_start"], f["span"]["char_end"]) if f["span"] else None
        if key in consumed:
            continue
        if (not keep_unlinked_attributes) and f["label"] in _ATTR_LABELS:
            continue
        survivors.append(f)

    out = composites + survivors
    out.sort(key=lambda f: f["span"]["char_start"] if f["span"] else 1 << 30)
    return out


def _refresh_fact_id(fact: ClinicalFact) -> str:
    from verichart.facts import compute_fact_id

    return compute_fact_id(
        label=fact["label"], value=fact["value"],
        concept_code=fact["concept_code"], concept_system=fact["concept_system"],
        span=fact["span"], manifest_id=fact["manifest_id"],
    )


def extract_relations(
    text: str,
    *,
    recognizer,
    anchor_facts: list[ClinicalFact],
    attribute_labels: list[str],
    extractor: RelationExtractor,
    manifest: dict | None = None,
    created_at: str | None = None,
    keep_unlinked_attributes: bool = False,
) -> list[ClinicalFact]:
    """Convenience: recognize attribute spans, link them to ``anchor_facts``, and merge
    into composite facts. The pieces (``mentions_from_facts``, ``extractor.extract``,
    ``relations_to_facts``) stay public for callers who want finer control.
    """
    attr_mentions = recognizer.recognize(text, attribute_labels)
    head_mentions = mentions_from_facts(anchor_facts)
    relations = extractor.extract(text, head_mentions + attr_mentions)
    return relations_to_facts(
        text, relations, anchor_facts,
        manifest=manifest, created_at=created_at,
        keep_unlinked_attributes=keep_unlinked_attributes,
    )


# --------------------------------------------------------------------- LlmRelationExtractor

_LLM_ATTRIBUTES: dict[str, list[str]] = {
    "MEDICATION": ["strength", "dose", "form", "route", "frequency", "duration"],
    "LAB": ["value", "unit"],
    "PROBLEM": ["severity", "laterality", "body_site", "stage"],
}


class LlmRelationExtractor:
    """Per-anchor relation extraction via veritract: a constrained-decoding schema of the
    anchor's attributes, then ``ground`` — so an attribute the model invents but cannot be
    found in the text is dropped, never linked. One LLM call per anchor.
    """

    def __init__(self, llm, *, attributes_for: dict[str, list[str]] | None = None,
                 ground_mode: str = "fuzzy"):
        self.llm = llm
        self.attributes_for = attributes_for or _LLM_ATTRIBUTES
        self.ground_mode = ground_mode
        self.version = f"llm-relations@{getattr(llm, 'model', 'unknown')}"
        try:
            self.digest = llm.model_digest()
        except Exception:
            self.digest = None

    def extract(self, text: str, entities: list[EntityMention]) -> list[ClinicalRelation]:
        from veritract import extract_raw, ground

        anchors = [e for e in entities if e["label"] in _ANCHOR_FAMILIES]
        rels: list[ClinicalRelation] = []

        for anchor in anchors:
            fields = self.attributes_for.get(anchor["label"])
            if not fields:
                continue
            schema = {
                "type": "object",
                "properties": {f: {"type": "string"} for f in fields},
                "required": list(fields),
            }
            prompt = (
                f"From the clinical text, extract the attributes of the "
                f"{anchor['label'].lower()} \"{anchor['text']}\" "
                f"(characters {anchor['char_start']}–{anchor['char_end']}).\n"
                f"For each field give the exact verbatim phrase from the text, or \"\" if absent. "
                f"Do not paraphrase."
            )
            raw = extract_raw(text, schema, self.llm, prompt=prompt, source_type="clinical_note")
            result = ground(raw, self.llm, mode=self.ground_mode)

            for field, gf in result.extracted.items():
                span = gf["span"]
                if span is None:
                    continue  # a relation needs a locatable tail
                attr = field.upper()
                relation = _RELATION_FOR_ATTR.get(attr)
                if relation is None:
                    continue
                tail = EntityMention(
                    text=span["text"], char_start=span["char_start"], char_end=span["char_end"],
                    label=attr, raw_label=field, score=gf["confidence"] / 100.0,
                    recognizer=self.version,
                )
                rels.append(ClinicalRelation(
                    relation=relation, head=anchor, tail=tail,
                    score=gf["confidence"] / 100.0, extractor=self.version,
                    method="llm-grounded", direction=_direction(anchor, tail),
                    token_gap=_token_gap(text, anchor, tail),
                ))
        return rels
