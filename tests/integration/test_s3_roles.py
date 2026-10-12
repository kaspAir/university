"""S3 review, part 3: a bootstrap approval is bound to ONE checking role. It never qualifies a producer combination, and producer
combination evidence never qualifies a reviewer or evaluator. Both directions are tested; the docstrings say what each test proves."""
from __future__ import annotations

import pytest

from campus_contracts.validation import load_example
from tests.conftest import build_catalog_document
from tests.integration.test_s0_flow import Api, _create_task, bootstrap, govern
from tests.integration.test_s3_factory import _candidate, _evaluate, _suite

pytestmark = pytest.mark.integration

SKILL_V1 = "fixture-source-analysis@1"
MODEL_A, MODEL_B = "fixture/model-a@1", "fixture/model-b@1"


def _approve(api: Api, ref: str, canonical: str, task_class: str, role: str):
    return api.post("/qualifications", {"ref": ref, "canonical_model_id": canonical, "task_class": task_class, "suite_ref": "bootstrap",
                                        "result_summary": f"{role} freigegeben (Bootstrap)", "passed": True, "evaluated_by": "forschungsleitung",
                                        "evaluated_by_kind": "human", "kind": "bootstrap", "role": role})


@pytest.fixture
def campus(client, session_factory, workspace_id):
    """No approvals at all: each test records exactly the approvals whose effect it measures."""
    boot = bootstrap(session_factory, workspace_id)
    api = Api(client)
    api.login(workspace_id, "leitung")
    return {"api": api, "boot": boot, **govern(api, boot)}


def _runtime_catalog(api: Api, revision: str) -> None:
    doc = build_catalog_document() | {"revision": revision, "purpose": "runtime_snapshot"}
    for dep in doc["deployments"]:
        dep["qualification_refs"] = []
    catalog_id = api.post("/model-catalog/drafts", {"document": doc}, method="PUT").json()["catalog_id"]
    assert api.post(f"/model-catalog/{catalog_id}/activate").status_code == 200


def _route(api: Api, task_id: str, selected: str):
    proposal = load_example("route_proposal") | {"task_id": task_id, "selected_deployment_id": selected, "evidence_refs": [], "alternatives": []}
    return api.post(f"/tasks/{task_id}/routing-decisions", {"proposal": proposal, "selection_mode": "fixture"})


def _combination(api: Api, ref: str, canonical: str, profile_ref: str, evaluated_by: str):
    return api.post("/qualifications", {"ref": ref, "canonical_model_id": canonical, "task_class": "source_analysis", "suite_ref": "s@1",
                                        "result_summary": "Kombination bestanden", "passed": True, "evaluated_by": evaluated_by,
                                        "evaluated_by_kind": "model", "profile_ref": profile_ref, "skill_refs": [SKILL_V1]})


def test_reviewer_approval_does_not_qualify_a_producer_combination(campus):
    """Direction 1. Proves: B holds a reviewer approval for source analyses, A holds the producer combination for profile v2. A task with
    profile v2 may run on A (B reviews); the same task proposed on B is refused with QUALIFICATION_MISSING."""
    api, project_id = campus["api"], campus["project"]["project_id"]
    assert _approve(api, "boot-evaluator-b", MODEL_B, "evaluation", "evaluator").status_code == 201
    assert _approve(api, "boot-reviewer-b", MODEL_B, "source_analysis", "reviewer").status_code == 201
    _suite(api, threshold=50)
    v2 = _candidate(api, version="2")
    assert _evaluate(api, project_id, v2).json()["activated"] is True
    _runtime_catalog(api, "runtime-roles-1")
    on_a = _create_task(api, project_id, agent_ref=v2, mode="qualified")
    r = _route(api, on_a["task_id"], "fixture-deployment-a")
    assert r.status_code == 201 and r.json()["decision"]["review_deployment_id"] == "fixture-deployment-b"
    on_b = _create_task(api, project_id, agent_ref=v2, mode="qualified")
    r = _route(api, on_b["task_id"], "fixture-deployment-b")
    assert r.status_code == 409 and r.json()["code"] == "PROPOSAL_INVALID", r.text
    assessed = {a["deployment_id"]: a for a in r.json()["details"]["assessments"]}
    assert "QUALIFICATION_MISSING" in assessed["fixture-deployment-b"]["exclusion_reasons"]


def test_producer_combination_does_not_qualify_a_reviewer(campus):
    """Direction 2. Proves: both A and B hold producer combination evidence for profile v2, nobody holds a reviewer approval. The route on A
    fails for want of a qualified reviewer (REVIEW_DIVERSITY_UNSATISFIED). After the reviewer approval for B, the same route succeeds and B
    reviews. Belegt die Rollentrennung im Routing, nicht die Prüfqualität von B."""
    api, project_id = campus["api"], campus["project"]["project_id"]
    v2 = _candidate(api, version="2")
    assert _combination(api, "combo-a", MODEL_A, v2, evaluated_by=MODEL_B).status_code == 201
    assert _combination(api, "combo-b", MODEL_B, v2, evaluated_by=MODEL_A).status_code == 201
    _runtime_catalog(api, "runtime-roles-2")
    task = _create_task(api, project_id, agent_ref=v2, mode="qualified")
    r = _route(api, task["task_id"], "fixture-deployment-a")
    assert r.status_code == 409 and r.json()["code"] == "REVIEW_DIVERSITY_UNSATISFIED", r.text
    assert _approve(api, "boot-reviewer-b", MODEL_B, "source_analysis", "reviewer").status_code == 201
    retry = _create_task(api, project_id, agent_ref=v2, mode="qualified")
    r = _route(api, retry["task_id"], "fixture-deployment-a")
    assert r.status_code == 201 and r.json()["decision"]["review_deployment_id"] == "fixture-deployment-b", r.text


def test_reviewer_approval_does_not_make_an_evaluator(campus):
    """Proves: a reviewer approval for B (even for the suite's evaluator task class) does not make B a qualified evaluator; a qualification
    run finds no evaluator. Only the evaluator approval does."""
    api, project_id = campus["api"], campus["project"]["project_id"]
    assert _approve(api, "boot-reviewer-b-eval", MODEL_B, "evaluation", "reviewer").status_code == 201
    _suite(api, threshold=50)
    v2 = _candidate(api, version="2")
    r = _evaluate(api, project_id, v2)
    assert r.status_code == 409 and r.json()["code"] == "QUALIFICATION_MISSING", r.text
    assert _approve(api, "boot-evaluator-b", MODEL_B, "evaluation", "evaluator").status_code == 201
    r = _evaluate(api, project_id, v2)
    assert r.status_code == 201 and r.json()["activated"] is True, r.text


def test_evaluator_approval_does_not_qualify_a_reviewer(campus):
    """Proves: the evaluator approval of B for 'evaluation' and a producer combination on A do not make B a reviewer for source analyses."""
    api, project_id = campus["api"], campus["project"]["project_id"]
    assert _approve(api, "boot-evaluator-b", MODEL_B, "evaluation", "evaluator").status_code == 201
    _suite(api, threshold=50)
    v2 = _candidate(api, version="2")
    assert _evaluate(api, project_id, v2).json()["activated"] is True
    _runtime_catalog(api, "runtime-roles-3")
    task = _create_task(api, project_id, agent_ref=v2, mode="qualified")
    r = _route(api, task["task_id"], "fixture-deployment-a")
    assert r.status_code == 409 and r.json()["code"] == "REVIEW_DIVERSITY_UNSATISFIED", r.text
