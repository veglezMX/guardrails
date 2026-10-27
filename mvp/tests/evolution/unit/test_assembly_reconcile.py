"""Replay safety through private files and a bounded Kubernetes metadata port.

The port simulates Kubernetes responses; actual OCI/container identity is covered
by test_image_identity and by the production platform's live verification.
"""

import base64
import copy
import importlib.util
import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[4]
SPEC = importlib.util.spec_from_file_location(
    "assembly_reconcile_test", ROOT / "deploy/evolution/assembly.py"
)
assembly = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(assembly)


class IdentityError(RuntimeError):
    pass


class MetadataDeployment:
    """Documented read-only API port with independently recorded live images."""

    def __init__(self, path):
        self.root = ROOT
        self.state = path
        self.state.mkdir(mode=0o700)
        self.events = []
        self.pins = {
            name: "docker.io/fixture/" + name + "@sha256:" + "a" * 64
            for name in (
                "evolution-service",
                "evolution-gateway",
                "evolution-model",
                "postgres",
                "python",
                "caddy",
            )
        }
        self.rows = {}
        self.pods = {}
        self.replicas = {}
        self.identities = {}
        self.fail_ready = False
        self.fail_helm = False
        self.secret = None
        self.secret_race = False

    def owner(self):
        return {"owner_id": "fixture-owner", "container_id": "fixture-node"}

    def image(self, name):
        return self.pins[name]

    @staticmethod
    def read_json(path):
        return json.loads(assembly.seed.private_read(path))

    @staticmethod
    def write_json(path, value):
        assembly._write(path, value)

    def lock(self):
        return nullcontext()

    def verify_prepared(self):
        self.events.append("verify_prepared")

    def support(self, name):
        assert name == "image_identity"
        return SimpleNamespace(
            verify_container=self.verify_identity,
            verify_completed_init=self.verify_completed_identity,
            ImageIdentityError=IdentityError
        )

    def verify_completed_identity(self, _deployment, expected, status):
        cid = status["containerID"]
        self.events.append(("completed_identity", cid))
        if status["state"]["terminated"]["exitCode"] != 0 or self.identities[cid] != expected:
            raise IdentityError("image_identity_config_mismatch")
        return {"container_id": cid, "expected_reference": expected, "completed": True}

    def verify_identity(self, _deployment, expected, status):
        cid = status["containerID"]
        self.events.append(("identity", cid))
        if not status["ready"] or self.identities[cid] != expected:
            raise IdentityError("image_identity_config_mismatch")
        return {"container_id": cid, "expected_reference": expected, "ready": True}

    def install(self, values):
        self.rows, self.pods, self.replicas, self.identities = {}, {}, {}, {}
        for name, row in values["components"].items():
            if not row["enabled"]:
                continue
            containers = [
                {
                    "name": row["component"],
                    "image": row["image"],
                    "resources": copy.deepcopy(row["resources"]),
                    "args": copy.deepcopy(row["args"]),
                    "readinessProbe": {
                        "exec": {"command": copy.deepcopy(row["readinessCommand"])}
                    },
                }
            ]
            if containers[0]["resources"]["limits"]["cpu"] == "1000m":
                containers[0]["resources"]["limits"]["cpu"] = "1"
            if row.get("sidecar"):
                containers.append({"name": "tls", "image": row["sidecar"]["image"]})
            if row.get("postgresTunnel"):
                containers.append(
                    {"name": "postgres-tunnel", "image": row["postgresTunnel"]["image"]}
                )
            self.rows[name] = {
                "metadata": {
                    "name": name,
                    "namespace": row["namespace"],
                    "uid": "deploy-" + name,
                    "labels": {"guardrails.evolution/owner": "fixture-owner"},
                },
                "spec": {
                    "replicas": 1,
                    "template": {
                        "spec": {"containers": containers},
                        "metadata": {
                            "annotations": {
                                "guardrails.evolution/config-revision": row[
                                    "configRevision"
                                ]
                            }
                        },
                    },
                },
            }
            self.replicas[name] = {
                "metadata": {
                    "annotations": {
                        "guardrails.evolution/config-revision": row["configRevision"]
                    },
                    "name": name,
                    "uid": "replica-" + name,
                    "ownerReferences": [
                        {
                            "kind": "Deployment",
                            "controller": True,
                            "uid": "deploy-" + name,
                        }
                    ],
                },
            }
            statuses = []
            for container in containers:
                cid = "container-" + name + "-" + container["name"]
                self.identities[cid] = container["image"]
                statuses.append(
                    {"name": container["name"], "containerID": cid, "ready": True}
                )
            self.pods[name] = {
                "metadata": {
                    "name": name,
                    "annotations": {
                        "guardrails.evolution/config-revision": row["configRevision"]
                    },
                    "ownerReferences": [
                        {
                            "name": name,
                            "uid": "replica-" + name,
                            "kind": "ReplicaSet",
                            "controller": True,
                        }
                    ],
                },
                "spec": {"containers": copy.deepcopy(containers)},
                "status": {"containerStatuses": statuses},
            }
            if row.get("sidecar"):
                security = {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                            "capabilities": {"drop": ["ALL"]}}
                tls = next(c for c in containers if c["name"] == "tls")
                tls.update(command=["/executable/caddy", "run", "--config", "/config/Caddyfile", "--adapter", "caddyfile"],
                           securityContext=copy.deepcopy(security),
                           resources={"requests": {"cpu": "20m", "memory": "32Mi"},
                                      "limits": {"cpu": "100m", "memory": "96Mi"}},
                           volumeMounts=[{"name": "caddy-executable", "mountPath": "/executable", "subPath": "private", "readOnly": True}])
                tls["volumeMounts"].append({"name": "state", "mountPath": "/state", "subPath": "data"})
                containers[0].update(
                    command=["ollama", "serve"], securityContext=copy.deepcopy(security),
                    env=[{"name": k, "value": v} for k, v in sorted(row["env"].items())],
                    volumeMounts=[
                        {"name": "private", "mountPath": "/config", "subPath": "data", "readOnly": True},
                        {"name": "state", "mountPath": "/state", "subPath": "data"},
                        {"name": "temporary", "mountPath": "/tmp"},
                        {"name": "state", "mountPath": "/models/manifests-v2", "subPath": "model-manifests-v2", "readOnly": True},
                    ])
                spec = {"containers": copy.deepcopy(containers), "securityContext": {"runAsUser": 10001, "runAsGroup": 10001},
                        "initContainers": [{"name": "caddy-binary", "image": row["sidecar"]["image"],
                                            "command": ["timeout", "30", "/bin/sh", "-ec"], "args": [assembly._caddy_copy_script(self)],
                                            "securityContext": security, "resources": {"requests": {"cpu": "10m", "memory": "16Mi"},
                                                                                     "limits": {"cpu": "100m", "memory": "64Mi"}},
                                            "volumeMounts": [{"name": "caddy-executable", "mountPath": "/executable"}]}],
                        "volumes": [{"name": "caddy-executable", "emptyDir": {"sizeLimit": "96Mi"}}]}
                spec["initContainers"].append({
                    "name": "private-files", "image": self.image("python"),
                    "command": ["python", "-B", "-c"], "args": [assembly._model_private_files_script(self)],
                    "securityContext": copy.deepcopy(security),
                    "resources": {"requests": {"cpu": "10m", "memory": "16Mi"},
                                  "limits": {"cpu": "100m", "memory": "32Mi"}},
                    "volumeMounts": [{"name": "state", "mountPath": "/state"}],
                })
                spec["volumes"].append({"name": "state", "emptyDir": {"sizeLimit": "64Mi"}})
                self.rows[name]["spec"]["template"]["spec"] = copy.deepcopy(spec)
                self.pods[name]["spec"] = copy.deepcopy(spec)
                cid = "initializer-" + name
                self.identities[cid] = row["sidecar"]["image"]
                self.pods[name]["status"]["initContainerStatuses"] = [
                    {"name": "caddy-binary", "containerID": cid, "state": {"terminated": {"exitCode": 0}}}]
                cid = "private-initializer-" + name
                self.identities[cid] = self.image("python")
                self.pods[name]["status"]["initContainerStatuses"].append(
                    {"name": "private-files", "containerID": cid, "state": {"terminated": {"exitCode": 0}}})

    def kubectl(self, *args, namespace, **_kwargs):
        if args[0] == "exec":
            if args[3] == "tls":
                assert args[:6] == ("exec", "ollama", "-c", "tls", "--", "timeout")
                assert _kwargs["timeout"] == 35
                self.events.append("verify_caddy_copy")
            else:
                assert args[:6] == ("exec", "ollama", "-c", "ollama", "--", "timeout")
                assert _kwargs["timeout"] == 15
                assert args[-1] == assembly._model_verify_command()
                self.events.append("verify_model_manifests")
            return 0, ""
        if args[:2] == ("get", "secret"):
            return 0, "" if self.secret is None else json.dumps(self.secret)
        if args[0] == "create":
            assert self.secret is None
            self.secret = json.loads(_kwargs["input_text"])
            self.secret["metadata"].update(uid="secret-uid", resourceVersion="1")
            self.events.append("create_secret")
            return 0, ""
        if args[0] == "patch":
            operations = json.loads(_kwargs["input_text"])
            if self.secret_race:
                self.secret["metadata"]["resourceVersion"] = "concurrent"
            for operation in operations:
                path = operation["path"].strip("/").split("/")
                selected = self.secret
                for key in path[:-1]:
                    selected = selected[key]
                if operation["op"] == "test":
                    if selected[path[-1]] != operation["value"]:
                        raise assembly.AssemblyError("fixture_secret_cas_conflict")
                else:
                    assert operation["op"] == "replace"
                    selected[path[-1]] = operation["value"]
            self.events.append("patch_secret")
            return 0, ""
        assert args[0] == "get"
        if args[1] == "deployments":
            value = {
                "items": [
                    r
                    for r in self.rows.values()
                    if r["metadata"]["namespace"] == namespace
                ]
            }
        elif args[1] == "pods":
            name = args[3].split("=", 1)[1]
            value = {"items": [self.pods[name]]}
        else:
            assert args[1] == "replicaset"
            value = self.replicas[args[2]]
        return 0, json.dumps(value)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    d = MetadataDeployment(tmp_path / "state")
    directory, _ = assembly._state(d)
    for relative in ("tls/gr-platform/postgres", "private"):
        path = directory / relative
        path.mkdir(parents=True, mode=0o700)
    for name in ("tls.crt", "tls.key", "ca.crt"):
        assembly._write(
            directory / "tls/gr-platform/postgres" / name, b"synthetic-" + name.encode()
        )
    assembly._write(
        directory / "private/database-owner.token", b"synthetic-owner-password"
    )
    pg_revision = assembly._postgres_configuration(
        d, d.owner(), assembly._postgres_files(d, directory)
    )
    values = {
        "ownerId": "fixture-owner",
        "initImage": d.image("python"),
        "servicesOnly": False,
        "components": {},
    }
    for name, (namespace, component, artifact) in assembly._APPLICATIONS.items():
        row = assembly._component(
            namespace,
            component,
            d.image(artifact),
            "retained-config",
            env={"RETAIN": "synthetic-setting"},
        )
        row["enabled"] = component != "ollama"
        if component == "ollama":
            row["sidecar"] = {"image": d.image("caddy")}
            row["env"] = dict(assembly.MODEL_ENV)
            row["command"] = ["ollama", "serve"]
        if component == "postgres":
            row["postgresTunnel"] = {"image": d.image("python")}
            row.update(
                args=assembly.POSTGRES_ARGS,
                readinessCommand=assembly.POSTGRES_READINESS,
                configRevision=pg_revision,
            )
        values["components"][name] = row
    assembly._write(directory / "values.json", values)
    assembly._write(
        directory / "inventory.json",
        {"credential_hash": "synthetic-hash", "binding_revision": 8},
    )
    assembly._write(directory / "gateway.token", b"synthetic-private-token")
    assembly._write(
        directory / "access.json",
        {
            "profile": "deterministic_mock",
            "seed_inventory_file": str(directory / "inventory.json"),
            "ca_file": str(directory / "ca.crt"),
            "tenants": {
                "team-a": {
                    "gateway_url": "https://fixture.invalid",
                    "gateway_token_file": str(directory / "gateway.token"),
                }
            },
        },
    )
    d.install(values)
    monkeypatch.setattr(assembly, "_network_baseline", lambda *_: None)
    # Host port recovery has its own real file/socket tests.
    monkeypatch.setattr(assembly, "_prepare_management_relocation", lambda *_: None)
    monkeypatch.setattr(assembly, "_port_record", lambda *_: {})
    monkeypatch.setattr(
        assembly, "_https_ready", lambda *_a, **_k: d.events.append("https_ready")
    )
    monkeypatch.setattr(
        assembly, "stop_forwards", lambda *_a, **_k: d.events.append("stop_forwards")
    )

    def helm(_deployment, selected, desired):
        d.events.append("helm")
        assembly._write(selected / "values.json", desired)
        if d.fail_helm:
            raise assembly.AssemblyError("helm_failed")
        d.install(desired)

    def ready(_deployment, name, namespace, **_kwargs):
        d.events.append(("ready", name, namespace))
        if d.fail_ready:
            raise assembly.AssemblyError("readiness_failed")

    monkeypatch.setattr(assembly, "_helm", helm)
    monkeypatch.setattr(assembly, "_ready", ready)
    # Ordered donor rollout has a separate resource_budget metadata-port suite.
    monkeypatch.setattr(assembly, "_cpu_donors", lambda *_a, **_k: None)

    def no_seed(*_a, **_k):
        pytest.fail("completed replay must not enter initial governance seeding")

    monkeypatch.setattr(assembly.seed, "bootstrap", no_seed)
    monkeypatch.setattr(assembly.seed, "configure", no_seed)
    monkeypatch.setattr(assembly.seed, "wait_applied", no_seed)
    d.events.clear()
    return d, directory, values


