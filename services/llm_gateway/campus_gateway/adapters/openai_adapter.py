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
    def __init__(self, secrets, name: str = "openai", timeout_seconds: float = 900.0):
        self.name = name
        self._secrets = secrets
        self._timeout = timeout_seconds

    def _client(self, secret_ref: str | None, api_base: str | None) -> openai.OpenAI:
        check_api_base(self.name, api_base)  # the key travels only to the approved endpoint (S1.1 finding 5)
        # The official adapter is bound to the host; the SDK's own default already carries the version path (https://api.openai.com/v1/),
        # so the catalog's host-only api_base must not replace it (it would turn /v1/models into /models: 404). Compatible endpoints
        # give their full base themselves.
        base_url = None if self.name == "openai" else (api_base or None)
        return openai.OpenAI(api_key=self._secrets.resolve(secret_ref), base_url=base_url, max_retries=0, timeout=self._timeout)

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
        content, refusal, tool_slots = [], [], {}
        finish_reason, model_id, usage = None, None, None
        try:
            # streamed with usage in the final chunk: the client timeout bounds the silence between chunks, not the whole answer
            stream = client.chat.completions.create(**kwargs, stream=True, stream_options={"include_usage": True})
            for chunk in stream:
                model_id = chunk.model or model_id
                if getattr(chunk, "usage", None):
                    usage = chunk.usage
                for choice in chunk.choices or []:
                    delta = choice.delta
                    if delta is not None:
                        if delta.content:
                            content.append(delta.content)
                        if getattr(delta, "refusal", None):
                            refusal.append(delta.refusal)
                        for call in getattr(delta, "tool_calls", None) or []:
                            slot = tool_slots.setdefault(call.index, {"id": None, "name": "", "arguments": ""})
                            slot["id"] = call.id or slot["id"]
                            if call.function is not None:
                                slot["name"] += call.function.name or ""
                                slot["arguments"] += call.function.arguments or ""
                    if choice.finish_reason:
                        finish_reason = choice.finish_reason
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
            if content or finish_reason:  # the connection broke mid-answer: the provider processed the request
                raise ProviderTimeout(f"Verbindung während der Antwort abgebrochen: {exc}") from exc
            raise ProviderUnavailable(str(exc)) from exc
        used = Usage(input_tokens=usage.prompt_tokens if usage else None, output_tokens=usage.completion_tokens if usage else None)
        if finish_reason == "content_filter" or refusal:
            raise ProviderRefusal("content_filter", "".join(refusal) or None, usage=used)
        blocks = [{"type": "text", "text": "".join(content), "origin": "provider"}] if content else []
        tool_requests = [{"id": s["id"], "name": s["name"], "input_json": s["arguments"]} for _, s in sorted(tool_slots.items())]
        return LLMResponse(
            invocation_id=request.invocation_id,
            deployment_id=deployment.id,
            effective_model_id=model_id or deployment.provider_model_id,
            blocks=blocks,
            tool_requests=tool_requests,
            stop_reason={"stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use"}.get(finish_reason or "", finish_reason or "unknown"),
            usage=used,
            safety_status="not_refused",
            raw_ref=None,
        )
