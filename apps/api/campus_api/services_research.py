"""Projects, tasks, registry, route authorization (selector + core), runs and distinct-model review.

Chapters 6, 7, 9–11, 16, 17–18 of the architecture. S1: real adapters behind the gateway, LLM selector with core
validation, replanning after provider failure, review by a canonically different model.
"""
from __future__ import annotations

import hashlib
import json

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from campus_contracts.models import AgentProfile, ModelCatalog, ReviewReport, RouteProposal, RoutingDecision, SkillManifest, TaskNeed
from campus_contracts.policy import RoutingPolicy
from campus_contracts.validation import provider_schema, validate_document
from campus_domain import routing
from campus_domain.authz import authorize
from campus_domain.budget import Pricing, estimate_cost
from campus_domain.errors import CampusError, ErrorCode
from campus_domain.guards import RunStartContext, check_run_start
from campus_domain.money import Money, parse_money
from campus_domain.statuses import ActivationState, MandateState, OperatingStatus, QualificationStatus, RunEndReason, RunState
from campus_gateway.adapters import build_adapters
from campus_gateway.gateway import Gateway
from campus_gateway.port import LLMRequest, LLMResponse
from campus_infra import budget_service
from campus_infra.audit import Actor, record
from campus_infra.ids import new_id, utcnow
from campus_infra.journal import DbInvocationJournal
from campus_infra.models import (
    AgentProfileVersion,
    ArtifactVersion,
    BudgetAccount,
    FindingRecord,
    Institute,
    MandateVersion,
    OutboxEvent,
    PricingVersion,
    Project,
    Reservation,
    ReviewReportRecord,
    RouteProposalRecord,
    RoutingDecisionRecord,
    RunAttempt,
    SkillVersion,
    Task,
    Workspace,
)

from .services_governance import active_catalog, active_policy

SELECTOR_TASK_CLASS = "route_selection"


def _own(db: Session, model, object_id: str, actor: Actor, what: str):
    row = db.get(model, object_id)
    if row is None or row.workspace_id != actor.workspace_id:
        raise CampusError(ErrorCode.NOT_FOUND, f"{what} nicht gefunden")
    return row


def _gateway(db: Session, workspace_id: str) -> Gateway:
    return Gateway(build_adapters(), DbInvocationJournal(db, workspace_id))


# ---------------------------------------------------------------- projects & tasks

def create_project(db: Session, *, actor: Actor, mandate_version_id: str, institute_id: str, title: str, budget: str,
                   correlation_id: str) -> Project:
    authorize("project.create", actor.roles)
    mandate = _own(db, MandateVersion, mandate_version_id, actor, "Mandatsfassung")
    if mandate.state != MandateState.APPROVED.value:
        raise CampusError(ErrorCode.MANDATE_NOT_APPROVED, "Projekt braucht ein freigegebenes Mandat", blocked_action="project.create")
    institute = _own(db, Institute, institute_id, actor, "Institut")
    root = db.execute(select(BudgetAccount).where(BudgetAccount.mandate_version_id == mandate.id,
                                                  BudgetAccount.parent_id.is_(None))).scalar_one()
    account = budget_service.create_child(db, parent_id=root.id, name=f"Projekt: {title}", limit=parse_money(budget, root.currency))
    project = Project(id=new_id("P"), workspace_id=actor.workspace_id, mandate_version_id=mandate.id, institute_id=institute.id,
                      budget_account_id=account.id, title=title)
    db.add(project)
    db.flush()
    record(db, actor=actor, command="project.create", object_type="project", object_id=project.id, object_version=None,
           correlation_id=correlation_id, project_id=project.id, payload={"budget_account_id": account.id})
    return project


def create_task(db: Session, *, actor: Actor, project_id: str, task_need: dict, budget: str | None, parent_task_id: str | None,
                correlation_id: str) -> Task:
    authorize("task.create", actor.roles)
    project = _own(db, Project, project_id, actor, "Projekt")
    task_id = new_id("T")
    document = dict(task_need)
    document.update({"task_id": task_id, "workspace_id": actor.workspace_id, "project_id": project.id,
                     "mandate_version_ref": project.mandate_version_id})
    parent_account_id = project.budget_account_id
    if parent_task_id:
        parent = _own(db, Task, parent_task_id, actor, "Elternaufgabe")
        parent_account_id = parent.budget_account_id
        policy = active_policy(db, actor.workspace_id)
        depth_limit = RoutingPolicy.model_validate(policy.document).limits.max_delegation_depth if policy else None
        depth = _task_depth(db, parent) + 1
        if depth_limit is not None and depth > depth_limit:
            raise CampusError(ErrorCode.FORBIDDEN, f"Delegationstiefe {depth} überschreitet das Limit {depth_limit}",
                              blocked_action="task.create", required_resolution="Aufgabe flacher planen (Art. 10)")
    if budget is not None:
        parent_account = db.get(BudgetAccount, parent_account_id)
        account = budget_service.create_child(db, parent_id=parent_account_id, name=f"Task {task_id}",
                                              limit=parse_money(budget, parent_account.currency))
        account_id = account.id
    else:
        account_id = parent_account_id
    document["budget_account_id"] = account_id
    violations = validate_document("task_need", document)
    if violations:
        raise CampusError(ErrorCode.VALIDATION_ERROR, "TaskNeed verletzt das Schema", blocked_action="task.create",
                          details={"violations": violations})
    need = TaskNeed.model_validate(document)
    task = Task(id=task_id, workspace_id=actor.workspace_id, project_id=project.id, parent_task_id=parent_task_id,
                budget_account_id=account_id, task_need=need.model_dump())
    db.add(task)
    db.flush()
    record(db, actor=actor, command="task.create", object_type="task", object_id=task.id, object_version="1",
           correlation_id=correlation_id, project_id=project.id)
    return task


