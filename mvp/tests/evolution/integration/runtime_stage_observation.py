"""Fixture-only volatile phase timing; never a runtime acceptance result.

Allocate before HTTPS fork. All request-time updates are nonblocking shared-memory
writes; source checks, projection and exclusive evidence publication happen only
outside measured request windows. Correlation UUIDs never leave volatile memory.
"""

import asyncio
import contextvars
import ctypes
import json
import multiprocessing
import os
import stat
import sys
import time
from pathlib import Path
from uuid import UUID

from evolution.diagnostics import SourceCatalog, read_catalog
from evolution.measurements import validate_measurement
from evolution.registry import EvidenceError
from evolution.stage_measurements import (
    CASE,
    FILENAME,
    JOB_LIMIT,
    KINDS,
    OUTCOMES,
    PHASES,
    ROW_LIMIT,
    SCHEMA,
    SOURCES,
    validate,
    validate_sources,
)

ROOT = Path(__file__).resolve().parents[4]
CAPACITY = 512
_REQUEST = contextvars.ContextVar("fixture_stage_request", default=None)
_JOB = contextvars.ContextVar("fixture_stage_job", default=None)


class _Job(ctypes.Structure):
    _fields_ = [(name, ctypes.c_longlong) for name in (
        "kind", "phase", "submitted", "worker_start", "worker_end", "await_end",
        "worker_outcome", "await_outcome",
    )]


class _Request(ctypes.Structure):
    _fields_ = [
        ("correlation", ctypes.c_ubyte * 16),
        ("started", ctypes.c_longlong),
        ("finished", ctypes.c_longlong),
        ("status", ctypes.c_int),
        ("jobs_count", ctypes.c_int),
        ("jobs", _Job * JOB_LIMIT),
    ]


def _outcome(error):
    from guardrails.evolution.errors import EvolutionError

    if error is None:
        return OUTCOMES.index("returned")
    if isinstance(error, asyncio.CancelledError):
        return OUTCOMES.index("cancelled")
    return OUTCOMES.index("known_error" if isinstance(error, EvolutionError) else "other_error")


def _safe(stages, method, *args, **kwargs):
    # Catch recorder failures only, never an original operation/await. Recording
    # cannot change an HTTP result or replace cancellation or the first error.
    try:
        return method(*args, **kwargs)
    except BaseException:  # noqa: BLE001 - recorder failures must not mask original outcomes
        try:
            stages.failed.value = 1
        except BaseException:  # noqa: BLE001,S110 - never log private errors from recorder guards
            pass
        return None


class SharedStages:
    def __init__(self):
        context = multiprocessing.get_context("fork")
        self.rows = context.RawArray(_Request, CAPACITY)
        self.lost = context.RawArray(ctypes.c_ubyte, CAPACITY)
        self.used = context.RawValue(ctypes.c_int, 0)
        self.full = context.RawValue(ctypes.c_ubyte, 0)
        self.failed = context.RawValue(ctypes.c_ubyte, 0)
        self.lock = context.Lock()

    def claim(self, correlation):
        # Invalid/duplicate headers are never repaired or interpreted as content.
        try:
            parsed = UUID(correlation)
            if str(parsed) != correlation:
                return None
        except (ValueError, TypeError, AttributeError):
            return None
        if not self.lock.acquire(False):
            return None
        try:
            index = self.used.value
            if index >= CAPACITY:
                self.full.value = 1
                return None
            self.used.value += 1
            self.rows[index].correlation[:] = parsed.bytes
            self.rows[index].started = time.monotonic_ns()
            return index
        finally:
            self.lock.release()

    def change(self, index, update):
        if index is None:
            return None
        if not self.lock.acquire(False):
            # Monotonic byte flag: never reset, and never wait for an observer.
            self.lost[index] = 1
            return None
        try:
            return update(self.rows[index], time.monotonic_ns())
        finally:
            self.lock.release()

    def begin(self, index, kind):
        def update(row, now):
            if row.jobs_count >= JOB_LIMIT:
                self.lost[index] = 1
                return None
            number = row.jobs_count
            row.jobs_count += 1
            job = row.jobs[number]
            job.kind = KINDS.index(kind)
            job.submitted = now
            if kind == "detector":
                job.phase = PHASES.index("detector")
                job.worker_outcome = OUTCOMES.index("not_applicable")
            return number
        return self.change(index, update)

    def job(self, index, number, **values):
        if number is None:
            return
        def update(row, now):
            for name, value in values.items():
                setattr(row.jobs[number], name, now if value == "now" else value)
        self.change(index, update)

    def mark(self, phase):
        active = _JOB.get()
        if active is not None and active[0] is self:
            self.job(active[1], active[2], phase=PHASES.index(phase))

    def snapshot(self):
        # Only the parent calls after windows. A busy read is explicit missing
        # observation rather than blocking an outstanding cleanup callback.
        if not self.lock.acquire(False):
            return None
        try:
            return [
                (_Request.from_buffer_copy(self.rows[i]), bool(self.lost[i]))
                for i in range(self.used.value)
            ]
        finally:
            self.lock.release()


