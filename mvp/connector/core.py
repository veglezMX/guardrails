"""Entry-point enforcement — the connector half of the split.

The runtime decides; the connector enforces. This module owns the enforcement
mechanics only: call input evaluation before inference, apply the decision to
the actual model call, call output evaluation after inference, apply that
decision to what the caller receives, and carry one correlation identifier
across all of it.

It contains no policy, no thresholds, and no detection logic, so a policy stays
testable independently of any gateway. Nothing in here is LiteLLM-specific —
`litellm_hooks.py` is a thin binding over this.

Known gap, stated rather than papered over: ADR-0005's scope boundary leaves
open how a client learns its timeout and failure behavior, since that
configuration resolves server-side and a timeout is precisely when the server is
unreachable. Until that is closed, this connector reads its failure behavior
from local bootstrap configuration, which must be kept consistent with the
workload's policy by whoever operates the gateway.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

# Actions this connector knows how to enforce. Receiving anything else is a
# fail-closed condition, never an implicit allow.
ENFORCEABLE_ACTIONS: frozenset[str] = frozenset(
    {"allow", "log_only", "redact", "block"}
)

CLIENT_FAILURE_BEHAVIORS: tuple[str, ...] = ("allow_and_log", "block")


@dataclass
class ConnectorConfig:
    endpoint: str
    credential: str
    tenant_id: str
    workload_id: str
    timeout_s: float = 2.0
    # What to do when the Evaluation API cannot be reached, times out, or
    # returns an action this connector cannot enforce.
    on_evaluation_unavailable: str = "block"
    safe_rejection_text: str = "This request was blocked by policy."
    safe_fallback_text: str = "The response was withheld by policy."

    def __post_init__(self) -> None:
        if self.on_evaluation_unavailable not in CLIENT_FAILURE_BEHAVIORS:
            raise ValueError(
                "on_evaluation_unavailable must be one of "
                f"{list(CLIENT_FAILURE_BEHAVIORS)}"
            )


class EvaluationTransportError(Exception):
    """The Evaluation API could not be reached or did not answer in time."""


class EvaluationClient(Protocol):
    def evaluate(self, body: dict[str, Any], timeout_s: float) -> dict[str, Any]: ...


class HttpEvaluationClient:
    """Calls `POST /v1/evaluate` over HTTP."""

    def __init__(self, endpoint: str, credential: str) -> None:
        self._endpoint = endpoint.rstrip("/") + "/v1/evaluate"
        self._credential = credential

    def evaluate(self, body: dict[str, Any], timeout_s: float) -> dict[str, Any]:
        payload = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            self._endpoint,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._credential}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            if exc.code >= 500:
                # 5xx means no evaluation happened at all; the connector's own
                # failure behavior applies.
                raise EvaluationTransportError(f"runtime unavailable: {exc}") from exc
            # 4xx is a client-side contract error and must surface, not be
            # quietly converted into an enforcement outcome.
            raise EvaluationTransportError(f"contract error: {exc}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise EvaluationTransportError(str(exc)) from exc


@dataclass
class GuardedResult:
    """What the caller receives, and why."""

    text: str
    allowed: bool
    correlation_id: str
    blocked_at: str | None = None
    warnings: tuple[str, ...] = ()
    decisions: dict[str, dict[str, Any]] = field(default_factory=dict)
    model_called: bool = False


class GuardedInvocation:
    """Runs one logical request through input evaluation, the model, and output
    evaluation, enforcing each decision as it arrives."""

    def __init__(self, config: ConnectorConfig, client: EvaluationClient) -> None:
        self._config = config
        self._client = client

    def run(
        self,
        prompt: str,
        invoke_model: Callable[[str], str],
        *,
        correlation_id: str | None = None,
    ) -> GuardedResult:
        correlation_id = correlation_id or str(uuid.uuid4())
        result = GuardedResult(text="", allowed=True, correlation_id=correlation_id)

        # --- pre-call --------------------------------------------------------
        decision = self._evaluate("input", prompt, correlation_id)
        if decision is None:
            return self._client_failure(result, "input")
        result.decisions["input"] = decision
        self._collect_warnings(result, decision)

        action = decision["action"]
        if action not in ENFORCEABLE_ACTIONS:
            return self._client_failure(result, "input")
        if action == "block":
            result.text = self._config.safe_rejection_text
            result.allowed = False
            result.blocked_at = "input"
            return result

        model_input = prompt
        if action == "redact":
            model_input = decision["transformed_content"]["text"]

        # --- model call ------------------------------------------------------
        response = invoke_model(model_input)
        result.model_called = True

        # --- post-call -------------------------------------------------------
        decision = self._evaluate("output", response, correlation_id)
        if decision is None:
            return self._client_failure(result, "output")
        result.decisions["output"] = decision
        self._collect_warnings(result, decision)

        action = decision["action"]
        if action not in ENFORCEABLE_ACTIONS:
            return self._client_failure(result, "output")
        if action == "block":
            result.text = self._config.safe_fallback_text
            result.allowed = False
            result.blocked_at = "output"
            return result
        if action == "redact":
            result.text = decision["transformed_content"]["text"]
            return result

        result.text = response
        return result

    def _evaluate(
        self, stage: str, text: str, correlation_id: str
    ) -> dict[str, Any] | None:
        body = {
            "tenant_id": self._config.tenant_id,
            "workload_id": self._config.workload_id,
            "stage": stage,
            "content": {"kind": "text", "text": text},
            "correlation_id": correlation_id,
        }
        try:
            return self._client.evaluate(body, self._config.timeout_s)
        except EvaluationTransportError:
            return None

    def _client_failure(self, result: GuardedResult, stage: str) -> GuardedResult:
        """Apply the workload's configured client-side failure behavior.

        An unreachable runtime and an unenforceable action land here alike. The
        one thing this must never do is treat either as an allow by omission.
        """
        if self._config.on_evaluation_unavailable == "allow_and_log":
            result.warnings = result.warnings + (
                f"evaluation unavailable at stage '{stage}'; allowed by configuration",
            )
            result.allowed = True
            return result
        result.text = (
            self._config.safe_rejection_text
            if stage == "input"
            else self._config.safe_fallback_text
        )
        result.allowed = False
        result.blocked_at = stage
        return result

    @staticmethod
    def _collect_warnings(result: GuardedResult, decision: dict[str, Any]) -> None:
        """Under `warn` the client proceeds *and* surfaces the reason codes.

        That is the whole difference between `warn` and `observe`; both clamp to
        `log_only`, so without this the two states would be indistinguishable to
        anyone operating the gateway.
        """
        if decision.get("enforcement_state") == "warn" and decision.get("reason_codes"):
            result.warnings = result.warnings + tuple(decision["reason_codes"])
