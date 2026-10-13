"""Projects, tasks, registry, route authorization (selector + core), runs, distinct-model review, discovery.

Chapters 6, 7, 9–11, 14, 16, 17–18. S1.1 hardening (review of commit 7b73171):
- one common pre-call check for every external model call (selector, generation, review): stop, mandate, task hold,
  authorized route, route-bound reservation;
- the call order and its reservation are committed BEFORE the external call; usage is settled and committed right after
  it, so a failing content check never erases the evidence of a call that happened;
- a protected refusal puts the task on hold; holds are lifted only by a human with a reason;
- routes are bound to the catalog revision they were authorized against;
- qualification evidence is bound to the task class; the selector receives evidence summaries and cost estimates.
"""
from __future__ import annotations

import hashlib
import json

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from campus_contracts.models import (
    AgentProfile,
    ClaimSet,
    ModelCatalog,
    ReviewReport,
    RouteProposal,
    RoutingDecision,
    SkillManifest,
    TaskNeed,
)
from campus_contracts.policy import RoutingPolicy
from campus_contracts.validation import provider_schema, validate_document
from campus_domain import routing
from campus_domain.authz import authorize
from campus_domain.budget import Pricing, estimate_cost
from campus_domain.composition import HARNESS_PROFILE, Composition, SkillInput, compose
from campus_domain.errors import CampusError, ErrorCode
from campus_domain.guards import InvocationContext, RunStartContext, check_invocation, check_run_start
from campus_domain.money import Money, parse_money
from campus_domain.qualification import check_tool_scopes
from campus_domain.statuses import ActivationState, MandateState, OperatingStatus, QualificationStatus, RunEndReason, RunState
from campus_gateway.adapters import build_adapters
from campus_gateway.endpoints import check_api_base
from campus_gateway.gateway import Gateway
from campus_gateway.port import LLMRequest, Usage
from campus_infra import budget_service
from campus_infra.audit import Actor, record
from campus_infra.ids import new_id, utcnow
from campus_infra.journal import DbInvocationJournal
from campus_infra.models import (
    AgentProfileVersion,
    ArtifactVersion,
    BudgetAccount,
    ClaimVersion,
    ExperimentRun,
    FindingRecord,
    Institute,
    MandateVersion,
    ModelCatalogRevision,
    NeedRecord,
    OutboxEvent,
    PricingVersion,
    Project,
    QualificationRecord,
    Reservation,
    ReviewReportRecord,
    RouteProposalRecord,
    RoutingDecisionRecord,
    RunAttempt,
    SkillVersion,
    Task,
    Workspace,
)

from . import services_claims
from .services_governance import active_catalog, active_policy

SELECTOR_TASK_CLASS = "route_selection"
R_QUAL_TASK_CLASS = "QUALIFICATION_NOT_FOR_TASK_CLASS:"


def _own(db: Session, model, object_id: str, actor: Actor, what: str):
    row = db.get(model, object_id)
    if row is None or row.workspace_id != actor.workspace_id:
        raise CampusError(ErrorCode.NOT_FOUND, f"{what} nicht gefunden")
    return row


def _gateway(db: Session, workspace_id: str) -> Gateway:
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False, future=True)
    return Gateway(build_adapters(), DbInvocationJournal(factory, workspace_id))


# ---------------------------------------------------------------- projects & tasks

def create_project(db: Session, *, actor: Actor, mandate_version_id: str, institute_id: str, title: str, budget: str,
                   correlation_id: str, budget_parent_id: str | None = None) -> Project:
    """A project under the mandate's root budget or, for pilot arms (S5), under a shared pilot budget of the same mandate."""
    authorize("project.create", actor.roles)
    mandate = _own(db, MandateVersion, mandate_version_id, actor, "Mandatsfassung")
    if mandate.state != MandateState.APPROVED.value:
        raise CampusError(ErrorCode.MANDATE_NOT_APPROVED, "Projekt braucht ein freigegebenes Mandat", blocked_action="project.create")
    institute = _own(db, Institute, institute_id, actor, "Institut")
    root = db.execute(select(BudgetAccount).where(BudgetAccount.mandate_version_id == mandate.id,
                                                  BudgetAccount.parent_id.is_(None))).scalar_one()
    if budget_parent_id is not None:
        parent = _own(db, BudgetAccount, budget_parent_id, actor, "Budgetkonto")
        if parent.mandate_version_id != mandate.id:
            raise CampusError(ErrorCode.VALIDATION_ERROR, "Budgetkonto gehört zu einem anderen Mandat", blocked_action="project.create")
        root = parent
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
        if parent.project_id != project.id:  # S0 finding: parent, project and budget must belong together
            raise CampusError(ErrorCode.VALIDATION_ERROR, "Elternaufgabe gehört zu einem anderen Projekt; Budget wird nicht übernommen",
                              blocked_action="task.create")
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


def release_hold(db: Session, *, actor: Actor, task_id: str, reason: str, correlation_id: str) -> Task:
    """A hold (e.g. protected refusal) is lifted only by a human with a documented reason (Art. 12, 18)."""
    authorize("task.hold.release", actor.roles)
    task = _own(db, Task, task_id, actor, "Aufgabe")
    if not reason.strip():
        raise CampusError(ErrorCode.VALIDATION_ERROR, "Aufhebung einer Sperre verlangt eine Begründung")
    if not task.hold_reason:
        raise CampusError(ErrorCode.INVALID_TRANSITION, "Aufgabe ist nicht gesperrt")
    previous = task.hold_reason
    task.hold_reason = None
    task.hold_since = None
    db.flush()
    record(db, actor=actor, command="task.hold.release", object_type="task", object_id=task.id, object_version=str(task.version),
           correlation_id=correlation_id, project_id=task.project_id, event_type="HoldReleased",
           payload={"previous_hold": previous, "reason": reason})
    return task


# ---------------------------------------------------------------- registry

def _resolve_qualification_refs(db: Session, workspace_id: str, refs: list[str], *, what: str) -> None:
    """Claimed qualification references must resolve to passed records (S0 finding: references are not evidence)."""
    if not refs:
        return
    rows = {r.ref: r for r in db.execute(select(QualificationRecord).where(QualificationRecord.workspace_id == workspace_id,
                                                                           QualificationRecord.ref.in_(refs))).scalars()}
    missing = [r for r in refs if r not in rows]
    failed = [r for r in refs if r in rows and not rows[r].passed]
    if missing or failed:
        raise CampusError(ErrorCode.QUALIFICATION_MISSING,
                          f"{what}: qualification_refs sind keine bestandenen Nachweise (fehlend {missing}, nicht bestanden {failed})",
                          blocked_action="registry.register", required_resolution="Nachweise mit POST /qualifications registrieren")


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
    if profile.qualification_status != "not_evaluated":
        _resolve_qualification_refs(db, actor.workspace_id, profile.qualification_refs, what="Agentenprofil")
    check_tool_scopes(profile.requested_tool_scopes, what="Agentenprofil")  # A10: no new privileges through registration
    if not db.execute(select(NeedRecord).where(NeedRecord.workspace_id == actor.workspace_id, NeedRecord.ref == profile.need_record_ref)).first():
        raise CampusError(ErrorCode.NEED_NOT_FOUND, f"need_record_ref {profile.need_record_ref!r} ist kein registrierter Bedarf (Art. 26, Anhang G)",
                          blocked_action="agent.register", required_resolution="Bedarf zuerst mit POST /needs erfassen")
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


