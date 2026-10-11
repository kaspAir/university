from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from campus_domain.errors import CampusError, ErrorCode
from campus_domain.money import Money
from campus_infra.audit import Actor
from campus_infra.models import HumanDecision, MandateVersion, ModelCatalogRevision, PolicyVersion

from .. import services_governance as svc
from ..deps import current_actor, execute_command, get_db

router = APIRouter(tags=["governance"])


def _mandate_view(row: MandateVersion) -> dict:
    return {"mandate_version_id": row.id, "mandate_id": row.mandate_id, "version": row.version, "state": row.state,
            "document": row.document, "document_hash": row.document_hash,
            "budget": Money(row.budget_limit_minor, row.currency).format(), "budget_minor": row.budget_limit_minor,
            "currency": row.currency, "created_by": row.created_by, "created_at": row.created_at}


class MandateBody(BaseModel):
    document: dict
    budget: str = Field(min_length=1, description="Decimal text, e.g. '12000.00'")
    currency: str = Field(min_length=3, max_length=3)
    mandate_id: str | None = None


@router.post("/mandates", status_code=201)
def create_mandate(body: MandateBody, request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    def handler(correlation: str):
        row = svc.create_mandate(db, actor=actor, document=body.document, budget=body.budget, currency=body.currency,
                                 mandate_id=body.mandate_id, correlation_id=correlation)
        return 201, _mandate_view(row)
    return execute_command(db, actor, request, command="mandate.create", payload=body, handler=handler, object_type="mandate")


class ConditionBody(BaseModel):
    text: str
    responsible: str
    due: str


class DecisionBody(BaseModel):
    expected_version: int
    option: str = Field(description="accept | accept_limited | revise | reject")
    rationale: str
    alternatives_presented: list[str]
    remaining_objections: list[str]
    consequences: str
    conditions: list[ConditionBody] = []


@router.post("/mandates/{mandate_version_id}/approve")
def decide_mandate(mandate_version_id: str, body: DecisionBody, request: Request, actor: Actor = Depends(current_actor),
                   db: Session = Depends(get_db)):
    """Reserved human decision (Art. 11). The path keeps the architecture's name; the body carries all four options."""
    def handler(correlation: str):
        row, decision, root = svc.decide_mandate(
            db, actor=actor, mandate_version_id=mandate_version_id, expected_version=body.expected_version, option=body.option,
            rationale=body.rationale, alternatives_presented=body.alternatives_presented,
            remaining_objections=body.remaining_objections, consequences=body.consequences,
            conditions=[c.model_dump() for c in body.conditions], correlation_id=correlation,
        )
        view = _mandate_view(row)
        view.update({"decision_id": decision.id, "option": decision.option, "root_budget_account_id": root.id if root else None})
        return 200, view
    return execute_command(db, actor, request, command="mandate.decide", payload=body, handler=handler,
                           object_type="mandate_version", object_id=mandate_version_id)


@router.get("/mandates")
def list_mandates(actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.execute(select(MandateVersion).where(MandateVersion.workspace_id == actor.workspace_id)
                      .order_by(MandateVersion.mandate_id, MandateVersion.version)).scalars().all()
    return [_mandate_view(r) for r in rows]


@router.get("/mandates/{mandate_version_id}")
def get_mandate(mandate_version_id: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> dict:
    row = db.get(MandateVersion, mandate_version_id)
    if row is None or row.workspace_id != actor.workspace_id:
        raise CampusError(ErrorCode.NOT_FOUND, "Mandatsfassung nicht gefunden")
    decisions = db.execute(select(HumanDecision).where(HumanDecision.object_id == row.id).order_by(HumanDecision.decided_at)).scalars().all()
    view = _mandate_view(row)
    view["decisions"] = jsonable_encoder([{k: v for k, v in d.__dict__.items() if not k.startswith("_")} for d in decisions])
    return view


@router.get("/decisions")
def open_decisions(state: str = "open", actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> list[dict]:
    """Aggregated «Wartet auf mich» (UI handoff §6.2). S0: open mandate drafts only."""
    if state != "open":
        raise CampusError(ErrorCode.VALIDATION_ERROR, "Nur state=open wird unterstützt")
    rows = db.execute(select(MandateVersion).where(MandateVersion.workspace_id == actor.workspace_id,
                                                   MandateVersion.state == "draft").order_by(MandateVersion.created_at)).scalars().all()
    return [{"kind": "Mandat", "object_type": "mandate_version", "object_id": r.id, "version": r.version,
             "title": r.document.get("title"), "due": r.document.get("decision_due"), "budget": Money(r.budget_limit_minor, r.currency).format(),
             "command": f"/api/v1/mandates/{r.id}/approve"} for r in rows]


class PolicyBody(BaseModel):
    document: dict


@router.post("/policies", status_code=201)
def import_policy(body: PolicyBody, request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    def handler(correlation: str):
        row = svc.import_policy(db, actor=actor, document=body.document, correlation_id=correlation)
        return 201, {"policy_id": row.id, "revision": row.revision, "state": row.state}
    return execute_command(db, actor, request, command="policy.import", payload=body, handler=handler, object_type="policy")


@router.post("/policies/{policy_id}/activate")
def activate_policy(policy_id: str, request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    def handler(correlation: str):
        row = svc.activate_policy(db, actor=actor, policy_id=policy_id, correlation_id=correlation)
        return 200, {"policy_id": row.id, "revision": row.revision, "state": row.state, "document": row.document}
    return execute_command(db, actor, request, command="policy.activate", payload={"policy_id": policy_id}, handler=handler,
                           object_type="policy", object_id=policy_id)


@router.get("/policies")
def list_policies(actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.execute(select(PolicyVersion).where(PolicyVersion.workspace_id == actor.workspace_id).order_by(PolicyVersion.created_at)).scalars().all()
    return [{"policy_id": r.id, "revision": r.revision, "state": r.state, "document": r.document, "activated_at": r.activated_at} for r in rows]


class CatalogBody(BaseModel):
    document: dict


@router.put("/model-catalog/drafts", status_code=201)
def import_catalog(body: CatalogBody, request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    def handler(correlation: str):
        row = svc.import_catalog(db, actor=actor, document=body.document, correlation_id=correlation)
        return 201, {"catalog_id": row.id, "revision": row.revision, "state": row.state}
    return execute_command(db, actor, request, command="catalog.import", payload=body, handler=handler, object_type="model_catalog")


@router.post("/model-catalog/{catalog_id}/activate")
def activate_catalog(catalog_id: str, request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    def handler(correlation: str):
        row = svc.activate_catalog(db, actor=actor, catalog_id=catalog_id, correlation_id=correlation)
        return 200, {"catalog_id": row.id, "revision": row.revision, "state": row.state}
    return execute_command(db, actor, request, command="catalog.activate", payload={"catalog_id": catalog_id}, handler=handler,
                           object_type="model_catalog", object_id=catalog_id)


@router.get("/model-catalog/status")
def catalog_status(actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> dict:
    """Operating view for the cockpit: identity, access status, capabilities as supported/unsupported/unknown. No secrets."""
    rows = db.execute(select(ModelCatalogRevision).where(ModelCatalogRevision.workspace_id == actor.workspace_id)
                      .order_by(ModelCatalogRevision.created_at)).scalars().all()
    active = next((r for r in rows if r.state == "active"), None)
    deployments = []
    if active:
        for d in active.document["deployments"]:
            deployments.append({k: v for k, v in d.items() if k != "secret_ref"} | {"secret_configured": bool(d.get("secret_ref"))})
    return {"active_revision": active.revision if active else None, "active_catalog_id": active.id if active else None,
            "revisions": [{"catalog_id": r.id, "revision": r.revision, "state": r.state} for r in rows], "deployments": deployments}


@router.get("/model-catalog/discovery")
def discover_models(adapter: str, secret_ref: str, api_base: str | None = None, actor: Actor = Depends(current_actor)) -> dict:
    """Native model ids a provider reports. Discovery is neither data authorization nor qualification (chapter 8)."""
    from .. import services_research

    ids = services_research.discover_models(actor=actor, adapter_name=adapter, secret_ref=secret_ref, api_base=api_base)
    return {"adapter": adapter, "model_ids": ids, "note": "Discovery ist weder Datenfreigabe noch Qualifikation."}


class ProcessingProfileBody(BaseModel):
    ref: str = Field(min_length=1)
    provider_id: str = Field(min_length=1)
    region: str = Field(min_length=1)
    allowed_data_labels: list[str] = Field(min_length=1)
    note: str = Field(min_length=1)


@router.post("/processing-profiles", status_code=201)
def approve_processing_profile(body: ProcessingProfileBody, request: Request, actor: Actor = Depends(current_actor),
                               db: Session = Depends(get_db)):
    def handler(correlation: str):
        row = svc.approve_processing_profile(db, actor=actor, ref=body.ref, provider_id=body.provider_id, region=body.region,
                                             allowed_data_labels=body.allowed_data_labels, note=body.note, correlation_id=correlation)
        return 201, {"processing_profile_id": row.id, "ref": row.ref, "provider_id": row.provider_id, "region": row.region,
                     "allowed_data_labels": row.allowed_data_labels, "approved_by": row.approved_by}
    return execute_command(db, actor, request, command="processing_profile.approve", payload=body, handler=handler,
                           object_type="processing_profile")


@router.get("/processing-profiles")
def list_processing_profiles(actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> list[dict]:
    from campus_infra.models import ProcessingProfile

    rows = db.execute(select(ProcessingProfile).where(ProcessingProfile.workspace_id == actor.workspace_id)
                      .order_by(ProcessingProfile.created_at)).scalars().all()
    return [{"ref": r.ref, "provider_id": r.provider_id, "region": r.region, "allowed_data_labels": r.allowed_data_labels,
             "note": r.note, "approved_by": r.approved_by, "created_at": r.created_at} for r in rows]


class QualificationBody(BaseModel):
    ref: str = Field(min_length=1)
    canonical_model_id: str = Field(min_length=1)
    task_class: str = Field(min_length=1)
    suite_ref: str = Field(min_length=1)
    result_summary: str = Field(min_length=1)
    passed: bool
    evaluated_by: str = Field(min_length=1)
    evaluated_by_kind: str = Field(description="model | human")


@router.post("/qualifications", status_code=201)
def record_qualification(body: QualificationBody, request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    def handler(correlation: str):
        row = svc.record_qualification(db, actor=actor, ref=body.ref, canonical_model_id=body.canonical_model_id, task_class=body.task_class,
                                       suite_ref=body.suite_ref, result_summary=body.result_summary, passed=body.passed,
                                       evaluated_by=body.evaluated_by, evaluated_by_kind=body.evaluated_by_kind, correlation_id=correlation)
        return 201, {"qualification_id": row.id, "ref": row.ref, "canonical_model_id": row.canonical_model_id, "task_class": row.task_class,
                     "passed": row.passed, "evaluated_by": row.evaluated_by, "evaluated_by_kind": row.evaluated_by_kind}
    return execute_command(db, actor, request, command="qualification.record", payload=body, handler=handler, object_type="qualification")


@router.get("/qualifications")
def list_qualifications(actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> list[dict]:
    from campus_infra.models import QualificationRecord

    rows = db.execute(select(QualificationRecord).where(QualificationRecord.workspace_id == actor.workspace_id)
                      .order_by(QualificationRecord.created_at)).scalars().all()
    return [{"ref": r.ref, "canonical_model_id": r.canonical_model_id, "task_class": r.task_class, "suite_ref": r.suite_ref,
             "result_summary": r.result_summary, "passed": r.passed, "evaluated_by": r.evaluated_by,
             "evaluated_by_kind": r.evaluated_by_kind, "created_at": r.created_at} for r in rows]


class PricingBody(BaseModel):
    pricing_ref: str
    currency: str = Field(min_length=3, max_length=3)
    input_per_million: str
    output_per_million: str
    source_note: str


@router.post("/pricing", status_code=201)
def set_pricing(body: PricingBody, request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    def handler(correlation: str):
        row = svc.set_pricing(db, actor=actor, pricing_ref=body.pricing_ref, currency=body.currency,
                              input_per_million=body.input_per_million, output_per_million=body.output_per_million,
                              source_note=body.source_note, correlation_id=correlation)
        return 201, {"pricing_ref": row.pricing_ref, "currency": row.currency, "input_per_million_minor": row.input_per_million_minor,
                     "output_per_million_minor": row.output_per_million_minor}
    return execute_command(db, actor, request, command="pricing.set", payload=body, handler=handler, object_type="pricing")


class ChildBudgetBody(BaseModel):
    name: str
    limit: str
    currency: str = Field(min_length=3, max_length=3)
    protected_kind: str | None = None


@router.post("/budget/accounts/{parent_id}/children", status_code=201)
def create_child_budget(parent_id: str, body: ChildBudgetBody, request: Request, actor: Actor = Depends(current_actor),
                        db: Session = Depends(get_db)):
    def handler(correlation: str):
        child = svc.create_child_budget(db, actor=actor, parent_id=parent_id, name=body.name, limit=body.limit, currency=body.currency,
                                        protected_kind=body.protected_kind, correlation_id=correlation)
        return 201, {"budget_account_id": child.id, "parent_id": child.parent_id, "limit": Money(child.limit_minor, child.currency).format(),
                     "limit_minor": child.limit_minor, "currency": child.currency, "protected_kind": child.protected_kind}
    return execute_command(db, actor, request, command="budget.create_child", payload=body, handler=handler,
                           object_type="budget_account", object_id=parent_id)


@router.get("/budget/tree")
def budget_tree(actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> list[dict]:
    return svc.budget_tree(db, actor.workspace_id)
