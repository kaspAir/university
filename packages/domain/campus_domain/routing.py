"""Trusted-core routing rules (architecture chapters 9–11).

The router (an LLM or a deterministic rule) *proposes*. This module decides whether the proposal
may be executed: hard filters, evidence existence, canonical model separation for review.
It never judges whether a scientific statement is true, and it never calls a provider.
"""
from __future__ import annotations

from dataclasses import dataclass

from campus_contracts.models import (
    CandidateAssessment,
    Deployment,
    ModelCatalog,
    RouteProposal,
    RoutingDecision,
    TaskNeed,
)
from campus_contracts.policy import RoutingPolicy

from .errors import CampusError, ErrorCode

# Exclusion reason codes. They are data for the registry view, not prose.
R_NOT_ENABLED = "NOT_ENABLED"
R_HEALTH = "HEALTH_"
R_IDENTITY_UNKNOWN = "CANONICAL_IDENTITY_UNKNOWN"
R_DATA_LABEL = "DATA_LABEL_NOT_ALLOWED"
R_TOOL_SCOPE = "TOOL_SCOPE_NOT_ALLOWED"
R_CAP_UNKNOWN = "CAPABILITY_UNKNOWN:"
R_CAP_UNSUPPORTED = "CAPABILITY_UNSUPPORTED:"
R_CONTEXT = "CONTEXT_TOO_SMALL"
R_OUTPUT = "OUTPUT_TOO_SMALL"
R_QUALIFICATION = "QUALIFICATION_MISSING"
R_PRICING = "PRICING_UNKNOWN"
R_CONTRIBUTOR = "SUBSTANTIVE_CONTRIBUTOR"
R_SHARED_BASE = "SHARED_BASE_WITH_PRODUCER"
R_SAME_CANONICAL = "SAME_CANONICAL_MODEL_AS_PRODUCER"


R_EXCLUDED = "EXCLUDED:"  # operational exclusion, e.g. FAILED_IN_ATTEMPT:<run id> after a provider failure (chapter 10)


def exclusion_reasons(
    task: TaskNeed,
    dep: Deployment,
    policy: RoutingPolicy,
    excluded: dict[str, str] | None = None,
    qualified_deployments: set[str] | None = None,
) -> list[str]:
    """All hard-filter violations of one deployment for one task. Empty list means eligible."""
    reasons: list[str] = []
    if excluded and dep.id in excluded:
        reasons.append(R_EXCLUDED + excluded[dep.id])
    if policy.hard_rules.require_enabled and not dep.enabled:
        reasons.append(R_NOT_ENABLED)
    if dep.health != "available":
        reasons.append(R_HEALTH + dep.health.upper())
    if dep.canonical_model_id is None:
        reasons.append(R_IDENTITY_UNKNOWN)
    if policy.hard_rules.require_data_authorization and not set(task.data_labels) <= set(dep.allowed_data_labels):
        reasons.append(R_DATA_LABEL)
    if not set(task.required_tool_scopes) <= set(dep.allowed_tool_scopes):
        reasons.append(R_TOOL_SCOPE)
    caps = dep.capabilities
    for feature in task.required_features:
        status = getattr(caps, feature)
        if status == "unknown":
            reasons.append(R_CAP_UNKNOWN + feature)  # unknown is excluded, never assumed (A08)
        elif status == "unsupported":
            reasons.append(R_CAP_UNSUPPORTED + feature)
    needed_context = task.context_tokens_estimate + task.max_output_tokens
    if caps.context_tokens is None or caps.context_tokens < needed_context:
        reasons.append(R_CONTEXT)
    if caps.output_tokens is None or caps.output_tokens < task.max_output_tokens:
        reasons.append(R_OUTPUT)
    if policy.hard_rules.qualified_combinations_required and task.qualification_mode == "qualified":
        # With a register view supplied, qualification means a passed record for this combination; otherwise the catalog's declaration.
        qualified = (dep.id in qualified_deployments) if qualified_deployments is not None else bool(dep.qualification_refs)
        if not qualified:
            reasons.append(R_QUALIFICATION)
    if dep.pricing_ref is None:
        reasons.append(R_PRICING)
    if task.role in {"reviewer", "replicator"} and dep.canonical_model_id in task.substantive_contributor_model_ids:
        reasons.append(R_CONTRIBUTOR)
    return reasons


