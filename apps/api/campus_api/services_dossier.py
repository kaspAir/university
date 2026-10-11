"""Research dossier, result approval and external release (chapters 6, 15, 22; T08).

The dossier is built deterministically from the registers; its hash is the thing a human approves. External release
binds the exact hash and an approval. S2 records the release without a transmission channel.
"""
from __future__ import annotations

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.orm import Session

from campus_domain.authz import authorize
from campus_domain.dossier import check_approval_binding, document_hash, release_allowed
from campus_domain.errors import CampusError, ErrorCode
from campus_domain.mandate import Condition, HumanDecisionInput, validate_decision
from campus_domain.statuses import DecisionOption
from campus_infra.audit import Actor, record
from campus_infra.ids import new_id
from campus_infra.models import (
    Approval,
    ArtifactVersion,
    FindingRecord,
    HumanDecision,
    MandateVersion,
    Project,
    ReassessmentTask,
    ReleaseAttempt,
    ReviewReportRecord,
    RunAttempt,
    Task,
)

from . import services_claims, services_sources


def _own(db: Session, model, object_id: str, actor: Actor, what: str):
    row = db.get(model, object_id)
    if row is None or row.workspace_id != actor.workspace_id:
        raise CampusError(ErrorCode.NOT_FOUND, f"{what} nicht gefunden")
    return row


def dossier_document(db: Session, *, actor: Actor, project_id: str) -> dict:
    """The dossier view (UI handoff §4.3): question/protocol, sources, runs, claims with the three axes, reviews, open findings."""
    project = _own(db, Project, project_id, actor, "Projekt")
    mandate = db.get(MandateVersion, project.mandate_version_id)
    tasks = db.execute(select(Task).where(Task.project_id == project.id).order_by(Task.created_at)).scalars().all()
    runs = db.execute(select(RunAttempt).join(Task, Task.id == RunAttempt.task_id).where(Task.project_id == project.id)
                      .order_by(RunAttempt.started_at)).scalars().all()
    reviews = db.execute(select(ReviewReportRecord).where(ReviewReportRecord.task_id.in_([t.id for t in tasks]))
                         .order_by(ReviewReportRecord.created_at)).scalars().all() if tasks else []
    findings = db.execute(select(FindingRecord).where(FindingRecord.task_id.in_([t.id for t in tasks]))).scalars().all() if tasks else []
    reassessments = db.execute(select(ReassessmentTask).where(ReassessmentTask.project_id == project.id)).scalars().all()
    claims = services_claims.project_claims(db, actor=actor, project_id=project.id, include_graph=True)
    open_findings = [f for f in findings if f.status == "open"]
    return {
        "dossier_schema": "1.0",
        "project": {"project_id": project.id, "title": project.title, "institute_id": project.institute_id, "work_state": project.work_state},
        "mandate": {"mandate_version_id": mandate.id, "mandate_id": mandate.mandate_id, "version": mandate.version, "state": mandate.state,
                    "title": mandate.document.get("title"), "question_space": mandate.document.get("question_space"),
                    "knowledge_goal": mandate.document.get("knowledge_goal"), "method_frame": mandate.document.get("method_frame")},
        "protocol": [{"task_id": t.id, "task_class": t.task_need["task_class"], "role": t.task_need["role"], "state": t.state,
                      "qualification_mode": t.task_need["qualification_mode"], "data_labels": t.task_need["data_labels"]} for t in tasks],
        "sources": services_sources.project_sources(db, actor=actor, project_id=project.id),
        "runs": [{"run_attempt_id": r.id, "task_id": r.task_id, "attempt_no": r.attempt_no, "state": r.state, "end_reason": r.end_reason,
                  "routing_decision_id": r.routing_decision_id, "agent_profile_version_id": r.agent_profile_version_id} for r in runs],
        "claims": claims["claims"],
        "dependency_edges": claims["edges"],
        "reviews": [{"review_report_id": r.id, "run_attempt_id": r.run_attempt_id, "verdict": r.verdict, "artifact_sha256": r.artifact_sha256,
                     "reviewer_canonical_model_id": r.reviewer_canonical_model_id, "producer_canonical_model_id": r.producer_canonical_model_id}
                    for r in reviews],
        "findings": [{"id": f.id, "subject": f.subject, "severity": f.severity, "status": f.status, "scope": f.scope} for f in findings],
        "reassessments": [{"id": r.id, "trigger": r.trigger, "trigger_object_id": r.trigger_object_id, "due_at": r.due_at, "state": r.state,
                           "affected_claim_version_ids": r.affected_claim_version_ids} for r in reassessments],
        "status_axes": {
            "work": project.work_state,
            "claims_by_epistemic_status": claims["counts_by_status"],
            "release": "blocked" if open_findings or any(r.state == "open" for r in reassessments) else "draft",
            "blocking": {"open_findings": [f.id for f in open_findings], "open_reassessments": [r.id for r in reassessments if r.state == "open"]},
        },
        "declarations": {"ai_involvement": "Erzeugung und Erstprüfung durch Sprachmodelle; Kontrollkern deterministisch; keine externe Begutachtung",
                         "synthetic_content": any(bool(a.synthetic) for a in db.execute(select(ArtifactVersion).where(
                             ArtifactVersion.task_id.in_([t.id for t in tasks]))).scalars()) if tasks else False},
    }