def register_skill(db: Session, *, actor: Actor, document: dict, correlation_id: str, instructions: str | None = None) -> SkillVersion:
    authorize("skill.register", actor.roles)
    violations = validate_document("skill_manifest", document)
    if violations:
        raise CampusError(ErrorCode.VALIDATION_ERROR, "Skill-Manifest verletzt das Schema", blocked_action="skill.register",
                          details={"violations": violations})
    manifest = SkillManifest.model_validate(document)
    if manifest.operating_status == "active" and not manifest.qualification_refs:
        raise CampusError(ErrorCode.QUALIFICATION_MISSING, "Skill ohne Qualifikationsnachweis bleibt Kandidat (T12)",
                          blocked_action="skill.register")
    if manifest.operating_status == "active" or manifest.qualification_refs:
        _resolve_qualification_refs(db, actor.workspace_id, manifest.qualification_refs, what="Skill")
    check_tool_scopes(manifest.requested_tool_scopes, what="Skill")  # A10
    if not db.execute(select(NeedRecord).where(NeedRecord.workspace_id == actor.workspace_id, NeedRecord.ref == manifest.origin_need_ref)).first():
        raise CampusError(ErrorCode.NEED_NOT_FOUND, f"origin_need_ref {manifest.origin_need_ref!r} ist kein registrierter Bedarf (Art. 26)",
                          blocked_action="skill.register", required_resolution="Bedarf zuerst mit POST /needs erfassen")
    if instructions is not None and hashlib.sha256(instructions.encode("utf-8")).hexdigest() != manifest.package_sha256:
        raise CampusError(ErrorCode.VALIDATION_ERROR, "package_sha256 passt nicht zu den gelieferten Anweisungen; das Paket ist hashgebunden",
                          blocked_action="skill.register")
    ref = f"{manifest.id}@{manifest.version}"
    if db.execute(select(SkillVersion).where(SkillVersion.workspace_id == actor.workspace_id, SkillVersion.ref == ref)).first():
        raise CampusError(ErrorCode.VERSION_CONFLICT, "Diese Skillfassung existiert bereits")
    row = SkillVersion(id=new_id("SK"), ref=ref, workspace_id=actor.workspace_id, skill_id=manifest.id,
                       version=manifest.version, document=manifest.model_dump(), operating_status=manifest.operating_status,
                       instructions=instructions, created_by=actor.actor_id)
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
                    "skill_version_refs": list(a.document.get("skill_version_refs", [])), "qualified_task_classes": list(a.qualified_task_classes or []),
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


def _loaded_policy_and_catalog(db: Session, workspace_id: str) -> tuple[RoutingPolicy, str, ModelCatalog, ModelCatalogRevision]:
    policy_row = active_policy(db, workspace_id)
    if policy_row is None:
        raise CampusError(ErrorCode.POLICY_NOT_ACTIVE, "Keine aktive Routing-Policy", blocked_action="route.authorize",
                          required_resolution="Policy aktivieren")
    catalog_row = active_catalog(db, workspace_id)
    if catalog_row is None:
        raise CampusError(ErrorCode.CATALOG_NOT_ACTIVE, "Kein aktiver Modellkatalog", blocked_action="route.authorize",
                          required_resolution="Katalog aktivieren", policy_version=policy_row.revision)
    return RoutingPolicy.model_validate(policy_row.document), policy_row.id, ModelCatalog.model_validate(catalog_row.document), catalog_row


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


def _record_applies(rec: QualificationRecord, need: TaskNeed) -> bool:
    """Applicability of a record to a need (chapter 5: qualification is per combination AND per role).

    bootstrap:      human approval of the canonical model for ONE checking role (`reviewer`) and task class; applies only to a need in that
                    role. It never qualifies a producer combination. An undeclared role (pre-0008) applies to nothing.
    combination:    producer evidence; applies only to a producer need with the SAME profile and the SAME skill set. Additional, missing or
                    re-versioned skills are a different combination. It never acts as a reviewer approval.
    legacy_unbound: history only.
    """
    if rec.task_class != need.task_class or not rec.passed:
        return False
    if rec.kind == "bootstrap":
        return need.role == "reviewer" and rec.role == "reviewer"
    if rec.kind != "combination" or rec.role != "producer" or need.role != "producer":
        return False
    if rec.profile_ref is None or rec.skill_refs is None:
        return False  # malformed combination record: never treated as coverage
    return rec.profile_ref == need.agent_profile_version_ref and set(rec.skill_refs) == set(need.skill_version_refs)


def _qualification_evidence(db: Session, workspace_id: str, catalog: ModelCatalog, need: TaskNeed) -> dict[str, list[dict]]:
    """Qualification evidence per deployment from the register: every record of the deployment's canonical model plus the catalog's
    declared refs. Declared refs without a record are reported as unverified (fixture or missing)."""
    canonicals = {dep.canonical_model_id for dep in catalog.deployments if dep.canonical_model_id}
    declared = {r for dep in catalog.deployments for r in dep.qualification_refs}
    rows = db.execute(select(QualificationRecord).where(QualificationRecord.workspace_id == workspace_id,
                                                        (QualificationRecord.canonical_model_id.in_(canonicals) | QualificationRecord.ref.in_(declared))
                                                        if declared else QualificationRecord.canonical_model_id.in_(canonicals))).scalars().all()
    by_ref = {r.ref: r for r in rows}
    by_canonical: dict[str, list[QualificationRecord]] = {}
    for r in rows:
        by_canonical.setdefault(r.canonical_model_id, []).append(r)
    out: dict[str, list[dict]] = {}
    for dep in catalog.deployments:
        items = []
        seen = set()
        for rec in by_canonical.get(dep.canonical_model_id or "", []) + [by_ref[r] for r in dep.qualification_refs if r in by_ref]:
            if rec.ref in seen:
                continue
            seen.add(rec.ref)
            items.append({"ref": rec.ref, "verified": True, "kind": rec.kind, "role": rec.role, "task_class": rec.task_class,
                          "applies_to_task": rec.task_class == need.task_class,
                          "applies_to_combination": _record_applies(rec, need) and rec.canonical_model_id == dep.canonical_model_id,
                          "profile_ref": rec.profile_ref, "skill_refs": rec.skill_refs, "suite_ref": rec.suite_ref,
                          "result_summary": rec.result_summary, "passed": rec.passed, "evaluated_by_kind": rec.evaluated_by_kind,
                          "canonical_matches": rec.canonical_model_id == dep.canonical_model_id})
        for ref in dep.qualification_refs:
            if ref not in by_ref:
                items.append({"ref": ref, "verified": False, "note": "Referenz ohne registrierten Nachweis (Fixture oder fehlend)"})
        out[dep.id] = items
    return out


