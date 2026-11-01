"""Closed local acceptance with isolated execution and signed, append-only evidence."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import platform
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .classifier_provenance_v3 import CASE_FAMILIES as CLASSIFIER_CASES
from .classifier_worker_invocation_v3 import MANIFEST_ENV, SHA_ENV
from .dependencies import (
    ROOT,
    canonical,
    digest,
    phase_artifacts,
    verify_environment,
    verify_installed_wheels,
)
from .diagnostics import (
    check_case,
    collect_diagnostics,
    source_snapshot,
    write_catalog,
)
from .gateway_route_case import (
    RECOVERY_REQUIRED as ROUTE_RECOVERY_REQUIRED, ROUTE_ENV, RouteCase, applies as route_case_applies,
    latch_recovery as latch_route_recovery, recovery_latched as route_recovery_latched,
)
from .measurements import FILES as MEASUREMENT_FILES
from .measurements import collect_measurements, prepare_directory
from .registry import Case, EvidenceError, load_registry, read_json

SOURCE_ROOTS = ("mvp", "deploy", "services", "examples", "tests/evolution-browser")
SOURCE_TOP = (
    "pyproject.toml",
    "justfile",
    "requirements.lock",
    "dev-requirements.lock",
    "evolution-requirements.lock",
)
WORKER_REASON_CODES = frozenset(
    {
        "case_inventory_mismatch",
        "collection_failed",
        "unexpected_case",
        "required_case_skipped",
        "required_case_xfailed",
        "case_setup_failed",
        "case_call_failed",
        "environment_clock_discontinuity",
        "case_teardown_failed",
        "case_missing",
        "execution_incomplete",
        "assertions_missing",
        "pytest_nonzero_exit",
    }
)


def source_files(root: Path) -> dict[str, str]:
    paths = {root / name for name in SOURCE_TOP}
    for directory in SOURCE_ROOTS:
        base = root / directory
        if base.exists():
            paths.update(
                path
                for path in base.rglob("*")
                if path.is_file()
                and not any(
                    part.startswith(".")
                    or part == "__pycache__"
                    or part.endswith(".egg-info")
                    for part in path.relative_to(base).parts
                )
                and path.suffix not in {".pyc", ".pyo"}
            )
    result = {}
    for path in sorted(paths):
        if path.is_symlink():
            raise EvidenceError("source_symlink_rejected")
        if not path.is_file():
            raise EvidenceError("source_file_missing")
        result[str(path.relative_to(root))] = digest(path)
    return result


def assert_same_source(expected: dict[str, str], root: Path):
    if source_files(root) != expected:
        raise EvidenceError("source_changed_during_execution")


def lock_files(source):
    """Include nested deployment, worker and model locks in provenance."""
    return {
        name: value
        for name, value in source.items()
        if name.endswith((".lock", ".lock.json"))
    }


def safe_failure(error, fallback):
    if isinstance(error, EvidenceError) and re.fullmatch(
        r"[a-z][a-z0-9_]{0,127}", str(error)
    ):
        return str(error)
    return fallback


def write_once(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    raw = canonical(value) + b"\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(raw)
        output.flush()
        os.fsync(output.fileno())


def signing_key(root: Path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    directory = root / ".evolution/secrets"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / "acceptance-ed25519.key"
    public_path = directory / "acceptance-ed25519.pub"
    if path.is_symlink() or public_path.is_symlink():
        raise EvidenceError("signing_key_symlink_rejected")
    if path.exists():
        if path.stat().st_mode & 0o077:
            raise EvidenceError("signing_key_permissions")
        key = Ed25519PrivateKey.from_private_bytes(path.read_bytes())
    else:
        key = Ed25519PrivateKey.generate()
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(
                key.private_bytes(
                    serialization.Encoding.Raw,
                    serialization.PrivateFormat.Raw,
                    serialization.NoEncryption(),
                )
            )
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    if public_path.exists():
        if public_path.read_bytes() != public:
            raise EvidenceError("signing_trust_root_mismatch")
    else:
        descriptor = os.open(public_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(public)
    return key, hashlib.sha256(public).hexdigest()


def sign_report(report: dict, key, key_id: str):
    payload = dict(report, key_id=key_id)
    payload.pop("signature", None)
    payload["signature"] = base64.b64encode(key.sign(canonical(payload))).decode(
        "ascii"
    )
    return payload


def verify_report(report: dict, public_key: bytes):
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    payload = dict(report)
    try:
        signature = base64.b64decode(payload.pop("signature"), validate=True)
        if payload.get("key_id") != hashlib.sha256(public_key).hexdigest():
            raise EvidenceError("evidence_key_mismatch")
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            signature, canonical(payload)
        )
    except (KeyError, ValueError, InvalidSignature) as exc:
        raise EvidenceError("evidence_signature_invalid") from exc


def worker_process_start(pid):
    """Observe the original child's kernel start tick before wait can reap it."""
    if type(pid) is not int or not 0 < pid < 2**31:
        raise EvidenceError("classifier_worker_identity_invalid")
    with Path(f"/proc/{pid}/stat").open("rb") as stream:
        raw = stream.read(8193)
    if len(raw) > 8192 or not raw.startswith(str(pid).encode() + b" ("):
        raise EvidenceError("classifier_worker_identity_invalid")
    tail = raw.rsplit(b") ", 1)
    fields = tail[-1].split()
    if len(tail) != 2 or len(fields) < 20 or not fields[19].isdigit() or int(fields[19]) <= 0:
        raise EvidenceError("classifier_worker_identity_invalid")
    return int(fields[19])


