"""Private observations of original declared-action POSTs; no extra requests."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from time import monotonic
from uuid import UUID

from evolution.diagnostics import SourceCatalog, read_catalog
from evolution.measurements import (
    FILES,
    RUNTIME_ACTION_CASES,
    RUNTIME_ACTION_CODES,
    RUNTIME_ACTION_LIMIT,
    RUNTIME_ACTION_SCHEMA,
    RUNTIME_ACTION_SOURCES,
    validate_runtime_action_observation,
)
from evolution.registry import EvidenceError, unique_object

ROOT = Path(__file__).resolve().parents[4]


def _directory(path, case_id):
    info = path.lstat()
    if (not path.is_absolute() or path.name != case_id or path.is_symlink()
            or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise EvidenceError("measurement_path_invalid")


def correlated_error_code(response):
    """Only a complete canonical runtime Error can supply a retained code."""
    try:
        status = response.status_code
        if status == 200:
            return None
        if (response.headers.get("content-type", "").split(";", 1)[0] != "application/json"
                or response.headers.get("content-encoding", "identity") != "identity"):
            return None
        # The original synchronous client already buffered this response. Never
        # fetch, iterate, stringify or persist another byte on the observer path.
        raw = response.content
        if type(raw) is not bytes or not 1 <= len(raw) <= RUNTIME_ACTION_LIMIT:
            return None
        value = json.loads(raw, object_pairs_hook=unique_object,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if type(value) is not dict or set(value) != {"error"}:
            return None
        error = value["error"]
        if type(error) is not dict or set(error) != {
            "code", "message", "correlation_id", "retryable", "details",
        }:
            return None
        code, correlation = error["code"], error["correlation_id"]
        if (type(code) is not str or code not in RUNTIME_ACTION_CODES
                or type(status) is not int or status != RUNTIME_ACTION_CODES[code]
                or type(correlation) is not str or str(UUID(correlation)) != correlation
                or type(error["message"]) is not str
                or error["message"] != code.replace("_", " ").capitalize()
                or type(error["retryable"]) is not bool
                or error["retryable"] != (status in (429, 503))
                or type(error["details"]) is not dict or error["details"]):
            return None
        # This direct runtime client sends no X-Request-ID. The server supplies
        # a transport UUID distinct from the evaluation payload's correlation.
        if response.headers.get_list("x-request-id") != [correlation]:
            return None
        supplied = response.request.headers.get_list("x-request-id")
        if supplied and supplied != [correlation]:
            return None
        return code
    except (EvidenceError, ValueError, TypeError, KeyError, RuntimeError, RecursionError):
        return None


class RuntimeActionObservation:
    """Preserve original failures while writing one bounded private attachment."""

    def __init__(self, action, stage):
        self.case_id = next((name for name, pair in RUNTIME_ACTION_CASES.items()
                             if pair == (action, stage)), None)
        if self.case_id is None:
            raise EvidenceError("measurement_schema_invalid")
        self.expected = ["input"] if stage == "input" else ["input", "output"]
        self.value = {"schema_version": RUNTIME_ACTION_SCHEMA, "rows": []}
        directory = os.environ.get("EVOLUTION_TEST_EVIDENCE_DIR")
        self.directory = Path(directory) if directory is not None else None
        if self.directory is not None:
            _directory(self.directory, self.case_id)
            self.sources = read_catalog(
                self.directory.parents[1] / (self.case_id + ".sources.json"), ROOT,
            )
        else:
            # Direct pytest freezes only these explicit sources before calls.
            catalog = SourceCatalog(ROOT)
            self.sources = {
                name: catalog.load(catalog.path(name))[0] for name in RUNTIME_ACTION_SOURCES
            }
        validate_runtime_action_observation(
            self.value, self.case_id, root=ROOT, sources=self.sources,
        )

    def __enter__(self):
        return self

    def post(self, client, stage, *, json):
        rows = self.value["rows"]
        if (len(rows) >= len(self.expected) or stage != self.expected[len(rows)]
                or rows and rows[-1]["http_status"] != 200):
            raise EvidenceError("measurement_schema_invalid")
        row = {"stage": stage, "http_status": 0, "wire_code": None, "elapsed_ms": 0.0}
        rows.append(row)
        started = monotonic()
        try:
            response = client.post("/v2/evaluations", json=json)
        finally:
            # This finalizer never inspects an exception or extends the client
            # deadline. A transport failure keeps status0 and propagates intact.
            row["elapsed_ms"] = round((monotonic() - started) * 1000, 3)
        row["http_status"] = response.status_code
        row["wire_code"] = correlated_error_code(response)
        return response

    def write(self, *, complete):
        validate_runtime_action_observation(
            self.value, self.case_id, root=ROOT, sources=self.sources, complete=complete,
        )
        if self.directory is None:
            return
        raw = json.dumps(self.value, sort_keys=True, allow_nan=False).encode() + b"\n"
        if not 1 <= len(raw) <= RUNTIME_ACTION_LIMIT:
            raise EvidenceError("measurement_schema_invalid")
        _directory(self.directory, self.case_id)
        directory_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(directory_fd)
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise EvidenceError("measurement_path_invalid")
            descriptor = os.open(
                FILES[self.case_id], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600, dir_fd=directory_fd,
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            os.close(directory_fd)

    def __exit__(self, kind, error, traceback):
        try:
            self.write(complete=kind is None)
        except BaseException:
            # A recording failure cannot replace the first test/transport error.
            # An otherwise successful test still fails closed on missing data.
            if kind is None:
                raise
        return False
