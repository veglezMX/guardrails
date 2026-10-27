"""Actual pinned Caddy execution plus rendered-copy/provenance boundaries."""

import copy
import io
import json
import os
import subprocess
import tarfile
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_assembly import ROOT, assembly, bootstrap, render_chart
from test_assembly_reconcile import assembly as replay_assembly
from test_assembly_reconcile import protected_bytes
from test_assembly_reconcile import setup as replay_setup  # noqa: F401 - pytest fixture


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    directory = tmp_path_factory.mktemp("caddy-render")
    state = Path(os.environ["EVOLUTION_DEPLOYMENT_STATE"])
    d = bootstrap.LocalDeployment(ROOT, state=state)
    row = assembly._component("gr-model", "ollama", d.image("evolution-model"),
                              "synthetic-revision", sidecar={"image": d.image("caddy")})
    values = {"ownerId": "caddy-test-owner", "servicesOnly": False,
              "initImage": d.image("python"), "components": {"ollama": row}}
    docs = render_chart(directory, "platform", values)
    deploy = next(x for x in docs if x["kind"] == "Deployment")
    return row, deploy["spec"]["template"]


def test_rendered_caddy_copy_is_private_bounded_and_read_only_after_init(rendered):
    row, template = rendered
    d = SimpleNamespace(root=ROOT)
    assert assembly._caddy_settings_match(d, row, template)
    spec = template["spec"]
    initial = next(x for x in spec["initContainers"] if x["name"] == "caddy-binary")
    assert initial["resources"] == {"requests": {"cpu": "10m", "memory": "16Mi"},
                                    "limits": {"cpu": "100m", "memory": "64Mi"}}
    assert initial["imagePullPolicy"] == "Never"
    assert initial["command"] == ["timeout", "30", "/bin/sh", "-ec"]
    assert initial["args"] == [assembly._caddy_copy_script(d)]
    assert 'cmp -s /usr/bin/caddy "$temporary"' in initial["args"][0]
    assert 'capabilities=$(getcap "$temporary")' in initial["args"][0]
    for container in spec["containers"] + spec["initContainers"]:
        assert container["securityContext"]["allowPrivilegeEscalation"] is False
        assert container["securityContext"]["capabilities"] == {"drop": ["ALL"]}
    assert spec["securityContext"]["runAsUser"] == spec["securityContext"]["runAsGroup"] == 10001


@pytest.mark.parametrize("change", ["image", "script", "writable", "capability", "foreign_mount", "budget"])
def test_caddy_copy_rejects_untrusted_execution_configuration(rendered, change):
    row, original = rendered
    template = copy.deepcopy(original)
    spec = template["spec"]
    initial = next(x for x in spec["initContainers"] if x["name"] == "caddy-binary")
    tls = next(x for x in spec["containers"] if x["name"] == "tls")
    if change == "image":
        initial["image"] = "foreign@sha256:" + "f" * 64
    elif change == "script":
        initial["args"] = ["exit 0"]
    elif change == "writable":
        next(x for x in tls["volumeMounts"] if x["name"] == "caddy-executable")["readOnly"] = False
    elif change == "capability":
        tls["securityContext"]["capabilities"]["add"] = ["NET_BIND_SERVICE"]
    elif change == "foreign_mount":
        spec["containers"][0]["volumeMounts"].append({"name": "caddy-executable", "mountPath": "/foreign"})
    else:
        initial["resources"]["limits"]["cpu"] = "150m"
    assert not assembly._caddy_settings_match(SimpleNamespace(root=ROOT), row, template)


