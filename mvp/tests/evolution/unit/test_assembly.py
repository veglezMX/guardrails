"""Assembly guards with private files, real process identities and sockets.

The file-only deployment port represents a caller whose cluster ownership was
already checked. These cases do not claim to exercise Kubernetes or its RBAC.
"""

import errno
import importlib.util
import json
import os
import socket
import subprocess
import sys
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path

import pytest
from guardrails.evolution.errors import EvolutionError

ROOT = Path(__file__).resolve().parents[4]


def load_support(name):
    spec = importlib.util.spec_from_file_location(
        "assembly_test_" + name, ROOT / "deploy/evolution" / (name + ".py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


assembly = load_support("assembly")
bootstrap = load_support("bootstrap")
certificates = load_support("certificates")


class FilesOnlyDeployment:
    read_json = staticmethod(bootstrap.read_json)
    write_json = staticmethod(bootstrap.atomic_json)

    def __init__(self, directory):
        self.state = directory / "deployment"
        self.state.mkdir(mode=0o700)
        self.bin = self.state / "bin"
        self.bin.mkdir(mode=0o700)
        self.kubeconfig = self.state / "kubeconfig"
        self.env = dict(os.environ)
        self.write_json(self.state / "owner.json", {
            "owner_id": "assembly-test-owner", "state": str(self.state), "uid": os.getuid(),
            "container_id": "owned-cluster-container",
        })

    def owner(self):
        return self.read_json(self.state / "owner.json")

    def support(self, name):
        assert name == "certificates"
        return certificates


@pytest.fixture
def deployment(tmp_path):
    return FilesOnlyDeployment(tmp_path)


@contextmanager
def unrelated_child():
    # A ready byte proves exec completed before reading /proc cmdline/starttime.
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", "import sys,time; print('ready'); time.sleep(60)"],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        assert process.stdout.readline() == b"ready\n"
        yield process
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=3)
        process.stdout.close()


@pytest.mark.parametrize("record_name", ["management", "unregistered-service"])
def test_stop_forwards_rejects_forged_matching_live_process_without_signalling_it(deployment, record_name):
    directory, _ = assembly._state(deployment)
    assembly._port_record(deployment, directory)
    with unrelated_child() as process:
        identity = assembly._process_identity(process.pid)
        assert identity is not None and identity["pid"] == process.pid
        record = directory / ("forward-" + record_name + ".json")
        deployment.write_json(record, identity)
        original = record.read_bytes()
        with pytest.raises(assembly.AssemblyError, match="forward_.*(owner_mismatch|invalid)"):
            assembly.stop_forwards(deployment)
        assert process.poll() is None
        assert assembly._process_identity(process.pid) == identity
        assert record.read_bytes() == original


def test_stop_forwards_rejects_reused_pid_identity_and_leaves_live_child(deployment):
    directory, _ = assembly._state(deployment)
    assembly._port_record(deployment, directory)
    with unrelated_child() as process:
        identity = assembly._process_identity(process.pid)
        deployment.write_json(directory / "forward-management.json", {
            **identity, "start": str(int(identity["start"]) - 1),
        })
        with pytest.raises(assembly.AssemblyError, match="forward_process_owner_mismatch"):
            assembly.stop_forwards(deployment)
        assert process.poll() is None
        assert assembly._process_identity(process.pid) == identity


def test_forward_rejects_unrelated_live_record_and_occupied_loopback_port(deployment):
    directory, _ = assembly._state(deployment)
    ports = assembly._port_record(deployment, directory)
    record = directory / "forward-management.json"
    with unrelated_child() as process:
        deployment.write_json(record, assembly._process_identity(process.pid))
        with pytest.raises(assembly.AssemblyError, match="forward_process_owner_mismatch"):
            assembly._forward(deployment, directory, "management", ports["management"])
        assert process.poll() is None
    record.unlink()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        occupied = listener.getsockname()[1]
        with pytest.raises(assembly.AssemblyError, match="forward_port_already_used"):
            assembly._forward(deployment, directory, "management", occupied)
        with socket.create_connection(("127.0.0.1", occupied), timeout=1) as connection:
            peer, _ = listener.accept()
            peer.close()
            assert connection.fileno() >= 0
    assert not record.exists()
    assert not (directory / "forward-management.log").exists()


def test_forward_listener_belongs_to_exact_process_and_requires_listen_state():
    identity = assembly._process_identity(os.getpid())
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        assert assembly._forward_listening(identity, port) is False
        listener.listen(1)
        assert assembly._forward_listening(identity, port) is True
        with unrelated_child() as child:
            assert assembly._forward_listening(assembly._process_identity(child.pid), port) is False
        stale = {**identity, "start": str(int(identity["start"]) - 1)}
        assert assembly._forward_listening(stale, port) is False
    assert assembly._forward_listening(identity, port) is False


def test_forward_startup_and_reuse_do_not_open_upstream_connections(deployment, monkeypatch):
    directory, _ = assembly._state(deployment)
    ports = assembly._port_record(deployment, directory)
    port = ports["management"]
    # A real listener exits on any unsolicited connection during readiness.
    program = (
        "import socket,select,sys,time; s=socket.socket(); "
        "s.bind(('127.0.0.1',int(sys.argv[1]))); s.listen(); "
        "ready=select.select([s],[],[],0.4)[0]; "
        "print('unexpected-connection' if ready else 'passive-listener',flush=True); "
        "sys.exit(17) if ready else time.sleep(60)"
    )
    monkeypatch.setattr(
        assembly, "_forward_argv",
        lambda _deployment, _name, selected: [sys.executable, "-u", "-c", program, str(selected)],
    )
    try:
        assembly._forward(deployment, directory, "management", port)
        record = deployment.read_json(directory / "forward-management.json")
        assembly._forward(deployment, directory, "management", port)
        assert deployment.read_json(directory / "forward-management.json") == record
        log = directory / "forward-management.log"
        deadline = time.monotonic() + 3
        while not log.read_bytes() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert log.read_text().strip() == "passive-listener"
        assert assembly._process_identity(record["pid"]) == record
        assert assembly._forward_listening(record, port)
    finally:
        assembly.stop_forwards(deployment)
    try:
        os.waitpid(record["pid"], 0)
    except ChildProcessError:
        pass
    assert assembly._process_identity(record["pid"]) is None
    assert deployment.read_json(directory / "forward-management.json") == record


def test_forward_preflight_reuses_owned_time_wait_address_before_local_listener(deployment):
    directory, _ = assembly._state(deployment)
    with ExitStack() as handles:
        listener = handles.enter_context(socket.socket())
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.settimeout(1)
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        listener.listen(1)
        client = handles.enter_context(socket.socket())
        client.settimeout(1)
        client.connect(("127.0.0.1", port))
        accepted, _ = listener.accept()
        handles.enter_context(accepted)
        accepted.settimeout(1)
        # The owned server initiates FIN; this retains its recently used local
        # address even though all three owned handles are then closed.
        accepted.shutdown(socket.SHUT_WR)
        assert client.recv(1) == b""
        client.shutdown(socket.SHUT_WR)
        assert accepted.recv(1) == b""
        accepted.close()
        client.close()
        listener.close()
        with socket.socket() as plain:
            with pytest.raises(OSError) as blocked:
                plain.bind(("127.0.0.1", port))
            assert blocked.value.errno == errno.EADDRINUSE

        class LocalListenerObserved(Exception):
            pass

        spawned = []

        def local_listener(argv, **kwargs):
            assert argv == assembly._forward_argv(deployment, "management", port)
            assert kwargs["start_new_session"] is True
            with socket.socket() as replacement:
                replacement.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                replacement.bind(("127.0.0.1", port))
                replacement.listen(1)
                spawned.append(True)
                # Stop at the injected boundary: no kubectl, subprocess, model
                # or upstream connection is needed to prove listener adoption.
                raise LocalListenerObserved

        with pytest.raises(LocalListenerObserved):
            assembly._forward(deployment, directory, "management", port,
                              timeout=1, spawn=local_listener)
        assert spawned == [True]
        assert not (directory / "forward-management.json").exists()


@pytest.mark.parametrize("foreign_reuse", [False, True])
def test_forward_reuse_preflight_still_refuses_foreign_listener(deployment, foreign_reuse):
    directory, _ = assembly._state(deployment)
    with socket.socket() as listener:
        if foreign_reuse:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]

        def forbidden_spawn(*args, **kwargs):
            raise AssertionError("foreign listener must prevent spawn")

        with pytest.raises(assembly.AssemblyError, match="^forward_port_already_used$") as refused:
            assembly._forward(deployment, directory, "management", port,
                              timeout=1, spawn=forbidden_spawn)
        assert refused.value.errno == errno.EADDRINUSE
        assert listener.fileno() >= 0
        assert not (directory / "forward-management.json").exists()
        assert not (directory / "forward-management.log").exists()