def _qualified_deployments(catalog: ModelCatalog, evidence: dict[str, list[dict]]) -> set[str]:
    """Deployments qualified for the task's combination according to the register. Fixture catalogs (no competence claimed)
    additionally accept their declared refs so that mock pipelines keep working; runtime catalogs trust the register only."""
    qualified = {dep.id for dep in catalog.deployments if any(e.get("applies_to_combination") for e in evidence.get(dep.id, []))}
    if catalog.purpose != "runtime_snapshot":
        qualified |= {dep.id for dep in catalog.deployments if dep.qualification_refs}
    return qualified


def _catalog_with_register_evidence(catalog: ModelCatalog, evidence: dict[str, list[dict]]) -> ModelCatalog:
    """Routing view of the catalog: each deployment's evidence refs are the register records that apply to this task's combination,
    plus the declared refs. Proposals may cite exactly these; anything else is an invented evidence reference (A02)."""
    deployments = []
    for dep in catalog.deployments:
        refs = [e["ref"] for e in evidence.get(dep.id, []) if e.get("applies_to_combination")]
        if catalog.purpose != "runtime_snapshot":
            refs += list(dep.qualification_refs)
        deployments.append(dep.model_copy(update={"qualification_refs": sorted(set(refs))}))
    return catalog.model_copy(update={"deployments": deployments})


def _task_class_exclusions(catalog: ModelCatalog, evidence: dict[str, list[dict]], need: TaskNeed) -> dict[str, str]:
    """Runtime catalogs, qualified tasks: the deployment needs a passed record for THIS combination (profile, skills, model, task class)."""
    if catalog.purpose != "runtime_snapshot" or need.qualification_mode != "qualified":
        return {}
    excluded = {}
    for dep in catalog.deployments:
        if not dep.enabled:
            continue
        if not any(e.get("applies_to_combination") for e in evidence.get(dep.id, [])):
            excluded[dep.id] = R_QUAL_TASK_CLASS + need.task_class
    return excluded


HARNESS_REF = f"{HARNESS_PROFILE['id']}@{HARNESS_PROFILE['version']}"


def load_composition(db: Session, workspace_id: str, need: TaskNeed, mandate_ref: str | None) -> Composition:
    """The one composition used for evaluation and execution alike: registered profile document + registered skills + task.
    A referenced skill (or profile) that is not registered stops the build; nothing is silently left out."""
    if need.agent_profile_version_ref == HARNESS_REF:
        profile_doc = HARNESS_PROFILE
    else:
        agent = find_agent(db, workspace_id, need.agent_profile_version_ref)
        if agent is None:
            raise CampusError(ErrorCode.AGENT_NOT_REGISTERED, f"Profil {need.agent_profile_version_ref} ist nicht registriert; kein Aufbau",
                              blocked_action="composition.build")
        profile_doc = agent.document
    skills: list[SkillInput] = []
    missing: list[str] = []
    for ref in need.skill_version_refs:
        row = db.execute(select(SkillVersion).where(SkillVersion.workspace_id == workspace_id, SkillVersion.ref == ref)).scalar_one_or_none()
        if row is None:
            missing.append(ref)
            continue
        skills.append(SkillInput(ref=row.ref, purpose=row.document["purpose"], limitations=tuple(row.document.get("limitations", [])),
                                 instructions=row.instructions))
    if missing:
        raise CampusError(ErrorCode.VALIDATION_ERROR, f"Referenzierte Skills sind nicht registriert: {missing}; Aufbau gestoppt, kein Modellaufruf",
                          blocked_action="composition.build", required_resolution="Skillfassungen registrieren oder Aufgabe ohne sie planen")
    return compose(profile_doc, skills, task_class=need.task_class, data_labels=list(need.data_labels), mandate_ref=mandate_ref)


def _record_rejection(db: Session, actor: Actor, task: Task, proposal: RouteProposal, assessments: list[dict], err: CampusError,
                      correlation_id: str) -> None:
    db.add(RouteProposalRecord(id=new_id("RP"), workspace_id=actor.workspace_id, task_id=task.id,
                               proposal=proposal.model_dump(), assessments=assessments, outcome="rejected", error_code=err.code.value))
    record(db, actor=actor, command="route.authorize", object_type="task", object_id=task.id, object_version=str(task.version),
           correlation_id=correlation_id, policy_decision=f"blocked:{err.code.value}", project_id=task.project_id,
           payload={"selected_deployment_id": proposal.selected_deployment_id, "message": err.message})
    db.commit()
    err.details["audited"] = True


def _settle_from_usage(db: Session, reservation: Reservation, pricing: Pricing | None, usage: Usage | None) -> Money | None:
    actual: Money | None = None
    if usage is not None and usage.known and pricing is not None:
        actual = estimate_cost(pricing, usage.input_tokens or 0, usage.output_tokens or 0)
    budget_service.settle(db, reservation_id=reservation.id, actual=actual)
    return actual


def _reservation_for(db: Session, decision_id: str, purpose: str) -> Reservation | None:
    return db.execute(select(Reservation).where(Reservation.routing_decision_id == decision_id, Reservation.purpose == purpose,
                                                Reservation.state == "reserved")).scalars().first()


