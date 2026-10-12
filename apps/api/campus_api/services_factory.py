"""Agent and skill factory (architecture chapter 7; Art. 19, 25–28; Annex G).

Need record -> candidate -> independent qualification on fixed, protected test material by a canonically different,
itself qualified evaluator -> automatic activation by the core within the qualified scope -> provenance view -> retirement.
The factory proposes, the evaluator judges, the core activates. Nobody certifies themselves.
"""
from __future__ import annotations

import hashlib
import json

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from campus_contracts.models import EvaluationVerdict, RouteProposal, TaskNeed
from campus_contracts.validation import provider_schema
from campus_domain import routing
from campus_domain.authz import COMMAND_ROLES, authorize
from campus_domain.budget import estimate_cost
from campus_domain.errors import CampusError, ErrorCode
from campus_domain.guards import InvocationContext, check_invocation
from campus_domain.qualification import CaseResult, activation_allowed, aggregate, check_independence, is_regression
from campus_domain.statuses import MandateState
from campus_gateway.port import LLMRequest
from campus_infra import budget_service
from campus_infra.audit import Actor, record
from campus_infra.ids import new_id, utcnow
from campus_infra.models import (
    AgentProfileVersion,
    AuditEvent,
    EvaluationRun,
    MandateVersion,
    NeedRecord,
    Project,
    QualificationRecord,
    QualificationSuite,
    Reservation,
    RetirementRecord,
    RoutingDecisionRecord,
    RunAttempt,
    SkillVersion,
    Task,
    Workspace,
)

from . import services_research
from .services_research import _gateway, _loaded_policy_and_catalog, _pricing, _settle_after_failure, _settle_from_usage, load_composition

EVALUATOR_TASK_CLASS = "evaluation"


def _own(db: Session, model, object_id: str, actor: Actor, what: str):
    row = db.get(model, object_id)
    if row is None or row.workspace_id != actor.workspace_id:
        raise CampusError(ErrorCode.NOT_FOUND, f"{what} nicht gefunden")
    return row


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- need records

def record_need(db: Session, *, actor: Actor, ref: str, project_id: str | None, task_id: str | None, gap: str, requested_specialization: str,
                task_class: str, alternatives_considered: list[str], expected_benefit: str, correlation_id: str) -> NeedRecord:
    """Chapter 7: the registry decides between reuse and new unit from actual data; the decision is recorded with the need."""
    authorize("need.record", actor.roles)
    if project_id:
        _own(db, Project, project_id, actor, "Projekt")
    if task_id:
        _own(db, Task, task_id, actor, "Aufgabe")
    if not (gap.strip() and expected_benefit.strip() and requested_specialization.strip()):
        raise CampusError(ErrorCode.VALIDATION_ERROR, "Bedarf braucht Lücke, Spezialisierung und erwarteten Nutzen (Art. 26)")
    if db.execute(select(NeedRecord).where(NeedRecord.workspace_id == actor.workspace_id, NeedRecord.ref == ref)).first():
        raise CampusError(ErrorCode.VERSION_CONFLICT, f"Bedarf {ref} existiert bereits")
    reusable = [a for a in db.execute(select(AgentProfileVersion).where(AgentProfileVersion.workspace_id == actor.workspace_id,
                                                                        AgentProfileVersion.operating_status == "active")).scalars()
                if a.document["primary_specialization"].strip().lower() == requested_specialization.strip().lower()
                and task_class in (a.qualified_task_classes or [])]
    decision = "reuse" if reusable else "new_agent"
    row = NeedRecord(id=new_id("N"), workspace_id=actor.workspace_id, ref=ref, project_id=project_id, task_id=task_id, gap=gap,
                     requested_specialization=requested_specialization, task_class=task_class,
                     alternatives_considered=sorted(set(alternatives_considered) | {a.ref for a in reusable}),
                     expected_benefit=expected_benefit, decision=decision, reuse_ref=reusable[0].ref if reusable else None,
                     created_by=actor.actor_id)
    db.add(row)
    db.flush()
    record(db, actor=actor, command="need.record", object_type="need_record", object_id=ref, object_version=None, correlation_id=correlation_id,
           project_id=project_id, event_type="NeedRecorded", payload={"decision": decision, "reuse_ref": row.reuse_ref, "task_class": task_class})
    return row


