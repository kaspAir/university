"""Activities: thin, idempotent wrappers around the application services. They run as service principals with
separate roles (producer vs. reviewer), never with human identities (chapter 15)."""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from temporalio.exceptions import ApplicationError

from campus_api import services_claims, services_dossier, services_research, services_sources  # noqa: F401 (claims used by run)
from campus_domain.authz import Role
from campus_domain.errors import CampusError
from campus_infra.audit import Actor
from campus_infra.models import ReviewReportRecord, RoutingDecisionRecord, RunAttempt, Task

_session_factory: sessionmaker[Session] | None = None


def configure(session_factory: sessionmaker[Session]) -> None:
    global _session_factory
    _session_factory = session_factory


def _db() -> Session:
    if _session_factory is None:
        raise RuntimeError("activities.configure(session_factory) wurde nicht aufgerufen")
    return _session_factory()


def _producer(workspace_id: str) -> Actor:
    return Actor(actor_type="service_principal", actor_id="sp:workflow-producer", workspace_id=workspace_id, roles=frozenset({Role.PRODUCER}))


def _reviewer(workspace_id: str) -> Actor:
    return Actor(actor_type="service_principal", actor_id="sp:workflow-reviewer", workspace_id=workspace_id, roles=frozenset({Role.REVIEWER}))


def _translate(err: CampusError) -> ApplicationError:
    return ApplicationError(err.message, err.to_object("workflow"), type=err.code.value, non_retryable=not err.retryable)


@dataclass
class FlowInput:
    workspace_id: str
    project_id: str
    task_id: str
    source_urls: list[str] = field(default_factory=list)
    selection_mode: str = "llm"
    correlation_id: str = "workflow"
    review_task_queue: str | None = None  # reviewers may run as separate worker instances (Art. 17)


@dataclass
class RunArgs:
    workspace_id: str
    task_id: str
    routing_decision_id: str
    correlation_id: str = "workflow"


@dataclass
class ReviewArgs:
    workspace_id: str
    run_attempt_id: str
    correlation_id: str = "workflow"


@dataclass
class DossierArgs:
    workspace_id: str
    project_id: str
    correlation_id: str = "workflow"


@activity.defn
def fetch_sources(flow: FlowInput) -> list[str]:
    """Fetch every permitted URL; a blocked or failed URL is an access gap, not a workflow failure (chapter 12)."""
    source_ids: list[str] = []
    with _db() as db:
        for url in flow.source_urls:
            try:
                source, _, _, _ = services_sources.fetch_source(db, actor=_producer(flow.workspace_id), project_id=flow.project_id, url=url,
                                                               task_id=flow.task_id, correlation_id=flow.correlation_id)
                db.commit()
                source_ids.append(source.id)
            except CampusError as err:
                activity.logger.warning("source blocked or failed: %s %s", err.code.value, url)
                db.rollback()
    return source_ids


@activity.defn
def authorize_route(flow: FlowInput) -> str:
    with _db() as db:
        existing = db.execute(
            select(RoutingDecisionRecord).where(RoutingDecisionRecord.task_id == flow.task_id, RoutingDecisionRecord.state == "authorized",
                                                RoutingDecisionRecord.document["required_review"].astext == "true")
            .order_by(RoutingDecisionRecord.created_at.desc())
        ).scalars().first()
        if existing is not None and not db.execute(select(RunAttempt).where(RunAttempt.routing_decision_id == existing.id)).first():
            return existing.id  # idempotent: an unexecuted authorized route exists already
        try:
            row = services_research.authorize_route(db, actor=_producer(flow.workspace_id), task_id=flow.task_id, proposal=None,
                                                    selection_mode=flow.selection_mode, selector_model_ref=None,
                                                    correlation_id=flow.correlation_id)
            db.commit()
            return row.id
        except CampusError as err:
            db.rollback()
            raise _translate(err) from err


@activity.defn
def run_generation(args: RunArgs) -> dict:
    with _db() as db:
        existing = db.execute(select(RunAttempt).where(RunAttempt.routing_decision_id == args.routing_decision_id)).scalar_one_or_none()
        if existing is not None:  # idempotent: the decision was executed before the worker died
            return {"run_attempt_id": existing.id, "state": existing.state, "end_reason": existing.end_reason, "replayed": True}
        try:
            result = services_research.start_run(db, actor=_producer(args.workspace_id), task_id=args.task_id,
                                                 routing_decision_id=args.routing_decision_id, correlation_id=args.correlation_id)
            db.commit()
            return result
        except CampusError as err:
            db.rollback()
            raise _translate(err) from err


@activity.defn
def review_run(args: ReviewArgs) -> dict:
    with _db() as db:
        existing = db.execute(select(ReviewReportRecord).where(ReviewReportRecord.run_attempt_id == args.run_attempt_id)).scalar_one_or_none()
        if existing is not None:
            return {"review_report_id": existing.id, "verdict": existing.verdict, "replayed": True}
        try:
            result = services_research.start_review(db, actor=_reviewer(args.workspace_id), run_id=args.run_attempt_id,
                                                    correlation_id=args.correlation_id)
            db.commit()
            return result
        except CampusError as err:
            db.rollback()
            raise _translate(err) from err


@activity.defn
def build_dossier(args: DossierArgs) -> dict:
    with _db() as db:
        try:
            artifact = services_dossier.build_dossier_artifact(db, actor=_producer(args.workspace_id), project_id=args.project_id,
                                                               correlation_id=args.correlation_id)
            db.commit()
            return {"artifact_version_id": artifact.id, "version": artifact.version, "sha256": artifact.sha256}
        except CampusError as err:
            db.rollback()
            raise _translate(err) from err


@activity.defn
def task_exists(args: RunArgs) -> bool:
    with _db() as db:
        return db.get(Task, args.task_id) is not None


ALL_ACTIVITIES = [fetch_sources, authorize_route, run_generation, review_run, build_dossier, task_exists]
