"""Migration proof: records from before 0007/0008 are classified by the data they carry, never promoted to human approvals.

Runs the real chain 0006 -> head on PostgreSQL against rows inserted in the 0006 shape. Proves the classification and that the
classified rows do not apply anywhere (`_record_applies`); it does not prove anything about a production database's content."""
from __future__ import annotations

import uuid

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from campus_contracts.models import TaskNeed
from campus_contracts.validation import load_example
from campus_infra.models import QualificationRecord
from tests.conftest import REPO

pytestmark = pytest.mark.integration

INSERT = text("""
    INSERT INTO qualification_records (id, workspace_id, ref, canonical_model_id, task_class, suite_ref, result_summary, passed,
                                       evaluated_by, evaluated_by_kind, profile_ref, skill_refs, created_by, created_at)
    VALUES (:id, :ws, :ref, 'fixture/model-a@1', 'source_analysis', 'alt', 'Altbestand', true, :by, :by_kind, :profile,
            CAST(:skills AS jsonb), 'test', now())
""")


def _need(role: str) -> TaskNeed:
    doc = load_example("task_need") | {"role": role, "qualification_mode": "qualified", "agent_profile_version_ref": "fixture-ai-researcher@1",
                                       "skill_version_refs": ["fixture-source-analysis@1"]}
    return TaskNeed.model_validate(doc)


def test_0008_classifies_legacy_records_without_inventing_approvals(database_url, app):
    cfg = Config(str(REPO / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO / "migrations"))
    engine = create_engine(database_url)
    ws = f"ws-mig-{uuid.uuid4().hex[:6]}"
    rows = {
        "model-unbound": {"by": "fixture/model-b@1", "by_kind": "model", "profile": None, "skills": None},
        "human-unbound": {"by": "forschungsleitung", "by_kind": "human", "profile": None, "skills": None},
        "model-combination": {"by": "fixture/model-b@1", "by_kind": "model", "profile": "fixture-ai-researcher@1",
                              "skills": '["fixture-source-analysis@1"]'},
    }
    try:
        command.downgrade(cfg, "0006_s3_combination_binding")
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO workspaces (id, name, stopped, capability_level, created_at) VALUES (:id, 'Migration', false, "
                              "'development_mock', now())"), {"id": ws})
            for ref, row in rows.items():
                conn.execute(INSERT, {"id": f"Q-{uuid.uuid4().hex[:8]}", "ws": ws, "ref": ref, **row})
        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            got = {r.ref: r for r in conn.execute(text("SELECT ref, kind, role FROM qualification_records WHERE workspace_id = :ws"), {"ws": ws})}
        assert got["model-unbound"].kind == "legacy_unbound" and got["model-unbound"].role is None, "a model never becomes a human approval"
        assert got["human-unbound"].kind == "bootstrap" and got["human-unbound"].role is None, "the role is not inferred"
        assert got["model-combination"].kind == "combination" and got["model-combination"].role == "producer"
        # none of the legacy rows applies anywhere: the model record is history, the human record has no declared role
        from campus_api.services_research import _record_applies

        for ref in ("model-unbound", "human-unbound"):
            rec = QualificationRecord(ref=ref, canonical_model_id="fixture/model-a@1", task_class="source_analysis", passed=True,
                                      kind=got[ref].kind, role=got[ref].role, profile_ref=None, skill_refs=None)
            assert not _record_applies(rec, _need("producer")) and not _record_applies(rec, _need("reviewer")), ref
        combo = QualificationRecord(ref="c", canonical_model_id="fixture/model-a@1", task_class="source_analysis", passed=True, kind="combination",
                                    role="producer", profile_ref="fixture-ai-researcher@1", skill_refs=["fixture-source-analysis@1"])
        assert _record_applies(combo, _need("producer")) and not _record_applies(combo, _need("reviewer"))
    finally:
        command.upgrade(cfg, "head")  # leave the database at head for the remaining tests
        engine.dispose()