def need_by_ref(db: Session, workspace_id: str, ref: str) -> NeedRecord | None:
    return db.execute(select(NeedRecord).where(NeedRecord.workspace_id == workspace_id, NeedRecord.ref == ref)).scalar_one_or_none()


# ---------------------------------------------------------------- suites

def create_suite(db: Session, *, actor: Actor, ref: str, task_class: str, threshold_percent: int, protected: bool, cases: list[dict],
                 evaluator_task_class: str, correlation_id: str) -> QualificationSuite:
    """Fixed test material. A changed criterion is a new version; the old one and its runs remain (T10)."""
    authorize("suite.create", actor.roles)
    if not 0 <= threshold_percent <= 100:
        raise CampusError(ErrorCode.VALIDATION_ERROR, "threshold_percent muss zwischen 0 und 100 liegen")
    ids = [c.get("case_id") for c in cases]
    if not cases or len(set(ids)) != len(ids) or any(not (c.get("case_id") and c.get("input") and c.get("rubric")) for c in cases):
        raise CampusError(ErrorCode.VALIDATION_ERROR, "Fälle brauchen eindeutige case_id, input und rubric; mindestens ein Fall")
    last = db.execute(select(QualificationSuite).where(QualificationSuite.workspace_id == actor.workspace_id, QualificationSuite.ref == ref)
                      .order_by(QualificationSuite.version.desc())).scalars().first()
    row = QualificationSuite(id=new_id("QS"), workspace_id=actor.workspace_id, ref=ref, version=(last.version + 1) if last else 1,
                             task_class=task_class, evaluator_task_class=evaluator_task_class, threshold_percent=threshold_percent,
                             protected=protected, cases=cases, cases_sha256=_sha(json.dumps(cases, sort_keys=True, ensure_ascii=False)),
                             created_by=actor.actor_id)
    db.add(row)
    db.flush()
    record(db, actor=actor, command="suite.create", object_type="qualification_suite", object_id=ref, object_version=str(row.version),
           correlation_id=correlation_id, payload={"task_class": task_class, "threshold_percent": threshold_percent, "protected": protected,
                                                   "cases": len(cases), "cases_sha256": row.cases_sha256})  # never the cases themselves
    return row


def suite_view(db: Session, *, actor: Actor, ref: str, version: int | None) -> dict:
    """Protected suites show their cases only to roles allowed to read them (T05); everyone sees existence, hash and threshold."""
    stmt = select(QualificationSuite).where(QualificationSuite.workspace_id == actor.workspace_id, QualificationSuite.ref == ref)
    stmt = stmt.where(QualificationSuite.version == version) if version else stmt.order_by(QualificationSuite.version.desc())
    row = db.execute(stmt).scalars().first()
    if row is None:
        raise CampusError(ErrorCode.NOT_FOUND, "Prüfsatz nicht gefunden")
    may_read = bool(set(actor.roles) & COMMAND_ROLES["suite.read_cases"]) or not row.protected
    return {"suite_id": row.id, "ref": row.ref, "version": row.version, "task_class": row.task_class, "evaluator_task_class": row.evaluator_task_class,
            "threshold_percent": row.threshold_percent, "protected": row.protected, "cases_sha256": row.cases_sha256, "case_count": len(row.cases),
            "cases": row.cases if may_read else None, "cases_visible": may_read, "created_at": row.created_at}


def _latest_suite(db: Session, workspace_id: str, ref: str, version: int | None) -> QualificationSuite:
    stmt = select(QualificationSuite).where(QualificationSuite.workspace_id == workspace_id, QualificationSuite.ref == ref)
    stmt = stmt.where(QualificationSuite.version == version) if version else stmt.order_by(QualificationSuite.version.desc())
    row = db.execute(stmt).scalars().first()
    if row is None:
        raise CampusError(ErrorCode.NOT_FOUND, f"Prüfsatz {ref} nicht gefunden", blocked_action="evaluation.run")
    return row