def protected_bytes(directory):
    return {
        str(p.relative_to(directory)): p.read_bytes()
        for p in directory.rglob("*")
        if p.is_file() and p.name not in {"values.json", "running-images.json"}
    }


def test_completed_replay_verifies_actual_images_without_helm_or_seed(setup):
    d, directory, values = setup
    before = protected_bytes(directory)
    assert assembly.platform(d)["status"] == "ready"
    assert "helm" not in d.events and "stop_forwards" not in d.events
    assert d.events[0] == "verify_prepared"
    identities = [
        event
        for event in d.events
        if isinstance(event, tuple) and event[0] == "identity"
    ]
    assert len(identities) == 8
    assert d.read_json(directory / "values.json") == values
    assert protected_bytes(directory) == before


def test_prepared_image_and_resource_changes_preserve_private_state_and_reconcile_once(
    setup,
):
    d, directory, values = setup
    values["components"]["management"]["resources"]["limits"]["cpu"] = "250m"
    values["components"]["ollama"]["resources"]["limits"]["cpu"] = "1250m"
    assembly._write(directory / "values.json", values)
    d.install(values)
    before = protected_bytes(directory)
    d.pins["evolution-service"] = (
        d.image("evolution-service").split("@")[0] + "@sha256:" + "b" * 64
    )
    assert assembly.platform(d)["status"] == "ready"
    assert d.events.count("helm") == 1
    assert d.events.index("stop_forwards") < d.events.index("helm")
    after = d.read_json(directory / "values.json")
    for name, row in after["components"].items():
        original = copy.deepcopy(values["components"][name])
        original["image"] = row["image"]
        original["resources"]["limits"] = row["resources"]["limits"]
        assert original == row
    assert after["components"]["management"]["resources"]["limits"]["cpu"] == "500m"
    assert after["components"]["ollama"]["resources"]["limits"]["cpu"] == "950m"
    assert protected_bytes(directory) == before
    assert assembly.platform(d)["status"] == "ready"
    assert d.events.count("helm") == 1


