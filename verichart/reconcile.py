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


# --------------------------------------------------------------------- group_facts

GroupKey = tuple[str, "str | None"]


def group_facts(
    facts: list["ClinicalFact"], *, hierarchy: ConceptHierarchy | None = None
) -> dict[GroupKey, list["ClinicalFact"]]:
    """Group facts by ``(concept_key, date_bucket)``, exactly — except when ``hierarchy`` is
    given: two same-``date_bucket`` groups whose concept codes are in the same system and one
    ``is_descendant`` of the other are merged into the ancestor's group. A group of size 1 is
    not a conflict; ``detect_conflicts`` ignores it.
    """
    groups: dict[GroupKey, list["ClinicalFact"]] = {}
    for f in facts:
        groups.setdefault((concept_key(f), date_bucket(f)), []).append(f)

    if hierarchy is None:
        return groups

    keys = list(groups)
    merged_away: set[GroupKey] = set()
    for i, ka in enumerate(keys):
        if ka in merged_away:
            continue
        a_fact = groups[ka][0]
        if not a_fact["concept_code"]:
            continue
        for kb in keys[i + 1:]:
            if kb in merged_away or ka[1] != kb[1]:  # same date_bucket required
                continue
            b_fact = groups[kb][0]
            if not b_fact["concept_code"] or a_fact["concept_system"] != b_fact["concept_system"]:
                continue
            system = a_fact["concept_system"]
            if hierarchy.is_descendant(b_fact["concept_code"], a_fact["concept_code"], system):
                groups[ka].extend(groups[kb])
                merged_away.add(kb)
            elif hierarchy.is_descendant(a_fact["concept_code"], b_fact["concept_code"], system):
                groups[kb].extend(groups[ka])
                merged_away.add(ka)
                break  # ka is now folded into kb; move to the next ka
    for k in merged_away:
        del groups[k]
    return groups


# --------------------------------------------------------------------- detect_conflicts

_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")

# assertion_status pairs that cannot both be true of the same concept for one patient
_INCOMPATIBLE_ASSERTIONS = {
    frozenset({"confirmed", "ruled_out"}),
    frozenset({"confirmed", "family_history"}),
    frozenset({"ruled_out", "family_history"}),
}


def _first_number(value: str) -> float | None:
    m = _NUMBER.search(value)
    return float(m.group()) if m else None


def _classify_pair(a: "ClinicalFact", b: "ClinicalFact", numeric_tolerance: float) -> ConflictKind:
    if frozenset({a["assertion_status"], b["assertion_status"]}) in _INCOMPATIBLE_ASSERTIONS:
        return "assertion_disagreement"
    na, nb = _first_number(a["value"]), _first_number(b["value"])
    if na is not None and nb is not None:
        return "duplicate" if abs(na - nb) <= numeric_tolerance else "value_disagreement"
    if _norm(a["value"]) == _norm(b["value"]):
        return "duplicate"
    return "value_disagreement"


_KIND_RANK = {"duplicate": 0, "value_disagreement": 1, "assertion_disagreement": 2}


def _classify_group(members: list["ClinicalFact"], numeric_tolerance: float) -> ConflictKind:
    worst: ConflictKind = "duplicate"
    for i in range(len(members)):
        for j in range(i + 1, len(members)):
            kind = _classify_pair(members[i], members[j], numeric_tolerance)
            if _KIND_RANK[kind] > _KIND_RANK[worst]:
                worst = kind
    return worst


def detect_conflicts(
    groups: dict[GroupKey, list["ClinicalFact"]], *, numeric_tolerance: float = 0.0
) -> list[ConflictSet]:
    """One ``ConflictSet`` per group of size >= 2, deterministic and reproducible."""
    out: list[ConflictSet] = []
    for (ck, bucket), members in groups.items():
        if len(members) < 2:
            continue
        fact_ids = [m["fact_id"] for m in members]
        out.append(ConflictSet(
            conflict_set_id=_conflict_set_id(ck, bucket, fact_ids),
            concept_key=ck, date_bucket=bucket,
            member_fact_ids=fact_ids,
            kind=_classify_group(members, numeric_tolerance),
        ))
    return out


# --------------------------------------------------------------------- ResolutionPolicy

_BUILTIN_METHODS = {"highest_confidence", "most_recent", "source_rank"}


