"""verichart — audit-grade clinical facts with fact-level provenance.

The open-source Silver + Reasoning tier for real-world evidence: turns a
``veritract`` extraction into ``ClinicalFact`` records that each carry source
document, exact span, extraction model + config version, confidence, and
(from Phase 5) detected conflicts and their resolution.
"""

from verichart.clinical.entities import extract_entities
from verichart.clinical.terminology import resolve_concepts
from verichart.facts import ClinicalFact, compute_fact_id, make_fact, to_facts

__all__ = [
    "ClinicalFact",
    "to_facts",
    "extract_entities",
    "resolve_concepts",
    "compute_fact_id",
    "make_fact",
]
