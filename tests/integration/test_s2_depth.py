"""S2 depth tests requested after review. Each test separates three questions that must never be merged (Art. 13; UI rule 6.5):
  (1) structural evidence  - does a real, locatable evidence link exist?
  (2) content assessment   - what did the distinct reviewer say, and is dissent recorded without being resolved?
  (3) release capability   - may THIS dossier version be released now?
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from campus_api import services_sources
from campus_infra.models import RunAttempt
from tests.conftest import build_catalog_document
from tests.integration.conftest_fetch import make_fetcher
from tests.integration.test_s0_flow import Api, _authorize, _create_task, bootstrap, govern
from tests.integration.test_s2_workflow import temporal_env  # noqa: F401, F811  (session fixture: external Temporal in CI, SDK dev server locally)

pytestmark = pytest.mark.integration


@pytest.fixture
def campus(client, session_factory, workspace_id, monkeypatch):
    monkeypatch.setattr(services_sources, "default_fetcher", make_fetcher())
    boot = bootstrap(session_factory, workspace_id)
    api = Api(client)
    api.login(workspace_id, "leitung")
    state = govern(api, boot)
    return {"api": api, "boot": boot, **state}


def _fetch(api: Api, project_id: str, url: str):
    r = api.post("/sources/fetch", {"project_id": project_id, "url": url, "task_id": None})
    assert r.status_code == 201, r.text
    return r.json()


def _generate(api: Api, project_id: str, **need) -> dict:
    task = _create_task(api, project_id, **need)
    decision_id = _authorize(api, task["task_id"]).json()["routing_decision_id"]
    r = api.post("/runs", {"task_id": task["task_id"], "routing_decision_id": decision_id})
    assert r.status_code == 201, r.text
    return {"task": task, "run": r.json(), "decision_id": decision_id}


def _approve(api: Api, artifact: dict, option: str = "accept"):
    body = {"expected_sha256": artifact["sha256"], "option": option, "rationale": "Prüfung ohne Einwand, Dossier vollständig.",
            "alternatives_presented": ["annehmen", "begrenzt annehmen", "überarbeiten", "ablehnen"], "remaining_objections": [],
            "consequences": "Fassung kann extern freigegeben werden."}
    return api.post(f"/artifacts/{artifact['artifact_version_id']}/approvals", body)


# ------------------------------------------------------------------ 1. valid but substantively unfitting evidence

def test_valid_passage_that_does_not_support_the_claim(campus):
    """The mock producer cites a real passage. Structurally the link is valid; whether the passage SUPPORTS the statement is the
    reviewer's content judgement, and an objection makes the claim 'contested' and blocks release, without refuting it."""
    api, project_id = campus["api"], campus["project"]["project_id"]
    _fetch(api, project_id, "https://public.example/urteil")
    out = _generate(api, project_id, task_class="source_analysis_contested")
    claims = api.get(f"/projects/{project_id}/claims").json()["claims"]
    evidenced = [c for c in claims if c["declared_kind"] == "observation"]
    # (1) structural: real, locatable evidence; no structural flag
    for c in evidenced:
        assert c["flags"] == [] and c["evidence"] and api.get(f"/passages/{c['evidence'][0]['source_passage_id']}").json()["locator_check"] is True
        assert c["epistemic_status"] == "observation" and c["assessment_ref"].startswith("run:")
    # (2) content: the distinct reviewer objects; the claims of the run become contested relative to that review, nobody refutes them
    review = api.post(f"/runs/{out['run']['run_attempt_id']}/review").json()
    assert review["verdict"] == "objections" and review["findings"]
    claims = api.get(f"/projects/{project_id}/claims").json()["claims"]
    assert all(c["epistemic_status"] == "contested" and c["assessment_ref"] == f"review:{review['review_report_id']}" for c in claims)
    assert all(c["flags"] == [] for c in claims if c["declared_kind"] == "observation"), "structural evidence stays valid"
    assert not any(c["epistemic_status"] == "refuted" for c in claims)
    # (3) release: open objections block the release axis and the unconditional approval
    dossier = api.get(f"/projects/{project_id}/dossier").json()
    assert dossier["status_axes"]["release"] == "blocked" and dossier["status_axes"]["claims_by_epistemic_status"] == {"contested": 4}
    artifact = api.post(f"/projects/{project_id}/dossier").json()
    assert _approve(api, artifact).status_code == 403


