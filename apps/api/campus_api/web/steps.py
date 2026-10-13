"""Presentation-only derivations for the research-order journey: pilot defaults for a task need and «where are we, what comes next».

No business rule lives here: every transition is decided by the services and the reserved human decisions. This module only reads
the state the services expose and names the next action a person can take in the browser.
"""
from __future__ import annotations

# Defaults a research order in the pilot gets for the parts of a task need the form does not ask for. Shown on the form, not hidden.
TASK_DEFAULTS: dict = {
    "schema_version": "1.0", "task_class": "source_analysis", "role": "producer", "language": "de-CH", "data_labels": ["public"],
    "required_features": ["text_input", "structured_output"], "context_tokens_estimate": 12000, "max_output_tokens": 4000,
    "required_tool_scopes": ["sources.read"], "substantive_contributor_model_ids": [],
}
MANDATE_DEFAULTS: dict = {
    "access_rules": "Nur öffentliche Quellen über den Internetdienst; kein Export vertraulicher Daten.",
    "risk_rules": "Keine Personendaten, keine Studien mit Menschen, keine Eingriffe in Produktivsysteme.",
    "review_requirements": "Jedes tragende Artefakt wird durch ein anderes kanonisches Modell gegengeprüft (Art. 27).",
    "duration_days": 30,
    "report_points": ["Tag 10: Zwischenstand", "Tag 30: Dossier"],
}


def task_need_for(profile: dict, policy_revision: str) -> dict:
    """A task need for a registered profile: qualified mode only for a task-qualified profile, otherwise the sandbox experiment mode
    the services demand for unqualified profiles (Art. 26). Skills: the profile's own."""
    qualified = profile.get("qualification_status") == "task_qualified" and TASK_DEFAULTS["task_class"] in (profile.get("qualified_task_classes") or [])
    return dict(TASK_DEFAULTS) | {"agent_profile_version_ref": profile["ref"], "skill_version_refs": list(profile.get("skill_version_refs") or []),
                                  "qualification_mode": "qualified" if qualified else "sandbox_experiment", "routing_policy_ref": policy_revision}


def next_step(*, stopped: bool, mandate_state: str | None, projects: int, tasks: list[dict], runs: list[dict], reviews: list[dict],
              artifacts: list[dict], workflows: list[dict]) -> dict:
    """The next human-visible step of an order. Returns code, title, hint and the action the page may offer."""
    if stopped:
        return {"code": "stopped", "title": "Alle Läufe angehalten", "hint": "Wiederaufnahme mit dokumentiertem Grund (Art. 12).", "action": None}
    if mandate_state is None or mandate_state == "draft":
        return {"code": "decide_mandate", "title": "Entscheid der Forschungsleitung offen", "hint": "Vorbehaltener Entscheid nach Art. 11: annehmen, begrenzt annehmen, überarbeiten oder ablehnen.", "action": "decide"}
    if mandate_state == "rejected":
        return {"code": "rejected", "title": "Mandat abgelehnt", "hint": "Kein Projekt unter diesem Mandat. Eine neue Fassung ist ein neuer Auftrag.", "action": None}
    if mandate_state == "superseded":
        return {"code": "superseded", "title": "Mandatsfassung ersetzt", "hint": "Die aktuelle Fassung findest du unter Prüfung & Freigabe.", "action": None}
    if mandate_state != "approved":
        return {"code": "revise", "title": "Mandat zur Überarbeitung", "hint": "Neue Fassung anlegen (API); die alte Fassung bleibt lesbar.", "action": None}
    if projects == 0:
        return {"code": "create_project", "title": "Projekt und Aufgabe anlegen", "hint": "Institut, Teilbudget und das Forscherprofil wählen; die Aufgabe erhält die Pilotvorgaben.", "action": "project"}
    if not tasks:
        return {"code": "create_task", "title": "Aufgabe anlegen", "hint": "Das Projekt hat noch keine Aufgabe.", "action": "project"}
    running = [w for w in workflows if w.get("status") in (None, "RUNNING")]
    if running:
        return {"code": "running", "title": "Ablauf läuft", "hint": "Quellen → Route → Erzeugung → Gegenprüfung → Dossier. Der Stand wird aus dem Ablaufdienst gelesen.", "action": "watch"}
    finished = [r for r in runs if r.get("state") == "finished"]
    if not runs and not workflows:
        return {"code": "start_workflow", "title": "Ablauf starten", "hint": "Optional öffentliche Quellen (eine URL je Zeile); ohne Quellen erzeugt der Lauf nur Hypothesen.", "action": "workflow"}
    if runs and not finished:
        return {"code": "run_failed", "title": "Lauf nicht beendet", "hint": "Endgrund im Tab Läufe; ein neuer Ablauf ist eine neue begründete Route.", "action": "workflow"}
    if finished and not reviews:
        return {"code": "review_open", "title": "Gegenprüfung offen", "hint": "Ohne Prüfung durch ein anderes Modell gilt das Ergebnis als offen, nie als bestanden (Art. 27).", "action": None}
    dossiers = [a for a in artifacts if a.get("kind") == "dossier"]
    if not dossiers:
        return {"code": "freeze_dossier", "title": "Dossier einfrieren", "hint": "Die aktuelle Fassung erhält einen Hash; Freigaben binden genau diese Fassung (T08).", "action": "freeze"}
    latest = dossiers[-1]
    approval = latest.get("approval")
    if approval and approval.get("valid_for_current") and not approval.get("basis_withdrawn_after_approval") and approval.get("option") in ("accept", "accept_limited"):
        return {"code": "approved", "title": "Ergebnis freigegeben", "hint": "Externe Freigabe und Einreichung bleiben ein gesonderter vorbehaltener Entscheid (Art. 22).", "action": None}
    if approval and approval.get("basis_withdrawn_after_approval"):
        return {"code": "basis_withdrawn", "title": "Grundlage nach Freigabe zurückgezogen", "hint": "Neubewertung fällig; die Freigabe trägt die Fassung nicht mehr (T09).", "action": "read"}
    return {"code": "judge", "title": "Ergebnis beurteilen", "hint": "Dossier lesen und die Ergebnisfreigabe entscheiden (Art. 11): annehmen, begrenzt, überarbeiten, ablehnen.", "action": "read"}
