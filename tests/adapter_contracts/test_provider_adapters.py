"""Adapter contracts without network: SDK clients are replaced by fakes. Checks the request shape the adapters build,
the response mapping and the failure classes (chapter 11, A08, I08)."""
from __future__ import annotations

import json
from types import SimpleNamespace

import anthropic
import httpx2
import openai
import pytest

from campus_gateway.adapters.anthropic_adapter import AnthropicAdapter
from campus_gateway.adapters.openai_adapter import OpenAIAdapter
from campus_gateway.port import (
    CapabilityUnsupported,
    LLMRequest,
    ProviderRefusal,
    ProviderRejected,
    ProviderTimeout,
    ProviderUnavailable,
)
from campus_gateway.secrets import EnvSecretResolver, env_name_for


class StaticSecrets:
    def resolve(self, secret_ref):
        return "test-key"


def _request(contract: dict | None = None) -> LLMRequest:
    return LLMRequest(invocation_id="inv-1", deployment_id="dep", system_rules="rules",
                      content_blocks=[{"type": "text", "text": "hallo", "data_label": "public"}], output_contract=contract,
                      max_output_tokens=512)


def _deployment(catalog, dep_id="fixture-deployment-a", **overrides):
    dep = catalog.deployment(dep_id)
    return dep.model_copy(update={"provider_model_id": "configured-model-id", "secret_ref": "secret://providers/test", **overrides})


def _http_response(status: int) -> httpx2.Response:
    return httpx2.Response(status, request=httpx2.Request("POST", "https://provider.invalid/v1"))


# ---------------------------------------------------------------- secrets

def test_secret_ref_maps_to_env_name(monkeypatch):
    assert env_name_for("secret://providers/anthropic") == "CAMPUS_SECRET_PROVIDERS_ANTHROPIC"
    monkeypatch.setenv("CAMPUS_SECRET_PROVIDERS_ANTHROPIC", "k")
    assert EnvSecretResolver().resolve("secret://providers/anthropic") == "k"
    monkeypatch.delenv("CAMPUS_SECRET_PROVIDERS_ANTHROPIC")
    with pytest.raises(Exception, match="nicht gesetzt"):
        EnvSecretResolver().resolve("secret://providers/anthropic")


# ---------------------------------------------------------------- anthropic

class FakeAnthropicMessages:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls: list[dict] = []

    def stream(self, **kwargs):  # the adapter streams; the context manager yields the final message
        self.calls.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        outcome = self.outcome

        class _Stream:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def get_final_message(self):
                return outcome

        return _Stream()


def _anthropic_message(text: str, stop_reason: str = "end_turn", model: str = "configured-model-id-2026") -> SimpleNamespace:
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text), SimpleNamespace(type="thinking", thinking="hidden")],
                           stop_reason=stop_reason, model=model, usage=SimpleNamespace(input_tokens=12, output_tokens=7), stop_details=None)


def _anthropic_adapter(outcome) -> tuple[AnthropicAdapter, FakeAnthropicMessages]:
    adapter = AnthropicAdapter(StaticSecrets())
    fake = FakeAnthropicMessages(outcome)
    adapter._client = lambda secret_ref, api_base: SimpleNamespace(messages=fake)  # type: ignore[method-assign]
    return adapter, fake


def test_anthropic_request_uses_catalog_model_and_enforced_schema(catalog):
    adapter, fake = _anthropic_adapter(_anthropic_message('{"ok": true}'))
    contract = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}
    response = adapter.invoke(_request(contract), _deployment(catalog))
    call = fake.calls[0]
    assert call["model"] == "configured-model-id"  # from the catalog, never from code
    assert call["output_config"] == {"format": {"type": "json_schema", "schema": contract}}
    assert "fallbacks" not in call and "thinking" not in call  # no hidden substitution, model default thinking
    assert response.effective_model_id == "configured-model-id-2026"
    assert response.usage.known and response.text() == '{"ok": true}'
    assert all(b["type"] == "text" for b in response.blocks), "thinking blocks are not returned"


def test_anthropic_structured_output_requires_catalog_capability(catalog):
    adapter, _ = _anthropic_adapter(_anthropic_message("x"))
    dep = _deployment(catalog)
    dep = dep.model_copy(update={"capabilities": dep.capabilities.model_copy(update={"structured_output": "unknown"})})
    with pytest.raises(CapabilityUnsupported):
        adapter.invoke(_request({"type": "object"}), dep)


@pytest.mark.parametrize(
    "exc, expected",
    [
        (anthropic.APITimeoutError(request=httpx2.Request("POST", "https://provider.invalid")), ProviderTimeout),
        (anthropic.RateLimitError("rl", response=_http_response(429), body=None), ProviderUnavailable),
        (anthropic.InternalServerError("ise", response=_http_response(500), body=None), ProviderUnavailable),
        (anthropic.AuthenticationError("auth", response=_http_response(401), body=None), ProviderRejected),
        (anthropic.NotFoundError("nf", response=_http_response(404), body=None), ProviderRejected),
        (anthropic.APIConnectionError(request=httpx2.Request("POST", "https://provider.invalid")), ProviderUnavailable),
    ],
)
def test_anthropic_failures_map_to_typed_classes(catalog, exc, expected):
    adapter, _ = _anthropic_adapter(exc)
    with pytest.raises(expected):
        adapter.invoke(_request(), _deployment(catalog))


