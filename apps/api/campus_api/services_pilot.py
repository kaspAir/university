"""S5 services: pre-registered pilot protocols, release gate, arm projects and tasks, blinded independent evaluation, report.

Scientific assessment stays separate from platform tests: the report carries both, side by side, never merged into one score.
External release of the report stays a human decision (existing approval and release path, T08).
"""
from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from campus_contracts.models import EvaluationVerdict, RouteProposal, TaskNeed
from campus_contracts.validation import provider_schema
from campus_domain import routing
from campus_domain.authz import Role, authorize
from campus_domain.budget import estimate_cost
from campus_domain.errors import CampusError, ErrorCode
from campus_domain.guards import InvocationContext, check_invocation
from campus_domain.mandate import Condition, HumanDecisionInput, validate_decision
from campus_domain.money import parse_money
from campus_domain.pilot import (
    CONDITIONS,
    ArmFacts,
    ReleaseContext,
    arm_differences,
    blinding_violations,
    check_release,
    parse_junit,
    preregistration_sha256,
    report_flags,
    rule_outcome,
    self_control,
    validate_protocol,
)
from campus_domain.qualification import CaseResult, aggregate
from campus_domain.statuses import DecisionOption, MandateState, RunState
from campus_gateway.port import LLMRequest
from campus_infra import budget_service
from campus_infra.audit import Actor, record
from campus_infra.ids import new_id, utcnow
from campus_infra.models import (
    ArtifactVersion,
    AuditEvent,
    BudgetAccount,
    ClaimVersion,
    ExperimentRun,
    FindingRecord,
    Grant,
    HumanDecision,
    Institute,
    LLMInvocation,
    MandateVersion,
    PilotArm,
    PilotArmProject,
    PilotArmTask,
    PilotEvaluation,
    PilotStudy,
    PlatformEvidence,
    Project,
    QualificationRecord,
    QualificationSuite,
    Reservation,
    ReviewReportRecord,
    RoutingDecisionRecord,
    RunAttempt,
    Task,
    Workspace,
)
from campus_infra.vault import CaseVault

from . import services_research
from .services_research import _gateway, _loaded_policy_and_catalog, _own, _pricing, _settle_after_failure, _settle_from_usage

PILOT_REPORT_KIND = "pilot_report"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- platform evidence

def import_evidence(db: Session, *, actor: Actor, build_sha: str, source: str, junit_xml: str, correlation_id: str) -> PlatformEvidence:
    """The operator imports a JUnit report of ONE build. It is evidence about the platform, never about research content."""
    authorize("evidence.import", actor.roles)
    if not (7 <= len(build_sha) <= 64) or any(c not in "0123456789abcdef" for c in build_sha.lower()):
        raise CampusError(ErrorCode.VALIDATION_ERROR, "build_sha muss ein Git-Commit (hex) sein")
    summary = parse_junit(junit_xml)
    row = PlatformEvidence(id=new_id("PE"), workspace_id=actor.workspace_id, build_sha=build_sha.lower(), source=source,
                           summary={"total": summary.total, "passed": summary.passed, "failed": summary.failed, "skipped": summary.skipped},
                           covered_ids=list(summary.covered_ids), failed_ids=list(summary.failed_ids), report_sha256=summary.report_sha256,
                           imported_by=actor.actor_id, created_at=utcnow())
    db.add(row)
    db.flush()
    record(db, actor=actor, command="evidence.import", object_type="platform_evidence", object_id=row.id, object_version=None, correlation_id=correlation_id,
           event_type="PlatformEvidenceImported", payload={"build_sha": row.build_sha, **row.summary, "covered_ids": row.covered_ids, "failed_ids": row.failed_ids})
    return row


def evidence_view(row: PlatformEvidence) -> dict:
    return {"evidence_id": row.id, "build_sha": row.build_sha, "source": row.source, **row.summary, "covered_ids": row.covered_ids,
            "failed_ids": row.failed_ids, "report_sha256": row.report_sha256, "imported_by": row.imported_by, "created_at": row.created_at}


def _latest_evidence(db: Session, workspace_id: str, build_sha: str | None) -> PlatformEvidence | None:
    stmt = select(PlatformEvidence).where(PlatformEvidence.workspace_id == workspace_id)
    if build_sha:
        stmt = stmt.where(PlatformEvidence.build_sha == build_sha.lower())
    return db.execute(stmt.order_by(PlatformEvidence.created_at.desc())).scalars().first()


# ---------------------------------------------------------------- protocol

