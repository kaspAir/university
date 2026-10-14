"""Both provider adapters stream their answers (no fixed wall-clock limit for long answers) and still report model id, usage, stop reason,
refusals and broken connections the way the gateway expects. The SDK clients are replaced by fakes; nothing leaves the machine."""
from __future__ import annotations

from types import SimpleNamespace

import openai
import pytest

from campus_contracts.models import Deployment
from campus_contracts.validation import load_example
from campus_gateway.adapters.anthropic_adapter import AnthropicAdapter
from campus_gateway.adapters.openai_adapter import OpenAIAdapter
from campus_gateway.port import LLMRequest, ProviderRefusal, ProviderTimeout, ProviderUnavailable


class _Secrets:
    def resolve(self, ref):
        return "test-key"


def _deployment(adapter: str) -> Deployment:
    base = load_example("model_catalog")["deployments"][0]
    base.update({"id": f"{adapter}-x", "provider_id": adapter, "adapter": adapter, "provider_model_id": "model-x", "canonical_model_id": f"{adapter}/x",
                 "secret_ref": f"secret://providers/{adapter}", "api_base": None, "capabilities": {**base["capabilities"], "structured_output": "supported"}})
    return Deployment.model_validate(base)


REQUEST = LLMRequest(invocation_id="i-1", deployment_id="x", system_rules="r", content_blocks=[{"type": "text", "text": "frage"}],
                     output_contract={"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"], "additionalProperties": False},
                     max_output_tokens=128000)


# ---------------------------------------------------------------- Anthropic


class _FakeAnthropicStream:
    def __init__(self, message):
        self._message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self._message


def test_anthropic_streams_and_returns_the_final_message(monkeypatch):
    seen = {}
    message = SimpleNamespace(model="model-x-20261001", stop_reason="end_turn", usage=SimpleNamespace(input_tokens=40000, output_tokens=9000),
                              content=[SimpleNamespace(type="thinking", thinking="…"), SimpleNamespace(type="text", text='{"a": "b"}')])

    def stream(**kwargs):
        seen.update(kwargs)
        return _FakeAnthropicStream(message)

    adapter = AnthropicAdapter(_Secrets())
    monkeypatch.setattr(adapter, "_client", lambda *a: SimpleNamespace(messages=SimpleNamespace(stream=stream)))
    response = adapter.invoke(REQUEST, _deployment("anthropic"))
    assert seen["max_tokens"] == 128000 and seen["output_config"]["format"]["type"] == "json_schema"
    assert response.text() == '{"a": "b"}' and response.effective_model_id == "model-x-20261001", "thinking blocks are not returned"
    assert response.usage.input_tokens == 40000 and response.usage.output_tokens == 9000 and response.stop_reason == "end_turn"


# ---------------------------------------------------------------- OpenAI


def _chunk(content=None, finish=None, usage=None, refusal=None, model="gpt-x-2026"):
    choices = [] if usage is not None and content is None and finish is None else [
        SimpleNamespace(delta=SimpleNamespace(content=content, refusal=refusal, tool_calls=None), finish_reason=finish)]
    return SimpleNamespace(model=model, choices=choices, usage=usage)


def _openai(monkeypatch, chunks, seen=None):
    def create(**kwargs):
        if seen is not None:
            seen.update(kwargs)
        yield from chunks() if callable(chunks) else chunks

    adapter = OpenAIAdapter(_Secrets())
    monkeypatch.setattr(adapter, "_client", lambda *a: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    return adapter


def test_openai_streams_aggregates_text_and_reads_usage_from_the_last_chunk(monkeypatch):
    seen: dict = {}
    chunks = [_chunk('{"a": '), _chunk('"b"}'), _chunk(finish="stop"),
              _chunk(usage=SimpleNamespace(prompt_tokens=30000, completion_tokens=700))]
    response = _openai(monkeypatch, chunks, seen).invoke(REQUEST, _deployment("openai"))
    assert seen["stream"] is True and seen["stream_options"] == {"include_usage": True} and seen["max_completion_tokens"] == 128000
    assert response.text() == '{"a": "b"}' and response.stop_reason == "end_turn" and response.effective_model_id == "gpt-x-2026"
    assert response.usage.input_tokens == 30000 and response.usage.output_tokens == 700


def test_openai_refusal_and_cut_answer_are_reported(monkeypatch):
    chunks = [_chunk(refusal="nein"), _chunk(finish="stop"), _chunk(usage=SimpleNamespace(prompt_tokens=10, completion_tokens=2))]
    with pytest.raises(ProviderRefusal) as err:
        _openai(monkeypatch, chunks).invoke(REQUEST, _deployment("openai"))
    assert err.value.usage.input_tokens == 10
    response = _openai(monkeypatch, [_chunk('{"a": "lang'), _chunk(finish="length")]).invoke(REQUEST, _deployment("openai"))
    assert response.stop_reason == "max_tokens" and response.usage.known is False, "a cut answer is flagged, missing usage stays unknown"


def test_openai_broken_connection_mid_answer_is_outcome_unknown_before_any_chunk_unavailable(monkeypatch):
    request = SimpleNamespace(method="POST", url="https://api.openai.com/v1/chat/completions")

    def mid():
        yield _chunk('{"a": ')
        raise openai.APIConnectionError(request=request)

    with pytest.raises(ProviderTimeout):
        _openai(monkeypatch, mid).invoke(REQUEST, _deployment("openai"))

    def before():
        raise openai.APIConnectionError(request=request)
        yield  # pragma: no cover

    with pytest.raises(ProviderUnavailable):
        _openai(monkeypatch, before).invoke(REQUEST, _deployment("openai"))
