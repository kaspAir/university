"""S5: the controlled pilot through the API with mock models. Proves the machinery (pre-registration, gate, shared budget, blinded independent
evaluation, report from data); it proves nothing about scientific added value, and says so in the report."""
from __future__ import annotations

import copy

import pytest

from campus_api import services_identity
from campus_domain.authz import Role
from campus_infra.audit import Actor
from tests.conftest import build_catalog_document
from tests.integration.test_s0_flow import Api, _authorize, _create_task, bootstrap, govern

pytestmark = pytest.mark.integration

CASES = [{"case_id": "p1", "input": "Trägt die Aussage ihre Geltungsbedingungen?", "rubric": "Geltung explizit, keine erfundenen Messwerte"},
         {"case_id": "p2", "input": "Ist der epistemische Status angemessen?", "rubric": "Hypothesen als Hypothesen, Beobachtungen belegt"}]
JUNIT_OK = """<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest" tests="3">
<testcase classname="tests.integration" name="test_t04_measurement" time="0.1"/>
<testcase classname="tests.integration" name="test_t05_material" time="0.1"/>
<testcase classname="tests.integration" name="test_t12_self_certificate" time="0.1"/></testsuite></testsuites>"""
DECISION = {"option": "accept", "rationale": "Protokoll vollständig, Tor bestanden", "alternatives_presented": ["zurückweisen", "begrenzt annehmen"],
            "remaining_objections": ["kleiner erster Lauf"], "consequences": "Machbarkeitsprüfung mit gemeinsamem Budget", "conditions": []}


def protocol(**over) -> dict:
    doc = {"question": "Welche zusätzliche Aussagequalität erzielt institutionell getrennte Prüfung?", "stage": "reproduction",
           "reproduction": {"source_ref": "fixture-source-1", "expected_outputs": "synthetische Kennzahl", "tolerances": "keine Abweichung"},
           "arms": [{"condition": c} for c in ("single_agent_self_check", "producer_reviewer_pair", "cross_institute")], "repetitions": 2,
           "evaluation": {"suite_ref": "pilot-eval", "rule": {"aggregation": "mean_score", "threshold_percent": 60}},
           "budget": {"total": "40.00", "evaluation_reserve": "8.00"}, "data_access": "nur synthetische Fixtures", "human_interventions_allowed": [],
           "required_tests": ["T04", "T05", "T12"], "capabilities_in_scope": ["workflow"]}
    doc.update(over)
    return doc


@pytest.fixture
def campus(client, session_factory, workspace_id):
    boot = bootstrap(session_factory, workspace_id)
    api = Api(client)
    api.login(workspace_id, "leitung")
    state = govern(api, boot)
    for ref, task_class, role in [("boot-evaluator-b", "evaluation", "evaluator"), ("boot-reviewer-b", "source_analysis", "reviewer")]:
        assert api.post("/qualifications", {"ref": ref, "canonical_model_id": "fixture/model-b@1", "task_class": task_class, "suite_ref": "bootstrap",
                                            "result_summary": "Bootstrap", "passed": True, "evaluated_by": "forschungsleitung", "evaluated_by_kind": "human",
                                            "kind": "bootstrap", "role": role}).status_code == 201
    assert api.post("/qualification-suites", {"ref": "pilot-eval", "task_class": "source_analysis", "threshold_percent": 60, "protected": True,
                                               "evaluator_task_class": "evaluation", "cases": CASES}).status_code == 201
    return {"api": api, "boot": boot, **state}


def _run(api: Api, project_id: str, review: bool) -> str:
    task = _create_task(api, project_id)
    decision_id = _authorize(api, task["task_id"]).json()["routing_decision_id"]
    run = api.post("/runs", {"task_id": task["task_id"], "routing_decision_id": decision_id})
    assert run.status_code == 201, run.text
    if review:
        assert api.post(f"/runs/{run.json()['run_attempt_id']}/review").status_code == 201
    return task["task_id"]


