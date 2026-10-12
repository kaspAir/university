"""Docker-based runner: one hardened, short-lived container per job (chapter 13).

Hardening in force for every job (see REQUIRED_HARDENING and the static test):
  no network, read-only root filesystem, all capabilities dropped, no new privileges, non-root user, pid limit, memory limit without
  swap, cpu limit, size-limited tmpfs, inputs mounted read-only, outputs in a separate writable directory, no host environment,
  no Docker socket, wall-clock timeout enforced by this process (not by the container).

This is runc on the development host. Stronger isolation for untrusted code (dedicated VM, gVisor) is an operational decision for the
target host and must be tested there; this runner does not claim it (architecture chapter 13, [T7]).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from .port import SandboxUnavailable
from .spec import IN_DIR, OUT_DIR, OutputFile, SandboxJob, SandboxLimits, SandboxResult

# flags every `docker run` of this runner carries; the static test and the probe both refer to this list
REQUIRED_HARDENING: tuple[str, ...] = ("--network", "none", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                                       "--ipc", "none", "--user", "65534:65534")
# the ONLY environment the program sees; nothing from the host process is forwarded
SANDBOX_ENV: tuple[str, ...] = ("HOME=/tmp", "PYTHONDONTWRITEBYTECODE=1", "PYTHONIOENCODING=utf-8", "PYTHONUNBUFFERED=1", "LANG=C.UTF-8")


def docker_argv(job: SandboxJob, *, container_name: str, in_dir: str, out_dir: str) -> list[str]:
    """The complete, deterministic `docker run` command for a job."""
    lim = job.limits
    argv = ["docker", "run", "--name", container_name, *REQUIRED_HARDENING,
            "--pids-limit", str(lim.pids), "--memory", f"{lim.memory_mb}m", "--memory-swap", f"{lim.memory_mb}m", "--cpus", str(lim.cpus),
            "--tmpfs", f"/tmp:rw,noexec,nosuid,size={lim.tmp_mb}m",
            "--mount", f"type=bind,source={in_dir},target={IN_DIR},readonly",
            "--mount", f"type=bind,source={out_dir},target={OUT_DIR}",
            "--workdir", OUT_DIR]
    for e in SANDBOX_ENV:
        argv += ["--env", e]
    argv += [job.image, *job.argv]
    return argv


class DockerSandboxRunner:
    kind = "docker"

    def __init__(self, image: str, workroot: Path, ceiling: SandboxLimits | None = None, docker_binary: str = "docker"):
        self.image = image
        self.workroot = workroot
        self.ceiling = ceiling or SandboxLimits(cpus=2.0, memory_mb=1024, pids=256, wall_seconds=300, tmp_mb=256, output_mb=64)
        self.docker = docker_binary

    # ---------------------------------------------------------------- host facts
    def _docker(self, *args: str, timeout: float = 60) -> subprocess.CompletedProcess:
        try:
            return subprocess.run([self.docker, *args], capture_output=True, timeout=timeout, check=False)
        except FileNotFoundError as exc:
            raise SandboxUnavailable("Docker-CLI nicht gefunden; Sandboxpfad nicht verfügbar") from exc
        except subprocess.TimeoutExpired as exc:
            raise SandboxUnavailable(f"Docker antwortet nicht ({args[0]})") from exc

    def host_fingerprint(self) -> str:
        """Identity of the execution host as the acceptance binds it: daemon id, server version, default runtime, OS."""
        info = self._docker("info", "--format", "{{.ID}}|{{.ServerVersion}}|{{.DefaultRuntime}}|{{.OperatingSystem}}|{{.Architecture}}")
        if info.returncode != 0:
            raise SandboxUnavailable("Docker-Daemon nicht erreichbar: " + info.stderr.decode("utf-8", "replace").strip()[:200])
        return hashlib.sha256(info.stdout.strip()).hexdigest()

    def image_present(self) -> bool:
        return self._docker("image", "inspect", self.image).returncode == 0

    # ---------------------------------------------------------------- execution
    def run(self, job: SandboxJob) -> SandboxResult:
        job.validate()
        if job.image != self.image:
            raise SandboxUnavailable("Job-Image entspricht nicht dem konfigurierten, abgenommenen Image")
        if not job.limits.within(self.ceiling):
            raise SandboxUnavailable("Job-Limits überschreiten die Obergrenzen des Runners")
        fingerprint = self.host_fingerprint()
        self.workroot.mkdir(parents=True, exist_ok=True)
        workdir = Path(tempfile.mkdtemp(prefix=f"job-{job.job_id}-", dir=self.workroot))
        in_dir, out_dir = workdir / "in", workdir / "out"
        in_dir.mkdir()
        out_dir.mkdir()
        try:
            for item in job.inputs:
                (in_dir / item.name).write_bytes(item.data)
            try:
                os.chmod(out_dir, 0o777)  # the non-root sandbox user must be able to write its outputs (Linux hosts)
            except OSError:
                pass
            name = f"campus-sb-{job.job_id}".lower()
            argv = docker_argv(job, container_name=name, in_dir=str(in_dir), out_dir=str(out_dir))
            started = datetime.now(UTC)
            timed_out = False
            try:
                proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            except FileNotFoundError as exc:
                raise SandboxUnavailable("Docker-CLI nicht gefunden") from exc
            try:
                out, err = proc.communicate(timeout=job.limits.wall_seconds + 15)  # +15 s container start/stop overhead
            except subprocess.TimeoutExpired:
                timed_out = True
                self._docker("kill", name, timeout=30)
                out, err = proc.communicate(timeout=60)
            ended = datetime.now(UTC)
            state = self._inspect_state(name)
            self._docker("rm", "-f", name, timeout=60)
            if state is None and not timed_out and proc.returncode != 0 and b"Unable to find image" in err:
                raise SandboxUnavailable("Sandbox-Image nicht vorhanden: " + job.image)
            # the container's own wall clock: a job may exceed its limit without exceeding ours (+15 s); both count as timed out
            if state is not None and state.get("StartedAt") and state.get("FinishedAt") and not timed_out:
                try:
                    ran = (datetime.fromisoformat(state["FinishedAt"].replace("Z", "+00:00")) - datetime.fromisoformat(state["StartedAt"].replace("Z", "+00:00")))
                    if ran.total_seconds() > job.limits.wall_seconds:
                        timed_out = True
                except ValueError:
                    pass
            oom = bool(state and state.get("OOMKilled"))
            exit_code = state.get("ExitCode") if state is not None else proc.returncode
            if timed_out:
                exit_code = None if exit_code in (137, 143) else exit_code
            outputs, exceeded = self._collect_outputs(out_dir, job.limits.output_mb)
            lim = job.limits.excerpt_bytes
            notes = []
            if timed_out:
                notes.append("Zeitlimit überschritten; Container beendet")
            if oom:
                notes.append("Speicherlimit erreicht (OOM-Kill)")
            if exceeded:
                notes.append("Ausgabegrenze überschritten; Ausgaben verworfen")
            return SandboxResult(
                job_id=job.job_id, runner_kind=self.kind, image=job.image, host_fingerprint=fingerprint,
                started_at=started.isoformat(), ended_at=ended.isoformat(), exit_code=exit_code, timed_out=timed_out, oom_killed=oom,
                stdout_excerpt=out[:lim].decode("utf-8", "replace"), stderr_excerpt=err[:lim].decode("utf-8", "replace"),
                stdout_truncated=len(out) > lim, stderr_truncated=len(err) > lim, outputs=outputs, output_limit_exceeded=exceeded,
                input_manifest_sha256=job.input_manifest_sha256(), limits=job.limits, notes=tuple(notes),
            )
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    def _inspect_state(self, name: str) -> dict | None:
        res = self._docker("inspect", "--format", "{{json .State}}", name, timeout=30)
        if res.returncode != 0:
            return None
        try:
            return json.loads(res.stdout.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _collect_outputs(out_dir: Path, output_mb: int) -> tuple[tuple[OutputFile, ...], bool]:
        files = sorted(p for p in out_dir.rglob("*") if p.is_file())
        total = sum(p.stat().st_size for p in files)
        if total > output_mb * 1024 * 1024:
            return (), True
        collected = []
        for p in files:
            data = p.read_bytes()
            collected.append(OutputFile(name=str(p.relative_to(out_dir)).replace("\\", "/"), sha256=hashlib.sha256(data).hexdigest(), size=len(data), data=data))
        return tuple(collected), False

