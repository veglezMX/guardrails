"""Actual private IO and allocation ordering with only Docker/DB calls faked."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import signal
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest
from evolution import platform_database as target
from evolution.registry import EvidenceError
from psycopg.conninfo import conninfo_to_dict
from sqlalchemy.engine import make_url

SECRET = "private-canary-password-do-not-retain"


def private(path, raw):
    path.write_bytes(raw)
    path.chmod(0o600)


class Rows:
    def __init__(self, value):
        self.value = value
    def fetchone(self):
        return self.value
    def fetchall(self):
        return self.value


class Server:
    def __init__(self, operation_root):
        self.operations = operation_root
        self.calls = []
        self.databases = {}
        self.fault = None
    def connect(self, dsn, **kwargs):
        fields = conninfo_to_dict(dsn)
        assert fields["host"] == "127.0.0.1" and fields["port"] == "44630"
        assert fields["connect_timeout"] == "2" and "statement_timeout=15000" in fields["options"]
        assert kwargs == {"autocommit": True}
        server = self
        class Connection:
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
            def execute(self, statement, args=None):
                statement = statement if isinstance(statement, str) else statement.as_string()
                server.calls.append((fields["dbname"], statement))
                if statement.startswith("SELECT current_user"):
                    return Rows((fields["user"], fields["dbname"], False))
                if statement.startswith("SELECT rolsuper,rolbypassrls"):
                    return Rows((False, server.fault == "bypass", False, False, True))
                if statement.startswith("SELECT rolsuper OR"):
                    return Rows((True,))
                if statement.startswith("SELECT oid FROM"):
                    return Rows((1,) if server.fault == "collision" else None)
                if statement.startswith("CREATE DATABASE"):
                    dirs = list(server.operations.iterdir());assert len(dirs) == 1
                    intent = json.loads((dirs[0]/"intent.json").read_bytes())
                    assert intent["status"] == "intent_before_create"
                    database = intent["database"]
                    assert statement == f'CREATE DATABASE "{database}" OWNER evolution_owner TEMPLATE template0'
                    assert (dirs[0]/"owner-dsn").is_file() and (dirs[0]/"app-dsn").is_file()
                    assert database not in server.databases
                    server.databases[database] = 17342
                    if server.fault == "unknown-create":
                        raise psycopg.OperationalError(SECRET)
                    return Rows(None)
                if statement.startswith("SELECT oid,pg_get_userbyid"):
                    return Rows((server.databases[args[0]], "evolution_owner"))
                if statement.startswith("GRANT"):
                    assert fields["dbname"].startswith(target.PREFIX)
                    return Rows(None)
                if statement.startswith("SELECT c.relname"):
                    return Rows([(name, server.fault != "no-rls", True, "evolution_owner")
                                 for name in ("audit_heads", "records", "record_links", "tenants")])
                if statement.startswith("SELECT count(*)"):
                    return Rows((1 if server.fault == "nonempty" else 0,))
                if statement.startswith("SELECT set_config"):
                    return Rows(("_platform",))
                raise AssertionError(statement)
        return Connection()


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    # Build a synthetic HOME boundary so this unit remains portable under the
    # closed worker's private basetemp. All source/state/DB boundaries are fake;
    # production still requires the real explicit ~/.agent-sessions root.
    synthetic_home=tmp_path/'synthetic-home';synthetic_home.mkdir(mode=0o700)
    sessions=synthetic_home/'.agent-sessions';sessions.mkdir(mode=0o700)
    monkeypatch.setattr(target.Path,'home',classmethod(lambda cls: synthetic_home))
    for key in list(os.environ):
        if key.startswith(("PG", "DOCKER")) or key == "BUILDX_CONFIG":
            monkeypatch.delenv(key)
    source = tmp_path / "source";source.mkdir(mode=0o700)
    sources = {}
    for name in target.REQUIRED_SOURCES:
        path = source/name;path.parent.mkdir(parents=True, exist_ok=True)
        raw = b"pass\n"
        if name == "mvp/evolution/platform_database.py":
            raw = Path(target.__file__).read_bytes()
        if name == "mvp/evolution/postgres.py":
            raw = Path(target.postgres.__file__).read_bytes()
        if name.startswith("mvp/guardrails/"):
            module = target.importlib.import_module(name.removeprefix("mvp/").removesuffix(".py").replace("/", "."))
            raw = Path(module.__file__).read_bytes()
        path.write_bytes(raw);path.chmod(0o644)
        sources[name] = hashlib.sha256(raw).hexdigest()
    root = tmp_path / "fixture";state = root/".evolution/postgres";state.mkdir(parents=True,mode=0o700)
    identity = hashlib.sha256(str(root).encode()).hexdigest()[:16]
    settings = {"container":"guardrails-evolution-db-"+identity,"owner":identity,"image":"1"*64,"port":44630}
    private(state/"instance.json",target.canonical(settings));private(state/".prepare.lock",b"")
    for role in ("owner","app"):
        private(state/(role+"-dsn"),f"postgresql+psycopg://evolution_{role}:{SECRET}@127.0.0.1:44630/evolution\n".encode())
    lock=root/"deploy/evolution/artifacts.lock.json";lock.parent.mkdir(parents=True)
    lock.write_bytes(target.canonical({"schema_version":"guardrails-evolution-artifacts-v1","artifacts":[{
        "name":"postgres","kind":"image","sha256":"1"*64,"source_url":"docker://postgres@sha256:"+"1"*64}]}));lock.chmod(0o644)
    info={"Id":"2"*64,"NetworkSettings":{"Ports":{"5432/tcp":[{"HostIp":"127.0.0.1","HostPort":"44630"}]}}}
    inspected=[]
    def owned(name,owner,image,container_id=None,*,running=False):
        assert name==settings['container'] and owner==identity and image==settings['image'] and running
        assert container_id in (None,info['Id'])
        inspected.append((name,container_id));return info
    monkeypatch.setattr(target.postgres,'owned_container',owned)
    monkeypatch.setattr(target.postgres,'verify_access',lambda rows: None)
    # Any accidental original Docker command is refused, rather than faked broadly.
    monkeypatch.setattr(target.postgres,'command',lambda *_: (_ for _ in ()).throw(AssertionError('unexpected Docker command')))
    operations=sessions/'operations';operations.mkdir(mode=0o700)
    server=Server(operations);monkeypatch.setattr(target.psycopg,'connect',server.connect)
    migrations=[]
    from guardrails.evolution import storage
    def migrate(dsn):
        parsed=make_url(dsn);assert parsed.database in server.databases
        assert parsed.password==SECRET and parsed.query['connect_timeout']=='2'
        assert 'statement_timeout=15000' in parsed.query['options']
        migrations.append(parsed.database)
        if server.fault=='migration':raise ValueError(SECRET)
    monkeypatch.setattr(storage,'migrate',migrate)
    return SimpleNamespace(source=source,sources=sources,root=root,state=state,settings=settings,
        operations=operations,server=server,info=info,inspected=inspected,migrations=migrations,lock=lock)


def allocate(f):
    return target.allocate(root=f.source,fixture_root=f.root,operation_root=f.operations,sources=f.sources)


def receipt(f,name):
    return json.loads((next(f.operations.iterdir())/name).read_bytes())


def test_actual_files_order_pristine_migration_grants_and_retention(fixture):
    f=fixture;before={p.name:p.read_bytes() for p in f.state.iterdir()}
    result=allocate(f);app=Path(result[target.ENV]);operation=app.parent
    assert operation.parent==f.operations and app.stat().st_mode&0o777==0o600
    assert operation.stat().st_mode&0o777==0o700
    assert f.migrations==[receipt(f,'ready.json')['database']]
    ready=receipt(f,'ready.json');assert ready['database_oid']==17342 and ready['case_executed'] is False
    assert ready['status']=='ready_empty_retained' and len(f.server.databases)==1
    assert before=={p.name:p.read_bytes() for p in f.state.iterdir()}
    assert not any('DROP' in query or 'ALTER ROLE' in query for _,query in f.server.calls)
    assert all(database.startswith(target.PREFIX) for database,query in f.server.calls if query.startswith('GRANT'))
    assert make_url(app.read_text()).password==SECRET
    assert {make_url((operation/name).read_text().strip()).database for name in ('app-dsn','owner-dsn')}==set(f.server.databases)
    assert all(SECRET not in p.read_text() for p in operation.glob('*.json'))
    assert signal.getitimer(signal.ITIMER_REAL)==(0.0,0.0)


@pytest.mark.parametrize('fault,created,outcome', [('bypass',False,'not_requested'),('collision',False,'not_requested'),
    ('unknown-create',True,'unknown'),('migration',True,'created'),('no-rls',True,'created'),('nonempty',True,'created')])
def test_failure_preserves_exact_attempt_without_retry_or_success(fixture,fault,created,outcome):
    f=fixture;f.server.fault=fault
    with pytest.raises(EvidenceError,match='^platform_database_allocation_failed$') as error:allocate(f)
    assert SECRET not in str(error.value)
    failure=receipt(f,'failed.json');assert failure['create_outcome']==outcome
    assert bool(f.server.databases)==created and not (next(f.operations.iterdir())/'ready.json').exists()
    assert sum(query.startswith('CREATE DATABASE') for _,query in f.server.calls)==int(created)
    if created:assert (next(f.operations.iterdir())/'intent.json').exists()


@pytest.mark.parametrize('fault',('foreign-owner','image','port','password-url','file-mode','state-symlink','lock-busy','artifact'))
def test_existing_fixture_refusal_precedes_any_database_command(fixture,fault):
    f=fixture
    descriptor=None
    if fault in {'foreign-owner','image'}:
        settings={**f.settings,('owner' if fault=='foreign-owner' else 'image'):'foreign'}
        private(f.state/'instance.json',target.canonical(settings))
    elif fault=='port':f.info['NetworkSettings']['Ports']['5432/tcp'][0]['HostPort']='44631'
    elif fault=='password-url':private(f.state/'app-dsn',b'postgresql+psycopg://evolution_app:p@foreign:44630/evolution')
    elif fault=='file-mode':(f.state/'app-dsn').chmod(0o644)
    elif fault=='state-symlink':
        moved=f.state.with_name('saved');f.state.rename(moved);f.state.symlink_to(moved,target_is_directory=True)
    elif fault=='lock-busy':
        descriptor=os.open(f.state/'.prepare.lock',os.O_RDWR);fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
    else:f.lock.write_text('{}')
    try:
        with pytest.raises(EvidenceError):allocate(f)
        assert f.server.calls==[] and f.server.databases=={}
    finally:
        if descriptor is not None:os.close(descriptor)


@pytest.mark.parametrize('variable',('PGHOSTADDR','PGSERVICE','PGOPTIONS','DOCKER_CONTEXT','DOCKER_CONFIG','DOCKER_HOST','BUILDX_CONFIG'))
def test_routing_override_refuses_before_operation_or_query(fixture,monkeypatch,variable):
    monkeypatch.setenv(variable,'foreign')
    with pytest.raises(EvidenceError,match='routing_override'):allocate(fixture)
    assert list(fixture.operations.iterdir())==[] and fixture.server.calls==[]


def test_catalog_source_change_refuses_before_any_state_operation(fixture):
    (fixture.source/next(iter(fixture.sources))).write_text('changed=True\n')
    with pytest.raises(EvidenceError):allocate(fixture)
    assert list(fixture.operations.iterdir())==[] and fixture.server.calls==[]


def test_operation_path_replacement_cannot_redirect_write(fixture):
    f=fixture;attempt=target.Attempt(f.operations)
    try:
        saved=attempt.path.with_name('retained');attempt.path.rename(saved);attempt.path.mkdir(mode=0o700)
        sentinel=attempt.path/'sentinel';private(sentinel,b'foreign')
        with pytest.raises(EvidenceError):attempt.write('intent.json',b'{}')
        assert {p.name for p in attempt.path.iterdir()}=={'sentinel'} and not (saved/'intent.json').exists()
    finally:attempt.close()


def test_created_directory_substitution_before_open_is_rejected(fixture,monkeypatch):
    original=target.os.open;seen=[]
    def replace(path,flags,*args,**kwargs):
        if kwargs.get('dir_fd') is not None and flags & os.O_DIRECTORY:
            child=fixture.operations/str(path);old=child.with_name('preserved');child.rename(old);child.mkdir(mode=0o700)
            private(child/'sentinel',b'foreign');seen.append(child)
        return original(path,flags,*args,**kwargs)
    monkeypatch.setattr(target.os,'open',replace)
    with pytest.raises(EvidenceError):target.Attempt(fixture.operations)
    assert len(seen)==1 and {p.name for p in seen[0].iterdir()}=={'sentinel'}


def test_only_exact_registered_node_gets_fresh_private_environment(monkeypatch,tmp_path):
    called=[]
    monkeypatch.setattr(target,'allocate',lambda **kwargs: called.append(kwargs) or {target.ENV:'/private/new/app-dsn'})
    assert target.environment_for_case(target.NODE+'[variant]',root=tmp_path,sources={},environ={})=={}
    for env in ({},{target.ENV:'/old'}, {'EVOLUTION_PLATFORM_FIXTURE_ROOT':'/fixture'},
                {'EVOLUTION_PLATFORM_FIXTURE_ROOT':'/fixture','EVOLUTION_PLATFORM_OPERATION_ROOT':'/ops','EVOLUTION_TEST_DSN_FILE':'/foreign'}):
        with pytest.raises(EvidenceError):target.environment_for_case(target.NODE,root=tmp_path,sources={},environ=env)
    assert called==[]
    result=target.environment_for_case(target.NODE,root=tmp_path,sources={'a':'b'},environ={
        'EVOLUTION_PLATFORM_FIXTURE_ROOT':'/fixture','EVOLUTION_PLATFORM_OPERATION_ROOT':'/ops',
        'EVOLUTION_TEST_DSN_FILE':'/fixture/.evolution/postgres/app-dsn'})
    assert result=={target.ENV:'/private/new/app-dsn'} and len(called)==1
    assert called[0]=={'root':tmp_path,'fixture_root':Path('/fixture'),'operation_root':Path('/ops'),'sources':{'a':'b'}}


def test_deadline_interrupt_retains_created_database_and_restores_signal(fixture,monkeypatch):
    previous=signal.getsignal(signal.SIGALRM)
    def timeout(_):
        signal.getsignal(signal.SIGALRM)(signal.SIGALRM,None)
    monkeypatch.setattr(target,'migrate_database',timeout)
    with pytest.raises(EvidenceError):allocate(fixture)
    assert receipt(fixture,'failed.json')['create_outcome']=='created'
    assert len(fixture.server.databases)==1
    assert signal.getitimer(signal.ITIMER_REAL)==(0.0,0.0) and signal.getsignal(signal.SIGALRM)==previous


def test_existing_alarm_refuses_without_changing_its_budget(fixture,monkeypatch):
    real=target.signal.getitimer
    monkeypatch.setattr(target.signal,'getitimer',lambda which:(20.0,0.0))
    with pytest.raises(EvidenceError):allocate(fixture)
    assert list(fixture.operations.iterdir())==[] and fixture.server.calls==[]
    monkeypatch.setattr(target.signal,'getitimer',real)


def test_lock_replacement_after_intent_refuses_before_create(fixture,monkeypatch):
    original=target.Attempt.write
    def swap(self,name,raw):
        original(self,name,raw)
        if name=='intent.json':
            lock=fixture.state/'.prepare.lock';lock.rename(lock.with_name('original-lock'))
            private(lock,b'')
    monkeypatch.setattr(target.Attempt,'write',swap)
    with pytest.raises(EvidenceError):allocate(fixture)
    assert not any(query.startswith('CREATE') for _,query in fixture.server.calls)
    assert receipt(fixture,'failed.json')['create_outcome']=='not_requested'


def test_filesystem_intent_failure_never_reaches_create(fixture,monkeypatch):
    original=target.Attempt.write
    def refusal(self,name,raw):
        if name=='intent.json':raise OSError(SECRET)
        original(self,name,raw)
    monkeypatch.setattr(target.Attempt,'write',refusal)
    with pytest.raises(EvidenceError):allocate(fixture)
    assert not any(query.startswith('CREATE') for _,query in fixture.server.calls)
    assert fixture.server.databases=={}


@pytest.mark.parametrize('module_name',target.STORAGE_MODULES)
def test_mixed_worktree_storage_or_migration_refuses_before_create(fixture,monkeypatch,module_name,tmp_path):
    module=target.importlib.import_module(module_name)
    foreign=tmp_path/'different-source.py';foreign.write_text('different=True\n');foreign.chmod(0o644)
    monkeypatch.setattr(module,'__file__',str(foreign))
    with pytest.raises(EvidenceError):allocate(fixture)
    assert list(fixture.operations.iterdir())==[] and fixture.server.calls==[]


@pytest.mark.parametrize('fault',('outside-session','public-mode','symlink'))
def test_explicit_operation_root_cannot_fall_back_or_redirect(fixture,tmp_path,fault):
    f=fixture
    if fault=='outside-session':
        path=tmp_path/'not-a-session';path.mkdir(mode=0o700);f.operations=path
    elif fault=='public-mode':f.operations.chmod(0o755)
    else:
        path=f.operations.with_name('alias');path.symlink_to(f.operations,target_is_directory=True);f.operations=path
    with pytest.raises(EvidenceError):allocate(f)
    assert f.server.calls==[] and list(f.operations.iterdir())==[]
