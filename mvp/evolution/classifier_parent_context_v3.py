"""Explicit parent launch/completion authority for the closed deployed families.

The operator independently supplies a frozen capsule and original whole-run
deadline. This coordinator never starts workers, repairs deployment state, or
discovers qualification. The final-worker adapter is mandatory. Worker completion
is recorded only from the runner's actual validated exit/result, and both
collectors reuse its independent completion anchor. Parent consume observations
use the original whole-run budget, never a renewed worker/60-second allowance.
"""
from __future__ import annotations

import copy
import os
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from mvp.tests.evolution.e2e import classifier_fixture_builder_v3 as builder

from . import classifier_deployed_collection as collection
from . import classifier_provenance_io_v3 as io
from . import classifier_provenance_v3 as proof
from . import classifier_worker_invocation_v3 as worker
from .classifier_source_projection import project_sources
from .diagnostics import _read, validate_catalog
from .registry import Case, EvidenceError

SCHEMA = "evolution-classifier-parent-run-capsule-v1"
SELF = "mvp/evolution/classifier_parent_context_v3.py"
FIELDS = {"schema_version", "created_at", "valid_until", "source_root", "deadline_monotonic",
    "registry", "source_catalog", "identity", "corpus_manifest", "required_corpora", "deployment_state",
    "evaluator_config", "native_executables", "python_executable", "launcher", "invocations_root",
    "governance_root", "public_files", "cases"}
MODULES = {__name__: SELF, proof.__name__: "mvp/evolution/classifier_provenance_v3.py",
    io.__name__: "mvp/evolution/classifier_provenance_io_v3.py",
    worker.__name__: "mvp/evolution/classifier_worker_invocation_v3.py",
    collection.__name__: "mvp/evolution/classifier_deployed_collection.py",
    "evolution.classifier_source_projection": "mvp/evolution/classifier_source_projection.py"}
REQUIRED = builder.REQUIRED | set(MODULES.values()) | {"mvp/evolution/classifier_source_projection.py",
    "mvp/evolution/run.py", "mvp/evolution/worker.py"}
FAILURES = {"case_timeout", "worker_exit", "invalid_worker_result", "provenance_refused",
    "io_failure", "parent_interrupted", "spawn_failed"}


def utc_now():
    return datetime.now(UTC)


def refuse_current(**kwargs):
    raise EvidenceError("classifier_parent_current_unavailable_before_consume")


def pinned_private(path, reader):
    """Parent observes new output bytes; this pin supplies no launch authority."""
    reader.budget.check()
    target = io.canonical_path(path)
    parent, descriptor = io.directory(target.parent, private=True)
    try:
        raw = _read(target, proof.MAX_BYTES, private=True)
        ref = {"path": str(target), "sha256": proof.raw_digest(raw)}
        proof.require(reader(ref) == raw)
        io.directory_unchanged(parent, descriptor)
        reader.budget.check()
        return ref, raw
    finally:
        os.close(descriptor)


def closed_files(path, allowed, reader, *, required=None):
    """Bound exact new private output inventory, then pin its original bytes."""
    parent, fd = io.directory(path, private=True)
    try:
        names = set()
        with os.scandir(parent) as entries:
            for entry in entries:
                reader.budget.check()
                proof.require(len(names) < len(allowed) and entry.name in allowed and entry.name not in names)
                names.add(entry.name)
        proof.require(names == allowed if required is None else required <= names)
        values = {name: pinned_private(parent / name, reader) for name in sorted(names)}
        io.directory_unchanged(parent, fd)
        return values
    finally:
        os.close(fd)


