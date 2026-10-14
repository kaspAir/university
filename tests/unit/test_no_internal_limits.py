"""No campus-internal bound may make a call, a fetch or an audit write fail (the person's standing rule, 14.10.2026). These tests pin the
places where a fixed width or time used to break a run."""
from __future__ import annotations

import inspect

from campus_gateway.adapters.anthropic_adapter import AnthropicAdapter
from campus_gateway.adapters.openai_adapter import OpenAIAdapter
from campus_infra.audit import _fit


def test_audit_identifiers_are_fitted_not_rejected():
    url = "https://example.org/" + "a" * 200
    short = _fit(url, 80)
    assert len(short) == 80 and short.startswith("https://example.org/") and _fit(url, 80) == short, "stable and within the column"
    assert _fit("kurz", 80) == "kurz" and _fit(None, 80) is None
    assert _fit(url + "x", 80) != short, "different values stay distinguishable"


def test_adapters_stream_and_wait_generously_between_chunks():
    for adapter in (AnthropicAdapter, OpenAIAdapter):
        assert inspect.signature(adapter.__init__).parameters["timeout_seconds"].default >= 900
    assert "messages.stream" in inspect.getsource(AnthropicAdapter.invoke)
    assert "stream=True" in inspect.getsource(OpenAIAdapter.invoke)


def test_workflow_model_steps_have_only_a_stuck_activity_bound():
    from campus_workers import workflows

    assert workflows.MODEL_STEP_TIMEOUT.total_seconds() >= 3600
    source = inspect.getsource(workflows.ResearchTaskWorkflow.run)
    for step in ("discover_sources", "run_generation", "review_run"):
        segment = source[source.index(step):][:400]
        assert "MODEL_STEP_TIMEOUT" in segment, step
