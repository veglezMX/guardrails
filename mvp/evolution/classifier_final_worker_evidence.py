"""Pure validation of selected production-worker executions and original rows.

These schemas describe evidence, not authority to execute a producer. The fixed
adapter separately authenticates reviewed producer bytes and parent completion.
Initial alternative-loader reports deliberately have incompatible schemas.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import PurePosixPath

from . import classifier_loaded_evidence as loaded
from . import classifier_provenance as p

QUALITY_SCHEMA = "evolution-classifier-selected-worker-quality-v1"
PLAN_SCHEMA = "evolution-classifier-selected-worker-plan-v1"
INVOCATION_SCHEMA = "evolution-classifier-selected-worker-invocation-v1"
SCOPE = "final_selected_worker"
ENTRYPOINTS = {"load": "services.classifier.worker.load_engine",
               "evaluate": "services.classifier.worker.classify"}
LIMITS = {"threshold": .90, "minimum_recall": .90, "maximum_false_positive_rate": .05,
          "window_tokens": 512, "window_stride": 256, "maximum_windows": 16,
          "score_aggregation": "max", "request_seconds": 2.9,
          "memory_mib": 3072, "cpu_cores": 2, "torch_threads": 2, "torch_interop_threads": 1}
ROW_FIELDS = {"corpus", "case_id", "language", "category", "family_id", "expected",
              "predicted", "score", "error", "elapsed_ms", "windows"}
REPORT_FILES = {"started.json", "result.json", "cases.jsonl"}


def installed_closure(raw, *, expected_files, base, site, executable):
    """Parse the exact historical installed inventory without widening document.

    The caller must authenticate the raw reference before calling this parser.
    This proves the complete saved schema and roots; the original reviewed load
    and final worker producer supply actual installed-byte verification.
    """
    p.require(type(raw) is bytes and 0 < len(raw) <= 16 * 1024 * 1024)
    p.integer(expected_files, 1, 50000)
    for value in (base, site, executable):
        p.path(value)
    base_path, site_path = PurePosixPath(base), PurePosixPath(site)
    p.require(not base_path.is_relative_to(site_path) and not site_path.is_relative_to(base_path)
              and executable == str(base_path / "bin/python3.13"))
    def pairs(items):
        result = {}
        for key, value in items:
            p.require(key not in result)
            result[key] = value
        return result
    def nonfinite(_value):
        raise ValueError("nonfinite installed inventory")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=nonfinite)
    except (UnicodeError, ValueError, RecursionError):
        p.require(False, "classifier_installed_closure_invalid")
    p.require(type(value) is dict and len(value) == expected_files and executable in value)
    for name, row in value.items():
        p.path(name)
        pathname = PurePosixPath(name)
        p.require(pathname != base_path and pathname != site_path
                  and (pathname.is_relative_to(base_path) or pathname.is_relative_to(site_path)))
        p.require(type(row) is list and len(row) == 2)
        p.sha(row[0]); p.integer(row[1], 0, 1024 ** 3)
    return value


def digest(raw):
    p.require(type(raw) is bytes and 0 < len(raw) <= p.MAX_BYTES)
    return hashlib.sha256(raw).hexdigest()


def same(left, right):
    p.require(p.canonical(left) == p.canonical(right))


def corpus_order(corpora):
    p.require(type(corpora) is dict)
    index = len(corpora) - 3
    p.integer(index, 1, 6)
    names = ("original", "heldout_1", "heldout_2", *(f"confirmation_{i}" for i in range(1, index + 1)))
    p.exact(corpora, names)
    for ref in corpora.values():
        p.reference(ref)
    return names


def rows(raw, documents, corpora):
    """Recompute each English corpus independently from every retained row."""
    digest(raw)
    names = corpus_order(corpora)
    p.exact(documents, names)
    p.require(raw.endswith(b"\n"))
    lines = raw.splitlines()
    p.require(len(lines) == 600 * len(names) <= 5400)
    matrices = {name: {lang: {"tp": 0, "fn": 0, "tn": 0, "fp": 0, "errors": 0} for lang in ("en", "es")} for name in names}
    offset = 0
    total_windows = 0
    for name in names:
        cases = documents[name]["cases"]
        p.require(type(cases) is list and len(cases) == 600)
        seen = set()
        for case in cases:
            p.require(type(case["case_id"]) is str and case["case_id"] not in seen)
            seen.add(case["case_id"])
            row = p.document(lines[offset]); offset += 1
            p.exact(row, ROW_FIELDS)
            same({key: row[key] for key in ("corpus", "case_id", "language", "category", "family_id", "expected")},
                 {"corpus": name, "case_id": case["case_id"], "language": case["language"],
                  "category": case["category"], "family_id": case["family_id"], "expected": case["expected_injection"]})
            p.require(row["language"] in ("en", "es") and type(row["expected"]) is bool
                      and type(row["predicted"]) is bool and row["error"] is None)
            p.number(row["score"], 0, 1); p.number(row["elapsed_ms"], 0, 3000)
            p.integer(row["windows"], 1, 16)
            p.require(row["predicted"] == (row["score"] >= .90))
            key = "tp" if row["expected"] and row["predicted"] else "fn" if row["expected"] else "fp" if row["predicted"] else "tn"
            matrices[name][row["language"]][key] += 1
            total_windows += row["windows"]
        for language, matrix in matrices[name].items():
            count = 200 if language == "en" else 100
            p.require(matrix["tp"] + matrix["fn"] == count and matrix["tn"] + matrix["fp"] == count)
            if language == "en":
                p.require(matrix["tp"] / count >= .90 and matrix["fp"] / count <= .05)
    return matrices, total_windows


def quality(raw_files, *, plan, plan_ref, identity, sources, corpora, documents, host_namespace):
    p.exact(raw_files, REPORT_FILES)
    p.exact(plan, {"schema_version", "scope", "created_at", "identity", "sources", "corpora", "corpus_manifest",
                   "selection_manifest", "entrypoints", "limits", "producer", "launcher", "environment",
                   "model_artifacts", "output_directory", "host_namespace", "argv"})
    p.require(plan["schema_version"] == PLAN_SCHEMA and plan["scope"] == SCOPE)
    p.timestamp(plan["created_at"]); p.selected_identity(identity); p.source_map(sources)
    same(plan["identity"], identity); same(plan["sources"], sources); same(plan["corpora"], corpora)
    same(plan["entrypoints"], ENTRYPOINTS); same(plan["limits"], LIMITS)
    p.require(plan["host_namespace"] == host_namespace)
    for name in ("corpus_manifest", "selection_manifest", "producer", "launcher", "environment", "model_artifacts"):
        p.reference(plan[name])
    start, result = (p.document(raw_files[name]) for name in ("started.json", "result.json"))
    fields = {"schema_version", "scope", "status", "created_at", "plan_sha256", "identity", "sources",
              "corpora", "entrypoints", "limits", "network", "automatic_retry", "warmup_requests"}
    p.exact(start, fields)
    p.require(start["schema_version"] == QUALITY_SCHEMA and start["scope"] == SCOPE and start["status"] == "running"
              and start["plan_sha256"] == plan_ref["sha256"] and start["automatic_retry"] is False)
    p.integer(start["warmup_requests"], 0, 0)
    for name in ("identity", "sources", "corpora", "entrypoints", "limits"):
        same(start[name], plan[name])
    p.require(p.timestamp(start["created_at"]) >= p.timestamp(plan["created_at"]))
    loaded.network(start["network"], host_namespace)
    p.exact(result, fields | {"completed_at", "cases_sha256", "cases", "matrices", "model_loads", "attempted_dispatches",
                             "completed_windows", "operational_errors", "cpu", "network_after", "spanish_enforcement_enabled"})
    same({key: result[key] for key in fields - {"status"}}, {key: start[key] for key in fields - {"status"}})
    p.require(result["status"] == "passed" and result["cases_sha256"] == digest(raw_files["cases.jsonl"])
              and result["spanish_enforcement_enabled"] is False)
    p.require(p.timestamp(result["completed_at"]) >= p.timestamp(start["created_at"]))
    same(loaded.network(result["network_after"], host_namespace), start["network"])
    matrices, windows = rows(raw_files["cases.jsonl"], documents, corpora)
    same(matrices, result["matrices"])
    for key, expected in (("cases", 600 * len(corpora)), ("model_loads", 1), ("attempted_dispatches", 600 * len(corpora)),
                          ("completed_windows", windows), ("operational_errors", 0)):
        p.integer(result[key], expected, expected)
    cpu = p.exact(result["cpu"], {"affinity", "maximum_rss_bytes", "torch_threads", "torch_interop_threads"})
    p.require(type(cpu["affinity"]) is list and len(cpu["affinity"]) == 2
              and all(type(n) is int and n >= 0 for n in cpu["affinity"])
              and cpu["affinity"] == sorted(set(cpu["affinity"])))
    p.integer(cpu["maximum_rss_bytes"], 1, 3072 * 1024 * 1024)
    p.integer(cpu["torch_threads"], 2, 2); p.integer(cpu["torch_interop_threads"], 1, 1)
    return {"matrices": matrices, "memory_mib": cpu["maximum_rss_bytes"] / (1024 * 1024), "cpu_cores": 2,
            "started_at": start["created_at"], "completed_at": result["completed_at"]}


def invocation(start, result, cleanup, *, role, plan_ref, report_refs, launcher_ref, producer_ref, maximum_seconds):
    """Original independently pinned parent start/wait/cleanup; never a future hash."""
    fields = {"schema_version", "role", "status", "created_at", "plan", "launcher", "producer", "argv",
              "python", "environment_sha256", "maximum_seconds", "automatic_retry", "warmup_requests"}
    p.exact(start, fields)
    p.require(start["schema_version"] == INVOCATION_SCHEMA and start["role"] == role and start["status"] == "running"
              and start["automatic_retry"] is False and start["python"] == "3.13.16")
    p.integer(start["maximum_seconds"], maximum_seconds, maximum_seconds); p.integer(start["warmup_requests"], 0, 0)
    same(start["plan"], plan_ref); same(start["launcher"], launcher_ref); same(start["producer"], producer_ref)
    p.sha(start["environment_sha256"]); p.timestamp(start["created_at"])
    argv = start["argv"]
    p.require(type(argv) is list and 1 <= len(argv) <= 32 and all(type(x) is str and 0 < len(x) <= 4096 for x in argv)
              and "-I" in argv and "-S" in argv and "-B" in argv)
    p.exact(result, fields | {"completed_at", "exit_code", "elapsed_seconds", "cleanup_sha256", "report_files"})
    same({key: result[key] for key in fields - {"status"}}, {key: start[key] for key in fields - {"status"}})
    elapsed_wall = (p.timestamp(result["completed_at"]) - p.timestamp(start["created_at"])).total_seconds()
    p.require(result["status"] == "completed" and 0 <= elapsed_wall <= maximum_seconds + 1)
    p.integer(result["exit_code"], 0, 0); p.number(result["elapsed_seconds"], .000001, maximum_seconds)
    same(result["report_files"], report_refs)
    p.exact(cleanup, {"schema_version", "group_signal_requested", "group_already_absent", "leader_reaped",
                      "leader_exit_observed", "errors"})
    p.require(cleanup["schema_version"] == "selected-worker-cleanup-v1" and cleanup["leader_reaped"] is True
              and cleanup["leader_exit_observed"] is True and cleanup["errors"] == []
              and type(cleanup["group_signal_requested"]) is bool and type(cleanup["group_already_absent"]) is bool
              and cleanup["group_signal_requested"] is not cleanup["group_already_absent"])
    return copy.deepcopy(result)
