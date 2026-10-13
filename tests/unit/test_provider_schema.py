"""The schema handed to a provider is the intersection both providers' enforced structured output accepts; the full contract stays
with the core. The first live probe failed on exactly this: Anthropic rejected `uniqueItems` in route_proposal."""
from __future__ import annotations

import pytest
from jsonschema import Draft202012Validator

from campus_contracts.models import ClaimSet, EvaluationVerdict
from campus_contracts.validation import _PROVIDER_UNSUPPORTED, load_example, provider_schema

CONTRACTS = ("route_proposal", "claim_set", "review_report", "evaluation_verdict")


def _walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


@pytest.mark.parametrize("name", CONTRACTS)
def test_provider_form_uses_only_the_common_subset(name):
    schema = provider_schema(name)
    Draft202012Validator.check_schema(schema)
    for node in _walk(schema):
        assert not (set(node) & _PROVIDER_UNSUPPORTED), (name, node)
        assert "oneOf" not in node and "allOf" not in node and "not" not in node and "if" not in node, (name, node)
        if "minItems" in node:
            assert node["minItems"] in (0, 1)
        if node.get("type") == "object" and "properties" in node:
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"]), "every property required; optional ones are nullable"
    for key in ("$schema", "$id", "title", "description"):
        assert key not in schema


def test_claim_evidence_alternatives_become_nullable_fields_and_the_core_still_decides():
    evidence = provider_schema("claim_set")["properties"]["claims"]["items"]["properties"]["evidence"]["items"]
    assert evidence["properties"]["passage_id"]["type"] == ["string", "null"]
    assert evidence["properties"]["experiment_run_id"]["type"] == ["string", "null"]
    assert evidence["required"] == ["passage_id", "experiment_run_id", "note"]
    # a provider-shaped answer (explicit nulls for the absent alternative) satisfies the provider form and the full pydantic contract
    answer = load_example("claim_set")
    for claim in answer["claims"]:
        for ev in claim["evidence"]:
            ev.setdefault("passage_id", None)
            ev.setdefault("experiment_run_id", None)
    assert not list(Draft202012Validator(provider_schema("claim_set")).iter_errors(answer))
    parsed = ClaimSet.model_validate(answer)
    assert all((e.passage_id is None) != (e.experiment_run_id is None) for c in parsed.claims for e in c.evidence)
    # the exclusivity the provider form cannot express is still enforced by the core
    answer["claims"][0]["evidence"][0].update({"passage_id": "p1", "experiment_run_id": "x1"})
    with pytest.raises(ValueError):
        ClaimSet.model_validate(answer)


def test_numeric_bounds_dropped_for_the_provider_are_enforced_by_the_core():
    score = provider_schema("evaluation_verdict")["properties"]["score"]
    assert "minimum" not in score and "maximum" not in score
    doc = load_example("evaluation_verdict") | {"score": 101}
    with pytest.raises(ValueError):
        EvaluationVerdict.model_validate(doc)


@pytest.mark.parametrize("name", ("route_proposal", "review_report", "evaluation_verdict"))
def test_fixtures_without_optional_fields_still_fit_the_provider_form(name):
    assert not list(Draft202012Validator(provider_schema(name)).iter_errors(load_example(name)))
