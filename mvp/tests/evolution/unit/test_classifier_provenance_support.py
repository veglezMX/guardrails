"""Finite proc/file and genuine journal/health-record composition; no live IO."""
from __future__ import annotations

import copy
import fcntl
import os
import socket
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace as N

import pytest
from guardrails.evolution.classifier_contract import canonical

from mvp.tests.evolution.e2e import classifier_provenance_support as s
from mvp.tests.evolution.unit import test_classifier_provenance as fixtures
from mvp.tests.evolution.unit import test_credentials as credentials_fixture


@pytest.fixture(autouse=True)
def no_external(monkeypatch):
    def no(*args, **kwargs):
        raise AssertionError('actual operation forbidden')
    monkeypatch.setattr(subprocess, 'Popen', no)
    monkeypatch.setattr(socket.socket, 'connect', no)
    monkeypatch.setattr(socket, 'getaddrinfo', no)


def write(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_bytes(raw)
    path.chmod(0o600)


@pytest.mark.parametrize('deadline', [None, True, float('nan'), float('inf'), -1])
def test_missing_or_invalid_deadline_has_no_fallback(deadline):
    with pytest.raises(s.ProvenanceCheckError):
        s.remaining(deadline)


def test_actual_existing_flock_required_and_never_acquired(tmp_path):
    path = tmp_path / '.lock'
    write(path, b'')
    descriptor = os.open(path, os.O_RDWR)
    try:
        with pytest.raises(s.ProvenanceCheckError):
            s.held_lock(tmp_path, deadline=s.time.monotonic()+3)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        s.held_lock(tmp_path, deadline=s.time.monotonic()+3)
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        with pytest.raises(s.ProvenanceCheckError):
            s.held_lock(tmp_path, deadline=s.time.monotonic()+3)
    finally:
        os.close(descriptor)


def test_lock_path_replacement_is_not_adopted(tmp_path):
    path = tmp_path / '.lock'
    write(path, b'')
    descriptor = os.open(path, os.O_RDWR)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        path.rename(tmp_path / 'retained.lock')
        write(path, b'foreign')
        with pytest.raises(s.ProvenanceCheckError):
            s.held_lock(tmp_path, deadline=s.time.monotonic()+3)
        assert path.read_bytes() == b'foreign'
    finally:
        os.close(descriptor)


def test_kernel_reader_size_and_fifo_guards(tmp_path):
    path = tmp_path / 'small'
    write(path, b'1234')
    assert s.kernel_read(path, deadline=s.time.monotonic()+3, maximum=4) == b'1234'
    with pytest.raises(s.ProvenanceCheckError):
        s.kernel_read(path, deadline=s.time.monotonic()+3, maximum=3)
    fifo = tmp_path / 'fifo'
    os.mkfifo(fifo, 0o600)
    with pytest.raises(s.ProvenanceCheckError):
        s.kernel_read(fifo, deadline=s.time.monotonic()+3, maximum=4)


def test_actual_self_process_identity_has_bounded_original_projection():
    row = s.process_identity(os.getpid(), deadline=s.time.monotonic()+3)
    assert row['pid'] == os.getpid() and row['start'].isdigit() and len(row['command']) == 64


@pytest.mark.parametrize('pid', [True, -1, 0, '123', 2**31])
def test_process_pid_refuses_before_proc(pid):
    with pytest.raises(s.ProvenanceCheckError):
        s.process_identity(pid, deadline=s.time.monotonic()+3)


def test_deadline_cannot_be_extended_by_original_observer_reset(monkeypatch):
    now = s.time.monotonic()
    cluster = N(profile='classifier', deployment=N(env={}))
    calls = []
    observer = s.BoundedObserver(cluster, runtime='runtime-1', deadline=now+1,
        executor=lambda *args, **kwargs: calls.append(kwargs) or '{}')
    observer.deadline = now+50
    assert observer.deadline == now+1
    observer._command(('synthetic',), timeout=50)
    assert 0 < calls[0]['timeout'] <= 1 and calls[0]['deadline'] == now+1
    monkeypatch.setattr(s.time, 'monotonic', lambda: now+2)
    with pytest.raises(s.ProvenanceCheckError):
        observer._command(('synthetic',))
    assert len(calls) == 1


@pytest.mark.parametrize('variable', ['DOCKER_CONFIG', 'BUILDX_CONFIG', 'DOCKER_CONTEXT', 'HTTPS_PROXY'])
def test_observer_rejects_ambient_routes_before_executor(monkeypatch, variable):
    calls = []
    monkeypatch.setenv(variable, 'foreign')
    observer = s.BoundedObserver(N(profile='classifier', deployment=N(env={})), runtime='runtime-1',
        deadline=s.time.monotonic()+3, executor=lambda *args, **kwargs: calls.append(args) or '{}')
    with pytest.raises((s.ProvenanceCheckError, s.network.ClassifierObservationError)):
        observer._command(('synthetic',))
    assert not calls


@pytest.fixture
def flow(tmp_path, monkeypatch):
    case = fixtures.case()
    original_datetime = s.datetime
    class Clock(original_datetime):
        @classmethod
        def now(cls, tz=None):
            return fixtures.NOW
    monkeypatch.setattr(s, 'datetime', Clock)
    state = tmp_path / 'state'
    state.mkdir(mode=0o700)
    saved = credentials_fixture.saved.__wrapped__(state)
    directory, owner, inventory, journal, journal_path = saved
    journal['owner_id'] = case.expected['owner_id']
    credentials_fixture.publish(saved)
    owner = {'profile': 'guardrails-evolution', 'uid': os.getuid(), 'state': str(state),
        'owner_id': case.expected['owner_id'], 'container_id': case.expected['node_id']}
    for name in s.MANDATORY_PRIVATE:
        if not (state/name).exists():
            write(state/name, b'synthetic-placeholder')
    write(state/'owner.json', canonical(owner))
    write(state/'prepared.json', canonical({'artifacts': []}))
    write(state/'assembly/ports.json', canonical({name: 5000+i for i, name in enumerate(sorted(s.FORWARDS))}))
    entry = journal['entries'][0]
    subject = {'scope': entry['scope'], 'principal_id': entry['principal_id'], 'credential_id': entry['receipt']['credential_id'],
        'roles': entry['roles'], 'workload_id': entry['workload_id'], 'effective_expires_at': entry['receipt']['expires_at']}
    pref = case.preflight
    pref['identity'] = s.identity()
    pref['binding']['files'] = {'owner.json': s.digest((state/'owner.json').read_bytes())}
    pref['images'] = {'runtime': 'runtime-1', 'native_images': {'runtime': {'synthetic': 1}, 'classifier': {'synthetic': 2}},
        'node_boundary_snapshot': {'synthetic': 3}}
    boundary_raw = canonical(pref['images']['node_boundary_snapshot'])
    pref['node_boundary']['sha256'] = pref['expected']['node_boundary_sha256'] = s.digest(boundary_raw)
    case.authorization['receipts']['node_boundary']['sha256'] = s.digest(boundary_raw)
    material = {p.relative_to(state).as_posix(): p.read_bytes() for p in state.rglob('*') if p.is_file()}
    pref['private_files'] = {n:s.digest(raw) for n,raw in material.items()}
    pref['expected'].update(credential_generation=journal['generation'], subjects=[subject],
        credential_journal_sha256=s.digest(journal_path.read_bytes()), prepared_sha256=s.digest(material['prepared.json']))
    case.authorization['identity'] = pref['identity']
    for key, field in [('binding', 'binding_sha256'), ('identity', 'identity_sha256'), ('private_files', 'private_files_sha256')]:
        pref['expected'][field] = s.digest(canonical(pref[key]))
    for key, field in [('prepared', 'prepared_sha256'), ('credentials', 'credential_journal_sha256')]:
        case.authorization['receipts'][key]['sha256'] = pref['expected'][field]
        pref['receipts'][key]['sha256'] = pref['expected'][field]
    pref_raw = canonical(pref)
    case.authorization['preflight']['sha256'] = s.digest(pref_raw)
    auth_raw = canonical(case.authorization)
    root = credentials_fixture.ROOT
    cluster = N(profile='classifier', deployment=N(root=root, state=state, env={}))
    checker = s.CurrentChecker(cluster, source_root=root, authorization_raw=auth_raw, authorization_sha256=s.digest(auth_raw),
        preflight_raw=pref_raw, preflight_sha256=s.digest(pref_raw), node_boundary_raw=boundary_raw, executor=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('native unmocked')))
    source = {'deploy/evolution/credentials.py': (root/'deploy/evolution/credentials.py').read_bytes()}
    calls = []
    monkeypatch.setattr(checker, '_sources', lambda *args: calls.append('sources') or source)
    monkeypatch.setattr(checker, '_loaded_sources', lambda: calls.append('loaded'))
    monkeypatch.setattr(checker, '_forwards', lambda *args: calls.append('forwards'))
    monkeypatch.setattr(checker, '_boundary', lambda *args: calls.append('boundary'))
    def subjects(*args, **kwargs):
        calls.append('authenticate');return copy.deepcopy(pref['expected']['subjects'])
    monkeypatch.setattr(s, 'actual_subjects', subjects)
    class Observer:
        def __init__(self, *args, **kwargs):
            self.deadline = kwargs['deadline']
        def _routing(self):pass
        def sample_locked(self):
            calls.append('ready')
            return {'binding_sha256': pref['expected']['binding_sha256'],
                    'counter': {'epoch': pref['expected']['worker_epoch'], 'attempts': 0}}
    monkeypatch.setattr(s, 'BoundedObserver', Observer)
    write(state/'.lock', b'')
    lock = os.open(state/'.lock', os.O_RDWR)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    yield N(**locals())
    os.close(lock)


