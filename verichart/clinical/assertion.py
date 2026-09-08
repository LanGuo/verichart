"""Assertion classification: map ConText-style flags to a ClinicalFact assertion_status.

The medspaCy adapter (Task 5) lives here too. This module is import-safe without the
``verichart[clinical]`` extra — ``MedspacyContextClassifier`` imports ``medspacy`` lazily
in ``__init__`` and raises a clear error if it is missing.
"""

from __future__ import annotations

from verichart.clinical.entities import AssertionResult
from verichart.facts import AssertionStatus

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
