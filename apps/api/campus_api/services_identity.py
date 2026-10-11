"""Workspace, users, grants, login (chapter 15, 19)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from campus_domain.authz import Role, authorize
from campus_domain.errors import CampusError, ErrorCode
from campus_infra.audit import Actor, record
from campus_infra.ids import new_id
from campus_infra.models import Grant, Institute, User, Workspace

from .security import hash_password, open_session, verify_password

# The five institutes of the constitution (Annex A) are the only seeded master data. No persons.
INSTITUTES = [
    ("grundlagen", "Grundlagen und formale Modelle",
     "Begriffsarbeit, Erkenntnistheorie, Theoriebildung, mathematische und formale Untersuchungen."),
    ("angewandt", "Angewandte Forschung und Entscheidungsprozesse",
     "Empirische und fallbezogene Untersuchungen, zunächst insbesondere Projektmanagement und Governance."),
    ("recht", "Recht, Institutionen und Gesellschaft",
     "Rechtsdogmatik, Rechtsvergleichung, Rechtsphilosophie und institutionelles Lernen."),
    ("ingenieur", "Ingenieurswesen und experimentelle Systeme",
     "Forschungssoftware, Testumgebungen, Simulationen und Prototypen."),
    ("meta", "Metawissenschaft, Methoden und Replikation",
     "Methodenbewertung, Gegenprüfung und Untersuchung der Forschungsorganisation."),
]


def init_workspace(db: Session, *, workspace_id: str, name: str, capability_level: str) -> Workspace:
    if db.get(Workspace, workspace_id) is not None:
        raise CampusError(ErrorCode.VERSION_CONFLICT, f"Workspace {workspace_id} existiert bereits")
    ws = Workspace(id=workspace_id, name=name, capability_level=capability_level)
    db.add(ws)
    db.flush()  # institutes reference the workspace; no relationship() is declared, so order explicitly
    for order, (key, title, remit) in enumerate(INSTITUTES):
        db.add(Institute(id=f"{workspace_id}:{key}", workspace_id=workspace_id, name=title, remit=remit, sort_order=order))
    db.flush()
    return ws


def create_user(
    db: Session, *, actor: Actor, workspace_id: str, username: str, display_name: str, password: str, roles: list[str],
    correlation_id: str,
) -> User:
    authorize("user.create", actor.roles)
    try:
        password_hash = hash_password(password)
    except ValueError as exc:
        raise CampusError(ErrorCode.VALIDATION_ERROR, str(exc)) from exc
    user = User(id=new_id("U"), workspace_id=workspace_id, username=username, display_name=display_name, password_hash=password_hash)
    db.add(user)
    db.flush()
    for role in roles:
        Role(role)  # validates
        db.add(Grant(id=new_id("G"), workspace_id=workspace_id, user_id=user.id, role=role))
    record(db, actor=actor, command="user.create", object_type="user", object_id=user.id, object_version=None,
           correlation_id=correlation_id, payload={"roles": roles})
    return user


def login(db: Session, *, workspace_id: str, username: str, password: str, ttl_hours: int, correlation_id: str) -> tuple[User, str, str]:
    user = db.execute(select(User).where(User.workspace_id == workspace_id, User.username == username)).scalar_one_or_none()
    if user is None or not user.is_active or not verify_password(user.password_hash, password):
        raise CampusError(ErrorCode.UNAUTHENTICATED, "Anmeldung fehlgeschlagen")
    token, csrf = open_session(db, user, ttl_hours)
    actor = Actor(actor_type="user", actor_id=user.id, workspace_id=workspace_id)
    record(db, actor=actor, command="auth.login", object_type="user", object_id=user.id, object_version=None,
           correlation_id=correlation_id, event_type="UserLoggedIn")
    return user, token, csrf


def roles_of(db: Session, user: User) -> list[str]:
    return list(db.execute(select(Grant.role).where(Grant.user_id == user.id)).scalars().all())