def test_caddy_copy_requires_completed_image_proof_and_actual_read_only_bytes(rendered):
    row, template = rendered
    pod = copy.deepcopy(template)
    pod["metadata"]["name"] = "owned-ollama"
    status = {"name": "caddy-binary", "containerID": "containerd://fixture-init"}
    pod["status"] = {"initContainerStatuses": [status]}
    calls = []

    class ImageError(RuntimeError):
        pass

    def completed(deployment, reference, supplied):
        assert reference == row["sidecar"]["image"] and supplied == status
        calls.append("completed_oci")
        return {"completed": True, "exit_code": 0}

    def kubectl(*args, **kwargs):
        calls.append("live_copy")
        assert args[:6] == ("exec", "owned-ollama", "-c", "tls", "--", "timeout")
        assert args[6:9] == ("30", "/bin/sh", "-ec")
        assert "cmp -s /usr/bin/caddy /executable/caddy" in args[9]
        assert "/proc/self/mountinfo" in args[9] and "CapEff:" in args[9]
        assert kwargs == {"namespace": "gr-model", "timeout": 35}
        return 0, ""

    d = SimpleNamespace(root=ROOT, kubectl=kubectl)
    verifier = SimpleNamespace(verify_completed_init=completed, ImageIdentityError=ImageError)
    evidence = assembly._verify_caddy_copy(d, "ollama", row, pod, verifier)
    assert calls == ["completed_oci", "live_copy"]
    assert evidence["completed"] and evidence["copied_bytes_verified"]
    assert evidence["copy_read_only"] and evidence["copy_capabilities_empty"]
    d.kubectl = lambda *args, **kwargs: (1, "")
    with pytest.raises(assembly.AssemblyError, match="caddy_copy_runtime_mismatch"):
        assembly._verify_caddy_copy(d, "ollama", row, pod, verifier)
    pod["status"]["initContainerStatuses"] = []
    with pytest.raises(assembly.AssemblyError, match="caddy_copy_initialization_missing"):
        assembly._verify_caddy_copy(d, "ollama", row, pod, verifier)


@pytest.mark.parametrize("boundary", ["template", "pod"])
@pytest.mark.parametrize(
    "container,kind,resource,value",
    [
        ("tls", "requests", "cpu", "25m"),
        ("tls", "requests", "memory", "48Mi"),
        ("tls", "limits", "cpu", "150m"),
        ("tls", "limits", "memory", "128Mi"),
        ("caddy-binary", "requests", "cpu", "20m"),
        ("caddy-binary", "requests", "memory", "32Mi"),
        ("caddy-binary", "limits", "cpu", "150m"),
        ("caddy-binary", "limits", "memory", "96Mi"),
    ],
    ids=["tls-request-cpu", "tls-request-memory", "tls-limit-cpu", "tls-limit-memory",
         "init-request-cpu", "init-request-memory", "init-limit-cpu", "init-limit-memory"],
)
def test_caddy_resource_drift_is_reconciled_or_refused_before_live_success(
    replay_setup, boundary, container, kind, resource, value  # noqa: F811 - pytest fixture
):
    # The metadata port keeps desired Deployment and actual Pod state separate.
    # Template drift must trigger reconciliation; Pod-only drift cannot hide
    # behind an unchanged desired template or a correct immutable image.
    d, directory, values = replay_setup
    values["components"]["ollama"]["enabled"] = True
    d.install(values)
    selected = (d.rows["ollama"]["spec"]["template"] if boundary == "template"
                else d.pods["ollama"])
    group = "containers" if container == "tls" else "initContainers"
    altered = next(c for c in selected["spec"][group] if c["name"] == container)
    altered["resources"][kind][resource] = value
    before = protected_bytes(directory)
    d.events.clear()
    if boundary == "template":
        replay_assembly._reconcile_application_images(d, directory, values, d.owner())
        assert d.events.count("helm") == 1
        assert d.events.index("stop_forwards") < d.events.index("helm")
        observed = d.read_json(directory / "running-images.json")
        assert any(row["container"] == "caddy-binary" and row["copied_bytes_verified"]
                   for row in observed["images"])
        repaired = next(c for c in d.pods["ollama"]["spec"][group]
                        if c["name"] == container)
        assert repaired["resources"][kind][resource] != value
    else:
        with pytest.raises(replay_assembly.AssemblyError,
                           match="^caddy_copy_configuration_mismatch$"):
            replay_assembly._reconcile_application_images(d, directory, values, d.owner())
        assert "helm" not in d.events and "stop_forwards" not in d.events
        assert not (directory / "running-images.json").exists()
        assert not any(isinstance(event, tuple) and event[0] == "completed_identity"
                       for event in d.events)
    assert protected_bytes(directory) == before


