"""Finite runtime-plus-required-audit observations; no deployment activity.

The response's evaluation_ms is produced after awaited durable finalization.
A separately read matching committed audit is mandatory. Direct classifier
timing and configured HTTP timeouts alone cannot satisfy these observations.
Parent launch/provenance, governance and collector integration remain required.
"""
from __future__ import annotations

import hashlib
import math
import re
from uuid import UUID

from guardrails.evolution import models
from guardrails.evolution.classifier_contract import canonical, identity

from evolution.classifier_dispatch import delta

SERVER_MS = 3000
CLIENT_MS = 4000
CONNECT_MS = 250
GROUP_MS = 60000
SCENARIOS = {"benign", "malicious", "slow_body"}


class BudgetObservationError(ValueError):
    """Finite failure that contains no response, bearer or request text."""


def require(value):
    if not value:
        raise BudgetObservationError("classifier_budget_observation_invalid")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def identifier(value):
    require(type(value) is str and len(value) == 36)
    try:
        require(str(UUID(value)) == value)
    except ValueError:
        raise BudgetObservationError("classifier_budget_observation_invalid") from None
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def finite(value, *, maximum, minimum=0):
    require(type(value) in (int, float) and math.isfinite(value) and minimum <= value <= maximum)
    return value


def expectation(value):
    require(type(value) is dict and set(value) == {"tenant", "workload_id", "principal_id", "stage", "scenario", "policy", "binding_revision", "snapshot_generations"}
            and value["tenant"] in {"team-a", "team-b"} and value["stage"] in {"input", "output"} and value["scenario"] in SCENARIOS
            and type(value["workload_id"]) is str and re.fullmatch(r"[a-z0-9-]{1,64}", value["workload_id"]) is not None)
    identifier(value["principal_id"])
    require(models.PolicyRef.model_validate_json(canonical(value["policy"])).tenant_id == value["tenant"])
    require(type(value["binding_revision"]) is int and value["binding_revision"] > 0)
    values = value["snapshot_generations"]
    require(type(values) is list and 1 <= len(values) <= 2 and all(type(v) is int and v > 0 for v in values)
            and sorted(set(values)) == values)
    return value


def request(value, expected):
    expected = expectation(expected)
    models.EvaluationRequest.model_validate_json(canonical(value))
    require(value["tenant_id"] == expected["tenant"] and value["workload_id"] == expected["workload_id"]
            and value["stage"] == expected["stage"] and type(value["deadline_ms"]) is int and value["deadline_ms"] == SERVER_MS)
    identifier(value["request_id"])
    identifier(value["correlation_id"])
    require(type(value["parts"]) is list and len(value["parts"]) == 1 and value["parts"][0]["kind"] == "text"
            and value["parts"][0]["role"] == ("user" if expected["stage"] == "input" else "assistant"))
    require(len(canonical(value)) <= 65536)
    return value


def readiness(before, after, *, attempts):
    for row in (before, after):
        require(type(row) is dict and set(row) == {"binding_sha256", "counter", "tls", "ready_elapsed_ms"}
                and type(row["binding_sha256"]) is str and re.fullmatch(r"[0-9a-f]{64}", row["binding_sha256"]) is not None
                and row["tls"] in {"TLSv1.2", "TLSv1.3"})
        finite(row["ready_elapsed_ms"], maximum=8000)
        require(row["ready_elapsed_ms"] < 8000)
    require(before["binding_sha256"] == after["binding_sha256"] and delta(before["counter"], after["counter"]) == attempts)