def create_pilot(db: Session, *, actor: Actor, mandate_version_id: str, ref: str, protocol: dict, correlation_id: str) -> PilotStudy:
    """A new pre-registered protocol version. Changing a protocol is creating a new version; nothing is edited in place."""
    authorize("pilot.create", actor.roles)
    problems = validate_protocol(protocol)
    if problems:
        raise CampusError(ErrorCode.VALIDATION_ERROR, "Protokoll unvollständig: " + "; ".join(problems), blocked_action="pilot.create",
                          details={"problems": problems})
    mandate = _own(db, MandateVersion, mandate_version_id, actor, "Mandatsfassung")
    if mandate.state != MandateState.APPROVED.value:
        raise CampusError(ErrorCode.MANDATE_NOT_APPROVED, "Pilot braucht ein freigegebenes Mandat", blocked_action="pilot.create")
    if protocol["stage"] == "follow_up":
        pred = db.get(PilotStudy, protocol["predecessor_pilot_id"])
        if pred is None or pred.workspace_id != actor.workspace_id or pred.state != "closed" or pred.protocol.get("stage") != "reproduction":
            raise CampusError(ErrorCode.VALIDATION_ERROR, "Eine Anschlussfrage setzt eine abgeschlossene Reproduktion voraus (Anhang C, Stufe 1 vor Stufe 2)",
                              blocked_action="pilot.create")
    suite = db.execute(select(QualificationSuite).where(QualificationSuite.workspace_id == actor.workspace_id,
                                                        QualificationSuite.ref == protocol["evaluation"]["suite_ref"])
                       .order_by(QualificationSuite.version.desc())).scalars().first()
    root = db.execute(select(BudgetAccount).where(BudgetAccount.mandate_version_id == mandate.id, BudgetAccount.parent_id.is_(None))).scalar_one()
    last = db.execute(select(PilotStudy).where(PilotStudy.workspace_id == actor.workspace_id, PilotStudy.ref == ref)
                      .order_by(PilotStudy.version.desc())).scalars().first()
    version = (last.version + 1) if last else 1
    account = budget_service.create_child(db, parent_id=root.id, name=f"Pilot {ref} v{version} (gemeinsames Budget)",
                                          limit=parse_money(protocol["budget"]["total"], root.currency))
    meta = db.execute(select(Institute).where(Institute.workspace_id == actor.workspace_id, Institute.id.like("%:meta"))).scalars().first()
    if meta is None:
        raise CampusError(ErrorCode.VALIDATION_ERROR, "Institut Metawissenschaft fehlt; die unabhängige Bewertung braucht es als Träger")
    eval_project = services_research.create_project(db, actor=actor, mandate_version_id=mandate.id, institute_id=meta.id,
                                                    title=f"Pilot {ref} v{version}: unabhängige Bewertung", budget=protocol["budget"]["evaluation_reserve"],
                                                    correlation_id=correlation_id, budget_parent_id=account.id)
    row = PilotStudy(id=new_id("PS"), workspace_id=actor.workspace_id, mandate_version_id=mandate.id, ref=ref, version=version, state="draft",
                     protocol=protocol, preregistration_sha256=preregistration_sha256(protocol), evaluation_suite_id=suite.id if suite else None,
                     budget_account_id=account.id, evaluation_project_id=eval_project.id, created_by=actor.actor_id, created_at=utcnow())
    db.add(row)
    db.flush()
    for condition in CONDITIONS:
        db.add(PilotArm(id=new_id("PA"), pilot_id=row.id, condition=condition))
    db.flush()
    record(db, actor=actor, command="pilot.create", object_type="pilot_study", object_id=row.id, object_version=str(version), correlation_id=correlation_id,
           event_type="PilotProtocolRegistered", payload={"ref": ref, "preregistration_sha256": row.preregistration_sha256, "stage": protocol["stage"],
                                                          "repetitions": protocol["repetitions"], "budget_account_id": account.id})
    return row


def _roles(db: Session, workspace_id: str, role: Role) -> frozenset[str]:
    return frozenset(db.execute(select(Grant.user_id).where(Grant.workspace_id == workspace_id, Grant.role == role.value)).scalars().all())


