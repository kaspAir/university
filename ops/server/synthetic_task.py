"""Runs ONE synthetic research task through the real control path of the pilot instance, over the public HTTPS entry point, after the
research director has approved the mandate in the UI (Art. 11). Nothing here approves anything.

Path: approved mandate → project (institute Recht) → task (sandbox_experiment, fixture profile) → routing decision (fixture proposal,
core authorizes, reviewer distinct) → run (mock adapter, synthetic) → review by the other model → dossier artifact (hash).
Every result is synthetic and is labelled so by the platform (development_mock, mock adapter, synthetic artifacts).

Usage: .venv/bin/python ops/server/synthetic_task.py --workspace ws-campus --username kaspar --password-file <file> --base https://university.kaspair.ch
"""
from __future__ import annotations

import argparse
import copy
import os
import sys
from pathlib import Path

REPO = Path(os.environ["CAMPUS_HOME"]) if os.environ.get("CAMPUS_HOME") else Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "packages" / "contracts"))
sys.path.insert(0, str(REPO / "ops" / "server"))
from bootstrap_campus import MANDATE_DOC, Api  # noqa: E402

from campus_contracts.validation import load_example  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--username", required=True)
    ap.add_argument("--password-file", required=True)
    ap.add_argument("--base", required=True)
    a = ap.parse_args()
    api = Api(a.base)
    api.login(a.workspace, a.username, Path(a.password_file).read_text(encoding="utf-8").strip())
    mandates = [m for m in api.get("/mandates") if m["document"].get("title") == MANDATE_DOC["title"]]
    approved = [m for m in mandates if m["state"] == "approved"]
    if not approved:
        states = sorted({m["state"] for m in mandates}) or ["(kein Mandat)"]
        print(f"Mandat ist nicht freigegeben (Zustand {states}); der Entscheid gehört der Forschungsleitung (Art. 11). Nichts ausgeführt.")
        return 2
    mandate = approved[-1]
    institutes = api.get("/institutes")
    recht = next(i for i in institutes if i["institute_id"].endswith(":recht"))
    projects = [p for p in api.get("/projects") if p["title"].startswith("Synthetischer Pilotauftrag")]
    if projects:
        project = projects[-1]
        print("projekt vorhanden:", project["project_id"])
    else:
        project = api.cmd("/projects", {"mandate_version_id": mandate["mandate_version_id"], "institute_id": recht["institute_id"],
                                        "title": "Synthetischer Pilotauftrag: Verfahrensgarantien (Mock-Modelle)", "budget": "10.00"})
        print("projekt angelegt:", project["project_id"], "Institut", recht["name"])
    pid = project["project_id"]
    # the fixture research profile and its skill, registered once (needs a recorded need first: Art. 26)
    inventory = api.get("/inventory")
    if not any(ag["ref"] == "fixture-ai-researcher@1" for ag in inventory["agents"]):
        if not any(n["ref"] == "fixture-need-1" for n in api.get("/needs")):
            api.cmd("/needs", {"ref": "fixture-need-1", "project_id": pid, "gap": "Quellenanalyse für synthetisches Testmaterial fehlt",
                               "requested_specialization": "AI research methodology", "task_class": "source_analysis",
                               "alternatives_considered": ["bestehendes Profil (keines vorhanden)"], "expected_benefit": "Kontrollweg nachweisen"})
        api.cmd("/skills/candidates", {"document": load_example("skill_manifest")})
        api.cmd("/agents/candidates", {"document": load_example("agent_profile")})
        print("fixture-profil und skill registriert (Kandidaten, nicht qualifiziert; Modus sandbox_experiment)")
    need = copy.deepcopy(load_example("task_need"))
    for k in ("task_id", "workspace_id", "project_id", "mandate_version_ref", "budget_account_id"):
        need.pop(k, None)
    need.update({"agent_profile_version_ref": "fixture-ai-researcher@1", "qualification_mode": "sandbox_experiment", "routing_policy_ref": "pilot-policy-1"})
    task = api.cmd(f"/projects/{pid}/tasks", {"task_need": need, "budget": "1.00"})
    print("aufgabe:", task["task_id"])
    proposal = load_example("route_proposal") | {"task_id": task["task_id"]}
    decision = api.cmd(f"/tasks/{task['task_id']}/routing-decisions", {"proposal": proposal, "selection_mode": "fixture"})
    d = decision["decision"]
    print("route:", decision["routing_decision_id"], "erzeuger", d["selected_deployment_id"], "prüfer", d.get("review_deployment_id"))
    run = api.cmd("/runs", {"task_id": task["task_id"], "routing_decision_id": decision["routing_decision_id"]})
    print("lauf:", run["run_attempt_id"], run.get("state"), "synthetisch:", run.get("synthetic", "siehe Dossier"))
    review = api.cmd(f"/runs/{run['run_attempt_id']}/review", {})
    print("gegenprüfung:", review.get("review_report_id"), review.get("verdict"))
    dossier = api.cmd(f"/projects/{pid}/dossier", {})
    print("dossier eingefroren:", dossier.get("artifact_id"), "sha256", str(dossier.get("sha256", ""))[:12])
    print(f"ansehen: {a.base}/campus/dossiers/{pid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
