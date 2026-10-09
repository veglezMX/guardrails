"""Fixed response faults, imported only by credentialed proof runtime controls."""
import asyncio
from fastapi.responses import JSONResponse


async def response_fault(decision, mode):
    if mode == 'delay':
        await asyncio.sleep(2)
        return None
    value = decision.to_wire()
    if mode == 'malformed':
        value['action'] = 'not-a-decision'
    elif mode == 'invalid_transformation':
        value.update(action='redact', allowed=True, transformed_content={'segments':[{'segment_id':'foreign','text':'[REDACTED]'}]})
    return JSONResponse(value)