def _add_third_model(api: Api) -> None:
    """A third canonical model F (own provider) so that an evaluator distinct from producer A and reviewer B exists."""
    assert api.post("/processing-profiles", {"ref": "profile-fixture-provider-f", "provider_id": "fixture-provider-f", "region": "test",
                                             "allowed_data_labels": ["public"], "note": "Fixture"}).status_code == 201
    doc = build_catalog_document() | {"revision": "with-f"}
    f = copy.deepcopy(doc["deployments"][1])
    f.update({"id": "fixture-deployment-f", "provider_id": "fixture-provider-f", "provider_model_id": "fixture-native-f", "canonical_model_id": "fixture/model-f@1",
              "model_family": "fixture-family-f", "qualification_refs": [], "processing_profile_ref": "profile-fixture-provider-f"})
    doc["deployments"].append(f)
    catalog_id = api.post("/model-catalog/drafts", {"document": doc}, method="PUT").json()["catalog_id"]
    assert api.post(f"/model-catalog/{catalog_id}/activate").status_code == 200
    assert api.post("/qualifications", {"ref": "boot-evaluator-f", "canonical_model_id": "fixture/model-f@1", "task_class": "evaluation", "suite_ref": "bootstrap",
                                        "result_summary": "Bootstrap", "passed": True, "evaluated_by": "forschungsleitung", "evaluated_by_kind": "human",
                                        "kind": "bootstrap", "role": "evaluator"}).status_code == 201


