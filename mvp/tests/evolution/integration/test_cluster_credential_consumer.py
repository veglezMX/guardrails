"""Cluster consumer wiring through explicit metadata ports; no deployed services."""

import copy
import importlib.util
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[4]
SPEC = importlib.util.spec_from_file_location(
    "cluster_credential_consumer", ROOT / "mvp/tests/evolution/e2e/cluster_support.py"
)
support = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(support)


def private(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value if isinstance(value, bytes) else json.dumps(value).encode())
    path.chmod(0o600)
    return str(path)


def consumer(tmp_path, monkeypatch, profile, *, reject=False, mismatched_access=False):
    events = []
    state = tmp_path / "state"
    historical = {"tenants": {
        tenant: {"roles": {"workload": {"principal_id": tenant + "-principal",
                                       "token_file": private(state / (tenant + "-old"), b"original-private-token")}},
                 "policy_ref": {"digest": tenant + "-unchanged"}}
        for tenant in ("team-a", "team-b")
    }}
    active = copy.deepcopy(historical)
    for tenant, value in active["tenants"].items():
        value["roles"]["workload"]["token_file"] = private(
            state / (tenant + "-new"), b"active-private-token")
    inventory_path = private(state / "assembly/seed/inventory.json", historical)
    before = Path(inventory_path).read_bytes()
    description = {"inventory": active, "generation": "fixed-generation",
                   "sha256": "a" * 64, "token_files": tuple(
                       inv["tenants"][tenant]["roles"]["workload"]["token_file"]
                       for inv in (historical, active) for tenant in historical["tenants"])}
    access = {"schema_version": "guardrails-evolution-local-access-v1", "profile": profile,
              "ca_file": "unused-ca", "database_url_file": private(state / "database", b"unused-dsn"),
              "seed_inventory_file": inventory_path,
              "tenants": {tenant: {"workload_token_file": value["roles"]["workload"]["token_file"]}
                          for tenant, value in active["tenants"].items()}}
    if mismatched_access:
        access["tenants"]["team-a"]["workload_token_file"] = historical["tenants"]["team-a"]["roles"]["workload"]["token_file"]
    private(state / "assembly/access.json", access)
    owner = {"owner_id": "fixed-owner"}

    class RenewalError(RuntimeError):
        pass

    class Deployment:
        def __init__(self, root, *, state):
            self.state = state

        @contextmanager
        def lock(self):
            events.append("lock")
            try:
                yield
            finally:
                events.append("unlock")

        def owner(self):
            assert events[-1] == "lock"
            events.append("owner")
            return owner

        def support(self, name):
            assert name == "credentials"
            return SimpleNamespace(describe_inventory=describe)

    def describe(directory, actual_owner, original):
        assert directory == state / "assembly"
        assert actual_owner is owner
        assert original == historical
        assert events[-1] == "owner"
        events.append("resolve")
        if reject:
            raise RenewalError("credential_renewal_pending")
        return copy.deepcopy(description)

    class Http:
        def __init__(self, **kwargs):
            events.append("http")

        def close(self):
            pass

    monkeypatch.setenv("EVOLUTION_DEPLOYMENT_STATE", str(state))
    monkeypatch.setattr(support, "load_module", lambda name, path: (
        SimpleNamespace(LocalDeployment=Deployment) if path.name == "bootstrap.py" else object()))
    monkeypatch.setattr(support.Cluster, "_access", lambda self: copy.deepcopy(access))
    monkeypatch.setattr(support.Cluster, "assert_ready", lambda self: events.append("ready"))
    monkeypatch.setattr(support.ssl, "create_default_context", lambda **kwargs: object())
    monkeypatch.setattr(support.httpx, "Client", Http)
    monkeypatch.setattr(support, "TenantRepository", lambda url: SimpleNamespace(close=lambda: None))
    return historical, description, inventory_path, before, events, RenewalError


@pytest.mark.parametrize("profile", ("deterministic_mock", "local_model"))
def test_cluster_resolves_current_slots_for_each_profile_without_rewriting_seed(tmp_path, monkeypatch, profile):
    historical, description, path, before, events, _ = consumer(tmp_path, monkeypatch, profile)
    cluster = support.Cluster(profile)
    try:
        assert cluster.inventory == description["inventory"]
        assert cluster.credentials == description
        assert events == ["lock", "owner", "resolve", "unlock", "http", "ready"]
        assert Path(path).read_bytes() == before
        for tenant in cluster.tenants:
            assert cluster.inventory["tenants"][tenant]["policy_ref"] == historical["tenants"][tenant]["policy_ref"]
            assert cluster.inventory["tenants"][tenant]["roles"]["workload"]["principal_id"] == historical["tenants"][tenant]["roles"]["workload"]["principal_id"]
            assert cluster.inventory["tenants"][tenant]["roles"]["workload"]["token_file"] != historical["tenants"][tenant]["roles"]["workload"]["token_file"]
    finally:
        cluster.close()


def test_cluster_pending_generation_refuses_before_any_http_consumer(tmp_path, monkeypatch):
    _, _, _, _, events, failure = consumer(tmp_path, monkeypatch, "deterministic_mock", reject=True)
    with pytest.raises(failure, match="^credential_renewal_pending$"):
        support.Cluster("deterministic_mock")
    assert events == ["lock", "owner", "resolve", "unlock"]


def test_cluster_refuses_access_pointer_from_another_credential_generation(tmp_path, monkeypatch):
    _, _, _, _, events, _ = consumer(tmp_path, monkeypatch, "deterministic_mock", mismatched_access=True)
    with pytest.raises(AssertionError, match="^access workload credential differs"):
        support.Cluster("deterministic_mock")
    assert "http" not in events


