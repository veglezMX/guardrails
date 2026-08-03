"""RT-3 — decision engine and decision normalization.

The single point every evaluation funnels through. Nothing else in the runtime
may emit a `Decision`, which is what stops an engine-specific shape from leaking
past the adapter layer.

Order of operations, and each step's authority:

1. A `disabled` binding skips evaluators entirely, returns `allow` with empty
   reason codes, and still writes an audit record (ADR-0004).
2. Only the evaluators the policy configures for this stage run.
3. Findings at or above the policy's threshold contribute; the emitted action is
   the most severe action those contributions call for.
4. The action is clamped to the binding's ceiling. Reason codes, category, and
   evaluator metadata are untouched by clamping — that is what makes an
   observe-to-enforce promotion measurable.
5. A `redact` action that cannot be satisfied is `transform_failed`, never a
   silent downgrade to `allow` or upgrade to `block`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from .audit import AuditProducer, new_audit_id
from .binding import Binding
from .contract import (
    ACTION_SEVERITY,
    Content,
    Decision,
    EvaluationRequest,
    EvaluatorMetadata,
    clamp,
)
from .errors import AuditDeliveryError, EvaluatorTimeout, EvaluatorUnavailable
from .evaluators import EvaluatorRegistry
from .evaluators.base import Finding
from .failure import CONTINUING_BEHAVIORS, FailureHandler, Substitute
from .policy import EvaluatorSelection, Policy

REDACTION_PLACEHOLDER = "[REDACTED]"


@dataclass
class _Working:
    """Mutable state for one evaluation, kept out of the public surface."""

    reason_codes: list[str]
    metadata: list[EvaluatorMetadata]
    contributions: list[tuple[EvaluatorSelection, Finding]]
    failure_path: str | None = None


class DecisionEngine:
    def __init__(
        self,
        registry: EvaluatorRegistry,
        audit: AuditProducer,
        *,
        failure_handler: FailureHandler | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._registry = registry
        self._audit = audit
        self._failure = failure_handler or FailureHandler()
        self._clock = clock

    def evaluate(
        self, request: EvaluationRequest, policy: Policy, binding: Binding
    ) -> Decision:
        started = self._clock()
        state = binding.enforcement_state
        text = request.content.text
        work = _Working(reason_codes=[], metadata=[], contributions=[])

        if state == "disabled":
            return self._finish(
                request, policy, binding, work, action="allow", started=started
            )

        for selection in policy.selections_for(request.stage):
            substitute = self._run_selection(text, selection, policy, work)
            if substitute is not None:
                return self._finish(
                    request,
                    policy,
                    binding,
                    work,
                    action=substitute.action,
                    started=started,
                    substitute=substitute,
                )

        action = "allow"
        for selection, _finding in work.contributions:
            if ACTION_SEVERITY[selection.action] > ACTION_SEVERITY[action]:
                action = selection.action

        return self._finish(
            request, policy, binding, work, action=action, started=started
        )

    # --- evaluator invocation (RT-4 boundary) --------------------------------

    def _run_selection(
        self,
        text: str,
        selection: EvaluatorSelection,
        policy: Policy,
        work: _Working,
    ) -> Substitute | None:
        try:
            evaluator = self._registry.resolve(selection.family, selection.capability)
            finding = evaluator.evaluate(text, selection)
        except EvaluatorTimeout:
            return self._handle_failure("evaluation_timeout", policy, work)
        except EvaluatorUnavailable:
            return self._handle_failure("detector_unavailable", policy, work)

        work.metadata.append(
            EvaluatorMetadata(finding.evaluator_name, finding.evaluator_version)
        )
        if finding.matched and finding.confidence >= selection.threshold:
            work.contributions.append((selection, finding))
            if finding.reason_code not in work.reason_codes:
                work.reason_codes.append(finding.reason_code)
        return None

    def _handle_failure(
        self, mode: str, policy: Policy, work: _Working
    ) -> Substitute | None:
        behavior = policy.failure_behavior[mode]
        if behavior in CONTINUING_BEHAVIORS:
            # `degrade_and_log`: carry on with the remaining detectors, but the
            # degradation is recorded on the decision, never silent.
            code = self._failure.annotation(mode)
            if code not in work.reason_codes:
                work.reason_codes.append(code)
            work.failure_path = mode
            return None
        return self._failure.substitute(mode, behavior)

    # --- normalization, clamping, audit --------------------------------------

    def _finish(
        self,
        request: EvaluationRequest,
        policy: Policy,
        binding: Binding,
        work: _Working,
        *,
        action: str,
        started: float,
        substitute: Substitute | None = None,
    ) -> Decision:
        state = binding.enforcement_state

        if substitute is not None:
            if substitute.reason_code not in work.reason_codes:
                work.reason_codes.append(substitute.reason_code)
            work.failure_path = substitute.failure_path

        final_action, transformed = self._apply_ceiling(
            request, policy, work, action, state
        )

        category = self._category(work)
        latency_ms = round((self._clock() - started) * 1000, 3)
        audit_id = new_audit_id()

        decision = Decision(
            action=final_action,
            allowed=final_action != "block",
            enforcement_state=state,
            reason_codes=tuple(work.reason_codes),
            guardrail_category=category,
            policy_name=policy.name,
            policy_version=policy.version,
            evaluator_metadata=tuple(work.metadata),
            correlation_id=request.correlation_id,
            audit_id=audit_id,
            evaluation_latency_ms=latency_ms,
            transformed_content=transformed,
            failure_path=work.failure_path,
        )

        return self._audit_or_substitute(decision, request, policy, binding)

    def _apply_ceiling(
        self,
        request: EvaluationRequest,
        policy: Policy,
        work: _Working,
        action: str,
        state: str,
    ) -> tuple[str, Content | None]:
        """Clamp to the binding's ceiling and satisfy a redaction if one is due.

        A substitute decision is clamped like any other: a workload in observe
        must not start affecting traffic because a detector went down.
        """
        state_ceiling = clamp(action, state)
        if state_ceiling != "redact":
            return state_ceiling, None

        spans = self._spans(work)
        if spans:
            return state_ceiling, Content(
                kind=request.content.kind,
                text=_redact(request.content.text, spans),
            )

        # The ceiling cannot be satisfied: the action calls for redaction and no
        # evaluator localized anything to redact. This is `transform_failed`,
        # never a silent downgrade to allow or upgrade to block. The resulting
        # action is deliberately not re-clamped — the ceiling is the thing that
        # just failed, so clamping back into it would be circular.
        fallback = self._failure.substitute(
            "transform_failed", policy.failure_behavior["transform_failed"]
        )
        if fallback.reason_code not in work.reason_codes:
            work.reason_codes.append(fallback.reason_code)
        work.failure_path = fallback.failure_path
        return fallback.action, None

    def _audit_or_substitute(
        self,
        decision: Decision,
        request: EvaluationRequest,
        policy: Policy,
        binding: Binding,
    ) -> Decision:
        try:
            self._audit.emit(
                audit_id=decision.audit_id,
                decision=decision,
                tenant_id=request.tenant_id,
                workload_id=request.workload_id,
                stage=request.stage,
                retain_content=policy.retain_content,
                content_text=request.content.text,
            )
            return decision
        except AuditDeliveryError:
            fallback = self._failure.substitute(
                "audit_delivery_failed", policy.failure_behavior["audit_delivery_failed"]
            )
            action = clamp(fallback.action, binding.enforcement_state)
            if action == "redact" and decision.transformed_content is None:
                # Same rule as an unsatisfiable ceiling during evaluation: the
                # declared behavior stands rather than being clamped into a
                # redaction that cannot be produced.
                action = fallback.action
            codes = list(decision.reason_codes)
            if fallback.reason_code not in codes:
                codes.append(fallback.reason_code)
            # The audit record could not be delivered, so the caller still gets
            # a normalized decision annotated with the path that produced it.
            return Decision(
                action=action,
                allowed=action != "block",
                enforcement_state=decision.enforcement_state,
                reason_codes=tuple(codes),
                guardrail_category=decision.guardrail_category,
                policy_name=decision.policy_name,
                policy_version=decision.policy_version,
                evaluator_metadata=decision.evaluator_metadata,
                correlation_id=decision.correlation_id,
                audit_id=decision.audit_id,
                evaluation_latency_ms=decision.evaluation_latency_ms,
                transformed_content=(
                    decision.transformed_content if action == "redact" else None
                ),
                failure_path=fallback.failure_path,
            )

    def _spans(self, work: _Working) -> tuple[tuple[int, int], ...]:
        spans: list[tuple[int, int]] = []
        for _, finding in work.contributions:
            spans.extend(finding.spans)
        return tuple(sorted(set(spans)))

    def _category(self, work: _Working) -> str | None:
        """The category of the finding that drove the action (ADR-0003)."""
        if not work.contributions:
            return None
        driving = max(
            work.contributions, key=lambda pair: ACTION_SEVERITY[pair[0].action]
        )
        return driving[1].category


def _redact(text: str, spans: tuple[tuple[int, int], ...]) -> str:
    out: list[str] = []
    cursor = 0
    for start, end in spans:
        if start < cursor:  # overlapping detections
            continue
        out.append(text[cursor:start])
        out.append(REDACTION_PLACEHOLDER)
        cursor = end
    out.append(text[cursor:])
    return "".join(out)
