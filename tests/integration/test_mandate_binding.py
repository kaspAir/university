"""The research order reaches the models: question space, knowledge goal and method frame of the approved mandate are in the actual
request of the producer AND of the reviewer, and two orders over the same sources carry their own question each (a mandate reference
alone conveys no order). The requests are captured at the gateway; the mock adapter answers, so nothing is paid."""
from __future__ import annotations

import pytest

from campus_api import services_sources
from campus_gateway.gateway import Gateway
from tests.conftest import active_policy_document, build_catalog_document
from tests.integration.conftest_fetch import make_fetcher
from tests.integration.test_s0_flow import MANDATE_DOC, Api, _authorize, _create_task, bootstrap

pytestmark = pytest.mark.integration

DECISION = {"expected_version": 1, "option": "accept", "rationale": "Testmandat", "alternatives_presented": ["begrenzt", "ablehnen"],
            "remaining_objections": [], "consequences": "Projekt darf starten", "conditions": []}
QUESTION_A = "Welche Einsprachefrist nennt die Quelle und wovon hängt sie ab? (Auftrag A)"
QUESTION_B = "Welche Rolle spielt die Offenlegung der Entscheidgrundlagen für die Wirksamkeit der Einsprache? (Auftrag B)"


@pytest.fixture
def captured(monkeypatch):
    seen: list = []
    original = Gateway.invoke

    def spy(self, request, deployment, decision):
        seen.append(request)
        return original(self, request, deployment, decision)

    monkeypatch.setattr(Gateway, "invoke", spy)
    monkeypatch.setattr(services_sources, "default_fetcher", make_fetcher())
    return seen


def _order(api: Api, director_id: str, institute_id: str, question: str, goal: str, frame: str) -> dict:
    doc = MANDATE_DOC | {"responsible_user_id": director_id, "title": question[:40], "question_space": question, "knowledge_goal": goal,
                         "method_frame": frame, "review_requirements": f"Prüfe gegen die Frage: {question}"}
    mandate = api.post("/mandates", {"document": doc, "budget": "20.00", "currency": "CHF"}).json()
    assert api.post(f"/mandates/{mandate['mandate_version_id']}/approve", DECISION).status_code == 200
    project = api.post("/projects", {"mandate_version_id": mandate["mandate_version_id"], "institute_id": institute_id, "title": question[:40],
                                     "budget": "10.00"}).json()
    assert api.post("/sources/fetch", {"project_id": project["project_id"], "url": "https://public.example/urteil", "task_id": None}).status_code == 201
    task = _create_task(api, project["project_id"])
    decision_id = _authorize(api, task["task_id"]).json()["routing_decision_id"]
    run = api.post("/runs", {"task_id": task["task_id"], "routing_decision_id": decision_id}).json()
    assert run["state"] == "finished", run
    assert api.post(f"/runs/{run['run_attempt_id']}/review").status_code == 201
    return {"run": run, "task": task, "question": question, "goal": goal, "frame": frame}


def _text(request, origin: str) -> str:
    return "\n".join(b["text"] for b in request.content_blocks if b.get("origin") == origin)


def test_question_goal_and_frame_reach_producer_and_reviewer(client, session_factory, workspace_id, captured):
    boot = bootstrap(session_factory, workspace_id)
    api = Api(client)
    api.login(workspace_id, "leitung")
    # governance as in govern(), without its fixture project: policy, price, data authorizations, catalog, the fixture profile's need and skill
    r = api.post("/policies", {"document": active_policy_document() | {"activation_state": "draft"}})
    api.post(f"/policies/{r.json()['policy_id']}/activate")
    api.post("/pricing", {"pricing_ref": "test-price-1", "currency": "CHF", "input_per_million": "10.00", "output_per_million": "50.00",
                          "source_note": "Synthetischer Testpreis"})
    for provider in sorted({d["provider_id"] for d in boot["catalog_doc"]["deployments"]}):
        api.post("/processing-profiles", {"ref": f"profile-{provider}", "provider_id": provider, "region": "test", "allowed_data_labels": ["public"],
                                          "note": "Synthetische Datenfreigabe"})
    cid = api.post("/model-catalog/drafts", {"document": build_catalog_document()}, method="PUT").json()["catalog_id"]
    assert api.post(f"/model-catalog/{cid}/activate").status_code == 200
    from campus_contracts.validation import load_example

    api.post("/needs", {"ref": "fixture-need-1", "gap": "Quellenanalyse", "requested_specialization": "AI research methodology",
                        "task_class": "source_analysis", "alternatives_considered": [], "expected_benefit": "Test"})
    api.post("/skills/candidates", {"document": load_example("skill_manifest")})
    api.post("/agents/candidates", {"document": load_example("agent_profile")})
    institute = api.get("/institutes").json()[0]["institute_id"]

    a = _order(api, boot["director_id"], institute, QUESTION_A, "Ziel A: Frist belegen", "Rahmen A: nur Fristangaben werten")
    b = _order(api, boot["director_id"], institute, QUESTION_B, "Ziel B: Offenlegung belegen", "Rahmen B: nur Offenlegung werten")
    for order in (a, b):
        gen = next(r for r in captured if r.invocation_id == f"{order['run']['run_attempt_id']}:gen:1")
        rev = next(r for r in captured if r.invocation_id == f"{order['run']['run_attempt_id']}:review:1")
        for req, who in ((gen, "Erzeuger"), (rev, "Prüfer")):
            mandate_text = _text(req, "mandate")
            assert order["question"] in mandate_text and order["goal"] in mandate_text and order["frame"] in mandate_text, who
            other = QUESTION_B if order["question"] == QUESTION_A else QUESTION_A
            assert other not in mandate_text, f"{who}: the other order's question must not leak in"
            assert "Einsprachefrist" in _text(req, "source_passage"), f"{who}: the same source passages"
        assert "Zugangsregeln" in _text(gen, "mandate") and "Risikoregeln" in _text(gen, "mandate")
        assert f"Prüfe gegen die Frage: {order['question']}" in _text(rev, "mandate"), "the reviewer gets the review requirements"
        assert "Forschungsauftrag (Mandat" in rev.system_rules and "open_questions" in _text(gen, "task")
    # the dossier carries what the generation declared beyond its claims
    dossier = api.get(f"/projects/{a['task']['project_id']}/dossier").json()
    run_entry = next(r for r in dossier["runs"] if r["run_attempt_id"] == a["run"]["run_attempt_id"])
    assert "open_questions" in run_entry and "omissions" in run_entry


def test_budgets_above_the_policy_limits_are_refused(client, session_factory, workspace_id):
    from tests.integration.test_s0_flow import govern

    boot = bootstrap(session_factory, workspace_id)
    api = Api(client)
    api.login(workspace_id, "leitung")
    state = govern(api, boot)  # test policy: max_task_cost 50.00, max_project_cost 500.00
    r = api.post("/projects", {"mandate_version_id": state["mandate"]["mandate_version_id"], "institute_id": state["project"]["institute_id"],
                               "title": "zu gross", "budget": "500.01"})
    assert r.status_code == 409 and r.json()["code"] == "BUDGET_EXHAUSTED" and "max_project_cost" in r.json()["message"]
    from tests.integration.test_s0_flow import task_need

    r = api.post(f"/projects/{state['project']['project_id']}/tasks", {"task_need": task_need(), "budget": "50.01"})
    assert r.status_code == 409 and "max_task_cost" in r.json()["message"]
    assert api.post(f"/projects/{state['project']['project_id']}/tasks", {"task_need": task_need(), "budget": "50.00"}).status_code == 201