# ------------------------------------------------------------------ 2. failure inside the generation activity

def test_provider_failure_inside_generation_is_replanned_by_the_workflow(campus, session_factory, temporal_env):  # noqa: F811
    """Deployment A fails before send. The workflow records the failed attempt, re-enters routing (A excluded), generates on B
    and reviews with a canonically different model. One failed and one finished attempt remain visible."""
    from tests.integration.test_s2_workflow import QUEUE, _worker

    loop, env = temporal_env
    api, project_id, ws = campus["api"], campus["project"]["project_id"], campus["boot"]["workspace_id"]
    doc = build_catalog_document() | {"revision": "test-catalog-flaky-a"}
    for dep in doc["deployments"]:
        if dep["id"] == "fixture-deployment-a":
            dep["provider_model_id"] = "mock-fail-a"
    catalog_id = api.post("/model-catalog/drafts", {"document": doc}, method="PUT").json()["catalog_id"]
    assert api.post(f"/model-catalog/{catalog_id}/activate").status_code == 200
    task = _create_task(api, project_id, budget="3.00")
    from campus_workers import activities
    from campus_workers.activities import FlowInput
    from campus_workers.workflows import ResearchTaskWorkflow

    activities.configure(session_factory)
    flow = FlowInput(workspace_id=ws, project_id=project_id, task_id=task["task_id"], source_urls=["https://public.example/plain"], selection_mode="llm")

    async def run():
        async with _worker(env, QUEUE + "-flaky"):
            return await env.client.execute_workflow(ResearchTaskWorkflow.run, flow, id=f"task-{task['task_id']}", task_queue=QUEUE + "-flaky")

    result = loop.run_until_complete(run())
    assert result["end_reason"] == "result"
    assert [a["end_reason"] for a in result["attempts"]] == ["provider_error", "result"]
    with session_factory() as db:
        attempts = db.execute(select(RunAttempt).where(RunAttempt.task_id == task["task_id"]).order_by(RunAttempt.attempt_no)).scalars().all()
        assert [a.end_reason for a in attempts] == ["provider_error", "result"]
    decision = api.get(f"/routing-decisions/{result['routing_decision_id']}").json()["decision"]
    assert decision["selected_deployment_id"] != "fixture-deployment-a"
    assert any(r.startswith("EXCLUDED:FAILED_IN_ATTEMPT:") for a in decision["candidate_assessments"] if a["deployment_id"] == "fixture-deployment-a"
               for r in a["exclusion_reasons"])
    # (1) structural: claims of the successful attempt carry evidence; (2) content: review done; (3) release: draft (no objections)
    dossier = api.get(f"/projects/{project_id}/dossier").json()
    assert dossier["claims"] and dossier["reviews"][0]["verdict"] == "no_objection" and dossier["status_axes"]["release"] == "draft"


# ------------------------------------------------------------------ 3. source -> claim A -> claim B

