"""Production factory configuration binds actual endpoint objects before startup."""
import asyncio
from types import SimpleNamespace

import pytest
import test_evaluator_config as fixtures
from guardrails.evolution import config, management_api, storage
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.runtime_api import EvaluationRuntime, RuntimeSlot
from test_execution_profiles import config as settings

certificate, inputs, no_network, write = fixtures.certificate, fixtures.inputs, fixtures.no_network, fixtures.write


def test_runtime_factory_loads_only_configured_tenant_and_keeps_health_unavailable(inputs):
    path, document = inputs
    write(path, document)
    service = EvaluationRuntime(object(), config=settings(evaluator_config_file=str(path)),
        runtime_slots={"team-a": RuntimeSlot(SimpleNamespace(tenant_id="team-a"))})
    try:
        capabilities = service.registry.capabilities("team-a")
        assert len([row for row in capabilities if row.capability_id == "prompt-injection-en"]) == 2
        assert all(row.status == "unavailable" for row in capabilities if row.capability_id == "prompt-injection-en")
        assert all(row.status == "disabled" for row in capabilities if row.capability_id == "managed-bedrock")
        assert service.slots["team-a"].store.capability_check == service.registry.require
        assert not service._started
    finally:
        asyncio.run(service.close())


def test_runtime_factory_rejects_endpoint_outside_its_tenant_slots(inputs):
    path, document = inputs
    write(path, document)
    with pytest.raises(EvolutionError) as failure:
        EvaluationRuntime(object(), config=settings(evaluator_config_file=str(path)),
            runtime_slots={"team-b": RuntimeSlot(SimpleNamespace(tenant_id="team-b"))})
    assert failure.value.code == "validation_failed"


def test_management_factory_loads_explicit_operator_snapshot_without_db_connection(inputs, monkeypatch):
    path, document = inputs
    write(path, document)
    database = path.parent / "dsn"
    write(database, b"synthetic-dsn")
    configured = settings(evaluator_config_file=str(path)).model_copy(update={"database_url_file": str(database)})
    monkeypatch.setattr(config, "load_config", lambda path: configured)
    repository = object()
    opened = []
    def construct(dsn):
        opened.append(dsn)
        return repository
    monkeypatch.setattr(storage, "TenantRepository", construct)
    service = management_api.create_app().state.service
    try:
        assert opened == ["synthetic-dsn"]
        assert service.repository is repository
        capabilities = service.registry.capabilities("team-a")
        assert len([row for row in capabilities if row.capability_id == "prompt-injection-en"]) == 2
        assert all(row.status == "unavailable" for row in capabilities if row.capability_id == "prompt-injection-en")
        assert all(row.status == "disabled" for row in capabilities if row.capability_id == "managed-bedrock")
    finally:
        asyncio.run(service.registry.close())
        service.audit.close()


@pytest.mark.parametrize("path", ["relative.json", "", "/private\x00/config.json"])
def test_operator_endpoint_configuration_requires_absolute_valid_path(path):
    with pytest.raises(ValueError):
        settings(evaluator_config_file=path)


def test_operator_endpoint_configuration_env_is_explicit_and_default_is_absent(tmp_path):
    path = tmp_path / "service.json"
    write(path, settings().model_dump(mode="json"))
    assert config.load_config(path, {}).evaluator_config_file is None
    assert config.load_config(path, {"GUARDRAILS_EVALUATOR_CONFIG_FILE": "/config/evaluators.json"}).evaluator_config_file == "/config/evaluators.json"
    with pytest.raises(EvolutionError):
        config.load_config(path, {"GUARDRAILS_UNRECOGNIZED_EVALUATOR": "ignored"})
