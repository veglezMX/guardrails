"""Owned, staged local platform assembled through real governance and receivers."""

from __future__ import annotations

import base64
import copy
import hashlib
import ipaddress
import json
import os
import re
import secrets
import signal
import socket
import ssl
import stat
import subprocess
import time
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from evolution import seed
from guardrails.evolution.models import canonical_json_bytes, strict_json_loads
from sqlalchemy.engine import URL


class AssemblyError(RuntimeError):
    """Finite public failure code, never subprocess output or secret bytes."""


SERVICES = {
    "postgres": ("gr-platform", 5432),
    "management": ("gr-platform", 8443),
    "runtime": ("gr-platform", 8443),
    "mock": ("gr-model", 8443),
    "ollama": ("gr-model", 8443),
    "team-a": ("gr-team-a", 8443),
    "team-b": ("gr-team-b", 8443),
}


RESOURCE_BUDGETS = {
    "postgres": ("500m", "512Mi"),
    "management": ("500m", "512Mi"),
    "runtime": ("1000m", "512Mi"),
    "gateway": ("750m", "1024Mi"),
    "mock": ("200m", "512Mi"),
    "ollama": ("950m", "1536Mi"),
    "classifier": ("2000m", "3072Mi"),
}

MODEL_ENV = {
    "OLLAMA_HOST": "127.0.0.1:11434",
    "OLLAMA_MODELS": "/models",
    "OLLAMA_NOPRUNE": "true",
    "OLLAMA_NUM_PARALLEL": "1",
    "OLLAMA_MAX_LOADED_MODELS": "1",
    "OLLAMA_KEEP_ALIVE": "30m",
    "OLLAMA_CONTEXT_LENGTH": "2048",
    "HOME": "/state",
}

# TCP always requires both TLS and the role's SCRAM password, including the
# loopback relay. Only the declared in-pod administrator may use a Unix socket.
POSTGRES_HBA = (
    b"local all evolution_owner trust\n"
    b"local all all reject\n"
    b"hostnossl all all 0.0.0.0/0 reject\n"
    b"hostnossl all all ::/0 reject\n"
    b"hostssl all all 0.0.0.0/0 scram-sha-256\n"
    b"hostssl all all ::/0 scram-sha-256\n"
)
_POSTGRES_OLD_ARGS = [
    "postgres",
    "-c",
    "ssl=on",
    "-c",
    "ssl_cert_file=/config/tls.crt",
    "-c",
    "ssl_key_file=/config/tls.key",
    "-c",
    "log_statement=none",
    "-c",
    "log_min_error_statement=panic",
]
POSTGRES_ARGS = _POSTGRES_OLD_ARGS + ["-c", "hba_file=/config/pg_hba.conf"]
POSTGRES_READINESS = [
    "pg_isready",
    "-h",
    "/var/run/postgresql",
    "-U",
    "evolution_owner",
    "-d",
    "guardrails_evolution",
]


def _postgres_files(deployment, directory):
    target = directory / "tls/gr-platform/postgres"
    return {
        **{
            key: seed.private_read(target / key)
            for key in ("tls.crt", "tls.key", "ca.crt")
        },
        "owner-password": seed.private_read(directory / "private/database-owner.token"),
        "postgres_tunnel.py": (
            deployment.root / "deploy/evolution/postgres_tunnel.py"
        ).read_bytes(),
    }


def _postgres_configuration(deployment, owner, files):
    """Add HBA only to an exact owned prior Secret, using Kubernetes CAS."""
    if set(files) != {
        "tls.crt",
        "tls.key",
        "ca.crt",
        "owner-password",
        "postgres_tunnel.py",
    }:
        raise AssemblyError("postgres_private_file_inventory_invalid")
    previous = {
        key: base64.b64encode(value).decode() for key, value in sorted(files.items())
    }
    desired = {**previous, "pg_hba.conf": base64.b64encode(POSTGRES_HBA).decode()}
    _, raw = deployment.kubectl(
        "get",
        "secret",
        "postgres-config",
        "--ignore-not-found",
        "-o",
        "json",
        namespace="gr-platform",
    )
    if not raw.strip():
        document = {
            "apiVersion": "v1",
            "kind": "Secret",
            "type": "Opaque",
            "metadata": {
                "name": "postgres-config",
                "namespace": "gr-platform",
                "labels": {"guardrails.evolution/owner": owner["owner_id"]},
            },
            "data": desired,
        }
        deployment.kubectl(
            "create",
            "-f",
            "-",
            namespace="gr-platform",
            input_text=json.dumps(document),
        )
    else:
        current = json.loads(raw)
        metadata = current["metadata"]
        if (
            metadata.get("name") != "postgres-config"
            or metadata.get("namespace") != "gr-platform"
            or metadata.get("labels", {}).get("guardrails.evolution/owner")
            != owner["owner_id"]
            or current.get("type") != "Opaque"
        ):
            raise AssemblyError("postgres_secret_owner_mismatch")
        if current.get("data") not in (previous, desired):
            raise AssemblyError("postgres_secret_bytes_mismatch")
        if current["data"] != desired:
            if not metadata.get("uid") or not metadata.get("resourceVersion"):
                raise AssemblyError("postgres_secret_identity_missing")
            operations = [
                {"op": "test", "path": "/metadata/uid", "value": metadata["uid"]},
                {
                    "op": "test",
                    "path": "/metadata/resourceVersion",
                    "value": metadata["resourceVersion"],
                },
                {"op": "test", "path": "/metadata/labels", "value": metadata["labels"]},
                {"op": "test", "path": "/data", "value": previous},
                {"op": "replace", "path": "/data", "value": desired},
            ]
            deployment.kubectl(
                "patch",
                "secret",
                "postgres-config",
                "--type=json",
                "--patch-file=/dev/stdin",
                namespace="gr-platform",
                input_text=json.dumps(operations),
            )
    return hashlib.sha256(canonical_json_bytes(desired)).hexdigest()


def _postgres_security(deployment, directory, values, owner):
    """Migrate an existing database's transport settings without reseeding."""
    row = values["components"]["postgres"]
    if row.get("args") not in (_POSTGRES_OLD_ARGS, POSTGRES_ARGS):
        raise AssemblyError("postgres_startup_settings_unexpected")
    revision = _postgres_configuration(
        deployment, owner, _postgres_files(deployment, directory)
    )
    desired = {
        "args": POSTGRES_ARGS,
        "readinessCommand": POSTGRES_READINESS,
        "configRevision": revision,
    }
    changed = any(row.get(key) != value for key, value in desired.items())
    row.update(desired)
    return changed


def _postgres_settings_match(row, template):
    containers = template.get("spec", {}).get("containers", [])
    app = next((item for item in containers if item.get("name") == "postgres"), {})
    return (
        app.get("args") == POSTGRES_ARGS
        and app.get("readinessProbe", {}).get("exec", {}).get("command")
        == POSTGRES_READINESS
        and template.get("metadata", {})
        .get("annotations", {})
        .get("guardrails.evolution/config-revision")
        == row["configRevision"]
    )


def _directory(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if (
        path.is_symlink()
        or path.stat().st_uid != os.getuid()
        or path.stat().st_mode & 0o077
    ):
        raise AssemblyError("assembly_directory_not_private")
    return path


def _write(path, value):
    raw = value if isinstance(value, bytes) else canonical_json_bytes(value)
    if path.exists():
        if seed.private_read(path) == raw:
            return
        seed.private_write(path, raw, replace=True)
    else:
        seed.private_write(path, raw)


def _secret_file(path):
    if not path.exists():
        seed.private_write(path, secrets.token_urlsafe(48).encode())
    return seed.private_read(path, maximum=4096).decode().strip()


def _state(deployment):
    owner = deployment.owner()
    directory = _directory(deployment.state / "assembly")
    path = directory / "owner.json"
    expected = {
        "owner_id": owner["owner_id"],
        "container_id": owner["container_id"],
        "state": str(deployment.state),
        "uid": os.getuid(),
    }
    if path.exists() and deployment.read_json(path) != expected:
        raise AssemblyError("assembly_owner_mismatch")
    if not path.exists():
        deployment.write_json(path, expected)
    return directory, owner


def _port_record(deployment, directory):
    path = directory / "ports.json"
    if path.exists():
        ports = deployment.read_json(path)
        if (
            set(ports) != set(SERVICES)
            or len(set(ports.values())) != len(ports)
            or any(type(x) is not int or not 1024 <= x <= 65535 for x in ports.values())
        ):
            raise AssemblyError("forward_port_inventory_invalid")
        return ports
    # Hold every reservation until all choices are made, so no duplicate ports.
    sockets, ports = [], {}
    try:
        for name in SERVICES:
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            sockets.append(listener)
            ports[name] = listener.getsockname()[1]
        deployment.write_json(path, ports)
    finally:
        for listener in sockets:
            listener.close()
    return ports


_RELOCATION_SEED_FILES = (
    "seed-spec.json", "seed/bootstrap-plan.json", "seed/bootstrap.json",
    "seed/inventory.json", "seed/applied.json",
)


def _relocation_seed_hashes(directory):
    return {
        name: (
            None if name == "seed/bootstrap-plan.json"
            and not (directory / name).exists() and not (directory / name).is_symlink()
            else hashlib.sha256(seed.private_read(directory / name)).hexdigest()
        )
        for name in _RELOCATION_SEED_FILES
    }


def _publish_relocation(path, record):
    # Atomic, exclusive publication: a torn staging file is never a journal.
    raw = canonical_json_bytes(record)
    completed = canonical_json_bytes({**record, "status": "complete"})
    if max(len(raw), len(completed)) > 512 * 1024:
        raise AssemblyError("forward_relocation_invalid")
    staged = path.parent.parent / (".management-relocation-" + uuid4().hex + ".pending")
    try:
        seed.private_write(staged, raw)
        os.link(staged, path, follow_symlinks=False)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        staged.unlink(missing_ok=True)


def _published_management_ports(deployment, owner):
    """Read only the exact owned running node's loopback publications."""
    cid = owner["container_id"]
    if not isinstance(cid, str) or not re.fullmatch(r"[0-9a-f]{64}", cid):
        raise AssemblyError("forward_relocation_owner_mismatch")
    template = (
        '{"id":{{json .Id}},"name":{{json .Name}},'
        '"running":{{json .State.Running}},"ports":{{json .NetworkSettings.Ports}}}'
    )
    code, output = deployment.run(
        ["docker", "container", "inspect", "--format", template, cid],
        timeout=15, check=False,
    )
    try:
        value = strict_json_loads(output, max_bytes=16384)
        if code or set(value) != {"id", "name", "running", "ports"} or (
            value["id"] != cid or value["name"] != "/guardrails-evolution"
            or value["running"] is not True
        ):
            raise ValueError
        _validate_publications(value["ports"])
        return value["ports"]
    except (ValueError, TypeError, KeyError):
        raise AssemblyError("forward_relocation_owner_mismatch") from None


def _validate_publications(value):
    if not isinstance(value, dict) or not 1 <= len(value) <= 32:
        raise ValueError
    ports = set()
    for remote, rows in value.items():
        if not re.fullmatch(r"[1-9][0-9]{0,4}/tcp", remote):
            raise ValueError
        if int(remote.split("/")[0]) > 65535:
            raise ValueError
        if rows is None:  # Declared but unpublished container port.
            continue
        if not isinstance(rows, list) or len(rows) != 1:
            raise ValueError
        row = rows[0]
        if set(row) != {"HostIp", "HostPort"} or row["HostIp"] != "127.0.0.1":
            raise ValueError
        port = row["HostPort"]
        if not isinstance(port, str) or not re.fullmatch(r"[1-9][0-9]{3,4}", port):
            raise ValueError
        number = int(port)
        if not 1024 <= number <= 65535 or number in ports:
            raise ValueError
        ports.add(number)
    if not ports:
        raise ValueError
    return ports


def _relocation_receipt(directory, owner):
    history = directory / "management-relocations"
    if not history.exists() and not history.is_symlink():
        return None
    _directory(history)
    paths = list(history.iterdir())
    if len(paths) > 32:
        raise AssemblyError("forward_relocation_invalid")
    current = None
    for path in paths:
        try:
            record = strict_json_loads(seed.private_read(path, maximum=512 * 1024))
            if set(record) != {
                "format", "status", "owner", "published", "before", "after", "seed_hashes",
            } or record["format"] != "management-forward-relocation-v1":
                raise ValueError
            identity = record["owner"]
            if set(identity) != {"owner_id", "container_id", "state", "uid"} or (
                identity["owner_id"] != owner["owner_id"]
                or identity["state"] != str(directory.parent)
                or identity["uid"] != os.getuid()
                or not re.fullmatch(r"[0-9a-f]{64}", identity["container_id"])
                or path.name != identity["container_id"] + ".json"
                or record["status"] not in {"pending", "complete"}
            ):
                raise ValueError
            _relocation_transition(record)
            if identity["container_id"] == owner["container_id"]:
                current = (path, record)
            elif record["status"] != "complete":
                raise ValueError
        except (ValueError, TypeError, KeyError, AttributeError):
            raise AssemblyError("forward_relocation_invalid") from None
    return current


def _relocation_transition(record):
    """Re-derive the permitted change; hashes alone do not authorize edits."""
    snapshots = {}
    for stage in ("before", "after"):
        if set(record[stage]) != {"ports", "access"}:
            raise ValueError
        snapshots[stage] = {}
        for name, item in record[stage].items():
            if set(item) != {"bytes", "sha256"}:
                raise ValueError
            raw = base64.b64decode(item["bytes"], validate=True)
            if len(raw) > 128 * 1024 or hashlib.sha256(raw).hexdigest() != item["sha256"]:
                raise ValueError
            snapshots[stage][name] = raw
    before = {key: strict_json_loads(raw) for key, raw in snapshots["before"].items()}
    after = {key: strict_json_loads(raw) for key, raw in snapshots["after"].items()}
    for item in (before["ports"], after["ports"]):
        if set(item) != set(SERVICES) or any(
            type(port) is not int or not 1024 <= port <= 65535 for port in item.values()
        ) or len(set(item.values())) != len(item):
            raise ValueError
    old, new = before["ports"]["management"], after["ports"]["management"]
    published = _validate_publications(record["published"])
    if old == new or set(before["ports"].values()) & published != {old} or (
        new in published or before["access"]["management_url"] != f"https://localhost:{old}"
        or set(record["seed_hashes"]) != set(_RELOCATION_SEED_FILES)
        or any(not (name == "seed/bootstrap-plan.json" and value is None)
               and (not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value))
               for name, value in record["seed_hashes"].items())
    ):
        raise ValueError
    expected_ports = {**before["ports"], "management": new}
    expected_access = {**before["access"], "management_url": f"https://localhost:{new}"}
    if snapshots["after"] != {
        "ports": canonical_json_bytes(expected_ports),
        "access": canonical_json_bytes(expected_access),
    }:
        raise ValueError
    return snapshots, after


