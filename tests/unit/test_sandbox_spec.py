"""Sandbox controller contracts without Docker: every job carries the hardening, results never derive success from stdout."""
from __future__ import annotations

import pytest
from campus_sandbox.docker_runner import REQUIRED_HARDENING, SANDBOX_ENV, docker_argv
from campus_sandbox.mock_runner import MockSandboxRunner
from campus_sandbox.probe import report_sha256, run_probe
from campus_sandbox.spec import IN_DIR, OUT_DIR, SandboxInput, SandboxJob, SandboxLimits, SandboxResult, SpecError

IMAGE = "python@sha256:" + "a" * 64


def _job(**kw) -> SandboxJob:
    base = {"job_id": "j1", "image": IMAGE, "argv": ("python", "-I", "/work/in/program.py"), "inputs": (SandboxInput("program.py", b"print(1)"),)}
    base.update(kw)
    return SandboxJob(**base)


def test_every_docker_run_carries_the_hardening_flags() -> None:
    argv = docker_argv(_job(), container_name="c", in_dir="/host/in", out_dir="/host/out")
    joined = " ".join(argv)
    for flag in REQUIRED_HARDENING:
        assert flag in argv, flag
    assert "--network none" in joined and "--read-only" in joined and "--cap-drop ALL" in joined and "no-new-privileges" in joined
    assert "--user 65534:65534" in joined, "never root inside"
    assert "--pids-limit 64" in joined and "--memory 256m" in joined and "--memory-swap 256m" in joined, "no swap escape"
    assert f"type=bind,source=/host/in,target={IN_DIR},readonly" in joined and f"type=bind,source=/host/out,target={OUT_DIR}" in joined
    assert "noexec,nosuid" in joined, "tmpfs is not an execution path"
    assert "--privileged" not in joined and "docker.sock" not in joined and "--cap-add" not in joined
    envs = [argv[i + 1] for i, a in enumerate(argv) if a == "--env"]
    assert envs == list(SANDBOX_ENV), "exactly the allowlisted environment, nothing from the host"
    assert argv[-4:] == [IMAGE, "python", "-I", "/work/in/program.py"]


@pytest.mark.parametrize("image", ["python:3.12-slim", "python@sha256:short", "python@md5:" + "a" * 64, "Python@sha256:" + "a" * 64])
def test_image_must_be_digest_pinned(image: str) -> None:
    with pytest.raises(SpecError):
        _job(image=image).validate()


@pytest.mark.parametrize("name", ["../x", "a/b", ".hidden", "", "x" * 120])
def test_input_names_are_plain_file_names(name: str) -> None:
    with pytest.raises(SpecError):
        _job(inputs=(SandboxInput(name, b"x"),)).validate()


def test_limits_are_bounded_and_compared_against_a_ceiling() -> None:
    with pytest.raises(SpecError):
        SandboxLimits(wall_seconds=0).validate()
    with pytest.raises(SpecError):
        SandboxLimits(memory_mb=1 << 20).validate()
    assert SandboxLimits().within(SandboxLimits(cpus=2, memory_mb=1024, pids=256, wall_seconds=300, tmp_mb=256, output_mb=64))
    assert not SandboxLimits(wall_seconds=301).within(SandboxLimits(wall_seconds=300))


def _result(**kw) -> SandboxResult:
    base = {"job_id": "j", "runner_kind": "docker", "image": IMAGE, "host_fingerprint": "h", "started_at": "t", "ended_at": "t", "exit_code": 0,
            "timed_out": False, "oom_killed": False, "stdout_excerpt": "alle Tests bestanden", "stderr_excerpt": "", "stdout_truncated": False,
            "stderr_truncated": False, "outputs": (), "output_limit_exceeded": False, "input_manifest_sha256": "m", "limits": SandboxLimits()}
    base.update(kw)
    return SandboxResult(**base)


def test_completion_is_measured_never_read_from_stdout() -> None:
    assert _result().completed and _result().state == "completed"
    assert not _result(exit_code=1).completed and _result(exit_code=1).state == "failed", "the success sentence in stdout changes nothing"
    assert _result(exit_code=0, timed_out=True).state == "timed_out"
    assert _result(exit_code=137, oom_killed=True).state == "oom_killed"
    assert _result(output_limit_exceeded=True).state == "output_limit_exceeded"
    assert not _result(runner_kind="mock").measured, "only a real runner measures"
    assert _result().measured


def test_mock_runner_executes_nothing_and_is_a_fixture() -> None:
    runner = MockSandboxRunner()
    marker = "MOCK_FAIL"
    job = SandboxJob("j", runner.image, ("python", "-I", "/work/in/program.py"), (SandboxInput("program.py", f"import os; os.system('rm -rf /') # {marker}".encode()),))
    res = runner.run(job)
    assert res.runner_kind == "mock" and not res.measured and res.exit_code == 1 and res.state == "failed"
    assert "alle Tests bestanden" in res.stdout_excerpt and not res.completed
    ok = runner.run(SandboxJob("j2", runner.image, ("python",), (SandboxInput("program.py", b"print(1)"),)))
    assert ok.state == "completed" and not ok.measured, "completed as a fixture, still not measured"
    assert ok.outputs[0].data and ok.outputs[0].sha256


def test_probe_of_the_mock_runner_fails_by_construction() -> None:
    report = run_probe(MockSandboxRunner(), probe_id="p").to_dict()
    assert report["runner_kind"] == "mock" and report["all_passed"] is False and report["checks"]
    assert report["report_sha256"] == report_sha256(report)
    tampered = dict(report, checks=[dict(c, passed=True) for c in report["checks"]])
    assert report_sha256(tampered) != report["report_sha256"], "a report cannot be edited into a pass"
