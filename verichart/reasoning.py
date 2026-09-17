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
