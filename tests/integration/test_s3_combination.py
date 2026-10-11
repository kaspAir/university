"""S3 review, part 2: full combination binding (profile AND exact skill set AND model AND task class), bootstrap approvals as their
own kind, and a hard stop when a referenced skill is missing. What each test proves is stated in its docstring."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from campus_contracts.validation import load_example
from campus_infra.models import EvaluationRun, LLMInvocation, RunAttempt
from tests.conftest import build_catalog_document
from tests.integration.test_s0_flow import Api, _authorize, _create_task, bootstrap, govern
from tests.integration.test_s3_factory import CASES, _candidate, _evaluate, _suite

pytestmark = pytest.mark.integration

SKILL_V1 = "fixture-source-analysis@1"


@pytest.fixture
def campus(client, session_factory, workspace_id):
    boot = bootstrap(session_factory, workspace_id)
    api = Api(client)
    api.login(workspace_id, "leitung")
    state = govern(api, boot)
    for ref, task_class, note in [("boot-evaluator-b", "evaluation", "Prüferrolle (Bootstrap)"),
                                  ("boot-reviewer-b", "source_analysis", "Prüferrolle für Quellenanalysen (Bootstrap)")]:
        r = api.post("/qualifications", {"ref": ref, "canonical_model_id": "fixture/model-b@1", "task_class": task_class, "suite_ref": "bootstrap",
                                         "result_summary": note, "passed": True, "evaluated_by": "forschungsleitung", "evaluated_by_kind": "human",
                                         "kind": "bootstrap"})
        assert r.status_code == 201, r.text
    return {"api": api, "boot": boot, **state}


def _activate_runtime_catalog(api: Api, revision: str) -> None:
    doc = build_catalog_document() | {"revision": revision, "purpose": "runtime_snapshot"}
    for dep in doc["deployments"]:
        dep["qualification_refs"] = []  # only the register counts
    catalog_id = api.post("/model-catalog/drafts", {"document": doc}, method="PUT").json()["catalog_id"]
    assert api.post(f"/model-catalog/{catalog_id}/activate").status_code == 200


def _register_skill(api: Api, skill_id: str, version: str) -> str:
    doc = load_example("skill_manifest") | {"id": skill_id, "version": version}
    r = api.post("/skills/candidates", {"document": doc})
    assert r.status_code == 201, r.text
    return r.json()["ref"]


def _route_a(api: Api, task_id: str):
    proposal = load_example("route_proposal") | {"task_id": task_id, "evidence_refs": [], "alternatives": []}
    return api.post(f"/tasks/{task_id}/routing-decisions", {"proposal": proposal, "selection_mode": "fixture"})


def _qualified_v2(api: Api, project_id: str) -> tuple[str, dict]:
    _suite(api, threshold=50)
    v2 = _candidate(api, version="2")
    run = _evaluate(api, project_id, v2).json()
    assert run["activated"] is True and run["composition_key"] == f"{v2}+{SKILL_V1}+source_analysis"
    return v2, run


def test_additional_skill_is_a_different_combination(campus):
    """Proves: a task that uses the qualified profile WITH an extra skill is not covered by the combination record (no silent coverage).
    Deployment A is excluded for that task; the bootstrap reviewer B stays eligible because bootstrap approvals bind no skills."""
    api, project_id = campus["api"], campus["project"]["project_id"]
    v2, _ = _qualified_v2(api, project_id)
    extra = _register_skill(api, "fixture-extra-skill", "1")
    _activate_runtime_catalog(api, "runtime-extra-skill")
    same = _create_task(api, project_id, agent_ref=v2, mode="qualified")
    assert _route_a(api, same["task_id"]).status_code == 201, "exact combination is covered"
    with_extra = _create_task(api, project_id, agent_ref=v2, mode="qualified", skill_version_refs=[SKILL_V1, extra])
    r = _route_a(api, with_extra["task_id"])
    assert r.status_code == 409 and r.json()["code"] == "PROPOSAL_INVALID", r.text
    assessed = {a["deployment_id"]: a for a in r.json()["details"]["assessments"]}
    assert not assessed["fixture-deployment-a"]["eligible"] and "QUALIFICATION_MISSING" in assessed["fixture-deployment-a"]["exclusion_reasons"]
    assert assessed["fixture-deployment-b"]["eligible"]


def test_other_skill_version_is_a_different_combination(campus):
    """Proves: the same skill in another version is not covered; the record binds the exact version."""
    api, project_id = campus["api"], campus["project"]["project_id"]
    v2, _ = _qualified_v2(api, project_id)
    v2_skill = _register_skill(api, "fixture-source-analysis", "2")
    _activate_runtime_catalog(api, "runtime-skill-v2")
    task = _create_task(api, project_id, agent_ref=v2, mode="qualified", skill_version_refs=[v2_skill])
    r = _route_a(api, task["task_id"])
    assert r.status_code == 409
    assessed = {a["deployment_id"]: a for a in r.json()["details"]["assessments"]}
    assert not assessed["fixture-deployment-a"]["eligible"]
    fewer = _create_task(api, project_id, agent_ref=v2, mode="qualified", skill_version_refs=[])
    r = _route_a(api, fewer["task_id"])
    assert r.status_code == 409, "a smaller skill set is a different combination as well"


def test_missing_skill_stops_the_build_before_any_model_call(campus, session_factory):
    """Proves: a referenced but unregistered skill stops the run before a call order exists: no run attempt, no journal entry.
    The same applies to a qualification run of a profile whose document references a missing skill."""
    api, project_id, ws = campus["api"], campus["project"]["project_id"], campus["boot"]["workspace_id"]
    task = _create_task(api, project_id, skill_version_refs=["ghost-skill@1"])
    decision_id = _authorize(api, task["task_id"]).json()["routing_decision_id"]
    r = api.post("/runs", {"task_id": task["task_id"], "routing_decision_id": decision_id})
    assert r.status_code == 422 and "nicht registriert" in r.json()["message"] and r.json()["blocked_action"] == "composition.build"
    with session_factory() as db:
        assert db.execute(select(RunAttempt).where(RunAttempt.task_id == task["task_id"])).first() is None
        assert db.execute(select(LLMInvocation).where(LLMInvocation.routing_decision_id == decision_id)).first() is None
    _suite(api)
    ghost_profile = _candidate(api, version="8", skill_version_refs=["ghost-skill@1"])
    r = _evaluate(api, project_id, ghost_profile)
    assert r.status_code == 422 and r.json()["blocked_action"] == "composition.build"
    with session_factory() as db:
        assert db.execute(select(EvaluationRun).where(EvaluationRun.candidate_ref == ghost_profile)).first() is None
        assert db.execute(select(LLMInvocation).where(LLMInvocation.workspace_id == ws, LLMInvocation.id.like("EV-%"))).first() is None


def test_bootstrap_and_combination_records_are_distinct_kinds(client, session_factory, workspace_id, campus):
    """Proves: a combination record needs profile AND skills together; a bootstrap approval is human, binds nothing and is reserved
    to the research director."""
    api = campus["api"]
    half = {"ref": "half", "canonical_model_id": "fixture/model-a@1", "task_class": "source_analysis", "suite_ref": "s", "result_summary": "x",
            "passed": True, "evaluated_by": "fixture/model-b@1", "evaluated_by_kind": "model", "profile_ref": "fixture-ai-researcher@2"}
    r = api.post("/qualifications", half)
    assert r.status_code == 422 and "gemeinsam" in r.json()["message"]
    full = half | {"ref": "full", "skill_refs": [SKILL_V1]}
    assert api.post("/qualifications", full).status_code == 201
    model_bootstrap = {"ref": "mb", "canonical_model_id": "fixture/model-a@1", "task_class": "evaluation", "suite_ref": "s", "result_summary": "x",
                       "passed": True, "evaluated_by": "fixture/model-b@1", "evaluated_by_kind": "model", "kind": "bootstrap"}
    assert api.post("/qualifications", model_bootstrap).status_code == 422, "bootstrap approvals are human"
    with session_factory() as db:
        from campus_api import services_identity
        from campus_domain.authz import Role
        from campus_infra.audit import Actor

        services_identity.create_user(db, actor=Actor("system", "t", workspace_id, frozenset(Role)), workspace_id=workspace_id, username="pruefer",
                                      display_name="Prüfer (Test)", password="test-passwort-mit-zwoelf-zeichen", roles=["reviewer"], correlation_id="t")
        db.commit()
    reviewer = Api(client)
    reviewer.login(workspace_id, "pruefer")
    human_boot = {"ref": "rb", "canonical_model_id": "fixture/model-a@1", "task_class": "evaluation", "suite_ref": "s", "result_summary": "x",
                  "passed": True, "evaluated_by": "pruefer", "evaluated_by_kind": "human", "kind": "bootstrap"}
    assert reviewer.post("/qualifications", human_boot).status_code == 403, "bootstrap is reserved to the research director"
    assert reviewer.post("/qualifications", full | {"ref": "full-by-reviewer"}).status_code == 201, "reviewers may record combination evidence"


def test_cases_constant_is_shared():
    assert len(CASES) == 2
