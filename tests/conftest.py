from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from campus_contracts.models import AgentProfile, ModelCatalog, RouteProposal, TaskNeed
from campus_contracts.policy import RoutingPolicy
from campus_contracts.validation import load_example

REPO = Path(__file__).resolve().parents[1]


def active_policy_document(selector_ids: list[str] | None = None, replans: int = 2) -> dict:
    """The package's policy template, activated and with all operating limits set for tests."""
    doc = yaml.safe_load((REPO / "config" / "routing_policy.example.yaml").read_text(encoding="utf-8"))
    doc["activation_state"] = "active"
    doc["bootstrap_selector_deployment_ids"] = selector_ids if selector_ids is not None else ["fixture-deployment-b"]
    doc["limits"].update({"max_route_replans_per_task": replans, "max_delegation_depth": 2, "max_concurrent_runs": 4,
                          "max_task_cost": "50.00", "max_project_cost": "500.00", "currency": "CHF"})
    return doc


def build_catalog_document() -> dict:
    """Fixture catalog: priced pair A/B, a disabled C, an alias of A (D) and a flaky E whose mock provider fails before send."""
    doc = copy.deepcopy(load_example("model_catalog"))
    for dep in doc["deployments"]:
        dep["pricing_ref"] = "test-price-1"
        dep["processing_profile_ref"] = f"profile-{dep['provider_id']}"
    c = copy.deepcopy(doc["deployments"][0])
    c.update({"id": "fixture-deployment-c", "provider_id": "fixture-provider-c", "provider_model_id": "fixture-native-c",
              "canonical_model_id": "fixture/model-c@1", "model_family": "fixture-family-c", "enabled": False,
              "qualification_refs": [], "health": "available", "processing_profile_ref": "profile-fixture-provider-c"})
    d = copy.deepcopy(doc["deployments"][0])
    d.update({"id": "fixture-deployment-d-alias-of-a", "provider_id": "fixture-provider-d", "provider_model_id": "fixture-native-a-alias",
              "qualification_refs": ["fixture-eval-a"], "processing_profile_ref": "profile-fixture-provider-d"})
    e = copy.deepcopy(doc["deployments"][0])
    e.update({"id": "fixture-deployment-e-flaky", "provider_id": "fixture-provider-e", "provider_model_id": "mock-fail-e",
              "canonical_model_id": "fixture/model-e@1", "model_family": "fixture-family-e", "qualification_refs": ["fixture-eval-e"],
              "processing_profile_ref": "profile-fixture-provider-e"})
    doc["deployments"].extend([c, d, e])
    doc["revision"] = "test-catalog-1"
    return doc


@pytest.fixture
def policy() -> RoutingPolicy:
    return RoutingPolicy.model_validate(active_policy_document())


@pytest.fixture
def catalog() -> ModelCatalog:
    return ModelCatalog.model_validate(build_catalog_document())


@pytest.fixture
def task() -> TaskNeed:
    return TaskNeed.model_validate(load_example("task_need"))


@pytest.fixture
def proposal_a() -> RouteProposal:
    return RouteProposal.model_validate(load_example("route_proposal"))


@pytest.fixture
def agent_profile() -> AgentProfile:
    return AgentProfile.model_validate(load_example("agent_profile"))
