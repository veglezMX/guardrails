"""Worker verifies CLI/registry/catalog inputs before creating the fixed plugin."""
import hashlib
import json
import sys
import time
from types import ModuleType, SimpleNamespace

import pytest
from evolution import classifier_deployed_collection as collection
from evolution import worker
from evolution.classifier_worker_invocation_v3 import MANIFEST_ENV, SHA_ENV
from evolution.registry import EvidenceError

from mvp.tests.evolution.unit.test_classifier_runner_hooks import CASE


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    registry = tmp_path / 'mvp/evolution/cases.json'; registry.parent.mkdir(parents=True)
    registry.write_text('{}')
    digest = hashlib.sha256(registry.read_bytes()).hexdigest()
    catalog = {'mvp/example.py': 'b' * 64}
    catalog_path = tmp_path / 'source-catalog.json'; catalog_path.write_text(json.dumps(catalog))
    deadline = time.monotonic() + 100
    args = SimpleNamespace(classifier_case_id=CASE.case_id, classifier_registry_sha256=digest,
        classifier_deadline=deadline, nodeid=CASE.nodeid, source_catalog=catalog_path)
    selected = []
    family = SimpleNamespace(gate_sources=lambda root, *, deadline: catalog.copy())
    monkeypatch.setattr(collection, 'cni', family)
    monkeypatch.setitem(collection.FAMILIES, CASE.case_id, family)
    monkeypatch.setattr(worker, 'load_registry', lambda path, phase: (CASE,))
    final = ModuleType('evolution.classifier_final_worker_qualification')
    final.verify = lambda **kwargs: pytest.fail('qualification should run only inside the entered builder')
    builder = ModuleType('mvp.tests.evolution.e2e.classifier_fixture_builder_v3')
    def build(reference, expected, root, **kwargs):
        selected.append((reference, expected, root, kwargs))
        return 'synthetic-unentered-session'
    builder.build_worker_plugin = build
    monkeypatch.setitem(sys.modules, final.__name__, final)
    monkeypatch.setitem(sys.modules, builder.__name__, builder)
    monkeypatch.setenv(MANIFEST_ENV, str(tmp_path / 'manifest.json'))
    monkeypatch.setenv(SHA_ENV, 'c' * 64)
    return SimpleNamespace(**locals())


def test_exact_fixed_builder_gets_independent_expected_inputs(setup):
    f = setup
    assert worker.classifier_session(f.args, f.catalog) == 'synthetic-unentered-session'
    ref, expected, root, kwargs = f.selected[0]
    assert ref == {'path': str(f.tmp_path / 'manifest.json'), 'sha256': 'c' * 64}
    assert expected['case_id'] == CASE.case_id and expected['nodeid'] == CASE.nodeid
    assert expected['registry_sha256'] == f.digest and expected['sources'] == f.catalog
    assert expected['deadline'] == f.deadline and expected['root'] == root == f.tmp_path
    assert expected['source_catalog_ref']['sha256'] == hashlib.sha256(f.catalog_path.read_bytes()).hexdigest()
    assert kwargs == {'qualify_final_worker': f.final.verify}


@pytest.mark.parametrize('fault', ['partial', 'case', 'registry', 'node', 'expired', 'extended', 'nan', 'missing_env'])
def test_invalid_worker_authority_refuses_before_builder(setup, fault, monkeypatch):
    f = setup
    if fault == 'partial': f.args.classifier_case_id = None
    elif fault == 'case': f.args.classifier_case_id = 'FOREIGN'
    elif fault == 'registry': f.registry.write_text('{"changed":true}')
    elif fault == 'node': f.args.nodeid = 'foreign::case'
    elif fault == 'expired': f.args.classifier_deadline = time.monotonic() - 1
    elif fault == 'extended': f.args.classifier_deadline = time.monotonic() + 121
    elif fault == 'nan': f.args.classifier_deadline = float('nan')
    else: monkeypatch.delenv(SHA_ENV)
    with pytest.raises(EvidenceError): worker.classifier_session(f.args, f.catalog)
    assert f.selected == []


def test_unrelated_worker_has_no_plugin_and_refuses_inherited_classifier_authority(setup, monkeypatch):
    f = setup
    f.args.classifier_case_id = f.args.classifier_registry_sha256 = f.args.classifier_deadline = None
    with pytest.raises(EvidenceError): worker.classifier_session(f.args, f.catalog)
    monkeypatch.delenv(MANIFEST_ENV); monkeypatch.delenv(SHA_ENV)
    with worker.classifier_session(f.args, f.catalog) as plugin:
        assert plugin is None
    assert f.selected == []
