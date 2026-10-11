"""End-to-end S0 slice against PostgreSQL: mandate -> decision -> budget -> task -> route -> mock run -> audit.

Every guard on the path has a negative test here (T02, T03, T11, A02, A04, A05, A14, audit immutability, CSRF, authz).
"""
from __future__ import annotations

import copy
import threading
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from campus_api import services_identity
from campus_contracts.validation import load_example
from campus_domain.authz import Role
from campus_domain.errors import CampusError
from campus_domain.money import Money
from campus_infra import budget_service
from campus_infra.audit import Actor
from campus_infra.models import AuditEvent, Reservation, RouteProposalRecord, RoutingDecisionRecord
from tests.conftest import active_policy_document, build_catalog_document

pytestmark = pytest.mark.integration

PASSWORD = "test-passwort-mit-zwoelf-zeichen"
MANDATE_DOC = {
    "title": "Pilotmandat Quellenanalyse (synthetisch)",
    "question_space": "Verfahrensgarantien in automatisierten Verwaltungsverfahren, Testmaterial",
    "knowledge_goal": "Prüfen, ob der S0-Kern einen Mandatsweg vollständig abbildet",
    "method_frame": "Quellenkartierung mit Gegenprüfung durch zweites Modell",
    "data_labels": ["public"],
    "access_rules": "Nur öffentliche Quellen über den Internetdienst",
    "risk_rules": "Keine Personendaten, keine externe Publikation",
    "responsible_user_id": "set-at-runtime",
    "review_requirements": "Jedes tragende Artefakt durch anderes kanonisches Modell prüfen",
    "duration_days": 30,
    "report_points": ["Tag 10", "Tag 30"],
}


class Api:
    """Thin client: CSRF header and a fresh Idempotency-Key per call unless one is given."""

    def __init__(self, client: TestClient):
        self.client = client
        self.csrf = None
        self.base = "/api/v1"

    def login(self, workspace_id: str, username: str) -> dict:
        r = self.client.post(f"{self.base}/auth/login", json={"workspace_id": workspace_id, "username": username, "password": PASSWORD})
        assert r.status_code == 200, r.text
        self.csrf = r.json()["csrf_token"]
        return r.json()

    def post(self, path: str, json=None, *, key: str | None = None, method: str = "POST"):
        headers = {"X-CSRF-Token": self.csrf or "", "Idempotency-Key": key or uuid.uuid4().hex}
        return self.client.request(method, f"{self.base}{path}", json=json, headers=headers)

    def get(self, path: str):
        return self.client.get(f"{self.base}{path}")


def bootstrap(session_factory, workspace_id: str, *, catalog_doc: dict | None = None) -> dict:
    """Workspace, one director user (also operator+producer for S0 tests) and one operator-only user."""
    with session_factory() as db:
        services_identity.init_workspace(db, workspace_id=workspace_id, name="Testcampus", capability_level="development_mock")
        system = Actor("system", "test-bootstrap", workspace_id, frozenset(Role))
        director = services_identity.create_user(db, actor=system, workspace_id=workspace_id, username="leitung",
                                                 display_name="Forschungsleitung (Test)", password=PASSWORD,
                                                 roles=["research_director", "operator", "producer"], correlation_id="boot")
        services_identity.create_user(db, actor=system, workspace_id=workspace_id, username="betrieb", display_name="Betrieb (Test)",
                                      password=PASSWORD, roles=["operator"], correlation_id="boot")
        db.commit()
        director_id = director.id
    return {"workspace_id": workspace_id, "director_id": director_id, "catalog_doc": catalog_doc or build_catalog_document()}