def release_pilot(db: Session, *, actor: Actor, pilot_id: str, current_build_sha: str | None, option: str, rationale: str, alternatives_presented: list[str],
                  remaining_objections: list[str], consequences: str, conditions: list[dict], correlation_id: str) -> PilotStudy:
    """Reserved human decision (Art. 11, Annex F): the gate collects every failure; accept/accept_limited freezes the protocol hash."""
    authorize("pilot.release", actor.roles)
    pilot = _own(db, PilotStudy, pilot_id, actor, "Pilot")
    if pilot.state != "draft":
        raise CampusError(ErrorCode.INVALID_TRANSITION, f"Pilot ist im Zustand {pilot.state}; nur Entwürfe werden freigegeben")
    opt = DecisionOption(option)
    validate_decision(HumanDecisionInput(option=opt, rationale=rationale, alternatives_presented=alternatives_presented,
                                         remaining_objections=remaining_objections, consequences=consequences, conditions=[Condition(**c) for c in conditions]))
    mandate = db.get(MandateVersion, pilot.mandate_version_id)
    policy, _pid, _catalog, _crow = _loaded_policy_and_catalog(db, actor.workspace_id)
    suite = db.get(QualificationSuite, pilot.evaluation_suite_id) if pilot.evaluation_suite_id else None
    evidence = _latest_evidence(db, actor.workspace_id, current_build_sha) or _latest_evidence(db, actor.workspace_id, None)
    ctx = ReleaseContext(
        protocol_problems=validate_protocol(pilot.protocol), mandate_state=MandateState(mandate.state),
        director_ids=_roles(db, actor.workspace_id, Role.RESEARCH_DIRECTOR), integrity_officer_ids=_roles(db, actor.workspace_id, Role.INTEGRITY_OFFICER),
        missing_limits=policy.limits.missing(), required_tests=list(pilot.protocol["required_tests"]),
        evidence_build_sha=evidence.build_sha if evidence else None,
        evidence_covered=frozenset(evidence.covered_ids) if evidence else frozenset(), evidence_failed=frozenset(evidence.failed_ids) if evidence else frozenset(),
        current_build_sha=current_build_sha.lower() if current_build_sha else None, suite_present=suite is not None,
        suite_protected=bool(suite and suite.protected), shared_budget_present=pilot.budget_account_id is not None)
    if opt in (DecisionOption.ACCEPT, DecisionOption.ACCEPT_LIMITED):
        check_release(ctx)
    decision = HumanDecision(id=new_id("HD"), workspace_id=actor.workspace_id, decision_type="pilot_release", object_type="pilot_study", object_id=pilot.id,
                             object_version=pilot.version, object_hash=pilot.preregistration_sha256, option=opt.value, rationale=rationale,
                             alternatives_presented=alternatives_presented, remaining_objections=remaining_objections, consequences=consequences,
                             conditions=conditions, decided_by=actor.actor_id)
    db.add(decision)
    db.flush()
    pilot.decision_id = decision.id
    pilot.self_control = self_control(ctx)
    if opt in (DecisionOption.ACCEPT, DecisionOption.ACCEPT_LIMITED):
        pilot.state, pilot.released_at, pilot.evidence_id = "registered", utcnow(), evidence.id if evidence else None
    db.flush()
    record(db, actor=actor, command="pilot.release", object_type="pilot_study", object_id=pilot.id, object_version=str(pilot.version), correlation_id=correlation_id,
           event_type="PilotReleaseDecided", payload={"option": opt.value, "state": pilot.state, "self_control": pilot.self_control,
                                                      "evidence_build_sha": evidence.build_sha if evidence else None, "decision_id": decision.id})
    return pilot


# ---------------------------------------------------------------- arms, projects, tasks

def _arm(db: Session, pilot: PilotStudy, condition: str) -> PilotArm:
    if condition not in CONDITIONS:
        raise CampusError(ErrorCode.VALIDATION_ERROR, f"Unbekannte Bedingung {condition}; zulässig: {list(CONDITIONS)}")
    return db.execute(select(PilotArm).where(PilotArm.pilot_id == pilot.id, PilotArm.condition == condition)).scalar_one()


def create_arm_project(db: Session, *, actor: Actor, pilot_id: str, condition: str, institute_id: str, title: str, budget: str, correlation_id: str) -> Project:
    """A project of one arm, budgeted from the shared pilot budget (no hidden resource comparison)."""
    authorize("pilot.arm_project", actor.roles)
    pilot = _own(db, PilotStudy, pilot_id, actor, "Pilot")
    if pilot.state not in ("registered", "running"):
        raise CampusError(ErrorCode.INVALID_TRANSITION, "Projekte eines Arms erst nach der Pilotfreigabe", blocked_action="pilot.arm_project")
    arm = _arm(db, pilot, condition)
    project = services_research.create_project(db, actor=actor, mandate_version_id=pilot.mandate_version_id, institute_id=institute_id, title=title,
                                               budget=budget, correlation_id=correlation_id, budget_parent_id=pilot.budget_account_id)
    db.add(PilotArmProject(id=new_id("PP"), arm_id=arm.id, project_id=project.id))
    pilot.state = "running"
    db.flush()
    record(db, actor=actor, command="pilot.arm_project", object_type="pilot_study", object_id=pilot.id, object_version=str(pilot.version),
           correlation_id=correlation_id, project_id=project.id, event_type="PilotArmProjectCreated", payload={"condition": condition, "institute_id": institute_id})
    return project


def _arm_project_ids(db: Session, arm_id: str) -> list[str]:
    return list(db.execute(select(PilotArmProject.project_id).where(PilotArmProject.arm_id == arm_id)).scalars().all())


def attach_task(db: Session, *, actor: Actor, pilot_id: str, task_id: str, repetition: int, correlation_id: str) -> PilotArmTask:
    """Binds a task to the arm whose project it belongs to. Repetition numbers are bounded by the protocol."""
    authorize("pilot.attach", actor.roles)
    pilot = _own(db, PilotStudy, pilot_id, actor, "Pilot")
    task = _own(db, Task, task_id, actor, "Aufgabe")
    if pilot.state not in ("registered", "running"):
        raise CampusError(ErrorCode.INVALID_TRANSITION, "Aufgaben erst nach der Pilotfreigabe zuordnen", blocked_action="pilot.attach")
    arm = db.execute(select(PilotArm).join(PilotArmProject, PilotArmProject.arm_id == PilotArm.id)
                     .where(PilotArm.pilot_id == pilot.id, PilotArmProject.project_id == task.project_id)).scalar_one_or_none()
    if arm is None:
        raise CampusError(ErrorCode.VALIDATION_ERROR, "Aufgabe gehört zu keinem Projekt eines Pilotarms; sie liegt ausserhalb des gemeinsamen Budgets",
                          blocked_action="pilot.attach")
    if not 1 <= repetition <= int(pilot.protocol["repetitions"]):
        raise CampusError(ErrorCode.VALIDATION_ERROR, f"repetition muss zwischen 1 und {pilot.protocol['repetitions']} liegen (vorregistriert)")
    if db.execute(select(PilotArmTask).where(PilotArmTask.task_id == task.id)).first():
        raise CampusError(ErrorCode.VERSION_CONFLICT, "Aufgabe ist bereits einem Arm zugeordnet")
    link = PilotArmTask(id=new_id("PT"), arm_id=arm.id, task_id=task.id, repetition=repetition, created_at=utcnow())
    db.add(link)
    pilot.state = "running"
    db.flush()
    record(db, actor=actor, command="pilot.attach", object_type="pilot_study", object_id=pilot.id, object_version=str(pilot.version), correlation_id=correlation_id,
           project_id=task.project_id, event_type="PilotTaskAttached", payload={"condition": arm.condition, "task_id": task.id, "repetition": repetition})
    return link


