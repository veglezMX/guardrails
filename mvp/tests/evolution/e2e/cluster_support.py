"""Actual deployment access, bounded HTTP, and metadata-only assertions."""

from __future__ import annotations

import asyncio
import importlib.util
import os
import ssl
import stat
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from evolution.measurements import (
    MATRIX_READINESS_ROLES,
    validate_matrix_readiness,
    validate_real_model_observation,
)
from evolution.seed import private_read
from guardrails.evolution import models
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.storage import TenantRepository

ROOT = Path(__file__).resolve().parents[4]


async def observe_sample_response(response, row):
    """Observe this one sample-client response under its existing call deadline."""
    row["http_status"] = response.status_code
    if response.status_code == 200:
        return  # The actual sample client still owns all success validation.
    if (response.headers.get("content-type", "").split(";", 1)[0] != "application/json"
            or response.headers.get("content-encoding", "identity") != "identity"):
        return
    length = response.headers.get("content-length")
    if length is not None and (not length.isascii() or not 1 <= len(length) <= 4
                               or not length.isdecimal() or int(length) > 4096):
        return
    raw = bytearray()
    try:
        async for chunk in response.aiter_raw(chunk_size=4097):
            if len(raw) + len(chunk) > 4096:
                return
            raw.extend(chunk)
        value = models.strict_json_loads(bytes(raw), max_bytes=4096)
        if not isinstance(value, dict) or set(value) != {"error"}:
            return
        error = models.Error.model_validate_json(models.canonical_json_bytes(value["error"]))
        correlation = response.request.headers.get("x-request-id")
        if (value == EvolutionError(error.code).envelope(correlation)
                and response.headers.get("x-request-id") == correlation):
            row["wire_code"] = error.code
    except (httpx.HTTPError, ValueError, TypeError, KeyError):
        # Existing TeamClient mappings remain authoritative; never retain body,
        # identifiers or arbitrary exception text. Cancellation is not swallowed.
        pass
    finally:
        raw.clear()


