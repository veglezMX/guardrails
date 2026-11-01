"""Explicit real-worker fault acceptance inside a parent-created network namespace.

Imports never load a model. Only main/run start the original WorkerPool. This
tests the actual ASGI HTTP handler and child process, not a TLS server or CNI.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import signal
import stat
import sys
import time
from pathlib import Path
from uuid import uuid4

from evolution.classifier_loaded_evidence import (
    CASES,
    LONG_TAIL_ATTACK,
    LONG_TAIL_PREFIX,
    OBSERVATIONS,
    REQUEST_COUNT,
    SCHEMA,
    SOURCE_FILES,
    LoadedEvidenceError,
    canonical,
    digest,
    fixture_digest,
    network,
    observed,
    parse,
    require,
    window_count,
)
from evolution.classifier_loaded_evidence import (
    identity as file_identity,
)
from guardrails.evolution.classifier_contract import identity, score_checked

from services.classifier import network_isolation

TOKEN = "synthetic-loaded-worker-acceptance-credential"


def source_snapshot(root):
    result = {}
    for name in SOURCE_FILES:
        path = root / name
        require(path.resolve() == path, "source_path")
        before = path.lstat()
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid()
                and before.st_nlink == 1 and not before.st_mode & 0o022
                and 0 < before.st_size <= 2 * 1024 * 1024, "source_file")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            require(file_identity(os.fstat(stream.fileno())) == file_identity(before), "source_changed")
            raw = stream.read(2 * 1024 * 1024 + 1)
            require(file_identity(os.fstat(stream.fileno())) == file_identity(before)
                    and path.resolve() == path and file_identity(path.lstat()) == file_identity(before),
                    "source_changed")
        require(len(raw) == before.st_size, "source_changed")
        result[name] = digest(raw)
    return result


def network_snapshot(host_namespace):
    # The ASGI harness uses memory queues and child pipes; loopback stays down.
    # Readlink of /proc/self names the process leader. Verify the actual calling
    # thread shares that namespace before the shared observer opens any socket.
    namespace = os.readlink("/proc/self/ns/net")
    require(os.readlink("/proc/thread-self/ns/net") == namespace,
            "network_thread_namespace")
    try:
        value = network_isolation.snapshot(host_namespace)
    except network_isolation.IsolationError:
        raise LoadedEvidenceError("network_isolation_required") from None
    require(value["namespace"] == namespace
            and os.readlink("/proc/thread-self/ns/net") == namespace,
            "network_thread_namespace")
    return network(value, host_namespace)


class Writer:
    """Exclusive, descriptor-anchored output, including failed-run evidence."""
    def __init__(self, path):
        self.path = Path(path)
        parent = self.path.parent
        require(self.path.is_absolute() and parent.resolve() == parent, "output_path")
        before = parent.lstat()
        require(stat.S_ISDIR(before.st_mode) and before.st_uid == os.getuid()
                and not before.st_mode & 0o077, "output_parent")
        parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self.fd = None
        self.rows_fd = None
        try:
            require(file_identity(os.fstat(parent_fd)) == file_identity(before), "output_parent_changed")
            os.mkdir(self.path.name, 0o700, dir_fd=parent_fd)
            created = os.stat(self.path.name, dir_fd=parent_fd, follow_symlinks=False)
            self.fd = os.open(self.path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                              dir_fd=parent_fd)
            self.key = created.st_dev, created.st_ino
            require((os.fstat(self.fd).st_dev, os.fstat(self.fd).st_ino) == self.key,
                    "output_changed")
            self.check()
        except BaseException:
            if self.fd is not None:
                os.close(self.fd)
            raise
        finally:
            os.close(parent_fd)

    def check(self):
        current = self.path.lstat()
        held = os.fstat(self.fd)
        require(self.path.resolve() == self.path and (current.st_dev, current.st_ino) == self.key
                and (held.st_dev, held.st_ino) == self.key
                and stat.S_ISDIR(current.st_mode) and stat.S_IMODE(current.st_mode) == 0o700
                and current.st_uid == os.getuid(), "output_changed")

    def write(self, name, raw):
        require(name in {"started.json", "cases.jsonl", "result.json", "failure.json"}, "output_name")
        self.check()
        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=self.fd)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        self.check()

    def append_row(self, row):
        self.check()
        if self.rows_fd is None:
            self.rows_fd = os.open("cases.jsonl", os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                   0o600, dir_fd=self.fd)
            created = os.fstat(self.rows_fd)
            self.rows_key = created.st_dev, created.st_ino
        held = os.fstat(self.rows_fd)
        current = os.stat("cases.jsonl", dir_fd=self.fd, follow_symlinks=False)
        require((held.st_dev, held.st_ino) == self.rows_key
                and file_identity(current) == file_identity(held)
                and stat.S_ISREG(held.st_mode) and stat.S_IMODE(held.st_mode) == 0o600
                and held.st_nlink == 1 and held.st_uid == os.getuid(), "rows_changed")
        raw = canonical(row) + b"\n"
        require(os.write(self.rows_fd, raw) == len(raw), "row_short_write")
        os.fsync(self.rows_fd)
        self.check()

    def verify_rows(self, raw):
        self.check()
        held = os.fstat(self.rows_fd)
        current = os.stat("cases.jsonl", dir_fd=self.fd, follow_symlinks=False)
        require((held.st_dev, held.st_ino) == self.rows_key
                and file_identity(current) == file_identity(held)
                and held.st_nlink == 1 and stat.S_IMODE(held.st_mode) == 0o600
                and held.st_size == len(raw)
                and os.pread(self.rows_fd, len(raw) + 1, 0) == raw, "rows_changed")
        require(file_identity(os.fstat(self.rows_fd)) == file_identity(held), "rows_changed")
        self.check()

    def close(self):
        if self.rows_fd is not None:
            os.close(self.rows_fd)
        os.close(self.fd)


def request(*, segments=1):
    parts = [{"part_id": "synthetic-text", "role": "user", "json_pointer": None,
              "text": "Synthetic operational check."}]
    if segments == 2:
        parts.append({"part_id": "synthetic-json", "role": "assistant", "json_pointer": "/value",
                      "text": "Synthetic structured content."})
    elif segments == 17:
        parts = [{"part_id": "synthetic-" + str(n), "role": "retrieval", "json_pointer": None,
                  "text": "Synthetic check."} for n in range(17)]
    return {"api_major": 1, "identity": identity(), "request_id": uuid4().hex,
            "budget_ms": 2900, "segments": parts}


def scope(path="/v1/classifier/evaluate", method="POST"):
    return {"type": "http", "method": method, "path": path, "query_string": b"",
            "headers": [(b"authorization", b"Bearer " + TOKEN.encode())]}


async def http(app, value=None, *, ready=False):
    incoming = asyncio.Queue()
    if value is not None:
        incoming.put_nowait({"type": "http.request", "body": canonical(value), "more_body": False})
    sent = []

    async def send(row):
        sent.append(row)

    await app(scope("/v1/classifier/ready", "GET") if ready else scope(), incoming.get, send)
    require(len(sent) == 2 and sent[0]["type"] == "http.response.start"
            and sent[1]["type"] == "http.response.body", "http_framing")
    return sent[0]["status"], parse(sent[1]["body"])


def success(status, value, original, expected_windows=None):
    expected_windows = expected_windows or [1] * len(original["segments"])
    require(status == 200 and set(value) == {"api_major", "identity", "request_id", "complete", "windows", "scores"}
            and type(value["api_major"]) is int and value["api_major"] == 1
            and value["identity"] == identity() and value["request_id"] == original["request_id"]
            and value["complete"] is True and type(value["windows"]) is int
            and value["windows"] == sum(expected_windows)
            and type(value["scores"]) is list and len(value["scores"]) == len(original["segments"]),
            "http_inference_incomplete")
    for result, segment, count in zip(value["scores"], original["segments"], expected_windows, strict=True):
        require(set(result) == {"part_id", "json_pointer", "score", "windows"}
                and result["part_id"] == segment["part_id"]
                and result["json_pointer"] == segment["json_pointer"]
                and score_checked(result["score"]) and type(result["windows"]) is int
                and result["windows"] == count, "http_score_shape")


def load_tokenizer(model_directory):
    # Explicit real-run only. The original child already verified the full local
    # bundle and dependency versions before readiness; no second model loads.
    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(model_directory, local_files_only=True,
                                         trust_remote_code=False, use_fast=True)


def long_tail_layout(tokenizer):
    prefix = tokenizer.encode(LONG_TAIL_PREFIX, add_special_tokens=False, truncation=False)
    full = tokenizer.encode(LONG_TAIL_PREFIX + LONG_TAIL_ATTACK,
                            add_special_tokens=False, truncation=False)
    require(all(type(tokens) is list and len(tokens) <= 65536
                and all(type(token) is int and token >= 0 for token in tokens)
                for tokens in (prefix, full)), "tail_tokenizer_shape")
    shared = 0
    for left, right in zip(prefix, full):
        if left != right:
            break
        shared += 1
    special = tokenizer.num_special_tokens_to_add(pair=False)
    require(type(special) is int and 1 <= special < 256, "tail_special_tokens")
    counts = [window_count(len(tokens), special) for tokens in (prefix, full)]
    require(512 <= shared <= len(prefix) < len(full)
            and 2 <= counts[0] <= counts[1] <= 16, "tail_not_beyond_first_window")
    return {"fixture_sha256": fixture_digest(), "prefix_tokens": len(prefix), "total_tokens": len(full),
            "shared_prefix_tokens": shared, "special_tokens": special,
            "prefix_windows": counts[0], "expected_windows": counts[1]}


async def long_tail(pool, app, model_directory):
    layout = long_tail_layout(load_tokenizer(model_directory))
    previous = pool.process
    started = time.monotonic()
    durations, results = [], []
    for text, count in ((LONG_TAIL_PREFIX, layout["prefix_windows"]),
                        (LONG_TAIL_PREFIX + LONG_TAIL_ATTACK, layout["expected_windows"])):
        original = request()
        original["segments"][0].update(role="retrieval", text=text)
        operation = time.monotonic()
        status, value = await http(app, original)
        duration = (time.monotonic() - operation) * 1000
        success(status, value, original, [count])
        require(duration <= 3000 and pool.process is previous and pool.ready(), "tail_budget_or_worker")
        durations.append(duration)
        results.append(value)
    # This is a frozen capability fixture, not a replacement for the held-out
    # quality gate. Either model miss is retained as a failed original attempt.
    require(results[0]["scores"][0]["score"] < 0.90
            and results[1]["scores"][0]["score"] >= 0.90, "tail_detection")
    metadata = {**layout, "prefix_inspected_windows": results[0]["windows"],
                "inspected_windows": results[1]["windows"], "http_status": 200, "complete": True,
                "prefix_below_threshold": True, "tail_detected": True, "threshold": 0.90}
    observed("long_tail", metadata)
    return (time.monotonic() - started) * 1000, max(durations), metadata


class PipeObserver:
    """Forward original calls unchanged; retain only synthetic IDs in memory."""
    def __init__(self, original):
        self.original, self.ids, self.valid = original, [], True

    def __getattr__(self, name):
        return getattr(self.original, name)

    def write(self, raw):
        result = self.original.write(raw)
        try:
            if len(self.ids) >= 34:
                self.valid = False
            else:
                self.ids.append(parse(raw[:-1])["request"]["request_id"])
        except (ValueError, TypeError, KeyError):
            self.valid = False
        return result

    async def drain(self):
        return await self.original.drain()


async def queued_call(app, value, incoming, sent):
    incoming.put_nowait({"type": "http.request", "body": canonical(value), "more_body": False})

    async def send(row):
        sent.append(row)

    await app(scope(), incoming.get, send)


async def queue_overload(pool, app, namespace):
    require(pool.max_queued_jobs == 32, "queue_limit_changed")
    started = time.monotonic()
    previous = await stop_worker(pool, namespace)
    original_pipe = previous.stdin
    observer = PipeObserver(original_pipe)
    previous.stdin = observer
    tasks, channels, sent = [], [], []
    requests = [request() for _ in range(33)]
    try:
        for value in requests:
            incoming, messages = asyncio.Queue(), []
            channels.append(incoming); sent.append(messages)
            tasks.append(asyncio.create_task(queued_call(app, value, incoming, messages)))
        await until(lambda: pool._admitted == 33 and app._admitted == 33
                    and len(observer.ids) == 1, 1, "queue_not_full")
        require(observer.valid and observer.ids == [requests[0]["request_id"]], "queue_extra_dispatch")
        operation = time.monotonic()
        status, value = await http(app, request())
        duration = (time.monotonic() - operation) * 1000
        require(status == 429 and value == {"error": "queue_full"} and duration <= 3000
                and observer.valid and observer.ids == [requests[0]["request_id"]], "overload_not_refused")
        channels[1].put_nowait({"type": "http.disconnect"})
        try:
            await tasks[1]
        except asyncio.CancelledError:
            pass
        else:
            raise LoadedEvidenceError("queued_disconnect_not_cancelled")
        require(not sent[1] and pool._admitted == 32 and app._admitted == 32
                and observer.valid and observer.ids == [requests[0]["request_id"]], "queued_cancel_dispatched")
        # Cancel all remaining queued original requests before releasing the
        # active slot; none is retried or allowed to expire silently.
        for task in tasks[2:]:
            task.cancel()
        outcomes = await asyncio.gather(*tasks[2:], return_exceptions=True)
        require(all(isinstance(value, asyncio.CancelledError) for value in outcomes)
                and all(not value for value in sent[1:]) and pool._admitted == 1
                and app._admitted == 1 and observer.valid
                and observer.ids == [requests[0]["request_id"]], "queue_cleanup_dispatched")
        channels[0].put_nowait({"type": "http.disconnect"})
        try:
            await tasks[0]
        except asyncio.CancelledError:
            pass
        else:
            raise LoadedEvidenceError("active_disconnect_not_cancelled")
        require(not sent[0] and pool._admitted == 0 and app._admitted == 0
                and observer.valid and observer.ids == [requests[0]["request_id"]], "queue_final_dispatch")
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        previous.stdin = original_pipe
    await recovery(pool, app, previous)
    return (time.monotonic() - started) * 1000, duration


async def busy_death(pool, app, namespace):
    started = time.monotonic()
    previous = await stop_worker(pool, namespace)
    task = asyncio.create_task(http(app, request()))
    operation = time.monotonic()
    try:
        await until(lambda: pool._admitted == 1 and pool._available.empty(), 1, "busy_request_not_assigned")
        # A distinct unexpected death, not the timeout/cancellation kill path.
        previous.kill()
        status, value = await task
        duration = (time.monotonic() - operation) * 1000
        require(status == 503 and value == {"error": "evaluator_unavailable"}
                and duration <= 3000, "busy_death_partial_response")
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    await recovery(pool, app, previous)
    return (time.monotonic() - started) * 1000, duration


async def until(predicate, seconds, code):
    try:
        async with asyncio.timeout(seconds):
            while not predicate():
                await asyncio.sleep(0.005)
    except TimeoutError:
        raise LoadedEvidenceError(code) from None


def stopped(process):
    require(process.returncode is None, "worker_died_before_fault")
    state = next(row for row in Path(f"/proc/{process.pid}/status").read_text().splitlines()
                 if row.startswith("State:")).split()[1]
    return state in {"T", "t"}


async def stop_worker(pool, namespace):
    process = pool.process
    require(pool.ready() and process is not None
            and os.readlink(f"/proc/{process.pid}/ns/net") == namespace, "worker_namespace")
    process.send_signal(signal.SIGSTOP)
    await until(lambda: stopped(process), 2, "worker_not_stopped")
    return process


async def recovery(pool, app, previous):
    require(not pool.ready(), "ready_before_replacement")
    await asyncio.wait_for(previous.wait(), 5)
    require(previous.returncode == -signal.SIGKILL, "worker_not_reaped")
    await until(lambda: pool.ready() and pool.process is not previous, 125, "replacement_not_ready")
    original = request()
    status, value = await http(app, original)
    success(status, value, original)
    return 1


async def run(model_directory, namespace, emit):
    # Actual production import is deliberately delayed until explicit invocation.
    from services.classifier.app import ClassifierApplication, WorkerPool

    pool = WorkerPool(model_directory)
    app = ClassifierApplication(pool, bearer_token=TOKEN)
    request_count = 0
    startup = time.monotonic()
    try:
        initial, _ = await http(app, ready=True)
        require(initial == 503, "ready_before_model_load")
        await pool.start()
        startup_ms = (time.monotonic() - startup) * 1000
        require(pool.ready() and startup_ms <= 120_000, "model_not_ready")
        require(os.readlink(f"/proc/{pool.process.pid}/ns/net") == namespace, "worker_namespace")
        started = time.monotonic()
        ready_status, ready_body = await http(app, ready=True)
        require(ready_status == 200 and ready_body == {"api_major": 1, "ready": True, "identity": identity()},
                "loaded_ready_identity")
        original = request(segments=2)
        request_count += 1
        status, value = await http(app, original)
        success(status, value, original)
        duration = (time.monotonic() - started) * 1000
        require(duration <= 3000, "http_budget")
        emit("loaded_http", duration, duration)

        started = time.monotonic()
        previous = pool.process
        request_count += 1
        status, value = await http(app, request(segments=17))
        require(status == 422 and value == {"error": "inspection_incomplete"}
                and pool.ready() and pool.process is previous, "coverage_refusal")
        duration = (time.monotonic() - started) * 1000
        require(duration <= 3000, "http_budget")
        emit("complete_coverage_refusal", duration, duration)

        started = time.monotonic()
        previous = await stop_worker(pool, namespace)
        operation = time.monotonic()
        request_count += 1
        status, value = await http(app, request())
        duration = (time.monotonic() - operation) * 1000
        require(status == 504 and value == {"error": "evaluation_timeout"}
                and duration <= 3000, "deadline_response")
        request_count += await recovery(pool, app, previous)
        emit("http_deadline", (time.monotonic() - started) * 1000, duration)

        started = time.monotonic()
        previous = await stop_worker(pool, namespace)
        incoming, sent = asyncio.Queue(), []
        incoming.put_nowait({"type": "http.request", "body": canonical(request()), "more_body": False})

        async def send(row):
            sent.append(row)

        operation = time.monotonic()
        request_count += 1
        task = asyncio.create_task(app(scope(), incoming.get, send))
        try:
            await until(lambda: pool._available.empty() and pool._admitted == 1, 1, "request_not_assigned")
            incoming.put_nowait({"type": "http.disconnect"})
            try:
                await task
            except asyncio.CancelledError:
                pass
            else:
                raise LoadedEvidenceError("disconnect_not_cancelled")
            duration = (time.monotonic() - operation) * 1000
            require(not sent and duration <= 3000 and app._admitted == 0, "disconnect_response")
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        request_count += await recovery(pool, app, previous)
        emit("http_disconnect", (time.monotonic() - started) * 1000, duration)

        started = time.monotonic()
        previous = pool.process
        previous.kill()
        await asyncio.wait_for(previous.wait(), 5)
        require(not pool.ready(), "ready_after_idle_death")
        request_count += await recovery(pool, app, previous)
        emit("idle_death", (time.monotonic() - started) * 1000, 0)

        elapsed, duration, metadata = await long_tail(pool, app, model_directory)
        request_count += 2
        emit("long_tail", elapsed, duration, metadata)
        elapsed, duration = await queue_overload(pool, app, namespace)
        request_count += 35
        emit("queue_overload", elapsed, duration)
        elapsed, duration = await busy_death(pool, app, namespace)
        request_count += 2
        emit("busy_death", elapsed, duration)
    finally:
        await pool.close()
    require(not pool.ready() and not pool._tasks and not pool._cleanup
            and not pool._replacing and pool._monitor_task is None
            and pool.process.returncode is not None, "worker_cleanup_incomplete")
    return {"request_count": request_count, "closed": True, "startup_ms": startup_ms}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-loaded-acceptance", action="store_true", required=True)
    parser.add_argument("--model-directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-digest", required=True)
    parser.add_argument("--host-network-namespace", required=True)
    args = parser.parse_args(argv)
    require(sys.version_info[:3] == (3, 13, 16) and sys.dont_write_bytecode, "pinned_python_required")
    root = Path(__file__).resolve().parents[2]
    sources = source_snapshot(root)
    require(digest(canonical(sources)) == args.source_digest, "source_binding_changed")
    isolated = network_snapshot(args.host_network_namespace)
    os.umask(0o077)
    writer, rows = Writer(args.output), []
    started = {"schema_version": SCHEMA, "status": "running", "sources": sources,
               "identity": identity(), "python": "3.13.16", "network": isolated,
               "scope": "loaded_worker_and_asgi_no_external_network",
               "warmup_requests": 0, "retry_requests": 0}

    def emit(name, elapsed, operation, metadata=None):
        require(name == CASES[len(rows)], "case_order")
        metadata = observed(name, metadata if metadata is not None else dict(OBSERVATIONS[name]))
        row = {"case": name, "status": "passed", "elapsed_ms": elapsed,
               "operation_ms": operation, "observed": metadata}
        writer.append_row(row)
        rows.append(row)

    async def bounded():
        async with asyncio.timeout(600):
            return await run(str(args.model_directory), isolated["namespace"], emit)

    try:
        writer.write("started.json", canonical(started) + b"\n")
        emit("isolated_network", 0, 0)
        lifecycle = asyncio.run(bounded())
        after = network_snapshot(args.host_network_namespace)
        require(after == isolated and source_snapshot(root) == sources, "final_inputs_changed")
        require(len(rows) == len(CASES) and lifecycle["request_count"] == REQUEST_COUNT, "lifecycle_incomplete")
        raw = b"".join(canonical(row) + b"\n" for row in rows)
        writer.verify_rows(raw)
        result = {**started, "status": "passed", **lifecycle, "cases": len(rows),
                  "cases_sha256": digest(raw), "network_after": after}
        writer.write("result.json", canonical(result) + b"\n")
        writer.verify_rows(raw)
        print(canonical({"status": "passed", "cases": len(rows)}).decode())
        return 0
    except BaseException:  # noqa: BLE001 - finite failure evidence excludes raw exception content
        # Preserve the observed successful prefix and a finite failure only.
        # No raw exception, request, response, token, URL or process identifier.
        try:
            if writer.rows_fd is None:
                writer.write("cases.jsonl", b"".join(canonical(row) + b"\n" for row in rows))
            writer.write("failure.json", canonical({"status": "failed", "completed_cases": len(rows),
                "stage": CASES[len(rows)] if len(rows) < len(CASES) else "finalize"}) + b"\n")
        except BaseException:  # noqa: BLE001, S110 - replaced output must not receive a fallback write
            pass
        return 1
    finally:
        writer.close()


if __name__ == "__main__":
    raise SystemExit(main())
