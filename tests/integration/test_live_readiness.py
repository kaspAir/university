"""Taking real providers into service: the probe through the gateway (journal, cap, audit), the real/synthetic marking the UI derives
from the ACTIVE catalog, and the limitations /health derives from the approved adapters. No provider is called here: the mock adapter
stands in and is reported as synthetic, which is exactly what the probe must say about it."""
from __future__ import annotations

import copy

import pytest
from sqlalchemy import select

from campus_api import services_probe
from campus_domain.authz import Role
from campus_domain.errors import CampusError, ErrorCode
from campus_infra.audit import Actor
from campus_infra.models import AuditEvent, LLMInvocation
from tests.conftest import build_catalog_document
from tests.integration.test_s0_flow import Api, bootstrap, govern

pytestmark = pytest.mark.integration


def _real_catalog_document() -> dict:
    """A runtime catalog with two real-adapter deployments and no mock. Never invoked in tests (no key, no call)."""
    base = build_catalog_document()["deployments"][0]
    deployments = []
    for name, api_base in (("anthropic", "https://api.anthropic.com"), ("openai", "https://api.openai.com")):
        d = copy.deepcopy(base)
        d.update({"id": f"{name}-test-model", "provider_id": name, "adapter": name, "provider_model_id": "test-model",
                  "canonical_model_id": f"{name}/test-model", "model_family": "test", "api_base": api_base, "secret_ref": f"secret://providers/{name}",
                  "qualification_refs": [], "processing_profile_ref": f"profile-{name}", "pricing_ref": "test-price-1"})
        deployments.append(d)
    return {"schema_version": "1.0", "revision": "test-live-1", "purpose": "runtime_snapshot", "deployments": deployments}


def test_probe_journals_one_call_enforces_the_cap_and_reports_synthetic(client, session_factory, workspace_id):
    boot = bootstrap(session_factory, workspace_id)
    api = Api(client)
    api.login(workspace_id, "leitung")
    govern(api, boot)
    operator = Actor("user", boot["director_id"], workspace_id, frozenset({Role.OPERATOR}))
    with session_factory() as db:
        result = services_probe.probe_deployment(db, actor=operator, deployment_id="fixture-deployment-a", max_cost="1.00", currency="CHF", correlation_id="t")
    assert result["synthetic"] is True and result["real_provider_call"] is False and result["adapter"] == "mock"
    assert result["structured_output_valid"] is True and result["selected_candidate"] == "cand-1"
    assert result["usage"]["input_tokens"] and result["usage"]["output_tokens"] and result["cost"] != "offen"
    assert result["routing_decision_id"].startswith(services_probe.PROBE_DECISION_PREFIX)
    with session_factory() as db:
        row = db.get(LLMInvocation, result["invocation_id"])
        assert row is not None and row.state == "completed" and row.effective_model_id == "fixture-native-a"
        assert services_probe.is_probe_decision(row.routing_decision_id)
        events = db.execute(select(AuditEvent).where(AuditEvent.workspace_id == workspace_id, AuditEvent.command == "catalog.probe")).scalars().all()
        assert len(events) == 2 and all(e.payload.get("probe_id") == result["probe_id"] for e in events), "intent before the call, outcome after it"
        assert events[-1].payload.get("real_provider_call") is False
        # the cap is checked from the bound price BEFORE the call: nothing is journaled when the estimate exceeds it
        with pytest.raises(CampusError) as err:
            services_probe.probe_deployment(db, actor=operator, deployment_id="fixture-deployment-b", max_cost="0.00", currency="CHF", correlation_id="t")
        assert err.value.code == ErrorCode.BUDGET_EXHAUSTED
        assert db.execute(select(LLMInvocation).where(LLMInvocation.workspace_id == workspace_id, LLMInvocation.deployment_id == "fixture-deployment-b")).first() is None
        with pytest.raises(CampusError) as err:
            services_probe.probe_deployment(db, actor=operator, deployment_id="not-registered", max_cost="1.00", currency="CHF", correlation_id="t")
        assert err.value.code == ErrorCode.NOT_FOUND
        producer = Actor("user", boot["director_id"], workspace_id, frozenset({Role.PRODUCER}))
        with pytest.raises(CampusError) as err:
            services_probe.probe_deployment(db, actor=producer, deployment_id="fixture-deployment-a", max_cost="1.00", currency="CHF", correlation_id="t")
        assert err.value.code == ErrorCode.FORBIDDEN
    # the operations page lists the probe as such, marked synthetic (mock adapter), with the effective model id
    page = client.get("/campus/betrieb").text
    assert result["invocation_id"] in page and "Probe" in page and "fixture-native-a" in page and "synthetisch" in page
    assert "nur Mock" in page and "Grenzen, die bestehen bleiben" in page


def test_ui_marks_real_calls_from_the_active_catalog_not_from_a_claim(client, session_factory, workspace_id):
    boot = bootstrap(session_factory, workspace_id, catalog_doc=_real_catalog_document())
    api = Api(client)
    api.login(workspace_id, "leitung")
    state = govern(api, boot)  # prices and data authorizations for anthropic/openai, then the runtime catalog is activated
    campus = client.get("/campus").text
    assert "echte Modellaufrufe · anthropic, openai" in campus and "Mockbetrieb" not in campus
    betrieb = client.get("/campus/betrieb").text
    assert "echt · anthropic, openai" in betrieb and "kosten Geld" in betrieb
    mandate_id = next(m["mandate_version_id"] for m in api.get("/mandates").json() if m["state"] == "approved")
    order = client.get(f"/campus/auftrag/{mandate_id}").text
    assert "anthropic/test-model" in order and "echt · kostet Geld" in order and "Erzeugung CHF 0.32" in order, "estimates from bound prices"
    assert "synthetischer Funktionsnachweis" not in order
    assert "echte Modellaufrufe aktiv" in client.get("/campus/ergebnisse").text
    assert state["project"]["project_id"]


def test_health_limitations_follow_the_approved_adapters(client, monkeypatch):
    monkeypatch.setenv("CAMPUS_APPROVED_ADAPTERS", "mock,anthropic")
    body = client.get("/api/v1/health").json()
    assert body["approved_adapters"] == ["mock", "anthropic"]
    assert any("echten Adaptern (anthropic)" in x for x in body["pilot_limitations"])
    assert any("Mock-Adapter bleibt freigegeben" in x for x in body["pilot_limitations"])
    monkeypatch.setenv("CAMPUS_APPROVED_ADAPTERS", "mock")
    body = client.get("/api/v1/health").json()
    assert any("Ergebnisse synthetisch" in x for x in body["pilot_limitations"]) and not any("echten Adaptern" in x for x in body["pilot_limitations"])
