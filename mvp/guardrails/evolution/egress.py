"""One-shot HTTPS dispatch using exact approved resource bytes and checked IPs.

This module is also imported by the Python 3.13 connector. It intentionally
depends only on public models/errors, never on service storage or compilation.
"""

from __future__ import annotations

import asyncio
import ipaddress
import math
import re
import socket
import ssl
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from urllib.parse import urlsplit

import httpcore
from pydantic import ValidationError

from .errors import EvolutionError
from .models import Destination, ProviderProfile, canonical_json_bytes, resource_digest


@dataclass(frozen=True)
class AuthorizedRoute:
    """Trusted policy-bound route; caller must authenticate the policy binding."""

    provider: ProviderProfile
    destination: Destination
    external_processing: bool = False

    def validate(self) -> None:
        try:
            provider = ProviderProfile.model_validate_json(
                canonical_json_bytes(self.provider)
            )
            destination = Destination.model_validate_json(
                canonical_json_bytes(self.destination)
            )
        except (ValidationError, ValueError, TypeError):
            raise EvolutionError("destination_denied") from None
        if (
            type(self.external_processing) is not bool
            or not provider.enabled
            or provider.digest != resource_digest(provider)
            or destination.digest != resource_digest(destination)
            or provider.destination_id != destination.destination_id
            or provider.zone != destination.zone
            or provider.tenant_id != destination.tenant_id
            and "_platform" not in (provider.tenant_id, destination.tenant_id)
            or destination.purpose
            != {"model": "inference", "evaluator": "evaluation"}[provider.kind]
        ):
            raise EvolutionError("destination_denied")
        if provider.vendor == "bedrock" and not self.external_processing:
            raise EvolutionError("residency_denied")


@dataclass(frozen=True)
class PinnedResponse:
    status_code: int
    headers: tuple[tuple[str, str], ...]
    content: bytes


Resolver = Callable[[str, int], Awaitable[Sequence[str]]]


async def _resolve(host: str, port: int) -> tuple[str, ...]:
    rows = await asyncio.get_running_loop().getaddrinfo(
        host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP
    )
    return tuple(dict.fromkeys(row[4][0] for row in rows))


def _path_allowed(path: str, prefix: str) -> bool:
    if (
        not isinstance(path, str)
        or not 1 <= len(path) <= 4096
        or not path.startswith("/")
        or "//" in path
        or any(char in path for char in "?#@\\%")
        or any(ord(char) <= 32 or ord(char) >= 127 for char in path)
        or any(piece in {".", ".."} for piece in path.split("/"))
    ):
        return False
    boundary = prefix.rstrip("/")
    return path == boundary or path.startswith(boundary + "/")


class _PinnedBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, transport: PinnedHttpsTransport, route: AuthorizedRoute):
        self.transport = transport
        self.route = route
        self.delegate = httpcore.AnyIOBackend()

    async def connect_tcp(
        self, host, port, timeout=None, local_address=None, socket_options=None
    ):
        destination = self.route.destination
        if (
            host != destination.host
            or port != destination.port
            or local_address is not None
        ):
            raise EvolutionError("destination_denied")
        connect_deadline = time.monotonic() + min(timeout or 0.25, 0.25)
        async with asyncio.timeout_at(connect_deadline):
            addresses = await self.transport.resolver(host, port)
            checked = self.transport._addresses(self.route, addresses)
            # Only numeric IPs reach the backend: DNS cannot change between
            # checking and connecting. The URL hostname remains TLS SNI/Host.
            stream = await self.delegate.connect_tcp(
                str(checked[0]), port, timeout=timeout, socket_options=socket_options
            )
        return _ConnectBudgetStream(stream, connect_deadline)

    async def connect_unix_socket(self, *args, **kwargs):
        raise EvolutionError("destination_denied")

    async def sleep(self, seconds):
        raise EvolutionError("destination_denied")


class _ConnectBudgetStream(httpcore.AsyncNetworkStream):
    """The 250 ms connect budget includes DNS, TCP and the TLS handshake."""

    def __init__(self, stream, deadline):
        self.stream = stream
        self.deadline = deadline

    async def read(self, max_bytes, timeout=None):
        return await self.stream.read(max_bytes, timeout=timeout)

    async def write(self, buffer, timeout=None):
        return await self.stream.write(buffer, timeout=timeout)

    async def aclose(self):
        await self.stream.aclose()

    def get_extra_info(self, info):
        return self.stream.get_extra_info(info)

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        try:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            async with asyncio.timeout_at(self.deadline):
                self.stream = await self.stream.start_tls(
                    ssl_context,
                    server_hostname=server_hostname,
                    timeout=min(timeout or remaining, remaining),
                )
        except BaseException:
            # AsyncHTTPConnection has no stream ownership until TLS succeeds.
            # Close on cancellation too: AnyIO's backend catches only Exception.
            await self.stream.aclose()
            raise
        return self


