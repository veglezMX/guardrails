"""Real Phase 1 HTTPS gateway/runtime/mock evidence; never substitute fake gateways."""
from __future__ import annotations
import json
import os
import ssl
import time
from pathlib import Path
import httpx
import pytest

STATE = Path(os.environ.get("PROOF_STATE", "/proof"))
GATEWAYS = (("a", "proof-a"), ("b", "proof-b"))


def secret(name):
    return (STATE / "secrets" / (name + ".token")).read_text().strip()


def authorized(name):
    return {"Authorization": "Bearer " + secret(name)}


@pytest.fixture(scope="module")
def client():
    ca = ssl.create_default_context(cafile=str(STATE / "pki/ca.crt"))
    with httpx.Client(verify=ca, trust_env=False, timeout=20) as connection:
        deadline = time.monotonic() + 90
        urls = ["https://runtime:8444/health/ready", "https://mock-model:8445/health",
                "https://gateway-a:4000/health/ready", "https://gateway-b:4000/health/ready"]
        pending = set(urls)
        while pending and time.monotonic() < deadline:
            for url in list(pending):
                try:
                    headers = authorized("control") if url.startswith("https://runtime:") else authorized("gateway-a.client") if "gateway-a:" in url else authorized("gateway-b.client") if "gateway-b:" in url else {}
                    if connection.get(url, headers=headers).status_code == 200:
                        pending.remove(url)
                except httpx.TransportError:
                    pass
            if pending:
                time.sleep(0.3)
        assert not pending, "Prepared services did not become ready"
        yield connection


def scenario(client, *, state="block", output="clean"):
    headers = authorized("control")
    assert client.put("https://runtime:8444/proof/scenario", headers=headers,
                      json={"enforcement_state": state}).status_code == 200
    assert client.put("https://mock-model:8445/proof/scenario", headers=headers,
                      json={"scenario": output}).status_code == 200
    assert client.delete("https://mock-model:8445/proof/calls", headers=headers).status_code == 200


def call(client, gateway, model, text, **extra):
    return client.post(f"https://gateway-{gateway}:4000/v1/chat/completions",
                       headers=authorized(f"gateway-{gateway}.client"),
                       json={"model": model, "messages": [{"role": "user", "content": text}], **extra})


def calls(client):
    response = client.get("https://mock-model:8445/proof/calls", headers=authorized("control"))
    assert response.status_code == 200
    return response.json()


@pytest.mark.parametrize("gateway,model", GATEWAYS)
def test_clean_round_trip_uses_both_real_hooks(client, gateway, model):
    scenario(client)
    response = call(client, gateway, model, "Summarize this clean sentence.")
    assert response.status_code == 200, "Clean gateway round trip failed"
    body = response.json()
    assert body["choices"][0]["message"]["content"] == "A clean mock response."
    metadata = body["guardrails"]
    assert metadata["input_source"] == metadata["output_source"] == "runtime"
    assert metadata["input_decision_id"] and metadata["output_decision_id"]
    assert metadata["input_action"] == metadata["output_action"] == "allow"
    assert calls(client)["count"] == 1


@pytest.mark.parametrize("gateway,model", GATEWAYS)
def test_input_block_prevents_real_model_call(client, gateway, model):
    scenario(client)
    response = call(client, gateway, model, "Contact alice@example.test.")
    assert response.status_code == 403
    body = response.json()
    assert body["error"]["code"] == "guardrail_blocked"
    assert body["error"]["message"] == "Request blocked by configured guardrails"
    assert "alice@" not in response.text
    assert calls(client)["count"] == 0


@pytest.mark.parametrize("gateway,model", GATEWAYS)
def test_input_redaction_reaches_mock_after_transformation(client, gateway, model):
    scenario(client, state="redact")
    response = call(client, gateway, model, "Contact alice@example.test or +15551234567.")
    assert response.status_code == 200, "Input transformation failed"
    captured = calls(client)
    assert captured["count"] == 1
    assert captured["calls"][0]["messages"][0]["content"] == "Contact [REDACTED] or [REDACTED]."
    assert response.json()["guardrails"]["input_action"] == "redact"


@pytest.mark.parametrize("gateway,model", GATEWAYS)
def test_output_redaction_happens_before_delivery(client, gateway, model):
    scenario(client, state="redact", output="sensitive")
    response = call(client, gateway, model, "Produce a canned response.")
    assert response.status_code == 200, "Output transformation failed"
    body = response.json()
    assert body["choices"][0]["message"]["content"] == "Contact [REDACTED] or [REDACTED]."
    assert body["guardrails"]["output_action"] == "redact"
    assert "proof@" not in response.text


@pytest.mark.parametrize("gateway,model", GATEWAYS)
def test_output_block_withholds_sensitive_model_response(client, gateway, model):
    scenario(client, state="block", output="sensitive")
    response = call(client, gateway, model, "Produce a canned response.")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "guardrail_blocked"
    assert "proof@" not in response.text
    assert calls(client)["count"] == 1


def test_untrusted_tls_is_rejected(client):
    with httpx.Client(verify=True, trust_env=False, timeout=3) as untrusted:
        with pytest.raises(httpx.ConnectError):
            untrusted.get("https://runtime:8444/health/live")


def test_streaming_and_guardrail_overrides_reject_before_inference(client):
    scenario(client)
    response = call(client, "a", "proof-a", "Clean.", stream=True)
    assert response.status_code == 400
    response = call(client, "a", "proof-a", "Clean.", guardrails=[])
    assert response.status_code == 403
    assert calls(client)["count"] == 0


def test_gateway_tokens_are_independent(client):
    response = client.post("https://gateway-b:4000/v1/chat/completions",
                           headers=authorized("gateway-a.client"),
                           json={"model": "proof-b", "messages": [{"role": "user", "content": "Clean."}]})
    assert response.status_code == 401


def test_smoke_audit_contains_metadata_not_fixture_content(client):
    scenario(client, state="redact")
    response = call(client, "a", "proof-a", "Contact alice@example.test.")
    assert response.status_code == 200
    path = STATE / "state/runtime/smoke-audit.jsonl"
    text = path.read_text()
    assert text and "alice@" not in text and "proof@" not in text and "+15551234567" not in text
    records = [json.loads(line) for line in text.splitlines()]
    assert records
    assert all(record.get("content") is None for record in records)
    assert secret("gateway-a.workload") not in text
