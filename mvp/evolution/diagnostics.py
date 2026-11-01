"""Bounded source-only failure locations; never serialize exception values."""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import stat
from pathlib import Path

from .registry import EvidenceError, unique_object

SCHEMA = "evolution-case-diagnostic-v1"
MAX_BYTES = 65536
MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_CATALOG_BYTES = 4 * 1024 * 1024
MAX_CATALOG_FILES = 10000
MAX_SOURCE_TOTAL = 64 * 1024 * 1024
MAX_EXCEPTIONS = 8
MAX_FRAMES = 8
PHASES = ("setup", "call", "teardown")
TYPE_NAMES = {
    ("builtins", name): name for name in (
        "AssertionError", "ValueError", "TypeError", "KeyError", "IndexError",
        "OSError", "FileNotFoundError", "PermissionError", "TimeoutError",
        "RuntimeError", "Exception", "ExceptionGroup", "BaseExceptionGroup",
    )
} | {
    ("asyncio.exceptions", "CancelledError"): "CancelledError",
    ("_pytest.outcomes", "Failed"): "PytestFailed",
    # Pinned pytest declares Failed.__module__ = "builtins".
    ("builtins", "Failed"): "PytestFailed",
    ("guardrails.evolution.errors", "EvolutionError"): "EvolutionError",
    ("evolution.registry", "EvidenceError"): "EvidenceError",
    ("httpx", "ReadTimeout"): "TransportTimeout",
    ("httpx", "ConnectTimeout"): "TransportTimeout",
    ("httpx", "ConnectError"): "TransportError",
    ("sqlalchemy.exc", "OperationalError"): "DatabaseError",
}
REPOSITORY_LABELS = frozenset({"AssemblyError", "DeploymentError", "NodeIntegrityError", "ImageIdentityError", "ResourceBudgetError"})
TYPE_LABELS = frozenset(TYPE_NAMES.values()) | REPOSITORY_LABELS | {"OtherError"}
ROOTS = frozenset({"mvp", "deploy", "services", "examples", "tests"})


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _read(path, limit, *, private=False):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if (not stat.S_ISREG(before.st_mode) or not 0 <= before.st_size <= limit
                    or private and before.st_size == 0
                    or private and (before.st_uid != os.getuid()
                        or stat.S_IMODE(before.st_mode) != 0o600 or before.st_nlink != 1)):
                raise EvidenceError("diagnostic_evidence_invalid")
            raw = stream.read(limit + 1)
            if (len(raw) > limit or _identity(before) != _identity(os.fstat(stream.fileno()))
                    or _identity(before) != _identity(path.lstat())):
                raise EvidenceError("diagnostic_evidence_invalid")
        return raw
    except (OSError, ValueError):
        raise EvidenceError("diagnostic_evidence_invalid") from None


class SourceCatalog:
    """Read only named repository Python files; names must occur in their AST."""

    def __init__(self, root, expected=None):
        self.root = Path(root).resolve()
        self.expected = expected
        self.cache = {}

    def path(self, filename):
        if type(filename) is not str or len(filename) > 1024:
            raise EvidenceError("diagnostic_source_invalid")
        path = Path(filename)
        if path.is_absolute():
            try:
                path = path.relative_to(self.root)
            except ValueError:
                raise EvidenceError("diagnostic_source_invalid") from None
        parts = path.parts
        if (not parts or parts[0] not in ROOTS or path.suffix != ".py"
                or any(p.startswith(".") or p == "__pycache__" or p.endswith(".egg-info")
                       for p in parts)
                or parts[0] == "tests" and parts[:2] != ("tests", "evolution-browser")
                or path.as_posix() != filename and not Path(filename).is_absolute()):
            raise EvidenceError("diagnostic_source_invalid")
        current = self.root
        for part in parts:
            current /= part
            if current.is_symlink():
                raise EvidenceError("diagnostic_source_invalid")
        name = path.as_posix()
        if self.expected is not None and name not in self.expected:
            raise EvidenceError("diagnostic_source_invalid")
        return name

    def load(self, name):
        raw = _read(self.root / name, MAX_SOURCE_BYTES)
        digest = hashlib.sha256(raw).hexdigest()
        if self.expected is not None and self.expected[name] != digest:
            raise EvidenceError("diagnostic_source_changed")
        if name not in self.cache or self.cache[name][0] != digest:
            try:
                tree = ast.parse(raw)
            except (ValueError, SyntaxError, RecursionError):
                raise EvidenceError("diagnostic_source_invalid") from None
            intervals = [("<module>", 1, max(1, len(raw.splitlines())))]
            special = {ast.Lambda: "<lambda>", ast.ListComp: "<listcomp>",
                       ast.SetComp: "<setcomp>", ast.DictComp: "<dictcomp>",
                       ast.GeneratorExp: "<genexpr>"}
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    intervals.append((node.name, node.lineno, node.end_lineno))
                elif type(node) in special:
                    intervals.append((special[type(node)], node.lineno, node.end_lineno))
            self.cache[name] = digest, intervals
        return self.cache[name]

    def frame(self, filename, function, line):
        name = self.path(filename)
        if (type(function) is not str or len(function) > 128
                or type(line) is not int or not 1 <= line <= 1000000):
            raise EvidenceError("diagnostic_frame_invalid")
        _, intervals = self.load(name)
        if not any(function == f and start <= line <= end for f, start, end in intervals):
            raise EvidenceError("diagnostic_frame_invalid")
        return {"file": name, "function": function, "line": line}



