"""Actual TLS slow-body and PostgreSQL lock deadlines preserve atomic mutations."""

import json
import socket
import ssl
import time
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from test_cli import https_system  # noqa: F401 -- shared real-server fixture


def test_management_slow_body_times_out_before_configuration_write(https_system):
    repo, service, tenant, tokens, actors, payload, cli, url, cert = https_system
    parsed = urlsplit(url)
    token = tokens["admin"].read_text().strip()
    request_id = str(uuid4())
    with repo.transaction(tenant) as tx:
        before = len(tx.list("config:quotas"))
    context = ssl.create_default_context(cafile=str(cert))
    with socket.create_connection(
        (parsed.hostname, parsed.port), timeout=8
    ) as connection:
        with context.wrap_socket(
            connection, server_hostname=parsed.hostname
        ) as secured:
            headers = (
                f"POST /v2/tenants/{tenant}/quotas HTTP/1.1\r\n"
                f"Host: {parsed.netloc}\r\nAuthorization: Bearer {token}\r\n"
                f"Idempotency-Key: {uuid4()}\r\nX-Request-ID: {request_id}\r\n"
                "Content-Type: application/json\r\nContent-Length: 200\r\n"
                "Connection: close\r\n\r\n{"
            )
            started = time.monotonic()
            secured.sendall(headers.encode())
            response = bytearray()
            while chunk := secured.recv(4096):
                response.extend(chunk)
                assert len(response) < 8192
            elapsed = time.monotonic() - started
    status, body = bytes(response).split(b"\r\n\r\n", 1)
    assert status.startswith(b"HTTP/1.1 504")
    error = json.loads(body)["error"]
    assert error["code"] == "upstream_timeout" and error["correlation_id"] == request_id
    assert token.encode() not in response
    assert 4.5 <= elapsed < 7
    with repo.transaction(tenant) as tx:
        assert len(tx.list("config:quotas")) == before


def test_management_lock_timeout_rolls_back_and_retry_creates_once(https_system):
    repo, service, tenant, tokens, actors, payload, cli, url, cert = https_system
    route = f"/v2/tenants/{tenant}/quotas"
    key, request_id = str(uuid4()), str(uuid4())
    token = tokens["admin"].read_text().strip()
    headers = {
        "Authorization": "Bearer " + token,
        "Idempotency-Key": key,
        "X-Request-ID": request_id,
    }
    idem = f"{actors['admin']['principal_id']}:POST:{route}:{key}"
    with httpx.Client(
        base_url=url,
        verify=ssl.create_default_context(cafile=str(cert)),
        timeout=8,
        trust_env=False,
    ) as client:
        with repo.transaction(tenant) as tx:
            before = len(tx.list("config:quotas"))
            tx.lock("idempotency:" + idem)
            started = time.monotonic()
            timed_out = client.post(
                route, headers=headers, json={"requests_per_minute": 99}
            )
            elapsed = time.monotonic() - started
            assert timed_out.status_code == 504
            assert timed_out.json()["error"]["code"] == "upstream_timeout"
            assert token not in timed_out.text and "SELECT" not in timed_out.text
            assert 4.5 <= elapsed < 7
            assert tx.get("idempotency", idem) is None
            assert len(tx.list("config:quotas")) == before
        retry = client.post(route, headers=headers, json={"requests_per_minute": 99})
        replay = client.post(route, headers=headers, json={"requests_per_minute": 99})
    assert retry.status_code == replay.status_code == 201
    assert (
        retry.json() == replay.json()
        and retry.headers["Location"] == replay.headers["Location"]
    )
    with repo.transaction(tenant) as tx:
        assert len(tx.list("config:quotas")) == before + 1
