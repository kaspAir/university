"""Audit and outbox in the same transaction as the business change (chapter 16, ADR-0004)."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from .models import AuditEvent, OutboxEvent


@dataclass(frozen=True)
class Actor:
    actor_type: str  # user | service_principal | system
    actor_id: str
    workspace_id: str
    roles: frozenset = frozenset()


def _fit(value: str | None, width: int) -> str | None:
    if value is None or len(value) <= width:
        return value
    import hashlib

    return value[: width - 9] + "~" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


def record(
    session: Session,
    *,
    actor: Actor,
    command: str,
    object_type: str,
    object_id: str,
    object_version: str | None,
    correlation_id: str,
    policy_decision: str = "allowed",
    payload: dict | None = None,
    event_type: str | None = None,
    project_id: str | None = None,
) -> AuditEvent:
    """Append one audit event and, for allowed commands, one outbox event. Payloads carry IDs, not full texts.

    Identifier columns have fixed widths; a long value (an address, a provider message) is shortened to the column with a hash tail so an
    audit write never fails for its length (first F-01 discovery, 14.10.2026: a search hit address longer than 80 characters)."""
    command, object_type, object_id = _fit(command, 80), _fit(object_type, 40), _fit(object_id, 80)
    object_version, policy_decision, correlation_id = _fit(object_version, 40), _fit(policy_decision, 40), _fit(correlation_id, 80)
    event = AuditEvent(
        workspace_id=actor.workspace_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        command=command,
        object_type=object_type,
        object_id=object_id,
        object_version=object_version,
        policy_decision=policy_decision,
        correlation_id=correlation_id,
        payload=payload or {},
    )
    session.add(event)
    if policy_decision == "allowed":
        session.add(
            OutboxEvent(
                workspace_id=actor.workspace_id,
                event_type=event_type or _event_name(command),
                object_type=object_type,
                object_id=object_id,
                object_version=object_version,
                project_id=project_id,
                payload=payload or {},
            )
        )
    session.flush()
    return event


def _event_name(command: str) -> str:
    """mandate.decide -> MandateDecided; run.start -> RunStarted (best effort naming for the event stream)."""
    obj, _, verb = command.partition(".")
    past = {"create": "Created", "decide": "Decided", "import": "Imported", "activate": "Activated",
            "register": "Registered", "authorize": "Authorized", "start": "Started", "cancel": "Cancelled",
            "stop": "Stopped", "resume": "Resumed", "set": "Set", "create_child": "ChildCreated",
            "reserve": "Reserved", "login": "LoggedIn", "logout": "LoggedOut"}
    return obj.capitalize() + past.get(verb, verb.capitalize())
