"""Projects, tasks, registry, route authorization and runs (chapters 6, 7, 9–11, 16)."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from campus_contracts.models import (
    AgentProfile,
    ModelCatalog,
    RouteProposal,
    RoutingDecision,
    SkillManifest,
    TaskNeed,
)
from campus_contracts.policy import RoutingPolicy
from campus_contracts.validation import validate_document
from campus_domain import routing
from campus_domain.authz import authorize
from campus_domain.budget import Pricing, estimate_cost
from campus_domain.errors import CampusError, ErrorCode
from campus_domain.guards import RunStartContext, check_run_start
from campus_domain.money import Money, parse_money
from campus_domain.statuses import (
    ActivationState,
    MandateState,
    OperatingStatus,
    QualificationStatus,
    RunEndReason,
    RunState,
)
from campus_gateway.gateway import Gateway
from campus_gateway.mock_adapter import MockAdapter
from campus_gateway.port import LLMRequest
from campus_infra import budget_service
from campus_infra.audit import Actor, record
from campus_infra.ids import new_id, utcnow
from campus_infra.journal import DbInvocationJournal
from campus_infra.models import (
    AgentProfileVersion,
    BudgetAccount,
    Institute,
    MandateVersion,
    OutboxEvent,
    PricingVersion,
    Project,
    Reservation,
    RouteProposalRecord,
    RoutingDecisionRecord,
    RunAttempt,
    SkillVersion,
    Task,
    Workspace,
)

from .services_governance import active_catalog, active_policy


def _own(db: Session, model, object_id: str, actor: Actor, what: str):
    row = db.get(model, object_id)
    if row is None or row.workspace_id != actor.workspace_id:
        raise CampusError(ErrorCode.NOT_FOUND, f"{what} nicht gefunden")
    return row


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
            raise CampusError(ErrorCode.LIMITS_NOT_SET if depth_limit is None else ErrorCode.FORBIDDEN,
                              f"Delegationstiefe {depth} überschreitet das Limit {depth_limit}", blocked_action="task.create")
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


def authorize_route(db: Session, *, actor: Actor, task_id: str, proposal: dict | None, selection_mode: str,
                    selector_model_ref: str | None, correlation_id: str) -> RoutingDecisionRecord:
    """Chapter 9 steps 5–7: validate, reserve (generation + review), register the decision atomically.
    A rejected proposal is still recorded with its note (A02)."""
    authorize("route.authorize", actor.roles)
    task = _own(db, Task, task_id, actor, "Aufgabe")
    need = TaskNeed.model_validate(task.task_need)
    policy, policy_id, catalog, catalog_revision = _loaded_policy_and_catalog(db, actor.workspace_id)
    if need.routing_policy_ref != policy.revision:
        raise CampusError(
            ErrorCode.POLICY_NOT_ACTIVE,
            f"Aufgabe ist an Policy {need.routing_policy_ref} gebunden; aktiv ist {policy.revision}",
            blocked_action="route.authorize",
            required_resolution="Aufgabe neu planen (neue Fassung) oder die gebundene Policy aktivieren",
            policy_version=policy.revision,
        )
    if proposal is None:
        route_proposal = routing.deterministic_proposal(need, catalog, policy)
        selection_mode = "deterministic_fallback"
    else:
        route_proposal = RouteProposal.model_validate(proposal)
    try:
        route = routing.validate_proposal(need, route_proposal, catalog, policy)
    except CampusError as err:
        assessments = err.details.get("assessments") or [a.model_dump() for a in routing.assess_candidates(need, catalog, policy)]
        db.add(RouteProposalRecord(id=new_id("RP"), workspace_id=actor.workspace_id, task_id=task.id,
                                   proposal=route_proposal.model_dump(), assessments=assessments, outcome="rejected",
                                   error_code=err.code.value))
        record(db, actor=actor, command="route.authorize", object_type="task", object_id=task.id, object_version=str(task.version),
               correlation_id=correlation_id, policy_decision=f"blocked:{err.code.value}", project_id=task.project_id,
               payload={"selected_deployment_id": route_proposal.selected_deployment_id, "message": err.message})
        db.commit()
        err.details["audited"] = True
        raise
    # Budget: generation and (when required) review are reserved before the call (chapter 16, A04).
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
        selection_mode=selection_mode, selector_model_ref=selector_model_ref, reservation_ref=reservations[0].id,
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
                    "policy_id": policy_id})
    return row


# ---------------------------------------------------------------- runs

def start_run(db: Session, *, actor: Actor, task_id: str, routing_decision_id: str, correlation_id: str) -> dict:
    """Guard, then execute one synchronous mock generation call and settle its cost. S0 only; S2 moves this to workflows."""
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
        review_required=bool(decision.required_review) if decision else routing.review_required(need, policy) if policy else True,
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
    # S0: synchronous mock call through the gateway with the authorized route.
    catalog = ModelCatalog.model_validate(catalog_row.document)
    deployment = catalog.deployment(decision.selected_deployment_id)
    gateway = Gateway({"mock": MockAdapter()}, DbInvocationJournal(db, actor.workspace_id))
    request = LLMRequest(
        invocation_id=f"{attempt.id}:gen:1", deployment_id=deployment.id,
        system_rules=f"Aufgabe {need.task_class}; Datenklassen {','.join(need.data_labels)}; Mandat {need.mandate_version_ref}",
        content_blocks=[{"type": "text", "text": f"Synthetic S0 task payload for {task.id}", "data_label": need.data_labels[0],
                         "origin": "fixture"}],
        max_output_tokens=need.max_output_tokens,
        metadata={"routing_decision_id": decision.id, "attempt_id": attempt.id},
    )
    generation = next(r for r in reservations if r.purpose == "generation")
    try:
        response = gateway.invoke(request, deployment, decision)
    except CampusError as err:
        attempt.state = RunState.OUTCOME_UNKNOWN.value if err.code == ErrorCode.OUTCOME_UNKNOWN else RunState.FAILED.value
        attempt.end_reason = (RunEndReason.OUTCOME_UNKNOWN if err.code == ErrorCode.OUTCOME_UNKNOWN else RunEndReason.PROVIDER_ERROR).value
        attempt.ended_at = utcnow()
        if err.code == ErrorCode.OUTCOME_UNKNOWN:
            budget_service.settle(db, reservation_id=generation.id, actual=None)  # stays bound as open_unknown
        db.flush()
        record(db, actor=actor, command="run.end", object_type="run_attempt", object_id=attempt.id, object_version=str(attempt_no),
               correlation_id=correlation_id, project_id=task.project_id, event_type="RunEnded",
               payload={"end_reason": attempt.end_reason, "error_code": err.code.value})
        db.commit()
        err.details["audited"] = True
        raise
    pricing = _pricing(db, actor.workspace_id, deployment.pricing_ref)
    actual: Money | None = None
    if response.usage.known and pricing is not None:
        actual = estimate_cost(pricing, response.usage.input_tokens or 0, response.usage.output_tokens or 0)
    budget_service.settle(db, reservation_id=generation.id, actual=actual)
    attempt.state = RunState.FINISHED.value
    attempt.end_reason = RunEndReason.RESULT.value
    attempt.ended_at = utcnow()
    task.state = "waiting"  # waits for the required review; a finished generation is not a finished task
    db.flush()
    record(db, actor=actor, command="run.end", object_type="run_attempt", object_id=attempt.id, object_version=str(attempt_no),
           correlation_id=correlation_id, project_id=task.project_id, event_type="RunEnded",
           payload={"end_reason": attempt.end_reason, "invocation_id": response.invocation_id,
                    "effective_model_id": response.effective_model_id, "cost_state": "settled" if actual else "open"})
    return {
        "run_attempt_id": attempt.id, "attempt_no": attempt_no, "state": attempt.state, "end_reason": attempt.end_reason,
        "invocation_id": response.invocation_id, "effective_model_id": response.effective_model_id,
        "usage": {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens},
        "cost": actual.format() if actual else "offen", "review_required": decision.required_review,
        "review_deployment_id": decision.review_deployment_id, "synthetic": True,
    }


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
