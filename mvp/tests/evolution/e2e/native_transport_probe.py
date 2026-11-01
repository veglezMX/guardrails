"""Pinned native providers over synthetic TLS with observed production transport.

Faults are explicit test ports after or before the real ModelTransport boundary;
no provider/model inference or cluster operation occurs in this offline fixture.
"""
from __future__ import annotations

import json
import os
import signal
import ssl
import subprocess
import sys
import threading
import time
from collections import Counter
from pathlib import Path
from uuid import uuid4

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from guardrails.evolution.models import (
    Decision,
    Destination,
    EvaluationRequest,
    PolicyRef,
    ProviderProfile,
    with_resource_digest,
)
from native_gateway_probe import port, server, tls_files

KINDS = ("clean", "protocol-failure", "connect-failure", "wrong-image", "wrong-path", "wrong-model", "wrong-message", "forbidden-dns", "redirect", "unsupported-response")
STAGE = "setup"


def classify(text):
    return text if text in KINDS else "clean"


def child(settings_path):
    from connector.evolution_gateway import ModelTransport, create_app

    output = Path(settings_path).parent / "transport-observation.json"
    observations = {"attempts": dict.fromkeys(KINDS, 0), "returned": dict.fromkeys(KINDS, 0),
                    "response_hooks": 0, "shared_client": False, "client_closed": False,
                    "reopen_refused": False, "shutdown_stage": "running", "in_flight": 0}
    handler = shared = None

    def save():
        if handler is not None:
            import litellm
            observations["shared_client"] = handler._client is shared and shared is litellm.aclient_session
            observations["client_closed"] = shared.is_closed
        output.write_text(json.dumps(observations, sort_keys=True))
        output.chmod(0o600)

    original = ModelTransport.handle_async_request

    async def observed(self, request):
        body = json.loads(await request.aread())
        kind = classify(body["messages"][-1]["content"])
        observations["attempts"][kind] += 1
        save()
        original_resolver = self.transport.resolver
        if kind == "connect-failure":
            raise httpx.ConnectError("Synthetic connection failure", request=request)
        if kind == "wrong-image":
            body["messages"][-1]["images"] = ["synthetic-uninspected"]
            request = httpx.Request(request.method, request.url, headers=request.headers,
                                    content=json.dumps(body).encode())
        elif kind == "wrong-path":
            request = httpx.Request(request.method, request.url.copy_with(path="/forbidden"),
                                    headers=request.headers, content=json.dumps(body).encode())
        elif kind in {"wrong-model", "wrong-message"}:
            if kind == "wrong-model":
                body["model"] = "unapproved-model"
            else:
                body["messages"][-1]["content"] = "uninspected replacement"
            request = httpx.Request(request.method, request.url, headers=request.headers,
                                    content=json.dumps(body).encode())
        elif kind == "forbidden-dns":
            async def foreign(*args):
                return ["169.254.169.254"]
            self.transport.resolver = foreign
        try:
            response = await original(self, request)
            observations["returned"][kind] += 1
            save()
            if kind == "protocol-failure":
                # The real pinned transport already delivered and read exactly
                # one accepted request; the native wrapper must not replay it.
                raise httpx.RemoteProtocolError("Synthetic post-delivery failure", request=request)
            return response
        finally:
            self.transport.resolver = original_resolver

    ModelTransport.handle_async_request = observed
    app = create_app()
    handler = app.native_http_handler
    shared = handler.client
    import litellm
    observations["shared_client"] = shared is litellm.aclient_session
    assert observations["shared_client"] and handler.transport.__class__ is ModelTransport

    async def response_seen(response):
        observations["response_hooks"] += 1
        save()

    shared.event_hooks["response"].append(response_seen)
    save()
    settings = app.settings
    from litellm.proxy.middleware.in_flight_requests_middleware import (
        get_in_flight_requests,
    )
    from litellm.proxy.shutdown.graceful_shutdown_manager import GracefulShutdownManager
    original_drain = GracefulShutdownManager.wait_for_drain

    async def observed_drain(*args, **kwargs):
        observations["shutdown_stage"] = "drain_entered"
        observations["in_flight"] = get_in_flight_requests()
        save()
        result = await original_drain(*args, **kwargs)
        observations["shutdown_stage"] = "drain_exited"
        save()
        return result

    GracefulShutdownManager.wait_for_drain = observed_drain
    original_close = handler.close

    async def observed_close():
        observations["shutdown_stage"] = "owned_close_entered"
        save()
        await original_close()
        observations["shutdown_stage"] = "owned_close_exited"
        save()

    handler.close = observed_close

    async def observed_app(scope, receive, send):
        async def observed_receive():
            message = await receive()
            if message["type"] == "lifespan.shutdown":
                observations["shutdown_stage"] = "lifespan_shutdown_received"
                save()
            return message

        async def observed_send(message):
            if message["type"] == "lifespan.shutdown.complete":
                observations["shutdown_stage"] = "lifespan_shutdown_complete"
                try:
                    _ = handler.client
                except httpx.ConnectError:
                    observations["reopen_refused"] = True
                save()
            await send(message)
        await app(scope, observed_receive, observed_send)

    runner = uvicorn.Server(uvicorn.Config(observed_app, host="127.0.0.1", port=settings.port,
                ssl_certfile=settings.tls_cert_file, ssl_keyfile=settings.tls_key_file,
                access_log=False, log_level="critical"))

    def observe_server(*args):
        observations["server_exiting"] = runner.should_exit
        observations["server_connections"] = len(runner.server_state.connections)
        observations["server_tasks"] = len(runner.server_state.tasks)
        save()

    signal.signal(signal.SIGUSR1, observe_server)
    runner.run()


