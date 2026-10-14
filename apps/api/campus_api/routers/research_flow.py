"""S2 endpoints: sources, claims, dossier, approvals, release, durable workflow."""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from campus_domain.authz import authorize
from campus_domain.errors import CampusError, ErrorCode
from campus_infra.audit import Actor, record
from campus_infra.models import Project, ReassessmentTask, Task, WorkflowRun

from .. import services_claims, services_dossier, services_sources
from ..deps import current_actor, execute_command, get_db
from ..errors import trace_id

router = APIRouter(tags=["research-flow"])


class FetchBody(BaseModel):
    project_id: str = Field(min_length=1)
    url: str = Field(min_length=1)
    task_id: str | None = None


@router.post("/sources/fetch", status_code=201)
def fetch_source(body: FetchBody, request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    def handler(correlation: str):
        source, version, passages, new = services_sources.fetch_source(db, actor=actor, project_id=body.project_id, url=body.url,
                                                                       task_id=body.task_id, correlation_id=correlation)
        return 201, {"source_id": source.id, "source_version_id": version.id, "version": version.version, "new_version": new,
                     "url": source.normalized_url, "final_url": version.final_url, "content_type": version.content_type,
                     "raw_sha256": version.raw_sha256, "text_sha256": version.text_sha256, "redirect_chain": version.redirect_chain,
                     "passages": [{"passage_id": p.id, "ordinal": p.ordinal, "start_offset": p.start_offset, "end_offset": p.end_offset} for p in passages]}
    return execute_command(db, actor, request, command="source.fetch", payload=body, handler=handler, object_type="source")


@router.get("/projects/{project_id}/sources")
def project_sources(project_id: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> list[dict]:
    return services_sources.project_sources(db, actor=actor, project_id=project_id)


@router.get("/passages/{passage_id}")
def passage(passage_id: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> dict:
    from campus_infra.models import SourcePassage, SourceVersion

    row = db.get(SourcePassage, passage_id)
    if row is None or row.workspace_id != actor.workspace_id:
        raise CampusError(ErrorCode.NOT_FOUND, "Passage nicht gefunden")
    version = db.get(SourceVersion, row.source_version_id)
    return {"passage_id": row.id, "source_version_id": row.source_version_id, "source_id": version.source_id, "version": version.version,
            "start_offset": row.start_offset, "end_offset": row.end_offset, "text": row.text, "text_sha256": row.text_sha256,
            "locator_check": version.extracted_text[row.start_offset:row.end_offset] == row.text}


class RetractBody(BaseModel):
    reason: str = Field(min_length=1)
    due_days: int | None = None


@router.post("/sources/{source_id}/retract")
def retract_source(source_id: str, body: RetractBody, request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    def handler(correlation: str):
        task = services_sources.retract_source(db, actor=actor, source_id=source_id, reason=body.reason, due_days=body.due_days,
                                               correlation_id=correlation)
        return 200, {"source_id": source_id, "status": "retracted",
                     "reassessment_task": {"id": task.id, "due_at": task.due_at, "affected_claim_version_ids": task.affected_claim_version_ids,
                                           "responsible_role": task.responsible_role} if task else None}
    return execute_command(db, actor, request, command="source.retract", payload=body, handler=handler, object_type="source", object_id=source_id)


@router.get("/reassessments")
def reassessments(actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.execute(select(ReassessmentTask).where(ReassessmentTask.workspace_id == actor.workspace_id).order_by(ReassessmentTask.due_at)).scalars().all()
    return [{"id": r.id, "project_id": r.project_id, "trigger": r.trigger, "trigger_object_id": r.trigger_object_id, "due_at": r.due_at,
             "state": r.state, "responsible_role": r.responsible_role, "affected_claim_version_ids": r.affected_claim_version_ids} for r in rows]


@router.get("/projects/{project_id}/claims")
def project_claims(project_id: str, include: str | None = None, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> dict:
    return services_claims.project_claims(db, actor=actor, project_id=project_id, include_graph=include == "graph")


@router.get("/projects/{project_id}/dossier")
def dossier(project_id: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> dict:
    return services_dossier.dossier_document(db, actor=actor, project_id=project_id)


@router.post("/projects/{project_id}/dossier", status_code=201)
def build_dossier(project_id: str, request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    def handler(correlation: str):
        a = services_dossier.build_dossier_artifact(db, actor=actor, project_id=project_id, correlation_id=correlation)
        return 201, {"artifact_version_id": a.id, "version": a.version, "sha256": a.sha256, "kind": a.kind, "synthetic": a.synthetic,
                     "release_axis": a.content["status_axes"]["release"]}
    return execute_command(db, actor, request, command="dossier.build", payload={"project_id": project_id}, handler=handler,
                           object_type="project", object_id=project_id)


@router.get("/projects/{project_id}/artifacts")
def project_artifacts(project_id: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> list[dict]:
    return services_dossier.project_artifacts(db, actor=actor, project_id=project_id)


class ConditionBody(BaseModel):
    text: str
    responsible: str
    due: str


class ApprovalBody(BaseModel):
    expected_sha256: str = Field(min_length=64, max_length=64)
    option: str
    rationale: str
    alternatives_presented: list[str]
    remaining_objections: list[str]
    consequences: str
    conditions: list[ConditionBody] = []


@router.post("/artifacts/{artifact_id}/approvals", status_code=201)
def approve_artifact(artifact_id: str, body: ApprovalBody, request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    def handler(correlation: str):
        a = services_dossier.approve_artifact(db, actor=actor, artifact_id=artifact_id, expected_sha256=body.expected_sha256, option=body.option,
                                              rationale=body.rationale, alternatives_presented=body.alternatives_presented,
                                              remaining_objections=body.remaining_objections, consequences=body.consequences,
                                              conditions=[c.model_dump() for c in body.conditions], correlation_id=correlation)
        return 201, {"approval_id": a.id, "artifact_version_id": a.artifact_version_id, "sha256": a.artifact_sha256, "option": a.option,
                     "purpose": a.purpose, "human_decision_id": a.human_decision_id}
    return execute_command(db, actor, request, command="artifact.approve", payload=body, handler=handler, object_type="artifact", object_id=artifact_id)


class ReleaseBody(BaseModel):
    expected_sha256: str = Field(min_length=64, max_length=64)
    target: str
    rationale: str
    alternatives_presented: list[str]
    remaining_objections: list[str]
    consequences: str


@router.post("/publications/{artifact_id}/release", status_code=201)
def release(artifact_id: str, body: ReleaseBody, request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    def handler(correlation: str):
        r = services_dossier.release_publication(db, actor=actor, artifact_id=artifact_id, expected_sha256=body.expected_sha256, target=body.target,
                                                 rationale=body.rationale, alternatives_presented=body.alternatives_presented,
                                                 remaining_objections=body.remaining_objections, consequences=body.consequences,
                                                 correlation_id=correlation)
        return 201, {"release_id": r.id, "state": r.state, "target": r.target, "sha256": r.artifact_sha256, "approval_id": r.approval_id,
                     "note": "S2: Freigabe protokolliert, kein Übertragungskanal angeschlossen"}
    return execute_command(db, actor, request, command="publication.release", payload=body, handler=handler, object_type="artifact", object_id=artifact_id)


# ---------------------------------------------------------------- durable workflow

class WorkflowBody(BaseModel):
    source_urls: list[str] = []  # optional start sources; the campus discovers the rest within the mandate
    selection_mode: str = "llm"
    discover: bool = True
    max_queries: int = Field(default=6, ge=1, le=20)
    max_sources: int = Field(default=8, ge=1, le=30)
    max_rounds: int = Field(default=2, ge=1, le=3)


async def _temporal_client(request: Request):
    address = os.environ.get("CAMPUS_TEMPORAL_ADDRESS")
    if not address:
        raise CampusError(ErrorCode.WORKFLOW_SERVICE_UNAVAILABLE, "CAMPUS_TEMPORAL_ADDRESS ist nicht gesetzt; kein Ablaufdienst erreichbar",
                          blocked_action="workflow.start", required_resolution="Temporal starten (docker compose up -d temporal) und Adresse setzen")
    client = getattr(request.app.state, "temporal_client", None)
    if client is None:
        from temporalio.client import Client

        try:
            client = await Client.connect(address, namespace=os.environ.get("CAMPUS_TEMPORAL_NAMESPACE", "default"))
        except Exception as exc:  # connection problems are reported, not hidden
            raise CampusError(ErrorCode.WORKFLOW_SERVICE_UNAVAILABLE, f"Ablaufdienst nicht erreichbar: {type(exc).__name__}",
                              blocked_action="workflow.start", retryable=True) from exc
        request.app.state.temporal_client = client
    return client


@router.post("/tasks/{task_id}/workflow", status_code=202)
async def start_workflow(task_id: str, body: WorkflowBody, request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    from campus_workers import TASK_QUEUE
    from campus_workers.activities import FlowInput
    from campus_workers.workflows import ResearchTaskWorkflow

    authorize("workflow.start", actor.roles)
    task = db.get(Task, task_id)
    if task is None or task.workspace_id != actor.workspace_id:
        raise CampusError(ErrorCode.NOT_FOUND, "Aufgabe nicht gefunden")
    if body.selection_mode not in {"llm", "deterministic_fallback"}:
        raise CampusError(ErrorCode.VALIDATION_ERROR, "selection_mode muss llm oder deterministic_fallback sein")
    client = await _temporal_client(request)
    workflow_id = f"task-{task_id}"
    correlation = trace_id(request)
    flow = FlowInput(workspace_id=actor.workspace_id, project_id=task.project_id, task_id=task.id, source_urls=body.source_urls,
                     selection_mode=body.selection_mode, correlation_id=correlation, discover=body.discover, max_queries=body.max_queries,
                     max_sources=body.max_sources, max_rounds=body.max_rounds)
    from temporalio.exceptions import WorkflowAlreadyStartedError

    try:
        handle = await client.start_workflow(ResearchTaskWorkflow.run, flow, id=workflow_id, task_queue=TASK_QUEUE)
    except WorkflowAlreadyStartedError as exc:
        raise CampusError(ErrorCode.INVALID_TRANSITION, "Für diese Aufgabe läuft bereits ein Ablauf; erst stoppen oder enden lassen",
                          blocked_action="workflow.start") from exc
    row = db.get(WorkflowRun, workflow_id)
    if row is None:
        db.add(WorkflowRun(id=workflow_id, workspace_id=actor.workspace_id, project_id=task.project_id, task_id=task.id, task_queue=TASK_QUEUE,
                           started_by=actor.actor_id))
    else:  # a new flow after a failed or stopped one: same durable id per task, new Temporal run id in the audit record below
        row.started_by = actor.actor_id
        row.task_queue = TASK_QUEUE
    record(db, actor=actor, command="workflow.start", object_type="workflow", object_id=workflow_id, object_version=handle.result_run_id,
           correlation_id=correlation, project_id=task.project_id, payload={"source_urls": len(body.source_urls), "selection_mode": body.selection_mode, "discover": body.discover,
                    "max_queries": body.max_queries, "max_sources": body.max_sources, "max_rounds": body.max_rounds})
    db.commit()
    return {"workflow_id": workflow_id, "run_id": handle.result_run_id, "task_queue": TASK_QUEUE}


@router.get("/workflows/{workflow_id}")
async def workflow_status(workflow_id: str, request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> dict:
    row = db.get(WorkflowRun, workflow_id)
    if row is None or row.workspace_id != actor.workspace_id:
        raise CampusError(ErrorCode.NOT_FOUND, "Workflow nicht gefunden")
    client = await _temporal_client(request)
    handle = client.get_workflow_handle(workflow_id)
    description = await handle.describe()
    status = description.status.name if description.status else "UNKNOWN"
    out = {"workflow_id": workflow_id, "status": status, "task_id": row.task_id, "project_id": row.project_id, "started_by": row.started_by}
    if status == "RUNNING":
        try:
            out["stage"] = await handle.query("stage")
        except Exception as exc:  # a query can fail while no worker is polling; report, do not invent a stage
            out["stage"] = f"unavailable: {type(exc).__name__}"
    return out


@router.post("/workflows/{workflow_id}/stop")
async def stop_workflow(workflow_id: str, body: RetractBody, request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> dict:
    authorize("workspace.stop", actor.roles)
    row = db.get(WorkflowRun, workflow_id)
    if row is None or row.workspace_id != actor.workspace_id:
        raise CampusError(ErrorCode.NOT_FOUND, "Workflow nicht gefunden")
    client = await _temporal_client(request)
    await client.get_workflow_handle(workflow_id).signal("stop", body.reason)
    project = db.get(Project, row.project_id)
    record(db, actor=actor, command="workflow.stop", object_type="workflow", object_id=workflow_id, object_version=None,
           correlation_id=trace_id(request), project_id=project.id, payload={"reason": body.reason})
    db.commit()
    return {"workflow_id": workflow_id, "signalled": "stop"}
