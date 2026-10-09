"""Negative acceptance accounting and evidence-privacy contracts."""
import json
from pathlib import Path
from uuid import uuid4

import pytest

from proof.fixtures import load_fixtures, PiiFixture
from proof.reports import row_results, write_safe


def test_missing_failed_or_empty_row_cannot_pass():
    inventory = {'case-a':{'rows':list(range(1,11))}}
    assert not any(row['passed'] for row in row_results(inventory,{}))
    assert not any(row['passed'] for row in row_results(inventory,{'case-a':{'passed':False,'assertion_count':4,'errors':['case_failed']}}))
    assert not any(row['passed'] for row in row_results({},{}))
    assert not any(row['passed'] for row in row_results(inventory,{'case-a':{'passed':True,'assertion_count':0}}))
    valid = {'case-a':{'passed':True,'assertion_count':3}}
    assert all(row['passed'] for row in row_results(inventory,valid))
    assert not any(row['passed'] for row in row_results(inventory,valid,global_errors=['offline_failed']))


@pytest.mark.parametrize('secret', ['synthetic-sensitive@example.test','文🙂 sensitive fixture','opaque-secret-token'])
def test_report_refuses_plain_and_json_escaped_traffic(tmp_path,secret):
    path=tmp_path/'report.json'
    with pytest.raises(ValueError,match='unsafe_evidence'):
        write_safe(path,{'bad':secret},[secret])
    assert not path.exists()
    write_safe(path,{'passed':True},[secret])
    assert path.stat().st_mode & 0o777 == 0o600


def test_fixture_loader_rejects_missing_duplicate_unknown_and_invalid_templates(tmp_path):
    import shutil
    source=Path(__file__).parents[1]/'fixtures'
    for p in source.glob('*.json'):
        shutil.copyfile(p,tmp_path/p.name)
    assert len(load_fixtures(tmp_path,str(uuid4()))) >= 100
    path=tmp_path/'pii-cases.json'
    values=json.loads(path.read_text())
    values.append(values[0])
    path.write_text(json.dumps(values))
    with pytest.raises(ValueError):
        load_fixtures(tmp_path,str(uuid4()))

    changed=values[0]|{'unrecognized':True}
    with pytest.raises(ValueError):
        PiiFixture.model_validate_json(json.dumps(changed))
    changed=values[0]|{'acceptance_rows':[11]}
    with pytest.raises(ValueError):
        PiiFixture.model_validate_json(json.dumps(changed))
    shutil.copyfile(source/'pii-cases.json',path)
    lifecycle=tmp_path/'lifecycle-cases.json'
    values=json.loads(lifecycle.read_text())
    values[0]['steps'][0]['request_template']['unexpected']=True
    lifecycle.write_text(json.dumps(values))
    with pytest.raises(ValueError):
        load_fixtures(tmp_path,str(uuid4()))


@pytest.mark.parametrize('body,success', [
    ('pass', False),
    ('import pytest; pytest.skip("required")', False),
    ('assert True', True),
    ('from proof.management_test_support import API; from types import SimpleNamespace as N; '
     'API(N(request=lambda *a,**k:N(status_code=200,headers={})),{}).request("GET","/probe",actor=None)', True),
    ('from proof.management_test_support import API; from types import SimpleNamespace as N; '
     'API(N(request=lambda *a,**k:N(status_code=201,headers={})),{}).request("GET","/probe",actor=None)', False),
])
def test_real_pytest_plugin_rejects_empty_and_skipped_cases(tmp_path,body,success):
    import os
    import subprocess
    import sys
    test=tmp_path/'test_probe.py'
    test.write_text('def test_case():\n    '+body+'\n')
    report=tmp_path/'cases.json'
    code="import sys,pytest;from pathlib import Path;from proof.reports import EvidencePlugin;p=Path(sys.argv[1]);raise SystemExit(pytest.main(['-p','no:terminal','-p','no:cacheprovider','-o','addopts=','-o','enable_assertion_pass_hook=true','--rootdir='+str(p.parent),str(p)],plugins=[EvidencePlugin({'test_probe.py::test_case':{'rows':[1]}},Path(sys.argv[2]))]))"
    env=os.environ|{'PYTHONPATH':str(Path(__file__).parents[2]),'PYTHONDONTWRITEBYTECODE':'1','PYTHONPYCACHEPREFIX':str(tmp_path/'cache')}
    result=subprocess.run([sys.executable,'-c',code,str(test),str(report)],env=env,capture_output=True,text=True)
    assert result.returncode==(0 if success else 1)
    values=json.loads(report.read_text())['cases']['test_probe.py::test_case']
    assert values['passed']==success


