"""Temporal reasoning, inference rules, and decay (Phase 6): derive facts that aren't stated
verbatim, normalize relative dates, and let callers ask whether a time-sensitive fact is stale.

**Who makes the clinical judgment call.** verichart ships zero default inference rules, zero
default decay windows, and zero default indication data. ``ConceptTriggerRule`` and
``MedRtTriggerRule`` are mechanisms; the *deploying organization's clinical/informatics team*
supplies and owns the actual rule content (which drug implies which diagnosis, which lab needs
what validity window) — the same governance a hospital's CDS committee applies before a
drug-interaction rule goes live. This extends Phase 3's "no vocabulary content shipped" to
clinical *reasoning* content, not just terminology. Full research behind the choices in this
module (why ``dateparser``, why no decay defaults, why two inference-rule flavors) is in
``docs/research/temporal-reasoning-and-decay.md``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from verichart.facts import compute_fact_id, make_fact

if TYPE_CHECKING:
    from verichart.facts import ClinicalFact


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@runtime_checkable
class ReasoningRule(Protocol):
    name: str
    version: str

    def apply(self, facts: list["ClinicalFact"]) -> list["ClinicalFact"]: ...


def _concept_key_of(system: str | None, code: str | None) -> str | None:
    return f"{system}:{code}" if code else None


def _already_has_concept(facts: list["ClinicalFact"], system: str, code: str) -> bool:
    """True if any fact — stated directly or previously inferred — already asserts this
    concept. The guard every rule in this module uses before deriving a new fact."""
    target = _concept_key_of(system, code)
    return any(
        f["concept_code"] and _concept_key_of(f["concept_system"], f["concept_code"]) == target
        for f in facts
    )


def apply_rules(
    facts: list["ClinicalFact"], rules: list[ReasoningRule], *, created_at: str | None = None,
) -> list["ClinicalFact"]:
    """``facts`` plus every rule's derived facts appended. Pure — does not mutate ``facts``.
    Removing a rule from ``rules`` changes exactly that rule's contribution; nothing is
    stateful across calls."""
    out = list(facts)
    for rule in rules:
        out.extend(rule.apply(facts))
    return out


def reasoning_versions(rules: list[ReasoningRule]) -> dict[str, str]:
    """``{"reasoning:<name>": version, ...}`` — merge into
    ``veritract.build_manifest(extra={"rule_versions": {**rule_versions(policy),
    **reasoning_versions(rules)}})`` alongside Phase 5's contribution to the same dict."""
    return {f"reasoning:{r.name}": r.version for r in rules}


# --------------------------------------------------------------------- ConceptTriggerRule


class ConceptTriggerRule:
    """Fixed-mapping inference: "if the patient has concept X, and doesn't already have concept
    Y stated anywhere, derive Y at an explicit confidence." For a deployer who has already
    reviewed and approved this specific, narrow rule — see the module docstring on who owns the
    content.
    """

    def __init__(
        self,
        *,
        name: str,
        version: str,
        trigger_concept: tuple[str, str],   # (system, code)
        infer: dict,                         # label, value, concept_system, concept_code
        confidence: float,
        assertion_status: str = "unknown",
    ):
        self.name = name
        self.version = version
        self.trigger_system, self.trigger_code = trigger_concept
        self.infer = infer
        self.confidence = confidence
        self.assertion_status = assertion_status

    def apply(self, facts: list["ClinicalFact"]) -> list["ClinicalFact"]:
        derived: list["ClinicalFact"] = []
        for f in facts:
            if f["concept_system"] != self.trigger_system or f["concept_code"] != self.trigger_code:
                continue
            if _already_has_concept(facts + derived, self.infer["concept_system"], self.infer["concept_code"]):
                continue
            derived.append(self._build(f))
        return derived

    def _build(self, trigger: "ClinicalFact") -> "ClinicalFact":
        stamp = _now_iso()
        fact = make_fact(
            label=self.infer["label"],
            value=self.infer["value"],
            span=None,
            provenance_type="inferred",
            confidence=self.confidence,
            note=f"inferred by rule {self.name!r} from {trigger['fact_id']} ({trigger['value']!r})",
            patient_pseudonym=trigger["patient_pseudonym"],
            effective_date=trigger["effective_date"],
            manifest_id=trigger["manifest_id"],
            model_tag=self.version,
            model_digest=None,
            created_at=stamp,
            assertion_status=self.assertion_status,
        )
        fact["concept_code"] = self.infer["concept_code"]
        fact["concept_system"] = self.infer["concept_system"]
        fact["concept_display"] = self.infer.get("concept_display")
        fact["fact_id"] = compute_fact_id(
            label=fact["label"], value=fact["value"],
            concept_code=fact["concept_code"], concept_system=fact["concept_system"],
            span=None, manifest_id=fact["manifest_id"],
        )
        return fact