def source_snapshot(root, expected=None):
    """Freeze before pytest imports; gates reuse their already hashed inventory."""
    catalog = SourceCatalog(root)
    if expected is not None:
        result = {name: digest for name, digest in expected.items()
                  if name.endswith(".py")}
    else:
        result = {}
        size = 0
        for base in sorted(ROOTS):
            directory = catalog.root / base
            if base == "tests":
                directory /= "evolution-browser"
            for current, directories, files in os.walk(directory, followlinks=False):
                directories[:] = [name for name in directories if not name.startswith(".")
                                  and name != "__pycache__" and not name.endswith(".egg-info")]
                for filename in sorted(files):
                    if not filename.endswith(".py") or filename.startswith("."):
                        continue
                    name = catalog.path(str(Path(current) / filename))
                    raw = _read(catalog.root / name, MAX_SOURCE_BYTES)
                    size += len(raw)
                    if size > MAX_SOURCE_TOTAL or len(result) >= MAX_CATALOG_FILES:
                        raise EvidenceError("diagnostic_catalog_limit")
                    result[name] = hashlib.sha256(raw).hexdigest()
    validate_catalog(result, catalog.root)
    return result


def validate_catalog(value, root):
    if type(value) is not dict or not 1 <= len(value) <= MAX_CATALOG_FILES:
        raise EvidenceError("diagnostic_catalog_invalid")
    catalog = SourceCatalog(root)
    for name, digest in value.items():
        if (catalog.path(name) != name or type(digest) is not str
                or not re.fullmatch(r"[a-f0-9]{64}", digest)):
            raise EvidenceError("diagnostic_catalog_invalid")


def write_catalog(path, root, value):
    validate_catalog(value, root)
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    if len(raw) > MAX_CATALOG_BYTES:
        raise EvidenceError("diagnostic_catalog_limit")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def read_catalog(path, root):
    raw = _read(path, MAX_CATALOG_BYTES, private=True)
    try:
        value = json.loads(raw, object_pairs_hook=unique_object,
                           parse_constant=lambda _: (_ for _ in ()).throw(
                               EvidenceError("diagnostic_catalog_invalid")))
        validate_catalog(value, root)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise EvidenceError("diagnostic_catalog_invalid") from None
    return value


def _exception_attribute(error, name):
    # Built-in descriptors avoid arbitrary exception __getattribute__ hooks.
    return BaseException.__dict__[name].__get__(error)


def capture(error, phase, catalog):
    pending = [(error, None, "root")]
    seen = set()
    rows = []
    truncated = False
    while pending and len(rows) < MAX_EXCEPTIONS:
        current, parent, relation = pending.pop(0)
        if id(current) in seen:
            truncated = True
            continue
        seen.add(id(current))
        frames = []
        tb = _exception_attribute(current, "__traceback__")
        traversed = 0
        while tb is not None and traversed < 128:
            traversed += 1
            try:
                frame = catalog.frame(tb.tb_frame.f_code.co_filename,
                                      tb.tb_frame.f_code.co_name, tb.tb_lineno)
                frames.append(frame)
            except EvidenceError:
                pass  # External/dynamic source names are never emitted.
            tb = tb.tb_next
        if tb is not None or len(frames) > MAX_FRAMES:
            truncated = True
        kind = type(current)
        module = type.__getattribute__(kind, "__module__")
        name = type.__getattribute__(kind, "__name__")
        label = "OtherError"
        if type(module) is str and type(name) is str:
            label = TYPE_NAMES.get((module, name), "OtherError")
            if name in REPOSITORY_LABELS and frames:
                label = name
        index = len(rows)
        rows.append({"parent": parent, "relation": relation, "type": label,
                     "frames": frames[-MAX_FRAMES:]})
        for name, child_relation in (("__cause__", "cause"), ("__context__", "context")):
            child = _exception_attribute(current, name)
            if child is not None:
                pending.append((child, index, child_relation))
    return {"phase": phase, "exceptions": rows, "truncated": truncated or bool(pending)}