@pytest.mark.parametrize("phase,number", [("setsockopt", errno.ENOPROTOOPT),
                                         ("bind", errno.EACCES), ("bind", errno.ENOBUFS)])
def test_forward_socket_failure_keeps_finite_code_numeric_errno_and_closes_probe(
    deployment, monkeypatch, phase, number,
):
    directory, _ = assembly._state(deployment)
    closed = []

    class FailingSocket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            closed.append(True)

        def setsockopt(self, *args):
            if phase == "setsockopt":
                raise OSError(number, "private OS diagnostic must not escape")

        def bind(self, *args):
            raise OSError(number, "private OS diagnostic must not escape")

    monkeypatch.setattr(assembly.socket, "socket", FailingSocket)
    with pytest.raises(assembly.AssemblyError, match="^forward_port_already_used$") as refused:
        assembly._forward(deployment, directory, "management", 49161,
                          spawn=lambda *args, **kwargs: pytest.fail("spawn must not run"))
    assert refused.value.errno == number and closed == [True]
    assert str(refused.value) == "forward_port_already_used"
    assert not (directory / "forward-management.json").exists()
    assert not (directory / "forward-management.log").exists()


def test_assembly_owner_mismatch_refuses_state_adoption(deployment):
    directory, _ = assembly._state(deployment)
    marker = directory / "owner.json"
    deployment.write_json(marker, {**deployment.read_json(marker), "owner_id": "another-owner"})
    original = marker.read_bytes()
    with pytest.raises(assembly.AssemblyError, match="assembly_owner_mismatch"):
        assembly._state(deployment)
    assert marker.read_bytes() == original


