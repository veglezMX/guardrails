"""Real SQLite failure/restart tests, with every mutation sent through HTTPS."""
import os
import sqlite3
import stat
import time

from proof.management_test_support import PREFIX, envelope, floor, policy


def test_control_restart_retains_policy_history_floor_and_pending_operations(https_management):
    service = https_management
    api = service.api
    seeded = api.seed()
    api.request('PUT', '/v1/governance/floors/organization/org', body=floor(), create=True, status=202)
    snapshot = api.request('GET', '/internal/v1/snapshot', actor='runtime').json()
    service.stop(kill=True)
    api = service.start()
    assert api.request('GET', '/internal/v1/snapshot', actor='runtime').json() == snapshot
    assert api.request('GET', PREFIX + '/policies/pii/versions/v1').json()['version'] == 'v1'
    assert api.request('GET', PREFIX + '/workloads/team-a/binding/history').json()['items'] == [seeded['resource']]
    assert api.request('GET', '/v1/governance/floors/organization/org').json()['require_binding'] is True
    op = api.request('GET', '/v1/operations/' + seeded['operation']['operation_id']).json()
    assert op['status'] == 'pending' and op['applied_generation'] == 0
    assert stat.S_IMODE(service.db.stat().st_mode) == 0o600
    assert stat.S_IMODE(service.db.parent.stat().st_mode) == 0o700
    with sqlite3.connect(service.db) as connection:
        assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []


def test_real_failed_audit_insert_rolls_back_publication_generation_and_operation(https_management):
    service = https_management
    api = service.api
    api.seed()
    before = api.request('GET', '/internal/v1/snapshot', actor='runtime').json()
    with sqlite3.connect(service.db) as connection:
        counts = [connection.execute('SELECT COUNT(*) FROM ' + name).fetchone()[0] for name in ('policies', 'operations', 'management_audit')]
        connection.execute("CREATE TRIGGER fail_audit BEFORE INSERT ON management_audit BEGIN SELECT RAISE(ABORT,'synthetic audit disk failure'); END")
    result = api.request('POST', PREFIX + '/policies', body=policy('v2'), status=503).json()
    assert result['code'] == 'audit_write_failed'
    assert api.request('GET', '/internal/v1/snapshot', actor='runtime').json() == before
    api.request('GET', PREFIX + '/policies/pii/versions/v2', status=404)
    with sqlite3.connect(service.db) as connection:
        assert [connection.execute('SELECT COUNT(*) FROM ' + name).fetchone()[0] for name in ('policies', 'operations', 'management_audit')] == counts
        connection.execute('DROP TRIGGER fail_audit')
    service.stop(kill=True)
    api = service.start()
    created = api.request('POST', PREFIX + '/policies', body=policy('v2'), status=201).json()
    assert created['policy']['publication_ordinal'] == 2
    assert created['operation']['desired_generation'] == before['generation'] + 1


def test_actual_sqlite_writer_lock_is_bounded_and_recovers(https_management):
    api = https_management.api
    api.seed()
    connection = sqlite3.connect(https_management.db)
    connection.execute('BEGIN IMMEDIATE')
    start = time.monotonic()
    try:
        api.request('POST', PREFIX + '/policies', body=policy('v2'), status=503)
    finally:
        connection.rollback()
        connection.close()
    assert time.monotonic() - start < 1.0
    api.request('POST', PREFIX + '/policies', body=policy('v2'), status=201)


def test_immutable_sql_rows_survive_restart_and_applied_receipt_persists(https_management):
    service = https_management
    api = service.api
    seeded = api.seed()
    before = api.request('GET', '/internal/v1/snapshot', actor='runtime').json()
    api.request('POST', '/internal/v1/applied', actor='runtime', body={k: before[k] for k in ('generation', 'digest')})
    for table in ('policies', 'binding_history'):
        with sqlite3.connect(service.db) as connection:
            try:
                connection.execute('DELETE FROM ' + table)
            except sqlite3.IntegrityError:
                pass
            else:
                raise AssertionError('Immutable row was deleted')
    service.stop()
    api = service.start()
    assert api.request('GET', '/internal/v1/snapshot', actor='runtime').json() == before
    assert api.request('GET', '/v1/operations/' + seeded['operation']['operation_id']).json()['status'] == 'applied'
    assert api.request('GET', PREFIX + '/workloads/team-a/binding').json()['applied_revision'] == 1
