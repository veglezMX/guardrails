"""Public LiteLLM CustomGuardrail hooks for the local technical proof.

Detection and policy selection belong to the runtime. The gateway verifies the
runtime decision and applies transformations before model dispatch/delivery.
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from litellm.integrations.custom_guardrail import CustomGuardrail
from litellm.types.guardrails import GuardrailEventHooks

from .core import RuntimeClient, RuntimeClientError
from .gateway_state import GatewayTurn, current_turn


SAFE_DECISION_FIELDS = (
    "decision_id", "correlation_id", "policy_id", "policy_version",
    "binding_revision", "snapshot_generation", "enforcement_state", "action",
    "reason_codes", "operational_codes", "source", "audit_status",
)


class LocalProofGuardrail(CustomGuardrail):
    """Mandatory native pre/post hooks with request-local policy consistency."""

    use_native_lifecycle_hooks = True

    def __init__(
        self, runtime_url: str, token_file: str, ca_file: str,
        tenant_id: str = "local-proof", workload_id: str = "gateway-a",
        model_alias: str = "proof-a", spool_path: str | None = None, **kwargs: Any,
    ) -> None:
        modes = [GuardrailEventHooks.pre_call, GuardrailEventHooks.post_call]
        requested = kwargs.pop("event_hook", modes)
        if set(requested if isinstance(requested, list) else [requested]) != set(modes):
            raise ValueError("local proof requires both pre_call and post_call")
        if kwargs.pop("default_on", True) is not True:
            raise ValueError("local proof guardrail must be mandatory")
        kwargs.pop("supported_event_hooks", None)
        super().__init__(event_hook=modes, supported_event_hooks=modes, default_on=True, **kwargs)
        self._client = RuntimeClient(runtime_url, token_file, ca_file, tenant_id=tenant_id, workload_id=workload_id, spool_path=spool_path)
        self._tenant_id = tenant_id
        self._workload_id = workload_id
        self._model_alias = model_alias

    @classmethod
    def get_supported_event_hooks(cls) -> list[GuardrailEventHooks]:
        return [GuardrailEventHooks.pre_call, GuardrailEventHooks.post_call]

    @staticmethod
    def _turn() -> GatewayTurn:
        turn = current_turn.get()
        if turn is None:
            raise HTTPException(status_code=503, detail="guardrail_boundary_unavailable")
        return turn

    @staticmethod
    def _reject(turn: GatewayTurn, code: str, status: int, decision: dict | None = None) -> None:
        turn.error = {
            "code": code,
            "message": "Request blocked by configured guardrails" if code == "guardrail_blocked" else "Guardrail evaluation unavailable",
            "correlation_id": turn.correlation_id,
        }
        if decision is not None:
            turn.error["decision_id"] = decision["decision_id"]
            if status == 503:
                turn.error.update(source=decision['source'], audit_status=decision['audit_status'], operational_codes=decision['operational_codes'])
        turn.error_status = status
        raise HTTPException(status_code=status, detail=code)

    async def _evaluate(self, stage: str, segments: list[dict[str, str]], turn: GatewayTurn) -> dict[str, Any]:
        body = {
            "schema_version": "local-proof-v1", "tenant_id": self._tenant_id,
            "workload_id": self._workload_id, "stage": stage,
            "content": {"segments": segments}, "correlation_id": turn.correlation_id,
            "context_id": turn.context_id if stage == "output" else None,
        }
        try:
            decision = await self._client.evaluate_turn(body, turn)
        except RuntimeClientError as error:
            self._reject(turn, error.code, 401 if error.code == "authentication_failed" else 503)
        if decision["action"] == "block":
            operational = set(decision['operational_codes']) - {'disabled'}
            self._reject(turn, 'evaluation_unavailable' if operational else 'guardrail_blocked', 503 if operational else 403, decision)
        return decision

    async def async_pre_call_hook(self, user_api_key_dict: Any, cache: Any, data: dict[str, Any], call_type: Any) -> dict[str, Any]:
        turn = self._turn()
        messages = data.get("messages")
        # The ASGI boundary already validated raw fields; recheck the structure
        # that the actual proxy will dispatch, after its own normalization.
        if call_type not in ("completion", "acompletion") or data.get("stream") is True:
            self._reject(turn, "unsupported_surface", 400)
        if not isinstance(messages, list) or not messages or len(messages) > 32:
            self._reject(turn, "unsupported_surface", 400)
        if any(not isinstance(message, dict) or not isinstance(message.get("content"), str) for message in messages):
            self._reject(turn, "unsupported_surface", 400)
        segments = [{"segment_id": str(index), "text": message["content"]} for index, message in enumerate(messages)]
        decision = await self._evaluate("input", segments, turn)
        if decision["action"] == "redact":
            # Copy each message to avoid mutating a shared caller object.
            data["messages"] = [dict(message, content=part["text"]) for message, part in zip(messages, decision["transformed_content"]["segments"], strict=True)]
        turn.context_id = decision["context_id"]
        turn.input_decision = {key: decision[key] for key in SAFE_DECISION_FIELDS}
        config = decision["client_config"]
        if config is not None:
            turn.output_timeout_s = config["output_timeout_ms"] / 1000.0
        return data

    async def async_post_call_failure_hook(self, request_data, original_exception, user_api_key_dict, traceback_str=None):
        from litellm import Timeout as ModelTimeout
        turn = current_turn.get()
        if turn is not None and turn.error is None and isinstance(original_exception, ModelTimeout):
            turn.error_status = 504
            turn.error = {'code':'upstream_failed', 'message':'Model request timed out', 'correlation_id':turn.correlation_id}
        return None

    async def async_post_call_success_hook(self, data: dict[str, Any], user_api_key_dict: Any, response: Any) -> Any:
        turn = self._turn()
        if turn.input_decision is None:
            self._reject(turn, "context_invalid", 503)
        choices = getattr(response, "choices", None)
        if not isinstance(choices, list) or len(choices) != 1:
            self._reject(turn, "unsupported_surface", 503)
        message = getattr(choices[0], "message", None)
        text = getattr(message, "content", None)
        if (not isinstance(text, str) or getattr(message, 'role', None) != 'assistant' or
                any(getattr(message, key, None) is not None for key in ('tool_calls','function_call','refusal','audio'))):
            self._reject(turn, "unsupported_surface", 503)
        decision = await self._evaluate("output", [{"segment_id": "0", "text": text}], turn)
        if decision["action"] == "redact":
            message.content = decision["transformed_content"]["segments"][0]["text"]
        turn.output_decision = {key: decision[key] for key in SAFE_DECISION_FIELDS}
        return response
