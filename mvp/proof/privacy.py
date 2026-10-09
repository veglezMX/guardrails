"""Volatile acceptance traffic inventory and exhaustive generated-file scans."""
import json
from pathlib import Path

TRAFFIC: set[str] = set()
SECRETS: set[str] = set()
INPUT_FILES: set[Path] = set()
RETAINED_DATABASES: set[Path] = set()


def remember(*texts):
    TRAFFIC.update(text for text in texts if isinstance(text,str) and text)


def unsafe(raw, values):
    return any(candidate in raw for value in values if value for candidate in
               (value.encode(),json.dumps(value,ensure_ascii=True)[1:-1].encode()))


def scan_reports(directory: Path, values):
    scanned=[]
    for path in directory.rglob('*'):
        if path.is_file():
            if unsafe(path.read_bytes(),values):
                raise ValueError('report_privacy_failed')
            scanned.append(path.name)
    if not scanned:
        raise ValueError('report_evidence_missing')
    return scanned


def input_file(path: Path, root: Path):
    return path.resolve() in INPUT_FILES


def retained_database(path: Path):
    return any(path.resolve()==Path(str(db)+suffix) for db in RETAINED_DATABASES for suffix in ('','-wal','-shm'))