def govern(api: Api, boot: dict) -> dict:
    """Activate policy and catalog, register a price, approve a mandate, create a project and a candidate agent."""
    r = api.post("/policies", {"document": active_policy_document() | {"activation_state": "draft"}})
    assert r.status_code == 201, r.text
    policy_id = r.json()["policy_id"]
    assert api.post(f"/policies/{policy_id}/activate").status_code == 200
    r = api.post("/model-catalog/drafts", {"document": boot["catalog_doc"]}, method="PUT")
    assert r.status_code == 201, r.text
    catalog_id = r.json()["catalog_id"]
    assert api.post(f"/model-catalog/{catalog_id}/activate").status_code == 200
    r = api.post("/pricing", {"pricing_ref": "test-price-1", "currency": "CHF", "input_per_million": "10.00",
                              "output_per_million": "50.00", "source_note": "Synthetischer Testpreis, 11.10.2026"})
    assert r.status_code == 201, r.text
    doc = MANDATE_DOC | {"responsible_user_id": boot["director_id"]}
    r = api.post("/mandates", {"document": doc, "budget": "1000.00", "currency": "CHF"})
    assert r.status_code == 201, r.text
    mandate = r.json()
    r = api.post(f"/mandates/{mandate['mandate_version_id']}/approve", {
        "expected_version": 1, "option": "accept", "rationale": "Synthetisches Pilotmandat, Budget gedeckt.",
        "alternatives_presented": ["annehmen", "begrenzt annehmen", "überarbeiten", "ablehnen"],
        "remaining_objections": [], "consequences": "Root-Budget CHF 1 000 wird eröffnet.", "conditions": []})
    assert r.status_code == 200, r.text
    approved = r.json()
    assert approved["state"] == "approved" and approved["root_budget_account_id"]
    institutes = api.get("/institutes").json()
    r = api.post("/projects", {"mandate_version_id": mandate["mandate_version_id"], "institute_id": institutes[2]["institute_id"],
                               "title": "Rechtliches Gehör (synthetisch)", "budget": "500.00"})
    assert r.status_code == 201, r.text
    project = r.json()
    r = api.post("/agents/candidates", {"document": load_example("agent_profile")})
    assert r.status_code == 201, r.text
    return {"policy_id": policy_id, "catalog_id": catalog_id, "mandate": approved, "project": project,
            "agent_id": r.json()["agent_profile_version_id"]}


def task_need(agent_ref: str = "fixture-ai-researcher@1", mode: str = "sandbox_experiment", **overrides) -> dict:
    need = copy.deepcopy(load_example("task_need"))
    need.update({"agent_profile_version_ref": agent_ref, "qualification_mode": mode, "routing_policy_ref": "routing-policy-proposal-1"})
    need.update(overrides)
    for k in ("task_id", "workspace_id", "project_id", "mandate_version_ref", "budget_account_id"):
        need.pop(k, None)
    return need


@pytest.fixture
def campus(client, session_factory, workspace_id):
    boot = bootstrap(session_factory, workspace_id)
    api = Api(client)
    api.login(workspace_id, "leitung")
    state = govern(api, boot)
    return {"api": api, "boot": boot, **state}


def _create_task(api: Api, project_id: str, budget: str | None = "1.00", **need_overrides) -> dict:
    r = api.post(f"/projects/{project_id}/tasks", {"task_need": task_need(**need_overrides), "budget": budget})
    assert r.status_code == 201, r.text
    return r.json()


def _authorize(api: Api, task_id: str, selected: str = "fixture-deployment-a"):
    proposal = load_example("route_proposal") | {"task_id": task_id, "selected_deployment_id": selected}
    return api.post(f"/tasks/{task_id}/routing-decisions", {"proposal": proposal, "selection_mode": "fixture"})


# ------------------------------------------------------------------ identity

def test_csrf_and_authentication(client, session_factory, workspace_id):
    bootstrap(session_factory, workspace_id)
    r = client.get("/api/v1/auth/me")
    assert r.status_code == 401 and r.json()["code"] == "UNAUTHENTICATED"
    api = Api(client)
    api.login(workspace_id, "leitung")
    assert client.get("/api/v1/auth/me").json()["roles"] == ["operator", "producer", "research_director"]
    r = client.post("/api/v1/workspace/stop", json={"reason": "x"}, headers={"Idempotency-Key": "k"})  # no CSRF header
    assert r.status_code == 403 and r.json()["code"] == "CSRF_FAILED"
    assert r.headers["X-Campus-Capability-Level"] == "development_mock"


def test_operator_cannot_approve_mandate(client, session_factory, workspace_id):
    bootstrap(session_factory, workspace_id)
    api = Api(client)
    api.login(workspace_id, "betrieb")
    r = api.post("/mandates", {"document": MANDATE_DOC, "budget": "10.00", "currency": "CHF"})
    assert r.status_code == 403
    body = r.json()
    assert body["code"] == "FORBIDDEN" and body["blocked_action"] == "mandate.create" and "research_director" in body["required_resolution"]


# ------------------------------------------------------------------ mandate, versions, idempotency

