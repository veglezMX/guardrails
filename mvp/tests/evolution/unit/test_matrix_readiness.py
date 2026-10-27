"""Original constructor/readiness proofs; session-staged and not yet executed."""

import json
import sys
from types import SimpleNamespace

import httpx
import pytest
from evolution.measurements import (
    FILES,
    MATRIX_READINESS_ROLES,
    MATRIX_SCHEMA_V2,
    validate_matrix_observation,
)
from evolution.registry import EvidenceError
from test_matrix_observation import CANARY, CASE, evidence, sample, wrapper  # noqa: F401


class BodyForbidden:
    def __init__(self, status):
        self.status_code = status

    @property
    def content(self):
        raise AssertionError("readiness observer must not read body")

    @property
    def text(self):
        raise AssertionError("readiness observer must not read text")

    def json(self):
        raise AssertionError("readiness observer must not parse body")


@pytest.fixture
def ports(wrapper, tmp_path, monkeypatch):  # noqa: F811 -- Pytest injects the imported wrapper fixture.
    # Keep the actual Cluster constructor and assert_ready; replace only external
    # filesystem/deployment/HTTP/DB ports with task-local, deterministic fixtures.
    support = sys.modules[wrapper.Cluster.__module__]
    state = tmp_path / "state"
    assembly = state / "assembly"
    assembly.mkdir(parents=True, mode=0o700)
    (assembly / "access.json").write_text(json.dumps({
        "schema_version": "guardrails-evolution-local-access-v1", "profile": "deterministic_mock"}))
    (assembly / "access.json").chmod(0o600)
    secret = tmp_path / "private-token"
    secret.write_text(CANARY)
    secret.chmod(0o600)
    database = tmp_path / "private-db-url"
    database.write_text("postgresql://unused-fixture")
    database.chmod(0o600)
    access = {"ca_file": "unused-ca", "database_url_file": str(database),
              "management_url": "https://management.private.test",
              "runtime_url": "https://runtime.private.test", "tenants": {}}
    inventory = {"tenants": {}}
    for side in ("a", "b"):
        tenant = "private-tenant-" + side
        access["tenants"][tenant] = {"workload_token_file": str(secret),
            "gateway_token_file": str(secret), "gateway_url": "https://gateway-" + side + ".private.test"}
        inventory["tenants"][tenant] = {"roles": {"workload": {"token_file": str(secret)}}}
    clock = SimpleNamespace(now=100.0, sleeps=[])
    def sleep(seconds):
        clock.sleeps.append(seconds)
        clock.now += seconds
    control = SimpleNamespace(script=[], fallback=200, duration=0.1, calls=[], clients=[], repos=[], clock=clock, close_error=None)
    class Client:
        def __init__(self, **kwargs):
            self.closed = 0
            control.clients.append(self)
        def get(self, url, *, headers, timeout):
            control.calls.append((url, dict(headers), timeout))
            clock.now += min(control.duration, timeout)
            result = control.script.pop(0) if control.script else control.fallback
            if isinstance(result, BaseException):
                raise result
            return BodyForbidden(result)
        def close(self):
            self.closed += 1
            if control.close_error is not None:
                raise control.close_error
    def repository(url):
        value = SimpleNamespace(close=lambda: None)
        control.repos.append(value)
        return value
    bootstrap = SimpleNamespace(LocalDeployment=lambda *_a, **_k: object())
    monkeypatch.setenv("EVOLUTION_DEPLOYMENT_STATE", str(state))
    monkeypatch.setattr(support, "load_module", lambda name, path: bootstrap if name == "evolution_cluster_deployment" else object())
    monkeypatch.setattr(support.Cluster, "_access", lambda self: access)
    monkeypatch.setattr(support.Cluster, "_credentials", lambda self: {"inventory": inventory})
    monkeypatch.setattr(support.Cluster, "platform", lambda self: pytest.fail("no profile replay expected"))
    monkeypatch.setattr(support.httpx, "Client", Client)
    monkeypatch.setattr(support.ssl, "create_default_context", lambda **_kwargs: object())
    monkeypatch.setattr(support, "TenantRepository", repository)
    monkeypatch.setattr(support, "time", SimpleNamespace(monotonic=lambda: clock.now, sleep=sleep))
    control.support = support
    return control