def observe(f):
    return f.checker.observe_locked(phase='before', operation=None, deadline=s.time.monotonic()+10)


def test_real_private_journal_flock_and_pure_current_record_compose(flow):
    value = observe(flow)
    assert value['subjects'] == flow.pref['expected']['subjects']
    assert flow.calls.count('ready') == flow.calls.count('forwards') == flow.calls.count('boundary') == 2
    assert flow.calls.count('authenticate') == 1 and value['elapsed_ms'] < 10000
    assert set(value) == s.provenance.CURRENT_FIELDS | {'schema_version', 'preflight_sha256', 'phase', 'operation', 'observed_at', 'elapsed_ms'}


@pytest.mark.parametrize('change', ['pending','journal','token','mode','owner','unheld','preflight-pin','auth-pin','subject','epoch','expired','profile'])
def test_composed_checker_refuses_changed_current_authority(flow, monkeypatch, change):
    f = flow
    if change == 'pending': write(f.directory/'credential-renewals/pending.json', b'{}')
    elif change == 'journal': f.journal_path.write_bytes(b'{}')
    elif change == 'token': Path(f.entry['file']).write_bytes(b'changed')
    elif change == 'mode': (f.state/'credentials/ca.crt').chmod(0o777)
    elif change == 'owner': (f.state/'owner.json').write_bytes(b'{}')
    elif change == 'unheld': fcntl.flock(f.lock, fcntl.LOCK_UN)
    elif change == 'preflight-pin': f.checker.preflight_sha256 = '0'*64
    elif change == 'auth-pin': f.checker.authorization_sha256 = '0'*64
    elif change == 'subject': monkeypatch.setattr(s, 'actual_subjects', lambda *args, **kwargs: [])
    elif change == 'epoch':
        def bad(self):return {'binding_sha256':f.pref['expected']['binding_sha256'], 'counter':{'epoch':'0'*32}}
        monkeypatch.setattr(f.Observer, 'sample_locked', bad)
    elif change == 'expired':
        monkeypatch.setattr(s.time, 'monotonic', lambda: 100)
        with pytest.raises(s.ProvenanceCheckError):
            f.checker.observe_locked(phase='before', operation=None, deadline=99)
        return
    elif change == 'profile': f.cluster.profile = 'deterministic_mock'
    with pytest.raises(s.ProvenanceCheckError):observe(f)


