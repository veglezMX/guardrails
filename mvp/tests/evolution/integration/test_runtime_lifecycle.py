"""Real runtime revocation, bounded cache lock and HTTPS sync recovery."""

import asyncio
import fcntl
import json
import os
import signal
import socket
import ssl
import time
from datetime import UTC, datetime, timedelta

import pytest
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.runtime_api import RuntimeSlot, create_app
from guardrails.evolution.runtime_apply import RuntimeApplyClient, RuntimeSnapshotStore
from https_support import serve_https
from test_destination_lifecycle import app as management_app
from test_evaluation_runtime import fixture_system, http, runtime
from test_evaluation_runtime import request as evaluation_request


def test_real_https_readiness_rejects_revoked_install_with_unexpired_signed_cache(tmp_path):
    system = fixture_system(tmp_path)
    try:
        with serve_https(lambda: create_app(service=runtime(system)), tmp_path / "tls") as server, http(server, system["tokens"]["workload"]) as client:
            schema = client.get("/openapi.json").json()
            responses = schema["paths"]["/health/ready"]["get"]["responses"]
            assert responses["200"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/Readiness"}
            assert {value["$ref"] for value in responses["503"]["content"]["application/json"]["schema"]["oneOf"]} == {"#/components/schemas/Readiness", "#/components/schemas/ErrorEnvelope"}
            assert client.get("/health/ready").status_code == 200
            assert system["stores"][0].ready()
            with system["repo"].transaction(system["tenant"]) as tx:
                value = tx.get("runtimes", system["runtime_ids"][0], required=True)
                tx.put("runtimes", system["runtime_ids"][0], {**value, "status": "revoked"}, expected_revision=value["revision"])
            response = client.get("/health/ready")
            assert response.status_code == 503, response.text
            assert response.json() == {"ready": False, "dependency_codes": ["forbidden"]}
            assert system["stores"][0].ready()
            assert client.get("/v2/capabilities").status_code == 403
    finally:
        system["service"].audit.close()
        system["repo"].close()


def test_cache_lock_is_bounded_without_request_deadline(tmp_path):
    system = fixture_system(tmp_path)
    store = system["stores"][0]
    lock = os.open(store.path.with_name(store.path.name + ".lock"), os.O_RDWR)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        started = time.monotonic()
        with pytest.raises(EvolutionError) as blocked:
            RuntimeSnapshotStore(store.path, store.tenant_id, store.runtime_id, store.trust_keys)
        assert blocked.value.code == "snapshot_stale"
        assert 1.9 <= time.monotonic() - started < 3
        fcntl.flock(lock, fcntl.LOCK_UN)
        assert store.ready()
    finally:
        os.close(lock)
        system["service"].audit.close()
        system["repo"].close()


def test_https_poll_retries_transport_failure_without_extending_cached_lease(tmp_path):
    system = fixture_system(tmp_path)
    store = system["stores"][0]
    credential = tmp_path / "runtime-token"
    credential.write_text(system["tokens"]["runtime-a"])
    credential.chmod(0o600)
    try:
        with serve_https(lambda: management_app(system), tmp_path / "management-tls") as server:
            sync = RuntimeApplyClient(server["url"], server["ca_file"], str(credential), store)
            # A refused loopback connection exercises the real HTTP transport.
            # The same long-lived poll task must recover after configuration of
            # its original trusted HTTPS origin; no alternative content source.
            origin = sync.client.base_url
            sync.client.base_url = "https://127.0.0.1:1"
            service = runtime(system)
            service.slots[system["tenant"]] = RuntimeSlot(store, sync)
            async def exercise():
                await service.start()
                try:
                    saved_generation, saved_expiry = store.snapshot.generation, store.snapshot.expires_at
                    await asyncio.sleep(0.4)
                    assert len(service._polls) == 1
                    assert all(not task.done() for task in service._polls)
                    assert store.snapshot.expires_at == saved_expiry
                    store.clock = lambda: datetime.now(UTC) + timedelta(seconds=61)
                    assert not store.ready()
                    store.clock = lambda: datetime.now(UTC)
                    with system["repo"].transaction(system["tenant"]) as tx:
                        system["service"].bindings.release(tx, system["actors"]["operator"], policy=system["policy_ref"], workload_ids=["chat"], stage="canary", state="observe", expected_revisions={"chat": 1}, reason="Refresh after outage")
                    sync.client.base_url = origin
                    deadline = time.monotonic() + 5
                    while store.snapshot.generation == saved_generation and time.monotonic() < deadline:
                        await asyncio.sleep(0.05)
                    assert store.snapshot.generation > saved_generation
                    assert store.ready()
                    assert len(service._polls) == 1
                finally:
                    await service.close()
            asyncio.run(exercise())
    finally:
        system["service"].audit.close()
        system["repo"].close()


def test_real_tls_disconnect_cancels_detector_and_releases_quota_without_allow_audit(tmp_path):
    system = fixture_system(tmp_path, mandatory=True)
    pids_file = tmp_path / "runtime-worker-pids.json"
    def factory():
        service = runtime(system)
        original_start = service.start
        async def start():
            await original_start()
            pids_file.write_text(json.dumps(service.registry._pools[system["tenant"]].worker_pids))
        service.start = start
        return create_app(service=service)
    try:
        with serve_https(factory, tmp_path / "disconnect-tls") as server:
            pids = json.loads(pids_file.read_bytes())
            for pid in pids:
                os.kill(pid, signal.SIGSTOP)
            from urllib.parse import urlsplit
            origin = urlsplit(server["url"])
            context = ssl.create_default_context(cafile=server["ca_file"])
            stream = context.wrap_socket(socket.create_connection((origin.hostname, origin.port), timeout=2), server_hostname=origin.hostname)
            payload = evaluation_request(system)
            body = json.dumps(payload).encode()
            started = time.monotonic()
            stream.sendall(("POST /v2/evaluations HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer " + system["tokens"]["workload"] + "\r\nContent-Type: application/json\r\nContent-Length: " + str(len(body)) + "\r\n\r\n").encode() + body)
            try:
                deadline = started + .35
                while True:
                    with system["repo"].transaction(system["tenant"]) as tx:
                        # One READ COMMITTED snapshot must observe both rows;
                        # separate queries may straddle the admission commit.
                        rows = list(tx.execute("SELECT kind,data FROM evolution.records WHERE tenant_id=:tenant AND kind IN ('quota_leases','evaluation_contexts')", {"tenant": system["tenant"]}))
                        leases = [row.data for row in rows if row.kind == "quota_leases"]
                        contexts = [row.data for row in rows if row.kind == "evaluation_contexts"]
                    if contexts:
                        break
                    assert time.monotonic() < deadline, "request was not admitted before detector deadline"
                    time.sleep(.002)
                assert leases[0]["state"] == "active"
                assert contexts[0]["state"] == "evaluating_input"
            finally:
                stream.close()
            deadline = time.monotonic() + 1
            while True:
                with system["repo"].transaction(system["tenant"]) as tx:
                    leases = tx.list("quota_leases")
                    events = [item for item in tx.list("audit") if item["event_type"] == "evaluation"]
                if leases and all(item["state"] != "active" for item in leases):
                    break
                assert time.monotonic() < deadline, "cancelled request retained an active quota lease"
                time.sleep(.01)
            assert events == []
            dead = 0
            for pid in pids:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    dead += 1
            assert dead >= 1
            assert time.monotonic() - started < .5
    finally:
        system["service"].audit.close()
        system["repo"].close()
