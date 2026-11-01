"""Tie a retained containerd rootfs to exact prepared OCI bytes, not image aliases."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from datetime import UTC, datetime

VERIFY_TIMEOUT = 30
MAX_METADATA_BYTES = 2 * 1024 * 1024
NODE = "guardrails-evolution"
INDEX_TYPES = frozenset(
    {
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
    }
)
MANIFEST_TYPES = frozenset(
    {
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    }
)
CONFIG_TYPES = frozenset(
    {
        "application/vnd.oci.image.config.v1+json",
        "application/vnd.docker.container.image.v1+json",
    }
)


class ImageIdentityError(RuntimeError):
    """Only static codes may cross the deployment diagnostic boundary."""


def _digest(value):
    if (
        not isinstance(value, str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None
    ):
        raise ImageIdentityError("image_identity_digest_invalid")
    return value


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ImageIdentityError("image_identity_metadata_invalid")
            result[key] = value
        return result

    try:
        value = json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except ValueError, TypeError, RecursionError:
        raise ImageIdentityError("image_identity_metadata_invalid") from None
    if not isinstance(value, dict):
        raise ImageIdentityError("image_identity_metadata_invalid")
    return value


def _descriptor(value, media_types=None):
    if (
        not isinstance(value, dict)
        or type(value.get("size")) is not int
        or value["size"] <= 0
    ):
        raise ImageIdentityError("image_identity_descriptor_invalid")
    if media_types is not None and value.get("mediaType") not in media_types:
        raise ImageIdentityError("image_identity_descriptor_invalid")
    return _digest(value.get("digest"))


def verify_container(deployment, expected_reference, status):
    """Verify OCI config and active overlayfs ancestry for a live ready container."""
    return _verify_container(deployment, expected_reference, status, completed=False)


def verify_completed_init(deployment, expected_reference, status):
    """Verify a successful init using retained CRI/container/rootfs metadata.

    Garbage-collected execution metadata cannot be replaced by Kubernetes image
    strings. Call promptly after completion; missing runtime proof fails closed.
    The running-container verifier retains its separate RUNNING/ready contract.
    """
    return _verify_container(deployment, expected_reference, status, completed=True)


def _completed_declaration(status):
    state = status.get("state")
    terminated = state.get("terminated") if isinstance(state, dict) else None
    if (
        not isinstance(terminated, dict)
        or set(state) != {"terminated"}
        or type(terminated.get("exitCode")) is not int
        or terminated["exitCode"] != 0
        or terminated.get("containerID") != status["containerID"]
        or not isinstance(status.get("name"), str)
        or re.fullmatch(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?", status["name"]) is None
        or len(status["name"]) > 63
    ):
        raise ImageIdentityError("image_identity_init_not_completed")
    times = []
    for field in ("startedAt", "finishedAt"):
        value = terminated.get(field)
        try:
            if not isinstance(value, str) or len(value) > 64:
                raise ValueError()
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None or parsed.timestamp() <= 0:
                raise ValueError()
        except ValueError, OverflowError:
            raise ImageIdentityError("image_identity_init_not_completed") from None
        times.append(parsed)
    if times[1] < times[0]:
        raise ImageIdentityError("image_identity_init_not_completed")


def _nanoseconds(value):
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,19}", value):
        value = int(value)
    elif isinstance(value, str):
        # crictl renders CRI int64 timestamps as RFC3339Nano. Parse seconds and
        # the fractional nanoseconds separately: datetime/float timestamps
        # would silently discard precision used by the second-read check.
        parsed = re.fullmatch(
            r"([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})"
            r"(?:\.([0-9]{1,9}))?(Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])", value
        )
        if parsed is None:
            raise ImageIdentityError("image_identity_init_not_completed")
        try:
            instant = datetime.fromisoformat(parsed[1] + parsed[3])
            elapsed = instant - datetime(1970, 1, 1, tzinfo=UTC)
        except ValueError, OverflowError:
            raise ImageIdentityError("image_identity_init_not_completed") from None
        value = (elapsed.days * 86400 + elapsed.seconds) * 1_000_000_000
        value += int((parsed[2] or "").ljust(9, "0"))
    if type(value) is not int or not 0 < value <= 2**63 - 1:
        raise ImageIdentityError("image_identity_init_not_completed")
    return value


def _verify_container(deployment, expected_reference, status, *, completed):
    """Verify exact OCI config and retained overlayfs ancestry within one budget."""
    if (
        not isinstance(expected_reference, str)
        or len(expected_reference) > 2048
        or re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:/-]*@sha256:[0-9a-f]{64}", expected_reference
        )
        is None
        or not isinstance(status, dict)
        or not completed
        and status.get("ready") is not True
        or not isinstance(status.get("containerID"), str)
        or re.fullmatch(r"containerd://[0-9a-f]{64}", status["containerID"]) is None
    ):
        raise ImageIdentityError("image_identity_container_invalid")
    if completed:
        _completed_declaration(status)
    container_id = status["containerID"].removeprefix("containerd://")
    expected_digest = expected_reference.rsplit("@", 1)[1]
    deadline = time.monotonic() + VERIFY_TIMEOUT

    def command(args):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ImageIdentityError("image_identity_timeout")
        try:
            code, raw = deployment.run(
                ["docker", "exec", NODE, *args], timeout=remaining, check=False
            )
        except TimeoutError, subprocess.TimeoutExpired:
            raise ImageIdentityError("image_identity_timeout") from None
        except Exception:  # noqa: BLE001 - process output never becomes a diagnostic
            raise ImageIdentityError("image_identity_unavailable") from None
        if code != 0 or not isinstance(raw, str):
            raise ImageIdentityError("image_identity_unavailable")
        if len(raw.encode("utf-8")) > MAX_METADATA_BYTES:
            raise ImageIdentityError("image_identity_metadata_too_large")
        if time.monotonic() >= deadline:
            raise ImageIdentityError("image_identity_timeout")
        return raw

    def blob(digest, size=None):
        raw = command(["ctr", "-n=k8s.io", "content", "get", _digest(digest)])
        encoded = raw.encode("utf-8")
        if (
            "sha256:" + hashlib.sha256(encoded).hexdigest() != digest
            or size is not None
            and len(encoded) != size
        ):
            raise ImageIdentityError("image_identity_blob_mismatch")
        return _json(raw)

    manifest_digest = expected_digest
    manifest = blob(expected_digest)
    if manifest.get("mediaType") in INDEX_TYPES:
        entries = manifest.get("manifests")
        if not isinstance(entries, list) or not 1 <= len(entries) <= 256:
            raise ImageIdentityError("image_identity_platform_mismatch")
        matches = [
            entry
            for entry in entries
            if isinstance(entry, dict)
            and isinstance(entry.get("platform"), dict)
            and entry["platform"].get("os") == "linux"
            and entry["platform"].get("architecture") == "amd64"
            and entry["platform"].get("variant", "") == ""
        ]
        if manifest.get("schemaVersion") != 2 or len(matches) != 1:
            raise ImageIdentityError("image_identity_platform_mismatch")
        selected = matches[0]
        manifest_digest = _descriptor(selected, MANIFEST_TYPES)
        manifest = blob(manifest_digest, selected["size"])
        if manifest.get("mediaType") != selected["mediaType"]:
            raise ImageIdentityError("image_identity_descriptor_invalid")
    if (
        manifest.get("schemaVersion") != 2
        or manifest.get("mediaType") not in MANIFEST_TYPES
    ):
        raise ImageIdentityError("image_identity_manifest_invalid")
    config_descriptor = manifest.get("config")
    config_digest = _descriptor(config_descriptor, CONFIG_TYPES)
    config = blob(config_digest, config_descriptor["size"])
    if (
        config.get("os") != "linux"
        or config.get("architecture") != "amd64"
        or config.get("variant", "") != ""
    ):
        raise ImageIdentityError("image_identity_platform_mismatch")
    rootfs = config.get("rootfs")
    if not isinstance(rootfs, dict) or rootfs.get("type") != "layers":
        raise ImageIdentityError("image_identity_rootfs_invalid")
    diff_ids = rootfs.get("diff_ids")
    layers = manifest.get("layers")
    if (
        not isinstance(diff_ids, list)
        or not 1 <= len(diff_ids) <= 512
        or not isinstance(layers, list)
        or len(layers) != len(diff_ids)
    ):
        raise ImageIdentityError("image_identity_rootfs_invalid")
    for layer in layers:
        _descriptor(layer)
    chain_id = _digest(diff_ids[0])
    for diff_id in diff_ids[1:]:
        chain_id = (
            "sha256:"
            + hashlib.sha256((chain_id + " " + _digest(diff_id)).encode()).hexdigest()
        )

    def live_status():
        value = _json(
            command(["crictl", "inspect", "--output=json", container_id])
        ).get("status")
        if completed:
            if (
                not isinstance(value, dict)
                or value.get("id") != container_id
                or value.get("state") != "CONTAINER_EXITED"
                or type(value.get("exitCode")) is not int
                or value["exitCode"] != 0
                or not isinstance(value.get("metadata"), dict)
                or value["metadata"].get("name") != status["name"]
            ):
                raise ImageIdentityError("image_identity_init_not_completed")
            started = _nanoseconds(value.get("startedAt"))
            finished = _nanoseconds(value.get("finishedAt"))
            if finished < started:
                raise ImageIdentityError("image_identity_init_not_completed")
            completion = (started, finished)
        else:
            if (
                not isinstance(value, dict)
                or value.get("id") != container_id
                or value.get("state") != "CONTAINER_RUNNING"
            ):
                raise ImageIdentityError("image_identity_container_not_running")
            completion = None
        ref = value.get("imageRef")
        if (
            not isinstance(ref, str)
            or len(ref) > 2048
            or re.fullmatch(
                r"(?:[A-Za-z0-9][A-Za-z0-9._:/-]*@)?sha256:[0-9a-f]{64}", ref
            )
            is None
        ):
            raise ImageIdentityError("image_identity_config_mismatch")
        return ref, completion

    actual_reference, completion = live_status()
    actual_image = _json(
        command(["crictl", "inspecti", "--output=json", actual_reference])
    ).get("status")
    if not isinstance(actual_image, dict) or actual_image.get("id") != config_digest:
        raise ImageIdentityError("image_identity_config_mismatch")
    container = _json(command(["ctr", "-n=k8s.io", "containers", "info", container_id]))
    if (
        container.get("ID") != container_id
        or container.get("SnapshotKey") != container_id
        or container.get("Snapshotter") != "overlayfs"
    ):
        raise ImageIdentityError("image_identity_snapshot_mismatch")
    snapshot = _json(
        command(
            [
                "ctr",
                "-n=k8s.io",
                "snapshots",
                "--snapshotter",
                "overlayfs",
                "info",
                container_id,
            ]
        )
    )
    if (
        snapshot.get("Name") != container_id
        or snapshot.get("Kind") != "Active"
        or snapshot.get("Parent") != chain_id
    ):
        raise ImageIdentityError("image_identity_snapshot_mismatch")
    if live_status() != (actual_reference, completion):
        raise ImageIdentityError("image_identity_container_changed")
    return {
        "container_id": container_id,
        "expected_reference": expected_reference,
        "manifest_digest": manifest_digest,
        "config_digest": config_digest,
        "rootfs_chain_id": chain_id,
        **(
            {"completed": True, "exit_code": 0, "finished_at_ns": completion[1]}
            if completed
            else {"ready": True}
        ),
    }
