"""Runner port. The API talks to this and to nothing else."""
from __future__ import annotations

from typing import Protocol

from .spec import SandboxJob, SandboxLimits, SandboxResult


class SandboxUnavailable(RuntimeError):
    """The runner cannot execute (daemon missing, image missing, ...). The capability is off; nothing falls back."""


class SandboxRunner(Protocol):
    kind: str
    image: str
    ceiling: SandboxLimits

    def host_fingerprint(self) -> str: ...

    def run(self, job: SandboxJob) -> SandboxResult: ...
