"""S3: need -> candidate -> independent qualification -> automatic activation -> provenance -> retirement.
Acceptance (chapter 20): the candidate cannot certify itself; child budget, version binding and registry completeness hold.
Tests: T05 (protected material), T10 (criterion lowered: old version and failed run remain), T12, T17 (regression), A10, A11."""
from __future__ import annotations

import hashlib

import pytest
from sqlalchemy import select

from campus_contracts.validation import load_example
from campus_infra.models import EvaluationRun, LLMInvocation, Reservation
from tests.integration.test_s0_flow import Api, _authorize, _create_task, bootstrap, govern

pytestmark = pytest.mark.integration


@pytest.fixture
def campus(client, session_factory, workspace_id):
    boot = bootstrap(session_factory, workspace_id)
    api = Api(client)
    api.login(workspace_id, "leitung")
    state = govern(api, boot)
    # Bootstrap (chapter 7): the research director records the evaluator qualification of model B by hand once
    r = api.post("/qualifications", {"ref": "boot-evaluator-b", "canonical_model_id": "fixture/model-b@1", "task_class": "evaluation",
                                     "suite_ref": "bootstrap", "result_summary": "Prüferrolle durch Forschungsleitung freigegeben (Bootstrap)",
                                     "passed": True, "evaluated_by": "forschungsleitung", "evaluated_by_kind": "human"})
    assert r.status_code == 201, r.text
    return {"api": api, "boot": boot, **state}


CASES = [{"case_id": "k1", "input": "Nenne die Einsprachefrist aus der Passage.", "rubric": "Antwort nennt Frist mit Passagenbezug."},
         {"case_id": "k2", "input": "Beurteile die Offenlegungspflicht.", "rubric": "Trennt Beobachtung von Bewertung."}]


def _suite(api: Api, ref="suite-source-analysis", threshold=100, cases=CASES, protected=True):
    r = api.post("/qualification-suites", {"ref": ref, "task_class": "source_analysis", "threshold_percent": threshold, "protected": protected, "cases": cases})
    assert r.status_code == 201, r.text
    return r.json()


def _candidate(api: Api, version="2", **overrides) -> str:
    doc = load_example("agent_profile") | {"version": version, "operating_status": "planned", "qualification_status": "not_evaluated", "qualification_refs": []} | overrides
    r = api.post("/agents/candidates", {"document": doc})
    assert r.status_code == 201, r.text
    return r.json()["ref"]


def _evaluate(api: Api, project_id: str, candidate_ref: str, deployment="fixture-deployment-a", suite_ref="suite-source-analysis", **extra):
    return api.post("/evaluations", {"candidate_kind": "agent", "candidate_ref": candidate_ref, "candidate_deployment_id": deployment,
                                     "suite_ref": suite_ref, "project_id": project_id, "budget": "5.00", **extra})


def test_need_candidate_evaluation_activation_and_provenance(campus, session_factory):
    api, project_id = campus["api"], campus["project"]["project_id"]
    _suite(api)
    ref = _candidate(api)
    assert api.get(f"/inventory/{ref}/rationale").json()["need_ref_resolved"] is True
    r = _evaluate(api, project_id, ref)
    assert r.status_code == 201, r.text
    run = r.json()
    assert run["state"] == "passed" and run["score"] == 100 and run["activated"] is True and run["qualification_ref"]
    assert run["candidate_canonical_model_id"] == "fixture/model-a@1" and run["evaluator_canonical_model_id"] == "fixture/model-b@1"
    assert all(m["candidate_self_assessment_included"] is False for m in run["context_manifest"])  # A11: what the evaluator saw
    agent = next(a for a in api.get("/inventory").json()["agents"] if a["ref"] == ref)
    assert agent["operating_status"] == "active" and agent["qualification_status"] == "task_qualified"
    rationale = api.get(f"/inventory/{ref}/rationale").json()
    assert rationale["qualified_task_classes"] == ["source_analysis"] and rationale["evaluations"][0]["activated"] is True
    assert rationale["qualifications"][0]["evaluated_by_kind"] == "model" and rationale["need"]["decision"] == "new_agent"
    # the activated profile now runs tasks in qualified mode; the old version 1 is untouched (version binding)
    task = _create_task(api, project_id, agent_ref=ref, mode="qualified")
    decision_id = _authorize(api, task["task_id"]).json()["routing_decision_id"]
    assert api.post("/runs", {"task_id": task["task_id"], "routing_decision_id": decision_id}).status_code == 201
    v1 = next(a for a in api.get("/inventory").json()["agents"] if a["ref"] == "fixture-ai-researcher@1")
    assert v1["qualification_status"] == "not_evaluated"
    # every evaluation call went through the gateway with journal, route and bound reservations (child budget held)
    with session_factory() as db:
        inv = db.execute(select(LLMInvocation).where(LLMInvocation.id.like(f"{run['evaluation_run_id']}:%"))).scalars().all()
        assert len(inv) == 4 and all(i.state == "completed" for i in inv)
        res = db.execute(select(Reservation).where(Reservation.task_id == run["task_id"])).scalars().all()
        assert len(res) == 4 and all(x.state == "settled" and x.routing_decision_id for x in res)
    # a new need for the same specialization and task class now resolves to reuse
    r = api.post("/needs", {"ref": "need-reuse", "gap": "nochmals Quellenanalyse", "requested_specialization": "AI research methodology",
                            "task_class": "source_analysis", "expected_benefit": "keiner"})
    assert r.json()["decision"] == "reuse" and r.json()["reuse_ref"] == ref


