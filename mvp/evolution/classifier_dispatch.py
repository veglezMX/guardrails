"""Finite readiness-header observations; no requests, model imports or content.

The caller authenticates TLS, ready JSON identity and stable deployed ownership.
A matching epoch and zero delta prove no original child pipe-write attempts in
the bracket. Positive deltas count attempts, never completed inference. Concurrent
traffic invalidates attribution; serialize the bracket under the deployment lock.
"""

import re

EPOCH_HEADER = "x-evolution-classifier-worker-epoch"
ATTEMPTS_HEADER = "x-evolution-classifier-dispatch-attempts"
MAX_ATTEMPTS = 2**63 - 1


class DispatchEvidenceError(ValueError):
    """Finite failure only, with no response contents."""


def _require(condition):
    if not condition:
        raise DispatchEvidenceError("classifier_dispatch_observation_invalid")


def snapshot(header_rows):
    """Read original raw header pairs so duplicates cannot silently coalesce."""
    _require(type(header_rows) in (tuple, list) and len(header_rows) <= 64)
    selected = {}
    for row in header_rows:
        _require(type(row) in (tuple, list) and len(row) == 2)
        name, value = row
        _require(type(name) is bytes and type(value) is bytes and len(name) <= 128)
        key = name.lower()
        if key not in (EPOCH_HEADER.encode("ascii"), ATTEMPTS_HEADER.encode("ascii")):
            continue
        _require(key not in selected and len(value) <= 32)
        selected[key] = value
    _require(set(selected) == {EPOCH_HEADER.encode("ascii"), ATTEMPTS_HEADER.encode("ascii")})
    epoch, count = selected[EPOCH_HEADER.encode("ascii")], selected[ATTEMPTS_HEADER.encode("ascii")]
    _require(re.fullmatch(rb"[a-f0-9]{32}", epoch) is not None)
    _require(re.fullmatch(rb"0|[1-9][0-9]{0,18}", count) is not None)
    attempts = int(count)
    _require(attempts <= MAX_ATTEMPTS)
    return {"epoch": epoch.decode("ascii"), "attempts": attempts}


def delta(before, after):
    for row in (before, after):
        _require(type(row) is dict and set(row) == {"epoch", "attempts"})
        _require(type(row["epoch"]) is str and re.fullmatch(r"[a-f0-9]{32}", row["epoch"]) is not None)
        _require(type(row["attempts"]) is int and 0 <= row["attempts"] <= MAX_ATTEMPTS)
    _require(before["epoch"] == after["epoch"] and after["attempts"] >= before["attempts"])
    return after["attempts"] - before["attempts"]
