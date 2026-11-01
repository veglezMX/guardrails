"""Private finite metadata from original context requests, with no extra calls."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from time import monotonic

from evolution import runtime_context_evidence as evidence
from evolution.diagnostics import SourceCatalog, read_catalog
from evolution.registry import EvidenceError
from runtime_action_observation import correlated_error_code

ROOT = Path(__file__).resolve().parents[4]


def _directory(path, case_id):
    info = path.lstat()
    if (not path.is_absolute() or path.resolve() != path or path.name != case_id or path.is_symlink()
            or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise EvidenceError("measurement_path_invalid")
    return info.st_dev, info.st_ino


class RuntimeContextObservation:
    """Keep original failures primary; persist only after the original scope exits."""

    def __init__(self, case):
        self.case_id = next((name for name, variant in evidence.CASES.items()
                             if variant == case), None)
        if self.case_id is None:
            raise EvidenceError("measurement_schema_invalid")
        self.value = {"schema_version": evidence.SCHEMA, "rows": []}
        self.recording_failed = False
        directory = os.environ.get("EVOLUTION_TEST_EVIDENCE_DIR")
        self.directory = Path(directory) if directory is not None else None
        if self.directory is not None:
            _directory(self.directory, self.case_id)
            self.sources = read_catalog(
                self.directory.parents[1] / (self.case_id + ".sources.json"), ROOT,
            )
        else:
            catalog = SourceCatalog(ROOT)
            self.sources = {name: catalog.load(catalog.path(name))[0] for name in evidence.SOURCES}
        evidence.validate(self.value, self.case_id, root=ROOT, sources=self.sources)

    def __enter__(self):
        return self

    def post(self, client, stage, **options):
        rows = self.value["rows"]
        if (len(rows) >= 2 or stage != ("input", "output")[len(rows)]
                or rows and rows[-1]["http_status"] != 200):
            raise EvidenceError("measurement_schema_invalid")
        row = {"stage": stage, "http_status": 0, "wire_code": None, "elapsed_ms": 0.0}
        rows.append(row)
        started = None
        try:
            started = monotonic()
        except Exception:  # noqa: BLE001 -- Recorder faults cannot prevent the original request.
            self.recording_failed = True
        try:
            response = client.post("/v2/evaluations", **options)
        finally:
            # No inspection of transport errors and no deadline/retry changes.
            try:
                finished = monotonic()
                if started is not None:
                    row["elapsed_ms"] = round((finished - started) * 1000, 3)
            except Exception:  # noqa: BLE001 -- Preserve any active transport exception unchanged.
                self.recording_failed = True
        try:
            row["http_status"] = response.status_code
            row["wire_code"] = correlated_error_code(response)
        except Exception:  # noqa: BLE001 -- Return the original response even if recording fails.
            self.recording_failed = True
        return response

    def write(self, *, complete):
        if self.recording_failed:
            raise EvidenceError("measurement_evidence_invalid")
        evidence.validate(self.value, self.case_id, root=ROOT, sources=self.sources, complete=complete)
        if self.directory is None:
            return
        raw = json.dumps(self.value, sort_keys=True, allow_nan=False).encode() + b"\n"
        if not 1 <= len(raw) <= evidence.MAX_BYTES:
            raise EvidenceError("measurement_schema_invalid")
        identity = _directory(self.directory, self.case_id)
        directory_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(directory_fd)
            if (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700
                    or (info.st_dev, info.st_ino) != identity
                    or _directory(self.directory, self.case_id) != identity):
                raise EvidenceError("measurement_path_invalid")
            descriptor = os.open(evidence.FILENAME,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory_fd)
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
            if kind is None:
                raise
        return False