def _task_depth(db: Session, task: Task) -> int:
    depth = 0
    current = task
    while current.parent_task_id:
        depth += 1
        current = db.get(Task, current.parent_task_id)
    return depth


# ---------------------------------------------------------------- registry

def register_agent(db: Session, *, actor: Actor, document: dict, correlation_id: str) -> AgentProfileVersion:
    """A candidate profile. Registration is not qualification (Art. 26): statuses stay as submitted, never upgraded."""
    authorize("agent.register", actor.roles)
    violations = validate_document("agent_profile", document)
    if violations:
        raise CampusError(ErrorCode.VALIDATION_ERROR, "Agentenprofil verletzt das Schema", blocked_action="agent.register",
                          details={"violations": violations})
    profile = AgentProfile.model_validate(document)
    if profile.qualification_status == "task_qualified" and not profile.qualification_refs:
        raise CampusError(ErrorCode.QUALIFICATION_MISSING, "task_qualified ohne qualification_refs ist ein Selbstzertifikat (T12)",
                          blocked_action="agent.register")
    if profile.operating_status == "active" and profile.qualification_status == "not_evaluated":
        raise CampusError(ErrorCode.QUALIFICATION_MISSING, "Ungeprüfte Profile werden nicht als aktiv registriert",
                          blocked_action="agent.register")
    ref = f"{profile.id}@{profile.version}"
    if find_agent(db, actor.workspace_id, ref) is not None:
        raise CampusError(ErrorCode.VERSION_CONFLICT, "Diese Profilfassung existiert bereits; neue Fassung nötig")
    row = AgentProfileVersion(id=new_id("AP"), ref=ref, workspace_id=actor.workspace_id, profile_id=profile.id,
                              version=profile.version, document=profile.model_dump(), operating_status=profile.operating_status,
                              qualification_status=profile.qualification_status, created_by=actor.actor_id)
    db.add(row)
    db.flush()
    record(db, actor=actor, command="agent.register", object_type="agent_profile", object_id=profile.id,
           object_version=profile.version, correlation_id=correlation_id,
           payload={"need_record_ref": profile.need_record_ref, "qualification_status": profile.qualification_status})
    return row


def register_skill(db: Session, *, actor: Actor, document: dict, correlation_id: str) -> SkillVersion:
    authorize("skill.register", actor.roles)
    violations = validate_document("skill_manifest", document)
    if violations:
        raise CampusError(ErrorCode.VALIDATION_ERROR, "Skill-Manifest verletzt das Schema", blocked_action="skill.register",
                          details={"violations": violations})
    manifest = SkillManifest.model_validate(document)
    if manifest.operating_status == "active" and not manifest.qualification_refs:
        raise CampusError(ErrorCode.QUALIFICATION_MISSING, "Skill ohne Qualifikationsnachweis bleibt Kandidat (T12)",
                          blocked_action="skill.register")
    ref = f"{manifest.id}@{manifest.version}"
    if db.execute(select(SkillVersion).where(SkillVersion.workspace_id == actor.workspace_id, SkillVersion.ref == ref)).first():
        raise CampusError(ErrorCode.VERSION_CONFLICT, "Diese Skillfassung existiert bereits")
    row = SkillVersion(id=new_id("SK"), ref=ref, workspace_id=actor.workspace_id, skill_id=manifest.id,
                       version=manifest.version, document=manifest.model_dump(), operating_status=manifest.operating_status,
                       created_by=actor.actor_id)
    db.add(row)
    db.flush()
    record(db, actor=actor, command="skill.register", object_type="skill", object_id=manifest.id, object_version=manifest.version,
           correlation_id=correlation_id)
    return row


def find_agent(db: Session, workspace_id: str, ref: str) -> AgentProfileVersion | None:
    return db.execute(select(AgentProfileVersion).where(AgentProfileVersion.workspace_id == workspace_id,
                                                        AgentProfileVersion.ref == ref)).scalar_one_or_none()


def inventory(db: Session, workspace_id: str) -> dict:
    """Tabular registry from actual configuration data; works without any LLM (A09)."""
    agents = db.execute(select(AgentProfileVersion).where(AgentProfileVersion.workspace_id == workspace_id)
                        .order_by(AgentProfileVersion.profile_id, AgentProfileVersion.created_at)).scalars().all()
    skills = db.execute(select(SkillVersion).where(SkillVersion.workspace_id == workspace_id)
                        .order_by(SkillVersion.skill_id, SkillVersion.created_at)).scalars().all()
    runs_by_agent = dict(db.execute(select(RunAttempt.agent_profile_version_id, func.count()).where(RunAttempt.workspace_id == workspace_id)
                                    .group_by(RunAttempt.agent_profile_version_id)).all())
    return {
        "agents": [{"id": a.id, "ref": a.ref, "profile_id": a.profile_id, "version": a.version, "institute_id": a.document["institute_id"],
                    "primary_specialization": a.document["primary_specialization"], "operating_status": a.operating_status,
                    "qualification_status": a.qualification_status, "need_record_ref": a.document["need_record_ref"],
                    "creation_reason": a.document["creation_reason"], "alternatives_considered": a.document["alternatives_considered"],
                    "runs": runs_by_agent.get(a.id, 0), "created_at": a.created_at} for a in agents],
        "skills": [{"id": s.id, "ref": s.ref, "skill_id": s.skill_id, "version": s.version, "purpose": s.document["purpose"],
                    "operating_status": s.operating_status, "qualification_refs": s.document["qualification_refs"],
                    "limitations": s.document["limitations"], "created_at": s.created_at} for s in skills],
    }


