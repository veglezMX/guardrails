import asyncio
import os
import time

import pytest

from guardrails.local_contract import ProofConfig
from guardrails.runtime.executor import DeadlineExecutor, ExecutionFailure
from guardrails.transport import ContractProblem
from proof.runtime_test_support import batch, components


@pytest.mark.parametrize('fault,code', [('stall', 'evaluation_timeout'), ('crash', 'evaluation_unavailable')])
def test_real_worker_termination_replacement_and_healthy_recovery(fault, code):
    async def run():
        executor = DeadlineExecutor(ProofConfig(workers=1, max_queued_jobs=1))
        await executor.start()
        old_pid = next(iter(executor.processes)).pid
        ticket = executor.admit()
        started = time.monotonic()
        try:
            with pytest.raises(ExecutionFailure) as error:
                await executor.execute(batch('a@b.co'), components()[1].checks, cutoff=started + .15, ticket=ticket, fault=fault)
            assert error.value.code == code and time.monotonic() - started < .25
            ticket.release()
            await asyncio.gather(*tuple(executor.cleanup))
            with pytest.raises(ProcessLookupError):
                os.kill(old_pid, 0)
            assert executor.admitted == 0 and executor.replacements == 1
            ticket = executor.admit()
            result = await executor.execute(batch('a@b.co'), components()[1].checks, cutoff=time.monotonic() + .4, ticket=ticket)
            assert result['findings'][0]['end'] == 6
            ticket.release()
        finally:
            await executor.close()
        assert not executor.processes
    asyncio.run(run())


def test_queue_admission_expiry_and_cleanup_do_not_leak_capacity():
    async def run():
        executor = DeadlineExecutor(ProofConfig(workers=1, max_queued_jobs=1))
        await executor.start()
        first, second = executor.admit(), executor.admit()
        with pytest.raises(ContractProblem) as error:
            executor.admit()
        assert error.value.status == 429
        task = asyncio.create_task(executor.execute(batch('clean'), components()[1].checks, cutoff=time.monotonic() + .3, ticket=first, fault='stall'))
        await asyncio.sleep(.02)
        try:
            with pytest.raises(ExecutionFailure) as error:
                await executor.execute(batch('clean'), components()[1].checks, cutoff=time.monotonic() + .05, ticket=second)
            assert error.value.code == 'evaluation_timeout'
            second.release()
            with pytest.raises(ExecutionFailure):
                await task
            first.release()
            await asyncio.gather(*tuple(executor.cleanup))
            assert executor.admitted == 0 and executor.available.qsize() == 1
        finally:
            await executor.close()
    asyncio.run(run())