def test_missing_historical_journal_token_pin_refuses_without_path_fallback(flow):
    material = dict(flow.material)
    material.pop(Path(flow.entry['old_file']).relative_to(flow.state).as_posix())
    with pytest.raises(s.ProvenanceCheckError, match='credential_file_not_pinned'):
        flow.checker._journal(material, flow.owner, flow.source, flow.pref, s.time.monotonic()+3)


def test_changed_same_record_after_authentication_is_rechecked(flow, monkeypatch):
    def subjects(*args, **kwargs):
        (flow.state/'credentials/ca.crt').write_bytes(b'changed')
        return flow.pref['expected']['subjects']
    monkeypatch.setattr(s, 'actual_subjects', subjects)
    with pytest.raises(s.ProvenanceCheckError):observe(flow)
    assert flow.calls.count('ready') == 2


def test_loaded_module_must_match_exact_authenticated_root(flow, monkeypatch):
    monkeypatch.setattr(s, 'MODULES', {'synthetic': 'mvp/synthetic.py'})
    monkeypatch.setitem(sys.modules, 'synthetic', N(__file__='/foreign/mvp/synthetic.py'))
    with pytest.raises(s.ProvenanceCheckError, match='loaded_source_mismatch'):
        s.CurrentChecker._loaded_sources(flow.checker)


