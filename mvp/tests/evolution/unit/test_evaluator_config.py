"""Synthetic private operator files, actual endpoint/transport construction, no network."""
import asyncio
import json
import os
import socket
import time
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from guardrails.evolution import egress
from guardrails.evolution import evaluator_config as subject
from guardrails.evolution.classifier import ClassifierEndpoint
from guardrails.evolution.classifier_contract import MODEL_ID, WEIGHTS_SHA256
from guardrails.evolution.egress import AuthorizedRoute, PinnedHttpsTransport
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.managed_endpoint import BedrockEndpoint
from guardrails.evolution.models import (
    Destination,
    ProviderProfile,
    with_resource_digest,
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("operator loader attempted network access")
    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)


@pytest.fixture(scope="module")
def certificate():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "synthetic-loader-ca")])
    return (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(1).not_valid_before(datetime(2026, 1, 1, tzinfo=UTC))
        .not_valid_after(datetime(2030, 1, 1, tzinfo=UTC))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM))


def write(path, value, mode=0o600):
    raw = value if isinstance(value, bytes) else json.dumps(value).encode()
    path.write_bytes(raw)
    path.chmod(mode)
    return str(path)


def resource(cls, **values):
    return with_resource_digest(cls(tenant_id="team-a", created_at="2026-10-12T00:00:00Z", **values)).model_dump(mode="json")


def change(row, key, **values):
    cls = ProviderProfile if key == "provider" else Destination
    row[key] = with_resource_digest(cls.model_validate(row[key]).model_copy(update=values)).model_dump(mode="json")


@pytest.fixture
def inputs(tmp_path, certificate):
    provider = resource(ProviderProfile, provider_id=str(UUID(int=1)), kind="evaluator",
        vendor="local_classifier", model_id=MODEL_ID, artifact_digest=WEIGHTS_SHA256,
        destination_id=str(UUID(int=2)), zone="local-shared", credential_ref="classifier-auth")
    destination = resource(Destination, destination_id=str(UUID(int=2)), name="classifier",
        host="classifier.internal", port=8443, path_prefix="/v1/classifier",
        cidrs=("10.42.0.5/32",), zone="local-shared", purpose="evaluation", tls_ca_ref="classifier-ca")
    row = {"tenant_id": "team-a", "provider": provider, "destination": destination,
           "language_profile": "en", "bearer_token_file": write(tmp_path / "bearer", b"x" * 40 + b"\n"),
           "ca_file": write(tmp_path / "ca.crt", certificate, 0o644)}
    managed_provider = resource(ProviderProfile, provider_id=str(UUID(int=3)), kind="evaluator",
        vendor="bedrock", model_id="synthetic123", destination_id=str(UUID(int=4)),
        zone="us-east-1", credential_ref="bedrock-auth")
    managed_destination = resource(Destination, destination_id=str(UUID(int=4)), name="bedrock",
        host="bedrock-runtime.us-east-1.amazonaws.com", path_prefix="/guardrail/synthetic123",
        cidrs=("54.0.0.0/8",), zone="us-east-1", purpose="evaluation", tls_ca_ref="managed-ca")
    credentials = {"credential_ref": "bedrock-auth", "access_key_id": "AKIAABCDEFGHIJKLMNOP",
        "secret_access_key": "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY", "session_token": None, "expires_at": None}
    managed = {"tenant_id": "team-a", "provider": managed_provider, "destination": managed_destination,
        "region": "us-east-1", "credentials_file": write(tmp_path / "credentials.json", credentials),
        "ca_file": row["ca_file"], "enabled": False}
    document = {"schema_version": subject.SCHEMA_VERSION, "classifier": [row], "bedrock": [managed]}
    return tmp_path / "endpoints.json", document


def load(inputs, **options):
    path, document = inputs
    write(path, document)
    return subject.load_evaluator_endpoints(path, runtime_zone="local-shared", **options)


