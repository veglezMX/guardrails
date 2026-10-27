"""Native lifecycle adapter for the pinned, unmodified LiteLLM proxy."""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from guardrails.evolution.errors import EvolutionError
from litellm.integrations.custom_guardrail import CustomGuardrail
from litellm.types.guardrails import GuardrailEventHooks

from .evolution import (
    EvaluationClient,
    GatewaySettings,
    TextConnector,
    Turn,
    current_turn,
    https_origin,
)


class EvolutionGuardrail(CustomGuardrail):
    use_native_lifecycle_hooks = True

    def __init__(self, settings_file: str, **kwargs: Any):
        modes = [GuardrailEventHooks.pre_call, GuardrailEventHooks.post_call]
        supplied = kwargs.pop("event_hook", modes)
        if set(supplied if isinstance(supplied, list) else [supplied]) != set(modes):
            raise ValueError("Both native guardrail hooks are required")
        if kwargs.pop("default_on", True) is not True:
            raise ValueError("The connector must be mandatory")
        kwargs.pop("supported_event_hooks", None)
        super().__init__(event_hook=modes, supported_event_hooks=modes, default_on=True, **kwargs)
        self.settings = GatewaySettings.from_file(settings_file)
        self.connector = TextConnector(self.settings.tenant_id, self.settings.workload_id, self.settings.provider_id,
                                       evaluation_profile=self.settings.evaluation_profile)
        self.client = EvaluationClient(self.settings.runtime_url, self.settings.workload_token_file, self.settings.ca_file,
                                       evaluation_profile=self.settings.evaluation_profile)

    @classmethod
    def get_supported_event_hooks(cls):
        return [GuardrailEventHooks.pre_call, GuardrailEventHooks.post_call]

    @staticmethod
    def turn() -> Turn:
        value = current_turn.get()
        if value is None:
            raise HTTPException(status_code=503, detail="context_invalid")
        return value

    @staticmethod
    def reject(turn: Turn, error: EvolutionError):
        turn.error = error
        turn.error_status = error.status if error.status in {400, 401, 403, 413, 429} else 503
        raise HTTPException(status_code=turn.error_status, detail=error.code)

    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        turn = self.turn()
        try:
            if call_type not in {"completion", "acompletion"} or data.get("stream", False) is not False:
                raise EvolutionError("unsupported_surface")
            if data.get("model") != self.settings.model_alias or turn.input_decision is not None:
                raise EvolutionError("provider_denied")
            request = self.connector.normalize(data, "input")
            decision = await self.client.evaluate(request)
            self.connector.verify(decision, request)
            result = self.connector.enforce(decision, data)
            turn.context_id = decision.context_id
            turn.input_decision = decision
            # The stored parts are transient, request-local and cleared by the boundary.
            turn.input_parts = request.parts
            self.mark_pre_call_hook_ran(result)
            return result
        except EvolutionError as error:
            self.reject(turn, error)

    async def async_pre_call_deployment_hook(self, kwargs, call_type):
        """Validate the selected route even when LiteLLM skips repeated pre-hooks."""
        turn = self.turn()
        try:
            if turn.input_decision is None or turn.model_dispatched:
                raise EvolutionError("context_invalid")
            adapter, _, model = self.settings.model_route.partition("/")
            selected = kwargs.get("model")
            if selected not in {model, self.settings.model_route}:
                raise EvolutionError("provider_denied")
            if kwargs.get("custom_llm_provider", adapter) != adapter:
                raise EvolutionError("provider_denied")
            try:
                base = https_origin(kwargs.get("api_base", ""))
            except (TypeError, ValueError):
                raise EvolutionError("destination_denied") from None
            if base != self.settings.model_base_url:
                raise EvolutionError("destination_denied")
            kwargs["timeout"] = self.settings.model_timeout
            kwargs["max_retries"] = 0
            kwargs["num_retries"] = 0
            if adapter == "ollama_chat":
                import litellm
                from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler

                from .evolution_gateway import ModelTransport

                handler = turn.native_http_handler
                if (not isinstance(handler, AsyncHTTPHandler)
                        or not isinstance(handler.transport, ModelTransport)
                        or handler.transport.settings != self.settings
                        or handler.client is not litellm.aclient_session):
                    raise EvolutionError("context_invalid")
                # BaseLLMHTTPHandler ignores a bare AsyncClient and otherwise
                # silently creates an independent, unguarded provider client.
                kwargs["client"] = handler
            turn.route_verified = True
            return kwargs
        except EvolutionError as error:
            self.reject(turn, error)

    async def async_post_call_success_hook(self, data, user_api_key_dict, response):
        turn = self.turn()
        try:
            if turn.input_decision is None or not turn.route_verified:
                raise EvolutionError("context_invalid")
            choices = getattr(response, "choices", None)
            if not isinstance(choices, list) or len(choices) != 1:
                raise EvolutionError("unsupported_surface")
            message = getattr(choices[0], "message", None)
            content = getattr(message, "content", None)
            if (not isinstance(content, str) or getattr(message, "role", None) != "assistant"
                    or any(getattr(message, name, None) is not None for name in ("tool_calls", "function_call", "refusal", "audio"))):
                raise EvolutionError("unsupported_surface")
            if turn.output_decision is not None:
                # LiteLLM invokes native callbacks at both model and proxy layers.
                # Reuse only the exact inspected (and possibly redacted) text.
                previous = turn.output_decision
                inspected = previous.transformed_parts[0].text if previous.action == "redact" else turn.output_parts[0].text
                if content != inspected:
                    raise EvolutionError("unsafe_transformation")
                return response
            payload = {"messages": [{"role": "assistant", "content": content}]}
            request = self.connector.normalize(payload, "output")
            decision = await self.client.evaluate(request)
            self.connector.verify(decision, request, turn.input_decision)
            transformed = self.connector.enforce(decision, payload)
            if decision.action == "redact":
                message.content = transformed["messages"][0]["content"]
            turn.output_decision = decision
            turn.output_parts = request.parts
            return response
        except EvolutionError as error:
            self.reject(turn, error)

    async def async_post_call_failure_hook(self, request_data, original_exception, user_api_key_dict, traceback_str=None):
        import os
        if os.environ.get("EVOLUTION_CONNECTOR_DIAGNOSTICS") == "1":
            import json
            import sys
            print(json.dumps({"event": "gateway_provider_failure", "type": type(original_exception).__name__}), file=sys.stderr, flush=True)
        turn = current_turn.get()
        if turn is not None and turn.error is None:
            from litellm import Timeout
            turn.error = EvolutionError("upstream_timeout" if isinstance(original_exception, Timeout) else "evaluator_unavailable")
            turn.error_status = 504 if isinstance(original_exception, Timeout) else 502
