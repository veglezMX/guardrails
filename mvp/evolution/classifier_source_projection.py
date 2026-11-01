"""One closed source projection for deployed classifier producers and collectors.

Diagnostics retain their original Python-only catalog. Deployed evidence binds
that entire frozen catalog plus the one native policy asset actually consumed by
the current checker. The parent authorization independently pins this projection;
reading a current asset here is not authority to admit a changed policy.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import stat
import time
from pathlib import Path

from .diagnostics import MAX_CATALOG_FILES, validate_catalog
from .registry import EvidenceError

NATIVE_ASSET = "deploy/evolution/images/node/offline.nft"
MAX_ASSET_BYTES = 65536
MAX_INPUT_FILES = 20000


def require(value):
    if not value:
        raise EvidenceError("classifier_source_projection_invalid")


def identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def check_deadline(deadline):
    require(type(deadline) in {int, float} and math.isfinite(deadline)
        and 0 < time.monotonic() < deadline)


def asset_digest(root, *, deadline):
    """Bound a regular owned policy read, including pathname replacement checks."""
    root = Path(root)
    path = root / NATIVE_ASSET
    require(root.is_absolute() and root.resolve() == root and path.resolve() == path)
    check_deadline(deadline)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            require(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid()
                and before.st_nlink == 1 and stat.S_IMODE(before.st_mode) in {0o400, 0o444, 0o600, 0o644}
                and 0 < before.st_size <= MAX_ASSET_BYTES)
            raw = stream.read(MAX_ASSET_BYTES + 1)
            require(len(raw) == before.st_size and time.monotonic() < deadline
                and identity(before) == identity(os.fstat(stream.fileno()))
                and identity(before) == identity(path.lstat()) and path.resolve() == path)
        return hashlib.sha256(raw).hexdigest()
    except (OSError, ValueError):
        raise EvidenceError("classifier_source_projection_invalid") from None


def project_sources(root, sources, *, deadline):
    """Project a Python catalog or whole-gate snapshot to identical evidence pins.

    Every Python entry is retained; there is no family-subset filtering. Other
    whole-gate assets are outside this projection, except the exact native policy
    path. If that path is supplied its pin must match the bounded current read.
    A consumer still compares this complete map with the original observation and
    independently authorized sources; missing/extra Python entries cannot vanish.
    """
    check_deadline(deadline)
    require(type(sources) is dict and 1 <= len(sources) <= MAX_INPUT_FILES)
    snapshot = dict(sources)
    for name, digest in snapshot.items():
        check_deadline(deadline)
        require(type(name) is str and 0 < len(name) <= 1024
            and Path(name).as_posix() == name and not Path(name).is_absolute()
            and all(part not in {".", ".."} for part in name.split("/"))
            and type(digest) is str and re.fullmatch(r"[a-f0-9]{64}", digest) is not None)
    python = {name: digest for name, digest in snapshot.items() if name.endswith(".py")}
    require(1 <= len(python) <= MAX_CATALOG_FILES)
    validate_catalog(python, root)
    native = asset_digest(root, deadline=deadline)
    require(NATIVE_ASSET not in snapshot or snapshot[NATIVE_ASSET] == native)
    require(sources == snapshot)
    check_deadline(deadline)
    return {**python, NATIVE_ASSET: native}
