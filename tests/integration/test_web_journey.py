"""The research-order journey in the browser: every step is an existing command; the pages only show state and name the next step."""
from __future__ import annotations

import pytest

from campus_contracts.validation import load_example
from tests.integration.test_s0_flow import MANDATE_DOC, PASSWORD, Api, _authorize, bootstrap, govern

pytestmark = pytest.mark.integration

DECISION = {"option": "accept", "rationale": "Pilotmandat, Budget gedeckt", "alternatives_presented": ["begrenzt annehmen", "ablehnen"],
            "remaining_objections": [], "consequences": "Projekt darf starten", "conditions": []}


def test_order_journey_from_mandate_to_judged_result(client, session_factory, workspace_id):
    """Proves: the order page derives the next step from server state at every stage (decide → project/task → start → freeze → judge →
    approved); the result reader renders the frozen dossier and offers the result approval; the results overview lists the version with its
    synthetic mark and approval state. No step is taken by the pages themselves."""
    boot = bootstrap(session_factory, workspace_id)
    api = Api(client)
    api.login(workspace_id, "leitung")
    state = govern(api, boot)  # one shared cookie jar: the API login's session also serves the pages
    assert "Mandatsentwurf anlegen" in client.get("/campus/auftrag/neu").text
    # 1 mandate draft (what the form posts)
    doc = MANDATE_DOC | {"responsible_user_id": boot["director_id"], "title": "Mein erster Auftrag"}
    r = api.post("/mandates", {"document": doc, "budget": "20.00", "currency": "CHF"})
    assert r.status_code == 201, r.text
    mid = r.json()["mandate_version_id"]
    page = client.get(f"/campus/auftrag/{mid}").text
    assert "Entscheid der Forschungsleitung offen" in page and "data-decision" in page and f"/mandates/{mid}/approve" in page and "0 von 6" in page
    # 2 reserved decision (through the same endpoint the dialog uses)
    assert api.post(f"/mandates/{mid}/approve", DECISION | {"expected_version": 1}).status_code == 200
    page = client.get(f"/campus/auftrag/{mid}").text
    assert "Projekt und Aufgabe anlegen" in page and 'data-journey="project"' in page and "fixture-ai-researcher@1" in page and "1 von 6" in page
    # 3 project and task (what the form posts, with the pilot defaults)
    institutes = api.get("/institutes").json()
    project = api.post("/projects", {"mandate_version_id": mid, "institute_id": institutes[0]["institute_id"], "title": "Mein erster Auftrag", "budget": "10.00"}).json()
    from campus_api.web.steps import task_need_for

    profile = next(a for a in api.get("/inventory").json()["agents"] if a["ref"] == "fixture-ai-researcher@1")
    need = task_need_for(profile, "routing-policy-proposal-1")
    task = api.post(f"/projects/{project['project_id']}/tasks", {"task_need": need, "budget": "1.00"})
    assert task.status_code == 201, task.text
    page = client.get(f"/campus/auftrag/{mid}").text
    assert "Ablauf starten" in page and 'data-journey="workflow"' in page and "2 von 6" in page
    # 4 the run (synchronously here; the browser starts the durable workflow, which ends in the same state)
    decision_id = _authorize(api, task.json()["task_id"]).json()["routing_decision_id"]
    run = api.post("/runs", {"task_id": task.json()["task_id"], "routing_decision_id": decision_id}).json()
    page = client.get(f"/campus/auftrag/{mid}").text
    assert "Gegenprüfung offen" in page, "a finished run without review stays open, never passed"
    assert api.post(f"/runs/{run['run_attempt_id']}/review").status_code == 201
    page = client.get(f"/campus/auftrag/{mid}").text
    assert "Dossier einfrieren" in page and 'data-action="freeze-dossier"' in page and "4 von 6" in page
    dossier = api.post(f"/projects/{project['project_id']}/dossier", {}).json()
    aid = dossier["artifact_version_id"]
    page = client.get(f"/campus/auftrag/{mid}").text
    assert "Ergebnis beurteilen" in page and f"/campus/artefakte/{aid}" in page and "5 von 6" in page
    # dossier page shows the same next step
    assert "Ergebnis beurteilen" in client.get(f"/campus/dossiers/{project['project_id']}").text
    # 5 the reader: readable dossier, synthetic mark, approval card bound to the hash
    reader = client.get(f"/campus/artefakte/{aid}").text
    assert "Mein erster Auftrag" in reader and "synthetischer Funktionsnachweis" in reader and "Hypothese" in reader and "EVIDENCE_MISSING" in reader
    assert "Ergebnisfreigabe" in reader and f'"expected_sha256": "{dossier["sha256"]}"' in reader and "no_objection" in reader
    results = client.get("/campus/ergebnisse").text
    assert "dossier v1" in results and "synthetisch" in results and "offen" in results
    # 6 result approval through the endpoint the dialog uses
    r = api.post(f"/artifacts/{aid}/approvals", {"expected_sha256": dossier["sha256"], **DECISION})
    assert r.status_code == 201, r.text
    reader = client.get(f"/campus/artefakte/{aid}").text
    assert "Angenommen" in reader and "data-decision" not in reader, "a valid approval of this hash removes the decision card"
    assert "Ergebnis freigegeben" in client.get(f"/campus/auftrag/{mid}").text and "6 von 6" in client.get(f"/campus/auftrag/{mid}").text
    assert "Angenommen" in client.get("/campus/ergebnisse").text
    # the pre-existing fixture project of govern() is untouched by the journey
    assert state["project"]["project_id"] != project["project_id"]


def test_producer_cannot_open_the_order_form(client, session_factory, workspace_id):
    from campus_api import services_identity
    from campus_domain.authz import Role
    from campus_infra.audit import Actor

    bootstrap(session_factory, workspace_id)
    with session_factory() as db:
        services_identity.create_user(db, actor=Actor("system", "t", workspace_id, frozenset(Role)), workspace_id=workspace_id, username="erzeuger",
                                      display_name="Erzeuger", password=PASSWORD, roles=["producer"], correlation_id="t")
        db.commit()
    client.post("/campus/login", data={"workspace_id": workspace_id, "username": "erzeuger", "password": PASSWORD})
    page = client.get("/campus/auftrag/neu").text
    assert "FORBIDDEN" in page and 'data-journey="mandate"' not in page
    assert "Neuer Forschungsauftrag" not in client.get("/campus/dossiers").text.replace("Neuer Forschungsauftrag · Phronesis", "")


def test_unknown_artifact_is_not_found(client, session_factory, workspace_id):
    bootstrap(session_factory, workspace_id)
    client.post("/campus/login", data={"workspace_id": workspace_id, "username": "leitung", "password": PASSWORD})
    assert client.get("/campus/artefakte/A-doesnotexist").status_code == 404
    assert load_example("task_need")["task_class"] == "source_analysis"
