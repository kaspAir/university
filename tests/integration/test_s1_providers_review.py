"""S1 against PostgreSQL: activation gate, LLM selector through the core, replanning, distinct-model review, discovery."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from campus_infra.models import Reservation, RoutingDecisionRecord
from tests.conftest import active_policy_document, build_catalog_document
from tests.integration.test_s0_flow import Api, _authorize, _create_task, bootstrap, govern

pytestmark = pytest.mark.integration


@pytest.fixture
def campus(client, session_factory, workspace_id):
    boot = bootstrap(session_factory, workspace_id)
    api = Api(client)
    api.login(workspace_id, "leitung")
    state = govern(api, boot)
    return {"api": api, "boot": boot, **state}


def _route_llm(api: Api, task_id: str):
    return api.post(f"/tasks/{task_id}/routing-decisions", {"proposal": None, "selection_mode": "llm"})


# ---------------------------------------------------------------- activation gate

def test_catalog_activation_refuses_without_data_authorization(client, session_factory, workspace_id):
    boot = bootstrap(session_factory, workspace_id)
    api = Api(client)
    api.login(workspace_id, "leitung")
    r = api.post("/policies", {"document": active_policy_document() | {"activation_state": "draft"}})
    api.post(f"/policies/{r.json()['policy_id']}/activate")
    r = api.post("/model-catalog/drafts", {"document": boot["catalog_doc"]}, method="PUT")
    catalog_id = r.json()["catalog_id"]
    r = api.post(f"/model-catalog/{catalog_id}/activate")
    assert r.status_code == 422 and r.json()["code"] == "VALIDATION_ERROR"
    violations = r.json()["details"]["violations"]
    assert any("Preisbindung" in v for v in violations) and any("Datenfreigabe" in v for v in violations)
    assert not any("fixture-deployment-c" in v for v in violations), "disabled deployments are not gated"


def test_runtime_catalog_needs_resolvable_qualification_evidence(campus):
    api = campus["api"]
    doc = build_catalog_document() | {"revision": "runtime-1", "purpose": "runtime_snapshot"}
    catalog_id = api.post("/model-catalog/drafts", {"document": doc}, method="PUT").json()["catalog_id"]
    r = api.post(f"/model-catalog/{catalog_id}/activate")
    assert r.status_code == 422 and any("existiert nicht als Nachweis" in v for v in r.json()["details"]["violations"])
    # a model may not certify itself
    r = api.post("/qualifications", {"ref": "q-self", "canonical_model_id": "fixture/model-a@1", "task_class": "source_analysis",
                                     "suite_ref": "suite-1", "result_summary": "ok", "passed": True,
                                     "evaluated_by": "fixture/model-a@1", "evaluated_by_kind": "model"})
    assert r.status_code == 409 and r.json()["code"] == "QUALIFICATION_MISSING"
    for ref, canonical in [("fixture-eval-a", "fixture/model-a@1"), ("fixture-eval-b", "fixture/model-b@1"), ("fixture-eval-e", "fixture/model-e@1")]:
        r = api.post("/qualifications", {"ref": ref, "canonical_model_id": canonical, "task_class": "source_analysis",
                                         "suite_ref": "suite-1", "result_summary": "synthetischer Testsatz bestanden", "passed": True,
                                         "evaluated_by": "fixture/model-b@1" if canonical != "fixture/model-b@1" else "fixture/model-a@1",
                                         "evaluated_by_kind": "model", "profile_ref": "fixture-ai-researcher@1", "skill_refs": ["fixture-source-analysis@1"]})
        assert r.status_code == 201, r.text
    assert api.post(f"/model-catalog/{catalog_id}/activate").status_code == 200


# ---------------------------------------------------------------- selector

def test_llm_selector_proposes_and_core_authorizes(campus, session_factory):
    api = campus["api"]
    task = _create_task(api, campus["project"]["project_id"])
    r = _route_llm(api, task["task_id"])
    assert r.status_code == 201, r.text
    decision = r.json()["decision"]
    assert decision["selection_mode"] == "llm"
    assert decision["selector_model_ref"] == "fixture/model-b@1"
    assert decision["selected_deployment_id"] == "fixture-deployment-a"  # mock selector takes the first eligible candidate
    assert decision["review_deployment_id"] == "fixture-deployment-b"
    with session_factory() as db:
        purposes = sorted(p for (p,) in db.execute(select(Reservation.purpose).where(Reservation.task_id == task["task_id"])).all())
        assert purposes == ["generation", "review", "selector"]
        selector_res = db.execute(select(Reservation).where(Reservation.task_id == task["task_id"], Reservation.purpose == "selector")).scalar_one()
        assert selector_res.state == "settled"
        decisions = db.execute(select(RoutingDecisionRecord).where(RoutingDecisionRecord.task_id == task["task_id"])).scalars().all()
        assert len(decisions) == 2  # selector route + producer route, both registered by the core


def test_selector_pool_empty_means_wait(campus):
    api = campus["api"]
    dup = api.post("/policies", {"document": active_policy_document(selector_ids=[]) | {"activation_state": "draft"}})
    assert dup.status_code == 409 and dup.json()["code"] == "VERSION_CONFLICT"  # a changed policy needs a new revision
    r = api.post("/policies", {"document": active_policy_document(selector_ids=[]) | {"activation_state": "draft", "revision": "policy-no-selector"}})
    assert r.status_code == 201, r.text
    assert api.post(f"/policies/{r.json()['policy_id']}/activate").status_code == 200
    task = _create_task(api, campus["project"]["project_id"], routing_policy_ref="policy-no-selector")
    r = _route_llm(api, task["task_id"])
    assert r.status_code == 409 and r.json()["code"] == "NO_ELIGIBLE_SELECTOR"


# ---------------------------------------------------------------- replanning after provider failure

def test_failed_deployment_is_excluded_on_replan(campus):
    api = campus["api"]
    task = _create_task(api, campus["project"]["project_id"], budget="2.00")
    first = _authorize(api, task["task_id"], selected="fixture-deployment-e-flaky")
    assert first.status_code == 201, first.text
    r = api.post("/runs", {"task_id": task["task_id"], "routing_decision_id": first.json()["routing_decision_id"]})
    assert r.status_code == 409 and r.json()["code"] == "NO_ELIGIBLE_MODEL" and r.json()["retryable"] is True
    runs = api.get(f"/projects/{campus['project']['project_id']}/runs").json()
    assert runs[-1]["end_reason"] == "provider_error"
    # same deployment again: excluded with the failing attempt as reason
    again = _authorize(api, task["task_id"], selected="fixture-deployment-e-flaky")
    assert again.status_code == 409 and again.json()["code"] == "PROPOSAL_INVALID"
    assessments = {a["deployment_id"]: a for a in again.json()["details"]["assessments"]}
    assert any(reason.startswith("EXCLUDED:FAILED_IN_ATTEMPT:") for reason in assessments["fixture-deployment-e-flaky"]["exclusion_reasons"])
    # replan under the same constraints succeeds on another deployment
    second = _authorize(api, task["task_id"], selected="fixture-deployment-a")
    assert second.status_code == 201, second.text
    r = api.post("/runs", {"task_id": task["task_id"], "routing_decision_id": second.json()["routing_decision_id"]})
    assert r.status_code == 201 and r.json()["state"] == "finished"


def test_replan_limit_is_enforced(campus):
    api = campus["api"]
    r = api.post("/policies", {"document": active_policy_document(replans=0) | {"activation_state": "draft", "revision": "policy-no-replan"}})
    assert r.status_code == 201, r.text
    assert api.post(f"/policies/{r.json()['policy_id']}/activate").status_code == 200
    task = _create_task(api, campus["project"]["project_id"], budget="2.00", routing_policy_ref="policy-no-replan")
    first = _authorize(api, task["task_id"], selected="fixture-deployment-e-flaky")
    api.post("/runs", {"task_id": task["task_id"], "routing_decision_id": first.json()["routing_decision_id"]})
    r = _authorize(api, task["task_id"], selected="fixture-deployment-a")
    assert r.status_code == 409 and r.json()["code"] == "REPLAN_LIMIT_REACHED"


def test_refusal_ends_run_without_rerouting(campus):
    api = campus["api"]
    doc = build_catalog_document() | {"revision": "test-catalog-refuse"}
    for dep in doc["deployments"]:
        if dep["id"] == "fixture-deployment-a":
            dep["provider_model_id"] = "mock-refuse-a"
    catalog_id = api.post("/model-catalog/drafts", {"document": doc}, method="PUT").json()["catalog_id"]
    assert api.post(f"/model-catalog/{catalog_id}/activate").status_code == 200
    task = _create_task(api, campus["project"]["project_id"])
    decision_id = _authorize(api, task["task_id"]).json()["routing_decision_id"]
    r = api.post("/runs", {"task_id": task["task_id"], "routing_decision_id": decision_id})
    assert r.status_code == 409 and r.json()["code"] == "PROTECTED_REFUSAL" and r.json()["retryable"] is False
    runs = api.get(f"/projects/{campus['project']['project_id']}/runs").json()
    assert runs[-1]["end_reason"] == "protected_refusal"


# ---------------------------------------------------------------- review

def _finished_run(api: Api, project_id: str, **need) -> dict:
    task = _create_task(api, project_id, **need)
    decision_id = _authorize(api, task["task_id"]).json()["routing_decision_id"]
    r = api.post("/runs", {"task_id": task["task_id"], "routing_decision_id": decision_id})
    assert r.status_code == 201, r.text
    return {"task": task, "run": r.json(), "decision_id": decision_id}


def test_review_by_distinct_model_closes_or_contests(campus, session_factory):
    api = campus["api"]
    project_id = campus["project"]["project_id"]
    clean = _finished_run(api, project_id)
    r = api.post(f"/runs/{clean['run']['run_attempt_id']}/review")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["verdict"] == "no_objection" and body["task_state"] == "finished"
    assert body["reviewer_canonical_model_id"] == "fixture/model-b@1" != body["producer_canonical_model_id"] == "fixture/model-a@1"
    assert body["artifact_sha256"] == clean["run"]["artifact_sha256"]
    # a second review of the same frozen artifact is refused (I05: new review needs a new artifact)
    assert api.post(f"/runs/{clean['run']['run_attempt_id']}/review").status_code == 409
    with session_factory() as db:
        review_res = db.execute(select(Reservation).where(Reservation.task_id == clean["task"]["task_id"], Reservation.purpose == "review")).scalar_one()
        assert review_res.state == "settled"
    contested = _finished_run(api, project_id, task_class="source_analysis_contested")
    r = api.post(f"/runs/{contested['run']['run_attempt_id']}/review")
    assert r.status_code == 201, r.text
    assert r.json()["verdict"] == "objections" and r.json()["task_state"] == "waiting" and r.json()["findings"]
    reviews = api.get(f"/tasks/{contested['task']['task_id']}/reviews").json()
    assert reviews[0]["findings"][0]["status"] == "open"
    events = [e["event_type"] for e in api.get(f"/projects/{project_id}/events").json()]
    assert "ReviewCompleted" in events and "ClaimContested" in events


def test_review_requires_a_finished_run(campus):
    api = campus["api"]
    task = _create_task(api, campus["project"]["project_id"], budget="2.00")
    first = _authorize(api, task["task_id"], selected="fixture-deployment-e-flaky")
    api.post("/runs", {"task_id": task["task_id"], "routing_decision_id": first.json()["routing_decision_id"]})
    runs = api.get(f"/projects/{campus['project']['project_id']}/runs").json()
    r = api.post(f"/runs/{runs[-1]['run_attempt_id']}/review")
    assert r.status_code == 409 and r.json()["code"] == "INVALID_TRANSITION"


# ---------------------------------------------------------------- discovery and roles

def test_discovery_requires_approved_adapter_with_listing(campus):
    r = campus["api"].get("/model-catalog/discovery?deployment_id=fixture-deployment-a")
    assert r.status_code == 409 and r.json()["code"] == "ADAPTER_NOT_APPROVED"  # mock has no listing
    r = campus["api"].get("/model-catalog/discovery?deployment_id=unregistered")
    assert r.status_code == 404  # adapter, endpoint and secret only ever come from a registered deployment


def test_operator_cannot_approve_data_processing(client, session_factory, workspace_id):
    bootstrap(session_factory, workspace_id)
    api = Api(client)
    api.login(workspace_id, "betrieb")
    r = api.post("/processing-profiles", {"ref": "p", "provider_id": "x", "region": "eu", "allowed_data_labels": ["public"], "note": "n"})
    assert r.status_code == 403 and r.json()["code"] == "FORBIDDEN"
