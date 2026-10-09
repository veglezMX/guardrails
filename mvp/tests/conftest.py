import pytest


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
