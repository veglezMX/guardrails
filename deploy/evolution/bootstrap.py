"""Task-owned local Kubernetes deployment; network acquisition is prepare-only."""

from __future__ import annotations

import argparse
import base64
import contextlib
import fcntl
import hashlib
import importlib.util
import json
import os
import platform
import re
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
PROFILE = "guardrails-evolution"
KUBERNETES = "v1.35.1"
NODE_SUBNET = "192.168.51.0/24"
NETWORK = PROFILE + "-isolated"
DNS_ANCHOR = PROFILE + "-dns-anchor"
POD_SUBNET = "10.244.0.0/16"
SERVICE_SUBNET = "10.96.0.0/12"


class DeploymentError(RuntimeError):
    """Safe machine-readable deployment failure; never include command output."""


def _hash_file(path: Path) -> tuple[str, int]:
    hasher = hashlib.sha256()
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise DeploymentError("hash_requires_regular_file")
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
        after = os.fstat(handle.fileno())
        current = path.lstat()
        fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
        identity = tuple(getattr(before, name) for name in fields)
        if any(tuple(getattr(value, name) for name in fields) != identity
               for value in (after, current)):
            raise DeploymentError("file_changed_during_hash")
    return hasher.hexdigest(), before.st_size


def sha256(path: Path) -> str:
    return _hash_file(path)[0]