def validate_capsule(value, *, source_root, deadline, now):
    proof.exact(value, FIELDS | ({"private_large_files"} if "private_large_files" in value else set()))
    proof.require(value["schema_version"] == SCHEMA and value["source_root"] == str(source_root)
        and value["deadline_monotonic"] == deadline
        and proof.timestamp(value["created_at"]) <= now < proof.timestamp(value["valid_until"]))
    proof.number(deadline, .001, 1e15)
    proof.require((proof.timestamp(value["valid_until"]) - proof.timestamp(value["created_at"])).total_seconds() <= 86400)
    proof.require(source_root.is_absolute() and source_root.resolve() == source_root)
    validate_catalog(value["source_catalog"], source_root)
    proof.selected_identity(value["identity"])
    for name in ("registry", "corpus_manifest", "evaluator_config", "python_executable", "launcher"):
        proof.reference(value[name])
    proof.require(value["registry"]["path"] == str(source_root / "mvp/evolution/cases.json")
        and value["launcher"]["path"] == str(source_root / "mvp/evolution/worker.py"))
    for name in ("deployment_state", "invocations_root", "governance_root"):
        proof.path(value[name])
    proof.exact(value["native_executables"], {"docker", "kubectl"})
    for ref in value["native_executables"].values(): proof.reference(ref)
    proof.require(value["native_executables"]["docker"]["path"] == "/usr/bin/docker"
        and value["native_executables"]["kubectl"]["path"] == value["deployment_state"] + "/bin/kubectl")
    proof.require(type(value["public_files"]) is dict and len(value["public_files"]) <= 8192)
    for name, hashed in value["public_files"].items(): proof.path(name); proof.sha(hashed)
    io.private_large_allowances(value.get("private_large_files", ()), value["public_files"])
    proof.require(type(value["cases"]) is dict and 1 <= len(value["cases"]) <= len(proof.CASE_FAMILIES)
        and set(value["cases"]) <= set(proof.CASE_FAMILIES))
    for row in value["cases"].values():
        proof.exact(row, {"nodeid", "minimum_assertions", "timeout_seconds", "sources", "preflight", "receipts"})
        proof.require(type(row["nodeid"]) is str and 1 <= len(row["nodeid"]) <= 2048)
        proof.integer(row["minimum_assertions"], 1, 100000); proof.number(row["timeout_seconds"], 1, 3600)
        proof.source_map(row["sources"]); proof.reference(row["preflight"])
        proof.require(REQUIRED <= set(row["sources"])
            and {name: hashed for name, hashed in row["sources"].items() if name.endswith(".py")} == value["source_catalog"])
        proof.exact(row["receipts"], proof.BASE_PROOFS | {"node_boundary"})
        for ref in row["receipts"].values(): proof.reference(ref)
        proof.require(all(value["public_files"].get(str(source_root / name)) == hashed for name, hashed in row["sources"].items()))
    return value


def loaded_sources(root, sources, reader):
    """The executing fixed modules must be the independently frozen source files."""
    proof.require(REQUIRED <= set(sources))
    for name, relative in {**builder.MODULES, **MODULES}.items():
        proof.require(Path(getattr(sys.modules.get(name), "__file__", "")).resolve() == root / relative)
    for relative in REQUIRED:
        reader({"path": str(root / relative), "sha256": sources[relative]})


def executable_identity(ref, budget):
    owner = Path(ref["path"]).lstat().st_uid
    proof.require(owner in {0, os.getuid()})
    with builder.native.executable(ref, owner=owner, check=budget.check):
        pass


def process_identity(pid, process_start, argv, executable, budget):
    """Read only the runner's reserved original child, without spawning/waiting."""
    proof.integer(pid, 1, 2**31 - 1); proof.integer(process_start, 1, 2**63 - 1)
    read = builder.current.kernel_read
    def started():
        raw = read(f"/proc/{pid}/stat", deadline=budget.deadline, maximum=8192)
        proof.require(raw.startswith(str(pid).encode() + b" ("))
        tail = raw.rsplit(b") ", 1)
        proof.require(len(tail) == 2 and len(tail[1].split()) >= 20)
        fields = tail[1].split()
        proof.require(fields[19].isdigit() and int(fields[19]) == process_start
            and fields[1] == str(os.getpid()).encode())
    started()
    raw = read(f"/proc/{pid}/cmdline", deadline=budget.deadline, maximum=65536)
    proof.require(raw == b"\0".join(part.encode() for part in argv) + b"\0")
    owner = Path(executable["path"]).lstat().st_uid
    proof.require(owner in {0, os.getuid()})
    with builder.native.executable(executable, owner=owner, check=budget.check) as fd:
        actual, expected = Path(f"/proc/{pid}/exe").stat(), os.fstat(fd)
        proof.require((actual.st_dev, actual.st_ino) == (expected.st_dev, expected.st_ino))
        started()
        proof.require(read(f"/proc/{pid}/cmdline", deadline=budget.deadline, maximum=65536) == raw)
    budget.check()


