"""One owned SQLite store. Configuration, audit and distribution commit together."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from ..local_contract import canonical_bytes
from ..transport import ContractProblem


def now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def document(value):
    return canonical_bytes(value).decode('utf-8')


def read_document(row):
    if row is None:
        raise ContractProblem(status=404)
    return json.loads(row['document'])


AUDIT_METADATA = frozenset({
    'operation_id', 'desired_generation', 'applied_generation', 'policy_id',
    'policy_version', 'policy_digest', 'binding_revision', 'state', 'reason',
    'previous', 'new', 'evidence_kind', 'fixture_run_id',
})


def audit(connection, *, actor_id, tenant_id, event_type, metadata,
          workload_id=None, correlation_id=None, occurred_at=None):
    # Call sites build these values from validated resources, never request/exception text.
    if not set(metadata) <= AUDIT_METADATA:
        raise ValueError('invalid_audit_metadata')
    event = dict(event_id=str(uuid4()), source='management', occurred_at=occurred_at or now(),
                 tenant_id=tenant_id, workload_id=workload_id, actor_id=actor_id,
                 event_type=event_type, correlation_id=correlation_id, metadata=metadata, content=None)
    try:
        connection.execute('INSERT INTO management_audit VALUES (?,?,?,?,?,?)',
                           (event['event_id'], event['occurred_at'], tenant_id, workload_id,
                            correlation_id, document(event)))
    except sqlite3.Error:
        raise ContractProblem('audit_write_failed', 503) from None


@dataclass
class Change:
    connection: sqlite3.Connection
    generation: int
    operation_id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: str = field(default_factory=now)
    metadata: dict = field(default_factory=dict)
    changed: bool = True


class ControlRepository:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.path.parent.chmod(0o700)
            descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
            os.close(descriptor)
            self.path.chmod(0o600)
            self.initialize()
        except (OSError, sqlite3.Error):
            raise ContractProblem('management_unavailable', 503) from None

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=0.1, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA busy_timeout=100')
        return connection

    def initialize(self):
        connection = self.connect()
        try:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables:
                if 'schema_migrations' not in tables or [row[0] for row in connection.execute('SELECT version FROM schema_migrations')] != [1]:
                    raise ContractProblem('management_unavailable', 503)
            else:
                schema = Path(__file__).with_name('schema.sql').read_text()
                connection.executescript('BEGIN IMMEDIATE;\n' + schema + '\nCOMMIT;')
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

    @contextmanager
    def transaction(self, *, write=False):
        connection = None
        try:
            connection = self.connect()
            connection.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield connection
            connection.commit()
        except sqlite3.IntegrityError as error:
            if error.sqlite_errorname in ('SQLITE_CONSTRAINT_UNIQUE', 'SQLITE_CONSTRAINT_PRIMARYKEY', 'SQLITE_CONSTRAINT_FOREIGNKEY'):
                raise ContractProblem(status=409) from None
            raise ContractProblem('management_unavailable', 503) from None
        except (sqlite3.Error, OSError):
            raise ContractProblem('management_unavailable', 503) from None
        finally:
            if connection is not None:
                connection.close()  # rolls back any unfinished transaction
            self.restrict_files()

    @contextmanager
    def mutation(self, actor, tenant_id, event_type, *, workload_id=None, correlation_id=None):
        with self.transaction(write=True) as connection:
            generation = connection.execute('SELECT COALESCE(MAX(desired_generation),0)+1 FROM operations').fetchone()[0]
            change = Change(connection, generation)
            yield change
            if not change.changed:
                return
            metadata = {**change.metadata, 'operation_id': change.operation_id, 'desired_generation': generation}
            audit(connection, actor_id=str(actor.credential_id), tenant_id=tenant_id, workload_id=workload_id,
                  correlation_id=correlation_id, event_type=event_type, metadata=metadata, occurred_at=change.timestamp)
            snapshot = self.build_snapshot(connection, generation, change.timestamp)
            connection.execute('INSERT INTO operations VALUES (?,?,?,?,?,NULL,?,?)',
                               (change.operation_id, generation, str(actor.credential_id), tenant_id,
                                change.timestamp, snapshot['digest'], document(snapshot)))

    @staticmethod
    def build_snapshot(connection, generation, timestamp):
        def docs(sql):
            return [json.loads(row[0]) for row in connection.execute(sql)]
        payload = dict(
            generation=generation, generated_at=timestamp,
            workloads=docs('SELECT document FROM workloads ORDER BY tenant_id,workload_id'),
            credential_hashes=docs("SELECT document FROM credentials WHERE role='workload' AND revoked_at IS NULL ORDER BY credential_id"),
            policies=docs('SELECT document FROM policies ORDER BY tenant_id,policy_id,ordinal'),
            bindings=docs('SELECT document FROM bindings_current ORDER BY tenant_id,workload_id'),
            floors=docs('SELECT document FROM governance_floors ORDER BY scope,scope_id'),
        )
        payload['digest'] = hashlib.sha256(canonical_bytes(payload)).hexdigest()
        return payload

    @staticmethod
    def operation(connection, operation_id):
        row = connection.execute('SELECT * FROM operations WHERE operation_id=?', (operation_id,)).fetchone()
        if row is None:
            raise ContractProblem(status=404)
        applied = connection.execute('SELECT generation FROM runtime_ack WHERE singleton=1').fetchone()
        return dict(operation_id=row['operation_id'], desired_generation=row['desired_generation'],
                    applied_generation=applied[0] if applied else 0,
                    status='applied' if row['applied_at'] else 'pending',
                    committed_at=row['committed_at'], applied_at=row['applied_at'])

    def get_operation(self, operation_id):
        with self.transaction() as connection:
            return self.operation(connection, operation_id)


def require_revision(current, expected, create=False):
    """Expected is a strict positive revision, '*' for creation, or None."""
    if expected is None:
        raise ContractProblem(status=428)
    if create:
        if expected != '*' or current is not None:
            raise ContractProblem(status=409)
    elif current is None or expected != current:
        raise ContractProblem(status=409)