def test_caddy_resources_accept_equivalent_quantities_from_rendered_chart(tmp_path):
    # Only local Helm rendering is required; image identity is a separate gate.
    image = "registry.invalid/test@sha256:" + "a" * 64
    row = assembly._component("gr-model", "ollama", image, "synthetic-revision",
                              sidecar={"image": image})
    values = {"ownerId": "caddy-resource-owner", "servicesOnly": False,
              "initImage": image, "components": {"ollama": row}}
    docs = render_chart(tmp_path, "platform", values)
    template = next(doc for doc in docs if doc["kind"] == "Deployment")["spec"]["template"]
    d = SimpleNamespace(root=ROOT)
    assert assembly._caddy_settings_match(d, row, template)
    initial = next(c for c in template["spec"]["initContainers"] if c["name"] == "caddy-binary")
    tls = next(c for c in template["spec"]["containers"] if c["name"] == "tls")
    initial["resources"] = {"requests": {"cpu": "0.010", "memory": "16384Ki"},
                            "limits": {"cpu": "0.1", "memory": "67108864"}}
    tls["resources"] = {"requests": {"cpu": "0.020", "memory": "32768Ki"},
                        "limits": {"cpu": "0.1", "memory": "100663296"}}
    assert assembly._caddy_settings_match(d, row, template)
    for target in (initial, tls):
        for kind in ("requests", "limits"):
            expected = target["resources"].pop(kind)
            assert not assembly._caddy_settings_match(d, row, template)
            target["resources"][kind] = expected


