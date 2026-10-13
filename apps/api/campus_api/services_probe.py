"""Operational probe of ONE catalog deployment: a single small real call through the gateway, journaled like every other call.

What it proves (and nothing more): the key resolves inside the gateway process, the approved endpoint answers, the provider reports
an effective model id and usage, and the technically enforced structured output satisfies a campus contract. It is operations
evidence for taking a provider into service (S1 gate, docs/S1_UMSETZUNG.md section 3). It is not research, not a qualification and
not a task: no claims, no artifact, no reservation in the budget tree. The cost cap is enforced here from the bound price BEFORE the
call; the settled cost is written to the audit trail and the journal entry carries the probe marker in its routing decision id, so
the operations view can tell probes from research calls.

The route the gateway demands is built by the trusted core's own `build_decision` and is not persisted as a task route (there is no
task). The adapter is the one the catalog names; the mock adapter yields a response marked synthetic, which the probe reports as such.
"""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from campus_contracts.models import CandidateAssessment, Deployment, ModelCatalog, RouteProposal
from campus_contracts.validation import provider_schema
from campus_domain import routing
from campus_domain.authz import authorize
from campus_domain.budget import estimate_cost
from campus_domain.errors import CampusError, ErrorCode
from campus_domain.money import parse_money
from campus_gateway.port import LLMRequest
from campus_infra.audit import Actor, record
from campus_infra.ids import new_id, utcnow
from campus_infra.models import ModelCatalogRevision

from .services_research import _gateway, _pricing

PROBE_DECISION_PREFIX = "RD-PROBE-"  # journal entries of probes carry this prefix in routing_decision_id
PROBE_INPUT_TOKENS_ESTIMATE = 700  # system rules plus the small candidate list below; the cap check uses this, settlement uses real usage
PROBE_MAX_OUTPUT_TOKENS = 400

SYSTEM_RULES = (
    "Betriebsprobe eines Forschungscampus, kein Forschungsauftrag. Wähle genau einen der zwei Kandidaten aus der Liste und antworte "
    "ausschliesslich im vorgegebenen JSON-Format. Nenne als evidence_refs nur Kennungen, die beim gewählten Kandidaten stehen."
)


def is_probe_decision(routing_decision_id: str | None) -> bool:
    return bool(routing_decision_id) and str(routing_decision_id).startswith(PROBE_DECISION_PREFIX)


def find_deployment(db: Session, workspace_id: str, deployment_id: str) -> tuple[ModelCatalogRevision, Deployment]:
    """The deployment as the newest catalog revision (draft or active) of this workspace registers it. Draft deployments may be
    probed on purpose: the probe is part of taking a deployment into service, before activation."""
    revisions = db.execute(select(ModelCatalogRevision).where(ModelCatalogRevision.workspace_id == workspace_id)
                           .order_by(ModelCatalogRevision.created_at.desc())).scalars().all()
    for rev in revisions:
        if any(d["id"] == deployment_id for d in rev.document["deployments"]):
            return rev, ModelCatalog.model_validate(rev.document).deployment(deployment_id)
    raise CampusError(ErrorCode.NOT_FOUND, "Deployment ist in keiner Katalogfassung dieses Workspace registriert", blocked_action="catalog.probe")


