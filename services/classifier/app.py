"""Bounded ASGI parent supervising exactly one isolated CPU model process."""
from __future__ import annotations

import asyncio
import hmac
import math
import os
import stat
import sys
import time
from pathlib import Path
from uuid import uuid4

from guardrails.evolution.classifier_contract import (
    DETECTOR_SECONDS,
    ERRORS,
    MAX_RESULT_BYTES,
    MAX_WIRE_BYTES,
    ClassifierError,
    canonical,
    identity,
    parse,
    request_checked,
)

EPOCH_HEADER = b"x-evolution-classifier-worker-epoch"
ATTEMPTS_HEADER = b"x-evolution-classifier-dispatch-attempts"
MAX_DISPATCH_ATTEMPTS = 2**63 - 1


class WorkerPool:
    """One request per worker, bounded queue; cancellation kills real CPU work."""

    def __init__(self, model_directory, *, max_queued_jobs=32):
        if type(max_queued_jobs) is not int or not 0 <= max_queued_jobs <= 32:
            raise ClassifierError("validation_failed")
        directory = Path(model_directory)
        if not directory.is_absolute() or directory.resolve() != directory:
            raise ClassifierError("validation_failed")
        self.directory = str(directory)
        self.max_queued_jobs = max_queued_jobs
        self.process = None
        self._available = asyncio.Queue(maxsize=1)
        self._tasks, self._cleanup = set(), set()
        self._replacing = set()
        self._monitor_task = None
        self._admitted = 0
        self._ready = False
        self._closed = False
        self._dispatch_epoch = None
        self._dispatch_attempts = 0

    def ready(self):
        return bool(self._ready and not self._closed and self.process is not None and self.process.returncode is None)

    def dispatch_headers(self):
        """Authenticated readiness evidence; not completed inference or content."""
        # No await separates this snapshot from ready(). An exhausted numeric
        # representation omits evidence rather than changing inference behavior.
        epoch, attempts = self._dispatch_epoch, self._dispatch_attempts
        if (not self.ready() or type(epoch) is not str or len(epoch) != 32
                or any(c not in "0123456789abcdef" for c in epoch)
                or type(attempts) is not int or not 0 <= attempts <= MAX_DISPATCH_ATTEMPTS):
            return ()
        return ((EPOCH_HEADER, epoch.encode("ascii")),
                (ATTEMPTS_HEADER, str(attempts).encode("ascii")))

    async def _spawn(self):
        epoch = uuid4().hex
        environment = {"PYTHONDONTWRITEBYTECODE": "1", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1", "TOKENIZERS_PARALLELISM": "false", "OMP_NUM_THREADS": "2", "MKL_NUM_THREADS": "2", "CUDA_VISIBLE_DEVICES": ""}
        if "PYTHONPATH" in os.environ:
            environment["PYTHONPATH"] = os.environ["PYTHONPATH"]
        process = await asyncio.create_subprocess_exec(sys.executable, "-B", "-s", "-m", "services.classifier.worker", self.directory, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, env=environment, limit=MAX_RESULT_BYTES)
        self.process = process
        # Each real child start gets a new comparison epoch, including reloads.
        # Before its verified handshake, ready() prevents this being observable.
        self._dispatch_epoch, self._dispatch_attempts = epoch, 0
        try:
            async with asyncio.timeout(120):
                raw = await process.stdout.readline()
                if raw != canonical({"api_major": 1, "ready": True, "identity": identity()}) + b"\n" or process.returncode is not None:
                    raise ClassifierError("evaluator_unavailable")
        except BaseException:
            await self._reap(process)
            raise
        if self._closed:
            await self._reap(process)
            raise ClassifierError("evaluator_unavailable")
        return process

    async def start(self):
        if self._closed or self.process is not None:
            raise ClassifierError("evaluator_unavailable")
        try:
            self._available.put_nowait(await self._spawn())
            self._ready = True
            self._monitor_task = asyncio.create_task(self._monitor())
        except BaseException:
            self._ready = False
            raise

    async def _reap(self, process):
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        async with asyncio.timeout(5):
            await process.communicate()

    async def _replace(self, process):
        try:
            await self._reap(process)
            if not self._closed:
                self._available.put_nowait(await self._spawn())
                self._ready = True
        except BaseException:  # noqa: BLE001 - replacement failure must leave readiness false
            self._ready = False

    def _schedule_replacement(self, process):
        if process in self._replacing:
            return
        self._replacing.add(process)
        self._ready = False
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass

        cleanup = asyncio.create_task(self._replace(process))
        self._cleanup.add(cleanup)

        def finished(task):
            self._cleanup.discard(task)
            self._replacing.discard(process)

        cleanup.add_done_callback(finished)

    async def _monitor(self):
        # Only idle queue entries belong to this monitor. Active requests retain
        # their own cancellation/failure cleanup and are never dispatched again.
        while True:
            await asyncio.sleep(0.05)
            if self._closed:
                return
            try:
                process = self._available.get_nowait()
            except asyncio.QueueEmpty:
                continue
            if process.returncode is None:
                self._available.put_nowait(process)
            else:
                self._schedule_replacement(process)

    async def evaluate(self, request, deadline):
        request_checked(request)
        if type(deadline) not in (int, float) or not math.isfinite(deadline):
            raise ClassifierError("validation_failed")
        if time.monotonic() >= deadline:
            raise ClassifierError("evaluation_timeout")
        if not self.ready():
            raise ClassifierError("evaluator_unavailable")
        if self._admitted >= self.max_queued_jobs + 1:
            raise ClassifierError("queue_full")
        self._admitted += 1
        task, process = asyncio.current_task(), None
        self._tasks.add(task)
        try:
            async with asyncio.timeout_at(deadline):
                process = await self._available.get()
                if process.returncode is not None or not self.ready():
                    raise ClassifierError("evaluator_unavailable")
                raw = canonical({"request": request, "deadline": deadline})
                if len(raw) > MAX_WIRE_BYTES:
                    raise ClassifierError("payload_too_large")
                # An attempted original pipe write counts even if write/drain
                # fails. Admission, queuing and readiness never increment it.
                self._dispatch_attempts += 1
                process.stdin.write(raw + b"\n")
                await process.stdin.drain()
                raw = await process.stdout.readline()
                if not raw or not raw.endswith(b"\n"):
                    raise ClassifierError("evaluator_unavailable")
                result = parse(raw[:-1], limit=MAX_RESULT_BYTES)
                if set(result) == {"error"} and result["error"] in ERRORS:
                    # An explicit bounded refusal leaves this worker usable.
                    self._available.put_nowait(process)
                    process = None
                    raise ClassifierError(result["error"])
                if result.get("identity") != identity() or result.get("request_id") != request["request_id"] or result.get("complete") is not True:
                    raise ClassifierError("evaluator_unavailable")
                self._available.put_nowait(process)
                process = None
                return result
        except TimeoutError:
            raise ClassifierError("evaluation_timeout") from None
        except (ClassifierError, asyncio.CancelledError):
            raise
        except Exception:  # noqa: BLE001 - IPC failure cannot expose content or driver text
            raise ClassifierError("evaluator_unavailable") from None
        finally:
            self._admitted -= 1
            self._tasks.discard(task)
            if process is not None:
                self._schedule_replacement(process)

    async def close(self):
        self._closed, self._ready = True, False
        if self._monitor_task is not None:
            self._monitor_task.cancel()
            await asyncio.gather(self._monitor_task, return_exceptions=True)
            self._monitor_task = None
        tasks = tuple(task for task in self._tasks if task is not asyncio.current_task())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._cleanup:
            cleanup = tuple(self._cleanup)
            for task in cleanup:
                task.cancel()
            await asyncio.gather(*cleanup, return_exceptions=True)
        if self.process is not None:
            await self._reap(self.process)


class ClassifierApplication:
    def __init__(self, pool, *, bearer_token):
        if type(bearer_token) is not str or not 32 <= len(bearer_token) <= 4096 or any(ord(c) < 33 or ord(c) > 126 for c in bearer_token):
            raise ClassifierError("validation_failed")
        self.pool, self.token = pool, ("Bearer " + bearer_token).encode("ascii")
        self._admitted = 0

    async def __call__(self, scope, receive, send):
        arrival = time.monotonic()
        if scope["type"] == "lifespan":
            while True:
                event = await receive()
                if event["type"] == "lifespan.startup":
                    try:
                        await self.pool.start()
                        await send({"type": "lifespan.startup.complete"})
                    except BaseException:  # noqa: BLE001 - reclaim partial startup before refusal
                        await self.pool.close()
                        await send({"type": "lifespan.startup.failed", "message": "evaluator_unavailable"})
                        return
                elif event["type"] == "lifespan.shutdown":
                    await self.pool.close()
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["type"] != "http":
            return
        admitted = False
        try:
            auth = [value for name, value in scope.get("headers", ()) if name.lower() == b"authorization"]
            if len(auth) != 1 or not hmac.compare_digest(auth[0], self.token):
                raise ClassifierError("unauthorized")
            if scope.get("query_string", b""):
                raise ClassifierError("validation_failed")
            if scope["method"] == "GET" and scope["path"] == "/v1/classifier/ready":
                if not self.pool.ready():
                    raise ClassifierError("evaluator_unavailable")
                await self._respond(send, 200, {"api_major": 1, "ready": True, "identity": identity()},
                                    headers=self.pool.dispatch_headers())
                return
            if scope["method"] != "POST" or scope["path"] != "/v1/classifier/evaluate":
                raise ClassifierError("resource_not_found")
            if self._admitted >= self.pool.max_queued_jobs + 1:
                raise ClassifierError("queue_full")
            self._admitted += 1
            admitted = True
            deadline = arrival + DETECTOR_SECONDS
            async with asyncio.timeout_at(deadline):
                body = bytearray()
                while True:
                    event = await receive()
                    if event["type"] == "http.disconnect":
                        raise asyncio.CancelledError
                    if event["type"] != "http.request" or not isinstance(event.get("body", b""), bytes):
                        raise ClassifierError("validation_failed")
                    if len(body) + len(event.get("body", b"")) > MAX_WIRE_BYTES:
                        raise ClassifierError("payload_too_large")
                    body.extend(event.get("body", b""))
                    if not event.get("more_body", False):
                        break
                request = request_checked(parse(bytes(body)))
                # The supplied budget includes elapsed body read from arrival.
                deadline = min(deadline, arrival + request["budget_ms"] / 1000)
                if time.monotonic() >= deadline:
                    raise ClassifierError("evaluation_timeout")
                async with asyncio.timeout_at(deadline):
                    job = asyncio.create_task(self.pool.evaluate(request, deadline))
                    disconnect = asyncio.create_task(self._disconnect(receive))
                    try:
                        done, _ = await asyncio.wait((job, disconnect), return_when=asyncio.FIRST_COMPLETED)
                        if disconnect in done:
                            raise asyncio.CancelledError
                        result = await job
                        await self._respond(send, 200, result)
                    finally:
                        for task in (job, disconnect):
                            if not task.done():
                                task.cancel()
                        await asyncio.gather(job, disconnect, return_exceptions=True)
        except TimeoutError:
            await self._respond(send, 504, {"error": "evaluation_timeout"})
        except ClassifierError as error:
            status = {"unauthorized": 401, "resource_not_found": 404, "validation_failed": 400, "payload_too_large": 413, "inspection_incomplete": 422, "queue_full": 429, "evaluation_timeout": 504}.get(error.code, 503)
            await self._respond(send, status, {"error": error.code})
        finally:
            if admitted:
                self._admitted -= 1

    @staticmethod
    async def _disconnect(receive):
        while True:
            if (await receive())["type"] == "http.disconnect":
                return

    @staticmethod
    async def _respond(send, status, value, *, headers=()):
        body = canonical(value)
        if len(body) > MAX_RESULT_BYTES:
            raise ClassifierError("evaluator_unavailable")
        await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode("ascii")), (b"cache-control", b"no-store"), *headers]})
        await send({"type": "http.response.body", "body": body})


def create_app():
    """Deployment factory; caller installs TLS and disables access logging."""
    if os.environ.get("CLASSIFIER_LANGUAGE_PROFILE") != "en":
        raise ClassifierError("evaluator_unavailable")
    token_path = Path(os.environ.get("CLASSIFIER_TOKEN_FILE", ""))
    model_path = Path(os.environ.get("CLASSIFIER_MODEL_DIRECTORY", ""))
    if not token_path.is_absolute() or not model_path.is_absolute():
        raise ClassifierError("evaluator_unavailable")
    # Kubernetes projected Secret leaf symlinks are allowed only after resolving
    # the deployment-selected absolute path; the final file is descriptor bound.
    resolved = token_path.resolve(strict=True)
    descriptor = os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_mode & 0o077 or not 32 <= before.st_size <= 4097:
            raise ClassifierError("evaluator_unavailable")
        raw = stream.read(4098)
        after = os.fstat(stream.fileno())
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) or token_path.resolve(strict=True) != resolved:
            raise ClassifierError("evaluator_unavailable")
    try:
        token = raw.decode("ascii").removesuffix("\n")
    except UnicodeError:
        raise ClassifierError("evaluator_unavailable") from None
    return ClassifierApplication(WorkerPool(model_path), bearer_token=token)
