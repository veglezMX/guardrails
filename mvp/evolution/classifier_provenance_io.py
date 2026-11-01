"""Bounded private I/O and parent-owned provenance journals; no live adapters.

The caller supplies one original deadline and an independently pinned capsule.
This module neither starts workers nor constructs clients or qualification ports.
All operational adapters and actual process observations remain the reviewed
parent's responsibility. A worker cannot manufacture its own completion anchor.
The original outer budget must include a declared failure-retention allowance;
a case cap may expire without refreshing that outer deadline. If the outer
budget expires, existing intent/partial files remain and no success is claimed.
"""
from __future__ import annotations

import copy
import hashlib
import math
import os
import stat
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from . import classifier_provenance as proof

PRIVATE_LARGE_SCHEMA = "evolution-classifier-private-large-file-v1"
PRIVATE_LARGE_BYTES = 16 * 1024 * 1024


def private_large_allowances(values, public_files):
    """One externally pinned historical closure; never a caller-selected cap."""
    proof.require(type(values) in {list, tuple} and len(values) <= 1
                  and type(public_files) is dict)
    for value in values:
        proof.exact(value, {"schema_version", "kind", "reference", "maximum_bytes"})
        proof.require(value["schema_version"] == PRIVATE_LARGE_SCHEMA
                      and value["kind"] == "historical_installed_closure"
                      and type(value["maximum_bytes"]) is int
                      and value["maximum_bytes"] == PRIVATE_LARGE_BYTES)
        proof.reference(value["reference"])
        proof.require(value["reference"]["path"] not in public_files,
                      "classifier_private_large_public_overlap")
    return tuple(copy.deepcopy(values))


class Budget:
    def __init__(self, deadline, *, monotonic=time.monotonic):
        proof.number(deadline, .001, 1e15)
        proof.require(callable(monotonic))
        self.deadline, self.monotonic = deadline, monotonic

    def check(self):
        now = self.monotonic()
        proof.require(type(now) in {int, float} and math.isfinite(now) and now < self.deadline,
                      "classifier_provenance_io_deadline")

    @contextmanager
    def cap(self, deadline):
        proof.number(deadline, .001, 1e15)
        old = self.deadline
        self.deadline = min(old, deadline)
        try:
            self.check()
            yield
            self.check()
        finally:
            self.deadline = old


def identity(info):
    return tuple(getattr(info, key) for key in ("st_dev", "st_ino", "st_mode", "st_uid", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns"))


def canonical_path(value):
    path = Path(proof.path(str(value)))
    proof.require(path.resolve() == path)
    return path


def directory(path, *, private):
    path = canonical_path(path)
    info = path.lstat()
    proof.require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
                  and stat.S_IMODE(info.st_mode) in ({0o700} if private else {0o700, 0o755}))
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        opened = os.fstat(fd)
        proof.require((opened.st_dev, opened.st_ino, opened.st_mode, opened.st_uid) ==
                      (info.st_dev, info.st_ino, info.st_mode, info.st_uid) and path.resolve() == path)
        return path, fd
    except BaseException:
        os.close(fd)
        raise


def directory_unchanged(path, fd):
    proof.require(path.resolve() == path)
    current, opened = path.lstat(), os.fstat(fd)
    proof.require(stat.S_ISDIR(current.st_mode) and current.st_uid == os.getuid()
                  and (current.st_dev, current.st_ino, current.st_mode, current.st_uid) ==
                  (opened.st_dev, opened.st_ino, opened.st_mode, opened.st_uid))


class StableReader:
    """Explicit public modes and one private historical closure, under one cap."""
    def __init__(self, budget, *, public_files, private_large_files=()):
        proof.require(isinstance(budget, Budget) and type(public_files) is dict)
        for name, hashed in public_files.items():
            proof.path(name); proof.sha(hashed)
        self.budget, self.public_files = budget, copy.deepcopy(public_files)
        self.private_large_files = private_large_allowances(private_large_files, public_files)

    def __call__(self, ref):
        self.budget.check(); proof.reference(ref)
        path = canonical_path(ref["path"])
        public = self.public_files.get(str(path)) == ref["sha256"]
        maximum = proof.MAX_BYTES
        for allowance in self.private_large_files:
            if allowance["reference"] == ref:
                maximum = allowance["maximum_bytes"]
        parent, parent_fd = directory(path.parent, private=not public)
        parent_mode = os.fstat(parent_fd).st_mode
        try:
            self.budget.check()
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
            try:
                before = os.fstat(fd)
                proof.require(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid() and before.st_nlink == 1
                              and stat.S_IMODE(before.st_mode) in ({0o400, 0o444, 0o600, 0o644} if public else {0o600})
                              and 0 < before.st_size <= maximum)
                chunks, total = [], 0
                while total <= maximum:
                    self.budget.check()
                    chunk = os.read(fd, min(65536, maximum + 1 - total))
                    if not chunk:
                        break
                    chunks.append(chunk); total += len(chunk)
                raw = b"".join(chunks)
                self.budget.check()
                proof.require(identity(before) == identity(os.fstat(fd)) == identity(os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False))
                              and len(raw) == before.st_size and hashlib.sha256(raw).hexdigest() == ref["sha256"])
                directory_unchanged(parent, parent_fd)
                proof.require(os.fstat(parent_fd).st_mode == parent_mode)
                proof.require(path.resolve() == path and identity(path.lstat()) == identity(before))
                self.budget.check()
                return raw
            finally:
                os.close(fd)
        finally:
            os.close(parent_fd)