def test_constructs_exact_actual_endpoints_without_health_or_network(inputs):
    result = load(inputs, tenant_ids=("team-a",))
    classifier = result["classifier_endpoints"]["team-a"]
    managed = result["bedrock_endpoints"]["team-a"]
    assert isinstance(classifier, ClassifierEndpoint) and isinstance(managed, BedrockEndpoint)
    assert not classifier.health_snapshot().ready and managed.enabled is False
    assert all(row.status == "disabled" for row in managed.capabilities())
    transport = classifier._transport
    assert type(transport) is PinnedHttpsTransport
    assert transport.ca_files == {}
    assert transport.ca_pem == {"classifier-ca": Path(inputs[1]["classifier"][0]["ca_file"]).read_bytes()}
    assert transport.local_services == {str(UUID(int=2)): ("classifier.internal", ("10.42.0.5",))}
    assert classifier.config.bearer_token == "x" * 40
    assert classifier.config.route().provider.model_id == MODEL_ID
    assert "x" * 40 not in repr(classifier.config)
    inputs[1]["classifier"][0]["provider"]["model_id"] = "changed-after-load"
    assert classifier.config.route().provider.model_id == MODEL_ID


@pytest.mark.parametrize("engine", ["classifier", "bedrock"])
@pytest.mark.parametrize("mutation", ["overwrite", "unlink", "symlink"])
def test_actual_tls_context_keeps_verified_ca_after_source_path_changes(inputs, certificate, monkeypatch, engine, mutation):
    result = load(inputs)
    endpoint = result[engine + "_endpoints"]["team-a"]
    ca_path = Path(inputs[1][engine][0]["ca_file"])
    if mutation == "overwrite":
        ca_path.write_bytes(b"replaced private trust input")
    elif mutation == "unlink":
        ca_path.unlink()
    else:
        other = ca_path.with_name("other-ca")
        other.write_bytes(b"foreign trust bytes")
        ca_path.unlink()
        ca_path.symlink_to(other)
    observed = []
    class ContextReached(Exception):
        pass
    def no_dispatch(**options):
        observed.append(options["ssl_context"].get_ca_certs(binary_form=True))
        assert options["proxy"] is None and options["retries"] == 0
        raise ContextReached
    monkeypatch.setattr(egress.httpcore, "AsyncConnectionPool", no_dispatch)
    if engine == "classifier":
        transport, route, path = endpoint._transport, endpoint.config.route(), "/v1/classifier/ready"
    else:
        provider, destination = endpoint.resources()
        transport = endpoint.transport
        route = AuthorizedRoute(provider, destination, external_processing=True)
        path = "/guardrail/synthetic123/version/7/apply"
    with pytest.raises(TypeError):
        transport.ca_pem[route.destination.tls_ca_ref] = b"replacement"
    with pytest.raises(ContextReached):
        asyncio.run(transport.request(route, method="GET", path=path, body=b"",
            deadline_monotonic=time.monotonic() + 1))
    assert observed == [[x509.load_pem_x509_certificate(certificate).public_bytes(serialization.Encoding.DER)]]


def test_empty_explicit_config_disables_both_engines(inputs, monkeypatch):
    inputs[1]["classifier"] = []
    inputs[1]["bedrock"] = []
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "do-not-use")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", "/missing/ambient")
    assert load(inputs) == {"classifier_endpoints": {}, "bedrock_endpoints": {}}


@pytest.mark.parametrize("enabled,external", [(False, False), (False, True), (True, True)])
def test_managed_enablement_is_explicit(inputs, enabled, external):
    inputs[1]["bedrock"][0]["enabled"] = enabled
    endpoint = load(inputs, external_processing=external)["bedrock_endpoints"]["team-a"]
    assert endpoint.enabled is enabled


