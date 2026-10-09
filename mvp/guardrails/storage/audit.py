"""Runtime-owned SQLite audit, closed metadata and idempotent client ingestion."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from ..local_contract import canonical_bytes
from ..runtime.models import RuntimeAuditEvent
from ..transport import ContractProblem, parse_model


class AuditRepository:
    def __init__(self, path, *, busy_timeout_ms=100):
        self.path, self.busy_timeout_ms = Path(path), busy_timeout_ms
        self.writer = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.parent.chmod(0o700)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        connection = self.connect()
        try:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables:
                if 'schema_migrations' not in tables or [r[0] for r in connection.execute('SELECT version FROM schema_migrations')] != [1]:
                    raise ContractProblem('audit_write_failed', 503)
            else:
                connection.executescript('BEGIN IMMEDIATE;\n' + Path(__file__).with_name('runtime-schema.sql').read_text() + '\nCOMMIT;')
            connection.execute('PRAGMA journal_mode=WAL')
            connection.execute('PRAGMA synchronous=FULL')
        finally:
            connection.close()
            self.restrict_files()

    def restrict_files(self):
        for suffix in ('', '-wal', '-shm'):
            try:
                Path(str(self.path) + suffix).chmod(0o600)
            except FileNotFoundError:
                pass

    def connect(self, timeout=None):
        connection = sqlite3.connect(self.path, timeout=(self.busy_timeout_ms / 1000 if timeout is None else timeout), isolation_level=None)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def transaction(self, deadline):
        connection, locked = None, False
        try:
            remaining = min(self.busy_timeout_ms / 1000, max(0, deadline - time.monotonic()))
            locked = self.writer.acquire(timeout=remaining)
            if not locked or time.monotonic() >= deadline:
                raise ContractProblem('audit_write_failed', 503)
            connection = self.connect(min(self.busy_timeout_ms / 1000, max(0, deadline - time.monotonic())))
            connection.execute('BEGIN IMMEDIATE')
            if time.monotonic() >= deadline:
                raise ContractProblem('audit_write_failed', 503)
            yield connection
            if time.monotonic() >= deadline:
                raise ContractProblem('audit_write_failed', 503)
            connection.commit()
        except (OSError, sqlite3.Error):
            raise ContractProblem('audit_write_failed', 503) from None
        finally:
            if connection:
                connection.close()
            if locked:
                self.writer.release()
            self.restrict_files()

    @staticmethod
    def insert(connection, event):
        table = 'decision_audit' if event['source'] == 'runtime' else 'client_events'
        raw = canonical_bytes(event).decode()
        old = connection.execute(f'SELECT document FROM {table} WHERE event_id=?', (event['event_id'],)).fetchone()
        if old:
            if old[0] != raw:
                raise ContractProblem(status=409)
            return
        connection.execute(f'INSERT INTO {table} VALUES (?,?,?,?,?,?,?)',
                           (event['event_id'], event['occurred_at'], event['tenant_id'], event['workload_id'],
                            event['correlation_id'], event['metadata']['guardrail_category'], raw))

    def write_decision(self, decision, evaluation, policy, *, deadline, cache_age):
        event_id = uuid4()
        stored = replace(decision, audit_id=event_id, audit_status='persisted')
        excluded = {'correlation_id', 'context_id', 'tenant_id', 'workload_id', 'source', 'client_config', 'transformed_content'}
        metadata = {key: value for key, value in stored.to_wire().items() if key not in excluded}
        metadata['cache_age_s'] = cache_age
        event = dict(event_id=str(event_id), source='runtime', occurred_at=datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                     tenant_id=evaluation.tenant_id, workload_id=evaluation.workload_id, actor_id=None,
                     event_type='evaluation', correlation_id=str(evaluation.correlation_id), metadata=metadata,
                     content=evaluation.content.to_wire() if policy and policy.retain_content else None)
        validated = parse_model(canonical_bytes(event), RuntimeAuditEvent).model_dump(mode='json')
        with self.transaction(deadline) as connection:
            self.insert(connection, validated)
        return event_id

    def ingest(self, events, identity, *, deadline):
        validated = []
        for event in events:
            model = parse_model(canonical_bytes(event), RuntimeAuditEvent)
            if model.source != 'connector' or (model.tenant_id, model.workload_id) != identity:
                raise ContractProblem('identity_mismatch', 403)
            validated.append(model.model_dump(mode='json'))
        with self.transaction(deadline) as connection:
            for event in validated:
                self.insert(connection, event)
        return [event['event_id'] for event in validated]

    def query(self, query, *, after=None, limit=50, include_content=False):
        if not query.get('tenant_id'):
            raise ContractProblem('identity_mismatch', 403)
        clauses, params = ['tenant_id=?'], [query['tenant_id']]
        for name in ('workload_id', 'correlation_id', 'category'):
            if name in query:
                clauses.append(name + '=?')
                params.append(query[name])
        if after is not None:
            if len(after) != 2:
                raise ContractProblem(status=422)
            clauses.append('(occurred_at,event_id)>(?,?)')
            params.extend(after)
        tables = {'runtime': ('decision_audit',), 'connector': ('client_events',)}.get(query.get('source'), ('decision_audit', 'client_events'))
        items = []
        connection = None
        try:
            connection = self.connect()
            for table in tables:
                rows = connection.execute(f'SELECT document FROM {table} WHERE ' + ' AND '.join(clauses) + ' ORDER BY occurred_at,event_id LIMIT ?', [*params, limit + 1])
                for row in rows:
                    event = json.loads(row[0])
                    if not include_content:
                        event['content'] = None
                    items.append(event)
        except (sqlite3.Error, OSError):
            raise ContractProblem('evaluation_unavailable', 503) from None
        finally:
            if connection:
                connection.close()
        return sorted(items, key=lambda e: (e['occurred_at'], e['event_id']))[:limit + 1]
