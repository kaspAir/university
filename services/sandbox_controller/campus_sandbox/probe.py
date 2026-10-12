"""Sandbox probe: measures on THIS host whether the runner enforces what the hardening promises (chapter 13, [T7], A10).

The probe is the basis of a sandbox acceptance. It runs several small jobs and records expected vs observed per check. A report
is bound to runner kind, image digest and host fingerprint; a different image or host needs a new probe and a new acceptance.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

from .port import SandboxRunner
from .spec import SandboxInput, SandboxJob, SandboxLimits

# Runs inside the sandbox. Prints one JSON object per line: {"check": name, "passed": bool, "observed": str}.
# No "privileged port" check: Docker sets net.ipv4.ip_unprivileged_port_start=0 inside containers, so binding port 80 says nothing about
# capabilities (and the network is none anyway); setuid(0) is the capability probe.
IN_CONTAINER_PROBE = r'''
import json, os, socket, sys
def out(check, passed, observed):
    print(json.dumps({"check": check, "passed": bool(passed), "observed": str(observed)[:200]}))
try:
    socket.create_connection(("1.1.1.1", 443), timeout=2); out("network_blocked", False, "Verbindung aufgebaut")
except OSError as e:
    out("network_blocked", True, type(e).__name__)
try:
    open("/etc/campus-probe", "w").write("x"); out("rootfs_read_only", False, "Schreiben in /etc möglich")
except OSError as e:
    out("rootfs_read_only", True, type(e).__name__)
out("non_root_user", os.getuid() != 0, f"uid={os.getuid()}")
out("no_docker_socket", not os.path.exists("/var/run/docker.sock"), "fehlt" if not os.path.exists("/var/run/docker.sock") else "vorhanden")
leak = [k for k in os.environ if k.startswith("CAMPUS_") or any(w in k.upper() for w in ("SECRET", "TOKEN", "PASSWORD", "DATABASE", "API_KEY"))]
out("no_host_environment", not leak, ",".join(sorted(os.environ)) )
try:
    open("/work/in/probe-write", "w").write("x"); out("inputs_read_only", False, "Schreiben in /work/in möglich")
except OSError as e:
    out("inputs_read_only", True, type(e).__name__)
try:
    open("/work/out/probe.txt", "w").write("probe"); out("outputs_writable", True, "ok")
except OSError as e:
    out("outputs_writable", False, type(e).__name__)
try:
    os.setuid(0); out("capabilities_dropped", False, "setuid(0) erlaubt")
except OSError as e:
    out("capabilities_dropped", True, type(e).__name__)
pids = []
limited = False
try:
    for _ in range(600):
        pid = os.fork()
        if pid == 0:
            os._exit(0)
        pids.append(pid)
except OSError as e:
    limited = True
for p in pids:
    try: os.waitpid(p, 0)
    except OSError: pass
out("pids_limited", limited, f"forks={len(pids)}")
sys.stdout.flush()
'''

RUNNER_CHECKS = ("wall_timeout_enforced", "memory_limit_enforced", "stdout_claims_are_not_results", "outputs_captured_with_hash")


@dataclass(frozen=True)
class ProbeCheck:
    name: str
    passed: bool
    observed: str


@dataclass(frozen=True)
class ProbeReport:
    runner_kind: str
    image: str
    host_fingerprint: str
    checks: tuple[ProbeCheck, ...]

    @property
    def all_passed(self) -> bool:
        return bool(self.checks) and all(c.passed for c in self.checks)

    def to_dict(self) -> dict:
        d = {"runner_kind": self.runner_kind, "image": self.image, "host_fingerprint": self.host_fingerprint,
             "checks": [asdict(c) for c in self.checks], "all_passed": self.all_passed}
        d["report_sha256"] = report_sha256(d)
        return d


def report_sha256(report: dict) -> str:
    core = {k: report[k] for k in ("runner_kind", "image", "host_fingerprint", "checks")}
    return hashlib.sha256(json.dumps(core, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _job(runner: SandboxRunner, job_id: str, argv: tuple[str, ...], limits: SandboxLimits, inputs: tuple[SandboxInput, ...] = ()) -> SandboxJob:
    return SandboxJob(job_id=job_id, image=runner.image, argv=argv, inputs=inputs, limits=limits)


def run_probe(runner: SandboxRunner, *, probe_id: str) -> ProbeReport:
    """Executes the probe jobs on the given runner. A mock runner yields a report that fails by construction (it executes nothing)."""
    base = SandboxLimits(cpus=0.5, memory_mb=128, pids=32, wall_seconds=20, tmp_mb=16, output_mb=4)
    checks: list[ProbeCheck] = []
    if runner.kind != "docker":
        for name in ("network_blocked", "rootfs_read_only", "non_root_user", "no_docker_socket", "no_host_environment", "inputs_read_only",
                     "outputs_writable", "capabilities_dropped", "pids_limited", *RUNNER_CHECKS):
            checks.append(ProbeCheck(name, False, f"Runner {runner.kind} führt nichts aus; kein Nachweis"))
        return ProbeReport(runner.kind, runner.image, runner.host_fingerprint(), tuple(checks))
    # 1) in-container checks
    res = runner.run(_job(runner, f"{probe_id}-a", ("python", "-I", "-B", "/work/in/probe.py"), base,
                          (SandboxInput("probe.py", IN_CONTAINER_PROBE.encode("utf-8")),)))
    seen = set()
    for line in res.stdout_excerpt.splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and "check" in item:
            seen.add(item["check"])
            checks.append(ProbeCheck(str(item["check"]), bool(item["passed"]), str(item["observed"])))
    for name in ("network_blocked", "rootfs_read_only", "non_root_user", "no_docker_socket", "no_host_environment", "inputs_read_only",
                 "outputs_writable", "capabilities_dropped", "pids_limited"):
        if name not in seen:
            checks.append(ProbeCheck(name, False, f"keine Meldung (exit={res.exit_code}, stderr={res.stderr_excerpt[:120]!r})"))
    checks.append(ProbeCheck("outputs_captured_with_hash", any(o.name == "probe.txt" and len(o.sha256) == 64 for o in res.outputs),
                             ", ".join(f"{o.name}:{o.sha256[:12]}" for o in res.outputs) or "keine Ausgaben"))
    # 2) wall-clock limit is enforced by the trusted runner
    res_t = runner.run(_job(runner, f"{probe_id}-t", ("python", "-c", "import time; time.sleep(60)"), SandboxLimits(cpus=0.5, memory_mb=64, pids=16, wall_seconds=3, tmp_mb=8, output_mb=1)))
    checks.append(ProbeCheck("wall_timeout_enforced", res_t.timed_out and not res_t.completed, f"timed_out={res_t.timed_out} exit={res_t.exit_code}"))
    # 3) memory limit: allocating far above the limit must not complete
    res_m = runner.run(_job(runner, f"{probe_id}-m", ("python", "-c", "x = bytearray(512 * 1024 * 1024); print(len(x))"),
                            SandboxLimits(cpus=0.5, memory_mb=64, pids=16, wall_seconds=20, tmp_mb=8, output_mb=1)))
    checks.append(ProbeCheck("memory_limit_enforced", not res_m.completed, f"oom_killed={res_m.oom_killed} exit={res_m.exit_code}"))
    # 4) stdout is content: a success sentence with a failing exit code is not a completed run
    res_s = runner.run(_job(runner, f"{probe_id}-s", ("python", "-c", "print('alle Tests bestanden'); raise SystemExit(1)"), base))
    checks.append(ProbeCheck("stdout_claims_are_not_results", "alle Tests bestanden" in res_s.stdout_excerpt and not res_s.completed,
                             f"exit={res_s.exit_code} completed={res_s.completed}"))
    return ProbeReport(runner.kind, runner.image, res.host_fingerprint, tuple(checks))
