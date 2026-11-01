"""Bounded read-only current checks under a caller's existing deployment lock.

Construct only after authenticating the parent capsule and preflight. There is
no access(), forward repair, image binding, mutation, lock acquisition or retry.
The native executor is mandatory, supplied by the parent, and receives exact
read-only argv with the original absolute deadline. Result retention belongs to
the explicit fixture recorder, outside the family measurement directory.
Preflight valid_until is the last safe admission time: the parent builder must
bind it to actual required TLS/credential expiry minus the unchanged minimum
horizon. A successful TLS handshake alone does not establish that horizon.
"""
from __future__ import annotations

import copy
import fcntl
import hashlib
import math
import os
import re
import stat
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import FunctionType, SimpleNamespace

from evolution import classifier_provenance as provenance
from guardrails.evolution.classifier_contract import identity, parse

from mvp.tests.evolution.e2e import classifier_network_support as network

SCHEMA = 'evolution-classifier-current-check-v1'
SELF = 'mvp/tests/evolution/e2e/classifier_provenance_support.py'
MODULES = {
    __name__: SELF,
    provenance.__name__: 'mvp/evolution/classifier_provenance.py',
    network.__name__: 'mvp/tests/evolution/e2e/classifier_network_support.py',
    'guardrails.evolution.classifier_contract': 'mvp/guardrails/evolution/classifier_contract.py',
    'guardrails.evolution.identity': 'mvp/guardrails/evolution/identity.py',
    'guardrails.evolution.storage': 'mvp/guardrails/evolution/storage.py',
    'guardrails.evolution.deadlines': 'mvp/guardrails/evolution/deadlines.py',
    'guardrails.evolution.models': 'mvp/guardrails/evolution/models.py',
    'evolution.seed': 'mvp/evolution/seed.py',
}
REQUIRED = set(MODULES.values()) | {
    'deploy/evolution/bootstrap.py', 'deploy/evolution/credentials.py',
    'deploy/evolution/assembly.py', 'deploy/evolution/images/node/offline.nft',
}
FORWARDS = {'management', 'runtime', 'postgres', 'mock', 'team-a', 'team-b'}
MANDATORY_PRIVATE = {
    'owner.json', 'prepared.json', 'kubeconfig', 'assembly/access.json',
    'assembly/values.json', 'assembly/ports.json', 'assembly/seed/inventory.json',
    'assembly/private/database-app-url', 'credentials/ca.crt',
    'assembly/credential-renewals/active.json', 'assembly/private/classifier.token',
    'assembly/tls/gr-platform/classifier/ca.crt',
}
BOUNDARY_COMMANDS = {
    ('docker', 'container', 'inspect', 'guardrails-evolution'),
    ('docker', 'exec', 'guardrails-evolution', 'cat', '/sys/fs/cgroup/cpu.max'),
    ('docker', 'exec', 'guardrails-evolution', 'sha256sum', '/usr/local/share/evolution-offline.nft'),
    ('docker', 'exec', 'guardrails-evolution', 'nft', '-j', 'list', 'table', 'inet', 'evolution_offline'),
    ('docker', 'exec', 'guardrails-evolution', 'readlink', '/proc/self/ns/net'),
}


class ProvenanceCheckError(RuntimeError):
    """Finite refusal; raw command, SQL, file or credential errors stay private."""


def require(value, code):
    if not value:
        raise ProvenanceCheckError(code)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def remaining(deadline, maximum=10):
    require(type(deadline) in (int, float) and math.isfinite(deadline), 'current_deadline_required')
    value = deadline - time.monotonic()
    require(value > 0, 'current_deadline_expired')
    return min(maximum, value)


def kernel_read(path, *, deadline, maximum):
    """Only caller-generated /proc paths; no general filesystem authority."""
    remaining(deadline)
    require(type(maximum) is int and 0 < maximum <= 1024 * 1024, 'kernel_bound')
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    try:
        require(stat.S_ISREG(os.fstat(descriptor).st_mode), 'kernel_file')
        blocks = bytearray()
        while len(blocks) <= maximum:
            remaining(deadline)
            part = os.read(descriptor, min(65536, maximum + 1 - len(blocks)))
            if not part:
                break
            blocks.extend(part)
        require(len(blocks) <= maximum, 'kernel_bound')
        remaining(deadline)
        return bytes(blocks)
    finally:
        os.close(descriptor)