class ClosureProvenanceVerifier(proof.ProvenanceVerifier):
    """Preserve the frozen verifier's ordinary cap and stable raw-file cache.

    Only an exact allowance already authenticated by the outer capsule/worker
    manifest reaches this path. It grants byte access, not semantic acceptance:
    the required final-worker adapter still authenticates the original closure.
    """
    def load(self, ref):
        proof.reference(ref)
        proof.require(isinstance(self._load, StableReader))
        selected = next((row for row in self._load.private_large_files
                         if row["reference"] == ref), None)
        if selected is None:
            return super().load(ref)
        original = proof.canonical(ref)
        argument = copy.deepcopy(ref)
        raw = self._load(argument)
        proof.require(proof.canonical(argument) == original and type(raw) is bytes
                      and 0 < len(raw) <= selected["maximum_bytes"]
                      and hashlib.sha256(raw).hexdigest() == ref["sha256"])
        key = (ref["path"], ref["sha256"])
        proof.require(not any(name == ref["path"] and hashed != ref["sha256"]
                              for name, hashed in self.raws))
        if key in self.raws:
            proof.require(raw == self.raws[key])
        self.raws[key] = raw
        return raw


class PrivateDirectory:
    """Exclusive retained files under a held owned0700 directory; no overwrite."""
    def __init__(self, path, budget, *, create):
        proof.require(isinstance(budget, Budget) and type(create) is bool)
        self.budget, self.fd = budget, None
        budget.check(); path = canonical_path(path)
        parent, fd = directory(path.parent, private=True)
        try:
            if create:
                os.mkdir(path.name, mode=0o700, dir_fd=fd); os.fsync(fd)
            self.path, opened = directory(path, private=True)
            try:
                directory_unchanged(parent, fd); budget.check()
                self.fd = opened
            except BaseException:
                os.close(opened)
                raise
        finally:
            os.close(fd)

    def check(self):
        self.budget.check()
        proof.require(self.fd is not None)
        directory_unchanged(self.path, self.fd)
        proof.require(stat.S_IMODE(os.fstat(self.fd).st_mode) == 0o700)

    def write(self, name, raw):
        self.check()
        proof.path(name, absolute=False)
        proof.require(Path(name).name == name and type(raw) is bytes and 0 < len(raw) <= proof.MAX_BYTES)
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.fd)
        try:
            opened = os.fstat(fd); proof.require(stat.S_IMODE(opened.st_mode) == 0o600)
            offset = 0
            while offset < len(raw):
                self.budget.check(); written = os.write(fd, raw[offset:offset + 65536])
                proof.require(type(written) is int and written > 0); offset += written
            os.fsync(fd)
            closed = os.fstat(fd)
            proof.require(stat.S_ISREG(closed.st_mode) and closed.st_uid == os.getuid() and closed.st_nlink == 1
                          and closed.st_size == len(raw) and stat.S_IMODE(closed.st_mode) == 0o600
                          and (closed.st_dev, closed.st_ino) == (opened.st_dev, opened.st_ino)
                          and identity(closed) == identity(os.stat(name, dir_fd=self.fd, follow_symlinks=False)))
            self.check(); os.fsync(self.fd); self.budget.check()
        finally:
            os.close(fd)
        return {"path": str(self.path / name), "sha256": hashlib.sha256(raw).hexdigest()}

    def document(self, name, value):
        return self.write(name, proof.canonical(value) + b"\n")

    def close(self):
        if self.fd is not None:
            fd, self.fd = self.fd, None
            os.close(fd)


class RecordedVerifier:
    """Persist each actual checked phase before its proof reaches a producer."""
    def __init__(self, verifier, checks_directory):
        proof.require(isinstance(verifier, proof.ProvenanceVerifier) and isinstance(checks_directory, PrivateDirectory))
        proof.require(isinstance(verifier._load, StableReader) and verifier._load.budget is checks_directory.budget)
        self.verifier, self.directory = verifier, checks_directory
        self.authorization = None
        self.refs = []

    def authorize(self):
        context = self.verifier.authorize()
        proof.require(str(self.directory.path) == context["authorization"]["current_checks_directory"])
        self.directory.check()
        self.authorization = context["authorization"]
        return context

    def __call__(self, **kwargs):
        proof.require(self.authorization is not None)
        phases = proof.CHECK_PHASES[self.authorization["case"]["family"]]
        index = len(self.refs)
        proof.require(index < len(phases) and kwargs.get("phase") == phases[index]
                      and len(self.verifier.validated_checks) == index)
        with self.directory.budget.cap(kwargs.get("deadline")):
            result = self.verifier(**kwargs)
            proof.require(len(self.verifier.validated_checks) == index + 1)
            row = copy.deepcopy(self.verifier.validated_checks[-1])
            ref = self.directory.document(f"{index + 1:02d}-{phases[index]}.json", row)
            proof.require(self.verifier.load(ref) == proof.canonical(row) + b"\n")
            self.refs.append(ref)
            return result


