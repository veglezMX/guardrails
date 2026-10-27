"""Real pure receipt/journal closure with explicit synthetic native boundaries."""
from __future__ import annotations

import copy
import hashlib
import os
import time
from contextlib import contextmanager
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace as N

import pytest
from evolution import classifier_parent_context as s
from evolution import classifier_provenance as p
from evolution import classifier_provenance_io as io
from evolution.diagnostics import write_catalog
from evolution.registry import Case, EvidenceError

from mvp.tests.evolution.unit import test_classifier_provenance as pure
from mvp.tests.evolution.unit import test_classifier_provenance_io as existing

isolated = existing.isolated


@pytest.fixture
def flow(tmp_path, monkeypatch, request):
    family = getattr(request, 'param', 'tls')
    c = pure.case(family); tmp_path.chmod(0o700)
    root = tmp_path / 'repository'; root.mkdir(mode=0o700)
    sources = {}
    for name in s.REQUIRED:
        path = root / name; path.parent.mkdir(parents=True, exist_ok=True)
        raw = ('# synthetic ' + name + '\n').encode(); path.write_bytes(raw)
        sources[name] = hashlib.sha256(raw).hexdigest()
    python = {name: sha for name, sha in sources.items() if name.endswith('.py')}
    c.rollout['sources_sha256'] = p.digest(sources)
    c.cold['rollout'] = c.store.put('rollout-current.json', c.rollout)
    c.receipts['cold'] = c.store.put('cold-current.json', c.cold)
    c.preflight['receipts']['cold'] = c.receipts['cold']
    c.preflight['sources'] = sources; c.expected['sources_sha256'] = p.digest(sources)
    preflight = c.store.put('preflight-current.json', c.preflight)
    c.final['sources_sha256'] = p.digest(sources)
    case = Case(c.case_id, 'synthetic-requirement', 'local', 'remote', 'classifier',
        'mvp/tests/evolution/e2e/synthetic.py::test_case', ('first', 'second'), 900)
    registry = existing.put(root/'mvp/evolution/cases.json', p.canonical({'phases': {'3': {'cases': [{**asdict(case), 'assertions': list(case.assertions)}]}}})+b'\n', 0o644)
    launch = {'path': str(root/'mvp/evolution/worker.py'), 'sha256': python['mvp/evolution/worker.py']}
    binary = root/'python'; binary.write_bytes(b'\x7fELFsynthetic'); binary.chmod(0o700)
    executable = {'path': str(binary), 'sha256': hashlib.sha256(binary.read_bytes()).hexdigest()}
    for name in ('invocations', 'governance', 'state'): (tmp_path/name).mkdir(mode=0o700)
    destination = root/'.evolution/reports/run'; destination.mkdir(parents=True, mode=0o700)
    output = destination/'measurements'/c.case_id; output.mkdir(parents=True, mode=0o700)
    temporary = destination.parent/'.worker-synthetic'; temporary.mkdir(mode=0o700)
    catalog = destination/(c.case_id+'.sources.json'); write_catalog(catalog,root,python)
    outer = time.monotonic()+1800; deadline = time.monotonic()+850
    clock = [pure.NOW]
    monkeypatch.setattr(s,'utc_now',lambda: clock[0])
    public = {str(root/name): sha for name,sha in sources.items()};public[registry['path']]=registry['sha256']
    capsule = {'schema_version':s.SCHEMA,'created_at':pure.stamp(-20),'valid_until':pure.stamp(10000),
        'source_root':str(root),'deadline_monotonic':outer,'registry':registry,'source_catalog':python,
        'identity':c.identity,'corpus_manifest':c.corpus_manifest_ref,'required_corpora':c.corpora,
        'deployment_state':str(tmp_path/'state'),'evaluator_config':{'path':str(tmp_path/'config.yaml'),'sha256':pure.SHA},
        'native_executables':{'docker':{'path':'/usr/bin/docker','sha256':pure.SHA},
            'kubectl':{'path':str(tmp_path/'state/bin/kubectl'),'sha256':pure.SHA}},
        'python_executable':executable,'launcher':launch,'invocations_root':str(tmp_path/'invocations'),
        'governance_root':str(tmp_path/'governance'),'public_files':public,
        'cases':{case.case_id:{'nodeid':case.nodeid,'minimum_assertions':2,'timeout_seconds':900,
            'sources':sources,'preflight':preflight,'receipts':c.receipts}}}
    capsule_ref = existing.put(tmp_path/'capsule.json',p.canonical(capsule)+b'\n')
    original_reader = io.StableReader
    class Reader(original_reader):
        def __call__(self,ref):
            self.budget.check()
            return c.store.load(ref) if ref['path'].startswith('/synthetic/') else super().__call__(ref)
    monkeypatch.setattr(io,'StableReader',Reader)
    events=[]; fault=[None]; constructed=[]
    original_loaded=s.loaded_sources
    def loaded(root_arg,map_arg,reader):
        events.append('sources');assert root_arg==root and map_arg==sources
        if fault[0]=='sources':raise OSError('private source detail')
        reader.budget.check()
    monkeypatch.setattr(s,'loaded_sources',loaded)
    monkeypatch.setattr(s,'executable_identity',lambda ref,budget:events.append('executable'))
    def process(*args):
        events.append('process')
        if fault[0]=='process':raise OSError('private process detail')
    monkeypatch.setattr(s,'process_identity',process)
    def qualify(**kwargs):
        events.append('qualify')
        assert kwargs['sources']==sources and kwargs['identity']==c.identity and kwargs['required_corpora']==c.corpora
        kwargs['load'](kwargs['reference'])
        if fault[0]=='qualify':raise EvidenceError('synthetic final proof refused')
        return copy.deepcopy(c.final)
    class Executor(s.builder.native.NativeReadOnlyExecutor):
        def __init__(self,*args):events.append('executor');super().__init__(*args)
    monkeypatch.setattr(s.builder.native,'NativeReadOnlyExecutor',Executor)
    class Constructor:
        def __init__(self,*,source_root,reader,executor):self.reader,self.executor=reader,executor
        def __call__(self,*,context,manifest,deadline):
            events.append('construct');assert deadline==outer
            self.executor._attempts.append({'spawned':True,'stdout':b'synthetic retained output','stderr':b'private warning',
                'truncated':False,'cleanup':'completed','status':'completed'})
            if fault[0]=='construct':raise OSError('private construct detail')
            executor=self.executor
            class Checker(s.builder.current.CurrentChecker):
                def __init__(self):self.authorization_sha256,self.executor=manifest['authorization']['sha256'],executor
                def observe_locked(self,*,phase,operation,deadline):
                    events.append('current');assert deadline==outer and phase=='consume'
                    if fault[0]=='current':raise OSError('private current detail')
                    return {key:copy.deepcopy(value) for key,value in c.expected.items() if key!='minimum_validity_seconds'}|{
                        'schema_version':p.CURRENT_SCHEMA,'preflight_sha256':preflight['sha256'], 'phase':phase,
                        'operation':operation,'observed_at':clock[0].isoformat(),'elapsed_ms':10}
            @contextmanager
            def lock():events.append('lock');yield;events.append('unlock')
            def close():
                events.append('close')
                if fault[0]=='close':raise OSError('private close detail')
            cluster=N(close=close,deployment=N(lock=lock),_classifier_current_checker=Checker());constructed.append(cluster)
            return cluster
    monkeypatch.setattr(s.builder.readonly,'ReadOnlyClusterConstructor',Constructor)
    if family in {'resource','budget'}:monkeypatch.setenv('EVOLUTION_EVALUATOR_OPERATION_ROOT',capsule['governance_root'])
    context=s.ParentClassifierContext(capsule_ref,root,outer,qualify_final_worker=qualify)
    argv=[str(binary),'-B','-m','evolution.worker',case.nodeid,str(destination/(case.case_id+'.json')),
        '--source-catalog',str(catalog),'--basetemp',str(temporary/'pytest'),'--classifier-case-id',case.case_id,
        '--classifier-registry-sha256',registry['sha256'],'--classifier-deadline',repr(deadline)]
    args={'case':case,'root':root,'destination':destination,'sources':python,'argv':argv,'source_catalog':catalog,
        'registry_sha256':registry['sha256'],'deadline':deadline}
    def prepare():return context.prepare(**args)
    bundle=N(**locals())
    try:yield bundle
    finally:context.close()