def assess_candidates(
    task: TaskNeed,
    catalog: ModelCatalog,
    policy: RoutingPolicy,
    excluded: dict[str, str] | None = None,
    qualified_deployments: set[str] | None = None,
) -> list[CandidateAssessment]:
    assessments = []
    for dep in catalog.deployments:
        reasons = exclusion_reasons(task, dep, policy, excluded, qualified_deployments)
        assessments.append(
            CandidateAssessment(
                deployment_id=dep.id,
                eligible=not reasons,
                exclusion_reasons=reasons,
                evidence_refs=list(dep.qualification_refs) if not reasons else [],
                note="Zulässig nach harten Regeln." if not reasons else "Ausgeschlossen: " + ", ".join(reasons),
            )
        )
    return assessments


def review_required(task: TaskNeed, policy: RoutingPolicy) -> bool:
    """Substantive generation needs a distinct-model review (Art. 27). Selectors and reviewers do not."""
    return policy.hard_rules.substantive_review_different_canonical_model and task.role == "producer"


def select_reviewer(
    task: TaskNeed,
    selected: Deployment,
    catalog: ModelCatalog,
    policy: RoutingPolicy,
    excluded: dict[str, str] | None = None,
    qualified_deployments: set[str] | None = None,
) -> Deployment:
    """Pick a review deployment that is canonically distinct from the producer and all prior contributors.

    Deterministic: candidates are ordered by (distinct provider first if preferred, deployment id).
    """
    review_task = task.model_copy(
        update={
            "role": "reviewer",
            "substantive_contributor_model_ids": sorted(
                set(task.substantive_contributor_model_ids) | {selected.canonical_model_id or ""}
            ),
        }
    )
    candidates: list[tuple[int, str, Deployment]] = []
    for dep in catalog.deployments:
        if dep.id == selected.id:
            continue
        reasons = exclusion_reasons(review_task, dep, policy, excluded, qualified_deployments)
        if dep.canonical_model_id == selected.canonical_model_id:
            reasons.append(R_SAME_CANONICAL)  # aliases are not diversity (A03)
        if dep.shared_base_id and dep.shared_base_id == selected.shared_base_id:
            reasons.append(R_SHARED_BASE)
        if reasons:
            continue
        provider_rank = 0 if (policy.hard_rules.prefer_distinct_provider and dep.provider_id != selected.provider_id) else 1
        candidates.append((provider_rank, dep.id, dep))
    if not candidates:
        raise CampusError(
            ErrorCode.REVIEW_DIVERSITY_UNSATISFIED,
            "Kein kanonisch verschiedenes, zulässiges Prüfmodell verfügbar; Prüfung bleibt offen",
            blocked_action="route.authorize",
            required_resolution="Zweites qualifiziertes Modell mit anderer kanonischer Identität zulassen",
            policy_version=policy.revision,
        )
    candidates.sort(key=lambda c: (c[0], c[1]))
    return candidates[0][2]


@dataclass(frozen=True)
class ValidatedRoute:
    selected: Deployment
    reviewer: Deployment | None
    assessments: list[CandidateAssessment]
    proposal: RouteProposal
    required_review: bool


def validate_proposal(
    task: TaskNeed,
    proposal: RouteProposal,
    catalog: ModelCatalog,
    policy: RoutingPolicy,
    excluded: dict[str, str] | None = None,
    qualified_deployments: set[str] | None = None,
) -> ValidatedRoute:
    """Technical admission of a proposal. Plausible language replaces no permission (chapter 9, step 5)."""
    if policy.activation_state != "active":
        raise CampusError(ErrorCode.POLICY_NOT_ACTIVE, "Routing-Policy ist nicht aktiv", policy_version=policy.revision)
    if proposal.task_id != task.task_id:
        raise CampusError(ErrorCode.PROPOSAL_INVALID, "Vorschlag gehört zu einer anderen Aufgabe", policy_version=policy.revision)
    assessments = assess_candidates(task, catalog, policy, excluded, qualified_deployments)
    by_id = {a.deployment_id: a for a in assessments}
    selected_assessment = by_id.get(proposal.selected_deployment_id)
    if selected_assessment is None:
        raise CampusError(
            ErrorCode.PROPOSAL_INVALID,
            "Gewähltes Deployment existiert im aktiven Katalog nicht",
            blocked_action="route.authorize",
            policy_version=policy.revision,
            details={"assessments": [a.model_dump() for a in assessments]},
        )
    if not selected_assessment.eligible:
        raise CampusError(
            ErrorCode.NO_ELIGIBLE_MODEL if not any(a.eligible for a in assessments) else ErrorCode.PROPOSAL_INVALID,
            "Gewähltes Deployment ist nach harten Regeln nicht zulässig: "
            + ", ".join(selected_assessment.exclusion_reasons),
            blocked_action="route.authorize",
            required_resolution="Zulässigen Kandidaten wählen oder Katalog/Qualifikation ändern",
            policy_version=policy.revision,
            details={"assessments": [a.model_dump() for a in assessments]},
        )
    selected = catalog.deployment(proposal.selected_deployment_id)
    assert selected is not None
    unknown_evidence = set(proposal.evidence_refs) - set(selected.qualification_refs)
    if unknown_evidence:
        raise CampusError(
            ErrorCode.PROPOSAL_INVALID,
            "Vorschlag nennt Nachweise, die im Katalog nicht existieren: " + ", ".join(sorted(unknown_evidence)),
            blocked_action="route.authorize",
            policy_version=policy.revision,
            details={"assessments": [a.model_dump() for a in assessments]},
        )
    for alt in proposal.alternatives:
        if alt.deployment_id not in by_id:
            raise CampusError(
                ErrorCode.PROPOSAL_INVALID,
                f"Alternative {alt.deployment_id} existiert im Katalog nicht",
                blocked_action="route.authorize",
                policy_version=policy.revision,
            )
    needs_review = review_required(task, policy)
    reviewer = select_reviewer(task, selected, catalog, policy, excluded, qualified_deployments) if needs_review else None
    return ValidatedRoute(selected, reviewer, assessments, proposal, needs_review)


