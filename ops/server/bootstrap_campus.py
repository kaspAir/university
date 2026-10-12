"""One-time governance bootstrap of the pilot instance through the real API on 127.0.0.1 (same path as every operator action).

Idempotent: every step checks what exists. Creates: active routing policy (pilot limits, see PILOT_LIMITS), a synthetic price, data
authorizations for the mock providers, the fixture catalog (mock adapters, purpose fixture), and ONE mandate draft that waits for the
reserved human decision in the UI. Nothing is approved here; nothing is labelled as research.

Usage on the host:  .venv/bin/python ops/server/bootstrap_campus.py --workspace <id> --username <user> --password-file <file> [--port 8060]
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
import uuid
from pathlib import Path

import httpx
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "packages" / "contracts"))
from campus_contracts.validation import load_example  # noqa: E402

# Pilot operating limits (constitution Annex F, F04): deliberately small; a change is a new policy revision, decided by the research director.
PILOT_LIMITS = {"max_route_replans_per_task": 2, "max_delegation_depth": 2, "max_concurrent_runs": 2, "max_task_cost": "5.00",
                "max_project_cost": "50.00", "currency": "CHF"}
MANDATE_DOC = {
    "schema_version": "1.0", "title": "Synthetischer Pilotauftrag: Verfahrensgarantien in automatisierten Verwaltungsverfahren (Testmaterial)",
    "question_space": "Welche Verfahrensgarantien verlangt das rechtliche Gehör in automatisierten Verwaltungsverfahren? Synthetisches Material, Mock-Modelle.",
    "knowledge_goal": "Nachweis, dass der Kontrollweg Mandat → Projekt → Aufgabe → Route → Lauf → Gegenprüfung → Dossier auf dieser Instanz durchläuft.",
    "method_frame": "Quellenkartierung mit Gegenprüfung durch ein zweites Modell; alle Modelle sind Mock-Adapter, jede Ausgabe ist synthetisch.",
    "data_labels": ["public"], "access_rules": "Nur öffentliche, synthetische Fixtures; kein Export vertraulicher Daten.",
    "risk_rules": "Keine Personendaten, keine Produktivsysteme, keine Sandbox-Ausführung auf diesem Host.",
}


class Api:
    def __init__(self, base: str):
        self.c = httpx.Client(base_url=base, timeout=60)
        self.csrf = ""

    def login(self, ws: str, user: str, pw: str) -> dict:
        r = self.c.post("/api/v1/auth/login", json={"workspace_id": ws, "username": user, "password": pw})
        r.raise_for_status()
        self.csrf = r.json()["csrf_token"]
        return r.json()

    def get(self, path: str):
        r = self.c.get("/api/v1" + path)
        r.raise_for_status()
        return r.json()

    def cmd(self, path: str, body: dict, method: str = "POST"):
        r = self.c.request(method, "/api/v1" + path, json=body, headers={"X-CSRF-Token": self.csrf, "Idempotency-Key": str(uuid.uuid4())})
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:400]}")
        return r.json()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--username", required=True)
    ap.add_argument("--password-file", required=True)
    ap.add_argument("--port", type=int, default=8060)
    a = ap.parse_args()
    api = Api(f"http://127.0.0.1:{a.port}")
    me = api.login(a.workspace, a.username, Path(a.password_file).read_text(encoding="utf-8").strip())
    print("angemeldet:", me["display_name"], me["roles"], "Leistungsebene", me["capability_level"])

    # 1) policy
    policies = api.get("/policies")
    if not any(p["state"] == "active" for p in policies):
        doc = yaml.safe_load((REPO / "config" / "routing_policy.example.yaml").read_text(encoding="utf-8"))
        doc["activation_state"] = "draft"
        doc["revision"] = "pilot-policy-1"
        doc["bootstrap_selector_deployment_ids"] = ["fixture-deployment-b"]
        doc["limits"].update(PILOT_LIMITS)
        pid = api.cmd("/policies", {"document": doc})["policy_id"]
        api.cmd(f"/policies/{pid}/activate", {})
        print("policy aktiviert:", doc["revision"], "Limits", PILOT_LIMITS)
    else:
        print("policy bereits aktiv")

    # 2) catalog with mock deployments, price and data authorizations
    catalog = api.get("/model-catalog/status")
    if not catalog["active_revision"]:
        cat = copy.deepcopy(load_example("model_catalog"))
        cat["revision"] = "pilot-mock-1"
        for dep in cat["deployments"]:
            dep["pricing_ref"] = "synthetic-price-1"
            dep["processing_profile_ref"] = f"profile-{dep['provider_id']}"
        api.cmd("/pricing", {"pricing_ref": "synthetic-price-1", "currency": "CHF", "input_per_million": "10.00", "output_per_million": "50.00",
                             "source_note": "Synthetischer Preis für Mock-Adapter; kein Anbieterpreis"})
        existing = {p["ref"] for p in api.get("/processing-profiles")}
        for provider in sorted({d["provider_id"] for d in cat["deployments"]}):
            if f"profile-{provider}" not in existing:
                api.cmd("/processing-profiles", {"ref": f"profile-{provider}", "provider_id": provider, "region": "mock",
                                                 "allowed_data_labels": ["public"], "note": "Datenfreigabe nur für öffentliche, synthetische Daten (Mock)"})
        cid = api.cmd("/model-catalog/drafts", {"document": cat}, method="PUT")["catalog_id"]
        api.cmd(f"/model-catalog/{cid}/activate", {})
        print("katalog aktiviert:", cat["revision"], "(Mock-Adapter, synthetisch)")
    else:
        print("katalog bereits aktiv:", catalog["active_revision"])

    # 3) one mandate draft for the reserved human decision in the UI
    mandates = api.get("/mandates")
    if not any(m["document"].get("title") == MANDATE_DOC["title"] for m in mandates):
        doc = MANDATE_DOC | {"responsible_user_id": me["user_id"]}
        m = api.cmd("/mandates", {"document": doc, "budget": "20.00", "currency": "CHF"})
        print("mandatsentwurf angelegt:", m.get("mandate_version_id"), "– wartet auf den Entscheid der Forschungsleitung")
    else:
        print("mandatsentwurf bereits vorhanden")
    print(json.dumps({"ready": api.get("/ready")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
