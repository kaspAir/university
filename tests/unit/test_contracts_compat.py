"""The Pydantic mirrors and the JSON Schemas of the package must agree on every fixture."""
from __future__ import annotations

import copy

import pytest
import yaml
from pydantic import ValidationError

from campus_contracts.models import MODEL_BY_SCHEMA
from campus_contracts.policy import RoutingPolicy
from campus_contracts.validation import SCHEMA_NAMES, load_example, validate_document
from tests.conftest import REPO


@pytest.mark.parametrize("name", SCHEMA_NAMES)
def test_fixture_valid_in_both_worlds(name: str) -> None:
    doc = load_example(name)
    assert validate_document(name, doc) == []
    MODEL_BY_SCHEMA[name].model_validate(doc)


@pytest.mark.parametrize("name", SCHEMA_NAMES)
def test_unknown_field_rejected_in_both_worlds(name: str) -> None:
    doc = copy.deepcopy(load_example(name))
    doc["unexpected_field"] = "x"
    assert validate_document(name, doc), "schema must reject additional properties"
    with pytest.raises(ValidationError):
        MODEL_BY_SCHEMA[name].model_validate(doc)


def test_enabled_deployment_needs_identity_and_price() -> None:
    doc = copy.deepcopy(load_example("model_catalog"))
    doc["deployments"][0]["pricing_ref"] = None
    assert any("pricing_ref" in v for v in validate_document("model_catalog", doc))


def test_config_templates_parse() -> None:
    catalog = yaml.safe_load((REPO / "config" / "model_catalog.example.yaml").read_text(encoding="utf-8"))
    assert validate_document("model_catalog", catalog) == []
    assert all(not d["enabled"] for d in catalog["deployments"]), "template providers must stay disabled"
    policy = RoutingPolicy.model_validate(yaml.safe_load((REPO / "config" / "routing_policy.example.yaml").read_text(encoding="utf-8")))
    assert policy.activation_state == "draft"
    assert policy.limits.missing(), "template limits are intentionally unset"