@pytest.fixture
def database_adapter(tmp_path, monkeypatch):
    import contextlib
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    import sqlalchemy
    from guardrails.evolution import storage
    from guardrails.evolution.deadlines import current_deadline
    from sqlalchemy.engine import URL
    credential_id, principal_id = str(uuid4()), str(uuid4())
    secret = 'gr2.team-a.'+credential_id+'.'+'s'*43
    expiry = (datetime.now(UTC)+timedelta(hours=20)).isoformat()
    credential = {'credential_id': credential_id, 'principal_id': principal_id, 'workload_id': None,
        'token_hash': s.digest(secret.encode()), 'revoked_at': None, 'previous_valid_until': None, 'expires_at': expiry}
    principal = {'principal_id': principal_id, 'tenant_id': 'team-a', 'roles': ['runtime'], 'status': 'active'}
    state = tmp_path/'state'
    url = URL.create('postgresql+psycopg', username='evolution_app', password='synthetic-secret', host='localhost',
        port=5000, database='guardrails_evolution', query={'sslmode':'verify-full','sslrootcert':str(state/'credentials/ca.crt')})
    material = {'assembly/private/database-app-url': url.render_as_string(hide_password=False).encode(),
        'credentials/ca.crt':b'synthetic-ca-snapshot', 'token':secret.encode()}
    calls=[]
    flags={'tls':True,'readonly':'on','recovery':False,'cleanup_fail':False}
    class Engine:
        @contextlib.contextmanager
        def connect(self):
            yield self
        def execute(self, sql):
            assert str(sql).startswith("SELECT current_setting('transaction_read_only')")
            calls.append('probe')
            return N(one=lambda:(flags['readonly'],flags['recovery'],datetime.now(UTC),flags['tls']))
        def dispose(self):
            calls.append('dispose')
            if flags['cleanup_fail']:raise RuntimeError('private database cleanup detail')
    engine=Engine()
    def create_engine(sealed, **kwargs):
        assert sealed.username=='evolution_app' and sealed.query['sslmode']=='verify-full'
        ca = int(sealed.query['sslrootcert'].rsplit('/',1)[1])
        assert Path(sealed.query['sslrootcert']).read_bytes()==material['credentials/ca.crt']
        assert fcntl.fcntl(ca, fcntl.F_GET_SEALS) & fcntl.F_SEAL_WRITE
        with pytest.raises(OSError):os.write(ca,b'bad')
        assert kwargs['hide_parameters'] and kwargs['max_overflow']==0 and kwargs['connect_args']['connect_timeout']==2
        assert 'default_transaction_read_only=on' in kwargs['connect_args']['options']
        assert current_deadline.get() is not None
        calls.append('engine');return engine
    class Repository:
        def __init__(self, actual):assert actual is engine
        @contextlib.contextmanager
        def transaction(self, scope):
            assert scope=='team-a' and current_deadline.get() is not None
            def get(table, key):
                assert table in {'credentials','principals','tenants'}
                calls.append(table)
                return {'credentials':credential, 'principals':principal, 'tenants':{'status':'active'}}[table]
            yield N(tenant_id=scope,get=get)
    monkeypatch.setattr(sqlalchemy,'create_engine',create_engine)
    monkeypatch.setattr(storage,'TenantRepository',Repository)
    entries=[{'file':str(state/'token')}]
    def invoke():return s.actual_subjects(material,entries,state=state,port=5000,deadline=s.time.monotonic()+5,minimum_validity_seconds=14400)
    return N(**locals())


