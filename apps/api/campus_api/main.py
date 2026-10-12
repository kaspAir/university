from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from campus_infra.blobstore import LocalBlobStore
from campus_infra.db import make_engine, make_session_factory
from campus_infra.vault import DbCaseVault

from . import errors
from .errors import trace_id
from .routers import auth, experiments, factory, governance, health, pilot, research, research_flow
from .settings import Settings, get_settings
from .web import router as web

API_PREFIX = "/api/v1"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(settings.database_url)
        app.state.engine = engine
        app.state.session_factory = make_session_factory(engine)
        # S4: protected material through its own connection and role; sandbox runner only as configured; blobs for experiment outputs
        vault_engine = make_engine(settings.vault_database_url) if settings.vault_database_url else None
        app.state.vault = DbCaseVault(vault_engine) if vault_engine is not None else None
        app.state.sandbox_runner = build_sandbox_runner(settings)
        app.state.blobs = LocalBlobStore(settings.blob_root)
        yield
        engine.dispose()
        if vault_engine is not None:
            vault_engine.dispose()

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
    app.include_router(experiments.router, prefix=API_PREFIX)
    app.include_router(pilot.router, prefix=API_PREFIX)
    # first surface (chapter 18): server-rendered pages behind the same session; static files are the design tokens and two small files
    app.mount("/campus/static", StaticFiles(directory=str(Path(__file__).parent / "web" / "static")), name="campus-static")
    app.include_router(web.router, prefix="/campus")

    @app.exception_handler(web.LoginRequired)
    async def _login_required(request: Request, exc: web.LoginRequired):
        return RedirectResponse("/campus/login", status_code=303)

    @app.get("/", include_in_schema=False)
    async def _root():
        return RedirectResponse("/campus", status_code=307)

    return app


def build_sandbox_runner(settings: Settings):
    """The configured runner or None. No runner means the capability is off (chapter 13); nothing is substituted."""
    if settings.sandbox_runner == "docker":
        from campus_sandbox.docker_runner import DockerSandboxRunner

        return DockerSandboxRunner(image=settings.sandbox_image, workroot=settings.sandbox_workroot)
    if settings.sandbox_runner == "mock":
        from campus_sandbox.mock_runner import MockSandboxRunner

        return MockSandboxRunner()
    return None


# ASGI entry point: `uvicorn campus_api.main:app`. Without settings (e.g. docs build) the import still succeeds.
try:
    app = create_app()
except RuntimeError:
    app = None  # type: ignore[assignment]