class ParentClassifierContext:
    """Fixed runner context; all authority is explicit, no environment factories."""
    def __init__(self, capsule_ref, source_root, deadline, *, qualify_final_worker):
        try:
            self._initialize(capsule_ref, source_root, deadline, qualify_final_worker=qualify_final_worker)
        except (OSError, ValueError, TypeError, KeyError):
            raise EvidenceError("classifier_parent_authority_invalid") from None

    def _initialize(self, capsule_ref, source_root, deadline, *, qualify_final_worker):
        proof.require(callable(qualify_final_worker))
        self.ref, self.root = copy.deepcopy(capsule_ref), Path(source_root)
        self.budget = io.Budget(deadline)
        self.qualify = qualify_final_worker
        self.initial = io.StableReader(self.budget, public_files={})
        self.raw = self.initial(self.ref)
        self.capsule = validate_capsule(proof.document(self.raw), source_root=self.root, deadline=deadline, now=utc_now())
        self.reader = io.StableReader(self.budget, public_files=self.capsule["public_files"],
            private_large_files=self.capsule.get("private_large_files", ()))
        self.invocations, self.consume_verifiers = {}, {}
        self.closed = False
        self.check()

    def check(self):
        proof.require(not self.closed)
        self.budget.check()
        proof.require(self.initial(self.ref) == self.raw and proof.document(self.raw) == self.capsule)
        validate_capsule(self.capsule, source_root=self.root, deadline=self.budget.deadline, now=utc_now())

    def __enter__(self):
        self.check()
        return self

    def __exit__(self, _kind, error, _traceback):
        try:
            self.close()
        except EvidenceError:
            if error is None:
                raise
            error.add_note("classifier_parent_context_close_failed")

    @property
    def deadline(self):
        return self.capsule["deadline_monotonic"]

    def close(self):
        if self.closed:
            return
        self.closed = True
        errors = []
        for invocation in self.invocations.values():
            if invocation.journal is not None and invocation.journal.state in {"authorized", "started"}:
                invocation.retain_failure("parent_interrupted", "close")
            try: invocation.directory.close()
            except BaseException: errors.append("classifier_parent_directory_close_failed")  # noqa: BLE001 - finite refusal/cleanup after preserving private evidence
        if errors:
            raise EvidenceError("classifier_parent_directory_close_failed") from None

    def prepare(self, *, case, root, destination, sources, argv, source_catalog, registry_sha256, deadline):
        """Runner supplies its independently frozen case/argv and original cap."""
        self.check()
        proof.require(isinstance(case, Case) and case.case_id in self.capsule["cases"]
            and case.case_id not in self.invocations and Path(root) == self.root)
        proof.number(deadline, .001, self.budget.deadline)
        proof.require(time.monotonic() < deadline <= time.monotonic() + case.timeout_s)
        identity = str(uuid4())
        directory = io.PrivateDirectory(Path(self.capsule["invocations_root"]) / identity, self.budget, create=True)
        invocation = ParentInvocation(self, directory, identity, case, deadline)
        self.invocations[case.case_id] = invocation  # A failed original prepare cannot be retried.
        try:
            directory.document("prepare-intent.json", {"schema_version": "evolution-classifier-parent-prepare-v1",
                "capsule": self.ref, "case_id": case.case_id, "authorization_id": identity,
                "created_at": utc_now().isoformat(), "status": "intent_before_authorization_no_execution_claim"})
            with self.budget.cap(deadline):
                invocation.prepare(destination=Path(destination), sources=sources, argv=argv,
                    source_catalog=Path(source_catalog), registry_sha256=registry_sha256)
            self.check()
            return invocation
        except BaseException:  # noqa: BLE001 - finite refusal/cleanup after preserving private evidence
            invocation.retain_failure("provenance_refused", "prepare")
            raise EvidenceError("classifier_parent_prepare_failed") from None