# ---------------------------------------------------------------- routing

def _pricing(db: Session, workspace_id: str, pricing_ref: str | None) -> Pricing | None:
    if not pricing_ref:
        return None
    row = db.execute(select(PricingVersion).where(PricingVersion.workspace_id == workspace_id,
                                                  PricingVersion.pricing_ref == pricing_ref)).scalar_one_or_none()
    if row is None:
        return None
    return Pricing(pricing_ref=row.pricing_ref, currency=row.currency, input_per_million_minor=row.input_per_million_minor,
                   output_per_million_minor=row.output_per_million_minor)


def _loaded_policy_and_catalog(db: Session, workspace_id: str) -> tuple[RoutingPolicy, str, ModelCatalog, str]:
    policy_row = active_policy(db, workspace_id)
    if policy_row is None:
        raise CampusError(ErrorCode.POLICY_NOT_ACTIVE, "Keine aktive Routing-Policy", blocked_action="route.authorize",
                          required_resolution="Policy aktivieren")
    catalog_row = active_catalog(db, workspace_id)
    if catalog_row is None:
        raise CampusError(ErrorCode.CATALOG_NOT_ACTIVE, "Kein aktiver Modellkatalog", blocked_action="route.authorize",
                          required_resolution="Katalog aktivieren", policy_version=policy_row.revision)
    return (RoutingPolicy.model_validate(policy_row.document), policy_row.id,
            ModelCatalog.model_validate(catalog_row.document), catalog_row.revision)


def _failed_deployments(db: Session, task: Task) -> dict[str, str]:
    """Deployments that failed technically in earlier attempts of this task are excluded from the replan (chapter 10)."""
    excluded: dict[str, str] = {}
    rows = db.execute(
        select(RunAttempt, RoutingDecisionRecord)
        .join(RoutingDecisionRecord, RoutingDecisionRecord.id == RunAttempt.routing_decision_id)
        .where(RunAttempt.task_id == task.id, RunAttempt.end_reason == RunEndReason.PROVIDER_ERROR.value)
    ).all()
    for attempt, decision in rows:
        excluded[decision.document["selected_deployment_id"]] = f"FAILED_IN_ATTEMPT:{attempt.id}"
    return excluded


def _record_rejection(db: Session, actor: Actor, task: Task, proposal: RouteProposal, assessments: list[dict], err: CampusError,
                      correlation_id: str) -> None:
    db.add(RouteProposalRecord(id=new_id("RP"), workspace_id=actor.workspace_id, task_id=task.id,
                               proposal=proposal.model_dump(), assessments=assessments, outcome="rejected", error_code=err.code.value))
    record(db, actor=actor, command="route.authorize", object_type="task", object_id=task.id, object_version=str(task.version),
           correlation_id=correlation_id, policy_decision=f"blocked:{err.code.value}", project_id=task.project_id,
           payload={"selected_deployment_id": proposal.selected_deployment_id, "message": err.message})
    db.commit()
    err.details["audited"] = True


