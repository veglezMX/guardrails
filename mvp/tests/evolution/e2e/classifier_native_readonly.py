"""Pinned native reads for a previously authenticated classifier preflight.

The constructor copies inputs only. Calls require the caller's original shared
Budget and held deployment lock (the surrounding CurrentChecker verifies the
lock). This is a narrow transport, not a preflight/qualification verifier. It
never repairs access, retries, discovers credentials, or admits arbitrary exec.
No native operation occurs on import. Actual execution belongs to the parent.
"""
from __future__ import annotations

import copy
import hashlib
import math
import os
import re
import selectors
import signal
import stat
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from evolution import classifier_provenance as proof
from evolution import classifier_provenance_io as private_io
from guardrails.evolution import classifier_contract as contract

from mvp.tests.evolution.e2e import classifier_network_support as network

SELF = 'mvp/tests/evolution/e2e/classifier_native_readonly.py'
SOURCE_MODULES = {
    __name__: SELF,
    proof.__name__: 'mvp/evolution/classifier_provenance.py',
    private_io.__name__: 'mvp/evolution/classifier_provenance_io.py',
    contract.__name__: 'mvp/guardrails/evolution/classifier_contract.py',
    network.__name__: 'mvp/tests/evolution/e2e/classifier_network_support.py',
    'evolution.classifier_dispatch': 'mvp/evolution/classifier_dispatch.py',
}
NATIVE_FORMAT = '{"identity":[{{json .Id}},{{json .Name}},{{json .State.Running}},{{json .State.Paused}},{{json .State.Restarting}},{{json .State.Dead}},{{json .Config.Image}}],"ports":{{json .NetworkSettings.Ports}}}'
GET = ('get', 'deployments,replicasets,pods,services,endpointslices.discovery.k8s.io', '-o', 'json')
BOUNDARY = {
    ('container', 'inspect', 'guardrails-evolution'),
    ('exec', 'guardrails-evolution', 'cat', '/sys/fs/cgroup/cpu.max'),
    ('exec', 'guardrails-evolution', 'sha256sum', '/usr/local/share/evolution-offline.nft'),
    ('exec', 'guardrails-evolution', 'nft', '-j', 'list', 'table', 'inet', 'evolution_offline'),
    ('exec', 'guardrails-evolution', 'readlink', '/proc/self/ns/net'),
}
MAX_BINARY = 256 * 1024 * 1024
MAX_OUTPUT = network.MAX_METADATA
CLEANUP_SECONDS = 5
# Constructor capture6 + 2 current checks52 each + 5 budget phases52 = 370.
# Keep a finite margin; the independent 16MiB total output cap is unchanged.
MAX_ATTEMPTS = 512
MAX_RETAINED = 16 * 1024 * 1024


class NativeReadOnlyError(RuntimeError):
    """Finite codes only; native output, credentials and argv stay private."""


def require(value, code):
    if not value:
        raise NativeReadOnlyError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def stamp(row, *, directory=False):
    names = ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid')
    if not directory:
        names += ('st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
    return tuple(getattr(row, name) for name in names)


@contextmanager
def executable(ref, *, owner, check):
    """Hold and hash the actual ELF to be executed, then recheck every identity."""
    descriptors, ancestors = [], []
    try:
        check()
        require(type(ref) is dict and set(ref) == {'path', 'sha256'}, 'native_executable_reference')
        path = Path(ref['path'])
        require(path.is_absolute() and '..' not in path.parts and path.resolve(strict=True) == path,
                'native_executable_path')
        require(type(ref['sha256']) is str and re.fullmatch('[0-9a-f]{64}', ref['sha256']), 'native_executable_reference')
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK)
        descriptors.append(fd)
        current = Path('/')
        for part in path.parts[1:-1]:
            check()
            row = os.fstat(fd)
            require(stat.S_ISDIR(row.st_mode) and row.st_uid in {0, os.getuid()} and not row.st_mode & 0o022,
                    'native_executable_ancestor')
            ancestors.append((current, fd, stamp(row, directory=True)))
            fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            descriptors.append(fd)
            current /= part
        row = os.fstat(fd)
        require(stat.S_ISDIR(row.st_mode) and row.st_uid in {0, os.getuid()} and not row.st_mode & 0o022,
                'native_executable_ancestor')
        ancestors.append((current, fd, stamp(row, directory=True)))
        before = os.stat(path.name, dir_fd=fd, follow_symlinks=False)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == owner and before.st_nlink == 1
                and stat.S_IMODE(before.st_mode) in {0o500, 0o700, 0o555, 0o755}
                and 4 <= before.st_size <= MAX_BINARY, 'native_executable_metadata')
        file_fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        descriptors.append(file_fd)
        def verify():
            check()
            require(stamp(os.fstat(file_fd)) == stamp(before) == stamp(os.stat(path.name, dir_fd=fd, follow_symlinks=False))
                    == stamp(path.lstat()), 'native_executable_changed')
            hashed, size, magic = hashlib.sha256(), 0, b''
            os.lseek(file_fd, 0, os.SEEK_SET)
            while True:
                check()
                raw = os.read(file_fd, 65536)
                if not raw:
                    break
                if size == 0:
                    magic = raw[:4]
                size += len(raw)
                require(size <= MAX_BINARY, 'native_executable_bound')
                hashed.update(raw)
            require(size == before.st_size and magic == b'\x7fELF' and hashed.hexdigest() == ref['sha256'], 'native_executable_hash')
            require(stamp(os.fstat(file_fd)) == stamp(before) == stamp(path.lstat()) and path.resolve(strict=True) == path,
                    'native_executable_changed')
            for ancestor, held, original in ancestors:
                require(stamp(os.fstat(held), directory=True) == original == stamp(ancestor.lstat(), directory=True),
                        'native_executable_ancestor_changed')
            check()
        verify()
        yield file_fd
        verify()
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def reap_owned(process):
    """Never poll/reap before killpg: the unreaped leader reserves its PID/PGID.

    A completed leader can leave children with open pipes. Kill the still-owned
    group before wait(), even in that case. The separate five-second cleanup
    allowance cannot turn an expired operation into successful evidence.
    """
    status = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        require(status is not None, 'native_cleanup_lost_group')
    process.wait(timeout=CLEANUP_SECONDS)