def test_recreated_cluster_cannot_adopt_previous_assembly_state(deployment):
    directory, _ = assembly._state(deployment)
    marker = directory / "owner.json"
    original = marker.read_bytes()
    owner_path = deployment.state / "owner.json"
    deployment.write_json(owner_path, {**deployment.owner(), "container_id": "replacement-cluster-container"})
    with pytest.raises(assembly.AssemblyError, match="assembly_owner_mismatch"):
        assembly._state(deployment)
    assert marker.read_bytes() == original


@pytest.mark.parametrize("unsafe", ["public_file", "symlink"])
def test_secret_file_refuses_unsafe_existing_file_without_replacing_bytes(tmp_path, unsafe):
    directory = assembly._directory(tmp_path / "private")
    target = directory / "credential"
    original = b"preexisting-private-material"
    if unsafe == "public_file":
        target.write_bytes(original)
        target.chmod(0o644)
        with pytest.raises(EvolutionError) as rejected:
            assembly._secret_file(target)
        assert rejected.value.code == "validation_failed"
    else:
        elsewhere = tmp_path / "outside-credential"
        elsewhere.write_bytes(original)
        elsewhere.chmod(0o600)
        target.symlink_to(elsewhere)
        with pytest.raises(OSError):
            assembly._secret_file(target)
        assert target.is_symlink()
        assert elsewhere.read_bytes() == original
    assert target.read_bytes() == original


