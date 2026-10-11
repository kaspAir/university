"""Gateway contract with the mock adapter: journaling, no silent substitution, unknown outcome (A06, I08)."""
from __future__ import annotations

import pytest

from campus_domain import routing
from campus_domain.errors import CampusError, ErrorCode
from campus_gateway.gateway import Gateway
from campus_gateway.mock_adapter import MockAdapter
from campus_gateway.port import LLMRequest


class MemoryJournal:
    def __init__(self) -> None:
        self.entries: dict[str, dict] = {}

    def begin(self, request, deployment, routing_decision_id) -> None:
        self.entries[request.invocation_id] = {"state": "pending", "deployment": deployment.id, "decision": routing_decision_id}

    def complete(self, response) -> None:
        self.entries[response.invocation_id].update(state="completed", model=response.effective_model_id)

    def fail(self, invocation_id, reason, usage=None) -> None:
        self.entries[invocation_id].update(state="failed", reason=reason, usage=usage)

    def mark_unknown(self, invocation_id, reason) -> None:
        self.entries[invocation_id].update(state="outcome_unknown", reason=reason)


@pytest.fixture
def decision(task, catalog, policy, proposal_a):
    route = routing.validate_proposal(task, proposal_a, catalog, policy)
    return routing.build_decision(route, decision_id="RD-1", catalog_revision=catalog.revision, routing_policy_ref=policy.revision,
                                  selection_mode="fixture", selector_model_ref=None, reservation_ref="RES-1",
                                  pricing_snapshot_ref="test-price-1", created_at="2026-10-11T12:00:00Z")


def _request(deployment_id: str, invocation_id: str = "inv-1") -> LLMRequest:
    return LLMRequest(invocation_id=invocation_id, deployment_id=deployment_id, system_rules="test",
                      content_blocks=[{"type": "text", "text": "hello", "data_label": "public"}])


def test_mock_call_is_journaled_and_marked_synthetic(catalog, decision) -> None:
    journal = MemoryJournal()
    gateway = Gateway({"mock": MockAdapter()}, journal)
    dep = catalog.deployment("fixture-deployment-a")
    response = gateway.invoke(_request(dep.id), dep, decision)
    assert response.blocks[0]["synthetic"] is True
    assert response.usage.known
    assert journal.entries["inv-1"]["state"] == "completed"
    assert journal.entries["inv-1"]["decision"] == "RD-1"


def test_deployment_outside_the_route_is_refused(catalog, decision) -> None:
    gateway = Gateway({"mock": MockAdapter()}, MemoryJournal())
    stranger = catalog.deployment("fixture-deployment-d-alias-of-a")
    with pytest.raises(CampusError) as excinfo:
        gateway.invoke(_request(stranger.id), stranger, decision)
    assert excinfo.value.code == ErrorCode.ROUTE_NOT_AUTHORIZED


def test_unauthorized_decision_is_refused(catalog, decision) -> None:
    gateway = Gateway({"mock": MockAdapter()}, MemoryJournal())
    dep = catalog.deployment("fixture-deployment-a")
    proposed = decision.model_copy(update={"state": "proposed"})
    with pytest.raises(CampusError) as excinfo:
        gateway.invoke(_request(dep.id), dep, proposed)
    assert excinfo.value.code == ErrorCode.ROUTE_NOT_AUTHORIZED


def test_unapproved_adapter_is_an_error_not_a_simulation(catalog, decision) -> None:
    journal = MemoryJournal()
    gateway = Gateway({}, journal)  # no adapters approved at all
    dep = catalog.deployment("fixture-deployment-a")
    with pytest.raises(CampusError) as excinfo:
        gateway.invoke(_request(dep.id), dep, decision)
    assert excinfo.value.code == ErrorCode.ADAPTER_NOT_APPROVED
    assert journal.entries == {}


def test_a06_timeout_after_send_is_outcome_unknown(catalog, decision) -> None:
    journal = MemoryJournal()
    gateway = Gateway({"mock": MockAdapter(hang_invocation_ids={"inv-hang"})}, journal)
    dep = catalog.deployment("fixture-deployment-a")
    with pytest.raises(CampusError) as excinfo:
        gateway.invoke(_request(dep.id, "inv-hang"), dep, decision)
    assert excinfo.value.code == ErrorCode.OUTCOME_UNKNOWN
    assert excinfo.value.retryable is False
    assert journal.entries["inv-hang"]["state"] == "outcome_unknown"


def test_connection_error_is_recorded_as_failed_and_retryable(catalog, decision) -> None:
    journal = MemoryJournal()
    gateway = Gateway({"mock": MockAdapter(fail_invocation_ids={"inv-fail"})}, journal)
    dep = catalog.deployment("fixture-deployment-a")
    with pytest.raises(CampusError) as excinfo:
        gateway.invoke(_request(dep.id, "inv-fail"), dep, decision)
    assert excinfo.value.retryable is True
    assert journal.entries["inv-fail"]["state"] == "failed"