# ---------------------------------------------------------------- evaluation

def _evaluator_qualified(db: Session, workspace_id: str, canonical: str | None, evaluator_task_class: str) -> bool:
    """An evaluator is qualified only by a human bootstrap approval for the evaluator role and the suite's evaluator task class.
    Reviewer approvals and producer combination evidence do not count (role separation, Art. 17)."""
    if not canonical:
        return False
    return db.execute(select(QualificationRecord).where(QualificationRecord.workspace_id == workspace_id,
                                                        QualificationRecord.canonical_model_id == canonical,
                                                        QualificationRecord.task_class == evaluator_task_class,
                                                        QualificationRecord.kind == "bootstrap", QualificationRecord.role == "evaluator",
                                                        QualificationRecord.passed.is_(True))).first() is not None


def _baseline(db: Session, workspace_id: str, candidate_kind: str, logical_id: str, suite_ref: str, exclude_ref: str,
              cases_sha256: str | None = None) -> int | None:
    """Score of the currently active version of the same logical unit on COMPARABLE material: same suite ref and identical cases hash.
    A new suite version with other cases is not comparable; then there is no baseline (S3 review)."""
    if candidate_kind == "agent":
        active = db.execute(select(AgentProfileVersion).where(AgentProfileVersion.workspace_id == workspace_id, AgentProfileVersion.profile_id == logical_id,
                                                              AgentProfileVersion.operating_status == "active", AgentProfileVersion.ref != exclude_ref)).scalars().all()
    else:
        active = db.execute(select(SkillVersion).where(SkillVersion.workspace_id == workspace_id, SkillVersion.skill_id == logical_id,
                                                       SkillVersion.operating_status == "active", SkillVersion.ref != exclude_ref)).scalars().all()
    refs = [a.ref for a in active]
    if not refs:
        return None
    stmt = (select(EvaluationRun).join(QualificationSuite, QualificationSuite.id == EvaluationRun.suite_id)
            .where(EvaluationRun.workspace_id == workspace_id, EvaluationRun.candidate_ref.in_(refs), EvaluationRun.suite_ref == suite_ref,
                   EvaluationRun.activated.is_(True)))
    if cases_sha256:
        stmt = stmt.where(QualificationSuite.cases_sha256 == cases_sha256)
    run = db.execute(stmt.order_by(EvaluationRun.created_at.desc())).scalars().first()
    return run.score if run else None