def test_actual_identity_authentication_through_readonly_adapter(database_adapter):
    f=database_adapter
    result=f.invoke()
    assert result==[{'scope':'team-a','principal_id':f.principal_id,'credential_id':f.credential_id,'workload_id':None,
                    'roles':['runtime'],'effective_expires_at':f.expiry.replace('+00:00','Z')}]
    assert f.calls==['engine','probe','credentials','principals','tenants','dispose']
    assert f.current_deadline.get() is None


@pytest.mark.parametrize('change',['wrong-secret','revoked','disabled-principal','overlap','tls','write','standby','ambient','foreign-dsn','cleanup'])
def test_actual_authentication_and_database_failure_refused(database_adapter, monkeypatch, change):
    f=database_adapter
    if change=='wrong-secret':f.credential['token_hash']='0'*64
    elif change=='revoked':f.credential['revoked_at']=f.expiry
    elif change=='disabled-principal':f.principal['status']='disabled'
    elif change=='overlap':f.credential['previous_valid_until']=(f.datetime.now(f.UTC)+f.timedelta(seconds=120)).isoformat()
    elif change=='tls':f.flags['tls']=False
    elif change=='write':f.flags['readonly']='off'
    elif change=='standby':f.flags['recovery']=True
    elif change=='ambient':monkeypatch.setenv('PGHOST','foreign')
    elif change=='foreign-dsn':f.material['assembly/private/database-app-url']=f.material['assembly/private/database-app-url'].replace(b'localhost',b'foreign')
    elif change=='cleanup':f.flags['cleanup_fail']=True
    from guardrails.evolution.errors import EvolutionError
    with pytest.raises((s.ProvenanceCheckError, EvolutionError)):f.invoke()
    assert f.current_deadline.get() is None
    if 'engine' in f.calls:assert f.calls[-1]=='dispose'


def test_authentication_error_not_replaced_by_cleanup_failure(database_adapter):
    from guardrails.evolution.errors import EvolutionError
    f=database_adapter;f.credential['token_hash']='0'*64;f.flags['cleanup_fail']=True
    with pytest.raises(EvolutionError):f.invoke()
    assert f.calls[-1]=='dispose' and f.current_deadline.get() is None


@pytest.fixture
def native_boundary(tmp_path, monkeypatch):
    import json
    root=tmp_path/'source';root.mkdir(mode=0o700)
    image='node@sha256:'+'a'*64
    nft=b'synthetic captured exact rule source'
    expected={'container_id':'c'*64,'image':image,'rules_sha256':s.digest(nft),'network_namespace':'net:[2]',
        'cpu_limit':6,'cpu_quota_us':600000,'cpu_period_us':100000,'memory_limit_bytes':8*1024**3}
    def match(protocol,value):return {'match':{'op':'==','left':{'payload':{'protocol':protocol,'field':'daddr'}},'right':value}}
    local=[[match('ip','@local4'),{'accept':None}],[match('ip6','::1'),{'accept':None}]]
    rows=[{'chain':{'name':name,'policy':'drop','prio':300}} for name in ('output','forward')]
    rows += [{'set':{'name':'local4','elem':[{'prefix':{'addr':'10.96.0.0','len':12}},
        {'prefix':{'addr':'10.244.0.0','len':16}},'127.0.0.1','192.168.51.2']}}]
    exprs={'output':local+[[{'match':{'op':'in','left':{'ct':{'key':'state'}},'right':['established','related']}},
        match('ip','192.168.51.1'),{'accept':None}],[{'drop':None}]],'forward':local+[[{'drop':None}]]}
    for name, rules in exprs.items():
        rows.extend({'rule':{'chain':name,'expr':rule}} for rule in rules)
    outputs={('container','inspect','guardrails-evolution'):json.dumps([{'Id':'c'*64,'HostConfig':{'NanoCpus':6000000000,'Memory':8*1024**3},'Config':{'Image':image}}]),
        ('exec','guardrails-evolution','cat','/sys/fs/cgroup/cpu.max'):'600000 100000',
        ('exec','guardrails-evolution','sha256sum','/usr/local/share/evolution-offline.nft'):s.digest(nft)+'  rules',
        ('exec','guardrails-evolution','nft','-j','list','table','inet','evolution_offline'):json.dumps({'nftables':rows}),
        ('exec','guardrails-evolution','readlink','/proc/self/ns/net'):'net:[2]'}
    calls=[]
    def execute(argv, **kwargs):
        assert argv[:3]==('/usr/bin/docker','--host',s.network.SOCKET)
        assert kwargs['timeout']<=10 and kwargs['maximum']==1024**2 and kwargs['input_text'] is None
        calls.append(argv);return outputs[argv[3:]]
    checker=s.CurrentChecker(N(deployment=N(root=root)),source_root=root,authorization_raw=b'',authorization_sha256='',
        preflight_raw=b'',preflight_sha256='',node_boundary_raw=b'',executor=execute)
    source={'deploy/evolution/bootstrap.py':(credentials_fixture.ROOT/'deploy/evolution/bootstrap.py').read_bytes(),
            'deploy/evolution/images/node/offline.nft':nft}
    material={'prepared.json':canonical({'artifacts':[{'name':'kicbase-offline','kind':'image','source_url':'docker://'+image}]}),
              'owner.json':canonical({'container_id':'c'*64})}
    preflight={'images':{'node_boundary_snapshot':expected}}
    monkeypatch.setattr(os,'readlink',lambda p:'net:[1]')
    def invoke():return checker._boundary(preflight,material,source,s.time.monotonic()+5)
    return N(**locals())


