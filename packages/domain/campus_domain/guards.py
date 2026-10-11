"""Start guard for runs (Art. 9, 12; chapter 16). Limits are enforced outside the generating agents."""
from __future__ import annotations

from dataclasses import dataclass, field

from .errors import CampusError, ErrorCode
from .statuses import ActivationState, MandateState, OperatingStatus, QualificationStatus


@dataclass
class RunStartContext:
    workspace_stopped: bool
    mandate_state: MandateState | None
    policy_state: ActivationState | None
    policy_revision: str | None
    missing_limits: list[str]
    catalog_state: ActivationState | None
    agent_registered: bool
    agent_operating_status: OperatingStatus | None
    agent_qualification_status: QualificationStatus | None
    task_qualification_mode: str
    route_state: str | None
    reservation_present: bool
    review_required: bool
    review_reservation_present: bool
    task_hold_reason: str | None = None
    catalog_revision_matches: bool = True
    extra: dict = field(default_factory=dict)


@dataclass
class InvocationContext:
    """The common pre-call check for EVERY external model call: selector, generation, review (S1.1 finding 1)."""

    purpose: str  # selector | generation | review
    workspace_stopped: bool
    mandate_state: MandateState | None
    task_hold_reason: str | None
    route_state: str | None
    reservation_present: bool
    policy_revision: str | None = None


def check_invocation(ctx: InvocationContext) -> None:
    """Order: global stop, mandate, task hold (e.g. unresolved protected refusal), authorized route, bound reservation."""
    action = f"llm.invoke:{ctx.purpose}"
    if ctx.workspace_stopped:
        raise CampusError(ErrorCode.WORKSPACE_STOPPED, "Alle Läufe sind angehalten (Stopp Forschungsleitung); kein Modellaufruf",
                          blocked_action=action, required_resolution="Wiederaufnahme mit dokumentiertem Grund (Art. 12)")
    if ctx.mandate_state != MandateState.APPROVED:
        raise CampusError(ErrorCode.MANDATE_NOT_APPROVED, "Kein freigegebenes Mandat für diese Aufgabe (T02)", blocked_action=action)
    if ctx.task_hold_reason:
        raise CampusError(ErrorCode.TASK_HELD, f"Aufgabe ist gesperrt: {ctx.task_hold_reason}", blocked_action=action,
                          required_resolution="Sperre durch Forschungsleitung oder Integritätsfunktion mit Begründung aufheben (Art. 12, 18)")
    if ctx.route_state != "authorized":
        raise CampusError(ErrorCode.ROUTE_NOT_AUTHORIZED, "Keine vom Kontrollkern autorisierte Route für diesen Aufruf", blocked_action=action,
                          policy_version=ctx.policy_revision)
    if not ctx.reservation_present:
        raise CampusError(ErrorCode.BUDGET_EXHAUSTED, f"Keine an diese Route gebundene Reservierung ({ctx.purpose})", blocked_action=action,
                          policy_version=ctx.policy_revision)