def launched(f):
    invocation=f.prepare(); f.clock[0]+=timedelta(seconds=1)
    invocation.started(pid=123,process_start=456,started_at=f.clock[0].isoformat())
    return invocation


def worker_outputs(f, invocation):
    observation=copy.deepcopy(f.c.observation)
    auth=invocation.authorized['authorization'];family=auth['case']['family']
    if family in {'resource','budget'}:
        operation=observation['operation'];directory=Path(auth['governance_root'])/operation;directory.mkdir(mode=0o700)
        refs={}
        for name,ref in f.c.governance_files.items():
            value=p.document(f.c.store.load(ref))
            if name=='intent.json':value['source_sha256']=dict(f.sources)
            refs[name]=existing.put(directory/name,p.canonical(value)+b'\n')
        observation['governance']={'intent_sha256':refs['intent.json']['sha256'],'completion_sha256':refs['completion.json']['sha256'],
            'release_sha256':refs['release-matrix-release.json']['sha256'],'restoration_sha256':refs['release-restore-original-binding.json']['sha256']}
    for name in p.FAMILY_FILES[family]:
        existing.put(f.output/name,p.canonical(observation if name.endswith('observation.json') else {'synthetic':name})+b'\n')
    checks=Path(auth['current_checks_directory']);checks.mkdir(mode=0o700)
    for index,phase in enumerate(p.CHECK_PHASES[family]):
        row={key:copy.deepcopy(value) for key,value in f.c.expected.items() if key!='minimum_validity_seconds'}|{
            'schema_version':p.CURRENT_SCHEMA,'preflight_sha256':f.preflight['sha256'],'phase':phase,
            'operation':observation['operation'],'observed_at':f.clock[0].isoformat(),'elapsed_ms':10}
        existing.put(checks/f'{index+1:02d}-{phase}.json',p.canonical(row)+b'\n')
    f.clock[0]+=timedelta(seconds=1)
    result={'nodeid':f.case.nodeid,'status':'pass','duration_ms':100,'assertions':2,'reason_codes':[]}
    existing.put(invocation.result_path,p.canonical(result)+b'\n')
    return observation,result