def _relocation_cas(path, before, after, *, maximum=128 * 1024):
    current = seed.private_read(path, maximum=maximum)
    if current == after:
        return
    if current != before:
        raise AssemblyError("forward_relocation_state_changed")
    seed.private_write(path, after, replace=True)


def _prepare_management_relocation(deployment, directory, owner):
    """Complete only journaled metadata before any generic forward lifecycle."""
    receipt = _relocation_receipt(directory, owner)
    ports = _port_record(deployment, directory)
    access_path = directory / "access.json"
    if not access_path.exists():
        if receipt:
            raise AssemblyError("forward_relocation_state_changed")
        return None
    if receipt is None:
        access_raw = seed.private_read(access_path, maximum=128 * 1024)
        current = strict_json_loads(access_raw)
        forward = directory / "forward-management.json"
        if forward.exists():
            saved = deployment.read_json(forward)
            identity = _process_identity(saved.get("pid"))
            if identity is not None:
                if identity != saved or saved["command"] != _forward_digest(
                    deployment, "management", ports["management"]
                ):
                    raise AssemblyError("forward_process_owner_mismatch")
                if not _forward_listening(identity, ports["management"]):
                    raise AssemblyError("forward_listener_unavailable")
                return None
        with socket.socket() as check:
            try:
                check.bind(("127.0.0.1", ports["management"]))
                return None
            except OSError:
                pass
        published = _published_management_ports(deployment, owner)
        overlap = set(ports.values()) & _validate_publications(published)
        if overlap != {ports["management"]}:
            raise AssemblyError("forward_port_already_used")
        if current["management_url"] != f'https://localhost:{ports["management"]}':
            raise AssemblyError("forward_relocation_state_changed")
        history = _directory(directory / "management-relocations")
        path = history / (owner["container_id"] + ".json")
        # Reserve the choice through journal publication. A later contender is
        # rejected by normal _forward; retries reuse this journal, never repick.
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            chosen = reservation.getsockname()[1]
            if chosen in set(ports.values()) | _validate_publications(published):
                raise AssemblyError("forward_relocation_port_unavailable")
            before = {"ports": seed.private_read(directory / "ports.json"), "access": access_raw}
            after = {
                "ports": canonical_json_bytes({**ports, "management": chosen}),
                "access": canonical_json_bytes({**current, "management_url": f"https://localhost:{chosen}"}),
            }
            record = {
                "format": "management-forward-relocation-v1", "status": "pending",
                "owner": {
                    "owner_id": owner["owner_id"], "container_id": owner["container_id"],
                    "state": str(deployment.state), "uid": os.getuid(),
                },
                "published": published, "seed_hashes": _relocation_seed_hashes(directory),
                **{stage: {name: {"bytes": base64.b64encode(raw).decode(),
                                  "sha256": hashlib.sha256(raw).hexdigest()}
                           for name, raw in values.items()}
                   for stage, values in (("before", before), ("after", after))},
            }
            _relocation_transition(record)
            _publish_relocation(path, record)
        receipt = path, record
    path, record = receipt
    snapshots, after = _relocation_transition(record)
    if record["status"] == "complete":
        current = strict_json_loads(seed.private_read(access_path))
        if ports["management"] != after["ports"]["management"] or (
            current["management_url"] != after["access"]["management_url"]
        ):
            raise AssemblyError("forward_relocation_state_changed")
        return None
    if _published_management_ports(deployment, owner) != record["published"] or (
        _relocation_seed_hashes(directory) != record["seed_hashes"]
    ):
        raise AssemblyError("forward_relocation_state_changed")
    actual = {name: seed.private_read(directory / (name + ".json")) for name in ("ports", "access")}
    if actual not in (snapshots["before"], snapshots["after"], {
        "ports": snapshots["after"]["ports"], "access": snapshots["before"]["access"],
    }):
        raise AssemblyError("forward_relocation_state_changed")
    for name in ("ports", "access"):
        _relocation_cas(directory / (name + ".json"), snapshots["before"][name], snapshots["after"][name])
    return receipt


def _finish_management_relocation(deployment, directory, owner, receipt):
    if receipt is None:
        return
    path, record = receipt
    if _relocation_receipt(directory, owner) != receipt:
        raise AssemblyError("forward_relocation_state_changed")
    snapshots, after = _relocation_transition(record)
    if _relocation_seed_hashes(directory) != record["seed_hashes"] or any(
        seed.private_read(directory / (name + ".json")) != raw
        for name, raw in snapshots["after"].items()
    ):
        raise AssemblyError("forward_relocation_state_changed")
    _forward(deployment, directory, "management", after["ports"]["management"])
    _https_ready(after["access"]["management_url"] + "/health/live", after["access"]["ca_file"], timeout=30)
    if _published_management_ports(deployment, owner) != record["published"] or (
        _relocation_seed_hashes(directory) != record["seed_hashes"]
    ) or any(seed.private_read(directory / (name + ".json")) != raw
             for name, raw in snapshots["after"].items()):
        raise AssemblyError("forward_relocation_state_changed")
    _relocation_cas(
        path, canonical_json_bytes(record),
        canonical_json_bytes({**record, "status": "complete"}), maximum=512 * 1024,
    )


def _process_identity(pid):
    try:
        root = Path("/proc") / str(pid)
        if root.stat().st_uid != os.getuid():
            return None
        stat = (root / "stat").read_text().rsplit(")", 1)[1].split()
        if stat[0] == "Z":
            return None
        return {
            "pid": pid,
            "start": stat[19],
            "command": hashlib.sha256((root / "cmdline").read_bytes()).hexdigest(),
        }
    except OSError, IndexError, ValueError:
        return None


def _forward_argv(deployment, name, port):
    if name not in SERVICES or type(port) is not int or not 1024 <= port <= 65535:
        raise AssemblyError("forward_port_inventory_invalid")
    namespace, remote = SERVICES[name]
    service = "gateway" if name.startswith("team-") else name
    resource = "service/" + service
    if name == "postgres":
        resource = "deployment/postgres"
        remote = 15432
    return [
        str(deployment.bin / "kubectl"),
        "--kubeconfig",
        str(deployment.kubeconfig),
        "--context",
        "guardrails-evolution",
        "--namespace",
        namespace,
        "port-forward",
        "--address",
        "127.0.0.1",
        resource,
        f"{port}:{remote}",
    ]


def _forward_digest(deployment, name, port):
    return hashlib.sha256(
        b"\0".join(item.encode() for item in _forward_argv(deployment, name, port))
        + b"\0"
    ).hexdigest()


def _forward_listening(identity, port):
    """Prove the owned process listens without opening an upstream connection."""
    if _process_identity(identity["pid"]) != identity:
        return False
    root = Path("/proc") / str(identity["pid"])
    try:
        sockets = set()
        for descriptor in (root / "fd").iterdir():
            try:
                target = os.readlink(descriptor)
            except FileNotFoundError:
                continue
            match = re.fullmatch(r"socket:\[(\d+)\]", target)
            if match:
                sockets.add(match.group(1))
        address = "0100007F:" + format(port, "04X")
        listening = any(
            len(fields) > 9
            and fields[1] == address
            and fields[3] == "0A"
            and fields[9] in sockets
            for line in (root / "net/tcp").read_text().splitlines()[1:]
            if (fields := line.split())
        )
        return listening and _process_identity(identity["pid"]) == identity
    except OSError:
        return False


def _forward(deployment, directory, name, port, *, timeout=30, spawn=None):
    argv = _forward_argv(deployment, name, port)
    path = directory / ("forward-" + name + ".json")
    expected = _forward_digest(deployment, name, port)
    if path.exists():
        saved = deployment.read_json(path)
        current = _process_identity(saved.get("pid"))
        if current is not None:
            if current != saved or saved["command"] != expected:
                raise AssemblyError("forward_process_owner_mismatch")
            if not _forward_listening(current, port):
                raise AssemblyError("forward_listener_unavailable")
            return
    with socket.socket() as check:
        try:
            # Match the Go TCP listener used by kubectl port-forward. Recently
            # closed connections may retain this address after its owner exits.
            # SO_REUSEADDR still refuses an already listening foreign socket.
            check.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            check.bind(("127.0.0.1", port))
        except OSError as error:
            refused = AssemblyError("forward_port_already_used")
            refused.errno = error.errno  # Numeric metadata; never the OS message.
            raise refused from None
    log = directory / ("forward-" + name + ".log")
    descriptor = os.open(
        log, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600
    )
    process = None
    try:
        with os.fdopen(descriptor, "ab") as output:
            process = (spawn or subprocess.Popen)(
                argv,
                env=deployment.env,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=output,
                start_new_session=True,
            )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            identity = _process_identity(process.pid)
            if process.poll() is not None:
                raise AssemblyError("forward_start_failed")
            if identity and identity["command"] == expected:
                deployment.write_json(path, identity)
                if _forward_listening(identity, port):
                    return
            time.sleep(0.1)
        raise AssemblyError("forward_start_timeout")
    except BaseException:
        # This Popen owns an unreaped child, not an adopted PID/record. Until
        # wait/poll reaps it, the PID cannot be reused by an unrelated process.
        try:
            if process is None:
                raise
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            pass  # Keep the original persistence/cancellation failure.
        raise