class PinnedHttpsTransport:
    """No proxy, redirect, retry, cross-route pooling, or implicit CA lookup."""

    def __init__(
        self,
        *,
        ca_files: Mapping[str, str] | None = None,
        ca_pem: Mapping[str, bytes] | None = None,
        local_services: Mapping[str, tuple[str, tuple[str, ...]]] | None = None,
        resolver: Resolver | None = None,
    ):
        self.ca_files = dict(ca_files or {})
        certificates = dict(ca_pem or {})
        if (certificates.keys() & self.ca_files.keys() or any(
            not isinstance(reference, str) or not reference
            or not isinstance(pem, bytes) or not 1 <= len(pem) <= 1_048_576
            for reference, pem in certificates.items()
        )):
            raise EvolutionError("validation_failed")
        # Operator endpoint snapshots retain the CA they actually validated.
        # File-based callers keep their existing explicit reload semantics.
        self.ca_pem = MappingProxyType(certificates)
        self.local_services = dict(local_services or {})
        self.resolver = resolver or _resolve

    def _addresses(self, route: AuthorizedRoute, addresses: Sequence[str]):
        if not isinstance(addresses, (tuple, list)) or not 1 <= len(addresses) <= 64:
            raise EvolutionError("destination_denied")
        destination = route.destination
        try:
            parsed = tuple(ipaddress.ip_address(value) for value in addresses)
            networks = tuple(
                ipaddress.ip_network(value, strict=True) for value in destination.cidrs
            )
            registration = self.local_services.get(destination.destination_id)
            local = (
                frozenset(ipaddress.ip_address(value) for value in registration[1])
                if registration and registration[0] == destination.host
                else frozenset()
            )
        except (ValueError, TypeError, IndexError):
            raise EvolutionError("destination_denied") from None
        for address in parsed:
            # Scoped IPv6 and mapped IPv4 aliases may hide a different address
            # class or interface; registrations must name ordinary exact IPs.
            if getattr(address, "scope_id", None) or getattr(
                address, "ipv4_mapped", None
            ):
                raise EvolutionError("destination_denied")
            if not any(address in network for network in networks):
                raise EvolutionError("destination_denied")
            if address.is_unspecified or address.is_multicast or address.is_link_local:
                raise EvolutionError("destination_denied")
            if address.is_global:
                if not route.external_processing:
                    raise EvolutionError("residency_denied")
            else:
                permitted_private = any(
                    address in ipaddress.ip_network(cidr)
                    for cidr in (
                        "10.0.0.0/8",
                        "172.16.0.0/12",
                        "192.168.0.0/16",
                        "fc00::/7",
                    )
                )
                if address not in local or not (
                    address.is_loopback or permitted_private
                ):
                    raise EvolutionError("destination_denied")
        return parsed

    @staticmethod
    def validate_url(route: AuthorizedRoute, url: str) -> str:
        """Match a connector's actual full URL before it extracts the path."""
        route.validate()
        try:
            parsed = urlsplit(url)
            destination = route.destination
            if (
                parsed.scheme != "https"
                or parsed.hostname != destination.host
                or (parsed.port or 443) != destination.port
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
                or not _path_allowed(parsed.path, destination.path_prefix)
                or parsed.netloc
                != destination.host
                + (f":{destination.port}" if destination.port != 443 else "")
            ):
                raise ValueError
        except (ValueError, TypeError):
            raise EvolutionError("destination_denied") from None
        return parsed.path

    async def request(
        self,
        route: AuthorizedRoute,
        *,
        method: str,
        path: str,
        body: bytes,
        headers: Mapping[str, str] | None = None,
        deadline_monotonic: float,
        max_response_bytes: int = 1_048_576,
    ) -> PinnedResponse:
        route.validate()
        destination = route.destination
        if not _path_allowed(path, destination.path_prefix):
            raise EvolutionError("destination_denied")
        if (
            method not in {"GET", "POST"}
            or not isinstance(body, bytes)
            or len(body) > 1_048_576
        ):
            raise EvolutionError("validation_failed")
        if (
            type(max_response_bytes) is not int
            or not 1 <= max_response_bytes <= 24 * 1_048_576
        ):
            raise EvolutionError("validation_failed")
        if type(deadline_monotonic) not in (float, int) or not math.isfinite(
            deadline_monotonic
        ):
            raise EvolutionError("validation_failed")
        remaining = deadline_monotonic - time.monotonic()
        if remaining <= 0:
            raise EvolutionError("upstream_timeout")
        supplied = dict(headers or {})
        if len(supplied) > 32:
            raise EvolutionError("validation_failed")
        outgoing = []
        seen = set()
        forbidden = {
            "host",
            "content-length",
            "transfer-encoding",
            "connection",
            "upgrade",
            "expect",
            "accept-encoding",
            "proxy-authorization",
            "proxy-connection",
        }
        for name, value in supplied.items():
            if (
                not isinstance(name, str)
                or not isinstance(value, str)
                or not re.fullmatch(r"[A-Za-z0-9!#$&'*+.^_`|~-]{1,128}", name)
                or len(value) > 8192
                or any(ord(char) < 32 or ord(char) >= 127 for char in value)
                or name.lower() in forbidden
                or name.lower() in seen
            ):
                raise EvolutionError("validation_failed")
            seen.add(name.lower())
            outgoing.append((name.encode("ascii"), value.encode("ascii")))
        outgoing.extend([(b"accept-encoding", b"identity"), (b"connection", b"close")])
        if sum(len(name) + len(value) for name, value in outgoing) > 32768:
            raise EvolutionError("validation_failed")
        ca_file = self.ca_files.get(destination.tls_ca_ref)
        ca_pem = self.ca_pem.get(destination.tls_ca_ref)
        if ca_pem is None and (not isinstance(ca_file, str) or not ca_file):
            raise EvolutionError("destination_denied")
        try:
            # Explicit CA only, hostname verification remains required. No
            # requests/SSL environment variables can widen this trust store.
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            if ca_pem is not None:
                context.load_verify_locations(cadata=ca_pem.decode("ascii"))
            else:
                context.load_verify_locations(cafile=ca_file)
            url = f"https://{destination.host}:{destination.port}{path}"
            async with asyncio.timeout_at(deadline_monotonic):
                async with (
                    httpcore.AsyncConnectionPool(
                        ssl_context=context,
                        proxy=None,
                        max_connections=1,
                        max_keepalive_connections=0,
                        http1=True,
                        http2=False,
                        retries=0,
                        network_backend=_PinnedBackend(self, route),
                    ) as pool,
                    pool.stream(
                        method,
                        url,
                        headers=outgoing,
                        content=body,
                        extensions={
                            "timeout": {
                                name: min(remaining, 0.25)
                                if name == "connect"
                                else remaining
                                for name in ("connect", "read", "write", "pool")
                            }
                        },
                    ) as response,
                ):
                    if 300 <= response.status < 400:
                        raise EvolutionError("destination_denied")
                    response_headers = tuple(
                        (name.decode("ascii").lower(), value.decode("latin-1"))
                        for name, value in response.headers
                    )
                    if any(
                        name == "content-encoding"
                        and value.lower().strip() != "identity"
                        for name, value in response_headers
                    ):
                        raise EvolutionError("validation_failed")
                    lengths = [
                        value
                        for name, value in response_headers
                        if name == "content-length"
                    ]
                    if (
                        len(lengths) > 1
                        or lengths
                        and (
                            not lengths[0].isdigit()
                            or int(lengths[0]) > max_response_bytes
                        )
                    ):
                        raise EvolutionError("validation_failed")
                    content = bytearray()
                    async for chunk in response.aiter_stream():
                        if len(content) + len(chunk) > max_response_bytes:
                            raise EvolutionError("validation_failed")
                        content.extend(chunk)
                    return PinnedResponse(
                        response.status, response_headers, bytes(content)
                    )
        except EvolutionError:
            raise
        except (TimeoutError, httpcore.TimeoutException):
            raise EvolutionError("upstream_timeout") from None
        except (
            OSError,
            ssl.SSLError,
            httpcore.NetworkError,
            httpcore.ProtocolError,
            ValueError,
            UnicodeError,
        ):
            raise EvolutionError("evaluator_unavailable") from None