def build_dossier_artifact(db: Session, *, actor: Actor, project_id: str, correlation_id: str) -> ArtifactVersion:
    """Freeze the current dossier as a versioned artifact with a content hash. Equal content yields no new version."""
    authorize("dossier.build", actor.roles)
    document = jsonable_encoder(dossier_document(db, actor=actor, project_id=project_id))  # JSON-safe (dates as ISO text) before hashing
    digest = document_hash(document)
    latest = db.execute(select(ArtifactVersion).where(ArtifactVersion.project_id == project_id, ArtifactVersion.kind == "dossier")
                        .order_by(ArtifactVersion.version.desc())).scalars().first()
    if latest is not None and latest.sha256 == digest:
        return latest
    artifact = ArtifactVersion(id=new_id("A"), workspace_id=actor.workspace_id, project_id=project_id, task_id=None, run_attempt_id=None,
                               version=(latest.version + 1) if latest else 1, kind="dossier", content=document, sha256=digest,
                               producer_canonical_model_id="core", synthetic=bool(document["declarations"]["synthetic_content"]))
    db.add(artifact)
    db.flush()
    record(db, actor=actor, command="dossier.build", object_type="artifact", object_id=artifact.id, object_version=str(artifact.version),
           correlation_id=correlation_id, project_id=project_id, event_type="DossierBuilt",
           payload={"sha256": digest, "release_axis": document["status_axes"]["release"]})
    return artifact


def approve_artifact(db: Session, *, actor: Actor, artifact_id: str, expected_sha256: str, option: str, rationale: str,
                     alternatives_presented: list[str], remaining_objections: list[str], consequences: str, conditions: list[dict],
                     correlation_id: str) -> Approval:
    """Result approval (fachliche Ergebnisfreigabe): a reserved human decision bound to the exact artifact hash (Art. 11, 22; I05)."""
    authorize("artifact.approve", actor.roles)
    artifact = _own(db, ArtifactVersion, artifact_id, actor, "Artefakt")
    check_approval_binding(expected_sha256, artifact.sha256)
    opt = DecisionOption(option)
    decision_input = HumanDecisionInput(option=opt, rationale=rationale, alternatives_presented=alternatives_presented,
                                        remaining_objections=remaining_objections, consequences=consequences,
                                        conditions=[Condition(**c) for c in conditions])
    validate_decision(decision_input)
    blocking = artifact.content.get("status_axes", {}).get("blocking", {}) if artifact.kind == "dossier" else {}
    if opt in {DecisionOption.ACCEPT, DecisionOption.ACCEPT_LIMITED} and (blocking.get("open_findings") or blocking.get("open_reassessments")):
        if opt == DecisionOption.ACCEPT:
            raise CampusError(ErrorCode.FORBIDDEN, "Offene Einwände oder Neubewertungen blockieren die uneingeschränkte Freigabe; "
                              "nur «begrenzt annehmen» mit Auflagen oder «überarbeiten» sind möglich (Art. 18, 22)",
                              blocked_action="artifact.approve", details={"blocking": blocking})
    decision = HumanDecision(id=new_id("HD"), workspace_id=actor.workspace_id, decision_type="result_release", object_type="artifact_version",
                             object_id=artifact.id, object_version=artifact.version, object_hash=artifact.sha256, option=opt.value,
                             rationale=rationale, alternatives_presented=alternatives_presented, remaining_objections=remaining_objections,
                             consequences=consequences, conditions=conditions, decided_by=actor.actor_id)
    db.add(decision)
    db.flush()
    approval = Approval(id=new_id("AP"), workspace_id=actor.workspace_id, artifact_version_id=artifact.id, artifact_sha256=artifact.sha256,
                        purpose="result_release", human_decision_id=decision.id, option=opt.value)
    db.add(approval)
    db.flush()
    record(db, actor=actor, command="artifact.approve", object_type="artifact", object_id=artifact.id, object_version=str(artifact.version),
           correlation_id=correlation_id, project_id=artifact.project_id, event_type="ArtifactApproved" if opt.value.startswith("accept") else "ArtifactDecided",
           payload={"option": opt.value, "sha256": artifact.sha256, "decision_id": decision.id})
    return approval