def main(*, production_entrypoint=False):
    global STAGE
    base = Path("/fixture/transport")
    base.mkdir(mode=0o700)
    files = tls_files(base)
    runtime_port, model_port = port(), port()
    runtime, model = FastAPI(), FastAPI()
    scopes, contexts = {}, {}
    events, calls = [], []
    sink = []
    credentials = []
    processes = []
    streams = []
    checks = 0

    @runtime.get("/v2/capabilities")
    async def capabilities(request: Request):
        if request.headers.get("authorization") not in scopes:
            return JSONResponse({}, status_code=401)
        return [{"capability_id": "fixture", "stage": stage, "part_kinds": ["text"], "actions": ["allow", "block"],
                 "evaluator_revision": "pii-v2", "max_input_bytes": 65536, "timeout_ms": 500,
                 "zone": "local-shared", "status": "enabled"} for stage in ("input", "output")]

    @runtime.post("/v2/evaluations")
    async def evaluate(wire: Request):
        value = EvaluationRequest.model_validate(await wire.json())
        scope = scopes.get(wire.headers.get("authorization"))
        if scope != (value.tenant_id, value.workload_id, value.provider_id):
            return JSONResponse({}, status_code=403)
        if value.stage == "input":
            context = str(uuid4())
            contexts[context] = scope
        else:
            context = value.context_id
            if contexts.get(context) != scope:
                return JSONResponse({}, status_code=503)
        events.append((value.tenant_id, value.stage))
        return Decision(decision_id=str(uuid4()), request_id=value.request_id, context_id=context,
            tenant_id=value.tenant_id, workload_id=value.workload_id, stage=value.stage,
            policy=PolicyRef(tenant_id=value.tenant_id, policy_id="fixture", version=1, digest="a" * 64),
            binding_revision=1, snapshot_generation=1, action="allow", would_be_action="allow",
            evaluation_ms=1, audit_status="persisted", evaluator_revisions=("pii-v2",)).model_dump(mode="json")

    @model.post("/sink")
    async def forbidden_sink():
        sink.append(1)
        return {"forbidden": True}

    @model.post("/v1/chat/completions")
    @model.post("/api/chat")
    async def completion(request: Request):
        body = await request.json()
        kind = classify(body["messages"][-1]["content"])
        profile = "local_model" if request.url.path == "/api/chat" else "deterministic_mock"
        calls.append((profile, kind))
        assert body["model"] == ("qwen2.5:0.5b" if profile == "local_model" else "gpt-4o-evolution-mock")
        if kind == "redirect":
            return RedirectResponse(f"https://localhost:{model_port}/sink", status_code=307)
        message = {"role": "assistant", "content": "safe synthetic response"}
        if kind == "unsupported-response":
            message["audio"] = {"data": "synthetic-uninspected"}
        if profile == "local_model":
            return {"model": "qwen2.5:0.5b", "message": message, "done": True, "done_reason": "stop",
                    "prompt_eval_count": 1, "eval_count": 1}
        return {"id": "synthetic", "object": "chat.completion", "created": 1,
                "model": "gpt-4o-evolution-mock", "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}

    with server(runtime, files, runtime_port), server(model, files, model_port):
        client = httpx.Client(verify=ssl.create_default_context(cafile=files["ca"]), trust_env=False, timeout=55)
        try:
            profiles = ("local_model", "deterministic_mock") if production_entrypoint else ("deterministic_mock", "local_model")
            for profile in profiles:
                STAGE = "profile_setup"
                if client.is_closed:
                    client = httpx.Client(verify=ssl.create_default_context(cafile=files["ca"]), trust_env=False, timeout=55)
                folder = base / profile
                folder.mkdir(mode=0o700)
                tenant = "team-a" if profile == "deterministic_mock" else "team-b"
                provider_id, number = str(uuid4()), port()
                workload_token, gateway_token = uuid4().hex * 2, uuid4().hex * 2
                scopes["Bearer " + workload_token] = (tenant, "chat", provider_id)
                for name, value in (("workload", workload_token), ("gateway", gateway_token), ("model", uuid4().hex * 2)):
                    path = folder / (name + ".token")
                    path.write_text(value)
                    path.chmod(0o600)
                    credentials.append(value)
                model_id = "qwen2.5:0.5b" if profile == "local_model" else "gpt-4o-evolution-mock"
                suffix = "" if profile == "local_model" else "/v1"
                destination = with_resource_digest(Destination(destination_id=str(uuid4()), tenant_id=tenant, name="synthetic-model",
                    host="localhost", port=model_port, path_prefix="/" if profile == "local_model" else "/v1",
                    cidrs=("127.0.0.1/32", "::1/128"), zone="local-shared", purpose="inference", tls_ca_ref="fixture-ca"))
                provider = with_resource_digest(ProviderProfile(provider_id=provider_id, tenant_id=tenant, kind="model", vendor="ollama",
                    model_id=model_id, destination_id=destination.destination_id, zone="local-shared"))
                manifest = {"tenant_id": tenant, "workload_id": "chat", "routes": [{
                    "policy": {"tenant_id": tenant, "policy_id": "fixture", "version": 1, "digest": "a" * 64},
                    "provider": provider.model_dump(mode="json"), "destination": destination.model_dump(mode="json")}],
                    "ca_files": {"fixture-ca": str(files["ca"])}, "local_services": {destination.destination_id: {
                        "hostname": "localhost", "addresses": ["127.0.0.1", "::1"]}}}
                (folder / "routes.json").write_text(json.dumps(manifest))
                settings = dict(  # noqa: C408 - grouped fixture configuration
                    tenant_id=tenant, workload_id="chat", provider_id=provider_id, model_alias=tenant,
                    model_route=("ollama_chat/" if profile == "local_model" else "openai/") + model_id,
                    model_base_url=f"https://localhost:{model_port}" + suffix, runtime_url=f"https://127.0.0.1:{runtime_port}",
                    workload_token_file=str(folder / "workload.token"), gateway_token_file=str(folder / "gateway.token"),
                    model_token_file=str(folder / "model.token"), ca_file=str(files["ca"]), tls_cert_file=str(files["cert"]),
                    tls_key_file=str(files["key"]), litellm_config_file=str(folder / "litellm.yaml"), profile=profile,
                    port=number, approved_routes_file=str(folder / "routes.json"))
                settings_path = folder / "settings.json"
                settings_path.write_text(json.dumps(settings))
                config = {"model_list": [{"model_name": tenant, "litellm_params": {"model": settings["model_route"],
                    "api_base": settings["model_base_url"], "api_key": "os.environ/EVOLUTION_MODEL_KEY", "timeout": 45,
                    "max_retries": 0, "guardrails": ["evolution"]}}],
                    "guardrails": [{"guardrail_name": "evolution", "litellm_params": {
                        "guardrail": "connector.evolution_guardrail.EvolutionGuardrail", "mode": ["pre_call", "post_call"],
                        "default_on": True, "settings_file": str(settings_path)}}],
                    "litellm_settings": {"ssl_verify": str(files["ca"]), "num_retries": 0, "request_timeout": 45,
                        "set_verbose": False, "turn_off_message_logging": True},
                    "general_settings": {"master_key": "os.environ/LITELLM_MASTER_KEY", "disable_spend_logs": True}}
                (folder / "litellm.yaml").write_text(json.dumps(config))
                # The production branch executes the actual module entrypoint.
                # No imported factory, monkeypatch, or replacement ASGI app is
                # involved; -B changes bytecode writes only.
                command = ([sys.executable, "-B", "-m", "connector.evolution_gateway"] if production_entrypoint else
                           [sys.executable, __file__, "--gateway", str(settings_path)])
                process = subprocess.Popen(command,
                    env=os.environ | {"EVOLUTION_GATEWAY_CONFIG_FILE": str(settings_path)}, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                processes.append(process)
                logs = bytearray()

                def drain(stream, captured):
                    while chunk := stream.read1(4096):
                        if len(captured) < 2_000_000:
                            captured.extend(chunk)

                for stream in (process.stdout, process.stderr):
                    thread = threading.Thread(target=drain, args=(stream, logs), daemon=True)
                    thread.start()
                    streams.append(thread)
                url = f"https://127.0.0.1:{number}"
                headers = {"Authorization": "Bearer " + gateway_token}
                deadline = time.monotonic() + 35
                while True:
                    if process.poll() is not None:
                        raise AssertionError("gateway_start_failed")
                    try:
                        if client.get(url + "/health/ready", headers=headers, timeout=2).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    if time.monotonic() >= deadline:
                        raise AssertionError("gateway_ready_timeout")
                    time.sleep(.1)
                STAGE = "native_requests"

                def send(kind, url=url, headers=headers, tenant=tenant, client=client):
                    return client.post(url + "/v1/chat/completions", headers=headers, json={"model": tenant,
                        "messages": [{"role": "user", "content": kind}], "stream": False, "temperature": 0, "max_tokens": 16})

                expected_attempts = Counter()
                sequence = (("clean", 1), ("redirect", 1), ("unsupported-response", 1), ("clean", 1)) if production_entrypoint else (
                                       ("clean", 1), ("protocol-failure", 1), ("clean", 1),
                                       ("connect-failure", 0), ("clean", 1), ("wrong-image", 0),
                                       ("wrong-path", 0), ("wrong-model", 0), ("wrong-message", 0),
                                       ("forbidden-dns", 0), ("redirect", 1), ("unsupported-response", 1), ("clean", 1))
                for kind, dispatch in sequence:
                    before_calls, before_events = len(calls), len(events)
                    response = send(kind)
                    code = response.json().get("error", {}).get("code")
                    from guardrails.evolution.errors import STATUS
                    code = code if code in STATUS else None
                    print(json.dumps({"event": "case", "profile": profile, "kind": kind,
                                      "status": response.status_code, "code": code, "dispatches": len(calls) - before_calls,
                                      "inspections": len(events) - before_events}), flush=True)
                    assert response.status_code == 200 if kind == "clean" else response.status_code != 200, "response_status"
                    assert len(calls) - before_calls == dispatch, "dispatch_count"
                    assert len(events) - before_events == (2 if kind == "clean" else 1), "inspection_count"
                    assert sink == [], "redirect_followed"
                    if production_entrypoint:
                        if kind == "clean":
                            value = response.json()
                            assert value["choices"][0]["message"]["content"] == "safe synthetic response", "response_content"
                            assert value["guardrails"]["input_action"] == value["guardrails"]["output_action"] == "allow", "inspection_actions"
                            checks += 2
                        checks += 4
                        continue
                    observation = json.loads((folder / "transport-observation.json").read_text())
                    assert observation["shared_client"] is True, "shared_client_changed"
                    expected_attempts[kind] += 1
                    assert observation["attempts"][kind] == expected_attempts[kind], "native_replayed_transport"
                    assert not observation["client_closed"], "client_closed_early"
                    checks += 7
                if production_entrypoint:
                    for kind in ("caller-stream", "caller-image", "caller-credential"):
                        before_calls, before_events = len(calls), len(events)
                        payload = {"model": tenant, "messages": [{"role": "user", "content": "clean"}], "stream": False}
                        authorization = headers
                        if kind == "caller-stream":
                            payload["stream"] = True
                        elif kind == "caller-image":
                            payload["messages"][0]["content"] = [{"type": "image_url", "image_url": {"url": "https://forbidden.invalid/image"}}]
                        else:
                            authorization = {"Authorization": "Bearer invalid-fixture-credential"}
                        response = client.post(url + "/v1/chat/completions", headers=authorization, json=payload)
                        print(json.dumps({"event": "case", "profile": profile, "kind": kind,
                                          "status": response.status_code, "dispatches": len(calls) - before_calls}), flush=True)
                        assert response.status_code == (401 if kind == "caller-credential" else 400), "response_status"
                        assert len(calls) == before_calls and len(events) == before_events, "preflight_dispatch"
                        assert sink == [], "redirect_followed"
                        checks += 3
                # Close the fixture caller's pooled TLS sockets before observing
                # gateway shutdown; no unconsumed response or caller remains.
                client.close()
                shutdown_started = time.monotonic()
                process.send_signal(signal.SIGTERM)
                STAGE = "native_shutdown"
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    if production_entrypoint:
                        raise
                    process.send_signal(signal.SIGUSR1)
                    time.sleep(.1)
                    diagnostic = json.loads((folder / "transport-observation.json").read_text())
                    print(json.dumps({"event": "shutdown", "stage": diagnostic["shutdown_stage"],
                                      "in_flight": diagnostic["in_flight"], "closed": diagnostic["client_closed"],
                                      "server_exiting": diagnostic.get("server_exiting"),
                                      "connections": diagnostic.get("server_connections"),
                                      "tasks": diagnostic.get("server_tasks")}), flush=True)
                    raise
                print(json.dumps({"event": "shutdown_duration", "profile": profile,
                                  "elapsed_ms": round((time.monotonic() - shutdown_started) * 1000, 3),
                                  "returncode": process.returncode}), flush=True)
                STAGE = "shutdown_checks"
                if production_entrypoint:
                    # Pinned Uvicorn re-raises captured SIGTERM after completing
                    # its graceful shutdown; a signal exit is expected here.
                    assert process.returncode in (0, -signal.SIGTERM), "shutdown_failed"
                    assert all(value.encode() not in bytes(logs) for value in credentials), "credential_logged"
                    assert b"synthetic-uninspected" not in bytes(logs), "content_logged"
                    checks += 3
                    processes.remove(process)
                    continue
                observation = json.loads((folder / "transport-observation.json").read_text())
                assert observation["client_closed"] is True, "client_not_closed"
                assert observation["reopen_refused"] is True, "closed_client_reopened"
                assert observation["attempts"]["clean"] == 4, "successful_transport_missing"
                assert observation["response_hooks"] == 4, "raw_response_hook_missing"
                assert all(value.encode() not in bytes(logs) for value in credentials), "credential_logged"
                assert b"uninspected replacement" not in bytes(logs), "content_logged"
                checks += 6
                processes.remove(process)
            count = 4 if production_entrypoint else 7
            assert Counter(profile for profile, kind in calls) == {"deterministic_mock": count, "local_model": count}
            checks += 1
            fixture = "production-module-entrypoint" if production_entrypoint else "synthetic-native-http-providers"
            print(json.dumps({"status": "pass", "checks": checks, "fixture": fixture,
                              "profiles": 2, "model_requests": len(calls), "sink_requests": len(sink),
                              "runtime_requests": len(events)}), flush=True)
        finally:
            client.close()
            for process in processes:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            for thread in streams:
                thread.join(timeout=2)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--gateway":
        child(sys.argv[2])
    else:
        try:
            main(production_entrypoint=sys.argv[1:] == ["--production-entrypoint"])
        except Exception as error:  # noqa: BLE001 - persist only fixed fixture reasons
            allowed = {"gateway_start_failed", "gateway_ready_timeout", "response_status", "dispatch_count", "inspection_count",
                       "redirect_followed", "shared_client_changed", "native_replayed_transport", "client_closed_early",
                       "client_not_closed", "closed_client_reopened", "successful_transport_missing", "raw_response_hook_missing", "credential_logged", "content_logged",
                       "response_content", "inspection_actions", "preflight_dispatch", "shutdown_failed"}
            reason = str(error) if str(error) in allowed else "fixture_failed"
            failure_type = type(error).__name__ if type(error).__name__ in {"AssertionError", "TimeoutExpired", "ReadTimeout", "ConnectError", "ValueError", "KeyError"} else "other"
            print(json.dumps({"status": "fail", "reason": reason, "type": failure_type, "stage": STAGE}), flush=True)
            raise SystemExit(1) from None
