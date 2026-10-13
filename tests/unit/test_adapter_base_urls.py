"""Official adapters keep the SDK's versioned default base once the host is approved; a host-only api_base from the catalog must not
strip the version path (the first live discovery against api.openai.com answered 404 for exactly that reason)."""
from __future__ import annotations

from campus_gateway.adapters.anthropic_adapter import AnthropicAdapter
from campus_gateway.adapters.openai_adapter import OpenAIAdapter


class _Secrets:
    def resolve(self, ref):
        return "test-key-never-sent"


def test_openai_official_host_keeps_the_versioned_default_base():
    client = OpenAIAdapter(_Secrets())._client("secret://providers/openai", "https://api.openai.com")
    assert str(client.base_url).rstrip("/") == "https://api.openai.com/v1"
    assert str(OpenAIAdapter(_Secrets())._client("secret://providers/openai", None).base_url).rstrip("/") == "https://api.openai.com/v1"


def test_anthropic_official_host_matches_the_sdk_default():
    client = AnthropicAdapter(_Secrets())._client("secret://providers/anthropic", "https://api.anthropic.com")
    assert str(client.base_url).rstrip("/") == "https://api.anthropic.com"
