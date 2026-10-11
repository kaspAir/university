from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from campus_domain.errors import CampusError, ErrorCode
from campus_infra.audit import Actor
from campus_infra.models import (
    Institute,
    Project,
    RouteProposalRecord,
    RoutingDecisionRecord,
    RunAttempt,
    Task,
    Workspace,
)

from .. import services_research as svc
from ..deps import current_actor, execute_command, get_db
from ..settings import get_settings

router = APIRouter(tags=["research"])


def _project_view(p: Project) -> dict:
    return {"project_id": p.id, "title": p.title, "institute_id": p.institute_id, "mandate_version_id": p.mandate_version_id,
            "budget_account_id": p.budget_account_id, "work_state": p.work_state, "created_at": p.created_at}


class ProjectBody(BaseModel):
    mandate_version_id: str
    institute_id: str
    title: str
    budget: str


@router.post("/projects", status_code=201)
def create_project(body: ProjectBody, request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    def handler(correlation: str):
        p = svc.create_project(db, actor=actor, mandate_version_id=body.mandate_version_id, institute_id=body.institute_id,
                               title=body.title, budget=body.budget, correlation_id=correlation)
        return 201, _project_view(p)
    return execute_command(db, actor, request, command="project.create", payload=body, handler=handler, object_type="project")


@router.get("/projects")
def list_projects(institute_id: str | None = None, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> list[dict]:
    stmt = select(Project).where(Project.workspace_id == actor.workspace_id)
    if institute_id:
        stmt = stmt.where(Project.institute_id == institute_id)
    return [_project_view(p) for p in db.execute(stmt.order_by(Project.created_at)).scalars().all()]


@router.get("/projects/{project_id}")
def get_project(project_id: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> dict:
    p = db.get(Project, project_id)
    if p is None or p.workspace_id != actor.workspace_id:
        raise CampusError(ErrorCode.NOT_FOUND, "Projekt nicht gefunden")
    tasks = db.execute(select(Task).where(Task.project_id == p.id).order_by(Task.created_at)).scalars().all()
    view = _project_view(p)
    view["tasks"] = [{"task_id": t.id, "state": t.state, "parent_task_id": t.parent_task_id, "budget_account_id": t.budget_account_id,
                      "task_class": t.task_need["task_class"], "role": t.task_need["role"]} for t in tasks]
    return view


@router.get("/projects/{project_id}/events")
def project_events(project_id: str, after: int = 0, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> list[dict]:
    return svc.project_events(db, actor=actor, project_id=project_id, after_id=after)


@router.get("/projects/{project_id}/runs")
def project_runs(project_id: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.execute(select(RunAttempt).join(Task, Task.id == RunAttempt.task_id).where(Task.project_id == project_id,
                      RunAttempt.workspace_id == actor.workspace_id).order_by(RunAttempt.started_at)).scalars().all()
    return [{"run_attempt_id": r.id, "task_id": r.task_id, "attempt_no": r.attempt_no, "state": r.state, "end_reason": r.end_reason,
             "routing_decision_id": r.routing_decision_id, "agent_profile_version_id": r.agent_profile_version_id,
             "started_at": r.started_at, "ended_at": r.ended_at} for r in rows]


class TaskBody(BaseModel):
    task_need: dict
    budget: str | None = None
    parent_task_id: str | None = None


@router.post("/projects/{project_id}/tasks", status_code=201)
def create_task(project_id: str, body: TaskBody, request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    def handler(correlation: str):
        t = svc.create_task(db, actor=actor, project_id=project_id, task_need=body.task_need, budget=body.budget,
                            parent_task_id=body.parent_task_id, correlation_id=correlation)
        return 201, {"task_id": t.id, "project_id": t.project_id, "budget_account_id": t.budget_account_id, "state": t.state,
                     "version": t.version, "task_need": t.task_need}
    return execute_command(db, actor, request, command="task.create", payload=body, handler=handler, object_type="project",
                           object_id=project_id)


@router.get("/tasks/{task_id}")
def get_task(task_id: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> dict:
    t = db.get(Task, task_id)
    if t is None or t.workspace_id != actor.workspace_id:
        raise CampusError(ErrorCode.NOT_FOUND, "Aufgabe nicht gefunden")
    proposals = db.execute(select(RouteProposalRecord).where(RouteProposalRecord.task_id == t.id)
                           .order_by(RouteProposalRecord.created_at)).scalars().all()
    return {"task_id": t.id, "project_id": t.project_id, "state": t.state, "version": t.version, "task_need": t.task_need,
            "route_proposals": [{"id": p.id, "outcome": p.outcome, "error_code": p.error_code, "routing_decision_id": p.routing_decision_id,
                                 "proposal": p.proposal, "assessments": p.assessments, "created_at": p.created_at} for p in proposals]}


class CandidateBody(BaseModel):
    document: dict


@router.post("/agents/candidates", status_code=201)
def register_agent(body: CandidateBody, request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    def handler(correlation: str):
        row = svc.register_agent(db, actor=actor, document=body.document, correlation_id=correlation)
        return 201, {"agent_profile_version_id": row.id, "ref": row.ref, "operating_status": row.operating_status,
                     "qualification_status": row.qualification_status}
    return execute_command(db, actor, request, command="agent.register", payload=body, handler=handler, object_type="agent_profile")


@router.post("/skills/candidates", status_code=201)
def register_skill(body: CandidateBody, request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    def handler(correlation: str):
        row = svc.register_skill(db, actor=actor, document=body.document, correlation_id=correlation)
        return 201, {"skill_version_id": row.id, "ref": row.ref, "operating_status": row.operating_status}
    return execute_command(db, actor, request, command="skill.register", payload=body, handler=handler, object_type="skill")


@router.get("/inventory")
def inventory(actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> dict:
    return svc.inventory(db, actor.workspace_id)


class RouteBody(BaseModel):
    proposal: dict | None = None
    selection_mode: str = "llm"
    selector_model_ref: str | None = None


@router.post("/tasks/{task_id}/routing-decisions", status_code=201)
def authorize_route(task_id: str, body: RouteBody, request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    if body.selection_mode not in {"llm", "fixture", "deterministic_fallback"}:
        raise CampusError(ErrorCode.VALIDATION_ERROR, "selection_mode ungültig")

    def handler(correlation: str):
        row = svc.authorize_route(db, actor=actor, task_id=task_id, proposal=body.proposal, selection_mode=body.selection_mode,
                                  selector_model_ref=body.selector_model_ref, correlation_id=correlation)
        return 201, {"routing_decision_id": row.id, "state": row.state, "decision": row.document}
    return execute_command(db, actor, request, command="route.authorize", payload=body, handler=handler, object_type="task",
                           object_id=task_id)


@router.get("/routing-decisions/{decision_id}")
def get_routing_decision(decision_id: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> dict:
    row = db.get(RoutingDecisionRecord, decision_id)
    if row is None or row.workspace_id != actor.workspace_id:
        raise CampusError(ErrorCode.NOT_FOUND, "Routingentscheid nicht gefunden")
    return {"routing_decision_id": row.id, "task_id": row.task_id, "state": row.state, "decision": row.document}


class RunBody(BaseModel):
    task_id: str
    routing_decision_id: str


@router.post("/runs", status_code=201)
def start_run(body: RunBody, request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    def handler(correlation: str):
        result = svc.start_run(db, actor=actor, task_id=body.task_id, routing_decision_id=body.routing_decision_id,
                               correlation_id=correlation)
        return 201, result
    return execute_command(db, actor, request, command="run.start", payload=body, handler=handler, object_type="task",
                           object_id=body.task_id)


@router.post("/runs/{run_id}/review", status_code=201)
def start_review(run_id: str, request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    """Distinct-model first review of the frozen artifact of a finished run (Art. 17, 27)."""
    def handler(correlation: str):
        return 201, svc.start_review(db, actor=actor, run_id=run_id, correlation_id=correlation)
    return execute_command(db, actor, request, command="review.start", payload={"run_id": run_id}, handler=handler,
                           object_type="run_attempt", object_id=run_id)


@router.get("/tasks/{task_id}/reviews")
def task_reviews(task_id: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> list[dict]:
    from campus_infra.models import FindingRecord, ReviewReportRecord

    reports = db.execute(select(ReviewReportRecord).where(ReviewReportRecord.task_id == task_id,
                                                          ReviewReportRecord.workspace_id == actor.workspace_id)
                         .order_by(ReviewReportRecord.created_at)).scalars().all()
    out = []
    for r in reports:
        findings = db.execute(select(FindingRecord).where(FindingRecord.review_report_id == r.id)).scalars().all()
        out.append({"review_report_id": r.id, "run_attempt_id": r.run_attempt_id, "verdict": r.verdict, "artifact_sha256": r.artifact_sha256,
                    "reviewer_canonical_model_id": r.reviewer_canonical_model_id, "reviewer_effective_model_id": r.reviewer_effective_model_id,
                    "producer_canonical_model_id": r.producer_canonical_model_id, "document": r.document,
                    "findings": [{"id": f.id, "subject": f.subject, "argument": f.argument, "severity": f.severity, "scope": f.scope,
                                  "status": f.status} for f in findings], "created_at": r.created_at})
    return out


class ReasonBody(BaseModel):
    reason: str


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str, body: ReasonBody, request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    def handler(correlation: str):
        row = svc.cancel_run(db, actor=actor, run_id=run_id, reason=body.reason, correlation_id=correlation)
        return 200, {"run_attempt_id": row.id, "state": row.state, "end_reason": row.end_reason}
    return execute_command(db, actor, request, command="run.cancel", payload=body, handler=handler, object_type="run_attempt",
                           object_id=run_id)


@router.post("/workspace/stop")
def stop_workspace(body: ReasonBody, request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    def handler(correlation: str):
        ws = svc.stop_workspace(db, actor=actor, reason=body.reason, correlation_id=correlation)
        return 200, {"workspace_id": ws.id, "stopped": ws.stopped, "stop_reason": ws.stop_reason}
    return execute_command(db, actor, request, command="workspace.stop", payload=body, handler=handler, object_type="workspace",
                           object_id=actor.workspace_id)


@router.post("/workspace/resume")
def resume_workspace(body: ReasonBody, request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    def handler(correlation: str):
        ws = svc.resume_workspace(db, actor=actor, reason=body.reason, correlation_id=correlation)
        return 200, {"workspace_id": ws.id, "stopped": ws.stopped}
    return execute_command(db, actor, request, command="workspace.resume", payload=body, handler=handler, object_type="workspace",
                           object_id=actor.workspace_id)


@router.get("/workspace")
def workspace(actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> dict:
    ws = db.get(Workspace, actor.workspace_id)
    return {"workspace_id": ws.id, "name": ws.name, "stopped": ws.stopped, "stop_reason": ws.stop_reason,
            "capability_level": get_settings().capability_level}


@router.get("/institutes")
def institutes(include: str | None = None, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.execute(select(Institute).where(Institute.workspace_id == actor.workspace_id).order_by(Institute.sort_order)).scalars().all()
    stats: dict[str, dict] = {}
    if include == "stats":
        for inst_id, state, count in db.execute(
            select(Project.institute_id, Task.state, func.count()).join(Task, Task.project_id == Project.id)
            .where(Project.workspace_id == actor.workspace_id).group_by(Project.institute_id, Task.state)
        ).all():
            stats.setdefault(inst_id, {})[state] = count
    return [{"institute_id": i.id, "name": i.name, "remit": i.remit, "tasks_by_state": stats.get(i.id, {})} for i in rows]
