"""Observe existing owned access without creating state or restarting forwards.

This fixed production adapter preserves the original assembly/credential semantic
validators through source-local bindings. It does not qualify a model or replace
separate deployment acceptance proofs. No arbitrary caller factory is accepted.
"""
from __future__ import annotations

import fcntl
import hashlib
import math
import os
import re
import selectors
import signal
import stat
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from types import FunctionType, SimpleNamespace

from guardrails.evolution.models import strict_json_loads


class ReadOnlyAccessError(RuntimeError):
    pass


def require(value, code='access_observation_invalid'):
    if not value:
        raise ReadOnlyAccessError(code)


def identity(row):
    return tuple(getattr(row, name) for name in ('st_dev', 'st_ino', 'st_mode', 'st_uid',
        'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns'))


def remaining(deadline):
    require(type(deadline) in (int, float) and math.isfinite(deadline), 'access_observation_deadline')
    value = deadline - time.monotonic()
    require(value > 0, 'access_observation_deadline')
    return value


def directory(path):
    path = Path(path)
    row = path.lstat()
    require(path.is_absolute() and path.resolve(strict=True) == path and stat.S_ISDIR(row.st_mode)
        and row.st_uid == os.getuid() and stat.S_IMODE(row.st_mode) == 0o700,
        'access_observation_directory')
    return path


