"""Integration tests run against a real PostgreSQL (CAMPUS_TEST_DATABASE_URL). They are skipped, never faked, without one."""
from __future__ import annotations

import os
import uuid

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from tests.conftest import REPO

TEST_URL = os.environ.get("CAMPUS_TEST_DATABASE_URL")


def _reachable(url: str) -> bool:
    try:
        engine = create_engine(url)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def database_url() -> str:
    if not TEST_URL:
        pytest.skip("CAMPUS_TEST_DATABASE_URL nicht gesetzt: Integrationstests brauchen PostgreSQL (kein SQLite-Ersatz)")
    if not _reachable(TEST_URL):
        pytest.skip("PostgreSQL unter CAMPUS_TEST_DATABASE_URL nicht erreichbar")
    os.environ["CAMPUS_MIGRATION_DATABASE_URL"] = TEST_URL
    os.environ["CAMPUS_DATABASE_URL"] = TEST_URL
    os.environ.setdefault("CAMPUS_CAPABILITY_LEVEL", "development_mock")
    os.environ["CAMPUS_COOKIE_SECURE"] = "false"
    cfg = Config(str(REPO / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO / "migrations"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    return TEST_URL


@pytest.fixture(scope="session")
def app(database_url):
    from campus_api.main import create_app
    from campus_api.settings import Settings, get_settings

    get_settings.cache_clear()
    return create_app(Settings.from_env())


@pytest.fixture(scope="session")
def session_factory(app, database_url):
    from campus_infra.db import make_engine, make_session_factory

    engine = make_engine(database_url)
    yield make_session_factory(engine)
    engine.dispose()


@pytest.fixture
def workspace_id() -> str:
    return f"ws-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c
