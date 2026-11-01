"""Real captured original setup semantics over synthetic files/native/SQL ports."""
from __future__ import annotations

import base64
import copy
import fcntl
import hashlib
import os
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace as N
from uuid import UUID

import pytest
from evolution import classifier_provenance as p
from evolution.classifier_provenance_io import Budget, StableReader
from guardrails.evolution.classifier_contract import MODEL_ID, WEIGHTS_SHA256
from guardrails.evolution.models import (
    Destination,
    ProviderProfile,
    with_resource_digest,
)

from mvp.tests.evolution.e2e import classifier_readonly_cluster as s
from mvp.tests.evolution.unit import test_classifier_provenance as pure
from mvp.tests.evolution.unit import test_credentials as renewal
from mvp.tests.evolution.unit import test_evaluator_config as config_fixture

ROOT = Path(__file__).resolve().parents[4]
certificate = config_fixture.certificate


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    import socket
    import subprocess
    def denied(*args, **kwargs):
        raise AssertionError('actual native/network operation forbidden')
    monkeypatch.setattr(subprocess, 'Popen', denied)
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket, 'getaddrinfo', denied)
    monkeypatch.setattr(p, 'BASELINE_CORPORA', {key: hashlib.sha256(p.canonical(pure.synthetic_corpus(key)) + b'\n').hexdigest()
        for key in ('original', 'heldout_1', 'heldout_2')})