@pytest.mark.parametrize("raw", [b"[]", b"null", b"{}", b"{", b"\xff",
    b'{"classifier":[],"classifier":[]}', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e309}'])
def test_json_failures_are_finite_and_private(inputs, raw):
    path, _ = inputs
    write(path, raw)
    with pytest.raises(EvolutionError) as error:
        subject.load_evaluator_endpoints(path, runtime_zone="local-shared")
    assert error.value.code == "validation_failed" and str(path) not in str(error.value)


@pytest.mark.parametrize("case", ["version", "unknown-top", "missing-array", "wrong-array", "unknown-row",
    "missing-row", "duplicate", "five", "tenant", "provider-owner", "destination-owner", "digest",
    "model", "weights", "vendor", "zone", "destination-id", "language", "path", "broad", "duplicate-ip", "public-ip"])
def test_closed_classifier_binding_refuses(inputs, case):
    document = inputs[1]
    row = document["classifier"][0]
    if case == "version": document["schema_version"] = "other"
    elif case == "unknown-top": document["ambient"] = True
    elif case == "missing-array": del document["bedrock"]
    elif case == "wrong-array": document["classifier"] = {}
    elif case == "unknown-row": row["policy"] = {}
    elif case == "missing-row": del row["language_profile"]
    elif case == "duplicate": document["classifier"] *= 2
    elif case == "five": document["classifier"] *= 5
    elif case == "tenant": row["tenant_id"] = "foreign"
    elif case == "provider-owner": change(row, "provider", tenant_id="foreign")
    elif case == "destination-owner": change(row, "destination", tenant_id="foreign")
    elif case == "digest": row["provider"]["digest"] = "0" * 64
    elif case == "model": change(row, "provider", model_id="other")
    elif case == "weights": change(row, "provider", artifact_digest="0" * 64)
    elif case == "vendor": change(row, "provider", vendor="bedrock")
    elif case == "zone": change(row, "provider", zone="foreign")
    elif case == "destination-id": change(row, "provider", destination_id=str(UUID(int=99)))
    elif case == "language": row["language_profile"] = "fr"
    elif case == "path": change(row, "destination", path_prefix="/v1/classifier/evaluate")
    elif case == "broad": change(row, "destination", cidrs=("10.42.0.0/24",))
    elif case == "duplicate-ip": change(row, "destination", cidrs=("10.42.0.5/32",) * 2)
    elif case == "public-ip": change(row, "destination", cidrs=("8.8.8.8/32",))
    with pytest.raises(EvolutionError): load(inputs, tenant_ids=("team-a",))


@pytest.mark.parametrize("cidr", ["10.42.0.5/32", "127.0.0.1/32", "fd00::1/128", "::1/128"])
def test_exact_private_and_loopback_registration_uses_original_transport_policy(inputs, cidr):
    change(inputs[1]["classifier"][0], "destination", cidrs=(cidr,))
    assert load(inputs)["classifier_endpoints"]["team-a"].config.route().destination.cidrs == (cidr,)


@pytest.mark.parametrize("case", ["external", "bool", "region", "host", "port", "owner", "zone", "vendor", "duplicate"])
def test_managed_route_refuses_before_dispatch(inputs, case):
    row = inputs[1]["bedrock"][0]
    if case == "external": row["enabled"] = True
    elif case == "bool": row["enabled"] = 1
    elif case == "region": row["region"] = "us-west-2"
    elif case == "host": change(row, "destination", host="other.amazonaws.com")
    elif case == "port": change(row, "destination", port=444)
    elif case == "owner": change(row, "destination", tenant_id="foreign")
    elif case == "zone": change(row, "provider", zone="us-west-2")
    elif case == "vendor": change(row, "provider", vendor="ollama")
    elif case == "duplicate": inputs[1]["bedrock"] *= 2
    with pytest.raises(EvolutionError): load(inputs)


@pytest.mark.parametrize("case", ["expired", "nonutc", "naive", "invalid-date", "notstring", "temporary-missing",
    "temporary-missing-token", "extra", "reference", "secret-type", "secret-bad", "unknown-key"])
def test_credentials_are_closed_current_explicit_snapshots(inputs, case):
    path = Path(inputs[1]["bedrock"][0]["credentials_file"])
    value = json.loads(path.read_bytes())
    if case == "expired": value["expires_at"] = "2000-01-01T00:00:00Z"
    elif case == "nonutc": value["expires_at"] = "2099-01-01T00:00:00+01:00"
    elif case == "naive": value["expires_at"] = "2099-01-01T00:00:00"
    elif case == "invalid-date": value["expires_at"] = "2099-99-01T00:00:00Z"
    elif case == "notstring": value["expires_at"] = 1
    elif case == "temporary-missing": value["access_key_id"] = "ASIAABCDEFGHIJKLMNOP"
    elif case == "temporary-missing-token": value.update(access_key_id="ASIAABCDEFGHIJKLMNOP", expires_at="2099-01-01T00:00:00Z")
    elif case == "extra": value["ambient_profile"] = "default"
    elif case == "reference": value["credential_ref"] = "different"
    elif case == "secret-type": value["secret_access_key"] = 5
    elif case == "secret-bad": value["secret_access_key"] = "private-invalid-content"
    elif case == "unknown-key": value["access_key_id"] = "other"
    write(path, value)
    with pytest.raises(EvolutionError) as error: load(inputs)
    assert "private-invalid-content" not in str(error.value) and str(path) not in str(error.value)


def test_current_temporary_credentials_are_frozen_and_shared_reference_is_allowed(inputs):
    path = Path(inputs[1]["bedrock"][0]["credentials_file"])
    value = json.loads(path.read_bytes())
    value.update(access_key_id="ASIAABCDEFGHIJKLMNOP", session_token="synthetic-token",
                 expires_at=(datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    write(path, value)
    endpoint = load(inputs)["bedrock_endpoints"]["team-a"]
    assert endpoint.credentials.expires_at.utcoffset().total_seconds() == 0
    assert "synthetic-token" not in repr(endpoint.credentials)


@pytest.mark.parametrize("path_kind", ["config", "token", "credentials", "ca"])
@pytest.mark.parametrize("damage", ["symlink", "hardlink", "unsafe-mode", "empty", "too-large", "directory", "fifo"])
def test_unsafe_input_files_refuse_without_blocking(inputs, path_kind, damage):
    config, document = inputs
    write(config, document)
    paths = {"config": config, "token": Path(document["classifier"][0]["bearer_token_file"]),
             "credentials": Path(document["bedrock"][0]["credentials_file"]), "ca": Path(document["classifier"][0]["ca_file"])}
    path = paths[path_kind]
    if damage in {"symlink", "hardlink"}:
        saved = path.with_name(path.name + ".saved")
        path.rename(saved)
        if damage == "symlink": path.symlink_to(saved)
        else: os.link(saved, path)
    elif damage == "unsafe-mode": path.chmod(0o666)
    elif damage == "empty": path.write_bytes(b"")
    elif damage == "too-large": path.write_bytes(b"x" * (subject.MAX_CONFIG_BYTES + 1))
    elif damage == "directory": path.unlink(); path.mkdir()
    elif damage == "fifo": path.unlink(); os.mkfifo(path, 0o600)
    with pytest.raises(EvolutionError):
        subject.load_evaluator_endpoints(config, runtime_zone="local-shared")


def test_symlinked_directory_ancestor_and_relative_config_refuse(inputs):
    config, document = inputs
    write(config, document)
    alias = config.parent.with_name(config.parent.name + "-alias")
    alias.symlink_to(config.parent, target_is_directory=True)
    with pytest.raises(EvolutionError): subject.load_evaluator_endpoints(alias / config.name, runtime_zone="local-shared")
    with pytest.raises(EvolutionError): subject.load_evaluator_endpoints(config.name, runtime_zone="local-shared")


@pytest.mark.parametrize("raw", [b"x" * 31, b"x" * 4097, b"x" * 40 + b"\n\n", b"x" * 40 + b"\r\n", b"x" * 39 + b" ", b"\xff" * 40])
def test_bearer_is_bounded_ascii_with_one_optional_lf(inputs, raw):
    write(Path(inputs[1]["classifier"][0]["bearer_token_file"]), raw)
    with pytest.raises(EvolutionError): load(inputs)


def test_bad_ca_fails_locally_without_default_trust(inputs):
    write(Path(inputs[1]["classifier"][0]["ca_file"]), b"private-not-a-certificate")
    with pytest.raises(EvolutionError) as error: load(inputs)
    assert "private-not-a-certificate" not in str(error.value)


def test_shared_classifier_secret_accepts_distinct_exact_tenant_resources(inputs):
    original = inputs[1]["classifier"][0]
    second = deepcopy(original)
    second["tenant_id"] = "team-b"
    change(second, "provider", tenant_id="team-b", provider_id=str(UUID(int=11)), destination_id=str(UUID(int=12)))
    change(second, "destination", tenant_id="team-b", destination_id=str(UUID(int=12)))
    inputs[1]["classifier"].append(second)
    endpoints = load(inputs, tenant_ids=("team-a", "team-b"))["classifier_endpoints"]
    assert set(endpoints) == {"team-a", "team-b"}
    assert endpoints["team-a"].config.binding_key != endpoints["team-b"].config.binding_key


def test_conflicting_resource_and_credential_references_refuse(inputs):
    original = inputs[1]["classifier"][0]
    second = deepcopy(original)
    second["tenant_id"] = "team-b"
    change(second, "provider", tenant_id="team-b")
    change(second, "destination", tenant_id="team-b")
    inputs[1]["classifier"].append(second)
    with pytest.raises(EvolutionError): load(inputs)
    change(second, "provider", provider_id=str(UUID(int=11)), destination_id=str(UUID(int=12)))
    change(second, "destination", destination_id=str(UUID(int=12)))
    second["bearer_token_file"] = write(inputs[0].parent / "other-token", b"y" * 40)
    with pytest.raises(EvolutionError): load(inputs)


def test_file_change_during_endpoint_construction_refuses_entire_snapshot(inputs, monkeypatch):
    original = subject.ClassifierEndpoint
    def changed(config, transport):
        Path(inputs[1]["classifier"][0]["bearer_token_file"]).write_bytes(b"y" * 40)
        return original(config, transport)
    monkeypatch.setattr(subject, "ClassifierEndpoint", changed)
    with pytest.raises(EvolutionError): load(inputs)


def test_inode_replacement_between_lstat_and_open_is_refused(inputs, monkeypatch):
    original = subject.os.open
    target = Path(inputs[1]["classifier"][0]["bearer_token_file"])
    def replaced(path, flags, *args, **kwargs):
        if Path(path) == target:
            saved = target.with_name("replacement")
            write(saved, target.read_bytes())
            os.replace(saved, target)
        return original(path, flags, *args, **kwargs)
    monkeypatch.setattr(subject.os, "open", replaced)
    with pytest.raises(EvolutionError): load(inputs)


def test_fifo_substitution_cannot_block_safe_reader(inputs, monkeypatch):
    original = subject.os.open
    target = Path(inputs[1]["classifier"][0]["bearer_token_file"])
    def replaced(path, flags, *args, **kwargs):
        if Path(path) == target:
            target.unlink()
            os.mkfifo(target, 0o600)
            assert flags & os.O_NONBLOCK
        return original(path, flags, *args, **kwargs)
    monkeypatch.setattr(subject.os, "open", replaced)
    with pytest.raises(EvolutionError): load(inputs)


def test_owned_file_requirement_is_not_only_a_mode_check(inputs, monkeypatch):
    owner = os.getuid()
    monkeypatch.setattr(subject.os, "getuid", lambda: owner + 1)
    with pytest.raises(EvolutionError): load(inputs)


def test_credential_expiring_during_snapshot_load_is_not_returned(inputs, monkeypatch):
    from types import SimpleNamespace
    path = Path(inputs[1]["bedrock"][0]["credentials_file"])
    value = json.loads(path.read_bytes())
    now = datetime(2026, 10, 12, tzinfo=UTC)
    value["expires_at"] = "2026-10-12T00:00:01Z"
    write(path, value)
    calls = iter((now, now + timedelta(seconds=2)))
    monkeypatch.setattr(subject, "datetime", SimpleNamespace(
        now=lambda _zone: next(calls), fromisoformat=datetime.fromisoformat))
    with pytest.raises(EvolutionError): load(inputs)


@pytest.mark.parametrize("options", [{"runtime_zone": ""}, {"runtime_zone": 1},
    {"external_processing": 1}, {"tenant_ids": "team-a"}, {"tenant_ids": ("team-a", "team-a")},
    {"tenant_ids": ("_platform",)}, {"tenant_ids": tuple("abcde")}, {"tenant_ids": ()}])
def test_caller_constraints_are_strict(inputs, options):
    path, document = inputs
    write(path, document)
    with pytest.raises(EvolutionError):
        subject.load_evaluator_endpoints(path, **({"runtime_zone": "local-shared"} | options))