def test_pilot_end_to_end_with_mock_models(campus, session_factory, workspace_id):
    """Proves: protocol pre-registered with hash; release gate collects failures (no evidence, no integrity officer) and passes only with imported
    evidence for the required tests; arm projects hang under the shared pilot budget; tasks outside the arms cannot be attached; without a third
    model the independent evaluation stays open (no silent seal); with a third model it runs blinded; the report lists facts per condition,
    differences, the pre-registered rule, platform evidence separately and the feasibility flag; the frozen report goes through the human
    approval path; the follow-up stage needs a closed reproduction. Not proven: anything about research quality (mock models)."""
    api, mandate = campus["api"], campus["mandate"]
    r = api.post("/pilots", {"mandate_version_id": mandate["mandate_version_id"], "ref": "pilot-1", "protocol": protocol()})
    assert r.status_code == 201, r.text
    pilot = r.json()
    assert pilot["state"] == "draft" and len(pilot["preregistration_sha256"]) == 64 and {a["condition"] for a in pilot["arms"]} == {
        "single_agent_self_check", "producer_reviewer_pair", "cross_institute"}
    pid = pilot["pilot_id"]
    # protocol gaps are named at creation
    bad = api.post("/pilots", {"mandate_version_id": mandate["mandate_version_id"], "ref": "pilot-bad", "protocol": protocol(repetitions=0)})
    assert bad.status_code == 422 and "repetitions" in bad.json()["message"]
    # gate: no integrity officer, no platform evidence
    r = api.post(f"/pilots/{pid}/release", DECISION)
    assert r.status_code == 422, r.text
    failures = r.json()["details"]["failures"]
    assert any("Integrität" in f for f in failures) and any("Testnachweis" in f for f in failures), failures
    with session_factory() as db:
        services_identity.create_user(db, actor=Actor("system", "t", workspace_id, frozenset(Role)), workspace_id=workspace_id, username="integritaet",
                                      display_name="Integrität (Test)", password="test-passwort-mit-zwoelf-zeichen", roles=["integrity_officer"], correlation_id="t")
        db.commit()
    bad_evidence = JUNIT_OK.replace('name="test_t05_material" time="0.1"/>', 'name="test_t05_material" time="0.1"><failure message="x">boom</failure></testcase>')
    assert api.post("/platform-evidence", {"build_sha": "abc1234def", "source": "jenkins:test#1", "junit_xml": bad_evidence}).status_code == 201
    r = api.post(f"/pilots/{pid}/release", DECISION | {"current_build_sha": "abc1234def"})
    assert r.status_code == 422 and any("nicht bestanden: T05" in f for f in r.json()["details"]["failures"]), r.text
    assert api.post("/platform-evidence", {"build_sha": "abc1234def", "source": "jenkins:test#2", "junit_xml": JUNIT_OK}).status_code == 201
    r = api.post(f"/pilots/{pid}/release", DECISION | {"current_build_sha": "abc1234def"})
    assert r.status_code == 200, r.text
    pilot = r.json()
    assert pilot["state"] == "registered" and pilot["decision_id"] and pilot["evidence_id"] and pilot["self_control"] is False
    # protocol is frozen: a change is a new version
    v2 = api.post("/pilots", {"mandate_version_id": mandate["mandate_version_id"], "ref": "pilot-1", "protocol": protocol(repetitions=3)}).json()
    assert v2["version"] == 2 and v2["preregistration_sha256"] != pilot["preregistration_sha256"]
    # arms: projects under the shared budget, tasks and runs
    institutes = api.get("/institutes").json()
    arm_projects = {}
    arm_accounts = []
    for condition, idx in (("single_agent_self_check", [0]), ("producer_reviewer_pair", [1]), ("cross_institute", [2, 3])):
        arm_projects[condition] = []
        for i in idx:
            r = api.post(f"/pilots/{pid}/arm-projects", {"condition": condition, "institute_id": institutes[i]["institute_id"], "title": f"Arm {condition} {i}",
                                                          "budget": "5.00"})
            assert r.status_code == 201, r.text
            arm_projects[condition].append(r.json()["project_id"])
            arm_accounts.append(r.json()["budget_account_id"])
    tree = {a["id"]: a for a in api.get("/budget/tree").json()}
    assert all(tree[acc]["parent_id"] == pilot["budget_account_id"] for acc in arm_accounts), "every arm project is budgeted from the shared pilot budget"
    tasks = {"single_agent_self_check": _run(api, arm_projects["single_agent_self_check"][0], review=False),
             "producer_reviewer_pair": _run(api, arm_projects["producer_reviewer_pair"][0], review=True),
             "cross_institute": _run(api, arm_projects["cross_institute"][1], review=True)}
    for tid in tasks.values():
        assert api.post(f"/pilots/{pid}/tasks", {"task_id": tid, "repetition": 1}).status_code == 201
    outside = _create_task(api, campus["project"]["project_id"])
    r = api.post(f"/pilots/{pid}/tasks", {"task_id": outside["task_id"], "repetition": 1})
    assert r.status_code == 422 and "gemeinsamen Budgets" in r.json()["message"]
    assert api.post(f"/pilots/{pid}/tasks", {"task_id": tasks["cross_institute"], "repetition": 5}).status_code in (409, 422)
    # independent evaluation: A and B are used in the arms, so no distinct evaluator exists yet
    r = api.post(f"/pilots/{pid}/evaluations", {"task_id": tasks["producer_reviewer_pair"]})
    assert r.status_code == 409 and r.json()["code"] == "QUALIFICATION_MISSING", r.text
    evs = api.get(f"/pilots/{pid}/evaluations").json()
    assert evs[0]["state"] == "open_no_independent_evaluator" and set(evs[0]["excluded_canonical_model_ids"]) == {"fixture/model-a@1", "fixture/model-b@1"}
    _add_third_model(api)
    for condition, tid in tasks.items():
        r = api.post(f"/pilots/{pid}/evaluations", {"task_id": tid})
        assert r.status_code == 201, r.text
        ev = r.json()
        assert ev["blinded"] is True and ev["evaluator_canonical_model_id"] == "fixture/model-f@1" and ev["state"] == "completed" and ev["score"] == 100
        assert len(ev["context_manifest"]) == len(CASES) and all(len(b["sha256"]) == 64 for c in ev["context_manifest"] for b in c["blocks"])
        assert ev["condition"] == condition
    # report from data
    report = api.get(f"/pilots/{pid}/report").json()
    arms = {a["condition"]: a for a in report["arms"]}
    assert arms["producer_reviewer_pair"]["reviews"] == 1 and arms["single_agent_self_check"]["reviews"] == 0
    assert arms["producer_reviewer_pair"]["invocations"] >= 2 and arms["producer_reviewer_pair"]["cost_settled_minor"] > 0
    assert arms["cross_institute"]["institutes"] and len(arms["cross_institute"]["institutes"]) == 2
    assert arms["producer_reviewer_pair"]["rule_outcome"] == {"mean_score": 100.0, "meets_threshold": True, "evaluations": 1, "open": 1}
    assert report["flags"]["feasibility_only"] is True and "Machbarkeitsprüfung" in report["flags"]["statement"]
    assert "keinen wissenschaftlichen Mehrwert" in report["flags"]["added_value_claim"]
    assert report["platform_evidence"]["build_sha"] == "abc1234def" and report["scientific_evaluation"]["per_condition"]["cross_institute"]["evaluations"] == 1
    assert report["shared_budget"]["consumed_minor"] > 0 and report["pilot"]["decision_option"] == "accept"
    assert isinstance(report["differences"], list)
    # follow-up needs a closed reproduction; the report is frozen and approved by a human; then the pilot closes
    r = api.post("/pilots", {"mandate_version_id": mandate["mandate_version_id"], "ref": "pilot-2",
                             "protocol": protocol(stage="follow_up", predecessor_pilot_id=pid, reproduction=None)})
    assert r.status_code == 422 and "abgeschlossene Reproduktion" in r.json()["message"]
    assert api.post(f"/pilots/{pid}/close").status_code == 422, "no report, no close"
    frozen = api.post(f"/pilots/{pid}/report").json()
    assert frozen["kind"] == "pilot_report" and frozen["feasibility_only"] is True
    again = api.post(f"/pilots/{pid}/report").json()
    assert again["artifact_id"] == frozen["artifact_id"], "equal content, no new version"
    r = api.post(f"/artifacts/{frozen['artifact_id']}/approvals", {"expected_sha256": frozen["sha256"], **DECISION})
    assert r.status_code == 201, r.text
    assert api.post(f"/pilots/{pid}/close").json()["state"] == "closed"
    r = api.post("/pilots", {"mandate_version_id": mandate["mandate_version_id"], "ref": "pilot-2",
                             "protocol": protocol(stage="follow_up", predecessor_pilot_id=pid, reproduction=None)})
    assert r.status_code == 201, r.text


def test_producer_role_cannot_evaluate_or_release(campus, client, session_factory, workspace_id):
    """Proves (Art. 17): the producing role neither releases a pilot nor evaluates arms; evidence import is the operator's."""
    api, mandate = campus["api"], campus["mandate"]
    pid = api.post("/pilots", {"mandate_version_id": mandate["mandate_version_id"], "ref": "pilot-roles", "protocol": protocol()}).json()["pilot_id"]
    with session_factory() as db:
        services_identity.create_user(db, actor=Actor("system", "t", workspace_id, frozenset(Role)), workspace_id=workspace_id, username="erzeuger",
                                      display_name="Erzeuger (Test)", password="test-passwort-mit-zwoelf-zeichen", roles=["producer"], correlation_id="t")
        db.commit()
    producer = Api(client)
    producer.login(workspace_id, "erzeuger")
    assert producer.post(f"/pilots/{pid}/release", DECISION).status_code == 403
    assert producer.post(f"/pilots/{pid}/evaluations", {"task_id": "T-x"}).status_code == 403
    assert producer.post("/platform-evidence", {"build_sha": "abc1234def", "source": "x", "junit_xml": JUNIT_OK}).status_code == 403
    assert producer.post(f"/pilots/{pid}/report").status_code == 403
