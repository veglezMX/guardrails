"""Private append-only governance evidence in an explicit durable operation root.

The journal records authorization and acknowledged identities, not credentials or
request content. It never resumes, replays a mutation or adopts another attempt.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
from pathlib import Path
from uuid import UUID, uuid4

from guardrails.evolution import models

from .evaluator_governance import policy_reference, require


def operation_root():
    """No default, discovery, directory creation or temporary-directory fallback."""
    value = os.environ.get("EVOLUTION_EVALUATOR_OPERATION_ROOT")
    require(type(value) is str and bool(value))
    path = Path(value)
    _directory(path)
    return path


def _source(path):
    require(path.is_absolute() and path.resolve() == path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid()
                and before.st_nlink == 1 and not before.st_mode & 0o022
                and 0 < before.st_size <= 1024 * 1024)
        with os.fdopen(os.dup(descriptor), "rb") as stream:
            raw = stream.read(1024 * 1024 + 1)
        def identity(info):
            return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink,
                    info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        require(len(raw) == before.st_size and identity(before) == identity(os.fstat(descriptor))
                == identity(path.lstat()) and path.resolve() == path)
        return hashlib.sha256(raw).hexdigest()
    finally:
        os.close(descriptor)


def _directory(path):
    info = path.lstat()
    require(path.is_absolute() and path.resolve() == path and stat.S_ISDIR(info.st_mode)
            and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700)
    return info.st_dev, info.st_ino


class GovernanceAttempt:
    def __init__(self, parent, *, tenant, original_binding, original_policy, actors, engine, stage, state, sources):
        self.parent = Path(parent)
        self.parent_identity = _directory(self.parent)
        self.fd = None
        self.counter = 0
        self.pending = None
        self.operation = str(uuid4())
        self.path = self.parent / self.operation
        require(type(sources) is dict and 1 <= len(sources) <= 8)
        require(all(type(name) is str and name.startswith("mvp/") and name.endswith(".py")
                    and len(name) <= 256 and not any(part in {"", ".", ".."} for part in name.split("/"))
                    for name in sources))
        self.sources = {name: Path(path) for name, path in sources.items()}
        self.source_sha256 = {name: _source(path) for name, path in self.sources.items()}
        require(tenant in {"team-a", "team-b"} and engine in
                {"pii", "secret", "term", "topic", "json_schema", "injection"}
                and stage in {"input", "output"} and state in {"disabled", "observe", "warn", "redact", "block"})
        binding = models.Binding.model_validate_json(models.canonical_json_bytes(original_binding))
        policy = policy_reference(original_policy)
        require(binding.tenant_id == tenant and binding.policy.model_dump(mode="json") == policy)
        require(set(actors) == {"tenant_admin", "author", "approver", "operator", "auditor"}
                and len(set(actors.values())) == 5 and all(str(UUID(value)) == value for value in actors.values()))
        self.tenant, self.actors, self.workload = tenant, dict(actors), binding.workload_id
        parent_fd = os.open(self.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(parent_fd)
            require((info.st_dev, info.st_ino) == self.parent_identity
                    and _directory(self.parent) == self.parent_identity)
            os.mkdir(self.operation, mode=0o700, dir_fd=parent_fd)
            created = os.stat(self.operation, dir_fd=parent_fd, follow_symlinks=False)
            require(stat.S_ISDIR(created.st_mode) and created.st_uid == os.getuid()
                    and stat.S_IMODE(created.st_mode) == 0o700)
            self.fd = os.open(self.operation, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
            info = os.fstat(self.fd)
            require((info.st_dev, info.st_ino) == (created.st_dev, created.st_ino))
            self.identity = info.st_dev, info.st_ino
            self.check()
            self.write("intent.json", {"schema_version": "evolution-governance-attempt-v1",
                "operation": self.operation, "tenant_id": tenant, "engine": engine,
                "stage": stage, "state": state, "actors": actors,
                "source_sha256": self.source_sha256,
                "original_binding": binding.model_dump(mode="json", exclude={"reason"}), "original_policy": policy,
                "status": "intent_only_no_mutation_claim"})
            os.fsync(parent_fd)
        except BaseException:
            self.close()
            raise
        finally:
            os.close(parent_fd)

    def check(self):
        require(self.fd is not None)
        info = os.fstat(self.fd)
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
                and stat.S_IMODE(info.st_mode) == 0o700
                and (info.st_dev, info.st_ino) == self.identity
                and _directory(self.parent) == self.parent_identity
                and _directory(self.path) == self.identity)
        require(self.source_sha256 == {name: _source(path) for name, path in self.sources.items()})

    def write(self, name, value):
        require(name == "intent.json" or name == "completion.json"
                or name in {"release-matrix-release.json", "release-restore-original-binding.json"}
                or name in {f"request-{index:02d}-{suffix}.json" for index in range(1, 17)
                            for suffix in ("intent", "returned")})
        raw = models.canonical_json_bytes(value) + b"\n"
        require(len(raw) <= 16384)
        self.check()
        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.fd)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(self.fd)
        self.check()

    def before_request(self, *, method, path, role, body, key, expected):
        require(method == "POST" and self.pending is None and self.counter < 16)
        require(role in {"tenant_admin", "author", "approver", "operator"}
                and type(path) is str and 1 <= len(path) <= 256
                and path.startswith("/") and not any(value in path for value in ("?", "#", ".."))
                and str(UUID(key)) == key and expected in {200, 201, 202, 409})
        require(path in {"/lists", "/releases", f"/policies/eval-{UUID(self.operation).hex}/versions",
                         f"/policies/eval-{UUID(self.operation).hex}/versions/1/reviews"}
                or re.fullmatch(r"/(?:providers|destinations)/[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}/versions", path))
        self.counter += 1
        self.write(f"request-{self.counter:02d}-intent.json", {"operation": self.operation,
            "method": method, "path": path, "role": role, "actor_id": self.actors[role], "idempotency_key": key,
            "body_sha256": hashlib.sha256(models.canonical_json_bytes(body)).hexdigest(),
            "expected_status": expected, "status": "request_intended_outcome_unknown"})
        self.pending = self.counter

    def after_request(self, value):
        require(self.pending is not None)
        self.write(f"request-{self.pending:02d}-returned.json", {"operation": self.operation,
            "response_sha256": hashlib.sha256(models.canonical_json_bytes(value)).hexdigest(),
            "status": "port_returned_expected_status_not_application_proof"})
        self.pending = None

    def acknowledged_release(self, handle):
        require(handle["operation"] == self.operation and handle["label"] in
                {"matrix-release", "restore-original-binding"})
        require(set(handle) == {"operation", "label", "release_id", "tenant_id", "workload_id",
                              "policy", "binding_revision", "previous_revision", "state", "actor_id"})
        require(handle["tenant_id"] == self.tenant and handle["workload_id"] == self.workload
                and handle["actor_id"] == self.actors["operator"])
        models.PolicyRef.model_validate_json(models.canonical_json_bytes(handle["policy"]))
        require(str(UUID(handle["release_id"])) == handle["release_id"]
                and handle["policy"]["tenant_id"] == self.tenant
                and type(handle["previous_revision"]) is int and handle["previous_revision"] >= 1
                and handle["binding_revision"] == handle["previous_revision"] + 1
                and handle["state"] in {"disabled", "observe", "warn", "redact", "block"})
        self.write("release-" + handle["label"] + ".json", {"schema_version": "evolution-governance-release-handle-v1",
            "status": "acknowledged_not_yet_applied", "handle": handle})

    def finish(self, *, passed, restoration):
        require(type(passed) is bool and restoration in {"not_needed", "applied", "failed"})
        self.write("completion.json", {"operation": self.operation,
            "status": "case_passed" if passed else "case_failed", "restoration": restoration,
            "last_request_outcome_unknown": self.pending is not None})

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