def test_actual_pinned_caddy_executes_read_only_capless_copy_and_starts_tls(rendered):
    row, template = rendered
    image = row["sidecar"]["image"]
    initial = next(x for x in template["spec"]["initContainers"] if x["name"] == "caddy-binary")
    name = "evolution-caddy-test-" + uuid4().hex[:12]
    state = Path(os.environ["EVOLUTION_DEPLOYMENT_STATE"])
    security = ["--pull=never", "--network=none", "--read-only", "--user", "10001:10001",
                "--cap-drop=ALL", "--security-opt=no-new-privileges"]
    init_security = [*security, "--memory=64m", "--cpus=0.1"]
    runtime_security = [*security, "--memory=96m", "--cpus=0.1"]

    def run(args, *, payload=None, timeout=40, success=True):
        result = subprocess.run(args, input=payload, capture_output=True, timeout=timeout, check=False)
        if success:
            assert result.returncode == 0, {"operation": args[1], "exit": result.returncode}
        return result

    created = False
    try:
        run(["docker", "volume", "create", "--label", "guardrails.evolution/test=" + name,
             "--opt", "type=tmpfs", "--opt", "device=tmpfs",
             "--opt", "o=uid=10001,gid=10001,mode=2770,size=96m", name])
        created = True
        # Keep the tmpfs mounted between the nonroot init and runtime. Kubernetes
        # itself owns the production emptyDir lifetime; this is a Docker fixture.
        run(["docker", "run", "-d", "--name", name + "-keep", *init_security,
             "--mount", "type=volume,src=" + name + ",dst=/executable",
             "--entrypoint", "/bin/sh", image, "-c", "sleep 180"])
        original = run(["docker", "run", "--rm", *runtime_security, "--entrypoint", "/usr/bin/caddy", image, "version"], success=False)
        assert original.returncode == 255
        assert b"operation not permitted" in original.stderr.lower()
        files = {"Caddyfile": b"{ admin off\n auto_https off\n}\n:8443 {\n tls /config/tls.crt /config/tls.key\n reverse_proxy 127.0.0.1:11434\n}\n"}
        for filename in ("tls.crt", "tls.key"):
            files[filename] = (state / "assembly/tls/gr-model/ollama" / filename).read_bytes()
        for executable, code in (("getcap", 41), ("cmp", 42)):
            files["fault-" + executable + "/" + executable] = f"#!/bin/sh\nexit {code}\n".encode()
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w") as tar:
            for filename, payload in files.items():
                info = tarfile.TarInfo(filename)
                info.size = len(payload)
                info.mode = 0o500 if filename.startswith("fault-") else 0o600
                tar.addfile(info, io.BytesIO(payload))
        command = initial["args"][0] + "\ntar -xf - -C /executable/private"
        run(["docker", "run", "--rm", "-i", "--name", name + "-init", *init_security,
             "--mount", "type=volume,src=" + name + ",dst=/executable",
             "--entrypoint", "timeout", image, "30", "/bin/sh", "-ec", command], payload=archive.getvalue())
        # A restarted init can safely replace an earlier completed/partial copy.
        run(["docker", "run", "--rm", "--name", name + "-init", *init_security,
             "--mount", "type=volume,src=" + name + ",dst=/executable",
             "--entrypoint", "timeout", image, "30", "/bin/sh", "-ec", initial["args"][0]])
        for executable, code in (("getcap", 41), ("cmp", 42)):
            # A trusted subprocess port deliberately fails with empty stdout.
            # The real pinned shell/copy recipe must propagate that failure.
            command = "PATH=/executable/private/fault-" + executable + ":$PATH; export PATH; " + initial["args"][0]
            failed = run(["docker", "run", "--rm", "--name", name + "-init", *init_security,
                          "--mount", "type=volume,src=" + name + ",dst=/executable",
                          "--entrypoint", "timeout", image, "30", "/bin/sh", "-ec", command], success=False)
            assert failed.returncode == code
            assert failed.stdout == b""
        mounts = ["--mount", "type=volume,src=" + name + ",dst=/executable,volume-subpath=private,readonly",
                  "--mount", "type=volume,src=" + name + ",dst=/config,volume-subpath=private,readonly",
                  "--tmpfs", "/state:uid=10001,gid=10001,mode=0700,size=16m",
                  "--env", "XDG_CONFIG_HOME=/state/config", "--env", "XDG_DATA_HOME=/state/data"]
        version = run(["docker", "run", "--rm", *runtime_security, *mounts,
                       "--entrypoint", "/executable/caddy", image, "version"])
        assert version.stdout.startswith(b"v2.11.4 ")
        run(["docker", "run", "--rm", *runtime_security, *mounts, "--entrypoint", "/executable/caddy",
             image, "validate", "--config", "/config/Caddyfile", "--adapter", "caddyfile"])
        run(["docker", "run", "-d", "--name", name + "-run", *runtime_security, *mounts,
             "--entrypoint", "/executable/caddy", image, "run", "--config", "/config/Caddyfile", "--adapter", "caddyfile"])
        deadline = time.monotonic() + 10
        listening = False
        while time.monotonic() < deadline:
            observed = json.loads(run(["docker", "inspect", name + "-run"]).stdout)[0]
            assert observed["State"]["Running"] is True
            raw = run(["docker", "exec", name + "-run", "cat", "/proc/net/tcp", "/proc/net/tcp6"]).stdout.decode()
            listening = any(len(line.split()) > 3 and line.split()[1].endswith(":20FB") and line.split()[3] == "0A" for line in raw.splitlines())
            if listening:
                break
            time.sleep(0.1)
        assert listening
        assert observed["HostConfig"]["ReadonlyRootfs"] is True
        assert observed["HostConfig"]["NanoCpus"] == 100_000_000
        assert observed["HostConfig"]["Memory"] == 96 * 1024**2
        assert observed["HostConfig"]["CapDrop"] == ["ALL"]
        assert observed["Config"]["User"] == "10001:10001"
        assert next(m for m in observed["Mounts"] if m["Destination"] == "/executable")["RW"] is False
        run(["docker", "exec", name + "-run", "timeout", "30", "/bin/sh", "-ec", assembly._caddy_verify_command()], timeout=35)
        for executable, code in (("getcap", 41), ("cmp", 42)):
            command = "PATH=/executable/fault-" + executable + ":$PATH; export PATH; " + assembly._caddy_verify_command()
            failed = run(["docker", "exec", name + "-run", "/bin/sh", "-ec", command], success=False)
            assert failed.returncode == code
            assert failed.stdout == b""
    finally:
        for suffix in ("-init", "-run", "-keep"):
            run(["docker", "rm", "-f", name + suffix], timeout=10, success=False)
        leftovers = run(["docker", "ps", "-a", "--filter", "name=" + name, "--format", "{{.Names}}"], timeout=10)
        assert leftovers.stdout.strip() == b""
        if created:
            run(["docker", "volume", "rm", name], timeout=10)
