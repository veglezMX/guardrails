"""OCI hash-chain and live-snapshot checks through an explicit read-only CRI port."""

import copy
import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
spec = importlib.util.spec_from_file_location(
    "image_identity_test", ROOT / "deploy/evolution/image_identity.py"
)
identity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(identity)
CID = "c" * 64
ALIAS = "registry.example/node@sha256:" + "d" * 64
CHAIN = "sha256:ccd722928bd92476ba1745586fed6e45a102504185ad88cd89e01ff116fd146c"


class ContentStore:
    """In-process content/CRI response fixture; no real-cluster claim is made."""

    def __init__(self, *, index=False, platform="amd64", index_copies=1):
        self.blobs, self.calls = {}, []
        config = {
            "architecture": platform,
            "os": "linux",
            "rootfs": {
                "type": "layers",
                "diff_ids": ["sha256:" + "a" * 64, "sha256:" + "b" * 64],
            },
        }
        self.config = self.add(config, "application/vnd.oci.image.config.v1+json")
        manifest = {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": self.config,
            "layers": [
                {
                    "digest": "sha256:" + "1" * 64,
                    "size": 123,
                    "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
                },
                {
                    "digest": "sha256:" + "2" * 64,
                    "size": 456,
                    "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
                },
            ],
        }
        self.manifest = self.add(manifest, manifest["mediaType"])
        self.descriptor = self.manifest
        if index:
            entry = dict(
                self.manifest, platform={"os": "linux", "architecture": platform}
            )
            value = {
                "schemaVersion": 2,
                "mediaType": "application/vnd.oci.image.index.v1+json",
                "manifests": [entry for _ in range(index_copies)],
            }
            self.descriptor = self.add(value, value["mediaType"])
        self.expected = "registry.example/node@" + self.descriptor["digest"]
        self.status = {
            "containerID": "containerd://" + CID,
            "ready": True,
            "imageID": ALIAS,
        }
        self.live = {
            "status": {"id": CID, "state": "CONTAINER_RUNNING", "imageRef": ALIAS}
        }
        self.image = {
            "status": {
                "id": self.config["digest"],
                "repoDigests": [self.expected, ALIAS],
            }
        }
        self.container = {
            "ID": CID,
            "Image": "registry.example/node:old-mutable-tag",
            "SnapshotKey": CID,
            "Snapshotter": "overlayfs",
        }
        self.snapshot = {"Name": CID, "Kind": "Active", "Parent": CHAIN}
        self.live_reads = 0
        self.stop_on_final = False
        self.timeout = False

    def add(self, value, media_type):
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"))
        digest = "sha256:" + hashlib.sha256(raw.encode()).hexdigest()
        self.blobs[digest] = raw
        return {"digest": digest, "size": len(raw.encode()), "mediaType": media_type}

    def run(self, argv, *, timeout, check):
        self.calls.append((list(argv), timeout, check))
        assert argv[:3] == ["docker", "exec", "guardrails-evolution"]
        assert 0 < timeout <= identity.VERIFY_TIMEOUT and check is False
        if self.timeout:
            raise subprocess.TimeoutExpired(
                argv, timeout, output=b"private driver details"
            )
        args = argv[3:]
        if args[:4] == ["ctr", "-n=k8s.io", "content", "get"]:
            return (
                (0, self.blobs[args[4]])
                if args[4] in self.blobs
                else (1, "private missing-blob diagnostics")
            )
        if args == ["crictl", "inspect", "--output=json", CID]:
            self.live_reads += 1
            value = copy.deepcopy(self.live)
            if self.stop_on_final and self.live_reads == 2:
                value["status"]["state"] = "CONTAINER_EXITED"
            return 0, json.dumps(value)
        if args[:3] == ["crictl", "inspecti", "--output=json"]:
            assert args[3] == self.live["status"]["imageRef"]
            return 0, json.dumps(self.image)
        if args == ["ctr", "-n=k8s.io", "containers", "info", CID]:
            return 0, json.dumps(self.container)
        assert args == [
            "ctr",
            "-n=k8s.io",
            "snapshots",
            "--snapshotter",
            "overlayfs",
            "info",
            CID,
        ]
        return 0, json.dumps(self.snapshot)


@pytest.mark.parametrize("index", [False, True])
def test_live_alias_requires_matching_config_and_active_rootfs_chain(index):
    store = ContentStore(index=index)
    result = identity.verify_container(store, store.expected, store.status)
    assert result == {
        "container_id": CID,
        "expected_reference": store.expected,
        "manifest_digest": store.manifest["digest"],
        "config_digest": store.config["digest"],
        "rootfs_chain_id": CHAIN,
        "ready": True,
    }
    assert store.status["imageID"] != store.expected
    assert store.container["Image"] != store.expected
    assert store.live_reads == 2
    assert len(store.calls) == (8 if index else 7)
    assert all(
        argv[:3] == ["docker", "exec", "guardrails-evolution"]
        for argv, _, _ in store.calls
    )


