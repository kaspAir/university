"""Deterministic mock adapter for S0 tests. Its output is a synthetic fixture, never research evidence."""
from __future__ import annotations

import hashlib

from campus_contracts.models import Deployment

from .port import LLMRequest, LLMResponse, Usage


class MockAdapter:
    name = "mock"

    def __init__(self, fail_invocation_ids: set[str] | None = None, hang_invocation_ids: set[str] | None = None):
        self._fail = fail_invocation_ids or set()
        self._hang = hang_invocation_ids or set()

    def invoke(self, request: LLMRequest, deployment: Deployment) -> LLMResponse:
        if request.invocation_id in self._hang:
            raise TimeoutError("mock timeout after send")  # outcome unknown: the call may have been processed
        if request.invocation_id in self._fail:
            raise ConnectionError("mock provider unavailable before send")
        text = " ".join(str(b.get("text", "")) for b in request.content_blocks)
        digest = hashlib.sha256((request.invocation_id + text).encode("utf-8")).hexdigest()[:12]
        input_tokens = max(1, len(text) // 4)
        reply = f"[MOCK {digest}] synthetic response; not a research result"
        return LLMResponse(
            invocation_id=request.invocation_id,
            deployment_id=deployment.id,
            effective_model_id=deployment.provider_model_id or "mock-unknown",
            blocks=[{"type": "text", "text": reply, "origin": "mock_adapter", "synthetic": True}],
            tool_requests=[],
            stop_reason="end_turn",
            usage=Usage(input_tokens=input_tokens, output_tokens=max(1, len(reply) // 4)),
            safety_status="not_evaluated",
            raw_ref=None,
        )