def consume_args(f, observation):
    kwargs={'phase':'consume','operation':observation['operation'],'sources':f.sources,'binding':f.c.binding}
    if f.family=='cni':kwargs['group']=observation['group']
    else:kwargs['observation']=observation
    return kwargs


@pytest.mark.parametrize('flow',['tls','resource','cni','budget'],indirect=True)
def test_real_authorize_started_completion_and_two_consumes_use_original_anchors(flow):
    f=flow;assert f.context.consume_verifiers=={} and f.events==[]
    invocation=launched(f);assert 'qualify' in f.events and 'construct' not in f.events
    assert not Path(invocation.authorized['authorization']['current_checks_directory']).exists()
    observation,result=worker_outputs(f,invocation)
    invocation.complete(exit_code=0,result=result,elapsed_ms=150)
    original=copy.deepcopy(invocation.completion);mapping=f.context.consume_verifiers
    for index in range(1,3):
        proof=mapping[f.case.case_id](**consume_args(f,observation))
        assert proof['parent_invocation']==invocation.authorization_ref['sha256']
        assert invocation.completion==original and f.context.consume_verifiers is mapping
        attempt=invocation.directory.path/f'consume-{index:02d}'
        assert p.document((attempt/'current.json').read_bytes())['phase']=='consume'
        retained=p.document((attempt/'native-attempts/index.json').read_bytes())
        assert retained['outcome']=='worker_returned' and len(retained['attempts'])==1
    assert f.events.count('construct')==2 and f.events.count('current')==2 and f.events.count('close')==2
    assert f.events.count('lock')==f.events.count('unlock')==2
    assert set(invocation.worker_environment)=={s.worker.MANIFEST_ENV,s.worker.SHA_ENV}
    completion=p.document(Path(original['path']).read_bytes())
    assert completion['pid']==123 and completion['process_start']==456 and completion['exit_code']==0