@pytest.mark.parametrize("surface", ("metadata", "logs"))
@pytest.mark.parametrize("secret", (
    "original-role", "renewed-role", "original-platform", "renewed-platform", "retired-generation", "gateway-ingress",
))
def test_privacy_scan_retains_original_active_and_retired_credentials(tmp_path, monkeypatch, surface, secret):
    secrets = {name: ("synthetic-private-" + name).encode() for name in (
        "original-role", "renewed-role", "original-platform", "renewed-platform", "retired-generation", "gateway-ingress")}
    paths = {name: private(tmp_path / name, value) for name, value in secrets.items()}
    selected = secrets[secret]
    cluster = object.__new__(support.Cluster)
    cluster.credentials = {"token_files": tuple(path for name, path in paths.items()
                                              if name != "gateway-ingress")}
    cluster.inventory = {"tenants": {
        tenant: {"gateway_token_file": paths["gateway-ingress"]} for tenant in ("team-a", "team-b")}}
    cluster.access = {
        "mock_control_token_file": private(tmp_path / "mock-control", b"synthetic-control"),
        "mock_token_file": private(tmp_path / "mock-token", b"synthetic-mock"),
    }
    cluster.tenants = ("team-a", "team-b")
    cluster.state = tmp_path

    class Repository:
        @contextmanager
        def transaction(self, tenant):
            assert tenant in ("_platform", *cluster.tenants)
            value = {"safe": selected.decode()} if surface == "metadata" else {"safe": True}
            yield SimpleNamespace(execute=lambda *args: [SimpleNamespace(data=value)])

    class Deployment:
        def __init__(self, root, *, state):
            pass

        def kubectl(self, operation, *args, **kwargs):
            if operation == "get":
                return 0, '{"items":[{"metadata":{"name":"synthetic-pod"}}]}'
            assert operation == "logs"
            return 0, selected.decode() if surface == "logs" else "safe"

    cluster.repo = Repository()
    monkeypatch.setattr(support, "load_module", lambda *args: SimpleNamespace(LocalDeployment=Deployment))
    with pytest.raises(AssertionError) as caught:
        cluster.assert_private(())
    expected = ("raw fixture or credential persisted in scoped metadata" if surface == "metadata"
                else "raw fixture or credential appeared in service logs")
    assert str(caught.value) == expected
    assert selected.decode() not in str(caught.value)


def clean_privacy_check(tmp_path, monkeypatch, *, empty_reservation=False, platform_leak=False):
    cluster = object.__new__(support.Cluster)
    cluster.credentials = {"token_files": (
        private(tmp_path / "old", b"private-old-token"),
        private(tmp_path / "new", b"private-new-token"),
    )}
    if empty_reservation:
        reservation = private(tmp_path / "lost-response-reservation", b"")
        cluster.credentials["token_files"] += (reservation,)
    if platform_leak:
        cluster.credentials["token_files"] += (
            private(tmp_path / "renewed-platform", b"synthetic-renewed-platform-secret"),
        )
    cluster.inventory = {"tenants": {
        tenant: {"gateway_token_file": private(tmp_path / (tenant + "-gateway"), b"private-gateway-token")}
        for tenant in ("team-a", "team-b")}}
    cluster.access = {
        "mock_control_token_file": private(tmp_path / "control", b"private-control-token"),
        "mock_token_file": private(tmp_path / "mock", b"private-mock-token"),
    }
    cluster.tenants = ("team-a", "team-b")
    cluster.state = tmp_path
    scopes, namespaces = [], []

    class Repository:
        @contextmanager
        def transaction(self, tenant):
            scopes.append(tenant)
            value = ({"credential": "synthetic-renewed-platform-secret"}
                     if platform_leak and tenant == "_platform" else {"safe": True})
            yield SimpleNamespace(execute=lambda *args: [SimpleNamespace(data=value)])

    class Deployment:
        def __init__(self, root, *, state):
            pass

        def kubectl(self, operation, *args, **kwargs):
            if operation == "get":
                return 0, '{"items":[{"metadata":{"name":"synthetic-pod"}}]}'
            assert operation == "logs"
            namespaces.append(kwargs["namespace"])
            return 0, "safe metadata only"

    cluster.repo = Repository()
    monkeypatch.setattr(support, "load_module", lambda *args: SimpleNamespace(LocalDeployment=Deployment))
    cluster.assert_private(())
    assert scopes == ["_platform", "team-a", "team-b"]
    assert namespaces == ["gr-platform", "gr-team-a", "gr-team-b", "gr-model"]
    if empty_reservation:
        assert reservation in cluster.credentials["token_files"]
        assert Path(reservation).read_bytes() == b""


def test_privacy_scan_checks_all_scopes_and_namespaces_when_retention_is_clean(tmp_path, monkeypatch):
    clean_privacy_check(tmp_path, monkeypatch)


def test_privacy_scan_ignores_empty_orphan_reservation_without_removing_history(tmp_path, monkeypatch):
    clean_privacy_check(tmp_path, monkeypatch, empty_reservation=True)


def test_privacy_scan_rejects_renewed_secret_in_platform_metadata(tmp_path, monkeypatch):
    with pytest.raises(AssertionError) as caught:
        clean_privacy_check(tmp_path, monkeypatch, platform_leak=True)
    assert str(caught.value) == "raw fixture or credential persisted in scoped metadata"
