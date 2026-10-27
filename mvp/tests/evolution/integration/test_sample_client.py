"""The team client validates attribution over real TLS and keeps error bodies private."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from guardrails.evolution.errors import EvolutionError
from https_support import serve_https, tls_files

spec = importlib.util.spec_from_file_location("evolution_sample_client", Path(__file__).resolve().parents[4] / "examples/evolution/client.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
TeamClient = module.TeamClient


def fixture_app():
    app = FastAPI()

    @app.get("/health/live")
    def health():
        return {"live": True}

    @app.post("/v1/chat/completions")
    async def complete(request: Request):
        payload = await request.json()
        text = payload["messages"][0]["content"]
        if text == "block":
            return JSONResponse({"error": "private-provider-echo"}, status_code=403)
        metadata = {"policy": {"tenant_id": "team-b" if text == "foreign" else "team-a", "policy_id": "sample", "version": 1, "digest": "a" * 64},
                    "correlation_id": request.headers["x-request-id"], "input_decision_id": str(uuid4()),
                    "output_decision_id": str(uuid4()), "binding_revision": 1, "snapshot_generation": 1,
                    "input_action": "allow", "output_action": "allow", "audit_status": "persisted"}
        if text == "missing-hook":
            metadata.pop("output_decision_id")
        if text == "wrong-request":
            metadata["correlation_id"] = str(uuid4())
        return JSONResponse({"model": payload["model"], "guardrails": metadata,
            "choices": [{"message": {"role": "assistant", "content": "synthetic fixture response"}}]},
            headers={"Cache-Control": "no-store"})

    return app


@pytest.mark.asyncio
async def test_team_client_tls_attribution_hooks_and_denial_privacy(tmp_path):
    token = tmp_path / "gateway-token"
    token.write_text("synthetic-gateway-token")
    token.chmod(0o600)
    with serve_https(fixture_app, tmp_path / "tls") as server:
        client = TeamClient(gateway=server["url"], ca_file=server["ca_file"], token_file=str(token),
                            tenant_id="team-a", model="team-a-model", profile="deterministic_mock")
        try:
            result = await client.complete("benign")
            assert result["content"] == "synthetic fixture response"
            assert result["policy"]["tenant_id"] == "team-a"
            assert result["input_decision_id"] != result["output_decision_id"]
            for text, code in (("foreign", "inspection_incomplete"), ("missing-hook", "inspection_incomplete"),
                               ("wrong-request", "inspection_incomplete"), ("block", "forbidden")):
                with pytest.raises(EvolutionError) as caught:
                    await client.complete(text)
                assert caught.value.code == code
                assert "private-provider-echo" not in str(caught.value)
        finally:
            await client.close()
        unrelated = tls_files(tmp_path / "unrelated")
        client = TeamClient(gateway=server["url"], ca_file=str(unrelated["ca"]), token_file=str(token),
                            tenant_id="team-a", model="team-a-model", profile="deterministic_mock")
        try:
            with pytest.raises(EvolutionError) as caught:
                await client.complete("benign")
            assert caught.value.code == "evaluator_unavailable"
        finally:
            await client.close()
