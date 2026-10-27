"""Confined deployed-gateway case journals and fail-closed parent recovery.

The 300-second case allowance is unchanged. Recovery has its own recorded bound
and never retries governance mutations or upgrades a failed case. A caller that
kills the acceptance runner must invoke this finalizer only after proving the
recorded worker and native processes closed.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib.util
import os
import re
import select
import signal
import ssl
import stat
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from .dependencies import canonical, digest
from .registry import EvidenceError

ROUTE_ENV = "EVOLUTION_GATEWAY_ROUTE_OPERATION_ROOT"
OPERATION_ENV = "EVOLUTION_EVALUATOR_OPERATION_ROOT"
RECOVERY_REQUIRED = "gateway_route_recovery_required"
CLEANUP_SECONDS = 90
CLOSURE_SECONDS = 10
FAMILY = re.compile(r"mvp/tests/evolution/e2e/test_cluster_evaluator_gateways\.py::"
    r"test_deployed_gateway_engine_matrix_exact_dispatch_transformation_and_attribution"
    r"\[(observe|warn|redact|block)-(input|output)-(pii|secret|term|topic|json_schema|injection)-(team-a|team-b)\]")
GATE = "import os,sys; f=int(sys.argv[1]); ok=os.read(f,1)==b'G'; os.close(f); ok or sys.exit(125); os.execvpe(sys.argv[2],sys.argv[2:],os.environ)"
MAXIMUM = 4 * 1024 * 1024
_RECOVERY_LATCHED = False


def require(value):
    if not value:
        raise EvidenceError(RECOVERY_REQUIRED)


def remaining(deadline):
    value = deadline - time.monotonic()
    require(0 < value <= CLEANUP_SECONDS + CLOSURE_SECONDS + 1)
    return value


def applies(nodeid):
    return type(nodeid) is str and FAMILY.fullmatch(nodeid) is not None


def recovery_latched(environ=None):
    if _RECOVERY_LATCHED:
        return True
    environ = os.environ if environ is None else environ
    value = environ.get(OPERATION_ENV)
    return bool(value and (Path(value) / 'gateway-route-recovery-required.json').exists())


def latch_recovery(environ=None, *, envelope=None):
    global _RECOVERY_LATCHED
    _RECOVERY_LATCHED = True
    environ = os.environ if environ is None else environ
    value = environ.get(OPERATION_ENV)
    if value:
        path = Path(value)
        directory(path, durable=True)
        target = path / 'gateway-route-recovery-required.json'
        if not target.exists():
            write(target, {'schema': 'evolution-gateway-route-recovery-latch-v1',
                'reason_code': RECOVERY_REQUIRED, 'envelope': envelope, 'gate_pass': False})


def directory(path, *, durable=False):
    path = Path(path)
    info = path.lstat()
    require(path.is_absolute() and path.resolve() == path and stat.S_ISDIR(info.st_mode)
            and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700)
    if durable:
        require(path.is_relative_to(Path.home().resolve() / ".agent-sessions"))
    return info.st_dev, info.st_ino


def read(path, *, maximum=MAXIMUM):
    path = Path(path)
    require(path.is_absolute() and path.resolve() == path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid()
                and before.st_nlink == 1 and not before.st_mode & 0o077
                and 0 < before.st_size <= maximum)
        raw = bytearray()
        while len(raw) <= maximum:
            part = os.read(fd, min(65536, maximum + 1 - len(raw)))
            if not part:
                break
            raw.extend(part)
        def stamp(row):
            return (row.st_dev, row.st_ino, row.st_mode, row.st_uid, row.st_nlink,
                    row.st_size, row.st_mtime_ns, row.st_ctime_ns)
        require(len(raw) == before.st_size and stamp(before) == stamp(os.fstat(fd)) == stamp(path.lstat()))
        return bytes(raw)
    finally:
        os.close(fd)


def document(path):
    from guardrails.evolution.models import strict_json_loads
    return strict_json_loads(read(path), max_bytes=MAXIMUM)


def write(path, value):
    raw = canonical(value) + b"\n"
    require(len(raw) <= MAXIMUM)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    parent = os.open(Path(path).parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)
    return {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest()}


def proc_read(path, maximum, deadline):
    remaining(deadline)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        require(stat.S_ISREG(os.fstat(fd).st_mode))
        raw = bytearray()
        while len(raw) <= maximum:
            remaining(deadline)
            part = os.read(fd, min(65536, maximum + 1 - len(raw)))
            if not part:
                break
            raw.extend(part)
        require(len(raw) <= maximum)
        return bytes(raw)
    finally:
        os.close(fd)


def identity(pid, deadline):
    require(type(pid) is int and 1 < pid < 2**31)
    try:
        base = Path('/proc') / str(pid)
        before = base.lstat()
        require(before.st_uid == os.getuid())
        raw = proc_read(base / 'stat', 8192, deadline)
        fields = raw.rsplit(b') ', 1)[1].split()
        require(raw.startswith(str(pid).encode() + b' (') and len(fields) >= 20)
        command = proc_read(base / 'cmdline', 1024 * 1024, deadline)
        again = proc_read(base / 'stat', 8192, deadline).rsplit(b') ', 1)[1].split()
        require(fields[19] == again[19] and before.st_ino == base.lstat().st_ino)
        return {'pid': pid, 'start': fields[19].decode(), 'group': int(fields[2]),
                'parent': int(fields[1]), 'state': fields[0].decode(),
                'command': hashlib.sha256(command).hexdigest()}
    except FileNotFoundError:
        return None


def children(deadline):
    raw = proc_read(Path(f'/proc/{os.getpid()}/task/{os.getpid()}/children'), 1024 * 1024, deadline)
    values = [int(value) for value in raw.split()]
    require(len(values) <= 4096 and len(values) == len(set(values)))
    return {pid: identity(pid, deadline) for pid in values}


def same_process(actual, expected):
    return actual is not None and isinstance(expected, dict) and (actual['pid'], actual['start']) == (expected.get('pid'), expected.get('start'))


def stop_recorded(row, deadline):
    """Signal only the still-matching start/argv through a bound pidfd."""
    current = identity(row['pid'], deadline)
    if not same_process(current, row):
        return
    require(current['command'] in {row['command'], row.get('gate_command', row['command'])}
            or current['state'] == 'Z')
    fd = os.pidfd_open(row['pid'])
    try:
        again = identity(row['pid'], deadline)
        require(same_process(again, row) and (again['command'] in {row['command'], row.get('gate_command', row['command'])}
                                            or again['state'] == 'Z'))
        if again['state'] != 'Z':
            signal.pidfd_send_signal(fd, signal.SIGKILL)
        poll = select.poll(); poll.register(fd, select.POLLIN)
        require(bool(poll.poll(max(1, int(remaining(deadline) * 1000)))))
        try:
            os.waitpid(row['pid'], os.WNOHANG)
        except ChildProcessError:
            pass
        require(not same_process(identity(row['pid'], deadline), row))
    finally:
        os.close(fd)


def native_records(route_root):
    directory(route_root)
    records = []
    for path in sorted(route_root.glob('native-*-started.json')):
        require(re.fullmatch(r'native-[0-9]{3}-started\.json', path.name))
        row = document(path)
        require(set(row) == {'pid', 'start', 'command', 'gate_command', 'group', 'forward',
                            'argv', 'command_id', 'status'} and row['status'] == 'gate_held'
                and type(row['pid']) is int and 1 < row['pid'] < 2**31
                and type(row['start']) is str and row['start'].isdigit()
                and type(row['forward']) is bool and type(row['group']) is int
                and type(row['argv']) is list and 1 <= len(row['argv']) <= 128
                and all(type(value) is str and '\0' not in value for value in row['argv'])
                and row['command'] == hashlib.sha256(b'\0'.join(value.encode() for value in row['argv']) + b'\0').hexdigest()
                and re.fullmatch('[0-9a-f]{64}', row['gate_command']))
        dispatch = path.with_name(path.name.replace('-started', '-dispatch'))
        if dispatch.exists():
            require(document(dispatch) == {'status': 'may_have_dispatched'})
        records.append(row)
    require(len(records) <= 256 and len({(row['pid'], row['start']) for row in records}) == len(records))
    return records


def close_native(route_root, worker, baseline, deadline, *, final_forward=None):
    """Prove every adopted child is accounted for; retain the selected forward.

    This is a bounded local observation, not a claim about remote exec processes.
    Unknown children and reused identities are never signaled.
    """
    stop_recorded(worker, deadline)
    records = native_records(route_root)
    allowed = []
    for row in records:
        if row['forward']:
            require(row['group'] == row['pid'])
            if final_forward is not None and all(row[key] == final_forward[key] for key in ('pid', 'start', 'command')):
                allowed.append(row)
            elif final_forward is None:
                # Before restoration, published and unpublished forwards remain
                # explicitly owned by B's exact-forward recovery adapter.
                allowed.append(row)
            else:
                stop_recorded(row, deadline)
        else:
            require(row['group'] == worker['group'])
            stop_recorded(row, deadline)
    for _ in range(2):
        observed = children(deadline)
        for pid, row in observed.items():
            if row is None or same_process(row, baseline.get(pid, {})):
                continue
            require(any(same_process(row, item) and row['command'] in {item['command'], item['gate_command']} for item in allowed))
        require(not same_process(identity(worker['pid'], deadline), worker))
        for row in records:
            if not row['forward']:
                require(not same_process(identity(row['pid'], deadline), row))
    return {'worker_closed': True, 'native_nonforward_closed': True,
            'owned_forward_count': len(allowed), 'remote_exec_closure_claimed': False}


def register_governance_attempt(attempt):
    """Bind the exact journal before any governance POST, including pre-stage work."""
    route_root = Path(os.environ[ROUTE_ENV])
    directory(route_root, durable=True)
    envelope_path = route_root.parent / 'envelope.json'
    envelope = document(envelope_path)
    require(envelope['route_root'] == str(route_root)
            and envelope['operation_root'] == str(attempt.parent)
            and str(UUID(attempt.operation)) == attempt.operation
            and attempt.path == attempt.parent / attempt.operation)
    intent = attempt.path / 'intent.json'
    value = document(intent)
    match = FAMILY.fullmatch(envelope['nodeid'])
    require(match is not None and (value['state'], value['stage'], value['engine'], value['tenant_id']) == match.groups()
            and value['operation'] == attempt.operation)
    write(route_root.parent / 'governance.json', {'operation': attempt.operation,
        'intent': {'path': str(intent), 'sha256': digest(intent)}})


def registered_governance(envelope, envelope_path):
    reference = document(envelope_path.parent / 'governance.json')
    operation = reference['operation']
    require(str(UUID(operation)) == operation)
    path = Path(envelope['operation_root']) / operation / 'intent.json'
    require(reference['intent'] == {'path': str(path), 'sha256': digest(path)})
    value = document(path)
    match = FAMILY.fullmatch(envelope['nodeid'])
    require(match is not None and (value['state'], value['stage'], value['engine'], value['tenant_id']) == match.groups())
    return value


def verify_governance_journal(route_intent, operation_root, sources, root):
    """Exactly one operation; unknown management outcomes are never replayed."""
    operation = route_intent['operation']
    require(str(UUID(operation)) == operation)
    path = operation_root / operation
    directory(path)
    intent = document(path / 'intent.json')
    require(intent['schema_version'] == 'evolution-governance-attempt-v1'
            and intent['operation'] == operation and intent['tenant_id'] == route_intent['tenant']
            and intent['original_binding'] == route_intent['original_binding']
            and intent['original_policy'] == route_intent['original_policy'])
    require(all(sources.get(name) == value == digest(root / name)
                for name, value in intent['source_sha256'].items()))
    intents = sorted(path.glob('request-*-intent.json'))
    require(len(intents) <= 16)
    for index, entry in enumerate(intents, 1):
        require(entry.name == f'request-{index:02d}-intent.json')
        request = document(entry)
        returned = document(path / f'request-{index:02d}-returned.json')
        require(request['operation'] == returned['operation'] == operation
                and request['method'] == 'POST'
                and returned['status'] == 'port_returned_expected_status_not_application_proof')
    require(len(tuple(path.glob('request-*-returned.json'))) == len(intents))
    if (path / 'completion.json').exists():
        require(document(path / 'completion.json')['last_request_outcome_unknown'] is False)
    original = intent['original_binding']
    expected = {'tenant_id': original['tenant_id'], 'workload_id': original['workload_id'],
        'policy': original['policy'], 'state': original['state'], 'binding_revision': original['revision'],
        'release_id': original['release_id'], 'previous_revision': original['previous_revision'],
        'actor_id': original['activated_by']}
    restore = path / 'release-restore-original-binding.json'
    if restore.exists():
        handle = document(restore)
        require(handle['schema_version'] == 'evolution-governance-release-handle-v1'
                and handle['status'] == 'acknowledged_not_yet_applied')
        value = handle['handle']
        require(value['operation'] == operation and value['label'] == 'restore-original-binding'
                and value['actor_id'] == intent['actors']['operator']
                and all(value[key] == original[key] for key in ('tenant_id', 'workload_id', 'policy', 'state'))
                and value['binding_revision'] == value['previous_revision'] + 1)
        matrix = document(path / 'release-matrix-release.json')['handle']
        require(matrix['operation'] == operation and matrix['label'] == 'matrix-release'
                and matrix['previous_revision'] == original['revision']
                and matrix['binding_revision'] == original['revision'] + 1
                and value['previous_revision'] == matrix['binding_revision'])
        expected = value
    return expected


def verify_binding_observation(expected, workload, release_result, runtime_ids):
    """Join current desired/applied pointers to one exact applied release."""
    from guardrails.evolution import models
    release = models.Release.model_validate_json(canonical(release_result['release'])).model_dump(mode='json')
    require(workload['tenant_id'] == expected['tenant_id'] and workload['workload_id'] == expected['workload_id']
            and workload['desired_binding']['binding_revision'] == expected['binding_revision']
            and workload['applied_binding']['binding_revision'] == expected['binding_revision']
            and workload['applied_binding']['policy'] == expected['policy'])
    require(release['release_id'] == expected['release_id'] and release['tenant_id'] == expected['tenant_id']
            and release['workload_ids'] == [expected['workload_id']] and release['policy'] == expected['policy']
            and release['state'] == expected['state'] and release['actor_id'] == expected['actor_id']
            and release['expected_revisions'] == {expected['workload_id']: expected['previous_revision'] or 0}
            and release['status'] == 'applied')
    require(len(runtime_ids) == len(set(runtime_ids)) == 2 and type(release_result['receipts']) is list
            and len(release_result['receipts']) <= 4096)
    latest = {}
    for raw in release_result['receipts']:
        receipt = models.RuntimeReceipt.model_validate_json(canonical(raw)).model_dump(mode='json')
        require(receipt['tenant_id'] == expected['tenant_id'] and receipt['workload_id'] == expected['workload_id'])
        if receipt['binding_revision'] != expected['binding_revision']:
            continue
        require(receipt['runtime_id'] in runtime_ids and receipt['digest'] == expected['policy']['digest'])
        old = latest.get(receipt['runtime_id'])
        if old is None or receipt['snapshot_generation'] > old['snapshot_generation']:
            latest[receipt['runtime_id']] = receipt
        elif receipt['snapshot_generation'] == old['snapshot_generation']:
            require(receipt == old)
    require(set(latest) == set(runtime_ids) and all(row['health'] == 'ready' for row in latest.values()))
    return {'verified_original_binding': True,
            **{key: expected[key] for key in ('tenant_id', 'workload_id', 'policy', 'state')},
            'revision': expected['binding_revision'], 'release_id': expected['release_id'],
            'release_sha256': hashlib.sha256(canonical(release)).hexdigest(),
            'workload_sha256': hashlib.sha256(canonical(workload)).hexdigest(),
            'runtime_receipts': [latest[key] for key in sorted(latest)]}


def credential_inputs(envelope, *, deployment=None):
    """Resolve the reviewed renewal generation without launching native commands.

    Seed inventory is immutable history, not the active operator-token authority.
    The deployment lock and generation/file pins make rotation during recovery a
    refusal. Owner authentication here is file-only; native custody belongs to
    the case supervisor and the route helper.
    """
    state = Path(envelope['state'])
    deployment = deployment or load_deployment(envelope)
    require(deployment.state == state)
    with deployment.lock():
        owner_path = state / 'owner.json'; owner = document(owner_path)
        require(owner['profile'] == 'guardrails-evolution' and owner['uid'] == os.getuid()
                and owner['state'] == str(state) and type(owner['owner_id']) is str
                and owner['owner_id'] and re.fullmatch('[0-9a-f]{64}', owner['container_id']))
        access_path = state / 'assembly/access.json'
        inventory_path = state / 'assembly/seed/inventory.json'
        access = document(access_path); historical = document(inventory_path)
        require(access['seed_inventory_file'] == str(inventory_path))
        resolved = deployment.support('credentials').describe_inventory(state / 'assembly', owner, historical)
        inventory = resolved['inventory']
        paths = [owner_path, access_path, inventory_path, Path(access['ca_file'])]
        generation = {'generation': resolved['generation'], 'sha256': resolved['sha256']}
        if resolved['generation'] is not None:
            value = str(UUID(resolved['generation'])); require(value == resolved['generation'])
            base = state / 'assembly/credential-renewals'
            paths.extend((base / 'active.json', base / value / 'journal.json'))
        else:
            require(resolved['sha256'] is None)
        for tenant in ('team-a', 'team-b'):
            paths.append(Path(inventory['tenants'][tenant]['roles']['operator']['token_file']))
        require(all(path.is_relative_to(state) for path in paths))
        pins = {str(path): hashlib.sha256(read(path)).hexdigest() for path in paths}
        if resolved['generation'] is not None:
            require(pins[str(base / value / 'journal.json')] == resolved['sha256']
                    and document(base / 'active.json') == generation)
        return {'access': access, 'inventory': inventory, 'private_inputs': pins,
                'credential_generation': generation}


def binding_verifier(envelope, route_intent, deadline):
    import httpx
    state, root = Path(envelope['state']), Path(envelope['root'])
    credentials = credential_inputs(envelope)
    require(credentials['private_inputs'] == envelope['private_inputs']
            and credentials['credential_generation'] == envelope['credential_generation'])
    expected = verify_governance_journal(route_intent, Path(envelope['operation_root']), envelope['sources'], root)
    access, inventory = credentials['access'], credentials['inventory']
    require(access['seed_inventory_file'] == str(state / 'assembly/seed/inventory.json'))
    tenant = route_intent['tenant']; item = inventory['tenants'][tenant]
    require(item['workload']['workload_id'] == expected['workload_id'])
    runtime_ids = tuple(row['runtime_id'] for row in item['runtimes'].values())
    if 'runtime_ids' in route_intent:
        require(sorted(runtime_ids) == route_intent['runtime_ids'])
    origin = access['management_url']; parsed = urlsplit(origin)
    require(parsed.scheme == 'https' and parsed.hostname == 'localhost' and parsed.port is not None
            and parsed.username is None and parsed.password is None
            and not parsed.path and not parsed.query and not parsed.fragment)
    token_path = Path(item['roles']['operator']['token_file']); ca_path = Path(access['ca_file'])
    require(token_path.is_relative_to(state) and ca_path.is_relative_to(state))
    token = read(token_path, maximum=16384).decode().strip()
    require(token and '\n' not in token and '\r' not in token)
    # No POST, redirect, environment proxy, connection retry or binding repair.
    with httpx.Client(verify=ssl.create_default_context(cafile=str(ca_path)), trust_env=False,
                      follow_redirects=False, timeout=min(5, remaining(deadline))) as client:
        def get(suffix):
            remaining(deadline)
            raw = bytearray()
            with client.stream('GET', origin + '/v2/tenants/' + tenant + suffix,
                    headers={'Authorization': 'Bearer ' + token, 'Accept-Encoding': 'identity'},
                    timeout=min(5, remaining(deadline))) as response:
                require(response.status_code == 200)
                for part in response.iter_bytes():
                    remaining(deadline); raw.extend(part); require(len(raw) <= 2 * 1024 * 1024)
            from guardrails.evolution.models import strict_json_loads
            return strict_json_loads(bytes(raw), max_bytes=2 * 1024 * 1024)
        observed = get('/releases/' + str(UUID(expected['release_id'])))
        workload = get('/workloads/' + expected['workload_id'])
    result = verify_binding_observation(expected, workload, observed, runtime_ids)
    require(credential_inputs(envelope) == credentials)
    source_guard(envelope)
    return result


def source_guard(envelope):
    from .diagnostics import source_snapshot
    from .run import source_files
    root = Path(envelope['root'])
    require(root.is_absolute() and root.resolve() == root
            and source_snapshot(root) == envelope['sources']
            and source_files(root) == envelope['source_inventory'])


def load_deployment(envelope):
    source_guard(envelope)
    root = Path(envelope['root'])
    path = root / 'deploy/evolution/bootstrap.py'
    spec = importlib.util.spec_from_file_location('gateway_route_case_bootstrap', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module.LocalDeployment(root, state=Path(envelope['state']))


def finalize(envelope_path, *, deadline, worker_closed, deployment=None, verifier=None):
    """Reusable after external supervision; no governance mutation or guessing."""
    require(worker_closed is True)
    envelope_path = Path(envelope_path)
    directory(envelope_path.parent, durable=True)
    envelope = document(envelope_path)
    require(envelope['schema'] == 'evolution-gateway-case-envelope-v1')
    source_guard(envelope)
    route_root = Path(envelope['route_root']); require(route_root == envelope_path.parent / 'routes')
    directory(route_root)
    remaining(deadline)
    if not (route_root / 'intent.json').exists():
        # Registration precedes every POST; an absent registration permits only
        # a no-stage failure. Registered pre-stage outcomes still must be known.
        if (envelope_path.parent / 'governance.json').exists():
            governance = registered_governance(envelope, envelope_path)
            synthetic = {'operation': governance['operation'], 'tenant': governance['tenant_id'],
                'original_binding': governance['original_binding'], 'original_policy': governance['original_policy']}
            verify_governance_journal(synthetic, Path(envelope['operation_root']), envelope['sources'], Path(envelope['root']))
            (verifier or (lambda value: binding_verifier(envelope, value, deadline)))(synthetic)
        # A pre-stage failure cannot claim a completed route case. B independently
        # authenticates that no route mutation/forward could have been issued.
        deployment = deployment or load_deployment(envelope)
        module = deployment.support('gateway_routes')
        result = module.restore(deployment, journal_root=route_root, deadline=deadline, worker_closed=True)
        require(result['status'] == 'not_staged')
        source_guard(envelope)
        return {'status': 'not_staged', 'gate_pass': False, 'route': result}
    intent = document(route_root / 'intent.json')
    governance = registered_governance(envelope, envelope_path)
    require(governance['operation'] == intent['operation'])
    expected = verify_governance_journal(intent, Path(envelope['operation_root']), envelope['sources'], Path(envelope['root']))
    del expected  # Authentication precedes any mutable route recovery.
    deployment = deployment or load_deployment(envelope)
    module = deployment.support('gateway_routes')
    module.close_unpublished_forwards(deployment, journal_root=route_root, deadline=deadline)
    callback = verifier or (lambda value: binding_verifier(envelope, value, deadline))
    binding = callback(intent)
    result = module.restore(deployment, journal_root=route_root, deadline=deadline,
        worker_closed=True, verify_binding=callback)
    require(result['status'] == 'restored' and result['operation'] == intent['operation']
            and result['original_spec_restored'] is True and result['created_secret_absent'] is True)
    source_guard(envelope)
    return {'status': 'restored', 'gate_pass': False, 'binding': binding, 'route': result}


class RouteCase:
    def __init__(self, *, case, root, sources, environ):
        require(applies(case.nodeid) and not recovery_latched(environ))
        operation_root = Path(environ.get(OPERATION_ENV, ''))
        directory(operation_root, durable=True)
        state = Path(environ['EVOLUTION_DEPLOYMENT_STATE'])
        directory(state)
        self.root = Path(root)
        self.path = operation_root / ('gateway-case-' + str(uuid4()))
        self.path.mkdir(mode=0o700); directory(self.path, durable=True)
        self.route_root = self.path / 'routes'; self.route_root.mkdir(mode=0o700)
        from .run import source_files
        self.envelope = {'schema': 'evolution-gateway-case-envelope-v1', 'case_id': case.case_id,
            'nodeid': case.nodeid, 'root': str(self.root), 'state': str(state),
            'operation_root': str(operation_root), 'route_root': str(self.route_root), 'sources': sources,
            'source_inventory': source_files(root)}
        credentials = credential_inputs(self.envelope)
        self.envelope.update(private_inputs=credentials['private_inputs'],
                             credential_generation=credentials['credential_generation'])
        require(all(name in sources for name in ('mvp/evolution/gateway_route_case.py',
            'deploy/evolution/gateway_routes.py', 'deploy/evolution/bootstrap.py', 'deploy/evolution/assembly.py')))
        self.envelope_ref = write(self.path / 'envelope.json', self.envelope)
        self.worker = None; self.previous_subreaper = None; self.baseline = {}

    def environment(self):
        return {ROUTE_ENV: str(self.route_root)}

    def spawn(self, command, *, cwd, env):
        deadline = time.monotonic() + CLOSURE_SECONDS
        libc = ctypes.CDLL(None, use_errno=True); previous = ctypes.c_int()
        require(libc.prctl(37, ctypes.byref(previous), 0, 0, 0) == 0)
        require(libc.prctl(36, 1, 0, 0, 0) == 0)
        self.previous_subreaper = previous.value; self.baseline = children(deadline)
        return self._spawn(command, cwd=cwd, env=env, label='worker')

    def _spawn(self, command, *, cwd, env, label):
        read_fd, write_fd = os.pipe(); process = None
        try:
            process = subprocess.Popen([sys.executable, '-B', '-c', GATE, str(read_fd), *command],
                cwd=cwd, env=env, pass_fds=(read_fd,), start_new_session=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            row = identity(process.pid, time.monotonic() + CLOSURE_SECONDS)
            require(row is not None and row['group'] == process.pid)
            row['gate_command'] = row['command']
            row['command'] = hashlib.sha256(b'\0'.join(value.encode() for value in command) + b'\0').hexdigest()
            write(self.path / (label + '-started.json'), row)
            if label == 'worker':
                self.worker = row
            write(self.path / (label + '-dispatch.json'), {'status': 'may_have_dispatched'})
            os.write(write_fd, b'G')
            return process, row
        except BaseException:
            if process is not None:
                process.kill(); process.wait(timeout=2)
            raise
        finally:
            os.close(read_fd); os.close(write_fd)

    def terminate_worker(self, process):
        require(self.worker is not None)
        deadline = time.monotonic() + CLOSURE_SECONDS
        close_native(self.route_root, self.worker, self.baseline, deadline)
        process.wait(timeout=remaining(deadline))

    def abandon_before_worker(self):
        if self.previous_subreaper is not None:
            libc = ctypes.CDLL(None, use_errno=True)
            require(libc.prctl(36, self.previous_subreaper, 0, 0, 0) == 0)
            self.previous_subreaper = None
        write(self.path / 'completion.json', {'schema': 'evolution-gateway-case-parent-completion-v1',
            'status': 'worker_not_dispatched', 'recovery_required': True, 'gate_pass': False})

    def close_worker(self, process):
        require(self.worker is not None)
        deadline = time.monotonic() + CLOSURE_SECONDS
        proof = close_native(self.route_root, self.worker, self.baseline, deadline)
        if process.returncode is None:
            process.wait(timeout=remaining(deadline))
        write(self.path / 'worker-closed.json', {**proof, 'exit_code': process.returncode,
            'reaped': process.returncode is not None})
        return proof

    def finish(self, process, *, passed):
        started = time.monotonic(); cleanup = None; cleanup_row = None
        result = None; closed = False
        try:
            self.close_worker(process)
            deadline = time.monotonic() + CLEANUP_SECONDS
            env = dict(os.environ, PYTHONPATH=str(self.root / 'mvp'), PYTHONDONTWRITEBYTECODE='1')
            env.pop(ROUTE_ENV, None)
            argv = [sys.executable, '-B', '-m', 'evolution.gateway_route_case', 'finalize',
                '--envelope', self.envelope_ref['path'], '--envelope-sha256', self.envelope_ref['sha256'],
                '--deadline', repr(deadline)]
            cleanup, cleanup_row = self._spawn(argv, cwd=self.root, env=env, label='cleanup')
            try:
                cleanup.wait(timeout=remaining(deadline))
            finally:
                # Cleanup's own synchronous commands use its group. Earlier
                # command identities are already proven absent; account by group.
                closure_deadline = time.monotonic() + CLOSURE_SECONDS
                stop_recorded(cleanup_row, closure_deadline)
                records = native_records(self.route_root)
                for row in records:
                    if not row['forward']:
                        require(row['group'] in {self.worker['group'], cleanup_row['group']})
                        stop_recorded(row, closure_deadline)
                if cleanup.returncode is None:
                    cleanup.wait(timeout=remaining(closure_deadline))
                for pid, current in children(closure_deadline).items():
                    if current is None or same_process(current, self.baseline.get(pid, {})):
                        continue
                    require(any(row['forward'] and same_process(current, row)
                                and current['command'] in {row['command'], row['gate_command']} for row in records))
                closed = True
            require(cleanup.returncode == 0)
            result = document(self.path / 'cleanup-result.json')
            source_guard(self.envelope)
            require(result['status'] in {'restored', 'not_staged'})
            forward = result.get('route', {}).get('forward', {}).get('record')
            # Validate adopted forwards after all source-bound restore checks.
            records = native_records(self.route_root)
            for row in records:
                if not row['forward']:
                    require(not same_process(identity(row['pid'], closure_deadline), row))
            for pid, current in children(closure_deadline).items():
                if current is None or same_process(current, self.baseline.get(pid, {})):
                    continue
                require(forward is not None and same_process(current, forward)
                        and current['command'] == forward['command'])
            require(result['status'] == 'restored' or passed is False)
            write(self.path / 'completion.json', {'schema': 'evolution-gateway-case-parent-completion-v1',
                'status': result['status'], 'recovery_required': False,
                'cleanup_ms': round((time.monotonic() - started) * 1000, 3),
                'cleanup_worker_closed': closed, 'case_passed_before_cleanup': passed,
                'cleanup_result': {'path': str(self.path / 'cleanup-result.json'),
                    'sha256': digest(self.path / 'cleanup-result.json')}, 'gate_pass': False})
            return True
        except BaseException as error:
            if not (self.path / 'completion.json').exists():
                write(self.path / 'completion.json', {'schema': 'evolution-gateway-case-parent-completion-v1',
                    'status': 'recovery_required', 'recovery_required': True,
                    'cleanup_ms': round((time.monotonic() - started) * 1000, 3),
                    'cleanup_worker_closed': closed, 'case_passed_before_cleanup': passed, 'gate_pass': False})
            if not isinstance(error, Exception):
                raise
            return False
        finally:
            if self.previous_subreaper is not None:
                libc = ctypes.CDLL(None, use_errno=True)
                if libc.prctl(36, self.previous_subreaper, 0, 0, 0) != 0:
                    raise EvidenceError(RECOVERY_REQUIRED)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['finalize'])
    parser.add_argument('--envelope', type=Path, required=True)
    parser.add_argument('--envelope-sha256', required=True)
    parser.add_argument('--deadline', type=float, required=True)
    args = parser.parse_args(argv)
    try:
        require(hashlib.sha256(read(args.envelope)).hexdigest() == args.envelope_sha256)
        proof = document(args.envelope.parent / 'worker-closed.json')
        require(proof['worker_closed'] is True and proof['native_nonforward_closed'] is True and proof['reaped'] is True)
        result = finalize(args.envelope, deadline=args.deadline, worker_closed=True)
        write(args.envelope.parent / 'cleanup-result.json', result)
        return 0
    except Exception:
        # Arbitrary native/HTTP exception values never enter retained output.
        write(args.envelope.parent / 'cleanup-failed.json', {'reason_code': RECOVERY_REQUIRED, 'gate_pass': False})
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
