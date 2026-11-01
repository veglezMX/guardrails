"""Actual pure authorization/recorder and private writes over synthetic ports."""
from __future__ import annotations

import base64
import hashlib
import time
from pathlib import Path
from types import SimpleNamespace as N

import pytest
from evolution import classifier_provenance_io_v3 as io
from evolution import classifier_provenance_v3 as p

from mvp.tests.evolution.e2e import classifier_fixture_builder_v3 as s
from mvp.tests.evolution.unit import test_classifier_provenance_io_v3 as existing
from mvp.tests.evolution.unit import (
    test_classifier_worker_invocation_v3 as worker_fixture,
)

filesystem = existing.filesystem
flow = existing.flow
isolated = existing.isolated


@pytest.fixture
def built(flow,monkeypatch):
    f=flow;f.checks.close();f.checks.path.rmdir()
    f.f.budget.deadline=time.monotonic()+900
    manifest,expected=worker_fixture.manifest(f)
    monkeypatch.setenv('EVOLUTION_TEST_EVIDENCE_DIR',f.c.authorization['output_directory'])
    ref=existing.put(f.f.root/'worker-manifest.json',p.canonical(manifest)+b'\n')
    original=io.StableReader
    class Reader(original):
        def __call__(self,reference):
            self.budget.check()
            if Path(reference['path']).is_relative_to(f.f.root):return super().__call__(reference)
            return f.c.store.load(reference)
    monkeypatch.setattr(io,'StableReader',Reader)
    class Clock(s.datetime):
        @classmethod
        def now(cls,tz=None):return existing.NOW
    monkeypatch.setattr(s,'datetime',Clock)
    events=[];fault=[None];created=[]
    def sources(value,root,reader):
        events.append('sources');assert value==manifest and root==f.f.root
        reader.budget.check()
        if fault[0]=='sources':raise OSError('synthetic private source detail')
    monkeypatch.setattr(s,'loaded_sources',sources)
    class Executor(s.native.NativeReadOnlyExecutor):
        def __init__(self,*args):
            events.append('executor');super().__init__(*args);created.append(self)
    monkeypatch.setattr(s.native,'NativeReadOnlyExecutor',Executor)
    class Constructor:
        def __init__(self,*,source_root,reader,executor):
            events.append('constructor');self.executor,self.reader=executor,reader
        def __call__(self,*,context,manifest,deadline):
            events.append('construct')
            assert context['authorization']==f.c.authorization and deadline<=expected['deadline']
            self.executor._attempts.append({'spawned':True,'stdout':b'synthetic-native-output','stderr':b'private-warning',
                'truncated':False,'cleanup':'completed','status':'completed'})
            if fault[0]=='construct':raise OSError('synthetic constructor private detail')
            executor=self.executor
            class Checker(s.current.CurrentChecker):
                def __init__(self):self.authorization_sha256,self.executor=manifest['authorization']['sha256'],executor
                def observe_locked(self,*,phase,operation,deadline):
                    events.append('current');assert deadline<=expected['deadline']
                    if fault[0]=='current':raise OSError('synthetic current private detail')
                    return f.c.options['current'](phase=phase,operation=operation,deadline=12345)
            def close():
                events.append('close')
                if fault[0]=='close':raise OSError('synthetic cleanup private detail')
            return N(close=close,_classifier_current_checker=Checker())
    monkeypatch.setattr(s.readonly,'ReadOnlyClusterConstructor',Constructor)
    def qualify(**kwargs):
        events.append('qualify')
        if fault[0]=='qualify':raise OSError('synthetic final worker refused')
        return f.c.options['qualify_final_worker'](**kwargs)
    def open_plugin():return s.build_worker_plugin(ref,expected,f.f.root,qualify_final_worker=qualify)
    def claim(plugin):
        request=N(node=N(nodeid=expected['nodeid']))
        selected=s.fixtures.ClassifierFixturePlugin.classifier_provenance_context.__wrapped__(plugin,request)
        return selected,selected.cluster_factory(deadline=expected['deadline']-1)
    return N(**locals())


def test_context_creation_is_inert_and_auth_precedes_executor_and_clients(built):
    b=built;manager=b.open_plugin();assert b.events==[] and not b.f.checks.path.exists()
    with manager as plugin:
        assert b.events.index('qualify')<b.events.index('executor')<b.events.index('constructor')
        assert 'construct' not in b.events and 'current' not in b.events
        context,cluster=b.claim(plugin)
        assert context.cluster is cluster
        for phase in ('before','after'):
            result=context.verify(phase=phase,operation=None,binding=b.f.c.binding,sources=existing.SOURCES,deadline=b.expected['deadline']-2)
            assert result['parent_invocation']==b.f.c.auth_ref['sha256']
        assert len(context.recorder.refs)==2 and context.recorder.verifier.completion_ref is None
    assert b.events[-1]=='close'
    saved=p.document((b.f.checks.path/'native-attempts/index.json').read_bytes())
    assert saved['outcome']=='worker_returned' and saved['operational_success_claim'] is False
    assert len(saved['attempts'])==1
    row=p.document(Path(saved['attempts'][0]['path']).read_bytes())
    assert base64.b64decode(row['stdout_base64'])==b'synthetic-native-output'
    assert base64.b64decode(row['stderr_base64'])==b'private-warning'
    assert all(file.stat().st_mode&0o777==0o600 for file in b.f.checks.path.rglob('*') if file.is_file())


