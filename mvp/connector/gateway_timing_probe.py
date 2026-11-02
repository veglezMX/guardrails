"""Temporary, single-correlation gateway observation; never a policy input."""
from __future__ import annotations

import contextvars
import functools
import hashlib
import json
import os
import stat
import sys
import threading
import time

OPERATION = "c35ddaeb-d9ec-439f-914f-416a5e8d3cb3"
CORRELATION = "bab85e39-02cf-42ea-9449-1e62816c0bad"
PATH = "/tmp/guardrails-gateway-timing-" + OPERATION + ".capture"
FRAME_BYTES = 4096
PHASES = (
    "request_validated", "input_evaluate_enter", "input_evaluate_return",
    "input_verified", "input_enforced", "input_accepted",
    "deployment_enter", "deployment_verified", "completion_enter",
    "completion_return", "platform_enter", "platform_return",
    "transport_enter", "transport_reserved", "transport_request_enter",
    "transport_response_return", "output_hook_enter", "output_evaluate_enter",
    "output_evaluate_return", "output_verified", "output_enforced",
    "output_accepted", "disconnect", "cancel_requested", "proxy_return",
    "boundary_return", "boundary_error", "boundary_finally",
    "completion_error", "platform_error",
)
_INDEX = {name: index for index, name in enumerate(PHASES)}
_ACTIVE = contextvars.ContextVar("gateway_fixed_timing", default=None)
_RECORDER = None


class _Recorder:
    """All mutations are nonblocking; observation loss never changes a request."""

    def __init__(self):
        self.lock = threading.Lock()
        self.fd = None
        self.identity = None
        self.claimed = False
        self.finished = False
        self.loss = False
        self.writes = 0
        self.hooks = {"completion": False, "platform": False}
        self.clock = None
        self.marks = [None] * len(PHASES)
        self.threads = [None] * len(PHASES)
        self.counts = [0] * len(PHASES)
        self.late = [False] * len(PHASES)
        self.terminal = None
        self.terminal_us = None
        self.state = "ready"

    def open(self):
        fd = os.open(PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        self.fd = fd
        os.fchmod(fd, 0o600)
        os.ftruncate(fd, FRAME_BYTES)
        info = os.fstat(fd)
        self.identity = (info.st_dev, info.st_ino)
        self._publish()

    def _publish(self):
        info = os.fstat(self.fd)
        leaf = os.stat(PATH, follow_symlinks=False)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid()
                or info.st_size != FRAME_BYTES
                or (info.st_dev, info.st_ino) != self.identity
                or (leaf.st_dev, leaf.st_ino) != self.identity):
            raise OSError("capture identity changed")
        record = {
            "schema": "gateway-fixed-correlation-timing-v1",
            "operation": OPERATION, "correlation_id": CORRELATION,
            "state": self.state, "clock": self.clock,
            "hooks": self.hooks, "marks_us": self.marks,
            "native_thread_ids": self.threads, "counts": self.counts,
            "late_marks": self.late,
            "terminal": self.terminal, "observation_loss": self.loss,
            "terminal_us": self.terminal_us,
        }
        raw = json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")
        state = {"ready": "R", "active": "A", "terminal": "F"}[self.state]
        header = f"EVOGWT1 {state} {len(raw):08x} {hashlib.sha256(raw).hexdigest()}\n".encode("ascii")
        frame = header + raw
        if len(frame) > FRAME_BYTES:
            raise ValueError("capture frame bound")
        if os.pwrite(self.fd, frame.ljust(FRAME_BYTES, b"\0"), 0) != FRAME_BYTES:
            raise OSError("short capture write")
        self.writes += 1

    def claim(self, correlation_id, arrived):
        if correlation_id != CORRELATION or not self.lock.acquire(blocking=False):
            return None
        try:
            if self.claimed or self.fd is None or self.finished:
                return None
            self.claimed = True
            self.state = "active"
            self.clock = {"wall_ns": arrived[0], "monotonic_ns": arrived[1]}
            self._publish()
            return self
        finally:
            self.lock.release()

    def mark(self, phase, *, terminal=None):
        if not self.lock.acquire(blocking=False):
            self.loss = True
            return
        try:
            if not self.claimed or self.fd is None:
                return
            if self.writes >= 64:
                self.loss = True
                return
            index = _INDEX[phase]
            elapsed = max(0, (time.monotonic_ns() - self.clock["monotonic_ns"]) // 1000)
            self.counts[index] = min(255, self.counts[index] + 1)
            first = self.marks[index] is None
            if self.marks[index] is None:
                self.marks[index] = elapsed
                self.threads[index] = threading.get_native_id()
                self.late[index] = self.finished
            if terminal is not None and not self.finished:
                self.terminal = terminal
                self.terminal_us = elapsed
                self.state = "terminal"
                self.finished = True
            elif not first:
                return
            self._publish()
        finally:
            self.lock.release()

    def close(self):
        if not self.lock.acquire(blocking=False):
            self.loss = True
            return
        try:
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None
        finally:
            self.lock.release()


def _safe(call, *args, **kwargs):
    try:
        return call(*args, **kwargs)
    except Exception:  # noqa: BLE001 -- Recorder failures must never affect routing.
        # Do not expose content, replace provider exceptions, or affect routing.
        if _RECORDER is not None:
            _RECORDER.loss = True
        return None


def _wrap_loaded(recorder, module_name, attribute, label):
    module = sys.modules.get(module_name)
    original = vars(module).get(attribute) if module is not None else None
    if not callable(original) or getattr(original, "__gateway_fixed_timing__", False):
        return

    @functools.wraps(original)
    def observed(*args, **kwargs):
        mark(label + "_enter")
        try:
            return original(*args, **kwargs)
        except BaseException:
            mark(label + "_error")
            raise
        finally:
            mark(label + "_return")

    observed.__gateway_fixed_timing__ = True
    setattr(module, attribute, observed)
    recorder.hooks[label] = True


def initialize():
    """Run only after the application's normal imports; never import a provider."""
    global _RECORDER
    if _RECORDER is not None:
        return
    recorder = _Recorder()
    _RECORDER = recorder
    _safe(_wrap_loaded, recorder, "litellm.main", "completion", "completion")
    _safe(_wrap_loaded, recorder, "openai._base_client", "get_platform", "platform")
    _safe(recorder.open)


def claim(correlation_id, arrived):
    recorder = _RECORDER
    value = _safe(recorder.claim, correlation_id, arrived) if recorder is not None else None
    return _ACTIVE.set(value)


def suspend():
    return _ACTIVE.set(None)


def mark(phase):
    recorder = _ACTIVE.get()
    if isinstance(recorder, _Recorder):
        _safe(recorder.mark, phase)


def finish(terminal):
    recorder = _ACTIVE.get()
    if isinstance(recorder, _Recorder):
        _safe(recorder.mark, "boundary_finally", terminal=terminal)


def terminate(terminal, phase):
    recorder = _ACTIVE.get()
    if isinstance(recorder, _Recorder):
        _safe(recorder.mark, phase, terminal=terminal)


def reset(token):
    _safe(_ACTIVE.reset, token)


def close():
    if _RECORDER is not None:
        _safe(_RECORDER.close)