def stop_forwards(deployment, names=None):
    """Stop only recorded processes whose owner and command still match."""
    directory, owner = _state(deployment)
    receipt = _relocation_receipt(directory, owner)
    if receipt and receipt[1]["status"] == "pending":
        _prepare_management_relocation(deployment, directory, owner)
    ports = _port_record(deployment, directory)
    selected = []
    for path in directory.glob("forward-*.json"):
        name = path.name.removeprefix("forward-").removesuffix(".json")
        if name not in SERVICES:
            raise AssemblyError("forward_process_owner_mismatch")
        saved = deployment.read_json(path)
        current = _process_identity(saved.get("pid"))
        if current is not None:
            if current != saved or current["command"] != _forward_digest(
                deployment, name, ports[name]
            ):
                raise AssemblyError("forward_process_owner_mismatch")
            selected.append(saved)
    # Validate the complete inventory before stopping any child.
    for saved in selected:
        if names is not None and saved["command"] not in {
            _forward_digest(deployment, name, ports[name]) for name in names
        }:
            continue
        if _process_identity(saved["pid"]) == saved:
            os.kill(saved["pid"], signal.SIGTERM)
            deadline = time.monotonic() + 2
            while (
                _process_identity(saved["pid"]) == saved and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            if _process_identity(saved["pid"]) == saved:
                raise AssemblyError("forward_stop_timeout")


def _network_baseline(deployment, owner):
    dns_rule = {
        "to": [
            {
                "namespaceSelector": {
                    "matchLabels": {"kubernetes.io/metadata.name": "kube-system"}
                },
                "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}},
            }
        ],
        "ports": [{"protocol": "UDP", "port": 53}, {"protocol": "TCP", "port": 53}],
    }
    for namespace in sorted({value[0] for value in SERVICES.values()}):
        _, raw = deployment.kubectl(
            "get", "namespace", namespace, "-o", "json", namespace=namespace
        )
        labels = json.loads(raw)["metadata"].get("labels", {})
        if (
            labels.get("guardrails.evolution/owner") != owner["owner_id"]
            or labels.get("pod-security.kubernetes.io/enforce") != "restricted"
        ):
            raise AssemblyError("namespace_owner_mismatch")
        _, raw = deployment.kubectl(
            "get", "networkpolicy", "default-deny", "-o", "json", namespace=namespace
        )
        policy = json.loads(raw)["spec"]
        if (
            policy.get("podSelector") != {}
            or set(policy.get("policyTypes", [])) != {"Ingress", "Egress"}
            or policy.get("ingress")
            or policy.get("egress")
        ):
            raise AssemblyError("platform_network_baseline_invalid")
        _, raw = deployment.kubectl(
            "get", "networkpolicy", "dns-only", "-o", "json", namespace=namespace
        )
        if json.loads(raw)["spec"] != {
            "podSelector": {"matchExpressions": [{"key": "guardrails.evolution/component", "operator": "NotIn", "values": ["classifier"]}]},
            "policyTypes": ["Egress"],
            "egress": [dns_rule],
        }:
            raise AssemblyError("platform_network_baseline_invalid")


def _helm(deployment, directory, values):
    _cpu_donors(deployment, directory, values, change=True)
    path = directory / "values.json"
    _write(path, values)
    deployment.run(
        [
            deployment.bin / "helm",
            "upgrade",
            "--install",
            "evolution-platform",
            deployment.root / "deploy/evolution/charts/platform",
            "--kubeconfig",
            deployment.kubeconfig,
            "--kube-context",
            "guardrails-evolution",
            "--namespace",
            "gr-platform",
            "--values",
            path,
        ],
        timeout=120,
    )


def _cpu_donors(deployment, directory, values, *, change=False):
    helper = deployment.support("resource_budget")
    expected = deployment.image("evolution-service")
    images = {expected}
    retained = directory / "values.json"
    if retained.exists():
        previous = deployment.read_json(retained)
        old = previous.get("components", {}).get("mock", {}).get("image")
        if previous.get("ownerId") != values["ownerId"]:
            raise AssemblyError("platform_chart_owner_mismatch")
        if old is not None:
            if not isinstance(old, str) or re.fullmatch(re.escape(expected.split("@", 1)[0]) + r"@sha256:[0-9a-f]{64}", old) is None:
                raise AssemblyError("platform_component_image_invalid")
            images.add(old)
    try:
        helper.check(deployment, values["ownerId"], _same_limits, mock_images=images, change=change)
    except helper.ResourceBudgetError as error:
        raise AssemblyError(str(error)) from None


def _ready(deployment, name, namespace, timeout=180):
    deployment.kubectl(
        "rollout",
        "status",
        "deployment/" + name,
        "--timeout=" + str(timeout) + "s",
        namespace=namespace,
        timeout=timeout + 5,
    )


def _https_ready(url, ca_file, *, token=None, timeout=120):
    deadline = time.monotonic() + timeout
    with httpx.Client(
        verify=ssl.create_default_context(cafile=ca_file),
        trust_env=False,
        timeout=5,
        follow_redirects=False,
    ) as client:
        while time.monotonic() < deadline:
            try:
                response = client.get(
                    url, headers={"Authorization": "Bearer " + token} if token else {}
                )
                if response.status_code == 200:
                    return
            except httpx.TransportError:
                pass
            time.sleep(0.2)
    raise AssemblyError("service_readiness_timeout")


def _warm_model(deployment, url, ca_file):
    """Load only the pinned local model before readiness, with no user content."""
    expected = next(
        item["sha256"]
        for item in deployment.specs()["files"]
        if item["name"] == "qwen-manifest"
    )
    _https_ready(url + "/api/tags", ca_file)
    with httpx.Client(
        verify=ssl.create_default_context(cafile=ca_file),
        trust_env=False,
        follow_redirects=False,
        timeout=90,
    ) as client:
        response = client.get(url + "/api/tags")
        if response.status_code != 200 or [
            (row.get("name"), row.get("digest", "").removeprefix("sha256:"))
            for row in response.json().get("models", [])
        ] != [("qwen2.5:0.5b", expected)]:
            raise AssemblyError("prepared_model_manifest_mismatch")
        response = client.post(
            url + "/api/generate",
            json={
                "model": "qwen2.5:0.5b",
                "prompt": "",
                "stream": False,
                "keep_alive": "30m",
            },
        )
        if response.status_code != 200:
            raise AssemblyError("prepared_model_load_failed")
        response = client.get(url + "/api/ps")
        if response.status_code != 200 or not any(
            row.get("digest", "").removeprefix("sha256:") == expected
            for row in response.json().get("models", [])
        ):
            raise AssemblyError("prepared_model_digest_mismatch")


def _config_secret(deployment, owner, name, namespace, files):
    _, result = deployment.kubectl(
        "get",
        "secret",
        name + "-config",
        "--ignore-not-found",
        "-o",
        "json",
        namespace=namespace,
    )
    if (
        result.strip()
        and json.loads(result)["metadata"]
        .get("labels", {})
        .get("guardrails.evolution/owner")
        != owner["owner_id"]
    ):
        raise AssemblyError("component_secret_owner_mismatch")
    if any(not re.fullmatch(r"[a-zA-Z0-9._-]{1,128}", key) for key in files):
        raise AssemblyError("component_file_name_invalid")
    data = {
        key: base64.b64encode(value).decode() for key, value in sorted(files.items())
    }
    document = {
        "apiVersion": "v1",
        "kind": "Secret",
        "type": "Opaque",
        "metadata": {
            "name": name + "-config",
            "namespace": namespace,
            "labels": {"guardrails.evolution/owner": owner["owner_id"]},
        },
        "data": data,
    }
    deployment.kubectl(
        "apply", "-f", "-", namespace=namespace, input_text=json.dumps(document)
    )
    return hashlib.sha256(canonical_json_bytes(data)).hexdigest()


def _tls(deployment, directory, name, namespace):
    target = directory / "tls" / namespace / name
    deployment.support("certificates").issue_server(
        deployment.state / "credentials",
        target,
        names=[name + "." + namespace + ".svc.cluster.local"],
    )
    return {
        key: seed.private_read(target / key) for key in ("tls.crt", "tls.key", "ca.crt")
    }


def _component(
    namespace,
    component,
    image,
    revision,
    *,
    uid=10001,
    env=None,
    command=None,
    args=None,
    readiness=None,
    liveness=None,
    sidecar=None,
    postgres_tunnel=None,
):
    cpu, memory = RESOURCE_BUDGETS[component]
    return {
        "enabled": True,
        "namespace": namespace,
        "component": component,
        "image": image,
        "uid": uid,
        "configRevision": revision,
        "command": command,
        "args": args,
        "env": env or {},
        "resources": {
            "requests": {"cpu": "100m", "memory": "128Mi"},
            "limits": {"cpu": cpu, "memory": memory},
        },
        "readinessCommand": readiness,
        "livenessCommand": liveness,
        "sidecar": sidecar,
        "postgresTunnel": postgres_tunnel,
    }


def _probe(path, tokens=()):
    source = "import pathlib,ssl,urllib.request; c=ssl.create_default_context(cafile='/config/ca.crt'); "
    if tokens:
        source += (
            "[(lambda r: urllib.request.urlopen(r,context=c,timeout=3).close())(urllib.request.Request('https://localhost:8443"
            + path
            + "',headers={'Authorization':'Bearer '+pathlib.Path(p).read_text().strip()})) for p in "
            + repr(list(tokens))
            + "]"
        )
    else:
        source += (
            "urllib.request.urlopen('https://localhost:8443"
            + path
            + "',context=c,timeout=3).close()"
        )
    return ["python", "-c", source]


def _service_config(runtime_id, *, signing=False):
    return {
        "mode": "remote",
        "database_url_file": "/config/database-url",
        "tls_cert_file": "/config/tls.crt",
        "tls_key_file": "/config/tls.key",
        "tls_ca_file": "/config/ca.crt",
        "signing_key_file": "/config/snapshot-signing.key" if signing else None,
        "runtime_id": runtime_id,
        "zone": "local-shared",
        "worker_count": 2,
        "queue_limit": 8,
    }


def _gateway(deployment, directory, owner, tenant, value, profile, model_token, *, secret_writer=None):
    selected = "mock" if profile in {"deterministic_mock", "classifier"} else "ollama"
    route = value["profiles"][selected]
    destination, provider = route["destination"], route["provider"]
    files = _tls(deployment, directory, "gateway", "gr-" + tenant)
    # Each tenant has a distinct server key despite identical service names.
    base = "https://" + destination["host"] + ":" + str(destination["port"])
    if selected == "mock":
        base += "/v1"
    settings = {
        "tenant_id": tenant,
        "workload_id": value["workload"]["workload_id"],
        "provider_id": provider["provider_id"],
        "model_alias": "guarded-chat-" + tenant.removeprefix("team-"),
        "model_route": ("openai/" if selected == "mock" else "ollama_chat/")
        + provider["model_id"],
        "model_base_url": base,
        "runtime_url": "https://runtime.gr-platform.svc.cluster.local:8443",
        "workload_token_file": "/config/workload.token",
        "gateway_token_file": "/config/gateway.token",
        "model_token_file": "/config/model.token" if selected == "mock" else None,
        "ca_file": "/config/ca.crt",
        "tls_cert_file": "/config/tls.crt",
        "tls_key_file": "/config/tls.key",
        "litellm_config_file": "/config/litellm.yaml",
        "approved_routes_file": "/config/routes.json",
        "profile": "deterministic_mock" if profile == "classifier" else profile,
        "port": 8443,
    }
    if profile == "classifier":
        settings["evaluation_profile"] = "classifier"
    manifest = json.loads(seed.private_read(route["route_manifest_file"]))
    manifest["ca_files"] = {key: "/config/ca.crt" for key in manifest["ca_files"]}
    model_parameters = {
        "model": settings["model_route"],
        "api_base": base,
        "timeout": 10 if selected == "mock" else 45,
        "max_retries": 0,
        "guardrails": ["evolution"],
    }
    if selected == "mock":
        model_parameters["api_key"] = "os.environ/EVOLUTION_MODEL_KEY"
        files["model.token"] = model_token.encode()
    native = {
        "model_list": [
            {"model_name": settings["model_alias"], "litellm_params": model_parameters}
        ],
        "guardrails": [
            {
                "guardrail_name": "evolution",
                "litellm_params": {
                    "guardrail": "connector.evolution_guardrail.EvolutionGuardrail",
                    "mode": ["pre_call", "post_call"],
                    "default_on": True,
                    "settings_file": "/config/settings.json",
                },
            }
        ],
        "litellm_settings": {
            "ssl_verify": "/config/ca.crt",
            "num_retries": 0,
            "set_verbose": False,
            "turn_off_message_logging": True,
            "request_timeout": model_parameters["timeout"],
        },
        "general_settings": {
            "master_key": "os.environ/LITELLM_MASTER_KEY",
            "disable_spend_logs": True,
        },
    }
    files.update(
        {
            "settings.json": canonical_json_bytes(settings),
            "routes.json": canonical_json_bytes(manifest),
            # The pinned proxy loads CONFIG_FILE_PATH only for .yaml/.yml.
            # JSON bytes are valid YAML; the recognized suffix is mandatory.
            "litellm.yaml": canonical_json_bytes(native),
            "workload.token": seed.private_read(
                value["roles"]["workload"]["token_file"]
            ),
            "gateway.token": seed.private_read(value["gateway_token_file"]),
        }
    )
    revision = (secret_writer or _config_secret)(
        deployment, owner, "gateway-" + tenant, "gr-" + tenant, files
    )
    return _component(
        "gr-" + tenant,
        "gateway",
        deployment.image("evolution-gateway"),
        revision,
        env={
            "EVOLUTION_GATEWAY_CONFIG_FILE": "/config/settings.json",
            "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        },
        command=["python", "-m", "connector.evolution_gateway"],
        readiness=_probe("/health/ready", ("/config/gateway.token",)),
        liveness=_probe("/health/live"),
    )


_APPLICATIONS = {
    "management": ("gr-platform", "management", "evolution-service"),
    "runtime-1": ("gr-platform", "runtime", "evolution-service"),
    "runtime-2": ("gr-platform", "runtime", "evolution-service"),
    "mock": ("gr-model", "mock", "evolution-service"),
    "ollama": ("gr-model", "ollama", "evolution-model"),
    "gateway-team-a": ("gr-team-a", "gateway", "evolution-gateway"),
    "gateway-team-b": ("gr-team-b", "gateway", "evolution-gateway"),
    "postgres": ("gr-platform", "postgres", "postgres"),
}

_CLASSIFIER_APPLICATION = ("gr-platform", "classifier", "evolution-classifier")


def _applications(values):
    return {**_APPLICATIONS, **({"classifier": _CLASSIFIER_APPLICATION}
            if "classifier" in values.get("components", {}) else {})}


def _component_limits(values, component):
    cpu, memory = RESOURCE_BUDGETS[component]
    if values.get("evaluatorProfile") == "classifier":
        cpu = {"runtime": "750m", "gateway": "500m"}.get(component, cpu)
    return {"cpu": cpu, "memory": memory}


def _same_limits(actual, expected):
    # Kubernetes canonicalizes 1000m to 1; compare quantities, not spellings.
    def quantity(value, cpu):
        match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(m|Ki|Mi|Gi|k|K|M|G)?", str(value))
        if match is None:
            return None
        number, suffix = Decimal(match[1]), match[2] or ""
        factors = (
            {"": 1, "m": Decimal("0.001")}
            if cpu
            else {
                "": 1,
                "Ki": 1024,
                "Mi": 1024**2,
                "Gi": 1024**3,
                "k": 1000,
                "K": 1000,
                "M": 1000**2,
                "G": 1000**3,
            }
        )
        return None if suffix not in factors else number * factors[suffix]

    return set(actual) == set(expected) == {"cpu", "memory"} and all(
        quantity(actual[key], key == "cpu") is not None
        and quantity(actual[key], key == "cpu") == quantity(expected[key], key == "cpu")
        for key in expected
    )


def _desired_images(deployment, values, owner):
    """Refresh only prepared application images and declared resource limits."""
    if (
        values.get("ownerId") != owner["owner_id"]
        or set(values.get("components", {})) != set(_applications(values))
        or values.get("initImage") != deployment.image("python")
    ):
        raise AssemblyError("platform_chart_owner_or_inventory_mismatch")
    profile = values.get("evaluatorProfile", "deterministic_mock")
    classifier = values["components"].get("classifier")
    if (profile not in {"deterministic_mock", "local_model", "classifier"}
            or (classifier is not None and (not values.get("classifierService")
                or classifier.get("enabled") is not (profile == "classifier")))
            or (profile == "classifier" and classifier is None)):
        raise AssemblyError("classifier_profile_inventory_mismatch")
    changed = False
    for name, (namespace, component, artifact) in _applications(values).items():
        row = values["components"][name]
        expected = deployment.image(artifact)
        if row.get("namespace") != namespace or row.get("component") != component:
            raise AssemblyError("platform_component_inventory_mismatch")
        old = row.get("image", "")
        if (
            not isinstance(old, str)
            or re.fullmatch(
                re.escape(expected.split("@", 1)[0]) + r"@sha256:[0-9a-f]{64}", old
            )
            is None
        ):
            raise AssemblyError("platform_component_image_invalid")
        # Infrastructure upgrades are outside application replay. Refuse drift.
        if (
            artifact == "postgres"
            and old != expected
            or component == "postgres"
            and row.get("postgresTunnel", {}).get("image") != deployment.image("python")
            or component == "ollama"
            and row.get("sidecar", {}).get("image") != deployment.image("caddy")
        ):
            raise AssemblyError("platform_infrastructure_image_mismatch")
        limits = _component_limits(values, component)
        if row["image"] != expected or row["resources"]["limits"] != limits:
            changed = True
            row["image"] = expected
            row["resources"]["limits"] = limits
        if component == "ollama" and row.get("env") != MODEL_ENV:
            row["env"] = dict(MODEL_ENV)
            changed = True
    return changed


def _owned_deployments(deployment, values, owner):
    """Check every existing target's ownership before any rollout mutation."""
    result = {}
    known = {**_APPLICATIONS, "classifier": _CLASSIFIER_APPLICATION}
    for namespace in sorted({v[0] for v in known.values()}):
        _, raw = deployment.kubectl(
            "get", "deployments", "-o", "json", namespace=namespace
        )
        for value in json.loads(raw)["items"]:
            name = value["metadata"]["name"]
            if name not in known or known[name][0] != namespace:
                continue
            if (
                value["metadata"].get("labels", {}).get("guardrails.evolution/owner")
                != owner["owner_id"]
            ):
                raise AssemblyError("platform_component_owner_mismatch")
            result[name] = value
    return result


def _caddy_copy_script(deployment):
    return (deployment.root / "deploy/evolution/charts/platform/files/caddy-copy.sh").read_text()


def _model_private_files_script(deployment):
    return (deployment.root / "deploy/evolution/charts/platform/files/model-private-files.py").read_text()


def _model_settings_match(deployment, row, template):
    spec = template.get("spec", {})
    containers = {c["name"]: c for c in spec.get("containers", [])}
    initializers = {c["name"]: c for c in spec.get("initContainers", [])}
    model = containers.get("ollama", {})
    initial = initializers.get("private-files", {})
    security = {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                "capabilities": {"drop": ["ALL"]}}
    expected_mounts = [
        {"name": "private", "mountPath": "/config", "subPath": "data", "readOnly": True},
        {"name": "state", "mountPath": "/state", "subPath": "data"},
        {"name": "temporary", "mountPath": "/tmp"},
        {"name": "state", "mountPath": "/models/manifests-v2", "subPath": "model-manifests-v2", "readOnly": True},
    ]
    state_mounts = {
        name: [m for m in c.get("volumeMounts", []) if m.get("name") == "state"]
        for name, c in {**containers, **initializers}.items()
    }
    return (
        spec.get("securityContext", {}).get("runAsUser") == 10001
        and spec.get("securityContext", {}).get("runAsGroup") == 10001
        and model.get("securityContext") == initial.get("securityContext") == security
        and model.get("command") == ["ollama", "serve"]
        and not model.get("envFrom")
        and sorted(model.get("env", []), key=lambda e: e.get("name", ""))
        == [{"name": k, "value": v} for k, v in sorted(MODEL_ENV.items())]
        and model.get("volumeMounts") == expected_mounts
        and state_mounts == {
            "ollama": [expected_mounts[1], expected_mounts[3]],
            "tls": [{"name": "state", "mountPath": "/state", "subPath": "data"}],
            "private-files": [{"name": "state", "mountPath": "/state"}],
            "caddy-binary": [],
        }
        and initial.get("image") == deployment.image("python")
        and initial.get("command") == ["python", "-B", "-c"]
        and initial.get("args") == [_model_private_files_script(deployment)]
        and not initial.get("env") and not initial.get("envFrom")
        and _same_limits(initial.get("resources", {}).get("requests", {}), {"cpu": "10m", "memory": "16Mi"})
        and _same_limits(initial.get("resources", {}).get("limits", {}), {"cpu": "100m", "memory": "32Mi"})
        and next((v for v in spec.get("volumes", []) if v.get("name") == "state"), None)
        == {"name": "state", "emptyDir": {"sizeLimit": "64Mi"}}
    )


def _model_verify_command():
    return (
        "test \"$OLLAMA_MODELS\" = /models; test \"$HOME\" = /state; "
        "test \"$OLLAMA_NOPRUNE\" = true; "
        "test ! -L /models/manifests-v2; "
        "metadata=$(stat -c '%u:%g:%a' /models/manifests-v2); test \"$metadata\" = 10001:10001:700; "
        "entries=$(find /models/manifests-v2 -mindepth 1 -maxdepth 1 -print); test -z \"$entries\"; "
        "awk '$5 == \"/models/manifests-v2\" && $6 ~ /(^|,)ro(,|$)/ { found=1 } END { exit !found }' /proc/self/mountinfo; "
        "awk '$5 == \"/\" && $6 ~ /(^|,)ro(,|$)/ { found=1 } END { exit !found }' /proc/self/mountinfo; "
        "test ! -e /state/model-manifests-v2"
    )


def _verify_model_manifests(deployment, name, row, pod, verifier):
    if not _model_settings_match(deployment, row, pod):
        raise AssemblyError("model_manifest_configuration_mismatch")
    completed = [s for s in pod["status"].get("initContainerStatuses", [])
                 if s.get("name") == "private-files"]
    if len(completed) != 1:
        raise AssemblyError("model_manifest_initialization_missing")
    try:
        identity = verifier.verify_completed_init(deployment, deployment.image("python"), completed[0])
    except verifier.ImageIdentityError as exc:
        raise AssemblyError(str(exc)) from None
    code, _ = deployment.kubectl("exec", pod["metadata"]["name"], "-c", "ollama", "--",
                       "timeout", "10", "/bin/sh", "-ec", _model_verify_command(),
                       namespace=row["namespace"], timeout=15)
    if code:
        raise AssemblyError("model_manifest_runtime_mismatch")
    return {"deployment": name, "container": "private-files", **identity,
            "model_v2_empty": True, "model_v2_read_only": True, "model_v2_mode": "0700"}


def _caddy_settings_match(deployment, row, template):
    spec = template.get("spec", {})
    initializers = {c["name"]: c for c in spec.get("initContainers", [])}
    copy = initializers.get("caddy-binary", {})
    containers = {c["name"]: c for c in spec.get("containers", [])}
    tls = containers.get("tls", {})
    security = {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                "capabilities": {"drop": ["ALL"]}}
    expected_mount = {"name": "caddy-executable", "mountPath": "/executable",
                      "subPath": "private", "readOnly": True}
    return (
        spec.get("securityContext", {}).get("runAsUser") == 10001
        and spec.get("securityContext", {}).get("runAsGroup") == 10001
        and copy.get("image") == row["sidecar"]["image"]
        and copy.get("command") == ["timeout", "30", "/bin/sh", "-ec"]
        and copy.get("args") == [_caddy_copy_script(deployment)]
        and copy.get("securityContext") == tls.get("securityContext") == security
        and copy.get("volumeMounts") == [{"name": "caddy-executable", "mountPath": "/executable"}]
        and _same_limits(copy.get("resources", {}).get("requests", {}), {"cpu": "10m", "memory": "16Mi"})
        and _same_limits(copy.get("resources", {}).get("limits", {}), {"cpu": "100m", "memory": "64Mi"})
        and _same_limits(tls.get("resources", {}).get("requests", {}), {"cpu": "20m", "memory": "32Mi"})
        and _same_limits(tls.get("resources", {}).get("limits", {}), {"cpu": "100m", "memory": "96Mi"})
        and tls.get("command") == ["/executable/caddy", "run", "--config", "/config/Caddyfile", "--adapter", "caddyfile"]
        and [m for m in tls.get("volumeMounts", []) if m.get("name") == "caddy-executable"] == [expected_mount]
        and not any(m.get("name") == "caddy-executable" for name, c in containers.items()
                    if name != "tls" for m in c.get("volumeMounts", []))
        and next((v for v in spec.get("volumes", []) if v.get("name") == "caddy-executable"), None)
        == {"name": "caddy-executable", "emptyDir": {"sizeLimit": "96Mi"}}
    )


def _verify_caddy_copy(deployment, name, row, pod, verifier):
    if not _caddy_settings_match(deployment, row, pod):
        raise AssemblyError("caddy_copy_configuration_mismatch")
    completed = [s for s in pod["status"].get("initContainerStatuses", [])
                 if s.get("name") == "caddy-binary"]
    if len(completed) != 1:
        raise AssemblyError("caddy_copy_initialization_missing")
    try:
        identity = verifier.verify_completed_init(deployment, row["sidecar"]["image"], completed[0])
    except verifier.ImageIdentityError as exc:
        raise AssemblyError(str(exc)) from None
    code, _ = deployment.kubectl("exec", pod["metadata"]["name"], "-c", "tls", "--",
                       "timeout", "30", "/bin/sh", "-ec", _caddy_verify_command(),
                       namespace=row["namespace"], timeout=35)
    if code:
        raise AssemblyError("caddy_copy_runtime_mismatch")
    return {"deployment": name, "container": "caddy-binary", **identity,
            "copied_bytes_verified": True, "copy_read_only": True,
            "copy_mode": "0500", "copy_capabilities_empty": True}


def _caddy_verify_command():
    return (
        "uid=$(id -u); gid=$(id -g); test \"$uid:$gid\" = 10001:10001; "
        "ownership=$(stat -c '%u:%g:%a' /executable/caddy); test \"$ownership\" = 10001:10001:500; "
        "capabilities=$(getcap /executable/caddy); test -z \"$capabilities\"; "
        "cmp -s /usr/bin/caddy /executable/caddy; "
        "awk '$5 == \"/executable\" && $6 ~ /(^|,)ro(,|$)/ { found=1 } END { exit !found }' /proc/self/mountinfo; "
        "grep -Eq '^CapEff:[[:space:]]+0+$' /proc/self/status; "
        "grep -Eq '^NoNewPrivs:[[:space:]]+1$' /proc/self/status"
    )


def _verify_application_images(deployment, values, owner):
    """Bind each Ready owned pod to its exact prepared OCI config and rootfs."""
    live = _owned_deployments(deployment, values, owner)
    verifier = deployment.support("image_identity")
    observed = []
    for name, row in values["components"].items():
        if not row["enabled"]:
            continue
        target = live.get(name)
        if target is None or target["spec"].get("replicas") != 1:
            raise AssemblyError("platform_component_unavailable")
        _, raw = deployment.kubectl(
            "get",
            "pods",
            "-l",
            "guardrails.evolution/instance=" + name,
            "-o",
            "json",
            namespace=row["namespace"],
        )
        pods = [
            p
            for p in json.loads(raw)["items"]
            if not p["metadata"].get("deletionTimestamp")
        ]
        if len(pods) != 1:
            raise AssemblyError("platform_component_unavailable")
        pod = pods[0]
        refs = [
            r
            for r in pod["metadata"].get("ownerReferences", [])
            if r.get("controller") is True and r.get("kind") == "ReplicaSet"
        ]
        if len(refs) != 1:
            raise AssemblyError("platform_pod_owner_mismatch")
        _, raw = deployment.kubectl(
            "get",
            "replicaset",
            refs[0]["name"],
            "-o",
            "json",
            namespace=row["namespace"],
        )
        replica = json.loads(raw)
        if replica["metadata"]["uid"] != refs[0]["uid"] or not any(
            r.get("kind") == "Deployment"
            and r.get("controller") is True
            and r.get("uid") == target["metadata"]["uid"]
            for r in replica["metadata"].get("ownerReferences", [])
        ):
            raise AssemblyError("platform_pod_owner_mismatch")
        expected = {row["component"]: row["image"]}
        if row.get("sidecar"):
            expected["tls"] = row["sidecar"]["image"]
        if row.get("postgresTunnel"):
            expected["postgres-tunnel"] = row["postgresTunnel"]["image"]
        actual = {c["name"]: c["image"] for c in pod["spec"]["containers"]}
        statuses = {s["name"]: s for s in pod["status"].get("containerStatuses", [])}
        if actual != expected or set(statuses) != set(expected):
            raise AssemblyError("platform_running_image_mismatch")
        app = next(
            c for c in pod["spec"]["containers"] if c["name"] == row["component"]
        )
        if not _same_limits(
            app.get("resources", {}).get("limits", {}), row["resources"]["limits"]
        ):
            raise AssemblyError("platform_running_resource_mismatch")
        if name == "postgres" and not _postgres_settings_match(row, pod):
            raise AssemblyError("postgres_running_authentication_mismatch")
        if name == "ollama" and not _model_settings_match(deployment, row, pod):
            raise AssemblyError("model_manifest_configuration_mismatch")
        if name == "classifier" and not _classifier_settings_match(row, pod):
            raise AssemblyError("classifier_configuration_mismatch")
        for container, reference in expected.items():
            try:
                identity = verifier.verify_container(
                    deployment, reference, statuses[container]
                )
            except verifier.ImageIdentityError as exc:
                raise AssemblyError(str(exc)) from None
            observed.append({"deployment": name, "container": container, **identity})
        if row.get("sidecar"):
            observed.append(_verify_caddy_copy(deployment, name, row, pod, verifier))
        if name == "ollama":
            observed.append(_verify_model_manifests(deployment, name, row, pod, verifier))
        if name == "classifier":
            completed = [s for s in pod["status"].get("initContainerStatuses", []) if s.get("name") == "private-files"]
            if len(completed) != 1:
                raise AssemblyError("classifier_private_initialization_missing")
            try:
                identity = verifier.verify_completed_init(deployment, deployment.image("python"), completed[0])
            except verifier.ImageIdentityError as exc:
                raise AssemblyError(str(exc)) from None
            observed.append({"deployment": name, "container": "private-files", **identity})
    return observed


def _reconcile_application_images(
    deployment, directory, values, owner, *, changed=False
):
    changed = _desired_images(deployment, values, owner) or changed
    live = _owned_deployments(deployment, values, owner)
    changed = _postgres_security(deployment, directory, values, owner) or changed
    for name, row in values["components"].items():
        if not row["enabled"]:
            continue
        existing = live.get(name)
        containers = (
            {}
            if existing is None
            else {
                c["name"]: c for c in existing["spec"]["template"]["spec"]["containers"]
            }
        )
        actual = containers.get(row["component"], {})
        if actual.get("image") != row["image"] or not _same_limits(
            actual.get("resources", {}).get("limits", {}), row["resources"]["limits"]
        ):
            changed = True
        if name == "postgres" and not _postgres_settings_match(
            row, {} if existing is None else existing["spec"]["template"]
        ):
            changed = True
        if row.get("sidecar") and not _caddy_settings_match(
            deployment, row, {} if existing is None else existing["spec"]["template"]
        ):
            changed = True
        if name == "ollama" and not _model_settings_match(
            deployment, row, {} if existing is None else existing["spec"]["template"]
        ):
            changed = True
        if name == "classifier" and not _classifier_settings_match(row, {} if existing is None else existing["spec"]["template"]):
            changed = True
    if changed:
        # A forward follows one pod, so retire it before any Recreate rollout.
        stop_forwards(deployment)
        _helm(deployment, directory, values)
    else:
        _cpu_donors(deployment, directory, values, change=True)
    for name, row in values["components"].items():
        if row["enabled"]:
            _ready(deployment, name, row["namespace"], timeout=240)
    _cpu_donors(deployment, directory, values)
    identities = _verify_application_images(deployment, values, owner)
    _write(
        directory / "running-images.json",
        {"owner_id": owner["owner_id"], "images": identities},
    )


def _classifier_private(path, *, maximum=1024 * 1024, public_ca=False):
    """Operator input must be a stable private regular file, not a projection."""
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path:
        raise AssemblyError("classifier_private_path_invalid")
    before = path.lstat()
    identity = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_nlink,
                          s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
            or before.st_nlink != 1 or stat.S_IMODE(before.st_mode) not in ({0o600, 0o644} if public_ca else {0o600})
            or not 0 < before.st_size <= maximum):
        raise AssemblyError("classifier_private_file_invalid")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        if identity(os.fstat(stream.fileno())) != identity(before):
            raise AssemblyError("classifier_private_file_changed")
        raw = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
    if (path.resolve() != path or identity(after) != identity(before)
            or identity(path.lstat()) != identity(before) or len(raw) != before.st_size):
        raise AssemblyError("classifier_private_file_changed")
    return raw


def _classifier_write(path, value, *, replace=False):
    """Journal output stays in the verified parent even across a path swap."""
    path = Path(path)
    raw = value if isinstance(value, bytes) else canonical_json_bytes(value)
    parent = path.parent
    if not path.is_absolute() or parent.resolve() != parent:
        raise AssemblyError("classifier_journal_directory_invalid")
    before = parent.lstat()
    if not stat.S_ISDIR(before.st_mode) or before.st_uid != os.getuid() or stat.S_IMODE(before.st_mode) != 0o700:
        raise AssemblyError("classifier_journal_directory_invalid")
    fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    name = path.name + "." + str(uuid4()) + ".pending" if replace else path.name
    def check():
        current, held = parent.lstat(), os.fstat(fd)
        if parent.resolve() != parent or any((s.st_dev, s.st_ino, s.st_mode, s.st_uid) !=
                (before.st_dev, before.st_ino, before.st_mode, before.st_uid) for s in (current, held)):
            raise AssemblyError("classifier_journal_directory_changed")
    try:
        check()
        with os.fdopen(os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd), "wb") as target:
            target.write(raw); target.flush(); os.fsync(target.fileno())
        check()
        if replace:
            os.replace(name, path.name, src_dir_fd=fd, dst_dir_fd=fd)
        os.fsync(fd)
        check()
    finally:
        os.close(fd)