def _selector_proposal(db: Session, *, actor: Actor, ws: Workspace, mandate: MandateVersion, task: Task, need: TaskNeed, policy: RoutingPolicy,
                       catalog: ModelCatalog, catalog_revision: str, excluded: dict[str, str], evidence: dict[str, list[dict]],
                       correlation_id: str, qualified: set[str] | None = None) -> tuple[RouteProposal, str]:
    """Chapter 10: the core picks the selector from the bootstrap pool in fixed order; the selector proposes among eligible
    candidates from permitted summaries, evidence and cost estimates. One selector call per attempt; no recursion."""
    assessments = routing.assess_candidates(need, catalog, policy, excluded, qualified)
    eligible = [a for a in assessments if a.eligible]
    if not eligible:
        raise CampusError(ErrorCode.NO_ELIGIBLE_MODEL, "Kein Deployment erfüllt die harten Regeln", blocked_action="route.propose",
                          policy_version=policy.revision, details={"assessments": [a.model_dump() for a in assessments]})
    selector_need = need.model_copy(update={
        "role": "selector", "task_class": SELECTOR_TASK_CLASS, "data_labels": ["public"],
        "required_features": ["text_input", "structured_output"], "context_tokens_estimate": 3000, "max_output_tokens": 800,
        "required_tool_scopes": [], "substantive_contributor_model_ids": [],
    })
    selector = None
    for dep_id in policy.bootstrap_selector_deployment_ids:  # fixed, logged order
        dep = catalog.deployment(dep_id)
        if dep is not None and not routing.exclusion_reasons(selector_need, dep, policy, excluded):
            selector = dep
            break
    if selector is None:
        raise CampusError(ErrorCode.NO_ELIGIBLE_SELECTOR, "Kein Selector aus dem Bootstrap-Pool ist zulässig; die Aufgabe wartet (Kap. 10)",
                          blocked_action="route.propose", policy_version=policy.revision,
                          required_resolution="bootstrap_selector_deployment_ids mit qualifizierten, zugelassenen Deployments belegen")
    selector_pricing = _pricing(db, actor.workspace_id, selector.pricing_ref)
    cost = estimate_cost(selector_pricing, selector_need.context_tokens_estimate, selector_need.max_output_tokens)
    decision_id = new_id("RD")
    reservation = budget_service.reserve(db, account_id=task.budget_account_id, amount=cost, task_id=task.id, purpose="selector",
                                         routing_decision_id=decision_id)
    selector_route = routing.ValidatedRoute(
        selected=selector, reviewer=None, assessments=routing.assess_candidates(selector_need, catalog, policy, excluded),
        proposal=RouteProposal(task_id=task.id, selected_deployment_id=selector.id,
                               rationale="Kontrollkern: erster zulässiger Selector in der festen Reihenfolge des Bootstrap-Pools.",
                               evidence_refs=list(selector.qualification_refs), alternatives=[], uncertainties=[]),
        required_review=False,
    )
    selector_decision = routing.build_decision(
        selector_route, decision_id=decision_id, catalog_revision=catalog_revision, routing_policy_ref=policy.revision,
        selection_mode="deterministic_fallback", selector_model_ref=None, reservation_ref=reservation.id,
        pricing_snapshot_ref=selector.pricing_ref or "-", created_at=utcnow().isoformat())
    db.add(RoutingDecisionRecord(id=selector_decision.id, workspace_id=actor.workspace_id, task_id=task.id,
                                 document=selector_decision.model_dump(), state=selector_decision.state))
    db.flush()
    check_invocation(InvocationContext(purpose="selector", workspace_stopped=bool(ws.stopped), mandate_state=MandateState(mandate.state),
                                       task_hold_reason=task.hold_reason, route_state=selector_decision.state, reservation_present=True,
                                       policy_revision=policy.revision))
    record(db, actor=actor, command="route.select", object_type="routing_decision", object_id=selector_decision.id, object_version=None,
           correlation_id=correlation_id, project_id=task.project_id, event_type="SelectorRouted",
           payload={"task_id": task.id, "selector_deployment_id": selector.id, "reservation_id": reservation.id})
    db.commit()  # call order and reservation are durable before anything leaves the campus (S1.1 finding 2)
    candidates = []
    for a in eligible:
        dep = catalog.deployment(a.deployment_id)
        try:
            est = estimate_cost(_pricing(db, actor.workspace_id, dep.pricing_ref), need.context_tokens_estimate, need.max_output_tokens)
            cost_text = est.format()
        except CampusError:
            cost_text = "offen"
        candidates.append({"deployment_id": a.deployment_id, "provider_id": dep.provider_id, "canonical_model_id": dep.canonical_model_id,
                           "capabilities": dep.capabilities.model_dump(), "evidence_refs": a.evidence_refs,
                           "qualification_evidence": evidence.get(dep.id, []), "estimated_generation_cost": cost_text})
    evidence_basis = "verified_for_combination" if any(e.get("applies_to_combination") for c in candidates
                                                        for e in c["qualification_evidence"]) else "unverified_or_fixture"
    system_rules = (
        "Du bist der Routing-Agent eines Forschungscampus. Wähle genau einen Kandidaten aus der Liste zulässiger Deployments. "
        "Nenne nur evidence_refs, die beim gewählten Kandidaten aufgeführt sind. Begründe anhand der mitgelieferten "
        "Eignungsnachweise (Aufgabenklasse, Ergebnis, Grenzen) und Kostenschätzungen. Fehlen vergleichende Nachweise, sage das "
        "ausdrücklich und behaupte keine nachgewiesene Überlegenheit. Nenne Alternativen mit Grund und offene Unsicherheiten. "
        "Du entscheidest nichts über die Zulässigkeit; diese prüft der Kontrollkern. Antworte ausschliesslich im vorgegebenen JSON-Format."
    )
    payload = {
        "task": {"task_id": task.id, "task_class": need.task_class, "role": need.role, "language": need.language,
                 "data_labels": need.data_labels, "required_features": need.required_features,
                 "context_tokens_estimate": need.context_tokens_estimate, "max_output_tokens": need.max_output_tokens,
                 "qualification_mode": need.qualification_mode},
        "eligible_candidates": candidates,
        "evidence_basis": evidence_basis,
        "selection_criteria": policy.selection_criteria,
    }
    request = LLMRequest(
        invocation_id=f"{task.id}:selector:{selector_decision.id}", deployment_id=selector.id, system_rules=system_rules,
        content_blocks=[{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=1), "data_label": "public",
                         "origin": "core_summary"}],
        output_contract=provider_schema("route_proposal"), max_output_tokens=selector_need.max_output_tokens,
        metadata={"task_id": task.id, "candidates": candidates, "routing_decision_id": selector_decision.id},
    )
    try:
        response = _gateway(db, actor.workspace_id).invoke(request, selector, selector_decision)
    except CampusError as err:
        _settle_after_failure(db, reservation, selector_pricing, err)
        db.commit()
        raise
    _settle_from_usage(db, reservation, selector_pricing, response.usage)
    db.commit()  # the consumed cost survives whatever the content check below says
    try:
        proposal = RouteProposal.model_validate(json.loads(response.text()))
    except Exception as exc:  # the model's answer violates the contract: recorded as PROPOSAL_INVALID, no second call
        raise CampusError(ErrorCode.PROPOSAL_INVALID, f"Selector-Antwort verletzt den Vertrag route_proposal: {type(exc).__name__}",
                          blocked_action="route.propose", policy_version=policy.revision,
                          details={"assessments": [a.model_dump() for a in assessments]}) from exc
    return proposal, selector.canonical_model_id or selector.id