def test_privacy_exclusions_are_exact_consumed_inputs_and_retained_audit_only(tmp_path):
    from proof.privacy import INPUT_FILES, RETAINED_DATABASES, input_file, retained_database, unsafe
    issued=tmp_path/'secrets'/'issued.token'
    audit=tmp_path/'boundary-tests'/'authorized-retention'/'runtime'/'db'/'audit.sqlite3'
    INPUT_FILES.add(issued.resolve())
    RETAINED_DATABASES.add(audit.resolve())
    try:
        assert input_file(issued,tmp_path)
        assert retained_database(audit) and retained_database(Path(str(audit)+'-wal'))
        for relative in ('unexpected.token','secrets/unexpected.log','negative-capture.bin',
                         'boundary-tests/authorized-retention/management/db/control.sqlite3'):
            path=tmp_path/relative
            assert not input_file(path,tmp_path) and not retained_database(path)
            assert unsafe(b'opaque-secret-token',{'opaque-secret-token'})
    finally:
        INPUT_FILES.remove(issued.resolve())
        RETAINED_DATABASES.remove(audit.resolve())


def test_held_mock_requires_control_release_and_has_bounded_watchdog(tmp_path,monkeypatch):
    import asyncio
    import httpx
    from proof.mock_model import create_app
    tokens={'control':'synthetic-control-'+'a'*32,'inference':'synthetic-inference-'+'b'*32}
    for name, variable in (('control','GUARDRAILS_PROOF_CONTROL_TOKEN_FILE'),('inference','GUARDRAILS_MOCK_TOKEN_FILE')):
        path=tmp_path/(name+'.token')
        path.write_text(tokens[name])
        monkeypatch.setenv(variable,str(path))
    monkeypatch.setenv('GUARDRAILS_PROOF_PROFILE','integration')
    monkeypatch.delenv('GUARDRAILS_MOCK_CORPUS',raising=False)
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()),base_url='https://mock') as client:
            control={'Authorization':'Bearer '+tokens['control']}
            inference={'Authorization':'Bearer '+tokens['inference']}
            assert (await client.post('/proof/release',headers=inference)).status_code==401
            assert (await client.post('/proof/release',headers=control,json={'delay':1})).status_code==422
            body={'model':'proof','messages':[{'role':'user','content':'Synthetic hold probe.'}]}
            assert (await client.put('/proof/scenario',headers=control,json={'scenario':'held_sensitive'})).status_code==200
            pending=asyncio.create_task(client.post('/v1/chat/completions',headers=inference,json=body))
            for _ in range(100):
                observed=(await client.get('/proof/calls',headers=control)).json()['count']
                if observed==1:
                    break
                await asyncio.sleep(.01)
            assert observed==1 and not pending.done()
            assert (await client.post('/proof/release',headers=control)).json()=={'released':True}
            response=await asyncio.wait_for(pending,1)
            assert response.status_code==200 and response.json()['choices'][0]['message']['content'].startswith('Contact ')
            # Selecting the scenario creates a fresh barrier. A missing release
            # has a bounded failure, without changing the model's 10s deadline.
            await client.put('/proof/scenario',headers=control,json={'scenario':'held_sensitive'})
            response=await asyncio.wait_for(client.post('/v1/chat/completions',headers=inference,json=body),9)
            assert response.status_code==503 and response.json()['code']=='upstream_failed'
    asyncio.run(exercise())
