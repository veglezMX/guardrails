"""Authenticated snapshot construction without deployment.access() or repair.

The explicit fixture authenticates final-worker receipts before this constructor.
Only current read-only native/identity checks run during construction. Existing
EvaluatorCluster governance methods are retained from authenticated source, but
its implicit access/platform/forward constructor is never called.
"""
from __future__ import annotations

import builtins
import copy
import fcntl
import os
import ssl
import stat
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import FunctionType, SimpleNamespace
from urllib.parse import urlsplit

from evolution import classifier_provenance as proof
from evolution.classifier_provenance_io import StableReader
from guardrails.evolution.deadlines import current_deadline

from mvp.tests.evolution.e2e import classifier_provenance_support as current

GET = ('get', 'deployments,replicasets,pods,services,endpointslices.discovery.k8s.io', '-o', 'json')
SOURCE_PATHS = {
    'bootstrap': 'deploy/evolution/bootstrap.py', 'credentials': 'deploy/evolution/credentials.py',
    'assembly': 'deploy/evolution/assembly.py', 'image_identity': 'deploy/evolution/image_identity.py',
    'cluster': 'mvp/tests/evolution/e2e/cluster_support.py',
    'evaluator': 'mvp/tests/evolution/e2e/evaluator_matrix_support.py',
    'config': 'mvp/guardrails/evolution/evaluator_config.py',
}


class ReadOnlyConstructionError(RuntimeError):
    pass


def require(value):
    if not value:
        raise ReadOnlyConstructionError('classifier_readonly_constructor_invalid')


def captured(raw, path, name, *, imports=None, package=None):
    """Source-local import binding; never replace process-global sys.modules."""
    allowed = dict(imports or {})
    original = builtins.__import__
    def importing(name, globals=None, locals=None, fromlist=(), level=0):
        if level == 0 and name in allowed:
            require(bool(fromlist))
            return allowed[name]
        return original(name, globals, locals, fromlist, level)
    namespace = {'__name__': name, '__file__': str(path), '__package__': package,
                 '__builtins__': dict(vars(builtins), __import__=importing)}
    exec(compile(raw, str(path), 'exec'), namespace)  # noqa: S102 - source bytes authenticated against original parent snapshot
    return SimpleNamespace(**namespace)


@contextmanager
def existing_lock(state, *, budget):
    """Only an existing private lock; never mkdir/create, nest, wait or repair."""
    budget.check()
    directory = state.lstat()
    require(state.is_absolute() and state.resolve() == state and stat.S_ISDIR(directory.st_mode)
            and directory.st_uid == os.getuid() and stat.S_IMODE(directory.st_mode) == 0o700)
    path = state / '.lock'; before = path.lstat()
    require(path.resolve() == path and stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid()
            and stat.S_IMODE(before.st_mode) == 0o600 and before.st_nlink == 1)
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        require((before.st_dev, before.st_ino, before.st_uid, before.st_mode) ==
                (opened.st_dev, opened.st_ino, opened.st_uid, opened.st_mode))
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        budget.check()
        current.held_lock(state, deadline=budget.deadline)
        after = state.lstat()
        require((directory.st_dev, directory.st_ino, directory.st_mode, directory.st_uid) ==
                (after.st_dev, after.st_ino, after.st_mode, after.st_uid))
        yield
        budget.check()
        current.held_lock(state, deadline=budget.deadline)
        after = state.lstat()
        require((directory.st_dev, directory.st_ino, directory.st_mode, directory.st_uid) ==
                (after.st_dev, after.st_ino, after.st_mode, after.st_uid))
    finally:
        os.close(fd)


def unavailable(*args, **kwargs):
    raise ReadOnlyConstructionError('classifier_implicit_deployment_access_forbidden')


