"""Synthetic evidence and actual private IO; no model, sockets or children."""
import copy
import json
import os
import socket
import subprocess
from pathlib import Path

import pytest
from evolution import classifier_loaded_evidence as e
from evolution import classifier_loaded_measurements as m
from evolution import measurements
from evolution.registry import EvidenceError
from guardrails.evolution.classifier_contract import identity


@pytest.fixture(autouse=True)
def no_operations(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("operational dispatch forbidden")
    monkeypatch.setattr(socket, "socket", denied)
    monkeypatch.setattr(subprocess, "Popen", denied)


def isolated_network(*, tunnel=False, ipv6_errno=101):
    links = [{"name": "lo", "index": 1, "hardware_type": 772, "flags": 8,
              "kind": None, "operstate": 2, "carrier": 1, "link_index": 0,
              "master_index": 0, "foreign_namespace_link": False, "tunnel": None,
              "address_count": 0, "route_count": 0}]
    if tunnel:
        links.append({"name": "tunl0", "index": 2, "hardware_type": 768, "flags": 128,
            "kind": "ipip", "operstate": 2, "carrier": 1, "link_index": 0,
            "master_index": 0, "foreign_namespace_link": False,
            "address_count": 0, "route_count": 0,
            "tunnel": {"link_index": 0, "local_zero": True, "remote_zero": True,
                "ttl": 0, "tos": 0, "protocol": 0, "pmtudisc": 0,
                "encapsulation_type": 0, "encapsulation_flags": 0,
                "source_port_zero": True, "destination_port_zero": True,
                "fwmark": 0, "collect_metadata": False}})
    return {"schema_version": e.network_isolation.SCHEMA,
            "namespace": "net:[2]", "host_namespace": "net:[1]",
            "interfaces": [row["name"] for row in links], "links": links,
            "external_routes": 0, "ipv4_connect_errno": 101,
            "ipv6_connect_errno": ipv6_errno}


def documents():
    sources = dict.fromkeys(e.SOURCE_FILES, "a" * 64)
    network = isolated_network()
    started = {"schema_version": e.SCHEMA, "status": "running", "sources": sources,
               "identity": identity(), "python": "3.13.16", "network": network,
               "scope": "loaded_worker_and_asgi_no_external_network", "warmup_requests": 0,
               "retry_requests": 0}
    tail = {"fixture_sha256": e.fixture_digest(), "prefix_tokens": 800, "total_tokens": 900,
            "shared_prefix_tokens": 800, "special_tokens": 2, "prefix_windows": 3,
            "expected_windows": 3, "inspected_windows": 3, "prefix_inspected_windows": 3,
            "http_status": 200, "complete": True, "prefix_below_threshold": True,
            "tail_detected": True, "threshold": 0.90}
    rows = [{"case": name, "status": "passed", "elapsed_ms": 10, "operation_ms": 5,
             "observed": copy.deepcopy(tail if name == "long_tail" else e.OBSERVATIONS[name])} for name in e.CASES]
    result = {**copy.deepcopy(started), "status": "passed", "request_count": e.REQUEST_COUNT, "closed": True,
              "startup_ms": 100, "cases": len(e.CASES), "network_after": copy.deepcopy(network),
              "cases_sha256": ""}
    return started, rows, result


def package(started, rows, result):
    raw_rows = b"".join(e.canonical(row) + b"\n" for row in rows)
    result = {**result, "cases_sha256": e.digest(raw_rows)}
    raw = {"started.json": e.canonical(started) + b"\n", "cases.jsonl": raw_rows,
           "result.json": e.canonical(result) + b"\n"}
    return raw, {name: e.digest(data) for name, data in raw.items()}


def check(docs):
    raw, pins = package(*docs)
    return e.verify(raw, pins=pins, expected_sources=docs[0]["sources"],
                    expected_identity=identity(), host_namespace="net:[1]")


def test_complete_original_attempt_lifecycle_validates():
    value = check(documents())
    assert value["status"] == "passed"
    assert [row["case"] for row in value["cases"]] == list(e.CASES)
    assert value["scope"] == "loaded_worker_and_asgi_no_external_network"


@pytest.mark.parametrize("fault", ["same-namespace", "interface", "route", "timeout", "connected", "bool-errno"])
def test_network_claim_requires_kernel_isolation_and_unreachable_probes(fault):
    docs = documents()
    row = docs[0]["network"]
    if fault == "same-namespace": row["namespace"] = "net:[1]"
    elif fault == "interface": row["interfaces"].append("eth0")
    elif fault == "route": row["external_routes"] = 1
    elif fault == "timeout": row["ipv4_connect_errno"] = 110
    elif fault == "connected": row["ipv6_connect_errno"] = 0
    else: row["ipv4_connect_errno"] = True
    docs[2]["network"] = copy.deepcopy(row)
    docs[2]["network_after"] = copy.deepcopy(row)
    with pytest.raises(e.LoadedEvidenceError): check(docs)


@pytest.mark.parametrize("fault", ["missing", "duplicate", "order", "failure", "budget", "bool-status",
    "no-reap", "ready-during-replacement", "raw-body", "bad-score-metadata", "nan", "negative"])
def test_required_scenario_contract_cannot_be_weakened(fault):
    docs = documents()
    rows = docs[1]
    if fault == "missing": rows.pop()
    elif fault == "duplicate": rows[-1] = copy.deepcopy(rows[-2])
    elif fault == "order": rows.reverse()
    elif fault == "failure": rows[3]["status"] = "failed"
    elif fault == "budget": rows[3]["operation_ms"] = 3000.001; rows[3]["elapsed_ms"] = 4000
    elif fault == "bool-status": rows[1]["observed"]["http_status"] = True
    elif fault == "no-reap": rows[3]["observed"]["worker_reaped"] = False
    elif fault == "ready-during-replacement": rows[4]["observed"]["not_ready_during_replacement"] = False
    elif fault == "raw-body": rows[2]["observed"]["body"] = "private"
    elif fault == "bad-score-metadata": rows[1]["observed"]["segments"] = 1
    elif fault == "nan": rows[3]["elapsed_ms"] = float("nan")
    else: rows[2]["elapsed_ms"] = -1
    with pytest.raises((e.LoadedEvidenceError, ValueError)): check(docs)


@pytest.mark.parametrize("fault", ["identity", "source", "retry", "warmup", "python", "not-closed",
    "count", "bool-count", "late-startup", "network-drift", "extra-field"])
def test_completed_summary_cannot_invent_current_or_complete_evidence(fault):
    docs = documents()
    result = docs[2]
    if fault == "identity": result["identity"]["weights_sha256"] = "0" * 64
    elif fault == "source": result["sources"][e.SOURCE_FILES[0]] = "b" * 64
    elif fault == "retry": result["retry_requests"] = 1
    elif fault == "warmup": result["warmup_requests"] = 1
    elif fault == "python": result["python"] = "3.14.0"
    elif fault == "not-closed": result["closed"] = False
    elif fault == "count": result["request_count"] = 8
    elif fault == "bool-count": result["cases"] = True
    elif fault == "late-startup": result["startup_ms"] = 120001
    elif fault == "network-drift": result["network_after"]["namespace"] = "net:[3]"
    else: result["raw"] = "private"
    with pytest.raises(e.LoadedEvidenceError): check(docs)


@pytest.mark.parametrize("raw", [b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":1e999}', b'[]', b'\xff'])
def test_closed_parser_rejects_ambiguous_or_nonfinite_values(raw):
    with pytest.raises(e.LoadedEvidenceError): e.parse(raw)


@pytest.mark.parametrize("field,value", [
    ("fixture_sha256", "b" * 64), ("shared_prefix_tokens", 511), ("prefix_tokens", True),
    ("total_tokens", 65537), ("special_tokens", 0), ("expected_windows", 1),
    ("expected_windows", 17), ("inspected_windows", 1), ("prefix_inspected_windows", 1),
    ("prefix_windows", 1), ("threshold", .89), ("prefix_below_threshold", False),
    ("tail_detected", False), ("complete", False), ("http_status", True),
])
def test_tail_proof_requires_original_fixture_full_coverage_and_unchanged_threshold(field, value):
    docs = documents()
    row = next(item for item in docs[1] if item["case"] == "long_tail")
    row["observed"][field] = value
    with pytest.raises(e.LoadedEvidenceError): check(docs)


@pytest.mark.parametrize("case,field,value", [
    ("queue_overload", "queued_limit", 1), ("queue_overload", "admitted", 32),
    ("queue_overload", "http_status", 200), ("queue_overload", "writes_after_excess", 2),
    ("queue_overload", "queued_cancelled", 0), ("queue_overload", "other_queued_cancelled", 30),
    ("queue_overload", "cancelled_never_dispatched", False),
    ("queue_overload", "excess_never_dispatched", False),
    ("queue_overload", "active_worker_reaped", False),
    ("busy_death", "http_status", 504), ("busy_death", "partial_response", True),
    ("busy_death", "replacement_ready", False),
])
def test_overload_and_busy_death_are_distinct_required_original_attempts(case, field, value):
    docs = documents()
    row = next(item for item in docs[1] if item["case"] == case)
    row["observed"][field] = value
    with pytest.raises(e.LoadedEvidenceError): check(docs)


def private(path, raw):
    path.write_bytes(raw)
    path.chmod(0o600)
    return {"path": str(path), "sha256": e.digest(raw)}


@pytest.fixture
def disk(tmp_path):
    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    sources = {}
    for name in m.SOURCES:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# source fixture\n")
        sources[name] = e.digest(path.read_bytes())
    docs = documents()
    docs[0]["sources"] = {name: sources[name] for name in e.SOURCE_FILES}
    docs[2]["sources"] = copy.deepcopy(docs[0]["sources"])
    raw, pins = package(*docs)
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    for name, data in raw.items(): private(output / name, data)
    inv = tmp_path / "invocation"
    inv.mkdir(mode=0o700)
    start = {"automatic_retry": False, "command_sha256": "b" * 64, "maximum_seconds": 900,
             "model_output": str(output), "host_namespace": "net:[1]", "warmup_requests": 0}
    end = {"cost": {"tokens": "unavailable", "tool_credits": "unavailable"}, "elapsed_seconds": 50,
           "exit_code": 0, "output": str(output), "phase_gate_claimed": False}
    binding = {"schema_version": m.BINDING_SCHEMA, "directory": str(output), "files": pins,
               "sources": docs[0]["sources"], "identity": identity(), "host_namespace": "net:[1]",
               "invocation": {"started": private(inv / "started.json", e.canonical(start)),
                              "result": private(inv / "result.json", e.canonical(end)),
                              "command_sha256": "b" * 64, "launcher_sha256": "c" * 64}}
    ref = private(tmp_path / "binding.json", e.canonical(binding))
    return root, sources, output, ref, binding


def bound(disk):
    return m.verify_binding(disk[3]["path"], disk[3]["sha256"], root=disk[0], sources=disk[1])


def test_private_bound_run_revalidates_all_sources_and_reports(disk):
    result = bound(disk)
    assert result["loaded"]["status"] == "passed"
    assert result["invocation"]["exit_code"] == 0


@pytest.mark.parametrize("fault", ["public", "symlink", "hardlink", "extra", "changed", "root-symlink"])
def test_original_report_files_require_exact_private_inventory(disk, fault):
    output = disk[2]
    path = output / "cases.jsonl"
    if fault == "public": path.chmod(0o644)
    elif fault == "symlink": path.rename(output / "held"); path.symlink_to(output / "held")
    elif fault == "hardlink": os.link(path, output.parent / "held")
    elif fault == "extra": private(output / "failure.json", b"{}")
    elif fault == "changed": path.write_bytes(b"{}\n")
    else: output.rename(output.with_name("held")); output.symlink_to(output.with_name("held"))
    with pytest.raises(e.LoadedEvidenceError): bound(disk)


@pytest.mark.parametrize("fault", ["exit", "retry", "warmup", "bound", "command", "failed", "gate-claim"])
def test_even_rebound_receipt_cannot_accept_failed_parent_invocation(disk, fault):
    invocation = disk[4]["invocation"]
    key = "started" if fault in {"retry", "warmup", "bound", "command"} else "result"
    path = Path(invocation[key]["path"])
    value = json.loads(path.read_bytes())
    if fault == "exit": value["exit_code"] = 1
    elif fault == "retry": value["automatic_retry"] = True
    elif fault == "warmup": value["warmup_requests"] = 1
    elif fault == "bound": value["maximum_seconds"] = 3600
    elif fault == "command": value["command_sha256"] = "0" * 64
    elif fault == "failed": private(path.parent / "failed.json", b"{}")
    else: value["phase_gate_claimed"] = True
    invocation[key] = private(path, e.canonical(value))
    disk[3].update(private(Path(disk[3]["path"]), e.canonical(disk[4])))
    with pytest.raises(e.LoadedEvidenceError): bound(disk)


def test_collector_checks_original_report_again(disk):
    value = bound(disk)
    (disk[2] / "cases.jsonl").write_bytes(b"{}\n")
    with pytest.raises(e.LoadedEvidenceError):
        m.validate(value, case_id=m.CASE_IDS["loaded_http"], root=disk[0], sources=disk[1], complete=True)


def test_record_is_private_exclusive_and_bound_to_actual_case(disk, monkeypatch):
    case = m.CASE_IDS["http_deadline"]
    path = disk[0].parent / "measurements" / case
    path.parent.mkdir(mode=0o700); path.mkdir(mode=0o700)
    monkeypatch.setenv("EVOLUTION_TEST_EVIDENCE_DIR", str(path))
    value = bound(disk)
    m.record(value, case_id=case, root=disk[0], sources=disk[1])
    assert json.loads((path / m.FILENAME).read_bytes()) == value
    assert (path / m.FILENAME).stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError): m.record(value, case_id=case, root=disk[0], sources=disk[1])


@pytest.mark.parametrize("scenario", e.CASES)
def test_shared_collector_requires_complete_original_loaded_run(disk, monkeypatch, scenario):
    case = m.CASE_IDS[scenario]
    destination = disk[0].parent / "report"
    destination.mkdir(mode=0o700)
    directory = measurements.prepare_directory(destination, case)
    monkeypatch.setenv("EVOLUTION_TEST_EVIDENCE_DIR", str(directory))
    m.record(bound(disk), case_id=case, root=disk[0], sources=disk[1])
    attachment = directory / m.FILENAME
    found = measurements.collect_measurements(destination, [case], required=[case],
                                               root=disk[0], sources=disk[1])
    assert found == {str(attachment.relative_to(destination)): {
        "sha256": e.digest(attachment.read_bytes()), "size_bytes": attachment.stat().st_size,
        "source_files": disk[1],
    }}


@pytest.mark.parametrize("fault", ("missing-attachment", "report", "source"))
def test_shared_collector_cannot_accept_missing_or_changed_loaded_evidence(disk, monkeypatch, fault):
    case = m.CASE_IDS["http_deadline"]
    destination = disk[0].parent / "report"
    destination.mkdir(mode=0o700)
    directory = measurements.prepare_directory(destination, case)
    monkeypatch.setenv("EVOLUTION_TEST_EVIDENCE_DIR", str(directory))
    m.record(bound(disk), case_id=case, root=disk[0], sources=disk[1])
    if fault == "missing-attachment":
        (directory / m.FILENAME).unlink()
    elif fault == "report":
        (disk[2] / "cases.jsonl").write_bytes(b"{}\n")
    else:
        (disk[0] / m.SOURCES[0]).write_bytes(b"# changed\n")
    with pytest.raises(EvidenceError):
        measurements.collect_measurements(destination, [case], required=[case],
                                          root=disk[0], sources=disk[1])


@pytest.mark.parametrize("tunnel,ipv6", [(False, 101), (True, 101), (False, 99), (True, 99)])
def test_shared_namespace_snapshot_requires_complete_inert_evidence(tunnel, ipv6):
    docs = documents()
    docs[0]["network"] = isolated_network(tunnel=tunnel, ipv6_errno=ipv6)
    docs[2]["network"] = copy.deepcopy(docs[0]["network"])
    docs[2]["network_after"] = copy.deepcopy(docs[0]["network"])
    assert check(docs)["status"] == "passed"
    assert docs[0]["network"]["ipv6_connect_errno"] == ipv6


@pytest.mark.parametrize("fault", ["legacy-schema", "forged-schema", "extra-field", "missing-link",
    "foreign-link", "loopback-up", "ipv6-no-source-with-address", "ipv6-no-source-with-route",
    "missing-probe", "host-mismatch", "bool-carrier", "configured-tunnel"])
def test_shared_namespace_cannot_accept_forged_or_incomplete_saved_metadata(fault):
    value = isolated_network(tunnel=True, ipv6_errno=99)
    if fault == "legacy-schema":
        value.pop("schema_version"); value.pop("links")
    elif fault == "forged-schema": value["schema_version"] = "foreign"
    elif fault == "extra-field": value["trusted"] = True
    elif fault == "missing-link": value["links"].pop()
    elif fault == "foreign-link": value["links"][1]["foreign_namespace_link"] = True
    elif fault == "loopback-up": value["links"][0]["flags"] |= 1
    elif fault == "ipv6-no-source-with-address": value["links"][0]["address_count"] = 1
    elif fault == "ipv6-no-source-with-route": value["links"][0]["route_count"] = 1
    elif fault == "missing-probe": value.pop("ipv6_connect_errno")
    elif fault == "host-mismatch": value["host_namespace"] = "net:[3]"
    elif fault == "bool-carrier": value["links"][1]["carrier"] = True
    else: value["links"][1]["tunnel"]["local_zero"] = False
    with pytest.raises(e.LoadedEvidenceError): e.network(value, "net:[1]")


def test_loaded_source_closure_includes_shared_observer_and_rejects_legacy_result_version():
    assert "services/classifier/network_isolation.py" in e.SOURCE_FILES
    assert "services/classifier/network_isolation.py" in m.SOURCES
    docs = documents()
    docs[0]["schema_version"] = docs[2]["schema_version"] = "evolution-classifier-loaded-v2"
    with pytest.raises(e.LoadedEvidenceError): check(docs)
