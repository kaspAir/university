"""Trusted-core routing (A02, A03, A08, I04) without any database or model."""
from __future__ import annotations

import pytest

from campus_contracts.models import RouteProposal
from campus_domain import routing
from campus_domain.errors import CampusError, ErrorCode


def test_happy_path_authorizes_a_with_distinct_reviewer_b(task, catalog, policy, proposal_a) -> None:
    route = routing.validate_proposal(task, proposal_a, catalog, policy)
    assert route.selected.id == "fixture-deployment-a"
    assert route.required_review is True
    assert route.reviewer is not None and route.reviewer.id == "fixture-deployment-b"
    decision = routing.build_decision(route, decision_id="RD-1", catalog_revision=catalog.revision, routing_policy_ref=policy.revision,
                                      selection_mode="fixture", selector_model_ref=None, reservation_ref="RES-1",
                                      pricing_snapshot_ref="test-price-1", created_at="2026-10-11T12:00:00Z")
    assert decision.state == "authorized"
    assert decision.selected_canonical_model_id == "fixture/model-a@1"
    excluded = {a.deployment_id: a.exclusion_reasons for a in decision.candidate_assessments if not a.eligible}
    assert "fixture-deployment-c" in excluded and routing.R_NOT_ENABLED in excluded["fixture-deployment-c"]


def test_a02_proposal_for_disabled_model_is_rejected_but_assessments_are_kept(task, catalog, policy) -> None:
    proposal = RouteProposal(task_id=task.task_id, selected_deployment_id="fixture-deployment-c",
                             rationale="Plausibel klingende Begründung für ein nicht zulässiges Modell.",
                             evidence_refs=[], alternatives=[], uncertainties=[])
    with pytest.raises(CampusError) as excinfo:
        routing.validate_proposal(task, proposal, catalog, policy)
    err = excinfo.value
    assert err.code == ErrorCode.PROPOSAL_INVALID
    assert err.blocked_action == "route.authorize"
    assessments = {a["deployment_id"]: a for a in err.details["assessments"]}
    assert assessments["fixture-deployment-c"]["eligible"] is False
    assert routing.R_NOT_ENABLED in assessments["fixture-deployment-c"]["exclusion_reasons"]


def test_a03_alias_of_the_producer_is_not_review_diversity(task, catalog, policy, proposal_a) -> None:
    only_a_and_alias = catalog.model_copy(update={"deployments": [
        d for d in catalog.deployments if d.id not in {"fixture-deployment-b", "fixture-deployment-e-flaky"}]})
    proposal = proposal_a.model_copy(update={"alternatives": []})
    with pytest.raises(CampusError) as excinfo:
        routing.validate_proposal(task, proposal, only_a_and_alias, policy)
    assert excinfo.value.code == ErrorCode.REVIEW_DIVERSITY_UNSATISFIED


def test_a08_unknown_capability_excludes_instead_of_degrading(task, catalog, policy, proposal_a) -> None:
    deps = [d.model_copy(update={"capabilities": d.capabilities.model_copy(update={"structured_output": "unknown"})})
            if d.id == "fixture-deployment-a" else d for d in catalog.deployments]
    weakened = catalog.model_copy(update={"deployments": deps})
    with pytest.raises(CampusError) as excinfo:
        routing.validate_proposal(task, proposal_a, weakened, policy)
    assessments = {a["deployment_id"]: a for a in excinfo.value.details["assessments"]}
    assert routing.R_CAP_UNKNOWN + "structured_output" in assessments["fixture-deployment-a"]["exclusion_reasons"]


def test_invented_evidence_is_rejected(task, catalog, policy, proposal_a) -> None:
    proposal = proposal_a.model_copy(update={"evidence_refs": ["invented-evidence"]})
    with pytest.raises(CampusError) as excinfo:
        routing.validate_proposal(task, proposal, catalog, policy)
    assert excinfo.value.code == ErrorCode.PROPOSAL_INVALID
    assert "invented-evidence" in excinfo.value.message


def test_reviewer_task_excludes_substantive_contributor(task, catalog, policy) -> None:
    review_task = task.model_copy(update={"role": "reviewer", "substantive_contributor_model_ids": ["fixture/model-a@1"]})
    assessments = {a.deployment_id: a for a in routing.assess_candidates(review_task, catalog, policy)}
    assert routing.R_CONTRIBUTOR in assessments["fixture-deployment-a"].exclusion_reasons
    assert routing.R_CONTRIBUTOR in assessments["fixture-deployment-d-alias-of-a"].exclusion_reasons
    assert assessments["fixture-deployment-b"].eligible


def test_data_label_mismatch_excludes(task, catalog, policy) -> None:
    confidential = task.model_copy(update={"data_labels": ["confidential"]})
    assert all(not a.eligible for a in routing.assess_candidates(confidential, catalog, policy))


def test_deterministic_fallback_requires_explicit_policy_rule(task, catalog, policy) -> None:
    with pytest.raises(CampusError) as excinfo:
        routing.deterministic_proposal(task, catalog, policy)
    assert excinfo.value.code == ErrorCode.NO_ELIGIBLE_SELECTOR
    allowed = policy.model_copy(update={"deterministic_fallback_rule": "first_eligible"})
    proposal = routing.deterministic_proposal(task, catalog, allowed)
    assert proposal.selected_deployment_id == "fixture-deployment-a"
    assert "first_eligible" in proposal.rationale


def test_inactive_policy_blocks(task, catalog, policy, proposal_a) -> None:
    draft = policy.model_copy(update={"activation_state": "draft"})
    with pytest.raises(CampusError) as excinfo:
        routing.validate_proposal(task, proposal_a, catalog, draft)
    assert excinfo.value.code == ErrorCode.POLICY_NOT_ACTIVE
