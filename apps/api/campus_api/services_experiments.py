"""S4 services: sandbox probe, sandbox acceptance and isolated experiment runs.

The API never imports or evaluates candidate code. It writes the program and its inputs into the job, hands the job to the sandbox
controller and records what the trusted runner measured. stdout is stored as content, never read for results (chapter 13, T04).
"""
from __future__ import annotations

import hashlib
import json

from campus_sandbox.port import SandboxRunner, SandboxUnavailable
from campus_sandbox.probe import report_sha256, run_probe
from campus_sandbox.spec import SandboxInput, SandboxJob, SandboxLimits, SpecError
from sqlalchemy import select
from sqlalchemy.orm import Session

from campus_contracts.models import TaskNeed
from campus_domain.authz import authorize
from campus_domain.errors import CampusError, ErrorCode
from campus_domain.experiments import ExperimentStartContext, acceptance_matches, check_acceptable, check_experiment_start, is_measured
from campus_domain.statuses import MandateState
from campus_infra.audit import Actor, record
from campus_infra.blobstore import BlobStore
from campus_infra.ids import new_id, utcnow
from campus_infra.models import ExperimentRun, MandateVersion, Project, SandboxAcceptance, Task, Workspace

PROGRAM_ARGV = ("python", "-I", "-B", "/work/in/program.py")  # fixed; a candidate chooses its program, never the interpreter flags
MAX_PROGRAM_BYTES = 256 * 1024
MAX_INLINE_INPUT_BYTES = 4 * 1024 * 1024


def _runner_or_unsupported(runner: SandboxRunner | None) -> SandboxRunner:
    if runner is None:
        raise CampusError(ErrorCode.CAPABILITY_UNSUPPORTED, "Kein Sandboxpfad konfiguriert (CAMPUS_SANDBOX_RUNNER=none); Fähigkeit abgeschaltet",
                          blocked_action="sandbox.probe", required_resolution="CAMPUS_SANDBOX_RUNNER=docker und ein per Digest festgeschriebenes Image setzen")
    return runner


# ---------------------------------------------------------------- probe and acceptance

def probe(db: Session, *, actor: Actor, runner: SandboxRunner | None, correlation_id: str) -> dict:
    """Runs the probe jobs on the configured runner and audits the report. The report is data for a human decision, not the decision."""
    authorize("sandbox.probe", actor.roles)
    r = _runner_or_unsupported(runner)
    try:
        report = run_probe(r, probe_id=new_id("PB").lower()).to_dict()
    except SandboxUnavailable as exc:
        raise CampusError(ErrorCode.CAPABILITY_UNSUPPORTED, f"Sandboxpfad nicht verfügbar: {exc}", blocked_action="sandbox.probe") from exc
    record(db, actor=actor, command="sandbox.probe", object_type="sandbox_probe", object_id=report["report_sha256"], object_version=None,
           correlation_id=correlation_id, event_type="SandboxProbed",
           payload={"runner_kind": report["runner_kind"], "image": report["image"], "host_fingerprint": report["host_fingerprint"],
                    "all_passed": report["all_passed"], "failed": [c["name"] for c in report["checks"] if not c["passed"]]})
    return report


def accept(db: Session, *, actor: Actor, runner: SandboxRunner | None, report: dict, note: str, correlation_id: str) -> SandboxAcceptance:
    """The research director accepts one sandbox path on the strength of a passed probe of THIS host (Art. 11: a protection decision)."""
    authorize("sandbox.accept", actor.roles)
    r = _runner_or_unsupported(runner)
    if not note.strip():
        raise CampusError(ErrorCode.VALIDATION_ERROR, "Eine Abnahme braucht eine Begründung", blocked_action="sandbox.accept")
    if report.get("report_sha256") != report_sha256(report):
        raise CampusError(ErrorCode.VALIDATION_ERROR, "Bericht verändert: Prüfsumme stimmt nicht", blocked_action="sandbox.accept")
    try:
        fingerprint = r.host_fingerprint()
    except SandboxUnavailable as exc:
        raise CampusError(ErrorCode.CAPABILITY_UNSUPPORTED, f"Sandboxpfad nicht verfügbar: {exc}", blocked_action="sandbox.accept") from exc
    check_acceptable(report, configured_runner=r.kind, configured_image=r.image, current_fingerprint=fingerprint)
    row = SandboxAcceptance(id=new_id("SA"), workspace_id=actor.workspace_id, runner_kind=report["runner_kind"], image=report["image"],
                            host_fingerprint=report["host_fingerprint"], probe_report=report, probe_sha256=report["report_sha256"], note=note,
                            accepted_by=actor.actor_id, created_at=utcnow())
    db.add(row)
    db.flush()
    record(db, actor=actor, command="sandbox.accept", object_type="sandbox_acceptance", object_id=row.id, object_version=None,
           correlation_id=correlation_id, event_type="SandboxAccepted",
           payload={"runner_kind": row.runner_kind, "image": row.image, "host_fingerprint": row.host_fingerprint, "probe_sha256": row.probe_sha256})
    return row


