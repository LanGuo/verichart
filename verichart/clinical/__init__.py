"""verichart.clinical — open-ended clinical entity extraction (Phase 2).

Public surface is wired up as tasks land. Adapters that need the ``verichart[clinical]``
extra (medspaCy / GLiNER) are imported lazily so ``import verichart.clinical`` works
without the extra installed.
"""

from verichart.clinical.entities import (
    AssertionClassifier,
    AssertionResult,
    EntityMention,
    EntityRecognizer,
    MockAssertionClassifier,
    MockRecognizer,
)

__all__ = [
    "EntityMention",
    "AssertionResult",
    "EntityRecognizer",
    "AssertionClassifier",
    "MockRecognizer",
    "MockAssertionClassifier",
]
