from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from campus_infra.audit import Actor, record
from campus_infra.models import User

from .. import services_identity
from ..deps import current_actor, get_db
from ..errors import trace_id
from ..security import SESSION_COOKIE, revoke_session
from ..settings import get_settings

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginBody(BaseModel):
    workspace_id: str = Field(min_length=1)
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


@router.post("/login")
def login(body: LoginBody, request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    user, token, csrf = services_identity.login(
        db, workspace_id=body.workspace_id, username=body.username, password=body.password,
        ttl_hours=settings.session_ttl_hours, correlation_id=trace_id(request),
    )
    roles = services_identity.roles_of(db, user)
    db.commit()
    response.set_cookie(
        SESSION_COOKIE, token, httponly=True, samesite="strict", secure=settings.cookie_secure,
        max_age=settings.session_ttl_hours * 3600, path="/",
    )
    return {"user_id": user.id, "display_name": user.display_name, "workspace_id": user.workspace_id, "roles": roles,
            "csrf_token": csrf, "capability_level": settings.capability_level}


@router.post("/logout")
def logout(request: Request, response: Response, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> dict:
    revoke_session(db, request.cookies.get(SESSION_COOKIE))
    record(db, actor=actor, command="auth.logout", object_type="user", object_id=actor.actor_id, object_version=None,
           correlation_id=trace_id(request), event_type="UserLoggedOut")
    db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"logged_out": True}


@router.get("/me")
def me(request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> dict:
    user = db.get(User, actor.actor_id)
    return {"user_id": user.id, "display_name": user.display_name, "workspace_id": actor.workspace_id,
            "roles": sorted(r.value for r in actor.roles), "csrf_token": request.state.session_row.csrf_token,
            "capability_level": get_settings().capability_level}
