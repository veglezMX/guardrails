"""Bounded subprocess pool with a queue deadline and asynchronous reaping."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass

from ..local_contract import ProofConfig
from ..transport import ContractProblem


class ExecutionFailure(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass
class Admission:
    executor: object
    held: bool = True
    deferred: bool = False
    release_requested: bool = False

    def release(self):
        self.release_requested = True
        if self.held and not self.deferred:
            self.held = False
            self.executor.admitted -= 1


class DeadlineExecutor:
    def __init__(self, config=None):
        self.config = config or ProofConfig()
        self.available = asyncio.Queue(maxsize=self.config.workers)
        self.processes = set()
        self.cleanup = set()
        self.admitted = 0
        self.closed = False
        self.replacements = 0

    def admit(self):
        if self.closed or self.admitted >= self.config.workers + self.config.max_queued_jobs:
            raise ContractProblem('capacity_exceeded', 429)
        self.admitted += 1
        return Admission(self)

    async def spawn(self):
        env = {'PYTHONDONTWRITEBYTECODE': '1'}
        if 'PYTHONPATH' in os.environ:
            env['PYTHONPATH'] = os.environ['PYTHONPATH']
        process = await asyncio.create_subprocess_exec(
            sys.executable, '-m', 'guardrails.runtime.worker', stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            env=env, limit=16_777_216)
        self.processes.add(process)
        try:
            async with asyncio.timeout(10):
                if await process.stdout.readline() != b'{"ready":true}\n':
                    raise RuntimeError('worker_start_failed')
        except BaseException:
            await self.reap(process)
            raise
        return process

    async def start(self):
        try:
            for _ in range(self.config.workers):
                self.available.put_nowait(await self.spawn())
        except BaseException:
            await self.close()
            raise

    async def reap(self, process):
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        # communicate drains pipes and waits for the killed process; this runs
        # in a cleanup task, never on a timed-out request's response path.
        await process.communicate()
        self.processes.discard(process)

    async def replace(self, process, ticket):
        try:
            await self.reap(process)
            if not self.closed:
                try:
                    replacement = await self.spawn()
                    self.available.put_nowait(replacement)
                    self.replacements += 1
                except Exception:
                    self.closed = True
        finally:
            ticket.deferred = False
            if ticket.release_requested:
                ticket.release()

    async def execute(self, content, checks, *, cutoff, ticket, fault=None):
        process = None
        try:
            async with asyncio.timeout_at(cutoff):
                process = await self.available.get()
                job = dict(content=content.to_wire(), checks=[c.to_wire() for c in checks], fault=fault)
                process.stdin.write(json.dumps(job, separators=(',', ':')).encode() + b'\n')
                await process.stdin.drain()
                raw = await process.stdout.readline()
                result = json.loads(raw)
                if not isinstance(result, dict) or set(result) != {'findings', 'elapsed_ms'}:
                    raise ValueError()
                self.available.put_nowait(process)
                process = None
                return result
        except TimeoutError:
            raise ExecutionFailure('evaluation_timeout') from None
        except asyncio.CancelledError:
            raise
        except Exception:
            raise ExecutionFailure('evaluation_unavailable') from None
        finally:
            if process is not None:
                ticket.deferred = True
                task = asyncio.create_task(self.replace(process, ticket))
                self.cleanup.add(task)
                task.add_done_callback(self.cleanup.discard)

    async def close(self):
        self.closed = True
        if self.cleanup:
            await asyncio.gather(*tuple(self.cleanup))
        for process in tuple(self.processes):
            await self.reap(process)

    def health(self):
        return dict(workers=len(self.processes), admitted=self.admitted,
                    available_workers=self.available.qsize(), replacements=self.replacements,
                    saturated=self.admitted >= self.config.workers + self.config.max_queued_jobs)