def test_named_expected_alias_cannot_authorize_a_foreign_config():
    store = ContentStore()
    store.image["status"]["id"] = "sha256:" + "e" * 64
    assert store.expected in store.image["status"]["repoDigests"]
    with pytest.raises(
        identity.ImageIdentityError, match="^image_identity_config_mismatch$"
    ):
        identity.verify_container(store, store.expected, store.status)
    assert store.live_reads == 1
    assert not any("snapshots" in args for args, _, _ in store.calls)


@pytest.mark.parametrize("target", ["index", "manifest", "config"])
@pytest.mark.parametrize("fault", ["missing", "corrupt"])
def test_missing_or_corrupted_blob_breaks_exact_digest_chain(target, fault):
    store = ContentStore(index=True)
    descriptor = {
        "index": store.descriptor,
        "manifest": store.manifest,
        "config": store.config,
    }[target]
    if fault == "missing":
        del store.blobs[descriptor["digest"]]
        code = "image_identity_unavailable"
    else:
        store.blobs[descriptor["digest"]] += "\n"
        code = "image_identity_blob_mismatch"
    with pytest.raises(identity.ImageIdentityError, match="^" + code + "$"):
        identity.verify_container(store, store.expected, store.status)
    assert store.live_reads == 0


@pytest.mark.parametrize("index", [False, True])
def test_non_amd64_platform_is_rejected_even_with_valid_content_hashes(index):
    store = ContentStore(index=index, platform="arm64")
    with pytest.raises(
        identity.ImageIdentityError, match="^image_identity_platform_mismatch$"
    ):
        identity.verify_container(store, store.expected, store.status)
    assert store.live_reads == 0


def test_multiple_matching_platform_manifests_are_ambiguous():
    store = ContentStore(index=True, index_copies=2)
    with pytest.raises(
        identity.ImageIdentityError, match="^image_identity_platform_mismatch$"
    ):
        identity.verify_container(store, store.expected, store.status)
    assert len(store.calls) == 1


def test_descriptor_size_must_match_hash_checked_config_bytes():
    store = ContentStore()
    manifest = json.loads(store.blobs[store.manifest["digest"]])
    manifest["config"]["size"] += 1
    descriptor = store.add(manifest, manifest["mediaType"])
    with pytest.raises(
        identity.ImageIdentityError, match="^image_identity_blob_mismatch$"
    ):
        identity.verify_container(
            store, "registry.example/node@" + descriptor["digest"], store.status
        )
    assert store.live_reads == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("Parent", "sha256:" + "f" * 64),
        ("Name", "another-container"),
        ("Kind", "Committed"),
    ],
)
def test_active_snapshot_must_belong_to_container_and_exact_layer_chain(field, value):
    store = ContentStore()
    store.snapshot[field] = value
    with pytest.raises(
        identity.ImageIdentityError, match="^image_identity_snapshot_mismatch$"
    ):
        identity.verify_container(store, store.expected, store.status)
    assert store.image["status"]["id"] == store.config["digest"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("SnapshotKey", "another-container"),
        ("Snapshotter", "native"),
        ("ID", "another-container"),
    ],
)
def test_container_metadata_cannot_substitute_another_rootfs(field, value):
    store = ContentStore()
    store.container[field] = value
    with pytest.raises(
        identity.ImageIdentityError, match="^image_identity_snapshot_mismatch$"
    ):
        identity.verify_container(store, store.expected, store.status)
    assert not any("snapshots" in args for args, _, _ in store.calls)


@pytest.mark.parametrize("when", ["initial", "final"])
def test_stopped_container_never_returns_verified_identity(when):
    store = ContentStore()
    if when == "initial":
        store.live["status"]["state"] = "CONTAINER_EXITED"
    else:
        store.stop_on_final = True
    with pytest.raises(
        identity.ImageIdentityError, match="^image_identity_container_not_running$"
    ):
        identity.verify_container(store, store.expected, store.status)
    assert store.live_reads == (1 if when == "initial" else 2)


@pytest.mark.parametrize(
    "field,value",
    [
        ("ready", False),
        ("containerID", "containerd://short"),
        ("containerID", "docker://" + CID),
    ],
)
def test_unready_or_invalid_container_reference_is_rejected_before_io(field, value):
    store = ContentStore()
    store.status[field] = value
    with pytest.raises(
        identity.ImageIdentityError, match="^image_identity_container_invalid$"
    ):
        identity.verify_container(store, store.expected, store.status)
    assert store.calls == []


def test_process_deadline_failure_is_finite_and_does_not_expose_output():
    store = ContentStore()
    store.timeout = True
    with pytest.raises(
        identity.ImageIdentityError, match="^image_identity_timeout$"
    ) as rejected:
        identity.verify_container(store, store.expected, store.status)
    assert str(rejected.value) == "image_identity_timeout"
    assert len(store.calls) == 1