def deterministic_proposal(
    task: TaskNeed,
    catalog: ModelCatalog,
    policy: RoutingPolicy,
    excluded: dict[str, str] | None = None,
    qualified_deployments: set[str] | None = None,
) -> RouteProposal:
    """Pre-approved deterministic rule (chapter 10). Only when the policy explicitly allows one."""
    if policy.deterministic_fallback_rule is None:
        raise CampusError(
            ErrorCode.NO_ELIGIBLE_SELECTOR,
            "Kein Selector zulässig und keine deterministische Auswahlregel in der Policy erlaubt; Aufgabe wartet",
            blocked_action="route.propose",
            required_resolution="Selector-Pool konfigurieren oder deterministic_fallback_rule setzen",
            policy_version=policy.revision,
        )
    assessments = assess_candidates(task, catalog, policy, excluded, qualified_deployments)
    eligible = sorted((a for a in assessments if a.eligible), key=lambda a: a.deployment_id)
    if not eligible:
        raise CampusError(
            ErrorCode.NO_ELIGIBLE_MODEL,
            "Kein Deployment erfüllt die harten Regeln",
            blocked_action="route.propose",
            policy_version=policy.revision,
            details={"assessments": [a.model_dump() for a in assessments]},
        )
    chosen = eligible[0]
    return RouteProposal(
        task_id=task.task_id,
        selected_deployment_id=chosen.deployment_id,
        rationale="Deterministische Regel first_eligible: erster zulässiger Kandidat in Katalogreihenfolge. "
        "Keine fachliche Eignungsbewertung.",
        evidence_refs=list(chosen.evidence_refs),
        alternatives=[
            {"deployment_id": a.deployment_id, "reason_not_selected": "Regel first_eligible: später in der Reihenfolge"}
            for a in eligible[1:]
        ],
        uncertainties=["Regelbasierte Wahl ohne Modellurteil"],
    )


def build_decision(
    route: ValidatedRoute,
    *,
    decision_id: str,
    catalog_revision: str,
    routing_policy_ref: str,
    selection_mode: str,
    selector_model_ref: str | None,
    reservation_ref: str,
    pricing_snapshot_ref: str,
    created_at: str,
) -> RoutingDecision:
    """The only place that may produce state=authorized (contracts/README)."""
    assert route.selected.canonical_model_id is not None
    return RoutingDecision(
        schema_version="1.0",
        id=decision_id,
        task_id=route.proposal.task_id,
        catalog_revision=catalog_revision,
        routing_policy_ref=routing_policy_ref,
        selection_mode=selection_mode,  # type: ignore[arg-type]
        selector_model_ref=selector_model_ref,
        selected_deployment_id=route.selected.id,
        selected_canonical_model_id=route.selected.canonical_model_id,
        candidate_assessments=route.assessments,
        rationale=route.proposal.rationale,
        required_review=route.required_review,
        review_deployment_id=route.reviewer.id if route.reviewer else None,
        reservation_ref=reservation_ref,
        pricing_snapshot_ref=pricing_snapshot_ref,
        created_at=created_at,
        state="authorized",
    )
