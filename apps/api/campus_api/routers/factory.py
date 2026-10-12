"""S3 endpoints: needs, qualification suites, evaluation runs, retirement, provenance."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from campus_domain.errors import CampusError, ErrorCode
from campus_infra.audit import Actor
from campus_infra.models import EvaluationRun, NeedRecord

from .. import services_factory as svc
from ..deps import current_actor, execute_command, get_db

router = APIRouter(tags=["factory"])


class NeedBody(BaseModel):
    ref: str = Field(min_length=1)
    project_id: str | None = None
    task_id: str | None = None
    gap: str
    requested_specialization: str
    task_class: str
    alternatives_considered: list[str] = []
    expected_benefit: str


@router.post("/needs", status_code=201)
def record_need(body: NeedBody, request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    def handler(correlation: str):
        n = svc.record_need(db, actor=actor, ref=body.ref, project_id=body.project_id, task_id=body.task_id, gap=body.gap,
                            requested_specialization=body.requested_specialization, task_class=body.task_class,
                            alternatives_considered=body.alternatives_considered, expected_benefit=body.expected_benefit, correlation_id=correlation)
        return 201, {"need_id": n.id, "ref": n.ref, "decision": n.decision, "reuse_ref": n.reuse_ref, "alternatives_considered": n.alternatives_considered}
    return execute_command(db, actor, request, command="need.record", payload=body, handler=handler, object_type="need_record")


@router.get("/needs")
def list_needs(actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.execute(select(NeedRecord).where(NeedRecord.workspace_id == actor.workspace_id).order_by(NeedRecord.created_at)).scalars().all()
    return [{"ref": n.ref, "decision": n.decision, "reuse_ref": n.reuse_ref, "task_class": n.task_class, "requested_specialization": n.requested_specialization,
             "gap": n.gap, "created_at": n.created_at} for n in rows]


class SuiteBody(BaseModel):
    ref: str = Field(min_length=1)
    task_class: str = Field(min_length=1)
    threshold_percent: int
    protected: bool = True
    evaluator_task_class: str = "evaluation"
    cases: list[dict]


@router.post("/qualification-suites", status_code=201)
def create_suite(body: SuiteBody, request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    def handler(correlation: str):
        s = svc.create_suite(db, actor=actor, ref=body.ref, task_class=body.task_class, threshold_percent=body.threshold_percent, protected=body.protected,
                             cases=body.cases, evaluator_task_class=body.evaluator_task_class, correlation_id=correlation, vault=request.app.state.vault)
        return 201, {"suite_id": s.id, "ref": s.ref, "version": s.version, "cases_sha256": s.cases_sha256, "case_count": s.case_count}
    return execute_command(db, actor, request, command="suite.create", payload=body, handler=handler, object_type="qualification_suite")


@router.get("/qualification-suites/{ref}")
def get_suite(ref: str, request: Request, version: int | None = None, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> dict:
    return svc.suite_view(db, actor=actor, ref=ref, version=version, vault=request.app.state.vault)


class EvaluationBody(BaseModel):
    candidate_kind: str = Field(description="agent | skill")
    candidate_ref: str = Field(min_length=1)
    candidate_deployment_id: str = Field(min_length=1)
    suite_ref: str = Field(min_length=1)
    suite_version: int | None = None
    project_id: str = Field(min_length=1)
    budget: str | None = None


def _run_view(e: EvaluationRun) -> dict:
    return {"evaluation_run_id": e.id, "state": e.state, "score": e.score, "baseline_score": e.baseline_score, "regression": e.regression,
            "activated": e.activated, "activation_note": e.activation_note, "qualification_ref": e.qualification_ref, "candidate_ref": e.candidate_ref,
            "candidate_deployment_id": e.candidate_deployment_id, "candidate_canonical_model_id": e.candidate_canonical_model_id,
            "evaluator_deployment_id": e.evaluator_deployment_id, "evaluator_canonical_model_id": e.evaluator_canonical_model_id,
            "suite": f"{e.suite_ref}@{e.suite_version}", "case_results": e.case_results, "context_manifest": e.context_manifest, "task_id": e.task_id,
            "composition_key": e.composition_key, "baseline_comparable": e.baseline_comparable,
            "created_at": e.created_at, "ended_at": e.ended_at}


@router.post("/evaluations", status_code=201)
def run_evaluation(body: EvaluationBody, request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    def handler(correlation: str):
        e = svc.run_evaluation(db, actor=actor, candidate_kind=body.candidate_kind, candidate_ref=body.candidate_ref,
                               candidate_deployment_id=body.candidate_deployment_id, suite_ref=body.suite_ref, suite_version=body.suite_version,
                               project_id=body.project_id, budget=body.budget, correlation_id=correlation, vault=request.app.state.vault)
        return 201, _run_view(e)
    return execute_command(db, actor, request, command="evaluation.run", payload=body, handler=handler, object_type="candidate", object_id=body.candidate_ref)


@router.get("/evaluations")
def list_evaluations(candidate_ref: str | None = None, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> list[dict]:
    stmt = select(EvaluationRun).where(EvaluationRun.workspace_id == actor.workspace_id)
    if candidate_ref:
        stmt = stmt.where(EvaluationRun.candidate_ref == candidate_ref)
    return [_run_view(e) for e in db.execute(stmt.order_by(EvaluationRun.created_at)).scalars().all()]


@router.get("/evaluations/{evaluation_id}")
def get_evaluation(evaluation_id: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> dict:
    e = db.get(EvaluationRun, evaluation_id)
    if e is None or e.workspace_id != actor.workspace_id:
        raise CampusError(ErrorCode.NOT_FOUND, "Qualifikationslauf nicht gefunden")
    return _run_view(e)


class RetireBody(BaseModel):
    candidate_kind: str
    reason: str
    successor_ref: str | None = None
    new_status: str = "dormant"


@router.post("/inventory/{ref}/retire")
def retire(ref: str, body: RetireBody, request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    def handler(correlation: str):
        r = svc.retire(db, actor=actor, candidate_kind=body.candidate_kind, candidate_ref=ref, reason=body.reason, successor_ref=body.successor_ref,
                       new_status=body.new_status, correlation_id=correlation)
        return 200, {"retirement_id": r.id, "candidate_ref": r.candidate_ref, "new_status": r.new_status, "successor_ref": r.successor_ref}
    return execute_command(db, actor, request, command="registry.retire", payload=body, handler=handler, object_type="unit", object_id=ref)


@router.get("/inventory/{ref}/rationale")
def rationale(ref: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> dict:
    return svc.rationale(db, actor=actor, ref=ref)
