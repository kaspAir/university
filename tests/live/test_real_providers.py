"""S1 gate evidence: two real model accesses of different providers (package CLAUDE.md: «two real approved models are a
gate, not merely two mock labels»). These tests spend money and are OFF unless CAMPUS_LIVE_TESTS=1.

Required environment (never committed):
  CAMPUS_LIVE_TESTS=1
  CAMPUS_APPROVED_ADAPTERS=mock,anthropic,openai
  CAMPUS_SECRET_PROVIDERS_ANTHROPIC=...   CAMPUS_LIVE_ANTHROPIC_MODEL=<native model id from discovery>
  CAMPUS_SECRET_PROVIDERS_OPENAI=...      CAMPUS_LIVE_OPENAI_MODEL=<native model id from discovery>
"""
from __future__ import annotations

import json
import os

import pytest

from campus_contracts.models import Deployment, RouteProposal
from campus_contracts.validation import load_example, provider_schema
from campus_gateway.adapters import build_adapters
from campus_gateway.port import LLMRequest

pytestmark = pytest.mark.skipif(os.environ.get("CAMPUS_LIVE_TESTS") != "1", reason="Live-Tests nur mit CAMPUS_LIVE_TESTS=1 (kosten Geld)")

PROVIDERS = [
    ("anthropic", "CAMPUS_LIVE_ANTHROPIC_MODEL", "secret://providers/anthropic"),
    ("openai", "CAMPUS_LIVE_OPENAI_MODEL", "secret://providers/openai"),
]


def _deployment(adapter: str, model: str, secret_ref: str) -> Deployment:
    base = load_example("model_catalog")["deployments"][0]
    base.update({"id": f"live-{adapter}", "provider_id": adapter, "adapter": adapter, "provider_model_id": model,
                 "canonical_model_id": f"{adapter}/{model}", "secret_ref": secret_ref, "api_base": None,
                 "capabilities": {**base["capabilities"], "structured_output": "supported"}})
    return Deployment.model_validate(base)


@pytest.mark.parametrize("adapter_name, model_env, secret_ref", PROVIDERS)
def test_discovery_lists_the_configured_model(adapter_name, model_env, secret_ref):
    model = os.environ.get(model_env)
    if not model:
        pytest.skip(f"{model_env} nicht gesetzt")
    adapter = build_adapters([adapter_name])[adapter_name]
    ids = adapter.list_models(secret_ref, None)
    assert model in ids, f"{model} nicht in der Discovery-Liste von {adapter_name}"


@pytest.mark.parametrize("adapter_name, model_env, secret_ref", PROVIDERS)
def test_structured_route_proposal_from_real_model(adapter_name, model_env, secret_ref):
    model = os.environ.get(model_env)
    if not model:
        pytest.skip(f"{model_env} nicht gesetzt")
    adapter = build_adapters([adapter_name])[adapter_name]
    dep = _deployment(adapter_name, model, secret_ref)
    request = LLMRequest(
        invocation_id=f"live-{adapter_name}-1", deployment_id=dep.id,
        system_rules="Wähle genau einen Kandidaten und antworte nur im vorgegebenen JSON-Format.",
        content_blocks=[{"type": "text", "text": json.dumps({"task_id": "live-task", "eligible_candidates": [
            {"deployment_id": "cand-1", "evidence_refs": ["ev-1"]}, {"deployment_id": "cand-2", "evidence_refs": ["ev-2"]}]})}],
        output_contract=provider_schema("route_proposal"), max_output_tokens=600,
    )
    response = adapter.invoke(request, dep)
    proposal = RouteProposal.model_validate_json(response.text())
    assert proposal.selected_deployment_id in {"cand-1", "cand-2"}
    assert response.usage.known, "Nutzungswerte müssen für die Abrechnung bekannt sein (A14)"
    assert response.effective_model_id, "effektive Modellkennung muss protokolliert werden (Art. 27)"


def test_two_providers_are_canonically_distinct():
    models = {name: os.environ.get(env) for name, env, _ in PROVIDERS}
    if not all(models.values()):
        pytest.skip("beide Modellkennungen nötig")
    canon = {f"{name}/{model}" for name, model in models.items()}
    assert len(canon) == 2
