from __future__ import annotations

import os

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, text

from campus_gateway.adapters import approved_adapter_names
from campus_infra.models import ModelCatalogRevision, PolicyVersion

from ..settings import get_settings

router = APIRouter(tags=["operations"])


def pilot_limitations(capability_level: str, sandbox_runner: str, temporal_mode: str, adapters: list[str],
                      search_provider: str | None = None) -> list[str]:
    """The limits that stay in force, derived from the running configuration (never from a claim). Shown in /health and in the UI."""
    real = [a for a in adapters if a != "mock"]
    limitations = []
    if temporal_mode.startswith("dev-server"):
        limitations.append("Temporal im Entwicklungsmodus (SQLite-Persistenz); nicht produktionsreif (ADR-0008)")
    if sandbox_runner == "none":
        limitations.append("Keine Sandbox: keine Ausführung fremden Codes auf diesem Host (S4 abgeschaltet)")
    if capability_level == "development_mock":
        if real:
            limitations.append(f"Leistungsebene development_mock mit echten Adaptern ({', '.join(real)}): Modellaufrufe kosten Geld; "
                               "Egress-Proxy fehlt (restricted_research nicht erreicht), Quellenabruf nur über den Internetdienst mit SSRF-Schutz")
        else:
            limitations.append("Leistungsebene development_mock: Modellaufrufe über den Mock-Adapter, Ergebnisse synthetisch")
    if "mock" in adapters and real:
        limitations.append("Mock-Adapter bleibt freigegeben: Ergebnisse von Mock-Deployments sind synthetisch und werden so gekennzeichnet")
    if not search_provider:
        limitations.append("Kein Suchdienst angebunden (CAMPUS_SEARCH_PROVIDER): der Campus erschliesst keine Quellen selbst, nur eingegebene URLs")
    return limitations


@router.get("/health")
def health() -> dict:
    """Process liveness only. LLM latency is never part of this verdict (chapter 19)."""
    s = get_settings()
    temporal_mode = os.environ.get("CAMPUS_TEMPORAL_MODE", "unknown")
    adapters = approved_adapter_names()
    return {"status": "ok", "capability_level": s.capability_level, "sandbox_runner": s.sandbox_runner,
            "vault": "configured" if s.vault_database_url else "missing", "build_sha": os.environ.get("CAMPUS_BUILD_SHA", "unknown"),
            "temporal_mode": temporal_mode, "approved_adapters": adapters, "search_provider": s.search_provider,
            "pilot_limitations": pilot_limitations(s.capability_level, s.sandbox_runner, temporal_mode, adapters, s.search_provider)}


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