# ---------------------------------------------------------------- blinded independent evaluation

def _models_used(db: Session, task_ids: list[str], catalog) -> tuple[set[str], set[str]]:
    producers: set[str] = set()
    reviewers: set[str] = set()
    if not task_ids:
        return producers, reviewers
    for dec in db.execute(select(RoutingDecisionRecord).where(RoutingDecisionRecord.task_id.in_(task_ids))).scalars().all():
        doc = dec.document
        sel = catalog.deployment(doc.get("selected_deployment_id") or "")
        if sel and sel.canonical_model_id:
            producers.add(sel.canonical_model_id)
        rev = catalog.deployment(doc.get("review_deployment_id") or "") if doc.get("review_deployment_id") else None
        if rev and rev.canonical_model_id:
            reviewers.add(rev.canonical_model_id)
    return producers, reviewers


def _evaluator_approved(db: Session, workspace_id: str, canonical: str | None, task_class: str) -> bool:
    if not canonical:
        return False
    return db.execute(select(QualificationRecord).where(QualificationRecord.workspace_id == workspace_id, QualificationRecord.canonical_model_id == canonical,
                                                        QualificationRecord.task_class == task_class, QualificationRecord.kind == "bootstrap",
                                                        QualificationRecord.role == "evaluator", QualificationRecord.passed.is_(True))).first() is not None


