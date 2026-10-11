"""Gateway failure mapping (chapter 10 fallback block) and the adapter approval switch (A01)."""
from __future__ import annotations

import pytest

from campus_contracts.models import Deployment
from campus_domain import routing
from campus_domain.errors import CampusError, ErrorCode
from campus_gateway.adapters import build_adapters
from campus_gateway.gateway import Gateway
from campus_gateway.mock_adapter import MockAdapter
from campus_gateway.port import LLMRequest
from tests.adapter_contracts.test_gateway_mock import MemoryJournal


@pytest.fixture
def decision(task, catalog, policy, proposal_a):
    route = routing.validate_proposal(task, proposal_a, catalog, policy)
    return routing.build_decision(route, decision_id="RD-1", catalog_revision=catalog.revision, routing_policy_ref=policy.revision,
                                  selection_mode="fixture", selector_model_ref=None, reservation_ref="RES-1",
                                  pricing_snapshot_ref="test-price-1", created_at="2026-10-11T12:00:00Z")


def _request(dep: Deployment, contract=None) -> LLMRequest:
    return LLMRequest(invocation_id=f"inv-{dep.id}", deployment_id=dep.id, system_rules="r",
                      content_blocks=[{"type": "text", "text": "x"}], output_contract=contract)


def _variant(catalog, native: str) -> Deployment:
    return catalog.deployment("fixture-deployment-a").model_copy(update={"provider_model_id": native})


def test_unavailable_is_retryable_through_replan(catalog, decision):
    journal = MemoryJournal()
    dep = _variant(catalog, "mock-fail-now")
    with pytest.raises(CampusError) as excinfo:
        Gateway({"mock": MockAdapter()}, journal).invoke(_request(dep), dep, decision)
    assert excinfo.value.code == ErrorCode.NO_ELIGIBLE_MODEL and excinfo.value.retryable is True
    assert journal.entries[f"inv-{dep.id}"]["state"] == "failed"


def test_refusal_is_not_routed_around(catalog, decision):
    dep = _variant(catalog, "mock-refuse")
    with pytest.raises(CampusError) as excinfo:
        Gateway({"mock": MockAdapter()}, MemoryJournal()).invoke(_request(dep), dep, decision)
    assert excinfo.value.code == ErrorCode.PROTECTED_REFUSAL and excinfo.value.retryable is False


def test_timeout_is_outcome_unknown(catalog, decision):
    journal = MemoryJournal()
    dep = _variant(catalog, "mock-hang")
    with pytest.raises(CampusError) as excinfo:
        Gateway({"mock": MockAdapter()}, journal).invoke(_request(dep), dep, decision)
    assert excinfo.value.code == ErrorCode.OUTCOME_UNKNOWN
    assert journal.entries[f"inv-{dep.id}"]["state"] == "outcome_unknown"


def test_structured_output_needs_catalog_capability(catalog, decision):
    dep = catalog.deployment("fixture-deployment-a")
    weakened = dep.model_copy(update={"capabilities": dep.capabilities.model_copy(update={"structured_output": "unknown"})})
    with pytest.raises(CampusError) as excinfo:
        Gateway({"mock": MockAdapter()}, MemoryJournal()).invoke(_request(weakened, {"type": "object"}), weakened, decision)
    assert excinfo.value.code == ErrorCode.CAPABILITY_UNSUPPORTED


def test_mock_structured_output_follows_candidates_and_verdict(catalog, decision):
    from campus_contracts.models import ReviewReport, RouteProposal
    from campus_contracts.validation import provider_schema

    dep = catalog.deployment("fixture-deployment-a")
    gateway = Gateway({"mock": MockAdapter()}, MemoryJournal())
    req = LLMRequest(invocation_id="sel", deployment_id=dep.id, system_rules="r", content_blocks=[{"type": "text", "text": "x"}],
                     output_contract=provider_schema("route_proposal"),
                     metadata={"task_id": "T-1", "candidates": [{"deployment_id": "fixture-deployment-b", "evidence_refs": ["fixture-eval-b"]}]})
    proposal = RouteProposal.model_validate_json(gateway.invoke(req, dep, decision).text())
    assert proposal.selected_deployment_id == "fixture-deployment-b" and proposal.evidence_refs == ["fixture-eval-b"]
    req = LLMRequest(invocation_id="rev", deployment_id=dep.id, system_rules="r", content_blocks=[{"type": "text", "text": "x"}],
                     output_contract=provider_schema("review_report"), metadata={"task_class": "source_analysis_contested"})
    report = ReviewReport.model_validate_json(gateway.invoke(req, dep, decision).text())
    assert report.verdict == "objections" and report.findings


def test_only_approved_adapters_are_built(monkeypatch):
    monkeypatch.setenv("CAMPUS_APPROVED_ADAPTERS", "mock")
    assert set(build_adapters()) == {"mock"}
    monkeypatch.setenv("CAMPUS_APPROVED_ADAPTERS", "mock,anthropic,openai,openai_compatible")
    assert set(build_adapters()) == {"mock", "anthropic", "openai", "openai_compatible"}
    monkeypatch.setenv("CAMPUS_APPROVED_ADAPTERS", "mock,unknown_vendor")
    with pytest.raises(ValueError):
        build_adapters()