def _classifier_service_info(deployment, owner):
    _, raw = deployment.kubectl("get", "service", "classifier", "-o", "json", namespace="gr-platform")
    value = json.loads(raw)
    meta, spec = value.get("metadata", {}), value.get("spec", {})
    try:
        address = ipaddress.ip_address(spec["clusterIP"])
    except (KeyError, ValueError):
        raise AssemblyError("classifier_service_invalid") from None
    if (meta.get("name") != "classifier" or meta.get("namespace") != "gr-platform"
            or meta.get("labels", {}).get("guardrails.evolution/owner") != owner["owner_id"]
            or not meta.get("uid") or spec.get("type") != "ClusterIP"
            or spec.get("selector") != {"guardrails.evolution/component": "classifier"}
            or spec.get("ports") != [{"name": "https", "port": 8443, "targetPort": 8443, "protocol": "TCP"}]
            or spec.get("externalIPs") or spec.get("externalName")
            or address.is_unspecified or address.is_multicast or address.is_loopback
            or not address.is_private):
        raise AssemblyError("classifier_service_invalid")
    return str(address)


def classifier_service(deployment):
    """Stage only a stable owned endpoint; governance remains the caller's job."""
    with deployment.lock():
        deployment.verify_prepared()
        directory, owner = _state(deployment)
        if not (directory / "access.json").exists():
            raise AssemblyError("classifier_requires_existing_platform")
        deployment.image("evolution-classifier")
        for name in ("tls.crt", "tls.key", "ca.crt"):
            _classifier_private(directory / "tls/gr-platform/classifier" / name)
        values = deployment.read_json(directory / "values.json")
        _desired_images(deployment, copy.deepcopy(values), owner)
        _owned_deployments(deployment, values, owner)
        if not values.get("classifierService"):
            values["classifierService"] = True
            _helm(deployment, directory, values)
        address = _classifier_service_info(deployment, owner)
        _secret_file(_directory(directory / "private") / "classifier.token")
        return {"status": "endpoint_staged_not_governed", "address": address,
                "host": "classifier.gr-platform.svc.cluster.local", "port": 8443,
                "bearer_token_file": str(directory / "private/classifier.token"),
                "ca_file": str(directory / "tls/gr-platform/classifier/ca.crt")}


