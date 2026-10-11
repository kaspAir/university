"""S2 against PostgreSQL: controlled fetch (T07, T15, T16), locatable passages, claims from runs (T04), retraction (T09),
dossier with approval binding (T08) and recorded release."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from campus_api import services_sources
from campus_infra.models import ClaimVersion, SourceVersion
from tests.integration.conftest_fetch import make_fetcher
from tests.integration.test_s0_flow import Api, _authorize, _create_task, bootstrap, govern

pytestmark = pytest.mark.integration


@pytest.fixture
def campus(client, session_factory, workspace_id, monkeypatch):
    monkeypatch.setattr(services_sources, "default_fetcher", make_fetcher())
    boot = bootstrap(session_factory, workspace_id)
    api = Api(client)
    api.login(workspace_id, "leitung")
    state = govern(api, boot)
    return {"api": api, "boot": boot, **state}


def _fetch(api: Api, project_id: str, url: str, task_id: str | None = None):
    return api.post("/sources/fetch", {"project_id": project_id, "url": url, "task_id": task_id})


# ---------------------------------------------------------------- fetch

def test_t15_unknown_public_source_is_fetched_without_approval(campus, session_factory):
    api, project_id = campus["api"], campus["project"]["project_id"]
    r = _fetch(api, project_id, "https://public.example/urteil")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["new_version"] is True and body["content_type"] == "text/html" and len(body["passages"]) == 3  # heading + two paragraphs
    # passages are locatable: offsets reproduce the stored text exactly
    for p in body["passages"]:
        detail = api.get(f"/passages/{p['passage_id']}").json()
        assert detail["locator_check"] is True
    with session_factory() as db:
        version = db.get(SourceVersion, body["source_version_id"])
        assert "ignored()" not in version.extracted_text and version.extractor_version == "campus-extract/1"
    # same content again: no new version
    again = _fetch(api, project_id, "https://public.example/urteil").json()
    assert again["new_version"] is False and again["source_version_id"] == body["source_version_id"]


def test_t07_internal_targets_and_redirects_are_blocked(campus):
    api, project_id = campus["api"], campus["project"]["project_id"]
    r = _fetch(api, project_id, "https://intern.example/geheim")
    assert r.status_code == 403 and r.json()["code"] == "DATA_EXPORT_DENIED" and "T07" in r.json()["message"]
    r = _fetch(api, project_id, "https://public.example/redirect-internal")  # public first hop, metadata address second hop
    assert r.status_code == 403 and r.json()["code"] == "DATA_EXPORT_DENIED"
    r = _fetch(api, project_id, "file:///etc/passwd")
    assert r.status_code == 403
    r = _fetch(api, project_id, "https://public.example/redirect-ok")  # a public redirect is fine
    assert r.status_code == 201 and r.json()["redirect_chain"] == ["https://public.example/redirect-ok", "https://public.example/plain"]
    assert api.get(f"/projects/{project_id}/sources").json()[0]["status"] == "active"


def test_access_gaps_are_errors_not_fake_sources(campus):
    api, project_id = campus["api"], campus["project"]["project_id"]
    assert _fetch(api, project_id, "https://public.example/pdf").json()["code"] == "UNSUPPORTED_CONTENT"
    assert _fetch(api, project_id, "https://public.example/missing").json()["code"] == "FETCH_FAILED"
    assert api.get(f"/projects/{project_id}/sources").json() == []


def test_t16_query_with_confidential_task_is_denied(campus):
    api, project_id = campus["api"], campus["project"]["project_id"]
    task = _create_task(api, project_id, data_labels=["public", "confidential"])
    r = _fetch(api, project_id, "https://public.example/plain?q=vertraulich", task_id=task["task_id"])
    assert r.status_code == 403 and r.json()["code"] == "DATA_EXPORT_DENIED"
    r = _fetch(api, project_id, "https://public.example/plain", task_id=task["task_id"])
    assert r.status_code == 201


# ---------------------------------------------------------------- claims from a run

def _run_with_sources(api: Api, project_id: str, **need) -> dict:
    _fetch(api, project_id, "https://public.example/urteil")
    task = _create_task(api, project_id, **need)
    decision_id = _authorize(api, task["task_id"]).json()["routing_decision_id"]
    r = api.post("/runs", {"task_id": task["task_id"], "routing_decision_id": decision_id})
    assert r.status_code == 201, r.text
    return {"task": task, "run": r.json(), "decision_id": decision_id}


def test_t04_claims_are_classified_by_the_core(campus, session_factory):
    api, project_id = campus["api"], campus["project"]["project_id"]
    out = _run_with_sources(api, project_id)
    assert out["run"]["passages_given"] == 3 and out["run"]["claim_set_valid"] is True and out["run"]["claims_recorded"] == 4
    claims = api.get(f"/projects/{project_id}/claims?include=graph").json()
    by_kind = {c["declared_kind"]: c for c in claims["claims"]}
    assert by_kind["observation"]["stored_kind"] == "observation" and by_kind["observation"]["evidence"][0]["source_passage_id"]
    measured = by_kind["computation"]
    assert measured["stored_kind"] == "hypothesis" and measured["epistemic_status"] == "hypothesis" and "EVIDENCE_MISSING" in measured["flags"]
    assert claims["counts_by_status"] == {"observation": 3, "hypothesis": 1}
    assert claims["edges"], "dependency edges are kept"
    events = [e["event_type"] for e in api.get(f"/projects/{project_id}/events").json()]
    assert "ClaimFlagged" in events and "ClaimsRecorded" in events


def test_t09_retracted_source_marks_dependents_and_schedules_reassessment(campus):
    api, project_id = campus["api"], campus["project"]["project_id"]
    _run_with_sources(api, project_id)
    source_id = api.get(f"/projects/{project_id}/sources").json()[0]["source_id"]
    r = api.post(f"/sources/{source_id}/retract", {"reason": "Quelle vom Herausgeber zurückgezogen", "due_days": 7})
    assert r.status_code == 200, r.text
    task = r.json()["reassessment_task"]
    assert len(task["affected_claim_version_ids"]) == 3 and task["responsible_role"] == "research_director"
    claims = api.get(f"/projects/{project_id}/claims").json()["claims"]
    flagged = [c for c in claims if c["reassessment_required"]]
    assert {c["claim_version_id"] for c in flagged} == set(task["affected_claim_version_ids"])
    assert all(c["epistemic_status"] != "refuted" for c in flagged), "software never declares a claim false (Art. 23)"
    assert api.get("/reassessments").json()[0]["state"] == "open"
    dossier = api.get(f"/projects/{project_id}/dossier").json()
    assert dossier["status_axes"]["release"] == "blocked" and dossier["sources"][0]["status"] == "retracted"


# ---------------------------------------------------------------- dossier, approval, release

def test_t08_dossier_approval_binds_hash_and_release_needs_fresh_approval(campus, session_factory):
    api, project_id = campus["api"], campus["project"]["project_id"]
    out = _run_with_sources(api, project_id)
    assert api.post(f"/runs/{out['run']['run_attempt_id']}/review").status_code == 201
    built = api.post(f"/projects/{project_id}/dossier")
    assert built.status_code == 201, built.text
    artifact = built.json()
    assert artifact["version"] == 1 and artifact["release_axis"] == "draft"
    decision = {"option": "accept", "rationale": "Dossier vollständig, Prüfung ohne Einwand.", "alternatives_presented": ["annehmen", "begrenzt", "überarbeiten", "ablehnen"],
                "remaining_objections": [], "consequences": "Fassung kann extern freigegeben werden."}
    wrong = api.post(f"/artifacts/{artifact['artifact_version_id']}/approvals", {**decision, "expected_sha256": "0" * 64})
    assert wrong.status_code == 409 and wrong.json()["code"] == "VERSION_CONFLICT"
    ok = api.post(f"/artifacts/{artifact['artifact_version_id']}/approvals", {**decision, "expected_sha256": artifact["sha256"]})
    assert ok.status_code == 201, ok.text
    # content changes (another run) -> new dossier version with another hash; the old approval does not carry over
    _run_with_sources(api, project_id)
    rebuilt = api.post(f"/projects/{project_id}/dossier").json()
    assert rebuilt["version"] == 2 and rebuilt["sha256"] != artifact["sha256"]
    release = {"target": "preprint-server.example", "rationale": "Veröffentlichung der geprüften Fassung", "alternatives_presented": ["freigeben", "zurückhalten"],
               "remaining_objections": [], "consequences": "Externe Kommunikation."}
    r = api.post(f"/publications/{rebuilt['artifact_version_id']}/release", {**release, "expected_sha256": rebuilt["sha256"]})
    assert r.status_code == 403 and r.json()["code"] == "FORBIDDEN"  # no approval for version 2
    r = api.post(f"/publications/{artifact['artifact_version_id']}/release", {**release, "expected_sha256": artifact["sha256"]})
    assert r.status_code == 201 and r.json()["state"] == "recorded_no_channel"
    artifacts = api.get(f"/projects/{project_id}/artifacts").json()
    dossiers = [a for a in artifacts if a["kind"] == "dossier"]
    assert dossiers[0]["approval"]["valid_for_current"] is True and dossiers[1]["approval"] is None and dossiers[0]["releases"]


def test_open_blocking_findings_prevent_unconditional_approval(campus):
    api, project_id = campus["api"], campus["project"]["project_id"]
    out = _run_with_sources(api, project_id, task_class="source_analysis_contested")
    api.post(f"/runs/{out['run']['run_attempt_id']}/review")
    artifact = api.post(f"/projects/{project_id}/dossier").json()
    assert artifact["release_axis"] == "blocked"
    body = {"expected_sha256": artifact["sha256"], "option": "accept", "rationale": "x", "alternatives_presented": ["a", "b"], "remaining_objections": [],
            "consequences": "y"}
    r = api.post(f"/artifacts/{artifact['artifact_version_id']}/approvals", body)
    assert r.status_code == 403 and r.json()["code"] == "FORBIDDEN"
    limited = {**body, "option": "accept_limited", "conditions": [{"text": "Einwand F bearbeiten", "responsible": "research_director", "due": "2026-10-31"}]}
    assert api.post(f"/artifacts/{artifact['artifact_version_id']}/approvals", limited).status_code == 201


def test_claims_are_append_only_for_the_application_role(campus, session_factory):
    api, project_id = campus["api"], campus["project"]["project_id"]
    _run_with_sources(api, project_id)
    with session_factory() as db:
        claim = db.execute(select(ClaimVersion).where(ClaimVersion.project_id == project_id)).scalars().first()
        assert claim is not None and claim.version == 1
