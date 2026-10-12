"""Case vault: protected evaluation material lives outside the application schema (S4; architecture chapter 13, T05).

The cases of a qualification suite are stored in schema `vault`, reachable only through a separate connection with the role
`campus_vault`. The application role has no privilege on that schema; the sandbox gets no connection at all. The application schema
keeps only the hash and the count. Material is append-only: the vault role can insert and read, never update or delete.
"""
from __future__ import annotations

import json
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.engine import Engine


class CaseVault(Protocol):
    def put(self, suite_id: str, workspace_id: str, cases: list[dict], cases_sha256: str) -> None: ...

    def get(self, suite_id: str) -> list[dict] | None: ...

    def ping(self) -> None: ...


class DbCaseVault:
    def __init__(self, engine: Engine):
        self._engine = engine

    def put(self, suite_id: str, workspace_id: str, cases: list[dict], cases_sha256: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(text("INSERT INTO vault.suite_cases (suite_id, workspace_id, cases, cases_sha256) VALUES (:id, :ws, CAST(:cases AS jsonb), :sha)"),
                         {"id": suite_id, "ws": workspace_id, "cases": json.dumps(cases, ensure_ascii=False), "sha": cases_sha256})

    def get(self, suite_id: str) -> list[dict] | None:
        with self._engine.connect() as conn:
            row = conn.execute(text("SELECT cases FROM vault.suite_cases WHERE suite_id = :id"), {"id": suite_id}).first()
        return None if row is None else list(row[0])

    def ping(self) -> None:
        with self._engine.connect() as conn:
            conn.execute(text("SELECT 1 FROM vault.suite_cases WHERE false"))