@pytest.mark.parametrize("failure", ["helm", "ready"])
def test_failed_image_reconciliation_retry_requires_live_rollout_before_ready(
    setup, failure
):
    d, directory, _ = setup
    before = protected_bytes(directory)
    d.pins["evolution-service"] = (
        d.image("evolution-service").split("@")[0] + "@sha256:" + "b" * 64
    )
    setattr(d, "fail_" + failure, True)
    with pytest.raises(assembly.AssemblyError, match="(helm_failed|readiness_failed)"):
        assembly.platform(d)
    assert not (directory / "running-images.json").exists()
    assert protected_bytes(directory) == before
    setattr(d, "fail_" + failure, False)
    assert assembly.platform(d)["status"] == "ready"
    assert d.events.count("helm") == (2 if failure == "helm" else 1)
    assert protected_bytes(directory) == before


@pytest.mark.parametrize("fault", ["values-owner", "deployment-owner", "foreign-image"])
def test_reconciliation_rejects_foreign_owner_or_image_before_mutation(setup, fault):
    d, directory, values = setup
    if fault == "values-owner":
        values["ownerId"] = "foreign-owner"
    elif fault == "deployment-owner":
        d.rows["runtime-2"]["metadata"]["labels"]["guardrails.evolution/owner"] = (
            "foreign-owner"
        )
    else:
        values["components"]["runtime-2"]["image"] = (
            "docker.io/foreign/image@sha256:" + "b" * 64
        )
    assembly._write(directory / "values.json", values)
    before = protected_bytes(directory)
    with pytest.raises(assembly.AssemblyError, match="(owner|image)"):
        assembly.platform(d)
    assert "helm" not in d.events and "stop_forwards" not in d.events
    assert protected_bytes(directory) == before
    assert d.read_json(directory / "values.json") == values