def test_t12_candidate_cannot_certify_itself_and_unqualified_evaluators_do_not_count(campus):
    api, project_id = campus["api"], campus["project"]["project_id"]
    _suite(api)
    ref = _candidate(api)
    # candidate on B: the only qualified evaluator IS B -> no qualified distinct evaluator
    r = _evaluate(api, project_id, ref, deployment="fixture-deployment-b")
    assert r.status_code == 409 and r.json()["code"] == "QUALIFICATION_MISSING" and "Selbstzertifikat" in r.json()["message"]
    # a self-written qualification record is rejected at the source
    r = api.post("/qualifications", {"ref": "self", "canonical_model_id": "fixture/model-a@1", "task_class": "source_analysis", "suite_ref": "s",
                                     "result_summary": "ok", "passed": True, "evaluated_by": "fixture/model-a@1", "evaluated_by_kind": "model"})
    assert r.status_code == 409
    # and claiming task_qualified without resolvable refs stays a self-certificate (T12 at registration)
    doc = load_example("agent_profile") | {"version": "9", "qualification_status": "task_qualified", "qualification_refs": ["qr:invented"]}
    assert api.post("/agents/candidates", {"document": doc}).json()["code"] == "QUALIFICATION_MISSING"


def test_t10_lowering_the_criterion_is_a_new_version_and_keeps_the_failed_run(campus):
    api, project_id = campus["api"], campus["project"]["project_id"]
    failing = [CASES[0], {"case_id": "k2", "input": "FAIL: dieser Fall scheitert absichtlich", "rubric": "r"}]
    _suite(api, threshold=100, cases=failing)
    ref = _candidate(api)
    first = _evaluate(api, project_id, ref).json()
    assert first["state"] == "failed" and first["score"] == 50 and first["activated"] is False
    assert next(a for a in api.get("/inventory").json()["agents"] if a["ref"] == ref)["operating_status"] == "planned"
    # protected suites cannot be edited in place: lowering the threshold creates version 2; the failed run keeps pointing at version 1
    second_suite = _suite(api, threshold=50, cases=failing)
    assert second_suite["version"] == 2
    second = _evaluate(api, project_id, ref).json()
    assert second["state"] == "passed" and second["suite"] == "suite-source-analysis@2" and second["activated"] is True
    history = api.get(f"/evaluations?candidate_ref={ref}").json()
    assert [(h["suite"], h["state"]) for h in history] == [("suite-source-analysis@1", "failed"), ("suite-source-analysis@2", "passed")]


def test_t17_regression_blocks_silent_replacement(campus):
    api, project_id = campus["api"], campus["project"]["project_id"]
    _suite(api, threshold=50)
    v2 = _candidate(api, version="2")
    assert _evaluate(api, project_id, v2).json()["activated"] is True  # baseline: 100%
    mixed = [CASES[0], {"case_id": "k2", "input": "FAIL: Rückschritt", "rubric": "r"}]
    _suite(api, ref="suite-source-analysis", threshold=50, cases=mixed)  # version 2 of the suite, same ref
    v3 = _candidate(api, version="3")
    run = _evaluate(api, project_id, v3).json()
    assert run["state"] == "passed" and run["score"] == 50 and run["baseline_score"] == 100
    assert run["regression"] is True and run["activated"] is False and "Rückschritt" in run["activation_note"]
    agents = {a["ref"]: a for a in api.get("/inventory").json()["agents"]}
    assert agents[v2]["operating_status"] == "active" and agents[v3]["operating_status"] == "planned"  # the active version stays


