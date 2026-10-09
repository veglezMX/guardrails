"""Single-gateway durable, bounded, content-free event delivery queue."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from guardrails.runtime.models import RuntimeAuditEvent
from guardrails.transport import parse_model


class EventSpool:
    def __init__(self, path, *, max_bytes=10 * 1024 * 1024):
        self.path = Path(path)
        self.max_bytes = max_bytes
        self.lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.parent.chmod(0o700)
        fd = os.open(self.path, os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
        os.fsync(fd)
        os.close(fd)
        directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    @staticmethod
    def encode(event):
        raw = json.dumps(event, separators=(',', ':'), sort_keys=True).encode()
        parsed = parse_model(raw, RuntimeAuditEvent)
        if parsed.source != 'connector' or parsed.content is not None:
            raise ValueError('invalid_spool_event')
        return raw + b'\n'

    def append(self, event):
        raw = self.encode(event)
        with self.lock:
            if self.path.stat().st_size + len(raw) > self.max_bytes:
                raise OSError('spool_full')
            with self.path.open('ab') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())

    def batch(self):
        with self.lock, self.path.open('rb') as stream:
            rows = []
            for _ in range(100):
                line = stream.readline(self.max_bytes + 1)
                if not line:
                    break
                # A damaged row is retained and visibly fails replay, never dropped.
                model = parse_model(line, RuntimeAuditEvent)
                event = model.model_dump(mode='json')
                self.encode(event)
                rows.append(event)
            return rows

    def acknowledge(self, event_ids):
        with self.lock:
            rows = self.path.read_bytes().splitlines(keepends=True)
            retained = [row for row in rows if json.loads(row)['event_id'] not in event_ids]
            temporary = self.path.with_suffix('.pending')
            fd = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.writelines(retained)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
