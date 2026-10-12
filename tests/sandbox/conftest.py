"""Sandbox tests run REAL containers on the development host. They are skipped, never faked, without CAMPUS_TEST_SANDBOX=1 and Docker.

What they prove: the hardening holds on THIS host with THIS daemon (runc under Docker Desktop/WSL2 on the development machine). They do
not prove anything about the target host; that is what the probe and the acceptance are for (chapter 13, [T7]).
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest
from campus_sandbox.docker_runner import DockerSandboxRunner


def _digest_of(tag: str) -> str | None:
    try:
        res = subprocess.run(["docker", "inspect", "--format", "{{index .RepoDigests 0}}", tag], capture_output=True, text=True, timeout=60, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return res.stdout.strip() or None if res.returncode == 0 else None


@pytest.fixture(scope="session")
def sandbox_image() -> str:
    if os.environ.get("CAMPUS_TEST_SANDBOX") != "1":
        pytest.skip("CAMPUS_TEST_SANDBOX=1 nicht gesetzt: Sandboxtests brauchen Docker und starten echte Container")
    image = os.environ.get("CAMPUS_TEST_SANDBOX_IMAGE") or _digest_of("python:3.12-slim")
    if not image or "@sha256:" not in image:
        pytest.skip("Kein per Digest festgeschriebenes Sandbox-Image (CAMPUS_TEST_SANDBOX_IMAGE oder lokales python:3.12-slim)")
    return image


@pytest.fixture(scope="session")
def runner(sandbox_image: str) -> DockerSandboxRunner:
    r = DockerSandboxRunner(image=sandbox_image, workroot=Path(tempfile.mkdtemp(prefix="campus-sbx-")))
    try:
        r.host_fingerprint()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Docker nicht erreichbar: {exc}")
    return r
