"""S4: isolated experiments through the API. With the mock runner (default in tests) nothing is executed and nothing is measured; with
CAMPUS_TEST_SANDBOX=1 a second app instance with the Docker runner proves the accepted path end to end."""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from campus_infra.models import AuditEvent, ExperimentRun
from tests.integration.test_s0_flow import Api, _authorize, _create_task, bootstrap, govern

pytestmark = pytest.mark.integration

PROGRAM = "import json\nopen('/work/out/result.json', 'w').write(json.dumps({'n': 42}))\nprint('alle Tests bestanden')\n"


@pytest.fixture
def campus(client, session_factory, workspace_id):
    boot = bootstrap(session_factory, workspace_id)
    api = Api(client)
    api.login(workspace_id, "leitung")
    return {"api": api, "boot": boot, **govern(api, boot)}


def _experiment(api: Api, task_id: str, program: str = PROGRAM, **extra):
    return api.post("/experiments", {"task_id": task_id, "program": program, "inputs": [{"name": "data.txt", "text": "hallo"}], **extra})


def test_mock_runner_results_are_fixtures_never_measured(campus, session_factory):
    """Proves: with the mock runner an experiment is recorded with runner_kind mock, measured false; the program is NOT executed (a program
    that would fail hard still yields the fixture result); stdout is stored as content; outputs are content-addressed. Proves nothing about isolation."""
    api, project_id = campus["api"], campus["project"]["project_id"]
    status = api.get("/sandbox/status").json()
    assert status["runner_kind"] == "mock" and status["measuring"] is False and status["accepted"] is False
    task = _create_task(api, project_id, required_tool_scopes=["sandbox.run"])
    r = _experiment(api, task["task_id"], program="import os\nos.system('rm -rf /')\nraise SystemExit(99)\n")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["runner_kind"] == "mock" and body["measured"] is False and body["completed"] is False and body["state"] == "completed"
    assert body["exit_code"] == 0 and "alle Tests bestanden" in body["stdout_excerpt"] and body["stdout_is_content_not_result"] is True
    assert body["outputs"] and len(body["outputs"][0]["sha256"]) == 64 and body["acceptance_id"] is None
    failed = _experiment(api, task["task_id"], program="# MOCK_FAIL\nprint(1)\n").json()
    assert failed["state"] == "failed" and failed["exit_code"] == 1
    with session_factory() as db:
        commands = [e.command for e in db.execute(select(AuditEvent).where(AuditEvent.object_id == body["experiment_run_id"])).scalars().all()]
        assert commands == ["experiment.run", "experiment.end"], "order committed before the run, outcome after it"
    assert api.get(f"/experiments/{body['experiment_run_id']}").json()["program_sha256"] == body["program_sha256"]
    assert len(api.get(f"/projects/{project_id}/experiments").json()) == 2


def test_task_without_sandbox_scope_cannot_run_experiments(campus):
    """Proves: the tool scope sandbox.run is required per task; a research task without it is refused before anything is prepared."""
    api, project_id = campus["api"], campus["project"]["project_id"]
    task = _create_task(api, project_id)
    r = _experiment(api, task["task_id"])
    assert r.status_code == 403 and r.json()["code"] == "FORBIDDEN" and "sandbox.run" in r.json()["message"]


def test_mock_probe_cannot_be_accepted(campus):
    """Proves: the probe of a mock runner fails by construction and the acceptance refuses it; an acceptance needs the measuring runner."""
    api = campus["api"]
    report = api.post("/sandbox/probe").json()
    assert report["runner_kind"] == "mock" and report["all_passed"] is False
    r = api.post("/sandbox/acceptances", {"report": report, "note": "Versuch"})
    assert r.status_code == 422 and "Mock" in r.json()["message"]
    assert api.get("/sandbox/acceptances").json() == []


def test_unmeasured_experiment_evidence_yields_a_flagged_hypothesis(campus):
    """Proves (T04, S4): a generation run that cites a mock experiment as evidence for a computation gets a hypothesis flagged
    EVIDENCE_NOT_MEASURED; the claim does not become a measured result because a fixture said so."""
    api, project_id = campus["api"], campus["project"]["project_id"]
    lab = _create_task(api, project_id, required_tool_scopes=["sandbox.run"])
    xid = _experiment(api, lab["task_id"]).json()["experiment_run_id"]
    gen = _create_task(api, project_id)
    decision_id = _authorize(api, gen["task_id"]).json()["routing_decision_id"]
    r = api.post("/runs", {"task_id": gen["task_id"], "routing_decision_id": decision_id})
    assert r.status_code == 201, r.text
    claims = api.get(f"/projects/{project_id}/claims").json()["claims"]
    cited = [c for c in claims if c["local_id"] == "x1"]
    assert cited, claims
    c = cited[0]
    assert c["declared_kind"] == "computation" and c["stored_kind"] == "hypothesis"
    assert "EVIDENCE_NOT_MEASURED" in c["flags"] and all(e["experiment_run_id"] != xid for e in c["evidence"]), "no evidence link to a fixture"