def test_mandate_flow_versions_and_idempotency(campus, session_factory):
    api = campus["api"]
    mv = campus["mandate"]
    detail = api.get(f"/mandates/{mv['mandate_version_id']}").json()
    assert detail["state"] == "approved" and len(detail["decisions"]) == 1
    assert detail["decisions"][0]["object_hash"] == detail["document_hash"]
    assert api.get("/decisions?state=open").json() == []
    # deciding an already approved version is an invalid transition; a wrong expected version is a conflict
    body = {"expected_version": 2, "option": "reject", "rationale": "x", "alternatives_presented": ["a", "b"],
            "remaining_objections": [], "consequences": "y", "conditions": []}
    r = api.post(f"/mandates/{mv['mandate_version_id']}/approve", body)
    assert r.status_code == 409 and r.json()["code"] == "VERSION_CONFLICT"
    body["expected_version"] = 1
    r = api.post(f"/mandates/{mv['mandate_version_id']}/approve", body)
    assert r.status_code == 409 and r.json()["code"] == "INVALID_TRANSITION"
    # idempotency: same key + same body replays; same key + other body is a mismatch
    key = uuid.uuid4().hex
    first = api.post("/mandates", {"document": MANDATE_DOC | {"responsible_user_id": "u"}, "budget": "20.00", "currency": "CHF"}, key=key)
    replay = api.post("/mandates", {"document": MANDATE_DOC | {"responsible_user_id": "u"}, "budget": "20.00", "currency": "CHF"}, key=key)
    assert first.status_code == 201 and replay.json() == first.json()
    other = api.post("/mandates", {"document": MANDATE_DOC | {"responsible_user_id": "u"}, "budget": "21.00", "currency": "CHF"}, key=key)
    assert other.status_code == 409 and other.json()["code"] == "IDEMPOTENCY_MISMATCH"
    assert len(api.get("/decisions?state=open").json()) == 1  # the new draft waits for a human
    # blocked attempts are audited
    with session_factory() as db:
        blocked = db.execute(select(AuditEvent).where(AuditEvent.workspace_id == campus["boot"]["workspace_id"],
                                                      AuditEvent.policy_decision == "blocked:VERSION_CONFLICT")).scalars().all()
        assert blocked and blocked[0].command == "mandate.decide"


def test_incomplete_mandate_is_rejected_not_defaulted(campus):
    r = campus["api"].post("/mandates", {"document": {"title": "nur Titel"}, "budget": "1.00", "currency": "CHF"})
    assert r.status_code == 422 and "question_space" in r.json()["message"]


def test_t02_project_needs_an_approved_mandate(campus):
    api = campus["api"]
    draft = api.post("/mandates", {"document": MANDATE_DOC, "budget": "5.00", "currency": "CHF"}).json()
    institutes = api.get("/institutes").json()
    r = api.post("/projects", {"mandate_version_id": draft["mandate_version_id"], "institute_id": institutes[0]["institute_id"],
                               "title": "ohne Mandat", "budget": "1.00"})
    assert r.status_code == 409
    body = r.json()
    assert body["code"] == "MANDATE_NOT_APPROVED" and body["blocked_action"] == "project.create" and body["trace_id"]


# ------------------------------------------------------------------ budget

def test_t03_child_budget_cannot_exceed_free_parent_amount(campus):
    api = campus["api"]
    root_id = campus["mandate"]["root_budget_account_id"]
    tree = {a["id"]: a for a in api.get("/budget/tree").json()}
    assert tree[root_id]["free_minor"] == 100000 - 50000  # project took CHF 500 of CHF 1 000
    r = api.post(f"/budget/accounts/{root_id}/children", {"name": "zu gross", "limit": "500.01", "currency": "CHF"})
    assert r.status_code == 409 and r.json()["code"] == "BUDGET_EXHAUSTED"
    r = api.post(f"/budget/accounts/{root_id}/children", {"name": "Replikation (geschützt)", "limit": "100.00", "currency": "CHF",
                                                          "protected_kind": "replication"})
    assert r.status_code == 201 and r.json()["limit"] == "CHF 100"


def test_a05_parallel_reservations_do_not_overallocate(campus, session_factory):
    api = campus["api"]
    task = _create_task(api, campus["project"]["project_id"], budget="0.30")
    account_id = task["budget_account_id"]
    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    lock = threading.Lock()

    def worker():
        with session_factory() as db:
            barrier.wait()
            try:
                budget_service.reserve(db, account_id=account_id, amount=Money(20, "CHF"), task_id=task["task_id"], purpose="generation")
                db.commit()
                result = "ok"
            except CampusError as err:
                db.rollback()
                result = err.code.value
            except DBAPIError as err:  # serialization/deadlock surfaces as a typed DB error, never as a silent double booking
                db.rollback()
                result = f"db:{type(err.orig).__name__}"
        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    [t.start() for t in threads]
    [t.join(timeout=30) for t in threads]
    assert sorted(outcomes) == ["BUDGET_EXHAUSTED", "ok"], outcomes
    with session_factory() as db:
        reserved = db.execute(select(Reservation).where(Reservation.account_id == account_id)).scalars().all()
        assert len(reserved) == 1 and reserved[0].amount_minor == 20


