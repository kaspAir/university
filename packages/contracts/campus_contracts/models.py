"""Pydantic mirrors of the six design schemas (contract set 1.0). Unknown fields are rejected."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Support = Literal["supported", "unsupported", "unknown"]
DataLabel = Literal["public", "internal", "confidential", "personal"]
Feature = Literal["text_input", "vision_input", "tool_calling", "structured_output", "streaming"]
AdapterName = Literal["mock", "anthropic", "openai", "google", "openai_compatible", "litellm"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Capabilities(_Strict):
    text_input: Support
    vision_input: Support
    tool_calling: Support
    structured_output: Support
    streaming: Support
    context_tokens: int | None = Field(ge=1)
    output_tokens: int | None = Field(ge=1)


class Deployment(_Strict):
    id: str = Field(min_length=1)
    provider_id: str = Field(min_length=1)
    adapter: AdapterName
    enabled: bool
    provider_model_id: str | None
    canonical_model_id: str | None
    model_family: str | None
    shared_base_id: str | None
    api_base: str | None
    secret_ref: str | None
    version_mode: Literal["fixed", "moving_alias", "unknown"]
    capabilities: Capabilities
    allowed_data_labels: list[DataLabel] = Field(min_length=1)
    allowed_tool_scopes: list[str]
    processing_profile_ref: str | None
    pricing_ref: str | None
    qualification_refs: list[str]
    health: Literal["available", "unavailable", "unknown", "throttled"]


class ModelCatalog(_Strict):
    schema_version: Literal["1.0"]
    revision: str = Field(min_length=1)
    purpose: Literal["configuration_template", "test_fixture", "runtime_snapshot"]
    deployments: list[Deployment] = Field(min_length=1)

    def deployment(self, deployment_id: str) -> Deployment | None:
        return next((d for d in self.deployments if d.id == deployment_id), None)


class TaskNeed(_Strict):
    schema_version: Literal["1.0"]
    task_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    mandate_version_ref: str = Field(min_length=1)
    agent_profile_version_ref: str = Field(min_length=1)
    skill_version_refs: list[str]
    role: Literal["producer", "reviewer", "selector", "replicator"]
    task_class: str = Field(min_length=1)
    language: str = Field(min_length=1)
    data_labels: list[DataLabel] = Field(min_length=1)
    required_features: list[Feature]
    context_tokens_estimate: int = Field(ge=1)
    max_output_tokens: int = Field(ge=1)
    required_tool_scopes: list[str]
    substantive_contributor_model_ids: list[str]
    qualification_mode: Literal["qualified", "sandbox_experiment"]
    routing_policy_ref: str = Field(min_length=1)
    budget_account_id: str = Field(min_length=1)


class Alternative(_Strict):
    deployment_id: str = Field(min_length=1)
    reason_not_selected: str = Field(min_length=1)


class RouteProposal(_Strict):
    """LLM- or rule-generated selection among eligible IDs. Carries no authority."""

    task_id: str = Field(min_length=1)
    selected_deployment_id: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    evidence_refs: list[str]
    alternatives: list[Alternative]
    uncertainties: list[str]


class CandidateAssessment(_Strict):
    deployment_id: str = Field(min_length=1)
    eligible: bool
    exclusion_reasons: list[str]
    evidence_refs: list[str]
    note: str = Field(min_length=1)


class RoutingDecision(_Strict):
    """Persisted audit record written by the trusted core only."""

    schema_version: Literal["1.0"]
    id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    catalog_revision: str = Field(min_length=1)
    routing_policy_ref: str = Field(min_length=1)
    selection_mode: Literal["llm", "deterministic_fallback", "fixture"]
    selector_model_ref: str | None
    selected_deployment_id: str = Field(min_length=1)
    selected_canonical_model_id: str = Field(min_length=1)
    candidate_assessments: list[CandidateAssessment] = Field(min_length=1)
    rationale: str = Field(min_length=1)
    required_review: bool
    review_deployment_id: str | None
    reservation_ref: str = Field(min_length=1)
    pricing_snapshot_ref: str = Field(min_length=1)
    created_at: str
    state: Literal["proposed", "authorized", "rejected", "executed"]


class AgentProfile(_Strict):
    schema_version: Literal["1.0"]
    id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    institute_id: str = Field(min_length=1)
    primary_specialization: str = Field(min_length=1)
    jurisdictions: list[str]
    languages: list[str]
    scope: str = Field(min_length=1)
    out_of_scope: list[str]
    need_record_ref: str = Field(min_length=1)
    creation_reason: str = Field(min_length=1)
    alternatives_considered: list[str] = Field(min_length=1)
    skill_version_refs: list[str]
    model_policy_ref: str = Field(min_length=1)
    requested_tool_scopes: list[str]
    data_labels: list[DataLabel] = Field(min_length=1)
    qualification_refs: list[str]
    operating_status: Literal["planned", "in_review", "active", "dormant", "blocked", "archived"]
    qualification_status: Literal["not_evaluated", "experimental_limited", "task_qualified"]


class SkillManifest(_Strict):
    schema_version: Literal["1.0"]
    id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    trigger: str = Field(min_length=1)
    instructions_path: Literal["instructions.md"]
    input_schema_ref: str = Field(min_length=1)
    output_schema_ref: str = Field(min_length=1)
    requested_tool_scopes: list[str]
    dependency_version_refs: list[str]
    allowed_data_labels: list[DataLabel] = Field(min_length=1)
    limitations: list[str] = Field(min_length=1)
    origin_need_ref: str = Field(min_length=1)
    evaluation_suite_ref: str = Field(min_length=1)
    qualification_refs: list[str]
    qualified_combination_refs: list[str]
    operating_status: Literal["candidate", "active", "blocked", "retired"]
    package_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class Finding(_Strict):
    subject: str = Field(min_length=1)
    argument: str = Field(min_length=1)
    severity: Literal["minor", "major", "blocking"]
    scope: str = Field(min_length=1)


class ReviewReport(_Strict):
    """Repository addition (S1): output contract of the distinct-model first review. A model opinion, labelled as such."""

    verdict: Literal["no_objection", "objections", "cannot_assess"]
    checked_aspects: list[str] = Field(min_length=1)
    findings: list[Finding]
    uncertainties: list[str]


ClaimKind = Literal["observation", "computation", "derivation", "hypothesis", "legal_assessment", "normative_proposal"]


class EvidenceRef(_Strict):
    """Exactly one of: a source passage the run was given, or an experiment run of the project (S4)."""

    passage_id: str | None = Field(default=None, min_length=1)
    experiment_run_id: str | None = Field(default=None, min_length=1)
    note: str

    @model_validator(mode="after")
    def _exactly_one(self) -> EvidenceRef:
        if (self.passage_id is None) == (self.experiment_run_id is None):
            raise ValueError("Beleg verweist entweder auf passage_id oder auf experiment_run_id")
        return self


class ClaimItem(_Strict):
    local_id: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    kind: ClaimKind
    validity_conditions: str
    evidence: list[EvidenceRef]
    depends_on: list[str]


class ClaimSet(_Strict):
    """Repository addition (S2): what a generation run declares. The core, not the model, decides what counts as observed."""

    claims: list[ClaimItem] = Field(min_length=1)
    open_questions: list[str]
    omissions: list[str]


class EvaluationVerdict(_Strict):
    """Repository addition (S3): one evaluator verdict on one qualification case. A model opinion, aggregated by the core."""

    passed: bool
    score: int = Field(ge=0, le=100)
    reasons: list[str] = Field(min_length=1)
    false_alarm_risk: Literal["low", "medium", "high"]
    uncertainties: list[str]




# ---------------------------------------------------------------- source discovery (repository addition, 14.10.2026)


class SubQuestion(_Strict):
    id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    basis: Literal["question_space", "knowledge_goal", "method_frame", "gap", "counter_evidence"]


class SearchQuery(_Strict):
    query: str = Field(min_length=1)
    sub_question_ids: list[str] = Field(min_length=1)
    language: str = Field(min_length=2)
    kind: Literal["evidence", "counter_evidence", "primary_source", "official_source", "practice"]
    rationale: str = Field(min_length=1)


class SearchPlan(_Strict):
    """What a model derives from the mandate for the campus's own source discovery. The core runs the searches."""

    sub_questions: list[SubQuestion] = Field(min_length=1)
    queries: list[SearchQuery] = Field(min_length=1)
    not_searchable: list[str]


class SelectedHit(_Strict):
    hit_id: str = Field(min_length=1)
    sub_question_ids: list[str] = Field(min_length=1)
    reason: str = Field(min_length=1)
    expected_kind: Literal["primary_study", "official_source", "legal_text", "secondary_summary", "vendor_claim", "self_description", "other"]


class RejectedHit(_Strict):
    hit_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class SourceSelection(_Strict):
    """A model's reasoned choice among hits the core found; a hit becomes a source only once the internet service fetched it."""

    selected: list[SelectedHit]
    rejected_examples: list[RejectedHit]
    uncovered_sub_question_ids: list[str]
    follow_up_queries: list[str]


MODEL_BY_SCHEMA: dict[str, type[_Strict]] = {
    "model_catalog": ModelCatalog,
    "task_need": TaskNeed,
    "route_proposal": RouteProposal,
    "routing_decision": RoutingDecision,
    "agent_profile": AgentProfile,
    "skill_manifest": SkillManifest,
    "review_report": ReviewReport,
    "claim_set": ClaimSet,
    "evaluation_verdict": EvaluationVerdict,
    "search_plan": SearchPlan,
    "source_selection": SourceSelection,
}
