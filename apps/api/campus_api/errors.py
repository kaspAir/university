"""Error handling: one error object (chapter 17), no silent fallback, no stack traces on the wire."""
from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from campus_domain.errors import CampusError, ErrorCode

log = logging.getLogger("campus_api")


def trace_id(request: Request) -> str:
    existing = getattr(request.state, "trace_id", None)
    if existing:
        return existing
    request.state.trace_id = request.headers.get("X-Trace-Id") or uuid.uuid4().hex
    return request.state.trace_id


def install(app: FastAPI) -> None:
    @app.exception_handler(CampusError)
    async def _campus_error(request: Request, exc: CampusError) -> JSONResponse:
        body = exc.to_object(trace_id(request))
        if exc.details:
            body["details"] = exc.details  # structured, e.g. candidate assessments; never secrets
        return JSONResponse(body, status_code=exc.http_status)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        err = CampusError(
            ErrorCode.VALIDATION_ERROR,
            "Anfrage entspricht nicht dem Vertrag",
            required_resolution="Felder gemäss OpenAPI korrigieren",
            details={"errors": [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]},
        )
        body = err.to_object(trace_id(request))
        body["details"] = err.details
        return JSONResponse(body, status_code=422)

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        tid = trace_id(request)
        log.exception("unexpected error trace_id=%s", tid)
        return JSONResponse(
            {
                "code": "INTERNAL_ERROR",
                "message": "Unerwarteter Fehler; Einzelheiten stehen im Serverlog unter der trace_id",
                "trace_id": tid,
                "blocked_action": None,
                "retryable": False,
                "required_resolution": "Betrieb informieren",
                "policy_version": None,
            },
            status_code=500,
        )
