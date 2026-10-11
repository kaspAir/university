from __future__ import annotations

import json
from functools import cache
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"
EXAMPLE_DIR = Path(__file__).resolve().parent / "examples"
SCHEMA_NAMES = (
    "model_catalog",
    "task_need",
    "route_proposal",
    "routing_decision",
    "agent_profile",
    "skill_manifest",
    "review_report",  # repository addition (S1)
    "claim_set",  # repository addition (S2)
    "evaluation_verdict",  # repository addition (S3)
)


def provider_schema(name: str) -> dict:
    """Schema as handed to a provider's structured-output feature: without `$schema`, `$id`, `title`, `description`."""
    schema = dict(load_schema(name))
    for key in ("$schema", "$id", "title", "description"):
        schema.pop(key, None)
    return schema


@cache
def load_schema(name: str) -> dict:
    with (SCHEMA_DIR / f"{name}.schema.json").open(encoding="utf-8") as handle:
        schema = json.load(handle)
    Draft202012Validator.check_schema(schema)
    return schema


def validate_document(name: str, document: dict) -> list[str]:
    """Return human-readable schema violations (empty when the document is valid)."""
    validator = Draft202012Validator(load_schema(name), format_checker=FormatChecker())
    violations = []
    for error in validator.iter_errors(document):
        path = "/".join(str(p) for p in error.absolute_path) or "<root>"
        violations.append(f"{path}: {error.message}")
    return violations


def load_example(name: str) -> dict:
    with (EXAMPLE_DIR / f"{name}.fixture.json").open(encoding="utf-8") as handle:
        return json.load(handle)