@pytest.mark.parametrize(
    "fault", ["pod-image", "actual-config", "pod-owner", "not-ready", "pod-resources"]
)
def test_ready_metadata_cannot_hide_wrong_running_image_or_owner(setup, fault):
    d, directory, _ = setup
    pod = d.pods["runtime-2"]
    if fault == "pod-image":
        pod["spec"]["containers"][0]["image"] = d.image("evolution-model")
    elif fault == "actual-config":
        d.identities[pod["status"]["containerStatuses"][0]["containerID"]] = d.image(
            "evolution-model"
        )
    elif fault == "not-ready":
        pod["status"]["containerStatuses"][0]["ready"] = False
    elif fault == "pod-resources":
        pod["spec"]["containers"][0]["resources"]["limits"]["cpu"] = "2"
    else:
        d.replicas["runtime-2"]["metadata"]["ownerReferences"][0]["uid"] = (
            "foreign-deployment"
        )
    with pytest.raises(assembly.AssemblyError, match="(image|owner|resource)"):
        assembly.platform(d)
    assert not (directory / "running-images.json").exists()
    assert "helm" not in d.events


def test_resource_rebalance_preserves_total_cpu_limit(setup):
    _, _, values = setup
    total = sum(
        int(row["resources"]["limits"]["cpu"].removesuffix("m"))
        for row in values["components"].values()
    )
    assert total == 5650
    assert total + 100 + 50 + 4 * 50 == 6000
    assert assembly.RESOURCE_BUDGETS["runtime"][0] == "1000m"
    assert assembly.RESOURCE_BUDGETS["postgres"][0] == "500m"