def _selector_proposal(db: Session, *, actor: Actor, task: Task, need: TaskNeed, policy: RoutingPolicy, catalog: ModelCatalog,
                       catalog_revision: str, excluded: dict[str, str], correlation_id: str) -> tuple[RouteProposal, str, Reservation]:
    """Chapter 10: the core picks the selector from the bootstrap pool in fixed order; the selector proposes among
    eligible candidates with permitted summaries only. One selector call per attempt; no recursion."""
    assessments = routing.assess_candidates(need, catalog, policy, excluded)
    eligible = [a for a in assessments if a.eligible]
    if not eligible:
        raise CampusError(ErrorCode.NO_ELIGIBLE_MODEL, "Kein Deployment erfüllt die harten Regeln", blocked_action="route.propose",
                          policy_version=policy.revision, details={"assessments": [a.model_dump() for a in assessments]})
    selector_need = need.model_copy(update={
        "role": "selector", "task_class": SELECTOR_TASK_CLASS, "data_labels": ["public"],
        "required_features": ["text_input", "structured_output"], "context_tokens_estimate": 3000, "max_output_tokens": 800,
        "required_tool_scopes": [], "substantive_contributor_model_ids": [],
    })
    selector: object | None = None
    for dep_id in policy.bootstrap_selector_deployment_ids:  # fixed, logged order
        dep = catalog.deployment(dep_id)
        if dep is not None and not routing.exclusion_reasons(selector_need, dep, policy, excluded):
            selector = dep
            break
    if selector is None:
        raise CampusError(ErrorCode.NO_ELIGIBLE_SELECTOR,
                          "Kein Selector aus dem Bootstrap-Pool ist zulässig; die Aufgabe wartet (Kap. 10)",
                          blocked_action="route.propose", policy_version=policy.revision,
                          required_resolution="bootstrap_selector_deployment_ids mit qualifizierten, zugelassenen Deployments belegen")
    cost = estimate_cost(_pricing(db, actor.workspace_id, selector.pricing_ref), selector_need.context_tokens_estimate,
                         selector_need.max_output_tokens)
    reservation = budget_service.reserve(db, account_id=task.budget_account_id, amount=cost, task_id=task.id, purpose="selector")
    selector_route = routing.ValidatedRoute(
        selected=selector, reviewer=None, assessments=routing.assess_candidates(selector_need, catalog, policy, excluded),
        proposal=RouteProposal(task_id=task.id, selected_deployment_id=selector.id,
                               rationale="Kontrollkern: erster zulässiger Selector in der festen Reihenfolge des Bootstrap-Pools.",
                               evidence_refs=list(selector.qualification_refs), alternatives=[], uncertainties=[]),
        required_review=False,
    )
    selector_decision = routing.build_decision(
        selector_route, decision_id=new_id("RD"), catalog_revision=catalog_revision, routing_policy_ref=policy.revision,
        selection_mode="deterministic_fallback", selector_model_ref=None, reservation_ref=reservation.id,
        pricing_snapshot_ref=selector.pricing_ref or "-", created_at=utcnow().isoformat())
    db.add(RoutingDecisionRecord(id=selector_decision.id, workspace_id=actor.workspace_id, task_id=task.id,
                                 document=selector_decision.model_dump(), state=selector_decision.state))
    db.flush()
    candidates = [{"deployment_id": a.deployment_id, "evidence_refs": a.evidence_refs,
                   "provider_id": catalog.deployment(a.deployment_id).provider_id,
                   "canonical_model_id": catalog.deployment(a.deployment_id).canonical_model_id,
                   "capabilities": catalog.deployment(a.deployment_id).capabilities.model_dump()} for a in eligible]
    system_rules = (
        "Du bist der Routing-Agent eines Forschungscampus. Wähle genau einen Kandidaten aus der Liste zulässiger "
        "Deployments. Nenne nur evidence_refs, die beim gewählten Kandidaten aufgeführt sind. Begründe knapp und sachlich, "
        "nenne Alternativen mit Grund und offene Unsicherheiten. Du entscheidest nichts über die Zulässigkeit; "
        "diese prüft der Kontrollkern. Antworte ausschliesslich im vorgegebenen JSON-Format."
    )
    payload = {
        "task": {"task_id": task.id, "task_class": need.task_class, "role": need.role, "language": need.language,
                 "data_labels": need.data_labels, "required_features": need.required_features,
                 "context_tokens_estimate": need.context_tokens_estimate, "max_output_tokens": need.max_output_tokens,
                 "qualification_mode": need.qualification_mode},
        "eligible_candidates": candidates,
        "selection_criteria": policy.selection_criteria,
    }
    request = LLMRequest(
        invocation_id=f"{task.id}:selector:{selector_decision.id}", deployment_id=selector.id, system_rules=system_rules,
        content_blocks=[{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=1), "data_label": "public",
                         "origin": "core_summary"}],
        output_contract=provider_schema("route_proposal"), max_output_tokens=selector_need.max_output_tokens,
        metadata={"task_id": task.id, "candidates": candidates, "routing_decision_id": selector_decision.id},
    )
    response = _gateway(db, actor.workspace_id).invoke(request, selector, selector_decision)
    _settle_from_usage(db, reservation, _pricing(db, actor.workspace_id, selector.pricing_ref), response)
    try:
        proposal = RouteProposal.model_validate(json.loads(response.text()))
    except Exception as exc:  # the model's answer violates the contract: recorded as PROPOSAL_INVALID, no second call
        raise CampusError(ErrorCode.PROPOSAL_INVALID, f"Selector-Antwort verletzt den Vertrag route_proposal: {type(exc).__name__}",
                          blocked_action="route.propose", policy_version=policy.revision,
                          details={"assessments": [a.model_dump() for a in assessments]}) from exc
    return proposal, selector.canonical_model_id or selector.id, reservation


def _settle_from_usage(db: Session, reservation: Reservation, pricing: Pricing | None, response: LLMResponse) -> Money | None:
    actual: Money | None = None
    if response.usage.known and pricing is not None:
        actual = estimate_cost(pricing, response.usage.input_tokens or 0, response.usage.output_tokens or 0)
    budget_service.settle(db, reservation_id=reservation.id, actual=actual)
    return actual