@pytest.fixture
def docker_client(app, workspace_id):
    """A second app instance with the Docker runner on the same database (only with CAMPUS_TEST_SANDBOX=1)."""
    if os.environ.get("CAMPUS_TEST_SANDBOX") != "1":
        pytest.skip("CAMPUS_TEST_SANDBOX=1 nicht gesetzt")
    from tests.sandbox.conftest import _digest_of

    image = os.environ.get("CAMPUS_TEST_SANDBOX_IMAGE") or _digest_of("python:3.12-slim")
    if not image:
        pytest.skip("kein Sandbox-Image")
    from campus_api.main import create_app
    from campus_api.settings import Settings

    saved = {k: os.environ.get(k) for k in ("CAMPUS_SANDBOX_RUNNER", "CAMPUS_SANDBOX_IMAGE")}
    os.environ["CAMPUS_SANDBOX_RUNNER"], os.environ["CAMPUS_SANDBOX_IMAGE"] = "docker", image
    try:
        settings = Settings.from_env()
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    with TestClient(create_app(settings)) as c:
        yield c


def test_docker_path_needs_probe_and_acceptance_then_measures(campus, docker_client, session_factory):
    """Proves on this host: without acceptance the Docker runner refuses (capability off); a passed probe can be accepted by the research
    director; then an experiment runs isolated, is measured and completed, its outputs are hashed; a generation run citing it stores a
    computation (not a hypothesis); after revocation the path is off again. Not proven: the target host."""
    ws, project_id = campus["boot"]["workspace_id"], campus["project"]["project_id"]
    api = Api(docker_client)
    api.login(ws, "leitung")
    assert api.get("/sandbox/status").json() == api.get("/sandbox/status").json()
    assert api.get("/sandbox/status").json()["accepted"] is False and api.get("/sandbox/status").json()["measuring"] is True
    task = _create_task(api, project_id, required_tool_scopes=["sandbox.run"])
    r = _experiment(api, task["task_id"])
    assert r.status_code == 409 and r.json()["code"] == "CAPABILITY_UNSUPPORTED" and "nicht abgenommen" in r.json()["message"], r.text
    report = api.post("/sandbox/probe").json()
    assert report["all_passed"], [c for c in report["checks"] if not c["passed"]]
    tampered = dict(report, host_fingerprint="0" * 64)
    assert api.post("/sandbox/acceptances", {"report": tampered, "note": "x"}).status_code == 422, "edited reports are refused"
    acc = api.post("/sandbox/acceptances", {"report": report, "note": "Entwicklungs-Host, Probe bestanden"}).json()
    assert acc["all_passed"] is True and api.get("/sandbox/status").json()["accepted"] is True
    run = _experiment(api, task["task_id"]).json()
    assert run["runner_kind"] == "docker" and run["measured"] is True and run["completed"] is True and run["exit_code"] == 0, run
    assert run["acceptance_id"] == acc["acceptance_id"] and {o["name"] for o in run["outputs"]} == {"result.json"}
    gen = _create_task(api, project_id)
    decision_id = _authorize(api, gen["task_id"]).json()["routing_decision_id"]
    assert api.post("/runs", {"task_id": gen["task_id"], "routing_decision_id": decision_id}).status_code == 201
    claims = api.get(f"/projects/{project_id}/claims").json()["claims"]
    c = next(c for c in claims if c["local_id"] == "x1")
    assert c["stored_kind"] == "computation" and "EVIDENCE_NOT_MEASURED" not in c["flags"]
    assert any(e["experiment_run_id"] == run["experiment_run_id"] for e in c["evidence"])
    assert api.post(f"/sandbox/acceptances/{acc['acceptance_id']}/revoke", {"reason": "Test"}).status_code == 200
    r = _experiment(api, task["task_id"])
    assert r.status_code == 409 and r.json()["code"] == "CAPABILITY_UNSUPPORTED"
    with session_factory() as db:
        rows = db.execute(select(ExperimentRun).where(ExperimentRun.task_id == task["task_id"])).scalars().all()
        assert {x.state for x in rows} == {"completed"} and all(x.measured for x in rows)