@pytest.mark.parametrize('fault',['qualify','sources'])
def test_failed_authority_never_creates_directories_or_native_executor(built,fault):
    b=built;b.fault[0]=fault
    with pytest.raises(s.FixtureBuildError,match='^classifier_fixture_worker_failed$'),b.open_plugin():pytest.fail('yielded invalid authority')
    assert 'executor' not in b.events and 'construct' not in b.events and not b.f.checks.path.exists()


@pytest.mark.parametrize('fault',['construct','current','body','close'])
def test_partial_attempts_retained_on_constructor_current_body_or_cleanup_failure(built,fault):
    b=built;b.fault[0]=fault
    with pytest.raises(s.FixtureBuildError) as caught,b.open_plugin() as plugin:
        context,_=b.claim(plugin)
        if fault=='current':context.verify(phase='before',operation=None,binding=b.f.c.binding,sources=existing.SOURCES,deadline=b.expected['deadline']-1)
        elif fault=='body':raise RuntimeError('arbitrary private case text')
    assert 'private' not in str(caught.value)
    saved=p.document((b.f.checks.path/'native-attempts/index.json').read_bytes())
    assert saved['outcome']=='worker_failed' and len(saved['attempts'])==1
    assert not (b.f.journal.path/'completion.json').exists()
    assert b.events.count('close')==(0 if fault=='construct' else 1)


def test_missing_final_adapter_refuses_before_manifest_read(built,monkeypatch):
    b=built
    monkeypatch.setattr(s.invocation,'load',lambda *a,**kw:pytest.fail('read before adapter check'))
    with pytest.raises(s.FixtureBuildError),s.build_worker_plugin(b.ref,b.expected,b.f.f.root,qualify_final_worker=None):pass
    assert b.events==[]


def test_existing_check_directory_is_not_reused(built):
    b=built;b.f.checks.path.mkdir(mode=0o700);marker=b.f.checks.path/'original';marker.write_bytes(b'preserve')
    with pytest.raises(s.FixtureBuildError),b.open_plugin():pytest.fail('reused old invocation')
    assert marker.read_bytes()==b'preserve' and 'executor' not in b.events


def test_retention_failure_cannot_turn_worker_return_into_pass(built,monkeypatch):
    b=built
    def refuse(*args,**kwargs):raise OSError('private write details')
    monkeypatch.setattr(s,'save_attempts',refuse)
    with pytest.raises(s.FixtureBuildError,match='^classifier_fixture_cleanup_failed') as caught,b.open_plugin() as plugin:
        b.claim(plugin)
    assert str(caught.value)=='classifier_fixture_cleanup_failed'
    assert caught.value.__notes__==['classifier_fixture_retention_failed'] and b.events[-1]=='close'


def test_primary_failure_survives_cleanup_and_retention_errors(built,monkeypatch):
    b=built;b.fault[0]='close'
    monkeypatch.setattr(s,'save_attempts',lambda *a,**kw:(_ for _ in ()).throw(OSError('private write detail')))
    with pytest.raises(s.FixtureBuildError,match='^classifier_fixture_worker_failed') as caught,b.open_plugin() as plugin:
        b.claim(plugin);raise OSError('original private error')
    assert str(caught.value)=='classifier_fixture_worker_failed'
    assert caught.value.__notes__==['classifier_fixture_cluster_cleanup_failed','classifier_fixture_retention_failed']


def test_expired_original_budget_is_never_refreshed_for_retention(built):
    b=built
    with pytest.raises(s.FixtureBuildError) as caught,b.open_plugin() as plugin:
        context,_=b.claim(plugin);context.recorder.directory.budget.deadline=time.monotonic()-1
    assert caught.value.__notes__==['classifier_fixture_retention_failed']
    assert not (b.f.checks.path/'native-attempts/index.json').exists() and b.events[-1]=='close'


@pytest.mark.parametrize('fault',['wrong_root','wrong_hash','wrong_registry','missing_private_manifest'])
def test_parent_manifest_expected_scope_is_not_inferred_from_manifest(built,fault):
    b=built
    if fault=='wrong_root':b.expected['root']=b.f.f.root/'foreign'
    elif fault=='wrong_hash':b.ref['sha256']='b'*64
    elif fault=='wrong_registry':b.expected['registry_sha256']='b'*64
    else:Path(b.ref['path']).chmod(0o644)
    with pytest.raises(s.FixtureBuildError),b.open_plugin():pytest.fail('accepted unbound manifest')
    assert not b.events and not b.f.checks.path.exists()