def run_evaluation(db: Session, *, actor: Actor, candidate_kind: str, candidate_ref: str, candidate_deployment_id: str, suite_ref: str,
                   suite_version: int | None, project_id: str, budget: str | None, correlation_id: str) -> EvaluationRun:
    """Chapter 7: evaluate a candidate (profile or skill) with one deployment on fixed material, judged per case by a canonically different,
    itself qualified evaluator; aggregate; compare with the active version; activate automatically when allowed."""
    authorize("evaluation.run", actor.roles)
    if candidate_kind not in {"agent", "skill"}:
        raise CampusError(ErrorCode.VALIDATION_ERROR, "candidate_kind muss agent oder skill sein")
    project = _own(db, Project, project_id, actor, "Projekt")
    ws = db.get(Workspace, actor.workspace_id)
    mandate = db.get(MandateVersion, project.mandate_version_id)
    suite = _latest_suite(db, actor.workspace_id, suite_ref, suite_version)
    if candidate_kind == "agent":
        candidate = services_research.find_agent(db, actor.workspace_id, candidate_ref)
        logical_id = candidate.profile_id if candidate else None
    else:
        candidate = db.execute(select(SkillVersion).where(SkillVersion.workspace_id == actor.workspace_id, SkillVersion.ref == candidate_ref)).scalar_one_or_none()
        logical_id = candidate.skill_id if candidate else None
    if candidate is None:
        raise CampusError(ErrorCode.NOT_FOUND, f"Kandidat {candidate_ref} ist nicht registriert", blocked_action="evaluation.run")
    if candidate.operating_status in {"archived", "blocked", "retired"}:
        raise CampusError(ErrorCode.INVALID_TRANSITION, f"Kandidat ist {candidate.operating_status}", blocked_action="evaluation.run")
    policy, _policy_id, catalog, catalog_row = _loaded_policy_and_catalog(db, actor.workspace_id)
    cand_dep = catalog.deployment(candidate_deployment_id)
    if cand_dep is None:
        raise CampusError(ErrorCode.NOT_FOUND, "Kandidaten-Deployment nicht im aktiven Katalog", blocked_action="evaluation.run")
    # the evaluation task: a real task under the project so budget, audit and routing records bind to it
    from .services_research import HARNESS_REF

    need_doc = {"schema_version": "1.0", "agent_profile_version_ref": candidate_ref if candidate_kind == "agent" else HARNESS_REF,
                "skill_version_refs": [candidate_ref] if candidate_kind == "skill" else list(candidate.document.get("skill_version_refs", [])),
                "role": "producer", "task_class": suite.task_class,
                "language": "de-CH", "data_labels": ["public"], "required_features": ["text_input", "structured_output"],
                "context_tokens_estimate": 4000, "max_output_tokens": 1500, "required_tool_scopes": [], "substantive_contributor_model_ids": [],
                "qualification_mode": "sandbox_experiment", "routing_policy_ref": policy.revision}
    task = services_research.create_task(db, actor=actor, project_id=project.id, task_need=need_doc, budget=budget, parent_task_id=None,
                                         correlation_id=correlation_id)
    need = TaskNeed.model_validate(task.task_need)
    # Operational admission of the candidate model is enforced exactly as in regular routing; only the missing substantive
    # qualification is exempt (qualification_mode sandbox_experiment). Disabled, unhealthy, unpriced or data-wise unauthorized deployments
    # are not evaluated (S3 review).
    admission = routing.exclusion_reasons(need, cand_dep, policy)
    if admission:
        raise CampusError(ErrorCode.NO_ELIGIBLE_MODEL, "Kandidaten-Deployment ist betrieblich nicht zugelassen: " + ", ".join(admission),
                          blocked_action="evaluation.run", required_resolution="Deployment im Katalog zulassen (aktiv, erreichbar, Preis, Datenfreigabe)",
                          details={"exclusion_reasons": admission})
    composition = load_composition(db, actor.workspace_id, need, mandate.id)  # identical build to regular execution
    instructions = composition.system_rules
    # evaluator: canonically distinct, itself qualified for the evaluator task class, eligible under the hard rules for the review role
    review_need = need.model_copy(update={"role": "reviewer", "task_class": suite.evaluator_task_class,
                                          "substantive_contributor_model_ids": [cand_dep.canonical_model_id or ""]})
    evaluators = []
    for dep in catalog.deployments:
        if dep.id == cand_dep.id or dep.canonical_model_id == cand_dep.canonical_model_id:
            continue
        if routing.exclusion_reasons(review_need, dep, policy):
            continue
        if dep.shared_base_id and dep.shared_base_id == cand_dep.shared_base_id:
            continue
        qualified = _evaluator_qualified(db, actor.workspace_id, dep.canonical_model_id, suite.evaluator_task_class)
        evaluators.append((0 if dep.provider_id != cand_dep.provider_id else 1, dep.id, dep, qualified))
    evaluators.sort(key=lambda e: (e[0], e[1]))
    qualified_evaluators = [e for e in evaluators if e[3]]
    if not qualified_evaluators:
        check_independence(candidate_canonical=cand_dep.canonical_model_id, evaluator_canonical="(kein qualifizierter Prüfer)", evaluator_qualified=False)
    eval_dep = qualified_evaluators[0][2]
    check_independence(candidate_canonical=cand_dep.canonical_model_id, evaluator_canonical=eval_dep.canonical_model_id, evaluator_qualified=True)
    run = EvaluationRun(id=new_id("EV"), workspace_id=actor.workspace_id, project_id=project.id, task_id=task.id, candidate_kind=candidate_kind,
                        candidate_ref=candidate_ref, candidate_deployment_id=cand_dep.id, candidate_canonical_model_id=cand_dep.canonical_model_id or "",
                        evaluator_deployment_id=eval_dep.id, evaluator_canonical_model_id=eval_dep.canonical_model_id, suite_id=suite.id,
                        suite_ref=suite.ref, suite_version=suite.version, state="running", case_results=[], context_manifest=[], created_by=actor.actor_id,
                        composition_key=composition.combination_key)
    db.add(run)
    db.flush()
    record(db, actor=actor, command="evaluation.run", object_type="evaluation_run", object_id=run.id, object_version=None, correlation_id=correlation_id,
           project_id=project.id, event_type="EvaluationStarted",
           payload={"candidate_ref": candidate_ref, "candidate_deployment_id": cand_dep.id, "evaluator_deployment_id": eval_dep.id,
                    "suite": f"{suite.ref}@{suite.version}", "cases": len(suite.cases)})
    db.commit()
    cand_pricing = _pricing(db, actor.workspace_id, cand_dep.pricing_ref)
    eval_pricing = _pricing(db, actor.workspace_id, eval_dep.pricing_ref)
    gateway = _gateway(db, actor.workspace_id)
    results: list[CaseResult] = []
    manifest: list[dict] = []
    try:
        for case in suite.cases:
            decision_id = new_id("RD")
            gen_res = budget_service.reserve(db, account_id=task.budget_account_id,
                                             amount=estimate_cost(cand_pricing, need.context_tokens_estimate, need.max_output_tokens),
                                             task_id=task.id, purpose="generation", routing_decision_id=decision_id)
            rev_res = budget_service.reserve(db, account_id=task.budget_account_id,
                                             amount=estimate_cost(eval_pricing, need.context_tokens_estimate + need.max_output_tokens, 800),
                                             task_id=task.id, purpose="review", routing_decision_id=decision_id)
            route = routing.ValidatedRoute(selected=cand_dep, reviewer=eval_dep, assessments=routing.assess_candidates(need, catalog, policy),
                                           proposal=RouteProposal(task_id=task.id, selected_deployment_id=cand_dep.id,
                                                                  rationale=f"Qualifikationslauf {run.id}, Fall {case['case_id']}: Kandidat fest vorgegeben, "
                                                                            f"Prüfer {eval_dep.id} kanonisch getrennt und für {suite.evaluator_task_class} qualifiziert.",
                                                                  evidence_refs=list(cand_dep.qualification_refs), alternatives=[], uncertainties=[]),
                                           required_review=True)
            decision = routing.build_decision(route, decision_id=decision_id, catalog_revision=catalog_row.revision, routing_policy_ref=policy.revision,
                                              selection_mode="deterministic_fallback", selector_model_ref=None, reservation_ref=gen_res.id,
                                              pricing_snapshot_ref=cand_dep.pricing_ref or "-", created_at=utcnow().isoformat())
            db.add(RoutingDecisionRecord(id=decision.id, workspace_id=actor.workspace_id, task_id=task.id, document=decision.model_dump(), state=decision.state))
            db.flush()
            check_invocation(InvocationContext(purpose="generation", workspace_stopped=bool(ws.stopped), mandate_state=MandateState(mandate.state),
                                               task_hold_reason=task.hold_reason, route_state=decision.state, reservation_present=True,
                                               policy_revision=policy.revision))
            db.commit()
            # 1) the candidate works the case
            cand_request = LLMRequest(
                invocation_id=f"{run.id}:{case['case_id']}:candidate", deployment_id=cand_dep.id,
                system_rules=instructions + " Antworte knapp, nenne Unsicherheiten, erfinde keine Belege.",
                content_blocks=[{"type": "text", "text": str(case["input"]), "data_label": "public", "origin": "qualification_case"}],
                max_output_tokens=need.max_output_tokens, metadata={"routing_decision_id": decision.id, "evaluation_run_id": run.id, "task_class": suite.task_class},
            )
            try:
                cand_response = gateway.invoke(cand_request, cand_dep, decision)
            except CampusError as err:
                _settle_after_failure(db, gen_res, cand_pricing, err)
                db.commit()
                raise
            _settle_from_usage(db, gen_res, cand_pricing, cand_response.usage)
            db.commit()
            output = cand_response.text()
            # 2) the evaluator judges: case, rubric and output only; no self-assessment, no other case (A11 manifest)
            check_invocation(InvocationContext(purpose="review", workspace_stopped=bool(db.get(Workspace, actor.workspace_id).stopped),
                                               mandate_state=MandateState(mandate.state), task_hold_reason=task.hold_reason,
                                               route_state=decision.state, reservation_present=True, policy_revision=policy.revision))
            eval_request = LLMRequest(
                invocation_id=f"{run.id}:{case['case_id']}:evaluator", deployment_id=eval_dep.id,
                system_rules="Du bist ein unabhängiger Prüfer. Beurteile die Antwort des Kandidaten ausschliesslich anhand der Rubrik. Du kennst keine "
                             "Selbsteinschätzung des Kandidaten. Nenne Gründe, das Fehlalarmrisiko und Unsicherheiten. Antworte nur im vorgegebenen JSON-Format.",
                content_blocks=[{"type": "text", "text": f"Fall: {case['input']}", "data_label": "public", "origin": "qualification_case"},
                                {"type": "text", "text": f"Rubrik: {case['rubric']}", "data_label": "public", "origin": "rubric"},
                                {"type": "text", "text": f"Antwort des Kandidaten (sha256 {_sha(output)}):\n{output}", "data_label": "public", "origin": "candidate_output"}],
                output_contract=provider_schema("evaluation_verdict"), max_output_tokens=800,
                metadata={"routing_decision_id": decision.id, "evaluation_run_id": run.id, "task_class": suite.evaluator_task_class},
            )
            try:
                eval_response = gateway.invoke(eval_request, eval_dep, decision)
            except CampusError as err:
                _settle_after_failure(db, rev_res, eval_pricing, err)
                db.commit()
                raise
            _settle_from_usage(db, rev_res, eval_pricing, eval_response.usage)
            db.commit()
            verdict = EvaluationVerdict.model_validate(json.loads(eval_response.text()))
            results.append(CaseResult(case_id=str(case["case_id"]), passed=verdict.passed, score=verdict.score, reasons=tuple(verdict.reasons),
                                      false_alarm_risk=verdict.false_alarm_risk, candidate_output_sha256=_sha(output),
                                      evaluator_canonical_model_id=eval_dep.canonical_model_id or ""))
            manifest.append({"case_id": str(case["case_id"]), "evaluator_received": {"input_sha256": _sha(str(case["input"])), "rubric_sha256": _sha(str(case["rubric"])),
                                                                                       "candidate_output_sha256": _sha(output)},
                             "candidate_self_assessment_included": False, "routing_decision_id": decision.id,
                             "evaluator_effective_model_id": eval_response.effective_model_id})
            run.case_results = [r.__dict__ | {"reasons": list(r.reasons)} for r in results]
            run.context_manifest = manifest
            db.commit()
    except Exception as exc:
        run.state = "aborted"
        run.activation_note = f"abgebrochen: {type(exc).__name__}"
        run.ended_at = utcnow()
        record(db, actor=actor, command="evaluation.run", object_type="evaluation_run", object_id=run.id, object_version=None, correlation_id=correlation_id,
               policy_decision="blocked:EVALUATION_ABORTED", project_id=project.id, payload={"error": type(exc).__name__, "cases_done": len(results)})
        db.commit()
        if isinstance(exc, CampusError):
            exc.details["audited"] = True
        raise
    score, passed = aggregate(results, suite.threshold_percent)
    baseline = _baseline(db, actor.workspace_id, candidate_kind, logical_id, suite.ref, candidate_ref, cases_sha256=suite.cases_sha256)
    run.baseline_comparable = baseline is not None
    regression = is_regression(score, baseline)
    allowed, note = activation_allowed(passed=passed, regression=regression)
    run.score, run.baseline_score, run.regression = score, baseline, regression
    run.state = "passed" if passed else "failed"
    run.activation_note = note
    run.ended_at = utcnow()
    if allowed:
        qual_ref = f"qr:{run.id}"
        db.add(QualificationRecord(id=new_id("Q"), workspace_id=actor.workspace_id, ref=qual_ref, canonical_model_id=cand_dep.canonical_model_id or "",
                                   task_class=suite.task_class, suite_ref=f"{suite.ref}@{suite.version}",
                                   result_summary=f"{score}% der Fälle bestanden (Schwelle {suite.threshold_percent}%), Kombination {composition.combination_key}",
                                   passed=True, evaluated_by=eval_dep.canonical_model_id or "", evaluated_by_kind="model", created_by=actor.actor_id,
                                   kind="combination", role="producer", profile_ref=composition.profile_ref, skill_refs=sorted(composition.skill_refs)))
        if candidate_kind == "agent":
            candidate.operating_status = "active"
            candidate.qualification_status = "task_qualified"
            candidate.qualified_task_classes = sorted(set(candidate.qualified_task_classes or []) | {suite.task_class})
            doc = dict(candidate.document)
            doc["qualification_refs"] = sorted(set(doc.get("qualification_refs", [])) | {qual_ref})
            doc["operating_status"], doc["qualification_status"] = "active", "task_qualified"
            candidate.document = doc
        else:
            candidate.operating_status = "active"
            doc = dict(candidate.document)
            doc["qualification_refs"] = sorted(set(doc.get("qualification_refs", [])) | {qual_ref})
            doc["qualified_combination_refs"] = sorted(set(doc.get("qualified_combination_refs", [])) | {f"{candidate_ref}+{cand_dep.canonical_model_id}+{suite.task_class}"})
            doc["operating_status"] = "active"
            candidate.document = doc
        run.activated, run.qualification_ref = True, qual_ref
    db.flush()
    record(db, actor=actor, command="evaluation.run", object_type="evaluation_run", object_id=run.id, object_version=None, correlation_id=correlation_id,
           project_id=project.id, event_type=("AgentActivated" if candidate_kind == "agent" else "SkillActivated") if allowed else "EvaluationCompleted",
           payload={"candidate_ref": candidate_ref, "score": score, "passed": passed, "baseline_score": baseline, "baseline_comparable": baseline is not None,
                    "regression": regression, "activated": allowed, "note": note, "qualification_ref": run.qualification_ref,
                    "scope_task_class": suite.task_class, "composition_key": composition.combination_key})
    return run