def test_generated_secrets_and_port_inventory_are_private_stable_and_distinct(deployment):
    directory, _ = assembly._state(deployment)
    a, b = directory / "tenant-a.token", directory / "tenant-b.token"
    first = assembly._secret_file(a)
    other = assembly._secret_file(b)
    assert len(first) >= 48 and len(other) >= 48 and first != other
    identity = (a.stat().st_ino, a.stat().st_mtime_ns, a.read_bytes())
    assert assembly._secret_file(a) == first
    assert (a.stat().st_ino, a.stat().st_mtime_ns, a.read_bytes()) == identity
    assert a.stat().st_mode & 0o777 == b.stat().st_mode & 0o777 == 0o600
    ports = assembly._port_record(deployment, directory)
    assert set(ports) == set(assembly.SERVICES)
    assert len(set(ports.values())) == len(ports)
    assert assembly._port_record(deployment, directory) == ports
    path = directory / "ports.json"
    assert path.stat().st_mode & 0o777 == 0o600
    modified = dict(ports)
    modified["team-b"] = modified["team-a"]
    deployment.write_json(path, modified)
    with pytest.raises(assembly.AssemblyError, match="forward_port_inventory_invalid"):
        assembly._port_record(deployment, directory)
    assert json.loads(path.read_bytes()) == modified


def test_gateway_namespaces_have_distinct_private_tls_keys_and_stable_replay(deployment):
    directory, _ = assembly._state(deployment)
    first = assembly._tls(deployment, directory, "gateway", "gr-team-a")
    second = assembly._tls(deployment, directory, "gateway", "gr-team-b")
    assert first["tls.key"] != second["tls.key"]
    assert first["tls.crt"] != second["tls.crt"]
    assert first["ca.crt"] == second["ca.crt"]
    assert assembly._tls(deployment, directory, "gateway", "gr-team-a") == first
    assert assembly._tls(deployment, directory, "gateway", "gr-team-b") == second
    for namespace in ("gr-team-a", "gr-team-b"):
        target = directory / "tls" / namespace / "gateway"
        assert target.stat().st_mode & 0o777 == 0o700
        assert {path.name for path in target.iterdir()} == {"tls.key", "tls.crt", "ca.crt"}
        assert all(path.stat().st_mode & 0o777 == 0o600 for path in target.iterdir())


def render_chart(tmp_path, name, values):
    state = Path(os.environ.get("EVOLUTION_DEPLOYMENT_STATE", ROOT / ".evolution/deployment"))
    helm = state / "bin/helm"
    assert helm.is_file(), "Phase2 Helm must already be prepared"
    value_file = tmp_path / (name + "-values.json")
    bootstrap.atomic_json(value_file, values)
    result = subprocess.run(
        [str(helm), "template", "evolution-" + name,
         str(ROOT / "deploy/evolution/charts" / name), "--namespace", "gr-platform",
         "--values", str(value_file)],
        text=True, capture_output=True, timeout=15, check=False,
    )
    assert result.returncode == 0, result.stderr
    # Use the prepared Helm parser itself. The service wheel lock deliberately
    # contains no general-purpose YAML package and tests must not acquire one.
    decoder = tmp_path / (name + "-decode")
    decoder.mkdir()
    (decoder / "templates").mkdir()
    (decoder / "Chart.yaml").write_text("apiVersion: v2\nname: manifest-decode\nversion: 1.0.0\n")
    (decoder / "manifest.yaml").write_text(result.stdout)
    (decoder / "templates/catalog.json").write_text(
        '{{- $documents := list -}}'
        '{{- range splitList "\\n---\\n" (.Files.Get "manifest.yaml") -}}'
        '{{- if trim . -}}{{- $documents = append $documents (fromYaml .) -}}{{- end -}}'
        '{{- end -}}'
        '{{ dict "apiVersion" "v1" "kind" "List" "items" $documents | toJson }}'
    )
    decoded = subprocess.run([str(helm), "template", "decode", str(decoder)],
                             text=True, capture_output=True, timeout=15, check=False)
    assert decoded.returncode == 0, decoded.stderr
    raw = "\n".join(line for line in decoded.stdout.splitlines()
                    if line.strip() != "---" and not line.startswith("#"))
    return json.loads(raw)["items"]


