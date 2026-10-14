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
    "search_plan",  # repository addition (source discovery, 14.10.2026)
    "source_selection",  # repository addition (source discovery, 14.10.2026)
)


# Keywords neither provider's technically enforced structured output accepts (Anthropic: no string/number constraints, no uniqueItems,
# minItems only 0 or 1; OpenAI strict: no minLength/maxLength/uniqueItems). They are dropped for the provider only; the campus validates
# every answer against the full contract afterwards (pydantic models and validate_document), so nothing is lost, only enforced later.
_PROVIDER_UNSUPPORTED = frozenset({"uniqueItems", "minLength", "maxLength", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
                                   "multipleOf", "maxItems"})


def _nullable(prop: dict) -> dict:
    """An optional property in provider form: both providers want every property listed as required and absence expressed as null."""
    t = prop.get("type")
    if isinstance(t, str):
        prop["type"] = [t, "null"]
    elif isinstance(t, list):
        if "null" not in t:
            prop["type"] = [*t, "null"]
    elif "enum" in prop:
        pass
    else:  # $ref or composition: wrap
        return {"anyOf": [prop, {"type": "null"}]}
    if "enum" in prop and None not in prop["enum"]:
        prop["enum"] = [*prop["enum"], None]
    return prop


def _provider_form(node):
    if isinstance(node, list):
        return [_provider_form(n) for n in node]
    if not isinstance(node, dict):
        return node
    out: dict = {}
    for key, value in node.items():
        if key in _PROVIDER_UNSUPPORTED or (key == "minItems" and isinstance(value, int) and value > 1):
            continue
        if key == "oneOf":
            if all(isinstance(b, dict) and set(b) <= {"required"} for b in value):
                continue  # presence alternatives (exactly one of two keys): expressed below as nullable fields, enforced by the core
            out["anyOf"] = _provider_form(value)  # neither provider accepts oneOf; the core still checks exclusivity
            continue
        out[key] = _provider_form(value)
    if out.get("type") == "object" and isinstance(out.get("properties"), dict):
        required = set(out.get("required", []))
        out["properties"] = {name: (prop if name in required else _nullable(prop)) for name, prop in out["properties"].items()}
        out["required"] = list(out["properties"])
        out["additionalProperties"] = False
    return out


def provider_schema(name: str) -> dict:
    """The contract in the form a provider's technically enforced structured output accepts (intersection of the Anthropic
    `output_config` and OpenAI strict `json_schema` rules, both read 13.10.2026): no `$schema`/`$id`/`title`/`description` at the root,
    no unsupported constraint keywords, no `oneOf`, every object closed with all properties required and optional ones nullable.
    The provider enforces the shape; the campus enforces the full contract on the answer (never the other way round)."""
    schema = _provider_form(load_schema(name))
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
