"""Targeted revision of a reviewed run over the same sources: passages reach the models as closed blocks with their register entry, the
previous claims and open findings are the revision's order, the producer answers every finding, the distinct-model review judges every
claim-passage pair and every finding, and only findings it judges resolved are closed (with the review as evidence). No new research."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from campus_api import services_research, services_sources
from campus_gateway.gateway import Gateway
from campus_infra.models import AuditEvent, FindingRecord
from tests.integration.conftest_fetch import make_fetcher
from tests.integration.test_s0_flow import Api, _authorize, _create_task, bootstrap, govern

pytestmark = pytest.mark.integration


@pytest.fixture
def captured(monkeypatch):
    seen: list = []
    original = Gateway.invoke

    def spy(self, request, deployment, decision):
        seen.append(request)
        return original(self, request, deployment, decision)

    monkeypatch.setattr(Gateway, "invoke", spy)
    monkeypatch.setattr(services_sources, "default_fetcher", make_fetcher())
    return seen


def test_revision_answers_findings_and_review_closes_only_judged_ones(client, session_factory, workspace_id, captured):
    boot = bootstrap(session_factory, workspace_id)
    api = Api(client)
    api.login(workspace_id, "leitung")
    state = govern(api, boot)
    project_id = state["project"]["project_id"]
    for url in ("https://public.example/urteil", "https://public.example/plain"):
        assert api.post("/sources/fetch", {"project_id": project_id, "url": url, "task_id": None}).status_code == 201
    # a first run whose review raises a major finding (mock switch: task class contains "contested")
    task = _create_task(api, project_id, budget="5.00", task_class="source_analysis_contested")
    first = api.post("/runs", {"task_id": task["task_id"], "routing_decision_id": _authorize(api, task["task_id"]).json()["routing_decision_id"]}).json()
    review = api.post(f"/runs/{first['run_attempt_id']}/review").json()
    assert review["verdict"] == "objections" and review["findings"], review
    finding_id = review["findings"][0]["id"]

    gen = next(r for r in captured if r.invocation_id == f"{first['run_attempt_id']}:gen:1")
    passages = [b for b in gen.content_blocks if b.get("origin") == "source_passage"]
    assert passages and all(b["text"].startswith("<passage id=") and b["text"].rstrip().endswith(f'</passage id="{b["passage_id"]}">') for b in passages)
    assert 'url="https://public.example/urteil"' in "".join(b["text"] for b in passages), "the register entry travels with each passage"
    first_review = next(r for r in captured if r.invocation_id == f"{first['run_attempt_id']}:review:1")
    assert any(b.get("origin") == "claim_pairs" and "<cited passage=" in b["text"] for b in first_review.content_blocks), "pairs also in a normal review"

    # the revision: a new route on the same task, the previous run as order, no discovery, the director's note
    decision_id = _authorize(api, task["task_id"]).json()["routing_decision_id"]
    with session_factory() as db:
        from campus_domain.authz import Role
        from campus_infra.audit import Actor

        actor = Actor("user", boot["director_id"], workspace_id, frozenset({Role.PRODUCER}))
        run = services_research.start_run(db, actor=actor, task_id=task["task_id"], routing_decision_id=decision_id, correlation_id="t",
                                          revision_of_run_id=first["run_attempt_id"], revision_note="Prozentangaben prüfen")
        db.commit()
    assert run["state"] == "finished" and run["finding_responses"][0]["finding_id"] == finding_id
    rgen = next(r for r in captured if r.invocation_id == f"{run['run_attempt_id']}:gen:1")
    rev_text = "\n".join(b["text"] for b in rgen.content_blocks if b.get("origin") == "revision")
    assert finding_id in rev_text and "Prozentangaben prüfen" in rev_text and "previous_claims" in rev_text
    assert rgen.output_contract["properties"].get("finding_responses"), "the revision answers each finding"

    rrev = api.post(f"/runs/{run['run_attempt_id']}/review").json()
    rreq = next(r for r in captured if r.invocation_id == f"{run['run_attempt_id']}:review:1")
    assert rreq.output_contract["properties"].get("pair_checks") and rreq.output_contract["properties"].get("finding_checks")
    assert rrev["verdict"] == "no_objection"
    with session_factory() as db:
        assert db.get(FindingRecord, finding_id).status == "resolved"
        closing = db.execute(select(AuditEvent).where(AuditEvent.command == "finding.resolve", AuditEvent.object_id == finding_id)).scalar_one()
        assert closing.payload["review_report_id"] == rrev["review_report_id"] and closing.payload["revision_run_id"] == run["run_attempt_id"]

    dossier = api.get(f"/projects/{project_id}/dossier").json()
    entry = next(r for r in dossier["runs"] if r["run_attempt_id"] == run["run_attempt_id"])
    assert entry["finding_responses"][0]["disposition"] == "changed"
    assert any(v["pair_checks"] for v in dossier["reviews"]), "pair judgements are part of the dossier"
    aid = api.post(f"/projects/{project_id}/dossier", {}).json()["artifact_version_id"]
    page = client.get(f"/campus/artefakte/{aid}").text
    assert "Antworten auf die Einwände der Vorfassung" in page and "erledigt (Gegenprüfung)" in page and "Paarprüfung" in page


def test_revision_of_a_foreign_or_unfinished_run_is_refused(client, session_factory, workspace_id):
    from campus_domain.errors import CampusError, ErrorCode

    boot = bootstrap(session_factory, workspace_id)
    api = Api(client)
    api.login(workspace_id, "leitung")
    state = govern(api, boot)
    task = _create_task(api, state["project"]["project_id"], budget="5.00")
    decision_id = _authorize(api, task["task_id"]).json()["routing_decision_id"]
    with session_factory() as db:
        from campus_domain.authz import Role
        from campus_infra.audit import Actor

        actor = Actor("user", boot["director_id"], workspace_id, frozenset({Role.PRODUCER}))
        with pytest.raises(CampusError) as err:
            services_research.start_run(db, actor=actor, task_id=task["task_id"], routing_decision_id=decision_id, correlation_id="t",
                                        revision_of_run_id="R-doesnotexist")
        assert err.value.code == ErrorCode.NOT_FOUND