def run_process(argv, *, file_fd, deadline, maximum, input_text, check, evidence):
    """Bound stdin/stdout/stderr incrementally, including child exit and cleanup."""
    process = None
    selector = None
    primary = None
    stdout, total = bytearray(), 0
    evidence.update({'spawned': False, 'stdout': b'', 'stderr': b'', 'truncated': False,
                     'cleanup': 'not_started', 'status': 'incomplete'})
    data = b'' if input_text is None else input_text.encode('utf-8')
    try:
        check()
        env = {'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8', 'PATH': '/usr/bin:/bin',
               'DOCKER_HOST': network.SOCKET, 'DOCKER_CONFIG': '/proc/self/fd'}
        process = subprocess.Popen(argv, executable=f'/proc/self/fd/{file_fd}', pass_fds=(file_fd,),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True, close_fds=True, env=env, cwd='/', shell=False)
        evidence['spawned'] = True
        selector = selectors.DefaultSelector()
        for stream in (process.stdout, process.stderr, process.stdin):
            os.set_blocking(stream.fileno(), False)
        selector.register(process.stdout, selectors.EVENT_READ, 'out')
        selector.register(process.stderr, selectors.EVENT_READ, 'err')
        if data:
            selector.register(process.stdin, selectors.EVENT_WRITE, 'in')
        else:
            process.stdin.close()
        sent = 0
        status = None
        while True:
            check()
            # WNOWAIT is essential: poll()/wait() could release the PGID before cleanup.
            status = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            if status is not None and not selector.get_map():
                break
            for key, _ in selector.select(min(.05, max(0, deadline - time.monotonic()))):
                if key.data == 'in':
                    count = os.write(key.fileobj.fileno(), data[sent:sent + 65536])
                    require(count > 0, 'native_stdin_failed')
                    sent += count
                    if sent == len(data):
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                else:
                    raw = os.read(key.fileobj.fileno(), min(65536, maximum + 1 - total))
                    if not raw:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                        continue
                    room = max(0, maximum - total)
                    label = 'stdout' if key.data == 'out' else 'stderr'
                    evidence[label] += raw[:room]
                    total += len(raw)
                    evidence['truncated'] = total > maximum
                    require(total <= maximum, 'native_output_bound')
                    if key.data == 'out':
                        stdout.extend(raw)
            check()
        require(status.si_code == os.CLD_EXITED and status.si_status == 0 and sent == len(data), 'native_command_failed')
        check()
        result = stdout.decode('utf-8', errors='strict')
    except BaseException as error:  # noqa: BLE001 - sanitize even interrupted native calls after owned cleanup
        evidence['status'] = 'failed'
        primary = error if isinstance(error, NativeReadOnlyError) else NativeReadOnlyError('native_execution_failed')
        raise primary from None
    finally:
        cleanup = False
        if process is not None:
            try:
                reap_owned(process)
            except BaseException:  # noqa: BLE001 - no raw native details, retain primary failure
                cleanup = True
        if selector is not None:
            try:
                selector.close()
            except BaseException:  # noqa: BLE001 - cleanup must not replace primary failure
                cleanup = True
        if process is not None:
            for stream in (process.stdin, process.stdout, process.stderr):
                try:
                    stream.close()
                except BaseException:  # noqa: BLE001 - attempt every owned stream
                    cleanup = True
        evidence['cleanup'] = 'failed' if cleanup else ('completed' if process is not None else 'not_started')
        if cleanup and primary is None:
            evidence['status'] = 'failed'
            raise NativeReadOnlyError('native_cleanup_failed') from None
        if cleanup and primary is not None:
            primary.add_note('native_cleanup_failed')
    check()
    evidence['status'] = 'completed'
    return result


