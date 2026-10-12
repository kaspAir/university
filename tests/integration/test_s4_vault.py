"""S4: protected evaluation material lives in the vault schema behind its own role (chapter 13, T05)."""
from __future__ import annotations

import os
import re

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError

from tests.integration.test_s0_flow import Api, bootstrap, govern
from tests.integration.test_s3_factory import CASES, _candidate, _evaluate, _suite

pytestmark = pytest.mark.integration


@pytest.fixture
def campus(client, session_factory, workspace_id):
    boot = bootstrap(session_factory, workspace_id)
    api = Api(client)
    api.login(workspace_id, "leitung")
    state = govern(api, boot)
    r = api.post("/qualifications", {"ref": "boot-evaluator-b", "canonical_model_id": "fixture/model-b@1", "task_class": "evaluation", "suite_ref": "bootstrap",
                                     "result_summary": "Prüferrolle (Bootstrap)", "passed": True, "evaluated_by": "forschungsleitung",
                                     "evaluated_by_kind": "human", "kind": "bootstrap", "role": "evaluator"})
    assert r.status_code == 201, r.text
    return {"api": api, "boot": boot, **state}


def _role_url(role: str, password: str) -> str | None:
    """The test database URL rewritten to another role; None when the role cannot connect (then the role test is skipped)."""
    base = os.environ.get(f"CAMPUS_TEST_{role.upper()}_DATABASE_URL")
    if not base:
        test_url = os.environ["CAMPUS_TEST_DATABASE_URL"]
        base = re.sub(r"//[^@]+@", f"//{role}:{password}@", test_url)
    try:
        engine = create_engine(base)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return base
    except Exception:  # noqa: BLE001
        return None


def test_cases_live_in_the_vault_not_in_the_application_schema(campus, database_url):
    """Proves: the application table has no cases column; the vault row carries the cases under the registered hash; the hash in the
    application schema equals the one in the vault."""
    api = campus["api"]
    suite = _suite(api)
    engine = create_engine(database_url)
    with engine.connect() as conn:
        cols = {r[0] for r in conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'qualification_suites'"))}
        assert "cases" not in cols and "case_count" in cols
        row = conn.execute(text("SELECT cases, cases_sha256 FROM vault.suite_cases WHERE suite_id = :id"), {"id": suite["suite_id"]}).first()
    engine.dispose()
    assert row is not None and row[1] == suite["cases_sha256"] and [c["case_id"] for c in row[0]] == [c["case_id"] for c in CASES]
    view = api.get("/qualification-suites/suite-source-analysis").json()
    assert view["cases_visible"] is True and view["case_count"] == len(CASES) and view["cases"] == CASES


def test_application_role_cannot_read_the_vault(campus):
    """Proves (database boundary): the application role reads the suite table but is denied on vault.suite_cases. Skipped where the role
    cannot connect (CI sidecar without roles)."""
    url = _role_url("campus_app", "campus_app_dev")
    if url is None:
        pytest.skip("Anwendungsrolle campus_app nicht verbindbar; Rollen-Grenze hier nicht prüfbar")
    _suite(campus["api"])
    engine = create_engine(url)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM qualification_suites")).scalar() >= 1
        with pytest.raises(ProgrammingError) as e:
            conn.execute(text("SELECT count(*) FROM vault.suite_cases"))
        assert "permission denied" in str(e.value).lower()
    engine.dispose()


def test_vault_role_is_append_only(campus):
    """Proves: the vault role can read and insert but neither update nor delete material (T10: material is never changed in place)."""
    url = _role_url("campus_vault", "campus_vault_dev")
    if url is None:
        pytest.skip("Tresorrolle campus_vault nicht verbindbar")
    suite = _suite(campus["api"])
    engine = create_engine(url)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM vault.suite_cases WHERE suite_id = :id"), {"id": suite["suite_id"]}).scalar() == 1
        for stmt in ("UPDATE vault.suite_cases SET cases = '[]'::jsonb WHERE suite_id = :id", "DELETE FROM vault.suite_cases WHERE suite_id = :id"):
            with pytest.raises(ProgrammingError) as e:
                conn.execute(text(stmt), {"id": suite["suite_id"]})
            assert "permission denied" in str(e.value).lower()
            conn.rollback()
        with pytest.raises(ProgrammingError):
            conn.execute(text("SELECT threshold_percent FROM public.qualification_suites"))
    engine.dispose()


def test_tampered_vault_material_stops_the_evaluation(campus, database_url):
    """Proves: material changed in the vault no longer matches the registered hash; the qualification run refuses to start."""
    api, project_id = campus["api"], campus["project"]["project_id"]
    suite = _suite(api)
    ref = _candidate(api)
    engine = create_engine(database_url)  # the owner may change anything; the point is detection, not prevention at this level
    with engine.begin() as conn:
        conn.execute(text("UPDATE vault.suite_cases SET cases = '[{\"case_id\": \"k1\", \"input\": \"leichter\", \"rubric\": \"r\"}]'::jsonb WHERE suite_id = :id"),
                     {"id": suite["suite_id"]})
    engine.dispose()
    r = _evaluate(api, project_id, ref)
    assert r.status_code == 422 and "Hash" in r.json()["message"], r.text
