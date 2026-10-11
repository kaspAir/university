"""Mandates and reserved human decisions (Art. 9, 11)."""
from __future__ import annotations

from dataclasses import dataclass, field

from .errors import CampusError, ErrorCode
from .statuses import DecisionOption, MandateState


@dataclass(frozen=True)
class Condition:
    """A structured obligation: text, responsible role or person, due date (ISO). Never free-text decoration."""

    text: str
    responsible: str
    due: str


@dataclass
class HumanDecisionInput:
    option: DecisionOption
    rationale: str
    alternatives_presented: list[str]
    remaining_objections: list[str]
    consequences: str
    conditions: list[Condition] = field(default_factory=list)


def validate_decision(decision: HumanDecisionInput) -> None:
    """Art. 11: a reserved decision names alternatives, carrying reasons, remaining objections and consequences."""
    problems = []
    if not decision.rationale.strip():
        problems.append("rationale fehlt")
    if len(decision.alternatives_presented) < 2:
        problems.append("mindestens zwei vorgelegte Alternativen fehlen")
    if not decision.consequences.strip():
        problems.append("consequences fehlt")
    if decision.option == DecisionOption.ACCEPT_LIMITED and not decision.conditions:
        problems.append("Begrenzt annehmen verlangt mindestens eine Auflage")
    for cond in decision.conditions:
        if not (cond.text.strip() and cond.responsible.strip() and cond.due.strip()):
            problems.append("Auflage braucht Text, Zuständigkeit und Frist")
    if problems:
        raise CampusError(
            ErrorCode.VALIDATION_ERROR,
            "Vorbehaltener Entscheid unvollständig: " + "; ".join(problems),
            blocked_action="mandate.decide",
            required_resolution="Felder gemäss Art. 11 ergänzen",
        )


def transition(current: MandateState, option: DecisionOption) -> tuple[MandateState, bool]:
    """Return (new state, needs_new_version). Only drafts can be decided; nothing is approved by default."""
    if current != MandateState.DRAFT:
        raise CampusError(
            ErrorCode.INVALID_TRANSITION,
            f"Mandatsfassung ist {current.value}; nur Entwürfe werden entschieden",
            blocked_action="mandate.decide",
        )
    if option in {DecisionOption.ACCEPT, DecisionOption.ACCEPT_LIMITED}:
        return MandateState.APPROVED, False
    if option == DecisionOption.REJECT:
        return MandateState.REJECTED, False
    return MandateState.DRAFT, True  # revise: the draft stays, a new version is expected