def _open_reassessments_after(db: Session, project_id: str | None, since) -> list[ReassessmentTask]:
    if project_id is None:
        return []
    return list(db.execute(select(ReassessmentTask).where(ReassessmentTask.project_id == project_id, ReassessmentTask.state == "open",
                                                          ReassessmentTask.created_at > since)).scalars().all())


def current_approval(db: Session, artifact: ArtifactVersion) -> Approval | None:
    return db.execute(select(Approval).where(Approval.artifact_version_id == artifact.id).order_by(Approval.created_at.desc())).scalars().first()


def release_publication(db: Session, *, actor: Actor, artifact_id: str, expected_sha256: str, target: str, rationale: str,
                        alternatives_presented: list[str], remaining_objections: list[str], consequences: str, correlation_id: str) -> ReleaseAttempt:
    """External release is a separate reserved decision (Art. 22). It needs a result approval of exactly this version (T08).
    S2 records the release; no channel transmits anything yet."""
    authorize("publication.release", actor.roles)
    artifact = _own(db, ArtifactVersion, artifact_id, actor, "Artefakt")
    check_approval_binding(expected_sha256, artifact.sha256)
    approval = current_approval(db, artifact)
    release_allowed(approval_hash=approval.artifact_sha256 if approval else None, artifact_hash=artifact.sha256,
                    approval_option=approval.option if approval else None)
    withdrawn = _open_reassessments_after(db, artifact.project_id, approval.created_at)
    if withdrawn:
        raise CampusError(ErrorCode.FORBIDDEN,
                          "Nach der Ergebnisfreigabe wurde eine Grundlage zurückgezogen; die Freigabe trägt diese Fassung nicht mehr (Art. 23). "
                          "Neubewertung abschliessen, Dossier neu bauen, neu freigeben.",
                          blocked_action="publication.release", details={"open_reassessments": [r.id for r in withdrawn]})
    decision_input = HumanDecisionInput(option=DecisionOption.ACCEPT, rationale=rationale, alternatives_presented=alternatives_presented,
                                        remaining_objections=remaining_objections, consequences=consequences)
    validate_decision(decision_input)
    if not target.strip():
        raise CampusError(ErrorCode.VALIDATION_ERROR, "Externes Ziel muss benannt sein (Art. 22)")
    decision = HumanDecision(id=new_id("HD"), workspace_id=actor.workspace_id, decision_type="external_release", object_type="artifact_version",
                             object_id=artifact.id, object_version=artifact.version, object_hash=artifact.sha256, option="accept",
                             rationale=rationale, alternatives_presented=alternatives_presented, remaining_objections=remaining_objections,
                             consequences=consequences, conditions=[], decided_by=actor.actor_id)
    db.add(decision)
    db.flush()
    attempt = ReleaseAttempt(id=new_id("REL"), workspace_id=actor.workspace_id, artifact_version_id=artifact.id, artifact_sha256=artifact.sha256,
                             approval_id=approval.id, target=target, human_decision_id=decision.id, state="recorded_no_channel")
    db.add(attempt)
    db.flush()
    record(db, actor=actor, command="publication.release", object_type="artifact", object_id=artifact.id, object_version=str(artifact.version),
           correlation_id=correlation_id, project_id=artifact.project_id, event_type="ReleaseRecorded",
           payload={"target": target, "sha256": artifact.sha256, "state": attempt.state, "approval_id": approval.id})
    return attempt


def project_artifacts(db: Session, *, actor: Actor, project_id: str) -> list[dict]:
    _own(db, Project, project_id, actor, "Projekt")
    tasks = db.execute(select(Task.id).where(Task.project_id == project_id)).scalars().all()
    rows = db.execute(select(ArtifactVersion).where((ArtifactVersion.project_id == project_id) | (ArtifactVersion.task_id.in_(tasks)))
                      .order_by(ArtifactVersion.created_at)).scalars().all()
    out = []
    for a in rows:
        approval = current_approval(db, a)
        releases = db.execute(select(ReleaseAttempt).where(ReleaseAttempt.artifact_version_id == a.id)).scalars().all()
        out.append({"artifact_version_id": a.id, "kind": a.kind, "version": a.version, "sha256": a.sha256, "synthetic": a.synthetic,
                    "task_id": a.task_id, "run_attempt_id": a.run_attempt_id, "created_at": a.created_at,
                    "approval": {"option": approval.option, "sha256": approval.artifact_sha256, "valid_for_current": approval.artifact_sha256 == a.sha256,
                                 "basis_withdrawn_after_approval": bool(_open_reassessments_after(db, a.project_id, approval.created_at))}
                    if approval else None,
                    "releases": [{"id": r.id, "target": r.target, "state": r.state} for r in releases]})
    return out