def _classifier_bundle(deployment, directory, owner, path):
    # Lazy import leaves the original two profiles independent of the optional
    # evaluator loader. The loader performs local validation, never HTTP/DNS.
    from guardrails.evolution.evaluator_config import load_evaluator_endpoints

    raw = _classifier_private(path)
    loaded = load_evaluator_endpoints(path, runtime_zone="local-shared",
                                     external_processing=False, tenant_ids=("team-a", "team-b"))
    value = strict_json_loads(raw)
    if (set(loaded["classifier_endpoints"]) != {"team-a", "team-b"}
            or loaded["bedrock_endpoints"] or value["bedrock"]
            or len(value["classifier"]) != 2):
        raise AssemblyError("classifier_tenant_inventory_invalid")
    address = _classifier_service_info(deployment, owner)
    token = _classifier_private(directory / "private/classifier.token", maximum=4096)
    ca = _classifier_private(directory / "tls/gr-platform/classifier/ca.crt")
    result = copy.deepcopy(value)
    seen, providers, destinations = set(), set(), set()
    for row in result["classifier"]:
        tenant = row["tenant_id"]
        destination, provider = row["destination"], row["provider"]
        if (tenant in seen or tenant not in loaded["classifier_endpoints"]
                or destination["host"] != "classifier.gr-platform.svc.cluster.local"
                or destination["port"] != 8443 or destination["path_prefix"] != "/v1/classifier"
                or destination["cidrs"] != [address + ("/32" if ":" not in address else "/128")]
                or _classifier_private(row["bearer_token_file"], maximum=4096) != token
                or _classifier_private(row["ca_file"], public_ca=True) != ca):
            raise AssemblyError("classifier_operator_binding_mismatch")
        seen.add(tenant)
        providers.add(provider["provider_id"])
        destinations.add(destination["destination_id"])
        row["bearer_token_file"], row["ca_file"] = "/config/classifier.token", "/config/ca.crt"
    if len(providers) != 2 or len(destinations) != 2 or _classifier_private(path) != raw:
        raise AssemblyError("classifier_tenant_binding_changed")
    return canonical_json_bytes(result), token, ca, hashlib.sha256(raw).hexdigest()


