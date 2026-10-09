"""Deterministic OpenAI-compatible model for isolated Phase-one smoke runs.

Synthetic response selection and volatile captures require a dedicated control
credential. The inference credential cannot select a scenario or inspect calls.
"""
from __future__ import annotations

import hmac
import os
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request

from guardrails.runtime.app import bearer, bounded_body, safe_problem
from guardrails.runtime.smoke import digest_token, strict_json
from guardrails.transport import install_exception_handlers

SCENARIOS = {
    "clean": "A clean mock response.",
    "sensitive": "Contact proof@example.test or +15551234567.",
}


def _read_token(path: str) -> str:
    token = Path(path).read_text(encoding="utf-8").strip()
    if len(token) < 32 or any(char.isspace() for char in token):
        raise ValueError("invalid_mock_credential")
    return token


def create_app() -> FastAPI:
    if os.environ.get("GUARDRAILS_PROOF_PROFILE") != "smoke":
        raise RuntimeError("The fixture model requires GUARDRAILS_PROOF_PROFILE=smoke")
    control_hash = digest_token(_read_token(os.environ["GUARDRAILS_PROOF_CONTROL_TOKEN_FILE"]))
    inference_hash = digest_token(_read_token(os.environ["GUARDRAILS_MOCK_TOKEN_FILE"]))
    if hmac.compare_digest(control_hash, inference_hash):
        raise ValueError("control_and_model_credentials_must_differ")
    app = FastAPI(title="Guardrails Volatile Mock Model", docs_url=None, redoc_url=None, openapi_url=None)
    install_exception_handlers(app)
    state: dict[str, Any] = {"scenario": "clean", "count": 0, "calls": []}
    app.state.mock = state

    def authenticated(request: Request, token_hash: str) -> bool:
        return hmac.compare_digest(digest_token(bearer(request)), token_hash)

    @app.exception_handler(OverflowError)
    async def oversized(_: Request, __: OverflowError) -> Any:
        return safe_problem(413, "invalid_request")

    @app.get("/health")
    @app.get("/health/live")
    async def live() -> dict[str, bool]:
        return {"live": True}

    @app.put("/proof/scenario")
    async def scenario(request: Request) -> Any:
        if not authenticated(request, control_hash):
            return safe_problem(401, "authentication_failed")
        try:
            value = strict_json(await bounded_body(request))
        except (ValueError, UnicodeDecodeError):
            return safe_problem(400, "invalid_request")
        if (not isinstance(value, dict) or set(value) != {"scenario"}
                or not isinstance(value["scenario"], str) or value["scenario"] not in SCENARIOS):
            return safe_problem(422, "invalid_request")
        state["scenario"] = value["scenario"]
        return {"scenario": state["scenario"]}

    @app.get("/proof/calls")
    async def calls(request: Request) -> Any:
        if not authenticated(request, control_hash):
            return safe_problem(401, "authentication_failed")
        return {"count": state["count"], "calls": state["calls"]}

    @app.delete("/proof/calls")
    async def clear(request: Request) -> Any:
        if not authenticated(request, control_hash):
            return safe_problem(401, "authentication_failed")
        state["calls"].clear()
        state["count"] = 0
        return {"cleared": True}

    @app.post("/v1/chat/completions")
    async def completion(request: Request) -> Any:
        if not authenticated(request, inference_hash):
            return safe_problem(401, "authentication_failed")
        try:
            value = strict_json(await bounded_body(request))
        except (ValueError, UnicodeDecodeError):
            return safe_problem(400, "invalid_request")
        if not isinstance(value, dict):
            return safe_problem(422, "invalid_request")
        messages = value.get("messages")
        if (
            not isinstance(value.get("model"), str)
            or not isinstance(messages, list) or not 1 <= len(messages) <= 32
            or value.get("stream", False) is not False
            or type(value.get("n", 1)) is not int or value.get("n", 1) != 1
        ):
            return safe_problem(422, "unsupported_surface")
        if any(
            not isinstance(message, dict)
            or set(message) != {"role", "content"}
            or message["role"] not in ("system", "developer", "user", "assistant")
            or not isinstance(message["content"], str)
            for message in messages
        ):
            return safe_problem(422, "unsupported_surface")
        if sum(len(message["content"].encode("utf-8")) for message in messages) > 1048576:
            return safe_problem(413, "invalid_request")
        # Request-local data is retained only in bounded process memory.
        state["count"] += 1
        state["calls"].append({
            "model": value["model"],
            "messages": [{"role": item["role"], "content": item["content"]} for item in messages],
        })
        del state["calls"][:-128]
        return {
            "id": f"chatcmpl-{uuid4().hex}", "object": "chat.completion",
            "created": int(time.time()), "model": value["model"],
            "choices": [{
                "index": 0, "message": {"role": "assistant", "content": SCENARIOS[state["scenario"]]},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    return app