def _settle_after_failure(db: Session, reservation: Reservation, pricing: Pricing | None, err: CampusError) -> None:
    """After a failed call: usage known -> settle it; unknown outcome or answer received -> keep bound; failed before send -> release."""
    usage = err.details.get("usage") if err.details else None
    if usage and usage.get("input_tokens") is not None:
        _settle_from_usage(db, reservation, pricing, Usage(usage["input_tokens"], usage["output_tokens"]))
    elif err.code in {ErrorCode.OUTCOME_UNKNOWN, ErrorCode.PROTECTED_REFUSAL, ErrorCode.PROVIDER_REJECTED}:
        budget_service.settle(db, reservation_id=reservation.id, actual=None)  # cannot claim "no cost": stays open_unknown
    elif err.retryable:
        reservation.state = "released"  # nothing was sent
    else:
        budget_service.settle(db, reservation_id=reservation.id, actual=None)
    db.flush()


def authorize_route(db: Session, *, actor: Actor, task_id: str, proposal: dict | None, selection_mode: str,
                    selector_model_ref: str | None, correlation_id: str) -> RoutingDecisionRecord:
    """Chapter 9 steps 1–7. Rejected proposals are recorded (A02); failed deployments are excluded on replan; replans are
    counted; a held task gets no route; qualification evidence is bound to the task class."""
    authorize("route.authorize", actor.roles)
    task = _own(db, Task, task_id, actor, "Aufgabe")
    need = TaskNeed.model_validate(task.task_need)
    if task.hold_reason:
        raise CampusError(ErrorCode.TASK_HELD, f"Aufgabe ist gesperrt: {task.hold_reason}", blocked_action="route.authorize",
                          required_resolution="Sperre durch Forschungsleitung oder Integritätsfunktion aufheben")
    ws = db.get(Workspace, actor.workspace_id)
    project = db.get(Project, task.project_id)
    mandate = db.get(MandateVersion, project.mandate_version_id)
    policy, policy_id, catalog, catalog_row = _loaded_policy_and_catalog(db, actor.workspace_id)
    if need.routing_policy_ref != policy.revision:
        raise CampusError(ErrorCode.POLICY_NOT_ACTIVE, f"Aufgabe ist an Policy {need.routing_policy_ref} gebunden; aktiv ist {policy.revision}",
                          blocked_action="route.authorize", policy_version=policy.revision,
                          required_resolution="Aufgabe neu planen (neue Fassung) oder die gebundene Policy aktivieren")
    evidence = _qualification_evidence(db, actor.workspace_id, catalog, need)
    qualified = _qualified_deployments(catalog, evidence)
    # the reviewer pool is judged separately: reviewer approvals, not producer combinations (Art. 17, 27)
    review_need = need.model_copy(update={"role": "reviewer"})
    qualified_reviewers = _qualified_deployments(catalog, _qualification_evidence(db, actor.workspace_id, catalog, review_need))
    failed = _failed_deployments(db, task)
    excluded = failed | _task_class_exclusions(catalog, evidence, need)  # the producer's missing combination does not concern the reviewer
    catalog = _catalog_with_register_evidence(catalog, evidence)  # evidence refs a proposal may cite come from the register
    prior = db.execute(select(func.count()).select_from(RoutingDecisionRecord).where(
        RoutingDecisionRecord.task_id == task.id, RoutingDecisionRecord.document["required_review"].astext == "true")).scalar() or 0
    limit = policy.limits.max_route_replans_per_task
    if failed and limit is not None and prior > limit:
        raise CampusError(ErrorCode.REPLAN_LIMIT_REACHED, f"Neuplanungen erschöpft ({prior - 1} von {limit}); Aufgabe wartet auf Entscheid",
                          blocked_action="route.authorize", policy_version=policy.revision,
                          required_resolution="Menschlicher Entscheid: Aufgabe anpassen, Katalog erweitern oder abbrechen")
    selector_used: str | None = selector_model_ref
    if proposal is None and selection_mode == "llm":
        route_proposal, selector_used = _selector_proposal(db, actor=actor, ws=ws, mandate=mandate, task=task, need=need, policy=policy,
                                                           catalog=catalog, catalog_revision=catalog_row.revision, excluded=excluded,
                                                           evidence=evidence, correlation_id=correlation_id, qualified=qualified)
    elif proposal is None:
        route_proposal = routing.deterministic_proposal(need, catalog, policy, excluded, qualified)
        selection_mode = "deterministic_fallback"
    else:
        route_proposal = RouteProposal.model_validate(proposal)
    try:
        route = routing.validate_proposal(need, route_proposal, catalog, policy, excluded, qualified, qualified_reviewers, reviewer_excluded=failed)
    except CampusError as err:
        assessments = err.details.get("assessments") or [a.model_dump() for a in routing.assess_candidates(need, catalog, policy, excluded, qualified)]
        _record_rejection(db, actor, task, route_proposal, assessments, err, correlation_id)
        raise
    decision_id = new_id("RD")
    generation_cost = estimate_cost(_pricing(db, actor.workspace_id, route.selected.pricing_ref), need.context_tokens_estimate, need.max_output_tokens)
    reservations: list[Reservation] = [budget_service.reserve(db, account_id=task.budget_account_id, amount=generation_cost, task_id=task.id,
                                                              purpose="generation", routing_decision_id=decision_id)]
    if route.reviewer is not None:
        review_cost = estimate_cost(_pricing(db, actor.workspace_id, route.reviewer.pricing_ref),
                                    need.context_tokens_estimate + need.max_output_tokens, need.max_output_tokens)
        reservations.append(budget_service.reserve(db, account_id=task.budget_account_id, amount=review_cost, task_id=task.id,
                                                   purpose="review", routing_decision_id=decision_id))
    for a in route.assessments:  # the evidence basis is visible in the decision record, not hidden behind a rationale
        ev = evidence.get(a.deployment_id, [])
        if a.eligible and not any(e.get("applies_to_combination") for e in ev):
            a.note = a.note + " Eignung für diese Kombination (Profil, Skills, Modell, Aufgabenklasse) nicht durch registrierten Nachweis belegt."
    decision = routing.build_decision(
        route, decision_id=decision_id, catalog_revision=catalog_row.revision, routing_policy_ref=policy.revision,
        selection_mode=selection_mode, selector_model_ref=selector_used, reservation_ref=reservations[0].id,
        pricing_snapshot_ref=route.selected.pricing_ref or "-", created_at=utcnow().isoformat(),
    )
    row = RoutingDecisionRecord(id=decision.id, workspace_id=actor.workspace_id, task_id=task.id, document=decision.model_dump(), state=decision.state)
    db.add(row)
    db.add(RouteProposalRecord(id=new_id("RP"), workspace_id=actor.workspace_id, task_id=task.id, proposal=route_proposal.model_dump(),
                               assessments=[a.model_dump() for a in route.assessments], outcome="authorized", routing_decision_id=decision.id))
    db.flush()
    record(db, actor=actor, command="route.authorize", object_type="routing_decision", object_id=decision.id, object_version=None,
           correlation_id=correlation_id, project_id=task.project_id, event_type="RoutingDecided",
           payload={"task_id": task.id, "selected_deployment_id": decision.selected_deployment_id, "review_deployment_id": decision.review_deployment_id,
                    "reservation_ids": [r.id for r in reservations], "policy_id": policy_id, "selection_mode": selection_mode, "excluded": excluded,
                    "catalog_revision": catalog_row.revision})
    return row