class ResolutionPolicy:
    """Routes a ``ConflictSet`` to a resolution method, cheapest first:
    ``route_to_review_when`` -> ``by_kind`` -> ``by_concept_system`` -> ``default``.

    ``named_rules`` maps a method name to ``callable(conflict_set, members) -> fact_id | None``
    — arbitrary domain logic, still deterministic and inspectable. ``llm_resolver`` (an
    ``LlmResolver``) is asked when a route resolves to ``"llm_assisted"``. ``rule_version``
    feeds ``rule_versions()`` for the pipeline manifest — bump it whenever the policy changes.
    """

    def __init__(
        self,
        *,
        default: str = "highest_confidence",
        by_kind: dict[str, str] | None = None,
        by_concept_system: dict[str, str] | None = None,
        source_rank: list[str] | None = None,
        named_rules: dict[str, Callable[[ConflictSet, list], str | None]] | None = None,
        llm_resolver: LlmResolver | None = None,
        route_to_review_when: Callable[[ConflictSet], bool] | None = None,
        numeric_tolerance: float = 0.0,
        rule_version: str = "v1",
    ):
        self.default = default
        self.by_kind = dict(by_kind) if by_kind else {}
        self.by_concept_system = dict(by_concept_system) if by_concept_system else {}
        self.source_rank = list(source_rank) if source_rank else []
        self.named_rules = dict(named_rules) if named_rules else {}
        self.llm_resolver = llm_resolver
        self.route_to_review_when = route_to_review_when
        self.numeric_tolerance = numeric_tolerance
        self.rule_version = rule_version

    def method_for(self, cs: ConflictSet) -> str:
        if self.route_to_review_when is not None and self.route_to_review_when(cs):
            return "human_review"
        if cs["kind"] in self.by_kind:
            return self.by_kind[cs["kind"]]
        system = cs["concept_key"].split(":", 1)[0]
        if system in self.by_concept_system:
            return self.by_concept_system[system]
        return self.default


# --------------------------------------------------------------------- built-in pickers


def _pick_highest_confidence(members: list["ClinicalFact"]) -> "ClinicalFact":
    top = max(f["extraction_confidence"] for f in members)
    tied = [f for f in members if f["extraction_confidence"] == top]
    return min(tied, key=lambda f: f["fact_id"])


def _pick_most_recent(members: list["ClinicalFact"]) -> "ClinicalFact":
    def sort_key(f):
        return f["effective_date"] or f["created_at"]

    top = max(sort_key(f) for f in members)
    tied = [f for f in members if sort_key(f) == top]
    return min(tied, key=lambda f: f["fact_id"])


def _pick_by_source_rank(members: list["ClinicalFact"], source_rank: list[str]) -> "ClinicalFact":
    def rank(f):
        source_type = f["span"]["source_type"] if f["span"] else None
        try:
            return source_rank.index(source_type)
        except ValueError:
            return len(source_rank)  # unranked source loses to any ranked one

    best = min(rank(f) for f in members)
    tied = [f for f in members if rank(f) == best]
    return min(tied, key=lambda f: f["fact_id"])


def resolve_conflict_set(
    cs: ConflictSet,
    members: list["ClinicalFact"],
    policy: ResolutionPolicy,
    *,
    context: str | None = None,
    created_at: str | None = None,
) -> Resolution:
    """Dispatch ``cs`` through ``policy`` and return the ``Resolution`` (does not mutate
    ``members`` or apply the decision to any fact — ``reconcile()`` does that)."""
    method = policy.method_for(cs)
    stamp = created_at if created_at is not None else _now_iso()

    if method == "human_review":
        return Resolution(
            conflict_set_id=cs["conflict_set_id"], winning_fact_id=None,
            method="human_review", resolver_id=None, rule_version=policy.rule_version,
            rationale="routed to human review", decided_at=stamp,
        )

    if method == "highest_confidence":
        winner = _pick_highest_confidence(members)
        rationale = f"highest extraction_confidence ({winner['extraction_confidence']:.2f})"
        resolver_id = None
    elif method == "most_recent":
        winner = _pick_most_recent(members)
        rationale = f"most recent date ({winner['effective_date'] or winner['created_at']})"
        resolver_id = None
    elif method == "source_rank":
        winner = _pick_by_source_rank(members, policy.source_rank)
        st = winner["span"]["source_type"] if winner["span"] else None
        rationale = f"highest-ranked source ({st!r} in {policy.source_rank})"
        resolver_id = None
    elif method == "llm_assisted":
        if policy.llm_resolver is None:
            raise ValueError("method 'llm_assisted' requires policy.llm_resolver")
        winning_fact_id, rationale = policy.llm_resolver.resolve(cs, members, context)
        return Resolution(
            conflict_set_id=cs["conflict_set_id"], winning_fact_id=winning_fact_id,
            method="llm_assisted", resolver_id=policy.llm_resolver.version,
            rule_version=policy.rule_version, rationale=rationale, decided_at=stamp,
        )
    elif method in policy.named_rules:
        winning_fact_id = policy.named_rules[method](cs, members)
        return Resolution(
            conflict_set_id=cs["conflict_set_id"], winning_fact_id=winning_fact_id,
            method="named_rule", resolver_id=method, rule_version=policy.rule_version,
            rationale=f"named rule {method!r}", decided_at=stamp,
        )
    else:
        raise ValueError(
            f"unknown resolution method {method!r} — not a built-in "
            f"({sorted(_BUILTIN_METHODS)}), not in policy.named_rules, not 'llm_assisted'/'human_review'"
        )

    return Resolution(
        conflict_set_id=cs["conflict_set_id"], winning_fact_id=winner["fact_id"],
        method=method, resolver_id=resolver_id, rule_version=policy.rule_version,
        rationale=rationale, decided_at=stamp,
    )
