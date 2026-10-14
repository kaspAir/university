"""The campus finds its own sources within the mandate: plan from the mandate, search by the core, reasoned selection, fetch through the
internet service, a source map with gaps. A fake search service and the network-free fetcher stand in; the mock model answers the two
contracts; nothing is paid. A hit is a pointer: only what was fetched carries passages."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from campus_api import services_search
from campus_domain.authz import Role
from campus_domain.errors import CampusError, ErrorCode
from campus_gateway.search_adapter import SearchHit, SearchUnavailable
from campus_infra.audit import Actor
from campus_infra.models import ArtifactVersion, AuditEvent, LLMInvocation, Reservation, Source
from tests.integration.conftest_fetch import make_fetcher
from tests.integration.test_s0_flow import Api, _authorize, _create_task, bootstrap, govern

pytestmark = pytest.mark.integration


class FakeSearch:
    name = "fake"

    def __init__(self, fail_query: str | None = None):
        self.queries: list[str] = []
        self.fail_query = fail_query

    def search(self, query, *, count=10, language="de", country="CH"):
        self.queries.append(query)
        if self.fail_query and self.fail_query in query:
            raise SearchUnavailable("HTTP 429")
        long_url = "https://public.example/" + "sehr-lange-adresse-" * 6 + "/fehlt"  # longer than the audit's 80-character object id
        return [SearchHit("Fehlende Seite", long_url, "liefert 404"),
                SearchHit("Testurteil", "https://public.example/urteil", "Einsprachefrist dreissig Tage"),
                SearchHit("Plain", "https://public.example/plain", "zwei Passagen"),
                SearchHit("PDF", "https://public.example/pdf", "nicht extrahierbar"),
                SearchHit("Unsicher", "http://public.example/http-only", "kein https")]


def _producer(boot, workspace_id):
    return Actor("user", boot["director_id"], workspace_id, frozenset({Role.PRODUCER}))


def _prepare(client, session_factory, workspace_id):
    boot = bootstrap(session_factory, workspace_id)
    api = Api(client)
    api.login(workspace_id, "leitung")
    state = govern(api, boot)
    r = api.post("/pricing", {"pricing_ref": "search-price-1", "currency": "CHF", "input_per_million": "0.00", "output_per_million": "5000.00",
                              "source_note": "Testpreis: CHF 5000 je Million Suchanfragen = 0.5 Rappen je Anfrage"})
    assert r.status_code == 201, r.text
    task = _create_task(api, state["project"]["project_id"], budget="5.00")
    decision_id = _authorize(api, task["task_id"]).json()["routing_decision_id"]
    return boot, api, state, task, decision_id


def test_discovery_plans_searches_selects_fetches_and_maps(client, session_factory, workspace_id):
    boot, api, state, task, decision_id = _prepare(client, session_factory, workspace_id)
    search = FakeSearch()
    with session_factory() as db:
        result = services_search.discover_sources(db, actor=_producer(boot, workspace_id), task_id=task["task_id"], routing_decision_id=decision_id,
                                                  round_no=1, gaps=[], max_queries=3, max_sources=2, correlation_id="t", search=search,
                                                  fetcher=make_fetcher(), search_pricing_ref="search-price-1")
    assert result["queries"] == 2 and len(search.queries) == 2, "the mock plan yields one evidence and one counter-evidence query, capped by max_queries"
    assert result["hits"] == 4, "http-only hits are dropped, duplicates across queries merged"
    assert result["selected"] == 2 and result["fetched"] == 1, "max_sources caps the choice; the missing page is a gap, not a crash"
    with session_factory() as db:
        art = db.get(ArtifactVersion, result["artifact_version_id"])
        assert art.kind == "source_map" and art.version == 1 and art.synthetic is True and art.sha256 == result["sha256"]
        content = art.content
        assert [q["status"] for q in content["queries"]] == ["ok", "ok"] and content["search_provider"] == "fake"
        assert {f["url"] for f in content["fetched"]} == {"https://public.example/urteil"}
        assert any(g["kind"] == "not_searchable" for g in content["gaps"])
        assert any(g["kind"] == "fetch_failed" and g["code"] == "FETCH_FAILED" and len(g["url"]) > 80 for g in content["gaps"]), "long failing url audited"
        urls = {s.normalized_url for s in db.execute(select(Source).where(Source.project_id == state["project"]["project_id"])).scalars()}
        assert "https://public.example/urteil" in urls and "https://public.example/pdf" not in urls, "a hit is not a source until fetched"
        purposes = sorted(p for (p,) in db.execute(select(Reservation.purpose).where(Reservation.task_id == task["task_id"])).all())
        assert purposes == ["discovery_plan", "discovery_select", "generation", "review", "search"]
        search_res = db.execute(select(Reservation).where(Reservation.task_id == task["task_id"], Reservation.purpose == "search")).scalar_one()
        assert search_res.state == "settled" and search_res.settled_minor == 1, "two requests at 0.5 Rappen, rounded up per request"
        journaled = {i.id for i in db.execute(select(LLMInvocation).where(LLMInvocation.routing_decision_id == decision_id)).scalars()}
        assert any(":discovery_plan:" in i for i in journaled) and any(":discovery_select:" in i for i in journaled), "both model calls journaled"
        searches = db.execute(select(AuditEvent).where(AuditEvent.workspace_id == workspace_id, AuditEvent.command == "source.search")).scalars().all()
        assert len(searches) == 2 and all(s.payload["provider"] == "fake" for s in searches)
    # the generation now sees the fetched passages; the dossier carries the source map with sub-questions and their sources
    run = api.post("/runs", {"task_id": task["task_id"], "routing_decision_id": decision_id}).json()
    assert run["passages_given"] >= 2 and run["state"] == "finished"
    dossier = api.get(f"/projects/{state['project']['project_id']}/dossier").json()
    assert dossier["source_map"]["rounds"] == 1 and dossier["source_map"]["fetched"] == 1
    q1 = next(s for s in dossier["source_map"]["sub_questions"] if s["id"] == "q1")
    assert len(q1["sources"]) == 1 and dossier["source_map"]["unanswerable"] == []
    page = client.get(f"/campus/artefakte/{result['artifact_version_id']}").text
    assert "Quellenkarte" in page and "public.example/urteil" in page and "not_searchable" in page
    assert "Quellenkarte des Campus" in client.get(f"/campus/dossiers/{state['project']['project_id']}?tab=quellen").text


def test_second_round_works_from_gaps_and_reports_uncovered_questions(client, session_factory, workspace_id):
    boot, api, state, task, decision_id = _prepare(client, session_factory, workspace_id)
    search = FakeSearch(fail_query="gegenbelege")
    with session_factory() as db:
        result = services_search.discover_sources(db, actor=_producer(boot, workspace_id), task_id=task["task_id"], routing_decision_id=decision_id,
                                                  round_no=2, gaps=["Welche Frist gilt?", "Gegenposition zur Offenlegung", "Kosten der Revision"],
                                                  max_queries=6, max_sources=1, correlation_id="t", search=search, fetcher=make_fetcher(),
                                                  search_pricing_ref="search-price-1")
    assert result["round"] == 2 and result["uncovered_sub_question_ids"] == ["q2", "q3"] and result["follow_up_queries"] == ["mock nachrecherche"]
    with session_factory() as db:
        content = db.get(ArtifactVersion, result["artifact_version_id"]).content
        assert content["gap_hints"][0] == "Welche Frist gilt?" and content["sub_questions"][0]["basis"] == "gap"
        statuses = {q["query"]: q["status"] for q in content["queries"]}
        assert any(s.startswith("unavailable") for s in statuses.values()), "a failed search is a recorded status, not a crash"
        assert sum(1 for g in content["gaps"] if g["kind"] == "uncovered_sub_question") == 2
        search_res = db.execute(select(Reservation).where(Reservation.task_id == task["task_id"], Reservation.purpose == "search")).scalar_one()
        assert search_res.settled_minor == 2, "only executed requests are paid: 3 of 4 (the unavailable one is free)"


def test_discovery_refuses_without_search_price_and_reports_missing_service(client, session_factory, workspace_id):
    boot, api, state, task, decision_id = _prepare(client, session_factory, workspace_id)
    with session_factory() as db:
        with pytest.raises(CampusError) as err:
            services_search.discover_sources(db, actor=_producer(boot, workspace_id), task_id=task["task_id"], routing_decision_id=decision_id,
                                             round_no=1, gaps=[], max_queries=3, max_sources=2, correlation_id="t", search=FakeSearch(),
                                             fetcher=make_fetcher(), search_pricing_ref="no-such-price")
        assert err.value.code == ErrorCode.PRICING_UNKNOWN
        db.rollback()
        skipped = services_search.discover_sources(db, actor=_producer(boot, workspace_id), task_id=task["task_id"], routing_decision_id=decision_id,
                                                   round_no=1, gaps=[], max_queries=3, max_sources=2, correlation_id="t", search=None,
                                                   fetcher=make_fetcher(), search_pricing_ref="search-price-1")
        assert skipped["skipped"] == "no_search_service"
        assert db.execute(select(ArtifactVersion).where(ArtifactVersion.kind == "source_map", ArtifactVersion.task_id == task["task_id"])).first() is None
    # the flow form tells the person whether the campus can discover sources on this instance
    page = client.get(f"/campus/auftrag/{state['mandate']['mandate_version_id']}").text
    assert "kein Suchdienst angebunden" in page or "Quellen selbst" in page