def authorize_route(db: Session, *, actor: Actor, task_id: str, proposal: dict | None, selection_mode: str,
                    selector_model_ref: str | None, correlation_id: str) -> RoutingDecisionRecord:
    """Chapter 9 steps 1–7: propose (fixture, deterministic rule or LLM selector), validate, reserve generation and review,
    register the decision atomically. A rejected proposal is still recorded with its note (A02). After a provider failure
    the replan excludes the failed deployment and counts against max_route_replans_per_task."""
    authorize("route.authorize", actor.roles)
    task = _own(db, Task, task_id, actor, "Aufgabe")
    need = TaskNeed.model_validate(task.task_need)
    policy, policy_id, catalog, catalog_revision = _loaded_policy_and_catalog(db, actor.workspace_id)
    if need.routing_policy_ref != policy.revision:
        raise CampusError(ErrorCode.POLICY_NOT_ACTIVE, f"Aufgabe ist an Policy {need.routing_policy_ref} gebunden; aktiv ist {policy.revision}",
                          blocked_action="route.authorize", policy_version=policy.revision,
                          required_resolution="Aufgabe neu planen (neue Fassung) oder die gebundene Policy aktivieren")
    excluded = _failed_deployments(db, task)
    prior = db.execute(select(func.count()).select_from(RoutingDecisionRecord).where(
        RoutingDecisionRecord.task_id == task.id, RoutingDecisionRecord.document["required_review"].astext == "true")).scalar() or 0
    limit = policy.limits.max_route_replans_per_task
    if excluded and limit is not None and prior > limit:
        raise CampusError(ErrorCode.REPLAN_LIMIT_REACHED, f"Neuplanungen erschöpft ({prior - 1} von {limit}); Aufgabe wartet auf Entscheid",
                          blocked_action="route.authorize", policy_version=policy.revision,
                          required_resolution="Menschlicher Entscheid: Aufgabe anpassen, Katalog erweitern oder abbrechen")
    selector_used: str | None = selector_model_ref
    if proposal is None and selection_mode == "llm":
        route_proposal, selector_used, _ = _selector_proposal(db, actor=actor, task=task, need=need, policy=policy, catalog=catalog,
                                                             catalog_revision=catalog_revision, excluded=excluded,
                                                             correlation_id=correlation_id)
    elif proposal is None:
        route_proposal = routing.deterministic_proposal(need, catalog, policy, excluded)
        selection_mode = "deterministic_fallback"
    else:
        route_proposal = RouteProposal.model_validate(proposal)
    try:
        route = routing.validate_proposal(need, route_proposal, catalog, policy, excluded)
    except CampusError as err:
        assessments = err.details.get("assessments") or [a.model_dump() for a in routing.assess_candidates(need, catalog, policy, excluded)]
        _record_rejection(db, actor, task, route_proposal, assessments, err, correlation_id)
        raise
    generation_cost = estimate_cost(_pricing(db, actor.workspace_id, route.selected.pricing_ref),
                                    need.context_tokens_estimate, need.max_output_tokens)
    reservations: list[Reservation] = [budget_service.reserve(db, account_id=task.budget_account_id, amount=generation_cost,
                                                              task_id=task.id, purpose="generation")]
    if route.reviewer is not None:
        review_cost = estimate_cost(_pricing(db, actor.workspace_id, route.reviewer.pricing_ref),
                                    need.context_tokens_estimate + need.max_output_tokens, need.max_output_tokens)
        reservations.append(budget_service.reserve(db, account_id=task.budget_account_id, amount=review_cost,
                                                   task_id=task.id, purpose="review"))
    decision = routing.build_decision(
        route, decision_id=new_id("RD"), catalog_revision=catalog_revision, routing_policy_ref=policy.revision,
        selection_mode=selection_mode, selector_model_ref=selector_used, reservation_ref=reservations[0].id,
        pricing_snapshot_ref=route.selected.pricing_ref or "-", created_at=utcnow().isoformat(),
    )
    row = RoutingDecisionRecord(id=decision.id, workspace_id=actor.workspace_id, task_id=task.id,
                                document=decision.model_dump(), state=decision.state)
    db.add(row)
    db.add(RouteProposalRecord(id=new_id("RP"), workspace_id=actor.workspace_id, task_id=task.id,
                               proposal=route_proposal.model_dump(), assessments=[a.model_dump() for a in route.assessments],
                               outcome="authorized", routing_decision_id=decision.id))
    db.flush()
    record(db, actor=actor, command="route.authorize", object_type="routing_decision", object_id=decision.id, object_version=None,
           correlation_id=correlation_id, project_id=task.project_id, event_type="RoutingDecided",
           payload={"task_id": task.id, "selected_deployment_id": decision.selected_deployment_id,
                    "review_deployment_id": decision.review_deployment_id, "reservation_ids": [r.id for r in reservations],
                    "policy_id": policy_id, "selection_mode": selection_mode, "excluded": excluded})
    return row


# ---------------------------------------------------------------- runs

def _end_reason_for(err: CampusError) -> RunEndReason:
    return {
        ErrorCode.OUTCOME_UNKNOWN: RunEndReason.OUTCOME_UNKNOWN,
        ErrorCode.PROTECTED_REFUSAL: RunEndReason.PROTECTED_REFUSAL,
    }.get(err.code, RunEndReason.PROVIDER_ERROR)