class InvocationJournal:
    """Record actual parent observations around Popen/wait; never execute them.

    The parent independently constructs and pins authorization.json in this
    directory before constructing this object. Success requires an original
    validated worker result. Complete family semantics are rechecked by the
    mandatory consume verifier after this independently pinned completion exists.
    """
    def __init__(self, *, verifier, directory, now=lambda: datetime.now(UTC)):
        proof.require(isinstance(verifier, proof.ProvenanceVerifier) and isinstance(directory, PrivateDirectory) and callable(now))
        proof.require(isinstance(verifier._load, StableReader) and verifier._load.budget is directory.budget)
        proof.require(verifier.completion_ref is None
                      and verifier.authorization_ref["path"] == str(directory.path / "authorization.json"))
        self.verifier, self.directory, self.now = verifier, directory, now
        self.state, self.authorization, self.process = "new", None, None

    def authorize_launch(self, *, case_id, nodeid, argv, executable_sha256, launcher_sha256, registry_sha256, sources):
        proof.require(self.state == "new")
        self.directory.check()
        proof.require(type(argv) is list and bool(argv) and all(type(value) is str and value and "\x00" not in value for value in argv))
        context = self.verifier.authorize(); authorization = context["authorization"]
        proof.require(case_id == authorization["case"]["case_id"] and nodeid == authorization["case"]["nodeid"]
                      and sources == authorization["sources"] and registry_sha256 == authorization["registry_sha256"]
                      and executable_sha256 == authorization["worker"]["executable_sha256"]
                      and launcher_sha256 == authorization["worker"]["launcher_sha256"]
                      and proof.digest(argv) == authorization["worker"]["command_sha256"])
        at = proof.now_utc(self.now()).isoformat()
        proof.require(proof.timestamp(authorization["created_at"]) <= proof.timestamp(at) < proof.timestamp(authorization["expires_at"]))
        self.directory.document("launch-intent.json", {"schema_version": "evolution-classifier-parent-launch-v1",
            "authorization": self.verifier.authorization_ref, "created_at": at, "case_id": case_id, "nodeid": nodeid,
            "worker": authorization["worker"], "status": "intent_before_spawn_no_execution_claim"})
        self.authorization, self.authorized_at, self.state = authorization, at, "authorized"
        return copy.deepcopy(context)

    def started(self, *, pid, process_start, started_at):
        proof.require(self.state == "authorized")
        proof.integer(pid, 1, 2**31 - 1); proof.integer(process_start, 1, 2**63 - 1)
        at = proof.timestamp(started_at)
        proof.require(proof.timestamp(self.authorized_at) <= at <= proof.now_utc(self.now())
                      and at < proof.timestamp(self.authorization["expires_at"]))
        self.process = {"pid": pid, "process_start": process_start, "started_at": started_at}
        self.directory.document("worker-started.json", {"schema_version": "evolution-classifier-parent-process-v1",
            "authorization": self.verifier.authorization_ref, **self.process, "status": "parent_observed_original_process"})
        self.state = "started"

    def finish(self, *, exit_code, elapsed_ms, result, observation_files, governance_files, current_checks):
        proof.require(self.state == "started")
        now = proof.now_utc(self.now())
        value = {"schema_version": proof.COMPLETION_SCHEMA, "authorization": self.verifier.authorization_ref,
            "authorization_id": self.authorization["authorization_id"], "case_id": self.authorization["case"]["case_id"],
            "nodeid": self.authorization["case"]["nodeid"], **self.process, "completed_at": now.isoformat(),
            "worker": self.authorization["worker"], "exit_code": exit_code, "elapsed_ms": elapsed_ms, "result": copy.deepcopy(result),
            "observation_files": copy.deepcopy(observation_files), "governance_files": copy.deepcopy(governance_files),
            "current_checks": copy.deepcopy(current_checks)}
        proof.validate_completion(value, authorization_ref=self.verifier.authorization_ref, authorization=self.authorization,
                                  observation_files=value["observation_files"], now=now)
        for ref in [*value["observation_files"].values(), *value["governance_files"].values(), *value["current_checks"]]:
            self.verifier.load(ref)
        self.verifier.stable()
        proof.require(proof.now_utc(self.now()) < proof.timestamp(self.authorization["expires_at"]))
        ref = self.directory.document("completion.json", value)
        self.state = "complete"
        return ref

    def failed(self, *, reason_code):
        proof.require(self.state != "complete" and reason_code in {"case_timeout", "worker_exit", "invalid_worker_result",
                      "provenance_refused", "io_failure", "parent_interrupted", "spawn_failed"})
        ref = self.directory.document("failed.json", {"schema_version": "evolution-classifier-parent-failure-v1",
            "authorization": self.verifier.authorization_ref, "reason_code": reason_code,
            "last_state": self.state, "process": copy.deepcopy(self.process), "status": "failed_no_completion_claim"})
        self.state = "failed"
        return ref
