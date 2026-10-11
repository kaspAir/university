"""OpenAI adapter on the official `openai` SDK (Chat Completions). Also serves `openai_compatible` endpoints
(api_base required). Same discipline as the Anthropic adapter: model id from the catalog, no SDK retries,
no model substitution, structured output only when technically enforced (json_schema, strict)."""
from __future__ import annotations

import openai

from campus_contracts.models import Deployment

from ..endpoints import check_api_base
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


class OpenAIAdapter:
    def __init__(self, secrets, name: str = "openai", timeout_seconds: float = 180.0):
        self.name = name
        self._secrets = secrets
        self._timeout = timeout_seconds

    def _client(self, secret_ref: str | None, api_base: str | None) -> openai.OpenAI:
        check_api_base(self.name, api_base)  # the key travels only to the approved endpoint (S1.1 finding 5)
        return openai.OpenAI(api_key=self._secrets.resolve(secret_ref), base_url=api_base or None, max_retries=0, timeout=self._timeout)

    def list_models(self, secret_ref: str | None, api_base: str | None) -> list[str]:
        client = self._client(secret_ref, api_base)
        try:
            return [m.id for m in client.models.list()]
        except openai.APIStatusError as exc:
            raise ProviderRejected(f"{exc.status_code}") from exc
        except openai.APIConnectionError as exc:
            raise ProviderUnavailable(str(exc)) from exc

    def invoke(self, request: LLMRequest, deployment: Deployment) -> LLMResponse:
        if not deployment.provider_model_id:
            raise ProviderRejected("Deployment ohne provider_model_id")
        client = self._client(deployment.secret_ref, deployment.api_base)
        user_text = "\n\n".join(str(b.get("text", "")) for b in request.content_blocks if b.get("type", "text") == "text")
        kwargs: dict = {
            "model": deployment.provider_model_id,
            "messages": [{"role": "system", "content": request.system_rules}, {"role": "user", "content": user_text}],
            "max_completion_tokens": request.max_output_tokens,
        }
        if request.output_contract is not None:
            if deployment.capabilities.structured_output != "supported":
                raise CapabilityUnsupported("structured_output ist für dieses Deployment nicht als unterstützt eingetragen")
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "campus_contract", "schema": request.output_contract, "strict": True},
            }
        if request.tool_definitions:
            if deployment.capabilities.tool_calling != "supported":
                raise CapabilityUnsupported("tool_calling ist für dieses Deployment nicht als unterstützt eingetragen")
            kwargs["tools"] = [
                {"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                                  "parameters": t["input_schema"], "strict": True}}
                for t in request.tool_definitions
            ]
        try:
            completion = client.chat.completions.create(**kwargs)
        except openai.APITimeoutError as exc:
            raise ProviderTimeout(str(exc)) from exc
        except openai.RateLimitError as exc:
            raise ProviderUnavailable(f"429 {exc.message}") from exc
        except (openai.AuthenticationError, openai.PermissionDeniedError, openai.NotFoundError,
                openai.BadRequestError, openai.UnprocessableEntityError) as exc:
            raise ProviderRejected(f"{exc.status_code}: {exc.message}") from exc
        except openai.APIStatusError as exc:
            if exc.status_code >= 500:
                raise ProviderUnavailable(f"{exc.status_code}") from exc
            raise ProviderRejected(f"{exc.status_code}: {exc.message}") from exc
        except openai.APIConnectionError as exc:
            raise ProviderUnavailable(str(exc)) from exc
        choice = completion.choices[0]
        if choice.finish_reason == "content_filter" or getattr(choice.message, "refusal", None):
            usage = completion.usage
            raise ProviderRefusal("content_filter", getattr(choice.message, "refusal", None),
                                  usage=Usage(input_tokens=usage.prompt_tokens if usage else None,
                                              output_tokens=usage.completion_tokens if usage else None))
        blocks = []
        if choice.message.content:
            blocks.append({"type": "text", "text": choice.message.content, "origin": "provider"})
        tool_requests = [
            {"id": call.id, "name": call.function.name, "input_json": call.function.arguments}
            for call in (choice.message.tool_calls or [])
        ]
        usage = completion.usage
        return LLMResponse(
            invocation_id=request.invocation_id,
            deployment_id=deployment.id,
            effective_model_id=completion.model,
            blocks=blocks,
            tool_requests=tool_requests,
            stop_reason={"stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use"}.get(choice.finish_reason or "", choice.finish_reason or "unknown"),
            usage=Usage(input_tokens=usage.prompt_tokens if usage else None, output_tokens=usage.completion_tokens if usage else None),
            safety_status="not_refused",
            raw_ref=None,
        )