def start_run(db: Session, *, actor: Actor, task_id: str, routing_decision_id: str, correlation_id: str) -> dict:
    """Guard, execute one generation call through the authorized route, freeze the output as an artifact, settle cost.
    S1 is synchronous; S2 moves this into durable workflows."""
    authorize("run.start", actor.roles)
    task = _own(db, Task, task_id, actor, "Aufgabe")
    need = TaskNeed.model_validate(task.task_need)
    ws = db.get(Workspace, actor.workspace_id)
    project = db.get(Project, task.project_id)
    mandate = db.get(MandateVersion, project.mandate_version_id)
    policy_row = active_policy(db, actor.workspace_id)
    policy = RoutingPolicy.model_validate(policy_row.document) if policy_row else None
    catalog_row = active_catalog(db, actor.workspace_id)
    agent = find_agent(db, actor.workspace_id, need.agent_profile_version_ref)
    decision_row = db.get(RoutingDecisionRecord, routing_decision_id)
    if decision_row is not None and (decision_row.task_id != task.id or decision_row.workspace_id != actor.workspace_id):
        raise CampusError(ErrorCode.ROUTE_NOT_AUTHORIZED, "Routingentscheid gehört nicht zu dieser Aufgabe", blocked_action="run.start")
    decision = RoutingDecision.model_validate(decision_row.document) if decision_row else None
    if decision is not None and db.execute(select(RunAttempt).where(RunAttempt.routing_decision_id == decision.id)).first():
        raise CampusError(ErrorCode.ROUTE_NOT_AUTHORIZED, "Dieser Routingentscheid wurde bereits ausgeführt; neue Route nötig",
                          blocked_action="run.start", required_resolution="route.authorize erneut aufrufen")
    reservations = db.execute(select(Reservation).where(Reservation.task_id == task.id, Reservation.state == "reserved")).scalars().all()
    ctx = RunStartContext(
        workspace_stopped=bool(ws.stopped),
        mandate_state=MandateState(mandate.state),
        policy_state=ActivationState(policy_row.state) if policy_row else None,
        policy_revision=policy_row.revision if policy_row else None,
        missing_limits=policy.limits.missing() if policy else [],
        catalog_state=ActivationState(catalog_row.state) if catalog_row else None,
        agent_registered=agent is not None,
        agent_operating_status=OperatingStatus(agent.operating_status) if agent else None,
        agent_qualification_status=QualificationStatus(agent.qualification_status) if agent else None,
        task_qualification_mode=need.qualification_mode,
        route_state=decision.state if decision else None,
        reservation_present=any(r.purpose == "generation" for r in reservations),
        review_required=bool(decision.required_review) if decision else True,
        review_reservation_present=any(r.purpose == "review" for r in reservations),
    )
    check_run_start(ctx)
    assert decision is not None and agent is not None and policy is not None
    attempt_no = (db.execute(select(func.max(RunAttempt.attempt_no)).where(RunAttempt.task_id == task.id)).scalar() or 0) + 1
    attempt = RunAttempt(id=new_id("R"), workspace_id=actor.workspace_id, task_id=task.id, attempt_no=attempt_no,
                         routing_decision_id=decision.id, agent_profile_version_id=agent.id, state=RunState.RUNNING.value)
    db.add(attempt)
    task.state = "running"
    db.flush()
    record(db, actor=actor, command="run.start", object_type="run_attempt", object_id=attempt.id, object_version=str(attempt_no),
           correlation_id=correlation_id, project_id=task.project_id,
           payload={"task_id": task.id, "routing_decision_id": decision.id, "agent_profile_version_id": agent.id})
    catalog = ModelCatalog.model_validate(catalog_row.document)
    deployment = catalog.deployment(decision.selected_deployment_id)
    request = LLMRequest(
        invocation_id=f"{attempt.id}:gen:1", deployment_id=deployment.id,
        system_rules=f"Aufgabe {need.task_class}; Datenklassen {','.join(need.data_labels)}; Mandat {need.mandate_version_ref}. "
                     "Kennzeichne Beobachtung, Berechnung, Ableitung und Hypothese getrennt; erfinde keine Fundstellen.",
        content_blocks=[{"type": "text", "text": f"Aufgabe {task.id} ({need.task_class}); Inhalt folgt in S2 aus dem Forschungsdossier.",
                         "data_label": need.data_labels[0], "origin": "task"}],
        max_output_tokens=need.max_output_tokens,
        metadata={"routing_decision_id": decision.id, "attempt_id": attempt.id},
    )
    generation = next(r for r in reservations if r.purpose == "generation")
    try:
        response = _gateway(db, actor.workspace_id).invoke(request, deployment, decision)
    except CampusError as err:
        reason = _end_reason_for(err)
        attempt.state = RunState.OUTCOME_UNKNOWN.value if reason == RunEndReason.OUTCOME_UNKNOWN else RunState.FAILED.value
        attempt.end_reason = reason.value
        attempt.ended_at = utcnow()
        task.state = "waiting"
        if reason == RunEndReason.OUTCOME_UNKNOWN:
            budget_service.settle(db, reservation_id=generation.id, actual=None)  # stays bound as open_unknown
        else:
            generation.state = "released"  # nothing was consumed before send
        db.flush()
        record(db, actor=actor, command="run.end", object_type="run_attempt", object_id=attempt.id, object_version=str(attempt_no),
               correlation_id=correlation_id, project_id=task.project_id, event_type="RunEnded",
               payload={"end_reason": attempt.end_reason, "error_code": err.code.value, "retryable": err.retryable})
        db.commit()
        err.details["audited"] = True
        raise
    actual = _settle_from_usage(db, generation, _pricing(db, actor.workspace_id, deployment.pricing_ref), response)
    synthetic = any(b.get("synthetic") for b in response.blocks)
    content = {"blocks": response.blocks, "stop_reason": response.stop_reason, "effective_model_id": response.effective_model_id}
    digest = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    artifact = ArtifactVersion(id=new_id("A"), workspace_id=actor.workspace_id, task_id=task.id, run_attempt_id=attempt.id,
                               kind="generation_output", content=content, sha256=digest,
                               producer_canonical_model_id=decision.selected_canonical_model_id, synthetic=synthetic)
    db.add(artifact)
    attempt.state = RunState.FINISHED.value
    attempt.end_reason = RunEndReason.RESULT.value
    attempt.ended_at = utcnow()
    task.state = "waiting"  # waits for the required review; a finished generation is not a finished task
    db.flush()
    record(db, actor=actor, command="run.end", object_type="run_attempt", object_id=attempt.id, object_version=str(attempt_no),
           correlation_id=correlation_id, project_id=task.project_id, event_type="RunEnded",
           payload={"end_reason": attempt.end_reason, "invocation_id": response.invocation_id, "artifact_version_id": artifact.id,
                    "artifact_sha256": digest, "effective_model_id": response.effective_model_id,
                    "cost_state": "settled" if actual else "open", "synthetic": synthetic})
    return {
        "run_attempt_id": attempt.id, "attempt_no": attempt_no, "state": attempt.state, "end_reason": attempt.end_reason,
        "invocation_id": response.invocation_id, "effective_model_id": response.effective_model_id,
        "usage": {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens},
        "cost": actual.format() if actual else "offen", "review_required": decision.required_review,
        "review_deployment_id": decision.review_deployment_id, "artifact_version_id": artifact.id, "artifact_sha256": digest,
        "synthetic": synthetic,
    }


