"""Validate raw model envelopes before the native SDK discards unsupported fields."""
import asyncio
import json
from .gateway_state import current_turn


async def validate_model_response(response):
    if response.status_code != 200:
        return
    turn = current_turn.get()
    try:
        async with asyncio.timeout(10):
            raw = bytearray()
            async for chunk in response.aiter_bytes():
                raw.extend(chunk)
                if len(raw) > 8 * 1024 * 1024:
                    raise ValueError()
            # HTTPX Response.aread uses this same cache; restore it so the native
            # SDK consumes the identical validated bytes after this event hook.
            response._content = bytes(raw)
        from guardrails.transport import strict_json_loads
        value = strict_json_loads(bytes(raw))
        choices = value['choices']
        if not isinstance(choices, list) or len(choices) != 1:
            raise ValueError()
        message = choices[0]['message']
        if (not isinstance(message, dict) or message.get('role') != 'assistant'
                or type(message.get('content')) is not str
                or any(message.get(field) is not None for field in ('tool_calls','function_call','refusal','audio'))
                or len(message['content'].encode()) > 1024 * 1024):
            raise ValueError()
    except Exception:
        if turn is not None:
            turn.error_status = 503
            turn.error = {'code':'unsupported_surface','message':'Unsupported model response', 'correlation_id':turn.correlation_id}
        # Never attach response text or SDK/provider details to this exception.
        raise ValueError('unsupported_model_response') from None
