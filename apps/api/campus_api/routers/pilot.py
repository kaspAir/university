"""S5 endpoints: platform evidence, pilot protocols, release, arms, evaluation, report."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from campus_domain.errors import CampusError, ErrorCode
from campus_infra.audit import Actor
from campus_infra.models import PilotArm, PilotEvaluation, PilotStudy, PlatformEvidence

from .. import services_pilot as svc
from ..deps import current_actor, execute_command, get_db
from .research_flow import ConditionBody

router = APIRouter(tags=["pilot"])


class EvidenceBody(BaseModel):
    build_sha: str = Field(min_length=7, max_length=64)
    source: str = Field(min_length=1, description="z. B. jenkins:university-develop#42")
    junit_xml: str = Field(min_length=1)


@router.post("/platform-evidence", status_code=201)
def import_evidence(body: EvidenceBody, request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    def handler(correlation: str):
        return 201, svc.evidence_view(svc.import_evidence(db, actor=actor, build_sha=body.build_sha, source=body.source, junit_xml=body.junit_xml, correlation_id=correlation))
    return execute_command(db, actor, request, command="evidence.import", payload={"build_sha": body.build_sha, "source": body.source, "size": len(body.junit_xml)},
                           handler=handler, object_type="platform_evidence")


@router.get("/platform-evidence")
def list_evidence(actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.execute(select(PlatformEvidence).where(PlatformEvidence.workspace_id == actor.workspace_id).order_by(PlatformEvidence.created_at.desc())).scalars().all()
    return [svc.evidence_view(r) for r in rows]


class PilotBody(BaseModel):
    mandate_version_id: str = Field(min_length=1)
    ref: str = Field(min_length=1)
    protocol: dict


@router.post("/pilots", status_code=201)
def create_pilot(body: PilotBody, request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    def handler(correlation: str):
        return 201, svc.pilot_view(db, svc.create_pilot(db, actor=actor, mandate_version_id=body.mandate_version_id, ref=body.ref, protocol=body.protocol, correlation_id=correlation))
    return execute_command(db, actor, request, command="pilot.create", payload=body, handler=handler, object_type="pilot_study")


@router.get("/pilots")
def list_pilots(actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.execute(select(PilotStudy).where(PilotStudy.workspace_id == actor.workspace_id).order_by(PilotStudy.created_at)).scalars().all()
    return [svc.pilot_view(db, r) for r in rows]


def _pilot(db: Session, actor: Actor, pilot_id: str) -> PilotStudy:
    row = db.get(PilotStudy, pilot_id)
    if row is None or row.workspace_id != actor.workspace_id:
        raise CampusError(ErrorCode.NOT_FOUND, "Pilot nicht gefunden")
    return row


@router.get("/pilots/{pilot_id}")
def get_pilot(pilot_id: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> dict:
    return svc.pilot_view(db, _pilot(db, actor, pilot_id))


class ReleaseBody(BaseModel):
    option: str
    rationale: str
    alternatives_presented: list[str]
    remaining_objections: list[str]
    consequences: str
    conditions: list[ConditionBody] = []
    current_build_sha: str | None = None


@router.post("/pilots/{pilot_id}/release")
def release_pilot(pilot_id: str, body: ReleaseBody, request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    def handler(correlation: str):
        row = svc.release_pilot(db, actor=actor, pilot_id=pilot_id, current_build_sha=body.current_build_sha, option=body.option, rationale=body.rationale,
                                alternatives_presented=body.alternatives_presented, remaining_objections=body.remaining_objections, consequences=body.consequences,
                                conditions=[c.model_dump() for c in body.conditions], correlation_id=correlation)
        return 200, svc.pilot_view(db, row)
    return execute_command(db, actor, request, command="pilot.release", payload=body, handler=handler, object_type="pilot_study", object_id=pilot_id)


class ArmProjectBody(BaseModel):
    condition: str
    institute_id: str
    title: str
    budget: str


@router.post("/pilots/{pilot_id}/arm-projects", status_code=201)
def create_arm_project(pilot_id: str, body: ArmProjectBody, request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    def handler(correlation: str):
        p = svc.create_arm_project(db, actor=actor, pilot_id=pilot_id, condition=body.condition, institute_id=body.institute_id, title=body.title,
                                   budget=body.budget, correlation_id=correlation)
        return 201, {"project_id": p.id, "condition": body.condition, "institute_id": p.institute_id, "budget_account_id": p.budget_account_id}
    return execute_command(db, actor, request, command="pilot.arm_project", payload=body, handler=handler, object_type="pilot_study", object_id=pilot_id)


class AttachBody(BaseModel):
    task_id: str
    repetition: int


@router.post("/pilots/{pilot_id}/tasks", status_code=201)
def attach_task(pilot_id: str, body: AttachBody, request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    def handler(correlation: str):
        link = svc.attach_task(db, actor=actor, pilot_id=pilot_id, task_id=body.task_id, repetition=body.repetition, correlation_id=correlation)
        arm = db.get(PilotArm, link.arm_id)
        return 201, {"task_id": link.task_id, "condition": arm.condition, "repetition": link.repetition}
    return execute_command(db, actor, request, command="pilot.attach", payload=body, handler=handler, object_type="pilot_study", object_id=pilot_id)


class EvaluateBody(BaseModel):
    task_id: str


@router.post("/pilots/{pilot_id}/evaluations", status_code=201)
def evaluate(pilot_id: str, body: EvaluateBody, request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    def handler(correlation: str):
        ev = svc.evaluate_task(db, actor=actor, pilot_id=pilot_id, task_id=body.task_id, vault=request.app.state.vault, correlation_id=correlation)
        return 201, svc.evaluation_view(ev, db.get(PilotArm, ev.arm_id).condition)
    return execute_command(db, actor, request, command="pilot.evaluate", payload=body, handler=handler, object_type="pilot_study", object_id=pilot_id)


@router.get("/pilots/{pilot_id}/evaluations")
def list_evaluations(pilot_id: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> list[dict]:
    _pilot(db, actor, pilot_id)
    rows = db.execute(select(PilotEvaluation).where(PilotEvaluation.pilot_id == pilot_id).order_by(PilotEvaluation.created_at)).scalars().all()
    return [svc.evaluation_view(e, db.get(PilotArm, e.arm_id).condition) for e in rows]


@router.get("/pilots/{pilot_id}/report")
def report(pilot_id: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> dict:
    return svc.report_document(db, actor=actor, pilot_id=pilot_id)


@router.post("/pilots/{pilot_id}/report", status_code=201)
def freeze_report(pilot_id: str, request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    def handler(correlation: str):
        a = svc.freeze_report(db, actor=actor, pilot_id=pilot_id, correlation_id=correlation)
        return 201, {"artifact_id": a.id, "version": a.version, "sha256": a.sha256, "kind": a.kind, "project_id": a.project_id,
                     "feasibility_only": a.content["flags"]["feasibility_only"]}
    return execute_command(db, actor, request, command="pilot.report", payload={"pilot_id": pilot_id}, handler=handler, object_type="pilot_study", object_id=pilot_id)


@router.post("/pilots/{pilot_id}/close")
def close_pilot(pilot_id: str, request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    def handler(correlation: str):
        return 200, svc.pilot_view(db, svc.close_pilot(db, actor=actor, pilot_id=pilot_id, correlation_id=correlation))
    return execute_command(db, actor, request, command="pilot.close", payload={"pilot_id": pilot_id}, handler=handler, object_type="pilot_study", object_id=pilot_id)
