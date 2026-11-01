"""Synthetic private files and fake process ports; no native command is run."""
from __future__ import annotations

import copy
import os
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

from mvp.tests.evolution.e2e import classifier_native_readonly as subject
from mvp.tests.evolution.unit.test_classifier_network_support import (  # noqa: F401 - shared real synthetic certificate fixture
    IMAGES,
    OWNER,
    objects,
    static_fixture,
)


@pytest.fixture(autouse=True)
def forbid_native(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('unexpected actual native/network operation')
    monkeypatch.setattr(subject.subprocess, 'Popen', forbidden)
    monkeypatch.setattr(os, 'killpg', forbidden)
    monkeypatch.setattr(os, 'waitid', forbidden)
    monkeypatch.setattr(socket, 'socket', forbidden)


def put(path, raw, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_bytes(raw)
    path.chmod(mode)
    return path


def test_constructor_only_snapshots(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('constructor performed I/O')
    monkeypatch.setattr(Path, 'resolve', forbidden)
    monkeypatch.setattr(os, 'open', forbidden)
    manifest, preflight = {'nested': []}, {'nested': []}
    executor = subject.NativeReadOnlyExecutor(manifest, preflight, object(), '/bound/source')
    manifest['nested'].append(1); preflight['nested'].append(2)
    assert executor.manifest == executor.preflight == {'nested': []}


@pytest.fixture
def fixture(static_fixture, monkeypatch):  # noqa: F811 - pytest injects the imported certificate fixture
    state, config, _ = static_fixture
    state.chmod(0o700)
    now = subject.time.monotonic()
    budget = subject.private_io.Budget(now + 120)
    owner = {'uid': os.getuid(), 'state': str(state), 'profile': 'guardrails-evolution',
             'owner_id': OWNER, 'container_id': 'e' * 64}
    prepared = {'artifacts': [{'name': artifact, 'kind': 'image', 'source_url': 'docker://' + IMAGES[name]}
        for name, artifact in (('node', 'kicbase-offline'), ('runtime', 'evolution-service'), ('classifier', 'evolution-classifier'))]}
    material = {'owner.json': subject.contract.canonical(owner), 'prepared.json': subject.contract.canonical(prepared),
                'kubeconfig': subject.contract.canonical(config), 'assembly/private/classifier.token': b'x' * 64,
                'assembly/tls/gr-platform/classifier/ca.crt': b'synthetic-mounted-ca'}
    for name, raw in material.items():
        put(state / name, raw)
    pins = {name: subject.sha(raw) for name, raw in material.items()}
    projection, _ = subject.network.project_objects(objects(), OWNER, IMAGES, 'runtime-1')
    binding = {'owner_id': OWNER, 'node_id': owner['container_id'], 'api_server': 'https://127.0.0.1:44634',
               'files': pins, **projection}
    manifest = {'deployment_state': str(state), 'sources': {}, 'deadline_monotonic': budget.deadline,
                'native_executables': {'docker': {'path': '/usr/bin/docker', 'sha256': 'a' * 64},
                'kubectl': {'path': str(state / 'bin/kubectl'), 'sha256': 'b' * 64}}}
    preflight = {'binding': binding, 'private_files': pins, 'sources': {}, 'identity': subject.contract.identity(),
                 'expected': {'owner_id': OWNER, 'node_id': owner['container_id'], 'prepared_sha256': pins['prepared.json']},
                 'images': {'runtime': 'runtime-1'}}
    executor = subject.NativeReadOnlyExecutor(manifest, preflight, budget, state)
    monkeypatch.setattr(executor, '_sources', lambda deadline: executor._check(deadline))
    calls = []
    native = {'identity': [owner['container_id'], '/guardrails-evolution', True, False, False, False, IMAGES['node']],
              'ports': {'8443/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '44634'}]}}
    def invoke(argv, name, deadline, maximum, input_text=None):
        calls.append((argv, name, deadline, maximum, input_text))
        if name == 'docker' and argv[-1] == subject.NATIVE_FORMAT:
            return subject.contract.canonical(native).decode()
        if name == 'kubectl' and argv[-4:] == subject.GET:
            return subject.contract.canonical(objects()).decode()
        return 'synthetic-result'
    monkeypatch.setattr(executor, '_invoke', invoke)
    prefix = (str(state / 'bin/kubectl'), '--kubeconfig', '/dev/stdin', '--context', 'guardrails-evolution',
              '--namespace', 'gr-platform', '--request-timeout=10s')
    text, _, _ = subject.network.static_kubeconfig(material['kubeconfig'], state=state,
        server=binding['api_server'], deadline=budget.deadline)
    settings = {'address': binding['service']['address'], 'identity': preflight['identity'],
                'token_sha256': pins['assembly/private/classifier.token'], 'ca_sha256': pins['assembly/tls/gr-platform/classifier/ca.crt']}
    ready = ('exec', binding['pods']['runtime-1']['pod_name'], '-c', 'runtime', '--', 'python', '-B', '-c',
             subject.network.READY_SCRIPT, subject.contract.canonical(settings).decode())
    return SimpleNamespace(executor=executor, calls=calls, owner=owner, config=config, state=state, material=material,
        native=native, prefix=prefix, ready=ready, text=text, deadline=now + 60, budget=budget)


def call(f, argv, **kwargs):
    return f.executor(argv, deadline=f.deadline, timeout=10, maximum=subject.MAX_OUTPUT, input_text=kwargs.get('input_text'))


@pytest.mark.parametrize('suffix', sorted(subject.BOUNDARY))
def test_exact_original_boundary_reads(fixture, suffix):
    f = fixture
    assert call(f, ('/usr/bin/docker', '--host', subject.network.SOCKET, *suffix)) == 'synthetic-result'
    assert len(f.calls) == 2
    assert f.calls[0][0][-1] == subject.NATIVE_FORMAT
    assert f.calls[1][0] == ('/usr/bin/docker', '--host', subject.network.SOCKET, *suffix)


def test_literal_docker_normalization_only(fixture):
    f = fixture
    raw = call(f, ('docker', '--host', subject.network.SOCKET, 'container', 'inspect', f.owner['container_id'], '--format', subject.NATIVE_FORMAT))
    assert subject.contract.parse(raw.encode()) == f.native
    assert all(row[0][0] == '/usr/bin/docker' for row in f.calls)


@pytest.mark.parametrize('ready', [False, True])
def test_static_kube_exact_forwarding_and_sole_cache_flag(fixture, ready):
    f = fixture
    suffix = f.ready if ready else subject.GET
    result = call(f, (*f.prefix, *suffix), input_text=f.text)
    assert result
    argv, name, limit, maximum, text = f.calls[-1]
    assert name == 'kubectl' and argv == (*f.prefix, '--cache-dir=/proc/self/fd', *suffix)
    assert argv.count('--cache-dir=/proc/self/fd') == 1 and text == f.text
    assert limit <= f.deadline and maximum == subject.MAX_OUTPUT


@pytest.mark.parametrize('fault', ['delete', 'patch', 'apply', 'wrong_namespace', 'wrong_pod', 'script', 'settings',
    'alternate_port', 'flags', 'get_secrets', 'stdin', 'host', 'exec_shell', 'path_lookup', 'docker_config', 'foreign_binary'])
def test_unknown_capabilities_refuse_before_target(fixture, fault):
    f = fixture
    args = [*f.prefix, *f.ready]; text = f.text
    if fault in ('delete', 'patch', 'apply'): args[8:] = [fault, 'pods', 'runtime-1-pod']
    elif fault == 'wrong_namespace': args[6] = 'foreign'
    elif fault == 'wrong_pod': args[9] = 'runtime-2-pod'
    elif fault == 'script': args[-2] += '\nprint("foreign")'
    elif fault == 'settings': args[-1] += ' '
    elif fault == 'alternate_port': args[7] = '--request-timeout=11s'
    elif fault == 'flags': args.append('--as=system:admin')
    elif fault == 'get_secrets': args[8:] = ['get', 'secrets', '-o', 'json']
    elif fault == 'stdin': text += ' '
    elif fault == 'host': args = ['/usr/bin/docker', '--host', 'tcp://foreign:2375', 'container', 'inspect', 'guardrails-evolution']
    elif fault == 'exec_shell': args = ['/usr/bin/docker', '--host', subject.network.SOCKET, 'exec', 'guardrails-evolution', 'sh', '-c', 'true']
    elif fault == 'path_lookup': args[0] = 'kubectl'
    elif fault == 'docker_config': args = ['/usr/bin/docker', '--config', '/foreign', '--host', subject.network.SOCKET, 'container', 'inspect', 'guardrails-evolution']
    elif fault == 'foreign_binary': args[0] = '/foreign/kubectl'
    with pytest.raises(subject.NativeReadOnlyError):
        call(f, tuple(args), input_text=text if args[0] != '/usr/bin/docker' else None)
    assert not any(row[1] == 'kubectl' or row[0][-1] != subject.NATIVE_FORMAT for row in f.calls)


@pytest.mark.parametrize('fault', ['owner', 'node', 'prepared', 'private_bytes', 'public_token', 'proxy', 'exec', 'server', 'native_port', 'native_image'])
def test_pinned_private_route_and_static_auth_refusals(fixture, fault):
    f = fixture
    if fault in ('owner', 'node'):
        f.executor.preflight['expected']['owner_id' if fault == 'owner' else 'node_id'] = 'c' * (32 if fault == 'owner' else 64)
    elif fault == 'prepared': f.executor.preflight['expected']['prepared_sha256'] = 'c' * 64
    elif fault == 'private_bytes': (f.state / 'kubeconfig').write_bytes(b'changed')
    elif fault == 'public_token': (f.state / 'assembly/private/classifier.token').chmod(0o644)
    elif fault == 'native_port': f.native['ports']['8443/tcp'][0]['HostIp'] = '0.0.0.0'
    elif fault == 'native_image': f.native['identity'][-1] = IMAGES['runtime']
    else:
        cfg = copy.deepcopy(f.config)
        if fault == 'proxy': cfg['clusters'][0]['cluster']['proxy-url'] = 'https://foreign'
        elif fault == 'exec': cfg['users'][0]['user']['exec'] = {'command': 'foreign'}
        elif fault == 'server': cfg['clusters'][0]['cluster']['server'] = 'https://foreign:6443'
        raw = subject.contract.canonical(cfg)
        put(f.state / 'kubeconfig', raw)
        f.executor.preflight['binding']['files']['kubeconfig'] = subject.sha(raw)
        f.executor.preflight['private_files']['kubeconfig'] = subject.sha(raw)
    with pytest.raises(subject.NativeReadOnlyError):
        call(f, (*f.prefix, *subject.GET), input_text=f.text)
    assert not any(row[1] == 'kubectl' for row in f.calls)


@pytest.mark.parametrize('field,value', [('deadline', None), ('deadline', True), ('deadline', float('nan')), ('deadline', 1),
    ('timeout', True), ('timeout', 121), ('timeout', 0), ('maximum', True), ('maximum', 0), ('maximum', 2**24)])
def test_original_finite_limits(fixture, field, value):
    f = fixture
    opts = {'deadline': f.deadline, 'timeout': 10, 'maximum': 4096, 'input_text': None}
    opts[field] = value
    with pytest.raises(subject.NativeReadOnlyError):
        f.executor(('/usr/bin/docker', '--host', subject.network.SOCKET, 'container', 'inspect', 'guardrails-evolution'), **opts)
    assert not f.calls


def test_deadline_extension_and_original_budget_restored(fixture):
    f = fixture
    original = f.budget.deadline
    f.deadline = original + 1
    with pytest.raises(subject.NativeReadOnlyError, match='native_deadline_extension'):
        call(f, (*f.prefix, *subject.GET), input_text=f.text)
    assert f.budget.deadline == original and not f.calls


def test_target_private_drift_after_execution_is_not_success(fixture, monkeypatch):
    f = fixture
    original = f.executor._invoke
    def invoke(*args, **kwargs):
        result = original(*args, **kwargs)
        if args[1] == 'kubectl':
            (f.state / 'kubeconfig').write_bytes(b'changed')
        return result
    monkeypatch.setattr(f.executor, '_invoke', invoke)
    with pytest.raises(subject.NativeReadOnlyError):
        call(f, (*f.prefix, *subject.GET), input_text=f.text)


def test_actual_executable_reader_holds_verified_bytes(tmp_path):
    file = put(tmp_path / 'native', b'\x7fELFsynthetic-not-executed', 0o700)
    ref = {'path': str(file), 'sha256': subject.sha(file.read_bytes())}
    checks = []
    with subject.executable(ref, owner=os.getuid(), check=lambda: checks.append(1)) as fd:
        assert os.fstat(fd).st_ino == file.stat().st_ino
        os.lseek(fd, 0, os.SEEK_SET)
        assert os.read(fd, 128) == file.read_bytes()
    assert len(checks) >= 4
    with pytest.raises(OSError): os.fstat(fd)


@pytest.mark.parametrize('fault', ['symlink', 'fifo', 'hardlink', 'writable', 'foreign_uid', 'bad_hash', 'not_elf', 'changed', 'replaced', 'ancestor'])
def test_executable_refuses_unsafe_or_changed_inputs(tmp_path, fault):
    folder = tmp_path / 'bin'; folder.mkdir(mode=0o700)
    file = put(folder / 'native', b'\x7fELFsynthetic', 0o700)
    ref = {'path': str(file), 'sha256': subject.sha(file.read_bytes())}
    owner = os.getuid()
    if fault == 'symlink': file.rename(folder / 'target'); file.symlink_to(folder / 'target')
    elif fault == 'fifo': file.unlink(); os.mkfifo(file)
    elif fault == 'hardlink': os.link(file, folder / 'other')
    elif fault == 'writable': file.chmod(0o722)
    elif fault == 'foreign_uid': owner += 1
    elif fault == 'bad_hash': ref['sha256'] = 'f' * 64
    elif fault == 'not_elf': file.write_bytes(b'#!script'); ref['sha256'] = subject.sha(file.read_bytes())
    with pytest.raises(subject.NativeReadOnlyError), subject.executable(ref, owner=owner, check=lambda: None):
        if fault == 'changed': file.write_bytes(b'\x7fELFCHANGEDXX')
        elif fault == 'replaced': file.rename(folder / 'old'); put(file, b'\x7fELFsynthetic', 0o700)
        elif fault == 'ancestor': folder.rename(tmp_path / 'old'); folder.mkdir(mode=0o700); put(file, b'\x7fELFsynthetic', 0o700)


class Stream:
    def __init__(self, fd): self.fd, self.closed = fd, False
    def fileno(self): return self.fd
    def close(self): self.closed = True


@pytest.fixture
def process_port(monkeypatch):
    now = [100.0]
    events, actions = {}, []
    streams = {name: Stream(fd) for name, fd in [('in', 71), ('out', 72), ('err', 73)]}
    incoming = {72: [b'good', b''], 73: [b'']}
    status = SimpleNamespace(si_code=os.CLD_EXITED, si_status=0)
    process = SimpleNamespace(pid=99999, stdin=streams['in'], stdout=streams['out'], stderr=streams['err'])
    def wait(timeout): actions.append(('wait', timeout)); return status.si_status
    process.wait = wait
    def popen(argv, **kwargs): actions.append(('spawn', argv, kwargs)); return process
    class Selector:
        def register(self, stream, mask, data): events[stream.fd] = SimpleNamespace(fileobj=stream, data=data)
        def unregister(self, stream): events.pop(stream.fd)
        def get_map(self): return events
        def select(self, timeout): now[0] += .01; return [(row, 1) for row in list(events.values())]
        def close(self): actions.append(('selector-close',))
    monkeypatch.setattr(subject.subprocess, 'Popen', popen)
    monkeypatch.setattr(subject.selectors, 'DefaultSelector', Selector)
    monkeypatch.setattr(subject.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(os, 'set_blocking', lambda *args: None)
    monkeypatch.setattr(os, 'read', lambda fd, size: incoming[fd].pop(0))
    monkeypatch.setattr(os, 'write', lambda fd, raw: (actions.append(('write', raw)) or len(raw)))
    monkeypatch.setattr(os, 'waitid', lambda *args: (actions.append(('waitid', args)) or status))
    monkeypatch.setattr(os, 'killpg', lambda *args: actions.append(('killpg', *args)))
    def check(): subject.require(now[0] < 101, 'native_deadline_expired')
    evidence = {}
    def run(maximum=4096, input_text='private-synthetic-stdin'):
        return subject.run_process(('/pinned/native', 'read'), file_fd=90, deadline=101, maximum=maximum,
                                   input_text=input_text, check=check, evidence=evidence)
    return SimpleNamespace(run=run, now=now, actions=actions, streams=streams, incoming=incoming, status=status, process=process, evidence=evidence)


def test_fake_process_exact_fd_environment_stdin_and_group_ownership(process_port, monkeypatch):
    p = process_port
    monkeypatch.setenv('HTTP_PROXY', 'https://ambient.invalid')
    monkeypatch.setenv('DOCKER_CONFIG', '/ambient-private')
    assert p.run() == 'good'
    _, argv, options = p.actions[0]
    assert argv == ('/pinned/native', 'read') and options['executable'] == '/proc/self/fd/90'
    assert options['pass_fds'] == (90,) and options['start_new_session'] and options['close_fds']
    assert options['shell'] is False and options['cwd'] == '/'
    assert set(options['env']) == {'LANG', 'LC_ALL', 'PATH', 'DOCKER_HOST', 'DOCKER_CONFIG'}
    assert options['env']['DOCKER_CONFIG'] == '/proc/self/fd'
    assert ('write', b'private-synthetic-stdin') in p.actions
    assert [a[0] for a in p.actions].index('killpg') < [a[0] for a in p.actions].index('wait')
    assert all(row.closed for row in p.streams.values())


@pytest.mark.parametrize('fault', ['output', 'stderr', 'nonzero', 'invalid_utf8', 'deadline', 'selector', 'read', 'cleanup', 'lost_child'])
def test_fake_native_failures_are_finite_and_cleanup_owned(process_port, monkeypatch, fault):
    p = process_port
    if fault == 'output': p.incoming[72] = [b'x' * 10]
    elif fault == 'stderr': p.incoming[73] = [b'secret native diagnostic' * 3]
    elif fault == 'nonzero': p.status.si_status = 1
    elif fault == 'invalid_utf8': p.incoming[72] = [b'\xff', b'']
    elif fault == 'deadline': p.now[0] = 100.99
    elif fault == 'selector': monkeypatch.setattr(subject.selectors, 'DefaultSelector', lambda: (_ for _ in ()).throw(OSError('private')))
    elif fault == 'read': monkeypatch.setattr(os, 'read', lambda *args: (_ for _ in ()).throw(OSError('secret text')))
    elif fault == 'cleanup': p.process.wait = lambda **kwargs: (_ for _ in ()).throw(OSError('secret cleanup'))
    elif fault == 'lost_child': monkeypatch.setattr(os, 'waitid', lambda *args: (_ for _ in ()).throw(ChildProcessError('unowned')))
    with pytest.raises(subject.NativeReadOnlyError) as caught:
        p.run(maximum=8)
    assert 'secret' not in str(caught.value) and 'private' not in str(caught.value)
    assert all(row.closed for row in p.streams.values())
    if fault == 'lost_child': assert not any(a[0] == 'killpg' for a in p.actions)
    else: assert any(a[0] == 'killpg' for a in p.actions)


def test_primary_output_error_survives_cleanup_error(process_port):
    p = process_port
    p.incoming[72] = [b'too many bytes']
    p.process.wait = lambda **kwargs: (_ for _ in ()).throw(OSError('cleanup'))
    with pytest.raises(subject.NativeReadOnlyError, match='native_output_bound') as caught:
        p.run(maximum=1)
    assert caught.value.__notes__ == ['native_cleanup_failed']


def test_exited_leader_open_descendant_pipe_remains_bounded(process_port):
    p = process_port
    p.incoming[72] = [b'x'] * 200
    p.incoming[73] = [b'']
    with pytest.raises(subject.NativeReadOnlyError, match='native_deadline_expired'):
        p.run()
    assert any(a[0] == 'killpg' for a in p.actions)
    assert any(a[0] == 'wait' for a in p.actions)


def test_private_stderr_retained_bounded_without_stdin_or_argv(process_port):
    p = process_port
    p.incoming[73] = [b'bounded warning', b'']
    assert p.run() == 'good'
    assert p.evidence == {'spawned': True, 'stdout': b'good', 'stderr': b'bounded warning', 'truncated': False,
                          'cleanup': 'completed', 'status': 'completed'}
    assert b'private-synthetic-stdin' not in repr(p.evidence).encode()


def test_partial_output_survives_failure_and_is_not_completed(process_port):
    p = process_port
    p.incoming[72] = [b'first', b'overflow']
    with pytest.raises(subject.NativeReadOnlyError, match='native_output_bound'):
        p.run(maximum=7)
    assert p.evidence['stdout'] == b'firstov'
    assert p.evidence['truncated'] is True and p.evidence['status'] == 'failed'
    assert p.evidence['cleanup'] == 'completed'


def test_actual_fd_reader_composes_with_fake_execution_and_post_hash(tmp_path, monkeypatch):
    file = put(tmp_path / 'kubectl', b'\x7fELFfake-verified', 0o700)
    budget = subject.private_io.Budget(subject.time.monotonic() + 10)
    executor = subject.NativeReadOnlyExecutor({'native_executables': {'kubectl': {'path': str(file),
        'sha256': subject.sha(file.read_bytes())}}}, {}, budget, tmp_path)
    calls = []
    def run(argv, **kwargs):
        fd = kwargs['file_fd']
        assert os.fstat(fd).st_ino == file.stat().st_ino
        kwargs['evidence'].update(stdout=b'output', status='completed')
        calls.append(fd)
        return 'output'
    monkeypatch.setattr(subject, 'run_process', run)
    assert executor._invoke((str(file), 'read'), 'kubectl', budget.deadline, 4096) == 'output'
    assert executor.attempts[0]['stdout'] == b'output'
    detached = executor.attempts; detached[0]['stdout'] = b'changed'
    assert executor.attempts[0]['stdout'] == b'output'
    with pytest.raises(OSError): os.fstat(calls[0])
    def changed(argv, **kwargs):
        file.write_bytes(b'\x7fELFchanged-after-run')
        kwargs['evidence']['status'] = 'completed'
        return 'false success'
    monkeypatch.setattr(subject, 'run_process', changed)
    with pytest.raises(subject.NativeReadOnlyError):
        executor._invoke((str(file), 'read'), 'kubectl', budget.deadline, 4096)
    assert executor.attempts[-1]['status'] == 'failed'


@pytest.mark.parametrize('fault', ['loaded_path', 'source_bytes', 'source_map', 'missing_source'])
def test_loaded_module_and_source_closure_refusals(tmp_path, monkeypatch, fault):
    root = tmp_path / 'source'; root.mkdir(mode=0o700)
    sources = {}
    for module_name, name in subject.SOURCE_MODULES.items():
        path = put(root / name, b'synthetic pinned source\n', 0o644)
        sources[name] = subject.sha(path.read_bytes())
        monkeypatch.setattr(subject.sys.modules[module_name], '__file__', str(path))
    budget = subject.private_io.Budget(subject.time.monotonic() + 10)
    executor = subject.NativeReadOnlyExecutor({'sources': sources}, {'sources': sources}, budget, root)
    executor._sources(budget.deadline)
    first_name = next(iter(subject.SOURCE_MODULES))
    relative = subject.SOURCE_MODULES[first_name]
    if fault == 'loaded_path': monkeypatch.setattr(subject.sys.modules[first_name], '__file__', str(root / 'foreign.py'))
    elif fault == 'source_bytes': (root / relative).write_bytes(b'changed')
    elif fault == 'source_map': executor.preflight['sources'][relative] = 'f' * 64
    else: executor.manifest['sources'].pop(relative)
    with pytest.raises(subject.NativeReadOnlyError): executor._sources(budget.deadline)


@pytest.mark.parametrize('fault', ['count', 'bytes'])
def test_retention_exhaustion_refuses_before_open_or_execution(tmp_path, monkeypatch, fault):
    budget = subject.private_io.Budget(subject.time.monotonic() + 10)
    executor = subject.NativeReadOnlyExecutor({'native_executables': {'kubectl': {}}}, {}, budget, tmp_path)
    if fault == 'count': executor._attempts = [{'stdout': b'', 'stderr': b''}] * subject.MAX_ATTEMPTS
    else: executor._attempts = [{'stdout': b'x' * subject.MAX_RETAINED, 'stderr': b''}]
    monkeypatch.setattr(os, 'open', lambda *a, **kw: pytest.fail('opened after retention exhaustion'))
    with pytest.raises(subject.NativeReadOnlyError, match='native_retention_bound'):
        executor._invoke(('never-run',), 'kubectl', budget.deadline, 4096)


@pytest.mark.parametrize('phases,expected', [(3, 266), (5, 370)])
def test_original_constructor_and_phase_invocation_counts_fit_with_byte_cap(tmp_path, monkeypatch, phases, expected):
    """Actual held-FD invoke path for constructor + resource/budget call counts."""
    binary = put(tmp_path / 'kubectl', b'\x7fELFfinite-synthetic-binary', 0o700)
    budget = subject.private_io.Budget(subject.time.monotonic() + 30)
    executor = subject.NativeReadOnlyExecutor({'native_executables': {'kubectl': {
        'path': str(binary), 'sha256': subject.sha(binary.read_bytes())}}}, {}, budget, tmp_path)
    limits = []
    def fake_process(argv, **kwargs):
        assert os.fstat(kwargs['file_fd']).st_ino == binary.stat().st_ino
        assert kwargs['deadline'] == budget.deadline
        limits.append(kwargs['maximum'])
        kwargs['evidence'].update(stdout=b'ok', status='completed', cleanup='completed')
        return 'ok'
    monkeypatch.setattr(subject, 'run_process', fake_process)
    per_current = (2 * 5 * 2) + (2 * (2 * 6 + 4))
    assert per_current == 52
    count = 6 + (2 + phases) * per_current
    assert count == expected
    for _ in range(count):
        assert executor._invoke((str(binary),), 'kubectl', budget.deadline, 4096) == 'ok'
    assert len(executor.attempts) == expected
    assert subject.MAX_ATTEMPTS == 512 and subject.MAX_RETAINED == 16 * 1024 * 1024
    assert len(limits) == expected and set(limits) == {4096}
    executor._attempts[-1]['stdout'] = b'x' * subject.MAX_RETAINED
    with pytest.raises(subject.NativeReadOnlyError, match='native_retention_bound'):
        executor._invoke((str(binary),), 'kubectl', budget.deadline, 4096)
    assert len(limits) == expected