# --------------------------------------------------------------------- AbsenceRule


class AbsenceRule:
    """Absence-as-negative, scoped: infer a negative finding only from a document type that
    would plausibly assert the concept if positive (e.g. a screening panel) — never from an
    unrelated document that simply doesn't mention it.

    Checks ``concept`` (the screening/test concept) **globally**, not just within the
    triggering document: if the actual test result was recorded anywhere for this patient — even
    in a document outside ``applies_to_source_types`` — the absence in *this* document must not
    be used to infer a result; a real answer exists elsewhere.
    """

    def __init__(
        self,
        *,
        name: str,
        version: str,
        concept: tuple[str, str],            # (system, code) — the screening/test concept
        applies_to_source_types: set[str],
        infer: dict,                          # label, value, concept_system, concept_code
        confidence: float,
    ):
        self.name = name
        self.version = version
        self.concept_system, self.concept_code = concept
        self.applies_to_source_types = set(applies_to_source_types)
        self.infer = infer
        self.confidence = confidence

    def apply(self, facts: list["ClinicalFact"]) -> list["ClinicalFact"]:
        if _already_has_concept(facts, self.concept_system, self.concept_code):
            return []  # the real result exists somewhere; never guess over it

        by_doc: dict[str, list["ClinicalFact"]] = {}
        for f in facts:
            if f["span"]:
                by_doc.setdefault(f["span"]["doc_id"], []).append(f)

        derived: list["ClinicalFact"] = []
        for doc_facts in by_doc.values():
            source_types = {f["span"]["source_type"] for f in doc_facts}
            if not (source_types & self.applies_to_source_types):
                continue
            derived.append(self._build(doc_facts[0]))
        return derived

    def _build(self, context_fact: "ClinicalFact") -> "ClinicalFact":
        stamp = _now_iso()
        fact = make_fact(
            label=self.infer["label"],
            value=self.infer["value"],
            span=None,
            provenance_type="inferred",
            confidence=self.confidence,
            note=(f"absence-as-negative: no {self.concept_system}:{self.concept_code} fact in "
                 f"doc {context_fact['span']['doc_id']!r} ({context_fact['span']['source_type']})"),
            patient_pseudonym=context_fact["patient_pseudonym"],
            effective_date=context_fact["effective_date"],
            manifest_id=context_fact["manifest_id"],
            model_tag=self.version,
            model_digest=None,
            created_at=stamp,
            assertion_status="unknown",
        )
        fact["concept_code"] = self.infer["concept_code"]
        fact["concept_system"] = self.infer["concept_system"]
        fact["concept_display"] = self.infer.get("concept_display")
        fact["fact_id"] = compute_fact_id(
            label=fact["label"], value=fact["value"],
            concept_code=fact["concept_code"], concept_system=fact["concept_system"],
            span=None, manifest_id=fact["manifest_id"],
        )
        return fact


# --------------------------------------------------------------------- temporal normalization
#
# A regex only *finds* candidate phrases; ``dateparser`` (pure Python, actively maintained)
# does the actual date arithmetic — no hand-rolled timedelta math. See
# docs/research/temporal-reasoning-and-decay.md for why. This is a cue-phrase floor, not a
# general temporal-relation system; no such system exists as an installable Python library.

_ABSOLUTE_CUE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b"
    r"|\b\d{1,2}/\d{1,2}/\d{2,4}\b"
    r"|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}\b"
    r"|\bin\s+\d{4}\b",
    re.IGNORECASE,
)
_RELATIVE_CUE = re.compile(
    r"\b\d+\s+(?:days?|weeks?|months?|years?)\s+ago\b"
    r"|\blast\s+(?:week|month|year)\b"
    r"|\byesterday\b|\btoday\b",
    re.IGNORECASE,
)
_TEMPORAL_CUES = re.compile(
    f"(?:{_ABSOLUTE_CUE.pattern})|(?:{_RELATIVE_CUE.pattern})", re.IGNORECASE
)
# Split on sentence punctuation unless it's sandwiched between two digits (a decimal like
# "8.2"); a trailing date ("...on 2026-06-01.") must still split even though a digit precedes
# the period. Newlines always split.
_SENTENCE_SPLIT = re.compile(r"(?<!\d)[.;!?]|[.;!?](?!\d)|\n")


def _is_relative_cue(cue_text: str) -> bool:
    return _RELATIVE_CUE.fullmatch(cue_text.strip()) is not None


def _sentence_bounds(text: str) -> list[tuple[int, int]]:
    bounds = []
    pos = 0
    for piece in _SENTENCE_SPLIT.split(text):
        bounds.append((pos, pos + len(piece)))
        pos += len(piece) + 1
    return bounds


def _scope_of(pos: int, bounds: list[tuple[int, int]]) -> int:
    for i, (s, e) in enumerate(bounds):
        if s <= pos < e:
            return i
    return -1


