"""Entry-point enforcement — the connector half of the split.

The runtime decides; the connector enforces. This module owns the enforcement
mechanics only: call input evaluation before inference, apply the decision to
the actual model call, call output evaluation after inference, apply that
decision to what the caller receives, and carry one correlation identifier
across all of it.

It contains no policy, no thresholds, and no detection logic, so a policy stays
testable independently of any gateway. Nothing in here is LiteLLM-specific —
`litellm_hooks.py` is a thin binding over this.

RuntimeClient implements the versioned proof with runtime-owned configuration,
per-turn pinning and durable fallback delivery. The earlier synchronous classes
remain explicitly legacy diagnostics for the unversioned reference fixtures;
they are never loaded by the proof gateway or lifecycle CLI.
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
        if decision is None or decision.get('action') not in ENFORCEABLE_ACTIONS:
            self._client_failure(result, 'input')
            if not result.allowed:
                return result
            # Legacy diagnostic: explicit unpinned fail-open still invokes and
            # returns the real model response, using the same captured fallback.
            result.text = invoke_model(prompt)
            result.model_called = True
            return self._client_failure(result, 'output')
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
        if decision is None or decision.get('action') not in ENFORCEABLE_ACTIONS:
            result.text = response
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


class RuntimeClientError(EvaluationTransportError):
    """A safe failure code from the local-proof async transport boundary."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class RuntimeClient:
    """Verified asynchronous runtime transport and per-turn failure handling.

    Imports stay lazy for the explicitly retained standard-library prototype.
    Configuration objects are immutable; each turn captures its own reference.
    """

    def __init__(self, runtime_url: str, token_file: str, ca_file: str, *, tenant_id=None, workload_id=None, spool_path=None) -> None:
        import ssl
        from pathlib import Path
        from urllib.parse import urlsplit

        url = urlsplit(runtime_url)
        if (url.scheme != "https" or not url.hostname or url.username or
                url.password or url.query or url.fragment or url.path not in ("", "/")):
            raise ValueError("runtime_url must be an HTTPS origin")
        token = Path(token_file).read_text(encoding="utf-8").strip()
        if not token or any(character.isspace() for character in token):
            raise ValueError("invalid runtime credential file")
        self._url = runtime_url.rstrip("/") + "/v1/evaluate"
        self._token = token
        self._tls = ssl.create_default_context(cafile=ca_file)
        self._origin = runtime_url.rstrip('/')
        self.identity = (tenant_id, workload_id)
        self.config = None
        self.degraded = True
        self.spool_failed = False
        self._task = None
        from .spool import EventSpool
        self.spool = EventSpool(spool_path) if spool_path else None

    async def evaluate(self, body: dict[str, Any], timeout_s: float = 1.5) -> dict[str, Any]:
        import asyncio
        import httpx
        from guardrails.transport import ContractProblem, parse_decision, strict_json_loads, parse_model, TextBatchModel, ProblemModel

        try:
            async with asyncio.timeout(timeout_s):
                async with httpx.AsyncClient(
                    verify=self._tls, trust_env=False, follow_redirects=False,
                    timeout=httpx.Timeout(timeout_s, connect=min(.25, timeout_s)),
                ) as client:
                    async with client.stream(
                        "POST", self._url, json=body,
                        headers={"Authorization": f"Bearer {self._token}"},
                    ) as response:
                        if response.status_code in (400, 401, 403, 413):
                            raise RuntimeClientError({401: 'authentication_failed', 403: 'identity_mismatch'}.get(response.status_code, 'invalid_request'))
                        if response.status_code not in (200, 422):
                            raise RuntimeClientError("evaluation_unavailable")
                        expected_type = 'application/problem+json' if response.status_code == 422 else 'application/json'
                        if response.headers.get("content-type", "").split(";", 1)[0] != expected_type:
                            raise RuntimeClientError('invalid_request' if response.status_code == 422 else 'invalid_runtime_response')
                        chunks = bytearray()
                        async for chunk in response.aiter_bytes():
                            chunks.extend(chunk)
                            if len(chunks) > 8 * 1024 * 1024:
                                raise RuntimeClientError("invalid_runtime_response")
                        if response.status_code == 422:
                            try:
                                problem = parse_model(bytes(chunks), ProblemModel)
                            except ContractProblem:
                                raise RuntimeClientError('invalid_request') from None
                            if (body.get('stage') == 'output' and body.get('context_id')
                                    and problem.status == 422 and problem.code in ('context_invalid', 'context_expired')):
                                raise RuntimeClientError('context_invalid')
                            raise RuntimeClientError('invalid_request')
                        raw = strict_json_loads(bytes(chunks))
                        if isinstance(raw, dict) and raw.get('action') == 'redact':
                            try:
                                transformed = parse_model(json.dumps(raw.get('transformed_content')).encode(), TextBatchModel)
                            except ContractProblem:
                                raise RuntimeClientError('unsafe_transformation') from None
                            if ([part.segment_id for part in transformed.segments] !=
                                    [part['segment_id'] for part in body['content']['segments']]):
                                raise RuntimeClientError('unsafe_transformation')
                        decision = parse_decision(bytes(chunks)).model_dump(mode="json")
        except (httpx.TimeoutException, TimeoutError):
            raise RuntimeClientError("evaluation_timeout") from None
        except (httpx.HTTPError, ContractProblem):
            raise RuntimeClientError("evaluation_unavailable") from None

        for field in ("tenant_id", "workload_id", "stage", "correlation_id"):
            if decision[field] != body[field]:
                raise RuntimeClientError("invalid_runtime_response")
        if decision["source"] != "runtime":
            raise RuntimeClientError("invalid_runtime_response")
        # A terminal block can release its consumed context and omit the
        # token. It still must match identity, stage and correlation above.
        # A non-null foreign token is always a protocol failure.
        if body["stage"] == "output":
            terminal_block = decision["action"] == "block" and decision["context_id"] is None
            if not terminal_block and decision["context_id"] != body["context_id"]:
                raise RuntimeClientError("invalid_runtime_response")
        if decision["allowed"] and not decision["context_id"]:
            raise RuntimeClientError("invalid_runtime_response")
        if 'content' in body:
            from guardrails import local_contract as domain
            try:
                original_batch = domain.TextBatch(segments=tuple(domain.TextSegment(**part) for part in body['content']['segments']))
                parse_decision(json.dumps(decision).encode()).to_domain().validate_content(original_batch)
            except domain.DomainValidationError:
                raise RuntimeClientError('unsafe_transformation') from None
        if decision["action"] == "redact":
            original = body["content"]["segments"]
            transformed = decision["transformed_content"]["segments"]
            if ([part["segment_id"] for part in original] !=
                    [part["segment_id"] for part in transformed]):
                raise RuntimeClientError("unsafe_transformation")
        return decision

    async def _request(self, method, path, *, body=None):
        import asyncio
        import httpx
        from guardrails.transport import strict_json_loads
        async with asyncio.timeout(.5):
            async with httpx.AsyncClient(verify=self._tls, trust_env=False, follow_redirects=False,
                                        timeout=httpx.Timeout(.5, connect=.25)) as client:
                async with client.stream(method, self._origin + path, json=body,
                                         headers={'Authorization': 'Bearer ' + self._token}) as response:
                    if response.status_code != 200:
                        raise RuntimeClientError('evaluation_unavailable')
                    if response.headers.get('content-type', '').split(';', 1)[0] != 'application/json':
                        raise RuntimeClientError('invalid_runtime_response')
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 8 * 1024 * 1024:
                            raise RuntimeClientError('invalid_runtime_response')
                    return strict_json_loads(bytes(raw))

    async def refresh(self):
        import json
        from guardrails.transport import ClientConfigModel, parse_model
        try:
            value = await self._request('GET', '/v1/client-config')
            candidate = parse_model(json.dumps(value).encode(), ClientConfigModel).to_domain()
            if (candidate.tenant_id, candidate.workload_id) != self.identity:
                raise RuntimeClientError('identity_mismatch')
            if self.config and candidate.generation < self.config.generation:
                raise RuntimeClientError('invalid_runtime_response')
            self.config = candidate
            self.degraded = False
        except Exception:
            self.degraded = True

    async def replay(self):
        import asyncio
        import json
        from guardrails.runtime.models import ClientEventsResponse
        from guardrails.transport import parse_model
        if self.spool is None:
            return
        try:
            rows = await asyncio.to_thread(self.spool.batch)
            if not rows:
                return
            value = await self._request('POST', '/v1/client-events', body=rows)
            receipt = parse_model(json.dumps(value).encode(), ClientEventsResponse)
            accepted = {str(value) for value in receipt.accepted_event_ids}
            if not accepted <= {row['event_id'] for row in rows}:
                raise RuntimeClientError('invalid_runtime_response')
            await asyncio.to_thread(self.spool.acknowledge, accepted)
            self.spool_failed = False
        except Exception:
            # Retain every unacknowledged event; retry once on the next iteration.
            self.spool_failed = True

    async def start(self):
        import asyncio
        if self._task is None:
            self._task = asyncio.create_task(self._background())

    async def _background(self):
        import asyncio
        while True:
            await self.refresh()
            await self.replay()
            await asyncio.sleep(1)

    async def close(self):
        import asyncio
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def health(self):
        return {'ready': self.config is not None,
                'degraded': self.degraded or self.spool_failed,
                'client_generation': self.config.generation if self.config else None,
                'audit_delivery_failed': self.spool_failed}

    async def fallback(self, body, config, code, *, prior=None):
        import asyncio
        import sys
        from datetime import datetime, timezone
        from uuid import uuid4
        from guardrails.transport import parse_decision
        import json
        field = {'evaluation_timeout': 'evaluation_timeout', 'unsafe_transformation': 'transformation_failure',
                 'audit_write_failed': 'audit_failure'}.get(code, 'evaluation_unavailable')
        code = code if code in ('evaluation_timeout', 'unsafe_transformation', 'audit_write_failed', 'context_invalid') else 'evaluation_unavailable'
        action = 'block' if getattr(config.failure_behavior, field) == 'block' else 'log_only'
        decision = dict(decision_id=str(uuid4()), evaluation_id=None, correlation_id=body['correlation_id'],
                        context_id=None, tenant_id=body['tenant_id'], workload_id=body['workload_id'], stage=body['stage'],
                        policy_id=None, policy_version=None, policy_digest=None, binding_revision=None,
                        snapshot_generation=None, enforcement_state=None, action=action, allowed=action != 'block',
                        would_be_action=action, reason_codes=[], operational_codes=[code], guardrail_category=None,
                        evaluator_metadata=[], audit_id=None, findings=[], transformed_content=None, evaluation_ms=0.0,
                        source='connector', audit_status='spooled', client_config=config.to_wire())
        if prior is not None:
            strength = {'allow':0,'log_only':1,'redact':2,'block':3}
            action = max((action, prior['action']), key=strength.get)
            decision.update(action=action, allowed=action != 'block',
                            transformed_content=prior['transformed_content'] if action == 'redact' else None)
            for key in ('policy_id','policy_version','policy_digest','binding_revision','snapshot_generation','enforcement_state'):
                decision[key] = prior[key]
        excluded = {'tenant_id', 'workload_id', 'correlation_id', 'context_id', 'source', 'client_config', 'transformed_content'}
        event = dict(event_id=str(uuid4()), source='connector', occurred_at=datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                     tenant_id=body['tenant_id'], workload_id=body['workload_id'], actor_id=None, event_type='client_fallback',
                     correlation_id=body['correlation_id'], content=None,
                     metadata={key: value for key, value in decision.items() if key not in excluded})
        try:
            if self.spool is None:
                raise OSError('spool_unavailable')
            await asyncio.to_thread(self.spool.append, event)
        except Exception:
            self.spool_failed = True
            decision['audit_status'] = 'failed'
            decision['operational_codes'] = sorted(set(decision['operational_codes'] + ['audit_write_failed']))
            # Failed durability cannot weaken the configured audit action.
            if config.failure_behavior.audit_failure == 'block':
                decision.update(action='block', allowed=False, transformed_content=None)
            print('audit_write_failed', file=sys.stderr)
        return parse_decision(json.dumps(decision).encode()).model_dump(mode='json')

    async def evaluate_turn(self, body, turn):
        """Capture configuration before input; never select a new policy for output."""
        import json
        from guardrails.transport import parse_model, ClientConfigModel
        if body['stage'] == 'input':
            if self.config is None:
                raise RuntimeClientError('client_unconfigured')
            turn.client_config = self.config
        config = turn.client_config
        if config is None:
            raise RuntimeClientError('client_unconfigured')
        if body['stage'] == 'output' and turn.context_id is None:
            return await self.fallback(body, config, 'context_invalid')
        try:
            timeout = getattr(config, body['stage'] + '_timeout_ms') / 1000
            decision = await self.evaluate(body, timeout)
            if body['stage'] == 'input':
                if decision['client_config'] is None:
                    raise RuntimeClientError('invalid_runtime_response')
                turn.client_config = parse_model(json.dumps(decision['client_config']).encode(), ClientConfigModel).to_domain()
                config = turn.client_config
                if decision['snapshot_generation'] is not None and config.generation != decision['snapshot_generation']:
                    raise RuntimeClientError('invalid_runtime_response')
            else:
                before = turn.input_decision
                for key in ('policy_id', 'policy_version', 'binding_revision', 'snapshot_generation', 'enforcement_state'):
                    # Runtime operational terminal blocks can omit policy metadata.
                    if decision[key] != before[key] and not (decision['action'] == 'block' and decision['policy_id'] is None):
                        raise RuntimeClientError('invalid_runtime_response')
            if decision['audit_status'] == 'failed':
                delivery = await self.fallback(body, config, 'audit_write_failed', prior=decision)
                decision['audit_status'] = delivery['audit_status']
                if delivery['action'] == 'block':
                    decision.update(action='block', allowed=False, transformed_content=None)
            return decision
        except RuntimeClientError as error:
            if error.code in ('authentication_failed', 'identity_mismatch', 'invalid_request'):
                raise
            return await self.fallback(body, config, error.code)