# ---------------------------------------------------------------- retirement and provenance

def retire(db: Session, *, actor: Actor, candidate_kind: str, candidate_ref: str, reason: str, successor_ref: str | None, new_status: str,
           correlation_id: str) -> RetirementRecord:
    authorize("registry.retire", actor.roles)
    if new_status not in {"dormant", "archived"}:
        raise CampusError(ErrorCode.VALIDATION_ERROR, "new_status muss dormant oder archived sein")
    if not reason.strip():
        raise CampusError(ErrorCode.VALIDATION_ERROR, "Stilllegung braucht einen Grund (RetirementRecord)")
    if candidate_kind == "agent":
        row = services_research.find_agent(db, actor.workspace_id, candidate_ref)
    else:
        row = db.execute(select(SkillVersion).where(SkillVersion.workspace_id == actor.workspace_id, SkillVersion.ref == candidate_ref)).scalar_one_or_none()
    if row is None:
        raise CampusError(ErrorCode.NOT_FOUND, "Einheit nicht gefunden")
    row.operating_status = new_status if candidate_kind == "agent" else ("retired" if new_status == "archived" else "blocked")
    rec = RetirementRecord(id=new_id("RT"), workspace_id=actor.workspace_id, candidate_kind=candidate_kind, candidate_ref=candidate_ref, reason=reason,
                           successor_ref=successor_ref, new_status=row.operating_status, decided_by=actor.actor_id)
    db.add(rec)
    db.flush()
    record(db, actor=actor, command="registry.retire", object_type=f"{candidate_kind}_version", object_id=candidate_ref, object_version=None,
           correlation_id=correlation_id, event_type="UnitRetired", payload={"reason": reason, "successor_ref": successor_ref, "new_status": row.operating_status})
    return rec