@pytest.mark.parametrize("failed_index,status", [(0, 401), (1, 403), (2, 429), (3, 500), (4, 504)])
def test_real_constructor_failure_records_original_ready_status_before_any_sample(wrapper, ports, tmp_path, monkeypatch, failed_index, status):  # noqa: F811 -- Pytest injects the imported wrapper fixture.
    directory, frozen = evidence(wrapper, tmp_path, monkeypatch)
    ports.script = [200] * failed_index + [status]
    with pytest.raises(AssertionError, match="actual cluster readiness rejected authenticated access"):
        wrapper.test_deployed_input_output_state_matrix_exact_dispatch_and_attribution("observe", 0)
    value = json.loads((directory / FILES[CASE]).read_text())
    assert value["schema_version"] == MATRIX_SCHEMA_V2
    assert value["actual_attempts"] == value["completed"] == 0 and value["rows"] == []
    observed = value["readiness"]
    assert observed["state"] == "partial" and len(observed["rows"]) == failed_index + 1
    assert [(row["endpoint_role"], row["tenant_side"]) for row in observed["rows"]] == list(MATRIX_READINESS_ROLES[:failed_index + 1])
    assert [row["http_status"] for row in observed["rows"]] == [200] * failed_index + [status]
    assert all(row["attempts"] == 1 and row["elapsed_ms"] == 100.0 for row in observed["rows"])
    assert len(ports.calls) == failed_index + 1 and ports.clients[0].closed == 1
    assert ports.repos == []  # Constructor stopped before its repository port.
    assert CANARY not in json.dumps(value) and "private.test" not in json.dumps(value)
    validate_matrix_observation(value, CASE, root=wrapper.ROOT, sources=frozen)


def test_observation_preserves_original_ready_get_order_headers_polling_and_timeouts(wrapper, ports):  # noqa: F811 -- Pytest injects the imported wrapper fixture.
    def execute(observed):
        ports.clock.now = 100.0
        ports.clock.sleeps.clear()
        ports.calls.clear()
        ports.script = [503, httpx.ConnectError(CANARY), 200, 200, 503, 200, 200, 200]
        observer = wrapper.ReadinessObservation() if observed else None
        cluster = wrapper.Cluster("deterministic_mock", readiness_observation=observer)
        cluster.close()
        return list(ports.calls), list(ports.clock.sleeps), observer
    plain, plain_sleeps, _ = execute(False)
    recorded, recorded_sleeps, observer = execute(True)
    assert recorded == plain and recorded_sleeps == plain_sleeps == [0.2, 0.2, 0.2]
    assert [call[2] for call in recorded] == [5] * 8
    assert [call[0] for call in recorded] == [
        "https://management.private.test/health/live"] * 3 + [
        "https://runtime.private.test/health/ready",
        "https://gateway-a.private.test/health/ready",
        "https://gateway-a.private.test/health/ready",
        "https://runtime.private.test/health/ready",
        "https://gateway-b.private.test/health/ready"]
    value = wrapper.readiness_snapshot(observer)
    assert value["state"] == "complete"
    assert [row["attempts"] for row in value["rows"]] == [3, 1, 2, 1, 1]
    assert all(row["http_status"] == 200 for row in value["rows"])
    assert CANARY not in json.dumps(value) and "private.test" not in json.dumps(value)