def check_run_start(ctx: RunStartContext) -> None:
    """Raise the first blocking condition as a typed error. Order: stop, mandate, policy, catalog, agent, route, budget."""
    if ctx.workspace_stopped:
        raise CampusError(
            ErrorCode.WORKSPACE_STOPPED,
            "Alle Läufe sind angehalten (Stopp Forschungsleitung)",
            blocked_action="run.start",
            required_resolution="Wiederaufnahme mit dokumentiertem Grund (Art. 12)",
        )
    if ctx.mandate_state != MandateState.APPROVED:
        raise CampusError(
            ErrorCode.MANDATE_NOT_APPROVED,
            "Kein freigegebenes Mandat für diese Aufgabe (T02)",
            blocked_action="run.start",
            required_resolution="Mandat durch die Forschungsleitung freigeben lassen (Art. 9, 11)",
        )
    if ctx.task_hold_reason:
        raise CampusError(ErrorCode.TASK_HELD, f"Aufgabe ist gesperrt: {ctx.task_hold_reason}", blocked_action="run.start",
                          required_resolution="Sperre mit Begründung aufheben (Art. 12, 18)")
    if ctx.policy_state != ActivationState.ACTIVE:
        raise CampusError(
            ErrorCode.POLICY_NOT_ACTIVE,
            "Keine aktive Routing-Policy",
            blocked_action="run.start",
            required_resolution="Policy-Entwurf prüfen und aktivieren",
            policy_version=ctx.policy_revision,
        )
    if ctx.missing_limits:
        raise CampusError(
            ErrorCode.LIMITS_NOT_SET,
            "Betriebslimits der Policy sind nicht gesetzt: " + ", ".join(ctx.missing_limits),
            blocked_action="run.start",
            required_resolution="Limits vor dem ersten echten Lauf festlegen (config/README)",
            policy_version=ctx.policy_revision,
        )
    if ctx.catalog_state != ActivationState.ACTIVE:
        raise CampusError(
            ErrorCode.CATALOG_NOT_ACTIVE,
            "Kein aktiver Modellkatalog",
            blocked_action="run.start",
            required_resolution="Katalog-Entwurf aktivieren",
            policy_version=ctx.policy_revision,
        )
    if not ctx.agent_registered:
        raise CampusError(
            ErrorCode.AGENT_NOT_REGISTERED,
            "Agentenprofil ist nicht registriert; kein Start ohne Fassung, Bedarf und Laufbindung (T11)",
            blocked_action="run.start",
            required_resolution="Agentenprofil als Kandidat registrieren (Art. 26)",
            policy_version=ctx.policy_revision,
        )
    if ctx.agent_operating_status not in {OperatingStatus.ACTIVE, OperatingStatus.PLANNED, OperatingStatus.IN_REVIEW}:
        raise CampusError(
            ErrorCode.AGENT_NOT_REGISTERED,
            f"Agentenprofil ist {ctx.agent_operating_status.value if ctx.agent_operating_status else 'unbekannt'} und darf nicht laufen",
            blocked_action="run.start",
            policy_version=ctx.policy_revision,
        )
    if ctx.task_qualification_mode == "qualified" and ctx.agent_qualification_status != QualificationStatus.TASK_QUALIFIED:
        # Unevaluated or experimental profiles may only run tasks declared as sandbox_experiment (Art. 26).
        raise CampusError(
            ErrorCode.QUALIFICATION_MISSING,
            "Aufgabe verlangt ein qualifiziertes Profil; Profil ist "
            + (ctx.agent_qualification_status.value if ctx.agent_qualification_status else "ungeprüft"),
            blocked_action="run.start",
            required_resolution="Getrennte Qualifikation durchführen oder Aufgabe als sandbox_experiment führen",
            policy_version=ctx.policy_revision,
        )
    if ctx.route_state != "authorized":
        raise CampusError(
            ErrorCode.ROUTE_NOT_AUTHORIZED,
            "Keine vom Kontrollkern autorisierte Modellroute",
            blocked_action="run.start",
            required_resolution="Routenvorschlag prüfen lassen (route.authorize)",
            policy_version=ctx.policy_revision,
        )
    if not ctx.catalog_revision_matches:
        raise CampusError(
            ErrorCode.ROUTE_NOT_AUTHORIZED,
            "Die Route wurde gegen eine andere Katalogfassung autorisiert; Route neu bestimmen (Fassungsbindung)",
            blocked_action="run.start",
            required_resolution="route.authorize erneut aufrufen",
            policy_version=ctx.policy_revision,
        )
    if not ctx.reservation_present:
        raise CampusError(
            ErrorCode.BUDGET_EXHAUSTED,
            "Keine Budgetreservierung für den Erzeugungslauf",
            blocked_action="run.start",
            required_resolution="Mittel reservieren",
            policy_version=ctx.policy_revision,
        )
    if ctx.review_required and not ctx.review_reservation_present:
        raise CampusError(
            ErrorCode.BUDGET_EXHAUSTED,
            "Erzeugungsbudget reicht, die vorgesehene Prüfreserve fehlt; tragender Lauf beginnt nicht (A04)",
            blocked_action="run.start",
            required_resolution="Prüfreserve für das Gegenprüfungsmodell reservieren",
            policy_version=ctx.policy_revision,
        )
