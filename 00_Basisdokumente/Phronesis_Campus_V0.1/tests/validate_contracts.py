"""Offline contract checks only. No campus implementation or provider calls."""
from __future__ import annotations
from copy import deepcopy
from pathlib import Path
import json
import sys
import yaml
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]

def load_json(path: str) -> dict:
    with (ROOT / path).open(encoding="utf-8") as handle:
        return json.load(handle)

def validate(name: str, value: dict) -> None:
    definition = load_json(f"contracts/{name}.schema.json")
    Draft202012Validator.check_schema(definition)
    Draft202012Validator(definition, format_checker=FormatChecker()).validate(value)

def relations(catalog: dict, task: dict, decision: dict) -> None:
    """Illustrative cross-record assertions, NOT an authorization service."""
    assert catalog["purpose"] == "test_fixture", "checker only accepts synthetic fixtures"
    assert len({d["id"] for d in catalog["deployments"]}) == len(catalog["deployments"])
    deployments = {d["id"]: d for d in catalog["deployments"]}
    assert decision["task_id"] == task["task_id"]
    assert decision["catalog_revision"] == catalog["revision"]
    assert decision["routing_policy_ref"] == task["routing_policy_ref"]
    selected = deployments[decision["selected_deployment_id"]]
    assert selected["enabled"] and selected["health"] == "available"
    assert selected["canonical_model_id"] == decision["selected_canonical_model_id"]
    assert set(task["data_labels"]) <= set(selected["allowed_data_labels"])
    assert set(task["required_tool_scopes"]) <= set(selected["allowed_tool_scopes"])
    assert selected["capabilities"]["context_tokens"] >= task["context_tokens_estimate"] + task["max_output_tokens"]
    assert selected["capabilities"]["output_tokens"] >= task["max_output_tokens"]
    for feature in task["required_features"]:
        assert selected["capabilities"][feature] == "supported"
    if task["qualification_mode"] == "qualified":
        assert selected["qualification_refs"], "scope matching requires real qualification records in the app"
    if task["role"] in {"reviewer", "replicator"}:
        assert selected["canonical_model_id"] not in task["substantive_contributor_model_ids"]
    if decision["required_review"]:
        reviewer = deployments[decision["review_deployment_id"]]
        assert reviewer["enabled"] and reviewer["health"] == "available"
        assert reviewer["canonical_model_id"] != selected["canonical_model_id"]
        assert reviewer["canonical_model_id"] not in task["substantive_contributor_model_ids"]
        assert set(task["data_labels"]) <= set(reviewer["allowed_data_labels"])
        assert decision["reservation_ref"]
    assessments = {a["deployment_id"]: a for a in decision["candidate_assessments"]}
    assert assessments[selected["id"]]["eligible"]
    assert set(assessments[selected["id"]]["evidence_refs"]) <= set(selected["qualification_refs"])


def main() -> int:
    checks = []
    for name in ["model_catalog", "task_need", "routing_decision", "agent_profile", "skill_manifest", "route_proposal"]:
        validate(name, load_json(f"examples/{name}.fixture.json"))
        checks.append(f"PASS schema: {name}")
    with (ROOT / "config/model_catalog.example.yaml").open(encoding="utf-8") as handle:
        validate("model_catalog", yaml.safe_load(handle))
    checks.append("PASS disabled configuration template")
    catalog = load_json("examples/model_catalog.fixture.json")
    task = load_json("examples/task_need.fixture.json")
    decision = load_json("examples/routing_decision.fixture.json")
    relations(catalog, task, decision)
    checks.append("PASS fixture relations")
    variants = []
    t = deepcopy(task); t["data_labels"] = ["confidential"]; variants.append((catalog, t, decision, "data export"))
    c = deepcopy(catalog); c["deployments"][0]["enabled"] = False; variants.append((c, task, decision, "disabled model"))
    c = deepcopy(catalog); c["deployments"][0]["capabilities"]["tool_calling"] = "unknown"; variants.append((c, task, decision, "unknown capability"))
    c = deepcopy(catalog); c["deployments"][1]["canonical_model_id"] = c["deployments"][0]["canonical_model_id"]; variants.append((c, task, decision, "alias diversity"))
    t = deepcopy(task); t["context_tokens_estimate"] = 40000; variants.append((catalog, t, decision, "context overflow"))
    d = deepcopy(decision); d["candidate_assessments"][0]["evidence_refs"] = ["invented-evidence"]; variants.append((catalog, task, d, "unknown evidence"))
    t = deepcopy(task); t["role"] = "reviewer"; t["substantive_contributor_model_ids"] = ["fixture/model-a@1"]; variants.append((catalog, t, decision, "producer as reviewer"))
    for c, t, d, label in variants:
        try:
            relations(c, t, d)
        except (AssertionError, KeyError, TypeError):
            checks.append(f"PASS rejects: {label}")
        else:
            raise AssertionError(f"Invalid fixture was accepted: {label}")
    print("\n".join(checks))
    print(f"{len(checks)} contract checks passed; no provider, sandbox or campus acceptance tests executed.")
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Contract check failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