def start_review(db: Session, *, actor: Actor, run_id: str, correlation_id: str) -> dict:
    """Distinct-model first review of the frozen artifact (Art. 17, 27; chapter 11). The reviewer receives the artifact and the
    task context, never the producer's self-assessment. Findings are recorded as objections; the verdict stays a model opinion."""
    authorize("review.start", actor.roles)
    attempt = _own(db, RunAttempt, run_id, actor, "Lauf")
    if attempt.state != RunState.FINISHED.value:
        raise CampusError(ErrorCode.INVALID_TRANSITION, f"Lauf ist {attempt.state}; geprüft wird nur ein beendeter Lauf", blocked_action="review.start")
    task = db.get(Task, attempt.task_id)
    need = TaskNeed.model_validate(task.task_need)
    artifact = db.execute(select(ArtifactVersion).where(ArtifactVersion.run_attempt_id == attempt.id)).scalar_one_or_none()
    if artifact is None:
        raise CampusError(ErrorCode.NOT_FOUND, "Kein eingefrorenes Artefakt zu diesem Lauf")
    if db.execute(select(ReviewReportRecord).where(ReviewReportRecord.run_attempt_id == attempt.id)).first():
        raise CampusError(ErrorCode.INVALID_TRANSITION, "Dieser Lauf wurde bereits geprüft; eine neue Prüfung verlangt ein neues Artefakt (I05)")
    decision_row = db.get(RoutingDecisionRecord, attempt.routing_decision_id)
    decision = RoutingDecision.model_validate(decision_row.document)
    if not decision.required_review or not decision.review_deployment_id:
        raise CampusError(ErrorCode.REVIEW_INDEPENDENCE_VIOLATED, "Für diese Route ist kein Prüfmodell autorisiert", blocked_action="review.start")
    catalog_row = active_catalog(db, actor.workspace_id)
    catalog = ModelCatalog.model_validate(catalog_row.document)
    reviewer = catalog.deployment(decision.review_deployment_id)
    if reviewer is None or reviewer.canonical_model_id in {None, artifact.producer_canonical_model_id} | set(need.substantive_contributor_model_ids):
        raise CampusError(ErrorCode.REVIEW_INDEPENDENCE_VIOLATED,
                          "Prüfmodell ist kanonisch nicht vom Erzeuger getrennt oder nicht mehr im Katalog; Prüfung bleibt offen (I04)",
                          blocked_action="review.start", required_resolution="Route neu bestimmen (anderes Prüfmodell)")
    reservation = db.execute(select(Reservation).where(Reservation.task_id == task.id, Reservation.purpose == "review",
                                                       Reservation.state == "reserved")).scalars().first()
    if reservation is None:
        raise CampusError(ErrorCode.BUDGET_EXHAUSTED, "Keine gebundene Prüfreserve für diese Aufgabe", blocked_action="review.start")
    artifact_text = "\n".join(str(b.get("text", "")) for b in artifact.content["blocks"] if b.get("type") == "text")
    system_rules = (
        "Du bist ein unabhängiger Erstprüfer in einem Forschungscampus. Du erhältst ein eingefrorenes Artefakt und den "
        "Aufgabenkontext, keine Bewertung des Erzeugers. Prüfe Belegbindung, Geltungsbereich, Trennung von Beobachtung und "
        "Vermutung und offene Unsicherheiten. Nenne Einwände mit Gegenstand, Argument, Tragweite und Bezug. Ein Einwand ist "
        "kein Beweis; fehlende Prüfbarkeit meldest du als cannot_assess. Antworte ausschliesslich im vorgegebenen JSON-Format."
    )
    request = LLMRequest(
        invocation_id=f"{attempt.id}:review:1", deployment_id=reviewer.id, system_rules=system_rules,
        content_blocks=[
            {"type": "text", "text": f"Aufgabe: {need.task_class}; Sprache {need.language}; Datenklassen {need.data_labels}",
             "data_label": need.data_labels[0], "origin": "task"},
            {"type": "text", "text": f"Artefakt (sha256 {artifact.sha256}):\n{artifact_text}", "data_label": need.data_labels[0],
             "origin": "artifact"},
        ],
        output_contract=provider_schema("review_report"), max_output_tokens=need.max_output_tokens,
        metadata={"routing_decision_id": decision.id, "attempt_id": attempt.id, "artifact_sha256": artifact.sha256,
                  "task_class": need.task_class},
    )
    try:
        response = _gateway(db, actor.workspace_id).invoke(request, reviewer, decision)
    except CampusError as err:
        if err.code == ErrorCode.OUTCOME_UNKNOWN:
            budget_service.settle(db, reservation_id=reservation.id, actual=None)
        record(db, actor=actor, command="review.start", object_type="run_attempt", object_id=attempt.id, object_version=None,
               correlation_id=correlation_id, policy_decision=f"blocked:{err.code.value}", project_id=task.project_id,
               payload={"message": err.message, "reviewer_deployment_id": reviewer.id})
        db.commit()
        err.details["audited"] = True
        raise
    _settle_from_usage(db, reservation, _pricing(db, actor.workspace_id, reviewer.pricing_ref), response)
    try:
        report = ReviewReport.model_validate(json.loads(response.text()))
    except Exception as exc:
        raise CampusError(ErrorCode.PROPOSAL_INVALID, f"Prüfbericht verletzt den Vertrag review_report: {type(exc).__name__}",
                          blocked_action="review.start") from exc
    row = ReviewReportRecord(
        id=new_id("RR"), workspace_id=actor.workspace_id, task_id=task.id, run_attempt_id=attempt.id, artifact_version_id=artifact.id,
        artifact_sha256=artifact.sha256, reviewer_deployment_id=reviewer.id, reviewer_canonical_model_id=reviewer.canonical_model_id or "",
        reviewer_effective_model_id=response.effective_model_id, producer_canonical_model_id=artifact.producer_canonical_model_id,
        invocation_id=response.invocation_id, verdict=report.verdict, document=report.model_dump(),
    )
    db.add(row)
    db.flush()
    findings = []
    for f in report.findings:
        finding = FindingRecord(id=new_id("F"), workspace_id=actor.workspace_id, review_report_id=row.id, task_id=task.id,
                                subject=f.subject, argument=f.argument, severity=f.severity, scope=f.scope, status="open")
        db.add(finding)
        findings.append(finding)
    task.state = "finished" if report.verdict == "no_objection" else "waiting"  # objections stay open; majority never closes them (T14)
    db.flush()
    record(db, actor=actor, command="review.start", object_type="review_report", object_id=row.id, object_version=None,
           correlation_id=correlation_id, project_id=task.project_id, event_type="ReviewCompleted",
           payload={"run_attempt_id": attempt.id, "verdict": report.verdict, "finding_ids": [f.id for f in findings],
                    "reviewer_canonical_model_id": row.reviewer_canonical_model_id, "producer_canonical_model_id": row.producer_canonical_model_id,
                    "artifact_sha256": artifact.sha256})
    for finding in findings:
        record(db, actor=actor, command="finding.raise", object_type="finding", object_id=finding.id, object_version=None,
               correlation_id=correlation_id, project_id=task.project_id, event_type="ClaimContested",
               payload={"severity": finding.severity, "review_report_id": row.id})
    return {"review_report_id": row.id, "verdict": report.verdict, "findings": [{"id": f.id, "subject": f.subject, "severity": f.severity}
                                                                               for f in findings],
            "reviewer_canonical_model_id": row.reviewer_canonical_model_id, "reviewer_effective_model_id": row.reviewer_effective_model_id,
            "producer_canonical_model_id": row.producer_canonical_model_id, "artifact_sha256": artifact.sha256, "task_state": task.state,
            "synthetic": any(b.get("synthetic") for b in response.blocks)}


