"""verichart.clinical — open-ended clinical entity extraction (Phase 2).

``recognize -> classify -> ground -> project`` to ``ClinicalFact``:

    from verichart.clinical import GlinerBiomedRecognizer, MedspacyContextClassifier
    from verichart.clinical import extract_entities, DEFAULT_GLINER_LABELS

    rec = GlinerBiomedRecognizer()                 # needs verichart[clinical]
    clf = MedspacyContextClassifier()
    facts = extract_entities(note_text, recognizer=rec,
                             labels=list(DEFAULT_GLINER_LABELS.values()),
                             assertion_classifier=clf, doc_id="note:1")

Adapter classes import their backend (medspaCy / gliner) lazily on construction, so
this module imports fine without the extra installed.
"""

from verichart.clinical.assertion import MedspacyContextClassifier, derive_assertion_status
from verichart.clinical.entities import (
    CANONICAL_LABELS,
    DEFAULT_GLINER_LABELS,
    AssertionClassifier,
    AssertionResult,
    EntityMention,
    EntityRecognizer,
    MockAssertionClassifier,
    MockRecognizer,
    extract_entities,
    normalize_label,
)
from verichart.clinical.ner import GlinerBiomedRecognizer, MedspacyRuleRecognizer
from verichart.clinical.terminology import (
    DEFAULT_ROUTING,
    ConceptMatch,
    ConceptResolver,
    MockResolver,
    ScispacyResolver,
    SqliteLookupResolver,
    load_vocab_sqlite,
    resolve_concepts,
    terminology_versions,
)

__all__ = [
    # Phase 2 — entities
    "extract_entities",
    "derive_assertion_status",
    "EntityMention",
    "AssertionResult",
    "EntityRecognizer",
    "AssertionClassifier",
    "MockRecognizer",
    "MockAssertionClassifier",
    "GlinerBiomedRecognizer",
    "MedspacyRuleRecognizer",
    "MedspacyContextClassifier",
    "CANONICAL_LABELS",
    "DEFAULT_GLINER_LABELS",
    "normalize_label",
    # Phase 3 — terminology
    "resolve_concepts",
    "terminology_versions",
    "ConceptResolver",
    "ConceptMatch",
    "DEFAULT_ROUTING",
    "MockResolver",
    "SqliteLookupResolver",
    "ScispacyResolver",
    "load_vocab_sqlite",
]