def access_snapshot(raw, *, state, ports):
    value = proof.document(raw)
    require(value.get('schema_version') == 'guardrails-evolution-local-access-v1'
            and value.get('profile') == 'classifier' and set(value.get('tenants', {})) == {'team-a', 'team-b'})
    urls = {name: value[name + '_url'] for name in ('management', 'runtime', 'mock')}
    urls.update({name: row['gateway_url'] for name, row in value['tenants'].items()})
    for name, url in {**urls, 'ollama': value['ollama_url']}.items():
        parsed = urlsplit(url)
        require(parsed.scheme == 'https' and parsed.hostname == 'localhost' and parsed.username is None
                and parsed.password is None and parsed.port is not None and 1024 <= parsed.port <= 65535
                and not parsed.path and not parsed.query and not parsed.fragment)
        if name != 'ollama': require(parsed.port == ports[name])
    require(value['ca_file'] == str(state / 'credentials/ca.crt')
            and value['database_url_file'] == str(state / 'assembly/private/database-app-url')
            and value['seed_inventory_file'] == str(state / 'assembly/seed/inventory.json'))
    return value


def read_only_repository(material, *, state, port, deadline):
    """Open only an app-role, TLS, read-only engine with sealed captured CA."""
    from guardrails.evolution.storage import TenantRepository
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    require(not any(name.startswith('PG') for name in os.environ))
    url = make_url(material['assembly/private/database-app-url'].decode('ascii').strip())
    require(url.drivername == 'postgresql+psycopg' and url.username == 'evolution_app' and bool(url.password)
            and url.database == 'guardrails_evolution' and url.host in {'localhost', '127.0.0.1'} and url.port == port
            and dict(url.query) == {'sslmode': 'verify-full', 'sslrootcert': str(state / 'credentials/ca.crt')})
    descriptor = os.memfd_create('classifier-fixture-ca', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
    engine = None
    try:
        ca = material['credentials/ca.crt']; require(os.write(descriptor, ca) == len(ca))
        fcntl.fcntl(descriptor, fcntl.F_ADD_SEALS, fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
        url = url.update_query_dict({'sslrootcert': '/proc/self/fd/' + str(descriptor)})
        require(current.remaining(deadline) >= 2)
        milliseconds = max(1, min(5000, int(current.remaining(deadline) * 1000)))
        engine = create_engine(url, hide_parameters=True, pool_size=1, max_overflow=0, pool_timeout=1,
            connect_args={'connect_timeout': 2, 'options': '-c default_transaction_read_only=on -c statement_timeout=' +
                          str(milliseconds) + ' -c lock_timeout=' + str(milliseconds)})
        with engine.connect() as connection:
            row = connection.execute(text("SELECT current_setting('transaction_read_only'), pg_is_in_recovery(), "
                "clock_timestamp(), (SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid())")).one()
            skew = (datetime.now(UTC) - row[2]).total_seconds()
            require(row[0] == 'on' and row[1] is False and row[3] is True and -5 <= skew <= 10)
        current.remaining(deadline)
        return TenantRepository(engine), descriptor
    except BaseException:
        try:
            if engine is not None: engine.dispose()
        except BaseException:  # noqa: BLE001, S110 - preserve original refusal; no raw SQL exception escapes caller
            pass
        os.close(descriptor)
        raise


def close_cluster(cluster):
    first = None
    for name in ('http', 'repo'):
        resource = getattr(cluster, name, None)
        if resource is not None:
            setattr(cluster, name, None)
            try:
                resource.close()
            except BaseException as error:  # noqa: BLE001 - finish both owned resources and preserve finite first failure
                if first is None: first = error
    fd = getattr(cluster, '_ca_fd', None)
    if fd is not None:
        cluster._ca_fd = None
        try:
            os.close(fd)
        except OSError as error:
            if first is None: first = error
    if first is not None:
        raise ReadOnlyConstructionError('classifier_readonly_cleanup_failed') from None


def ready_once(cluster, *, deadline):
    checks = [(cluster.access['management_url'] + '/health/live', {})]
    for tenant in cluster.tenants:
        item = cluster.access['tenants'][tenant]
        checks.extend([(cluster.access['runtime_url'] + '/health/ready', cluster.headers(item['workload_token_file'])),
                       (item['gateway_url'] + '/health/ready', cluster.headers(item['gateway_token_file']))])
    for url, headers in checks:
        remaining = current.remaining(deadline, 5)
        with cluster.http.stream('GET', url, headers=headers, timeout=remaining) as response:
            require(response.status_code == 200 and response.headers.get('content-encoding', 'identity') == 'identity')
            size = 0
            for block in response.iter_raw(chunk_size=4096):
                size += len(block); require(size <= 4096); current.remaining(deadline)
        current.remaining(deadline)


class ReadOnlyClusterConstructor:
    """One explicitly authenticated cluster; no live operation at object creation."""
    def __init__(self, *, source_root, reader, executor):
        require(isinstance(reader, StableReader) and callable(executor))
        self.root, self.reader, self.executor = Path(source_root), reader, executor

    def __call__(self, *, context, manifest, deadline):
        cluster = None
        token = current_deadline.set(deadline)
        try:
            with self.reader.budget.cap(deadline):
                cluster = self._construct(context, manifest)
            return cluster
        except BaseException:  # noqa: BLE001 - sanitize private constructor errors after complete owned cleanup
            if cluster is not None:
                try:
                    close_cluster(cluster)
                except BaseException:  # noqa: BLE001, S110 - primary construction failure stays primary
                    pass
            raise ReadOnlyConstructionError('classifier_readonly_constructor_failed') from None
        finally:
            current_deadline.reset(token)

    def _construct(self, context, manifest):
        import httpx

        budget = self.reader.budget; deadline = budget.deadline
        auth_raw = self.reader(manifest['authorization']); auth = proof.document(auth_raw)
        preflight_raw = self.reader(auth['preflight']); preflight = proof.document(preflight_raw)
        require(auth == context['authorization'] and preflight == context['preflight'])
        proof.validate_authorization(auth, expected_case=manifest['case_id'], expected_nodeid=manifest['nodeid'],
            expected_sources=manifest['sources'], expected_registry_sha256=manifest['registry_sha256'],
            expected_corpus_manifest=manifest['corpus_manifest'], now=datetime.now(UTC))
        proof.validate_preflight(preflight, auth, now=datetime.now(UTC))
        node_raw = self.reader(auth['receipts']['node_boundary'])
        state = Path(manifest['deployment_state']); require(state.resolve() == state)
        require(self.root.resolve() == self.root and self.root.is_absolute())
        source = {name: self.reader({'path': str(self.root / name), 'sha256': auth['sources'][name]}) for name in SOURCE_PATHS.values()}
        def module(key, **kwargs):
            path = SOURCE_PATHS[key]
            return captured(source[path], self.root / path, 'classifier_readonly_' + key, **kwargs)
        bootstrap = module('bootstrap')
        deployment = bootstrap.LocalDeployment(self.root, state=state)
        deployment.lock = lambda: existing_lock(state, budget=budget)
        deployment.access = unavailable
        base = module('cluster')
        evaluator = module('evaluator', imports={'cluster_support': base})
        cluster = object.__new__(evaluator.EvaluatorCluster)
        cluster.profile, cluster.state, cluster.deployment = 'classifier', state, deployment
        cluster.readiness_observation = None
        cluster.http = cluster.repo = cluster._ca_fd = None
        cluster.close = lambda: close_cluster(cluster)
        cluster.platform = cluster._access = unavailable
        checker = current.CurrentChecker(cluster, source_root=self.root, authorization_raw=auth_raw,
            authorization_sha256=manifest['authorization']['sha256'], preflight_raw=preflight_raw,
            preflight_sha256=auth['preflight']['sha256'], node_boundary_raw=node_raw, executor=self.executor)
        try:
            with deployment.lock():
                full_source = checker._sources(auth['sources'], deadline)
                material, owner = checker._private(preflight, deadline)
                ports = proof.document(material['assembly/ports.json'])
                cluster.access = access_snapshot(material['assembly/access.json'], state=state, ports=ports)
                def inputs(path, *, maximum=4 * 1024 * 1024, public_ca=False):
                    budget.check()
                    if str(path) == manifest['evaluator_config']['path']:
                        raw = self.reader(manifest['evaluator_config'])
                    else:
                        name = Path(path).relative_to(state).as_posix()
                        require(name in material and len(material[name]) <= maximum)
                        raw = material[name]
                    require(0 < len(raw) <= maximum)
                    return raw
                credentials = module('credentials')
                credentials.describe_inventory.__globals__['private_read'] = inputs
                cluster.credentials = credentials.describe_inventory(state / 'assembly', owner,
                    proof.document(material['assembly/seed/inventory.json']))
                require(cluster.credentials['generation'] == preflight['expected']['credential_generation']
                        and cluster.credentials['sha256'] == preflight['expected']['credential_journal_sha256'])
                cluster.inventory = cluster.credentials['inventory']; cluster.tenants = tuple(sorted(cluster.inventory['tenants']))
                require(cluster.tenants == ('team-a', 'team-b'))
                for tenant in cluster.tenants:
                    require(cluster.access['tenants'][tenant]['workload_token_file'] ==
                            cluster.inventory['tenants'][tenant]['roles']['workload']['token_file'])
                cluster.headers = lambda token_file, **extra: {'Authorization': 'Bearer ' + inputs(token_file, maximum=4096).decode('ascii').strip(),
                    'Accept-Encoding': 'identity', **extra}
                config = module('config', package='guardrails.evolution')
                class SnapshotInputs:
                    def read(self, path, *, limit, private=True):
                        return inputs(path, maximum=limit)
                    def check(self):
                        budget.check()
                function = config.load_evaluator_endpoints
                loaded = FunctionType(function.__code__, dict(function.__globals__, _Inputs=SnapshotInputs),
                    function.__name__, function.__defaults__, function.__closure__)
                loaded.__kwdefaults__ = function.__kwdefaults__
                config.load_evaluator_endpoints = loaded
                assembly = module('assembly', imports={'guardrails.evolution.evaluator_config': config})
                assembly._classifier_bundle.__globals__['_classifier_private'] = inputs
                raw_config = inputs(manifest['evaluator_config']['path'])
                cluster.endpoints = {row['tenant_id']: row for row in proof.document(raw_config)['classifier']}
                captured_list = []
                def recording_executor(argv, **kwargs):
                    raw = self.executor(argv, **kwargs)
                    if tuple(argv[-4:]) == GET: captured_list.append(proof.document(raw.encode()))
                    return raw
                observer = current.BoundedObserver(cluster, runtime=preflight['images']['runtime'], deadline=deadline, executor=recording_executor)
                observed, _, _ = observer._capture()
                require(observed == preflight['binding'] and len(captured_list) == 1)
                services = [row for row in captured_list[0]['items'] if row.get('kind') == 'Service'
                    and row.get('metadata', {}).get('name') == 'classifier']
                require(len(services) == 1)
                def service(*args, **kwargs):
                    require(args == ('get', 'service', 'classifier', '-o', 'json') and kwargs == {'namespace': 'gr-platform'})
                    budget.check()
                    return 0, proof.canonical(services[0]).decode()
                facade = SimpleNamespace(kubectl=service)
                bundle, _, _, _ = assembly._classifier_bundle(facade, state / 'assembly', owner, Path(manifest['evaluator_config']['path']))
                cluster.endpoints = {row['tenant_id']: row for row in proof.document(bundle)['classifier']}
                images = proof.document(material['prepared.json'])['artifacts']
                def image(name):
                    rows = [row for row in images if row.get('kind') == 'image' and row.get('name') == name]
                    require(len(rows) == 1)
                    return rows[0]['source_url'].removeprefix('docker://')
                deployment.image = image
                deployment.owner = lambda **kwargs: copy.deepcopy(owner) if not kwargs else unavailable()
                deployment.support = lambda name: module('image_identity') if name == 'image_identity' else unavailable()
                checker.observe_locked(phase='before', operation=None, deadline=deadline)
                tls = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                tls.load_verify_locations(cadata=material['credentials/ca.crt'].decode('ascii'))
                cluster.http = httpx.Client(verify=tls, trust_env=False, follow_redirects=False, timeout=15)
                cluster.repo, cluster._ca_fd = read_only_repository(material, state=state, port=ports['postgres'], deadline=deadline)
                ready_once(cluster, deadline=deadline)
                checker.observe_locked(phase='before', operation=None, deadline=deadline)
                require(checker._private(preflight, deadline)[0] == material and checker._sources(auth['sources'], deadline) == full_source)
                require(inputs(manifest['evaluator_config']['path']) == raw_config)
                budget.check()
            cluster._classifier_current_checker = checker
            return cluster
        except BaseException:
            try:
                close_cluster(cluster)
            except BaseException:  # noqa: BLE001, S110 - preserve original failure
                pass
            raise
