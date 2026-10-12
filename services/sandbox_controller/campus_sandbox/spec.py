"""Job and result contracts of the sandbox controller. Pure data; no Docker here.

A result records what the trusted runner measured: start, end, exit code, kill reasons, hashes of inputs and outputs and the limits
in force. stdout is content, not a result (chapter 13): nothing in here derives success from what the program printed.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field

IMAGE_DIGEST = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*@sha256:[0-9a-f]{64}$")
INPUT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
RUNNER_KINDS = ("docker", "mock")
IN_DIR = "/work/in"
OUT_DIR = "/work/out"


class SpecError(ValueError):
    """A job that violates the contract is refused before anything starts."""


@dataclass(frozen=True)
class SandboxLimits:
    cpus: float = 1.0
    memory_mb: int = 256
    pids: int = 64
    wall_seconds: int = 30
    tmp_mb: int = 32
    output_mb: int = 16
    excerpt_bytes: int = 16384

    def validate(self) -> None:
        if not (0 < self.cpus <= 8):
            raise SpecError("cpus muss zwischen 0 und 8 liegen")
        for name, value, low, high in (("memory_mb", self.memory_mb, 16, 8192), ("pids", self.pids, 4, 4096), ("wall_seconds", self.wall_seconds, 1, 3600),
                                       ("tmp_mb", self.tmp_mb, 1, 4096), ("output_mb", self.output_mb, 1, 4096), ("excerpt_bytes", self.excerpt_bytes, 256, 1 << 20)):
            if not (low <= value <= high):
                raise SpecError(f"{name} muss zwischen {low} und {high} liegen")

    def within(self, ceiling: SandboxLimits) -> bool:
        return (self.cpus <= ceiling.cpus and self.memory_mb <= ceiling.memory_mb and self.pids <= ceiling.pids
                and self.wall_seconds <= ceiling.wall_seconds and self.tmp_mb <= ceiling.tmp_mb and self.output_mb <= ceiling.output_mb)


@dataclass(frozen=True)
class SandboxInput:
    name: str  # file name below /work/in, no directories
    data: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


@dataclass(frozen=True)
class SandboxJob:
    job_id: str
    image: str  # digest-pinned: name@sha256:...
    argv: tuple[str, ...]
    inputs: tuple[SandboxInput, ...] = ()
    limits: SandboxLimits = field(default_factory=SandboxLimits)

    def validate(self) -> None:
        if not IMAGE_DIGEST.match(self.image):
            raise SpecError("Sandbox-Image muss per Digest festgeschrieben sein (name@sha256:...)")
        if not self.argv:
            raise SpecError("argv darf nicht leer sein")
        names = [i.name for i in self.inputs]
        if len(set(names)) != len(names) or any(not INPUT_NAME.match(n) for n in names):
            raise SpecError("Eingabedateien brauchen eindeutige, einfache Dateinamen (keine Pfade)")
        self.limits.validate()

    def input_manifest(self) -> list[dict]:
        return [{"name": i.name, "sha256": i.sha256, "size": len(i.data)} for i in sorted(self.inputs, key=lambda x: x.name)]

    def input_manifest_sha256(self) -> str:
        return hashlib.sha256(json.dumps(self.input_manifest(), sort_keys=True).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class OutputFile:
    name: str
    sha256: str
    size: int
    data: bytes = field(default=b"", repr=False)  # the bytes themselves; the caller stores them content-addressed, the record keeps the hash

    def meta(self) -> dict:
        return {"name": self.name, "sha256": self.sha256, "size": self.size}


@dataclass(frozen=True)
class SandboxResult:
    job_id: str
    runner_kind: str
    image: str
    host_fingerprint: str
    started_at: str
    ended_at: str
    exit_code: int | None
    timed_out: bool
    oom_killed: bool
    stdout_excerpt: str
    stderr_excerpt: str
    stdout_truncated: bool
    stderr_truncated: bool
    outputs: tuple[OutputFile, ...]
    output_limit_exceeded: bool
    input_manifest_sha256: str
    limits: SandboxLimits
    notes: tuple[str, ...] = ()

    @property
    def measured(self) -> bool:
        """Only a real runner measures anything. A mock result is a fixture, whatever it says."""
        return self.runner_kind == "docker"

    @property
    def completed(self) -> bool:
        """Exit 0 within all limits. Says nothing about correctness and nothing about what stdout claims."""
        return self.exit_code == 0 and not self.timed_out and not self.oom_killed and not self.output_limit_exceeded

    @property
    def state(self) -> str:
        if self.timed_out:
            return "timed_out"
        if self.oom_killed:
            return "oom_killed"
        if self.output_limit_exceeded:
            return "output_limit_exceeded"
        if self.exit_code == 0:
            return "completed"
        return "failed"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["outputs"] = [o.meta() for o in self.outputs]
        d["limits"] = asdict(self.limits)
        d["measured"] = self.measured
        d["completed"] = self.completed
        d["state"] = self.state
        return d
