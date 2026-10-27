"""Real operator-loader/assembly composition with synthetic private inputs only.

Runs the current repository assembly and loader; no deployment state is written.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import socket
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from guardrails.evolution import evaluator_config
from guardrails.evolution.classifier import ClassifierEndpoint
from guardrails.evolution.classifier_contract import MODEL_ID, WEIGHTS_SHA256
from guardrails.evolution.egress import PinnedHttpsTransport
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.models import Destination, ProviderProfile, with_resource_digest

ROOT = Path(__file__).resolve().parents[4]
SOURCE = ROOT / "deploy/evolution/assembly.py"
raw = SOURCE.read_bytes()
spec = importlib.util.spec_from_file_location("classifier_real_loader_assembly", SOURCE)
a = importlib.util.module_from_spec(spec)
exec(compile(raw, str(SOURCE), "exec"), a.__dict__)


@pytest.fixture(autouse=True)
def forbid_operational_dispatch(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("synthetic composition attempted operational dispatch")
    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    monkeypatch.setattr(subprocess, "Popen", denied)
    monkeypatch.setattr(PinnedHttpsTransport, "request", denied)


@pytest.fixture(scope="module")
def certificate():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "assembly-composition-ca")])
    return (x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(17)
        .not_valid_before(datetime(2026, 1, 1, tzinfo=UTC))
        .not_valid_after(datetime(2030, 1, 1, tzinfo=UTC))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM))


def write(path, value, mode=0o600):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    data = value if isinstance(value, bytes) else a.canonical_json_bytes(value)
    path.write_bytes(data)
    path.chmod(mode)
    return str(path)


class MetadataOnlyDeployment:
    def __init__(self):
        self.calls = []
        self.service = {"metadata": {"name": "classifier", "namespace": "gr-platform",
            "uid": "synthetic-classifier-service", "labels": {"guardrails.evolution/owner": "synthetic-owner"}},
            "spec": {"type": "ClusterIP", "clusterIP": "10.96.0.35",
                "selector": {"guardrails.evolution/component": "classifier"},
                "ports": [{"name": "https", "port": 8443, "targetPort": 8443, "protocol": "TCP"}]}}

    def kubectl(self, *args, namespace, **kwargs):
        assert args == ("get", "service", "classifier", "-o", "json")
        assert namespace == "gr-platform" and kwargs == {}
        self.calls.append(args)
        return 0, json.dumps(self.service)


@pytest.fixture
def inputs(tmp_path, certificate):
    directory = tmp_path / "assembly"
    token = write(directory / "private/classifier.token", b"x" * 40 + b"\n")
    ca = write(directory / "tls/gr-platform/classifier/ca.crt", certificate)
    rows = []
    for n, tenant in enumerate(("team-a", "team-b"), 1):
        destination = with_resource_digest(Destination(
            destination_id=str(UUID(int=n * 2)), tenant_id=tenant, name="classifier",
            created_at="2026-10-12T00:00:00Z", host="classifier.gr-platform.svc.cluster.local",
            port=8443, path_prefix="/v1/classifier", cidrs=("10.96.0.35/32",),
            zone="local-shared", purpose="evaluation", tls_ca_ref="local-classifier-ca"))
        provider = with_resource_digest(ProviderProfile(
            provider_id=str(UUID(int=n * 2 - 1)), tenant_id=tenant,
            created_at="2026-10-12T00:00:00Z", kind="evaluator", vendor="local_classifier",
            model_id=MODEL_ID, artifact_digest=WEIGHTS_SHA256,
            destination_id=destination.destination_id, zone="local-shared",
            credential_ref="shared-classifier-operator-identity"))
        rows.append({"tenant_id": tenant, "provider": provider.model_dump(mode="json"),
            "destination": destination.model_dump(mode="json"), "language_profile": "en",
            "bearer_token_file": token, "ca_file": ca})
    document = {"schema_version": "evolution-evaluator-endpoints-v1", "classifier": rows, "bedrock": []}
    path = Path(write(directory / "operator.json", document))
    return MetadataOnlyDeployment(), directory, path, document


def redigest(row, field, **updates):
    cls = ProviderProfile if field == "provider" else Destination
    value = cls.model_validate_json(a.canonical_json_bytes({**row[field], **updates}))
    row[field] = with_resource_digest(value).model_dump(mode="json")


@pytest.mark.parametrize("ca_mode", [0o600, 0o644], ids=["private-ca", "public-ca-copy"])
def test_real_loader_then_assembly_preserves_governed_resources_and_private_identity(inputs, certificate, ca_mode):
    deployment, directory, path, document = inputs
    if ca_mode == 0o644:
        public = write(directory / "operator-ca.crt", certificate, ca_mode)
        for row in document["classifier"]:
            row["ca_file"] = public
        write(path, document)
    before = path.read_bytes()
    loaded = evaluator_config.load_evaluator_endpoints(path, runtime_zone="local-shared",
        external_processing=False, tenant_ids=("team-a", "team-b"))
    assert set(loaded["classifier_endpoints"]) == {"team-a", "team-b"}
    assert loaded["bedrock_endpoints"] == {}
    for row in document["classifier"]:
        endpoint = loaded["classifier_endpoints"][row["tenant_id"]]
        assert isinstance(endpoint, ClassifierEndpoint)
        assert isinstance(endpoint._transport, PinnedHttpsTransport)
        assert not endpoint.health_snapshot().ready
        assert endpoint.config.bearer_token == "x" * 40
        assert endpoint.config.provider_bytes == a.canonical_json_bytes(row["provider"])
        assert endpoint.config.destination_bytes == a.canonical_json_bytes(row["destination"])
    raw, token, ca, digest = a._classifier_bundle(deployment, directory,
        {"owner_id": "synthetic-owner"}, path)
    assert token == b"x" * 40 + b"\n" and ca == certificate
    assert digest == hashlib.sha256(before).hexdigest() and path.read_bytes() == before
    rewritten = json.loads(raw)
    for original, actual in zip(document["classifier"], rewritten["classifier"], strict=True):
        assert {key: value for key, value in actual.items() if key not in {"ca_file", "bearer_token_file"}} == {
            key: value for key, value in original.items() if key not in {"ca_file", "bearer_token_file"}}
        assert actual["bearer_token_file"] == "/config/classifier.token"
        assert actual["ca_file"] == "/config/ca.crt"
    assert len(deployment.calls) == 1


@pytest.mark.parametrize("fault", ["provider-digest", "destination-digest", "foreign-tenant",
    "unlinked-destination", "model-artifact", "wrong-zone", "broad-cidr", "wrong-language", "short-token"])
def test_real_loader_semantic_refusal_precedes_even_fake_service_observation(inputs, fault):
    deployment, directory, path, document = inputs
    row = document["classifier"][1]
    if fault == "provider-digest":
        row["provider"]["digest"] = "0" * 64
    elif fault == "destination-digest":
        row["destination"]["digest"] = "0" * 64
    elif fault == "foreign-tenant":
        redigest(row, "provider", tenant_id="unrelated-team")
    elif fault == "unlinked-destination":
        redigest(row, "provider", destination_id=str(UUID(int=99)))
    elif fault == "model-artifact":
        redigest(row, "provider", artifact_digest="0" * 64)
    elif fault == "wrong-zone":
        redigest(row, "provider", zone="other-zone")
    elif fault == "broad-cidr":
        redigest(row, "destination", cidrs=["10.96.0.0/24"])
    elif fault == "wrong-language":
        row["language_profile"] = "es"
    else:
        write(directory / "private/classifier.token", b"short")
    write(path, document)
    before = path.read_bytes()
    with pytest.raises(EvolutionError):
        a._classifier_bundle(deployment, directory, {"owner_id": "synthetic-owner"}, path)
    assert deployment.calls == [] and path.read_bytes() == before


@pytest.mark.parametrize("fault", ["wrong-current-ip", "different-valid-ca", "different-valid-token"])
def test_real_constructible_endpoint_still_requires_actual_assembly_binding(inputs, certificate, fault):
    deployment, directory, path, document = inputs
    if fault == "wrong-current-ip":
        for row in document["classifier"]:
            redigest(row, "destination", cidrs=["10.96.0.36/32"])
    elif fault == "different-valid-ca":
        # The operator CA remains real and parseable, but prepared trust differs.
        write(directory / "tls/gr-platform/classifier/ca.crt", certificate + b"\n")
        independent = write(directory / "independent-ca.crt", certificate)
        for row in document["classifier"]:
            row["ca_file"] = independent
    else:
        other = write(directory / "other-token", b"y" * 40 + b"\n")
        for row in document["classifier"]:
            row["bearer_token_file"] = other
    write(path, document)
    with pytest.raises(a.AssemblyError, match="classifier_operator_binding_mismatch"):
        a._classifier_bundle(deployment, directory, {"owner_id": "synthetic-owner"}, path)
    assert len(deployment.calls) == 1


def test_real_loader_and_assembly_reject_redirected_private_config(inputs):
    deployment, directory, path, document = inputs
    original = copy.deepcopy(document)
    alternate = directory / "actual.json"
    os.rename(path, alternate)
    path.symlink_to(alternate)
    with pytest.raises(a.AssemblyError, match="classifier_private_path_invalid"):
        a._classifier_bundle(deployment, directory, {"owner_id": "synthetic-owner"}, path)
    assert deployment.calls == [] and json.loads(alternate.read_bytes()) == original