def _classifier_probe():
    result = _probe("/v1/classifier/ready", ("/config/token",))
    return ["/opt/classifier/venv/bin/python", "-B", "-s", *result[1:]]


def _classifier_row(deployment, revision):
    row = _component("gr-platform", "classifier", deployment.image("evolution-classifier"),
                     revision, readiness=_classifier_probe(), liveness=_classifier_probe())
    row["resources"]["requests"] = {"cpu": "1500m", "memory": "2048Mi"}
    return row


def _classifier_settings_match(row, pod):
    spec = pod.get("spec", {})
    containers = spec.get("containers", [])
    app = containers[0] if len(containers) == 1 else {}
    initializers = spec.get("initContainers", [])
    initial = initializers[0] if len(initializers) == 1 else {}
    security = {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                "capabilities": {"drop": ["ALL"]}}
    private_script = "import os,pathlib; [pathlib.Path('/'+v+'/data').mkdir(mode=0o700,exist_ok=True) for v in ('private','state')]; [(pathlib.Path('/private/data',p.name).write_bytes(p.read_bytes()),os.chmod('/private/data/'+p.name,0o600)) for p in pathlib.Path('/projected').iterdir() if not p.name.startswith('.')]"
    return (
        spec.get("automountServiceAccountToken") is False and not spec.get("hostNetwork")
        and spec.get("securityContext") == {"runAsNonRoot": True, "runAsUser": 10001, "runAsGroup": 10001,
            "fsGroup": 10001, "seccompProfile": {"type": "RuntimeDefault"}}
        and app.get("name") == "classifier" and app.get("image") == row["image"]
        and not app.get("command") and not app.get("args") and not app.get("env") and not app.get("envFrom")
        and app.get("securityContext") == security
        and _same_limits(app.get("resources", {}).get("requests", {}), row["resources"]["requests"])
        and app.get("readinessProbe", {}).get("exec", {}).get("command") == _classifier_probe()
        and app.get("livenessProbe", {}).get("exec", {}).get("command") == _classifier_probe()
        and app.get("volumeMounts") == [
            {"name": "private", "mountPath": "/config", "subPath": "data", "readOnly": True},
            {"name": "state", "mountPath": "/state", "subPath": "data"},
            {"name": "temporary", "mountPath": "/tmp"}]
        and initial.get("name") == "private-files" and initial.get("command") == ["python", "-B", "-c"]
        and initial.get("args") == [private_script] and initial.get("securityContext") == security
        and next((v for v in spec.get("volumes", []) if v.get("name") == "projected"), None)
        == {"name": "projected", "secret": {"secretName": "classifier-config", "defaultMode": 0o440}}
        and pod.get("metadata", {}).get("annotations", {}).get("guardrails.evolution/config-revision") == row["configRevision"]
    )


def _classifier_secret(deployment, owner, name, namespace, *, optional=False):
    _, raw = deployment.kubectl("get", "secret", name + "-config", "--ignore-not-found", "-o", "json", namespace=namespace)
    if not raw.strip() and optional:
        return None
    value = json.loads(raw)
    meta = value.get("metadata", {})
    if (meta.get("name") != name + "-config" or meta.get("namespace") != namespace
            or not meta.get("uid") or not meta.get("resourceVersion")
            or meta.get("labels", {}).get("guardrails.evolution/owner") != owner["owner_id"]
            or value.get("type") != "Opaque" or type(value.get("data")) is not dict
            or len(canonical_json_bytes(value["data"])) > 1024 * 1024):
        raise AssemblyError("classifier_secret_owner_or_shape")
    return value


def _classifier_secret_apply(deployment, owner, row):
    current = _classifier_secret(deployment, owner, row["name"], row["namespace"], optional=row["before"] is None)
    before, desired = row["before"], row["data"]
    if current is not None and current["data"] == desired:
        if before is not None and current["metadata"]["uid"] != before["metadata"]["uid"]:
            raise AssemblyError("classifier_secret_identity_changed")
        return
    if current != before:
        raise AssemblyError("classifier_secret_snapshot_changed")
    if before is None:
        deployment.kubectl("create", "-f", "-", namespace=row["namespace"], input_text=json.dumps({
            "apiVersion": "v1", "kind": "Secret", "type": "Opaque", "metadata": {
                "name": row["name"] + "-config", "namespace": row["namespace"],
                "labels": {"guardrails.evolution/owner": owner["owner_id"]}}, "data": desired}))
    else:
        meta = before["metadata"]
        ops = [{"op": "test", "path": "/metadata/" + key, "value": meta[key]}
               for key in ("uid", "resourceVersion", "labels")]
        ops += [{"op": "test", "path": "/data", "value": before["data"]},
                {"op": "replace", "path": "/data", "value": desired}]
        deployment.kubectl("patch", "secret", row["name"] + "-config", "--type=json",
                           "--patch-file=/dev/stdin", namespace=row["namespace"], input_text=json.dumps(ops))
    actual = _classifier_secret(deployment, owner, row["name"], row["namespace"])
    if actual["data"] != desired or (before is not None and actual["metadata"]["uid"] != before["metadata"]["uid"]):
        raise AssemblyError("classifier_secret_write_unconfirmed")


def _classifier_capacity(values):
    # Four existing probe limits are reserved even if a probe is absent. Pod
    # init limits are lower than each enabled application's steady limits.
    cpu, memory = Decimal("0.2"), Decimal(192)
    for row in values["components"].values():
        if not row["enabled"]:
            continue
        limit = row["resources"]["limits"]
        if not re.fullmatch(r"[0-9]+m", limit["cpu"]) or not re.fullmatch(r"[0-9]+Mi", limit["memory"]):
            raise AssemblyError("classifier_capacity_units")
        cpu += Decimal(limit["cpu"][:-1]) / 1000
        memory += Decimal(limit["memory"][:-2])
        if row.get("postgresTunnel"):
            cpu += Decimal("0.05"); memory += 64
        if row.get("sidecar"):
            cpu += Decimal("0.1"); memory += 96
    if cpu > 6 or memory > 8192 or (values["components"].get("classifier", {}).get("enabled")
                                   and values["components"]["ollama"]["enabled"]):
        raise AssemblyError("classifier_capacity_exceeded")
    return {"cpu_millicores": int(cpu * 1000), "memory_mib": int(memory)}


_HEAVY = {"runtime-1", "runtime-2", "gateway-team-a", "gateway-team-b", "ollama", "classifier"}


def _classifier_drain(deployment, values, owner, *, timeout=180):
    deadline = time.monotonic() + timeout
    while True:
        live = _owned_deployments(deployment, values, owner)
        remaining = bool(_HEAVY & set(live))
        for namespace in ("gr-platform", "gr-team-a", "gr-team-b", "gr-model"):
            _, raw = deployment.kubectl("get", "pods", "-o", "json", namespace=namespace, timeout=15)
            rows = json.loads(raw)["items"]
            if len(rows) > 128:
                raise AssemblyError("classifier_drain_inventory_limit")
            remaining |= any(row.get("metadata", {}).get("labels", {}).get("guardrails.evolution/instance") in _HEAVY for row in rows)
        if not remaining:
            return
        if time.monotonic() >= deadline:
            raise AssemblyError("classifier_profile_drain_timeout")
        time.sleep(1)


