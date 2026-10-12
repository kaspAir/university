"""Experiment rules (chapter 13, T04, A10): start conditions, acceptance binding, evidence validity."""
from __future__ import annotations

import pytest

from campus_domain.claims import classify
from campus_domain.errors import CampusError, ErrorCode
from campus_domain.experiments import ExperimentStartContext, acceptance_matches, check_acceptable, check_experiment_start, evidence_valid
from campus_domain.statuses import MandateState


def _ctx(**kw) -> ExperimentStartContext:
    base = {"workspace_stopped": False, "mandate_state": MandateState.APPROVED, "task_hold_reason": None, "required_tool_scopes": ("sandbox.run",),
            "runner_kind": "docker", "acceptance_present": True, "capability_level": "development_mock"}
    base.update(kw)
    return ExperimentStartContext(**base)


def _code(ctx) -> ErrorCode:
    with pytest.raises(CampusError) as e:
        check_experiment_start(ctx)
    return e.value.code


def test_start_conditions() -> None:
    check_experiment_start(_ctx())
    assert _code(_ctx(workspace_stopped=True)) == ErrorCode.WORKSPACE_STOPPED
    assert _code(_ctx(mandate_state=MandateState.DRAFT)) == ErrorCode.MANDATE_NOT_APPROVED
    assert _code(_ctx(task_hold_reason="Schutzverweigerung")) == ErrorCode.TASK_HELD
    assert _code(_ctx(required_tool_scopes=("sources.read",))) == ErrorCode.FORBIDDEN, "a run is not free shell access"
    assert _code(_ctx(runner_kind=None)) == ErrorCode.CAPABILITY_UNSUPPORTED, "no runner: capability off, no substitute"
    assert _code(_ctx(acceptance_present=False)) == ErrorCode.CAPABILITY_UNSUPPORTED, "a real runner needs an acceptance"
    check_experiment_start(_ctx(runner_kind="mock", acceptance_present=False))
    assert _code(_ctx(runner_kind="mock", acceptance_present=False, capability_level="restricted_research")) == ErrorCode.CAPABILITY_UNSUPPORTED


def test_evidence_requires_a_measured_completed_run() -> None:
    assert evidence_valid(measured=True, state="completed")
    assert not evidence_valid(measured=False, state="completed"), "a mock result is a fixture"
    for state in ("failed", "timed_out", "oom_killed", "output_limit_exceeded", "running", "runner_unavailable"):
        assert not evidence_valid(measured=True, state=state), state


def test_classification_flags_unmeasured_evidence_and_stores_a_hypothesis() -> None:
    cls = classify("computation", 0, 0, unmeasured_evidence_count=1)
    assert cls.stored_kind == "hypothesis" and "EVIDENCE_NOT_MEASURED" in cls.flags and "EVIDENCE_MISSING" in cls.flags
    good = classify("computation", 1, 0, 0)
    assert good.stored_kind == "computation" and good.flags == ()


def test_acceptance_binds_runner_image_and_host() -> None:
    acc = {"runner_kind": "docker", "image": "python@sha256:" + "a" * 64, "host_fingerprint": "h1", "revoked_at": None}
    assert acceptance_matches(acc, runner_kind="docker", image=acc["image"], host_fingerprint="h1")
    assert not acceptance_matches(acc, runner_kind="docker", image="python@sha256:" + "b" * 64, host_fingerprint="h1"), "other image"
    assert not acceptance_matches(acc, runner_kind="docker", image=acc["image"], host_fingerprint="h2"), "other host"
    assert not acceptance_matches(dict(acc, revoked_at="now"), runner_kind="docker", image=acc["image"], host_fingerprint="h1")
    assert not acceptance_matches(None, runner_kind="docker", image=acc["image"], host_fingerprint="h1")


def _report(**kw) -> dict:
    base = {"runner_kind": "docker", "image": "python@sha256:" + "a" * 64, "host_fingerprint": "h1",
            "checks": [{"name": "network_blocked", "passed": True, "observed": "x"}, {"name": "rootfs_read_only", "passed": True, "observed": "x"}]}
    base.update(kw)
    return base


def test_only_a_passed_docker_report_of_this_host_is_acceptable() -> None:
    img = "python@sha256:" + "a" * 64
    check_acceptable(_report(), configured_runner="docker", configured_image=img, current_fingerprint="h1")
    with pytest.raises(CampusError, match="Mock"):
        check_acceptable(_report(runner_kind="mock"), configured_runner="mock", configured_image=img, current_fingerprint="h1")
    with pytest.raises(CampusError, match="anderen Host"):
        check_acceptable(_report(), configured_runner="docker", configured_image=img, current_fingerprint="h2")
    with pytest.raises(CampusError, match="anderen Runner oder Image"):
        check_acceptable(_report(), configured_runner="docker", configured_image="python@sha256:" + "b" * 64, current_fingerprint="h1")
    with pytest.raises(CampusError, match="nicht bestanden") as e:
        check_acceptable(_report(checks=[{"name": "network_blocked", "passed": False, "observed": "offen"}]), configured_runner="docker",
                         configured_image=img, current_fingerprint="h1")
    assert e.value.details["failed_checks"] == ["network_blocked"]
    with pytest.raises(CampusError):
        check_acceptable(_report(checks=[]), configured_runner="docker", configured_image=img, current_fingerprint="h1")
