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
