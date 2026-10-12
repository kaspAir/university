"""Mandates, reserved decisions, policy, catalog, pricing, budget (Art. 9–11, chapters 8, 16)."""
from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from campus_contracts.models import ModelCatalog
from campus_contracts.policy import RoutingPolicy
from campus_contracts.validation import validate_document
from campus_domain.authz import authorize
from campus_domain.errors import CampusError, ErrorCode
from campus_domain.mandate import Condition, HumanDecisionInput, transition, validate_decision
from campus_domain.money import Money, parse_money
from campus_domain.statuses import ActivationState, DecisionOption, MandateState
from campus_infra import budget_service
from campus_infra.audit import Actor, record
from campus_infra.ids import new_id, utcnow
from campus_infra.models import (
    BudgetAccount,
    HumanDecision,
    MandateVersion,
    ModelCatalogRevision,
    PolicyVersion,
    PricingVersion,
    ProcessingProfile,
    QualificationRecord,
    Workspace,
)


def _hash(document: dict) -> str:
    return hashlib.sha256(json.dumps(document, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _own(db: Session, model, object_id: str, actor: Actor, what: str):
    row = db.get(model, object_id)
    if row is None or row.workspace_id != actor.workspace_id:
        raise CampusError(ErrorCode.NOT_FOUND, f"{what} nicht gefunden")
    return row


# ---------------------------------------------------------------- mandates

REQUIRED_MANDATE_FIELDS = (
    "title", "question_space", "knowledge_goal", "method_frame", "data_labels", "access_rules", "risk_rules",
    "responsible_user_id", "review_requirements", "duration_days", "report_points",
)


def create_mandate(db: Session, *, actor: Actor, document: dict, budget: str, currency: str, mandate_id: str | None,
                   correlation_id: str) -> MandateVersion:
    """Art. 9: a mandate names question space, goal, method frame, data classes, access/risk rules, responsibility,
    review requirements, budget, duration and report points. Missing elements are rejected, not defaulted."""
    authorize("mandate.create", actor.roles)
    missing = [f for f in REQUIRED_MANDATE_FIELDS if not document.get(f)]
    if missing:
        raise CampusError(ErrorCode.VALIDATION_ERROR, "Mandat unvollständig (Art. 9): " + ", ".join(missing),
                          blocked_action="mandate.create")
    money = parse_money(budget, currency)
    if money.amount_minor <= 0:
        raise CampusError(ErrorCode.VALIDATION_ERROR, "Mandatsbudget muss positiv sein")
    if mandate_id:
        last = db.execute(
            select(MandateVersion).where(MandateVersion.mandate_id == mandate_id, MandateVersion.workspace_id == actor.workspace_id)
            .order_by(MandateVersion.version.desc())
        ).scalars().first()
        if last is None:
            raise CampusError(ErrorCode.NOT_FOUND, "Mandat nicht gefunden")
        version = last.version + 1
    else:
        mandate_id, version = new_id("M"), 1
    row = MandateVersion(
        id=new_id("MV"), workspace_id=actor.workspace_id, mandate_id=mandate_id, version=version, document=document,
        document_hash=_hash(document), budget_limit_minor=money.amount_minor, currency=money.currency,
        state=MandateState.DRAFT.value, created_by=actor.actor_id,
    )
    db.add(row)
    db.flush()
    record(db, actor=actor, command="mandate.create", object_type="mandate", object_id=mandate_id,
           object_version=str(version), correlation_id=correlation_id, payload={"mandate_version_id": row.id})
    return row


def decide_mandate(db: Session, *, actor: Actor, mandate_version_id: str, expected_version: int, option: str,
                   rationale: str, alternatives_presented: list[str], remaining_objections: list[str], consequences: str,
                   conditions: list[dict], correlation_id: str) -> tuple[MandateVersion, HumanDecision, BudgetAccount | None]:
    """Art. 11: a reserved human decision with four real options; approval opens the root budget."""
    authorize("mandate.decide", actor.roles)
    row = _own(db, MandateVersion, mandate_version_id, actor, "Mandatsfassung")
    if row.version != expected_version:
        raise CampusError(ErrorCode.VERSION_CONFLICT, f"Erwartete Fassung {expected_version}, aktuell {row.version}",
                          blocked_action="mandate.decide")
    try:
        opt = DecisionOption(option)
    except ValueError as exc:
        raise CampusError(ErrorCode.VALIDATION_ERROR, f"Unbekannte Option {option}") from exc
    decision_input = HumanDecisionInput(
        option=opt, rationale=rationale, alternatives_presented=alternatives_presented,
        remaining_objections=remaining_objections, consequences=consequences,
        conditions=[Condition(**c) for c in conditions],
    )
    validate_decision(decision_input)
    new_state, needs_new_version = transition(MandateState(row.state), opt)
    decision = HumanDecision(
        id=new_id("HD"), workspace_id=actor.workspace_id, decision_type="mandate_approval", object_type="mandate_version",
        object_id=row.id, object_version=row.version, object_hash=row.document_hash, option=opt.value, rationale=rationale,
        alternatives_presented=alternatives_presented, remaining_objections=remaining_objections, consequences=consequences,
        conditions=conditions, decided_by=actor.actor_id,
    )
    db.add(decision)
    row.state = new_state.value
    root: BudgetAccount | None = None
    if new_state == MandateState.APPROVED:
        root = BudgetAccount(
            id=new_id("B"), workspace_id=actor.workspace_id, parent_id=None, mandate_version_id=row.id,
            name=f"Mandat {row.mandate_id} v{row.version}", limit_minor=row.budget_limit_minor, currency=row.currency, depth=0,
        )
        db.add(root)
    db.flush()
    record(db, actor=actor, command="mandate.decide", object_type="mandate", object_id=row.mandate_id,
           object_version=str(row.version), correlation_id=correlation_id,
           payload={"option": opt.value, "decision_id": decision.id, "new_state": new_state.value,
                    "needs_new_version": needs_new_version, "root_budget_id": root.id if root else None})
    return row, decision, root


# ---------------------------------------------------------------- policy

def import_policy(db: Session, *, actor: Actor, document: dict, correlation_id: str) -> PolicyVersion:
    authorize("policy.import", actor.roles)
    try:
        policy = RoutingPolicy.model_validate(document)
    except Exception as exc:  # pydantic ValidationError
        raise CampusError(ErrorCode.VALIDATION_ERROR, f"Routing-Policy ungültig: {exc}", blocked_action="policy.import") from exc
    if policy.activation_state != "draft":
        raise CampusError(ErrorCode.VALIDATION_ERROR, "Import nur als Entwurf; Aktivierung ist ein eigener Entscheid",
                          blocked_action="policy.import")
    if db.execute(select(PolicyVersion).where(PolicyVersion.workspace_id == actor.workspace_id,
                                              PolicyVersion.revision == policy.revision)).first():
        raise CampusError(ErrorCode.VERSION_CONFLICT, f"Policy-Revision {policy.revision} existiert bereits; Änderungen brauchen eine neue Revision",
                          blocked_action="policy.import")
    row = PolicyVersion(id=new_id("POL"), workspace_id=actor.workspace_id, revision=policy.revision,
                        document=policy.model_dump(), state=ActivationState.DRAFT.value, created_by=actor.actor_id)
    db.add(row)
    db.flush()
    record(db, actor=actor, command="policy.import", object_type="policy", object_id=row.id, object_version=policy.revision,
           correlation_id=correlation_id)
    return row


def activate_policy(db: Session, *, actor: Actor, policy_id: str, correlation_id: str) -> PolicyVersion:
    """Protection/review standards are reserved to humans (Art. 11): research director activates."""
    authorize("policy.activate", actor.roles)
    row = _own(db, PolicyVersion, policy_id, actor, "Policy")
    if row.state != ActivationState.DRAFT.value:
        raise CampusError(ErrorCode.INVALID_TRANSITION, f"Policy ist {row.state}")
    for active in db.execute(select(PolicyVersion).where(PolicyVersion.workspace_id == actor.workspace_id,
                                                           PolicyVersion.state == ActivationState.ACTIVE.value)).scalars():
        active.state = ActivationState.RETIRED.value
    row.state = ActivationState.ACTIVE.value
    row.activated_at = utcnow()
    row.activated_by = actor.actor_id
    doc = dict(row.document)
    doc["activation_state"] = "active"
    row.document = doc
    db.flush()
    record(db, actor=actor, command="policy.activate", object_type="policy", object_id=row.id, object_version=row.revision,
           correlation_id=correlation_id, payload={"missing_limits": RoutingPolicy.model_validate(doc).limits.missing()})
    return row


def active_policy(db: Session, workspace_id: str) -> PolicyVersion | None:
    return db.execute(select(PolicyVersion).where(PolicyVersion.workspace_id == workspace_id,
                                                  PolicyVersion.state == ActivationState.ACTIVE.value)).scalar_one_or_none()


# ---------------------------------------------------------------- catalog & pricing

def import_catalog(db: Session, *, actor: Actor, document: dict, correlation_id: str) -> ModelCatalogRevision:
    authorize("catalog.import", actor.roles)
    violations = validate_document("model_catalog", document)
    if violations:
        raise CampusError(ErrorCode.VALIDATION_ERROR, "Katalog verletzt das Schema", blocked_action="catalog.import",
                          details={"violations": violations})
    catalog = ModelCatalog.model_validate(document)
    ws = db.get(Workspace, actor.workspace_id)
    if catalog.purpose == "test_fixture" and ws is not None and ws.capability_level != "development_mock":
        raise CampusError(ErrorCode.VALIDATION_ERROR, "Fixture-Kataloge nur in development_mock", blocked_action="catalog.import")
    if db.execute(select(ModelCatalogRevision).where(ModelCatalogRevision.workspace_id == actor.workspace_id,
                                                      ModelCatalogRevision.revision == catalog.revision)).first():
        raise CampusError(ErrorCode.VERSION_CONFLICT, f"Katalog-Revision {catalog.revision} existiert bereits; neue Revision nötig",
                          blocked_action="catalog.import")
    from campus_gateway.endpoints import check_api_base

    for dep in catalog.deployments:
        if dep.enabled:
            check_api_base(dep.adapter, dep.api_base)  # keys are bound to approved endpoints at import time (S1.1 finding 5)
    canonical: dict[str, set[str]] = {}
    for dep in catalog.deployments:
        if dep.canonical_model_id:
            canonical.setdefault(dep.canonical_model_id, set()).add(dep.id)
    row = ModelCatalogRevision(id=new_id("CAT"), workspace_id=actor.workspace_id, revision=catalog.revision,
                               document=catalog.model_dump(), state=ActivationState.DRAFT.value, created_by=actor.actor_id)
    db.add(row)
    db.flush()
    record(db, actor=actor, command="catalog.import", object_type="model_catalog", object_id=row.id, object_version=catalog.revision,
           correlation_id=correlation_id,
           payload={"aliases": {k: sorted(v) for k, v in canonical.items() if len(v) > 1}})
    return row


def catalog_activation_violations(db: Session, workspace_id: str, catalog: ModelCatalog) -> list[str]:
    """S1 gate (chapters 8, 20): every enabled deployment needs a bound price, an approved data-processing profile
    that covers its data labels and provider, and, for runtime catalogs, resolvable qualification evidence."""
    violations: list[str] = []
    prices = {p.pricing_ref for p in db.execute(select(PricingVersion).where(PricingVersion.workspace_id == workspace_id)).scalars()}
    profiles = {p.ref: p for p in db.execute(select(ProcessingProfile).where(ProcessingProfile.workspace_id == workspace_id)).scalars()}
    quals = {q.ref: q for q in db.execute(select(QualificationRecord).where(QualificationRecord.workspace_id == workspace_id)).scalars()}
    for dep in catalog.deployments:
        if not dep.enabled:
            continue
        if dep.pricing_ref not in prices:
            violations.append(f"{dep.id}: pricing_ref {dep.pricing_ref!r} ist keine registrierte Preisfassung (Preisbindung)")
        profile = profiles.get(dep.processing_profile_ref or "")
        if profile is None:
            violations.append(f"{dep.id}: processing_profile_ref {dep.processing_profile_ref!r} ist nicht als Datenfreigabe genehmigt")
        else:
            if profile.provider_id != dep.provider_id:
                violations.append(f"{dep.id}: Datenfreigabe {profile.ref} gilt für Provider {profile.provider_id}, nicht {dep.provider_id}")
            missing = set(dep.allowed_data_labels) - set(profile.allowed_data_labels)
            if missing:
                violations.append(f"{dep.id}: Datenklassen {sorted(missing)} sind in der Datenfreigabe {profile.ref} nicht gedeckt")
        if catalog.purpose == "runtime_snapshot":
            for ref in dep.qualification_refs:
                rec = quals.get(ref)
                if rec is None:
                    violations.append(f"{dep.id}: qualification_ref {ref!r} existiert nicht als Nachweis")
                elif rec.canonical_model_id != dep.canonical_model_id:
                    violations.append(f"{dep.id}: Nachweis {ref} gehört zu {rec.canonical_model_id}, nicht zu {dep.canonical_model_id}")
                elif not rec.passed:
                    violations.append(f"{dep.id}: Nachweis {ref} ist nicht bestanden")
    return violations


def activate_catalog(db: Session, *, actor: Actor, catalog_id: str, correlation_id: str) -> ModelCatalogRevision:
    """Administrative access is separate from substantive qualification (chapter 8). Activation refuses unbound prices,
    missing data authorizations and, for runtime catalogs, unresolvable qualification evidence."""
    authorize("catalog.activate", actor.roles)
    row = _own(db, ModelCatalogRevision, catalog_id, actor, "Katalog")
    if row.state != ActivationState.DRAFT.value:
        raise CampusError(ErrorCode.INVALID_TRANSITION, f"Katalog ist {row.state}")
    violations = catalog_activation_violations(db, actor.workspace_id, ModelCatalog.model_validate(row.document))
    if violations:
        raise CampusError(ErrorCode.VALIDATION_ERROR,
                          "Katalog kann nicht aktiviert werden: Datenfreigabe, Preisbindung oder Nachweise fehlen",
                          blocked_action="catalog.activate",
                          required_resolution="Preisfassung, Datenfreigabe-Profil und Nachweise registrieren, dann erneut aktivieren",
                          details={"violations": violations})
    for active in db.execute(select(ModelCatalogRevision).where(ModelCatalogRevision.workspace_id == actor.workspace_id,
                                                                  ModelCatalogRevision.state == ActivationState.ACTIVE.value)).scalars():
        active.state = ActivationState.RETIRED.value
    row.state = ActivationState.ACTIVE.value
    row.activated_at = utcnow()
    row.activated_by = actor.actor_id
    db.flush()
    record(db, actor=actor, command="catalog.activate", object_type="model_catalog", object_id=row.id,
           object_version=row.revision, correlation_id=correlation_id)
    return row


def active_catalog(db: Session, workspace_id: str) -> ModelCatalogRevision | None:
    return db.execute(select(ModelCatalogRevision).where(ModelCatalogRevision.workspace_id == workspace_id,
                                                         ModelCatalogRevision.state == ActivationState.ACTIVE.value)).scalar_one_or_none()


def set_pricing(db: Session, *, actor: Actor, pricing_ref: str, currency: str, input_per_million: str,
                output_per_million: str, source_note: str, correlation_id: str) -> PricingVersion:
    authorize("pricing.set", actor.roles)
    if not source_note.strip():
        raise CampusError(ErrorCode.VALIDATION_ERROR, "Preisfassung braucht eine datierte Quellenangabe")
    inp = parse_money(input_per_million, currency)
    out = parse_money(output_per_million, currency)
    existing = db.execute(select(PricingVersion).where(PricingVersion.workspace_id == actor.workspace_id,
                                                        PricingVersion.pricing_ref == pricing_ref)).scalar_one_or_none()
    if existing is not None:
        raise CampusError(ErrorCode.VERSION_CONFLICT, "pricing_ref existiert bereits; neue Fassung braucht neue Kennung")
    row = PricingVersion(id=new_id("PR"), workspace_id=actor.workspace_id, pricing_ref=pricing_ref, currency=currency,
                         input_per_million_minor=inp.amount_minor, output_per_million_minor=out.amount_minor,
                         source_note=source_note, created_by=actor.actor_id)
    db.add(row)
    db.flush()
    record(db, actor=actor, command="pricing.set", object_type="pricing", object_id=pricing_ref, object_version=None,
           correlation_id=correlation_id)
    return row


# ---------------------------------------------------------------- budget

def create_child_budget(db: Session, *, actor: Actor, parent_id: str, name: str, limit: str, currency: str,
                        protected_kind: str | None, correlation_id: str) -> BudgetAccount:
    authorize("budget.create_child", actor.roles)
    _own(db, BudgetAccount, parent_id, actor, "Budgetkonto")
    child = budget_service.create_child(db, parent_id=parent_id, name=name, limit=parse_money(limit, currency),
                                        protected_kind=protected_kind)
    record(db, actor=actor, command="budget.create_child", object_type="budget_account", object_id=child.id,
           object_version=None, correlation_id=correlation_id, payload={"parent_id": parent_id, "limit_minor": child.limit_minor})
    return child


def budget_tree(db: Session, workspace_id: str) -> list[dict]:
    return budget_service.tree(db, workspace_id)


def money_of(account: BudgetAccount) -> Money:
    return Money(account.limit_minor, account.currency)


# ---------------------------------------------------------------- S1: data authorization and qualification evidence

DATA_LABELS = {"public", "internal", "confidential", "personal"}


def approve_processing_profile(db: Session, *, actor: Actor, ref: str, provider_id: str, region: str,
                               allowed_data_labels: list[str], note: str, correlation_id: str) -> ProcessingProfile:
    """A data-processing authorization is a protection standard: reserved to the research director (Art. 11, chapter 12)."""
    authorize("processing_profile.approve", actor.roles)
    labels = sorted(set(allowed_data_labels))
    bad = [label for label in labels if label not in DATA_LABELS]
    if bad or not labels:
        raise CampusError(ErrorCode.VALIDATION_ERROR, f"Ungültige Datenklassen: {bad or 'keine'}")
    if not note.strip():
        raise CampusError(ErrorCode.VALIDATION_ERROR, "Datenfreigabe braucht eine Begründung (Vertrag, Region, Grenzen)")
    if db.execute(select(ProcessingProfile).where(ProcessingProfile.workspace_id == actor.workspace_id,
                                                  ProcessingProfile.ref == ref)).first():
        raise CampusError(ErrorCode.VERSION_CONFLICT, "Profil existiert; Änderungen brauchen eine neue Kennung")
    row = ProcessingProfile(id=new_id("PP"), workspace_id=actor.workspace_id, ref=ref, provider_id=provider_id, region=region,
                            allowed_data_labels=labels, note=note, approved_by=actor.actor_id)
    db.add(row)
    db.flush()
    record(db, actor=actor, command="processing_profile.approve", object_type="processing_profile", object_id=ref,
           object_version=None, correlation_id=correlation_id,
           payload={"provider_id": provider_id, "region": region, "allowed_data_labels": labels})
    return row


BOOTSTRAP_ROLES = {"reviewer", "evaluator"}


def record_qualification(db: Session, *, actor: Actor, ref: str, canonical_model_id: str, task_class: str, suite_ref: str,
                         result_summary: str, passed: bool, evaluated_by: str, evaluated_by_kind: str,
                         correlation_id: str, profile_ref: str | None = None, skill_refs: list[str] | None = None,
                         kind: str | None = None, role: str | None = None) -> QualificationRecord:
    """Qualification evidence. Two kinds, never mixed: `bootstrap` = human approval of a canonical model for ONE checking role
    (`reviewer` | `evaluator`) and task class (ends the certification regress, research director only; never qualifies a producer);
    `combination` = producer evidence binding profile AND the exact skill set AND the model AND the task class.
    A model never certifies itself (T12, Art. 19)."""
    authorize("qualification.record", actor.roles)
    if evaluated_by_kind not in {"model", "human"}:
        raise CampusError(ErrorCode.VALIDATION_ERROR, "evaluated_by_kind muss model oder human sein")
    if evaluated_by_kind == "model" and evaluated_by == canonical_model_id:
        raise CampusError(ErrorCode.QUALIFICATION_MISSING, "Ein Modell kann sich nicht selbst qualifizieren (T12)",
                          blocked_action="qualification.record")
    if kind is None:
        kind = "combination" if (profile_ref is not None or skill_refs is not None) else "bootstrap"
    if kind not in {"bootstrap", "combination"}:
        raise CampusError(ErrorCode.VALIDATION_ERROR, "kind muss bootstrap oder combination sein")
    if kind == "bootstrap":
        authorize("qualification.bootstrap", actor.roles)
        if evaluated_by_kind != "human" or profile_ref is not None or skill_refs is not None:
            raise CampusError(ErrorCode.VALIDATION_ERROR, "Bootstrap-Freigaben sind menschlich und binden weder Profil noch Skills",
                              blocked_action="qualification.record")
        if role not in BOOTSTRAP_ROLES:
            raise CampusError(ErrorCode.VALIDATION_ERROR, "Bootstrap-Freigaben gelten für genau eine Prüfrolle: role muss reviewer oder evaluator sein; "
                              "Erzeuger werden nie per Freigabe qualifiziert", blocked_action="qualification.record")
    else:
        if profile_ref is None or skill_refs is None:
            raise CampusError(ErrorCode.VALIDATION_ERROR, "Kombinationsnachweise binden Profil UND Skillmenge gemeinsam (Skillmenge darf leer sein)",
                              blocked_action="qualification.record")
        if role not in {None, "producer"}:
            raise CampusError(ErrorCode.VALIDATION_ERROR, "Kombinationsnachweise sind Erzeugernachweise (role producer); eine Prüfrolle wird nur per "
                              "Bootstrap-Freigabe erteilt", blocked_action="qualification.record")
        role = "producer"
    if not (suite_ref.strip() and result_summary.strip()):
        raise CampusError(ErrorCode.VALIDATION_ERROR, "Prüfsatz und Ergebnis sind Pflicht; ein Nachweis ohne Testsatz ist keiner")
    if db.execute(select(QualificationRecord).where(QualificationRecord.workspace_id == actor.workspace_id,
                                                    QualificationRecord.ref == ref)).first():
        raise CampusError(ErrorCode.VERSION_CONFLICT, "Nachweis existiert bereits")
    row = QualificationRecord(id=new_id("Q"), workspace_id=actor.workspace_id, ref=ref, canonical_model_id=canonical_model_id,
                              task_class=task_class, suite_ref=suite_ref, result_summary=result_summary, passed=passed,
                              evaluated_by=evaluated_by, evaluated_by_kind=evaluated_by_kind, profile_ref=profile_ref,
                              skill_refs=sorted(set(skill_refs)) if skill_refs is not None else None, kind=kind, role=role, created_by=actor.actor_id)
    db.add(row)
    db.flush()
    record(db, actor=actor, command="qualification.record", object_type="qualification", object_id=ref, object_version=None,
           correlation_id=correlation_id, payload={"canonical_model_id": canonical_model_id, "task_class": task_class, "passed": passed,
                                                   "kind": kind, "role": role, "profile_ref": profile_ref, "skill_refs": skill_refs})
    return row
