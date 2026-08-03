"""RT-5 — audit producer.

Every evaluation produces a record: normal path, failure path, and deliberately
disabled bindings alike. The record is what distinguishes a workload that is
disabled on purpose from one whose connector was quietly removed.

Raw content is never written unless the policy explicitly opted in (AGR-014).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .contract import Decision
from .errors import AuditDeliveryError


def new_audit_id() -> str:
    return uuid.uuid4().hex


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class AuditProducer:
    """Appends audit events as JSON lines.

    The hosted deployment buffers to a queue and archives to object storage;
    this writes to a local file. What crosses the boundary is identical either
    way, which is the point of the shared audit-metadata contract.
    """

    def __init__(
        self,
        path: Path | None = None,
        *,
        clock: Callable[[], str] = _now,
        sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._path = path
        self._clock = clock
        self._sink = sink
        self.records: list[dict[str, Any]] = []

    def emit(
        self,
        *,
        audit_id: str,
        decision: Decision,
        tenant_id: str,
        workload_id: str,
        stage: str,
        retain_content: bool,
        content_text: str,
    ) -> dict[str, Any]:
        record: dict[str, Any] = {
            "audit_id": audit_id,
            "timestamp": self._clock(),
            "tenant_id": tenant_id,
            "workload_id": workload_id,
            "stage": stage,
            "policy_name": decision.policy_name,
            "policy_version": decision.policy_version,
            "enforcement_state": decision.enforcement_state,
            "action": decision.action,
            "allowed": decision.allowed,
            "reason_codes": list(decision.reason_codes),
            "guardrail_category": decision.guardrail_category,
            "evaluator_metadata": [m.to_wire() for m in decision.evaluator_metadata],
            "correlation_id": decision.correlation_id,
            "evaluation_latency_ms": decision.evaluation_latency_ms,
            "failure_path": decision.failure_path,
            "content_retained": retain_content,
        }
        if retain_content:
            record["content"] = content_text

        self._deliver(record)
        self.records.append(record)
        return record

    def _deliver(self, record: dict[str, Any]) -> None:
        try:
            if self._sink is not None:
                self._sink(record)
            if self._path is not None:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with self._path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, sort_keys=True) + "\n")
        except AuditDeliveryError:
            raise
        except OSError as exc:
            raise AuditDeliveryError(str(exc)) from exc