def normal(value, *, expected, sent, audit, ready_before, ready_after, client_elapsed_ms):
    """Validate transient full records and return only approved finite metadata."""
    expected = expectation(expected)
    require(expected["scenario"] in {"benign", "malicious"})
    request(sent, expected)
    require(type(value) is dict and type(value.get("binding_revision")) is int and type(value.get("snapshot_generation")) is int)
    finite(value.get("evaluation_ms"), maximum=SERVER_MS, minimum=0.001)
    decision = models.Decision.model_validate_json(canonical(value)).model_dump(mode="json")
    event = models.AuditEvent.model_validate_json(canonical(audit)).model_dump(mode="json")
    finite(client_elapsed_ms, maximum=CLIENT_MS, minimum=0.001)
    elapsed = finite(decision["evaluation_ms"], maximum=SERVER_MS, minimum=0.001)
    require(elapsed <= client_elapsed_ms and decision["audit_status"] == "persisted"
            and decision["request_id"] == sent["request_id"] and decision["tenant_id"] == expected["tenant"]
            and decision["workload_id"] == expected["workload_id"] and decision["stage"] == expected["stage"]
            and decision["policy"] == expected["policy"] and decision["binding_revision"] == expected["binding_revision"]
            and decision["snapshot_generation"] in expected["snapshot_generations"]
            and identity()["evaluator_revision"] in decision["evaluator_revisions"]
            and decision["transformed_parts"] is None and decision["action"] in {"allow", "block"})
    identifier(decision["context_id"])
    if expected["stage"] == "output":
        require(decision["context_id"] == sent["context_id"])
    malicious = expected["scenario"] == "malicious"
    require(decision["would_be_action"] == ("block" if malicious else "allow"))
    findings = decision["findings"]
    require(bool(findings) == malicious and not set(decision["reason_codes"]) & set(models.ERROR_STATUS)
            and not {row["reason_code"] for row in findings} & set(models.ERROR_STATUS))
    if malicious:
        require(any(row["evaluator_revision"] == identity()["evaluator_revision"] and row["category"] == "injection"
                    and row["reason_code"] == "injection_detected" and type(row["score"]) in (int, float) and row["score"] >= 0.90 for row in findings))
    else:
        require(decision["action"] == "allow" and not decision["reason_codes"])
    require(event["event_type"] == "evaluation" and event["event_id"] == event["decision_id"] == decision["decision_id"]
            and event["tenant_id"] == expected["tenant"] and event["workload_id"] == expected["workload_id"]
            and event["actor_id"] == expected["principal_id"] and event["correlation_id"] == sent["correlation_id"]
            and event["policy_digest"] == expected["policy"]["digest"] and event["binding_revision"] == expected["binding_revision"]
            and event["action"] == decision["action"] and event["reason_codes"] == decision["reason_codes"]
            and event["metadata"] == {"stage": expected["stage"]} and event["raw_content_retained"] is False)
    readiness(ready_before, ready_after, attempts=1)
    return {"tenant": expected["tenant"], "stage": expected["stage"], "scenario": expected["scenario"],
        "request_sha256": identifier(sent["request_id"]), "correlation_sha256": identifier(sent["correlation_id"]),
        "decision_sha256": identifier(decision["decision_id"]), "context_sha256": identifier(decision["context_id"]),
        "actor_sha256": identifier(expected["principal_id"]), "policy_sha256": digest(expected["policy"]),
        "binding_revision": decision["binding_revision"], "snapshot_generation": decision["snapshot_generation"],
        "evaluator_identity_sha256": digest(identity()), "binding_sha256": ready_before["binding_sha256"],
        "counter_before": ready_before["counter"], "counter_after": ready_after["counter"], "dispatch_delta": 1,
        "http_status": 200, "action": decision["action"], "finding_count": len(findings), "audit_status": "persisted",
        "audit_event_hash": event["event_hash"], "server_elapsed_ms": elapsed, "client_elapsed_ms": client_elapsed_ms,
        "connect_timeout_ms": CONNECT_MS}


def slow_body(value, *, expected, sent, ready_before, ready_after, client_elapsed_ms, body_delay_ms, audit_matches):
    expected = expectation(expected)
    require(expected["scenario"] == "slow_body")
    request(sent, expected)
    require(type(value) is dict and set(value) == {"error"} and type(value["error"]) is dict)
    error = models.Error.model_validate_json(canonical(value["error"])).model_dump(mode="json")
    require(error["code"] == "evaluation_timeout" and error["correlation_id"] == sent["correlation_id"])
    finite(client_elapsed_ms, maximum=CLIENT_MS, minimum=550)
    finite(body_delay_ms, maximum=CLIENT_MS, minimum=550)
    require(body_delay_ms <= client_elapsed_ms and type(audit_matches) is int and audit_matches == 0)
    readiness(ready_before, ready_after, attempts=0)
    return {"tenant": expected["tenant"], "stage": expected["stage"], "scenario": "slow_body",
        "request_sha256": identifier(sent["request_id"]), "correlation_sha256": identifier(sent["correlation_id"]),
        "binding_sha256": ready_before["binding_sha256"], "counter_before": ready_before["counter"], "counter_after": ready_after["counter"],
        "dispatch_delta": 0, "http_status": 504, "error_code": "evaluation_timeout", "audit_matches": 0,
        "client_elapsed_ms": client_elapsed_ms, "body_delay_ms": body_delay_ms, "connect_timeout_ms": CONNECT_MS}
