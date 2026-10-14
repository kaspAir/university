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
            raise ProviderRefusal("mock_category", "synthetic refusal", usage=Usage(input_tokens=17, output_tokens=0))
        text = " ".join(str(b.get("text", "")) for b in request.content_blocks)
        digest = hashlib.sha256((request.invocation_id + text).encode("utf-8")).hexdigest()[:12]
        input_tokens = max(1, len(text) // 4)
        if request.output_contract is not None and "badjson" in str(request.metadata.get("task_class", "")):
            reply = "{ not valid json"  # content-check failure probe: the call happened, the answer violates the contract
        elif request.output_contract is not None:
            reply = json.dumps(self._structured(request), ensure_ascii=False)
        else:
            reply = f"[MOCK {digest}] synthetic response; not a research result"
            if "FAIL" in request.system_rules:
                reply += " FAIL"  # a profile whose documents carry the marker produces answers the mock evaluator fails
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
        if "finding_responses" in props:  # claim_revision: one evidenced observation per passage, one response per finding
            passage_ids = list(request.metadata.get("passage_ids") or [])
            claims = [{"local_id": f"r{i + 1}", "statement": f"Überarbeitete Beobachtung aus Passage {pid}.", "kind": "observation",
                       "validity_conditions": "Nur für die zitierte Quellenfassung.", "evidence": [{"passage_id": pid, "experiment_run_id": None,
                                                                                                  "note": "Mock"}], "depends_on": []}
                      for i, pid in enumerate(passage_ids[:2])] or [{"local_id": "r1", "statement": "Mock-Hypothese.", "kind": "hypothesis",
                                                                       "validity_conditions": "keine", "evidence": [], "depends_on": []}]
            return {"claims": claims, "open_questions": ["Mock: offene Frage"], "omissions": ["Mock: nichts Weiteres"],
                    "finding_responses": [{"finding_id": f, "disposition": "changed", "explanation": "Mock: korrigiert", "claim_local_ids": ["r1"]}
                                          for f in request.metadata.get("finding_ids") or []]}
        if "finding_checks" in props:  # revision_review: every handed pair supports, every finding resolved
            return {"verdict": "no_objection", "checked_aspects": ["Mock-Paarprüfung"], "findings": [], "uncertainties": ["Synthetisch."],
                    "pair_checks": [{"claim_local_id": c, "passage_id": pid, "support": "supports", "reason": "Mock"}
                                    for c, pid in request.metadata.get("pairs") or []],
                    "finding_checks": [{"finding_id": f, "resolved": True, "reason": "Mock: erledigt"} for f in request.metadata.get("finding_ids") or []]}
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
        if "claims" in props:  # claim_set: one evidenced observation per given passage, plus one unsupported "computation" (T04 probe)
            passage_ids = list(request.metadata.get("passage_ids") or [])
            claims = [{"local_id": f"c{i + 1}", "statement": f"Synthetische Beobachtung aus Passage {pid}.", "kind": "observation",
                       "validity_conditions": "Nur für die zitierte Quellenfassung.", "evidence": [{"passage_id": pid, "note": "Mock"}],
                       "depends_on": []} for i, pid in enumerate(passage_ids[:3])]
            claims.append({"local_id": "m1", "statement": "Synthetischer Messwert ohne Lauf: 42 Prozent.", "kind": "computation",
                           "validity_conditions": "keine", "evidence": [], "depends_on": [c["local_id"] for c in claims[:1]]})
            for i, xid in enumerate(request.metadata.get("experiment_run_ids") or []):  # S4: computations citing frozen experiment runs
                claims.append({"local_id": f"x{i + 1}", "statement": f"Synthetischer Messwert aus Experiment {xid}.", "kind": "computation",
                               "validity_conditions": "Nur für diesen Lauf.", "evidence": [{"experiment_run_id": xid, "note": "Mock"}], "depends_on": []})
            return {"claims": claims, "open_questions": ["Mock: offene Frage"], "omissions": ["Mock: nichts Weiteres untersucht"]}
        if "sub_questions" in props:  # search_plan: one sub-question per hint the core passed, one query each, plus a counter-evidence query
            hints = list(request.metadata.get("gap_hints") or []) or ["Mock-Teilfrage aus dem Mandat"]
            subs = [{"id": f"q{i + 1}", "question": h, "basis": "gap" if request.metadata.get("round_no", 1) > 1 else "question_space"}
                    for i, h in enumerate(hints[:3])]
            queries = [{"query": f"mock {s['question'][:40]}", "sub_question_ids": [s["id"]], "language": "de", "kind": "evidence",
                        "rationale": "Mock-Suchanfrage"} for s in subs]
            queries.append({"query": "mock gegenbelege", "sub_question_ids": [subs[0]["id"]], "language": "de", "kind": "counter_evidence",
                            "rationale": "Mock: Gegenbelege"})
            return {"sub_questions": subs, "queries": queries, "not_searchable": ["Mock: interne Unterlagen sind nicht öffentlich"]}
        if "uncovered_sub_question_ids" in props:  # source_selection: the first hits, mapped to the first sub-question
            hit_ids = list(request.metadata.get("hit_ids") or [])
            subs = list(request.metadata.get("sub_question_ids") or ["q1"])
            limit = int(request.metadata.get("max_sources") or 2)
            return {"selected": [{"hit_id": h, "sub_question_ids": [subs[0]], "reason": "Mock: erster Treffer", "expected_kind": "other"}
                                 for h in hit_ids[:limit]],
                    "rejected_examples": [{"hit_id": h, "reason": "Mock: nicht gewählt"} for h in hit_ids[limit:limit + 1]],
                    "uncovered_sub_question_ids": subs[1:], "follow_up_queries": ["mock nachrecherche"] if subs[1:] else []}
        if "false_alarm_risk" in props:  # evaluation_verdict: the case fails when its input carries the marker FAIL
            text_in = " ".join(str(b.get("text", "")) for b in request.content_blocks)
            failed = "FAIL" in text_in
            return {"passed": not failed, "score": 0 if failed else 100,
                    "reasons": ["Mock-Prüfer: Marker FAIL im Fall" if failed else "Mock-Prüfer: Fall ohne Marker"],
                    "false_alarm_risk": "high" if failed else "low", "uncertainties": ["Synthetische Prüfung ohne fachliche Kompetenz."]}
        if "verdict" in props:  # review_report: a task class containing "contested" yields objections (test switch)
            task_class = str(request.metadata.get("task_class", ""))
            verdict = "objections" if "contested" in task_class else "no_objection"
            findings = []
            if "inconsistent" in task_class:  # contradictory report: lenient verdict with a blocking finding (S1.1 finding 4 probe)
                findings = [{"subject": "Widersprüchlicher Einwand", "argument": "Mock-Prüfer meldet blockierenden Einwand trotz no_objection.",
                             "severity": "blocking", "scope": "gesamtes Artefakt"}]
            elif verdict == "objections":
                findings = [{"subject": "Synthetischer Einwand", "argument": "Mock-Prüfer meldet einen Einwand zu Testzwecken.",
                             "severity": "major", "scope": "gesamtes Artefakt"}]
            return {"verdict": verdict, "checked_aspects": ["Mock-Prüfung"], "findings": findings,
                    "uncertainties": ["Synthetische Prüfung; keine fachliche Kompetenz nachgewiesen."]}
        return {}
