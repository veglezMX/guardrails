"""Explicitly invoked budget reader; no deployment/network activity at import.

The caller owns governance, current qualification/prepared/cold/access proof and
the deployment lock. Its REQUIRED transport recheck must authenticate the current
owned forwarding process and unexpired credential generation before/after each
observation; no digest callback stands in for that verification. A reader result
is an observation component, never a registered complete acceptance artifact.
"""
from __future__ import annotations

import copy
import hashlib
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

from evolution import classifier_budget_evidence as evidence
from guardrails.evolution import models
from guardrails.evolution.classifier_contract import canonical, parse
from guardrails.evolution.deadlines import current_deadline

from mvp.tests.evolution.e2e.classifier_network_support import (
    ReadinessObserver,
    private_bytes,
)

MAX_RESPONSE = 2 * 1024 * 1024


def check(deadline):
    evidence.require(time.monotonic() < deadline)


class BudgetObserver(ReadinessObserver):
    """A readiness method cannot extend the active whole-observation deadline."""

    budget_deadline = None

    def _remaining(self, maximum=10):
        original = super()._remaining(maximum)
        if self.budget_deadline is None:
            return original
        check(self.budget_deadline)
        return min(original, self.budget_deadline - time.monotonic())


def transport_material(cluster, expected, *, deadline):
    """Exact currently inventoried private bytes, independent of ambient DNS."""
    check(deadline)
    evidence.expectation(expected)
    access = cluster.access
    item = cluster.inventory["tenants"][expected["tenant"]]
    role = item["roles"]["workload"]
    evidence.require(cluster.profile == "classifier" and access["profile"] == "classifier"
                     and item["workload"]["workload_id"] == expected["workload_id"]
                     and role["principal_id"] == expected["principal_id"]
                     and access["tenants"][expected["tenant"]]["workload_token_file"] == role["token_file"])
    origin = access["runtime_url"]
    parsed = urlsplit(origin)
    evidence.require(parsed.scheme == "https" and parsed.hostname == "localhost"
        and parsed.username is None and parsed.password is None and parsed.port is not None
        and 1024 <= parsed.port <= 65535 and not parsed.path and not parsed.query and not parsed.fragment
        and origin == f"https://localhost:{parsed.port}")
    paths = {"ca": Path(access["ca_file"]), "token": Path(role["token_file"])}
    evidence.require(paths["ca"].is_absolute()
                     and paths["ca"] == cluster.state / "credentials" / "ca.crt")
    evidence.require(paths["token"].is_absolute()
                     and paths["token"].is_relative_to(cluster.state / "assembly"))
    raw = {name: private_bytes(path, deadline=deadline, maximum=65536 if name == "ca" else 4097)
           for name, path in paths.items()}
    try:
        token = raw["token"].decode("ascii").removesuffix("\n")
        ca = raw["ca"].decode("ascii")
    except UnicodeError:
        raise evidence.BudgetObservationError("classifier_budget_transport_invalid") from None
    evidence.require(re.fullmatch(r"[!-~]{32,4096}", token) is not None)
    check(deadline)
    # The issued serving certificate includes the 127.0.0.1 IP SAN. HTTPX
    # authenticates that exact address using only the pinned private CA bytes.
    return {"url": f"https://127.0.0.1:{parsed.port}/v2/evaluations", "host": f"localhost:{parsed.port}",
        "ca": ca, "token": token, "pins": {name + "_sha256": hashlib.sha256(value).hexdigest() for name, value in raw.items()}}


def response_headers(response, correlation):
    rows = response.headers.raw
    evidence.require(type(rows) is list and len(rows) <= 64)
    selected = {}
    for name, value in rows:
        evidence.require(type(name) is bytes and type(value) is bytes and len(name) <= 128 and len(value) <= 8192)
        key = name.lower()
        evidence.require(key not in selected)
        selected[key] = value
    evidence.require(selected.get(b"content-type", b"").split(b";", 1)[0] == b"application/json"
                     and selected.get(b"content-encoding", b"identity") == b"identity"
                     and selected.get(b"x-request-id") == correlation.encode("ascii")
                     and selected.get(b"cache-control") == b"no-store")
    if b"content-length" in selected:
        length = selected[b"content-length"]
        evidence.require(re.fullmatch(rb"[0-9]{1,7}", length) is not None and int(length) <= MAX_RESPONSE)
        return int(length)
    return None


async def exchange(material, sent, *, slow, deadline):
    """One original request; same 4 s clock includes setup/serialization/close."""
    started = time.monotonic()
    absolute = min(deadline, started + evidence.CLIENT_MS / 1000)
    check(absolute)
    import asyncio
    import ssl

    import httpx

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_verify_locations(cadata=material["ca"])
    body = canonical(sent)
    headers = {"Host": material["host"], "Authorization": "Bearer " + material["token"],
        "Content-Type": "application/json", "Content-Length": str(len(body)),
        "Accept-Encoding": "identity", "X-Request-ID": sent["correlation_id"]}
    delay = 0

    async def chunks():
        nonlocal delay
        yield body[:1]
        waiting = time.monotonic()
        await asyncio.sleep(0.6)
        delay = (time.monotonic() - waiting) * 1000
        yield body[1:]

    # Synchronous setup cannot consume the budget and receive a fresh timeout.
    # This check also prevents the first dispatch after a slow CA/body setup.
    check(absolute)
    loop = asyncio.get_running_loop()
    async with asyncio.timeout_at(loop.time() + absolute - time.monotonic()):
        async with httpx.AsyncClient(verify=context, trust_env=False, follow_redirects=False,  # noqa: SIM117 - explicit timeout/client/stream close order
                timeout=httpx.Timeout(4, connect=0.25),
                limits=httpx.Limits(max_connections=1, max_keepalive_connections=0)) as client:
            async with client.stream("POST", material["url"], headers=headers,
                    content=chunks() if slow else body) as response:
                evidence.require(type(response.status_code) is int and response.status_code == (504 if slow else 200))
                length = response_headers(response, sent["correlation_id"])
                stream = response.extensions.get("network_stream")
                evidence.require(stream is not None)
                tls = stream.get_extra_info("ssl_object")
                evidence.require(tls is not None and tls.version() in {"TLSv1.2", "TLSv1.3"})
                protocol = tls.version()
                raw = bytearray()
                async for chunk in response.aiter_raw(chunk_size=65536):
                    evidence.require(len(raw) + len(chunk) <= MAX_RESPONSE)
                    raw.extend(chunk)
                evidence.require(length is None or len(raw) == length)
                value = parse(bytes(raw), limit=MAX_RESPONSE)
                raw.clear()
    elapsed = (time.monotonic() - started) * 1000
    evidence.finite(elapsed, maximum=evidence.CLIENT_MS, minimum=0.001)
    check(deadline)
    return {"value": value, "client_elapsed_ms": elapsed, "body_delay_ms": delay, "tls": protocol}


