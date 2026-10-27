"""Verify the offline node's current source against its immutable local image.

This does not refresh preparation or cold-boot records. The only container it
starts replaces the node entrypoint with an unprivileged checksum command.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import time
from pathlib import Path
from uuid import uuid4

VERIFY_TIMEOUT = 30
CLEANUP_TIMEOUT = 5
ENTRYPOINT = ["/usr/local/bin/evolution-entrypoint", "/sbin/init"]
IMAGE_FILES = {
    "entrypoint": "/usr/local/bin/evolution-entrypoint",
    "offline.nft": "/usr/local/share/evolution-offline.nft",
}


class NodeIntegrityError(RuntimeError):
    """Static diagnostic only; never retain a Docker response or source text."""


def _source(root, relative):
    path = root / relative
    if any(parent.is_symlink() for parent in (path, *path.parents) if parent != root):
        raise NodeIntegrityError("node_source_invalid")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 65536:
                raise NodeIntegrityError("node_source_invalid")
            value = stream.read(65537)
            if len(value) != info.st_size:
                raise NodeIntegrityError("node_source_invalid")
            return value
    except OSError:
        raise NodeIntegrityError("node_source_invalid") from None


def _reference(item):
    if (
        not isinstance(item, dict)
        or item.get("kind") != "image"
        or not isinstance(item.get("sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) is None
        or not isinstance(item.get("source_url"), str)
        or not item["source_url"].startswith("docker://")
        or not item["source_url"].endswith("@sha256:" + item["sha256"])
    ):
        raise NodeIntegrityError("node_image_pin_invalid")
    return item["source_url"].removeprefix("docker://")


def verify_inputs(deployment):
    """Require exact recipe, pinned base ancestry, entrypoint and source bytes."""
    deadline = time.monotonic() + VERIFY_TIMEOUT

    def run(argv):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise NodeIntegrityError("node_verification_timeout")
        try:
            code, output = deployment.run(argv, timeout=remaining, check=False)
        except TimeoutError, subprocess.TimeoutExpired:
            raise NodeIntegrityError("node_verification_timeout") from None
        except Exception:  # noqa: BLE001 - process diagnostics must remain private
            raise NodeIntegrityError("node_verification_unavailable") from None
        if code != 0:
            raise NodeIntegrityError("node_verification_unavailable")
        return output

    try:
        images = deployment.specs()["images"]
        selected = {}
        for name in ("kicbase", "kicbase-offline"):
            matches = [item for item in images if item.get("name") == name]
            if len(matches) != 1:
                raise NodeIntegrityError("node_image_pin_invalid")
            selected[name] = matches[0]
    except NodeIntegrityError:
        raise
    except Exception:  # noqa: BLE001 - deployment errors become finite node codes
        raise NodeIntegrityError("node_image_pin_invalid") from None
    base_ref, node_ref = (_reference(selected[name]) for name in selected)
    root = Path(deployment.root)
    recipe = _source(root, "deploy/evolution/images/node.Dockerfile")
    expected_recipe = (
        "FROM " + base_ref + "\n"
        "COPY --chmod=0444 offline.nft /usr/local/share/evolution-offline.nft\n"
        "COPY --chmod=0555 entrypoint /usr/local/bin/evolution-entrypoint\n"
        'ENTRYPOINT ["/usr/local/bin/evolution-entrypoint", "/sbin/init"]\n'
    ).encode()
    if recipe != expected_recipe:
        raise NodeIntegrityError("node_recipe_changed")
    sources = {
        image_path: hashlib.sha256(
            _source(root, "deploy/evolution/images/node/" + name)
        ).hexdigest()
        for name, image_path in IMAGE_FILES.items()
    }
    inspected = []
    for name, reference in (("kicbase", base_ref), ("kicbase-offline", node_ref)):
        try:
            values = json.loads(run(["docker", "image", "inspect", reference]))
            if not isinstance(values, list) or len(values) != 1:
                raise ValueError
            value = values[0]
            pin = "sha256:" + selected[name]["sha256"]
            if value["Id"] != pin and not any(
                digest.endswith("@" + pin) for digest in value.get("RepoDigests", [])
            ):
                raise NodeIntegrityError("node_image_pin_mismatch")
            if value["Size"] != selected[name]["size_bytes"]:
                raise NodeIntegrityError("node_image_pin_mismatch")
            layers = value["RootFS"]["Layers"]
            if (
                value["RootFS"]["Type"] != "layers"
                or not isinstance(layers, list)
                or not layers
            ):
                raise ValueError
            if any(
                not isinstance(layer, str)
                or re.fullmatch(r"sha256:[0-9a-f]{64}", layer) is None
                for layer in layers
            ):
                raise ValueError
            inspected.append(value)
        except NodeIntegrityError:
            raise
        except KeyError, TypeError, ValueError, AttributeError:
            raise NodeIntegrityError("node_image_metadata_invalid") from None
    base, node = inspected
    base_layers, node_layers = base["RootFS"]["Layers"], node["RootFS"]["Layers"]
    if (
        len(node_layers) != len(base_layers) + 2
        or node_layers[: len(base_layers)] != base_layers
    ):
        raise NodeIntegrityError("node_image_ancestry_mismatch")
    if (
        not isinstance(node.get("Config"), dict)
        or node["Config"].get("Entrypoint") != ENTRYPOINT
    ):
        raise NodeIntegrityError("node_image_entrypoint_mismatch")

    container = "evolution-node-integrity-" + uuid4().hex
    try:
        raw = run(
            [
                "docker",
                "run",
                "--rm",
                "--pull=never",
                "--name",
                container,
                "--network=none",
                "--read-only",
                "--user",
                "65534:65534",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--log-driver=none",
                "--memory=64m",
                "--memory-swap=64m",
                "--pids-limit=16",
                "--entrypoint",
                "/usr/bin/sha256sum",
                node_ref,
                *sources,
            ]
        )
        observed = {}
        for line in raw.splitlines():
            match = re.fullmatch(r"([0-9a-f]{64})  (/[^\s]+)", line)
            if match is None or match[2] in observed:
                raise NodeIntegrityError("node_image_response_invalid")
            observed[match[2]] = match[1]
        if observed != sources:
            raise NodeIntegrityError("node_image_source_mismatch")
    finally:
        # Timeout may kill the Docker client while leaving its container alive.
        # Cleanup has its own finite budget and only touches our random name.
        cleanup_deadline = time.monotonic() + CLEANUP_TIMEOUT
        try:
            deployment.run(
                ["docker", "rm", "--force", container],
                timeout=cleanup_deadline - time.monotonic(),
                check=False,
            )
            remaining = cleanup_deadline - time.monotonic()
            if remaining <= 0:
                raise NodeIntegrityError("node_verification_cleanup_failed")
            code, present = deployment.run(
                [
                    "docker",
                    "ps",
                    "--all",
                    "--filter",
                    "name=^/" + container + "$",
                    "--format",
                    "{{.Names}}",
                ],
                timeout=remaining,
                check=False,
            )
            if code != 0 or present.strip():
                raise NodeIntegrityError("node_verification_cleanup_failed")
        except Exception:  # noqa: BLE001 - failed cleanup cannot publish success
            raise NodeIntegrityError("node_verification_cleanup_failed") from None
