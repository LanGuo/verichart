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

from typing_extensions import Literal, TypedDict

from veritract import Span

AssertionStatus = Literal[
    "confirmed",
    "ruled_out",
    "family_history",
    "patient_reported",
    "historical",
    "hypothetical",
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
