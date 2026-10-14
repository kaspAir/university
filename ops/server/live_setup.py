"""Takes real provider adapters into service on the pilot instance through the public HTTPS API, in explicit steps.

Keys never pass through this script, the browser, the repository or any log: the gateway process resolves `secret://providers/<name>`
from its own environment (`CAMPUS_SECRET_PROVIDERS_<NAME>` in ~/campus-data/secrets/campus.env, mode 0600). This script only registers
references, prices, data authorizations and catalog/policy drafts, and it changes nothing without --yes.

  status     what the instance has now (catalog revisions, policies, health, approved adapters); writes nothing
  discover   imports the configuration template as a catalog DRAFT (once) and lists the native model ids each provider reports
             through its registered template deployment (adapter, endpoint and secret reference come from the catalog)
  prepare    registers the prices (operator; CHF, converted from the official USD list prices with a stated rate and a dated source),
             the data-processing authorizations (research director; Art. 11), the runtime catalog DRAFT with the two chosen
             deployments and the new policy DRAFT (selector pool = the two deployments, limits = the confirmed budget cap).
             Nothing is activated; the probes run against the draft: campus-admin probe-invocation --deployment-id <id> --max-cost …
  activate   activates the catalog draft (operator) and the policy draft (research director) once the operator confirms the probes
             with --probed <deployment id> for every enabled deployment. No qualification evidence is created here.
  policy     a new policy revision copied from the active one with changed cost limits (research director); nothing else changes

Usage on the host:
  .venv/bin/python ops/server/live_setup.py --workspace ws-campus --username <user> --password-file <file> --base https://<host> <step> …
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from decimal import ROUND_UP, Decimal
from pathlib import Path

import yaml

REPO = Path(os.environ["CAMPUS_HOME"]) if os.environ.get("CAMPUS_HOME") else Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "packages" / "contracts"))
sys.path.insert(0, str(REPO / "ops" / "server"))
from bootstrap_campus import PILOT_LIMITS, Api  # noqa: E402

from campus_contracts.validation import validate_document  # noqa: E402

PROVIDERS = {
    "anthropic": {"template_deployment": "anthropic-configured-model", "api_base": "https://api.anthropic.com",
                  "secret_ref": "secret://providers/anthropic", "env": "CAMPUS_SECRET_PROVIDERS_ANTHROPIC",
                  "version_mode": "fixed"},  # dateless Claude ids are pinned snapshots from the 4.6 generation on (models overview)
    "openai": {"template_deployment": "openai-configured-model", "api_base": "https://api.openai.com",
               "secret_ref": "secret://providers/openai", "env": "CAMPUS_SECRET_PROVIDERS_OPENAI",
               "version_mode": "unknown"},  # whether a bare id is a moving alias is not asserted; routing does not depend on it
}
TOOL_SCOPES = ["sources.read", "claims.propose"]
OPENAI_TEXT_PREFIXES = ("gpt-", "o1", "o3", "o4")
OPENAI_EXCLUDE = ("realtime", "audio", "tts", "transcribe", "image", "embedding", "moderation", "search", "codex", "instruct", "chat-latest")


def try_cmd(api: Api, path: str, body: dict, method: str = "POST") -> tuple[int, dict]:
    import uuid

    r = api.c.request(method, "/api/v1" + path, json=body, headers={"X-CSRF-Token": api.csrf, "Idempotency-Key": str(uuid.uuid4())})
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {"text": r.text[:300]}


def to_chf(usd: str, rate: Decimal) -> str:
    """USD list price per million tokens -> CHF, rounded UP to the cent: the campus never underestimates a cost (A14)."""
    return str((Decimal(usd) * rate).quantize(Decimal("0.01"), rounding=ROUND_UP))


# ---------------------------------------------------------------- steps

def status(api: Api, a) -> int:
    print(json.dumps({"health": api.get("/health"), "ready": api.get("/ready"), "catalog": api.get("/model-catalog/status"),
                      "policies": [{k: p[k] for k in ("revision", "state")} for p in api.get("/policies")],
                      "processing_profiles": [{k: p[k] for k in ("ref", "provider_id", "region", "allowed_data_labels")} for p in api.get("/processing-profiles")]},
                     indent=1, ensure_ascii=False, default=str))
    return 0


def discover(api: Api, a) -> int:
    revisions = {r["revision"]: r for r in api.get("/model-catalog/status")["revisions"]}
    template = yaml.safe_load((REPO / "config" / "model_catalog.example.yaml").read_text(encoding="utf-8"))
    if template["revision"] not in revisions:
        res = api.cmd("/model-catalog/drafts", {"document": template}, method="PUT")
        print("Konfigurationsvorlage als Katalog-Entwurf registriert:", res["revision"], res["state"], "(alle Deployments deaktiviert, keine Preise)")
    else:
        print("Konfigurationsvorlage bereits registriert:", template["revision"], revisions[template["revision"]]["state"])
    rc = 0
    for name, p in PROVIDERS.items():
        print(f"\n== {name}: Discovery über {p['template_deployment']} ({p['api_base']}, Schlüssel {p['secret_ref']} -> {p['env']} im API-Prozess)")
        r = api.c.get("/api/v1/model-catalog/discovery", params={"deployment_id": p["template_deployment"]})
        if r.status_code >= 400:
            body = r.json()
            print(f"   FEHLGESCHLAGEN {r.status_code} {body.get('code')}: {body.get('message')}")
            print("   -> Prüfen: Adapter in CAMPUS_APPROVED_ADAPTERS? Schlüssel in campus.env? API nach dem Eintrag neu gestartet (campusctl.sh restart api worker)?")
            rc = 2
            continue
        ids = r.json()["model_ids"]
        if name == "openai":
            shown = sorted(i for i in ids if i.startswith(OPENAI_TEXT_PREFIXES) and not any(x in i for x in OPENAI_EXCLUDE))
            print(f"   {len(ids)} Kennungen gemeldet; Textmodelle (gefiltert, {len(shown)}):")
        else:
            shown = sorted(ids)
            print(f"   {len(ids)} Kennungen gemeldet:")
        for i in shown:
            print("   -", i)
    print("\nDiscovery ist weder Datenfreigabe noch Qualifikation (Kap. 8). Nächster Schritt: prepare mit den gewählten Kennungen und bestätigten Preisen.")
    return rc


def prepare(api: Api, a) -> int:
    rate = Decimal(a.usd_chf)
    day = date.today().strftime("%Y%m%d")
    chosen = {"anthropic": (a.anthropic_model, a.anthropic_usd, a.anthropic_tokens), "openai": (a.openai_model, a.openai_usd, a.openai_tokens)}
    deployments, prices, profiles = [], {}, {}
    for name, (model, usd, tokens) in chosen.items():
        p = PROVIDERS[name]
        pricing_ref = f"{name}-{model}-{day}"
        profile_ref = f"profile-{name}-live"
        prices[pricing_ref] = {"pricing_ref": pricing_ref, "currency": "CHF", "input_per_million": to_chf(usd[0], rate), "output_per_million": to_chf(usd[1], rate),
                               "source_note": f"{a.price_source}; Listenpreis USD {usd[0]}/{usd[1]} je Mio. Token; Umrechnung 1 USD = {rate} CHF "
                                              f"(bewusst konservativ, auf den Rappen aufgerundet); Fassung vom {date.today().isoformat()}"}
        profiles[profile_ref] = {"ref": profile_ref, "provider_id": name, "region": a.region, "allowed_data_labels": ["public"], "note": a.profile_note}
        deployments.append({
            "id": f"{name}-{model}", "provider_id": name, "adapter": name, "enabled": True, "provider_model_id": model,
            "canonical_model_id": f"{name}/{model}", "model_family": model, "shared_base_id": None, "api_base": p["api_base"],
            "secret_ref": p["secret_ref"], "version_mode": p["version_mode"],
            # only what the official documentation states and the probe verifies; everything else stays unknown (= excluded if required, A08)
            "capabilities": {"text_input": "supported", "vision_input": "unknown", "tool_calling": "unknown", "structured_output": "supported",
                             "streaming": "unknown", "context_tokens": tokens[0], "output_tokens": tokens[1]},
            "allowed_data_labels": ["public"], "allowed_tool_scopes": TOOL_SCOPES, "processing_profile_ref": profile_ref,
            "pricing_ref": pricing_ref, "qualification_refs": [], "health": "available",
        })
    catalog = {"schema_version": "1.0", "revision": a.revision, "purpose": "runtime_snapshot", "deployments": deployments}
    violations = validate_document("model_catalog", catalog)
    if violations:
        print("Katalog verletzt das Schema:", violations)
        return 2
    policy = yaml.safe_load((REPO / "config" / "routing_policy.example.yaml").read_text(encoding="utf-8"))
    policy.update({"revision": a.policy_revision, "activation_state": "draft",
                   "bootstrap_selector_deployment_ids": [d["id"] for d in deployments if d["provider_id"] == a.selector_first]
                   + [d["id"] for d in deployments if d["provider_id"] != a.selector_first],
                   "note": f"Pilot mit echten Anbietern; gemeinsame Kostenobergrenze {a.budget_chf} CHF je Aufgabe und Projekt (bestätigt durch die "
                           f"Forschungsleitung); Erzeuger und Prüfer kanonisch verschieden, verschiedener Anbieter bevorzugt."})
    policy["limits"].update(PILOT_LIMITS | {"max_task_cost": a.budget_chf, "max_project_cost": a.budget_chf})

    print("== Plan (nichts davon legt Schlüssel offen)")
    for pr in prices.values():
        print(f"  Preisfassung {pr['pricing_ref']}: {pr['input_per_million']} / {pr['output_per_million']} CHF je Mio. Token (Betrieb)")
    for pf in profiles.values():
        print(f"  Datenfreigabe {pf['ref']}: Anbieter {pf['provider_id']}, Region {pf['region']}, Datenklassen {pf['allowed_data_labels']} (Forschungsleitung, Art. 11)")
    for d in deployments:
        print(f"  Deployment {d['id']}: {d['canonical_model_id']} über {d['api_base']}, Kontext {d['capabilities']['context_tokens']}, Ausgabe {d['capabilities']['output_tokens']}")
    print(f"  Katalog-ENTWURF {a.revision} (runtime_snapshot, zwei echte Deployments, keine Mock-Deployments, keine Nachweise behauptet)")
    print(f"  Policy-ENTWURF {a.policy_revision}: Selector-Pool {policy['bootstrap_selector_deployment_ids']}, Limits {policy['limits']}")
    if a.out:
        Path(a.out).write_text(yaml.safe_dump(catalog, sort_keys=False, allow_unicode=True), encoding="utf-8", newline="\n")
        print(f"  Katalogentwurf als Datei: {a.out}")
    if not a.yes:
        print("\nOhne --yes wurde nichts geschrieben.")
        return 0

    for pr in prices.values():
        code, body = try_cmd(api, "/pricing", pr)
        print("  Preis", pr["pricing_ref"], "registriert" if code == 201 else f"-> {code} {body.get('code')}: {body.get('message')}")
        if code not in (201, 409):
            return 2
    existing = {p["ref"] for p in api.get("/processing-profiles")}
    for pf in profiles.values():
        if pf["ref"] in existing:
            print("  Datenfreigabe", pf["ref"], "bereits vorhanden")
            continue
        code, body = try_cmd(api, "/processing-profiles", pf)
        print("  Datenfreigabe", pf["ref"], "genehmigt" if code == 201 else f"-> {code} {body.get('code')}: {body.get('message')}")
        if code != 201:
            return 2
    revisions = {r["revision"]: r for r in api.get("/model-catalog/status")["revisions"]}
    if a.revision in revisions:
        print("  Katalog", a.revision, "bereits registriert:", revisions[a.revision]["state"])
    else:
        code, body = try_cmd(api, "/model-catalog/drafts", {"document": catalog}, method="PUT")
        print("  Katalog-Entwurf", a.revision, body.get("catalog_id") if code == 201 else f"-> {code} {body.get('code')}: {body.get('message')} {body.get('details')}")
        if code != 201:
            return 2
    if any(p["revision"] == a.policy_revision for p in api.get("/policies")):
        print("  Policy", a.policy_revision, "bereits registriert")
    else:
        code, body = try_cmd(api, "/policies", {"document": policy})
        print("  Policy-Entwurf", a.policy_revision, body.get("policy_id") if code == 201 else f"-> {code} {body.get('code')}: {body.get('message')}")
        if code != 201:
            return 2
    print("\nJetzt die Proben gegen den Entwurf (kosten Geld, Obergrenze je Probe):")
    for d in deployments:
        print(f"  .venv/bin/python -m campus_api.cli probe-invocation --workspace {a.workspace} --deployment-id {d['id']} --max-cost 0.05")
    print("Danach: live_setup.py … activate --probed", " --probed ".join(d["id"] for d in deployments))
    return 0


def policy(api: Api, a) -> int:
    """A new policy revision with changed limits, copied from the active one: a director's decision, nothing else changes."""
    active = next((p for p in api.get("/policies") if p["state"] == "active"), None)
    if active is None:
        print("Keine aktive Policy")
        return 2
    doc = dict(active["document"])
    doc["revision"] = a.policy_revision
    doc["activation_state"] = "draft"
    doc["limits"] = dict(doc["limits"]) | {"max_task_cost": a.task_chf, "max_project_cost": a.project_chf}
    doc["note"] = a.note
    print(f"== Plan: Policy {a.policy_revision} aus {active['revision']} mit Limits max_task_cost {a.task_chf}, max_project_cost {a.project_chf} "
          f"(Forschungsleitung); Selector-Pool und harte Regeln unverändert. Laufende Aufgaben bleiben an ihre Policy gebunden.")
    if not a.yes:
        print("Ohne --yes wurde nichts geschrieben.")
        return 0
    if any(p["revision"] == a.policy_revision for p in api.get("/policies")):
        print("Policy", a.policy_revision, "bereits registriert")
    else:
        code, body = try_cmd(api, "/policies", {"document": doc})
        print("  Policy-Entwurf", a.policy_revision, body.get("policy_id") if code == 201 else f"-> {code} {body.get('code')}: {body.get('message')}")
        if code != 201:
            return 2
    pol = next(p for p in api.get("/policies") if p["revision"] == a.policy_revision)
    if pol["state"] == "draft":
        code, body = try_cmd(api, f"/policies/{pol['policy_id']}/activate", {})
        print("  Policy aktiviert" if code == 200 else f"  Policy NICHT aktiviert -> {code} {body.get('code')}: {body.get('message')}")
        return 0 if code == 200 else 2
    print("  Policy ist", pol["state"])
    return 0