def test_actual_original_node_boundary_uses_only_five_native_reads(native_boundary):
    f=native_boundary;f.invoke()
    assert len(f.calls)==5 and not (f.root/'deploy/evolution/images/node/offline.nft').exists()


@pytest.mark.parametrize('change',['limits','quota','image','source','policy','namespace','preflight'])
def test_current_node_boundary_mismatch_refused(native_boundary, change):
    f=native_boundary
    if change in {'limits','image'}:
        key=('container','inspect','guardrails-evolution');node=f.json.loads(f.outputs[key])
        if change=='limits':node[0]['HostConfig']['Memory']=16*1024**3
        else:node[0]['Config']['Image']='foreign'
        f.outputs[key]=f.json.dumps(node)
    elif change=='quota':f.outputs[('exec','guardrails-evolution','cat','/sys/fs/cgroup/cpu.max')]='max 100000'
    elif change=='source':f.source['deploy/evolution/images/node/offline.nft']=b'changed'
    elif change=='policy':
        f.rows[0]['chain']['policy']='accept'
        f.outputs[('exec','guardrails-evolution','nft','-j','list','table','inet','evolution_offline')]=f.json.dumps({'nftables':f.rows})
    elif change=='namespace':f.outputs[('exec','guardrails-evolution','readlink','/proc/self/ns/net')]='net:[1]'
    elif change=='preflight':f.expected['cpu_limit']=7
    with pytest.raises(RuntimeError):f.invoke()


@pytest.mark.parametrize('change', ['raw', 'hash', 'snapshot'])
def test_original_node_boundary_receipt_is_required_before_native_io(flow, change):
    if change == 'raw':
        flow.checker.node_boundary_raw = b'{}'
    else:
        pref = copy.deepcopy(flow.pref)
        if change == 'hash':
            pref['node_boundary']['sha256'] = pref['expected']['node_boundary_sha256'] = 'a'*64
        else:
            pref['images']['node_boundary_snapshot'] = {'different': 1}
        raw = canonical(pref)
        auth = copy.deepcopy(flow.case.authorization)
        auth['preflight']['sha256'] = s.digest(raw)
        auth['receipts']['node_boundary']['sha256'] = pref['node_boundary']['sha256']
        flow.checker.preflight_raw, flow.checker.preflight_sha256 = raw, s.digest(raw)
        flow.checker.authorization_raw = canonical(auth)
        flow.checker.authorization_sha256 = s.digest(canonical(auth))
    with pytest.raises(s.ProvenanceCheckError):
        observe(flow)
    assert not flow.calls
