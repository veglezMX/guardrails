"""Existing-only deployment access with native/kernel boundaries synthesized."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from evolution.dependencies import ROOT as DEPENDENCY_ROOT

ROOT = Path(__file__).resolve().parents[4]


def load(root, name):
    spec = importlib.util.spec_from_file_location('readonly_test_' + name, root / 'deploy/evolution' / (name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


@pytest.fixture
def subject():
    return load(ROOT, 'readonly_access')


def write(path, value):
    raw = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True).encode()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_bytes(raw)
    path.chmod(0o600)


@pytest.fixture
def fixture(subject, tmp_path, monkeypatch):
    assembly, credentials = load(DEPENDENCY_ROOT, 'assembly'), load(DEPENDENCY_ROOT, 'credentials')
    root = tmp_path / 'state'; root.mkdir(mode=0o700)
    where = root / 'assembly'; where.mkdir(mode=0o700)
    owner = {'profile': 'guardrails-evolution', 'uid': os.getuid(), 'state': str(root),
             'owner_id': 'a' * 32, 'container_id': 'b' * 64}
    write(root / 'owner.json', owner)
    write(root / '.lock', b'')
    write(where / 'owner.json', {key: owner[key] for key in ('uid', 'state', 'owner_id', 'container_id')})
    ports = {name: 18000 + index for index, name in enumerate(assembly.SERVICES)}
    write(where / 'ports.json', ports)
    access = {'schema_version': 'guardrails-evolution-local-access-v1', 'profile': 'classifier',
        **{name + '_url': 'https://localhost:' + str(ports[name]) for name in ('management', 'runtime', 'mock', 'ollama')},
        'tenants': {name: {'gateway_url': 'https://localhost:' + str(ports[name])} for name in ('team-a', 'team-b')}}
    write(where / 'access.json', access)
    deployment = SimpleNamespace(state=root, bin=root / 'bin', kubeconfig=root / 'kubeconfig',
        support=lambda name: {'assembly': assembly, 'credentials': credentials}[name])
    processes, native = {}, []
    for index, (name, port) in enumerate(ports.items(), 100):
        record = {'pid': index, 'start': '123', 'command': assembly._forward_digest(deployment, name, port)}
        processes[index] = (record, port)
        write(where / ('forward-' + name + '.json'), record)
    def node(deadline):
        native.append(deadline)
        return owner['container_id']
    monkeypatch.setattr(subject, 'node_identity', node)
    monkeypatch.setattr(subject, 'process_identity', lambda pid, deadline: dict(processes[pid][0]))
    def proc(path, maximum, deadline):
        assert path.name == 'tcp'
        pid = int(path.parts[2]); port = processes[pid][1]
        return ('header\n0 0100007F:' + format(port, '04X') + ' 00000000:0000 0A 0 0 0 0 0 987\n').encode()
    monkeypatch.setattr(subject, 'proc_read', proc)
    original_scandir, original_link = os.scandir, os.readlink
    @contextmanager
    def scan(path):
        if str(path).startswith('/proc/'):
            yield iter([SimpleNamespace(path=str(path) + '/1')])
        else:
            with original_scandir(path) as value:
                yield value
    def link(path, *args, **kwargs):
        if str(path).startswith('/proc/'):
            return 'socket:[987]'
        return original_link(path, *args, **kwargs)
    monkeypatch.setattr(subject.os, 'scandir', scan)
    monkeypatch.setattr(subject.os, 'readlink', link)
    def forbidden(*args, **kwargs):
        raise AssertionError('read-only access attempted a mutation')
    monkeypatch.setattr(assembly, '_forward', forbidden)
    monkeypatch.setattr(assembly.socket, 'socket', forbidden)
    monkeypatch.setattr(assembly.subprocess, 'Popen', forbidden)
    return SimpleNamespace(subject=subject, deployment=deployment, root=root, where=where,
        owner=owner, ports=ports, access=access, assembly=assembly, credentials=credentials,
        processes=processes, native=native, deadline=subject.time.monotonic() + 30)


def snapshot(root):
    return {str(path.relative_to(root)): (path.stat().st_mode, hashlib.sha256(path.read_bytes()).hexdigest())
            for path in root.rglob('*') if path.is_file()}


def test_original_access_semantics_run_without_repair_or_new_files(fixture):
    f = fixture; before = snapshot(f.root)
    result = f.subject.observe(f.deployment, deadline=f.deadline)
    assert result == {'status': 'available', 'access_file': str(f.where / 'access.json'),
        'management_url': f.access['management_url'],
        'gateways': {name: row['gateway_url'] for name, row in f.access['tenants'].items()},
        'snapshot': f.access}
    assert f.native == [f.deadline, f.deadline]
    assert snapshot(f.root) == before


@pytest.mark.parametrize('name', ['.lock', 'owner.json', 'assembly/owner.json', 'assembly/ports.json',
    'assembly/access.json', 'assembly/forward-postgres.json', 'assembly/forward-management.json',
    'assembly/forward-runtime.json', 'assembly/forward-mock.json', 'assembly/forward-team-a.json',
    'assembly/forward-team-b.json'])
def test_missing_owned_state_or_required_forward_is_never_recreated(fixture, name):
    f = fixture; (f.root / name).unlink(); before = snapshot(f.root)
    with pytest.raises((OSError, RuntimeError)):
        f.subject.observe(f.deployment, deadline=f.deadline)
    assert snapshot(f.root) == before and not (f.root / name).exists()


@pytest.mark.parametrize('fault', ['symlink', 'public', 'hardlink', 'fifo', 'directory', 'oversize'])
def test_owned_manifest_rejects_unsafe_file_before_native(fixture, fault):
    f = fixture; target = f.root / 'owner.json'; saved = target.read_bytes(); target.unlink()
    if fault == 'symlink':
        write(f.root / 'other', saved); target.symlink_to(f.root / 'other')
    elif fault == 'public':
        write(target, saved); target.chmod(0o644)
    elif fault == 'hardlink':
        write(f.root / 'other', saved); os.link(f.root / 'other', target)
    elif fault == 'fifo':
        os.mkfifo(target, 0o600)
    elif fault == 'directory':
        target.mkdir(mode=0o700)
    else:
        write(target, b' ' * (1024 * 1024 + 1))
    with pytest.raises((OSError, RuntimeError)):
        f.subject.observe(f.deployment, deadline=f.deadline)
    assert f.native == []


@pytest.mark.parametrize('field', ['pid', 'start', 'command'])
def test_forward_process_identity_and_original_argv_are_required(fixture, field):
    f = fixture; path = f.where / 'forward-runtime.json'; value = json.loads(path.read_bytes())
    value[field] = value[field] + 1 if field == 'pid' else ('9' if field == 'start' else '0' * 64)
    write(path, value)
    with pytest.raises((KeyError, RuntimeError)):
        f.subject.observe(f.deployment, deadline=f.deadline)


@pytest.mark.parametrize('fault', ['socket_inode', 'address', 'state', 'lost_after'])
def test_exact_owned_listener_is_required_before_reuse(fixture, monkeypatch, fault):
    f = fixture; original = f.subject.proc_read
    if fault == 'lost_after':
        calls = {}
        def process(pid, deadline):
            calls[pid] = calls.get(pid, 0) + 1
            return {**f.processes[pid][0], 'start': '999' if calls[pid] > 1 else '123'}
        monkeypatch.setattr(f.subject, 'process_identity', process)
    else:
        def read(*args):
            raw = original(*args)
            return raw.replace({'socket_inode': b'987', 'address': b'0100007F', 'state': b' 0A '}[fault],
                {'socket_inode': b'123', 'address': b'00000000', 'state': b' 01 '}[fault])
        monkeypatch.setattr(f.subject, 'proc_read', read)
    with pytest.raises(RuntimeError, match='access_observation_forward_unavailable'):
        f.subject.observe(f.deployment, deadline=f.deadline)


@pytest.mark.parametrize('field', ['management_url', 'runtime_url', 'mock_url', 'ollama_url', 'team-a', 'team-b'])
def test_access_urls_must_select_the_validated_existing_ports(fixture, field):
    f = fixture; value = f.access
    if field.startswith('team-'):
        value['tenants'][field]['gateway_url'] = 'https://localhost:65500'
    else:
        value[field] = 'https://localhost:65500'
    write(f.where / 'access.json', value)
    with pytest.raises(RuntimeError, match='access_observation_endpoint_changed'):
        f.subject.observe(f.deployment, deadline=f.deadline)


@pytest.mark.parametrize('fault', ['pending', 'invalid_active', 'bad_directory'])
def test_original_credential_generation_refusals_remain_effective(fixture, fault):
    f = fixture; root = f.where / 'credential-renewals'; root.mkdir(mode=0o700)
    if fault == 'bad_directory':
        root.chmod(0o755)
    else:
        write(root / ('pending.json' if fault == 'pending' else 'active.json'), {})
    with pytest.raises(RuntimeError):
        f.subject.observe(f.deployment, deadline=f.deadline)


def test_relocation_directory_is_bounded_before_original_semantic_validation(fixture):
    f = fixture; root = f.where / 'management-relocations'; root.mkdir(mode=0o700)
    for index in range(33):
        write(root / str(index), {})
    with pytest.raises(RuntimeError, match='access_observation_relocation_bound'):
        f.subject.observe(f.deployment, deadline=f.deadline)
    assert f.native == []


@pytest.mark.parametrize('fault', ['expired', 'missing', 'nan', 'boolean'])
def test_no_native_observation_or_fresh_budget_for_bad_original_deadline(fixture, fault):
    f = fixture
    deadline = {'expired': 1, 'missing': None, 'nan': float('nan'), 'boolean': True}[fault]
    with pytest.raises(RuntimeError, match='access_observation_deadline'):
        f.subject.observe(f.deployment, deadline=deadline)
    assert f.native == []


def test_native_delay_cannot_gain_a_new_deadline(fixture, monkeypatch):
    f = fixture; now = [0.]
    monkeypatch.setattr(f.subject.time, 'monotonic', lambda: now[0])
    def node(deadline):
        now[0] = deadline
        return f.owner['container_id']
    monkeypatch.setattr(f.subject, 'node_identity', node)
    with pytest.raises(RuntimeError, match='access_observation_deadline'):
        f.subject.observe(f.deployment, deadline=30)


@pytest.mark.parametrize('when', ['first', 'last'])
def test_current_native_node_identity_must_match_original_owner(fixture, monkeypatch, when):
    f = fixture; calls = []
    def node(deadline):
        calls.append(deadline)
        return 'e' * 64 if (when == 'first' or len(calls) == 2) else f.owner['container_id']
    monkeypatch.setattr(f.subject, 'node_identity', node)
    with pytest.raises(RuntimeError, match='access_observation_node_identity'):
        f.subject.observe(f.deployment, deadline=f.deadline)


def test_original_read_file_replacement_is_detected_after_access(fixture, monkeypatch):
    f = fixture; original = f.subject.forward
    def changed(*args):
        result = original(*args)
        if args[-2] == 'team-b':
            write(f.root / 'owner.json', {**f.owner, 'owner_id': 'c' * 32})
        return result
    monkeypatch.setattr(f.subject, 'forward', changed)
    with pytest.raises(RuntimeError, match='access_observation_file_changed'):
        f.subject.observe(f.deployment, deadline=f.deadline)


def test_public_deployment_api_keeps_legacy_access_and_has_explicit_readonly_boundary(subject, monkeypatch, tmp_path):
    bootstrap = load(ROOT, 'bootstrap'); instance = bootstrap.LocalDeployment(ROOT, state=tmp_path)
    calls = []
    def legacy(value):
        calls.append(('legacy', value)); return {'status': 'legacy'}
    def observe(value, *, deadline):
        calls.append(('read_only', value, deadline)); return {'status': 'observed'}
    monkeypatch.setattr(instance, 'support', lambda name: SimpleNamespace(access=legacy, AssemblyError=RuntimeError)
        if name == 'assembly' else SimpleNamespace(observe=observe, ReadOnlyAccessError=subject.ReadOnlyAccessError))
    assert instance.access() == {'status': 'legacy'}
    assert instance.access(read_only=True, deadline=123) == {'status': 'observed'}
    assert calls == [('legacy', instance), ('read_only', instance, 123)]
    for options in ({'read_only': 1}, {'deadline': 123}):
        with pytest.raises(bootstrap.DeploymentError, match='access_observation_invalid'):
            instance.access(**options)


@pytest.fixture
def native(subject, monkeypatch):
    state = SimpleNamespace(chunks=[b'"' + b'b' * 64 + b'" true\n', b''], now=0., calls=[],
        status=0, code=os.CLD_EXITED, failure=None, stall=False, cleanup_expiry=False)
    class Pipe:
        def fileno(self): return 567
        def close(self):
            state.calls.append('close')
            if state.failure == 'close': raise OSError('private close detail')
    class Process:
        pid = 12345
        stdout = Pipe()
        def wait(self, timeout):
            state.calls.append(('wait', timeout))
            if state.cleanup_expiry: state.now = 30.
            if state.failure == 'wait': raise TimeoutError('private wait detail')
            return state.status
    class Selector:
        active = True
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def register(self, file, events):
            assert file is Process.stdout and events == subject.selectors.EVENT_READ
        def get_map(self): return {567: True} if self.active else {}
        def unregister(self, file): self.active = False
        def select(self, timeout):
            assert 0 < timeout <= .1
            if state.stall:
                state.now = 30.
                return []
            return [(SimpleNamespace(fd=567, fileobj=Process.stdout), None)] if self.active else []
    def popen(argv, **kwargs):
        state.calls.append(('popen', argv, kwargs)); return Process()
    def read(fd, maximum):
        assert fd == 567 and 0 < maximum <= 4097
        return state.chunks.pop(0)
    def waitid(kind, pid, flags):
        assert kind == os.P_PID and pid == Process.pid
        assert flags & os.WNOWAIT and flags & os.WNOHANG
        return None if state.stall else SimpleNamespace(si_code=state.code, si_status=state.status)
    def killpg(pid, sig):
        state.calls.append(('kill', pid, sig))
        assert pid == Process.pid and sig == subject.signal.SIGKILL
        if state.failure == 'kill': raise PermissionError('private kill detail')
    monkeypatch.setattr(subject.subprocess, 'Popen', popen)
    monkeypatch.setattr(subject.selectors, 'DefaultSelector', Selector)
    monkeypatch.setattr(subject.os, 'read', read)
    monkeypatch.setattr(subject.os, 'waitid', waitid)
    monkeypatch.setattr(subject.os, 'killpg', killpg)
    monkeypatch.setattr(subject.os, 'set_blocking', lambda fd, value: None)
    monkeypatch.setattr(subject.time, 'monotonic', lambda: state.now)
    return state


def test_fixed_native_inspect_has_no_ambient_docker_authority(subject, native):
    assert subject.node_identity(30.) == 'b' * 64
    _, argv, kwargs = native.calls[0]
    assert argv == ['/usr/bin/docker', '--host', 'unix:///var/run/docker.sock', 'container', 'inspect',
        '--format', '{{json .Id}} {{json .State.Running}}', 'guardrails-evolution']
    assert kwargs == {'env': {'PATH': '/usr/bin:/bin', 'DOCKER_CONFIG': '/proc/self/fd'},
        'stdin': subject.subprocess.DEVNULL, 'stdout': subject.subprocess.PIPE,
        'stderr': subject.subprocess.STDOUT, 'start_new_session': True}
    assert native.calls[1:] == [('kill', 12345, subject.signal.SIGKILL), ('wait', 5), 'close']


@pytest.mark.parametrize('fault', ['stopped', 'bad_id', 'exit', 'signal', 'oversize', 'empty', 'deadline', 'cleanup_expiry'])
def test_native_output_exit_and_original_deadline_refusals_cleanup_owned_child(subject, native, fault):
    if fault == 'stopped': native.chunks[0] = native.chunks[0].replace(b'true', b'false')
    elif fault == 'bad_id': native.chunks[0] = b'"abc" true\n'
    elif fault == 'exit': native.status = 1
    elif fault == 'signal': native.code = os.CLD_KILLED
    elif fault == 'oversize': native.chunks[0] = b'x' * 4097
    elif fault == 'empty': native.chunks = [b'']
    elif fault == 'deadline': native.stall = True
    else: native.cleanup_expiry = True
    with pytest.raises(subject.ReadOnlyAccessError) as error:
        subject.node_identity(30.)
    assert str(error.value).startswith('access_observation_')
    assert native.calls[-1] == 'close'
    assert ('kill', 12345, subject.signal.SIGKILL) in native.calls


@pytest.mark.parametrize('failure', ['kill', 'wait', 'close'])
@pytest.mark.parametrize('primary', [False, True])
def test_native_cleanup_failure_retains_primary_without_private_error_text(subject, native, failure, primary):
    native.failure = failure
    if primary: native.chunks[0] = b'invalid native output\n'
    with pytest.raises(subject.ReadOnlyAccessError) as error:
        subject.node_identity(30.)
    assert str(error.value) == ('access_observation_node_unavailable' if primary else 'access_observation_cleanup_failed')
    assert getattr(error.value, '__notes__', []) == (['Access observation process cleanup also failed'] if primary else [])
    assert native.calls[-1] == 'close'


@pytest.mark.parametrize('fault', ['regular', 'foreign_pseudo_uid', 'oversize', 'fifo', 'symlink', 'expired'])
def test_bounded_kernel_reader_requires_regular_fd_but_not_pseudo_file_uid(subject, tmp_path, monkeypatch, fault):
    path = tmp_path / 'kernel'; path.write_bytes(b'content')
    if fault == 'fifo': path.unlink(); os.mkfifo(path)
    elif fault == 'symlink': path.unlink(); path.symlink_to(tmp_path / 'target'); (tmp_path / 'target').write_bytes(b'content')
    elif fault == 'foreign_pseudo_uid':
        original = subject.os.fstat
        def fstat(fd):
            value = original(fd)
            return SimpleNamespace(st_mode=value.st_mode, st_uid=os.getuid() + 1000)
        monkeypatch.setattr(subject.os, 'fstat', fstat)
    deadline = subject.time.monotonic() + 30 if fault != 'expired' else 0
    if fault in {'regular', 'foreign_pseudo_uid'}:
        assert subject.proc_read(path, 8, deadline) == b'content'
    else:
        with pytest.raises((subject.ReadOnlyAccessError, OSError)):
            subject.proc_read(path, 3 if fault == 'oversize' else 8, deadline)


@pytest.mark.parametrize('fault', ['none', 'owner', 'zombie', 'changed_root', 'unterminated_command'])
def test_forward_identity_binds_owned_proc_directory_and_start_ticks(subject, tmp_path, monkeypatch, fault):
    row = tmp_path.stat(); calls = []
    def lstat(path, *args, **kwargs):
        assert path == Path('/proc/12345')
        calls.append(path)
        values = {name: getattr(row, name) for name in ('st_dev', 'st_ino', 'st_mode', 'st_uid',
            'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns')}
        if fault == 'owner': values['st_uid'] += 1
        if fault == 'changed_root' and len(calls) > 1: values['st_ino'] += 1
        return SimpleNamespace(**values)
    command = b'/owned/kubectl\0port-forward\0'
    def read(path, maximum, deadline):
        if path.name == 'stat':
            fields = ['Z' if fault == 'zombie' else 'S'] + ['0'] * 18 + ['987654']
            return ('12345 (kubectl name) ' + ' '.join(fields)).encode()
        assert path.name == 'cmdline'
        return command[:-1] if fault == 'unterminated_command' else command
    monkeypatch.setattr(subject.Path, 'lstat', lstat)
    monkeypatch.setattr(subject, 'proc_read', read)
    if fault == 'none':
        assert subject.process_identity(12345, subject.time.monotonic() + 30) == {
            'pid': 12345, 'start': '987654', 'command': hashlib.sha256(command).hexdigest()}
    else:
        with pytest.raises(subject.ReadOnlyAccessError):
            subject.process_identity(12345, subject.time.monotonic() + 30)


def test_relocation_growth_is_bounded_during_actual_semantic_enumeration(fixture, monkeypatch):
    f = fixture; history = f.where / 'management-relocations'; history.mkdir(mode=0o700)
    original = f.subject.node_identity
    def node(deadline):
        result = original(deadline)
        for index in range(33): write(history / str(index), {})
        return result
    monkeypatch.setattr(f.subject, 'node_identity', node)
    with pytest.raises(RuntimeError, match='access_observation_relocation_bound'):
        f.subject.observe(f.deployment, deadline=f.deadline)
    assert len(f.native) == 1


@pytest.mark.parametrize('fault', ['none', 'status', 'path', 'profile', 'url', 'late_return'])
def test_matrix_access_uses_fixed_readonly_api_and_same_original_access_deadline(
        fixture, monkeypatch, fault):
    import sys
    f = fixture
    monkeypatch.syspath_prepend(str(DEPENDENCY_ROOT / 'mvp/tests/evolution/e2e'))
    path = ROOT / 'mvp/tests/evolution/e2e/evaluator_matrix_support.py'
    spec = importlib.util.spec_from_file_location('matrix_readonly_test', path)
    matrix = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, matrix); spec.loader.exec_module(matrix)
    cluster = object.__new__(matrix.EvaluatorCluster)
    cluster.state, cluster.profile, cluster._access_deadline = f.root, 'classifier', f.deadline
    result = {'status': 'available', 'access_file': str(f.where / 'access.json'), 'snapshot': f.access}
    calls = []
    def access(*, read_only, deadline):
        calls.append((read_only, deadline))
        if fault == 'status': result['status'] = 'missing'
        elif fault == 'path': result['access_file'] = str(f.root / 'elsewhere.json')
        elif fault == 'profile': result['snapshot']['profile'] = 'deterministic_mock'
        elif fault == 'url': result['snapshot']['runtime_url'] = 'https://user@localhost:8443/'
        elif fault == 'late_return': monkeypatch.setattr(matrix.time, 'monotonic', lambda: f.deadline)
        return result
    cluster.deployment = SimpleNamespace(access=access)
    if fault == 'none':
        assert cluster._access() is f.access
    else:
        with pytest.raises(AssertionError): cluster._access()
    assert calls == [(True, f.deadline)]


def test_public_readonly_api_sanitizes_arbitrary_failures_without_swallowing_interrupts(subject, monkeypatch, tmp_path):
    bootstrap = load(ROOT, 'bootstrap'); instance = bootstrap.LocalDeployment(ROOT, state=tmp_path)
    failure = [OSError('private bearer and filesystem detail')]
    def observe(*args, **kwargs): raise failure[0]
    monkeypatch.setattr(instance, 'support', lambda name: SimpleNamespace(observe=observe,
        ReadOnlyAccessError=subject.ReadOnlyAccessError))
    with pytest.raises(bootstrap.DeploymentError, match='^access_observation_failed$'):
        instance.access(read_only=True, deadline=123)
    failure[0] = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt): instance.access(read_only=True, deadline=123)