def evaluate_task(db: Session, *, actor: Actor, pilot_id: str, task_id: str, vault: CaseVault | None, correlation_id: str) -> PilotEvaluation:
    """Independent, blinded evaluation of one arm task's claims on the hidden material. The evaluator is canonically distinct from EVERY
    model used in ANY arm and approved for the evaluator role. Without such a model the evaluation stays open (no silent seal, T13)."""
    authorize("pilot.evaluate", actor.roles)
    pilot = _own(db, PilotStudy, pilot_id, actor, "Pilot")
    link = db.execute(select(PilotArmTask).join(PilotArm, PilotArm.id == PilotArmTask.arm_id)
                      .where(PilotArmTask.task_id == task_id, PilotArm.pilot_id == pilot.id)).scalar_one_or_none()
    if link is None:
        raise CampusError(ErrorCode.NOT_FOUND, "Aufgabe ist diesem Pilot nicht zugeordnet")
    arm = db.get(PilotArm, link.arm_id)
    if vault is None or pilot.evaluation_suite_id is None:
        raise CampusError(ErrorCode.CAPABILITY_UNSUPPORTED, "Kein Tresor oder kein Prüfmaterial; Bewertung nicht möglich", blocked_action="pilot.evaluate")
    suite = db.get(QualificationSuite, pilot.evaluation_suite_id)
    cases = vault.get(suite.id)
    if cases is None or _sha(json.dumps(cases, sort_keys=True, ensure_ascii=False)) != suite.cases_sha256:
        raise CampusError(ErrorCode.VALIDATION_ERROR, "Prüfmaterial im Tresor fehlt oder stimmt nicht mit dem registrierten Hash überein", blocked_action="pilot.evaluate")
    claims = db.execute(select(ClaimVersion).where(ClaimVersion.task_id == task_id).order_by(ClaimVersion.created_at)).scalars().all()
    if not claims:
        raise CampusError(ErrorCode.VALIDATION_ERROR, "Aufgabe hat keine Aussagen; nichts zu bewerten", blocked_action="pilot.evaluate")
    policy, _pid, catalog, catalog_row = _loaded_policy_and_catalog(db, actor.workspace_id)
    all_task_ids = list(db.execute(select(PilotArmTask.task_id).join(PilotArm, PilotArm.id == PilotArmTask.arm_id).where(PilotArm.pilot_id == pilot.id)).scalars().all())
    producers, reviewers = _models_used(db, all_task_ids, catalog)
    excluded = producers | reviewers
    eval_task = _evaluation_task(db, actor, pilot, suite, correlation_id)
    need = TaskNeed.model_validate(eval_task.task_need)
    candidates = []
    for dep in catalog.deployments:
        if not dep.canonical_model_id or dep.canonical_model_id in excluded:
            continue
        if routing.exclusion_reasons(need.model_copy(update={"substantive_contributor_model_ids": sorted(excluded)}), dep, policy):
            continue
        if _evaluator_approved(db, actor.workspace_id, dep.canonical_model_id, suite.evaluator_task_class):
            candidates.append(dep)
    candidates.sort(key=lambda d: d.id)
    ev = PilotEvaluation(id=new_id("PV"), workspace_id=actor.workspace_id, pilot_id=pilot.id, arm_id=arm.id, task_id=task_id, repetition=link.repetition,
                         state="running", excluded_canonical_model_ids=sorted(excluded), created_by=actor.actor_id, created_at=utcnow())
    db.add(ev)
    db.flush()
    if not candidates:
        ev.state, ev.note = "open_no_independent_evaluator", ("Kein für die Prüfrolle freigegebenes Modell, das kanonisch von allen in den Armen eingesetzten "
                                                              f"Modellen verschieden ist ({sorted(excluded)}); Bewertung bleibt offen (T13)")
        record(db, actor=actor, command="pilot.evaluate", object_type="pilot_evaluation", object_id=ev.id, object_version=None, correlation_id=correlation_id,
               project_id=pilot.evaluation_project_id, event_type="PilotEvaluationOpen", policy_decision="blocked:NO_INDEPENDENT_EVALUATOR",
               payload={"task_id": task_id, "excluded": sorted(excluded)})
        db.commit()
        raise CampusError(ErrorCode.QUALIFICATION_MISSING, ev.note, blocked_action="pilot.evaluate", details={"pilot_evaluation_id": ev.id, "audited": True},
                          required_resolution="Drittes Modell für die Prüfrolle freigeben (Bootstrap evaluator) oder Bewertung offen ausweisen")
    eval_dep = candidates[0]
    ev.evaluator_deployment_id, ev.evaluator_canonical_model_id = eval_dep.id, eval_dep.canonical_model_id
    db.commit()
    # blinding: what the evaluator sees must not reveal condition, arm, task, project, institute or model
    projects = db.execute(select(Project).where(Project.id.in_(_arm_project_ids(db, arm.id)))).scalars().all()
    institutes = db.execute(select(Institute).where(Institute.workspace_id == actor.workspace_id)).scalars().all()
    forbidden = [*CONDITIONS, arm.id, task_id, *[p.id for p in projects], *[p.title for p in projects], *[i.name for i in institutes],
                 *[d.id for d in catalog.deployments], *[d.canonical_model_id or "" for d in catalog.deployments], *[d.provider_id for d in catalog.deployments]]
    claims_text = "\n".join(f"- [{c.stored_kind}/{c.epistemic_status}] {c.statement} (Geltung: {c.validity_conditions})" for c in claims)
    ws = db.get(Workspace, actor.workspace_id)
    mandate = db.get(MandateVersion, pilot.mandate_version_id)
    pricing = _pricing(db, actor.workspace_id, eval_dep.pricing_ref)
    gateway = _gateway(db, actor.workspace_id)
    results: list[CaseResult] = []
    manifest: list[dict] = []
    for case in cases:
        blocks = [{"type": "text", "text": f"Aspekt: {case['input']}", "data_label": "public", "origin": "pilot_case"},
                  {"type": "text", "text": f"Rubrik: {case['rubric']}", "data_label": "public", "origin": "rubric"},
                  {"type": "text", "text": f"Aussagen des bewerteten Ergebnisses (sha256 {_sha(claims_text)}):\n{claims_text}", "data_label": "public", "origin": "arm_output"}]
        leaks = blinding_violations([b["text"] for b in blocks], forbidden)
        if leaks:
            ev.state, ev.note = "aborted_blinding", f"Verblindung verletzt; Bewertung abgebrochen: {leaks}"
            db.commit()
            raise CampusError(ErrorCode.VALIDATION_ERROR, ev.note, blocked_action="pilot.evaluate", details={"leaks": leaks})
        decision_id = new_id("RD")
        reservation = budget_service.reserve(db, account_id=eval_task.budget_account_id,
                                             amount=estimate_cost(pricing, need.context_tokens_estimate, 800), task_id=eval_task.id, purpose="review",
                                             routing_decision_id=decision_id)
        route = routing.ValidatedRoute(selected=eval_dep, reviewer=None, assessments=routing.assess_candidates(need, catalog, policy),
                                       proposal=RouteProposal(task_id=eval_task.id, selected_deployment_id=eval_dep.id,
                                                              rationale=f"Pilotbewertung {ev.id}, Fall {case['case_id']}: Prüfer kanonisch verschieden von allen "
                                                                        f"Arm-Modellen und für {suite.evaluator_task_class} freigegeben.",
                                                              evidence_refs=[], alternatives=[], uncertainties=[]), required_review=False)
        decision = routing.build_decision(route, decision_id=decision_id, catalog_revision=catalog_row.revision, routing_policy_ref=policy.revision,
                                          selection_mode="deterministic_fallback", selector_model_ref=None, reservation_ref=reservation.id,
                                          pricing_snapshot_ref=eval_dep.pricing_ref or "-", created_at=utcnow().isoformat())
        db.add(RoutingDecisionRecord(id=decision.id, workspace_id=actor.workspace_id, task_id=eval_task.id, document=decision.model_dump(), state=decision.state))
        db.flush()
        check_invocation(InvocationContext(purpose="review", workspace_stopped=bool(ws.stopped), mandate_state=MandateState(mandate.state),
                                           task_hold_reason=eval_task.hold_reason, route_state=decision.state, reservation_present=True, policy_revision=policy.revision))
        db.commit()
        request = LLMRequest(
            invocation_id=f"{ev.id}:{case['case_id']}:evaluator", deployment_id=eval_dep.id,
            system_rules="Du bist ein unabhängiger Prüfer einer Pilotstudie. Du weisst nicht, unter welcher Bedingung das Ergebnis entstand. Beurteile die "
                         "Aussagen ausschliesslich anhand der Rubrik. Nenne Gründe, Fehlalarmrisiko und Unsicherheiten. Antworte nur im vorgegebenen JSON-Format.",
            content_blocks=blocks, output_contract=provider_schema("evaluation_verdict"), max_output_tokens=800,
            metadata={"routing_decision_id": decision.id, "pilot_evaluation_id": ev.id, "task_class": suite.evaluator_task_class})
        try:
            response = gateway.invoke(request, eval_dep, decision)
        except CampusError as err:
            _settle_after_failure(db, reservation, pricing, err)
            ev.state, ev.note = "aborted_provider", f"Prüferaufruf gescheitert: {err.code.value}"
            db.commit()
            raise
        _settle_from_usage(db, reservation, pricing, response.usage)
        db.commit()
        verdict = EvaluationVerdict.model_validate(json.loads(response.text()))
        results.append(CaseResult(case_id=str(case["case_id"]), passed=verdict.passed, score=verdict.score, reasons=tuple(verdict.reasons),
                                  false_alarm_risk=verdict.false_alarm_risk, candidate_output_sha256=_sha(claims_text), evaluator_canonical_model_id=eval_dep.canonical_model_id or ""))
        manifest.append({"case_id": case["case_id"], "blocks": [{"origin": b["origin"], "sha256": _sha(b["text"])} for b in blocks], "invocation_id": request.invocation_id})
    score, passed = aggregate(results, int(pilot.protocol["evaluation"]["rule"]["threshold_percent"]))
    ev.case_results = [r.__dict__ for r in results]
    ev.context_manifest, ev.blinded, ev.score, ev.passed, ev.state = manifest, True, score, passed, "completed"
    db.flush()
    record(db, actor=actor, command="pilot.evaluate", object_type="pilot_evaluation", object_id=ev.id, object_version=None, correlation_id=correlation_id,
           project_id=pilot.evaluation_project_id, event_type="PilotEvaluationCompleted",
           payload={"task_id": task_id, "condition": arm.condition, "evaluator_canonical_model_id": eval_dep.canonical_model_id, "score": score, "passed": passed,
                    "cases": len(results), "blinded": True})
    db.commit()
    return ev