def instrument(service, stages):
    """Wrap this fixture service's ports; each original method is called once."""
    original_metadata = service.metadata

    async def metadata(tenant, operation, deadline):
        active = _REQUEST.get()
        if active is None or active[0] is not stages:
            return await original_metadata(tenant, operation, deadline)
        index = active[1]
        number = _safe(stages, stages.begin, index, "metadata")

        def callback():
            token = _safe(stages, _JOB.set, (stages, index, number))
            _safe(stages, stages.job, index, number, worker_start="now")
            error = None
            try:
                return operation()
            except BaseException as caught:
                error = caught
                raise
            finally:
                _safe(stages, lambda: stages.job(index, number, worker_end="now", worker_outcome=_outcome(error)))
                if token is not None:
                    _safe(stages, _JOB.reset, token)

        error = None
        try:
            return await original_metadata(tenant, callback, deadline)
        except BaseException as caught:
            error = caught
            raise
        finally:
            _safe(stages, lambda: stages.job(index, number, await_end="now", await_outcome=_outcome(error)))

    def sync_port(original, phase):
        def observed(*args, **kwargs):
            _safe(stages, stages.mark, phase)
            return original(*args, **kwargs)
        return observed

    original_detector = service.registry.evaluate

    async def detector(*args, **kwargs):
        active = _REQUEST.get()
        if active is None or active[0] is not stages:
            return await original_detector(*args, **kwargs)
        index = active[1]
        number = _safe(stages, stages.begin, index, "detector")
        error = None
        try:
            return await original_detector(*args, **kwargs)
        except BaseException as caught:
            error = caught
            raise
        finally:
            _safe(stages, lambda: stages.job(index, number, await_end="now", await_outcome=_outcome(error)))

    service.metadata = metadata
    service.identity.authenticate = sync_port(service.identity.authenticate, "authenticate")
    service._admit = sync_port(service._admit, "admit")
    service._finalize = sync_port(service._finalize, "finalize")
    service.quotas.release = sync_port(service.quotas.release, "cleanup")
    service.registry.evaluate = detector
    return service


class StageBoundary:
    def __init__(self, app, stages):
        self.app, self.stages = app, stages

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("path") != "/v2/evaluations":
            return await self.app(scope, receive, send)
        headers = [v for k, v in scope.get("headers", ()) if k.lower() == b"x-request-id"]
        try:
            correlation = headers[0].decode("ascii") if len(headers) == 1 else None
        except UnicodeError:
            correlation = None
        index = _safe(self.stages, self.stages.claim, correlation)
        token = _safe(self.stages, _REQUEST.set, None if index is None else (self.stages, index))

        async def observed_send(message):
            if message["type"] == "http.response.start":
                _safe(self.stages, self.stages.change, index, lambda row, now: setattr(row, "status", message["status"]))
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                _safe(self.stages, self.stages.change, index, lambda row, now: setattr(row, "finished", now))
        try:
            return await self.app(scope, receive, observed_send)
        finally:
            if token is not None:
                _safe(self.stages, _REQUEST.reset, token)


def frozen_sources():
    directory = os.environ.get("EVOLUTION_TEST_EVIDENCE_DIR")
    if directory is not None:
        path = Path(directory)
        info = path.lstat()
        if (not path.is_absolute() or path.is_symlink() or not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700):
            raise EvidenceError("measurement_path_invalid")
        sources = read_catalog(path.parents[1] / (CASE + ".sources.json"), ROOT)
    else:
        catalog = SourceCatalog(ROOT)
        sources = {name: catalog.load(catalog.path(name))[0] for name in SOURCES}
    validate_sources(ROOT, sources)
    return sources


