"""Approved provider adapters. Each adapter is a separately approved software change (A01).

Only adapters named in CAMPUS_APPROVED_ADAPTERS are registered at runtime; the model identifier always comes
from the catalog deployment, never from code (I01). No adapter retries or substitutes models (I08).
"""
from __future__ import annotations

import os

from ..mock_adapter import MockAdapter
from ..port import Adapter
from ..secrets import EnvSecretResolver


def approved_adapter_names() -> list[str]:
    raw = os.environ.get("CAMPUS_APPROVED_ADAPTERS", "mock")
    return [n.strip() for n in raw.split(",") if n.strip()]


def build_adapters(names: list[str] | None = None) -> dict[str, Adapter]:
    names = names if names is not None else approved_adapter_names()
    secrets = EnvSecretResolver()
    registry: dict[str, Adapter] = {}
    for name in names:
        if name == "mock":
            registry["mock"] = MockAdapter()
        elif name == "anthropic":
            from .anthropic_adapter import AnthropicAdapter

            registry["anthropic"] = AnthropicAdapter(secrets)
        elif name in {"openai", "openai_compatible"}:
            from .openai_adapter import OpenAIAdapter

            registry[name] = OpenAIAdapter(secrets, name=name)
        else:
            raise ValueError(f"Unbekannter Adapter in CAMPUS_APPROVED_ADAPTERS: {name}")
    return registry