def _evaluation_task(db: Session, actor: Actor, pilot: PilotStudy, suite: QualificationSuite, correlation_id: str) -> Task:
    """One task under the evaluation project carries the evaluator calls (route, reservation, journal like every call)."""
    existing = db.execute(select(Task).where(Task.project_id == pilot.evaluation_project_id).order_by(Task.created_at)).scalars().first()
    if existing is not None:
        return existing
    policy, _pid, _catalog, _crow = _loaded_policy_and_catalog(db, actor.workspace_id)
    need = {"schema_version": "1.0", "task_class": suite.evaluator_task_class, "role": "reviewer", "agent_profile_version_ref": "evaluation-harness@1",
            "skill_version_refs": [], "language": "de-CH", "data_labels": ["public"], "required_features": ["text_input", "structured_output"],
            "context_tokens_estimate": 4000, "max_output_tokens": 800, "required_tool_scopes": [], "substantive_contributor_model_ids": [],
            "qualification_mode": "sandbox_experiment", "routing_policy_ref": policy.revision}
    return services_research.create_task(db, actor=actor, project_id=pilot.evaluation_project_id, task_need=need, budget=None, parent_task_id=None,
                                         correlation_id=correlation_id)


def evaluation_view(ev: PilotEvaluation, condition: str | None = None) -> dict:
    return {"pilot_evaluation_id": ev.id, "pilot_id": ev.pilot_id, "arm_id": ev.arm_id, "condition": condition, "task_id": ev.task_id, "repetition": ev.repetition,
            "state": ev.state, "evaluator_deployment_id": ev.evaluator_deployment_id, "evaluator_canonical_model_id": ev.evaluator_canonical_model_id,
            "excluded_canonical_model_ids": ev.excluded_canonical_model_ids, "blinded": ev.blinded, "score": ev.score, "passed": ev.passed,
            "case_results": ev.case_results, "context_manifest": ev.context_manifest, "note": ev.note, "created_at": ev.created_at}


# ---------------------------------------------------------------- facts and report

