"""Gateway contract (architecture chapter 11)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from campus_contracts.models import Deployment


@dataclass(frozen=True)
class LLMRequest:
    invocation_id: str  # idempotent call identity; journaled before sending (chapter 16)
    deployment_id: str
    system_rules: str
    content_blocks: list[dict]  # typed blocks, each labelled with its data class and origin
    tool_definitions: list[dict] = field(default_factory=list)
    output_contract: dict | None = None  # JSON schema the answer must satisfy (technically enforced, never simulated)
    max_output_tokens: int = 1024
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Usage:
    input_tokens: int | None
    output_tokens: int | None

    @property
    def known(self) -> bool:
        return self.input_tokens is not None and self.output_tokens is not None


@dataclass(frozen=True)
class LLMResponse:
    invocation_id: str
    deployment_id: str
    effective_model_id: str  # what the provider actually reported
    blocks: list[dict]
    tool_requests: list[dict]
    stop_reason: str
    usage: Usage
    safety_status: str
    raw_ref: str | None  # protected raw response reference, never the raw body itself

    def text(self) -> str:
        return "\n".join(str(b.get("text", "")) for b in self.blocks if b.get("type") == "text")


# --- Provider failure classes. Adapters raise these; the gateway maps them to campus errors. ---------------------


class ProviderTimeout(Exception):
    """No answer within the deadline. The provider may have processed the call: outcome unknown."""


class ProviderUnavailable(Exception):
    """Rate limit, overload, 5xx or connection failure before an answer. Retryable through a new route (chapter 10)."""


class ProviderRejected(Exception):
    """4xx other than rate limit: invalid key, unknown model, malformed request. A configuration problem, not retryable."""


class ProviderRefusal(Exception):
    """The provider declined for safety reasons. Never routed around (policy fallback.protected_refusal)."""

    def __init__(self, category: str | None, explanation: str | None = None):
        super().__init__(f"refusal:{category or 'unspecified'}")
        self.category = category
        self.explanation = explanation


class CapabilityUnsupported(Exception):
    """The adapter cannot technically honour a required feature (e.g. enforced structured output). No silent downgrade."""


class SecretUnavailable(Exception):
    """The deployment's secret reference does not resolve in this process."""


class Adapter(Protocol):
    name: str

    def invoke(self, request: LLMRequest, deployment: Deployment) -> LLMResponse: ...


class ModelDiscovery(Protocol):
    """Optional: list native model identifiers. Discovery is neither data authorization nor qualification (chapter 8)."""

    def list_models(self, secret_ref: str | None, api_base: str | None) -> list[str]: ...


class InvocationJournal(Protocol):
    """Journal of every call: begun before sending, completed or marked unknown afterwards."""

    def begin(self, request: LLMRequest, deployment: Deployment, routing_decision_id: str) -> None: ...

    def complete(self, response: LLMResponse) -> None: ...

    def fail(self, invocation_id: str, reason: str) -> None: ...

    def mark_unknown(self, invocation_id: str, reason: str) -> None: ...
