"""Anthropic adapter on the official `anthropic` SDK (Messages API).

Deliberate choices, all traceable to the architecture:
- model id from the catalog deployment (I01); no default model in code
- max_retries=0: technical failures re-enter campus routing instead of silent SDK retries (chapter 10)
- no server-side fallbacks parameter: a refusal is reported, never routed around (I08, policy.fallback)
- structured output through output_config.format (technically enforced), never a "please answer in JSON" prompt (A08)
- thinking left at the model default; the raw chain of thought is not requested or stored
"""
from __future__ import annotations

import anthropic

from campus_contracts.models import Deployment

from ..port import (
    CapabilityUnsupported,
    LLMRequest,
    LLMResponse,
    ProviderRefusal,
    ProviderRejected,
    ProviderTimeout,
    ProviderUnavailable,
    Usage,
)


class AnthropicAdapter:
    name = "anthropic"

    def __init__(self, secrets, timeout_seconds: float = 180.0):
        self._secrets = secrets
        self._timeout = timeout_seconds

    def _client(self, secret_ref: str | None, api_base: str | None) -> anthropic.Anthropic:
        return anthropic.Anthropic(
            api_key=self._secrets.resolve(secret_ref),
            base_url=api_base or None,
            max_retries=0,
            timeout=self._timeout,
        )

    def list_models(self, secret_ref: str | None, api_base: str | None) -> list[str]:
        client = self._client(secret_ref, api_base)
        try:
            return [m.id for m in client.models.list()]
        except anthropic.APIStatusError as exc:
            raise ProviderRejected(f"{exc.status_code} {exc.type}") from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderUnavailable(str(exc)) from exc

    def invoke(self, request: LLMRequest, deployment: Deployment) -> LLMResponse:
        if not deployment.provider_model_id:
            raise ProviderRejected("Deployment ohne provider_model_id")
        client = self._client(deployment.secret_ref, deployment.api_base)
        content = [{"type": "text", "text": str(b.get("text", ""))} for b in request.content_blocks if b.get("type", "text") == "text"]
        kwargs: dict = {
            "model": deployment.provider_model_id,
            "max_tokens": request.max_output_tokens,
            "system": request.system_rules,
            "messages": [{"role": "user", "content": content}],
        }
        if request.output_contract is not None:
            if deployment.capabilities.structured_output != "supported":
                raise CapabilityUnsupported("structured_output ist für dieses Deployment nicht als unterstützt eingetragen")
            kwargs["output_config"] = {"format": {"type": "json_schema", "schema": request.output_contract}}
        if request.tool_definitions:
            if deployment.capabilities.tool_calling != "supported":
                raise CapabilityUnsupported("tool_calling ist für dieses Deployment nicht als unterstützt eingetragen")
            kwargs["tools"] = [
                {"name": t["name"], "description": t.get("description", ""), "input_schema": t["input_schema"], "strict": True}
                for t in request.tool_definitions
            ]
        try:
            message = client.messages.create(**kwargs)
        except anthropic.APITimeoutError as exc:
            raise ProviderTimeout(str(exc)) from exc
        except anthropic.RateLimitError as exc:
            raise ProviderUnavailable(f"429 {exc.type}") from exc
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError, anthropic.NotFoundError,
                anthropic.BadRequestError, anthropic.UnprocessableEntityError) as exc:
            raise ProviderRejected(f"{exc.status_code} {exc.type}: {exc.message}") from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500:
                raise ProviderUnavailable(f"{exc.status_code} {exc.type}") from exc
            raise ProviderRejected(f"{exc.status_code} {exc.type}") from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderUnavailable(str(exc)) from exc
        if message.stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            raise ProviderRefusal(getattr(details, "category", None), getattr(details, "explanation", None))
        blocks, tool_requests = [], []
        for block in message.content:
            if block.type == "text":
                blocks.append({"type": "text", "text": block.text, "origin": "provider"})
            elif block.type == "tool_use":
                tool_requests.append({"id": block.id, "name": block.name, "input": block.input})
            # thinking blocks are neither returned nor stored (chapter 9: no hidden reasoning as audit evidence)
        return LLMResponse(
            invocation_id=request.invocation_id,
            deployment_id=deployment.id,
            effective_model_id=message.model,
            blocks=blocks,
            tool_requests=tool_requests,
            stop_reason=message.stop_reason or "unknown",
            usage=Usage(input_tokens=message.usage.input_tokens, output_tokens=message.usage.output_tokens),
            safety_status="not_refused",
            raw_ref=None,  # S1: raw responses are not retained; a protected store is a later decision (chapter 15)
        )