def revoke(db: Session, *, actor: Actor, acceptance_id: str, reason: str, correlation_id: str) -> SandboxAcceptance:
    authorize("sandbox.revoke", actor.roles)
    row = db.get(SandboxAcceptance, acceptance_id)
    if row is None or row.workspace_id != actor.workspace_id:
        raise CampusError(ErrorCode.NOT_FOUND, "Abnahme nicht gefunden")
    if row.revoked_at is not None:
        raise CampusError(ErrorCode.INVALID_TRANSITION, "Abnahme ist bereits widerrufen")
    if not reason.strip():
        raise CampusError(ErrorCode.VALIDATION_ERROR, "Ein Widerruf braucht einen Grund")
    row.revoked_at, row.revoked_by, row.revoke_reason = utcnow(), actor.actor_id, reason
    db.flush()
    record(db, actor=actor, command="sandbox.revoke", object_type="sandbox_acceptance", object_id=row.id, object_version=None,
           correlation_id=correlation_id, event_type="SandboxAcceptanceRevoked", payload={"reason": reason})
    return row


def current_acceptance(db: Session, workspace_id: str, runner: SandboxRunner) -> SandboxAcceptance | None:
    """The newest unrevoked acceptance that binds exactly this runner kind, image and host. None otherwise."""
    try:
        fingerprint = runner.host_fingerprint()
    except SandboxUnavailable:
        return None
    rows = db.execute(select(SandboxAcceptance).where(SandboxAcceptance.workspace_id == workspace_id, SandboxAcceptance.revoked_at.is_(None))
                      .order_by(SandboxAcceptance.created_at.desc())).scalars().all()
    for row in rows:
        if acceptance_matches({"runner_kind": row.runner_kind, "image": row.image, "host_fingerprint": row.host_fingerprint, "revoked_at": row.revoked_at},
                              runner_kind=runner.kind, image=runner.image, host_fingerprint=fingerprint):
            return row
    return None


def acceptance_view(row: SandboxAcceptance) -> dict:
    return {"acceptance_id": row.id, "runner_kind": row.runner_kind, "image": row.image, "host_fingerprint": row.host_fingerprint,
            "probe_sha256": row.probe_sha256, "all_passed": row.probe_report.get("all_passed"), "note": row.note, "accepted_by": row.accepted_by,
            "created_at": row.created_at, "revoked_at": row.revoked_at, "revoke_reason": row.revoke_reason}


def status(db: Session, *, workspace_id: str, runner: SandboxRunner | None, capability_level: str) -> dict:
    if runner is None:
        return {"runner_kind": None, "image": None, "accepted": False, "measuring": False, "capability_level": capability_level,
                "note": "Kein Sandboxpfad konfiguriert; Experimente sind abgeschaltet"}
    acc = current_acceptance(db, workspace_id, runner) if is_measured(runner.kind) else None
    return {"runner_kind": runner.kind, "image": runner.image, "accepted": acc is not None, "acceptance_id": acc.id if acc else None,
            "measuring": is_measured(runner.kind), "capability_level": capability_level,
            "note": "Mock-Runner: führt nichts aus; Ergebnisse sind Fixtures" if not is_measured(runner.kind) else
                    ("Sandboxpfad abgenommen" if acc else "Sandboxpfad nicht abgenommen: Probe und Abnahme nötig")}


# ---------------------------------------------------------------- experiments