def resources(value, resource):
    if resource == "cpu":
        return int(value[:-1]) if value.endswith("m") else int(value) * 1000
    suffix = "Mi" if value.endswith("Mi") else "Gi"
    assert value.endswith(suffix)
    return int(value[:-2]) * (1 if suffix == "Mi" else 1024)


@pytest.mark.parametrize("profile", ["deterministic_mock", "local_model"])
def test_rendered_platform_and_probe_resource_envelope_and_private_mounts(tmp_path, profile):
    state = Path(os.environ.get("EVOLUTION_DEPLOYMENT_STATE", ROOT / ".evolution/deployment"))
    prepared = bootstrap.read_json(state / "prepared.json")
    image = next(item["source_url"].removeprefix("docker://")
                 for item in prepared["artifacts"] if item["name"] == "python")
    values = {"ownerId": "assembly-test-owner", "servicesOnly": False,
              "initImage": image, "components": {}}
    components = [
        ("postgres", "gr-platform", "postgres"),
        ("management", "gr-platform", "management"),
        ("runtime-1", "gr-platform", "runtime"),
        ("runtime-2", "gr-platform", "runtime"),
        ("gateway-team-a", "gr-team-a", "gateway"),
        ("gateway-team-b", "gr-team-b", "gateway"),
        ("mock", "gr-model", "mock"),
        ("ollama", "gr-model", "ollama"),
    ]
    for name, namespace, component in components:
        value = assembly._component(namespace, component, image, "fixture-revision",
                                    uid=999 if component == "postgres" else 10001,
                                    sidecar={"image": image} if component == "ollama" else None,
                                    postgres_tunnel={"image": image} if component == "postgres" else None)
        if component == "ollama":
            value["enabled"] = profile == "local_model"
        values["components"][name] = value
    platform = render_chart(tmp_path, "platform", values)
    probes = render_chart(tmp_path, "probes", {"ownerId": values["ownerId"]})
    actual = [doc for doc in platform if doc["kind"] == "Deployment"]
    assert len(actual) == (7 if profile == "deterministic_mock" else 8)
    postgres = next(row for row in actual if row["metadata"]["name"] == "postgres")
    tunnel = next(row for row in postgres["spec"]["template"]["spec"]["containers"]
                  if row["name"] == "postgres-tunnel")
    assert tunnel["command"] == ["python", "-B", "/config/postgres_tunnel.py"]
    assert tunnel["readinessProbe"]["exec"]["command"][-1] == "--check"
    postgres_service = next(row for row in platform if row["kind"] == "Service"
                            and row["metadata"]["name"] == "postgres")
    assert postgres_service["spec"]["ports"] == [
        {"name": "postgres", "port": 5432, "targetPort": 5432}]
    assert len([doc for doc in probes if doc["kind"] == "Deployment"]) == 4
    for document in probes:
        assert document["metadata"]["labels"]["guardrails.evolution/owner"] == values["ownerId"]
        if document["kind"] == "Deployment":
            assert document["spec"]["strategy"] == {"type": "Recreate"}
            template = document["spec"]["template"]
            assert template["metadata"]["labels"]["guardrails.evolution/owner"] == values["ownerId"]
            assert document["spec"]["selector"]["matchLabels"] == {"app": document["metadata"]["name"]}
            assert template["metadata"]["labels"]["app"] == document["metadata"]["name"]
    totals = {(kind, resource): 0 for kind in ("requests", "limits") for resource in ("cpu", "memory")}
    for document in platform + probes:
        if document["kind"] != "Deployment":
            continue
        spec = document["spec"]["template"]["spec"]
        assert spec["automountServiceAccountToken"] is False
        assert spec["securityContext"]["runAsNonRoot"] is True
        for kind, resource in totals:
            running = sum(resources(container["resources"][kind][resource], resource)
                          for container in spec["containers"])
            initializing = max(resources(container["resources"][kind][resource], resource)
                               for container in spec["initContainers"])
            totals[kind, resource] += max(running, initializing) * document["spec"]["replicas"]
        for container in spec["containers"] + spec["initContainers"]:
            assert container["imagePullPolicy"] == "Never"
            assert container["securityContext"]["allowPrivilegeEscalation"] is False
            assert container["securityContext"]["readOnlyRootFilesystem"] is True
            assert container["securityContext"]["capabilities"]["drop"] == ["ALL"]
    assert totals["limits", "cpu"] <= 6000
    assert totals["limits", "memory"] <= 8192
    assert totals["requests", "cpu"] <= 5000
    assert totals["requests", "memory"] <= 6144
    assert totals == {
        ("limits", "cpu"): 6000 if profile == "local_model" else 4950,
        ("limits", "memory"): 6496 if profile == "local_model" else 4864,
        ("requests", "cpu"): 870 if profile == "local_model" else 750,
        ("requests", "memory"): 1136 if profile == "local_model" else 976,
    }
    for document in actual:
        name = document["metadata"]["name"]
        spec = document["spec"]["template"]["spec"]
        volumes = {volume["name"]: volume for volume in spec["volumes"]}
        assert volumes["projected"]["secret"]["secretName"] == name + "-config"
        assert volumes["private"]["emptyDir"]["medium"] == "Memory"
        for container in spec["containers"]:
            mounts = {mount["name"]: mount for mount in container["volumeMounts"]}
            assert "projected" not in mounts
            assert mounts["private"] == {"name": "private", "mountPath": "/config", "subPath": "data", "readOnly": True}


