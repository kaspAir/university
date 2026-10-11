"""Request-scoped dependencies: database session, authenticated actor, CSRF, idempotent command execution."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator

from fastapi import Depends, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from campus_domain.authz import Role
from campus_domain.errors import CampusError, ErrorCode
from campus_infra.audit import Actor, record
from campus_infra.ids import new_id
from campus_infra.models import Grant, IdempotencyKey, User

from .errors import trace_id
from .security import CSRF_HEADER, SESSION_COOKIE, load_session

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


def get_db(request: Request) -> Iterator[Session]:
    factory = request.app.state.session_factory
    db: Session = factory()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def current_actor(request: Request, db: Session = Depends(get_db)) -> Actor:
    row = load_session(db, request.cookies.get(SESSION_COOKIE))
    if row is None:
        raise CampusError(ErrorCode.UNAUTHENTICATED, "Keine gültige Sitzung", required_resolution="Anmelden")
    if request.method in MUTATING and request.headers.get(CSRF_HEADER) != row.csrf_token:
        raise CampusError(ErrorCode.CSRF_FAILED, "CSRF-Token fehlt oder stimmt nicht", blocked_action=request.url.path)
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise CampusError(ErrorCode.UNAUTHENTICATED, "Benutzer inaktiv")
    roles = db.execute(select(Grant.role).where(Grant.user_id == user.id, Grant.workspace_id == user.workspace_id)).scalars().all()
    request.state.session_row = row
    return Actor(actor_type="user", actor_id=user.id, workspace_id=user.workspace_id, roles=frozenset(Role(r) for r in roles))


def _hash_payload(payload: object) -> str:
    return hashlib.sha256(json.dumps(jsonable_encoder(payload), sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def execute_command(
    db: Session,
    actor: Actor,
    request: Request,
    *,
    command: str,
    payload: object,
    handler: Callable[[str], tuple[int, object]],
    object_type: str = "command",
    object_id: str = "-",
) -> JSONResponse:
    """Run a state-changing command once per Idempotency-Key (ADR-0005) and audit blocked attempts."""
    key = request.headers.get("Idempotency-Key")
    if not key:
        raise CampusError(ErrorCode.VALIDATION_ERROR, "Header Idempotency-Key fehlt", blocked_action=command)
    correlation = trace_id(request)
    request_hash = _hash_payload(payload)
    existing = db.execute(
        select(IdempotencyKey).where(
            IdempotencyKey.workspace_id == actor.workspace_id,
            IdempotencyKey.actor_id == actor.actor_id,
            IdempotencyKey.command == command,
            IdempotencyKey.key == key,
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.request_hash != request_hash:
            raise CampusError(
                ErrorCode.IDEMPOTENCY_MISMATCH,
                "Idempotency-Key wurde bereits mit anderem Inhalt verwendet",
                blocked_action=command,
                required_resolution="Neuen Key für eine neue Anfrage verwenden",
            )
        return JSONResponse(existing.response_body, status_code=existing.response_status)
    try:
        status, body = handler(correlation)
    except CampusError as err:
        db.rollback()
        if not err.details.get("audited"):
            record(
                db,
                actor=actor,
                command=command,
                object_type=object_type,
                object_id=object_id,
                object_version=None,
                correlation_id=correlation,
                policy_decision=f"blocked:{err.code.value}",
                payload={"message": err.message, "blocked_action": err.blocked_action},
            )
            db.commit()
        raise
    encoded = jsonable_encoder(body)
    db.add(
        IdempotencyKey(
            id=new_id("IDK"),
            workspace_id=actor.workspace_id,
            actor_id=actor.actor_id,
            command=command,
            key=key,
            request_hash=request_hash,
            response_status=status,
            response_body=encoded,
        )
    )
    db.commit()
    return JSONResponse(encoded, status_code=status)