def _arm_facts(db: Session, actor: Actor, pilot: PilotStudy, arm: PilotArm, catalog) -> ArmFacts:
    facts = ArmFacts(condition=arm.condition)
    project_ids = _arm_project_ids(db, arm.id)
    projects = db.execute(select(Project).where(Project.id.in_(project_ids))).scalars().all() if project_ids else []
    facts.institutes = {p.institute_id for p in projects}
    links = db.execute(select(PilotArmTask).where(PilotArmTask.arm_id == arm.id)).scalars().all()
    task_ids = [x.task_id for x in links]
    facts.tasks = len(task_ids)
    if not task_ids:
        return facts
    tasks = db.execute(select(Task).where(Task.id.in_(task_ids))).scalars().all()
    runs = db.execute(select(RunAttempt).where(RunAttempt.task_id.in_(task_ids))).scalars().all()
    facts.runs = len(runs)
    done_tasks = {r.task_id for r in runs if r.state == RunState.FINISHED.value}
    facts.completed_repetitions = len({x.repetition for x in links if x.task_id in done_tasks})
    facts.runtime_seconds = round(sum((r.ended_at - r.started_at).total_seconds() for r in runs if r.ended_at and r.started_at), 3)
    decisions = db.execute(select(RoutingDecisionRecord.id).where(RoutingDecisionRecord.task_id.in_(task_ids))).scalars().all()
    if decisions:
        invs = db.execute(select(LLMInvocation).where(LLMInvocation.routing_decision_id.in_(decisions))).scalars().all()
        facts.invocations = len(invs)
        facts.input_tokens = sum(i.input_tokens or 0 for i in invs)
        facts.output_tokens = sum(i.output_tokens or 0 for i in invs)
    facts.producer_models, facts.reviewer_models = _models_used(db, task_ids, catalog)
    accounts = {t.budget_account_id for t in tasks} | {p.budget_account_id for p in projects}
    # costs live in the reservations (settled amount, or open/unknown: A14 never assumes zero)
    reservations = db.execute(select(Reservation).where(Reservation.account_id.in_(accounts))).scalars().all()
    facts.cost_settled_minor = sum(r.settled_minor or 0 for r in reservations if r.state == "settled")
    facts.cost_open_entries = sum(1 for r in reservations if r.state != "settled")
    claims = db.execute(select(ClaimVersion).where(ClaimVersion.task_id.in_(task_ids))).scalars().all()
    for c in claims:
        facts.claims_by_kind[c.stored_kind] = facts.claims_by_kind.get(c.stored_kind, 0) + 1
    facts.flagged_claims = sum(1 for c in claims if c.flags)
    facts.reviews = len(db.execute(select(ReviewReportRecord.id).where(ReviewReportRecord.task_id.in_(task_ids))).scalars().all())
    facts.open_findings = len(db.execute(select(FindingRecord.id).where(FindingRecord.task_id.in_(task_ids), FindingRecord.status == "open")).scalars().all())
    xs = db.execute(select(ExperimentRun).where(ExperimentRun.task_id.in_(task_ids))).scalars().all()
    facts.experiments_measured = sum(1 for x in xs if x.measured and x.state == "completed")
    facts.experiments_unmeasured = len(xs) - facts.experiments_measured
    facts.human_interventions = len(db.execute(select(AuditEvent.id).where(AuditEvent.workspace_id == actor.workspace_id, AuditEvent.actor_type == "user",
                                                                           AuditEvent.object_id.in_(task_ids + [r.id for r in runs]),
                                                                           AuditEvent.command.in_(["task.hold.release", "run.cancel"]))).scalars().all())
    evs = db.execute(select(PilotEvaluation).where(PilotEvaluation.arm_id == arm.id)).scalars().all()
    facts.evaluation_scores = [e.score for e in evs if e.state == "completed" and e.score is not None]
    facts.evaluations_open = sum(1 for e in evs if e.state != "completed")
    return facts


def report_document(db: Session, *, actor: Actor, pilot_id: str) -> dict:
    """The pilot report from data: facts per condition, differences, pre-registered rule outcome, platform evidence (separate), flags."""
    pilot = _own(db, PilotStudy, pilot_id, actor, "Pilot")
    _policy, _pid, catalog, _crow = _loaded_policy_and_catalog(db, actor.workspace_id)
    arms = db.execute(select(PilotArm).where(PilotArm.pilot_id == pilot.id).order_by(PilotArm.condition)).scalars().all()
    facts = [_arm_facts(db, actor, pilot, arm, catalog) for arm in arms]
    threshold = int(pilot.protocol["evaluation"]["rule"]["threshold_percent"])
    evidence = db.get(PlatformEvidence, pilot.evidence_id) if pilot.evidence_id else None
    decision = db.get(HumanDecision, pilot.decision_id) if pilot.decision_id else None
    account = db.get(BudgetAccount, pilot.budget_account_id)
    return {
        "report_schema": "1.0",
        "pilot": {"pilot_id": pilot.id, "ref": pilot.ref, "version": pilot.version, "state": pilot.state, "stage": pilot.protocol["stage"],
                  "question": pilot.protocol["question"], "preregistration_sha256": pilot.preregistration_sha256, "released_at": pilot.released_at,
                  "self_control": pilot.self_control, "decision_id": pilot.decision_id, "decision_option": decision.option if decision else None},
        "protocol": pilot.protocol,
        "shared_budget": {"budget_account_id": account.id, "limit_minor": account.limit_minor, "currency": account.currency,
                          "consumed_minor": budget_service.consumed_minor(db, account)},
        "arms": [{**{k: (sorted(v) if isinstance(v, set) else v) for k, v in f.__dict__.items()}, "rule_outcome": rule_outcome(f, threshold)} for f in facts],
        "differences": arm_differences(facts, list(pilot.protocol.get("human_interventions_allowed") or [])),
        "scientific_evaluation": {"note": "Unabhängige, verblindete Bewertung auf verdecktem Material; Modellurteile, vom Kern aggregiert; kein Plattformtest",
                                  "suite_ref": pilot.protocol["evaluation"]["suite_ref"], "rule": pilot.protocol["evaluation"]["rule"],
                                  "per_condition": {f.condition: rule_outcome(f, threshold) for f in facts}},
        "platform_evidence": {"note": "Softwaretests der Plattform für den freigegebenen Build; sagen nichts über Forschungsinhalte",
                              **(evidence_view(evidence) if evidence else {"evidence_id": None})},
        "flags": report_flags(int(pilot.protocol["repetitions"]), facts),
        "generated_at": utcnow(),
    }