def _classifier_plan(deployment, directory, owner, current, saved, profile, config_path, credentials):
    desired = copy.deepcopy(saved)
    desired["classifierService"] = True
    desired["evaluatorProfile"] = profile
    desired["components"]["ollama"]["enabled"] = profile == "local_model"
    for name in ("runtime-1", "runtime-2", "gateway-team-a", "gateway-team-b"):
        desired["components"][name]["enabled"] = True
    updates = []
    def capture(_d, _owner, name, namespace, files):
        before = _classifier_secret(deployment, owner, name, namespace, optional=name == "classifier")
        data = {key: base64.b64encode(raw).decode() for key, raw in sorted(files.items())}
        updates.append({"name": name, "namespace": namespace, "before": before, "data": data})
        return hashlib.sha256(canonical_json_bytes(data)).hexdigest()
    bundle, token, ca, input_digest = (None, None, None, None)
    if profile == "classifier":
        bundle, token, ca, input_digest = _classifier_bundle(deployment, directory, owner, config_path)
        files = {name: _classifier_private(directory / "tls/gr-platform/classifier" / name)
                 for name in ("tls.crt", "tls.key", "ca.crt")}
        files["token"] = token
        desired["components"]["classifier"] = _classifier_row(deployment, capture(deployment, owner, "classifier", "gr-platform", files))
    elif "classifier" in desired["components"]:
        desired["components"]["classifier"]["enabled"] = False
    for name in ("runtime-1", "runtime-2"):
        before = _classifier_secret(deployment, owner, name, "gr-platform")
        files = {key: base64.b64decode(raw, validate=True) for key, raw in before["data"].items()}
        settings = strict_json_loads(files["service.json"])
        if settings.get("evaluator_config_file") not in (None, "/config/evaluators.json"):
            raise AssemblyError("classifier_runtime_configuration_unexpected")
        if profile == "classifier":
            if files["ca.crt"] != ca:
                raise AssemblyError("classifier_runtime_trust_mismatch")
            settings["evaluator_config_file"] = "/config/evaluators.json"
            files.update({"evaluators.json": bundle, "classifier.token": token})
        else:
            settings.pop("evaluator_config_file", None)
            files.pop("evaluators.json", None)
            files.pop("classifier.token", None)
        files["service.json"] = canonical_json_bytes(settings)
        desired["components"][name]["configRevision"] = capture(deployment, owner, name, "gr-platform", files)
    inventory = json.loads(seed.private_read(current["seed_inventory_file"]))
    if credentials is not None:
        inventory = credentials.resolve_inventory(directory, owner, inventory)
    mock_token = seed.private_read(current["mock_token_file"]).decode().strip()
    for tenant, item in inventory["tenants"].items():
        desired["components"]["gateway-" + tenant] = _gateway(deployment, directory, owner, tenant, item, profile, mock_token, secret_writer=capture)
    _desired_images(deployment, desired, owner)
    budget = _classifier_capacity(desired)
    live = _owned_deployments(deployment, saved, owner)
    identities = {}
    for name, row in live.items():
        meta = row["metadata"]
        if not meta.get("uid") or not meta.get("resourceVersion") or row["spec"].get("replicas") != 1:
            raise AssemblyError("classifier_transition_identity_missing")
        identities[name] = {"uid": meta["uid"], "spec": row["spec"]}
    return {"schema": "classifier-profile-transition-v1", "owner": owner,
            "profile": profile, "input_sha256": input_digest, "before": saved,
            "desired": desired, "secrets": updates, "deployments": identities, "budget": budget}


def _classifier_transition(deployment, directory, owner, current, saved, profile, config_path, credentials):
    root = _directory(directory / "classifier-transitions")
    active = root / "active.json"
    pending = None
    if active.exists():
        pointer = strict_json_loads(_classifier_private(active, maximum=1024))
        if set(pointer) != {"id", "plan_sha256"} or str(UUID(pointer["id"])) != pointer["id"]:
            raise AssemblyError("classifier_transition_pointer_invalid")
        attempt = root / pointer["id"]
        raw = _classifier_private(attempt / "plan.json", maximum=4 * 1024 * 1024)
        if hashlib.sha256(raw).hexdigest() != pointer["plan_sha256"]:
            raise AssemblyError("classifier_transition_plan_changed")
        if not (attempt / "complete.json").exists():
            pending = strict_json_loads(raw)
        else:
            completed = strict_json_loads(_classifier_private(attempt / "complete.json"))
            previous = strict_json_loads(raw)
            if completed != {"status": "profile_ready", "profile": previous["profile"],
                    "values_sha256": hashlib.sha256(canonical_json_bytes(previous["desired"])).hexdigest()}:
                raise AssemblyError("classifier_transition_completion_changed")
    if pending is None:
        plan = _classifier_plan(deployment, directory, owner, current, saved, profile, config_path, credentials)
        if (saved == plan["desired"] and current["profile"] == profile
                and all(row["before"] is not None and row["before"]["data"] == row["data"] for row in plan["secrets"])):
            return saved, None
        attempt = _directory(root / str(uuid4()))
        raw = canonical_json_bytes(plan)
        if len(raw) > 4 * 1024 * 1024:
            raise AssemblyError("classifier_transition_plan_size")
        _classifier_write(attempt / "plan.json", raw)
        _classifier_write(active, {"id": attempt.name, "plan_sha256": hashlib.sha256(raw).hexdigest()}, replace=active.exists())
    else:
        plan = pending
    if (plan["owner"] != owner or plan["profile"] != profile
            or (profile == "classifier" and _classifier_bundle(deployment, directory, owner, config_path)[3] != plan["input_sha256"])):
        raise AssemblyError("classifier_transition_binding_changed")
    desired = plan["desired"]
    expected = copy.deepcopy(desired)
    if _desired_images(deployment, expected, owner) or expected != desired:
        raise AssemblyError("classifier_transition_images_changed")
    _classifier_capacity(desired)
    quiet = copy.deepcopy(plan["before"])
    for name in _HEAVY & set(quiet["components"]):
        quiet["components"][name]["enabled"] = False
    retained = deployment.read_json(directory / "values.json")
    if retained not in (plan["before"], quiet, desired):
        raise AssemblyError("classifier_transition_values_changed")
    if not (attempt / "drained.json").exists():
        live = _owned_deployments(deployment, retained, owner)
        for name, row in live.items():
            if plan["deployments"].get(name) != {"uid": row["metadata"].get("uid"), "spec": row["spec"]}:
                raise AssemblyError("classifier_transition_live_changed")
        stop_forwards(deployment)
        _helm(deployment, directory, quiet)
        _classifier_drain(deployment, quiet, owner)
        _classifier_write(attempt / "drained.json", {"plan_sha256": hashlib.sha256(raw).hexdigest()})
    else:
        if strict_json_loads(_classifier_private(attempt / "drained.json")) != {"plan_sha256": hashlib.sha256(raw).hexdigest()}:
            raise AssemblyError("classifier_transition_drain_changed")
        if retained == quiet:
            _classifier_drain(deployment, quiet, owner)
        elif retained != desired:
            raise AssemblyError("classifier_transition_stage_ambiguous")
        else:
            live = _owned_deployments(deployment, desired, owner)
            for name in _HEAVY & set(live):
                row = desired["components"].get(name, {})
                template = live[name]["spec"]["template"]
                apps = [c for c in template["spec"]["containers"] if c["name"] == row.get("component")]
                if (not row.get("enabled") or len(apps) != 1 or apps[0]["image"] != row["image"]
                        or not _same_limits(apps[0].get("resources", {}).get("limits", {}), row["resources"]["limits"])
                        or template.get("metadata", {}).get("annotations", {}).get("guardrails.evolution/config-revision") != row["configRevision"]):
                    raise AssemblyError("classifier_transition_workload_changed")
    for row in plan["secrets"]:
        _classifier_secret_apply(deployment, owner, row)
    _helm(deployment, directory, desired)
    return desired, attempt