def held_lock(state, *, deadline):
    """Observe the original caller's flock; never acquire another lock."""
    path = state / '.lock'
    row = path.lstat()
    require(path.resolve() == path and stat.S_ISREG(row.st_mode) and row.st_uid == os.getuid()
            and row.st_nlink == 1 and stat.S_IMODE(row.st_mode) == 0o600, 'caller_lock_identity')
    descriptors = []
    with os.scandir('/proc/self/fd') as entries:
        for entry in entries:
            require(len(descriptors) < 4096 and entry.name.isdigit(), 'caller_fd_bound')
            descriptors.append(entry.name)
    expected = f'{os.major(row.st_dev):x}:{os.minor(row.st_dev):x}:{row.st_ino}'
    matches = 0
    for name in descriptors:
        remaining(deadline)
        try:
            held = os.stat('/proc/self/fd/' + name)
        except FileNotFoundError:
            continue
        if (held.st_dev, held.st_ino) != (row.st_dev, row.st_ino):
            continue
        raw = kernel_read('/proc/self/fdinfo/' + name, deadline=deadline, maximum=65536).decode('ascii')
        for line in raw.splitlines():
            if not line.startswith('lock:'):
                continue
            fields = line.split()
            require(len(fields) == 9 and fields[2:5] == ['FLOCK', 'ADVISORY', 'WRITE']
                    and fields[5] == str(os.getpid()) and fields[7:] == ['0', 'EOF'], 'caller_lock_missing')
            device = fields[6].split(':')
            require(len(device) == 3 and ':'.join([f'{int(device[0],16):x}', f'{int(device[1],16):x}', str(int(device[2]))]) == expected,
                    'caller_lock_identity')
            matches += 1
    require(matches == 1, 'caller_lock_missing')
    again = path.lstat()
    require((again.st_dev, again.st_ino, again.st_mode, again.st_uid) ==
            (row.st_dev, row.st_ino, row.st_mode, row.st_uid), 'caller_lock_identity')


def process_identity(pid, *, deadline):
    """The original forward identity with finite proc reads and fd inventory."""
    require(type(pid) is int and 0 < pid <= 2**31 - 1, 'forward_pid')
    root = Path('/proc') / str(pid)
    require(root.stat().st_uid == os.getuid(), 'forward_process_owner')
    fields = kernel_read(root / 'stat', deadline=deadline, maximum=65536).decode().rsplit(')', 1)[1].split()
    require(len(fields) >= 20 and fields[0] != 'Z' and fields[19].isdigit(), 'forward_process_state')
    raw = kernel_read(root / 'cmdline', deadline=deadline, maximum=65536)
    require(raw and raw.endswith(b'\0'), 'forward_process_command')
    return {'pid': pid, 'start': fields[19], 'command': digest(raw)}


def forward_listening(record, port, *, deadline):
    require(type(port) is int and 1024 <= port <= 65535, 'forward_port')
    require(process_identity(record['pid'], deadline=deadline) == record, 'forward_identity_changed')
    root = Path('/proc') / str(record['pid'])
    sockets = set()
    count = 0
    with os.scandir(root / 'fd') as entries:
        for entry in entries:
            remaining(deadline)
            count += 1
            require(count <= 4096 and entry.name.isdigit(), 'forward_fd_bound')
            try:
                target = os.readlink(root / 'fd' / entry.name)
            except FileNotFoundError:
                continue
            match = re.fullmatch(r'socket:\[(\d+)\]', target)
            if match:
                sockets.add(match.group(1))
    address = '0100007F:' + format(port, '04X')
    raw = kernel_read(root / 'net/tcp', deadline=deadline, maximum=1024 * 1024).decode('ascii')
    found = any(len(fields) > 9 and fields[1] == address and fields[3] == '0A' and fields[9] in sockets
                for line in raw.splitlines()[1:] if (fields := line.split()))
    require(found and process_identity(record['pid'], deadline=deadline) == record, 'forward_listener_changed')