# ---------------------------------------------------------------- runs

def _end_reason_for(err: CampusError) -> RunEndReason:
    return {ErrorCode.OUTCOME_UNKNOWN: RunEndReason.OUTCOME_UNKNOWN, ErrorCode.PROTECTED_REFUSAL: RunEndReason.PROTECTED_REFUSAL}.get(
        err.code, RunEndReason.PROVIDER_ERROR)


def _hold_task(db: Session, task: Task, reason: str) -> None:
    task.hold_reason = reason
    task.hold_since = utcnow()


def start_run(db: Session, *, actor: Actor, task_id: str, routing_decision_id: str, correlation_id: str) -> dict:
    """Guard, persist the call order, execute one generation call through the authorized route, settle its cost, freeze the output
    as an artifact and record the declared claims (S2). Synchronous; the workflow layer sequences it (chapter 6)."""
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
    generation = _reservation_for(db, decision.id, "generation") if decision else None
    review_res = _reservation_for(db, decision.id, "review") if decision else None
    ctx = RunStartContext(
        workspace_stopped=bool(ws.stopped), mandate_state=MandateState(mandate.state),
        policy_state=ActivationState(policy_row.state) if policy_row else None, policy_revision=policy_row.revision if policy_row else None,
        missing_limits=policy.limits.missing() if policy else [], catalog_state=ActivationState(catalog_row.state) if catalog_row else None,
        agent_registered=agent is not None, agent_operating_status=OperatingStatus(agent.operating_status) if agent else None,
        agent_qualification_status=QualificationStatus(agent.qualification_status) if agent else None,
        task_qualification_mode=need.qualification_mode, route_state=decision.state if decision else None,
        reservation_present=generation is not None, review_required=bool(decision.required_review) if decision else True,
        review_reservation_present=review_res is not None, task_hold_reason=task.hold_reason,
        catalog_revision_matches=bool(decision and catalog_row and decision.catalog_revision == catalog_row.revision),
    )
    check_run_start(ctx)
    assert decision is not None and agent is not None and policy is not None and generation is not None
    composition = load_composition(db, actor.workspace_id, need, need.mandate_version_ref)  # stops before any call order if a skill is missing
    attempt_no = (db.execute(select(func.max(RunAttempt.attempt_no)).where(RunAttempt.task_id == task.id)).scalar() or 0) + 1
    attempt = RunAttempt(id=new_id("R"), workspace_id=actor.workspace_id, task_id=task.id, attempt_no=attempt_no,
                         routing_decision_id=decision.id, agent_profile_version_id=agent.id, state=RunState.RUNNING.value)
    db.add(attempt)
    task.state = "running"
    db.flush()
    record(db, actor=actor, command="run.start", object_type="run_attempt", object_id=attempt.id, object_version=str(attempt_no),
           correlation_id=correlation_id, project_id=task.project_id,
           payload={"task_id": task.id, "routing_decision_id": decision.id, "agent_profile_version_id": agent.id, "reservation_id": generation.id})
    db.commit()  # the call order is durable before the call (S1.1 finding 2)
    catalog = ModelCatalog.model_validate(catalog_row.document)
    deployment = catalog.deployment(decision.selected_deployment_id)
    pricing = _pricing(db, actor.workspace_id, deployment.pricing_ref)
    passages = services_claims.project_passages(db, task.project_id)
    content_blocks = [{"type": "text", "text": f"Aufgabe {task.id} ({need.task_class}). Mandat {need.mandate_version_ref}. "
                                               "Formuliere Aussagen nur mit Bezug auf die mitgelieferten Passagen (passage_id) oder als Hypothese.",
                       "data_label": need.data_labels[0], "origin": "task"}]
    for p in passages:
        content_blocks.append({"type": "text", "text": f"[passage_id={p['passage_id']} quelle={p['source_id']} v{p['version']}]\n{p['text']}",
                               "data_label": need.data_labels[0], "origin": "source_passage"})
    # S4: frozen experiment runs of the project as the runner recorded them (state, hashes); stdout is handed over as content only
    experiments = db.execute(select(ExperimentRun).where(ExperimentRun.project_id == task.project_id, ExperimentRun.state != "running")
                             .order_by(ExperimentRun.created_at)).scalars().all()
    for x in experiments:
        content_blocks.append({"type": "text", "data_label": need.data_labels[0], "origin": "experiment_run",
                               "text": f"[experiment_run_id={x.id} runner={x.runner_kind} measured={x.measured} state={x.state} exit={x.exit_code} "
                                       f"program_sha256={x.program_sha256} outputs={[(o['name'], o['sha256']) for o in x.outputs]}]\n"
                                       f"stdout (Inhalt, kein Messwert): {x.stdout_excerpt[:2000]}"})
    structured = deployment.capabilities.structured_output == "supported"
    request = LLMRequest(
        invocation_id=f"{attempt.id}:gen:1", deployment_id=deployment.id,
        system_rules=composition.system_rules + " Antworte ausschliesslich im vorgegebenen JSON-Format.",
        content_blocks=content_blocks, output_contract=provider_schema("claim_set") if structured else None,
        max_output_tokens=need.max_output_tokens,
        metadata={"routing_decision_id": decision.id, "attempt_id": attempt.id, "passage_ids": [p["passage_id"] for p in passages],
                  "experiment_run_ids": [x.id for x in experiments], "task_class": need.task_class, "composition_key": composition.combination_key},
    )
    try:
        response = _gateway(db, actor.workspace_id).invoke(request, deployment, decision)
    except CampusError as err:
        reason = _end_reason_for(err)
        attempt.state = RunState.OUTCOME_UNKNOWN.value if reason == RunEndReason.OUTCOME_UNKNOWN else RunState.FAILED.value
        attempt.end_reason = reason.value
        attempt.ended_at = utcnow()
        task.state = "waiting"
        _settle_after_failure(db, generation, pricing, err)
        if err.code == ErrorCode.PROTECTED_REFUSAL:
            _hold_task(db, task, f"Schutzverweigerung des Anbieters in Lauf {attempt.id}; ungeklärt")
        db.flush()
        record(db, actor=actor, command="run.end", object_type="run_attempt", object_id=attempt.id, object_version=str(attempt_no),
               correlation_id=correlation_id, project_id=task.project_id, event_type="RunEnded",
               payload={"end_reason": attempt.end_reason, "error_code": err.code.value, "retryable": err.retryable, "hold": bool(task.hold_reason)})
        db.commit()
        err.details["audited"] = True
        raise
    actual = _settle_from_usage(db, generation, pricing, response.usage)
    db.commit()  # consumed cost survives any later content failure
    synthetic = any(b.get("synthetic") for b in response.blocks)
    content = {"blocks": response.blocks, "stop_reason": response.stop_reason, "effective_model_id": response.effective_model_id}
    digest = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    artifact = ArtifactVersion(id=new_id("A"), workspace_id=actor.workspace_id, task_id=task.id, run_attempt_id=attempt.id, project_id=task.project_id,
                               kind="generation_output", content=content, sha256=digest,
                               producer_canonical_model_id=decision.selected_canonical_model_id, synthetic=synthetic)
    db.add(artifact)
    db.flush()
    claims_recorded, claim_set_valid = 0, None
    if structured:
        try:
            claim_set = ClaimSet.model_validate(json.loads(response.text()))
            claim_set_valid = True
        except Exception as exc:  # kept as artifact, no claims, flagged in the audit
            claim_set_valid = False
            record(db, actor=actor, command="claim.record", object_type="run_attempt", object_id=attempt.id, object_version=None,
                   correlation_id=correlation_id, policy_decision="blocked:PROPOSAL_INVALID", project_id=task.project_id,
                   payload={"message": f"claim_set ungültig: {type(exc).__name__}"})
        else:
            claims_recorded = len(services_claims.record_claim_set(db, actor=actor, task=task, attempt=attempt, artifact=artifact, claim_set=claim_set,
                                                                  producer_canonical_model_id=decision.selected_canonical_model_id,
                                                                  correlation_id=correlation_id))
    attempt.state = RunState.FINISHED.value
    attempt.end_reason = RunEndReason.RESULT.value
    attempt.ended_at = utcnow()
    task.state = "waiting"  # waits for the required review; a finished generation is not a finished task
    db.flush()
    record(db, actor=actor, command="run.end", object_type="run_attempt", object_id=attempt.id, object_version=str(attempt_no),
           correlation_id=correlation_id, project_id=task.project_id, event_type="RunEnded",
           payload={"end_reason": attempt.end_reason, "invocation_id": response.invocation_id, "artifact_version_id": artifact.id,
                    "artifact_sha256": digest, "effective_model_id": response.effective_model_id, "cost_state": "settled" if actual else "open",
                    "synthetic": synthetic, "claims_recorded": claims_recorded})
    return {
        "run_attempt_id": attempt.id, "attempt_no": attempt_no, "state": attempt.state, "end_reason": attempt.end_reason,
        "invocation_id": response.invocation_id, "effective_model_id": response.effective_model_id,
        "usage": {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens},
        "cost": actual.format() if actual else "offen", "review_required": decision.required_review,
        "review_deployment_id": decision.review_deployment_id, "artifact_version_id": artifact.id, "artifact_sha256": digest,
        "synthetic": synthetic, "claims_recorded": claims_recorded, "claim_set_valid": claim_set_valid, "passages_given": len(passages),
    }


