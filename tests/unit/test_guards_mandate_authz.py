from __future__ import annotations

import pytest

from campus_domain.authz import Role, authorize
from campus_domain.errors import CampusError, ErrorCode
from campus_domain.guards import RunStartContext, check_run_start
from campus_domain.mandate import Condition, HumanDecisionInput, transition, validate_decision
from campus_domain.statuses import (
    ActivationState,
    DecisionOption,
    MandateState,
    OperatingStatus,
    QualificationStatus,
)


def _ctx(**overrides) -> RunStartContext:
    base = dict(
        workspace_stopped=False, mandate_state=MandateState.APPROVED, policy_state=ActivationState.ACTIVE, policy_revision="pol-1",
        missing_limits=[], catalog_state=ActivationState.ACTIVE, agent_registered=True, agent_operating_status=OperatingStatus.PLANNED,
        agent_qualification_status=QualificationStatus.NOT_EVALUATED, task_qualification_mode="sandbox_experiment",
        route_state="authorized", reservation_present=True, review_required=True, review_reservation_present=True,
    )
    base.update(overrides)
    return RunStartContext(**base)


def test_all_green_passes() -> None:
    check_run_start(_ctx())


@pytest.mark.parametrize(
    "overrides, code",
    [
        ({"workspace_stopped": True}, ErrorCode.WORKSPACE_STOPPED),
        ({"mandate_state": MandateState.DRAFT}, ErrorCode.MANDATE_NOT_APPROVED),  # T02
        ({"mandate_state": None}, ErrorCode.MANDATE_NOT_APPROVED),
        ({"policy_state": ActivationState.DRAFT}, ErrorCode.POLICY_NOT_ACTIVE),
        ({"missing_limits": ["max_task_cost"]}, ErrorCode.LIMITS_NOT_SET),
        ({"catalog_state": None}, ErrorCode.CATALOG_NOT_ACTIVE),
        ({"agent_registered": False, "agent_operating_status": None, "agent_qualification_status": None}, ErrorCode.AGENT_NOT_REGISTERED),  # T11
        ({"agent_operating_status": OperatingStatus.BLOCKED}, ErrorCode.AGENT_NOT_REGISTERED),
        ({"task_qualification_mode": "qualified"}, ErrorCode.QUALIFICATION_MISSING),
        ({"route_state": "proposed"}, ErrorCode.ROUTE_NOT_AUTHORIZED),
        ({"reservation_present": False}, ErrorCode.BUDGET_EXHAUSTED),
        ({"review_reservation_present": False}, ErrorCode.BUDGET_EXHAUSTED),  # A04
    ],
)
def test_each_guard_blocks_with_typed_error(overrides, code) -> None:
    with pytest.raises(CampusError) as excinfo:
        check_run_start(_ctx(**overrides))
    assert excinfo.value.code == code
    assert excinfo.value.blocked_action == "run.start"
    assert excinfo.value.required_resolution or code == ErrorCode.AGENT_NOT_REGISTERED


def test_a04_message_names_the_missing_review_reserve() -> None:
    with pytest.raises(CampusError) as excinfo:
        check_run_start(_ctx(review_reservation_present=False))
    assert "Prüfreserve" in excinfo.value.message


def _decision(option=DecisionOption.ACCEPT, **overrides) -> HumanDecisionInput:
    base = dict(option=option, rationale="Mandat ist im Themenraum begründet und budgetiert.",
                alternatives_presented=["annehmen", "begrenzt annehmen", "überarbeiten", "ablehnen"],
                remaining_objections=["Datenklasse personal noch nicht geregelt"], consequences="Root-Budget wird eröffnet.", conditions=[])
    base.update(overrides)
    return HumanDecisionInput(**base)


def test_decision_requires_reasons_alternatives_consequences() -> None:
    validate_decision(_decision())
    with pytest.raises(CampusError):
        validate_decision(_decision(rationale=" "))
    with pytest.raises(CampusError):
        validate_decision(_decision(alternatives_presented=["nur eine"]))
    with pytest.raises(CampusError):
        validate_decision(_decision(DecisionOption.ACCEPT_LIMITED))  # limited acceptance needs a condition
    validate_decision(_decision(DecisionOption.ACCEPT_LIMITED, conditions=[Condition("Datenklasse klären", "Forschungsleitung", "2026-10-31")]))


def test_transitions_only_from_draft() -> None:
    assert transition(MandateState.DRAFT, DecisionOption.ACCEPT) == (MandateState.APPROVED, False)
    assert transition(MandateState.DRAFT, DecisionOption.REJECT) == (MandateState.REJECTED, False)
    assert transition(MandateState.DRAFT, DecisionOption.REVISE) == (MandateState.DRAFT, True)
    with pytest.raises(CampusError) as excinfo:
        transition(MandateState.APPROVED, DecisionOption.ACCEPT)
    assert excinfo.value.code == ErrorCode.INVALID_TRANSITION


def test_operator_cannot_approve_mandates_but_can_activate_catalog() -> None:
    authorize("catalog.activate", {Role.OPERATOR})
    with pytest.raises(CampusError) as excinfo:
        authorize("mandate.decide", {Role.OPERATOR})
    assert excinfo.value.code == ErrorCode.FORBIDDEN
    with pytest.raises(CampusError):
        authorize("policy.activate", {Role.OPERATOR})  # protection standards stay with the research director
    authorize("policy.activate", {Role.RESEARCH_DIRECTOR})