def cancel_run(db: Session, *, actor: Actor, run_id: str, reason: str, correlation_id: str) -> RunAttempt:
    authorize("run.cancel", actor.roles)
    attempt = _own(db, RunAttempt, run_id, actor, "Lauf")
    if attempt.state in {RunState.FINISHED.value, RunState.CANCELLED.value, RunState.FAILED.value}:
        raise CampusError(ErrorCode.INVALID_TRANSITION, f"Lauf ist bereits {attempt.state}")
    attempt.state = RunState.CANCELLED.value
    attempt.end_reason = RunEndReason.STOP.value
    attempt.ended_at = utcnow()
    db.flush()
    task = db.get(Task, attempt.task_id)
    record(db, actor=actor, command="run.cancel", object_type="run_attempt", object_id=attempt.id, object_version=str(attempt.attempt_no),
           correlation_id=correlation_id, project_id=task.project_id, payload={"reason": reason})
    return attempt


def stop_workspace(db: Session, *, actor: Actor, reason: str, correlation_id: str) -> Workspace:
    """Global stop (Art. 12, UI §4.5). Works without any LLM; registers and approvals stay usable."""
    authorize("workspace.stop", actor.roles)
    ws = db.get(Workspace, actor.workspace_id)
    ws.stopped = True
    ws.stop_reason = reason
    affected = db.execute(select(RunAttempt).where(RunAttempt.workspace_id == ws.id, RunAttempt.state == RunState.RUNNING.value)).scalars().all()
    for attempt in affected:
        attempt.state = RunState.CANCELLED.value
        attempt.end_reason = RunEndReason.STOP.value
        attempt.ended_at = utcnow()
    db.flush()
    record(db, actor=actor, command="workspace.stop", object_type="workspace", object_id=ws.id, object_version=None,
           correlation_id=correlation_id, payload={"reason": reason, "cancelled_runs": [a.id for a in affected]})
    return ws


def resume_workspace(db: Session, *, actor: Actor, reason: str, correlation_id: str) -> Workspace:
    authorize("workspace.resume", actor.roles)
    if not reason.strip():
        raise CampusError(ErrorCode.VALIDATION_ERROR, "Wiederaufnahme verlangt einen dokumentierten Grund (Art. 12)")
    ws = db.get(Workspace, actor.workspace_id)
    ws.stopped = False
    ws.stop_reason = None
    db.flush()
    record(db, actor=actor, command="workspace.resume", object_type="workspace", object_id=ws.id, object_version=None,
           correlation_id=correlation_id, payload={"reason": reason})
    return ws


def project_events(db: Session, *, actor: Actor, project_id: str, after_id: int) -> list[dict]:
    _own(db, Project, project_id, actor, "Projekt")
    rows = db.execute(select(OutboxEvent).where(OutboxEvent.project_id == project_id, OutboxEvent.id > after_id)
                      .order_by(OutboxEvent.id).limit(200)).scalars().all()
    return [{"id": e.id, "event_type": e.event_type, "object_type": e.object_type, "object_id": e.object_id,
             "object_version": e.object_version, "created_at": e.created_at, "payload": e.payload} for e in rows]


def discover_models(*, actor: Actor, adapter_name: str, secret_ref: str, api_base: str | None) -> list[str]:
    """Provider discovery for the cockpit. Lists native ids only; enabling, data authorization and qualification stay separate."""
    authorize("catalog.discover", actor.roles)
    adapters = build_adapters()
    adapter = adapters.get(adapter_name)
    if adapter is None or not hasattr(adapter, "list_models"):
        raise CampusError(ErrorCode.ADAPTER_NOT_APPROVED, f"Adapter {adapter_name} ist nicht freigegeben oder kennt keine Discovery",
                          blocked_action="catalog.discover")
    try:
        return adapter.list_models(secret_ref, api_base)
    except Exception as exc:  # typed provider failures surface as campus errors; nothing is cached or enabled
        raise CampusError(ErrorCode.PROVIDER_REJECTED, f"Discovery fehlgeschlagen: {type(exc).__name__}: {exc}",
                          blocked_action="catalog.discover") from exc
