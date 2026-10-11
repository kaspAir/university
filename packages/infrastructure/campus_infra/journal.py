"""Database-backed invocation journal (gateway port). Begun before the call, never rewritten silently."""
from __future__ import annotations

import hashlib
import json

from sqlalchemy.orm import Session

from campus_contracts.models import Deployment
from campus_gateway.port import LLMRequest, LLMResponse

from .ids import utcnow
from .models import LLMInvocation


class DbInvocationJournal:
    def __init__(self, session: Session, workspace_id: str):
        self._session = session
        self._workspace_id = workspace_id

    def begin(self, request: LLMRequest, deployment: Deployment, routing_decision_id: str) -> None:
        digest = hashlib.sha256(
            json.dumps(
                {"s": request.system_rules, "c": request.content_blocks, "t": request.tool_definitions,
                 "o": request.output_contract, "m": request.max_output_tokens},
                sort_keys=True, ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        self._session.add(
            LLMInvocation(
                id=request.invocation_id,
                workspace_id=self._workspace_id,
                deployment_id=deployment.id,
                routing_decision_id=routing_decision_id,
                request_hash=digest,
                state="pending",
            )
        )
        self._session.flush()

    def complete(self, response: LLMResponse) -> None:
        row = self._session.get(LLMInvocation, response.invocation_id)
        assert row is not None
        row.state = "completed"
        row.effective_model_id = response.effective_model_id
        row.input_tokens = response.usage.input_tokens
        row.output_tokens = response.usage.output_tokens
        row.completed_at = utcnow()
        self._session.flush()

    def fail(self, invocation_id: str, reason: str) -> None:
        row = self._session.get(LLMInvocation, invocation_id)
        assert row is not None
        row.state = "failed"
        row.error = reason
        row.completed_at = utcnow()
        self._session.flush()

    def mark_unknown(self, invocation_id: str, reason: str) -> None:
        row = self._session.get(LLMInvocation, invocation_id)
        assert row is not None
        row.state = "outcome_unknown"
        row.error = reason
        self._session.flush()