def activate(api: Api, a) -> int:
    revisions = {r["revision"]: r for r in api.get("/model-catalog/status")["revisions"]}
    rev = revisions.get(a.revision)
    if rev is None:
        print("Katalog", a.revision, "ist nicht registriert; zuerst prepare")
        return 2
    policies = {p["revision"]: p for p in api.get("/policies")}
    pol = policies.get(a.policy_revision)
    if pol is None:
        print("Policy", a.policy_revision, "ist nicht registriert; zuerst prepare")
        return 2
    probed = set(a.probed or [])
    if not probed:
        print("Aktivierung verlangt --probed <deployment id> für jedes aktivierte Deployment: Bestätigung, dass die Probe mit state completed, "
              "bekannten Nutzungswerten und gültiger strukturierter Ausgabe im Aufrufjournal steht (Betrieb & Kosten).")
        return 2
    print(f"== Plan: Katalog {a.revision} ({rev['state']}) aktivieren (Betrieb) und Policy {a.policy_revision} ({pol['state']}) aktivieren (Forschungsleitung); "
          f"der bisher aktive Katalog und die bisher aktive Policy werden zurückgezogen. Bestätigte Proben: {sorted(probed)}")
    if not a.yes:
        print("Ohne --yes wurde nichts geschrieben.")
        return 0
    if rev["state"] == "draft":
        code, body = try_cmd(api, f"/model-catalog/{rev['catalog_id']}/activate", {})
        print("  Katalog aktiviert" if code == 200 else f"  Katalog NICHT aktiviert -> {code} {body.get('code')}: {body.get('message')} {json.dumps(body.get('details'), ensure_ascii=False)}")
        if code != 200:
            return 2
    else:
        print("  Katalog ist", rev["state"])
    if pol["state"] == "draft":
        code, body = try_cmd(api, f"/policies/{pol['policy_id']}/activate", {})
        print("  Policy aktiviert" if code == 200 else f"  Policy NICHT aktiviert -> {code} {body.get('code')}: {body.get('message')}")
        if code != 200:
            return 2
    else:
        print("  Policy ist", pol["state"])
    print(json.dumps({"ready": api.get("/ready"), "limitations": api.get("/health")["pilot_limitations"]}, indent=1, ensure_ascii=False))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--username", required=True)
    ap.add_argument("--password-file", required=True)
    ap.add_argument("--base", required=True, help="https://<host> (Secure-Cookie)")
    sub = ap.add_subparsers(dest="step", required=True)
    sub.add_parser("status")
    sub.add_parser("discover")
    p = sub.add_parser("prepare")
    p.add_argument("--anthropic-model", required=True)
    p.add_argument("--openai-model", required=True)
    p.add_argument("--anthropic-usd", nargs=2, metavar=("IN", "OUT"), required=True, help="Listenpreis USD je Mio. Token (Eingabe, Ausgabe)")
    p.add_argument("--openai-usd", nargs=2, metavar=("IN", "OUT"), required=True)
    p.add_argument("--anthropic-tokens", nargs=2, type=int, metavar=("CONTEXT", "OUTPUT"), required=True)
    p.add_argument("--openai-tokens", nargs=2, type=int, metavar=("CONTEXT", "OUTPUT"), required=True)
    p.add_argument("--usd-chf", default="1.00", help="Umrechnungskurs; Standard 1.00 = bewusst konservativ")
    p.add_argument("--price-source", required=True, help="Datierte Quellenangabe der Listenpreise (URL und Datum)")
    p.add_argument("--budget-chf", default="5.00", help="Gemeinsame Kostenobergrenze je Aufgabe und Projekt in der Policy")
    p.add_argument("--region", default="USA (Anbieter-Cloud)")
    p.add_argument("--profile-note", default="Datenfreigabe nur für die Datenklasse public: öffentliche Quellen und daraus abgeleitete Aussagen; "
                                            "keine Personendaten, keine internen Dokumente. Verarbeitung beim Anbieter unter dessen API-Bedingungen.")
    p.add_argument("--revision", default="pilot-live-1")
    p.add_argument("--policy-revision", default="pilot-policy-2")
    p.add_argument("--selector-first", default="anthropic", choices=sorted(PROVIDERS))
    p.add_argument("--out", default=None, help="Katalogentwurf zusätzlich als YAML speichern (keine Geheimnisse, nur Referenzen)")
    p.add_argument("--yes", action="store_true")
    p = sub.add_parser("policy")
    p.add_argument("--policy-revision", required=True)
    p.add_argument("--task-chf", required=True)
    p.add_argument("--project-chf", required=True)
    p.add_argument("--note", default="Limits fuer die ersten realen Vorhaben; bestaetigt durch die Forschungsleitung.")
    p.add_argument("--yes", action="store_true")
    p = sub.add_parser("activate")
    p.add_argument("--revision", default="pilot-live-1")
    p.add_argument("--policy-revision", default="pilot-policy-2")
    p.add_argument("--probed", action="append", help="Deployment, dessen Probe bestanden ist (mehrfach)")
    p.add_argument("--yes", action="store_true")
    a = ap.parse_args()
    api = Api(a.base)
    me = api.login(a.workspace, a.username, Path(a.password_file).read_text(encoding="utf-8").strip())
    print("angemeldet:", me["display_name"], me["roles"], "Leistungsebene", me["capability_level"])
    return {"status": status, "discover": discover, "prepare": prepare, "policy": policy, "activate": activate}[a.step](api, a)


if __name__ == "__main__":
    sys.exit(main())
