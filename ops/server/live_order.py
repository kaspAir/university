"""TEST-02: the first LIVE research order, kept apart from the synthetic TEST-01 (which stays as it is).

Two steps, each honest about who decides:
  mandate    creates the TEST-02 mandate DRAFT with a small budget; the research director decides in the browser (Art. 11). The
             question names the public source that is already stored in this workspace; the run fetches it again for the new
             project through the internet service (SSRF guard) and stores it version-exact.
  register   records the need (Art. 26) and registers a real candidate skill and candidate profile for public source analysis.
             Candidate means: not qualified, no evidence claimed; the task runs as sandbox_experiment and the dossier says so.

Project, task, run and judgement happen in the browser (/campus/auftrag/<mandate version>): choose the profile
`source-analyst-public@1`, start the flow with the stored source URL, watch, read the dossier, decide.

Usage: .venv/bin/python ops/server/live_order.py --workspace ws-campus --username <user> --password-file <file> --base https://<host> <step>
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

REPO = Path(os.environ["CAMPUS_HOME"]) if os.environ.get("CAMPUS_HOME") else Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "packages" / "contracts"))
sys.path.insert(0, str(REPO / "ops" / "server"))
from bootstrap_campus import Api  # noqa: E402

from campus_contracts.validation import validate_document  # noqa: E402

TITLE = "TEST-02: Auswertung einer gespeicherten öffentlichen Quelle mit echten Modellen (Live-Auftrag)"
NEED_REF = "live-need-public-source-analysis-1"
SKILL_ID, SKILL_VERSION = "public-source-analysis", "1"
PROFILE_ID, PROFILE_VERSION = "source-analyst-public", "1"
SPECIALIZATION = "Analyse öffentlicher Quellen (Belegbindung, Aussagenklassifikation)"

INSTRUCTIONS = """# Analyse einer öffentlichen Quelle (Kandidat, Fassung 1)

Du erhältst Passagen einer öffentlichen Quelle, jede mit einer passage_id. Arbeite ausschliesslich mit diesen Passagen.

1. Formuliere drei bis acht Aussagen. Jede Aussage trägt genau eine Art: observation (steht wörtlich oder sinngemäss in einer
   Passage), derivation (folgt aus mehreren Passagen; nenne alle), hypothesis (Vermutung, in den Passagen nicht belegt).
