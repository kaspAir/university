"""Static boundaries: I01 (no provider names in domain/API code) and the import direction of ADR-0001."""
from __future__ import annotations

import re
from pathlib import Path

from tests.conftest import REPO

PROVIDER_NAMES = re.compile(r"\b(anthropic|openai|gemini|claude|gpt-?\d|mistral|cohere|llama|litellm)\b", re.IGNORECASE)
DOMAIN = REPO / "packages" / "domain" / "campus_domain"
API = REPO / "apps" / "api" / "campus_api"


def _py_files(root: Path):
    return [p for p in root.rglob("*.py")]


def test_i01_no_provider_or_model_names_in_domain_and_api() -> None:
    offenders = []
    for path in _py_files(DOMAIN) + _py_files(API):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if PROVIDER_NAMES.search(line):
                offenders.append(f"{path.relative_to(REPO)}:{lineno}: {line.strip()}")
    assert offenders == [], "\n".join(offenders)


def test_domain_imports_nothing_from_infrastructure_api_or_gateway() -> None:
    forbidden = re.compile(r"^\s*(from|import)\s+(campus_infra|campus_api|campus_gateway|sqlalchemy|fastapi|psycopg|alembic)\b")
    offenders = []
    for path in _py_files(DOMAIN):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if forbidden.match(line):
                offenders.append(f"{path.relative_to(REPO)}:{lineno}: {line.strip()}")
    assert offenders == [], "\n".join(offenders)


def test_only_the_gateway_may_know_adapters() -> None:
    """Adapter implementations live under services/llm_gateway only."""
    for path in _py_files(DOMAIN) + _py_files(API):
        text = path.read_text(encoding="utf-8")
        assert "class .*Adapter" not in text
        assert not re.search(r"^class \w+Adapter\b", text, re.MULTILINE), path


def test_no_dynamic_execution_outside_the_sandbox_controller() -> None:
    """S4 (chapter 13): the API, the workers, the domain, the infrastructure and the gateway never start processes or evaluate code.
    Only the sandbox controller's Docker runner may spawn a process, and only `docker`."""
    forbidden = re.compile(r"\b(subprocess|os\.system|os\.popen|os\.exec\w*|os\.spawn\w*|importlib|__import__|\beval\(|\bexec\(|(?<!re\.)compile\()")
    roots = [REPO / "apps" / "api", REPO / "workers", REPO / "packages", REPO / "services" / "llm_gateway",
             REPO / "services" / "sandbox_controller" / "campus_sandbox" / "spec.py", REPO / "services" / "sandbox_controller" / "campus_sandbox" / "probe.py",
             REPO / "services" / "sandbox_controller" / "campus_sandbox" / "mock_runner.py"]
    offenders = []
    for root in roots:
        for path in ([root] if root.is_file() else _py_files(root)):
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                code = line.split("#", 1)[0]
                if forbidden.search(code):
                    offenders.append(f"{path.relative_to(REPO)}:{lineno}: {line.strip()}")
    assert offenders == [], "\n".join(offenders)


def test_api_never_imports_the_docker_runner_at_module_level() -> None:
    """The runner is constructed from configuration inside a function; no request path imports it unconditionally."""
    for path in _py_files(API):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith(("from campus_sandbox.docker_runner", "import campus_sandbox.docker_runner")):
                raise AssertionError(f"{path.relative_to(REPO)}: {line}")
