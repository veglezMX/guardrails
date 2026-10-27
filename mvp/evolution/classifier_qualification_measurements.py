"""Complete two-corpus Wolf qualification bound to a genuine parent invocation."""

from __future__ import annotations

import os
import stat
from datetime import UTC, datetime
from pathlib import Path

from services.classifier import wolf_qualification as evidence

from .diagnostics import SourceCatalog, read_catalog
from .registry import EvidenceError

CASE = "INT-CLASSIFIER-WOLF-ORIGINAL-AND-INDEPENDENT-HELDOUT-QUALIFICATION"
CASES = frozenset({CASE})
FILENAME = "classifier-qualification-observation.json"
SCHEMA = "evolution-classifier-qualification-observation-v1"
BINDING_SCHEMA = "evolution-classifier-qualification-binding-v1"
MAX_BYTES = 24576
SOURCES = (
    "mvp/evolution/classifier_qualification_measurements.py",
    "mvp/tests/evolution/integration/test_classifier_wolf_qualification.py",
    *evidence.SOURCE_FILES,
)
RECEIPT_ENV = "EVOLUTION_CLASSIFIER_QUALIFICATION_RECEIPT"
PIN_ENV = "EVOLUTION_CLASSIFIER_QUALIFICATION_RECEIPT_SHA256"
# Independently reviewed actual launcher. Arbitrary self-consistent invocation
# bytes are not execution proof; a genuine parent invocation is still required.
PARENT_LAUNCHER_SHA256 = (
    "38921f29b1f9622a7a2cdf8c14a4b772b6c4ee1cbd5dba7cf8993ac1c2b47c8c"
)


def require(condition):
    if not condition:
        raise EvidenceError("classifier_qualification_binding_invalid")


def validate_sources(root, sources):
    require(
        root is not None
        and type(sources) is dict
        and all(name in sources for name in SOURCES)
    )
    catalog = SourceCatalog(root, sources)
    for name in SOURCES:
        catalog.load(catalog.path(name))


def timestamp(value):
    result = datetime.fromisoformat(value)
    require(result.tzinfo == UTC)
    return result


