"""S4 endpoints: sandbox probe/acceptance/status and isolated experiment runs."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from campus_domain.errors import CampusError, ErrorCode
from campus_infra.audit import Actor
from campus_infra.models import ExperimentRun, SandboxAcceptance

from .. import services_experiments as svc
from ..deps import current_actor, execute_command, get_db
from ..settings import get_settings

router = APIRouter(tags=["sandbox"])


def _runner(request: Request):
    return getattr(request.app.state, "sandbox_runner", None)


@router.get("/sandbox/status")
def sandbox_status(request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> dict:
    return svc.status(db, workspace_id=actor.workspace_id, runner=_runner(request), capability_level=get_settings().capability_level)


@router.post("/sandbox/probe", status_code=201)
def sandbox_probe(request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    def handler(correlation: str):
        return 201, svc.probe(db, actor=actor, runner=_runner(request), correlation_id=correlation)
    return execute_command(db, actor, request, command="sandbox.probe", payload={"probe": True}, handler=handler, object_type="sandbox_probe")


class AcceptBody(BaseModel):
    report: dict
    note: str = Field(min_length=1)


@router.post("/sandbox/acceptances", status_code=201)
def accept_sandbox(body: AcceptBody, request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    def handler(correlation: str):
        row = svc.accept(db, actor=actor, runner=_runner(request), report=body.report, note=body.note, correlation_id=correlation)
        return 201, svc.acceptance_view(row)
    return execute_command(db, actor, request, command="sandbox.accept", payload=body, handler=handler, object_type="sandbox_acceptance")


@router.get("/sandbox/acceptances")
def list_acceptances(actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.execute(select(SandboxAcceptance).where(SandboxAcceptance.workspace_id == actor.workspace_id)
                      .order_by(SandboxAcceptance.created_at.desc())).scalars().all()
    return [svc.acceptance_view(r) for r in rows]


class RevokeBody(BaseModel):
    reason: str = Field(min_length=1)


@router.post("/sandbox/acceptances/{acceptance_id}/revoke")
def revoke_acceptance(acceptance_id: str, body: RevokeBody, request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    def handler(correlation: str):
        return 200, svc.acceptance_view(svc.revoke(db, actor=actor, acceptance_id=acceptance_id, reason=body.reason, correlation_id=correlation))
    return execute_command(db, actor, request, command="sandbox.revoke", payload=body, handler=handler, object_type="sandbox_acceptance", object_id=acceptance_id)


class ExperimentBody(BaseModel):
    task_id: str = Field(min_length=1)
    program: str = Field(min_length=1, description="Python-Quelltext; wird nie im API-Prozess ausgeführt, nur als Datei in die Sandbox gegeben")
    inputs: list[dict] = Field(default_factory=list, description="[{name, text}] – schreibgeschützt unter /work/in")
    limits: dict | None = None


@router.post("/experiments", status_code=201)
def run_experiment(body: ExperimentBody, request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    def handler(correlation: str):
        row = svc.run_experiment(db, actor=actor, runner=_runner(request), blobs=request.app.state.blobs, capability_level=get_settings().capability_level,
                                 task_id=body.task_id, program=body.program, inputs=body.inputs, limits=body.limits, correlation_id=correlation)
        return 201, svc.experiment_view(row)
    return execute_command(db, actor, request, command="experiment.run", payload=body, handler=handler, object_type="experiment_run")


@router.get("/experiments/{experiment_id}")
def get_experiment(experiment_id: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> dict:
    row = db.get(ExperimentRun, experiment_id)
    if row is None or row.workspace_id != actor.workspace_id:
        raise CampusError(ErrorCode.NOT_FOUND, "Experiment nicht gefunden")
    return svc.experiment_view(row)


@router.get("/projects/{project_id}/experiments")
def list_experiments(project_id: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.execute(select(ExperimentRun).where(ExperimentRun.workspace_id == actor.workspace_id, ExperimentRun.project_id == project_id)
                      .order_by(ExperimentRun.created_at)).scalars().all()
    return [svc.experiment_view(r) for r in rows]
