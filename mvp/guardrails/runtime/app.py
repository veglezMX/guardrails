"""Explicit Phase-one fixture runtime served over HTTPS by Uvicorn.

No management lifecycle, snapshot polling, SQLite audit repository or supervised
evaluation deadline is implemented by this smoke application.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from guardrails.runtime.smoke import SCHEMA_VERSION, STATES, SmokeState, strict_json
from guardrails.transport import (
    ContractProblem, install_exception_handlers, parse_decision, parse_evaluation,
    problem_response,
)


def bearer(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer "):
        return ""
    token = authorization[7:]
    return token if token and len(token) <= 1024 and not any(char.isspace() for char in token) else ""


def safe_problem(status: int, code: str) -> JSONResponse:
    return problem_response(ContractProblem(code=code, status=status), uuid4())


async def bounded_body(request: Request) -> bytes:
    chunks = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > 8388608:
            raise OverflowError("request_too_large")
        chunks.append(chunk)
    return b"".join(chunks)


def create_app() -> FastAPI:
    if os.environ.get("GUARDRAILS_PROOF_PROFILE") != "smoke":
        raise RuntimeError("The fixture runtime requires GUARDRAILS_PROOF_PROFILE=smoke")
    state = SmokeState(
        Path(os.environ["GUARDRAILS_SMOKE_CONFIG"]),
        Path(os.environ.get("GUARDRAILS_SMOKE_AUDIT_PATH", ".proof/smoke-audit.jsonl")),
    )
    app = FastAPI(title="Guardrails Phase One Smoke Runtime", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.smoke = state
    install_exception_handlers(app)

    @app.exception_handler(OverflowError)
    async def oversized(_: Request, __: OverflowError) -> JSONResponse:
        return safe_problem(413, "invalid_request")

    @app.get("/health")
    @app.get("/health/live")
    async def live() -> dict[str, bool]:
        return {"live": True}

    @app.get("/health/ready")
    async def ready(request: Request) -> Any:
        if not state.control_authenticated(bearer(request)):
            return safe_problem(401, "authentication_failed")
        return {"ready": True, "degraded": False, "applied_generation": 1, "profile": "smoke"}

    @app.get("/v1/client-config")
    async def client_config(request: Request) -> Any:
        identity = state.authenticate(bearer(request))
        if identity is None:
            return safe_problem(401, "authentication_failed")
        return state.client_config(identity)

    @app.get("/v1/capabilities")
    async def capabilities(request: Request) -> Any:
        if state.authenticate(bearer(request)) is None:
            return safe_problem(401, "authentication_failed")
        return {
            "schema_version": SCHEMA_VERSION, "supported_stages": ["input", "output"],
            "supported_actions": ["allow", "log_only", "redact", "block"],
            "evaluators": [{"id": "local_pii", "format": "pii-ascii-v1", "category": "GR-003"}],
        }

    @app.put("/proof/scenario")
    async def scenario(request: Request) -> Any:
        if not state.control_authenticated(bearer(request)):
            return safe_problem(401, "authentication_failed")
        try:
            value = strict_json(await bounded_body(request))
        except (ValueError, UnicodeDecodeError):
            return safe_problem(400, "invalid_request")
        if (not isinstance(value, dict) or set(value) != {"enforcement_state"}
                or not isinstance(value["enforcement_state"], str) or value["enforcement_state"] not in STATES):
            return safe_problem(422, "invalid_request")
        state.state = value["enforcement_state"]
        state.binding_revision += 1
        return {"profile": "smoke", "enforcement_state": state.state, "binding_revision": state.binding_revision}

    @app.post("/v1/evaluate")
    async def evaluate(request: Request) -> Any:
        identity = state.authenticate(bearer(request))
        if identity is None:
            return safe_problem(401, "authentication_failed")
        model = parse_evaluation(await bounded_body(request))
        if (model.tenant_id, model.workload_id) != identity:
            return safe_problem(403, "identity_mismatch")
        if model.stage not in ("input", "output"):
            return safe_problem(422, "unsupported_surface")
        try:
            decision = state.evaluate(model, identity)
        except ValueError as error:
            code = str(error)
            if code not in {"context_invalid", "capacity_exceeded"}:
                code = "invalid_request"
            return safe_problem(429 if code == "capacity_exceeded" else 422, code)
        # Validate our own wire output with the same schema used by the gateway.
        validated = parse_decision(json.dumps(decision).encode("utf-8"))
        return validated.model_dump(mode="json")

    return app
