"""Claims, evidence and dependencies (Art. 13–14, chapter 14, T04)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from campus_contracts.models import ClaimSet
from campus_domain.claims import classify
from campus_domain.errors import CampusError, ErrorCode
from campus_domain.experiments import evidence_valid
from campus_infra.audit import Actor, _fit, record
from campus_infra.ids import new_id
from campus_infra.models import (
    ArtifactVersion,
    ClaimVersion,
    DependencyEdge,
    EvidenceLink,
    ExperimentRun,
    Project,
    RunAttempt,
    Source,
    SourcePassage,
    SourceVersion,
    Task,
)


def project_passages(db: Session, project_id: str) -> list[dict]:
    """Passages of the latest version of every active source in the project, in a stable order (the producer's input)."""
    rows = db.execute(
        select(SourcePassage, SourceVersion.version, Source.id.label("source_id"))
        .join(SourceVersion, SourceVersion.id == SourcePassage.source_version_id)
        .join(Source, Source.id == SourceVersion.source_id)
        .where(Source.project_id == project_id, Source.status == "active")
        .order_by(Source.created_at, SourceVersion.version.desc(), SourcePassage.ordinal)
    ).all()
    seen_sources: dict[str, int] = {}
    out = []
    for passage, version, source_id in rows:
        if seen_sources.setdefault(source_id, version) != version:
            continue  # only the latest version of each source is offered
        out.append({"passage_id": passage.id, "source_id": source_id, "version": version, "ordinal": passage.ordinal, "text": passage.text})
    return out


def project_passage_ids(db: Session, project_id: str) -> set[str]:
    rows = db.execute(
        select(SourcePassage.id).join(SourceVersion, SourceVersion.id == SourcePassage.source_version_id)
        .join(Source, Source.id == SourceVersion.source_id).where(Source.project_id == project_id, Source.status == "active")
    ).scalars().all()
    return set(rows)


def record_claim_set(db: Session, *, actor: Actor, task: Task, attempt: RunAttempt, artifact: ArtifactVersion, claim_set: ClaimSet,
                     producer_canonical_model_id: str, correlation_id: str) -> list[ClaimVersion]:
    """Store what the run declared, classified by the core: observations and computations without real evidence become
    flagged hypotheses (T04); invented passage ids are flagged; dependencies become edges."""
    allowed = project_passage_ids(db, task.project_id)
    experiments = {x.id: x for x in db.execute(select(ExperimentRun).where(ExperimentRun.project_id == task.project_id)).scalars().all()}
    assessment_ref = f"run:{attempt.id}"
    created: dict[str, ClaimVersion] = {}
    for item in claim_set.claims:
        passages = [e for e in item.evidence if e.passage_id is not None]
        runs = [e for e in item.evidence if e.experiment_run_id is not None]
        valid = [e for e in passages if e.passage_id in allowed]
        invalid = [e for e in passages if e.passage_id not in allowed]
        # S4: an experiment supports a computation only if a real runner measured a completed run; a mock or failed run is content
        valid_runs = [e for e in runs if e.experiment_run_id in experiments
                      and evidence_valid(measured=experiments[e.experiment_run_id].measured, state=experiments[e.experiment_run_id].state)]
        unmeasured_runs = [e for e in runs if e.experiment_run_id in experiments and e not in valid_runs]
        invalid += [e for e in runs if e.experiment_run_id not in experiments]
        cls = classify(item.kind, len(valid) + len(valid_runs), len(invalid), len(unmeasured_runs))
        claim = ClaimVersion(
            id=new_id("C"), workspace_id=actor.workspace_id, project_id=task.project_id, task_id=task.id, run_attempt_id=attempt.id,
            artifact_version_id=artifact.id, claim_id=new_id("CL"), version=1, local_id=_fit(item.local_id, 80), statement=item.statement,
            declared_kind=cls.declared_kind, stored_kind=cls.stored_kind, epistemic_status=cls.epistemic_status.value,
            assessment_ref=assessment_ref, validity_conditions=item.validity_conditions, flags=list(cls.flags),
            producer_canonical_model_id=producer_canonical_model_id,
        )
        db.add(claim)
        db.flush()
        for e in valid:
            db.add(EvidenceLink(id=new_id("EV"), workspace_id=actor.workspace_id, claim_version_id=claim.id, kind="source_passage",
                                source_passage_id=e.passage_id, note=e.note))
        for e in valid_runs:
            db.add(EvidenceLink(id=new_id("EV"), workspace_id=actor.workspace_id, claim_version_id=claim.id, kind="experiment_run",
                                experiment_run_id=e.experiment_run_id, note=e.note))
        created[item.local_id] = claim
        if cls.flags:
            record(db, actor=actor, command="claim.record", object_type="claim", object_id=claim.id, object_version="1",
                   correlation_id=correlation_id, policy_decision="allowed", project_id=task.project_id, event_type="ClaimFlagged",
                   payload={"flags": list(cls.flags), "declared_kind": cls.declared_kind, "stored_kind": cls.stored_kind,
                            "invalid_evidence_refs": [e.passage_id or e.experiment_run_id for e in invalid],
                            "unmeasured_experiment_run_ids": [e.experiment_run_id for e in unmeasured_runs]})
    for item in claim_set.claims:
        for dep in item.depends_on:
            if dep in created and dep != item.local_id:
                db.add(DependencyEdge(id=new_id("DE"), workspace_id=actor.workspace_id, from_claim_version_id=created[dep].id,
                                      to_claim_version_id=created[item.local_id].id, relation="supports"))
    db.flush()
    record(db, actor=actor, command="claim.record", object_type="run_attempt", object_id=attempt.id, object_version=None,
           correlation_id=correlation_id, project_id=task.project_id, event_type="ClaimsRecorded",
           payload={"claim_version_ids": [c.id for c in created.values()], "open_questions": claim_set.open_questions,
                    "omissions": claim_set.omissions})
    return list(created.values())


def project_claims(db: Session, *, actor: Actor, project_id: str, include_graph: bool) -> dict:
    project = db.get(Project, project_id)
    if project is None or project.workspace_id != actor.workspace_id:
        raise CampusError(ErrorCode.NOT_FOUND, "Projekt nicht gefunden")
    claims = db.execute(select(ClaimVersion).where(ClaimVersion.project_id == project_id).order_by(ClaimVersion.created_at)).scalars().all()
    ids = [c.id for c in claims]
    evidence = db.execute(select(EvidenceLink).where(EvidenceLink.claim_version_id.in_(ids))).scalars().all() if ids else []
    edges = db.execute(select(DependencyEdge).where(DependencyEdge.to_claim_version_id.in_(ids))).scalars().all() if ids and include_graph else []
    by_status: dict[str, int] = {}
    for c in claims:
        by_status[c.epistemic_status] = by_status.get(c.epistemic_status, 0) + 1
    return {
        "project_id": project_id,
        "counts_by_status": by_status,
        "claims": [{"claim_version_id": c.id, "claim_id": c.claim_id, "version": c.version, "local_id": c.local_id, "statement": c.statement,
                    "declared_kind": c.declared_kind, "stored_kind": c.stored_kind, "epistemic_status": c.epistemic_status,
                    "assessment_ref": c.assessment_ref, "validity_conditions": c.validity_conditions, "flags": c.flags,
                    "reassessment_required": c.reassessment_required, "run_attempt_id": c.run_attempt_id,
                    "producer_canonical_model_id": c.producer_canonical_model_id,
                    "evidence": [{"kind": e.kind, "source_passage_id": e.source_passage_id, "run_attempt_id": e.run_attempt_id,
                                  "experiment_run_id": e.experiment_run_id, "note": e.note}
                                 for e in evidence if e.claim_version_id == c.id]} for c in claims],
        "edges": [{"from": e.from_claim_version_id, "to": e.to_claim_version_id, "relation": e.relation} for e in edges],
    }