def run_experiment(db: Session, *, actor: Actor, runner: SandboxRunner | None, blobs: BlobStore, capability_level: str, task_id: str, program: str,
                   inputs: list[dict], limits: dict | None, correlation_id: str) -> ExperimentRun:
    authorize("experiment.run", actor.roles)
    task = db.get(Task, task_id)
    if task is None or task.workspace_id != actor.workspace_id:
        raise CampusError(ErrorCode.NOT_FOUND, "Aufgabe nicht gefunden")
    need = TaskNeed.model_validate(task.task_need)
    ws = db.get(Workspace, actor.workspace_id)
    project = db.get(Project, task.project_id)
    mandate = db.get(MandateVersion, project.mandate_version_id) if project else None
    acceptance = current_acceptance(db, actor.workspace_id, runner) if (runner is not None and is_measured(runner.kind)) else None
    check_experiment_start(ExperimentStartContext(
        workspace_stopped=bool(ws.stopped), mandate_state=MandateState(mandate.state) if mandate else None, task_hold_reason=task.hold_reason,
        required_tool_scopes=tuple(need.required_tool_scopes), runner_kind=runner.kind if runner else None,
        acceptance_present=acceptance is not None, capability_level=capability_level))
    assert runner is not None
    program_bytes = program.encode("utf-8")
    if not program.strip() or len(program_bytes) > MAX_PROGRAM_BYTES:
        raise CampusError(ErrorCode.VALIDATION_ERROR, f"Programm fehlt oder ist grösser als {MAX_PROGRAM_BYTES} Bytes")
    job_inputs = [SandboxInput("program.py", program_bytes)]
    for item in inputs:
        name, text = str(item.get("name", "")), item.get("text")
        if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_INLINE_INPUT_BYTES or name == "program.py":
            raise CampusError(ErrorCode.VALIDATION_ERROR, f"Eingabe {name!r}: Text fehlt, zu gross oder reservierter Name")
        job_inputs.append(SandboxInput(name, text.encode("utf-8")))
    lim = SandboxLimits(**(limits or {})) if limits else SandboxLimits()
    job = SandboxJob(job_id=new_id("X").lower(), image=runner.image, argv=PROGRAM_ARGV, inputs=tuple(job_inputs), limits=lim)
    try:
        job.validate()
    except SpecError as exc:
        raise CampusError(ErrorCode.VALIDATION_ERROR, f"Ungültiger Experimentauftrag: {exc}", blocked_action="experiment.run") from exc
    if not lim.within(runner.ceiling):
        raise CampusError(ErrorCode.VALIDATION_ERROR, "Limits überschreiten die Obergrenzen des Sandboxpfads", blocked_action="experiment.run")
    run = ExperimentRun(id=new_id("X"), workspace_id=actor.workspace_id, project_id=task.project_id, task_id=task.id,
                        acceptance_id=acceptance.id if acceptance else None, runner_kind=runner.kind, image=runner.image,
                        program_sha256=hashlib.sha256(program_bytes).hexdigest(), argv=list(PROGRAM_ARGV), input_manifest=job.input_manifest(),
                        input_manifest_sha256=job.input_manifest_sha256(), limits=json.loads(json.dumps(lim.__dict__)), state="running",
                        measured=False, created_by=actor.actor_id, created_at=utcnow())
    db.add(run)
    db.flush()
    record(db, actor=actor, command="experiment.run", object_type="experiment_run", object_id=run.id, object_version=None, correlation_id=correlation_id,
           project_id=task.project_id, event_type="ExperimentStarted",
           payload={"task_id": task.id, "runner_kind": run.runner_kind, "image": run.image, "program_sha256": run.program_sha256,
                    "input_manifest_sha256": run.input_manifest_sha256, "acceptance_id": run.acceptance_id})
    db.commit()  # the order is durable before anything runs; a crash leaves a visible `running` row, never a silent gap
    try:
        result = runner.run(job)
    except SandboxUnavailable as exc:
        run.state, run.notes, run.ended_at = "runner_unavailable", [str(exc)], utcnow()
        db.flush()
        record(db, actor=actor, command="experiment.end", object_type="experiment_run", object_id=run.id, object_version=None, correlation_id=correlation_id,
               project_id=task.project_id, event_type="ExperimentEnded", policy_decision="blocked:SANDBOX_UNAVAILABLE", payload={"error": str(exc)})
        db.commit()
        raise CampusError(ErrorCode.CAPABILITY_UNSUPPORTED, f"Sandboxpfad nicht verfügbar: {exc}", blocked_action="experiment.run",
                          details={"experiment_run_id": run.id}) from exc
    stored = []
    for out in result.outputs:
        digest = blobs.put(out.data)
        stored.append({"name": out.name, "sha256": digest, "size": out.size})
    run.state, run.measured, run.exit_code = result.state, result.measured, result.exit_code
    run.timed_out, run.oom_killed = result.timed_out, result.oom_killed
    run.started_at, run.ended_at = _parse(result.started_at), _parse(result.ended_at)
    run.stdout_excerpt, run.stderr_excerpt = result.stdout_excerpt, result.stderr_excerpt
    run.outputs, run.notes, run.host_fingerprint = stored, list(result.notes), result.host_fingerprint
    db.flush()
    record(db, actor=actor, command="experiment.end", object_type="experiment_run", object_id=run.id, object_version=None, correlation_id=correlation_id,
           project_id=task.project_id, event_type="ExperimentEnded",
           payload={"state": run.state, "measured": run.measured, "exit_code": run.exit_code, "timed_out": run.timed_out, "oom_killed": run.oom_killed,
                    "outputs": stored, "completed": result.completed})
    db.commit()
    return run


def _parse(iso: str):
    from datetime import datetime

    return datetime.fromisoformat(iso)


def experiment_view(row: ExperimentRun) -> dict:
    return {"experiment_run_id": row.id, "project_id": row.project_id, "task_id": row.task_id, "state": row.state, "measured": row.measured,
            "completed": row.state == "completed" and row.measured, "runner_kind": row.runner_kind, "image": row.image, "acceptance_id": row.acceptance_id,
            "host_fingerprint": row.host_fingerprint, "program_sha256": row.program_sha256, "argv": row.argv, "input_manifest": row.input_manifest,
            "input_manifest_sha256": row.input_manifest_sha256, "limits": row.limits, "exit_code": row.exit_code, "timed_out": row.timed_out,
            "oom_killed": row.oom_killed, "started_at": row.started_at, "ended_at": row.ended_at, "outputs": row.outputs, "notes": row.notes,
            "stdout_excerpt": row.stdout_excerpt, "stderr_excerpt": row.stderr_excerpt,
            "stdout_is_content_not_result": True, "created_by": row.created_by, "created_at": row.created_at}