def record_real_model_observation(rows):
    value = {
        "schema_version": "evolution-real-model-observation-v1",
        "expected_calls": 20, "actual_attempts": len(rows),
        "completed": sum(row["sample_completed"] for row in rows), "rows": rows,
    }
    validate_real_model_observation(value)
    if directory := os.environ.get("EVOLUTION_TEST_EVIDENCE_DIR"):
        path = Path(directory)
        info = path.lstat()
        if (not path.is_absolute() or not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700):
            raise ValueError("measurement_path_invalid")
        descriptor = os.open(path / "real-model-observation.json",
                             os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(models.canonical_json_bytes(value) + b"\n")


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module



class ReadinessObservation:
    """Fixture-only numeric observation of the original readiness GETs."""

    def __init__(self):
        self.rows = []
        self.started = []
        self.complete = False
        self.failed = False

    def begin(self, index):
        now = time.monotonic()
        if len(self.rows) == index:
            role, side = MATRIX_READINESS_ROLES[index]
            self.rows.append({"endpoint_role": role, "tenant_side": side,
                              "attempts": 0, "http_status": 0, "elapsed_ms": 0.0})
            self.started.append(now)
        if len(self.rows) != index + 1:
            raise ValueError("readiness_observer_order")
        row = self.rows[index]
        row["attempts"] += 1
        row["http_status"] = 0
        row["elapsed_ms"] = round((now - self.started[index]) * 1000, 3)

    def returned(self, index, response):
        self.rows[index]["http_status"] = response.status_code
        self.elapsed(index)

    def elapsed(self, index):
        if index < len(self.rows):
            self.rows[index]["elapsed_ms"] = round((time.monotonic() - self.started[index]) * 1000, 3)

    def finished(self):
        self.complete = True

    def snapshot(self):
        if self.failed:
            return {"state": "unavailable", "rows": []}
        return {"state": "complete" if self.complete else "partial",
                "rows": [dict(row) for row in self.rows]}


def _observe_readiness(observer, operation, *args):
    # Guard the complete bookkeeping computation, never the original HTTP GET.
    if observer is not None:
        try:
            getattr(observer, operation)(*args)
        except BaseException:  # noqa: BLE001 -- Observation must not change the original outcome.
            try:
                observer.failed = True
            except BaseException:  # noqa: BLE001, S110 -- Drop bookkeeping errors without exposing private text.
                pass


def readiness_snapshot(observer):
    try:
        value = observer.snapshot()
        validate_matrix_readiness(value)
        return value
    except BaseException:  # noqa: BLE001 -- Untrusted observer failure becomes finite unavailable evidence.
        return {"state": "unavailable", "rows": []}


class Cluster:
    def __init__(self, profile, *, readiness_observation=None):
        self.readiness_observation = readiness_observation
        assert profile in {"deterministic_mock", "local_model"}
        self.state = Path(os.environ["EVOLUTION_DEPLOYMENT_STATE"]).absolute()
        self.profile = profile
        bootstrap = load_module(
            "evolution_cluster_deployment", ROOT / "deploy/evolution/bootstrap.py"
        )
        self.deployment = bootstrap.LocalDeployment(ROOT, state=self.state)
        access_path = self.state / "assembly/access.json"
        # Reuse only protected state belonging to this actual deployment. Every
        # case still validates live TLS/readiness, and gate pre/post verification
        # still hashes the complete prepared inventory. A profile switch calls
        # the production platform path, including its full integrity check.
        if os.path.lexists(access_path):
            current = models.strict_json_loads(private_read(access_path))
            assert current["schema_version"] == "guardrails-evolution-local-access-v1"
            assert current["profile"] in {"deterministic_mock", "local_model"}
            if current["profile"] != profile:
                self.platform()
        else:
            self.platform()
        self.access = self._access()
        self.credentials = self._credentials()
        self.inventory = self.credentials["inventory"]
        self.tenants = tuple(sorted(self.inventory["tenants"]))
        assert len(self.tenants) == 2
        assert set(self.access["tenants"]) == set(self.tenants)
        for tenant in self.tenants:
            matches = (self.access["tenants"][tenant]["workload_token_file"]
                       == self.inventory["tenants"][tenant]["roles"]["workload"]["token_file"])
            assert matches, "access workload credential differs from the validated generation"
        self.http = httpx.Client(
            verify=ssl.create_default_context(cafile=self.access["ca_file"]),
            trust_env=False,
            follow_redirects=False,
            timeout=15,
        )
        try:
            self.assert_ready()
            self.repo = TenantRepository(
                private_read(self.access["database_url_file"], maximum=4096)
                .decode()
                .strip()
            )
        except BaseException:
            if readiness_observation is None:
                self.http.close()
            else:
                # Preserve the original observed constructor failure even when
                # closing its already-created client also fails.
                try:
                    self.http.close()
                except BaseException:  # noqa: BLE001, S110 -- Preserve the original failure; never log private text.
                    pass
            raise
        self.sample = load_module(
            "evolution_cluster_sample", ROOT / "examples/evolution/client.py"
        )

    def platform(self):
        # The measured 13.58 GiB integrity scan alone takes about 240 seconds on
        # this host. A real profile switch therefore gets a bounded 600-second
        # preparation allowance; this never changes evaluation/model deadlines.
        # One dedicated replay case exercises this full path after onboarding.
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "deploy/evolution/bootstrap.py"),
                "platform",
                "--state",
                str(self.state),
                "--profile",
                self.profile,
            ],
            capture_output=True,
            timeout=600,
            check=False,
        )
        assert result.returncode == 0, (
            "actual cluster platform/profile must become ready"
        )

    def _access(self):
        # This production operation checks the live task/container owner,
        # assembly owner and each forwarding process identity before reuse.
        observed = self.deployment.access()
        path = self.state / "assembly/access.json"
        assert observed["status"] == "available"
        assert Path(observed["access_file"]) == path
        value = models.strict_json_loads(private_read(path))
        assert value["schema_version"] == "guardrails-evolution-local-access-v1"
        assert value["profile"] == self.profile
        origins = [
            value[name]
            for name in (
                "management_url",
                "runtime_url",
                "mock_url",
                "ollama_url",
            )
        ]
        origins.extend(row["gateway_url"] for row in value["tenants"].values())
        for origin in origins:
            parsed = urlsplit(origin)
            assert (
                parsed.scheme == "https"
                and parsed.hostname == "localhost"
                and parsed.username is None
                and parsed.password is None
                and parsed.port is not None
                and 0 < parsed.port < 65536
                and not parsed.path
                and not parsed.query
                and not parsed.fragment
            ), "cluster access must use explicit loopback TLS forwards"
        return value

    def _credentials(self):
        # The production resolver owns journal, pending-state and slot identity
        # validation. Never treat the historical seed inventory as the current
        # credential authority after maintenance has published a generation.
        with self.deployment.lock():
            owner = self.deployment.owner()
            historical = models.strict_json_loads(
                private_read(self.access["seed_inventory_file"])
            )
            return self.deployment.support("credentials").describe_inventory(
                self.state / "assembly", owner, historical
            )

    def assert_ready(self):
        observer = getattr(self, "readiness_observation", None)
        checks = [(self.access["management_url"] + "/health/live", {})]
        for tenant in self.tenants:
            value = self.access["tenants"][tenant]
            checks.extend(
                (
                    (
                        self.access["runtime_url"] + "/health/ready",
                        self.headers(value["workload_token_file"]),
                    ),
                    (
                        value["gateway_url"] + "/health/ready",
                        self.headers(value["gateway_token_file"]),
                    ),
                )
            )
        deadline = time.monotonic() + 30
        for index, (url, headers) in enumerate(checks):
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _observe_readiness(observer, "elapsed", index)
                assert remaining > 0, "actual cluster TLS readiness deadline exceeded"
                _observe_readiness(observer, "begin", index)
                try:
                    response = self.http.get(
                        url, headers=headers, timeout=min(5, remaining)
                    )
                except httpx.TransportError:
                    _observe_readiness(observer, "elapsed", index)
                else:
                    _observe_readiness(observer, "returned", index, response)
                    assert response.status_code in {200, 503}, (
                        "actual cluster readiness rejected authenticated access"
                    )
                    if response.status_code == 200:
                        break
                time.sleep(min(0.2, max(0, deadline - time.monotonic())))
        _observe_readiness(observer, "finished")

    def close(self):
        self.http.close()
        self.repo.close()

    def headers(self, token_file, **extra):
        return {
            "Authorization": "Bearer "
            + private_read(token_file, maximum=4096).decode().strip(),
            "Accept-Encoding": "identity",
            **extra,
        }

    def management(
        self, tenant, method, suffix, *, role="operator", body=None, expected=200
    ):
        actor = self.inventory["tenants"][tenant]["roles"][role]
        headers = self.headers(actor["token_file"])
        if method == "POST":
            headers["Idempotency-Key"] = str(uuid4())
        response = self.http.request(
            method,
            self.access["management_url"] + "/v2/tenants/" + tenant + suffix,
            headers=headers,
            json=body,
        )
        assert response.status_code == expected, (
            "management status differs from the authorized contract"
        )
        return models.strict_json_loads(response.content)

    def release(self, tenant, state):
        value = self.inventory["tenants"][tenant]
        workload = value["workload"]["workload_id"]
        current = self.management(tenant, "GET", "/workloads/" + workload)
        revision = current["desired_binding"]["binding_revision"]
        release = self.management(
            tenant,
            "POST",
            "/releases",
            body={
                "policy": value["policy_ref"],
                "workload_ids": [workload],
                "stage": "canary",
                "state": state,
                "expected_revisions": {workload: revision},
                "reason": "Actual cluster acceptance rollout",
            },
            expected=202,
        )
        deadline = time.monotonic() + 120
        targets = {item["runtime_id"] for item in value["runtimes"].values()}
        while True:
            observed = self.management(
                tenant, "GET", "/releases/" + release["release_id"]
            )
            assert observed["release"]["status"] != "failed", (
                "actual runtime rejected the candidate binding"
            )
            receipts = [
                row
                for row in observed["receipts"]
                if row["binding_revision"] == revision + 1
                and row["digest"] == value["policy_ref"]["digest"]
                and row["health"] == "ready"
            ]
            if (
                observed["release"]["status"] == "applied"
                and {row["runtime_id"] for row in receipts} == targets
            ):
                pointer = self.management(tenant, "GET", "/workloads/" + workload)
                assert pointer["desired_binding"]["binding_revision"] == revision + 1
                assert pointer["applied_binding"]["binding_revision"] == revision + 1
                return revision + 1
            assert time.monotonic() < deadline, (
                "genuine runtime receipt deadline exceeded"
            )
            time.sleep(0.3)

    def mock(self, method, path, body=None):
        response = self.http.request(
            method,
            self.access["mock_url"] + path,
            headers=self.headers(self.access["mock_control_token_file"]),
            json=body,
        )
        assert response.status_code == 200, "mock control channel failed"
        return models.strict_json_loads(response.content)

    def scenario(self, name):
        assert (
            self.mock("PUT", "/proof/scenario", {"scenario": name})["scenario"] == name
        )
        assert self.mock("DELETE", "/proof/calls")["cleared"] is True

    def calls(self):
        return self.mock("GET", "/proof/calls")

    def send(self, tenant, text, *, token_file=None, extra=None):
        access = self.access["tenants"][tenant]
        correlation = str(uuid4())
        response = self.http.post(
            access["gateway_url"] + "/v1/chat/completions",
            headers=self.headers(
                token_file or access["gateway_token_file"],
                **{"X-Request-ID": correlation},
            ),
            json={
                "model": access["model_alias"],
                "messages": [{"role": "user", "content": text}],
                "stream": False,
                "temperature": 0,
                "max_tokens": 128,
                **(extra or {}),
            },
        )
        assert len(response.content) <= 256 * 1024
        return response, correlation

    def sample_complete(self, tenant, text, *, observation=None):
        access = self.access["tenants"][tenant]
        started = time.monotonic()

        async def exercise():
            client = None
            try:
                client = self.sample.TeamClient(
                    gateway=access["gateway_url"],
                    ca_file=self.access["ca_file"],
                    token_file=access["gateway_token_file"],
                    tenant_id=tenant,
                    model=access["model_alias"],
                    profile=self.profile,
                )
                if observation is not None:
                    async def observe(response):
                        await observe_sample_response(response, observation)
                    client.client.event_hooks["response"].append(observe)
                try:
                    result = await client.complete(text)
                except EvolutionError as error:
                    # An opt-in error-body observation can reach the original
                    # deadline after headers arrived. Preserve the sample's
                    # header-known mapping; external cancellation still propagates.
                    mapped = {401: "authentication_failed", 403: "forbidden", 429: "quota_exceeded"}
                    status = observation["http_status"] if observation is not None else 0
                    if error.code == "evaluator_unavailable" and status in mapped:
                        raise EvolutionError(mapped[status]) from None
                    raise
                if observation is not None:
                    observation["sample_completed"] = True
                return result
            finally:
                if client is not None:
                    await client.close()

        try:
            return asyncio.run(exercise())
        finally:
            if observation is not None:
                observation["elapsed_ms"] = round((time.monotonic() - started) * 1000, 3)

    def assert_attribution(
        self, tenant, result, revision, *, input_action, output_action
    ):
        value = self.inventory["tenants"][tenant]
        assert result["policy"] == value["policy_ref"]
        assert result["binding_revision"] == revision
        assert result["snapshot_generation"] >= 1
        assert result["input_action"] == input_action
        assert result["output_action"] == output_action
        assert result["audit_status"] == "persisted"
        decision_ids = {result["input_decision_id"], result["output_decision_id"]}
        assert len(decision_ids) == 2
        with self.repo.transaction(tenant) as tx:
            events = [
                tx.get("audit", identity, required=True) for identity in decision_ids
            ]
        assert {row["metadata"]["stage"] for row in events} == {"input", "output"}
        assert all(row["correlation_id"] == result["correlation_id"] for row in events)
        assert all(
            row["actor_id"] == value["roles"]["workload"]["principal_id"]
            for row in events
        )
        assert all(
            row["tenant_id"] == tenant
            and row["workload_id"] == value["workload"]["workload_id"]
            for row in events
        )
        assert all(
            row["policy_digest"] == value["policy_ref"]["digest"]
            and row["binding_revision"] == revision
            for row in events
        )
        assert all(row["raw_content_retained"] is False for row in events)

    def evaluation_events(self, tenant, correlation):
        with self.repo.transaction(tenant) as tx:
            return [
                row.data
                for row in tx.execute(
                    "SELECT data FROM evolution.records WHERE tenant_id=:tenant AND kind='audit' AND data->>'event_type'='evaluation' AND data->>'correlation_id'=:correlation",
                    {"tenant": tenant, "correlation": correlation},
                )
            ]

    def assert_private(self, canaries):
        # Raw fixtures and returned prose exist only in this test's memory.
        # Failures report booleans, never the matching data or pod log body.
        token_files = {
            self.access["mock_control_token_file"],
            self.access["mock_token_file"],
        }
        # This validated set includes original credentials and every completed
        # renewal generation, including the platform token. Retired credentials
        # remain privacy canaries; selecting active credentials must not hide them.
        token_files.update(self.credentials["token_files"])
        # Gateway ingress tokens are static secrets outside DB credential slots.
        # Preserve their explicit coverage independently of the renewal resolver.
        token_files.update(value["gateway_token_file"]
                           for value in self.inventory["tenants"].values())
        retained_tokens = tuple(
            private_read(path, maximum=4096).strip() for path in token_files
        )
        # A lost one-time response can leave a journaled empty reservation.
        # Its path/digest remains in replay evidence, but it contains no secret
        # bytes and must not become the universally matching empty needle.
        needles = tuple(value.encode() for value in canaries if value) + tuple(
            value for value in retained_tokens if value
        )
        assert needles
        for tenant in ("_platform", *self.tenants):
            with self.repo.transaction(tenant) as tx:
                rows = list(
                    tx.execute(
                        "SELECT data FROM evolution.records WHERE tenant_id=:tenant LIMIT 100001",
                        {"tenant": tenant},
                    )
                )
            assert len(rows) <= 100000, "privacy scan bound exceeded"
            retained = any(
                needle in models.canonical_json_bytes(row.data)
                for row in rows
                for needle in needles
            )
            assert not retained, (
                "raw fixture or credential persisted in scoped metadata"
            )
        bootstrap = load_module(
            "evolution_cluster_deployment", ROOT / "deploy/evolution/bootstrap.py"
        )
        deployment = bootstrap.LocalDeployment(ROOT, state=self.state)
        for namespace in ("gr-platform", "gr-team-a", "gr-team-b", "gr-model"):
            _, raw = deployment.kubectl(
                "get", "pods", "-o", "json", namespace=namespace
            )
            pods = models.strict_json_loads(raw)["items"]
            assert pods, "required namespace contains no actual pods"
            for pod in pods:
                _, log = deployment.kubectl(
                    "logs",
                    pod["metadata"]["name"],
                    "--all-containers=true",
                    "--limit-bytes=4194304",
                    namespace=namespace,
                    timeout=20,
                )
                assert len(log.encode()) < 4194304, "privacy log scan was truncated"
                leaked = any(needle in log.encode() for needle in needles)
                assert not leaked, "raw fixture or credential appeared in service logs"
