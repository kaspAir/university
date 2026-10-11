from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from campus_contracts.models import AgentProfile, ModelCatalog, RouteProposal, TaskNeed
from campus_contracts.policy import RoutingPolicy
from campus_contracts.validation import load_example

REPO = Path(__file__).resolve().parents[1]


def active_policy_document() -> dict:
    """The package's policy template, activated and with all operating limits set for tests."""
    doc = yaml.safe_load((REPO / "config" / "routing_policy.example.yaml").read_text(encoding="utf-8"))
    doc["activation_state"] = "active"
    doc["limits"].update({"max_route_replans_per_task": 2, "max_delegation_depth": 2, "max_concurrent_runs": 4,
                          "max_task_cost": "50.00", "max_project_cost": "500.00", "currency": "CHF"})
    return doc


def build_catalog_document() -> dict:
    """Fixture catalog with a priced pair (A/B), a disabled C and an alias of A (D)."""
    doc = copy.deepcopy(load_example("model_catalog"))
    for dep in doc["deployments"]:
        dep["pricing_ref"] = "test-price-1"
    c = copy.deepcopy(doc["deployments"][0])
    c.update({"id": "fixture-deployment-c", "provider_id": "fixture-provider-c", "provider_model_id": "fixture-native-c",
              "canonical_model_id": "fixture/model-c@1", "model_family": "fixture-family-c", "enabled": False,
              "qualification_refs": [], "health": "available"})
    d = copy.deepcopy(doc["deployments"][0])
    d.update({"id": "fixture-deployment-d-alias-of-a", "provider_id": "fixture-provider-d", "provider_model_id": "fixture-native-a-alias",
              "qualification_refs": ["fixture-eval-a"]})
    doc["deployments"].extend([c, d])
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