class ParentInvocation:
    def __init__(self, parent, directory, identity, case, deadline):
        self.parent, self.directory, self.identity, self.case, self.deadline = parent, directory, identity, case, deadline
        self.journal = self.manifest = self.authorization_ref = self.completion = None
        self.worker_environment = {}
        self.consume_count = 0

    def verifier(self, *, current=refuse_current, completion=None):
        p, row = self.parent, self.parent.capsule["cases"][self.case.case_id]
        return io.ClosureProvenanceVerifier(authorization_ref=self.authorization_ref,
            expected_case=self.case.case_id, expected_nodeid=self.case.nodeid, expected_sources=row["sources"],
            expected_registry_sha256=p.capsule["registry"]["sha256"], expected_corpus_manifest=p.capsule["corpus_manifest"],
            load=p.reader, qualify_final_worker=p.qualify, current=current, now=utc_now, completion_ref=completion)

    def prepare(self, *, destination, sources, argv, source_catalog, registry_sha256):
        p, c, case = self.parent, self.parent.capsule, self.case
        row = c["cases"][case.case_id]
        proof.require(row["nodeid"] == case.nodeid and row["minimum_assertions"] == len(case.assertions)
            and row["timeout_seconds"] == case.timeout_s and registry_sha256 == c["registry"]["sha256"])
        registry = proof.document(p.reader(c["registry"]))
        actual = [entry for phase in registry["phases"].values() for entry in phase["cases"] if entry["case_id"] == case.case_id]
        proof.require(len(actual) == 1 and actual[0] == {**asdict(case), "assertions": list(case.assertions)})
        proof.require(sources == c["source_catalog"] and project_sources(p.root, sources, deadline=p.budget.deadline) == row["sources"])
        loaded_sources(p.root, row["sources"], p.reader)
        catalog_ref, raw = pinned_private(source_catalog, p.reader)
        proof.require(proof.document(raw) == sources and source_catalog == destination / (case.case_id + ".sources.json"))
        output = destination / "measurements" / case.case_id
        proof.require(destination.is_absolute() and destination.resolve() == destination
            and destination.parent == p.root / ".evolution/reports")
        for path in (destination, output):
            _, fd = io.directory(path, private=True); os.close(fd)
        proof.require(type(argv) is list and len(argv) == 16 and all(type(part) is str and part and "\0" not in part for part in argv))
        proof.require(Path(argv[0]).resolve() == Path(c["python_executable"]["path"]))
        expected = [argv[0], "-B", "-m", "evolution.worker", case.nodeid, str(destination / (case.case_id + ".json")),
            "--source-catalog", str(source_catalog), "--basetemp", argv[9], "--classifier-case-id", case.case_id,
            "--classifier-registry-sha256", registry_sha256, "--classifier-deadline", repr(self.deadline)]
        proof.require(argv == expected)
        temporary = Path(argv[9])
        proof.require(temporary.name == "pytest" and temporary.parent.parent == destination.parent
            and temporary.parent.name.startswith(".worker-") and temporary.resolve() == temporary)
        _, fd = io.directory(temporary.parent, private=True); os.close(fd)
        executable_identity(c["python_executable"], p.budget)
        proof.require(p.reader(c["launcher"]) and c["launcher"]["sha256"] == sources["mvp/evolution/worker.py"])
        preflight = proof.document(p.reader(row["preflight"]))
        expires = min(proof.timestamp(c["valid_until"]), proof.timestamp(preflight["valid_until"]))
        governed = proof.CASE_FAMILIES[case.case_id] in {"budget", "resource"}
        if governed: proof.require(os.environ.get("EVOLUTION_EVALUATOR_OPERATION_ROOT") == c["governance_root"])
        auth = {"schema_version": proof.AUTHORIZATION_SCHEMA, "authorization_id": self.identity,
            "created_at": utc_now().isoformat(), "expires_at": expires.isoformat(),
            "case": {"case_id": case.case_id, "nodeid": case.nodeid, "family": proof.CASE_FAMILIES[case.case_id], "minimum_assertions": len(case.assertions)},
            "sources": copy.deepcopy(row["sources"]), "registry_sha256": registry_sha256, "identity": c["identity"],
            "preflight": row["preflight"], "receipts": row["receipts"],
            "worker": {"executable_sha256": c["python_executable"]["sha256"], "command_sha256": proof.digest(argv),
                "launcher_sha256": c["launcher"]["sha256"], "timeout_seconds": case.timeout_s, "automatic_retry": False, "warmup_requests": 0},
            "output_directory": str(output), "governance_root": c["governance_root"] if governed else None,
            "required_corpora": c["required_corpora"], "corpus_manifest": c["corpus_manifest"],
            "current_checks_directory": str(self.directory.path / "checks")}
        self.authorization_ref = self.directory.document("authorization.json", auth)
        self.journal = io.InvocationJournal(verifier=self.verifier(), directory=self.directory, now=utc_now)
        self.authorized = self.journal.authorize_launch(case_id=case.case_id, nodeid=case.nodeid, argv=argv,
            executable_sha256=c["python_executable"]["sha256"], launcher_sha256=c["launcher"]["sha256"],
            registry_sha256=registry_sha256, sources=row["sources"])
        self.manifest = {"schema_version": worker.SCHEMA, "authorization": self.authorization_ref,
            "corpus_manifest": c["corpus_manifest"], "case_id": case.case_id, "nodeid": case.nodeid,
            "registry_sha256": registry_sha256, "sources": row["sources"], "source_catalog": catalog_ref,
            "deadline_monotonic": self.deadline, "public_files": c["public_files"], "evaluator_config": c["evaluator_config"],
            "deployment_state": c["deployment_state"], "native_executables": c["native_executables"]}
        if "private_large_files" in c:
            self.manifest["private_large_files"] = copy.deepcopy(c["private_large_files"])
        worker.validate(self.manifest, case_id=case.case_id, nodeid=case.nodeid, registry_sha256=registry_sha256,
            sources=row["sources"], source_catalog_ref=catalog_ref, source_catalog=sources, root=p.root, deadline=self.deadline)
        ref = self.directory.document("worker-manifest.json", self.manifest)
        self.worker_environment = {worker.MANIFEST_ENV: ref["path"], worker.SHA_ENV: ref["sha256"]}
        self.argv = copy.deepcopy(argv)
        self.result_path = destination / (case.case_id + ".json")

    def retain_failure(self, reason, stage):
        """Attempt within original outer budget; preserve any already written prefix."""
        try:
            if self.journal is not None and self.journal.state not in {"complete", "failed"}:
                self.journal.failed(reason_code=reason)
            else:
                self.directory.document(stage + "-failed.json", {"schema_version": "evolution-classifier-parent-failure-prefix-v1",
                    "capsule": self.parent.ref, "case_id": self.case.case_id, "reason_code": reason,
                    "status": "failed_no_consume_authority"})
        except BaseException:  # noqa: BLE001, S110 - never refresh the original deadline for failure retention
            pass

    def started(self, *, pid, process_start, started_at):
        try:
            self.parent.check()
            with self.parent.budget.cap(self.deadline):
                proof.require(self.journal is not None)
                process_identity(pid, process_start, self.argv, self.parent.capsule["python_executable"], self.parent.budget)
                self.journal.started(pid=pid, process_start=process_start, started_at=started_at)
        except BaseException:  # noqa: BLE001 - finite refusal/cleanup after preserving private evidence
            self.retain_failure("provenance_refused", "started")
            raise EvidenceError("classifier_parent_started_failed") from None

    def failed(self, *, reason_code):
        proof.require(reason_code in FAILURES)
        self.parent.consume_verifiers.pop(self.case.case_id, None)
        self.retain_failure(reason_code, "worker")

    def complete(self, *, exit_code, result, elapsed_ms):
        p = self.parent
        try:
            p.check()
            with p.budget.cap(self.deadline):
                proof.require(self.journal is not None and self.journal.state == "started")
                result_ref, raw_result = pinned_private(self.result_path, p.reader)
                proof.require(proof.document(raw_result) == result)
                self.directory.document("worker-result-reference.json", {"result": result_ref, "status": "parent_observed_original_worker_result"})
                auth = self.authorized["authorization"]
                family = auth["case"]["family"]
                values = closed_files(Path(auth["output_directory"]), proof.FAMILY_FILES[family], p.reader)
                refs = {name: row[0] for name, row in values.items()}
                observation = proof.document(values["classifier-" + family + "-observation.json"][1])
                governance = {}
                if auth["governance_root"] is not None:
                    proof.uuid(observation.get("operation"))
                    operation = observation["operation"]
                    fixed = {"intent.json", "completion.json", "release-matrix-release.json", "release-restore-original-binding.json"}
                    allowed = fixed | {f"request-{index:02d}-{suffix}.json" for index in range(1, 17) for suffix in ("intent", "returned")}
                    rows = closed_files(Path(auth["governance_root"]) / operation, allowed, p.reader, required=fixed)
                    governance = {name: row[0] for name, row in rows.items()}
                    proof.verify_governance(governance, authorization=auth, observation=observation, operation=operation, load=p.reader)
                checks = [pinned_private(Path(auth["current_checks_directory"]) / f"{index + 1:02d}-{phase}.json", p.reader)[0]
                    for index, phase in enumerate(proof.CHECK_PHASES[family])]
                self.completion = self.journal.finish(exit_code=exit_code, elapsed_ms=elapsed_ms, result=result,
                    observation_files=refs, governance_files=governance, current_checks=checks)
            p.check()
            p.consume_verifiers[self.case.case_id] = self.consume
        except BaseException:  # noqa: BLE001 - finite refusal/cleanup after preserving private evidence
            p.consume_verifiers.pop(self.case.case_id, None)
            self.retain_failure("invalid_worker_result", "completion")
            raise EvidenceError("classifier_parent_completion_failed") from None

    def consume(self, **kwargs):
        """Fresh read-only parent check anchored to the original completed worker."""
        p = self.parent
        cluster = executor = attempt = raw_attempts = None
        primary = None
        try:
            p.check()
            proof.require(self.completion is not None and self.journal.state == "complete" and kwargs.get("phase") == "consume")
            self.consume_count += 1
            proof.require(self.consume_count <= 4)
            attempt = io.PrivateDirectory(self.directory.path / f"consume-{self.consume_count:02d}", p.budget, create=True)
            attempt.document("intent.json", {"schema_version": "evolution-classifier-parent-consume-v1",
                "authorization": self.authorization_ref, "completion": self.completion, "status": "intent_before_current_observation"})
            raw_attempts = io.PrivateDirectory(attempt.path / "native-attempts", p.budget, create=True)
            manifest = copy.deepcopy(self.manifest)
            manifest["deadline_monotonic"] = p.budget.deadline  # Explicit original parent scope, never a new timeout.
            loaded_sources(p.root, manifest["sources"], p.reader)
            verifier = self.verifier(completion=self.completion)
            context = verifier.authorize()
            executor = builder.native.NativeReadOnlyExecutor(manifest, context["preflight"], p.budget, p.root)
            constructor = builder.readonly.ReadOnlyClusterConstructor(source_root=p.root, reader=p.reader, executor=executor)
            cluster = constructor(context=copy.deepcopy(context), manifest=manifest, deadline=p.budget.deadline)
            def current(*, phase, operation, deadline):
                proof.require(phase == "consume" and deadline is None)
                checker = cluster._classifier_current_checker
                proof.require(isinstance(checker, builder.current.CurrentChecker)
                    and checker.authorization_sha256 == self.authorization_ref["sha256"] and checker.executor is executor)
                with cluster.deployment.lock():
                    return checker.observe_locked(phase=phase, operation=operation, deadline=p.budget.deadline)
            verifier.current = current
            result = verifier(**kwargs)
            proof.require(len(verifier.validated_checks) == 1)
            attempt.document("current.json", verifier.validated_checks[0])
            loaded_sources(p.root, manifest["sources"], p.reader)
            p.check()
            return result
        except BaseException:  # noqa: BLE001 - finite refusal/cleanup after preserving private evidence
            primary = EvidenceError("classifier_parent_consume_failed")
            if attempt is not None:
                try: attempt.document("failed.json", {"status": "failed_no_current_proof", "completion": self.completion})
                except BaseException: primary.add_note("classifier_parent_failure_retention_failed")  # noqa: BLE001 - finite refusal/cleanup after preserving private evidence
            raise primary from None
        finally:
            errors = []
            if cluster is not None:
                try: cluster.close()
                except BaseException: errors.append("classifier_parent_cluster_close_failed")  # noqa: BLE001 - finite refusal/cleanup after preserving private evidence
            if executor is not None and raw_attempts is not None:
                try:
                    builder.save_attempts(executor, raw_attempts, authorization_ref=self.authorization_ref,
                        outcome="worker_failed" if primary is not None or errors else "worker_returned")
                except BaseException: errors.append("classifier_parent_attempt_retention_failed")  # noqa: BLE001 - finite refusal/cleanup after preserving private evidence
            for directory in (raw_attempts, attempt):
                if directory is not None:
                    try: directory.close()
                    except BaseException: errors.append("classifier_parent_directory_close_failed")  # noqa: BLE001 - finite refusal/cleanup after preserving private evidence
            if errors:
                if primary is None:
                    primary = EvidenceError("classifier_parent_consume_cleanup_failed")
                    for note in errors: primary.add_note(note)
                    raise primary from None
                for note in errors: primary.add_note(note)