def _project_row(raw, lost, selected):
    base = {**selected, "observation": "unavailable", "reason": "missing",
            "response_ms": None, "jobs": []}
    if raw is None:
        return base
    if lost:
        return {**base, "reason": "dropped_update"}
    if not raw.jobs_count:
        return {**base, "reason": "no_jobs"}
    def ms(value):
        return round((value - raw.started) / 1_000_000, 3) if value else None
    jobs = [{
        "phase": PHASES[job.phase], "kind": KINDS[job.kind],
        "submitted_ms": ms(job.submitted), "worker_start_ms": ms(job.worker_start),
        "worker_end_ms": ms(job.worker_end), "await_end_ms": ms(job.await_end),
        "worker_outcome": OUTCOMES[job.worker_outcome],
        "await_outcome": OUTCOMES[job.await_outcome],
    } for job in raw.jobs[:raw.jobs_count]]
    values = [ms(raw.finished)] + [job[k] for job in jobs for k in (
        "submitted_ms", "worker_start_ms", "worker_end_ms", "await_end_ms")]
    if any(v is not None and not 0 <= v <= 10000 for v in values):
        return {**base, "reason": "timing_out_of_range"}
    complete = bool(raw.finished) and all(
        job["phase"] != "unknown" and job["await_end_ms"] is not None and (job["kind"] == "detector" or
        job["worker_end_ms"] is not None) for job in jobs
    )
    admitted = any(job["phase"] == "admit" and job["worker_outcome"] == "returned"
                   and job["await_outcome"] == "returned" for job in jobs)
    if admitted and not any(job["phase"] == "cleanup" for job in jobs):
        complete = False
    return {**base, "observation": "complete" if complete else "partial",
            "reason": None, "response_ms": ms(raw.finished), "jobs": jobs}


def project(stages, baseline, contention):
    snapshots = [_safe(item, item.snapshot) for item in stages]
    rows, omitted = [], {}
    for name, results in (("baseline_b", baseline["b"]), ("contention_b", contention["b"]),
                          ("contention_a", contention["a"])):
        failed = [(i, result) for i, result in enumerate(results) if not result["correct"]]
        selected = failed[:max(0, ROW_LIMIT - len(rows))]
        omitted[name] = len(failed) - len(selected)
        for ordinal, result in selected:
            selected_row = {"cohort": name, "ordinal": ordinal, "replica": result["replica"],
                            "status": result["status"]}
            available = snapshots[result["replica"]]
            matches = [] if available is None else [
                (row, lost) for row, lost in available
                if bytes(row.correlation) == UUID(result["correlation_id"]).bytes
            ]
            if stages[result["replica"]].failed.value:
                row = {**_project_row(None, False, selected_row), "reason": "recorder_error"}
            elif len(matches) != 1:
                row = _project_row(None, False, selected_row)
                row["reason"] = "busy" if available is None else "duplicate" if matches else "missing"
            else:
                row = _project_row(*matches[0], selected_row)
                if matches[0][0].status and matches[0][0].status != result["status"]:
                    row = {**_project_row(None, False, selected_row), "reason": "status_mismatch"}
            rows.append(row)
    return {"schema_version": SCHEMA, "rows": rows, "omitted": omitted}


def record(stages, baseline, contention, evidence, sources):
    value = project(stages, baseline, contention)
    validate_measurement(evidence)
    validate(value, case_id=CASE, root=ROOT, sources=sources, measurement=evidence)
    if directory := os.environ.get("EVOLUTION_TEST_EVIDENCE_DIR"):
        path = Path(directory)
        info = path.lstat()
        if (not path.is_absolute() or path.is_symlink() or not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700):
            raise EvidenceError("measurement_path_invalid")
        fd = os.open(path / FILENAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(json.dumps(value, sort_keys=True, allow_nan=False) + "\n")


def record_all(*recorders):
    """Try all observers without replacing the original operational exception."""
    original, first = sys.exc_info()[1], None
    for save in recorders:
        try:
            save()
        except BaseException as error:  # noqa: BLE001 - preserve the first failure across all writers
            if first is None:
                first = error
    if original is None and first is not None:
        raise first