class Files:
    def __init__(self, state, deadline):
        self.state, self.deadline = directory(state), deadline
        self.state_identity = identity(self.state.lstat())[:4]
        self.saved, self.total = {}, 0

    def read(self, path, *, maximum=1024 * 1024):
        remaining(self.deadline)
        require(identity(directory(self.state).lstat())[:4] == self.state_identity)
        path = Path(path)
        require(path.is_absolute() and path.resolve(strict=True) == path and path.is_relative_to(self.state)
            and type(maximum) is int and 0 < maximum <= 2 * 1024 * 1024)
        parent = directory(path.parent)
        parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            parent_id = identity(os.fstat(parent_fd))[:4]
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
            try:
                before = os.fstat(fd)
                require(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid()
                    and stat.S_IMODE(before.st_mode) == 0o600 and before.st_nlink == 1
                    and (0 <= before.st_size if path == self.state / '.lock' else 0 < before.st_size)
                    and before.st_size <= maximum, 'access_observation_private_file')
                raw = bytearray()
                while len(raw) <= maximum:
                    remaining(self.deadline)
                    part = os.read(fd, min(65536, maximum + 1 - len(raw)))
                    if not part:
                        break
                    raw.extend(part)
                require(len(raw) == before.st_size and identity(before) == identity(os.fstat(fd))
                    == identity(os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False))
                    == identity(path.lstat()) and path.resolve(strict=True) == path,
                    'access_observation_file_changed')
                require(parent_id == identity(parent.lstat())[:4] == identity(os.fstat(parent_fd))[:4])
            finally:
                os.close(fd)
        finally:
            os.close(parent_fd)
        remaining(self.deadline)
        raw = bytes(raw)
        value = (identity(before), hashlib.sha256(raw).hexdigest(), maximum)
        if str(path) in self.saved:
            require(self.saved[str(path)][:2] == value[:2], 'access_observation_file_changed')
        else:
            require(len(self.saved) < 128 and self.total + len(raw) <= 16 * 1024 * 1024,
                    'access_observation_file_bound')
            self.saved[str(path)] = value
            self.total += len(raw)
        return raw

    def json(self, path):
        return strict_json_loads(self.read(path), max_bytes=1024 * 1024)

    def recheck(self):
        for path, (_, _, maximum) in tuple(self.saved.items()):
            self.read(path, maximum=maximum)
        remaining(self.deadline)

    @contextmanager
    def lock(self):
        path = self.state / '.lock'
        self.read(path, maximum=4096)
        fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            before = os.fstat(fd)
            require(identity(before) == identity(path.lstat()) and stat.S_ISREG(before.st_mode)
                and before.st_uid == os.getuid() and before.st_nlink == 1
                and stat.S_IMODE(before.st_mode) == 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            remaining(self.deadline)
            yield
            require(identity(before) == identity(os.fstat(fd)) == identity(path.lstat()))
            remaining(self.deadline)
        finally:
            os.close(fd)


def clone(module, **overrides):
    """Rebind only this invocation's original functions; no global monkeypatch."""
    namespace = dict(vars(module))
    for name, value in tuple(namespace.items()):
        if isinstance(value, FunctionType) and value.__globals__ is vars(module):
            copied = FunctionType(value.__code__, namespace, value.__name__, value.__defaults__, value.__closure__)
            copied.__kwdefaults__ = value.__kwdefaults__
            namespace[name] = copied
    namespace.update(overrides)
    return SimpleNamespace(**namespace)


def forbidden(*args, **kwargs):
    raise ReadOnlyAccessError('access_observation_mutation_forbidden')


def node_identity(deadline):
    """One fixed explicit-daemon inspect with bounded output and owned cleanup."""
    remaining(deadline)
    argv = ['/usr/bin/docker', '--host', 'unix:///var/run/docker.sock', 'container', 'inspect',
        '--format', '{{json .Id}} {{json .State.Running}}', 'guardrails-evolution']
    process = subprocess.Popen(argv, env={'PATH': '/usr/bin:/bin', 'DOCKER_CONFIG': '/proc/self/fd'},
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    raw, observed = bytearray(), None
    primary = None
    try:
        os.set_blocking(process.stdout.fileno(), False)
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while observed is None or selector.get_map():
                allowance = remaining(deadline)
                for key, _ in selector.select(min(.1, allowance)):
                    block = os.read(key.fd, 4097 - len(raw))
                    if block:
                        raw.extend(block)
                        require(len(raw) <= 4096, 'access_observation_native_bound')
                    else:
                        selector.unregister(key.fileobj)
                observed = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        require(observed.si_code == os.CLD_EXITED and observed.si_status == 0, 'access_observation_node_unavailable')
        match = re.fullmatch(rb'"([0-9a-f]{64})" true\n', raw)
        require(match is not None, 'access_observation_node_unavailable')
        remaining(deadline)
        return match[1].decode('ascii')
    except BaseException as error:
        primary = error
        raise
    finally:
        cleanup = None
        try:
            # WNOWAIT leaves the group leader unreaped, preserving PID ownership.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                require(observed is not None, 'access_observation_cleanup_failed')
            process.wait(timeout=5)
        except Exception as error:  # noqa: BLE001 - never mask the original finite refusal
            cleanup = error
        finally:
            try:
                process.stdout.close()
            except Exception as error:  # noqa: BLE001 - retain the primary failure
                cleanup = cleanup or error
        if cleanup is not None:
            if primary is None:
                raise ReadOnlyAccessError('access_observation_cleanup_failed') from None
            primary.add_note('Access observation process cleanup also failed')
        if primary is None:
            remaining(deadline)


def proc_read(path, maximum, deadline):
    remaining(deadline)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        row = os.fstat(fd)
        require(stat.S_ISREG(row.st_mode))
        raw = bytearray()
        while len(raw) <= maximum:
            remaining(deadline)
            part = os.read(fd, min(65536, maximum + 1 - len(raw)))
            if not part:
                break
            raw.extend(part)
        require(len(raw) <= maximum, 'access_observation_proc_bound')
    finally:
        os.close(fd)
    remaining(deadline)
    return bytes(raw)


def process_identity(pid, deadline):
    require(type(pid) is int and 1 < pid <= 2**31 - 1)
    root = Path('/proc') / str(pid)
    before = root.lstat()
    require(stat.S_ISDIR(before.st_mode) and before.st_uid == os.getuid())
    fields = proc_read(root / 'stat', 65536, deadline).decode().rsplit(')', 1)[1].split()
    require(len(fields) > 19 and fields[0] != 'Z', 'access_observation_forward_unavailable')
    command = proc_read(root / 'cmdline', 256 * 1024, deadline)
    require(command and command.endswith(b'\0'))
    require(identity(before)[:4] == identity(root.lstat())[:4])
    return {'pid': pid, 'start': fields[19], 'command': hashlib.sha256(command).hexdigest()}


def forward(assembly, deployment, files, directory_path, name, port):
    remaining(files.deadline)
    saved = files.json(directory_path / ('forward-' + name + '.json'))
    require(set(saved) == {'pid', 'start', 'command'}
        and saved['command'] == assembly._forward_digest(deployment, name, port), 'access_observation_forward_identity')
    require(process_identity(saved['pid'], files.deadline) == saved, 'access_observation_forward_identity')
    root, sockets = Path('/proc') / str(saved['pid']), set()
    with os.scandir(root / 'fd') as rows:
        for index, entry in enumerate(rows):
            remaining(files.deadline)
            require(index < 4096, 'access_observation_proc_bound')
            try:
                target = os.readlink(entry.path)
            except FileNotFoundError:
                continue
            match = re.fullmatch(r'socket:\[(\d+)\]', target)
            if match:
                sockets.add(match[1])
    tcp = proc_read(root / 'net/tcp', 2 * 1024 * 1024, files.deadline)
    address = '0100007F:' + format(port, '04X')
    listening = any(len(fields) > 9 and fields[1] == address and fields[3] == '0A' and fields[9] in sockets
        for line in tcp.decode('ascii').splitlines()[1:] if (fields := line.split()))
    require(listening and process_identity(saved['pid'], files.deadline) == saved,
            'access_observation_forward_unavailable')


def observe(deployment, *, deadline):
    remaining(deadline)
    files = Files(deployment.state, deadline)
    assembly = deployment.support('assembly')
    credentials = deployment.support('credentials')
    owner = files.json(files.state / 'owner.json')
    require(owner.get('profile') == 'guardrails-evolution' and owner.get('state') == str(files.state)
        and owner.get('uid') == os.getuid() and re.fullmatch('[0-9a-f]{64}', owner.get('container_id', ''))
        and re.fullmatch('[0-9a-f]{32}', owner.get('owner_id', '')), 'access_observation_owner')
    def ports(_, location):
        value = files.json(location / 'ports.json')
        require(set(value) == set(assembly.SERVICES) and all(type(port) is int and 1024 <= port <= 65535 for port in value.values())
            and len(set(value.values())) == len(value), 'access_observation_ports')
        return value
    credential_reader = clone(credentials, private_read=files.read, _directory=directory)
    proxy = SimpleNamespace(state=files.state, bin=deployment.bin, kubeconfig=deployment.kubeconfig,
        read_json=files.json, write_json=forbidden, lock=files.lock, owner=lambda: owner,
        support=lambda name: credential_reader if name == 'credentials' else forbidden())
    class BoundedHistoryPath(type(Path())):
        def iterdir(self):
            # Keep the original relocation validator, bounding its enumeration
            # itself rather than checking once before a second unbounded scan.
            with os.scandir(self) as rows:
                for index, entry in enumerate(rows):
                    remaining(deadline)
                    require(index < 32, 'access_observation_relocation_bound')
                    yield Path(entry.path)
    semantic_receipts = clone(assembly, _directory=directory, seed=SimpleNamespace(private_read=files.read))
    observed_assembly = clone(assembly, _directory=directory, _port_record=ports,
        seed=SimpleNamespace(private_read=files.read),
        _relocation_receipt=lambda location, value: semantic_receipts._relocation_receipt(BoundedHistoryPath(location), value),
        _forward=lambda dep, location, name, port: forward(assembly, dep, files, location, name, port))
    history = files.state / 'assembly/management-relocations'
    with files.lock():
        if history.exists() or history.is_symlink():
            directory(history)
            with os.scandir(history) as rows:
                for index, _ in enumerate(rows):
                    remaining(deadline)
                    require(index < 32, 'access_observation_relocation_bound')
        require(node_identity(deadline) == owner['container_id'], 'access_observation_node_identity')
        # Invoke the original semantic flow; its lock is already held here.
        proxy.lock = lambda: _held(files)
        result = observed_assembly.access(proxy)
        snapshot = files.json(files.state / 'assembly/access.json')
        observed_ports = ports(proxy, files.state / 'assembly')
        require(snapshot.get('schema_version') == 'guardrails-evolution-local-access-v1'
            and snapshot.get('profile') in {'deterministic_mock', 'local_model', 'classifier'}
            and set(snapshot.get('tenants', {})) == {'team-a', 'team-b'})
        for name in ('management', 'runtime', 'mock', 'ollama'):
            require(snapshot[name + '_url'] == 'https://localhost:' + str(observed_ports[name]),
                    'access_observation_endpoint_changed')
        for name in ('team-a', 'team-b'):
            require(snapshot['tenants'][name]['gateway_url'] == 'https://localhost:' + str(observed_ports[name]),
                    'access_observation_endpoint_changed')
        files.recheck()
        require(node_identity(deadline) == owner['container_id'], 'access_observation_node_identity')
        remaining(deadline)
        return {**result, 'snapshot': snapshot}


@contextmanager
def _held(files):
    remaining(files.deadline)
    yield
    remaining(files.deadline)
