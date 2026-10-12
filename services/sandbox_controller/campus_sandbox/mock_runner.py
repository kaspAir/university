"""Mock runner for development_mock: executes NOTHING. Its results are fixtures (`runner_kind=mock`, never measured).

It exists so that the control flow around experiments can be built and tested without a sandbox. Nothing derived from a mock result
may be relabelled as measured (T04, CLAUDE.md: real experiments, synthetic fixtures and model opinions are never relabelled).
"""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from .spec import OutputFile, SandboxJob, SandboxLimits, SandboxResult


class MockSandboxRunner:
    kind = "mock"

    def __init__(self, image: str = "mock/sandbox@sha256:" + "0" * 64):
        self.image = image
        self.ceiling = SandboxLimits(cpus=2.0, memory_mb=1024, pids=256, wall_seconds=300, tmp_mb=256, output_mb=64)

    def host_fingerprint(self) -> str:
        return "mock-host"

    def run(self, job: SandboxJob) -> SandboxResult:
        job.validate()
        now = datetime.now(UTC).isoformat()
        program = next((i.data.decode("utf-8", "replace") for i in job.inputs if i.name == "program.py"), "")
        # test switches, mirrored from the program text, nothing is executed
        exit_code = 1 if "MOCK_FAIL" in program else 0
        canned = b"synthetic output (mock runner, nothing executed)\n"
        return SandboxResult(
            job_id=job.job_id, runner_kind=self.kind, image=job.image, host_fingerprint=self.host_fingerprint(), started_at=now, ended_at=now,
            exit_code=exit_code, timed_out="MOCK_TIMEOUT" in program, oom_killed=False,
            stdout_excerpt="alle Tests bestanden (Mock: dieser Satz ist Inhalt, kein Messwert)", stderr_excerpt="",
            stdout_truncated=False, stderr_truncated=False,
            outputs=(OutputFile(name="result.json", sha256=hashlib.sha256(canned).hexdigest(), size=len(canned), data=canned),),
            output_limit_exceeded=False, input_manifest_sha256=job.input_manifest_sha256(), limits=job.limits,
            notes=("Mock-Runner: keine Ausführung, Fixture",),
        )