2. Jede observation und jede derivation nennt die passage_ids, auf die sie sich stützt. Erfinde keine passage_ids und keine Zahlen.
3. Nenne je Aussage die Geltungsbedingungen (Kontext, Stichprobe, Zeitraum, Methode), soweit die Quelle sie nennt; sonst «nicht angegeben».
4. Trenne Befunde der Quelle von Deutungen der Autorinnen und Autoren; eine Deutung ist höchstens eine derivation.
5. Zahlen nur, wenn sie in einer Passage stehen, in der dort genannten Einheit; keine Rundung ohne Hinweis.
6. Antworte ausschliesslich im vorgegebenen JSON-Format.
"""


def mandate_doc(source_url: str, responsible_user_id: str) -> dict:
    return {
        "schema_version": "1.0", "title": TITLE,
        "question_space": f"Welche Befunde berichtet die öffentliche Quelle {source_url} zur Wirkung von KI-Unterstützung auf Wissensarbeit, "
                          "welche Aussagen sind durch den Quelltext belegt (Beobachtung), welche sind Ableitungen, und welche bleiben Hypothesen?",
        "knowledge_goal": "Erste echte, belegbezogene Aussagenmenge aus einer gespeicherten öffentlichen Quelle, gegengeprüft durch ein kanonisch "
                          "anderes Modell eines anderen Anbieters (Art. 27); zugleich Nachweis des Kontrollwegs mit echten Aufrufen "
                          "(Journal, Verbrauch, Kosten).",
        "method_frame": "Quellenanalyse (source_analysis): Abruf der Quelle über den Internetdienst, Aussagen mit passage_id-Belegen durch ein "
                        "Modell, unabhängige Erstprüfung durch ein zweites Modell, Dossier mit Hash. Kandidatenprofil im Modus "
                        "sandbox_experiment (Art. 26); keine Qualifikation wird behauptet.",
        "data_labels": ["public"],
        "access_rules": "Nur die genannte öffentliche Quelle und weitere öffentliche URLs über den Internetdienst; kein Export vertraulicher Daten.",
        "risk_rules": "Keine Personendaten, keine Studien mit Menschen, keine Sandbox-Ausführung; Kosten begrenzt durch Mandatsbudget und Policy-Limits.",
        "review_requirements": "Jedes tragende Artefakt wird durch ein kanonisch anderes Modell eines anderen Anbieters gegengeprüft (Art. 27); "
                               "eine fehlende Prüfung gilt als offen, nie als bestanden.",
        "duration_days": 14,
        "report_points": ["Tag 1: erster Lauf mit Gegenprüfung", "Tag 14: Dossier beurteilt"],
        "responsible_user_id": responsible_user_id,
    }


def step_mandate(api: Api, me: dict, a) -> int:
    existing = [m for m in api.get("/mandates") if m["document"].get("title") == TITLE]
    if existing:
        m = existing[-1]
        print("Mandat vorhanden:", m["mandate_version_id"], m["state"], "->", f"{a.base}/campus/auftrag/{m['mandate_version_id']}")
        return 0
    m = api.cmd("/mandates", {"document": mandate_doc(a.source_url, me["user_id"]), "budget": a.budget_chf, "currency": "CHF"})
    print("Mandatsentwurf angelegt:", m["mandate_version_id"], f"Budget {a.budget_chf} CHF")
    print("Entscheid der Forschungsleitung im Browser:", f"{a.base}/campus/auftrag/{m['mandate_version_id']}")
    return 0


def step_register(api: Api, me: dict, a) -> int:
    institutes = api.get("/institutes")
    inst = next((i for i in institutes if a.institute.lower() in (i["institute_id"] + " " + i["name"]).lower()), None)
    if inst is None:
        print("Kein Institut passt zu", repr(a.institute), "- vorhanden:", [(i["institute_id"], i["name"]) for i in institutes])
        return 2
    if not any(n["ref"] == NEED_REF for n in api.get("/needs")):
        n = api.cmd("/needs", {"ref": NEED_REF, "gap": "Kein registriertes Profil für die Analyse öffentlicher Quellen mit echten Modellen; das "
                                                     "Fixture-Profil ist eine Vertragsillustration ohne Fachanweisung",
                               "requested_specialization": SPECIALIZATION, "task_class": "source_analysis",
                               "alternatives_considered": ["fixture-ai-researcher@1 (Vertragsfixture, keine Fachbeschreibung)"],
                               "expected_benefit": "Belegbezogene Aussagen mit passage_id-Bindung aus echten Modellaufrufen, getrennt nach "
                                                   "Beobachtung, Ableitung und Hypothese"})
        print("Bedarf erfasst:", n["ref"], n["decision"])
    else:
        print("Bedarf vorhanden:", NEED_REF)
    inventory = api.get("/inventory")
    skill_ref = f"{SKILL_ID}@{SKILL_VERSION}"
    if not any(s["ref"] == skill_ref for s in inventory["skills"]):
        manifest = {
            "schema_version": "1.0", "id": SKILL_ID, "version": SKILL_VERSION,
            "purpose": "Analysiert übergebene Passagen einer öffentlichen Quelle und formuliert belegte Aussagen mit passage_id-Bindung.",
            "trigger": "Eine Aufgabe der Klasse source_analysis mit Datenklasse public wird zugewiesen.",
            "instructions_path": "instructions.md", "input_schema_ref": "source-passages@1", "output_schema_ref": "claim_set@1",
            "requested_tool_scopes": ["sources.read"], "dependency_version_refs": [], "allowed_data_labels": ["public"],
            "limitations": ["Nur übergebene Passagen; keine eigene Recherche", "Keine Rechts- oder Fachurteile ausserhalb des Quelltexts",
                            "Kandidat ohne Prüfsatz: keine Qualifikation behauptet"],
            "origin_need_ref": NEED_REF, "evaluation_suite_ref": "public-source-analysis-suite@0-not-yet-created",
            "qualification_refs": [], "qualified_combination_refs": [], "operating_status": "candidate",
            "package_sha256": hashlib.sha256(INSTRUCTIONS.encode("utf-8")).hexdigest(),
        }
        bad = validate_document("skill_manifest", manifest)
        if bad:
            print("Skill-Manifest verletzt das Schema:", bad)
            return 2
        s = api.cmd("/skills/candidates", {"document": manifest, "instructions": INSTRUCTIONS})
        print("Skill-Kandidat registriert:", s["ref"], s["operating_status"], "(hashgebundene Anweisungen)")
    else:
        print("Skill vorhanden:", skill_ref)
    profile_ref = f"{PROFILE_ID}@{PROFILE_VERSION}"
    if not any(ag["ref"] == profile_ref for ag in inventory["agents"]):
        profile = {
            "schema_version": "1.0", "id": PROFILE_ID, "version": PROFILE_VERSION, "institute_id": inst["institute_id"],
            "primary_specialization": SPECIALIZATION, "jurisdictions": [], "languages": ["de-CH", "en-GB"],
            "scope": "Analyse öffentlicher Quellen: belegte Aussagen mit Passagenbindung, getrennt nach Beobachtung, Ableitung und Hypothese.",
            "out_of_scope": ["Rechtsurteile", "Studien mit Menschen", "externe Veröffentlichung", "Aussagen ohne übergebene Passage"],
            "need_record_ref": NEED_REF,
            "creation_reason": "Erstes Kandidatenprofil für echte Modellaufrufe im Live-Auftrag TEST-02; ersetzt die Vertragsfixture.",
            "alternatives_considered": ["fixture-ai-researcher@1 (Vertragsfixture, keine Fachbeschreibung)"],
            "skill_version_refs": [skill_ref], "model_policy_ref": a.policy_revision,
            "requested_tool_scopes": ["sources.read", "claims.propose"], "data_labels": ["public"], "qualification_refs": [],
            "operating_status": "planned", "qualification_status": "not_evaluated",
        }
        bad = validate_document("agent_profile", profile)
        if bad:
            print("Agentenprofil verletzt das Schema:", bad)
            return 2
        p = api.cmd("/agents/candidates", {"document": profile})
        print("Profil-Kandidat registriert:", p["ref"], p["operating_status"], "/", p["qualification_status"], "(nicht qualifiziert -> sandbox_experiment)")
    else:
        print("Profil vorhanden:", profile_ref)
    mandates = [m for m in api.get("/mandates") if m["document"].get("title") == TITLE]
    mid = mandates[-1]["mandate_version_id"] if mandates else "<Mandatsfassung>"
    print("\nIm Browser:", f"{a.base}/campus/auftrag/{mid}")
    print(f"  1. Projekt und Aufgabe anlegen: Institut {inst['name']}, Teilbudget z. B. 2.00 CHF, Profil {profile_ref} (Modus sandbox_experiment)")
    print(f"  2. Ablauf starten mit der Quelle {a.source_url} (eine URL je Zeile); Route, Erzeugung, Gegenprüfung und Dossier laufen im Ablaufdienst")
    print("  3. Dossier lesen (Aussagen mit passage_id-Belegen, Prüfbericht des anderen Modells) und die Ergebnisfreigabe entscheiden")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--username", required=True)
    ap.add_argument("--password-file", required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument("--source-url", default="https://aiinstitute.hbs.edu/navigating-the-jagged-technological-frontier/",
                    help="Die bereits gespeicherte öffentliche Quelle (Bibliothek)")
    ap.add_argument("--budget-chf", default="3.00")
    ap.add_argument("--institute", default="angewandt", help="Teil der Instituts-ID oder des Namens")
    ap.add_argument("--policy-revision", default="pilot-policy-2")
    ap.add_argument("step", choices=["mandate", "register"])
    a = ap.parse_args()
    api = Api(a.base)
    me = api.login(a.workspace, a.username, Path(a.password_file).read_text(encoding="utf-8").strip())
    print("angemeldet:", me["display_name"], me["roles"])
    return {"mandate": step_mandate, "register": step_register}[a.step](api, me, a)


if __name__ == "__main__":
    sys.exit(main())