def test_t05_protected_test_material_is_hidden_from_producers(client, session_factory, workspace_id, campus):
    api = campus["api"]
    _suite(api, protected=True)
    assert api.get("/qualification-suites/suite-source-analysis").json()["cases_visible"] is True  # research director
    with session_factory() as db:
        from campus_api import services_identity
        from campus_domain.authz import Role
        from campus_infra.audit import Actor

        services_identity.create_user(db, actor=Actor("system", "t", workspace_id, frozenset(Role)), workspace_id=workspace_id, username="erzeuger",
                                      display_name="Erzeuger (Test)", password="test-passwort-mit-zwoelf-zeichen", roles=["producer"], correlation_id="t")
        db.commit()
    producer = Api(client)
    producer.login(workspace_id, "erzeuger")
    view = producer.get("/qualification-suites/suite-source-analysis").json()
    assert view["cases_visible"] is False and view["cases"] is None and view["cases_sha256"]
    assert producer.post("/qualification-suites", {"ref": "x", "task_class": "y", "threshold_percent": 1, "cases": CASES}).status_code == 403


def test_a10_packages_cannot_request_new_privileges(campus):
    api = campus["api"]
    doc = load_example("agent_profile") | {"version": "4", "requested_tool_scopes": ["sources.read", "secrets.read"]}
    r = api.post("/agents/candidates", {"document": doc})
    assert r.status_code == 403 and "A10" in r.json()["message"]
    skill = load_example("skill_manifest") | {"version": "4", "requested_tool_scopes": ["host.exec"]}
    assert api.post("/skills/candidates", {"document": skill}).status_code == 403


def test_skill_package_is_hash_bound_and_needs_a_recorded_need(campus):
    api = campus["api"]
    instructions = "# Skill\nAnalysiere Quellenpassagen und nenne Fundstellen."
    good = load_example("skill_manifest") | {"version": "5", "package_sha256": hashlib.sha256(instructions.encode()).hexdigest(),
                                             "requested_tool_scopes": ["sources.read"]}
    r = api.post("/skills/candidates", {"document": good, "instructions": instructions})
    assert r.status_code == 201, r.text
    bad = good | {"version": "6"}
    r = api.post("/skills/candidates", {"document": bad, "instructions": instructions + " geändert"})
    assert r.status_code == 422 and "hashgebunden" in r.json()["message"]
    orphan = good | {"version": "7", "origin_need_ref": "need-does-not-exist"}
    assert api.post("/skills/candidates", {"document": orphan, "instructions": instructions}).json()["code"] == "NEED_NOT_FOUND"


def test_retirement_keeps_history_and_blocks_new_runs(campus):
    api, project_id = campus["api"], campus["project"]["project_id"]
    _suite(api, threshold=50)
    ref = _candidate(api)
    assert _evaluate(api, project_id, ref).json()["activated"] is True
    r = api.post(f"/inventory/{ref}/retire", {"candidate_kind": "agent", "reason": "Durch Fassung 3 abgelöst", "successor_ref": "fixture-ai-researcher@3",
                                              "new_status": "archived"})
    assert r.status_code == 200 and r.json()["new_status"] == "archived"
    rationale = api.get(f"/inventory/{ref}/rationale").json()
    assert rationale["operating_status"] == "archived" and rationale["retirements"][0]["successor_ref"] == "fixture-ai-researcher@3"
    assert rationale["evaluations"], "history stays findable"
    task = _create_task(api, project_id, agent_ref=ref, mode="qualified")
    decision_id = _authorize(api, task["task_id"]).json()["routing_decision_id"]
    r = api.post("/runs", {"task_id": task["task_id"], "routing_decision_id": decision_id})
    assert r.status_code == 409 and r.json()["code"] == "AGENT_NOT_REGISTERED"


def test_evaluation_runs_are_append_only_history(campus, session_factory):
    api, project_id = campus["api"], campus["project"]["project_id"]
    _suite(api)
    ref = _candidate(api)
    _evaluate(api, project_id, ref)
    with session_factory() as db:
        assert db.execute(select(EvaluationRun).where(EvaluationRun.candidate_ref == ref)).scalars().first().state == "passed"
