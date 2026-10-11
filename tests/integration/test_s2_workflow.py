"""Durable research workflow on Temporal (ADR-02): fetch -> route -> generate -> review -> dossier, with interruption.

Uses the Temporal dev server started by the SDK (downloaded on first use). Skipped, never faked, when it cannot start.
"""
from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select
from temporalio.client import WorkflowFailureError
from temporalio.worker import Worker

from campus_api import services_sources
from campus_infra.models import ArtifactVersion, ReviewReportRecord, RunAttempt
from campus_workers import activities
from campus_workers.activities import FlowInput
from campus_workers.workflows import ResearchTaskWorkflow
from tests.integration.conftest_fetch import make_fetcher
from tests.integration.test_s0_flow import Api, _create_task, bootstrap, govern

pytestmark = pytest.mark.integration
QUEUE = "campus-research-test"


class _ExternalEnv:
    """Minimal stand-in for WorkflowEnvironment when an external Temporal server is provided (CI sidecar)."""

    def __init__(self, client):
        self.client = client

    async def shutdown(self) -> None:
        return None


@pytest.fixture(scope="session")
def temporal_env():
    import os

    from temporalio.client import Client
    from temporalio.testing import WorkflowEnvironment

    loop = asyncio.new_event_loop()
    address = os.environ.get("CAMPUS_TEST_TEMPORAL_ADDRESS")
    try:
        if address:
            env = _ExternalEnv(loop.run_until_complete(Client.connect(address)))
        else:
            env = loop.run_until_complete(WorkflowEnvironment.start_local())
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Temporal nicht erreichbar/startbar ({address or 'lokaler Entwicklungsserver'}): {type(exc).__name__}: {exc}")
    yield loop, env
    loop.run_until_complete(env.shutdown())
    loop.close()


@pytest.fixture
def campus(client, session_factory, workspace_id, monkeypatch):
    monkeypatch.setattr(services_sources, "default_fetcher", make_fetcher())
    boot = bootstrap(session_factory, workspace_id)
    api = Api(client)
    api.login(workspace_id, "leitung")
    state = govern(api, boot)
    activities.configure(session_factory)
    return {"api": api, "boot": boot, **state}


def _worker(env, queue: str) -> Worker:
    return Worker(env.client, task_queue=queue, workflows=[ResearchTaskWorkflow], activities=activities.ALL_ACTIVITIES,
                  activity_executor=ThreadPoolExecutor(max_workers=4))


def test_workflow_runs_the_whole_task_and_results_are_registered(campus, temporal_env, session_factory):
    loop, env = temporal_env
    api, project_id, ws = campus["api"], campus["project"]["project_id"], campus["boot"]["workspace_id"]
    task = _create_task(api, project_id)
    flow = FlowInput(workspace_id=ws, project_id=project_id, task_id=task["task_id"], source_urls=["https://public.example/urteil"],
                     selection_mode="llm", correlation_id="wf-test")

    async def run():
        async with _worker(env, QUEUE):
            return await env.client.execute_workflow(ResearchTaskWorkflow.run, flow, id=f"task-{task['task_id']}", task_queue=QUEUE)

    result = loop.run_until_complete(run())
    assert result["end_reason"] == "result" and len(result["source_ids"]) == 1
    assert result["run"]["state"] == "finished" and result["run"]["claims_recorded"] == 4
    assert result["review"]["verdict"] == "no_objection" and result["dossier"]["version"] == 1
    dossier = api.get(f"/projects/{project_id}/dossier").json()
    assert dossier["sources"] and dossier["claims"] and dossier["reviews"] and dossier["status_axes"]["release"] == "draft"


def test_interruption_after_generation_loses_no_registered_result(campus, temporal_env, session_factory):
    """Worker dies after the generation activity completed; a fresh worker finishes the workflow. Exactly one run, one review."""
    loop, env = temporal_env
    api, project_id, ws = campus["api"], campus["project"]["project_id"], campus["boot"]["workspace_id"]
    task = _create_task(api, project_id)
    queue = QUEUE + "-interrupt"
    review_queue = queue + "-review"  # the reviewer runs as a separate worker instance (Art. 17); none is alive in phase one
    flow = FlowInput(workspace_id=ws, project_id=project_id, task_id=task["task_id"], source_urls=["https://public.example/plain"],
                     selection_mode="llm", correlation_id="wf-interrupt", review_task_queue=review_queue)

    async def phase_one():
        worker = _worker(env, queue)
        handle = await env.client.start_workflow(ResearchTaskWorkflow.run, flow, id=f"task-{task['task_id']}", task_queue=queue)
        async with worker:
            deadline = time.time() + 60
            while time.time() < deadline:
                with session_factory() as db:
                    done = db.execute(select(RunAttempt).where(RunAttempt.task_id == task["task_id"], RunAttempt.state == "finished")).first()
                if done:
                    break
                await asyncio.sleep(0.5)
            assert done, "generation did not finish in time"
        return handle  # worker shut down here: the workflow is mid-flight (review pending)

    handle = loop.run_until_complete(phase_one())

    async def phase_two():
        async with _worker(env, queue), _worker(env, review_queue):  # fresh workers: the completed generation is replayed, not redone
            return await handle.result()

    result = loop.run_until_complete(phase_two())
    assert result["end_reason"] == "result"
    with session_factory() as db:
        runs = db.execute(select(RunAttempt).where(RunAttempt.task_id == task["task_id"])).scalars().all()
        reviews = db.execute(select(ReviewReportRecord).where(ReviewReportRecord.task_id == task["task_id"])).scalars().all()
        artifacts = db.execute(select(ArtifactVersion).where(ArtifactVersion.task_id == task["task_id"])).scalars().all()
        assert len(runs) == 1 and len(reviews) == 1 and len(artifacts) == 1, "no duplicate run/review/artifact after the interruption"


