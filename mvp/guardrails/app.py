"""The Guardrails Runtime service — RT-1 through RT-7 wired together.

Surface (ADR-0005 section 4):

    POST /v1/evaluate      evaluate content at a stage, returns a Decision
    GET  /v1/capabilities  the evaluator capabilities this deployment serves

A completed evaluation is always `200`, whatever its action. A `block` is a
successful evaluation with a restrictive outcome, and encoding it as an error
would make every client's error path also its enforcement path. `5xx` means no
evaluation happened at all, and the client's own failure behavior applies.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .audit import AuditProducer, new_audit_id
from .binding import WorkloadIdentity, load_identities_file
from .contract import CONTRACT_VERSION, Decision, platform_code
from .engine import DecisionEngine
from .errors import AuditDeliveryError, ProblemError, malformed
from .evaluators import EvaluatorRegistry, default_registry
from .intake import (
    DEFAULT_MAX_CONTENT_BYTES,
    authenticate,
    correlation_id_of,
    new_correlation_id,
    parse_request,
)
from .store import PolicyCache, PolicyUnresolved

# `policy_unresolved` is the one failure mode whose behavior cannot come from
# the policy, because the failure *is* that there is no policy. AGR-012 assigns
# it to the policy anyway, which is circular. The behavior is therefore declared
# at deployment level and required at startup, so it is still stated rather than
# defaulted — but the circularity is a documentation gap, not a design choice.
UNRESOLVED_BEHAVIORS: tuple[str, ...] = ("block", "require_manual_approval")


@dataclass
class RuntimeConfig:
    unresolved_policy_behavior: str
    max_content_bytes: int = DEFAULT_MAX_CONTENT_BYTES
    audit_path: Path | None = None
    identities: dict[str, WorkloadIdentity] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.unresolved_policy_behavior not in UNRESOLVED_BEHAVIORS:
            raise ValueError(
                "unresolved_policy_behavior must be one of "
                f"{list(UNRESOLVED_BEHAVIORS)}; it has no default"
            )


class GuardrailsRuntime:
    """Transport-independent runtime. The HTTP handler is a thin shell over it."""

    def __init__(
        self,
        cache: PolicyCache,
        config: RuntimeConfig,
        *,
        registry: EvaluatorRegistry | None = None,
        audit: AuditProducer | None = None,
    ) -> None:
        self.cache = cache
        self.config = config
        self.registry = registry or default_registry()
        self.audit = audit or AuditProducer(config.audit_path)
        self.engine = DecisionEngine(self.registry, self.audit)

    # --- POST /v1/evaluate ---------------------------------------------------

    def evaluate(
        self, auth_header: str | None, raw_body: bytes
    ) -> tuple[int, dict[str, Any]]:
        try:
            body = json.loads(raw_body or b"")
        except json.JSONDecodeError as exc:
            problem = malformed(f"Body is not valid JSON: {exc}")
            return problem.status, problem.to_wire(None)

        try:
            identity = authenticate(auth_header, self.config.identities)
            request = parse_request(
                body, identity, max_content_bytes=self.config.max_content_bytes
            )
        except ProblemError as problem:
            return problem.status, problem.to_wire(correlation_id_of(body))

        try:
            policy, binding = self.cache.resolve(request.tenant_id, request.workload_id)
        except PolicyUnresolved:
            return 200, self._unresolved_decision(request).to_wire()

        decision = self.engine.evaluate(request, policy, binding)
        return 200, decision.to_wire()

    def _unresolved_decision(self, request: Any) -> Decision:
        """A substitute decision for a workload with no usable cached policy.

        Still a `200` with a well-formed Decision, and still audited — a
        rejection the runtime declined to evaluate is exactly the case an audit
        stream must be able to distinguish from a clean allow.
        """
        behavior = self.config.unresolved_policy_behavior
        code = platform_code(
            "policy_unresolved" if behavior == "block" else "manual_approval_required"
        )
        decision = Decision(
            action="block",
            allowed=False,
            # There is no binding, so there is no ladder state to report. The
            # most restrictive state is stated rather than a state invented for
            # a workload the runtime has never heard of.
            enforcement_state="block",
            reason_codes=(code,),
            guardrail_category=None,
            policy_name="unresolved",
            policy_version=0,
            evaluator_metadata=(),
            correlation_id=request.correlation_id,
            audit_id=new_audit_id(),
            evaluation_latency_ms=0.0,
            failure_path=f"policy_unresolved:{behavior}",
        )
        try:
            self.audit.emit(
                audit_id=decision.audit_id,
                decision=decision,
                tenant_id=request.tenant_id,
                workload_id=request.workload_id,
                stage=request.stage,
                retain_content=False,
                content_text=request.content.text,
            )
        except AuditDeliveryError:
            # Nothing further to fall back to: the decision is already the most
            # restrictive one this path can produce.
            pass
        return decision

    # --- GET /v1/capabilities ------------------------------------------------

    def capabilities(self) -> tuple[int, dict[str, Any]]:
        return 200, {
            "contract_version": CONTRACT_VERSION,
            "capabilities": self.registry.capabilities(),
        }


class _Handler(BaseHTTPRequestHandler):
    server_version = "guardrails-runtime/0.1"
    runtime: GuardrailsRuntime  # bound in make_server

    def do_POST(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's contract
        if self.path != "/v1/evaluate":
            self._not_found()
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        status, payload = self.runtime.evaluate(
            self.headers.get("Authorization"), raw
        )
        self._respond(status, payload)

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/v1/capabilities":
            self._not_found()
            return
        status, payload = self.runtime.capabilities()
        self._respond(status, payload)

    def _not_found(self) -> None:
        self._respond(
            404,
            {
                "type": "https://guardrails.example.com/problems/not-found",
                "title": "No such resource",
                "status": 404,
                "detail": f"Unknown path '{self.path}'.",
                "reason_code": platform_code("not_found"),
                "correlation_id": new_correlation_id(),
            },
            problem=True,
        )

    def _respond(
        self, status: int, payload: dict[str, Any], *, problem: bool | None = None
    ) -> None:
        is_problem = problem if problem is not None else status >= 400
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header(
            "Content-Type",
            "application/problem+json" if is_problem else "application/json",
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        # Access logs would carry evaluated content in query strings if any
        # endpoint ever took one. Silence by default; operational metrics are a
        # separate stream from evaluation decisions.
        pass


def make_server(runtime: GuardrailsRuntime, host: str, port: int) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (_Handler,), {"runtime": runtime})
    return ThreadingHTTPServer((host, port), handler)


def load_runtime(root: Path, *, audit_path: Path | None = None) -> GuardrailsRuntime:
    """Assemble a runtime from a deployment directory.

    The directory holds what the control plane distributed: policy documents,
    bindings, workload identities, and the deployment's own configuration.
    """
    config_doc = json.loads((root / "runtime.json").read_text())
    identities = load_identities_file(root / "identities.json")
    cache = PolicyCache.from_dirs(root / "policies", root / "bindings")
    config = RuntimeConfig(
        unresolved_policy_behavior=config_doc["unresolved_policy_behavior"],
        max_content_bytes=config_doc.get("max_content_bytes", DEFAULT_MAX_CONTENT_BYTES),
        audit_path=audit_path or Path(config_doc.get("audit_path", "audit.jsonl")),
        identities=identities,
    )
    return GuardrailsRuntime(cache, config)