def freeze_report(db: Session, *, actor: Actor, pilot_id: str, correlation_id: str) -> ArtifactVersion:
    """Freeze the report as a versioned artifact of the evaluation project. Approval and external release stay human (T08)."""
    authorize("pilot.report", actor.roles)
    from fastapi.encoders import jsonable_encoder

    pilot = _own(db, PilotStudy, pilot_id, actor, "Pilot")
    document = jsonable_encoder(report_document(db, actor=actor, pilot_id=pilot_id))
    document.pop("generated_at", None)
    digest = _sha(json.dumps(document, sort_keys=True, ensure_ascii=False))
    latest = db.execute(select(ArtifactVersion).where(ArtifactVersion.project_id == pilot.evaluation_project_id, ArtifactVersion.kind == PILOT_REPORT_KIND)
                        .order_by(ArtifactVersion.version.desc())).scalars().first()
    if latest is not None and latest.sha256 == digest:
        return latest
    artifact = ArtifactVersion(id=new_id("A"), workspace_id=actor.workspace_id, project_id=pilot.evaluation_project_id, version=(latest.version + 1) if latest else 1,
                               kind=PILOT_REPORT_KIND, content=document, sha256=digest, producer_canonical_model_id="core", synthetic=False)
    db.add(artifact)
    db.flush()
    record(db, actor=actor, command="pilot.report", object_type="artifact_version", object_id=artifact.id, object_version=str(artifact.version),
           correlation_id=correlation_id, project_id=pilot.evaluation_project_id, event_type="PilotReportFrozen",
           payload={"pilot_id": pilot.id, "sha256": digest, "feasibility_only": document["flags"]["feasibility_only"]})
    return artifact


def close_pilot(db: Session, *, actor: Actor, pilot_id: str, correlation_id: str) -> PilotStudy:
    authorize("pilot.close", actor.roles)
    pilot = _own(db, PilotStudy, pilot_id, actor, "Pilot")
    if pilot.state not in ("registered", "running"):
        raise CampusError(ErrorCode.INVALID_TRANSITION, f"Pilot im Zustand {pilot.state} kann nicht abgeschlossen werden")
    has_report = db.execute(select(ArtifactVersion.id).where(ArtifactVersion.project_id == pilot.evaluation_project_id,
                                                              ArtifactVersion.kind == PILOT_REPORT_KIND)).first() is not None
    if not has_report:
        raise CampusError(ErrorCode.VALIDATION_ERROR, "Abschluss braucht einen eingefrorenen Pilotbericht (auch ein Bericht ohne tragfähige Erkenntnis)",
                          blocked_action="pilot.close")
    pilot.state, pilot.closed_at = "closed", utcnow()
    db.flush()
    record(db, actor=actor, command="pilot.close", object_type="pilot_study", object_id=pilot.id, object_version=str(pilot.version), correlation_id=correlation_id,
           event_type="PilotClosed", payload={})
    return pilot


def pilot_view(db: Session, pilot: PilotStudy) -> dict:
    arms = db.execute(select(PilotArm).where(PilotArm.pilot_id == pilot.id).order_by(PilotArm.condition)).scalars().all()
    return {"pilot_id": pilot.id, "ref": pilot.ref, "version": pilot.version, "state": pilot.state, "stage": pilot.protocol["stage"],
            "preregistration_sha256": pilot.preregistration_sha256, "protocol": pilot.protocol, "budget_account_id": pilot.budget_account_id,
            "evaluation_project_id": pilot.evaluation_project_id, "evaluation_suite_id": pilot.evaluation_suite_id, "decision_id": pilot.decision_id,
            "evidence_id": pilot.evidence_id, "self_control": pilot.self_control, "released_at": pilot.released_at, "closed_at": pilot.closed_at,
            "arms": [{"arm_id": a.id, "condition": a.condition, "project_ids": _arm_project_ids(db, a.id),
                      "task_ids": list(db.execute(select(PilotArmTask.task_id).where(PilotArmTask.arm_id == a.id)).scalars().all())} for a in arms]}