def install_binary(source: Path, target: Path, expected_digest: str):
    """Reuse exact running executables; replace changed bytes by atomic rename."""
    if target.exists() or target.is_symlink():
        current = target.lstat()
        if not stat.S_ISREG(current.st_mode) or current.st_uid != os.getuid():
            raise DeploymentError("prepared_binary_target_invalid")
        if stat.S_IMODE(current.st_mode) == 0o700 and sha256(target) == expected_digest:
            return
    temporary = target.with_name("." + target.name + "-" + uuid4().hex)
    try:
        source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(source_fd, "rb") as input_file:
            if not stat.S_ISREG(os.fstat(input_file.fileno()).st_mode):
                raise DeploymentError("prepared_binary_source_invalid")
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as output_file:
                shutil.copyfileobj(input_file, output_file)
                output_file.flush()
                os.fchmod(output_file.fileno(), 0o700)
                os.fsync(output_file.fileno())
        if sha256(temporary) != expected_digest:
            raise DeploymentError("prepared_binary_integrity_failed")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path: Path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise DeploymentError("duplicate_json_key")
            result[key] = value
        return result

    try:
        return json.loads(
            path.read_text(),
            object_pairs_hook=unique,
            parse_constant=lambda _: (_ for _ in ()).throw(
                DeploymentError("nonfinite_json")
            ),
        )
    except OSError, ValueError:
        raise DeploymentError("invalid_json_file") from None


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name("." + path.name + "-" + uuid4().hex)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        json.dump(value, handle, sort_keys=True, indent=2, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def command(
    argv, *, env=None, timeout=120, output_file=None, check=True, input_text=None,
    input_stream=None,
):
    """Bound the complete process group; return output only to trusted callers."""
    if input_stream is not None and input_text is not None:
        raise DeploymentError("conflicting_command_input")
    with contextlib.ExitStack() as stack:
        output = (
            stack.enter_context(output_file.open("ab"))
            if output_file
            else subprocess.PIPE
        )
        process = subprocess.Popen(
            [str(x) for x in argv],
            env=env,
            stdin=input_stream if input_stream is not None else (
                subprocess.PIPE if input_text else subprocess.DEVNULL
            ),
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            stdout, _ = process.communicate(
                input_text.encode() if input_text else None, timeout=timeout
            )
        except BaseException:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise
        if check and process.returncode:
            raise DeploymentError("command_failed_" + Path(str(argv[0])).name)
        return process.returncode, stdout.decode(errors="replace") if stdout else ""


class LocalDeployment:
    def __init__(self, root=ROOT, *, state=None):
        self.root = Path(root).resolve()
        self.state = Path(
            state
            or os.environ.get(
                "EVOLUTION_DEPLOYMENT_STATE", self.root / ".evolution/deployment"
            )
        ).absolute()
        if self.state.is_symlink():
            raise DeploymentError("deployment_state_symlink")
        self.home = self.state / ".minikube"
        self.kubeconfig = self.state / "kubeconfig"
        self.bin = self.state / "bin"
        self.downloads = self.state / "downloads"
        self.env = dict(
            os.environ,
            MINIKUBE_HOME=str(self.home),
            KUBECONFIG=str(self.kubeconfig),
            MINIKUBE_IN_STYLE="false",
            MINIKUBE_WANTUPDATENOTIFICATION="false",
        )

    def run(self, argv, *, timeout=120, check=True, input_text=None, output_file=None,
            input_stream=None):
        return command(
            argv,
            env=self.env,
            timeout=timeout,
            check=check,
            input_text=input_text,
            output_file=output_file,
            input_stream=input_stream,
        )

    @staticmethod
    def write_json(path, value):
        atomic_json(Path(path), value)

    @staticmethod
    def read_json(path):
        return read_json(Path(path))

    @staticmethod
    def sha256(path):
        return sha256(Path(path))

    def support(self, name):
        if name not in {
            "certificates",
            "assembly",
            "build_images",
            "node_integrity",
            "image_identity",
            "resource_budget",
            "credentials",
            "readonly_access",
            "gateway_routes",
        }:
            raise DeploymentError("unknown_deployment_support")
        key = "evolution_deployment_" + name
        module_path = self.root / "deploy/evolution" / (name + ".py")
        spec = importlib.util.spec_from_file_location(key, module_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
        return module

    def image(self, name):
        items = (
            self.specs()["images"]
            + read_json(self.root / "deploy/evolution/artifacts.lock.json")["artifacts"]
        )
        matches = [
            item for item in items if item["name"] == name and item["kind"] == "image"
        ]
        if not matches or len({item["source_url"] for item in matches}) != 1:
            raise DeploymentError("prepared_image_name_missing_or_ambiguous")
        self.inspect_image(matches[0])
        return matches[0]["source_url"].removeprefix("docker://")

    def platform(self, *, profile="deterministic_mock", evaluator_config_file=None):
        module = self.support("assembly")
        try:
            options = {} if evaluator_config_file is None else {
                "evaluator_config_file": Path(evaluator_config_file)
            }
            return module.platform(self, profile=profile, **options)
        except module.AssemblyError as exc:
            raise DeploymentError(str(exc)) from None
        except Exception:  # noqa: BLE001 — public boundary must not expose private configuration
            raise DeploymentError("platform_operation_failed") from None

    def classifier_service(self):
        module = self.support("assembly")
        try:
            return module.classifier_service(self)
        except module.AssemblyError as exc:
            raise DeploymentError(str(exc)) from None
        except Exception:  # noqa: BLE001 — finite public error must not expose private tokens
            raise DeploymentError("classifier_service_operation_failed") from None

    def access(self, *, read_only=False, deadline=None):
        if type(read_only) is not bool:
            raise DeploymentError("access_observation_invalid")
        if read_only:
            observation = self.support("readonly_access")
            try:
                return observation.observe(self, deadline=deadline)
            except observation.ReadOnlyAccessError as exc:
                raise DeploymentError(str(exc)) from None
            except Exception:  # noqa: BLE001 - no private metadata or native exception detail may escape
                raise DeploymentError("access_observation_failed") from None
        if deadline is not None:
            raise DeploymentError("access_observation_invalid")
        module = self.support("assembly")
        try:
            return module.access(self)
        except module.AssemblyError as exc:
            raise DeploymentError(str(exc)) from None
        except Exception:  # noqa: BLE001 — public boundary must not expose private configuration
            raise DeploymentError("access_operation_failed") from None

    def renew_credentials(self, *, renewal_id, expires_at, recover_lost=False):
        module = self.support("credentials")
        try:
            return module.renew(self, renewal_id=renewal_id, expires_at=expires_at, recover_lost=recover_lost)
        except module.CredentialRenewalError as exc:
            raise DeploymentError(str(exc)) from None
        except Exception:  # noqa: BLE001 — no secret, URL or infrastructure exception may escape
            raise DeploymentError("credential_renewal_failed") from None

    @contextlib.contextmanager
    def lock(self):
        self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.state.stat().st_uid != os.getuid() or self.state.stat().st_mode & 0o077:
            raise DeploymentError("deployment_state_not_private")
        descriptor = os.open(
            self.state / ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        except BlockingIOError:
            raise DeploymentError("deployment_busy") from None
        finally:
            os.close(descriptor)

    def specs(self):
        lock = read_json(self.root / "deploy/evolution/preparation.lock.json")
        if lock.get("schema_version") != "guardrails-evolution-preparation-v1":
            raise DeploymentError("preparation_schema_invalid")
        names = set()
        for item in lock["files"] + lock["images"]:
            if item["name"] in names or not re.fullmatch(
                "[0-9a-f]{64}", item["sha256"]
            ):
                raise DeploymentError("artifact_pin_invalid")
            names.add(item["name"])
            if not item.get("license") or not item.get("version"):
                raise DeploymentError("artifact_metadata_missing")
            if item["kind"] == "image":
                if not item["source_url"].startswith("docker://") or not item[
                    "source_url"
                ].endswith("@sha256:" + item["sha256"]):
                    raise DeploymentError("image_pin_invalid")
            elif (
                not item["source_url"].startswith("https://")
                or Path(item["filename"]).name != item["filename"]
            ):
                raise DeploymentError("artifact_url_invalid")
        return lock

    def inspect_image(self, item):
        ref = item["source_url"].removeprefix("docker://")
        code, output = command(["docker", "image", "inspect", ref], check=False)
        if code:
            raise DeploymentError("prepared_image_missing")
        image = json.loads(output)[0]
        pin = "@sha256:" + item["sha256"]
        if image["Id"] != "sha256:" + item["sha256"] and not any(
            x.endswith(pin) for x in image.get("RepoDigests", [])
        ):
            raise DeploymentError("prepared_image_mismatch")
        if item.get("size_bytes") is not None and item["size_bytes"] != image["Size"]:
            raise DeploymentError("prepared_image_size_mismatch")
        return image

    def owner(self, *, create=False):
        marker = self.state / "owner.json"
        existing = read_json(marker) if marker.exists() else None
        code, output = command(["docker", "container", "inspect", PROFILE], check=False)
        container = json.loads(output)[0] if code == 0 else None
        if existing:
            if (
                existing.get("profile") != PROFILE
                or existing.get("uid") != os.getuid()
                or existing.get("state") != str(self.state)
            ):
                raise DeploymentError("profile_owner_mismatch")
            if container and existing.get("container_id") != container["Id"]:
                raise DeploymentError("profile_container_collision")
            return existing
        if container or (self.home / "profiles" / PROFILE).exists():
            raise DeploymentError("profile_collision")
        if not create:
            raise DeploymentError("profile_not_owned")
        value = {
            "profile": PROFILE,
            "uid": os.getuid(),
            "state": str(self.state),
            "container_id": None,
            "owner_id": uuid4().hex,
        }
        atomic_json(marker, value)
        return value

    def preflight(self, *, starting=True):
        self.owner(create=False)
        if platform.system() != "Linux" or platform.machine() != "x86_64":
            raise DeploymentError("unsupported_host_platform")
        memory = dict(
            line.split(":", 1)
            for line in Path("/proc/meminfo").read_text().splitlines()
        )
        available = int(memory["MemAvailable"].strip().split()[0]) * 1024
        free = shutil.disk_usage(self.state).free
        if starting and available < 10 * 1024**3:
            raise DeploymentError("host_memory_below_10_gib")
        if free < 30 * 1024**3:
            raise DeploymentError("host_disk_below_30_gib")
        if (os.cpu_count() or 0) < 6:
            raise DeploymentError("host_cpu_below_6")
        _, raw = command(["docker", "info", "--format", "{{json .}}"])
        info = json.loads(raw)
        if info["OSType"] != "linux" or any(
            "rootless" in item for item in info.get("SecurityOptions", [])
        ):
            raise DeploymentError("unsupported_docker_mode")
        return {
            "available_memory_bytes": available,
            "free_disk_bytes": free,
            "cpus": os.cpu_count(),
            "kernel": platform.release(),
            "docker_version": info["ServerVersion"],
        }

    def download(self, item):
        path = self.downloads / item["filename"]
        if path.exists():
            if path.is_symlink() or sha256(path) != item["sha256"]:
                raise DeploymentError("download_integrity_failed")
            return path
        temporary = path.with_name(path.name + ".partial-" + uuid4().hex)
        try:
            with (
                urlopen(item["source_url"], timeout=30) as response,
                temporary.open("xb") as destination,
            ):
                started, received = time.monotonic(), 0
                while chunk := response.read(1024 * 1024):
                    received += len(chunk)
                    if (
                        received > item["size_bytes"]
                        or time.monotonic() - started > 600
                    ):
                        raise DeploymentError("download_size_or_deadline_exceeded")
                    destination.write(chunk)
                destination.flush()
                os.fsync(destination.fileno())
            if sha256(temporary) != item["sha256"]:
                raise DeploymentError("download_integrity_failed")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return path

    def mk(self, *args, timeout=600, log=None):
        return command(
            [self.bin / "minikube", "-p", PROFILE, *args],
            env=self.env,
            timeout=timeout,
            output_file=log,
        )

    def kubectl(self, *args, namespace="gr-platform", timeout=120, input_text=None):
        return command(
            [
                self.bin / "kubectl",
                "--kubeconfig",
                self.kubeconfig,
                "--context",
                PROFILE,
                "--namespace",
                namespace,
                *args,
            ],
            env=self.env,
            timeout=timeout,
            input_text=input_text,
        )

    def start_args(self):
        base = next(i for i in self.specs()["images"] if i["name"] == "kicbase-offline")
        return [
            "start",
            "--driver=docker",
            "--container-runtime=containerd",
            "--kubernetes-version=" + KUBERNETES,
            "--cpus=6",
            "--memory=8192mb",
            "--disk-size=40g",
            "--subnet=" + NODE_SUBNET,
            "--static-ip=192.168.51.2",
            "--network=" + NETWORK,
            "--service-cluster-ip-range=" + SERVICE_SUBNET,
            "--extra-config=controller-manager.cluster-cidr=" + POD_SUBNET,
            "--base-image=" + base["source_url"].removeprefix("docker://"),
            "--cni=" + str(self.state / "calico-pinned.yaml"),
            "--cache-images=true",
            "--preload=true",
            "--keep-context",
            "--interactive=false",
            "--auto-update-drivers=false",
            "--listen-address=127.0.0.1",
            "--wait=apiserver",
            "--wait-timeout=5m",
        ]

    def prepare(self, *, tools_only=False, classifier_context=None,
                classifier_seal_sha256=None):
        if (classifier_context is None) != (classifier_seal_sha256 is None):
            raise DeploymentError("classifier_context_arguments_required")
        if tools_only and classifier_context is not None:
            raise DeploymentError("classifier_context_requires_full_prepare")
        if not tools_only:
            module = self.support("build_images")
            try:
                module.classifier_arguments(self, classifier_context, classifier_seal_sha256)
            except module.BuildError as exc:
                raise DeploymentError(str(exc)) from None
        with self.lock():
            self.owner(create=True)
            measured = self.preflight(starting=False)
            self.downloads.mkdir(exist_ok=True, mode=0o700)
            self.bin.mkdir(exist_ok=True, mode=0o700)
            specs = self.specs()
            self.prepare_certificates()
            artifacts = []
            for item in specs["files"]:
                if tools_only and item["kind"] == "model":
                    continue
                path = self.download(item)
                if item["name"] == "helm":
                    with tarfile.open(path) as archive:
                        member = archive.getmember("linux-amd64/helm")
                        if not member.isfile():
                            raise DeploymentError("helm_archive_invalid")
                        with (
                            archive.extractfile(member) as source,
                            (self.bin / "helm").open("wb") as target,
                        ):
                            shutil.copyfileobj(source, target)
                    (self.bin / "helm").chmod(0o700)
                elif item["kind"] == "binary":
                    install_binary(path, self.bin / item["name"], item["sha256"])
                artifacts.append(
                    {key: value for key, value in item.items() if key != "filename"}
                    | {"size_bytes": path.stat().st_size}
                )
            models = self.state / "models"
            for item in specs["files"]:
                if tools_only or item["kind"] != "model":
                    continue
                target = (
                    models / "manifests/registry.ollama.ai/library/qwen2.5/0.5b"
                    if item["name"] == "qwen-manifest"
                    else models / "blobs" / item["filename"]
                )
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                source = self.downloads / item["filename"]
                if target.exists():
                    if target.is_symlink() or sha256(target) != item["sha256"]:
                        raise DeploymentError("model_copy_integrity_failed")
                else:
                    os.link(source, target)
            manifest = (self.downloads / "calico.yaml").read_text()
            for item in specs["images"]:
                if item["name"].startswith("calico-"):
                    role = item["name"].removeprefix("calico-")
                    old = "quay.io/calico/" + role + ":v3.31.3"
                    if old not in manifest:
                        raise DeploymentError("calico_source_shape_changed")
                    manifest = manifest.replace(
                        old, item["source_url"].removeprefix("docker://")
                    )
            manifest = manifest.replace(
                '# - name: CALICO_IPV4POOL_CIDR\n            #   value: "192.168.0.0/16"',
                '- name: CALICO_IPV4POOL_CIDR\n              value: "'
                + POD_SUBNET
                + '"',
            )
            if 'value: "' + POD_SUBNET + '"' not in manifest:
                raise DeploymentError("calico_pod_cidr_missing")
            manifest = manifest.replace(
                "imagePullPolicy: IfNotPresent", "imagePullPolicy: Never"
            )
            (self.state / "calico-pinned.yaml").write_text(manifest)
            if tools_only:
                atomic_json(
                    self.state / "tools.json",
                    {"artifacts": artifacts, "environment": measured},
                )
                return {"status": "tools_prepared", "artifacts": len(artifacts)}
            for item in specs["images"]:
                if item["name"].startswith("evolution-"):
                    continue  # Owned images are built from the current exact source below.
                try:
                    observed = self.inspect_image(item)
                except DeploymentError:
                    if item["name"] == "kicbase-offline":
                        command(
                            [
                                "docker",
                                "build",
                                "--pull=false",
                                "--network=none",
                                "--provenance=false",
                                "--tag",
                                item["source_url"]
                                .removeprefix("docker://")
                                .split("@")[0],
                                "--file",
                                self.root / "deploy/evolution/images/node.Dockerfile",
                                self.root / "deploy/evolution/images/node",
                            ],
                            timeout=120,
                            output_file=self.state / "prepare-node.log",
                        )
                    else:
                        command(
                            [
                                "docker",
                                "pull",
                                "--platform=linux/amd64",
                                item["source_url"].removeprefix("docker://"),
                            ],
                            timeout=1800,
                            output_file=self.state / "prepare-images.log",
                        )
                    observed = self.inspect_image(item)
                artifacts.append(dict(item, size_bytes=observed["Size"]))
            module = self.support("build_images")
            try:
                artifacts.extend(module.prepare(
                    self, classifier_context=classifier_context,
                    classifier_seal_sha256=classifier_seal_sha256,
                ))
            except module.BuildError as exc:
                raise DeploymentError(str(exc)) from None
            specs = self.specs()
            if classifier_context is not None:
                # The optional image now has an authenticated build and lock row.
                self.prepare_certificates()
            images = self.state / "images"
            images.mkdir(exist_ok=True, mode=0o700)
            for item in specs["images"]:
                if item["name"] != "kicbase":
                    archive = images / (item["name"] + ".tar")
                    if archive.exists():
                        with tarfile.open(archive) as content:
                            if (
                                "blobs/sha256/" + item["sha256"]
                                not in content.getnames()
                            ):
                                previous = self.state / "superseded-images"
                                previous.mkdir(mode=0o700, exist_ok=True)
                                os.replace(
                                    archive,
                                    previous
                                    / (item["name"] + "-" + uuid4().hex + ".tar"),
                                )
                    if not archive.exists():
                        command(
                            [
                                "docker",
                                "image",
                                "save",
                                "-o",
                                archive,
                                item["source_url"].removeprefix("docker://"),
                            ],
                            timeout=600,
                        )
                    with tarfile.open(archive) as content:
                        pinned = "blobs/sha256/" + item["sha256"]
                        if (
                            hashlib.sha256(
                                content.extractfile(pinned).read()
                            ).hexdigest()
                            != item["sha256"]
                        ):
                            raise DeploymentError("oci_manifest_digest_mismatch")
            # Minikube's Docker cache lookup requires repository:tag@digest.
            # Seed that exact immutable KIC archive before download-only may run.
            base = next(i for i in specs["images"] if i["name"] == "kicbase-offline")
            name = base["source_url"].rsplit("/", 1)[1].replace(":", "_") + ".tar"
            cache = self.home / "cache/kic/amd64" / name
            cache.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            source = images / "kicbase-offline.tar"
            if cache.exists():
                if cache.is_symlink() or sha256(cache) != sha256(source):
                    raise DeploymentError("kic_cache_integrity_failed")
            else:
                os.link(source, cache)
            self.mk(
                *self.start_args(),
                "--download-only",
                log=self.state / "prepare-minikube.log",
                timeout=1800,
            )
            inventory = self.file_inventory()
            atomic_json(
                self.state / "prepared.json",
                {
                    "schema_version": 1,
                    "artifacts": artifacts,
                    "preparation_lock_digest": sha256(
                        self.root / "deploy/evolution/preparation.lock.json"
                    ),
                    "files": inventory,
                    "environment": measured,
                },
            )
            return {
                "status": "prepared",
                "artifacts": len(artifacts),
                "files": len(inventory),
            }

    def file_inventory(self):
        paths = [self.state / "calico-pinned.yaml"]
        for directory in (
            self.bin,
            self.downloads,
            self.home / "cache",
            self.state / "images",
            self.state / "models",
        ):
            for path in directory.rglob("*"):
                if path.is_symlink():
                    raise DeploymentError("prepared_file_symlink")
                if path.is_file():
                    paths.append(path)
        credentials = self.state / "credentials"
        if credentials.exists():
            paths.extend(credentials.glob("*.crt"))
        paths.extend((self.state / "assembly/tls").rglob("*.crt"))
        paths.extend((self.state / "builds").glob("*.json"))
        def inspect(path):
            if path.is_symlink():
                raise DeploymentError("prepared_file_symlink")
            digest, size = _hash_file(path)
            return {
                "path": str(path.relative_to(self.state)),
                "sha256": digest,
                "size_bytes": size,
            }

        # Every byte is rehashed on every invocation. Independent reads share a
        # bounded three-worker pool; map retains the deterministic path order.
        with ThreadPoolExecutor(max_workers=3) as workers:
            return list(workers.map(inspect, sorted(paths)))

    def verify_prepared(self):
        prepared = read_json(self.state / "prepared.json")
        if prepared.get("preparation_lock_digest") != sha256(
            self.root / "deploy/evolution/preparation.lock.json"
        ):
            raise DeploymentError("preparation_lock_stale")
        expected = self.specs()["files"] + self.specs()["images"]
        if any(x["name"] == "kicbase-offline" for x in expected):
            module = self.support("node_integrity")
            try:
                module.verify_inputs(self)
            except module.NodeIntegrityError as exc:
                raise DeploymentError(str(exc)) from None
        if not prepared.get("files") or self.file_inventory() != prepared["files"]:
            raise DeploymentError("prepared_file_inventory_mismatch")
        if {i["name"] for i in prepared["artifacts"]} != {i["name"] for i in expected}:
            raise DeploymentError("prepared_artifact_inventory_mismatch")
        by_name = {i["name"]: i for i in prepared["artifacts"]}
        for item in expected:
            actual = by_name[item["name"]]
            for key in ("sha256", "version", "source_url", "license", "kind"):
                if actual[key] != item[key]:
                    raise DeploymentError("prepared_artifact_mismatch")
            if item["kind"] == "image":
                self.inspect_image(actual)
            else:
                path = self.downloads / item["filename"]
                if (
                    sha256(path) != item["sha256"]
                    or path.stat().st_size != actual["size_bytes"]
                ):
                    raise DeploymentError("prepared_artifact_mismatch")
        if any(x["name"].startswith("evolution-") for x in expected):
            module = self.support("build_images")
            try:
                module.verify_inputs(self)
            except module.BuildError as exc:
                raise DeploymentError(str(exc)) from None
        derived = [
            {
                "name": "prepared-"
                + hashlib.sha256(item["path"].encode()).hexdigest()[:24],
                "version": "1",
                "source_url": "local://guardrails-evolution-preparation/"
                + item["path"],
                "sha256": item["sha256"],
                "license": "LicenseRef-Local-Prepared",
                "size_bytes": item["size_bytes"],
                "kind": "fixture",
            }
            for item in prepared["files"]
            if item["size_bytes"] > 0
        ]
        return prepared["artifacts"] + derived

    def up(self):
        started = time.monotonic()
        with self.lock():
            owner = self.owner()
            cold_start = owner["container_id"] is None
            self.verify_prepared()
            self.preflight(starting=owner["container_id"] is None)
            self.ensure_internal_network(owner)
            self.env.update(
                HTTP_PROXY="http://192.168.51.1:9",
                HTTPS_PROXY="http://192.168.51.1:9",
                ALL_PROXY="http://192.168.51.1:9",
                NO_PROXY="127.0.0.1,localhost,"
                + NODE_SUBNET
                + ","
                + POD_SUBNET
                + ","
                + SERVICE_SUBNET,
            )
            try:
                running = False
                if owner["container_id"]:
                    _, raw = command(["docker", "container", "inspect", PROFILE])
                    running = json.loads(raw)[0]["State"]["Running"]
                if running:
                    self.kubectl(
                        "get", "--raw=/readyz", namespace="kube-system", timeout=15
                    )
                else:
                    self.mk(*self.start_args(), log=self.state / "up.log")
            finally:
                code, raw = command(
                    ["docker", "container", "inspect", PROFILE], check=False
                )
                if code == 0 and owner["container_id"] is None:
                    owner["container_id"] = json.loads(raw)[0]["Id"]
                    atomic_json(self.state / "owner.json", owner)
            self.enforce_node_resources()
            boundary = self.verify_node_boundary()
            attempt = self.record_boot_attempt(boundary, cold_start=cold_start)
            cold_start = attempt["cold_start"]
            self.import_images()
            self.pin_system_images()
            self.kubectl(
                "wait",
                "--for=condition=Ready",
                "node/" + PROFILE,
                "--timeout=180s",
                namespace="kube-system",
                timeout=200,
            )
            self.kubectl(
                "rollout",
                "status",
                "daemonset/calico-node",
                "--timeout=180s",
                namespace="kube-system",
                timeout=200,
            )
            self.kubectl(
                "rollout",
                "status",
                "deployment/calico-kube-controllers",
                "--timeout=180s",
                namespace="kube-system",
                timeout=200,
            )
            self.restrict_cluster_dns()
            systems = self.verify_system_images()
            old_path = self.state / "offline-boot.json"
            previous = read_json(old_path) if old_path.exists() else {}
            retained_cold_start = (
                previous.get("cold_start") is True
                and previous.get("container_id") == boundary["container_id"]
                and previous.get("prepared_digest")
                == sha256(self.state / "prepared.json")
            )
            atomic_json(
                self.state / "offline-boot.json",
                boundary
                | {
                    "prepared_digest": sha256(self.state / "prepared.json"),
                    "node_ready": True,
                    "runtime": "containerd",
                    "cold_start": cold_start or retained_cold_start,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                    "system_images": systems,
                    "attempt": attempt,
                },
            )
            return self.status()

    def record_boot_attempt(self, boundary, *, cold_start):
        path = self.state / "offline-attempt.json"
        previous = read_json(path) if path.exists() else {}
        prepared_digest = sha256(self.state / "prepared.json")
        if (
            previous.get("container_id") == boundary["container_id"]
            and previous.get("prepared_digest") == prepared_digest
        ):
            return previous
        _, raw = command(["docker", "container", "inspect", PROFILE])
        created = json.loads(raw)[0]["Created"]
        recovered = False
        if (
            not cold_start
            and datetime.fromisoformat(created).timestamp()
            > (self.state / "prepared.json").stat().st_mtime
        ):
            _, raw = self.kubectl("get", "namespaces", "-o", "name")
            # Recovery is allowed only before application namespaces/data exist.
            recovered = set(raw.split()) == {
                "namespace/default",
                "namespace/kube-system",
                "namespace/kube-public",
                "namespace/kube-node-lease",
            }
        value = boundary | {
            "created_at": created,
            "prepared_digest": prepared_digest,
            "cold_start": cold_start or recovered,
            "recovered_before_application_setup": recovered,
        }
        atomic_json(path, value)
        return value

    def pin_system_images(self):
        """Replace Minikube's mutable preload tags with the prepared image identities."""
        self.owner()
        refs = {
            x["name"]: x["source_url"].removeprefix("docker://")
            for x in self.specs()["images"]
        }
        for name in (
            "etcd",
            "kube-apiserver",
            "kube-controller-manager",
            "kube-scheduler",
        ):
            path = "/etc/kubernetes/manifests/" + name + ".yaml"
            _, value = command(["docker", "exec", PROFILE, "cat", path])
            updated, count = re.subn(
                r"(?m)^(\s*image:)\s+[^\n]+$",
                lambda match, name=name: match[1] + " " + refs[name],
                value,
            )
            if count != 1:
                raise DeploymentError("static_manifest_image_ambiguous")
            updated = updated.replace(
                "imagePullPolicy: IfNotPresent", "imagePullPolicy: Never"
            )
            if value != updated:
                temporary = "/var/lib/minikube/evolution-" + name + ".yaml"
                command(
                    [
                        "docker",
                        "exec",
                        "-i",
                        PROFILE,
                        "sh",
                        "-ec",
                        'umask 077; cat > "$1"; mv "$1" "$2"',
                        "sh",
                        temporary,
                        path,
                    ],
                    input_text=updated,
                )
        deadline = time.monotonic() + 120
        while True:
            try:
                self.kubectl(
                    "get", "--raw=/readyz", namespace="kube-system", timeout=10
                )
                break
            except DeploymentError, subprocess.TimeoutExpired:
                if time.monotonic() >= deadline:
                    raise DeploymentError("pinned_control_plane_not_ready") from None
                time.sleep(1)
        for kind, name in (("daemonset", "kube-proxy"), ("deployment", "coredns")):
            patch = {
                "spec": {
                    "template": {
                        "spec": {
                            "containers": [
                                {
                                    "name": name,
                                    "image": refs[name],
                                    "imagePullPolicy": "Never",
                                }
                            ]
                        }
                    }
                }
            }
            self.kubectl(
                "patch",
                kind,
                name,
                "--type=strategic",
                "--patch",
                json.dumps(patch),
                namespace="kube-system",
            )
            self.kubectl(
                "rollout",
                "status",
                kind + "/" + name,
                "--timeout=120s",
                namespace="kube-system",
                timeout=140,
            )
        self.kubectl(
            "set",
            "image",
            "pod/storage-provisioner",
            "storage-provisioner=" + refs["storage-provisioner"],
            namespace="kube-system",
        )
        self.kubectl(
            "wait",
            "--for=condition=Ready",
            "pod/storage-provisioner",
            "--timeout=120s",
            namespace="kube-system",
            timeout=140,
        )

    def verify_system_images(self):
        pins = {
            x["source_url"].removeprefix("docker://"): x for x in self.specs()["images"]
        }
        required = {
            "etcd",
            "kube-apiserver",
            "kube-controller-manager",
            "kube-scheduler",
            "kube-proxy",
            "coredns",
            "storage-provisioner",
            "calico-node",
            "calico-kube-controllers",
        }
        module = self.support("image_identity")
        deadline = time.monotonic() + 120
        while True:
            _, raw = self.kubectl(
                "get", "pods", "-o", "json", namespace="kube-system", timeout=15
            )
            records, pending, identities, present = [], False, [], set()
            for pod in json.loads(raw)["items"]:
                if pod["metadata"].get("deletionTimestamp"):
                    continue
                statuses = {
                    x["name"]: x for x in pod["status"].get("containerStatuses", [])
                }
                for container in pod["spec"]["containers"]:
                    expected = pins.get(container["image"])
                    if expected is None:
                        pending = True
                        continue
                    present.add(expected["name"])
                    status = statuses.get(container["name"], {})
                    image_id = status.get("imageID", "")
                    if not status.get("ready"):
                        pending = True
                    records.append(
                        {
                            "pod": pod["metadata"]["name"],
                            "container": container["name"],
                            "image": container["image"],
                            "image_id": image_id,
                            "ready": status.get("ready", False),
                        }
                    )
                    identities.append((container["image"], status))
            if required <= present and not pending:
                for record, (reference, status) in zip(
                    records, identities, strict=True
                ):
                    try:
                        record["verified_identity"] = module.verify_container(
                            self, reference, status
                        )
                    except module.ImageIdentityError as exc:
                        raise DeploymentError(str(exc)) from None
                return records
            if time.monotonic() >= deadline:
                raise DeploymentError("running_system_image_pin_mismatch")
            time.sleep(1)

    def verify_node_boundary(self):
        owner = self.owner()
        _, raw = command(["docker", "container", "inspect", PROFILE])
        node = json.loads(raw)[0]
        limits = node["HostConfig"]
        if (
            limits.get("NanoCpus") != 6_000_000_000
            or limits.get("Memory") != 8 * 1024**3
        ):
            raise DeploymentError("node_resource_limits_mismatch")
        _, raw = command(["docker", "exec", PROFILE, "cat", "/sys/fs/cgroup/cpu.max"])
        quota, period = raw.split()
        if quota == "max" or int(quota) != 6 * int(period):
            raise DeploymentError("node_cpu_quota_not_enforced")
        ref = self.image("kicbase-offline")
        if node["Config"]["Image"] != ref:
            raise DeploymentError("node_image_fallback_forbidden")
        _, checksum = command(
            [
                "docker",
                "exec",
                PROFILE,
                "sha256sum",
                "/usr/local/share/evolution-offline.nft",
            ]
        )
        rules_digest = sha256(self.root / "deploy/evolution/images/node/offline.nft")
        if checksum.split()[0] != rules_digest:
            raise DeploymentError("node_offline_rules_mismatch")
        _, raw = command(
            [
                "docker",
                "exec",
                PROFILE,
                "nft",
                "-j",
                "list",
                "table",
                "inet",
                "evolution_offline",
            ]
        )
        rules = json.loads(raw)["nftables"]
        chains = {x["chain"]["name"]: x["chain"] for x in rules if "chain" in x}
        if set(chains) != {"output", "forward"} or any(
            x.get("policy") != "drop" or x.get("prio") != 300 for x in chains.values()
        ):
            raise DeploymentError("node_offline_policy_missing")
        sets = [x["set"] for x in rules if "set" in x]
        expected_elements = [
            {"prefix": {"addr": "10.96.0.0", "len": 12}},
            {"prefix": {"addr": "10.244.0.0", "len": 16}},
            "127.0.0.1",
            "192.168.51.2",
        ]
        if (
            len(sets) != 1
            or sets[0].get("name") != "local4"
            or sets[0].get("elem") != expected_elements
        ):
            raise DeploymentError("node_offline_address_set_mismatch")

        def match_address(protocol, value):
            return {
                "match": {
                    "op": "==",
                    "left": {"payload": {"protocol": protocol, "field": "daddr"}},
                    "right": value,
                }
            }

        local_rules = [
            [match_address("ip", "@local4"), {"accept": None}],
            [match_address("ip6", "::1"), {"accept": None}],
        ]
        expected_rules = {
            "output": local_rules
            + [
                [
                    {
                        "match": {
                            "op": "in",
                            "left": {"ct": {"key": "state"}},
                            "right": ["established", "related"],
                        }
                    },
                    match_address("ip", "192.168.51.1"),
                    {"accept": None},
                ],
                [{"drop": None}],
            ],
            "forward": local_rules + [[{"drop": None}]],
        }
        actual_rules = {name: [] for name in expected_rules}
        for row in rules:
            if "rule" not in row:
                continue
            rule = row["rule"]
            if rule.get("chain") not in actual_rules:
                raise DeploymentError("node_offline_extra_chain")
            actual_rules[rule["chain"]].append(
                [x for x in rule["expr"] if "counter" not in x]
            )
        if actual_rules != expected_rules:
            raise DeploymentError("node_offline_live_rules_mismatch")
        # The driver is namespaced; a host firewall must never be the boundary.
        _, network_namespace = command(
            ["docker", "exec", PROFILE, "readlink", "/proc/self/ns/net"]
        )
        if network_namespace.strip() == os.readlink("/proc/self/ns/net"):
            raise DeploymentError("node_host_network_namespace_forbidden")
        return {
            "container_id": owner["container_id"],
            "image": ref,
            "rules_sha256": rules_digest,
            "network_namespace": network_namespace.strip(),
            "cpu_limit": 6,
            "cpu_quota_us": int(quota),
            "cpu_period_us": int(period),
            "memory_limit_bytes": limits["Memory"],
        }

    def enforce_node_resources(self):
        self.owner()
        command(["docker", "update", "--cpus=6", PROFILE])
        _, raw = command(["docker", "exec", PROFILE, "cat", "/sys/fs/cgroup/cpu.max"])
        quota, period = raw.split()
        if quota == "max" or int(quota) != 6 * int(period):
            raise DeploymentError("node_cpu_quota_not_enforced")

    def restrict_cluster_dns(self):
        _, raw = self.kubectl(
            "get", "configmap", "coredns", "-o", "json", namespace="kube-system"
        )
        config = json.loads(raw)
        corefile = config["data"]["Corefile"]
        # No upstream DNS proxy is permitted to turn external names into egress.
        updated, count = re.subn(
            r"(?m)^\s*forward\s+\.\s+[^\n{]+(?:\{[^}]*\})?\n", "\n", corefile
        )
        if "forward" in updated or "proxy" in updated:
            raise DeploymentError("cluster_dns_external_forwarder")
        if count:
            config["data"]["Corefile"] = updated
            self.kubectl(
                "apply",
                "-f",
                "-",
                namespace="kube-system",
                input_text=json.dumps(config),
            )

    def ensure_internal_network(self, owner):
        """Normal bridge permits driver ports; the pinned node installs its own firewall."""
        code, output = command(["docker", "network", "inspect", NETWORK], check=False)
        if code:
            command(
                [
                    "docker",
                    "network",
                    "create",
                    "--driver=bridge",
                    "--subnet=" + NODE_SUBNET,
                    "--label=guardrails.evolution/owner=" + owner["owner_id"],
                    NETWORK,
                ]
            )
            _, output = command(["docker", "network", "inspect", NETWORK])
        network = json.loads(output)[0]
        if (
            network.get("Driver") != "bridge"
            or network.get("Internal") is not False
            or network.get("Labels", {}).get("guardrails.evolution/owner")
            != owner["owner_id"]
            or [
                entry.get("Subnet")
                for entry in network.get("IPAM", {}).get("Config", [])
            ]
            != [NODE_SUBNET]
            or any(
                value.get("Name") != PROFILE
                for value in network.get("Containers", {}).values()
            )
        ):
            raise DeploymentError("offline_network_owner_or_boundary_mismatch")
        return network

    def _wait_for_imported_image(self, ref):
        """Await CRI registration once after import, within its original budget."""
        deadline = time.monotonic() + 120

        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError()
                result[key] = value
            return result

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DeploymentError("node_image_lookup_timeout")
            try:
                code, output = command(
                    ["docker", "exec", PROFILE, "crictl", "inspecti", "--output=json", ref],
                    timeout=min(5, remaining), check=False,
                )
            except (subprocess.TimeoutExpired, TimeoutError):
                code, output = 1, ""
            except OSError:
                raise DeploymentError("node_image_lookup_failed") from None
            if time.monotonic() >= deadline:
                raise DeploymentError("node_image_lookup_timeout")
            if code == 0:
                try:
                    if not isinstance(output, str) or len(output.encode()) > 2 * 1024 * 1024:
                        raise ValueError()
                    record = json.loads(
                        output, object_pairs_hook=unique,
                        parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
                    )
                    status = record.get("status") if isinstance(record, dict) else None
                    digests = status.get("repoDigests") if isinstance(status, dict) else None
                    if not isinstance(digests, list) or not all(isinstance(value, str) for value in digests):
                        raise ValueError()
                except (ValueError, TypeError, UnicodeError, RecursionError):
                    raise DeploymentError("node_image_metadata_invalid") from None
                if ref not in digests:
                    raise DeploymentError("node_image_digest_mismatch")
                if time.monotonic() >= deadline:
                    raise DeploymentError("node_image_lookup_timeout")
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DeploymentError("node_image_lookup_timeout")
            time.sleep(min(.25, remaining))

    def import_images(self):
        for item in self.specs()["images"]:
            if item["name"] in ("kicbase", "kicbase-offline", "ollama", "litellm"):
                continue
            ref = item["source_url"].removeprefix("docker://")
            code, output = command(
                ["docker", "exec", PROFILE, "crictl", "inspecti", "--output=json", ref],
                check=False,
            )
            if not code and ref in json.loads(output)["status"].get("repoDigests", []):
                continue
            archive = self.state / "images" / (item["name"] + ".tar")
            node_path = "/var/lib/minikube/" + item["name"] + "-pinned.tar"
            command(["docker", "cp", archive, PROFILE + ":" + node_path], timeout=600)
            command(
                [
                    "docker",
                    "exec",
                    PROFILE,
                    "timeout",
                    "--kill-after=5s",
                    "900s",
                    "ctr",
                    "-n=k8s.io",
                    "images",
                    "import",
                    "--digests",
                    "--platform",
                    "linux/amd64",
                    "--base-name",
                    ref.split("@")[0],
                    node_path,
                ],
                timeout=915,
            )
            self._wait_for_imported_image(ref)
            command(["docker", "exec", PROFILE, "rm", node_path])

    def prepare_certificates(self):
        # Deferred import keeps read-only status usable without service dependencies.
        module = self.support("certificates")
        module.prepare(self.state / "credentials")
        for namespace, names in {
            "gr-platform": ("postgres", "management", "runtime"),
            "gr-model": ("mock", "ollama"),
            "gr-team-a": ("gateway",),
            "gr-team-b": ("gateway",),
        }.items():
            for name in names:
                module.issue_server(
                    self.state / "credentials",
                    self.state / "assembly/tls" / namespace / name,
                    names=[name + "." + namespace + ".svc.cluster.local"],
                )
        if any(x["name"] == "evolution-classifier" for x in self.specs()["images"]):
            module.issue_server(
                self.state / "credentials",
                self.state / "assembly/tls/gr-platform/classifier",
                names=["classifier.gr-platform.svc.cluster.local"],
            )

    def _probe_cpu_budget(self, owner, *, change=False):
        helper = self.support("resource_budget")
        try:
            helper.check(self, owner["owner_id"], self.support("assembly")._same_limits, change=change, require_probes=not change)
        except helper.ResourceBudgetError as error:
            raise DeploymentError(str(error)) from None

    def probes(self):
        with self.lock():
            owner = self.owner()
            self.verify_prepared()
            credentials = self.state / "credentials"
            if not credentials.exists():
                raise DeploymentError("local_ca_not_prepared")
            for namespace in ("gr-platform", "gr-team-a", "gr-team-b", "gr-model"):
                _, output = self.kubectl(
                    "get", "namespace", namespace, "--ignore-not-found", "-o", "json"
                )
                if output.strip():
                    existing = json.loads(output)
                    if (
                        existing.get("metadata", {})
                        .get("labels", {})
                        .get("guardrails.evolution/owner")
                        != owner["owner_id"]
                    ):
                        raise DeploymentError("namespace_owner_mismatch")
                document = {
                    "apiVersion": "v1",
                    "kind": "Namespace",
                    "metadata": {
                        "name": namespace,
                        "labels": {
                            "guardrails.evolution/owner": owner["owner_id"],
                            "app.kubernetes.io/managed-by": "Helm",
                        },
                        "annotations": {
                            "meta.helm.sh/release-name": "evolution-probes",
                            "meta.helm.sh/release-namespace": "gr-platform",
                        },
                    },
                }
                self.kubectl(
                    "apply",
                    "-f",
                    "-",
                    namespace=namespace,
                    input_text=json.dumps(document),
                )
                names = ["ca.crt", "wrong-ca.crt"] + (
                    ["server.crt", "server.key", "expired.crt"]
                    if namespace == "gr-platform"
                    else []
                )
                secret = {
                    "apiVersion": "v1",
                    "kind": "Secret",
                    "metadata": {"name": "probe-tls", "namespace": namespace},
                    "type": "Opaque",
                    "data": {
                        name: base64.b64encode(
                            (credentials / name).read_bytes()
                        ).decode()
                        for name in names
                    },
                }
                self.kubectl(
                    "apply",
                    "-f",
                    "-",
                    namespace=namespace,
                    input_text=json.dumps(secret),
                )
            self._probe_cpu_budget(owner, change=True)
            # Older Helm history contains larger donor limits. A failed upgrade
            # must retain the reduced allocation, never roll it back after PG grew.
            command(
                [
                    self.bin / "helm",
                    "upgrade",
                    "--install",
                    "evolution-probes",
                    self.root / "deploy/evolution/charts/probes",
                    "--kubeconfig",
                    self.kubeconfig,
                    "--kube-context",
                    PROFILE,
                    "--namespace",
                    "gr-platform",
                    "--set-string",
                    "ownerId=" + owner["owner_id"],
                    "--set-string",
                    "tlsRevision="
                    + hashlib.sha256(
                        b"".join(
                            (credentials / name).read_bytes()
                            for name in ("ca.crt", "server.crt", "expired.crt")
                        )
                    ).hexdigest(),
                    "--wait",
                    "--timeout",
                    "3m",
                ],
                env=self.env,
                timeout=240,
            )
            self._probe_cpu_budget(owner)
            observations = []
            counts = {}
            canary = "synthetic-sensitive-" + uuid4().hex
            probe_payload = (
                "POST / HTTP/1.1\r\nHost: probe\r\nContent-Length: "
                + str(len(canary))
                + "\r\n\r\n"
                + canary
            )

            def sink_counts():
                _, output = self.kubectl(
                    "exec",
                    "-i",
                    "deployment/tls-probe",
                    "-c",
                    "probe",
                    "--",
                    "python",
                    "-B",
                    "/code/probe.py",
                    "count",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "9443",
                    timeout=15,
                )
                return json.loads(output)["counts"]

            checks = [
                ("gr-team-a", "allowed-probe", "dns", []),
                ("gr-team-a", "allowed-probe", "query", []),
                ("gr-team-b", "allowed-probe", "query", []),
                ("gr-team-a", "allowed-probe", "wrong-ca", []),
                ("gr-team-b", "allowed-probe", "wrong-name", []),
                ("gr-team-a", "allowed-probe", "expired", ["--port", "8445"]),
                ("gr-team-a", "denied-probe", "deny", []),
                ("gr-team-a", "allowed-probe", "deny", ["--port", "9443"]),
                (
                    "gr-team-a",
                    "allowed-probe",
                    "deny",
                    ["--host", "1.1.1.1", "--port", "443"],
                ),
                (
                    "gr-platform",
                    "tls-probe",
                    "query",
                    ["--host", "127.0.0.1", "--port", "9443"],
                ),
            ]
            for namespace, pod, mode, extra in checks:
                if len(observations) == 3:
                    counts["before_denials"] = sink_counts()
                elif len(observations) == 9:
                    counts["after_denials"] = sink_counts()
                    if counts["before_denials"] != counts["after_denials"]:
                        raise DeploymentError("forbidden_probe_dispatch_observed")
                atomic_json(
                    self.state / "network-probes-progress.json",
                    {
                        "status": "running",
                        "completed": len(observations),
                        "namespace": namespace,
                        "pod": pod,
                        "mode": mode,
                    },
                )
                started = time.monotonic()
                _, output = self.kubectl(
                    "exec",
                    "-i",
                    "deployment/" + pod,
                    "-c",
                    "probe",
                    "--",
                    "python",
                    "-B",
                    "/code/probe.py",
                    mode,
                    *extra,
                    *(["--payload-stdin"] if mode == "deny" else []),
                    namespace=namespace,
                    timeout=15,
                    input_text=probe_payload if mode == "deny" else None,
                )
                observed = json.loads(output)
                if mode == "deny" and observed.get("payload_bytes") != len(
                    probe_payload.encode()
                ):
                    raise DeploymentError("probe_canary_not_attempted")
                observations.append(
                    {
                        "namespace": namespace,
                        "pod": pod,
                        "mode": mode,
                        "observation": observed,
                        "duration_ms": round((time.monotonic() - started) * 1000),
                    }
                )
            log_bytes = 0
            for namespace, name in (
                ("gr-platform", "tls-probe"),
                ("gr-team-a", "allowed-probe"),
                ("gr-team-b", "allowed-probe"),
                ("gr-team-a", "denied-probe"),
            ):
                _, logs = self.kubectl(
                    "logs",
                    "deployment/" + name,
                    "-c",
                    "probe",
                    "--tail=-1",
                    "--limit-bytes=1048576",
                    namespace=namespace,
                    timeout=15,
                )
                size = len(logs.encode())
                if size >= 1048576:
                    raise DeploymentError("probe_privacy_scan_incomplete")
                if canary in logs:
                    raise DeploymentError("probe_raw_canary_persisted")
                log_bytes += size
            privacy = {
                "canary_attempts": 3,
                "raw_canary_in_logs": False,
                "scanned_log_bytes": log_bytes,
            }
            atomic_json(
                self.state / "network-probes.json",
                {
                    "observations": observations,
                    "dispatch_counts": counts,
                    "privacy": privacy,
                },
            )
            atomic_json(
                self.state / "network-probes-progress.json",
                {"status": "passed", "completed": len(observations)},
            )
            return {
                "status": "probes_executed",
                "observations": observations,
                "dispatch_counts": counts,
                "privacy": privacy,
            }

    def status(self):
        self.owner()
        code, output = command(
            [self.bin / "minikube", "-p", PROFILE, "status", "--output=json"],
            env=self.env,
            check=False,
        )
        return {"profile": PROFILE, "state": json.loads(output), "exit_code": code}

    def reset_failed_boot(self, *, confirmation, backup_directory):
        """Only an unseeded, pre-etcd failed boot can use the diagnostic-only backup."""
        if confirmation != PROFILE:
            raise DeploymentError("explicit_reset_confirmation_required")
        with self.lock():
            owner = self.owner()
            if not owner["container_id"] or (self.state / "assembly/seed").exists():
                raise DeploymentError("failed_boot_reset_not_allowed")
            command(
                [
                    "docker",
                    "exec",
                    PROFILE,
                    "sh",
                    "-ec",
                    "test ! -e /var/lib/minikube/etcd/member; test ! -e /var/lib/etcd/member; test ! -e /etc/kubernetes/manifests/kube-apiserver.yaml",
                ]
            )
            backup = Path(backup_directory).absolute()
            if backup.exists():
                raise DeploymentError("backup_destination_exists")
            backup.mkdir(parents=True, mode=0o700)
            _, metadata = command(["docker", "container", "inspect", PROFILE])
            atomic_json(backup / "container.json", json.loads(metadata))
            _, logs = command(["docker", "logs", PROFILE])
            path = backup / "container.log"
            path.write_text(logs)
            path.chmod(0o600)
            shutil.copytree(self.home / "profiles" / PROFILE, backup / "profile")
            for path in self.state.glob("*.log"):
                shutil.copyfile(path, backup / path.name)
                (backup / path.name).chmod(0o600)
            atomic_json(
                backup / "backup.json",
                {"owner": owner, "etcd": "never_created", "app_data": "not_seeded"},
            )
            self.owner()
            self.mk("delete", timeout=240, log=self.state / "reset.log")
            owner["container_id"] = None
            atomic_json(self.state / "owner.json", owner)
            return {"status": "failed_boot_reset", "backup_directory": str(backup)}

    def reset(self, *, confirmation, backup_directory):
        if confirmation != PROFILE:
            raise DeploymentError("explicit_reset_confirmation_required")
        with self.lock():
            owner = self.owner()
            if not owner["container_id"]:
                raise DeploymentError("no_owned_container_to_reset")
            backup = Path(backup_directory).absolute()
            if backup.exists():
                raise DeploymentError("backup_destination_exists")
            backup.mkdir(parents=True, mode=0o700)
            _, output = command(
                ["docker", "exec", PROFILE, "crictl", "ps", "--name", "etcd", "-q"]
            )
            container = output.strip()
            if not re.fullmatch("[a-f0-9]{12,64}", container):
                raise DeploymentError("etcd_container_unavailable")
            snapshot = "/var/lib/minikube/etcd/evolution-reset-snapshot.db"
            command(
                [
                    "docker",
                    "exec",
                    PROFILE,
                    "crictl",
                    "exec",
                    container,
                    "etcdctl",
                    "--endpoints=https://127.0.0.1:2379",
                    "--cacert=/var/lib/minikube/certs/etcd/ca.crt",
                    "--cert=/var/lib/minikube/certs/etcd/server.crt",
                    "--key=/var/lib/minikube/certs/etcd/server.key",
                    "snapshot",
                    "save",
                    snapshot,
                ]
            )
            _, status = command(
                [
                    "docker",
                    "exec",
                    PROFILE,
                    "crictl",
                    "exec",
                    container,
                    "etcdutl",
                    "snapshot",
                    "status",
                    snapshot,
                    "--write-out=json",
                ]
            )
            snapshot_status = json.loads(status)
            if not snapshot_status.get("revision") or not snapshot_status.get(
                "totalKey"
            ):
                raise DeploymentError("etcd_snapshot_invalid")
            command(["docker", "cp", PROFILE + ":" + snapshot, backup / "etcd.db"])
            (backup / "etcd.db").chmod(0o600)
            _, objects = self.kubectl(
                "get",
                "all,configmaps,secrets,persistentvolumes,persistentvolumeclaims,networkpolicies",
                "--all-namespaces",
                "-o",
                "json",
                namespace="kube-system",
            )
            atomic_json(backup / "kubernetes.json", json.loads(objects))
            if (self.state / "assembly/seed/bootstrap.json").exists():
                _, dump = self.kubectl(
                    "exec",
                    "deployment/postgres",
                    "-c",
                    "postgres",
                    "--",
                    "pg_dumpall",
                    "--username=evolution_owner",
                    timeout=120,
                )
                if (
                    not dump.startswith("--")
                    or "PostgreSQL database cluster dump" not in dump
                ):
                    raise DeploymentError("postgres_backup_invalid")
                database_backup = backup / "postgres.sql"
                fd = os.open(
                    database_backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
                )
                with os.fdopen(fd, "w") as target:
                    target.write(dump)
                    target.flush()
                    os.fsync(target.fileno())
            for path in self.state.glob("*.log"):
                shutil.copyfile(path, backup / path.name)
                (backup / path.name).chmod(0o600)
            atomic_json(
                backup / "backup.json",
                {
                    "owner": owner,
                    "snapshot": snapshot_status,
                    "snapshot_sha256": sha256(backup / "etcd.db"),
                },
            )
            # Recheck identity immediately before the destructive task-scoped command.
            self.owner()
            assembly = self.state / "assembly"
            if (assembly / "owner.json").exists():
                self.support("assembly").stop_forwards(self)
            if assembly.exists():
                archived = backup / "assembly"
                archived.mkdir(mode=0o700)
                for path in assembly.iterdir():
                    if path.name != "tls":
                        if path.is_symlink():
                            raise DeploymentError("assembly_backup_symlink")
                        if path.is_dir():
                            shutil.copytree(path, archived / path.name)
                        else:
                            shutil.copy2(path, archived / path.name)
            self.mk("delete")
            owner["container_id"] = None
            atomic_json(self.state / "owner.json", owner)
            if assembly.exists():
                for path in assembly.iterdir():
                    if path.name != "tls":
                        if path.is_dir():
                            shutil.rmtree(path)
                        else:
                            path.unlink()
            return {
                "status": "reset",
                "profile": PROFILE,
                "backup_directory": str(backup),
            }

    def down(self):
        with self.lock():
            owner = self.owner()
            if owner["container_id"]:
                if (self.state / "assembly/owner.json").exists():
                    self.support("assembly").stop_forwards(self)
                self.mk("stop")
            return {"profile": PROFILE, "status": "stopped", "data_preserved": True}


def verify_prepared(root=ROOT, *, state=None):
    return LocalDeployment(root, state=state).verify_prepared()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=[
            "prepare",
            "verify",
            "up",
            "status",
            "down",
            "probes",
            "reset",
            "platform",
            "classifier-service",
            "access",
            "renew-credentials",
        ],
    )
    parser.add_argument("--state", type=Path)
    parser.add_argument("--tools-only", action="store_true")
    parser.add_argument("--classifier-context", type=Path)
    parser.add_argument("--classifier-seal-sha256")
    parser.add_argument(
        "--profile",
        choices=["deterministic_mock", "local_model", "classifier"],
        default="deterministic_mock",
    )
    parser.add_argument("--confirm-reset")
    parser.add_argument("--evaluator-config-file", type=Path)
    parser.add_argument("--failed-boot", action="store_true")
    parser.add_argument("--backup-directory", type=Path)
    parser.add_argument("--renewal-id")
    parser.add_argument("--expires-at")
    parser.add_argument("--recover-lost", action="store_true")
    args = parser.parse_args(argv)
    deployment = LocalDeployment(state=args.state)
    try:
        if args.command != "prepare" and (args.classifier_context is not None or args.classifier_seal_sha256 is not None):
            raise DeploymentError("classifier_context_requires_prepare")
        if args.command != "platform" and args.evaluator_config_file is not None:
            raise DeploymentError("evaluator_config_requires_platform")
        if args.command == "prepare":
            result = deployment.prepare(
                tools_only=args.tools_only, classifier_context=args.classifier_context,
                classifier_seal_sha256=args.classifier_seal_sha256,
            )
        elif args.command == "reset":
            if args.backup_directory is None:
                raise DeploymentError("backup_directory_required")
            action = (
                deployment.reset_failed_boot if args.failed_boot else deployment.reset
            )
            result = action(
                confirmation=args.confirm_reset, backup_directory=args.backup_directory
            )
        elif args.command == "platform":
            result = deployment.platform(profile=args.profile, evaluator_config_file=args.evaluator_config_file)
        elif args.command == "classifier-service":
            result = deployment.classifier_service()
        elif args.command == "renew-credentials":
            if args.renewal_id is None or args.expires_at is None:
                raise DeploymentError("credential_renewal_arguments_required")
            result = deployment.renew_credentials(renewal_id=args.renewal_id, expires_at=args.expires_at, recover_lost=args.recover_lost)
        elif args.command == "verify":
            result = {
                "status": "verified",
                "artifacts": len(deployment.verify_prepared()),
            }
        else:
            result = getattr(deployment, args.command)()
        print(json.dumps(result, sort_keys=True))
    except (
        DeploymentError,
        subprocess.TimeoutExpired,
        OSError,
        ValueError,
        KeyError,
        tarfile.TarError,
    ) as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "reason": str(exc)
                    if isinstance(exc, DeploymentError)
                    else type(exc).__name__,
                }
            )
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