def test_observation_keeps_shared_thirty_second_deadline_and_remaining_get_timeout(wrapper, ports):  # noqa: F811 -- Pytest injects the imported wrapper fixture.
    def execute(observed):
        ports.clock.now = 100.0
        ports.clock.sleeps.clear()
        ports.calls.clear()
        ports.fallback, ports.duration = 503, 4.9
        observer = wrapper.ReadinessObservation() if observed else None
        with pytest.raises(AssertionError, match="actual cluster TLS readiness deadline exceeded"):
            wrapper.Cluster("deterministic_mock", readiness_observation=observer)
        return list(ports.calls), list(ports.clock.sleeps), observer, ports.clock.now
    plain, sleeps, _, end = execute(False)
    recorded, recorded_sleeps, observer, recorded_end = execute(True)
    assert recorded == plain and recorded_sleeps == sleeps
    assert end == recorded_end == 130.0
    assert all(0 < call[2] <= 5 for call in recorded) and recorded[-1][2] < 5
    value = wrapper.readiness_snapshot(observer)
    assert value["state"] == "partial" and len(value["rows"]) == 1
    assert value["rows"][0]["http_status"] == 503
    assert value["rows"][0]["elapsed_ms"] == 30000.0
    assert value["rows"][0]["attempts"] == len(recorded)


@pytest.mark.parametrize("event", ["begin", "returned", "finished"])
def test_recorder_failures_preserve_original_ready_success_but_cannot_support_pass(wrapper, ports, tmp_path, monkeypatch, event):  # noqa: F811 -- Pytest injects the imported wrapper fixture.
    observer = wrapper.ReadinessObservation()
    def failed(*args):
        raise RuntimeError(CANARY)
    monkeypatch.setattr(observer, event, failed)
    cluster = wrapper.Cluster("deterministic_mock", readiness_observation=observer)
    cluster.close()
    assert len(ports.calls) == 5 and len(ports.repos) == 1
    ready = wrapper.readiness_snapshot(observer)
    assert ready == {"state": "unavailable", "rows": []}
    _, frozen = evidence(wrapper, tmp_path, monkeypatch)
    value = sample(completed=True)
    value.update(schema_version=MATRIX_SCHEMA_V2, readiness=ready, actual_attempts=2, completed=2)
    value["rows"].append({**value["rows"][0], "call_ordinal": 1})
    with pytest.raises(EvidenceError):
        validate_matrix_observation(value, CASE, root=wrapper.ROOT, sources=frozen, complete=True)
    assert CANARY not in json.dumps(value)


def test_original_constructor_exception_survives_observation_publication_failure(wrapper, ports, tmp_path, monkeypatch):  # noqa: F811 -- Pytest injects the imported wrapper fixture.
    directory, _ = evidence(wrapper, tmp_path, monkeypatch)
    class PrivateFailure(RuntimeError):
        def __str__(self):
            raise AssertionError("original message must not be read")
    original = PrivateFailure(CANARY)
    ports.script = [original]
    def broken_record(*args):
        raise ValueError("secondary-" + CANARY)
    monkeypatch.setattr(wrapper, "_record_matrix", broken_record)
    with pytest.raises(PrivateFailure) as caught:
        wrapper.test_deployed_input_output_state_matrix_exact_dispatch_and_attribution("observe", 0)
    assert caught.value is original
    assert len(ports.calls) == 1 and ports.clients[0].closed == 1 and ports.repos == []
    assert list(directory.iterdir()) == []


def test_original_constructor_readiness_failure_survives_client_close_failure(wrapper, ports, tmp_path, monkeypatch):  # noqa: F811 -- Pytest injects the imported wrapper fixture.
    directory, frozen = evidence(wrapper, tmp_path, monkeypatch)
    ports.script = [403]
    ports.close_error = RuntimeError(CANARY)
    with pytest.raises(AssertionError, match="actual cluster readiness rejected authenticated access"):
        wrapper.test_deployed_input_output_state_matrix_exact_dispatch_and_attribution("observe", 0)
    value = json.loads((directory / FILES[CASE]).read_text())
    assert value["actual_attempts"] == 0 and value["rows"] == []
    assert value["readiness"] == {"state": "partial", "rows": [{
        "endpoint_role": "management", "tenant_side": None, "attempts": 1,
        "http_status": 403, "elapsed_ms": 100.0}]}
    assert len(ports.calls) == 1 and ports.clients[0].closed == 1 and ports.repos == []
    assert CANARY not in json.dumps(value)
    validate_matrix_observation(value, CASE, root=wrapper.ROOT, sources=frozen)
