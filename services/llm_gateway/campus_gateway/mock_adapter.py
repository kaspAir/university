"""Deterministic mock adapter for tests. Its output is a synthetic fixture, never research evidence.

Behaviour switches live in the deployment's provider_model_id so tests can drive them from the catalog:
  mock-fail-*    -> ProviderUnavailable before send (retryable through a new route)
  mock-hang-*    -> ProviderTimeout (outcome unknown)
  mock-refuse-*  -> ProviderRefusal
Structured output: when the request carries an output contract, the mock returns JSON that satisfies the
two contracts the campus uses (route_proposal, review_report), driven by request.metadata.
"""
from __future__ import annotations

import hashlib
import json

from campus_contracts.models import Deployment

from .port import LLMRequest, LLMResponse, ProviderRefusal, ProviderTimeout, ProviderUnavailable, Usage


class MockAdapter:
    name = "mock"

    def __init__(self, fail_invocation_ids: set[str] | None = None, hang_invocation_ids: set[str] | None = None):
        self._fail = fail_invocation_ids or set()
        self._hang = hang_invocation_ids or set()

    def invoke(self, request: LLMRequest, deployment: Deployment) -> LLMResponse:
        native = deployment.provider_model_id or ""
        if request.invocation_id in self._hang or native.startswith("mock-hang"):
            raise ProviderTimeout("mock timeout after send")
        if request.invocation_id in self._fail or native.startswith("mock-fail"):
            raise ProviderUnavailable("mock provider unavailable before send")
        if native.startswith("mock-refuse"):
            raise ProviderRefusal("mock_category", "synthetic refusal")
        text = " ".join(str(b.get("text", "")) for b in request.content_blocks)
        digest = hashlib.sha256((request.invocation_id + text).encode("utf-8")).hexdigest()[:12]
        input_tokens = max(1, len(text) // 4)
        if request.output_contract is not None:
            reply = json.dumps(self._structured(request), ensure_ascii=False)
        else:
            reply = f"[MOCK {digest}] synthetic response; not a research result"
        return LLMResponse(
            invocation_id=request.invocation_id,
            deployment_id=deployment.id,
            effective_model_id=native or "mock-unknown",
            blocks=[{"type": "text", "text": reply, "origin": "mock_adapter", "synthetic": True}],
            tool_requests=[],
            stop_reason="end_turn",
            usage=Usage(input_tokens=input_tokens, output_tokens=max(1, len(reply) // 4)),
            safety_status="not_evaluated",
            raw_ref=None,
        )

    @staticmethod
    def _structured(request: LLMRequest) -> dict:
        props = set((request.output_contract or {}).get("properties", {}))
        if "selected_deployment_id" in props:  # route_proposal
            candidates = request.metadata.get("candidates") or []
            if not candidates:
                return {"task_id": request.metadata.get("task_id", "?"), "selected_deployment_id": "none",
                        "rationale": "Mock: keine Kandidaten übergeben", "evidence_refs": [], "alternatives": [], "uncertainties": []}
            chosen = candidates[0]
            return {
                "task_id": request.metadata.get("task_id", "?"),
                "selected_deployment_id": chosen["deployment_id"],
                "rationale": "Mock-Selector: erster zulässiger Kandidat; keine fachliche Eignungsbewertung.",
                "evidence_refs": list(chosen.get("evidence_refs", [])),
                "alternatives": [{"deployment_id": c["deployment_id"], "reason_not_selected": "Mock: nicht erster Kandidat"}
                                 for c in candidates[1:]],
                "uncertainties": ["Synthetischer Vorschlag eines Mock-Adapters."],
            }
        if "verdict" in props:  # review_report: a task class containing "contested" yields objections (test switch)
            verdict = "objections" if "contested" in str(request.metadata.get("task_class", "")) else "no_objection"
            findings = []
            if verdict == "objections":
                findings = [{"subject": "Synthetischer Einwand", "argument": "Mock-Prüfer meldet einen Einwand zu Testzwecken.",
                             "severity": "major", "scope": "gesamtes Artefakt"}]
            return {"verdict": verdict, "checked_aspects": ["Mock-Prüfung"], "findings": findings,
                    "uncertainties": ["Synthetische Prüfung; keine fachliche Kompetenz nachgewiesen."]}
        return {}