@pytest.mark.parametrize('fault',['qualify','sources','wrong_case','source_drift','argv','registry','catalog','governance'])
def test_prepare_refusal_retains_finite_original_intent_and_has_no_client_or_retry(flow,fault,monkeypatch):
    f=flow
    if fault in {'qualify','sources'}:f.fault[0]=fault
    elif fault=='wrong_case':f.args['case']=Case(**(asdict(f.case)|{'assertions':('different',)}))
    elif fault=='source_drift':(f.root/'deploy/evolution/images/node/offline.nft').write_bytes(b'changed')
    elif fault=='argv':f.argv[2]='-c'
    elif fault=='registry':f.args['registry_sha256']='b'*64
    elif fault=='catalog':f.catalog.write_bytes(b'{}')
    else:
        f.args['destination']=f.root/'foreign'
    with pytest.raises(EvidenceError,match='classifier_parent_prepare_failed') as error:f.prepare()
    assert 'private' not in str(error.value) and 'construct' not in f.events
    invocation=f.context.invocations[f.case.case_id]
    assert (invocation.directory.path/'prepare-intent.json').exists()
    assert (invocation.directory.path/('failed.json' if invocation.journal else 'prepare-failed.json')).exists()
    assert f.context.consume_verifiers=={}
    with pytest.raises(EvidenceError):f.prepare()


@pytest.mark.parametrize('fault',['process','wrong_time','duplicate'])
def test_started_failure_never_installs_completion_authority(flow,fault):
    f=flow;invocation=f.prepare();f.clock[0]+=timedelta(seconds=1)
    if fault=='process':f.fault[0]='process'
    if fault=='duplicate':invocation.started(pid=123,process_start=456,started_at=f.clock[0].isoformat())
    at=pure.stamp(-100) if fault=='wrong_time' else f.clock[0].isoformat()
    with pytest.raises(EvidenceError):invocation.started(pid=123,process_start=456,started_at=at)
    assert f.context.consume_verifiers=={} and not (invocation.directory.path/'completion.json').exists()


@pytest.mark.parametrize('fault',['exit','assertions','status','extra_result','missing_observation','extra_observation','missing_check','expired_case','retention_failure'])
def test_parent_completion_failure_cannot_become_consume_authority(flow,monkeypatch,fault):
    f=flow;invocation=launched(f);_observation,result=worker_outputs(f,invocation);exit_code=0
    if fault=='exit':exit_code=1
    elif fault=='assertions':result['assertions']=0
    elif fault=='status':result['status']='fail'
    elif fault=='extra_result':result['arbitrary']='text'
    elif fault=='missing_observation':(f.output/'classifier-tls-observation.json').unlink()
    elif fault=='extra_observation':existing.put(f.output/'extra.json')
    elif fault=='missing_check':next(Path(invocation.authorized['authorization']['current_checks_directory']).glob('*.json')).unlink()
    elif fault=='expired_case':invocation.deadline=time.monotonic()-1
    else:
        original=invocation.directory.document
        def write(name,value):
            if name=='completion.json':raise OSError('private write text')
            return original(name,value)
        monkeypatch.setattr(invocation.directory,'document',write)
    with pytest.raises(EvidenceError,match='classifier_parent_completion_failed'):invocation.complete(exit_code=exit_code,result=result,elapsed_ms=150)
    assert f.context.consume_verifiers=={} and (invocation.directory.path/'failed.json').exists()
    assert not (invocation.directory.path/'completion.json').exists()


