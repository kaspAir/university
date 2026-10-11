"""The research task workflow: fetch -> route -> generate -> review -> dossier (chapter 6, steps 2–8 for one task).

Deterministic code only. Every external effect is an activity; the workflow keeps stage, stop signal and the
documented end reason. A stop is a reason, not a result (Art. 12).
"""
from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from .activities import (
        DossierArgs,
        FlowInput,
        ReviewArgs,
        RunArgs,
        authorize_route,
        build_dossier,
        fetch_sources,
        review_run,
        run_generation,
    )

RETRY = RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=1), backoff_coefficient=2.0)
STEP_TIMEOUT = timedelta(minutes=10)


@workflow.defn(name="ResearchTaskWorkflow")
class ResearchTaskWorkflow:
    def __init__(self) -> None:
        self._stage = "created"
        self._stop_reason: str | None = None
        self._result: dict = {}

    @workflow.signal
    def stop(self, reason: str) -> None:
        self._stop_reason = reason or "Stopp ohne Grund"

    @workflow.query
    def stage(self) -> str:
        return self._stage

    @workflow.query
    def partial_result(self) -> dict:
        return self._result

    def _check_stop(self) -> dict | None:
        if self._stop_reason is not None:
            self._stage = "stopped"
            return {**self._result, "end_reason": "stop", "stop_reason": self._stop_reason, "stage_reached": self._stage}
        return None

    @workflow.run
    async def run(self, flow: FlowInput) -> dict:
        self._stage = "fetching_sources"
        self._result["source_ids"] = await workflow.execute_activity(fetch_sources, flow, start_to_close_timeout=STEP_TIMEOUT, retry_policy=RETRY)
        if stopped := self._check_stop():
            return stopped
        self._stage = "routing"
        decision_id = await workflow.execute_activity(authorize_route, flow, start_to_close_timeout=STEP_TIMEOUT, retry_policy=RETRY)
        self._result["routing_decision_id"] = decision_id
        if stopped := self._check_stop():
            return stopped
        self._stage = "generating"
        run = await workflow.execute_activity(run_generation, RunArgs(flow.workspace_id, flow.task_id, decision_id, flow.correlation_id),
                                              start_to_close_timeout=STEP_TIMEOUT, retry_policy=RETRY)
        self._result["run"] = run
        if run.get("state") != "finished":
            self._stage = "ended_without_result"
            return {**self._result, "end_reason": run.get("end_reason", "unknown")}
        if stopped := self._check_stop():
            return stopped
        self._stage = "reviewing"
        self._result["review"] = await workflow.execute_activity(
            review_run, ReviewArgs(flow.workspace_id, run["run_attempt_id"], flow.correlation_id), start_to_close_timeout=STEP_TIMEOUT,
            retry_policy=RETRY, task_queue=flow.review_task_queue or workflow.info().task_queue,  # separate reviewer instances (Art. 17)
        )
        self._stage = "building_dossier"
        self._result["dossier"] = await workflow.execute_activity(build_dossier, DossierArgs(flow.workspace_id, flow.project_id, flow.correlation_id),
                                                                  start_to_close_timeout=STEP_TIMEOUT, retry_policy=RETRY)
        self._stage = "completed"
        return {**self._result, "end_reason": "result"}
