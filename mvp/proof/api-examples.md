# Management API examples

Run bootstrap and start management as described in [README.md](README.md). These direct API examples use the generated CA, read the tenant-operator token from its restricted file, write the one-time workload secret to a 0600 file, and print only safe metadata. Run from the repository root with host Python 3.11 or later. They do not call a model or fabricate runtime application.

```python
import json
import os
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

os.umask(0o077)
root = Path('.proof')
context = ssl.create_default_context(cafile=str(root / 'pki/ca.crt'))
token = (root / 'secrets/tenant-operator.token').read_text().strip()
base = 'https://127.0.0.1:8443'
tenant = '/v1/tenants/local-proof'

def call(method, path, body=None, **headers):
    request = urllib.request.Request(
        base + path,
        data=None if body is None else json.dumps(body).encode(),
        method=method,
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json', **headers},
    )
    try:
        with urllib.request.urlopen(request, context=context, timeout=5) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        problem = json.load(error)
        raise SystemExit(f"HTTP {error.code}: {problem['code']}") from None

def body(**values):
    return {'schema_version': 'local-proof-v1', **values}

# Choose a fresh ID for each example run. Registering an existing ID returns 409.
workload = 'example-' + uuid4().hex[:8]
registered = call('POST', tenant + '/workloads', body(workload_id=workload, display_name='Example workload'))
credential_file = root / 'secrets' / (workload + '.token')
descriptor = os.open(credential_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(descriptor, 'w') as stream:
    stream.write(registered['token'] + '\n')
    stream.flush()
    os.fsync(stream.fileno())

policy = body(
    tenant_id='local-proof', policy_id='example-pii', version='v1',
    checks=[{'check_id': 'pii', 'evaluator': 'local_pii', 'stages': ['input', 'output'],
             'pii_types': ['email', 'phone'], 'requested_action': 'block'}],
    failure_behavior={name: 'block' for name in (
        'evaluation_timeout', 'evaluation_unavailable', 'policy_unresolved',
        'management_unavailable', 'audit_failure', 'transformation_failure')},
    retain_content=False, detector_format_version='pii-ascii-v1',
)
print(call('POST', tenant + '/policies/validate', policy))
published = call('POST', tenant + '/policies', policy)
binding_path = tenant + '/workloads/' + workload + '/binding'
bound = call('PUT', binding_path,
             body(policy_id='example-pii', policy_version='v1', reason='initial_binding'),
             **{'If-None-Match': '*'})
promoted = call('POST', binding_path + '/state',
                body(enforcement_state='block', reason='synthetic_promotion',
                     evidence_kind='synthetic_local_proof', fixture_run_id=str(uuid4())),
                **{'If-Match': str(bound['resource']['revision'])})
rolled_back = call('POST', binding_path + '/rollback',
                   body(target_revision=bound['resource']['revision'], reason='rollback'),
                   **{'If-Match': str(promoted['resource']['revision'])})
print(call('GET', '/v1/operations/' + rolled_back['operation']['operation_id']))
print(call('GET', binding_path))
print(call('GET', '/v1/audit?source=management&tenant_id=local-proof&workload_id=' + workload))
```

Initial binding always starts in Observe. State changes require a current revision and synthetic fixture reference. Updates and rollback create new history revisions; stale revisions return 409, missing preconditions 428. To publish another version, change `version` and submit the full policy, then PUT the binding with `reason=policy_update` and its current revision. The existing enforcement state is retained. Changed content under a published version returns 409; identical content returns the existing publication and operation.

For lost credential issuance, GET the workload's `credential_revision`, then POST `.../credentials/rotate` with that revision in `If-Match` and `body(reason='credential_rotation')`. Save the returned token as above; the previous credential is revoked. Never print credential responses. Operators cannot create tenants, change floors, publish retention-enabled policies or inspect retained content; use the admin token for those authorized operations.

Runtime application and the lifecycle CLI are not part of this Phase 2 example. Do not submit a synthetic acknowledgement to make a manual operation appear applied. The management integration gate explicitly labels its test acknowledgement boundary.