def test_chain_source_claim_a_claim_b(campus):
    """A is observed from a passage; B depends on A and has no evidence of its own. Structure, content and release are judged separately."""
    api, project_id = campus["api"], campus["project"]["project_id"]
    _fetch(api, project_id, "https://public.example/plain")
    out = _generate(api, project_id)
    graph = api.get(f"/projects/{project_id}/claims?include=graph").json()
    by_local = {c["local_id"]: c for c in graph["claims"]}
    a, b = by_local["c1"], by_local["m1"]
    # (1) structural: A has a real passage link; B has none and depends on A through an edge
    assert a["evidence"] and a["flags"] == [] and a["stored_kind"] == "observation"
    assert b["evidence"] == [] and "EVIDENCE_MISSING" in b["flags"] and b["stored_kind"] == "hypothesis"
    assert {"from": a["claim_version_id"], "to": b["claim_version_id"], "relation": "supports"} in graph["edges"]
    # (2) content: a clean review leaves A observed and B a hypothesis; nothing becomes "supported" by consensus
    review = api.post(f"/runs/{out['run']['run_attempt_id']}/review").json()
    assert review["verdict"] == "no_objection"
    graph = api.get(f"/projects/{project_id}/claims?include=graph").json()
    statuses = {c["local_id"]: c["epistemic_status"] for c in graph["claims"]}
    assert statuses["c1"] == "observation" and statuses["m1"] == "hypothesis"
    # (3) release: draft; after retracting the source, A and (through the edge) B need reassessment and release is blocked
    assert api.get(f"/projects/{project_id}/dossier").json()["status_axes"]["release"] == "draft"
    source_id = api.get(f"/projects/{project_id}/sources").json()[0]["source_id"]
    r = api.post(f"/sources/{source_id}/retract", {"reason": "Testrückzug"})
    affected = set(r.json()["reassessment_task"]["affected_claim_version_ids"])
    assert {a["claim_version_id"], b["claim_version_id"]} <= affected, "dependents of dependents are affected"
    graph = api.get(f"/projects/{project_id}/claims").json()["claims"]
    assert all(c["reassessment_required"] for c in graph) and all(c["epistemic_status"] != "refuted" for c in graph)
    assert api.get(f"/projects/{project_id}/dossier").json()["status_axes"]["release"] == "blocked"


# ------------------------------------------------------------------ 4. retraction after a granted dossier approval

def test_retraction_after_dossier_approval_invalidates_release(campus):
    api, project_id = campus["api"], campus["project"]["project_id"]
    _fetch(api, project_id, "https://public.example/urteil")
    out = _generate(api, project_id)
    assert api.post(f"/runs/{out['run']['run_attempt_id']}/review").json()["verdict"] == "no_objection"
    v1 = api.post(f"/projects/{project_id}/dossier").json()
    assert v1["release_axis"] == "draft" and _approve(api, v1).status_code == 201
    # the basis is withdrawn AFTER the approval
    source_id = api.get(f"/projects/{project_id}/sources").json()[0]["source_id"]
    assert api.post(f"/sources/{source_id}/retract", {"reason": "Herausgeber zieht die Quelle zurück"}).status_code == 200
    # (1) structural: the approved version v1 still exists with its hash and approval record (nothing is overwritten, Art. 23)
    artifacts = {a["version"]: a for a in api.get(f"/projects/{project_id}/artifacts").json() if a["kind"] == "dossier"}
    assert artifacts[1]["approval"]["valid_for_current"] is True and artifacts[1]["approval"]["basis_withdrawn_after_approval"] is True
    # (2) content: claims are marked for reassessment, not declared false
    claims = api.get(f"/projects/{project_id}/claims").json()["claims"]
    assert all(c["reassessment_required"] for c in claims) and not any(c["epistemic_status"] == "refuted" for c in claims)
    # (3) release: v1 may no longer be released; the rebuilt v2 is blocked and unapproved
    release = {"expected_sha256": v1["sha256"], "target": "preprint.example", "rationale": "x", "alternatives_presented": ["a", "b"],
               "remaining_objections": [], "consequences": "y"}
    r = api.post(f"/publications/{v1['artifact_version_id']}/release", release)
    assert r.status_code == 403 and "zurückgezogen" in r.json()["message"]
    v2 = api.post(f"/projects/{project_id}/dossier").json()
    assert v2["version"] == 2 and v2["sha256"] != v1["sha256"] and v2["release_axis"] == "blocked"
    assert {a["version"]: a for a in api.get(f"/projects/{project_id}/artifacts").json() if a["kind"] == "dossier"}[2]["approval"] is None