def audit_rows(cluster, expected, sent, decision, *, deadline):
    """Read committed metadata via the existing non-owner tenant-scoped repo.

    The original response has already been timed. Query time is deliberately
    outside that clock; it verifies actual persistence rather than inflating or
    substituting for the runtime's post-commit elapsed field.
    """
    check(deadline)
    token = current_deadline.set(deadline)
    try:
        rows = []
        for tenant in (expected["tenant"], "team-b" if expected["tenant"] == "team-a" else "team-a"):
            with cluster.repo.transaction(tenant) as tx:
                selected = list(tx.execute("SELECT data FROM evolution.records WHERE tenant_id=:tenant "
                    "AND kind='audit' AND data->>'event_type'='evaluation' "
                    "AND data->>'correlation_id'=:correlation LIMIT 2",
                    {"tenant": tenant, "correlation": sent["correlation_id"]}))
                evidence.require(len(selected) <= 1 and (tenant == expected["tenant"] or not selected))
                rows.extend(row.data for row in selected)
                if decision is not None:
                    event = tx.get("audit", decision["decision_id"], required=tenant == expected["tenant"])
                    evidence.require(event == rows[0] if tenant == expected["tenant"] and rows else event is None)
        check(deadline)
        if decision is None:
            evidence.require(not rows)
            return 0
        evidence.require(len(rows) == 1)
        return rows[0]
    finally:
        current_deadline.reset(token)


class BudgetReader:
    """Single use, caller-locked bracket with retained finite partial progress."""

    def __init__(self, observer, *, recheck_transport):
        evidence.require(callable(recheck_transport) and isinstance(observer, BudgetObserver))
        self.observer = observer
        self.recheck_transport = recheck_transport
        self.value = {"status": "unavailable", "phase": "not_started", "observations": {}}

    def sample_locked(self, sent, *, expected, deadline):
        import asyncio

        evidence.require(self.value["phase"] == "not_started" and self.observer.bound is not None)
        evidence.request(sent, expected)
        started = time.monotonic()
        deadline = min(deadline, started + evidence.GROUP_MS / 1000)
        observer = self.observer
        previous = observer.budget_deadline
        observer.budget_deadline = deadline
        progress = self.value["observations"]
        try:
            self.value["phase"] = "binding"
            check(deadline)
            self.recheck_transport(deadline=deadline)
            material = transport_material(observer.cluster, expected, deadline=deadline)
            progress["transport_pins"] = copy.deepcopy(material["pins"])
            progress["ready_before"] = observer.sample_locked()
            self.value["phase"] = "request"
            wire = asyncio.run(exchange(material, sent, slow=expected["scenario"] == "slow_body", deadline=deadline))
            # Only finite transport facts enter retained evidence, never body/token.
            progress["wire"] = {key: wire[key] for key in ("client_elapsed_ms", "body_delay_ms", "tls")}
            self.value["phase"] = "audit"
            decision = None if expected["scenario"] == "slow_body" else wire["value"]
            if decision is not None:
                models.Decision.model_validate_json(canonical(decision))
            audit = audit_rows(observer.cluster, expected, sent, decision, deadline=deadline)
            self.value["phase"] = "after"
            progress["ready_after"] = observer.sample_locked()
            self.recheck_transport(deadline=deadline)
            after = transport_material(observer.cluster, expected, deadline=deadline)
            evidence.require(after == material)
            arguments = {"expected": expected, "sent": sent, "ready_before": progress["ready_before"],
                "ready_after": progress["ready_after"], "client_elapsed_ms": wire["client_elapsed_ms"]}
            if decision is None:
                row = evidence.slow_body(wire["value"], **arguments, body_delay_ms=wire["body_delay_ms"], audit_matches=audit)
            else:
                row = evidence.normal(decision, **arguments, audit=audit)
            check(deadline)
            self.value.update(status="observed", phase="complete", row=row,
                              elapsed_ms=(time.monotonic() - started) * 1000)
            evidence.finite(self.value["elapsed_ms"], maximum=evidence.GROUP_MS)
            return copy.deepcopy(self.value)
        except BaseException as error:
            self.value["status"] = "failed"
            kind = type(error).__name__
            self.value["failure_type"] = kind if kind in {"BudgetObservationError", "ClassifierObservationError", "TimeoutError", "AssertionError"} else "OtherError"
            raise
        finally:
            observer.budget_deadline = previous