def _normalize_temporal(candidate: str, document_date: str | None) -> str | None:
    import dateparser

    settings = {"PREFER_DATES_FROM": "past"}
    if document_date:
        try:
            settings["RELATIVE_BASE"] = datetime.fromisoformat(document_date)
        except ValueError:
            pass
    dt = dateparser.parse(candidate, settings=settings)
    return dt.date().isoformat() if dt else None


def assign_effective_dates(
    facts: list["ClinicalFact"],
    documents: dict[str, str],
    *,
    document_dates: dict[str, str] | None = None,
) -> list["ClinicalFact"]:
    """Fill ``effective_date`` from relative/absolute temporal phrases near a fact's mention.

    Never overwrites an existing ``effective_date``. A relative phrase ("3 weeks ago") needs an
    anchor — ``document_dates[doc_id]``, or (if absent) the first absolute expression found
    anywhere in that document's text — and is left unresolved, not guessed, when neither exists.
    An absolute phrase resolves regardless of anchor. Pure — returns new fact dicts.
    """
    document_dates = dict(document_dates) if document_dates else {}
    out: list["ClinicalFact"] = []

    for f in facts:
        if f["effective_date"] is not None or f["span"] is None:
            out.append(dict(f))
            continue

        doc_id = f["span"]["doc_id"]
        text = documents.get(doc_id)
        if text is None:
            out.append(dict(f))
            continue

        bounds = _sentence_bounds(text)
        sc = _scope_of(f["span"]["char_start"], bounds)
        if sc < 0:
            out.append(dict(f))
            continue
        s_start, s_end = bounds[sc]

        cues = [
            (m.start(), m.end(), m.group())
            for m in _TEMPORAL_CUES.finditer(text)
            if s_start <= m.start() < s_end
        ]
        if not cues:
            out.append(dict(f))
            continue

        mstart = f["span"]["char_start"]
        cue_text = min(cues, key=lambda c: min(abs(c[0] - mstart), abs(c[1] - mstart)))[2]

        if _is_relative_cue(cue_text):
            anchor = document_dates.get(doc_id)
            if anchor is None:
                abs_match = _ABSOLUTE_CUE.search(text)
                anchor = _normalize_temporal(abs_match.group(), None) if abs_match else None
            if anchor is None:
                out.append(dict(f))  # no anchor available -- leave unresolved, don't guess
                continue
            resolved = _normalize_temporal(cue_text, anchor)
        else:
            resolved = _normalize_temporal(cue_text, None)

        new = dict(f)
        if resolved:
            new["effective_date"] = resolved
            note_bits = [f["note"]] if f["note"] else []
            note_bits.append(f"effective_date inferred from {cue_text!r} -> {resolved}")
            new["note"] = "; ".join(note_bits)
        out.append(new)

    return out


# --------------------------------------------------------------------- decay


@dataclass(frozen=True)
class DecayRule:
    """How long a concept's value stays valid. ``source`` is required — a citation, even if
    that citation is "internal clinical review, <date>" — never a bare number with no
    provenance. No universal clinical staleness knowledge base exists (see
    docs/research/temporal-reasoning-and-decay.md); this is a nudge toward at least writing
    down where a window came from.

    ``concept_key`` (most specific) is tried before ``label`` (fallback). At least one of the
    two must be set.
    """

    max_age_days: float
    source: str
    concept_key: str | None = None
    label: str | None = None

    def __post_init__(self):
        if not self.source:
            raise ValueError("DecayRule.source is required — cite where this window comes from")
        if not self.concept_key and not self.label:
            raise ValueError("DecayRule needs concept_key and/or label to match a fact against")


DEFAULT_DECAY_RULES: list[DecayRule] = []  # ships empty — see the module docstring


def decay_window_days(fact: "ClinicalFact", table: list[DecayRule]) -> float | None:
    from verichart.reconcile import concept_key as _concept_key

    fkey = _concept_key(fact)
    for rule in table:
        if rule.concept_key and rule.concept_key == fkey:
            return rule.max_age_days
    for rule in table:
        if rule.concept_key is None and rule.label == fact["label"]:
            return rule.max_age_days
    return None


def is_stale(
    fact: "ClinicalFact", as_of: str, *, table: list[DecayRule] | None = None
) -> bool:
    """No matching rule, or no ``effective_date`` -> ``False`` — never claim staleness a fact
    can't support. ``table=None`` uses ``DEFAULT_DECAY_RULES`` (empty)."""
    table = DEFAULT_DECAY_RULES if table is None else table
    window = decay_window_days(fact, table)
    if window is None or fact["effective_date"] is None:
        return False
    age_days = (
        datetime.fromisoformat(as_of) - datetime.fromisoformat(fact["effective_date"])
    ).days
    return age_days > window
