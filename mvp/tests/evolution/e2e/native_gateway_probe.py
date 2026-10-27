"""Real LiteLLM processes against explicitly synthetic runtime/model wire fixtures.

Runs offline inside the exact gateway image. This proves native adapter behavior,
not real detector accuracy, shared-runtime admission or cluster network policy.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
from pathlib import Path
from uuid import uuid4

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from guardrails.evolution.models import (
    Decision,
    Destination,
    EvaluationRequest,
    Finding,
    PolicyRef,
    ProviderProfile,
    with_resource_digest,
)
from guardrails.evolution.transformations import transform_text

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))
from https_support import tls_files


PROBE_PATH = "mvp/tests/evolution/e2e/native_gateway_probe.py"
FAILURE_TAGS = {
    "gateway_start_failed", "gateway_readiness_timeout", "fixture_server_start_failed",
    "native_action_status", "input_block_dispatch", "raw_fixture_in_gateway_logs",
    "native_disconnect_start", "native_disconnect_cancel", "cancelled_response_not_inspected_or_released",
}


def remember_failure(error, observation):
    """Retain the first source failure; cleanup cannot replace its origin."""
    if observation["probe_status"] == "fail":
        return
    from guardrails.evolution.errors import STATUS

    args = BaseException.__dict__["args"].__get__(error)
    tag = args[0] if len(args) == 1 and type(args[0]) is str else "probe_failed"
    if tag not in FAILURE_TAGS and not (tag.startswith("benign_") and tag[7:] in STATUS):
        tag = "probe_failed"
    if type(error) is AssertionError:
        category = "assertion"
    elif type(error) is RuntimeError:
        category = "runtime"
    elif isinstance(error, httpx.TimeoutException):
        category = "transport_timeout"
    elif isinstance(error, httpx.HTTPError):
        category = "transport_error"
    else:
        category = "other"
    frames = []
    trace = BaseException.__dict__["__traceback__"].__get__(error)
    for _ in range(64):
        if trace is None:
            break
        code = trace.tb_frame.f_code
        if code.co_filename == __file__:
            frames.append({"file": PROBE_PATH, "function": code.co_name, "line": trace.tb_lineno})
        trace = trace.tb_next
    observation.update(probe_status="fail", failure_bin=tag, error_category=category, frames=frames[-8:])


def port():
    with socket.socket() as stream:
        stream.bind(("127.0.0.1", 0))
        return stream.getsockname()[1]


@contextlib.contextmanager
def server(app, files, number, observation=None, stage=None):
    if observation is not None:
        observation["stage"] = stage
    instance = uvicorn.Server(uvicorn.Config(app, host="localhost", port=number,
        ssl_certfile=str(files["cert"]), ssl_keyfile=str(files["key"]), access_log=False, log_level="critical"))
    thread = threading.Thread(target=instance.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 30
        while not instance.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("fixture_server_start_failed")
            time.sleep(.02)
        yield
    finally:
        instance.should_exit = True
        thread.join(5)


def main(observation):
    base = Path("/fixture/native")
    base.mkdir(parents=True, mode=0o700)
    files = tls_files(base)
    runtime_port, model_port = port(), port()
    events, model_calls = [], []
    cancelled_models = set()
    credential_canaries = []
    scopes = {}
    contexts = {}
    runtime = FastAPI()
    model = FastAPI()

    @runtime.get("/v2/capabilities")
    async def capabilities(request: Request):
        if request.headers.get("authorization") not in scopes:
            return JSONResponse({}, status_code=401)
        return [{"capability_id": "synthetic-pii", "stage": stage, "part_kinds": ["text"],
                 "actions": ["allow", "warn", "redact", "block"], "evaluator_revision": "pii-v2",
                 "max_input_bytes": 65536, "timeout_ms": 500, "zone": "local-shared", "status": "enabled"}
                for stage in ("input", "output")]

    @runtime.post("/v2/evaluations")
    async def evaluate(wire: Request):
        scope = scopes.get(wire.headers.get("authorization"))
        if scope is None:
            return JSONResponse({}, status_code=401)
        request = EvaluationRequest.model_validate(await wire.json())
        if (request.tenant_id, request.workload_id, request.provider_id) != scope:
            return JSONResponse({}, status_code=403)
        events.append((request.tenant_id, request.stage))
        if request.stage == "input":
            context = str(uuid4())
            contexts[context] = scope
        else:
            context = request.context_id
            if contexts.get(context) != scope:
                return JSONResponse({}, status_code=503)
        findings = []
        action = "allow"
        for part in request.parts:
            if "BLOCK" in part.text:
                action = "block"
            if "@example.com" in part.text:
                start = part.text.index("@example.com") - 1
                findings.append(Finding(rule_id="synthetic-pii", category="pii", part_id=part.part_id,
                    reason_code="pii_detected", start=start, end=start + 13, evaluator_revision="pii-v2"))
                if action != "block":
                    action = "redact"
        decision = Decision(decision_id=str(uuid4()), request_id=request.request_id, context_id=context,
            tenant_id=request.tenant_id, workload_id=request.workload_id, stage=request.stage,
            policy=PolicyRef(tenant_id=request.tenant_id, policy_id="fixture", version=1, digest="a" * 64),
            binding_revision=1, snapshot_generation=1, action=action, would_be_action=action,
            reason_codes=("pii_detected",) if action != "allow" else (), findings=tuple(findings),
            transformed_parts=transform_text(request.parts, tuple(findings)) if action == "redact" else None,
            evaluation_ms=1.0, audit_status="persisted", evaluator_revisions=("pii-v2",))
        return decision.model_dump(mode="json")

    @model.post("/v1/chat/completions")
    async def completion(request: Request):
        body = await request.json()
        model_calls.append(body["messages"])
        text = " ".join(message["content"] for message in body["messages"])
        if "cancel-only-" in text:
            deadline = time.monotonic() + 5
            while not await request.is_disconnected():
                if time.monotonic() > deadline:
                    return JSONResponse({}, status_code=504)
                await asyncio.sleep(.01)
            cancelled_models.add(text)
            return JSONResponse({}, status_code=499)
        content = "BLOCK" if "output-denial" in text else "b@example.com" if "output-redact" in text else "safe fixture response"
        message = {"role": "assistant", "content": content}
        if "unsupported-output" in text:
            message["tool_calls"] = [{"id": "forbidden", "type": "function", "function": {"name": "x", "arguments": "{}"}}]
        return {"id": "fixture-response", "object": "chat.completion", "created": 1, "model": "gpt-4o-evolution-mock",
                "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}

    processes = []
    logs = {}
    safe_logs = True
    checks = 0
    observation["stage"] = "runtime_start"
    with server(runtime, files, runtime_port, observation, "runtime_start"), server(model, files, model_port, observation, "model_start"):
        try:
            client = httpx.Client(verify=ssl.create_default_context(cafile=files["ca"]), trust_env=False, timeout=20)
            gateways = []
            for gateway_index, team in enumerate(("team-a", "team-b")):
                observation.update(stage="gateway_start", gateway_index=gateway_index)
                folder = base / team
                folder.mkdir(mode=0o700)
                workload_token, gateway_token = uuid4().hex * 2, uuid4().hex * 2
                provider_id = str(uuid4())
                scopes["Bearer " + workload_token] = (team, "chat", provider_id)
                for name, value in (("workload", workload_token), ("gateway", gateway_token), ("model", uuid4().hex * 2)):
                    path = folder / (name + ".token")
                    path.write_text(value)
                    path.chmod(0o600)
                    credential_canaries.append(value)
                number = port()
                settings = dict(  # noqa: C408 - grouped fixture values
                    tenant_id=team, workload_id="chat", provider_id=provider_id,
                    model_alias=team, model_route="openai/gpt-4o-evolution-mock",
                    model_base_url=f"https://localhost:{model_port}/v1", runtime_url=f"https://127.0.0.1:{runtime_port}",
                    workload_token_file=str(folder / "workload.token"), gateway_token_file=str(folder / "gateway.token"),
                    model_token_file=str(folder / "model.token"), ca_file=str(files["ca"]), tls_cert_file=str(files["cert"]),
                    tls_key_file=str(files["key"]), litellm_config_file=str(folder / "litellm.yaml"),
                    profile="deterministic_mock", port=number, approved_routes_file=str(folder / "routes.json"))
                destination = with_resource_digest(Destination(destination_id=str(uuid4()), tenant_id=team,
                    name="synthetic-local-model", host="localhost", port=model_port, path_prefix="/v1",
                    cidrs=("127.0.0.1/32", "::1/128"), zone="local-shared", purpose="inference", tls_ca_ref="fixture-ca"))
                provider = with_resource_digest(ProviderProfile(provider_id=provider_id, tenant_id=team,
                    kind="model", vendor="ollama", model_id="gpt-4o-evolution-mock", destination_id=destination.destination_id,
                    zone="local-shared"))
                route_manifest = {"tenant_id": team, "workload_id": "chat", "routes": [{
                    "policy": {"tenant_id": team, "policy_id": "fixture", "version": 1, "digest": "a" * 64},
                    "provider": provider.model_dump(mode="json"), "destination": destination.model_dump(mode="json")}],
                    "ca_files": {"fixture-ca": str(files["ca"])}, "local_services": {destination.destination_id: {
                        "hostname": "localhost", "addresses": ["127.0.0.1", "::1"]}}}
                (folder / "routes.json").write_text(json.dumps(route_manifest))
                settings_path = folder / "settings.json"
                settings_path.write_text(json.dumps(settings))
                # JSON is a YAML subset accepted by the actual proxy configuration loader.
                configuration = {
                    "model_list": [{"model_name": team, "litellm_params": {"model": settings["model_route"],
                        "api_base": settings["model_base_url"], "api_key": "os.environ/EVOLUTION_MODEL_KEY",
                        "timeout": 10, "max_retries": 0, "guardrails": ["evolution"]}}],
                    "guardrails": [{"guardrail_name": "evolution", "litellm_params": {
                        "guardrail": "connector.evolution_guardrail.EvolutionGuardrail", "mode": ["pre_call", "post_call"],
                        "default_on": True, "settings_file": str(settings_path)}}],
                    "litellm_settings": {"ssl_verify": str(files["ca"]), "num_retries": 0,
                        "set_verbose": False, "turn_off_message_logging": True, "request_timeout": 10},
                    "general_settings": {"master_key": "os.environ/LITELLM_MASTER_KEY", "disable_spend_logs": True},
                }
                (folder / "litellm.yaml").write_text(json.dumps(configuration))
                process = subprocess.Popen([sys.executable, "-c",
                    ("import faulthandler,signal; faulthandler.register(signal.SIGUSR1); "
                    "from connector.evolution_gateway import main; main()")],
                    env=os.environ | {"EVOLUTION_GATEWAY_CONFIG_FILE": str(settings_path), "EVOLUTION_CONNECTOR_DIAGNOSTICS": "1"},
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                processes.append(process)
                logs[process] = bytearray()

                def drain(stream, buffer):
                    while chunk := stream.read1(4096):
                        if len(buffer) < 2_000_000:
                            buffer.extend(chunk)

                for stream in (process.stdout, process.stderr):
                    threading.Thread(target=drain, args=(stream, logs[process]), daemon=True).start()
                gateway = (f"https://127.0.0.1:{number}", {"Authorization": "Bearer " + gateway_token}, team)
                gateways.append(gateway)
                deadline = time.monotonic() + 30
                ready_status, ready_code = None, None
                observation.update(stage="gateway_readiness", readiness_status=0, readiness_code=None)
                while True:
                    if process.poll() is not None:
                        raise RuntimeError("gateway_start_failed")
                    try:
                        ready = client.get(gateway[0] + "/health/ready", headers=gateway[1], timeout=2)
                        ready_status = ready.status_code
                        observation.update(readiness_status=ready_status, readiness_code=None)
                        from guardrails.evolution.errors import STATUS
                        code = ready.json().get("error", {}).get("code")
                        ready_code = code if code in STATUS else None
                        observation["readiness_code"] = (code if type(code) is str and code in STATUS
                                                         and ready_status == STATUS[code] else None)
                        if ready.status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    if time.monotonic() > deadline:
                        process.send_signal(signal.SIGUSR1)
                        time.sleep(.2)
                        import re
                        print(json.dumps({"readiness_status": ready_status, "readiness_code": ready_code,
                                          "gateway_log_bytes": len(logs[process]),
                                          "stack_functions": [x.decode() for x in re.findall(rb'File "[^"\n]+", line [0-9]+, in ([A-Za-z_][A-Za-z0-9_]*)', bytes(logs[process]))][-30:]}), flush=True)
                        raise RuntimeError("gateway_readiness_timeout")
                    time.sleep(.1)

            def send(gateway, text, extra=None):
                return client.post(gateway[0] + "/v1/chat/completions", headers=gateway[1],
                    json={"model": gateway[2], "messages": [{"role": "system", "content": "fixture system"},
                         {"role": "user", "content": text}]} | (extra or {}))

            for gateway_index, gateway in enumerate(gateways):
                observation.update(stage="benign", gateway_index=gateway_index)
                response = send(gateway, "benign")
                if response.status_code != 200:
                    # Retain only the finite error code, never an upstream body.
                    code = response.json().get("error", {}).get("code", "unknown")
                    raise RuntimeError("benign_" + code)
                assert response.json()["guardrails"]["policy"]["tenant_id"] == gateway[2]
                assert response.headers["cache-control"] == "no-store"
                checks += 2
            for gateway_index, gateway in enumerate(gateways):
                observation.update(stage="actions", gateway_index=gateway_index)
                for text, status in (("BLOCK", 403), ("a@example.com", 200), ("output-denial", 403),
                                     ("output-redact", 200), ("unsupported-output", 503)):
                    prior = len(model_calls)
                    response = send(gateway, text)
                    assert response.status_code == status, "native_action_status"
                    assert len(model_calls) == prior + (text != "BLOCK"), "input_block_dispatch"
                    if text == "a@example.com":
                        assert model_calls[-1][-1]["content"] == "[REDACTED]"
                        checks += 1
                    if text == "output-redact":
                        assert response.json()["choices"][0]["message"]["content"] == "[REDACTED]"
                        checks += 1
                    checks += 2
                observation["stage"] = "bypass"
                for extra in ({"guardrails": []}, {"stream": True}, {"model": "unregistered"},
                              {"api_base": "https://denied.invalid"}, {"tools": []}):
                    prior = len(model_calls)
                    response = send(gateway, "never-dispatch", extra)
                    assert response.status_code in {400, 403}
                    assert len(model_calls) == prior
                    checks += 2
                observation["stage"] = "disconnect"
                marker = "cancel-only-" + gateway[2]
                prior_model, prior_runtime = len(model_calls), len(events)
                payload = json.dumps({"model": gateway[2], "messages": [{"role": "user", "content": marker}]}).encode()
                from urllib.parse import urlsplit
                address = urlsplit(gateway[0])
                connection = ssl.create_default_context(cafile=files["ca"]).wrap_socket(
                    socket.create_connection((address.hostname, address.port), timeout=2), server_hostname=address.hostname)
                try:
                    header = (f"POST /v1/chat/completions HTTP/1.1\r\nHost: {address.netloc}\r\n"
                              f"Authorization: {gateway[1]['Authorization']}\r\nContent-Type: application/json\r\n"
                              f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n").encode()
                    connection.sendall(header + payload)
                    deadline = time.monotonic() + 3
                    while len(model_calls) == prior_model and time.monotonic() < deadline:
                        time.sleep(.01)
                    assert len(model_calls) == prior_model + 1, "native_disconnect_start"
                finally:
                    connection.close()
                deadline = time.monotonic() + 2
                while marker not in cancelled_models and time.monotonic() < deadline:
                    time.sleep(.01)
                assert marker in cancelled_models, "native_disconnect_cancel"
                assert len(events) == prior_runtime + 1, "cancelled_response_not_inspected_or_released"
                observation["stage"] = "recovery"
                assert send(gateway, "benign-after-cancellation").status_code == 200
                checks += 4
            assert {tenant for tenant, stage in events} == {"team-a", "team-b"}
            checks += 1
            print(json.dumps({"status": "pass", "checks": checks, "gateway_count": 2,
                              "model_requests": len(model_calls), "runtime_requests": len(events),
                              "fixture": "synthetic-runtime-and-model", "litellm_version": "1.104.2"}), flush=True)
        except BaseException as error:
            remember_failure(error, observation)
            raise
        finally:
            observation.update(checks_completed=checks, model_requests=len(model_calls), runtime_requests=len(events))
            if observation["probe_status"] != "fail":
                observation["stage"] = "privacy"
            try:
                print(json.dumps({"model_requests": len(model_calls), "runtime_requests": len(events)}), flush=True)
                for process in processes:
                    process.terminate()
                for process in processes:
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                    err = bytes(logs[process])
                    for line in err.splitlines():
                        if line.startswith(b'{"event": "gateway_'):
                            value = json.loads(line)
                            print(json.dumps(value), flush=True)
                    safe_logs &= all(value.encode() not in err for value in ("a@example.com", "b@example.com", "never-dispatch", *credential_canaries))
                    # Emit only finite structural diagnostics when debugging startup.
                    import re
                    print(json.dumps({"gateway_exit": process.returncode,
                                      "error_types": sorted({x.decode() for x in re.findall(rb"\b[A-Z][A-Za-z]+(?:Error|Exception)\b", err)}),
                                      "log_bytes": len(err),
                                      "frames": [x.decode() for x in re.findall(rb'File "[^"\n]+", line [0-9]+, in ([A-Za-z_][A-Za-z0-9_]*)', err)][-30:],
                                      "markers": [term for term in ("Permission denied", "Read-only file system", "Traceback", "settings_file", "guardrail", "not found", "ModuleNotFound", "startup", "ImportError", "AttributeError", "ValidationError") if term.encode() in err]}), flush=True)
                if not safe_logs:
                    raise RuntimeError("raw_fixture_in_gateway_logs")
            except BaseException as error:
                observation["cleanup_failed"] = True  # Child cleanup only, not Docker rm.
                remember_failure(error, observation)
                raise
            finally:
                observation.update(checks_completed=checks, model_requests=len(model_calls), runtime_requests=len(events))


if __name__ == "__main__":
    observation = {
        "probe_status": "pass", "stage": "tls_setup", "gateway_index": None,
        "failure_bin": None, "error_category": None, "checks_completed": 0,
        "model_requests": 0, "runtime_requests": 0, "readiness_status": 0,
        "readiness_code": None, "frames": [], "cleanup_failed": False,
    }
    try:
        main(observation)
        observation["stage"] = "complete"
    except Exception as error:  # noqa: BLE001 - preserve only closed source-defined diagnostics
        remember_failure(error, observation)
        print(json.dumps({"status": "fail", "reason": observation["failure_bin"],
                          "type": observation["error_category"]}), flush=True)
        raise SystemExit(1) from None
    finally:
        # No top-level status: the existing pass/fail report count is unchanged.
        print(json.dumps({"native_fixture_observation": observation}), flush=True)
