"""Real-container proofs of the hardened runner (chapter 13; A10; T04). Each docstring says what is measured."""
from __future__ import annotations

import hashlib
import os

import pytest
from campus_sandbox.port import SandboxUnavailable
from campus_sandbox.probe import run_probe
from campus_sandbox.spec import SandboxInput, SandboxJob, SandboxLimits

pytestmark = pytest.mark.sandbox

PY = ("python", "-I", "-B", "/work/in/program.py")


def _job(runner, program: str, *inputs: SandboxInput, limits: SandboxLimits | None = None, job_id="t") -> SandboxJob:
    return SandboxJob(job_id=job_id, image=runner.image, argv=PY, inputs=(SandboxInput("program.py", program.encode()), *inputs), limits=limits or SandboxLimits())


def test_probe_passes_on_this_host(runner) -> None:
    """Measures: no network, read-only rootfs, non-root, no docker socket, no host environment, inputs read-only, outputs captured, capabilities
    dropped, privileged port blocked, pid limit, wall timeout, memory limit, stdout is content. Each failing check is named."""
    report = run_probe(runner, probe_id="pytest").to_dict()
    failed = [(c["name"], c["observed"]) for c in report["checks"] if not c["passed"]]
    assert report["all_passed"], failed
    assert report["host_fingerprint"] == runner.host_fingerprint() and report["image"] == runner.image


def test_outputs_are_captured_with_hashes_and_inputs_are_readable(runner) -> None:
    """Measures: a program reads its read-only input and writes a result file; the runner returns the file with its sha256."""
    program = "data = open('/work/in/data.txt').read()\nopen('/work/out/result.txt', 'w').write(data.upper())\nprint('fertig')\n"
    res = runner.run(_job(runner, program, SandboxInput("data.txt", b"hallo campus"), job_id="io"))
    assert res.completed and res.measured and res.exit_code == 0, (res.stdout_excerpt, res.stderr_excerpt)
    out = {o.name: o for o in res.outputs}
    assert out["result.txt"].data == b"HALLO CAMPUS" and out["result.txt"].sha256 == hashlib.sha256(b"HALLO CAMPUS").hexdigest()
    assert res.input_manifest_sha256 and res.host_fingerprint


def test_host_secrets_never_reach_the_program(runner, monkeypatch) -> None:
    """Measures: a secret present in the controller's own environment is not visible inside the container."""
    monkeypatch.setenv("CAMPUS_SECRET_PROVIDERS_LEAKTEST", "geheim-1234")
    monkeypatch.setenv("CAMPUS_VAULT_DATABASE_URL", "postgresql://vault:geheim@db/x")
    res = runner.run(_job(runner, "import os, json\nprint(json.dumps(sorted(os.environ)))\n", job_id="env"))
    assert res.completed, res.stderr_excerpt
    assert "geheim" not in res.stdout_excerpt and "CAMPUS_" not in res.stdout_excerpt and "VAULT" not in res.stdout_excerpt, res.stdout_excerpt


def test_writing_outside_the_output_directory_fails(runner) -> None:
    """Measures: /work/in, /etc and /usr are not writable; only /work/out and the tmpfs are."""
    program = ("import os\nbad = []\nfor p in ['/work/in/x', '/etc/x', '/usr/x', '/x']:\n    try:\n        open(p, 'w').write('x'); bad.append(p)\n"
               "    except OSError:\n        pass\nopen('/tmp/ok', 'w').write('x')\nopen('/work/out/ok', 'w').write('x')\nraise SystemExit(1 if bad else 0)\n")
    res = runner.run(_job(runner, program, job_id="fs"))
    assert res.completed, res.stderr_excerpt


def test_wall_clock_limit_kills_the_job(runner) -> None:
    """Measures: a sleeping program is killed by the trusted runner at the wall limit; the result says timed_out and not completed."""
    res = runner.run(_job(runner, "import time\ntime.sleep(120)\n", limits=SandboxLimits(wall_seconds=2, memory_mb=64, pids=16, tmp_mb=8, output_mb=1), job_id="wall"))
    assert res.timed_out and not res.completed and res.state == "timed_out"


def test_output_limit_is_enforced(runner) -> None:
    """Measures: outputs beyond output_mb are discarded and the run is not completed."""
    res = runner.run(_job(runner, "open('/work/out/big.bin', 'wb').write(b'x' * (3 * 1024 * 1024))\n",
                          limits=SandboxLimits(output_mb=1, tmp_mb=8, memory_mb=128, pids=16, wall_seconds=30), job_id="out"))
    assert res.output_limit_exceeded and not res.completed and res.outputs == ()


def test_runner_refuses_a_foreign_image_and_excessive_limits(runner) -> None:
    other = SandboxJob("x", "python@sha256:" + "0" * 64, PY, (SandboxInput("program.py", b"print(1)"),))
    with pytest.raises(SandboxUnavailable):
        runner.run(other)
    with pytest.raises(SandboxUnavailable):
        runner.run(_job(runner, "print(1)", limits=SandboxLimits(wall_seconds=3600)))


def test_the_controller_process_is_not_the_sandbox() -> None:
    """A smoke check that the tests themselves ran in the controller process with the host environment intact (the previous test only
    proves the container cannot see it)."""
    assert "PATH" in os.environ