@pytest.mark.parametrize('flow',['budget','resource'],indirect=True)
@pytest.mark.parametrize('fault',['unknown_ack','missing_returned','wrong_restoration','wrong_operation','foreign_sources'])
def test_completion_authenticates_actual_governance_journal_and_restoration(flow,fault):
    f=flow;invocation=launched(f);observation,result=worker_outputs(f,invocation)
    directory=Path(invocation.authorized['authorization']['governance_root'])/observation['operation']
    if fault=='missing_returned':(directory/'request-01-returned.json').unlink()
    else:
        path=directory/('intent.json' if fault=='foreign_sources' else 'completion.json');value=p.document(path.read_bytes())
        if fault=='unknown_ack':value['last_request_outcome_unknown']=True
        elif fault=='wrong_restoration':value['restoration']='unknown'
        elif fault=='wrong_operation':value['operation']=pure.uid(999)
        else:value['source_sha256']={'mvp/foreign.py':pure.SHA}
        existing.put(path,p.canonical(value)+b'\n')
    with pytest.raises(EvidenceError):invocation.complete(exit_code=0,result=result,elapsed_ms=150)
    assert not f.context.consume_verifiers


@pytest.mark.parametrize('fault',['qualify','construct','current','close','changed_observation','source_drift','outer_expired'])
def test_consume_requires_fresh_authority_and_saves_partial_native_attempts(flow,fault):
    f=flow;invocation=launched(f);observation,result=worker_outputs(f,invocation)
    invocation.complete(exit_code=0,result=result,elapsed_ms=150)
    if fault in {'qualify','construct','current','close'}:f.fault[0]=fault
    elif fault=='changed_observation':existing.put(f.output/'classifier-tls-observation.json',b'{}\n')
    elif fault=='source_drift':f.fault[0]='sources'
    else:f.context.budget.deadline=time.monotonic()-1
    with pytest.raises(EvidenceError) as error:invocation.consume(**consume_args(f,observation))
    assert 'private' not in str(error.value)
    if fault in {'construct','current','close','changed_observation'}:
        saved=p.document((invocation.directory.path/'consume-01/native-attempts/index.json').read_bytes())
        assert saved['outcome']=='worker_failed' and len(saved['attempts'])==1
    assert f.events.count('close')==(1 if fault in {'current','close','changed_observation'} else 0)


def test_missing_final_adapter_rejected_before_reading_operator_capsule(flow,monkeypatch):
    f=flow;monkeypatch.setattr(io.StableReader,'__call__',lambda *a:pytest.fail('read without required adapter'))
    with pytest.raises(EvidenceError):s.ParentClassifierContext(f.capsule_ref,f.root,f.outer,qualify_final_worker=None)


@pytest.mark.parametrize('fault',['capsule_bytes','capsule_in_memory','closed','deadline_refresh'])
def test_operator_original_scope_cannot_drift_or_be_refreshed(flow,fault):
    f=flow
    if fault=='capsule_bytes':Path(f.capsule_ref['path']).write_bytes(b'{}\n')
    elif fault=='capsule_in_memory':f.context.capsule['valid_until']=pure.stamp(20000)
    elif fault=='closed':f.context.close()
    else:f.context.budget.deadline+=60
    with pytest.raises(EvidenceError):f.prepare()
    assert not f.context.invocations and not list((f.tmp_path/'invocations').iterdir())


def test_worker_failure_preserves_original_journal_without_future_completion(flow):
    f=flow;invocation=launched(f);invocation.failed(reason_code='worker_exit')
    assert p.document((invocation.directory.path/'failed.json').read_bytes())['process']['pid']==123
    assert not f.context.consume_verifiers and not (invocation.directory.path/'completion.json').exists()


def test_loaded_module_paths_and_exact_required_source_bytes_are_checked(flow,monkeypatch):
    f=flow
    modules={name:N(__file__=str(f.root/relative)) for name,relative in {**s.builder.MODULES,**s.MODULES}.items()}
    monkeypatch.setattr(s,'sys',N(modules=modules))
    f.original_loaded(f.root,f.sources,f.context.reader)
    relative=s.SELF;(f.root/relative).write_bytes(b'# changed\n')
    with pytest.raises(EvidenceError):f.original_loaded(f.root,f.sources,f.context.reader)