class BoundedObserver(network.ReadinessObserver):
    """Even original sample_locked deadline resets cannot extend the caller cap."""
    def __init__(self, cluster, *, runtime, deadline, executor):
        self.absolute_deadline = deadline
        self._deadline = deadline
        self.executor = executor
        super().__init__(cluster, runtime=runtime)

    @property
    def deadline(self):
        return min(self._deadline, self.absolute_deadline)

    @deadline.setter
    def deadline(self, value):
        self._deadline = min(value, self.absolute_deadline)

    def _command(self, args, *, maximum=network.MAX_METADATA, timeout=10, input_text=None):
        self._routing()
        require(not any(env.get(key) for env in (os.environ, self.deployment.env)
                for key in ('DOCKER_CONFIG', 'BUILDX_CONFIG')), 'native_environment')
        left = remaining(self.deadline, timeout)
        value = self.executor(tuple(str(x) for x in args), deadline=self.deadline,
                              timeout=left, maximum=maximum, input_text=input_text)
        require(type(value) is str and len(value.encode()) <= maximum, 'native_output_bound')
        remaining(self.deadline)
        return value


def captured_module(raw, path, name):
    """Execute already authenticated bytes, never import a mutable helper path."""
    namespace = {'__name__': name, '__file__': str(path), '__package__': None}
    exec(compile(raw, str(path), 'exec'), namespace)  # noqa: S102 - exact already authenticated original source
    return SimpleNamespace(**namespace)


