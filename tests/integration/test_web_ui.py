"""First surface: access protection, the pages render real instance data, synthetic content is marked, no fixture data is invented."""
from __future__ import annotations

import pytest

from tests.integration.test_s0_flow import MANDATE_DOC, PASSWORD, Api, _authorize, _create_task, bootstrap, govern

pytestmark = pytest.mark.integration


def test_pages_require_a_session_and_login_form_works(client, session_factory, workspace_id):
    bootstrap(session_factory, workspace_id)
    for path in ("/campus", "/campus/dossiers", "/campus/register", "/campus/betrieb"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/campus/login", path
    r = client.get("/campus/login")
    assert r.status_code == 200 and "Anmelden" in r.text
    bad = client.post("/campus/login", data={"workspace_id": workspace_id, "username": "leitung", "password": "falsch"})
    assert bad.status_code == 401 and "UNAUTHENTICATED" in bad.text
    ok = client.post("/campus/login", data={"workspace_id": workspace_id, "username": "leitung", "password": PASSWORD}, follow_redirects=False)
    assert ok.status_code == 303 and ok.headers["location"] == "/campus" and "campus_session" in ok.headers.get("set-cookie", "")
    page = client.get("/campus")
    assert page.status_code == 200 and "development_mock" in page.text and "Mockbetrieb" in page.text
    assert client.get("/campus/static/tokens.css").status_code == 200 and "googleapis" not in client.get("/campus/static/tokens.css").text
    # logout needs the CSRF token of the session
    assert client.post("/campus/logout", data={"csrf_token": "falsch"}).status_code == 403
    csrf = client.get("/api/v1/auth/me").json()["csrf_token"]
    out = client.post("/campus/logout", data={"csrf_token": csrf}, follow_redirects=False)
    assert out.status_code == 303 and client.get("/campus", follow_redirects=False).status_code == 303


def test_empty_instance_shows_institutes_and_no_invented_data(client, session_factory, workspace_id):
    """An instance with only workspace, institutes and the user shows exactly that: five institutes, empty states, no fixtures."""
    bootstrap(session_factory, workspace_id)
    client.post("/campus/login", data={"workspace_id": workspace_id, "username": "leitung", "password": PASSWORD})
    page = client.get("/campus").text
    assert "Keine Entscheide offen" in page and page.count('class="inst"') == 5 and "Metawissenschaft" in page
    assert "Kein Mandat freigegeben" in page and "Kein aktiver Modellkatalog" in page
    assert "Noch keine Dossiers" in client.get("/campus/dossiers").text
    assert "Noch keine Agentenprofile" in client.get("/campus/register").text
    assert "Kein aktiver Katalog" in client.get("/campus/modelle").text
    assert "Keine offenen Mandatsentwürfe" in client.get("/campus/pruefung").text


def test_campus_pages_render_real_data_and_mark_synthetic_runs(client, session_factory, workspace_id):
    boot = bootstrap(session_factory, workspace_id)
    api = Api(client)
    api.login(workspace_id, "leitung")
    state = govern(api, boot)
    project_id = state["project"]["project_id"]
    task = _create_task(api, project_id)
    decision_id = _authorize(api, task["task_id"]).json()["routing_decision_id"]
    run = api.post("/runs", {"task_id": task["task_id"], "routing_decision_id": decision_id}).json()
    assert api.post(f"/runs/{run['run_attempt_id']}/review").status_code == 201
    # a second mandate draft waits for a decision
    draft = api.post("/mandates", {"document": MANDATE_DOC | {"responsible_user_id": boot["director_id"], "title": "Zweites Mandat"}, "budget": "100.00", "currency": "CHF"})
    assert draft.status_code == 201, draft.text
    campus = client.get("/campus").text
    assert "Ein Entscheid wartet" in campus and "Zweites Mandat" in campus and "Entscheiden" in campus
    assert "fixture/model-a@1" in campus and "synthetisch" in campus, "mock deployments are marked"
    dossiers = client.get("/campus/dossiers").text
    assert state["project"]["title"] in dossiers and "synthetisch" in dossiers
    filtered = client.get(f"/campus/dossiers?institut={state['project']['institute_id']}").text
    assert state["project"]["title"] in filtered and "Filter:" in filtered
    for tab, needle in (("frage", "Modellroute"), ("aussagen", "Hypothese"), ("laeufe", run["run_attempt_id"]), ("fassungen", "Fassung"), ("quellen", "Quellen")):
        page = client.get(f"/campus/dossiers/{project_id}?tab={tab}")
        assert page.status_code == 200 and needle in page.text, (tab, needle)
    laeufe = client.get(f"/campus/dossiers/{project_id}?tab=laeufe").text
    assert "fixture/model-b@1" in laeufe and "synthetisch" in laeufe, "producer and reviewer visible, synthetic marked"
    aussagen = client.get(f"/campus/dossiers/{project_id}?tab=aussagen").text
    assert "EVIDENCE_MISSING" in aussagen, "T04 flag stays visible in the UI"
    register = client.get("/campus/register").text
    assert "fixture-ai-researcher@1" in register and "fixture-source-analysis@1" in register
    assert client.get("/campus/register/fixture-ai-researcher@1").status_code == 200
    pruefung = client.get("/campus/pruefung").text
    assert "Zweites Mandat" in pruefung and "Angenommen" in pruefung, "the approved first mandate appears as a recorded decision"
    betrieb = client.get("/campus/betrieb").text
    assert "database" in betrieb and "Sandboxpfad" in betrieb and "mock" in betrieb
    assert "fixture/model-a@1" in client.get("/campus/modelle").text
    assert client.get("/campus/bibliothek").status_code == 200
    # stop is visible in every page; it is executed through the JSON API (the page only renders the state)
    api.post("/workspace/stop", {"reason": "Test"})
    assert "Alle Läufe angehalten" in client.get("/campus").text


def test_producer_sees_no_decision_button(client, session_factory, workspace_id):
    from campus_api import services_identity
    from campus_domain.authz import Role
    from campus_infra.audit import Actor

    boot = bootstrap(session_factory, workspace_id)
    api = Api(client)
    api.login(workspace_id, "leitung")
    govern(api, boot)
    api.post("/mandates", {"document": MANDATE_DOC | {"responsible_user_id": boot["director_id"], "title": "Entwurf"}, "budget": "10.00", "currency": "CHF"})
    with session_factory() as db:
        services_identity.create_user(db, actor=Actor("system", "t", workspace_id, frozenset(Role)), workspace_id=workspace_id, username="erzeuger",
                                      display_name="Erzeuger", password="test-passwort-mit-zwoelf-zeichen", roles=["producer"], correlation_id="t")
        db.commit()
    client.post("/campus/login", data={"workspace_id": workspace_id, "username": "erzeuger", "password": "test-passwort-mit-zwoelf-zeichen"})
    page = client.get("/campus/pruefung").text
    assert "Entwurf" in page and "nur Forschungsleitung" in page and "data-decide" not in page
    assert "data-stop" not in client.get("/campus").text