class ManifestInventory:
    """Read-only cluster response port backed by actual rendered manifest bytes."""
    def __init__(self, root, documents):
        self.directory = root / "rendered-cluster"
        self.directory.mkdir(mode=0o700)
        self.paths = {}
        self.calls = []
        for document in documents:
            kind, metadata = document["kind"].lower(), document["metadata"]
            if kind not in {"namespace", "networkpolicy"}:
                continue
            namespace = metadata.get("namespace", metadata["name"])
            key = kind, namespace, metadata["name"]
            path = self.directory / ("-".join(key) + ".json")
            bootstrap.atomic_json(path, document)
            self.paths[key] = path

    def kubectl(self, verb, kind, name, *args, namespace, **kwargs):
        self.calls.append((verb, kind, name, namespace))
        assert verb == "get", "Preflight must not mutate cluster objects"
        assert args == ("-o", "json")
        assert not kwargs
        return 0, self.paths[kind, namespace, name].read_text()


@pytest.mark.parametrize("change", ["foreign_owner", "unrestricted_namespace", "allow_all_egress", "dns_anywhere"])
def test_network_baseline_rejects_rendered_namespace_or_policy_tampering_before_mutation(tmp_path, change):
    owner = {"owner_id": "assembly-test-owner"}
    documents = render_chart(tmp_path, "probes", {"ownerId": owner["owner_id"]})
    inventory = ManifestInventory(tmp_path, documents)
    assembly._network_baseline(inventory, owner)
    assert len(inventory.calls) == 12
    if change in {"foreign_owner", "unrestricted_namespace"}:
        path = inventory.paths["namespace", "gr-team-a", "gr-team-a"]
        value = json.loads(path.read_bytes())
        label = "guardrails.evolution/owner" if change == "foreign_owner" else "pod-security.kubernetes.io/enforce"
        value["metadata"]["labels"][label] = "foreign" if change == "foreign_owner" else "privileged"
        expected = "namespace_owner_mismatch"
    else:
        name = "default-deny" if change == "allow_all_egress" else "dns-only"
        path = inventory.paths["networkpolicy", "gr-team-a", name]
        value = json.loads(path.read_bytes())
        if change == "allow_all_egress":
            value["spec"]["egress"] = [{}]
        else:
            value["spec"]["egress"][0].pop("to")
        expected = "platform_network_baseline_invalid"
    bootstrap.atomic_json(path, value)
    saved = path.read_bytes()
    with pytest.raises(assembly.AssemblyError, match=expected):
        assembly._network_baseline(inventory, owner)
    assert path.read_bytes() == saved
    assert all(call[0] == "get" for call in inventory.calls)