def probe_deployment(db: Session, *, actor: Actor, deployment_id: str, max_cost: str, currency: str, correlation_id: str) -> dict:
    authorize("catalog.probe", actor.roles)
    revision, deployment = find_deployment(db, actor.workspace_id, deployment_id)
    if not deployment.canonical_model_id or not deployment.provider_model_id:
        raise CampusError(ErrorCode.VALIDATION_ERROR, "Probe nur für vollständig konfigurierte Deployments (provider_model_id und canonical_model_id)",
                          blocked_action="catalog.probe")
    pricing = _pricing(db, actor.workspace_id, deployment.pricing_ref)
    cap = parse_money(max_cost, currency)
    estimate = estimate_cost(pricing, PROBE_INPUT_TOKENS_ESTIMATE, PROBE_MAX_OUTPUT_TOKENS)  # unknown price -> PRICING_UNKNOWN, never zero
    if estimate.currency != cap.currency:
        raise CampusError(ErrorCode.VALIDATION_ERROR, f"Preisfassung in {estimate.currency}, Obergrenze in {cap.currency}", blocked_action="catalog.probe")
    if estimate.amount_minor > cap.amount_minor:
        raise CampusError(ErrorCode.BUDGET_EXHAUSTED, f"Geschätzte Probekosten {estimate.format()} übersteigen die Obergrenze {cap.format()}",
                          blocked_action="catalog.probe", required_resolution="Obergrenze bewusst erhöhen oder Deployment nicht proben")
    probe_id = new_id("PB")
    decision_id = PROBE_DECISION_PREFIX + probe_id
    proposal = RouteProposal(task_id=probe_id, selected_deployment_id=deployment.id, evidence_refs=[], alternatives=[],
                             rationale="Betriebsprobe: ein kleiner Aufruf über das Gateway; keine Eignungsaussage, kein Forschungslauf.",
                             uncertainties=["Probe prüft Zugang, Journal, Nutzungswerte und Ausgabeformat, nicht die fachliche Eignung."])
    route = routing.ValidatedRoute(
        selected=deployment, reviewer=None, proposal=proposal, required_review=False,
        assessments=[CandidateAssessment(deployment_id=deployment.id, eligible=True, exclusion_reasons=[], evidence_refs=[],
                                         note="Betriebsprobe des Betriebs; harte Regeln des Routings werden hier nicht behauptet.")],
    )
    decision = routing.build_decision(route, decision_id=decision_id, catalog_revision=revision.revision, routing_policy_ref="probe",
                                      selection_mode="deterministic_fallback", selector_model_ref=None, reservation_ref=f"probe:{probe_id}",
                                      pricing_snapshot_ref=deployment.pricing_ref or "-", created_at=utcnow().isoformat())
    payload = {"probe_id": probe_id, "eligible_candidates": [{"deployment_id": "cand-1", "evidence_refs": ["ev-1"]},
                                                           {"deployment_id": "cand-2", "evidence_refs": ["ev-2"]}]}
    request = LLMRequest(
        invocation_id=f"{probe_id}:probe:1", deployment_id=deployment.id, system_rules=SYSTEM_RULES,
        content_blocks=[{"type": "text", "text": json.dumps(payload | {"task_id": probe_id}, ensure_ascii=False), "data_label": "public",
                         "origin": "core_summary"}],
        output_contract=provider_schema("route_proposal"), max_output_tokens=PROBE_MAX_OUTPUT_TOKENS,
        metadata={"probe_id": probe_id, "task_id": probe_id, "candidates": payload["eligible_candidates"]},
    )
    record(db, actor=actor, command="catalog.probe", object_type="model_catalog", object_id=deployment.id, object_version=revision.revision,
           correlation_id=correlation_id, event_type="ProbeStarted",
           payload={"probe_id": probe_id, "adapter": deployment.adapter, "provider_id": deployment.provider_id, "estimate_minor": estimate.amount_minor,
                    "cap_minor": cap.amount_minor, "currency": cap.currency})
    db.commit()  # the intent is durable before anything leaves the campus (S1.1 finding 2)
    try:
        response = _gateway(db, actor.workspace_id).invoke(request, deployment, decision)
    except CampusError as err:
        record(db, actor=actor, command="catalog.probe", object_type="model_catalog", object_id=deployment.id, object_version=revision.revision,
               correlation_id=correlation_id, policy_decision=f"blocked:{err.code.value}", event_type="ProbeFailed",
               payload={"probe_id": probe_id, "message": err.message, "usage": (err.details or {}).get("usage")})
        db.commit()
        err.details["audited"] = True
        raise
    usage = response.usage
    actual = estimate_cost(pricing, usage.input_tokens or 0, usage.output_tokens or 0) if usage.known else None
    try:
        selected = RouteProposal.model_validate_json(response.text()).selected_deployment_id
        structured_valid = selected in {"cand-1", "cand-2"}
    except Exception:  # noqa: BLE001 - the answer violates the contract: reported, never repaired
        selected, structured_valid = None, False
    synthetic = any(b.get("synthetic") for b in response.blocks)
    result = {
        "probe_id": probe_id, "deployment_id": deployment.id, "adapter": deployment.adapter, "provider_id": deployment.provider_id,
        "catalog_revision": revision.revision, "catalog_state": revision.state, "provider_model_id": deployment.provider_model_id,
        "canonical_model_id": deployment.canonical_model_id, "effective_model_id": response.effective_model_id,
        "invocation_id": response.invocation_id, "routing_decision_id": decision_id, "stop_reason": response.stop_reason,
        "usage": {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens},
        "cost": actual.format() if actual else "offen", "cost_minor": actual.amount_minor if actual else None, "currency": cap.currency,
        "cap": cap.format(), "structured_output_valid": structured_valid, "selected_candidate": selected, "synthetic": synthetic,
        "real_provider_call": deployment.adapter != "mock" and not synthetic,
        "note": "Betriebsprobe: belegt Zugang, Journal, Nutzungswerte und Ausgabeformat; keine Qualifikation, keine Forschung.",
    }
    record(db, actor=actor, command="catalog.probe", object_type="model_catalog", object_id=deployment.id, object_version=revision.revision,
           correlation_id=correlation_id, event_type="ProbeCompleted",
           payload={k: result[k] for k in ("probe_id", "effective_model_id", "invocation_id", "usage", "cost_minor", "currency",
                                           "structured_output_valid", "synthetic", "real_provider_call")})
    db.commit()
    return result
