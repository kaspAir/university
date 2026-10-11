"""Qualification rules for agent and skill candidates (Art. 19, 26, 28; architecture chapter 7; T05, T10, T12, T17, A10).

The factory may propose; only a separately qualified evaluator with fixed test material qualifies; only the core activates.
"""
from __future__ import annotations

from dataclasses import dataclass

from .errors import CampusError, ErrorCode

# Tool scopes a generated package may request. Everything else (secrets, host, docker, policy, grants) is a privilege change
# that needs a separate software release, never a candidate registration (A10, chapter 4).
CAMPUS_TOOL_SCOPES = frozenset({"sources.read", "sources.fetch", "claims.propose", "claims.read", "sandbox.run", "dossier.read"})


def check_tool_scopes(scopes: list[str], *, what: str) -> None:
    unknown = sorted(set(scopes) - CAMPUS_TOOL_SCOPES)
    if unknown:
        raise CampusError(
            ErrorCode.FORBIDDEN,
            f"{what} verlangt Rechte ausserhalb der Werkzeugkontrolle: {unknown}. Neue Rechte sind eine Softwareänderung, keine Registrierung (A10)",
            blocked_action="registry.register",
            required_resolution="Nur freigegebene Tool-Scopes anfordern: " + ", ".join(sorted(CAMPUS_TOOL_SCOPES)),
        )


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    passed: bool
    score: int
    reasons: tuple[str, ...]
    false_alarm_risk: str
    candidate_output_sha256: str
    evaluator_canonical_model_id: str


def aggregate(results: list[CaseResult], threshold_percent: int) -> tuple[int, bool]:
    """Score = share of passed cases in percent; passed when the share reaches the suite's threshold."""
    if not results:
        return 0, False
    share = round(100 * sum(1 for r in results if r.passed) / len(results))
    return share, share >= threshold_percent


def check_independence(*, candidate_canonical: str | None, evaluator_canonical: str | None, evaluator_qualified: bool) -> None:
    """T12: nobody certifies themselves, and an unqualified evaluator is not a substitute (chapter 7)."""
    if not candidate_canonical or not evaluator_canonical:
        raise CampusError(ErrorCode.REVIEW_INDEPENDENCE_VIOLATED, "Kanonische Modellidentität von Kandidat oder Prüfer unbekannt",
                          blocked_action="evaluation.run")
    if candidate_canonical == evaluator_canonical:
        raise CampusError(ErrorCode.REVIEW_INDEPENDENCE_VIOLATED, "Ein Kandidat prüft sich nicht selbst (T12)", blocked_action="evaluation.run",
                          required_resolution="Prüfmodell mit anderer kanonischer Identität wählen")
    if not evaluator_qualified:
        raise CampusError(ErrorCode.QUALIFICATION_MISSING,
                          "Kein für die Prüfaufgabe qualifizierter Prüfer verfügbar; ein ungeprüfter Prüfer wird nicht zum Selbstzertifikat (Kap. 7)",
                          blocked_action="evaluation.run",
                          required_resolution="Prüfer-Qualifikation durch Forschungsleitung registrieren (Bootstrap) oder qualifizieren")


def is_regression(new_score: int, baseline_score: int | None) -> bool:
    """A candidate version must not fall behind the active version on the same suite (Art. 28, T17)."""
    return baseline_score is not None and new_score < baseline_score


def activation_allowed(*, passed: bool, regression: bool) -> tuple[bool, str]:
    if not passed:
        return False, "Prüfsatz nicht bestanden; Kandidat bleibt begrenzt"
    if regression:
        return False, "Rückschritt gegenüber der aktiven Fassung auf demselben Prüfsatz; keine stille Ablösung (T17)"
    return True, "bestanden ohne Rückschritt"
