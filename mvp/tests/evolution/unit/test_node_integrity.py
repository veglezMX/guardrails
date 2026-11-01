"""Pin/source closure using real local images and adversarial process responses.

The Docker response port is explicit in metadata/deadline cases. File mismatch
and positive cases execute the actual pinned image, without its node wrapper.
"""

import copy
import hashlib
import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]


def load(name):
    spec = importlib.util.spec_from_file_location(
        "node_integrity_test_" + name, ROOT / "deploy/evolution" / (name + ".py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


integrity = load("node_integrity")
bootstrap = load("bootstrap")


class LocalImages:
    """Real Docker command port; no prepared state or cluster mutation methods."""

    def __init__(self, root):
        self.root = root
        self.calls = []
        declared = json.loads(
            (ROOT / "deploy/evolution/preparation.lock.json").read_bytes()
        )
        self.images = [
            item
            for item in declared["images"]
            if item["name"] in {"kicbase", "kicbase-offline"}
        ]
        assert len(self.images) == 2, (
            "Immutable local node/base images must be prepared"
        )

    def specs(self):
        return {"images": self.images}

    def run(self, argv, *, timeout, check):
        self.calls.append((list(argv), timeout, check))
        return bootstrap.command(argv, timeout=timeout, check=check)


@pytest.fixture
def deployment(tmp_path):
    sources = tmp_path / "deploy/evolution/images"
    sources.mkdir(parents=True)
    shutil.copyfile(
        ROOT / "deploy/evolution/images/node.Dockerfile", sources / "node.Dockerfile"
    )
    shutil.copytree(ROOT / "deploy/evolution/images/node", sources / "node")
    return LocalImages(tmp_path)


def snapshot(root):
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_actual_pinned_node_matches_sources_without_changing_cold_or_prepared_records(
    deployment,
):
    state = deployment.root / ".evolution/deployment"
    state.mkdir(parents=True)
    (state / "prepared.json").write_bytes(b'{"prepared_digest":"original-preparation"}')
    (state / "cold-start.json").write_bytes(
        b'{"prepared_digest":"original-cold-proof"}'
    )
    before = snapshot(deployment.root)
    assert integrity.verify_inputs(deployment) is None
    assert snapshot(deployment.root) == before
    calls = deployment.calls
    assert len(calls) == 5
    assert all(0 < timeout <= 30 and check is False for _, timeout, check in calls)
    run = calls[2][0]
    assert run[:2] == ["docker", "run"]
    for flag in (
        "--pull=never",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--log-driver=none",
    ):
        assert flag in run
    assert run[run.index("--user") + 1] == "65534:65534"
    assert run[run.index("--entrypoint") + 1] == "/usr/bin/sha256sum"
    assert calls[3][0] == ["docker", "rm", "--force", run[run.index("--name") + 1]]
    assert 0 < calls[4][1] <= calls[3][1] <= integrity.CLEANUP_TIMEOUT
    assert calls[4][0][:3] == ["docker", "ps", "--all"]
    assert "name=^/" + run[run.index("--name") + 1] + "$" in calls[4][0]
    assert not any("pull" in argv or "build" in argv for argv, _, _ in calls)


@pytest.mark.parametrize("name", ["entrypoint", "offline.nft"])
def test_actual_pinned_image_rejects_changed_node_source_bytes(deployment, name):
    path = deployment.root / "deploy/evolution/images/node" / name
    path.write_bytes(path.read_bytes() + b"\n# changed after preparation\n")
    changed = snapshot(deployment.root)
    with pytest.raises(
        integrity.NodeIntegrityError, match="^node_image_source_mismatch$"
    ):
        integrity.verify_inputs(deployment)
    assert snapshot(deployment.root) == changed
    assert len(deployment.calls) == 5
    assert deployment.calls[-2][0][:3] == ["docker", "rm", "--force"]


@pytest.mark.parametrize(
    "change", ["extra_run", "mutable_base", "copy_mode", "copy_target", "entrypoint"]
)
def test_changed_recipe_is_rejected_before_any_container_command(deployment, change):
    path = deployment.root / "deploy/evolution/images/node.Dockerfile"
    recipe = path.read_text()
    if change == "extra_run":
        recipe += "RUN true\n"
    elif change == "mutable_base":
        recipe = recipe.replace(recipe.splitlines()[0], "FROM kicbase:latest")
    elif change == "copy_mode":
        recipe = recipe.replace("--chmod=0555", "--chmod=0777")
    elif change == "copy_target":
        recipe = recipe.replace(
            "/usr/local/share/evolution-offline.nft", "/tmp/offline.nft"
        )
    else:
        recipe = recipe.replace('"/sbin/init"', '"/bin/sh"')
    path.write_text(recipe)
    with pytest.raises(integrity.NodeIntegrityError, match="^node_recipe_changed$"):
        integrity.verify_inputs(deployment)
    assert path.read_text() == recipe
    assert deployment.calls == []


@pytest.mark.parametrize(
    "name", ["node.Dockerfile", "node/entrypoint", "node/offline.nft"]
)
def test_source_symlink_is_refused_before_image_execution(deployment, name):
    path = deployment.root / "deploy/evolution/images" / name
    original = path.read_bytes()
    outside = deployment.root / "outside-source"
    outside.write_bytes(original)
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises(integrity.NodeIntegrityError, match="^node_source_invalid$"):
        integrity.verify_inputs(deployment)
    assert path.is_symlink() and outside.read_bytes() == original
    assert deployment.calls == []


def test_actual_base_image_cannot_substitute_for_offline_derivative(deployment):
    base = next(item for item in deployment.images if item["name"] == "kicbase")
    deployment.images = [base, dict(base, name="kicbase-offline")]
    with pytest.raises(
        integrity.NodeIntegrityError, match="^node_image_ancestry_mismatch$"
    ):
        integrity.verify_inputs(deployment)
    assert len(deployment.calls) == 2
    assert all(
        argv[:3] == ["docker", "image", "inspect"] for argv, _, _ in deployment.calls
    )


@pytest.fixture(scope="module")
def actual_image_metadata():
    images = LocalImages(ROOT).images
    values = {}
    for item in images:
        reference = item["source_url"].removeprefix("docker://")
        code, output = bootstrap.command(
            ["docker", "image", "inspect", reference], timeout=15
        )
        assert code == 0
        values[reference] = json.loads(output)
    return values


class InspectionResponsePort:
    """Finite injected process failures based on actual image-inspect records."""

    def __init__(self, deployment, metadata, *, fault):
        self.root, self.specs = deployment.root, deployment.specs
        self.responses = copy.deepcopy(metadata)
        self.calls = []
        self.fault = fault
        node = next(
            item for item in deployment.images if item["name"] == "kicbase-offline"
        )
        response = self.responses[node["source_url"].removeprefix("docker://")][0]
        if fault == "base_prefix":
            response["RootFS"]["Layers"][0] = "sha256:" + "a" * 64
        elif fault == "extra_layer":
            response["RootFS"]["Layers"].append("sha256:" + "b" * 64)
        elif fault == "entrypoint":
            response["Config"]["Entrypoint"] = ["/sbin/init"]
        elif fault == "identity":
            response["Id"], response["RepoDigests"] = "sha256:" + "c" * 64, []

    def run(self, argv, *, timeout, check):
        self.calls.append((list(argv), timeout, check))
        if argv[:3] == ["docker", "image", "inspect"]:
            return 0, json.dumps(self.responses[argv[-1]])
        if argv[:2] == ["docker", "run"]:
            if self.fault == "timeout":
                raise subprocess.TimeoutExpired(
                    argv, timeout, output=b"private command response"
                )
            if self.fault == "failure":
                return 1, "private command response"
            if self.fault == "cleanup":
                directory = self.root / "deploy/evolution/images/node"
                return 0, "".join(
                    hashlib.sha256((directory / name).read_bytes()).hexdigest()
                    + "  "
                    + target
                    + "\n"
                    for name, target in (
                        ("entrypoint", "/usr/local/bin/evolution-entrypoint"),
                        ("offline.nft", "/usr/local/share/evolution-offline.nft"),
                    )
                )
            return 0, "invalid checksum response with private content"
        if argv[:3] == ["docker", "ps", "--all"]:
            return (
                (0, "evolution-node-integrity-leftover")
                if self.fault == "cleanup"
                else (0, "")
            )
        assert argv[:3] == ["docker", "rm", "--force"]
        return 0, ""


@pytest.mark.parametrize(
    "fault,code",
    [
        ("base_prefix", "node_image_ancestry_mismatch"),
        ("extra_layer", "node_image_ancestry_mismatch"),
        ("entrypoint", "node_image_entrypoint_mismatch"),
        ("identity", "node_image_pin_mismatch"),
    ],
)
def test_adversarial_image_metadata_is_rejected_before_container_start(
    deployment, actual_image_metadata, fault, code
):
    port = InspectionResponsePort(deployment, actual_image_metadata, fault=fault)
    with pytest.raises(integrity.NodeIntegrityError, match="^" + code + "$"):
        integrity.verify_inputs(port)
    assert all(argv[:3] == ["docker", "image", "inspect"] for argv, _, _ in port.calls)
    assert len(port.calls) == 2


@pytest.mark.parametrize(
    "fault,code",
    [
        ("timeout", "node_verification_timeout"),
        ("failure", "node_verification_unavailable"),
        ("malformed", "node_image_response_invalid"),
        ("cleanup", "node_verification_cleanup_failed"),
    ],
)
def test_failed_transient_checks_have_bounded_owned_cleanup_and_safe_errors(
    deployment, actual_image_metadata, fault, code
):
    port = InspectionResponsePort(deployment, actual_image_metadata, fault=fault)
    with pytest.raises(
        integrity.NodeIntegrityError, match="^" + code + "$"
    ) as rejected:
        integrity.verify_inputs(port)
    assert str(rejected.value) == code
    assert len(port.calls) == 5
    command = port.calls[-3][0]
    name = command[command.index("--name") + 1]
    assert name.startswith("evolution-node-integrity-")
    assert port.calls[-2][0] == ["docker", "rm", "--force", name]
    assert 0 < port.calls[-1][1] <= port.calls[-2][1] <= integrity.CLEANUP_TIMEOUT
    assert port.calls[-1][0] == [
        "docker",
        "ps",
        "--all",
        "--filter",
        "name=^/" + name + "$",
        "--format",
        "{{.Names}}",
    ]
