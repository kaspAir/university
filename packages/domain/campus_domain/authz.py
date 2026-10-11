"""Role-based command authorization. Attribute checks (workspace, project) happen in the services."""
from __future__ import annotations

from enum import StrEnum

from .errors import CampusError, ErrorCode


class Role(StrEnum):
    RESEARCH_DIRECTOR = "research_director"  # human: mandates, protection standards, release (Art. 6, 11)
    INTEGRITY_OFFICER = "integrity_officer"  # human escalation point, holds (Art. 7)
    OPERATOR = "operator"  # technical operation; no substantive approvals (Art. 7, chapter 15)
    REVIEWER = "reviewer"  # separate instance with separate rights (Art. 17)
    PRODUCER = "producer"  # generating role (Art. 17)


COMMAND_ROLES: dict[str, frozenset[Role]] = {
    "mandate.create": frozenset({Role.RESEARCH_DIRECTOR}),
    "mandate.decide": frozenset({Role.RESEARCH_DIRECTOR}),
    "policy.import": frozenset({Role.OPERATOR, Role.RESEARCH_DIRECTOR}),
    "policy.activate": frozenset({Role.RESEARCH_DIRECTOR}),
    "catalog.import": frozenset({Role.OPERATOR}),
    "catalog.activate": frozenset({Role.OPERATOR}),
    "pricing.set": frozenset({Role.OPERATOR}),
    "budget.create_child": frozenset({Role.RESEARCH_DIRECTOR}),
    "project.create": frozenset({Role.RESEARCH_DIRECTOR}),
    "task.create": frozenset({Role.RESEARCH_DIRECTOR, Role.PRODUCER}),
    "agent.register": frozenset({Role.RESEARCH_DIRECTOR, Role.PRODUCER}),
    "skill.register": frozenset({Role.RESEARCH_DIRECTOR, Role.PRODUCER}),
    "route.authorize": frozenset({Role.RESEARCH_DIRECTOR, Role.PRODUCER}),
    "run.start": frozenset({Role.RESEARCH_DIRECTOR, Role.PRODUCER}),
    "run.cancel": frozenset({Role.RESEARCH_DIRECTOR, Role.INTEGRITY_OFFICER, Role.OPERATOR}),
    "workspace.stop": frozenset({Role.RESEARCH_DIRECTOR, Role.INTEGRITY_OFFICER}),
    "workspace.resume": frozenset({Role.RESEARCH_DIRECTOR}),
    "user.create": frozenset({Role.OPERATOR}),
    "grant.create": frozenset({Role.OPERATOR}),
}


def authorize(command: str, roles: set[Role] | frozenset[Role]) -> None:
    """Raise FORBIDDEN unless one of the actor's roles may issue the command."""
    allowed = COMMAND_ROLES.get(command)
    if allowed is None:
        raise CampusError(ErrorCode.FORBIDDEN, f"Unbekanntes Command: {command}", blocked_action=command)
    if not (set(roles) & allowed):
        raise CampusError(
            ErrorCode.FORBIDDEN,
            "Rolle berechtigt nicht zu diesem Command",
            blocked_action=command,
            required_resolution="Grant mit einer der Rollen: " + ", ".join(sorted(r.value for r in allowed)),
        )
