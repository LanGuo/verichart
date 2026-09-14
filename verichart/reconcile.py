"""Reconciliation (Phase 5): dedup, conflict detection, and policy-driven resolution across
``ClinicalFact``s for one patient drawn from multiple documents or sources.

Design rationale is the same "honest uncertainty beats silent failure" principle as
veritract's quarantine: a ``ConflictSet`` is what a quarantine looks like one level up —
"these N facts disagree, here's how, here's what backs each one" — rather than a value
picked silently or dropped. Detection is deterministic (plain comparison, reproducible,
auditable); resolution is layered — policy rules, then named callables, then an optional
pinned LLM, then human review — never a single opaque model call. Full design notes in
``docs/reconciliation.md``.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable, Protocol, runtime_checkable

from typing_extensions import Literal, TypedDict

if TYPE_CHECKING:
    from verichart.facts import ClinicalFact

ConflictKind = Literal["duplicate", "value_disagreement", "assertion_disagreement"]


class ConflictSet(TypedDict):
    conflict_set_id: str
    concept_key: str
    date_bucket: str | None
    member_fact_ids: list[str]
    kind: ConflictKind


class Resolution(TypedDict):
    conflict_set_id: str
    winning_fact_id: str | None
    method: str
    resolver_id: str | None
    rule_version: str
    rationale: str
    decided_at: str


@runtime_checkable
class ConceptHierarchy(Protocol):
    """Hook for concept subsumption (e.g. "Type 2 diabetes" is-a "diabetes mellitus").

    Inert by default — verichart ships no vocabulary hierarchy data (same rule as Phase 3's
    terminology resolvers). Supply a real implementation over your own SNOMED release to
    make ``group_facts`` join a child code into its ancestor's group.
    """

    def is_descendant(self, code: str, ancestor: str, system: str) -> bool: ...


@runtime_checkable
class LlmResolver(Protocol):
    version: str

    def resolve(
        self, conflict_set: ConflictSet, members: list["ClinicalFact"], context: str | None
    ) -> tuple[str | None, str]: ...


_WS = re.compile(r"\s+")


def _norm(s: str) -> str:
    return _WS.sub(" ", s).strip().lower()


def concept_key(fact: "ClinicalFact") -> str:
    """``"<system>:<code>"`` when Phase 3 resolved the fact, else ``"<label>:<normalized value>"``.

    The fallback means two facts for the same drug that were never terminology-resolved only
    group when their surface text matches exactly (normalized) — the gap Phase 3 closes.
    """
    if fact["concept_code"]:
        return f"{fact['concept_system']}:{fact['concept_code']}"
    return f"{fact['label']}:{_norm(fact['value'])}"


def date_bucket(fact: "ClinicalFact") -> str | None:
    """``effective_date`` truncated to the day, or ``None`` (its own bucket — "unknown date",
    not a wildcard; two facts with no date are assumed the same episode until Phase 6 does
    real temporal extraction)."""
    ed = fact["effective_date"]
    return ed[:10] if ed else None


def rule_versions(policy: "ResolutionPolicy") -> dict[str, str]:
    """``{"reconciliation": policy.rule_version}`` — pass to
    ``veritract.build_manifest(extra={"rule_versions": rule_versions(policy)})`` so a
    point-in-time replay reproduces the same reconciliation decisions."""
    return {"reconciliation": policy.rule_version}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conflict_set_id(concept_key_: str, bucket: str | None, fact_ids: list[str]) -> str:
    key = {"concept_key": concept_key_, "date_bucket": bucket, "members": sorted(fact_ids)}
    import json

    return hashlib.sha256(
        json.dumps(key, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