def execute_case(case: Case, root: Path, destination: Path, *, sources=None, classifier_context=None):
    lifecycle = {"route": None, "process": None, "finalizer_attempted": False}
    try:
        return _execute_case(case, root, destination, sources=sources,
            classifier_context=classifier_context, route_lifecycle=lifecycle)
    except BaseException:
        if route_case_applies(case.nodeid):
            try:
                latch_route_recovery(envelope=None if lifecycle["route"] is None else lifecycle["route"].envelope_ref)
            except (EvidenceError, OSError):
                pass
        raise
    finally:
        route = lifecycle["route"]
        if route is not None and not lifecycle["finalizer_attempted"]:
            lifecycle["finalizer_attempted"] = True
            try:
                if lifecycle["process"] is not None:
                    route.finish(lifecycle["process"], passed=False)
                else:
                    route.abandon_before_worker()
            except BaseException:
                # Preserve a pending interruption, while refusing any further
                # live case even if cleanup evidence publication itself failed.
                try:
                    latch_route_recovery(envelope=route.envelope_ref)
                except (EvidenceError, OSError):
                    pass


def _execute_case(case: Case, root: Path, destination: Path, *, sources=None,
                  classifier_context=None, route_lifecycle):
    started = time.monotonic()
    path = destination / (case.case_id + ".json")
    env = dict(
        os.environ,
        PYTHONPATH=str(root / "mvp"),
        PYTHONDONTWRITEBYTECODE="1",
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
    )
    # These can inject command line arguments or a different test/plugin inventory.
    env.pop("PYTEST_ADDOPTS", None)
    env.pop("PYTEST_PLUGINS", None)
    env.pop("PYTHONSTARTUP", None)
    env.pop("PYTEST_DEBUG_TEMPROOT", None)
    env.pop("EVOLUTION_TEST_EVIDENCE_DIR", None)
    env.pop(MANIFEST_ENV, None)
    env.pop(SHA_ENV, None)
    # Only this exact deployed matrix receives a confined route journal.
    env.pop(ROUTE_ENV, None)
    catalog_path = destination / (case.case_id + ".sources.json")
    command = [sys.executable, "-B", "-m", "evolution.worker", case.nodeid, str(path),
               "--source-catalog", str(catalog_path)]
    frozen_sources = {}
    catalog_ready = False
    platform_preparing = False
    classifier = case.case_id in CLASSIFIER_CASES
    invocation = None
    worker_deadline = None
    route_required = route_case_applies(case.nodeid)
    route = None
    process = None
    try:
        if classifier and classifier_context is None:
            raise EvidenceError("classifier_invocation_context_required")
        frozen_sources = source_snapshot(root, sources)
        write_catalog(catalog_path, root, frozen_sources)
        catalog_ready = True
        if route_required:
            route = RouteCase(case=case, root=root, sources=frozen_sources, environ=env)
            route_lifecycle["route"] = route
            env.update(route.environment())
        # Keep fixture files private and separate from signed report artifacts.
        # Each attempt gets a new directory; pytest never receives an existing
        # basetemp that it could recursively delete. Retain failed attempts too.
        parent = destination.absolute().parent
        metadata = parent.lstat()
        if (parent.resolve() != parent or not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != os.getuid() or metadata.st_mode & 0o022):
            raise OSError("worker_temporary_parent_invalid")
        temporary = Path(tempfile.mkdtemp(prefix=".worker-", dir=parent))
        system_temporary = temporary / "system"
        system_temporary.mkdir(mode=0o700)
        env.update(TMPDIR=str(system_temporary), TMP=str(system_temporary),
                   TEMP=str(system_temporary),
                   PYTHONPYCACHEPREFIX=str(temporary / "bytecode"))
        command.extend(["--basetemp", str(temporary / "pytest")])
        measurement_directory = prepare_directory(destination, case.case_id)
        if measurement_directory is not None:
            env["EVOLUTION_TEST_EVIDENCE_DIR"] = str(measurement_directory)
        from .platform_database import NODE as PLATFORM_DATABASE_NODE
        from .platform_database import environment_for_case
        platform_preparing = case.nodeid == PLATFORM_DATABASE_NODE
        env.update(environment_for_case(case.nodeid, root=root, sources=frozen_sources, environ=env))
        platform_preparing = False
        if classifier:
            # This original case allowance includes authorization work; it is
            # never reset after preparing the independently pinned manifest.
            worker_deadline = started + case.timeout_s
            registry_sha256 = digest(root / "mvp/evolution/cases.json")
            command.extend(["--classifier-case-id", case.case_id,
                            "--classifier-registry-sha256", registry_sha256,
                            "--classifier-deadline", repr(worker_deadline)])
            original_command = tuple(command)
            invocation = classifier_context.prepare(case=case, root=root, destination=destination,
                sources=frozen_sources, argv=command, source_catalog=catalog_path,
                registry_sha256=registry_sha256, deadline=worker_deadline)
            if tuple(command) != original_command or set(invocation.worker_environment) != {MANIFEST_ENV, SHA_ENV}:
                raise EvidenceError("classifier_invocation_context_invalid")
            env.update(invocation.worker_environment)
            if time.monotonic() >= worker_deadline:
                raise subprocess.TimeoutExpired(command, case.timeout_s)
        if route is not None:
            process, _ = route.spawn(command, cwd=root, env=env)
            route_lifecycle["process"] = process
        else:
            process = subprocess.Popen(
                command, cwd=root, env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        try:
            if invocation is not None:
                invocation.started(pid=process.pid, process_start=worker_process_start(process.pid),
                    started_at=datetime.now(UTC).isoformat())
            allowance = case.timeout_s if worker_deadline is None else max(0, worker_deadline - time.monotonic())
            process.wait(timeout=allowance)
        except BaseException as primary:
            # The case may own real server/runtime children. Terminating only
            # pytest would leave those processes alive after a failed gate.
            if route is not None:
                # The route parent binds PID/start/argv and uses pidfds. Its
                # separately bounded finalizer records any unresolved custody.
                try:
                    route.terminate_worker(process)
                except BaseException:
                    primary.add_note("Gateway route worker custody requires independent recovery")
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            raise
    except subprocess.TimeoutExpired:
        result = {"status": "fail", "assertions": 0, "reason_codes": ["case_timeout"]}
    except OSError:
        result = {
            "status": "fail",
            "assertions": 0,
            "reason_codes": ["worker_start_failed"],
        }
    except EvidenceError:
        result = {
            "status": "fail", "assertions": 0,
            "reason_codes": ["classifier_provenance_missing_or_invalid" if classifier else
                             "platform_database_allocation_failed" if platform_preparing else
                             "measurement_evidence_missing_or_invalid" if catalog_ready
                             else "diagnostic_catalog_missing_or_invalid"],
        }
    else:
        try:
            result = read_json(path)
            if (
                not isinstance(result, dict)
                or result.get("nodeid") != case.nodeid
                or set(result)
                != {
                    "nodeid",
                    "status",
                    "duration_ms",
                    "assertions",
                    "reason_codes",
                }
            ):
                raise EvidenceError("invalid_worker_evidence")
            if (
                result["status"] not in ("pass", "fail")
                or type(result["duration_ms"]) not in (int, float)
                or not 0 <= result["duration_ms"] <= 1e12
                or not math.isfinite(result["duration_ms"])
                or type(result["assertions"]) is not int
                or result["assertions"] < 0
                or not isinstance(result["reason_codes"], list)
                or any(
                    not isinstance(code, str) or code not in WORKER_REASON_CODES
                    for code in result["reason_codes"]
                )
                or len(result["reason_codes"]) != len(set(result["reason_codes"]))
                or (result["status"] == "pass" and result["reason_codes"])
            ):
                raise EvidenceError("invalid_worker_evidence")
            if result["status"] != "pass" or process.returncode != 0:
                result["status"] = "fail"
                result["reason_codes"] = sorted(
                    set(result["reason_codes"]) | {"case_failed"}
                )
            if type(result["assertions"]) is not int or result["assertions"] < len(
                case.assertions
            ):
                result["status"] = "fail"
                result["reason_codes"] = sorted(
                    set(result["reason_codes"]) | {"assertions_missing"}
                )
        except EvidenceError:
            result = {
                "status": "fail",
                "assertions": 0,
                "reason_codes": ["worker_evidence_missing_or_invalid"],
            }
    if route_required:
        restored = False
        route_lifecycle["finalizer_attempted"] = True
        if route is not None and process is not None:
            restored = route.finish(process, passed=result["status"] == "pass")
        elif route is not None:
            route.abandon_before_worker()
        if not restored:
            try:
                latch_route_recovery(env, envelope=None if route is None else route.envelope_ref)
            except (EvidenceError, OSError):
                # The in-process latch is set before durable publication; failed
                # publication cannot permit another case in this invocation.
                pass
            result["status"] = "fail"
            result["reason_codes"] = sorted(set(result["reason_codes"]) | {ROUTE_RECOVERY_REQUIRED})
    try:
        check_case(destination, case.case_id, root, result, frozen_sources)
    except EvidenceError:
        result["status"] = "fail"
        result["reason_codes"] = sorted(set(result["reason_codes"]) | {
            "diagnostic_evidence_missing_or_invalid",
        })
    if invocation is not None:
        try:
            if result["status"] == "pass":
                invocation.complete(exit_code=process.returncode, result=result,
                    elapsed_ms=round((time.monotonic() - started) * 1000, 3))
            else:
                invocation.failed(reason_code="case_timeout" if "case_timeout" in result["reason_codes"] else "invalid_worker_result")
        except (EvidenceError, OSError):
            result["status"] = "fail"
            result["reason_codes"] = sorted(set(result["reason_codes"]) | {"classifier_provenance_missing_or_invalid"})
    if case.case_id in MEASUREMENT_FILES:
        try:
            collect_measurements(
                destination, MEASUREMENT_FILES,
                [case.case_id] if result["status"] == "pass" else (),
                root=root, sources=frozen_sources,
                  provenance=None if classifier_context is None else classifier_context.consume_verifiers,
            classifier_deadline=None if classifier_context is None else classifier_context.deadline,
            )
        except EvidenceError:
            result["status"] = "fail"
            result["reason_codes"] = sorted(set(result["reason_codes"]) | {
                "measurement_evidence_missing_or_invalid",
            })
    return {
        "case_id": case.case_id,
        "requirement_id": case.requirement_id,
        "status": result["status"],
        "duration_ms": round((time.monotonic() - started) * 1000, 3),
        "assertions": result["assertions"],
        "reason_codes": result["reason_codes"],
    }


def validate_results(cases, results):
    expected = {case.case_id for case in cases}
    if not isinstance(results, list) or any(
        not isinstance(row, dict) or not isinstance(row.get("case_id"), str)
        for row in results
    ):
        raise EvidenceError("case_inventory_mismatch")
    executed = [result["case_id"] for result in results]
    if not expected or len(executed) != len(set(executed)) or expected != set(executed):
        raise EvidenceError("case_inventory_mismatch")
    for result in results:
        if (
            result.get("status") != "pass"
            or type(result.get("assertions")) is not int
            or result["assertions"] <= 0
            or result.get("reason_codes")
        ):
            raise EvidenceError("required_case_failed")


def run_gate(phase: int, *, root=ROOT, classifier_context=None):
    run_id = str(uuid4())
    destination = root / ".evolution/reports" / run_id
    try:
        destination.mkdir(parents=True, mode=0o700)
    except OSError:
        print(
            json.dumps(
                {
                    "status": "fail",
                    "phase": phase,
                    "run_id": run_id,
                    "reason_codes": ["evidence_directory_unavailable"],
                },
                sort_keys=True,
            )
        )
        return 1
    try:
        return _run_gate(phase, root, run_id, destination, classifier_context=classifier_context)
    except Exception as error:  # noqa: BLE001 - command boundary strips private diagnostics
        # Diagnostics cannot pretend an incomplete attempt is a signed run.
        # Never serialize arbitrary exception strings, paths, SQL or credentials.
        reason = safe_failure(error, "gate_operation_failed")
        diagnostic = {
            "run_id": run_id,
            "phase": phase,
            "status": "fail",
            "reason_codes": [reason],
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        write_once(destination / "diagnostic.json", diagnostic)
        print(
            json.dumps(
                {
                    "status": "fail",
                    "phase": phase,
                    "run_id": run_id,
                    "diagnostic": str(destination / "diagnostic.json"),
                    "reason_codes": [reason],
                },
                sort_keys=True,
            )
        )
        return 1


def execute_cases(cases, root, destination, *, sources, classifier_context=None):
    """Retain every required row after a recovery latch; never dispatch later cases."""
    results = []
    route_blocked = False
    for case in cases:
        if route_blocked:
            result = {"case_id": case.case_id, "requirement_id": case.requirement_id,
                "status": "fail", "duration_ms": 0.0, "assertions": 0,
                "reason_codes": ["not_executed_due_to_gateway_route_recovery"]}
        else:
            try:
                result = execute_case(case, root, destination, sources=sources, classifier_context=classifier_context)
            except Exception:
                if not route_case_applies(case.nodeid):
                    raise
                try:
                    latch_route_recovery()
                except (EvidenceError, OSError):
                    pass
                result = {"case_id": case.case_id, "requirement_id": case.requirement_id,
                    "status": "fail", "duration_ms": 0.0, "assertions": 0,
                    "reason_codes": [ROUTE_RECOVERY_REQUIRED]}
            route_blocked = ROUTE_RECOVERY_REQUIRED in result["reason_codes"]
        results.append(result)
        print(
            f"{case.case_id} {result['status']} ({result['assertions']} assertions)",
            flush=True,
        )
    return results


def _run_gate(phase, root, run_id, destination, *, classifier_context=None):
    cases = load_registry(root / "mvp/evolution/cases.json", phase)
    key, key_id = signing_key(root)
    started = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    source = source_files(root)
    locks = lock_files(source)
    artifacts = []
    measurements = {}
    diagnostics = {}
    errors = set()
    results = []
    try:
        if route_recovery_latched():
            raise EvidenceError(ROUTE_RECOVERY_REQUIRED)
        verify_environment(root / "evolution-requirements.lock")
        artifacts = phase_artifacts(root, phase)
        verify_installed_wheels(root)
        from .postgres import prepare as prepare_postgres

        os.environ.update(prepare_postgres(root))
    except Exception as exc:  # noqa: BLE001 - every preflight failure prevents case execution
        errors.add(safe_failure(exc, "preflight_operation_failed"))
    if errors:
        results = [
            {
                "case_id": case.case_id,
                "requirement_id": case.requirement_id,
                "status": "fail",
                "duration_ms": 0.0,
                "assertions": 0,
                "reason_codes": ["preflight_failed"],
            }
            for case in cases
        ]
    else:
        results = execute_cases(cases, root, destination, sources=source,
            classifier_context=classifier_context)
    try:
        measurements = collect_measurements(
            destination, [case.case_id for case in cases],
            [result["case_id"] for result in results if result["status"] == "pass"],
            root=root, sources=source,
            provenance=None if classifier_context is None else classifier_context.consume_verifiers,
            classifier_deadline=None if classifier_context is None else classifier_context.deadline,
        )
        diagnostics = collect_diagnostics(destination, root, results, source)
        validate_results(cases, results)
        assert_same_source(source, root)
        if artifacts != phase_artifacts(root, phase):
            raise EvidenceError("artifacts_changed_during_execution")
        verify_installed_wheels(root)
    except Exception as exc:  # noqa: BLE001 - failed final verification cannot publish a pass
        errors.add(safe_failure(exc, "postflight_operation_failed"))
    manifest = {
        "source_files": source,
        "lock_files": locks,
        "registry_cases": [
            {
                "case_id": case.case_id,
                "nodeid": case.nodeid,
                "profile": case.profile,
                "mode": case.mode,
                "assertions": case.assertions,
                "timeout_s": case.timeout_s,
            }
            for case in cases
        ],
        "python_executable_sha256": digest(Path(sys.executable).resolve()),
        "errors": sorted(errors),
        "measurements": measurements,
        "case_diagnostics": diagnostics,
    }
    write_once(destination / "manifest.json", manifest)
    report = {
        "run_id": run_id,
        "created_at": started,
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        "source_digest": hashlib.sha256(canonical(source)).hexdigest(),
        "lock_digest": hashlib.sha256(canonical(locks)).hexdigest(),
        "profile": "local",
        "phase": phase,
        "started_at": started,
        "finished_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "cases": results,
        "artifact_digests": artifacts,
        "status": "fail" if errors else "pass",
        "environment": {
            "python": platform.python_version(),
            "platform": platform.system(),
            "architecture": platform.machine(),
            "manifest_sha256": digest(destination / "manifest.json"),
        },
    }
    report = sign_report(report, key, key_id)
    from guardrails.evolution.models import EvidenceReport

    EvidenceReport.model_validate_json(canonical(report))
    matrix = (
        "| Case | Requirement | Status | Assertions |\n|---|---|---|---:|\n"
        + "".join(
            f"| {row['case_id']} | {row['requirement_id']} | {row['status']} | {row['assertions']} |\n"
            for row in results
        )
    )
    with (destination / "matrix.md").open("x") as output:
        output.write(matrix)
    # Publish the signed aggregate last, after all required artifacts succeeded.
    write_once(destination / "report.json", report)
    print(
        json.dumps(
            {
                "status": report["status"],
                "phase": phase,
                "run_id": run_id,
                "report": str(destination / "report.json"),
                "reason_codes": sorted(errors),
            },
            sort_keys=True,
        )
    )
    return 0 if report["status"] == "pass" else 1


def classifier_run_context(args):
    """Explicit pinned parent authority, under its original whole-run deadline."""
    fields = (args.classifier_run_capsule, args.classifier_run_capsule_sha256,
              args.classifier_run_deadline)
    if all(value is None for value in fields):
        return nullcontext(None)
    if any(value is None for value in fields):
        raise EvidenceError("classifier_run_arguments_required")
    capsule, hashed, deadline = fields
    if (not capsule.is_absolute() or capsule.resolve() != capsule
            or not re.fullmatch(r"[0-9a-f]{64}", hashed)
            or not math.isfinite(deadline) or deadline <= time.monotonic()):
        raise EvidenceError("classifier_run_authority_invalid")
    try:
        from .classifier_final_worker_qualification import verify
        from .classifier_parent_context_v3 import ParentClassifierContext
    except ImportError:
        raise EvidenceError("classifier_run_adapter_unavailable") from None

    return ParentClassifierContext({"path": str(capsule), "sha256": hashed}, ROOT,
                                   deadline, qualify_final_worker=verify)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    gate = commands.add_parser("gate")
    gate.add_argument("phase", type=int)
    commands.add_parser("test")
    acceptance = commands.add_parser("acceptance")
    for command in (gate, acceptance):
        command.add_argument("--classifier-run-capsule", type=Path)
        command.add_argument("--classifier-run-capsule-sha256")
        command.add_argument("--classifier-run-deadline", type=float)
    deployment_commands = {"prepare", "verify", "up", "status", "down", "reset", "probes", "platform", "classifier-service", "access", "renew-credentials"}
    for name in sorted(deployment_commands):
        command = commands.add_parser(name)
        command.add_argument("--state", type=Path)
        if name == "platform":
            command.add_argument("--profile", choices=("deterministic_mock", "local_model", "classifier"), default="deterministic_mock")
            command.add_argument("--evaluator-config-file", type=Path)
        if name == "prepare":
            command.add_argument("--tools-only", action="store_true")
            command.add_argument("--classifier-context", type=Path)
            command.add_argument("--classifier-seal-sha256")
        if name == "reset":
            command.add_argument("--confirm-reset", required=True)
            command.add_argument("--backup-directory", type=Path, required=True)
        if name == "renew-credentials":
            command.add_argument("--renewal-id", required=True)
            command.add_argument("--expires-at", required=True)
            command.add_argument("--recover-lost", action="store_true")
    benchmark = commands.add_parser("benchmark")
    benchmark.add_argument("--python", dest="classifier_python", type=Path, required=True)
    benchmark.add_argument("--model-directory", type=Path, required=True)
    benchmark.add_argument("--output", type=Path, required=True)
    benchmark.add_argument("--source-digest", required=True)
    managed = commands.add_parser("bedrock-live")
    managed.add_argument("--allow-external", action="store_true")
    managed.add_argument("--config", type=Path, required=True)
    managed.add_argument("--credentials", type=Path, required=True)
    managed.add_argument("--ca-file", type=Path, required=True)
    for name in (
        "ui-test",
        "backup",
        "restore-drill",
        "bundle",
    ):
        commands.add_parser(name)
    args = parser.parse_args(argv)
    try:
        if args.command in deployment_commands:
            executable = ROOT / "deploy/evolution/bootstrap.py"
            if executable.is_symlink() or not executable.is_file():
                raise EvidenceError("deployment_preparation_missing")
            forwarded = [sys.executable, str(executable), args.command]
            if args.state is not None:
                forwarded.extend(["--state", str(args.state)])
            if args.command == "platform":
                forwarded.extend(["--profile", args.profile])
                if args.evaluator_config_file is not None:
                    forwarded.extend(["--evaluator-config-file", str(args.evaluator_config_file)])
            if args.command == "prepare" and args.tools_only:
                forwarded.append("--tools-only")
            if args.command == "prepare":
                if (args.classifier_context is None) != (args.classifier_seal_sha256 is None):
                    raise EvidenceError("classifier_context_arguments_required")
                if args.classifier_context is not None:
                    forwarded.extend(["--classifier-context", str(args.classifier_context),
                                      "--classifier-seal-sha256", args.classifier_seal_sha256])
            if args.command == "reset":
                forwarded.extend(["--confirm-reset", args.confirm_reset, "--backup-directory", str(args.backup_directory)])
            if args.command == "renew-credentials":
                forwarded.extend(["--renewal-id", args.renewal_id, "--expires-at", args.expires_at])
                if args.recover_lost:
                    forwarded.append("--recover-lost")
            return subprocess.run(forwarded, cwd=ROOT, check=False).returncode
        if args.command in {"benchmark", "bedrock-live"}:
            from .profiles import run_profile
            return run_profile(args, ROOT)
        if args.command == "gate":
            with classifier_run_context(args) as context:
                if context is None:
                    return run_gate(args.phase)
                return run_gate(args.phase, classifier_context=context)
        if args.command == "test":
            from .postgres import prepare as prepare_postgres

            test_environment = {**os.environ, **prepare_postgres(ROOT)}
            return subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "mvp/tests/evolution/unit",
                    "mvp/tests/evolution/contract",
                ],
                cwd=ROOT,
                env=test_environment,
                check=False,
            ).returncode
        if args.command == "acceptance":
            # Prevalidate every registry before spending a partial-release run.
            for phase in range(1, 7):
                load_registry(ROOT / "mvp/evolution/cases.json", phase)
            with classifier_run_context(args) as context:
                if context is None:
                    return max(run_gate(phase) for phase in range(1, 7))
                return max(run_gate(phase, classifier_context=context) for phase in range(1, 7))
        raise EvidenceError("command_not_implemented_for_current_phase")
    except EvidenceError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
