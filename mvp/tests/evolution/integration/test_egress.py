"""Real socket/TLS boundaries; synthetic server emits deterministic failures."""

import asyncio
import multiprocessing
import time
from dataclasses import replace
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import (
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from guardrails.evolution.egress import AuthorizedRoute, PinnedHttpsTransport
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.models import (
    Destination,
    ProviderProfile,
    with_resource_digest,
)
from https_support import serve_https, tls_files


@pytest.fixture
def server(tmp_path):
    calls = multiprocessing.get_context("fork").Value("i", 0)
    sink = multiprocessing.get_context("fork").Value("i", 0)

    def factory():
        app = FastAPI()

        @app.get("/health/live")
        async def live():
            return {"alive": True}

        @app.post("/sink")
        async def forbidden():
            with sink.get_lock():
                sink.value += 1
            return {"received": True}

        @app.post("/api/{operation}")
        async def operation(operation: str, request: Request):
            with calls.get_lock():
                calls.value += 1
            if operation == "redirect":
                return RedirectResponse("/sink", status_code=307)
            if operation == "compressed":
                return Response(b"encoded", headers={"Content-Encoding": "gzip"})
            if operation == "large":
                return Response(b"x" * 1025)
            if operation in {"trickle", "chunked"}:

                async def chunks():
                    for _ in range(20):
                        yield b"x" * 128
                        await asyncio.sleep(0.075 if operation == "trickle" else 0)

                return StreamingResponse(chunks())
            return JSONResponse(
                {
                    "host": request.headers["host"],
                    "body": (await request.body()).decode(),
                    "encoding": request.headers.get("accept-encoding"),
                }
            )

        return app

    with serve_https(factory, tmp_path / "tls") as running:
        yield {**running, "calls": calls, "sink": sink}


def route_for(server, **changes):
    destination = with_resource_digest(
        Destination(
            tenant_id="egress-test",
            destination_id=str(uuid4()),
            version=1,
            name="registered-model",
            host="localhost",
            port=urlsplit(server["url"]).port,
            path_prefix="/api",
            cidrs=("127.0.0.1/32",),
            zone="local-shared",
            purpose="inference",
            tls_ca_ref="test-ca",
            **changes,
        )
    )
    provider = with_resource_digest(
        ProviderProfile(
            tenant_id="egress-test",
            provider_id=str(uuid4()),
            version=1,
            kind="model",
            vendor="ollama",
            model_id="synthetic-fixture",
            destination_id=destination.destination_id,
            zone="local-shared",
        )
    )
    return AuthorizedRoute(provider, destination)


def transport_for(server, route, *, resolver=None, registered=True, ca_file=None):
    async def resolve(host, port):
        assert host == route.destination.host
        assert port == route.destination.port
        return ("127.0.0.1",)

    return PinnedHttpsTransport(
        ca_files={"test-ca": ca_file or server["ca_file"]},
        local_services={
            route.destination.destination_id: (route.destination.host, ("127.0.0.1",))
        }
        if registered
        else {},
        resolver=resolver or resolve,
    )


def send(transport, route, path="/api/echo", *, budget=2, limit=1024, **options):
    return asyncio.run(
        transport.request(
            route,
            method="POST",
            path=path,
            body=b"synthetic",
            deadline_monotonic=time.monotonic() + budget,
            max_response_bytes=limit,
            **options,
        )
    )


def assert_denied(call, code):
    with pytest.raises(EvolutionError) as failure:
        call()
    assert failure.value.code == code
    assert "synthetic" not in str(failure.value)


def test_tls_numeric_connect_preserves_original_hostname_and_ignores_proxies(
    server, monkeypatch
):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("SSL_CERT_FILE", "/nonexistent-ca")
    route = route_for(server)
    transport = transport_for(server, route)
    response = send(transport, route)
    assert response.status_code == 200
    assert f'"host":"localhost:{route.destination.port}"'.encode() in response.content
    assert b'"body":"synthetic"' in response.content
    assert b'"encoding":"identity"' in response.content
    assert server["calls"].value == 1


def test_each_connection_resolves_again_and_rebinding_never_dispatches(server):
    route = route_for(server)
    lookups = []

    async def resolve(host, port):
        lookups.append((host, port))
        return ("127.0.0.1",) if len(lookups) == 1 else ("169.254.169.254",)

    transport = transport_for(server, route, resolver=resolve)
    assert send(transport, route).status_code == 200
    assert_denied(lambda: send(transport, route), "destination_denied")
    assert len(lookups) == 2
    assert server["calls"].value == 1


@pytest.mark.parametrize(
    "addresses",
    [
        (),
        ("127.0.0.1", "169.254.169.254"),
        ("127.0.0.1", "::1"),
        ("::ffff:127.0.0.1",),
        ("0.0.0.0",),
        ("224.0.0.1",),
        ("not-an-ip",),
    ],
)
def test_every_dns_answer_must_be_valid_pinned_and_registered(server, addresses):
    route = route_for(server)

    async def resolve(host, port):
        return addresses

    assert_denied(
        lambda: send(transport_for(server, route, resolver=resolve), route),
        "destination_denied",
    )
    assert server["calls"].value == 0


def test_cidr_alone_does_not_authorize_private_local_service(server):
    route = route_for(server)
    assert_denied(
        lambda: send(transport_for(server, route, registered=False), route),
        "destination_denied",
    )
    assert server["calls"].value == 0


@pytest.mark.parametrize("failure", ["hostname", "ca", "missing_ca"])
def test_certificate_identity_and_explicit_ca_are_required(server, tmp_path, failure):
    route = route_for(server)
    ca_file = None
    if failure == "hostname":
        destination = with_resource_digest(
            route.destination.model_copy(
                update={"host": "unregistered-certificate.test"}
            )
        )
        route = replace(route, destination=destination)
    if failure == "ca":
        ca_file = str(tls_files(tmp_path / "other-ca")["ca"])
    transport = transport_for(server, route, ca_file=ca_file)
    if failure == "missing_ca":
        transport.ca_files.clear()
    assert_denied(
        lambda: send(transport, route),
        "destination_denied" if failure == "missing_ca" else "evaluator_unavailable",
    )
    assert server["calls"].value == 0


def test_redirect_is_never_followed_or_retried(server):
    route = route_for(server)
    assert_denied(
        lambda: send(transport_for(server, route), route, "/api/redirect"),
        "destination_denied",
    )
    assert server["calls"].value == 1
    assert server["sink"].value == 0


@pytest.mark.parametrize(
    "path",
    [
        "/api-other/echo",
        "/api/../sink",
        "/api/%2e%2e/sink",
        "//localhost/api/echo",
        "/api/echo?next=/sink",
        "/api/echo#fragment",
        "/api\\echo",
    ],
)
def test_literal_path_boundary_rejected_before_dns(server, path):
    route = route_for(server)

    async def never(host, port):
        pytest.fail("invalid path reached DNS")

    assert_denied(
        lambda: send(transport_for(server, route, resolver=never), route, path),
        "destination_denied",
    )
    assert server["calls"].value == 0


def test_response_caps_compression_and_absolute_trickle_deadline(server):
    route = route_for(server)
    transport = transport_for(server, route)
    for operation in ("large", "chunked", "compressed"):
        assert_denied(
            lambda operation=operation: send(transport, route, f"/api/{operation}"),
            "validation_failed",
        )
    started = time.monotonic()
    assert_denied(
        lambda: send(transport, route, "/api/trickle", budget=0.25), "upstream_timeout"
    )
    assert time.monotonic() - started < 0.8
    assert server["calls"].value == 4


def test_route_digest_authority_and_framing_are_checked_before_io(server):
    route = route_for(server)
    transport = transport_for(server, route)
    bad = replace(
        route, destination=route.destination.model_copy(update={"path_prefix": "/"})
    )
    assert_denied(lambda: send(transport, bad), "destination_denied")
    url = f"https://localhost:{route.destination.port}/api/echo"
    assert transport.validate_url(route, url) == "/api/echo"
    for malformed in (
        url.replace("https:", "http:"),
        url.replace("localhost", "other.test"),
        url.replace(str(route.destination.port), str(route.destination.port + 1)),
        url.replace("localhost", "user@localhost"),
        url + "?query=1",
    ):
        assert_denied(
            lambda malformed=malformed: transport.validate_url(route, malformed),
            "destination_denied",
        )
    for headers in (
        {"Host": "other.test"},
        {"Content-Length": "0"},
        {"Accept-Encoding": "gzip"},
        {"Authorization": "token\r\nInjected: yes"},
    ):
        assert_denied(
            lambda headers=headers: send(transport, route, headers=headers),
            "validation_failed",
        )
    assert server["calls"].value == 0


def test_external_global_address_needs_explicit_policy_authorization(server):
    route = route_for(server)
    route = replace(
        route,
        destination=with_resource_digest(
            route.destination.model_copy(update={"cidrs": ("8.8.8.8/32",)})
        ),
    )
    transport = transport_for(server, route)
    assert_denied(lambda: transport._addresses(route, ("8.8.8.8",)), "residency_denied")
    allowed = replace(route, external_processing=True)
    assert str(transport._addresses(allowed, ("8.8.8.8",))[0]) == "8.8.8.8"
    assert server["calls"].value == 0


@pytest.mark.parametrize(
    "address,cidr",
    [
        ("169.254.169.254", "169.254.169.254/32"),
        ("fe80::1", "fe80::1/128"),
        ("100.64.0.1", "100.64.0.1/32"),
        ("240.0.0.1", "240.0.0.1/32"),
    ],
)
def test_local_registration_cannot_authorize_metadata_link_local_or_reserved(
    server, address, cidr
):
    route = route_for(server)
    route = replace(
        route,
        destination=with_resource_digest(
            route.destination.model_copy(update={"cidrs": (cidr,)})
        ),
    )
    transport = transport_for(server, route)
    transport.local_services[route.destination.destination_id] = (
        route.destination.host,
        (address,),
    )
    assert_denied(lambda: transport._addresses(route, (address,)), "destination_denied")
    assert server["calls"].value == 0


def test_slow_dns_is_part_of_connect_budget_before_any_dispatch(server):
    route = route_for(server)

    async def slow(host, port):
        await asyncio.sleep(1)
        return ("127.0.0.1",)

    transport = transport_for(server, route, resolver=slow)
    started = time.monotonic()
    assert_denied(lambda: send(transport, route, budget=2), "upstream_timeout")
    assert time.monotonic() - started < 0.6
    assert server["calls"].value == 0
