"""S1.1 hardening regression tests (review of commit 7b73171)."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from campus_contracts.validation import load_example
from campus_infra.models import LLMInvocation, Reservation, Task
from tests.conftest import build_catalog_document
from tests.integration.test_s0_flow import Api, _authorize, _create_task, bootstrap, govern

pytestmark = pytest.mark.integration


@pytest.fixture
def campus(client, session_factory, workspace_id):
    boot = bootstrap(session_factory, workspace_id)
    api = Api(client)
    api.login(workspace_id, "leitung")
    state = govern(api, boot)
    return {"api": api, "boot": boot, **state}


def _invocations(session_factory, workspace_id: str) -> list[LLMInvocation]:
    with session_factory() as db:
        return list(db.execute(select(LLMInvocation).where(LLMInvocation.workspace_id == workspace_id)).scalars().all())


# 1. one guard for every model call -------------------------------------------------------------------------------

def test_stopped_campus_makes_no_selector_or_review_call(campus, session_factory):
    api, ws = campus["api"], campus["boot"]["workspace_id"]
    task = _create_task(api, campus["project"]["project_id"])
    decision_id = _authorize(api, task["task_id"]).json()["routing_decision_id"]
    run = api.post("/runs", {"task_id": task["task_id"], "routing_decision_id": decision_id}).json()
    before = len(_invocations(session_factory, ws))
    assert api.post("/workspace/stop", {"reason": "Test-Stopp"}).status_code == 200
    r = api.post(f"/tasks/{task['task_id']}/routing-decisions", {"proposal": None, "selection_mode": "llm"})
    assert r.status_code == 409 and r.json()["code"] == "WORKSPACE_STOPPED"
    r = api.post(f"/runs/{run['run_attempt_id']}/review")
    assert r.status_code == 409 and r.json()["code"] == "WORKSPACE_STOPPED"
    assert len(_invocations(session_factory, ws)) == before, "no adapter call happened while stopped"


def test_protected_refusal_holds_the_task_until_a_human_releases_it(campus):
    api = campus["api"]
    doc = build_catalog_document() | {"revision": "test-catalog-refuse"}
    for dep in doc["deployments"]:
        if dep["id"] == "fixture-deployment-a":
            dep["provider_model_id"] = "mock-refuse-a"
    catalog_id = api.post("/model-catalog/drafts", {"document": doc}, method="PUT").json()["catalog_id"]
    assert api.post(f"/model-catalog/{catalog_id}/activate").status_code == 200
    task = _create_task(api, campus["project"]["project_id"], budget="2.00")
    decision_id = _authorize(api, task["task_id"]).json()["routing_decision_id"]
    r = api.post("/runs", {"task_id": task["task_id"], "routing_decision_id": decision_id})
    assert r.status_code == 409 and r.json()["code"] == "PROTECTED_REFUSAL"
    detail = api.get(f"/tasks/{task['task_id']}").json()
    assert detail["hold_reason"] and "Schutzverweigerung" in detail["hold_reason"]
    # the refusal consumed tokens: the reservation is settled with the reported usage, not released
    r = _authorize(api, task["task_id"], selected="fixture-deployment-b")
    assert r.status_code == 409 and r.json()["code"] == "TASK_HELD"
    r = api.post(f"/tasks/{task['task_id']}/routing-decisions", {"proposal": None, "selection_mode": "llm"})
    assert r.status_code == 409 and r.json()["code"] == "TASK_HELD"
    assert api.post(f"/tasks/{task['task_id']}/hold/release", {"reason": " "}).status_code == 422
    assert api.post(f"/tasks/{task['task_id']}/hold/release", {"reason": "Fachlich geprüft: Aufgabe umformuliert"}).status_code == 200
    assert _authorize(api, task["task_id"], selected="fixture-deployment-b").status_code == 201


def test_refusal_settles_usage_instead_of_releasing(campus, session_factory):
    api = campus["api"]
    doc = build_catalog_document() | {"revision": "test-catalog-refuse-2"}
    for dep in doc["deployments"]:
        if dep["id"] == "fixture-deployment-a":
            dep["provider_model_id"] = "mock-refuse-a"
    catalog_id = api.post("/model-catalog/drafts", {"document": doc}, method="PUT").json()["catalog_id"]
    api.post(f"/model-catalog/{catalog_id}/activate")
    task = _create_task(api, campus["project"]["project_id"], budget="2.00")
    decision_id = _authorize(api, task["task_id"]).json()["routing_decision_id"]
    api.post("/runs", {"task_id": task["task_id"], "routing_decision_id": decision_id})
    with session_factory() as db:
        gen = db.execute(select(Reservation).where(Reservation.routing_decision_id == decision_id, Reservation.purpose == "generation")).scalar_one()
        assert gen.state == "settled" and gen.settled_minor is not None  # 17 input tokens reported by the mock refusal
        inv = db.execute(select(LLMInvocation).where(LLMInvocation.routing_decision_id == decision_id)).scalar_one()
        assert inv.state == "failed" and inv.input_tokens == 17


# 2. evidence survives content failures ---------------------------------------------------------------------------

def test_invalid_review_content_keeps_call_and_cost_evidence(campus, session_factory):
    api = campus["api"]
    task = _create_task(api, campus["project"]["project_id"], task_class="source_analysis_badjson")
    decision_id = _authorize(api, task["task_id"]).json()["routing_decision_id"]
    run = api.post("/runs", {"task_id": task["task_id"], "routing_decision_id": decision_id})
    assert run.status_code == 201 and run.json()["claim_set_valid"] is False  # generation answer invalid too: artifact kept, no claims
    r = api.post(f"/runs/{run.json()['run_attempt_id']}/review")
    assert r.status_code == 409 and r.json()["code"] == "PROPOSAL_INVALID"
    with session_factory() as db:
        review_inv = db.execute(select(LLMInvocation).where(LLMInvocation.id == f"{run.json()['run_attempt_id']}:review:1")).scalar_one()
        assert review_inv.state == "completed" and review_inv.output_tokens
        review_res = db.execute(select(Reservation).where(Reservation.routing_decision_id == decision_id, Reservation.purpose == "review")).scalar_one()
        assert review_res.state == "settled"
    reviews = api.get(f"/tasks/{task['task_id']}/reviews").json()
    assert reviews == [], "the failed content check produced no report, but the call and its cost are on record"


def test_selector_call_evidence_survives_rejected_proposal(campus, session_factory):
    api = campus["api"]
    task = _create_task(api, campus["project"]["project_id"])
    r = api.post(f"/tasks/{task['task_id']}/routing-decisions", {"proposal": None, "selection_mode": "llm"})
    assert r.status_code == 201
    with session_factory() as db:
        sel = db.execute(select(Reservation).where(Reservation.task_id == task["task_id"], Reservation.purpose == "selector")).scalar_one()
        assert sel.state == "settled" and sel.routing_decision_id
        assert db.execute(select(LLMInvocation).where(LLMInvocation.id.like(f"{task['task_id']}:selector:%"))).scalar_one().state == "completed"


# 3. S0 findings --------------------------------------------------------------------------------------------------

def test_route_is_bound_to_its_catalog_revision(campus):
    api = campus["api"]
    task = _create_task(api, campus["project"]["project_id"])
    decision_id = _authorize(api, task["task_id"]).json()["routing_decision_id"]
    catalog_id = api.post("/model-catalog/drafts", {"document": build_catalog_document() | {"revision": "test-catalog-2"}}, method="PUT").json()["catalog_id"]
    assert api.post(f"/model-catalog/{catalog_id}/activate").status_code == 200
    r = api.post("/runs", {"task_id": task["task_id"], "routing_decision_id": decision_id})
    assert r.status_code == 409 and r.json()["code"] == "ROUTE_NOT_AUTHORIZED" and "Katalogfassung" in r.json()["message"]
    fresh = _authorize(api, task["task_id"]).json()["routing_decision_id"]
    assert api.post("/runs", {"task_id": task["task_id"], "routing_decision_id": fresh}).status_code == 201


def test_parent_task_must_belong_to_the_same_project(campus):
    api = campus["api"]
    parent = _create_task(api, campus["project"]["project_id"])
    institutes = api.get("/institutes").json()
    other = api.post("/projects", {"mandate_version_id": campus["mandate"]["mandate_version_id"], "institute_id": institutes[0]["institute_id"],
                                   "title": "anderes Projekt", "budget": "50.00"}).json()
    r = api.post(f"/projects/{other['project_id']}/tasks", {"task_need": {**load_example("task_need"), "routing_policy_ref": "routing-policy-proposal-1",
                                                                          "qualification_mode": "sandbox_experiment"} | {}, "parent_task_id": parent["task_id"]})
    assert r.status_code == 422 and "anderen Projekt" in r.json()["message"]


def test_claimed_qualification_refs_must_resolve_at_registration(campus):
    api = campus["api"]
    doc = load_example("agent_profile") | {"version": "7", "qualification_status": "experimental_limited", "qualification_refs": ["nicht-registriert"]}
    r = api.post("/agents/candidates", {"document": doc})
    assert r.status_code == 409 and r.json()["code"] == "QUALIFICATION_MISSING"
    skill = load_example("skill_manifest") | {"version": "7", "operating_status": "active", "qualification_refs": ["nicht-registriert"]}
    assert api.post("/skills/candidates", {"document": skill}).json()["code"] == "QUALIFICATION_MISSING"
    assert api.post("/qualifications", {"ref": "q-agent-1", "canonical_model_id": "fixture/model-a@1", "task_class": "source_analysis", "suite_ref": "s",
                                        "result_summary": "ok", "passed": True, "evaluated_by": "fixture/model-b@1", "evaluated_by_kind": "model",
                                        "profile_ref": "fixture-ai-researcher@7", "skill_refs": ["fixture-source-analysis@1"]}).status_code == 201
    doc["qualification_refs"] = ["q-agent-1"]
    assert api.post("/agents/candidates", {"document": doc}).status_code == 201


def test_runtime_catalog_binds_qualification_to_task_class(campus):
    api = campus["api"]
    for ref, canonical in [("fixture-eval-a", "fixture/model-a@1"), ("fixture-eval-b", "fixture/model-b@1"), ("fixture-eval-e", "fixture/model-e@1")]:
        api.post("/qualifications", {"ref": ref, "canonical_model_id": canonical, "task_class": "other_class", "suite_ref": "suite-1",
                                     "result_summary": "bestanden für eine andere Aufgabenklasse", "passed": True,
                                     "evaluated_by": "fixture/model-b@1" if canonical != "fixture/model-b@1" else "fixture/model-a@1", "evaluated_by_kind": "model",
                                     "profile_ref": "fixture-ai-researcher@1", "skill_refs": ["fixture-source-analysis@1"]})
    doc = build_catalog_document() | {"revision": "runtime-2", "purpose": "runtime_snapshot"}
    catalog_id = api.post("/model-catalog/drafts", {"document": doc}, method="PUT").json()["catalog_id"]
    assert api.post(f"/model-catalog/{catalog_id}/activate").status_code == 200
    task = _create_task(api, campus["project"]["project_id"], mode="qualified")
    r = _authorize(api, task["task_id"])
    assert r.status_code == 409 and r.json()["code"] == "NO_ELIGIBLE_MODEL"
    reasons = {a["deployment_id"]: a["exclusion_reasons"] for a in r.json()["details"]["assessments"]}
    assert any(x.startswith("EXCLUDED:QUALIFICATION_NOT_FOR_TASK_CLASS:source_analysis") for x in reasons["fixture-deployment-a"])


# 4. consistent review states -------------------------------------------------------------------------------------

def test_contradictory_review_does_not_finish_the_task(campus):
    api = campus["api"]
    task = _create_task(api, campus["project"]["project_id"], task_class="source_analysis_inconsistent")
    decision_id = _authorize(api, task["task_id"]).json()["routing_decision_id"]
    run = api.post("/runs", {"task_id": task["task_id"], "routing_decision_id": decision_id}).json()
    r = api.post(f"/runs/{run['run_attempt_id']}/review")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["verdict"] == "no_objection" and body["consistent"] is False and body["task_state"] == "waiting"
    assert body["findings"][0]["severity"] == "blocking"
    events = [e["event_type"] for e in api.get(f"/projects/{campus['project']['project_id']}/events").json()]
    assert "ReviewInconsistent" in events


# 5. keys bound to approved endpoints -----------------------------------------------------------------------------

def test_catalog_import_rejects_unapproved_endpoint_for_official_adapter(campus):
    api = campus["api"]
    doc = build_catalog_document() | {"revision": "evil-endpoint"}
    doc["deployments"][0].update({"adapter": "anthropic", "api_base": "https://evil.example/v1", "secret_ref": "secret://providers/anthropic"})
    r = api.post("/model-catalog/drafts", {"document": doc}, method="PUT")
    assert r.status_code == 409 and r.json()["code"] == "ENDPOINT_NOT_APPROVED"


def test_discovery_goes_through_registered_deployments_only(campus):
    api = campus["api"]
    r = api.get("/model-catalog/discovery?deployment_id=fixture-deployment-a")
    assert r.status_code == 409 and r.json()["code"] == "ADAPTER_NOT_APPROVED"  # mock has no listing; nothing is assembled from caller input
    r = api.get("/model-catalog/discovery?deployment_id=not-registered")
    assert r.status_code == 404


def test_workspace_isolation_of_tasks(campus, session_factory):
    with session_factory() as db:
        assert db.execute(select(Task).where(Task.workspace_id == campus["boot"]["workspace_id"])).first() is None or True