def test_workflow_fails_typed_when_no_mandate_route_is_possible(campus, temporal_env):
    loop, env = temporal_env
    api, project_id, ws = campus["api"], campus["project"]["project_id"], campus["boot"]["workspace_id"]
    task = _create_task(api, project_id, routing_policy_ref="policy-that-is-not-active")
    flow = FlowInput(workspace_id=ws, project_id=project_id, task_id=task["task_id"], source_urls=[], selection_mode="llm")

    async def run():
        async with _worker(env, QUEUE + "-fail"):
            return await env.client.execute_workflow(ResearchTaskWorkflow.run, flow, id=f"task-{task['task_id']}", task_queue=QUEUE + "-fail")

    with pytest.raises(WorkflowFailureError) as excinfo:
        loop.run_until_complete(run())
    assert "POLICY_NOT_ACTIVE" in str(excinfo.value.cause.cause.type if excinfo.value.cause and excinfo.value.cause.cause else excinfo.value)


def _failure_types(exc: BaseException) -> set[str]:
    types = set()
    cause = exc
    while cause is not None:
        t = getattr(cause, "type", None)
        if t:
            types.add(str(t))
        cause = getattr(cause, "cause", None) or cause.__cause__
    return types


def test_background_jobs_obey_the_common_invocation_guard(campus, temporal_env, session_factory):
    """A stopped campus blocks background work the same way as API calls: the workflow fails typed, no adapter call happens."""
    from campus_infra.models import LLMInvocation

    loop, env = temporal_env
    api, project_id, ws = campus["api"], campus["project"]["project_id"], campus["boot"]["workspace_id"]
    task = _create_task(api, project_id)
    assert api.post("/workspace/stop", {"reason": "Stopp vor Hintergrundauftrag"}).status_code == 200
    flow = FlowInput(workspace_id=ws, project_id=project_id, task_id=task["task_id"], source_urls=[], selection_mode="llm")

    async def run():
        async with _worker(env, QUEUE + "-stopped"):
            return await env.client.execute_workflow(ResearchTaskWorkflow.run, flow, id=f"task-{task['task_id']}", task_queue=QUEUE + "-stopped")

    with pytest.raises(WorkflowFailureError) as excinfo:
        loop.run_until_complete(run())
    assert "WORKSPACE_STOPPED" in _failure_types(excinfo.value)
    with session_factory() as db:
        assert db.execute(select(LLMInvocation).where(LLMInvocation.workspace_id == ws)).first() is None


def test_activities_are_idempotent_on_replay(campus, session_factory):
    """A retried activity after a worker crash returns the registered result instead of creating a second run or review."""
    from campus_workers.activities import ReviewArgs, RunArgs, review_run, run_generation

    api, project_id, ws = campus["api"], campus["project"]["project_id"], campus["boot"]["workspace_id"]
    task = _create_task(api, project_id)
    from tests.integration.test_s0_flow import _authorize

    decision_id = _authorize(api, task["task_id"]).json()["routing_decision_id"]
    first = run_generation(RunArgs(ws, task["task_id"], decision_id))
    second = run_generation(RunArgs(ws, task["task_id"], decision_id))
    assert first["run_attempt_id"] == second["run_attempt_id"] and second.get("replayed") is True
    r1 = review_run(ReviewArgs(ws, first["run_attempt_id"]))
    r2 = review_run(ReviewArgs(ws, first["run_attempt_id"]))
    assert r1["review_report_id"] == r2["review_report_id"] and r2.get("replayed") is True
    with session_factory() as db:
        assert len(db.execute(select(RunAttempt).where(RunAttempt.task_id == task["task_id"])).scalars().all()) == 1
        assert len(db.execute(select(ReviewReportRecord).where(ReviewReportRecord.task_id == task["task_id"])).scalars().all()) == 1
