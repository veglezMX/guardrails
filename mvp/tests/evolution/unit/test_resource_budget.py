"""Ordered CPU donor transitions through a CAS-aware Kubernetes metadata port.

No cluster enforcement is claimed: rendered charts and live acceptance own it.
The port checks real JSON patch tests and records mutation/rollout ordering.
"""

import copy
import importlib.util
import json
from contextlib import nullcontext
from pathlib import Path
from types import MethodType

import pytest

ROOT = Path(__file__).resolve().parents[4]


def load(name):
    spec = importlib.util.spec_from_file_location(
        "budget_test_" + name, ROOT / "deploy/evolution" / (name + ".py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


assembly, budget, bootstrap = (
    load("assembly"),
    load("resource_budget"),
    load("bootstrap"),
)


class Port:
    def __init__(self, path):
        self.root, self.bin, self.kubeconfig = ROOT, path, path / "kubeconfig"
        self.events, self.rows, self.pods, self.replicas = [], {}, {}, {}
        self.fault = None
        self.before_patch = None
        self.include_old_pod = False
        self.directory = path / "assembly"
        self.directory.mkdir()
        self.private = self.directory / "retained.token"
        self.private.write_bytes(b"synthetic-retained-credential")
        for namespace, name in (*budget.PROBES, ("gr-model", "mock")):
            probe = name != "mock"
            key = namespace, name
            security = {
                "allowPrivilegeEscalation": False,
                "readOnlyRootFilesystem": True,
                "capabilities": {"drop": ["ALL"]},
            }

            def container(label, image, request, limit, security=security):
                return {
                    "name": label,
                    "image": image,
                    "imagePullPolicy": "Never",
                    "securityContext": copy.deepcopy(security),
                    "resources": {"requests": request, "limits": limit},
                    "args": ["retained-argument"],
                    "env": [{"name": "SAFE", "value": "retained"}],
                }

            app = container(
                "probe" if probe else "mock",
                self.image("python" if probe else "evolution-service"),
                {"cpu": "10m", "memory": "16Mi"}
                if probe
                else {"cpu": "100m", "memory": "128Mi"},
                {"cpu": "100m", "memory": "48Mi"}
                if probe
                else {"cpu": "250m", "memory": "512Mi"},
            )
            init = container(
                "private-secrets" if probe else "private-files",
                self.image("python"),
                {"cpu": "10m", "memory": "16Mi"},
                {"cpu": "50m" if probe else "100m", "memory": "32Mi"},
            )
            labels = {"app": name} if probe else {"guardrails.evolution/instance": name}
            row = {
                "metadata": {
                    "name": name,
                    "namespace": namespace,
                    "uid": namespace + "-" + name,
                    "resourceVersion": "1",
                    "labels": {"guardrails.evolution/owner": "owner"},
                },
                "spec": {
                    "replicas": 1,
                    "selector": {"matchLabels": labels},
                    "strategy": {
                        "type": "RollingUpdate",
                        "rollingUpdate": {"maxSurge": "25%", "maxUnavailable": "25%"},
                    }
                    if probe
                    else {"type": "Recreate"},
                    "template": {
                        "metadata": {
                            "labels": labels,
                            "annotations": {"retained": "revision"},
                        },
                        "spec": {"containers": [app], "initContainers": [init]},
                    },
                },
            }
            self.rows[key] = row
            self.install(key)

    def image(self, name):
        return "docker.io/fixture/" + name + "@sha256:" + "a" * 64

    def support(self, name):
        if name == "assembly":
            return assembly
        assert name == "resource_budget"
        return budget

    @staticmethod
    def read_json(path):
        return json.loads(path.read_text())

    def install(self, key):
        row = self.rows[key]
        uid = row["metadata"]["uid"]
        self.replicas[key] = {
            "metadata": {
                "uid": "rs-" + uid,
                "ownerReferences": [
                    {"kind": "Deployment", "controller": True, "uid": uid}
                ],
            }
        }
        self.pods[key] = (
            []
            if row["spec"]["replicas"] == 0
            else [
                {
                    "metadata": {
                        "ownerReferences": [
                            {
                                "kind": "ReplicaSet",
                                "controller": True,
                                "name": key[1],
                                "uid": "rs-" + uid,
                            }
                        ]
                    },
                    "spec": copy.deepcopy(row["spec"]["template"]["spec"]),
                    "status": {
                        "phase": "Running",
                        "containerStatuses": [
                            {
                                "name": "mock" if key[1] == "mock" else "probe",
                                "ready": True,
                                "state": {"running": {}},
                            }
                        ],
                    },
                }
            ]
        )

    def kubectl(self, *args, namespace=None, **kwargs):
        if args[:2] == ("get", "namespace"):
            return 0, json.dumps(
                {"metadata": {"labels": {"guardrails.evolution/owner": "owner"}}}
            )
        if args[0] == "apply":
            return 0, ""
        assert 0 < kwargs["timeout"] <= 125
        key = (
            namespace,
            args[2].removeprefix("deployment/"),
        )
        if args[:2] == ("get", "deployment"):
            return 0, json.dumps(self.rows[key]) if key in self.rows else ""
        if args[:2] == ("get", "pods"):
            key = namespace, args[3].split("=", 1)[1]
            self.events.append(("pods", key))
            pods = copy.deepcopy(self.pods[key])
            if self.include_old_pod:
                pods.append({"metadata": {"deletionTimestamp": "pending"}})
            return 0, json.dumps({"items": pods})
        if args[:2] == ("get", "replicaset"):
            return 0, json.dumps(self.replicas[key])
        if args[0] == "patch":
            if self.before_patch:
                self.before_patch(self.rows[key])
                self.before_patch = None
            result = copy.deepcopy(self.rows[key])
            for op in json.loads(kwargs["input_text"]):
                path = op["path"].strip("/").split("/")
                parent = result
                for field in path[:-1]:
                    parent = parent[field]
                if op["op"] == "test":
                    if parent[path[-1]] != op["value"]:
                        return 1, ""
                else:
                    assert op["op"] == "replace" and op["path"] == "/spec"
                    parent[path[-1]] = op["value"]
            result["metadata"]["resourceVersion"] = str(
                int(result["metadata"]["resourceVersion"]) + 1
            )
            self.rows[key] = result
            self.events.append(("patch", key))
            return 0, json.dumps(result)
        assert args[:2] == ("rollout", "status")
        self.events.append(("rollout", key))
        if self.fault == key:
            return 1, ""
        self.install(key)
        return 0, ""

    def run(self, args, **kwargs):
        assert args[1:3] == ["upgrade", "--install"] and kwargs["timeout"] == 120
        self.events.append(("helm", None))
        return 0, ""


@pytest.fixture
def port(tmp_path):
    return Port(tmp_path)


def values(port):
    return {
        "ownerId": "owner",
        "components": {
            name: assembly._component(
                "gr-platform" if name == "postgres" else "gr-model",
                name,
                port.image("postgres" if name == "postgres" else "evolution-service"),
                "retained",
            )
            for name in ("postgres", "mock")
        },
    }


def apply(port):
    assembly._helm(port, port.directory, values(port))


def test_production_support_loader_reaches_ordered_donor_checks(port, monkeypatch):
    deployment = bootstrap.LocalDeployment(ROOT, state=port.directory.parent)
    # Only external command/image ports are replaced. Both dynamic support
    # loads and the assembly-to-resource-budget call use production methods.
    monkeypatch.setattr(deployment, "image", port.image)
    monkeypatch.setattr(deployment, "kubectl", port.kubectl)
    helper = deployment.support("resource_budget")
    assert Path(helper.__file__).resolve() == ROOT / "deploy/evolution/resource_budget.py"
    loaded_assembly = deployment.support("assembly")

    loaded_assembly._cpu_donors(
        deployment, port.directory, values(port), change=True
    )
    assert [key for event, key in port.events if event == "patch"] == list(port.rows)
    for key, row in port.rows.items():
        assert row["spec"]["strategy"] == {"type": "Recreate"}
        assert row["spec"]["template"]["spec"]["containers"][0]["resources"][
            "limits"
        ]["cpu"] == ("200m" if key[1] == "mock" else "50m")
        assert port.events.index(("patch", key)) < port.events.index(("rollout", key))
        assert port.events.index(("rollout", key)) < port.events.index(("pods", key))

    # A subsequent production check must still reject drift without mutation;
    # admitting the loader must not accidentally bypass its security checks.
    port.events.clear()
    port.rows["gr-model", "mock"]["metadata"]["labels"][
        "guardrails.evolution/owner"
    ] = "foreign"
    with pytest.raises(loaded_assembly.AssemblyError, match="^cpu_donor_owner_mismatch$"):
        loaded_assembly._cpu_donors(deployment, port.directory, values(port), change=True)
    assert port.events == []


def test_production_support_loader_remains_closed_to_unapproved_modules(port):
    deployment = bootstrap.LocalDeployment(ROOT, state=port.directory.parent)
    with pytest.raises(bootstrap.DeploymentError, match="^unknown_deployment_support$"):
        deployment.support("../resource_budget")
    assert port.events == []


def test_real_helm_boundary_waits_for_all_donors_and_replay_preserves_private_state(
    port,
):
    originals = copy.deepcopy(port.rows)
    apply(port)
    keys = list(originals)
    assert [key for event, key in port.events if event == "patch"] == keys
    assert port.events[-1] == ("helm", None)
    for key in keys:
        assert (
            port.events.index(("patch", key))
            < port.events.index(("rollout", key))
            < port.events.index(("pods", key))
            < port.events.index(("helm", None))
        )
        expected = copy.deepcopy(originals[key]["spec"])
        expected["strategy"] = {"type": "Recreate"}
        expected["template"]["spec"]["containers"][0]["resources"]["limits"]["cpu"] = (
            "200m" if key[1] == "mock" else "50m"
        )
        assert port.rows[key]["spec"] == expected
    assert port.private.read_bytes() == b"synthetic-retained-credential"
    assert port.read_json(port.directory / "values.json")["components"]["postgres"][
        "resources"
    ]["limits"] == {"cpu": "500m", "memory": "512Mi"}
    port.events.clear()
    apply(port)
    assert not any(event == "patch" for event, _ in port.events)
    assert port.private.read_bytes() == b"synthetic-retained-credential"


@pytest.mark.parametrize(
    "fault",
    [
        "foreign-owner",
        "missing-uid",
        "missing-rv",
        "unknown-cpu",
        "memory",
        "request",
        "image",
        "init-image",
        "init-limit",
        "security",
        "extra-container",
        "strategy",
    ],
)
def test_any_untrusted_donor_aborts_before_first_mutation_or_pg_helm(port, fault):
    # Corrupt the last donor: all preceding valid donors must remain untouched.
    row = port.rows["gr-model", "mock"]
    app = row["spec"]["template"]["spec"]["containers"][0]
    init = row["spec"]["template"]["spec"]["initContainers"][0]
    if fault == "foreign-owner":
        row["metadata"]["labels"]["guardrails.evolution/owner"] = "foreign"
    elif fault == "missing-uid":
        row["metadata"].pop("uid")
    elif fault == "missing-rv":
        row["metadata"].pop("resourceVersion")
    elif fault == "unknown-cpu":
        app["resources"]["limits"]["cpu"] = "300m"
    elif fault == "memory":
        app["resources"]["limits"]["memory"] = "1Gi"
    elif fault == "request":
        app["resources"]["requests"]["cpu"] = "50m"
    elif fault == "image":
        app["image"] = port.image("foreign")
    elif fault == "init-image":
        init["image"] = port.image("foreign")
    elif fault == "init-limit":
        init["resources"]["limits"]["cpu"] = "150m"
    elif fault == "security":
        app["securityContext"]["readOnlyRootFilesystem"] = False
    elif fault == "extra-container":
        row["spec"]["template"]["spec"]["containers"].append(copy.deepcopy(app))
    elif fault == "strategy":
        row["spec"]["strategy"] = {"type": "RollingUpdate"}
    with pytest.raises(assembly.AssemblyError, match="cpu_donor_"):
        apply(port)
    assert not port.events
    assert not (port.directory / "values.json").exists()


@pytest.mark.parametrize("field", ["uid", "resourceVersion", "template"])
def test_concurrent_donor_change_fails_cas_and_never_increases_pg(port, field):
    def race(row):
        if field == "template":
            row["spec"]["template"]["metadata"]["annotations"]["retained"] = (
                "concurrent"
            )
        else:
            row["metadata"][field] = "concurrent"

    port.before_patch = race
    with pytest.raises(assembly.AssemblyError, match="cpu_donor_command_failed"):
        apply(port)
    assert not any(event in {"patch", "helm"} for event, _ in port.events)


def test_failed_rollout_retains_safe_reduction_and_retry_resumes_without_repatch(port):
    port.fault = budget.PROBES[1]
    with pytest.raises(assembly.AssemblyError, match="cpu_donor_rollout_failed"):
        apply(port)
    assert ("helm", None) not in port.events
    assert ("patch", budget.PROBES[0]) in port.events
    assert ("patch", ("gr-model", "mock")) not in port.events
    port.fault = None
    port.events.clear()
    apply(port)
    assert ("patch", budget.PROBES[0]) not in port.events
    assert ("patch", budget.PROBES[1]) not in port.events
    assert port.events[-1] == ("helm", None)


def test_terminating_old_pod_still_blocks_pg_increase(port):
    port.include_old_pod = True
    with pytest.raises(assembly.AssemblyError, match="cpu_donor_not_converged"):
        apply(port)
    assert ("helm", None) not in port.events


def test_absent_donors_are_never_created(port):
    port.rows.clear()
    apply(port)
    assert port.events == [("helm", None)]


def test_post_probe_helm_cannot_claim_missing_deployment_as_converged(port):
    port.rows.pop(budget.PROBES[-1])
    budget.check(port, "owner", assembly._same_limits, change=True)
    with pytest.raises(budget.ResourceBudgetError, match="cpu_donor_missing"):
        budget.check(port, "owner", assembly._same_limits, require_probes=True)


@pytest.mark.parametrize("fault", ["resource-shape", "template-shape"])
def test_malformed_donor_metadata_has_finite_failure(port, fault):
    row = port.rows["gr-model", "mock"]
    if fault == "resource-shape":
        row["spec"]["template"]["spec"]["containers"][0]["resources"] = None
    else:
        row["spec"]["template"] = None
    with pytest.raises(assembly.AssemblyError, match="cpu_donor_response_invalid"):
        apply(port)
    assert not port.events


def test_zero_replica_donors_remain_stopped_and_require_no_old_pods(port):
    for key, row in port.rows.items():
        row["spec"]["replicas"] = 0
        port.install(key)
    apply(port)
    assert all(row["spec"]["replicas"] == 0 for row in port.rows.values())
    assert all(not pods for pods in port.pods.values())


@pytest.mark.parametrize("fault", ["old-pod-limit", "foreign-replicaset", "not-ready"])
def test_post_helm_probe_verification_rejects_nonconverged_live_metadata(port, fault):
    budget.check(port, "owner", assembly._same_limits, change=True)
    key = budget.PROBES[0]
    if fault == "old-pod-limit":
        port.pods[key][0]["spec"]["containers"][0]["resources"]["limits"]["cpu"] = (
            "100m"
        )
    elif fault == "foreign-replicaset":
        port.replicas[key]["metadata"]["ownerReferences"][0]["uid"] = "foreign"
    else:
        port.pods[key][0]["status"]["containerStatuses"][0]["ready"] = False
    with pytest.raises(budget.ResourceBudgetError, match="cpu_donor_"):
        budget.check(port, "owner", assembly._same_limits)


def test_equivalent_quantities_are_accepted_without_resource_rewrite(port):
    budget.check(port, "owner", assembly._same_limits, change=True)
    for key in budget.PROBES:
        port.rows[key]["spec"]["template"]["spec"]["containers"][0]["resources"][
            "limits"
        ] = {"cpu": "0.05", "memory": "50331648"}
        port.install(key)
    port.events.clear()
    budget.check(port, "owner", assembly._same_limits, change=True)
    assert not any(event == "patch" for event, _ in port.events)


def test_probe_helm_failure_never_rolls_back_to_previous_larger_allocation(
    port, monkeypatch
):
    port.state, port.env = port.directory.parent, {}
    credentials = port.state / "credentials"
    credentials.mkdir()
    for name in ("ca.crt", "wrong-ca.crt", "server.crt", "server.key", "expired.crt"):
        (credentials / name).write_bytes(b"synthetic-certificate-fixture")
    port.lock = nullcontext
    port.owner = lambda: {"owner_id": "owner"}
    port.verify_prepared = lambda: None
    port._probe_cpu_budget = MethodType(
        bootstrap.LocalDeployment._probe_cpu_budget, port
    )
    observed = []

    def failed_helm(command, **kwargs):
        observed.append(command)
        assert "--atomic" not in command
        assert "rollback" not in command
        assert "--wait" in command and command[-2:] == ["--timeout", "3m"]
        assert kwargs["timeout"] == 240
        raise bootstrap.DeploymentError("command_failed")

    monkeypatch.setattr(bootstrap, "command", failed_helm)
    with pytest.raises(bootstrap.DeploymentError, match="command_failed"):
        bootstrap.LocalDeployment.probes(port)
    assert len(observed) == 1
    for key in budget.PROBES:
        row = port.rows[key]
        assert row["spec"]["strategy"] == {"type": "Recreate"}
        assert (
            row["spec"]["template"]["spec"]["containers"][0]["resources"]["limits"][
                "cpu"
            ]
            == "50m"
        )
        assert ("pods", key) in port.events
    assert (
        port.rows["gr-model", "mock"]["spec"]["template"]["spec"]["containers"][0][
            "resources"
        ]["limits"]["cpu"]
        == "250m"
    )
