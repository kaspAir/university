"""The research task workflow: fetch -> route -> generate -> review -> dossier (chapter 6, steps 2–8 for one task).

Deterministic code only. Every external effect is an activity; the workflow keeps stage, stop signal and the
documented end reason. A stop is a reason, not a result (Art. 12).
"""
from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    from .activities import (
        DiscoverArgs,
        DossierArgs,
        FlowInput,
        ReviewArgs,
        RunArgs,
        authorize_route,
        build_dossier,
        discover_sources,
        fetch_sources,
        review_run,
        run_generation,
    )

RETRY = RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=1), backoff_coefficient=2.0)
GENERATION_RETRY = RetryPolicy(maximum_attempts=1)  # a failed generation is not retried blindly; the workflow replans through the core
STEP_TIMEOUT = timedelta(minutes=10)
# model steps stream their answers; this only catches a stuck activity, it never cuts a long answer short
MODEL_STEP_TIMEOUT = timedelta(hours=1)
MAX_GENERATION_ATTEMPTS = 3


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
        run: dict = {}
        attempts: list[dict] = []
        discoveries: list[dict] = []
        gaps: list[str] = []
        rounds = 0
        while True:
            rounds += 1
            for _replan in range(MAX_GENERATION_ATTEMPTS):
                self._stage = "routing"
                decision_id = await workflow.execute_activity(authorize_route, flow, start_to_close_timeout=STEP_TIMEOUT, retry_policy=RETRY)
                self._result["routing_decision_id"] = decision_id
                if stopped := self._check_stop():
                    return stopped
                if flow.discover and not flow.revise_run_id:
                    # the campus finds its own sources under the authorized route; round 2 works from the gaps the previous round left
                    self._stage = "discovering_sources"
                    disc = await workflow.execute_activity(
                        discover_sources,
                        DiscoverArgs(flow.workspace_id, flow.task_id, decision_id, rounds, gaps, flow.max_queries, flow.max_sources, flow.correlation_id),
                        start_to_close_timeout=MODEL_STEP_TIMEOUT, retry_policy=GENERATION_RETRY)
                    discoveries.append(disc)
                    self._result["discovery"] = discoveries
                    if disc.get("skipped") and not self._result.get("source_ids") and not any(d.get("fetched") for d in discoveries):
                        # discovery was asked for but could not run, and nothing was entered by hand: a generation without any source
                        # would only produce hypotheses for money. The flow ends visibly; the page offers a new flow once the cause is fixed.
                        self._stage = "ended_without_sources"
                        raise ApplicationError(f"Keine Quellen: Erschliessung übersprungen ({disc.get('skipped')}: {disc.get('message', '')}) und "
                                               "keine Startquellen eingegeben; keine Erzeugung ohne Quellenbasis", non_retryable=True)
                    if stopped := self._check_stop():
                        return stopped
                self._stage = "generating"
                run = await workflow.execute_activity(run_generation, RunArgs(flow.workspace_id, flow.task_id, decision_id, flow.correlation_id,
                                                                              flow.revise_run_id, flow.revision_note),
                                                      start_to_close_timeout=MODEL_STEP_TIMEOUT, retry_policy=GENERATION_RETRY)
                attempts.append({"routing_decision_id": decision_id, "state": run.get("state"), "end_reason": run.get("end_reason")})
                self._result["run"], self._result["attempts"] = run, attempts
                if run.get("state") == "finished" or run.get("end_reason") != "provider_error":
                    break
                # provider failure inside the generation step: replan under the same constraints (chapter 10); the core excludes the failed deployment
            if run.get("state") != "finished":
                self._stage = "ended_without_result"
                return {**self._result, "end_reason": run.get("end_reason", "unknown")}
            if stopped := self._check_stop():
                return stopped
            self._stage = "reviewing"
            review = await workflow.execute_activity(
                review_run, ReviewArgs(flow.workspace_id, run["run_attempt_id"], flow.correlation_id), start_to_close_timeout=MODEL_STEP_TIMEOUT,
                retry_policy=RETRY, task_queue=flow.review_task_queue or workflow.info().task_queue,  # separate reviewer instances (Art. 17)
            )
            self._result["review"] = review
            # limited re-search: the gaps the generation declared, the reviewer's findings and the selection's follow-up queries drive at most
            # one more round, within the budget; a skipped discovery (no service, no budget) ends the loop
            last = discoveries[-1] if discoveries else {}
            gaps = [*run.get("open_questions", []), *[f.get("subject", "") for f in review.get("findings", [])], *last.get("follow_up_queries", [])]
            gaps = [g for g in gaps if g]
            if flow.revise_run_id or not (flow.discover and rounds < flow.max_rounds and gaps and discoveries and not last.get("skipped")):
                break
        self._stage = "building_dossier"
        self._result["dossier"] = await workflow.execute_activity(build_dossier, DossierArgs(flow.workspace_id, flow.project_id, flow.correlation_id),
                                                                  start_to_close_timeout=STEP_TIMEOUT, retry_policy=RETRY)
        self._stage = "completed"
        return {**self._result, "end_reason": "result", "rounds": rounds}