@pytest.mark.parametrize(
    "initial,target",
    [("deterministic_mock", "local_model"), ("local_model", "deterministic_mock")],
)
def test_profile_change_and_new_image_share_one_owned_reconciliation(
    setup, monkeypatch, initial, target
):
    d, directory, values = setup
    values["components"]["ollama"]["enabled"] = initial == "local_model"
    assembly._write(directory / "values.json", values)
    d.install(values)
    access = d.read_json(directory / "access.json")
    access.update(
        profile=initial,
        mock_token_file=str(directory / "gateway.token"),
        ollama_url="https://fixture.invalid",
    )
    assembly._write(directory / "access.json", access)
    inventory = {
        "tenants": {
            "team-a": {"binding_revision": 8},
            "team-b": {"binding_revision": 12},
        }
    }
    assembly._write(directory / "inventory.json", inventory)
    inventory_before = (directory / "inventory.json").read_bytes()
    token_before = (directory / "gateway.token").read_bytes()
    monkeypatch.setattr(assembly, "_port_record", lambda *_: {"ollama": 24443})
    monkeypatch.setattr(
        assembly, "_forward", lambda *_a, **_k: d.events.append("forward")
    )
    monkeypatch.setattr(
        assembly, "_warm_model", lambda *_a, **_k: d.events.append("warm_model")
    )

    def gateway(_d, _directory, _owner, tenant, _value, profile, _token):
        row = copy.deepcopy(values["components"]["gateway-" + tenant])
        row["configRevision"] = profile
        return row

    monkeypatch.setattr(assembly, "_gateway", gateway)
    d.pins["evolution-service"] = (
        d.image("evolution-service").split("@")[0] + "@sha256:" + "b" * 64
    )
    assert assembly.platform(d, profile=target)["status"] == "ready"
    assert d.events.count("helm") == 1
    assert d.events.index("stop_forwards") < d.events.index("helm")
    assert ("warm_model" in d.events) == (target == "local_model")
    assert d.read_json(directory / "access.json")["profile"] == target
    assert (directory / "inventory.json").read_bytes() == inventory_before
    assert (directory / "gateway.token").read_bytes() == token_before


def legacy_postgres(setup):
    d, directory, values = setup
    del d.secret["data"]["pg_hba.conf"]
    row = values["components"]["postgres"]
    row["args"] = assembly._POSTGRES_OLD_ARGS
    row["readinessCommand"] = [
        "pg_isready",
        "-h",
        "127.0.0.1",
        "-U",
        "evolution_owner",
        "-d",
        "guardrails_evolution",
    ]
    row["configRevision"] = assembly.hashlib.sha256(
        assembly.canonical_json_bytes(d.secret["data"])
    ).hexdigest()
    assembly._write(directory / "values.json", values)
    d.install(values)
    return d, directory, values


