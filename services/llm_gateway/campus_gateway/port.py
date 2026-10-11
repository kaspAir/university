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
    output_contract: dict | None = None
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


class Adapter(Protocol):
    name: str

    def invoke(self, request: LLMRequest, deployment: Deployment) -> LLMResponse: ...


class InvocationJournal(Protocol):
    """Journal of every call: begun before sending, completed or marked unknown afterwards."""

    def begin(self, request: LLMRequest, deployment: Deployment, routing_decision_id: str) -> None: ...

    def complete(self, response: LLMResponse) -> None: ...

    def fail(self, invocation_id: str, reason: str) -> None: ...

    def mark_unknown(self, invocation_id: str, reason: str) -> None: ...
