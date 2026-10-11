"""Database-backed invocation journal (gateway port).

S1.1 finding 2: the journal is durable independently of the caller's transaction. Each entry is written and committed
in its own short session, so a later rollback of the business transaction (invalid content, blocked follow-up) can
never erase the evidence that an external call was made and what it consumed.
"""
from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from campus_contracts.models import Deployment
from campus_gateway.port import LLMRequest, LLMResponse, Usage

from .ids import utcnow
from .models import LLMInvocation


def request_hash(request: LLMRequest) -> str:
    return hashlib.sha256(
        json.dumps({"s": request.system_rules, "c": request.content_blocks, "t": request.tool_definitions,
                    "o": request.output_contract, "m": request.max_output_tokens}, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


class DbInvocationJournal:
    def __init__(self, session_factory: sessionmaker[Session], workspace_id: str):
        self._factory = session_factory
        self._workspace_id = workspace_id

    def begin(self, request: LLMRequest, deployment: Deployment, routing_decision_id: str) -> None:
        with self._factory() as db:
            if db.get(LLMInvocation, request.invocation_id) is not None:
                raise RuntimeError(f"invocation {request.invocation_id} already journaled; refusing a second send under the same id")
            db.add(LLMInvocation(id=request.invocation_id, workspace_id=self._workspace_id, deployment_id=deployment.id,
                                 routing_decision_id=routing_decision_id, request_hash=request_hash(request), state="pending"))
            db.commit()

    def complete(self, response: LLMResponse) -> None:
        with self._factory() as db:
            row = db.get(LLMInvocation, response.invocation_id)
            assert row is not None
            row.state = "completed"
            row.effective_model_id = response.effective_model_id
            row.input_tokens = response.usage.input_tokens
            row.output_tokens = response.usage.output_tokens
            row.completed_at = utcnow()
            db.commit()

    def fail(self, invocation_id: str, reason: str, usage: Usage | None = None) -> None:
        with self._factory() as db:
            row = db.get(LLMInvocation, invocation_id)
            assert row is not None
            row.state = "failed"
            row.error = reason
            if usage is not None:
                row.input_tokens = usage.input_tokens
                row.output_tokens = usage.output_tokens
            row.completed_at = utcnow()
            db.commit()

    def mark_unknown(self, invocation_id: str, reason: str) -> None:
        with self._factory() as db:
            row = db.get(LLMInvocation, invocation_id)
            assert row is not None
            row.state = "outcome_unknown"
            row.error = reason
            db.commit()

    def entries(self, prefix: str) -> list[LLMInvocation]:
        with self._factory() as db:
            return list(db.execute(select(LLMInvocation).where(LLMInvocation.id.like(prefix + "%"))).scalars().all())