def validate(value, catalog):
    if (type(value) is not dict or set(value) != {"schema_version", "failures"}
            or value["schema_version"] != SCHEMA or type(value["failures"]) is not list
            or not 1 <= len(value["failures"]) <= len(PHASES)):
        raise EvidenceError("diagnostic_schema_invalid")
    phases = set()
    sources = {}
    for failure in value["failures"]:
        if (type(failure) is not dict or set(failure) != {"phase", "exceptions", "truncated"}
                or failure["phase"] not in PHASES or failure["phase"] in phases
                or type(failure["truncated"]) is not bool
                or type(failure["exceptions"]) is not list
                or not 1 <= len(failure["exceptions"]) <= MAX_EXCEPTIONS):
            raise EvidenceError("diagnostic_schema_invalid")
        phases.add(failure["phase"])
        for index, row in enumerate(failure["exceptions"]):
            if (type(row) is not dict or set(row) != {"parent", "relation", "type", "frames"}
                    or type(row["type"]) is not str or row["type"] not in TYPE_LABELS
                    or type(row["frames"]) is not list or len(row["frames"]) > MAX_FRAMES):
                raise EvidenceError("diagnostic_schema_invalid")
            if index == 0:
                if row["parent"] is not None or row["relation"] != "root":
                    raise EvidenceError("diagnostic_schema_invalid")
            elif (type(row["parent"]) is not int or not 0 <= row["parent"] < index
                  or row["relation"] not in ("cause", "context")):
                raise EvidenceError("diagnostic_schema_invalid")
            for frame in row["frames"]:
                if type(frame) is not dict or set(frame) != {"file", "function", "line"}:
                    raise EvidenceError("diagnostic_schema_invalid")
                verified = catalog.frame(frame["file"], frame["function"], frame["line"])
                if verified != frame:
                    raise EvidenceError("diagnostic_schema_invalid")
                sources[frame["file"]] = catalog.load(frame["file"])[0]
    return sources


def sidecar(result_path):
    return result_path.with_suffix(".diagnostic.json")


def write_diagnostic(path, failures, catalog):
    value = {"schema_version": SCHEMA, "failures": failures}
    validate(value, catalog)
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    if len(raw) > MAX_BYTES:
        raise EvidenceError("diagnostic_evidence_invalid")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def read_diagnostic(path, root, expected=None):
    raw = _read(path, MAX_BYTES, private=True)
    try:
        value = json.loads(raw, object_pairs_hook=unique_object,
                           parse_constant=lambda _: (_ for _ in ()).throw(
                               EvidenceError("diagnostic_schema_invalid")))
        sources = validate(value, SourceCatalog(root, expected))
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise EvidenceError("diagnostic_evidence_invalid") from None
    return value, {"sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw),
                   "source_files": sources}


def check_case(destination, case_id, root, result, expected=None):
    path = sidecar(destination / (case_id + ".json"))
    required = {phase for phase in PHASES if "case_" + phase + "_failed" in result["reason_codes"]}
    if not path.exists() and not path.is_symlink():
        if required:
            raise EvidenceError("diagnostic_evidence_missing")
        return None
    value, record = read_diagnostic(path, root, expected)
    if result["status"] != "fail" or {row["phase"] for row in value["failures"]} != required:
        raise EvidenceError("diagnostic_evidence_invalid")
    return record


def collect_diagnostics(destination, root, results, expected):
    allowed = {sidecar(Path(row["case_id"] + ".json")).name for row in results}
    if any(path.name not in allowed for path in destination.glob("*.diagnostic.json")):
        raise EvidenceError("diagnostic_evidence_invalid")
    found = {}
    for row in results:
        record = check_case(destination, row["case_id"], root, row, expected)
        if record is not None:
            found[sidecar(Path(row["case_id"] + ".json")).name] = record
    return found