# ------------------------------------------------------------------ routing

def test_route_authorized_with_distinct_reviewer_and_two_reservations(campus, session_factory):
    api = campus["api"]
    task = _create_task(api, campus["project"]["project_id"])
    r = _authorize(api, task["task_id"])
    assert r.status_code == 201, r.text
    decision = r.json()["decision"]
    assert decision["state"] == "authorized"
    assert decision["selected_deployment_id"] == "fixture-deployment-a" and decision["review_deployment_id"] == "fixture-deployment-b"
    assert decision["required_review"] is True
    excluded = {a["deployment_id"]: a["exclusion_reasons"] for a in decision["candidate_assessments"] if not a["eligible"]}
    assert "NOT_ENABLED" in excluded["fixture-deployment-c"]
    with session_factory() as db:
        res = db.execute(select(Reservation).where(Reservation.task_id == task["task_id"]).order_by(Reservation.purpose)).scalars().all()
        assert [(x.purpose, x.amount_minor) for x in res] == [("generation", 15), ("review", 17)]


def test_a02_rejected_proposal_keeps_its_note(campus, session_factory):
    api = campus["api"]
    task = _create_task(api, campus["project"]["project_id"])
    r = _authorize(api, task["task_id"], selected="fixture-deployment-c")
    assert r.status_code == 409 and r.json()["code"] == "PROPOSAL_INVALID"
    detail = api.get(f"/tasks/{task['task_id']}").json()
    assert detail["route_proposals"][0]["outcome"] == "rejected"
    assert detail["route_proposals"][0]["proposal"]["rationale"]
    assert any(not a["eligible"] for a in detail["route_proposals"][0]["assessments"])
    with session_factory() as db:
        assert db.execute(select(RoutingDecisionRecord).where(RoutingDecisionRecord.task_id == task["task_id"])).first() is None


def test_a04_missing_review_reserve_blocks_before_generation(campus, session_factory):
    api = campus["api"]
    task = _create_task(api, campus["project"]["project_id"], budget="0.20")  # covers generation (15) but not review (17)
    r = _authorize(api, task["task_id"])
    assert r.status_code == 409 and r.json()["code"] == "BUDGET_EXHAUSTED"
    with session_factory() as db:
        assert db.execute(select(Reservation).where(Reservation.task_id == task["task_id"])).first() is None  # atomic: nothing reserved
        assert db.execute(select(RouteProposalRecord).where(RouteProposalRecord.task_id == task["task_id"])).first() is None


def test_task_bound_to_another_policy_is_blocked(campus):
    api = campus["api"]
    task = _create_task(api, campus["project"]["project_id"], routing_policy_ref="other-policy")
    r = _authorize(api, task["task_id"])
    assert r.status_code == 409 and r.json()["code"] == "POLICY_NOT_ACTIVE"


def test_a14_unknown_price_blocks_the_route(client, session_factory, workspace_id):
    doc = build_catalog_document()
    for dep in doc["deployments"]:
        if dep["id"] == "fixture-deployment-b":
            dep["pricing_ref"] = "price-never-registered"
    boot = bootstrap(session_factory, workspace_id, catalog_doc=doc)
    api = Api(client)
    api.login(workspace_id, "leitung")
    state = govern(api, boot)
    task = _create_task(api, state["project"]["project_id"])
    r = _authorize(api, task["task_id"])
    assert r.status_code == 409 and r.json()["code"] == "PRICING_UNKNOWN"


# ------------------------------------------------------------------ runs

def test_t11_unregistered_agent_cannot_run(campus):
    api = campus["api"]
    task = _create_task(api, campus["project"]["project_id"], agent_ref="ghost-agent@1")
    decision_id = _authorize(api, task["task_id"]).json()["routing_decision_id"]
    r = api.post("/runs", {"task_id": task["task_id"], "routing_decision_id": decision_id})
    assert r.status_code == 409 and r.json()["code"] == "AGENT_NOT_REGISTERED"


