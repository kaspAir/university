"""Mandate drafts for real research orders, from a YAML file (ops/server/mandates/*.yaml), through the public HTTPS API.

  draft   creates the mandate DRAFT (idempotent by title) with the file's budget; the research director decides in the browser (Art. 11).
          Nothing is started and nothing costs money here.
  show    prints what the browser steps need: the institute hint, the start sources to paste into the flow form, the stages.
  task    adds a further task to the order's project with its own budget (--task-budget, default 3.00); an existing task keeps its limit.

Fields of the mandate document are exactly the campus mask (Art. 9). Anything else in the file (start_sources, institute) is for the
person, not for the model: start sources enter the campus only through the flow form, through the internet service, version-exact.

Usage: .venv/bin/python ops/server/live_mandate.py --workspace ws-campus --username <user> --password-file <file> --base https://<host> \\
         --file ops/server/mandates/F01_bki.yaml draft
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml

REPO = Path(os.environ["CAMPUS_HOME"]) if os.environ.get("CAMPUS_HOME") else Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "ops" / "server"))
sys.path.insert(0, str(REPO / "apps" / "api"))
from bootstrap_campus import Api  # noqa: E402

from campus_api.web.steps import task_need_for  # noqa: E402

REQUIRED = ("title", "question_space", "knowledge_goal", "method_frame", "data_labels", "access_rules", "risk_rules", "review_requirements",
            "duration_days", "report_points")


def load(path: Path) -> dict:
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    doc = spec["document"]
    missing = [f for f in REQUIRED if not doc.get(f)]
    if missing:
        raise SystemExit(f"Mandatsdatei unvollständig (Art. 9): {missing}")
    if doc.get("data_labels") != ["public"]:
        raise SystemExit("Nur Datenklasse public ist für externe Modelle freigegeben; andere Klassen brauchen eine eigene Datenfreigabe")
    return spec


def step_draft(api: Api, me: dict, a, spec: dict) -> int:
    doc = dict(spec["document"]) | {"schema_version": "1.0", "responsible_user_id": me["user_id"]}
    existing = [m for m in api.get("/mandates") if m["document"].get("title") == doc["title"]]
    if existing:
        m = existing[-1]
        print("Mandat vorhanden:", m["mandate_version_id"], m["state"], "->", f"{a.base}/campus/auftrag/{m['mandate_version_id']}")
        return 0
    m = api.cmd("/mandates", {"document": doc, "budget": str(spec["budget"]), "currency": spec.get("currency", "CHF")})
    print("Mandatsentwurf angelegt:", m["mandate_version_id"], f"Budget {spec['budget']} {spec.get('currency', 'CHF')}")
    print("Entscheid der Forschungsleitung im Browser:", f"{a.base}/campus/auftrag/{m['mandate_version_id']}")
    return 0


def step_show(api: Api, me: dict, a, spec: dict) -> int:
    institutes = api.get("/institutes")
    hint = spec.get("institute", "")
    inst = next((i for i in institutes if hint and hint.lower() in (i["institute_id"] + " " + i["name"]).lower()), None)
    print("Institut:", f"{inst['name']} ({inst['institute_id']})" if inst else f"kein Treffer für {hint!r}; vorhanden: {[i['name'] for i in institutes]}")
    print("Profil (Kandidat, sandbox_experiment): source-analyst-public@1")
    print("Start-Quellen für das Feld «Öffentliche Quellen» (eine URL je Zeile):")
    for s in spec.get("start_sources", []):
        print(" ", s["url"])
    print("Hinweise je Quelle:")
    for s in spec.get("start_sources", []):
        print(f"  {s['id']}: {s.get('note', '')}")
    print("Etappe A = erste Aufgabe (Ausgangslage, Quellenkarte, Untersuchungsfragen); B und C als weitere Aufgaben desselben Projekts.")
    return 0


def step_task(api: Api, me: dict, a, spec: dict) -> int:
    """A further task in the order's project, with its own budget (budget accounts only grow forward: an existing task keeps its limit).
    The order page then offers the flow for this newest task."""
    title = spec["document"]["title"]
    mandates = [m for m in api.get("/mandates") if m["document"].get("title") == title and m["state"] == "approved"]
    if not mandates:
        print("Kein freigegebenes Mandat mit diesem Titel; der Entscheid gehört der Forschungsleitung (Art. 11).")
        return 2
    mv = mandates[-1]["mandate_version_id"]
    projects = [p for p in api.get("/projects") if p["mandate_version_id"] == mv]
    if not projects:
        print("Noch kein Projekt unter dem Mandat; im Browser «Projekt und Aufgabe anlegen».")
        return 2
    project = projects[-1]
    policy = next((p["revision"] for p in api.get("/policies") if p["state"] == "active"), None)
    profile = next((ag for ag in api.get("/inventory")["agents"] if ag["ref"] == a.profile), None)
    if profile is None or policy is None:
        print("Profil oder aktive Policy fehlt:", a.profile, policy)
        return 2
    need = task_need_for(profile, policy)
    task = api.cmd(f"/projects/{project['project_id']}/tasks", {"task_need": need, "budget": a.task_budget})
    print("Aufgabe angelegt:", task["task_id"], f"Budget {a.task_budget} CHF, Profil {a.profile}, Modus {need['qualification_mode']}, Policy {policy}")
    print("Im Browser den Ablauf für diese Aufgabe starten:", f"{a.base}/campus/auftrag/{mv}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--username", required=True)
    ap.add_argument("--password-file", required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument("--file", required=True)
    ap.add_argument("--task-budget", default="3.00", help="Budget der neuen Aufgabe (Schritt task)")
    ap.add_argument("--profile", default="source-analyst-public@1")
    ap.add_argument("step", choices=["draft", "show", "task"])
    a = ap.parse_args()
    spec = load(Path(a.file))
    api = Api(a.base)
    me = api.login(a.workspace, a.username, Path(a.password_file).read_text(encoding="utf-8").strip())
    print("angemeldet:", me["display_name"], me["roles"])
    return {"draft": step_draft, "show": step_show, "task": step_task}[a.step](api, me, a, spec)


if __name__ == "__main__":
    sys.exit(main())