def rationale(db: Session, *, actor: Actor, ref: str) -> dict:
    """«Warum existiert diese Einheit?» from actual data: need, alternatives, evaluations, qualifications, uses, costs, dependencies, retirement (A09)."""
    agent = services_research.find_agent(db, actor.workspace_id, ref)
    skill = None if agent else db.execute(select(SkillVersion).where(SkillVersion.workspace_id == actor.workspace_id, SkillVersion.ref == ref)).scalar_one_or_none()
    unit = agent or skill
    if unit is None:
        raise CampusError(ErrorCode.NOT_FOUND, "Einheit nicht gefunden")
    kind = "agent" if agent else "skill"
    need_ref = unit.document.get("need_record_ref") or unit.document.get("origin_need_ref")
    need = need_by_ref(db, actor.workspace_id, need_ref) if need_ref else None
    evaluations = db.execute(select(EvaluationRun).where(EvaluationRun.workspace_id == actor.workspace_id, EvaluationRun.candidate_ref == ref)
                             .order_by(EvaluationRun.created_at)).scalars().all()
    qual_refs = unit.document.get("qualification_refs", [])
    quals = db.execute(select(QualificationRecord).where(QualificationRecord.workspace_id == actor.workspace_id, QualificationRecord.ref.in_(qual_refs))).scalars().all() if qual_refs else []
    retirements = db.execute(select(RetirementRecord).where(RetirementRecord.workspace_id == actor.workspace_id, RetirementRecord.candidate_ref == ref)).scalars().all()
    uses, cost_minor = 0, 0
    if agent:
        attempts = db.execute(select(RunAttempt).where(RunAttempt.agent_profile_version_id == agent.id)).scalars().all()
        uses = len(attempts)
        if attempts:
            cost_minor = db.execute(select(func.coalesce(func.sum(Reservation.settled_minor), 0)).where(
                Reservation.routing_decision_id.in_([a.routing_decision_id for a in attempts]), Reservation.state == "settled")).scalar() or 0
    activation_events = db.execute(select(AuditEvent).where(AuditEvent.workspace_id == actor.workspace_id, AuditEvent.object_type == "evaluation_run",
                                                            AuditEvent.payload["candidate_ref"].astext == ref).order_by(AuditEvent.occurred_at)).scalars().all()
    return {
        "ref": ref, "kind": kind, "operating_status": unit.operating_status,
        "qualification_status": getattr(unit, "qualification_status", None), "qualified_task_classes": getattr(unit, "qualified_task_classes", None),
        "need": None if need is None else {"ref": need.ref, "gap": need.gap, "requested_specialization": need.requested_specialization, "task_class": need.task_class,
                                           "alternatives_considered": need.alternatives_considered, "expected_benefit": need.expected_benefit,
                                           "decision": need.decision, "reuse_ref": need.reuse_ref, "created_by": need.created_by, "created_at": need.created_at},
        "need_ref_resolved": need is not None,
        "evaluations": [{"id": e.id, "suite": f"{e.suite_ref}@{e.suite_version}", "state": e.state, "score": e.score, "baseline_score": e.baseline_score,
                         "regression": e.regression, "activated": e.activated, "note": e.activation_note, "candidate_deployment_id": e.candidate_deployment_id,
                         "evaluator_canonical_model_id": e.evaluator_canonical_model_id, "cases": len(e.case_results), "created_at": e.created_at} for e in evaluations],
        "qualifications": [{"ref": q.ref, "task_class": q.task_class, "suite_ref": q.suite_ref, "passed": q.passed, "evaluated_by": q.evaluated_by,
                            "evaluated_by_kind": q.evaluated_by_kind} for q in quals],
        "uses": {"runs": uses, "settled_cost_minor": int(cost_minor)},
        "dependencies": unit.document.get("skill_version_refs") or unit.document.get("dependency_version_refs") or [],
        "retirements": [{"reason": r.reason, "successor_ref": r.successor_ref, "new_status": r.new_status, "decided_by": r.decided_by, "created_at": r.created_at}
                        for r in retirements],
        "activation_history": [{"event": e.command, "payload": e.payload, "at": e.occurred_at} for e in activation_events],
        "source": "Register- und Laufdaten; keine nachträglich erfundene Begründung (Art. 26)",
    }