class NativeReadOnlyExecutor:
    def __init__(self, manifest, preflight, budget, source_root):
        # No filesystem/native inspection here; authentication precedes construction.
        self.manifest = copy.deepcopy(manifest)
        self.preflight = copy.deepcopy(preflight)
        self.budget = budget
        self.root = Path(source_root)
        self._attempts = []

    @property
    def attempts(self):
        """Detached private raw output, for the explicit caller's failure recorder.

        Never log these bytes or attach them to public measurements. No stdin,
        command, token or kubeconfig is retained. Records do not prove success.
        """
        return copy.deepcopy(self._attempts)

    def _check(self, deadline):
        require(finite(deadline) and time.monotonic() < deadline, 'native_deadline_expired')
        self.budget.check()

    def _sources(self, deadline):
        require(self.root.is_absolute() and self.root.resolve() == self.root, 'native_source_root')
        sources = self.manifest['sources']
        require(sources == self.preflight['sources'] and set(SOURCE_MODULES.values()) <= set(sources), 'native_source_closure')
        for module_name, name in SOURCE_MODULES.items():
            module = sys.modules.get(module_name)
            require(Path(getattr(module, '__file__', '')).resolve() == self.root / name, 'native_loaded_source')
            raw = network.private_bytes(self.root / name, deadline=deadline, modes=(0o600, 0o644, 0o755))
            require(sha(raw) == sources[name], 'native_source_changed')
        self._check(deadline)

    def _material(self, deadline):
        state = Path(self.manifest['deployment_state'])
        require(state.is_absolute() and state.resolve() == state, 'native_state_path')
        binding = self.preflight['binding']
        pins = binding['files']
        material = {}
        require(type(pins) is dict and 2 <= len(pins) <= 64, 'native_private_pins')
        for name, digest in pins.items():
            require(type(name) is str and not Path(name).is_absolute() and '..' not in Path(name).parts
                    and self.preflight['private_files'].get(name) == digest, 'native_private_pin')
            modes = (0o600, 0o644) if name.startswith('.minikube/') and 'key' not in name else (0o600,)
            raw = network.private_bytes(state / name, deadline=deadline, modes=modes)
            require(sha(raw) == digest, 'native_private_changed')
            material[name] = raw
        require({'owner.json', 'prepared.json', 'kubeconfig'} <= set(material), 'native_private_pins')
        owner = contract.parse(material['owner.json'], limit=MAX_OUTPUT)
        require(owner.get('uid') == os.getuid() and owner.get('state') == str(state)
                and owner.get('profile') == 'guardrails-evolution'
                and owner.get('owner_id') == binding['owner_id'] == self.preflight['expected']['owner_id']
                and owner.get('container_id') == binding['node_id'] == self.preflight['expected']['node_id']
                and re.fullmatch('[0-9a-f]{64}', owner['container_id']), 'native_owner')
        prepared = contract.parse(material['prepared.json'], limit=MAX_OUTPUT)
        require(sha(material['prepared.json']) == self.preflight['expected']['prepared_sha256'], 'native_prepared')
        images = {}
        for name, artifact in (('node', 'kicbase-offline'), ('runtime', 'evolution-service'), ('classifier', 'evolution-classifier')):
            rows = [r for r in prepared['artifacts'] if r.get('name') == artifact and r.get('kind') == 'image']
            require(len(rows) == 1, 'native_prepared_image')
            image = rows[0]['source_url'].removeprefix('docker://')
            require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]*@sha256:[0-9a-f]{64}', image), 'native_prepared_image')
            images[name] = image
        return state, material, owner, images

    def _invoke(self, argv, name, deadline, maximum, input_text=None):
        ref = self.manifest['native_executables'][name]
        retained = sum(len(row['stdout']) + len(row['stderr']) for row in self._attempts)
        require(len(self._attempts) < MAX_ATTEMPTS and retained < MAX_RETAINED, 'native_retention_bound')
        evidence = {'spawned': False, 'stdout': b'', 'stderr': b'', 'truncated': False,
                    'cleanup': 'not_started', 'status': 'incomplete'}
        self._attempts.append(evidence)
        try:
            with executable(ref, owner=0 if name == 'docker' else os.getuid(), check=lambda: self._check(deadline)) as fd:
                return run_process(argv, file_fd=fd, deadline=deadline, maximum=min(maximum, MAX_RETAINED - retained),
                                   input_text=input_text, check=lambda: self._check(deadline), evidence=evidence)
        except BaseException:
            evidence['status'] = 'failed'
            raise

    def _native(self, owner, images, deadline):
        # Reuse the exact existing response validator, with only its command port injected.
        observer = network.ReadinessObserver.__new__(network.ReadinessObserver)
        observer._command = lambda args: self._invoke(('/usr/bin/docker', *args[1:]), 'docker', deadline, MAX_OUTPUT)
        return observer._native(owner, images)

    def _validate_kube(self, argv, input_text, state, material, owner, images, deadline):
        expected_prefix = (str(state / 'bin/kubectl'), '--kubeconfig', '/dev/stdin', '--context',
                           'guardrails-evolution', '--namespace', 'gr-platform', '--request-timeout=10s')
        require(argv[:8] == expected_prefix, 'native_kube_argv')
        suffix = argv[8:]
        bound = self.preflight['binding']
        runtime = self.preflight['images']['runtime']
        require(runtime in ('runtime-1', 'runtime-2'), 'native_runtime')
        settings = {'address': bound['service']['address'], 'identity': self.preflight['identity'],
                    'token_sha256': bound['files']['assembly/private/classifier.token'],
                    'ca_sha256': bound['files']['assembly/tls/gr-platform/classifier/ca.crt']}
        ready = ('exec', bound['pods'][runtime]['pod_name'], '-c', 'runtime', '--', 'python', '-B', '-c',
                 network.READY_SCRIPT, contract.canonical(settings).decode())
        require(suffix in (GET, ready), 'native_kube_argv')
        server = self._native(owner, images, deadline)
        require(server == bound['api_server'], 'native_api_changed')
        static, pins, _ = network.static_kubeconfig(material['kubeconfig'], server=server, state=state, deadline=deadline)
        require(type(input_text) is str and input_text == static and all(bound['files'].get(k) == v for k, v in pins.items()),
                'native_static_input')
        # No credential or discovery-cache reads through ambient HOME/config paths.
        actual = (*expected_prefix, '--cache-dir=/proc/self/fd', *suffix)
        return actual, suffix == GET

    def __call__(self, argv, *, deadline, timeout, maximum, input_text):
        try:
            require(isinstance(self.budget, private_io.Budget) and finite(deadline) and finite(timeout)
                    and 0 < timeout <= 120 and type(maximum) is int and 0 < maximum <= MAX_OUTPUT,
                    'native_call_limits')
            require(type(argv) is tuple and 0 < len(argv) <= 32 and all(type(x) is str and '\0' not in x for x in argv)
                    and sum(map(len, argv)) <= 32768 and (input_text is None or
                    (type(input_text) is str and len(input_text.encode()) <= MAX_OUTPUT)), 'native_call_shape')
            manifest_deadline = self.manifest['deadline_monotonic']
            require(finite(manifest_deadline) and deadline <= manifest_deadline, 'native_deadline_extension')
            limit = min(deadline, self.budget.deadline, time.monotonic() + timeout)
            with self.budget.cap(limit):
                self._check(limit)
                self._sources(limit)
                native = self.manifest['native_executables']
                require(set(native) == {'docker', 'kubectl'} and native['docker']['path'] == '/usr/bin/docker'
                        and native['kubectl']['path'] == self.manifest['deployment_state'] + '/bin/kubectl', 'native_executable_scope')
                state, material, owner, images = self._material(limit)
                formatted = ('container', 'inspect', owner['container_id'], '--format', NATIVE_FORMAT)
                if argv[0] in ('docker', '/usr/bin/docker'):
                    require(input_text is None and argv[1:3] == ('--host', network.SOCKET), 'native_docker_argv')
                    suffix = argv[3:]
                    require(suffix in BOUNDARY or suffix == formatted, 'native_docker_argv')
                    # Every named boundary read remains tied to the exact preflight node.
                    server = self._native(owner, images, limit)
                    require(server == self.preflight['binding']['api_server'], 'native_api_changed')
                    result = self._invoke(('/usr/bin/docker', *argv[1:]), 'docker', limit, maximum)
                else:
                    actual, is_get = self._validate_kube(argv, input_text, state, material, owner, images, limit)
                    result = self._invoke(actual, 'kubectl', limit, maximum, input_text)
                    if is_get:
                        objects, _ = network.project_objects(contract.parse(result.encode(), limit=MAX_OUTPUT), owner['owner_id'], images,
                                                             self.preflight['images']['runtime'])
                        require(all(objects[k] == self.preflight['binding'][k] for k in ('service', 'pods')), 'native_objects_changed')
                require(self._material(limit)[1] == material, 'native_private_changed')
                self._sources(limit)
                self._check(limit)
                return result
        except NativeReadOnlyError:
            raise
        except Exception:  # noqa: BLE001 - private metadata/OS exceptions remain finite
            raise NativeReadOnlyError('native_readonly_refused') from None