def review_consistency(report: ReviewReport) -> tuple[bool, str | None]:
    """Verdict and findings must agree; contradictions are kept as such, never read in favour of the lenient field (S1.1 finding 4)."""
    severe = [f for f in report.findings if f.severity in {"major", "blocking"}]
    if report.verdict == "no_objection" and severe:
        return False, "Urteil no_objection bei gleichzeitig blockierenden oder gewichtigen Einwänden"
    if report.verdict == "objections" and not report.findings:
        return False, "Urteil objections ohne einen einzigen Einwand"
    return True, None


def start_review(db: Session, *, actor: Actor, run_id: str, correlation_id: str) -> dict:
    """Distinct-model first review of the frozen artifact (Art. 17, 27; chapter 11). Same pre-call check as every model call."""
    authorize("review.start", actor.roles)
    attempt = _own(db, RunAttempt, run_id, actor, "Lauf")
    if attempt.state != RunState.FINISHED.value:
        raise CampusError(ErrorCode.INVALID_TRANSITION, f"Lauf ist {attempt.state}; geprüft wird nur ein beendeter Lauf", blocked_action="review.start")
    task = db.get(Task, attempt.task_id)
    need = TaskNeed.model_validate(task.task_need)
    ws = db.get(Workspace, actor.workspace_id)
    project = db.get(Project, task.project_id)
    mandate = db.get(MandateVersion, project.mandate_version_id)
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
    if catalog_row is None or catalog_row.revision != decision.catalog_revision:
        raise CampusError(ErrorCode.ROUTE_NOT_AUTHORIZED, "Die Route wurde gegen eine andere Katalogfassung autorisiert; Prüfung bleibt offen",
                          blocked_action="review.start", required_resolution="Route neu bestimmen (Fassungsbindung)")
    catalog = ModelCatalog.model_validate(catalog_row.document)
    reviewer = catalog.deployment(decision.review_deployment_id)
    if reviewer is None or reviewer.canonical_model_id in {None, artifact.producer_canonical_model_id} | set(need.substantive_contributor_model_ids):
        raise CampusError(ErrorCode.REVIEW_INDEPENDENCE_VIOLATED,
                          "Prüfmodell ist kanonisch nicht vom Erzeuger getrennt oder nicht mehr im Katalog; Prüfung bleibt offen (I04)",
                          blocked_action="review.start", required_resolution="Route neu bestimmen (anderes Prüfmodell)")
    reservation = _reservation_for(db, decision.id, "review")
    check_invocation(InvocationContext(purpose="review", workspace_stopped=bool(ws.stopped), mandate_state=MandateState(mandate.state),
                                       task_hold_reason=task.hold_reason, route_state=decision.state, reservation_present=reservation is not None,
                                       policy_revision=decision.routing_policy_ref))
    assert reservation is not None
    record(db, actor=actor, command="review.start", object_type="run_attempt", object_id=attempt.id, object_version=None,
           correlation_id=correlation_id, project_id=task.project_id, event_type="ReviewStarted",
           payload={"reviewer_deployment_id": reviewer.id, "artifact_sha256": artifact.sha256, "reservation_id": reservation.id})
    db.commit()  # durable call order
    pricing = _pricing(db, actor.workspace_id, reviewer.pricing_ref)
    artifact_text = "\n".join(str(b.get("text", "")) for b in artifact.content["blocks"] if b.get("type") == "text")
    system_rules = (
        "Du bist ein unabhängiger Erstprüfer in einem Forschungscampus. Du erhältst ein eingefrorenes Artefakt und den Aufgabenkontext, "
        "keine Bewertung des Erzeugers. Prüfe Belegbindung, Geltungsbereich, Trennung von Beobachtung und Vermutung und offene "
        "Unsicherheiten. Nenne Einwände mit Gegenstand, Argument, Tragweite und Bezug. Dein Urteil muss zu deinen Einwänden passen: "
        "no_objection nur ohne gewichtige oder blockierende Einwände. Ein Einwand ist kein Beweis; fehlende Prüfbarkeit meldest du als "
        "cannot_assess. Antworte ausschliesslich im vorgegebenen JSON-Format."
    )
    request = LLMRequest(
        invocation_id=f"{attempt.id}:review:1", deployment_id=reviewer.id, system_rules=system_rules,
        content_blocks=[
            {"type": "text", "text": f"Aufgabe: {need.task_class}; Sprache {need.language}; Datenklassen {need.data_labels}",
             "data_label": need.data_labels[0], "origin": "task"},
            {"type": "text", "text": f"Artefakt (sha256 {artifact.sha256}):\n{artifact_text}", "data_label": need.data_labels[0], "origin": "artifact"},
        ],
        output_contract=provider_schema("review_report"), max_output_tokens=need.max_output_tokens,
        metadata={"routing_decision_id": decision.id, "attempt_id": attempt.id, "artifact_sha256": artifact.sha256, "task_class": need.task_class},
    )
    try:
        response = _gateway(db, actor.workspace_id).invoke(request, reviewer, decision)
    except CampusError as err:
        _settle_after_failure(db, reservation, pricing, err)
        if err.code == ErrorCode.PROTECTED_REFUSAL:
            _hold_task(db, task, f"Schutzverweigerung des Prüfmodells für Lauf {attempt.id}; ungeklärt")
        record(db, actor=actor, command="review.start", object_type="run_attempt", object_id=attempt.id, object_version=None,
               correlation_id=correlation_id, policy_decision=f"blocked:{err.code.value}", project_id=task.project_id,
               payload={"message": err.message, "reviewer_deployment_id": reviewer.id})
        db.commit()
        err.details["audited"] = True
        raise
    _settle_from_usage(db, reservation, pricing, response.usage)
    db.commit()  # call and cost evidence survive a failing content check
    try:
        report = ReviewReport.model_validate(json.loads(response.text()))
    except Exception as exc:
        raise CampusError(ErrorCode.PROPOSAL_INVALID, f"Prüfbericht verletzt den Vertrag review_report: {type(exc).__name__}",
                          blocked_action="review.start", details={"invocation_id": response.invocation_id}) from exc
    consistent, inconsistency = review_consistency(report)
    row = ReviewReportRecord(
        id=new_id("RR"), workspace_id=actor.workspace_id, task_id=task.id, run_attempt_id=attempt.id, artifact_version_id=artifact.id,
        artifact_sha256=artifact.sha256, reviewer_deployment_id=reviewer.id, reviewer_canonical_model_id=reviewer.canonical_model_id or "",
        reviewer_effective_model_id=response.effective_model_id, producer_canonical_model_id=artifact.producer_canonical_model_id,
        invocation_id=response.invocation_id, verdict=report.verdict, consistent=consistent, document=report.model_dump(),
    )
    db.add(row)
    db.flush()
    findings = []
    for f in report.findings:
        finding = FindingRecord(id=new_id("F"), workspace_id=actor.workspace_id, review_report_id=row.id, task_id=task.id,
                                subject=f.subject, argument=f.argument, severity=f.severity, scope=f.scope, status="open")
        db.add(finding)
        findings.append(finding)
    blocking_open = any(f.severity == "blocking" for f in findings)
    task.state = "finished" if (report.verdict == "no_objection" and consistent and not blocking_open) else "waiting"
    contested_claims: list[str] = []
    if findings or not consistent:
        # content judgement of the reviewer: the run's claims become "contested" relative to this review; nothing is refuted (Art. 13, 18)
        for claim in db.execute(select(ClaimVersion).where(ClaimVersion.run_attempt_id == attempt.id)).scalars():
            claim.epistemic_status = "contested"
            claim.assessment_ref = f"review:{row.id}"
            contested_claims.append(claim.id)
    db.flush()
    record(db, actor=actor, command="review.start", object_type="review_report", object_id=row.id, object_version=None,
           correlation_id=correlation_id, project_id=task.project_id, event_type="ReviewCompleted",
           payload={"run_attempt_id": attempt.id, "verdict": report.verdict, "consistent": consistent, "inconsistency": inconsistency,
                    "finding_ids": [f.id for f in findings], "reviewer_canonical_model_id": row.reviewer_canonical_model_id,
                    "producer_canonical_model_id": row.producer_canonical_model_id, "artifact_sha256": artifact.sha256,
                    "contested_claim_version_ids": contested_claims})
    if not consistent:
        record(db, actor=actor, command="review.start", object_type="review_report", object_id=row.id, object_version=None,
               correlation_id=correlation_id, project_id=task.project_id, event_type="ReviewInconsistent",
               payload={"inconsistency": inconsistency})
    for finding in findings:
        record(db, actor=actor, command="finding.raise", object_type="finding", object_id=finding.id, object_version=None,
               correlation_id=correlation_id, project_id=task.project_id, event_type="ClaimContested",
               payload={"severity": finding.severity, "review_report_id": row.id})
    return {"review_report_id": row.id, "verdict": report.verdict, "consistent": consistent, "inconsistency": inconsistency,
            "findings": [{"id": f.id, "subject": f.subject, "severity": f.severity} for f in findings],
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


def discover_models(db: Session, *, actor: Actor, deployment_id: str) -> dict:
    """Provider discovery through a REGISTERED catalog deployment: adapter, approved endpoint and secret reference come from the
    catalog, never from the caller (S1.1 finding 5). Lists native ids only; enabling, data authorization and qualification stay separate."""
    authorize("catalog.discover", actor.roles)
    revisions = db.execute(select(ModelCatalogRevision).where(ModelCatalogRevision.workspace_id == actor.workspace_id)
                           .order_by(ModelCatalogRevision.created_at.desc())).scalars().all()
    deployment = None
    for rev in revisions:
        for d in rev.document["deployments"]:
            if d["id"] == deployment_id:
                deployment = ModelCatalog.model_validate(rev.document).deployment(deployment_id)
                break
        if deployment:
            break
    if deployment is None:
        raise CampusError(ErrorCode.NOT_FOUND, "Deployment ist in keiner Katalogfassung dieses Workspace registriert", blocked_action="catalog.discover")
    check_api_base(deployment.adapter, deployment.api_base)
    adapter = build_adapters().get(deployment.adapter)
    if adapter is None or not hasattr(adapter, "list_models"):
        raise CampusError(ErrorCode.ADAPTER_NOT_APPROVED, f"Adapter {deployment.adapter} ist nicht freigegeben oder kennt keine Discovery",
                          blocked_action="catalog.discover")
    try:
        ids = adapter.list_models(deployment.secret_ref, deployment.api_base)
    except Exception as exc:
        raise CampusError(ErrorCode.PROVIDER_REJECTED, f"Discovery fehlgeschlagen: {type(exc).__name__}: {exc}", blocked_action="catalog.discover") from exc
    return {"deployment_id": deployment.id, "adapter": deployment.adapter, "api_base": deployment.api_base, "model_ids": ids,
            "note": "Discovery ist weder Datenfreigabe noch Qualifikation."}
