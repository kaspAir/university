from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, text

from campus_infra.models import ModelCatalogRevision, PolicyVersion

from ..settings import get_settings

router = APIRouter(tags=["operations"])


@router.get("/health")
def health() -> dict:
    """Process liveness only. LLM latency is never part of this verdict (chapter 19)."""
    s = get_settings()
    return {"status": "ok", "capability_level": s.capability_level, "sandbox_runner": s.sandbox_runner,
            "vault": "configured" if s.vault_database_url else "missing"}


@router.get("/ready")
def ready(request: Request) -> JSONResponse:
    """Readiness: database reachable, an active policy and an active catalog exist somewhere in the instance."""
    checks: dict[str, str] = {}
    factory = request.app.state.session_factory
    try:
        with factory() as db:
            db.execute(text("SELECT 1"))
            checks["database"] = "ok"
            checks["active_policy"] = "ok" if db.execute(select(PolicyVersion.id).where(PolicyVersion.state == "active")).first() else "missing"
            checks["active_catalog"] = "ok" if db.execute(select(ModelCatalogRevision.id).where(ModelCatalogRevision.state == "active")).first() else "missing"
    except Exception as exc:  # noqa: BLE001 - reported as a readiness failure, not hidden
        checks["database"] = f"error: {type(exc).__name__}"
    vault = getattr(request.app.state, "vault", None)
    if vault is None:
        checks["vault"] = "missing"
    else:
        try:
            vault.ping()
            checks["vault"] = "ok"
        except Exception as exc:  # noqa: BLE001
            checks["vault"] = f"error: {type(exc).__name__}"
    ok = all(v == "ok" for v in checks.values())
    return JSONResponse({"ready": ok, "checks": checks, "capability_level": get_settings().capability_level}, status_code=200 if ok else 503)