def test_foreign_loaded_module_is_refused_even_if_expected_files_are_unchanged(flow,monkeypatch):
    f=flow
    modules={name:N(__file__=str(f.root/relative)) for name,relative in {**s.builder.MODULES,**s.MODULES}.items()}
    modules[s.__name__]=N(__file__='/foreign/classifier_parent_context.py')
    monkeypatch.setattr(s,'sys',N(modules=modules))
    with pytest.raises(EvidenceError):f.original_loaded(f.root,f.sources,f.context.reader)


@pytest.mark.parametrize('fault',['none','wrong_parent','wrong_start','wrong_cmdline','wrong_executable','changed_start','changed_cmdline'])
def test_actual_process_identity_reads_original_parent_owned_child_twice(tmp_path,monkeypatch,fault):
    binary=tmp_path/'python';binary.write_bytes(b'\x7fELFsynthetic');binary.chmod(0o700)
    foreign=tmp_path/'foreign';foreign.write_bytes(b'\x7fELFforeign');foreign.chmod(0o700)
    reference={'path':str(binary),'sha256':hashlib.sha256(binary.read_bytes()).hexdigest()}
    budget=io.Budget(time.monotonic()+60);argv=[str(binary),'-B','-m','evolution.worker'];counts={'stat':0,'cmdline':0}
    @contextmanager
    def executable(ref,*,owner,check):
        assert ref==reference and owner==os.getuid();check()
        fd=os.open(binary,os.O_RDONLY)
        try:yield fd
        finally:os.close(fd)
    monkeypatch.setattr(s.builder.native,'executable',executable)
    def read(path,*,deadline,maximum):
        assert deadline==budget.deadline and maximum in {8192,65536}
        if path.endswith('/stat'):
            counts['stat']+=1;start=456
            if fault=='wrong_start' or fault=='changed_start' and counts['stat']==2:start=999
            parent=os.getpid()+1 if fault=='wrong_parent' else os.getpid()
            return b'123 (python) '+f'S {parent} '.encode()+b'0 '*17+str(start).encode()
        counts['cmdline']+=1
        if fault=='wrong_cmdline' or fault=='changed_cmdline' and counts['cmdline']==2:return b'foreign\0'
        return b'\0'.join(part.encode() for part in argv)+b'\0'
    monkeypatch.setattr(s.builder.current,'kernel_read',read)
    original=Path.stat
    def observed(path,*args,**kwargs):
        if str(path)=='/proc/123/exe':return original(foreign if fault=='wrong_executable' else binary)
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'stat',observed)
    if fault=='none':
        s.process_identity(123,456,argv,reference,budget);assert counts=={'stat':2,'cmdline':2}
    else:
        with pytest.raises(EvidenceError):s.process_identity(123,456,argv,reference,budget)


def test_closing_incomplete_context_records_interruption_and_is_idempotent(flow):
    f=flow;invocation=launched(f);f.context.close();f.context.close()
    saved=p.document((invocation.directory.path/'failed.json').read_bytes())
    assert saved['reason_code']=='parent_interrupted' and saved['last_state']=='started'
    assert not f.context.consume_verifiers


@pytest.mark.parametrize('fault',['missing','symlink','public_mode','wrong_hash','malformed'])
def test_initial_private_authority_failures_are_finite_before_any_client(flow,fault):
    f=flow;ref=copy.deepcopy(f.capsule_ref);path=Path(ref['path'])
    if fault=='missing':ref['path']=str(path.with_name('missing-private-name.json'))
    elif fault=='symlink':
        target=path.with_name('original-capsule.json');path.rename(target);path.symlink_to(target)
    elif fault=='public_mode':path.chmod(0o644)
    elif fault=='wrong_hash':ref['sha256']='b'*64
    else:ref=existing.put(path,b'{malformed private detail}\n')
    with pytest.raises(EvidenceError,match='^classifier_parent_authority_invalid$') as error:
        s.ParentClassifierContext(ref,f.root,f.outer,qualify_final_worker=f.qualify)
    assert str(error.value)=='classifier_parent_authority_invalid' and 'construct' not in f.events
