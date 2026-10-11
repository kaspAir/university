from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from campus_infra.db import make_engine, make_session_factory

from . import errors
from .errors import trace_id
from .routers import auth, factory, governance, health, research, research_flow
from .settings import Settings, get_settings

API_PREFIX = "/api/v1"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(settings.database_url)
        app.state.engine = engine
        app.state.session_factory = make_session_factory(engine)
        yield
        engine.dispose()

    app = FastAPI(
        title="Phronesis-Forschungscampus API",
        version="0.1.0-S0",
        description="Kontrollkern des Forschungscampus. Stufe S0: Verträge, Mandate, Policy, Budget, Register, Mock-Gateway.",
        lifespan=lifespan,
        docs_url=f"{API_PREFIX}/docs",
        openapi_url=f"{API_PREFIX}/openapi.json",
    )
    errors.install(app)

    @app.middleware("http")
    async def _headers(request: Request, call_next):
        tid = trace_id(request)
        response = await call_next(request)
        response.headers["X-Trace-Id"] = tid
        response.headers["X-Campus-Capability-Level"] = settings.capability_level  # always visible (chapter 13)
        return response

    app.include_router(health.router, prefix=API_PREFIX)
    app.include_router(auth.router, prefix=API_PREFIX)
    app.include_router(governance.router, prefix=API_PREFIX)
    app.include_router(research.router, prefix=API_PREFIX)
    app.include_router(research_flow.router, prefix=API_PREFIX)
    app.include_router(factory.router, prefix=API_PREFIX)
    return app


# ASGI entry point: `uvicorn campus_api.main:app`. Without settings (e.g. docs build) the import still succeeds.
try:
    app = create_app()
except RuntimeError:
    app = None  # type: ignore[assignment]
