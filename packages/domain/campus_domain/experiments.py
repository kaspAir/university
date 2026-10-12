"""Rules for isolated experiments (architecture chapter 13; T04, A10; constitution Art. 9, 14).

Pure rules, no Docker and no database:
  * an experiment starts only under an approved mandate, in an unstopped workspace, on an unheld task that declares `sandbox.run`;
  * a real runner needs an acceptance bound to runner kind, image digest and host; the mock runner is a fixture and only exists in
    development_mock; no runner means the capability is off (never in-process execution);
  * what an experiment proves is derived from the trusted runner's measurements, never from stdout;
  * experiment evidence supports a claim only when the run was measured and completed within its limits.
"""
from __future__ import annotations

from dataclasses import dataclass

from .errors import CampusError, ErrorCode
from .statuses import MandateState

SANDBOX_SCOPE = "sandbox.run"
MEASURING_RUNNERS = frozenset({"docker"})


@dataclass(frozen=True)
class ExperimentStartContext:
    workspace_stopped: bool
    mandate_state: MandateState | None
    task_hold_reason: str | None
    required_tool_scopes: tuple[str, ...]
    runner_kind: str | None  # None: no runner configured
    acceptance_present: bool
    capability_level: str


def check_experiment_start(ctx: ExperimentStartContext) -> None:
    if ctx.workspace_stopped:
        raise CampusError(ErrorCode.WORKSPACE_STOPPED, "Workspace ist gestoppt; kein Experiment", blocked_action="experiment.run")
    if ctx.mandate_state != MandateState.APPROVED:
        raise CampusError(ErrorCode.MANDATE_NOT_APPROVED, "Experimente brauchen ein aktives Mandat (Art. 9)", blocked_action="experiment.run",
                          required_resolution="Mandat genehmigen lassen")
    if ctx.task_hold_reason:
        raise CampusError(ErrorCode.TASK_HELD, f"Aufgabe ist gesperrt: {ctx.task_hold_reason}", blocked_action="experiment.run")
    if SANDBOX_SCOPE not in ctx.required_tool_scopes:
        raise CampusError(ErrorCode.FORBIDDEN, f"Aufgabe verlangt das Recht {SANDBOX_SCOPE} nicht; ein Rechenlauf ist kein freier Shell-Zugang",
                          blocked_action="experiment.run", required_resolution=f"Aufgabe mit required_tool_scopes [{SANDBOX_SCOPE}] anlegen")
    if ctx.runner_kind is None:
        raise CampusError(ErrorCode.CAPABILITY_UNSUPPORTED, "Kein Sandboxpfad konfiguriert (CAMPUS_SANDBOX_RUNNER=none); die Fähigkeit ist abgeschaltet, "
                          "nichts wird im Prozess ausgeführt", blocked_action="experiment.run", required_resolution="Sandbox-Runner konfigurieren und abnehmen")
    if ctx.runner_kind in MEASURING_RUNNERS and not ctx.acceptance_present:
        raise CampusError(ErrorCode.CAPABILITY_UNSUPPORTED, "Sandboxpfad nicht abgenommen: keine gültige Abnahme für Runner, Image und Host; "
                          "die Fähigkeit bleibt abgeschaltet", blocked_action="experiment.run",
                          required_resolution="Probe laufen lassen und Abnahme durch die Forschungsleitung eintragen")
    if ctx.runner_kind not in MEASURING_RUNNERS and ctx.capability_level != "development_mock":
        raise CampusError(ErrorCode.CAPABILITY_UNSUPPORTED, f"Runner {ctx.runner_kind} führt nichts aus und ist ausserhalb von development_mock unzulässig",
                          blocked_action="experiment.run")


def is_measured(runner_kind: str) -> bool:
    return runner_kind in MEASURING_RUNNERS


def evidence_valid(*, measured: bool, state: str) -> bool:
    """An experiment supports a computation only if a real runner measured a completed run (exit 0 within all limits)."""
    return measured and state == "completed"


def acceptance_matches(acceptance: dict | None, *, runner_kind: str, image: str, host_fingerprint: str) -> bool:
    """An acceptance binds exactly one runner kind, image digest and host; anything else is a different sandbox path."""
    if not acceptance or acceptance.get("revoked_at"):
        return False
    return (acceptance.get("runner_kind") == runner_kind and acceptance.get("image") == image
            and acceptance.get("host_fingerprint") == host_fingerprint)


def check_acceptable(report: dict, *, configured_runner: str | None, configured_image: str | None, current_fingerprint: str | None) -> None:
    """A probe report can be accepted only if it is a passed report of the measuring runner for the configured image on this host."""
    if report.get("runner_kind") not in MEASURING_RUNNERS:
        raise CampusError(ErrorCode.VALIDATION_ERROR, "Nur ein Bericht des messenden Runners (docker) kann abgenommen werden; ein Mock beweist nichts",
                          blocked_action="sandbox.accept")
    if report.get("runner_kind") != configured_runner or report.get("image") != configured_image:
        raise CampusError(ErrorCode.VALIDATION_ERROR, "Bericht gehört zu einem anderen Runner oder Image als konfiguriert", blocked_action="sandbox.accept")
    if current_fingerprint is None or report.get("host_fingerprint") != current_fingerprint:
        raise CampusError(ErrorCode.VALIDATION_ERROR, "Bericht stammt von einem anderen Host; Abnahme gilt nur für den geprüften Host", blocked_action="sandbox.accept")
    failed = [c["name"] for c in report.get("checks", []) if not c.get("passed")]
    if not report.get("checks") or failed:
        raise CampusError(ErrorCode.VALIDATION_ERROR, "Probe nicht bestanden; keine Abnahme: " + ", ".join(failed or ["keine Prüfungen"]),
                          blocked_action="sandbox.accept", details={"failed_checks": failed})