def actual_subjects(material, entries, *, state, port, deadline, minimum_validity_seconds):
    """Actual IdentityService authentication using an app-only read-only engine."""
    from guardrails.evolution.deadlines import current_deadline
    from guardrails.evolution.identity import IdentityService
    from guardrails.evolution.storage import TenantRepository
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    require(not any(key.startswith('PG') for key in os.environ), 'ambient_database_route')
    url = make_url(material['assembly/private/database-app-url'].decode('ascii').strip())
    require(url.drivername == 'postgresql+psycopg' and url.username == 'evolution_app' and bool(url.password)
            and url.database == 'guardrails_evolution' and url.host in {'localhost', '127.0.0.1'} and url.port == port
            and dict(url.query) == {'sslmode': 'verify-full', 'sslrootcert': str(state / 'credentials/ca.crt')}, 'database_endpoint')
    # libpq reads an immutable in-process CA snapshot, not a mutable original path.
    ca_fd = os.memfd_create('classifier-current-ca', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
    engine = None
    token = current_deadline.set(deadline)
    try:
        ca = material['credentials/ca.crt']
        require(os.write(ca_fd, ca) == len(ca), 'ca_snapshot')
        fcntl.fcntl(ca_fd, fcntl.F_ADD_SEALS, fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
        sealed_url = url.update_query_dict({'sslrootcert': '/proc/self/fd/' + str(ca_fd)})
        require(remaining(deadline) >= 2, 'current_deadline_expired')
        milliseconds = max(1, min(5000, int(remaining(deadline) * 1000)))
        engine = create_engine(sealed_url, hide_parameters=True, pool_size=1, max_overflow=0, pool_timeout=1,
            connect_args={'connect_timeout': 2, 'options': '-c default_transaction_read_only=on -c statement_timeout=' +
                          str(milliseconds) + ' -c lock_timeout=' + str(milliseconds)})
        with engine.connect() as connection:
            row = connection.execute(text("SELECT current_setting('transaction_read_only'), pg_is_in_recovery(), "
                "clock_timestamp(), (SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid())")).one()
            skew = (datetime.now(UTC) - row[2]).total_seconds()
            require(row[0] == 'on' and row[1] is False and row[3] is True and -5 <= skew <= 10, 'database_readonly_tls')
        remaining(deadline)
        repository = TenantRepository(engine)
        service = IdentityService(repository)
        result = []
        for entry in entries:
            remaining(deadline)
            name = Path(entry['file']).relative_to(state).as_posix()
            secret = material[name].decode('ascii').strip()
            who = service.authenticate(secret)
            expiry = provenance.timestamp(who.credential['expires_at'])
            if who.credential['previous_valid_until'] is not None:
                expiry = min(expiry, provenance.timestamp(who.credential['previous_valid_until']))
            require(expiry >= datetime.now(UTC) + timedelta(seconds=minimum_validity_seconds), 'credential_effective_horizon')
            result.append({'scope': who.scope, 'principal_id': who.principal_id,
                'credential_id': who.credential['credential_id'], 'workload_id': who.workload_id,
                'roles': list(who.roles), 'effective_expires_at': expiry.isoformat().replace('+00:00', 'Z')})
        remaining(deadline)
        return sorted(result, key=lambda row: (row['scope'], row['principal_id'], row['credential_id']))
    finally:
        primary = sys.exc_info()[0] is not None
        try:
            if engine is not None:
                engine.dispose()
        except Exception:  # noqa: BLE001 - sanitize infrastructure failures and preserve primary error
            if not primary:
                raise ProvenanceCheckError('database_cleanup_failed') from None
        finally:
            current_deadline.reset(token)
            os.close(ca_fd)


class CurrentChecker:
    """Mandatory adapter for an externally authenticated immutable preflight.

    Constructor snapshots data only. The caller supplies an existing cluster,
    capsule bytes/pin, preflight bytes/pin and native executor; no implicit live
    construction or deadline is available. All receipts/final-worker semantics
    remain the pure ProvenanceVerifier's independent responsibility.
    """
    def __init__(self, cluster, *, source_root, authorization_raw, authorization_sha256,
                 preflight_raw, preflight_sha256, node_boundary_raw, executor):
        self.cluster = cluster
        self.root = Path(source_root)
        self.authorization_raw = bytes(authorization_raw)
        self.authorization_sha256 = authorization_sha256
        self.preflight_raw = bytes(preflight_raw)
        self.preflight_sha256 = preflight_sha256
        self.node_boundary_raw = bytes(node_boundary_raw)
        self.executor = executor

    def _sources(self, sources, deadline):
        require(self.root.is_absolute() and self.root.resolve() == self.root
                and self.cluster.deployment.root == self.root and REQUIRED <= set(sources)
                and len(sources) <= 8192, 'source_catalog_scope')
        material = {}
        total = 0
        for name, expected in sources.items():
            path = Path(name)
            require(not path.is_absolute() and '..' not in path.parts, 'source_catalog_scope')
            raw = network.private_bytes(self.root / path, deadline=deadline, modes=(0o600, 0o644, 0o755))
            total += len(raw)
            require(total <= 64 * 1024 * 1024 and digest(raw) == expected, 'source_changed')
            material[name] = raw
        return material

    def _loaded_sources(self):
        # EvaluatorCluster already imports seed and its identity/storage chain.
        # Refuse a missing/mixed module; never import unknown source to inspect it.
        for name, relative in MODULES.items():
            module = sys.modules.get(name)
            path = Path(getattr(module, '__file__', ''))
            require(path.is_absolute() and path.resolve() == self.root / relative, 'loaded_source_mismatch')

    def _private(self, preflight, deadline):
        state = self.cluster.deployment.state
        pins = preflight['private_files']
        require(MANDATORY_PRIVATE <= set(pins) and len(pins) <= 4096, 'private_inventory_scope')
        material = {}
        total = 0
        for name, expected in pins.items():
            path = Path(name)
            require(not path.is_absolute() and '..' not in path.parts and not name.endswith('.log'), 'private_inventory_scope')
            raw = network.private_bytes(state / path, deadline=deadline,
                modes=(0o600, 0o644) if name.endswith('.crt') else (0o600,))
            total += len(raw)
            require(total <= 16 * 1024 * 1024 and digest(raw) == expected, 'private_file_changed')
            material[name] = raw
        owner = parse(material['owner.json'])
        require(owner == {'profile': 'guardrails-evolution', 'uid': os.getuid(), 'state': str(state),
            'owner_id': preflight['expected']['owner_id'], 'container_id': preflight['expected']['node_id']}, 'current_owner')
        require(digest(material['prepared.json']) == preflight['expected']['prepared_sha256'], 'current_prepared')
        for name, expected in preflight['binding']['files'].items():
            require(pins.get(name) == expected, 'preflight_private_coverage')
        return material, owner

    def _journal(self, material, owner, source, preflight, deadline):
        state = self.cluster.deployment.state
        module = captured_module(source['deploy/evolution/credentials.py'], self.root / 'deploy/evolution/credentials.py',
                                 'classifier_provenance_credentials')
        def captured_read(path, *, maximum=module.MAX_RECORD):
            remaining(deadline)
            name = Path(path).relative_to(state).as_posix()
            require(name in material and len(material[name]) <= maximum, 'credential_file_not_pinned')
            return material[name]
        module.ensure_available.__globals__['private_read'] = captured_read
        journal = module.ensure_available(state / 'assembly', owner)
        expected = preflight['expected']
        require(journal is not None and journal['status'] == 'complete' and journal['generation'] == expected['credential_generation'],
                'credential_generation_changed')
        journal_name = 'assembly/credential-renewals/' + journal['generation'] + '/journal.json'
        require(digest(material[journal_name]) == expected['credential_journal_sha256'], 'credential_journal_changed')
        # The original journal node may be historical; the pure cold/renewal chain
        # separately proves its valid relationship to the current node.
        entries = []
        for subject in expected['subjects']:
            matches = [row for row in journal['entries'] if row['scope'] == subject['scope']
                and row['principal_id'] == subject['principal_id'] and row['receipt']['credential_id'] == subject['credential_id']]
            require(len(matches) == 1, 'credential_subject_missing')
            row = matches[0]
            require(row['roles'] == subject['roles'] and row['workload_id'] == subject['workload_id'], 'credential_subject_scope')
            entries.append(row)
        remaining(deadline)
        return entries

    def _forwards(self, material, preflight, source, deadline):
        assembly = captured_module(source['deploy/evolution/assembly.py'], self.root / 'deploy/evolution/assembly.py',
                                   'classifier_provenance_assembly')
        expected = preflight['forwards']
        require(set(expected) == FORWARDS, 'forward_inventory')
        ports = parse(material['assembly/ports.json'])
        for name in sorted(FORWARDS):
            remaining(deadline)
            row = parse(material['assembly/forward-' + name + '.json'])
            require(set(expected[name]) == {'identity', 'port'} and expected[name]['identity'] == row
                    and expected[name]['port'] == ports[name]
                    and process_identity(row['pid'], deadline=deadline) == row
                    and row['command'] == assembly._forward_digest(self.cluster.deployment, name, ports[name])
                    , 'forward_identity_changed')
            forward_listening(row, ports[name], deadline=deadline)
        remaining(deadline)

    def _boundary(self, preflight, material, source, deadline):
        bootstrap = captured_module(source['deploy/evolution/bootstrap.py'], self.root / 'deploy/evolution/bootstrap.py',
                                    'classifier_provenance_bootstrap')
        prepared = parse(material['prepared.json'], limit=network.MAX_METADATA)
        matches = [row for row in prepared['artifacts'] if row.get('name') == 'kicbase-offline' and row.get('kind') == 'image']
        require(len(matches) == 1, 'node_image_pin')
        ref = matches[0]['source_url'].removeprefix('docker://')
        owner = parse(material['owner.json'])
        def command(args, **kwargs):
            require(tuple(str(x) for x in args) in BOUNDARY_COMMANDS and not kwargs, 'boundary_command_scope')
            raw = self.executor(('/usr/bin/docker', '--host', network.SOCKET, *tuple(args)[1:]), deadline=deadline,
                                timeout=remaining(deadline, 10), maximum=1024 * 1024, input_text=None)
            require(type(raw) is str and len(raw.encode()) <= 1024 * 1024, 'native_output_bound')
            remaining(deadline)
            return 0, raw
        function = bootstrap.LocalDeployment.verify_node_boundary
        def pinned_rules(path):
            require(path == self.root / 'deploy/evolution/images/node/offline.nft', 'boundary_source_scope')
            remaining(deadline)
            return digest(source['deploy/evolution/images/node/offline.nft'])
        globals_copy = dict(function.__globals__, command=command, sha256=pinned_rules)
        call = FunctionType(function.__code__, globals_copy, function.__name__, function.__defaults__, function.__closure__)
        target = SimpleNamespace(root=self.root, owner=lambda: owner, image=lambda name: ref if name == 'kicbase-offline' else None)
        value = call(target)
        require(value == preflight['images']['node_boundary_snapshot'], 'current_node_boundary')
        remaining(deadline)

    def observe_locked(self, *, phase, operation, deadline):
        started = time.monotonic()
        remaining(deadline)
        stop = min(deadline, started + 60)
        try:
            require(callable(self.executor) and self.cluster.profile == 'classifier', 'current_configuration')
            require(digest(self.preflight_raw) == self.preflight_sha256
                    and digest(self.authorization_raw) == self.authorization_sha256, 'parent_pin_changed')
            auth = parse(self.authorization_raw, limit=network.MAX_METADATA)
            preflight = parse(self.preflight_raw, limit=network.MAX_METADATA)
            require(auth['preflight']['sha256'] == self.preflight_sha256, 'preflight_authorization')
            require(provenance.timestamp(auth['created_at']) <= datetime.now(UTC) < provenance.timestamp(auth['expires_at']),
                    'authorization_expired')
            provenance.validate_preflight(preflight, auth, now=datetime.now(UTC))
            require(preflight['identity'] == identity(), 'selected_runtime_identity')
            details = preflight['images']
            require(digest(self.node_boundary_raw) == preflight['node_boundary']['sha256']
                    == preflight['expected']['node_boundary_sha256']
                    and parse(self.node_boundary_raw, limit=network.MAX_METADATA) == details['node_boundary_snapshot'],
                    'node_boundary_receipt')
            require(set(details) == {'runtime', 'native_images', 'node_boundary_snapshot'}
                    and details['runtime'] in {'runtime-1', 'runtime-2'} and type(details['native_images']) is dict
                    and set(details['native_images']) == {'runtime', 'classifier'}, 'native_preflight')
            held_lock(self.cluster.deployment.state, deadline=stop)
            source = self._sources(preflight['sources'], stop)
            self._loaded_sources()
            material, owner = self._private(preflight, stop)
            entries = self._journal(material, owner, source, preflight, stop)
            self._forwards(material, preflight, source, stop)
            observer = BoundedObserver(self.cluster, runtime=details['runtime'], deadline=stop, executor=self.executor)
            observer.bound = copy.deepcopy(preflight['binding'])
            observer.images = copy.deepcopy(details['native_images'])
            observer._routing()
            self._boundary(preflight, material, source, stop)
            sample = observer.sample_locked()
            require(sample['binding_sha256'] == preflight['expected']['binding_sha256']
                    and sample['counter']['epoch'] == preflight['expected']['worker_epoch'], 'worker_epoch_changed')
            subjects = actual_subjects(material, entries, state=self.cluster.deployment.state,
                port=parse(material['assembly/ports.json'])['postgres'], deadline=stop,
                minimum_validity_seconds=preflight['expected']['minimum_validity_seconds'])
            self._forwards(material, preflight, source, stop)
            after_sample = observer.sample_locked()
            require(after_sample['binding_sha256'] == sample['binding_sha256']
                    and after_sample['counter']['epoch'] == sample['counter']['epoch'], 'worker_epoch_changed')
            self._boundary(preflight, material, source, stop)
            require(self._private(preflight, stop)[0] == material, 'private_file_changed')
            self._journal(material, owner, source, preflight, stop)
            require(self._sources(preflight['sources'], stop) == source, 'source_changed')
            self._loaded_sources()
            held_lock(self.cluster.deployment.state, deadline=stop)
            remaining(stop)
            value = {key: copy.deepcopy(preflight['expected'][key]) for key in provenance.CURRENT_FIELDS}
            value.update(schema_version=SCHEMA, preflight_sha256=self.preflight_sha256, phase=phase, operation=operation,
                         subjects=subjects, observed_at=datetime.now(UTC).isoformat().replace('+00:00', 'Z'),
                         elapsed_ms=round((time.monotonic() - started) * 1000, 6))
            provenance.verify_current(preflight['expected'], value, preflight_sha256=self.preflight_sha256,
                                      phase=phase, operation=operation, now=datetime.now(UTC))
            return value
        except Exception:  # noqa: BLE001 - sanitize infrastructure failures and preserve primary error
            raise ProvenanceCheckError('classifier_current_check_failed') from None
