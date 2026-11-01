"""Runtime-only synchronization routes; mount on the TLS management service."""

from __future__ import annotations

import asyncio
import hashlib
import queue
import threading
import time
from concurrent.futures import Future
from contextlib import asynccontextmanager
from contextvars import copy_context
from datetime import timedelta
from uuid import UUID, uuid4

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from .deadlines import current_deadline, remaining_ms
from .errors import EvolutionError
from .governance import principal
from .models import canonical_json_bytes, parse_time, strict_json_loads
from .snapshots import stamp


class SyncExecutor:
    """Fixed workers and bounded admission, including cancelled queued jobs."""

    def __init__(self, *, workers=4, max_pending=8):
        if not 1 <= workers <= max_pending <= 32:
            raise ValueError("invalid_sync_executor_bound")
        self._workers = workers
        self._limit = max_pending
        self._queue = queue.Queue(maxsize=max_pending)
        self._lock = threading.Lock()
        self._threads = []
        self._futures = set()
        self._closed = False

    @property
    def pending(self):
        with self._lock:
            return len(self._futures)

    def _work(self):
        while True:
            try:
                future, context, operation = self._queue.get(timeout=0.05)
            except queue.Empty:
                with self._lock:
                    if self._closed:
                        return
                continue
            try:
                if future.set_running_or_notify_cancel():
                    try:
                        result = context.run(self._invoke, operation)
                    except BaseException as error:  # noqa: BLE001 -- keep worker alive and deliver every failure through its future
                        future.set_exception(error)
                    else:
                        future.set_result(result)
            finally:
                # A cancelled queued item still occupies its bounded queue slot
                # until a real worker drains it; cancellation cannot grow a
                # hidden queue of abandoned work items.
                with self._lock:
                    self._futures.discard(future)
                self._queue.task_done()

    @staticmethod
    def _invoke(operation):
        remaining_ms()
        result = operation()
        remaining_ms()
        return result

    async def run(self, operation):
        deadline = current_deadline.get()
        if deadline is None:
            deadline = time.monotonic() + 2.0
        if deadline <= time.monotonic():
            raise EvolutionError("upstream_timeout")
        context = copy_context()
        context.run(current_deadline.set, deadline)
        with self._lock:
            if self._closed:
                raise EvolutionError("policy_unresolved")
            if len(self._futures) >= self._limit:
                raise EvolutionError("queue_full")
            if not self._threads:
                self._threads = [
                    threading.Thread(
                        target=self._work, name=f"runtime-sync-{index}", daemon=True
                    )
                    for index in range(self._workers)
                ]
                for thread in self._threads:
                    thread.start()
            future = Future()
            self._futures.add(future)
            self._queue.put_nowait((future, context, operation))
        wrapped = asyncio.wrap_future(future)

        def consume(value):
            if not value.cancelled():
                value.exception()

        wrapped.add_done_callback(consume)
        shielded = asyncio.shield(wrapped)
        shielded.add_done_callback(consume)
        try:
            async with asyncio.timeout_at(deadline):
                return await shielded
        except TimeoutError:
            future.cancel()
            raise EvolutionError("upstream_timeout") from None
        except asyncio.CancelledError:
            future.cancel()
            raise

    async def close(self):
        with self._lock:
            self._closed = True
            futures = tuple(self._futures)
        for future in futures:
            future.cancel()
        deadline = time.monotonic() + 5.0
        while any(thread.is_alive() for thread in self._threads):
            if time.monotonic() >= deadline:
                raise RuntimeError("sync_workers_shutdown_timeout")
            await asyncio.sleep(0.01)
        for thread in self._threads:
            thread.join(timeout=0)


def mount_sync_routes(app, service, distributor):
    executor = SyncExecutor()
    previous_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with previous_lifespan(application) as state:
            try:
                yield state
            finally:
                await executor.close()

    app.router.lifespan_context = lifespan
    app.state.runtime_sync_executor = executor

    async def handle(request: Request):
        correlation = str(uuid4())
        headers = {"Cache-Control": "no-store", "X-Request-ID": correlation}
        try:
            if request.url.scheme != "https":
                raise EvolutionError("forbidden")
            authorization = request.headers.get("authorization", "")
            if not authorization.startswith("Bearer "):
                raise EvolutionError("authentication_failed")
            identity = await executor.run(
                lambda: service.identity.authenticate(authorization[7:])
            )
            actor = principal(identity)
            if "runtime" not in actor["roles"] or not actor["tenant_id"]:
                raise EvolutionError("forbidden")
            body, idempotency = None, None
            if request.method == "POST":
                if "idempotency-key" not in request.headers:
                    raise EvolutionError("precondition_required")
                idempotency = str(UUID(request.headers["idempotency-key"]))
                if (
                    request.headers.get("content-type", "").split(";")[0]
                    != "application/json"
                ):
                    raise EvolutionError("invalid_json")
                raw = bytearray()
                async for chunk in request.stream():
                    raw.extend(chunk)
                    if len(raw) > 65536:
                        raise EvolutionError("payload_too_large")
                try:
                    body = strict_json_loads(bytes(raw))
                except ValueError:
                    raise EvolutionError("invalid_json") from None

            def transact():
                with service.repository.transaction(actor["tenant_id"]) as tx:
                    return dispatch(tx)

            def dispatch(tx):
                # Revoked installs and suspended tenants cannot replay old success.
                distributor.runtime(tx, identity)
                if request.method == "GET":
                    snapshot = distributor.issue(tx, identity)
                    headers["ETag"] = '"' + snapshot["digest"] + '"'
                    if request.headers.get("if-none-match") == headers["ETag"]:
                        return Response(status_code=304, headers=headers)
                    return JSONResponse(snapshot, headers=headers)
                fingerprint = hashlib.sha256(canonical_json_bytes(body)).hexdigest()
                key = f"{actor['principal_id']}:POST:/v2/runtime/receipts:{idempotency}"
                tx.lock("idempotency:" + key)
                previous = tx.get("sync_idempotency", key)
                if (
                    previous
                    and parse_time(previous["expires_at"]) > distributor.clock()
                ):
                    if previous["fingerprint"] != fingerprint:
                        raise EvolutionError("idempotency_conflict")
                    return JSONResponse(previous["response"], headers=headers)
                receipt = distributor.accept_receipt(tx, identity, body)
                value = {
                    "fingerprint": fingerprint,
                    "response": receipt,
                    "expires_at": stamp(distributor.clock() + timedelta(hours=24)),
                }
                if previous:
                    tx.put(
                        "sync_idempotency",
                        key,
                        value,
                        expected_revision=previous["revision"],
                    )
                else:
                    tx.insert("sync_idempotency", key, value)
                return JSONResponse(receipt, headers=headers)

            return await executor.run(transact)
        except EvolutionError as exc:
            return JSONResponse(
                exc.envelope(correlation), status_code=exc.status, headers=headers
            )
        except ValidationError, ValueError, TypeError, KeyError:
            error = EvolutionError("validation_failed")
            return JSONResponse(
                error.envelope(correlation), status_code=422, headers=headers
            )

    app.add_api_route(
        "/v2/runtime/snapshot", handle, methods=["GET"], name="runtime_snapshot"
    )
    app.add_api_route(
        "/v2/runtime/receipts", handle, methods=["POST"], name="runtime_receipt"
    )
    return app
