import hashlib
import os
from pathlib import Path

import pytest


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    # Native evidence readers reject world-writable ancestors, including /tmp.
    # Keep pytest's normal retention and respect explicit operator overrides.
    if config.option.basetemp or "PYTEST_DEBUG_TEMPROOT" in os.environ:
        return
    repository = Path(__file__).resolve().parents[2]
    identity = hashlib.sha256(str(repository).encode()).hexdigest()[:16]
    # Synthetic repositories must live outside this checkout: otherwise pytest
    # discovers our pyproject.toml and changes their closed worker node IDs.
    temporary = Path.home() / ".cache" / "guardrails-pytest" / identity
    temporary.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.environ["PYTEST_DEBUG_TEMPROOT"] = str(temporary)
    config.add_cleanup(lambda: os.environ.pop("PYTEST_DEBUG_TEMPROOT", None))


@pytest.fixture
def management(tmp_path):
    from fastapi.testclient import TestClient
    from guardrails.management.app import create_app
    from guardrails.storage.control import ControlRepository
    from proof.management_test_support import API, actors
    tokens, identities = actors()
    repository = ControlRepository(tmp_path / 'control/control.sqlite3')
    app = create_app(repository, credentials=identities)
    with TestClient(app) as client:
        api = API(client, tokens)
        api.repository, api.identities, api.app = repository, identities, app
        yield api


@pytest.fixture
def https_management():
    from proof.management_test_support import HTTPSProcess, integration_root
    service = HTTPSProcess(integration_root())
    try:
        service.start()
        yield service
    finally:
        service.stop()


@pytest.fixture
def runtime_api(management, tmp_path):
    from fastapi.testclient import TestClient
    from guardrails.local_contract import ProofConfig
    from guardrails.runtime.app import EvaluationRuntime, create_app
    from guardrails.runtime.snapshots import SnapshotCache
    from guardrails.storage.audit import AuditRepository
    from proof.management_test_support import API
    management.seed()
    cache = SnapshotCache(ProofConfig(workers=1))
    cache.apply(management.request('GET', '/internal/v1/snapshot', actor='runtime').content)
    runtime = EvaluationRuntime(cache, AuditRepository(tmp_path / 'runtime/audit.sqlite3'), credentials=(management.identities[3],))
    with TestClient(create_app(runtime)) as client:
        api = API(client, management.tokens)
        api.runtime, api.management = runtime, management
        yield api


@pytest.fixture
def https_runtime():
    from proof.management_test_support import integration_root
    from proof.runtime_test_support import RuntimeStack
    stack = RuntimeStack(integration_root())
    try:
        yield stack.start()
    finally:
        stack.stop()


@pytest.fixture(scope='session')
def gateways():
    import os

    from proof.gateway_test_support import GatewayStack
    if os.environ.get('PROOF_ACCEPTANCE_STATE'):
        from proof.acceptance_stack import AcceptanceStack
        stack = AcceptanceStack()
        stack.observations = []
    else:
        stack = GatewayStack()
    try:
        yield stack.start()
    finally:
        stack.stop()