def test_completed_replay_installs_exact_scram_hba_without_changing_private_bytes(
    setup,
):
    d, directory, _ = legacy_postgres(setup)
    before = protected_bytes(directory)
    old_secret = copy.deepcopy(d.secret["data"])
    assert assembly.platform(d)["status"] == "ready"
    assert d.events.count("patch_secret") == d.events.count("helm") == 1
    assert d.events.index("stop_forwards") < d.events.index("helm")
    assert {
        k: v for k, v in d.secret["data"].items() if k != "pg_hba.conf"
    } == old_secret
    assert base64.b64decode(d.secret["data"]["pg_hba.conf"]).splitlines() == [
        b"local all evolution_owner trust",
        b"local all all reject",
        b"hostnossl all all 0.0.0.0/0 reject",
        b"hostnossl all all ::/0 reject",
        b"hostssl all all 0.0.0.0/0 scram-sha-256",
        b"hostssl all all ::/0 scram-sha-256",
    ]
    row = d.read_json(directory / "values.json")["components"]["postgres"]
    assert row["args"][-2:] == ["-c", "hba_file=/config/pg_hba.conf"]
    assert row["readinessCommand"][2] == "/var/run/postgresql"
    assert protected_bytes(directory) == before
    assert assembly.platform(d)["status"] == "ready"
    assert d.events.count("patch_secret") == d.events.count("helm") == 1
    assert protected_bytes(directory) == before


@pytest.mark.parametrize(
    "fault", ["owner", "tls", "password", "relay", "hba", "race", "args"]
)
def test_postgres_authentication_repair_refuses_foreign_or_changed_state(setup, fault):
    d, directory, values = legacy_postgres(setup)
    if fault == "owner":
        d.secret["metadata"]["labels"]["guardrails.evolution/owner"] = "other"
    elif fault == "race":
        d.secret_race = True
    elif fault == "args":
        values["components"]["postgres"]["args"] = ["postgres", "-c", "ssl=off"]
        assembly._write(directory / "values.json", values)
    else:
        key = {
            "tls": "tls.key",
            "password": "owner-password",
            "relay": "postgres_tunnel.py",
            "hba": "pg_hba.conf",
        }[fault]
        d.secret["data"][key] = base64.b64encode(b"synthetic-foreign-bytes").decode()
    before = protected_bytes(directory)
    old_data = copy.deepcopy(d.secret["data"])
    with pytest.raises(assembly.AssemblyError, match="(postgres_|cas_conflict)"):
        assembly.platform(d)
    assert d.secret["data"] == old_data
    assert "helm" not in d.events and "patch_secret" not in d.events
    assert protected_bytes(directory) == before


@pytest.mark.parametrize("failure", ["helm", "ready"])
def test_postgres_authentication_rollout_failure_retries_without_secret_or_seed_rewrite(
    setup, failure
):
    d, directory, _ = legacy_postgres(setup)
    before = protected_bytes(directory)
    setattr(d, "fail_" + failure, True)
    with pytest.raises(assembly.AssemblyError, match="(helm_failed|readiness_failed)"):
        assembly.platform(d)
    assert not (directory / "running-images.json").exists()
    assert protected_bytes(directory) == before
    setattr(d, "fail_" + failure, False)
    assert assembly.platform(d)["status"] == "ready"
    assert d.events.count("patch_secret") == 1
    assert d.events.count("helm") == (2 if failure == "helm" else 1)
    assert protected_bytes(directory) == before


@pytest.mark.parametrize("fault", ["args", "revision", "probe"])
def test_ready_postgres_requires_actual_secure_arguments_and_config_revision(
    setup, fault
):
    d, directory, _ = setup
    pod = d.pods["postgres"]
    if fault == "args":
        pod["spec"]["containers"][0]["args"] = assembly._POSTGRES_OLD_ARGS
    elif fault == "probe":
        pod["spec"]["containers"][0]["readinessProbe"]["exec"]["command"] = ["true"]
    else:
        pod["metadata"]["annotations"]["guardrails.evolution/config-revision"] = "stale"
    with pytest.raises(
        assembly.AssemblyError, match="postgres_running_authentication_mismatch"
    ):
        assembly.platform(d)
    assert not (directory / "running-images.json").exists()
    assert "helm" not in d.events