def write(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_bytes(raw); path.chmod(0o600)
    return {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest()}


def test_source_local_import_substitution_never_mutates_global_modules(tmp_path):
    import sys
    sentinel = N(value=17)
    before = dict(sys.modules)
    module = s.captured(b'from synthetic_module import value\ndef result(): return value\n', tmp_path/'source.py',
        'synthetic_source', imports={'synthetic_module': sentinel})
    assert module.result() == 17 and dict(sys.modules) == before and 'synthetic_module' not in sys.modules


@pytest.mark.parametrize('kind', ['missing', 'symlink', 'hardlink', 'permissions', 'already_held', 'public_state'])
def test_constructor_lock_never_creates_or_adopts_invalid_lock(tmp_path, kind):
    tmp_path.chmod(0o700); path = tmp_path/'.lock'; fd = None
    if kind != 'missing': write(path, b'')
    if kind == 'symlink': path.rename(tmp_path/'original'); path.symlink_to(tmp_path/'original')
    elif kind == 'hardlink': os.link(path, tmp_path/'alias')
    elif kind == 'permissions': path.chmod(0o644)
    elif kind == 'public_state': tmp_path.chmod(0o755)
    elif kind == 'already_held':
        fd = os.open(path, os.O_RDWR); fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises((s.ReadOnlyConstructionError, OSError)), s.existing_lock(tmp_path, budget=Budget(time.monotonic()+5)):
            pytest.fail('invalid lock acquired')
        assert path.exists() == (kind != 'missing')
    finally:
        if fd is not None: os.close(fd)


def test_original_lock_is_observed_and_released_without_replacement(tmp_path):
    write(tmp_path/'.lock', b''); before=(tmp_path/'.lock').stat()
    with s.existing_lock(tmp_path, budget=Budget(time.monotonic()+5)):
        s.current.held_lock(tmp_path, deadline=time.monotonic()+2)
    after=(tmp_path/'.lock').stat()
    assert (before.st_dev,before.st_ino)==(after.st_dev,after.st_ino)
    with pytest.raises(s.current.ProvenanceCheckError):
        s.current.held_lock(tmp_path, deadline=time.monotonic()+2)


@pytest.fixture
def flow(tmp_path, monkeypatch, certificate):
    tmp_path.chmod(0o700);state=tmp_path/'state';state.mkdir(mode=0o700)
    saved=renewal.saved.__wrapped__(state);directory,owner,inventory,journal,journal_path=saved
    c=pure.case();owner.update(profile='guardrails-evolution',uid=os.getuid(),state=str(state),
        owner_id=c.expected['owner_id'],container_id=c.expected['node_id'])
    journal['owner_id']=owner['owner_id']
    ports={name:5100+i for i,name in enumerate(sorted(s.current.FORWARDS))}
    access=p.document((directory/'access.json').read_bytes())
    access.update(schema_version='guardrails-evolution-local-access-v1',profile='classifier',
        ca_file=str(state/'credentials/ca.crt'),database_url_file=str(directory/'private/database-app-url'),
        seed_inventory_file=str(directory/'seed/inventory.json'),ollama_url='https://localhost:5999')
    for name in ('management','runtime','mock'):access[name+'_url']='https://localhost:'+str(ports[name])
    for tenant in ('team-a','team-b'):
        access['tenants'][tenant].update(gateway_url='https://localhost:'+str(ports[tenant]),gateway_token_file=inventory['tenants'][tenant]['gateway_token_file'])
    write(directory/'access.json',p.canonical(access))
    journal['metadata']['access.json']['after']=base64.b64encode(p.canonical(access)).decode()
    prior_access=copy.deepcopy(access)
    for tenant in ('team-a','team-b'):
        prior_access['tenants'][tenant]['workload_token_file']=inventory['tenants'][tenant]['roles']['workload']['token_file']
    journal['metadata']['access.json']['before']=base64.b64encode(p.canonical(prior_access)).decode()
    renewal.publish(saved)
    for name in s.current.MANDATORY_PRIVATE:
        if not (state/name).exists():write(state/name,b'synthetic-placeholder')
    prepared={'artifacts':[{'kind':'image','name':'evolution-service','source_url':'docker://synthetic/runtime@sha256:'+pure.SHA},
        {'kind':'image','name':'evolution-classifier','source_url':'docker://synthetic/classifier@sha256:'+pure.SHA}]}
    write(state/'owner.json',p.canonical(owner));write(state/'prepared.json',p.canonical(prepared))
    write(directory/'ports.json',p.canonical(ports));write(state/'credentials/ca.crt',certificate)
    write(directory/'tls/gr-platform/classifier/ca.crt',certificate)
    write(directory/'private/classifier.token',b'x'*40+b'\n')
    write(state/'.lock',b'')
    rows=[]
    for index,tenant in enumerate(('team-a','team-b')):
        def resource(cls,tenant=tenant,**kwargs):
            return with_resource_digest(cls(tenant_id=tenant,created_at='2026-10-12T00:00:00Z',**kwargs)).model_dump(mode='json')
        destination=resource(Destination,destination_id=str(UUID(int=2+index*10)),name='classifier',host='classifier.gr-platform.svc.cluster.local',
            port=8443,path_prefix='/v1/classifier',cidrs=('10.42.0.5/32',),zone='local-shared',purpose='evaluation',tls_ca_ref='classifier-ca')
        provider=resource(ProviderProfile,provider_id=str(UUID(int=1+index*10)),kind='evaluator',vendor='local_classifier',model_id=MODEL_ID,
            artifact_digest=WEIGHTS_SHA256,destination_id=destination['destination_id'],zone='local-shared',credential_ref='classifier-auth')
        rows.append({'tenant_id':tenant,'provider':provider,'destination':destination,'language_profile':'en',
            'bearer_token_file':str(directory/'private/classifier.token'),'ca_file':str(directory/'tls/gr-platform/classifier/ca.crt')})
    config={'schema_version':'evolution-evaluator-endpoints-v1','classifier':rows,'bedrock':[]}
    config_ref=write(tmp_path/'evaluator.json',p.canonical(config))
    source={name:(ROOT/name).read_bytes() for name in set(s.SOURCE_PATHS.values())|s.current.REQUIRED}
    sources={name:hashlib.sha256(raw).hexdigest() for name,raw in source.items()}
    pref=c.preflight;auth=c.authorization;auth['sources']=sources;pref['sources']=sources
    pref['images']={'runtime':'runtime-1','native_images':{'runtime':{},'classifier':{}},'node_boundary_snapshot':{'synthetic':True}}
    boundary_ref=write(tmp_path/'boundary.json',p.canonical(pref['images']['node_boundary_snapshot']))
    auth['receipts']['node_boundary']=pref['node_boundary']=boundary_ref
    material={file.relative_to(state).as_posix():file.read_bytes() for file in state.rglob('*') if file.is_file() and file.name!='.lock'}
    pref['private_files']={name:hashlib.sha256(raw).hexdigest() for name,raw in material.items()}
    pref['binding']['files']={'owner.json':pref['private_files']['owner.json']}
    pref['expected']['binding_sha256']=p.digest(pref['binding'])
    pref['expected'].update(credential_generation=journal['generation'],credential_journal_sha256=hashlib.sha256(journal_path.read_bytes()).hexdigest(),
        prepared_sha256=hashlib.sha256(material['prepared.json']).hexdigest(),node_boundary_sha256=boundary_ref['sha256'])
    for key,field in [('sources','sources_sha256'),('private_files','private_files_sha256')]:pref['expected'][field]=p.digest(pref[key])
    for key,field in [('prepared','prepared_sha256'),('credentials','credential_journal_sha256')]:
        auth['receipts'][key]['sha256']=pref['expected'][field];pref['receipts'][key]=copy.deepcopy(auth['receipts'][key])
    auth['preflight']=write(tmp_path/'preflight.json',p.canonical(pref));auth_ref=write(tmp_path/'authorization.json',p.canonical(auth))
    budget=Budget(time.monotonic()+120)
    reader=StableReader(budget,public_files={str(ROOT/name):hashed for name,hashed in sources.items()})
    manifest={'authorization':auth_ref,'case_id':c.case_id,'nodeid':auth['case']['nodeid'],'registry_sha256':auth['registry_sha256'],
        'sources':sources,'corpus_manifest':auth['corpus_manifest'],'evaluator_config':config_ref,'deployment_state':str(state)}
    context={'authorization':auth,'preflight':pref,'proof':{}}
    original_datetime=s.datetime
    class Clock(original_datetime):
        @classmethod
        def now(cls,tz=None):return pure.NOW
    monkeypatch.setattr(s,'datetime',Clock)
    events=[];fault=[None]
    Original=s.current.CurrentChecker
    class Checker(Original):
        def observe_locked(self,**kwargs):
            s.current.held_lock(state,deadline=kwargs['deadline']);events.append('current')
            if fault[0]=='current' or fault[0]=='current_after' and events.count('current')==2:raise RuntimeError('synthetic current refusal')
            assert events.count('current')==2 or 'http' not in events
            return {'synthetic_current':True}
    monkeypatch.setattr(s.current,'CurrentChecker',Checker)
    service={'kind':'Service','metadata':{'name':'classifier','namespace':'gr-platform','uid':str(UUID(int=50)),
        'labels':{'guardrails.evolution/owner':owner['owner_id']}},'spec':{'clusterIP':'10.42.0.5','type':'ClusterIP',
        'selector':{'guardrails.evolution/component':'classifier'},'ports':[{'name':'https','port':8443,'targetPort':8443,'protocol':'TCP'}]}}
    def executor(argv,**kwargs):
        assert tuple(argv[-4:])==s.GET;events.append('native')
        if fault[0]=='native':raise RuntimeError('synthetic private native failure')
        return p.canonical({'items':[service]}).decode()
    class Observer:
        def __init__(self,cluster,*,runtime,deadline,executor):self.executor,self.deadline=executor,deadline
        def _capture(self):
            self.executor(('synthetic',*s.GET),deadline=self.deadline,maximum=1048576,timeout=10,input_text=None)
            return copy.deepcopy(pref['binding']),{},{}
    monkeypatch.setattr(s.current,'BoundedObserver',Observer)
    import httpx
    class Http:
        def __init__(self,**kwargs):
            assert kwargs['trust_env'] is False and kwargs['follow_redirects'] is False
            events.append('http')
        @contextmanager
        def stream(self,method,url,*,headers,timeout):
            assert method=='GET' and 0<timeout<=5;events.append('health')
            if fault[0]=='health':raise RuntimeError('synthetic private HTTP failure')
            yield N(status_code=200,headers={},iter_raw=lambda **kwargs:iter([b'{}']))
        def close(self):
            events.append('http_close')
            if fault[0]=='dual_close':raise OSError('synthetic close failure')
    monkeypatch.setattr(httpx,'Client',Http)
    class Repo:
        def close(self):events.append('repo_close')
    def repository(*args,**kwargs):
        events.append('repository')
        if fault[0]=='repository':raise OSError('synthetic private SQL failure')
        return Repo(),os.memfd_create('synthetic-ca',os.MFD_CLOEXEC)
    monkeypatch.setattr(s,'read_only_repository',repository)
    constructor=s.ReadOnlyClusterConstructor(source_root=ROOT,reader=reader,executor=executor)
    return N(**locals())


def test_complete_constructor_reuses_original_credentials_config_and_bundle_without_access(flow):
    f=flow;cluster=f.constructor(context=f.context,manifest=f.manifest,deadline=f.budget.deadline)
    try:
        assert f.events==['native','current','http','repository',*(['health']*5),'current']
        assert cluster.credentials['generation']==f.journal['generation']
        assert set(cluster.endpoints)=={'team-a','team-b'}
        assert all(row['bearer_token_file']=='/config/classifier.token' and row['ca_file']=='/config/ca.crt' for row in cluster.endpoints.values())
        with pytest.raises(s.ReadOnlyConstructionError):cluster.deployment.access()
        with pytest.raises(s.ReadOnlyConstructionError):cluster.platform()
        assert callable(cluster.journey) and callable(cluster.assert_exact_audit)
    finally:cluster.close()
    assert f.events[-2:]==['http_close','repo_close']


@pytest.mark.parametrize('fault',['native','current','repository','health','current_after'])
def test_constructor_failure_is_finite_closes_all_owned_resources_and_never_repairs(flow,fault):
    f=flow;f.fault[0]=fault
    with pytest.raises(s.ReadOnlyConstructionError,match='^classifier_readonly_constructor_failed$'):
        f.constructor(context=f.context,manifest=f.manifest,deadline=f.budget.deadline)
    assert f.events.count('native')==1
    assert ('http' in f.events)==(fault not in {'native','current'})
    assert f.events.count('http_close')==f.events.count('http')
    assert f.events.count('repo_close')==(1 if fault in {'health','current_after'} else 0)


@pytest.mark.parametrize('fault',['provider','destination','token','ca','service_owner','service_address','ambient_config'])
def test_original_operator_binding_semantics_refuse_before_http_clients(flow,fault):
    f=flow
    if fault=='service_owner':f.service['metadata']['labels']['guardrails.evolution/owner']='foreign'
    elif fault=='service_address':f.service['spec']['clusterIP']='10.42.0.6'
    elif fault=='ambient_config':f.manifest['evaluator_config']['path']=str(f.state/'missing')
    else:
        row=f.config['classifier'][0]
        if fault=='provider':row['provider']['model_id']='foreign'
        elif fault=='destination':row['destination']['cidrs']=['10.42.0.6/32']
        elif fault=='token':row['bearer_token_file']=str(f.state/'not-pinned.token')
        else:row['ca_file']=str(f.state/'not-pinned.crt')
        f.manifest['evaluator_config']=write(Path(f.config_ref['path']),p.canonical(f.config))
    with pytest.raises(s.ReadOnlyConstructionError):
        f.constructor(context=f.context,manifest=f.manifest,deadline=f.budget.deadline)
    assert 'http' not in f.events and 'repository' not in f.events


def test_cleanup_attempts_both_clients_and_ca_after_first_close_error(flow):
    f=flow; cluster=f.constructor(context=f.context,manifest=f.manifest,deadline=f.budget.deadline)
    fd=cluster._ca_fd; f.fault[0]='dual_close'
    with pytest.raises(s.ReadOnlyConstructionError,match='^classifier_readonly_cleanup_failed$'):cluster.close()
    assert f.events[-2:]==['http_close','repo_close'] and cluster.http is cluster.repo is cluster._ca_fd is None
    with pytest.raises(OSError):os.fstat(fd)
    cluster.close()


@pytest.mark.parametrize('fault',['authorization','preflight','configuration','private','expired'])
def test_constructor_refuses_stale_authority_or_snapshot_before_clients(flow,fault):
    f=flow
    if fault=='authorization':Path(f.auth_ref['path']).write_bytes(b'changed')
    elif fault=='preflight':Path(f.auth['preflight']['path']).write_bytes(b'changed')
    elif fault=='configuration':Path(f.config_ref['path']).write_bytes(b'changed')
    elif fault=='private':(f.state/'credentials/ca.crt').write_bytes(b'changed')
    else:f.budget.deadline=time.monotonic()-1
    with pytest.raises(s.ReadOnlyConstructionError):f.constructor(context=f.context,manifest=f.manifest,deadline=f.budget.deadline)
    assert 'http' not in f.events and 'repository' not in f.events


@pytest.fixture
def database_port(tmp_path,monkeypatch):
    import sqlalchemy
    from sqlalchemy.engine import Engine
    events=[];fault=[None];captured=[]
    for key in tuple(os.environ):
        if key.startswith('PG'):monkeypatch.delenv(key)
    class FakeEngine(Engine):
        def __init__(self):pass
        def connect(self):
            @contextmanager
            def connection():
                events.append('connect')
                if fault[0]=='connect':raise OSError('synthetic SQL detail')
                yield self
            return connection()
        def execute(self,statement):
            sql=str(statement);events.append(sql)
            if 'transaction_read_only' in sql:
                row=['on',False,datetime.now(UTC),True]
                if fault[0]=='writable':row[0]='off'
                elif fault[0]=='recovery':row[1]=True
                elif fault[0]=='plaintext':row[3]=False
                elif fault[0]=='clock':row[2]=datetime(2020,1,1,tzinfo=UTC)
                return N(one=lambda:row)
            if 'rolsuper' in sql:return N(one=lambda:N(rolsuper=fault[0]=='superuser',rolbypassrls=fault[0]=='bypass'))
            assert 'pg_class' in sql
            return N(scalar_one=lambda:fault[0]=='owner')
        def dispose(self):events.append('dispose')
    engine=FakeEngine()
    def create(url,**kwargs):
        captured.append((url,kwargs));events.append('create')
        fd=int(url.query['sslrootcert'].removeprefix('/proc/self/fd/'))
        assert os.pread(fd,128,0)==b'synthetic-ca'
        assert fcntl.fcntl(fd,fcntl.F_GET_SEALS)==fcntl.F_SEAL_WRITE|fcntl.F_SEAL_GROW|fcntl.F_SEAL_SHRINK|fcntl.F_SEAL_SEAL
        return engine
    monkeypatch.setattr(sqlalchemy,'create_engine',create)
    material={'credentials/ca.crt':b'synthetic-ca','assembly/private/database-app-url':
        ('postgresql+psycopg://evolution_app:synthetic-password@localhost:5544/guardrails_evolution?sslmode=verify-full&sslrootcert='+str(tmp_path/'credentials/ca.crt')).encode()}
    return N(**locals())


def test_real_repository_role_checks_and_sealed_ca_use_only_readonly_tls_engine(database_port):
    f=database_port
    repository,fd=s.read_only_repository(f.material,state=f.tmp_path,port=5544,deadline=time.monotonic()+10)
    try:
        assert repository.engine is f.engine
        url,options=f.captured[0]
        assert url.username=='evolution_app' and url.query['sslmode']=='verify-full'
        assert options['hide_parameters'] and options['pool_size']==1 and options['max_overflow']==0
        assert options['connect_args']=={'connect_timeout':2,'options':'-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=5000'}
        assert len([e for e in f.events if e.startswith('SELECT')])==3
    finally:repository.close();os.close(fd)
    assert f.events[-1]=='dispose'


@pytest.mark.parametrize('fault',['connect','writable','recovery','plaintext','clock','superuser','bypass','owner'])
def test_database_authentication_refusal_disposes_engine_and_sealed_ca(database_port,fault):
    f=database_port;f.fault[0]=fault
    with pytest.raises((s.ReadOnlyConstructionError,ValueError,OSError)):
        s.read_only_repository(f.material,state=f.tmp_path,port=5544,deadline=time.monotonic()+10)
    assert f.events[-1]=='dispose'
    fd=int(f.captured[0][0].query['sslrootcert'].removeprefix('/proc/self/fd/'))
    with pytest.raises(OSError):os.fstat(fd)


@pytest.mark.parametrize('fault',['ambient','role','host','port','tls','expiry'])
def test_database_rejects_ambient_or_unbound_destination_before_engine(database_port,monkeypatch,fault):
    f=database_port;deadline=time.monotonic()+10
    if fault=='ambient':monkeypatch.setenv('PGSERVICE','synthetic')
    elif fault=='expiry':deadline=time.monotonic()+1
    else:
        old,new={'role':('evolution_app','postgres'),'host':('localhost','foreign.invalid'),
            'port':(':5544/',':5545/'),'tls':('verify-full','require')}[fault]
        f.material['assembly/private/database-app-url']=f.material['assembly/private/database-app-url'].replace(old.encode(),new.encode())
    with pytest.raises((s.ReadOnlyConstructionError,s.current.ProvenanceCheckError)):
        s.read_only_repository(f.material,state=f.tmp_path,port=5544,deadline=deadline)
    assert not f.captured