def test_anthropic_refusal_is_reported_not_routed_around(catalog):
    msg = _anthropic_message("", stop_reason="refusal")
    msg.stop_details = SimpleNamespace(category="cyber", explanation="declined")
    adapter, _ = _anthropic_adapter(msg)
    with pytest.raises(ProviderRefusal) as excinfo:
        adapter.invoke(_request(), _deployment(catalog))
    assert excinfo.value.category == "cyber"


def test_anthropic_without_model_id_is_rejected(catalog):
    adapter, _ = _anthropic_adapter(_anthropic_message("x"))
    with pytest.raises(ProviderRejected):
        adapter.invoke(_request(), _deployment(catalog, provider_model_id=None))


# ---------------------------------------------------------------- openai

class FakeCompletions:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls: list[dict] = []

    def create(self, **kwargs):  # the adapter streams: an iterator of chunks, usage in the last one
        self.calls.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return iter(self.outcome)


def _openai_completion(text: str, finish_reason: str = "stop", refusal=None) -> list:
    delta = SimpleNamespace(content=text or None, tool_calls=None, refusal=refusal)
    return [SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish_reason)], model="configured-model-id-v1", usage=None),
            SimpleNamespace(choices=[], model="configured-model-id-v1", usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5))]


def _openai_adapter(outcome, name="openai") -> tuple[OpenAIAdapter, FakeCompletions]:
    adapter = OpenAIAdapter(StaticSecrets(), name=name)
    fake = FakeCompletions(outcome)
    adapter._client = lambda secret_ref, api_base: SimpleNamespace(chat=SimpleNamespace(completions=fake))  # type: ignore[method-assign]
    return adapter, fake


def test_openai_request_shape_and_strict_schema(catalog):
    adapter, fake = _openai_adapter(_openai_completion(json.dumps({"ok": True})))
    contract = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}
    response = adapter.invoke(_request(contract), _deployment(catalog, adapter="openai"))
    call = fake.calls[0]
    assert call["model"] == "configured-model-id"
    assert call["response_format"]["type"] == "json_schema" and call["response_format"]["json_schema"]["strict"] is True
    assert call["messages"][0]["role"] == "system" and call["stream"] is True and call["stream_options"] == {"include_usage": True}
    assert response.effective_model_id == "configured-model-id-v1" and response.stop_reason == "end_turn" and response.usage.known


@pytest.mark.parametrize(
    "exc, expected",
    [
        (openai.APITimeoutError(request=httpx2.Request("POST", "https://provider.invalid")), ProviderTimeout),
        (openai.RateLimitError("rl", response=_http_response(429), body=None), ProviderUnavailable),
        (openai.InternalServerError("ise", response=_http_response(503), body=None), ProviderUnavailable),
        (openai.AuthenticationError("auth", response=_http_response(401), body=None), ProviderRejected),
        (openai.BadRequestError("bad", response=_http_response(400), body=None), ProviderRejected),
    ],
)
def test_openai_failures_map_to_typed_classes(catalog, exc, expected):
    adapter, _ = _openai_adapter(exc)
    with pytest.raises(expected):
        adapter.invoke(_request(), _deployment(catalog, adapter="openai"))


def test_openai_content_filter_is_a_refusal(catalog):
    adapter, _ = _openai_adapter(_openai_completion("", finish_reason="content_filter"))
    with pytest.raises(ProviderRefusal):
        adapter.invoke(_request(), _deployment(catalog, adapter="openai"))


def test_openai_compatible_needs_an_approved_api_base(catalog, monkeypatch):
    from campus_domain.errors import CampusError, ErrorCode

    adapter = OpenAIAdapter(StaticSecrets(), name="openai_compatible")
    with pytest.raises(CampusError) as excinfo:
        adapter.invoke(_request(), _deployment(catalog, adapter="openai_compatible", api_base=None))
    assert excinfo.value.code == ErrorCode.ENDPOINT_NOT_APPROVED
    monkeypatch.setenv("CAMPUS_APPROVED_API_HOSTS", "")
    with pytest.raises(CampusError) as excinfo:
        adapter.invoke(_request(), _deployment(catalog, adapter="openai_compatible", api_base="https://unknown.example/v1"))
    assert excinfo.value.code == ErrorCode.ENDPOINT_NOT_APPROVED


def test_official_adapter_refuses_foreign_endpoint_before_building_a_client(catalog):
    from campus_domain.errors import CampusError, ErrorCode

    adapter = AnthropicAdapter(StaticSecrets())
    with pytest.raises(CampusError) as excinfo:
        adapter._client("secret://providers/test", "https://evil.example/v1")
    assert excinfo.value.code == ErrorCode.ENDPOINT_NOT_APPROVED