def platform(deployment, *, profile="deterministic_mock", evaluator_config_file=None):
    if profile not in {"deterministic_mock", "local_model", "classifier"}:
        raise AssemblyError("platform_profile_invalid")
    if (profile == "classifier") != (evaluator_config_file is not None):
        raise AssemblyError("classifier_explicit_config_required")
    with deployment.lock():
        deployment.verify_prepared()
        directory, owner = _state(deployment)
        credentials = None
        renewals = directory / "credential-renewals"
        if renewals.exists() or renewals.is_symlink():
            credentials = deployment.support("credentials")
            try:
                credentials.ensure_available(directory, owner)
            except credentials.CredentialRenewalError as exc:
                raise AssemblyError(str(exc)) from None
        _network_baseline(deployment, owner)
        relocation = _prepare_management_relocation(deployment, directory, owner)
        ports = _port_record(deployment, directory)
        # Completed platform replay never rewrites governance or waits for the
        # original Observe release after the operator has advanced its binding.
        access_path = directory / "access.json"
        if access_path.exists():
            current = json.loads(seed.private_read(access_path))
            changed_profile = current["profile"] != profile
            values = deployment.read_json(directory / "values.json")
            classifier_attempt = None
            if profile == "classifier" or current["profile"] == "classifier" or "classifier" in values.get("components", {}) or (directory / "classifier-transitions/active.json").exists():
                values, classifier_attempt = _classifier_transition(deployment, directory, owner, current, values, profile, evaluator_config_file, credentials)
                # Profile configuration, budgets and convergence are already
                # journaled above. The shared verification path remains below.
                profile_configured = True
            else:
                profile_configured = False
            changed_images = _desired_images(deployment, values, owner)
            # Refuse foreign targets before profile configuration writes too.
            _owned_deployments(deployment, values, owner)
            if changed_profile and not profile_configured:
                inventory = json.loads(
                    seed.private_read(current["seed_inventory_file"])
                )
                if credentials is not None:
                    try:
                        inventory = credentials.resolve_inventory(directory, owner, inventory)
                    except credentials.CredentialRenewalError as exc:
                        raise AssemblyError(str(exc)) from None
                token = seed.private_read(current["mock_token_file"]).decode().strip()
                values["components"]["ollama"]["enabled"] = profile == "local_model"
                for tenant, value in inventory["tenants"].items():
                    values["components"]["gateway-" + tenant] = _gateway(
                        deployment, directory, owner, tenant, value, profile, token
                    )
            _reconcile_application_images(
                deployment,
                directory,
                values,
                owner,
                changed=(changed_profile and not profile_configured) or changed_images,
            )
            _finish_management_relocation(deployment, directory, owner, relocation)
            if profile == "local_model":
                _forward(deployment, directory, "ollama", ports["ollama"])
                _warm_model(deployment, current["ollama_url"], current["ca_file"])
            if changed_profile:
                current["profile"] = profile
                _write(access_path, current)
            for name, port in ports.items():
                if name != "ollama" or profile == "local_model":
                    _forward(deployment, directory, name, port)
            for value in current["tenants"].values():
                _https_ready(
                    value["gateway_url"] + "/health/ready",
                    current["ca_file"],
                    token=seed.private_read(value["gateway_token_file"])
                    .decode()
                    .strip(),
                    timeout=30,
                )
            if classifier_attempt is not None:
                _classifier_write(classifier_attempt / "complete.json", {
                    "status": "profile_ready", "profile": profile,
                    "values_sha256": hashlib.sha256(canonical_json_bytes(values)).hexdigest()})
            return {
                "status": "ready",
                "profile": profile,
                "tenant_count": 2,
                "runtime_count": 4,
                "access_file": str(access_path),
            }
        if profile == "classifier":
            raise AssemblyError("classifier_requires_existing_platform")
        # A partial assembly may roll any Recreate workload below. kubectl binds
        # a forward to one selected pod; an alive local listener cannot follow
        # its replacement. Retire only validated owned processes before rollout.
        stop_forwards(deployment)
        ca_file = deployment.state / "credentials/ca.crt"
        values_path = directory / "values.json"
        values = (
            deployment.read_json(values_path)
            if values_path.exists()
            else {
                "ownerId": owner["owner_id"],
                "initImage": deployment.image("python"),
                "servicesOnly": True,
                "components": {},
            }
        )
        if values["ownerId"] != owner["owner_id"]:
            raise AssemblyError("platform_chart_owner_mismatch")
        if "postgres" in values["components"]:
            _owned_deployments(deployment, values, owner)
            _postgres_security(deployment, directory, values, owner)
        _helm(deployment, directory, values)
        addresses = {}
        for name in ("mock", "ollama"):
            _, result = deployment.kubectl(
                "get", "service", name, "-o", "json", namespace="gr-model"
            )
            addresses[name] = json.loads(result)["spec"]["clusterIP"]
        private = _directory(directory / "private")
        owner_password = _secret_file(private / "database-owner.token")
        app_password = _secret_file(private / "database-app.token")
        model_token = _secret_file(private / "mock-model.token")
        _secret_file(private / "mock-control.token")
        pg_files = _tls(deployment, directory, "postgres", "gr-platform")
        pg_files["owner-password"] = owner_password.encode()
        pg_files["postgres_tunnel.py"] = (
            deployment.root / "deploy/evolution/postgres_tunnel.py"
        ).read_bytes()
        revision = _postgres_configuration(deployment, owner, pg_files)
        values["servicesOnly"] = False
        values["components"]["postgres"] = _component(
            "gr-platform",
            "postgres",
            deployment.image("postgres"),
            revision,
            uid=999,
            env={
                "POSTGRES_USER": "evolution_owner",
                "POSTGRES_DB": "guardrails_evolution",
                "POSTGRES_PASSWORD_FILE": "/config/owner-password",
            },
            args=POSTGRES_ARGS,
            readiness=POSTGRES_READINESS,
            postgres_tunnel={"image": deployment.image("python")},
        )
        _helm(deployment, directory, values)
        _ready(deployment, "postgres", "gr-platform")
        _forward(deployment, directory, "postgres", ports["postgres"])
        for name, user, password, host, port, ca in (
            (
                "database-owner-url",
                "evolution_owner",
                owner_password,
                "localhost",
                ports["postgres"],
                str(ca_file),
            ),
            (
                "database-app-url",
                "evolution_app",
                app_password,
                "localhost",
                ports["postgres"],
                str(ca_file),
            ),
            (
                "database-cluster-url",
                "evolution_app",
                app_password,
                "postgres.gr-platform.svc.cluster.local",
                5432,
                "/config/ca.crt",
            ),
        ):
            url = URL.create(
                "postgresql+psycopg",
                username=user,
                password=password,
                host=host,
                port=port,
                database="guardrails_evolution",
                query={"sslmode": "verify-full", "sslrootcert": ca},
            )
            _write(private / name, url.render_as_string(hide_password=False).encode())
        from evolution.run import signing_key

        _, key_id = signing_key(deployment.root)
        spec = seed.SeedSpec.model_validate(
            {
                "database_url_file": str(private / "database-app-url"),
                "owner_database_url_file": str(private / "database-owner-url"),
                "management_url": "https://localhost:" + str(ports["management"]),
                "ca_file": str(ca_file),
                "evidence_public_key_file": str(
                    deployment.root / ".evolution/secrets/acceptance-ed25519.pub"
                ),
                "evidence_key_id": key_id,
                "mock": {
                    "host": "mock.gr-model.svc.cluster.local",
                    "addresses": [addresses["mock"]],
                    "path_prefix": "/v1",
                    "model_id": "gpt-4o-evolution-mock",
                },
                "ollama": {
                    "host": "ollama.gr-model.svc.cluster.local",
                    "addresses": [addresses["ollama"]],
                    "path_prefix": "/api",
                    "model_id": "qwen2.5:0.5b",
                },
            }
        )
        _write(directory / "seed-spec.json", spec.model_dump(mode="json"))
        initial = seed.bootstrap(spec, directory / "seed")
        files = _tls(deployment, directory, "management", "gr-platform")
        files.update(
            {
                "database-url": seed.private_read(private / "database-cluster-url"),
                "snapshot-signing.key": seed.private_read(
                    initial["snapshot_private_key_file"]
                ),
                "service.json": canonical_json_bytes(
                    _service_config(str(uuid4()), signing=True)
                ),
            }
        )
        # The management runtime_id is an inert service identity; retain it on replay.
        stored_config = directory / "management-config.json"
        if stored_config.exists():
            files["service.json"] = seed.private_read(stored_config)
        else:
            _write(stored_config, files["service.json"])
        revision = _config_secret(deployment, owner, "management", "gr-platform", files)
        values["components"]["management"] = _component(
            "gr-platform",
            "management",
            deployment.image("evolution-service"),
            revision,
            command=["python", "/app/application.py", "management"],
            readiness=_probe("/health/live"),
            liveness=_probe("/health/live"),
        )
        _helm(deployment, directory, values)
        _ready(deployment, "management", "gr-platform")
        _forward(deployment, directory, "management", ports["management"])
        _https_ready(spec.management_url + "/health/live", ca_file)
        inventory = seed.configure(spec, directory / "seed")
        runtime_access = {}
        for index in (1, 2):
            name = "runtime-" + str(index)
            files = _tls(deployment, directory, "runtime", "gr-platform")
            slots = []
            for tenant, value in inventory["tenants"].items():
                identity = value["runtimes"][name]
                files[tenant + ".token"] = seed.private_read(identity["token_file"])
                slots.append(
                    {
                        "tenant_id": tenant,
                        "runtime_id": identity["runtime_id"],
                        "url": "https://management.gr-platform.svc.cluster.local:8443",
                        "ca_file": "/config/ca.crt",
                        "credential_file": "/config/" + tenant + ".token",
                        "state_file": "/state/" + tenant + ".json",
                        "trust_key_files": {
                            inventory["snapshot_key_id"]: "/config/snapshot-signing.pub"
                        },
                        "trust_key_expiries": {},
                    }
                )
            files.update(
                {
                    "database-url": seed.private_read(private / "database-cluster-url"),
                    "snapshot-signing.pub": seed.private_read(
                        inventory["snapshot_public_key_file"]
                    ),
                    "service.json": canonical_json_bytes(
                        _service_config(slots[0]["runtime_id"])
                    ),
                    "runtime-slots.json": canonical_json_bytes(slots),
                }
            )
            revision = _config_secret(deployment, owner, name, "gr-platform", files)
            values["components"][name] = _component(
                "gr-platform",
                "runtime",
                deployment.image("evolution-service"),
                revision,
                command=["python", "/app/application.py", "runtime"],
                readiness=_probe(
                    "/health/ready",
                    tuple(
                        "/config/" + value["tenant_id"] + ".token" for value in slots
                    ),
                ),
                liveness=_probe("/health/live"),
            )
            runtime_access[name] = {
                "namespace": "gr-platform",
                "runtime_ids": {
                    item["tenant_id"]: item["runtime_id"] for item in slots
                },
            }
        files = _tls(deployment, directory, "mock", "gr-model")
        files.update(
            {
                "model.token": model_token.encode(),
                "control.token": seed.private_read(private / "mock-control.token"),
            }
        )
        revision = _config_secret(deployment, owner, "mock", "gr-model", files)
        values["components"]["mock"] = _component(
            "gr-model",
            "mock",
            deployment.image("evolution-service"),
            revision,
            command=[
                "python",
                "-m",
                "uvicorn",
                "proof.mock_model:create_app",
                "--factory",
                "--host",
                "0.0.0.0",
                "--port",
                "8443",
                "--ssl-certfile",
                "/config/tls.crt",
                "--ssl-keyfile",
                "/config/tls.key",
                "--no-access-log",
            ],
            env={
                "GUARDRAILS_PROOF_PROFILE": "integration",
                "GUARDRAILS_PROOF_CONTROL_TOKEN_FILE": "/config/control.token",
                "GUARDRAILS_MOCK_TOKEN_FILE": "/config/model.token",
            },
            readiness=_probe("/health/live"),
        )
        files = _tls(deployment, directory, "ollama", "gr-model")
        files["Caddyfile"] = (
            b"{ admin off\n auto_https off\n}\n:8443 {\n tls /config/tls.crt /config/tls.key\n reverse_proxy 127.0.0.1:11434\n}\n"
        )
        revision = _config_secret(deployment, owner, "ollama", "gr-model", files)
        values["components"]["ollama"] = _component(
            "gr-model",
            "ollama",
            deployment.image("evolution-model"),
            revision,
            command=["ollama", "serve"],
            env=dict(MODEL_ENV),
            sidecar={"image": deployment.image("caddy")},
        )
        values["components"]["ollama"]["enabled"] = profile == "local_model"
        _helm(deployment, directory, values)
        for name in ("runtime-1", "runtime-2", "mock", "ollama"):
            if name == "ollama" and profile != "local_model":
                continue
            _ready(
                deployment,
                name,
                "gr-model" if name in {"mock", "ollama"} else "gr-platform",
            )
        seed.wait_applied(spec, directory / "seed", timeout=120)
        for tenant, value in inventory["tenants"].items():
            values["components"]["gateway-" + tenant] = _gateway(
                deployment, directory, owner, tenant, value, profile, model_token
            )
        _helm(deployment, directory, values)
        for tenant in inventory["tenants"]:
            _ready(deployment, "gateway-" + tenant, "gr-" + tenant, timeout=240)
        for name, port in ports.items():
            if name != "ollama" or profile == "local_model":
                _forward(deployment, directory, name, port)
        if profile == "local_model":
            _warm_model(
                deployment, "https://localhost:" + str(ports["ollama"]), ca_file
            )
        _cpu_donors(deployment, directory, values)
        identities = _verify_application_images(deployment, values, owner)
        _write(
            directory / "running-images.json",
            {"owner_id": owner["owner_id"], "images": identities},
        )
        value = {
            "schema_version": "guardrails-evolution-local-access-v1",
            "profile": profile,
            "ca_file": str(ca_file),
            "management_url": spec.management_url,
            "runtime_url": "https://localhost:" + str(ports["runtime"]),
            "mock_url": "https://localhost:" + str(ports["mock"]),
            "ollama_url": "https://localhost:" + str(ports["ollama"]),
            "mock_control_token_file": str(private / "mock-control.token"),
            "mock_token_file": str(private / "mock-model.token"),
            "seed_inventory_file": str(directory / "seed/inventory.json"),
            "database_url_file": str(private / "database-app-url"),
            "runtimes": runtime_access,
            "tenants": {
                tenant: {
                    "gateway_url": "https://localhost:" + str(ports[tenant]),
                    "gateway_token_file": item["gateway_token_file"],
                    "workload_token_file": item["roles"]["workload"]["token_file"],
                    "model_alias": "guarded-chat-" + tenant.removeprefix("team-"),
                }
                for tenant, item in inventory["tenants"].items()
            },
        }
        _write(directory / "access.json", value)
        return {
            "status": "ready",
            "profile": profile,
            "tenant_count": 2,
            "runtime_count": 4,
            "access_file": str(directory / "access.json"),
        }


def access(deployment):
    with deployment.lock():
        directory, owner = _state(deployment)
        renewals = directory / "credential-renewals"
        if renewals.exists() or renewals.is_symlink():
            credentials = deployment.support("credentials")
            try:
                credentials.ensure_available(directory, owner)
            except credentials.CredentialRenewalError as exc:
                raise AssemblyError(str(exc)) from None
        receipt = _relocation_receipt(directory, owner)
        if receipt and receipt[1]["status"] == "pending":
            raise AssemblyError("forward_relocation_pending")
        path = directory / "access.json"
        if not path.exists():
            raise AssemblyError("platform_not_assembled")
        value = json.loads(seed.private_read(path))
        ports = _port_record(deployment, directory)
        if receipt:
            _, after = _relocation_transition(receipt[1])
            if ports["management"] != after["ports"]["management"] or (
                value["management_url"] != after["access"]["management_url"]
            ):
                raise AssemblyError("forward_relocation_state_changed")
        for name, port in ports.items():
            if name != "ollama" or value["profile"] == "local_model":
                _forward(deployment, directory, name, port)
        return {
            "status": "available",
            "access_file": str(path),
            "management_url": value["management_url"],
            "gateways": {
                tenant: row["gateway_url"] for tenant, row in value["tenants"].items()
            },
        }
