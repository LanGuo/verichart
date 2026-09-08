"""The ClinicalFact record and the pure projection from a veritract ExtractionResult.

A ``ClinicalFact`` carries the six attribute categories the FDA's December 2025 RWE
guidance implies as per-fact properties: identity, clinical content, provenance,
reconciliation, versioning (plus a free-form ``note``). Phase 1 populates the identity,
provenance, and versioning fields; later phases fill the rest:

- ``concept_code`` / ``concept_system`` / ``concept_display`` / ``terminology_version`` — Phase 3
- ``assertion_status`` beyond ``"unknown"`` — Phase 2
- ``value_normalized`` — Phase 4 / 6
- ``conflict_set_id`` / ``resolution_method`` / ``resolver_id`` / ``rule_version`` — Phase 5

verichart does not redefine provenance: it imports ``Span`` from veritract and reuses its
``provenance_type`` values, adding only ``"unverified"`` at the fact level for the quarantine
case (a fact with no supporting span).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from typing_extensions import Literal, TypedDict

from veritract import Span

if TYPE_CHECKING:
    from veritract import ExtractionResult, PipelineManifest

AssertionStatus = Literal[
    "confirmed",
    "ruled_out",
    "family_history",
    "patient_reported",
    "historical",
    "hypothetical",
    "uncertain",
    "unknown",
]

ProvenanceType = Literal["direct", "paraphrased", "inferred", "unverified"]

ResolutionMethod = Literal[
    "none",
    "highest_confidence",
    "most_recent",
    "source_rank",
    "human_review",
    "named_rule",
    "llm_assisted",
]


class ClinicalFact(TypedDict):
    # --- identity ---
    fact_id: str
    patient_pseudonym: str | None
    label: str  # schema field name (Phase 1) / entity label (Phase 2)

    # --- clinical content ---
    concept_code: str | None
    concept_system: str | None
    concept_display: str | None
    value: str
    value_normalized: str | None
    effective_date: str | None
    assertion_status: AssertionStatus

    # --- provenance ---
    provenance_type: ProvenanceType
    span: Span | None
    supporting_spans: list[Span]
    extraction_model: str | None
    extraction_model_digest: str | None
    extraction_confidence: float  # 0-1

    # --- reconciliation (Phase 5) ---
    conflict_set_id: str | None
    resolution_method: ResolutionMethod
    resolver_id: str | None
    rule_version: str | None

    # --- versioning ---
    manifest_id: str | None
    terminology_version: str | None

    # --- annotation ---
    note: str | None

    created_at: str


def compute_fact_id(
    *,
    label: str,
    value: str,
    concept_code: str | None,
    concept_system: str | None,
    span: Span | None,
    manifest_id: str | None,
) -> str:
    """Deterministic content hash identifying a clinical fact.

    Keyed on concept, value, source location, and the pipeline manifest — so two
    facts with the same concept/value at the same span under the same manifest
    collapse to one id (the key Phase 5 dedup builds on). Excludes ``created_at``
    and confidence.
    """
    key = {
        "label": label,
        "value": value,
        "concept_code": concept_code,
        "concept_system": concept_system,
        "doc_id": span["doc_id"] if span else None,
        "char_start": span["char_start"] if span else None,
        "char_end": span["char_end"] if span else None,
        "manifest_id": manifest_id,
    }
    return hashlib.sha256(
        json.dumps(key, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_facts(
    result: "ExtractionResult",
    *,
    patient_pseudonym: str | None = None,
    effective_date: str | None = None,
    manifest: "PipelineManifest | None" = None,
    created_at: str | None = None,
) -> list[ClinicalFact]:
    """Project a veritract ``ExtractionResult`` into ``ClinicalFact`` records.

    Pure function — no LLM call, no I/O, does not mutate ``result``.

    Grounded fields become facts with their ``Span`` and its ``provenance_type``
    (``"inferred"`` when the field grounded without a locatable span). Quarantined
    fields are *not* dropped: they become facts with ``provenance_type="unverified"``,
    ``span=None``, ``extraction_confidence=0.0``, and the quarantine reason in ``note``.

    ``manifest`` (a veritract ``PipelineManifest``) supplies ``manifest_id`` and the
    extraction model tag/digest; without it, ``manifest_id`` falls back to
    ``result.manifest_id`` and the model fields are ``None``.

    Note: for a result produced with ``mode="no-grounding"`` every field lands in
    ``result.extracted`` with no span, so every fact becomes ``"inferred"`` — which
    overstates confidence. Use ``to_facts`` on ``mode="full"`` (or ``"fuzzy"``) results.
    """
    manifest_id = manifest["manifest_id"] if manifest else result.manifest_id
    model_tag = manifest["model_tag"] if manifest else None
    model_digest = manifest["model_digest"] if manifest else None
    stamp = created_at if created_at is not None else _now_iso()

    facts: list[ClinicalFact] = []

    for label, gf in result.extracted.items():
        if not gf["value"].strip():
            continue  # a fact with no value is not a fact
        span = gf["span"]
        provenance_type: ProvenanceType = span["provenance_type"] if span else "inferred"
        facts.append(make_fact(
            label=label,
            value=gf["value"],
            span=span,
            provenance_type=provenance_type,
            confidence=_clamp01(gf["confidence"] / 100.0),
            note=None,
            patient_pseudonym=patient_pseudonym,
            effective_date=effective_date,
            manifest_id=manifest_id,
            model_tag=model_tag,
            model_digest=model_digest,
            created_at=stamp,
        ))

    for qf in result.quarantined:
        if not qf["value"].strip():
            continue
        facts.append(make_fact(
            label=qf["field_name"],
            value=qf["value"],
            span=None,
            provenance_type="unverified",
            confidence=0.0,
            note=qf["reason"],
            patient_pseudonym=patient_pseudonym,
            effective_date=effective_date,
            manifest_id=manifest_id,
            model_tag=model_tag,
            model_digest=model_digest,
            created_at=stamp,
        ))

    return facts


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def make_fact(
    *,
    label: str,
    value: str,
    span: Span | None,
    provenance_type: ProvenanceType,
    confidence: float,
    note: str | None,
    patient_pseudonym: str | None,
    effective_date: str | None,
    manifest_id: str | None,
    model_tag: str | None,
    model_digest: str | None,
    created_at: str,
    assertion_status: AssertionStatus = "unknown",
) -> ClinicalFact:
    """Build one ClinicalFact with Phase-1 defaults for the fields later phases own.

    Shared by ``to_facts`` (schema extraction) and
    ``verichart.clinical.extract_entities`` (open-ended NER) so the record shape has
    exactly one definition. ``concept_*`` and reconciliation fields are always the
    Phase-1 defaults here; Phase 3 / Phase 5 populate them downstream.
    """
    return ClinicalFact(
        fact_id=compute_fact_id(
            label=label, value=value, concept_code=None, concept_system=None,
            span=span, manifest_id=manifest_id,
        ),
        patient_pseudonym=patient_pseudonym,
        label=label,
        concept_code=None,
        concept_system=None,
        concept_display=None,
        value=value,
        value_normalized=None,
        effective_date=effective_date,
        assertion_status=assertion_status,
        provenance_type=provenance_type,
        span=span,
        supporting_spans=[span] if span else [],
        extraction_model=model_tag,
        extraction_model_digest=model_digest,
        extraction_confidence=confidence,
        conflict_set_id=None,
        resolution_method="none",
        resolver_id=None,
        rule_version=None,
        manifest_id=manifest_id,
        terminology_version=None,
        note=note,
        created_at=created_at,
    )