def verify_binding(receipt_path, receipt_sha256, *, root, sources):
    validate_sources(root, sources)
    try:
        ref = {"path": str(receipt_path), "sha256": receipt_sha256}
        raw = evidence.reference(ref)
        binding = evidence.document(raw, MAX_BYTES)
        require(
            set(binding) == {"schema_version", "plan", "reports", "invocation"}
            and binding["schema_version"] == BINDING_SCHEMA
        )
        plan_raw = evidence.reference(binding["plan"])
        plan = evidence.document(plan_raw)
        corpus_raws, _ = evidence.validate_plan(plan, Path(root))
        require(
            plan["sources"] == {name: sources[name] for name in evidence.SOURCE_FILES}
        )
        reports = binding["reports"]
        require(
            type(reports) is dict
            and set(reports) == {"started.json", "result.json", "cases.jsonl"}
        )
        output = Path(plan["output"])
        require(
            all(Path(row["path"]) == output / name for name, row in reports.items())
            and not os.path.lexists(output / "failure.json")
        )
        report_raws = {name: evidence.reference(row) for name, row in reports.items()}
        invocation = binding["invocation"]
        require(
            type(invocation) is dict
            and set(invocation) == {"launcher", "started", "result", "cleanup"}
        )
        require(
            type(PARENT_LAUNCHER_SHA256) is str
            and invocation["launcher"]["sha256"] == PARENT_LAUNCHER_SHA256
        )
        evidence.reference(invocation["launcher"])
        invocation_raws = {
            name: evidence.reference(invocation[name])
            for name in ("started", "result", "cleanup")
        }
        start, result = (
            evidence.document(invocation_raws[name], MAX_BYTES)
            for name in ("started", "result")
        )
        start_path, result_path = (
            Path(invocation[name]["path"]) for name in ("started", "result")
        )
        require(
            start_path.name == "started.json"
            and result_path.name == "result.json"
            and start_path.parent == result_path.parent
            and start_path.parent != output
            and not os.path.lexists(start_path.parent / "failure.json")
        )
        require(
            set(start)
            == {
                "schema_version",
                "status",
                "started_at",
                "plan_sha256",
                "launcher_sha256",
                "output",
                "maximum_seconds",
                "automatic_retry",
                "warmup_requests",
            }
            and start["schema_version"] == "wolf-qualification-invocation-v1"
            and start["status"] == "running"
            and start["plan_sha256"] == binding["plan"]["sha256"]
            and start["launcher_sha256"] == invocation["launcher"]["sha256"]
            and start["output"] == str(output)
            and type(start["maximum_seconds"]) is int
            and start["maximum_seconds"] == 3600
            and start["automatic_retry"] is False
            and type(start["warmup_requests"]) is int
            and start["warmup_requests"] == 0
        )
        require(
            set(result)
            == {
                "schema_version",
                "status",
                "exit_code",
                "elapsed_seconds",
                "plan_sha256",
                "launcher_sha256",
                "output",
                "enabled_model_changed",
                "quality_report_pins",
                "cleanup_sha256",
            }
            and result["schema_version"] == "wolf-qualification-invocation-v1"
            and result["status"] == "completed"
            and type(result["exit_code"]) is int
            and result["exit_code"] == 0
            and evidence.numeric(result["elapsed_seconds"])
            and 0 < result["elapsed_seconds"] <= 3600
            and result["plan_sha256"] == start["plan_sha256"]
            and result["launcher_sha256"] == start["launcher_sha256"]
            and result["output"] == str(output)
            and result["enabled_model_changed"] is False
            and result["quality_report_pins"]
            == {name: row["sha256"] for name, row in reports.items()}
            and result["cleanup_sha256"] == invocation["cleanup"]["sha256"]
        )
        cleanup = evidence.document(invocation_raws["cleanup"], MAX_BYTES)
        require(
            Path(invocation["cleanup"]["path"]) == start_path.parent / "cleanup.json"
            and set(cleanup)
            == {
                "schema_version",
                "group_signal_requested",
                "group_already_absent",
                "leader_reaped",
                "leader_exit_observed",
                "errors",
            }
            and cleanup["schema_version"] == "wolf-initial-cleanup-v1"
            and all(
                type(cleanup[key]) is bool
                for key in (
                    "group_signal_requested",
                    "group_already_absent",
                    "leader_reaped",
                    "leader_exit_observed",
                )
            )
            and cleanup["leader_reaped"] is True
            and cleanup["leader_exit_observed"] is True
            and cleanup["errors"] == []
            and (cleanup["group_signal_requested"] or cleanup["group_already_absent"])
        )
        checked = evidence.validate_report(
            corpus_raws,
            report_raws["started.json"],
            report_raws["result.json"],
            report_raws["cases.jsonl"],
            plan["sources"],
        )
        report = evidence.document(report_raws["result.json"])
        require(
            report["plan_sha256"] == binding["plan"]["sha256"]
            and 0
            <= (
                timestamp(report["created_at"]) - timestamp(start["started_at"])
            ).total_seconds()
            <= result["elapsed_seconds"]
        )
        value = {
            "schema_version": SCHEMA,
            "status": "verified",
            "binding": ref,
            "qualification": checked,
            "report_sha256": result["quality_report_pins"],
            "cpu": report["cpu"],
            "cost": report["cost"],
            "invocation": {
                "launcher_sha256": result["launcher_sha256"],
                "started_sha256": invocation["started"]["sha256"],
                "result_sha256": invocation["result"]["sha256"],
                "cleanup_sha256": invocation["cleanup"]["sha256"],
                "exit_code": 0,
                "elapsed_seconds": result["elapsed_seconds"],
            },
        }
        require(
            len(evidence.canonical(value) + b"\n") <= MAX_BYTES
            and evidence.reference(ref) == raw
            and evidence.reference(binding["plan"]) == plan_raw
            and all(
                evidence.reference(row) == report_raws[name]
                for name, row in reports.items()
            )
            and all(
                evidence.reference(invocation[name]) == invocation_raws[name]
                for name in ("started", "result", "cleanup")
            )
            and not os.path.lexists(output / "failure.json")
            and not os.path.lexists(start_path.parent / "failure.json")
        )
        validate_sources(root, sources)
        return value
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        UnicodeError,
        RecursionError,
        evidence.ClassifierError,
    ):
        raise EvidenceError("classifier_qualification_binding_invalid") from None


def validate(value, *, case_id, root, sources, complete=False):
    require(
        case_id in CASES
        and type(complete) is bool
        and type(value) is dict
        and set(value)
        == {
            "schema_version",
            "status",
            "binding",
            "qualification",
            "report_sha256",
            "cpu",
            "cost",
            "invocation",
        }
    )
    expected = verify_binding(
        value["binding"]["path"], value["binding"]["sha256"], root=root, sources=sources
    )
    require(evidence.canonical(value) == evidence.canonical(expected))
    return value


def directory():
    value = os.environ.get("EVOLUTION_TEST_EVIDENCE_DIR")
    if value is None:
        return None
    path = Path(value)
    before = path.lstat()
    require(
        path.is_absolute()
        and path.resolve() == path
        and path.name == CASE
        and path.parent.name == "measurements"
        and stat.S_ISDIR(before.st_mode)
        and before.st_uid == os.getuid()
        and stat.S_IMODE(before.st_mode) == 0o700
    )
    return path, before


def gate_sources(root):
    checked = directory()
    if checked is None:
        sources = {
            name: evidence.sha(evidence.read(Path(root) / name)) for name in SOURCES
        }
    else:
        sources = read_catalog(checked[0].parents[1] / (CASE + ".sources.json"), root)
    validate_sources(root, sources)
    return sources


def record(value, *, root, sources):
    validate(value, case_id=CASE, root=root, sources=sources, complete=True)
    checked = directory()
    if checked is None:
        return value
    path, before = checked
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        require(
            evidence._stamp(os.fstat(fd)) == evidence._stamp(before)
            and path.resolve() == path
            and evidence._stamp(path.lstat()) == evidence._stamp(before)
        )
        leaf = os.open(
            FILENAME,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=fd,
        )
        with os.fdopen(leaf, "wb") as stream:
            stream.write(evidence.canonical(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(fd)
    finally:
        os.close(fd)
    return value