def test_qualified_mode_needs_a_qualified_profile(campus):
    api = campus["api"]
    task = _create_task(api, campus["project"]["project_id"], mode="qualified")
    decision_id = _authorize(api, task["task_id"]).json()["routing_decision_id"]
    r = api.post("/runs", {"task_id": task["task_id"], "routing_decision_id": decision_id})
    assert r.status_code == 409 and r.json()["code"] == "QUALIFICATION_MISSING"


def test_t12_self_certified_agent_is_not_registered(campus):
    doc = load_example("agent_profile") | {"version": "2", "qualification_status": "task_qualified", "qualification_refs": []}
    r = campus["api"].post("/agents/candidates", {"document": doc})
    assert r.status_code == 409 and r.json()["code"] == "QUALIFICATION_MISSING"


def test_mock_run_settles_cost_and_leaves_evidence(campus, session_factory):
    api = campus["api"]
    project_id = campus["project"]["project_id"]
    task = _create_task(api, project_id)
    decision_id = _authorize(api, task["task_id"]).json()["routing_decision_id"]
    r = api.post("/runs", {"task_id": task["task_id"], "routing_decision_id": decision_id})
    assert r.status_code == 201, r.text
    run = r.json()
    assert run["state"] == "finished" and run["end_reason"] == "result" and run["synthetic"] is True
    assert run["cost"] != "offen" and run["review_required"] is True
    events = [e["event_type"] for e in api.get(f"/projects/{project_id}/events").json()]
    assert {"TaskCreated", "RoutingDecided", "RunStarted", "RunEnded"} <= set(events)
    runs = api.get(f"/projects/{project_id}/runs").json()
    assert runs[-1]["run_attempt_id"] == run["run_attempt_id"]
    inventory = api.get("/inventory").json()
    agent = next(a for a in inventory["agents"] if a["ref"] == "fixture-ai-researcher@1")
    assert agent["runs"] == 1 and agent["qualification_status"] == "not_evaluated"
    with session_factory() as db:
        gen = db.execute(select(Reservation).where(Reservation.task_id == task["task_id"], Reservation.purpose == "generation")).scalar_one()
        assert gen.state == "settled" and gen.settled_minor is not None
        review = db.execute(select(Reservation).where(Reservation.task_id == task["task_id"], Reservation.purpose == "review")).scalar_one()
        assert review.state == "reserved"  # review has not happened: reserve stays bound, status stays open
    # replaying the same run command with a new key is a new attempt, not a hidden retry of the first
    r2 = api.post("/runs", {"task_id": task["task_id"], "routing_decision_id": decision_id})
    assert r2.status_code == 409 and r2.json()["code"] == "BUDGET_EXHAUSTED"  # generation reserve is consumed


def test_stop_blocks_runs_and_resume_needs_a_reason(campus):
    api = campus["api"]
    task = _create_task(api, campus["project"]["project_id"])
    decision_id = _authorize(api, task["task_id"]).json()["routing_decision_id"]
    assert api.post("/workspace/stop", {"reason": "Test-Stopp Forschungsleitung"}).status_code == 200
    r = api.post("/runs", {"task_id": task["task_id"], "routing_decision_id": decision_id})
    assert r.status_code == 409 and r.json()["code"] == "WORKSPACE_STOPPED"
    assert api.get("/inventory").status_code == 200  # registers stay usable (A09)
    assert api.post("/workspace/resume", {"reason": "  "}).status_code == 422
    assert api.post("/workspace/resume", {"reason": "Ursache behoben, dokumentiert"}).status_code == 200
    assert api.post("/runs", {"task_id": task["task_id"], "routing_decision_id": decision_id}).status_code == 201


# ------------------------------------------------------------------ audit

def test_audit_events_are_immutable_in_the_database(campus, session_factory):
    with session_factory() as db:
        event_id = db.execute(select(AuditEvent.id).where(AuditEvent.workspace_id == campus["boot"]["workspace_id"])).scalars().first()
        assert event_id is not None
        with pytest.raises(DBAPIError, match="append-only"):
            db.execute(text("UPDATE audit_events SET command = 'tampered' WHERE id = :id"), {"id": event_id})
            db.commit()
        db.rollback()
        with pytest.raises(DBAPIError, match="append-only"):
            db.execute(text("DELETE FROM audit_events WHERE id = :id"), {"id": event_id})
            db.commit()


def test_readiness_reports_components(client, campus):
    r = client.get("/api/v1/ready")
    assert r.status_code == 200 and r.json()["checks"] == {"database": "ok", "active_policy": "ok", "active_catalog": "ok"}
