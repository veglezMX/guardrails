"""LiteLLM binding for the guardrail connector.

LiteLLM is the first supported gateway, not the product boundary. Everything
that matters lives in `core.py`; this file only maps LiteLLM's pre-call and
post-call hooks onto that, and maps decisions back into LiteLLM-native effects.

`litellm` is imported lazily so the connector core stays testable — and the
contract stays demonstrable — without the gateway installed.
"""

from __future__ import annotations

from typing import Any

from .core import (
    ENFORCEABLE_ACTIONS,
    ConnectorConfig,
    EvaluationClient,
    EvaluationTransportError,
    HttpEvaluationClient,
)


class GuardrailBlocked(Exception):
    """Raised to stop a LiteLLM call. The gateway turns this into a response."""

    def __init__(self, message: str, decision: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.decision = decision or {}


class GuardrailsHandler:
    """A LiteLLM custom-logger style handler.

    Registered as `litellm.callbacks = [GuardrailsHandler(config)]`. LiteLLM
    calls `async_pre_call_hook` before dispatching to the model provider and
    `async_post_call_success_hook` with the model's response.
    """

    def __init__(
        self, config: ConnectorConfig, client: EvaluationClient | None = None
    ) -> None:
        self._config = config
        self._client = client or HttpEvaluationClient(
            config.endpoint, config.credential
        )

    # --- pre-call ------------------------------------------------------------

    async def async_pre_call_hook(
        self,
        user_api_key_dict: Any = None,
        cache: Any = None,
        data: dict[str, Any] | None = None,
        call_type: str | None = None,
    ) -> dict[str, Any] | None:
        data = data or {}
        correlation_id = self._correlation_id(data)
        text = self._last_user_message(data)

        decision = self._evaluate("input", text, correlation_id)
        action = decision["action"]

        if action not in ENFORCEABLE_ACTIONS or action == "block":
            raise GuardrailBlocked(self._config.safe_rejection_text, decision)
        if action == "redact":
            self._replace_last_user_message(
                data, decision["transformed_content"]["text"]
            )

        # Carry the correlation id so the paired output evaluation joins to this
        # one in the audit stream.
        data.setdefault("metadata", {})["guardrails_correlation_id"] = correlation_id
        return data

    # --- post-call -----------------------------------------------------------

    async def async_post_call_success_hook(
        self,
        data: dict[str, Any] | None = None,
        user_api_key_dict: Any = None,
        response: Any = None,
    ) -> Any:
        data = data or {}
        correlation_id = (data.get("metadata") or {}).get(
            "guardrails_correlation_id"
        ) or self._correlation_id(data)
        text = self._response_text(response)

        decision = self._evaluate("output", text, correlation_id)
        action = decision["action"]

        if action not in ENFORCEABLE_ACTIONS or action == "block":
            raise GuardrailBlocked(self._config.safe_fallback_text, decision)
        if action == "redact":
            self._replace_response_text(
                response, decision["transformed_content"]["text"]
            )
        return response

    # --- helpers -------------------------------------------------------------

    def _evaluate(
        self, stage: str, text: str, correlation_id: str
    ) -> dict[str, Any]:
        body = {
            "tenant_id": self._config.tenant_id,
            "workload_id": self._config.workload_id,
            "stage": stage,
            "content": {"kind": "text", "text": text},
            "correlation_id": correlation_id,
        }
        try:
            return self._client.evaluate(body, self._config.timeout_s)
        except EvaluationTransportError as exc:
            if self._config.on_evaluation_unavailable == "allow_and_log":
                # Documented behavior, chosen by the workload — not a default.
                return {
                    "action": "log_only",
                    "reason_codes": ["GR-000:platform:evaluation_unavailable"],
                    "enforcement_state": "observe",
                }
            raise GuardrailBlocked(
                f"evaluation unavailable at stage '{stage}': {exc}"
            ) from exc

    @staticmethod
    def _correlation_id(data: dict[str, Any]) -> str:
        import uuid

        metadata = data.get("metadata") or {}
        existing = metadata.get("guardrails_correlation_id") or data.get("litellm_call_id")
        return str(existing) if existing else str(uuid.uuid4())

    @staticmethod
    def _last_user_message(data: dict[str, Any]) -> str:
        for message in reversed(data.get("messages") or []):
            if message.get("role") == "user":
                content = message.get("content")
                if isinstance(content, str):
                    return content
        return data.get("input") or ""

    @staticmethod
    def _replace_last_user_message(data: dict[str, Any], text: str) -> None:
        for message in reversed(data.get("messages") or []):
            if message.get("role") == "user":
                message["content"] = text
                return
        data["input"] = text

    @staticmethod
    def _response_text(response: Any) -> str:
        try:
            return response.choices[0].message.content or ""
        except AttributeError:
            pass
        if isinstance(response, dict):
            choices = response.get("choices") or []
            if choices:
                return (choices[0].get("message") or {}).get("content") or ""
        return str(response or "")

    @staticmethod
    def _replace_response_text(response: Any, text: str) -> None:
        try:
            response.choices[0].message.content = text
            return
        except AttributeError:
            pass
        if isinstance(response, dict):
            choices = response.get("choices") or []
            if choices:
                choices[0].setdefault("message", {})["content"] = text


def __getattr__(name: str) -> Any:
    """Load the actual LiteLLM extension only in the installed gateway image."""
    if name == "LocalProofGuardrail":
        from .native_guardrail import LocalProofGuardrail
        return LocalProofGuardrail
    raise AttributeError(name)
