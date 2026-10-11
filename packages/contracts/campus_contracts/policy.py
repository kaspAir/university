"""Routing policy document (config/routing_policy.example.yaml).

Not one of the six design schemas; typed here so the trusted core can enforce it.
Null limits block real execution (config/README of the package).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HardRules(_Strict):
    require_enabled: bool = True
    require_data_authorization: bool = True
    unknown_required_capability: Literal["exclude"] = "exclude"
    unknown_review_model_identity: Literal["exclude"] = "exclude"
    qualified_combinations_required: bool = True
    substantive_review_different_canonical_model: bool = True
    prefer_distinct_provider: bool = True
    reserve_required_review_budget: bool = True
    hidden_gateway_fallbacks: Literal[False] = False


class Limits(_Strict):
    max_selector_calls_per_attempt: int | None = Field(default=1, ge=1)
    max_route_replans_per_task: int | None = Field(default=None, ge=0)
    max_delegation_depth: int | None = Field(default=None, ge=0)
    max_concurrent_runs: int | None = Field(default=None, ge=1)
    max_task_cost: str | None = None
    max_project_cost: str | None = None
    currency: str | None = None

    def missing(self) -> list[str]:
        return [name for name, value in self.model_dump().items() if value is None]


class Fallback(_Strict):
    technical_failure: Literal["replan_under_same_constraints"] = "replan_under_same_constraints"
    protected_refusal: Literal["do_not_route_around_safeguards"] = "do_not_route_around_safeguards"
    no_eligible_model: Literal["wait_or_request_scoped_decision"] = "wait_or_request_scoped_decision"
    outcome_unknown: Literal["reconcile_before_retry"] = "reconcile_before_retry"


class RoutingPolicy(_Strict):
    schema_version: Literal["1.0"]
    revision: str = Field(min_length=1)
    activation_state: Literal["draft", "active", "retired"]
    selection_mode: Literal["llm_proposal_validated_by_core"]
    bootstrap_selector_deployment_ids: list[str]
    hard_rules: HardRules
    selection_criteria: list[str]
    limits: Limits
    fallback: Fallback
    deterministic_fallback_rule: Literal["first_eligible"] | None = None
    note: str | None = None