@pytest.mark.parametrize('fault',['loaded_path','source_bytes','public_pin','required_source'])
def test_loaded_sources_checks_actual_module_locations_and_public_pins(tmp_path,monkeypatch,fault):
    root=tmp_path/'source';root.mkdir(mode=0o700);sources={}
    for name,relative in s.MODULES.items():
        path=root/relative;path.parent.mkdir(parents=True,exist_ok=True,mode=0o700);path.write_bytes(b'synthetic-source');path.chmod(0o644)
        sources[relative]=hashlib.sha256(path.read_bytes()).hexdigest()
        monkeypatch.setattr(s.sys.modules[name],'__file__',str(path))
    for relative in s.REQUIRED:sources.setdefault(relative,existing.SHA)
    reader=io.StableReader(io.Budget(time.monotonic()+10),public_files={str(root/name):hashed for name,hashed in sources.items()})
    s.loaded_sources({'sources':sources},root,reader)
    first=next(iter(s.MODULES));relative=s.MODULES[first]
    if fault=='loaded_path':monkeypatch.setattr(s.sys.modules[first],'__file__',str(root/'foreign.py'))
    elif fault=='source_bytes':(root/relative).write_bytes(b'changed')
    elif fault=='public_pin':reader.public_files.pop(str(root/relative))
    else:sources.pop(relative)
    with pytest.raises(existing.EvidenceError):s.loaded_sources({'sources':sources},root,reader)


@pytest.mark.parametrize('fault',['private_stdout_type','maximum_bytes','maximum_rows','unknown_field','unknown_status'])
def test_attempt_retention_closed_shape_and_bounds(filesystem,fault):
    f=filesystem;directory=io.PrivateDirectory(f.root/'native',f.budget,create=True)
    row={'spawned':True,'stdout':b'abc','stderr':b'','truncated':False,'cleanup':'completed','status':'completed'}
    rows=[row]
    if fault=='private_stdout_type':row['stdout']='not bytes'
    elif fault=='maximum_bytes':row['stdout']=b'x'*(s.native.MAX_OUTPUT+1)
    elif fault=='maximum_rows':rows*=s.native.MAX_ATTEMPTS+1
    elif fault=='unknown_field':row['argv']=['forbidden']
    else:row['status']='passed'
    try:
        with pytest.raises(existing.EvidenceError):s.save_attempts(N(attempts=rows),directory,authorization_ref={'path':'/synthetic/auth','sha256':existing.SHA},outcome='worker_failed')
        assert not (directory.path/'index.json').exists()
    finally:directory.close()


def test_partial_private_retention_survives_later_write_failure(filesystem,monkeypatch):
    f=filesystem;directory=io.PrivateDirectory(f.root/'native',f.budget,create=True)
    row={'spawned':True,'stdout':b'first','stderr':b'','truncated':False,'cleanup':'completed','status':'failed'}
    real=directory.document
    def write(name,value):
        if name=='002.json':raise OSError('synthetic persistence failure')
        return real(name,value)
    monkeypatch.setattr(directory,'document',write)
    try:
        with pytest.raises(OSError):s.save_attempts(N(attempts=[row,row]),directory,authorization_ref={'path':'/synthetic/auth','sha256':existing.SHA},outcome='worker_failed')
        assert base64.b64decode(p.document((directory.path/'001.json').read_bytes())['stdout_base64'])==b'first'
        assert not (directory.path/'index.json').exists()
    finally:directory.close()


def test_bound_current_callback_cannot_be_used_for_parent_consume(built):
    b=built
    with b.open_plugin() as plugin:
        context,_=b.claim(plugin)
        with pytest.raises(existing.EvidenceError):context.recorder.verifier.current(phase='consume',operation=None,deadline=b.expected['deadline'])
        assert 'current' not in b.events


@pytest.mark.parametrize('fault',['missing','wrong','late'])
def test_authenticated_output_environment_refuses_before_cluster(built,monkeypatch,fault):
    b=built
    if fault=='missing':monkeypatch.delenv('EVOLUTION_TEST_EVIDENCE_DIR')
    elif fault=='wrong':monkeypatch.setenv('EVOLUTION_TEST_EVIDENCE_DIR','/foreign/output')
    with pytest.raises(s.FixtureBuildError),b.open_plugin() as plugin:
        assert fault=='late'
        monkeypatch.setenv('EVOLUTION_TEST_EVIDENCE_DIR','/foreign/late')
        b.claim(plugin)
    assert 'construct' not in b.events and 'current' not in b.events
    if fault!='late':assert 'executor' not in b.events and not b.f.checks.path.exists()


@pytest.mark.parametrize('value',[None,'/foreign/governance'])
def test_authenticated_governance_environment_has_no_ambient_fallback(monkeypatch,value):
    authorization={'output_directory':'/authorized/output','governance_root':'/authorized/governance'}
    monkeypatch.setenv('EVOLUTION_TEST_EVIDENCE_DIR',authorization['output_directory'])
    if value is None:monkeypatch.delenv('EVOLUTION_EVALUATOR_OPERATION_ROOT',raising=False)
    else:monkeypatch.setenv('EVOLUTION_EVALUATOR_OPERATION_ROOT',value)
    with pytest.raises(existing.EvidenceError):s.runtime_environment(authorization)
    monkeypatch.setenv('EVOLUTION_EVALUATOR_OPERATION_ROOT',authorization['governance_root'])
    s.runtime_environment(authorization)
