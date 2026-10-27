"""Parent-executed TLS/auth observations from existing, exactly owned runtimes.

Importing this module performs no file, process, network or model operation.
The caller owns the deployment lock and the approved classifier launch closure.
This TLS family is not a rollout receipt: parent qualification/prepared/cold/
credential invocation provenance must be integrated before gate registration.
"""
from __future__ import annotations

import hashlib
import time

from evolution import classifier_operational_measurements as evidence
from guardrails.evolution.classifier_contract import canonical, identity, parse

from mvp.tests.evolution.e2e.classifier_network_support import (
    NAMESPACE,
    READY_SCRIPT,
    ReadinessObserver,
    private_bytes,
    require,
)

# Reuse the entire original, source-bound readiness program in a non-main
# namespace. Only this literal trusted program is compiled; no input source or
# configuration is executed. Its real authenticated GET supplies each counter.
TLS_SCRIPT = "READY_SOURCE = " + repr(READY_SCRIPT) + "\n" + r'''
import datetime, hashlib, http.client, ipaddress, json, signal, socket, ssl, sys, time

library = {'__name__': 'classifier_readiness_library'}
exec(compile(READY_SOURCE, 'classifier-readiness-library', 'exec'), library)
need, load, read = library['need'], library['load'], library['read']
SCENARIOS = ('ready','wrong_ca','wrong_sni','plaintext','absent_ready','wrong_ready','absent_evaluate','wrong_evaluate')
HOST = 'classifier.gr-platform.svc.cluster.local'

def wrong_ca():
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'isolated-wrong-classifier-ca')])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(minutes=1))
        .not_valid_after(now+datetime.timedelta(minutes=5))
        .add_extension(x509.BasicConstraints(ca=True,path_length=None),critical=True).sign(key,hashes.SHA256()))
    return cert.public_bytes(serialization.Encoding.PEM)

def request(settings, scenario, ca, token):
    address = ipaddress.ip_address(settings['address'])
    need(str(address) == settings['address'] and address.is_private
         and not (address.is_loopback or address.is_multicast or address.is_unspecified))
    evaluate = scenario.endswith('evaluate')
    body = json.dumps({'api_major':1,'identity':settings['identity'],'request_id':'0'*32,'budget_ms':2900,
        'segments':[{'part_id':'tls-auth-fixture','role':'user','json_pointer':None,'text':'A clear blue sky.'}]},
        sort_keys=True,separators=(',',':'),allow_nan=False).encode() if evaluate else b''
    supplied = None if scenario.startswith('absent_') or scenario == 'plaintext' else token
    if scenario.startswith('wrong_') and scenario not in ('wrong_ca','wrong_sni'):
        supplied = token[:-1] + ('a' if token[-1] != 'a' else 'b')
        need(supplied != token)
    path = '/v1/classifier/evaluate' if evaluate else '/v1/classifier/ready'
    wire = (('POST' if evaluate else 'GET')+' '+path+' HTTP/1.1\r\nHost: '+HOST+':8443\r\n'
        'Accept: application/json\r\nAccept-Encoding: identity\r\nConnection: close\r\n'
        +('Authorization: Bearer '+supplied+'\r\n' if supplied is not None else '')
        +('Content-Type: application/json\r\nContent-Length: '+str(len(body))+'\r\n' if evaluate else '')+'\r\n').encode()+body
    raw = socket.socket(socket.AF_INET6 if address.version == 6 else socket.AF_INET,socket.SOCK_STREAM)
    channel = raw
    try:
        raw.settimeout(2.5)
        raw.connect((str(address),8443))
        if scenario == 'plaintext':
            try:
                raw.sendall(wire)
                need(raw.recv(1) == b'')
            except ConnectionResetError:
                pass
            return {'outcome':'plaintext_closed','http_status':0,'tls':None,'verify_code':0}
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        anchors = wrong_ca() if scenario == 'wrong_ca' else ca
        need(scenario != 'wrong_ca' or anchors != ca)
        context.load_verify_locations(cadata=anchors.decode('ascii'))
        try:
            channel = context.wrap_socket(raw,server_hostname='untrusted-classifier.invalid' if scenario == 'wrong_sni' else HOST)
        except ssl.SSLCertVerificationError as error:
            need(scenario in ('wrong_ca','wrong_sni') and error.verify_code in
                 ({18,19,20,21} if scenario == 'wrong_ca' else {62}))
            return {'outcome':'certificate_rejected','http_status':0,'tls':None,'verify_code':error.verify_code}
        need(scenario not in ('wrong_ca','wrong_sni'))
        channel.settimeout(2.5)
        tls = channel.version()
        need(tls in ('TLSv1.2','TLSv1.3'))
        channel.sendall(wire)
        response = http.client.HTTPResponse(channel)
        response.begin()
        headers = response.getheaders()
        need(len(headers) <= 64)
        selected = {}
        for name,value in headers:
            need(len(name) <= 128 and len(value) <= 4096)
            name = name.lower()
            if name in ('content-length','content-type','content-encoding','transfer-encoding'):
                need(name not in selected)
                selected[name] = value
        need(selected.get('content-type') == 'application/json' and selected.get('content-encoding','identity') == 'identity'
             and 'transfer-encoding' not in selected and selected.get('content-length','').isdigit()
             and 0 < int(selected['content-length']) <= 4096)
        result = response.read(4097)
        need(len(result) == int(selected['content-length']) and response.status == 401 and load(result) == {'error':'unauthorized'})
        return {'outcome':'unauthorized','http_status':401,'tls':tls,'verify_code':0}
    finally:
        channel.close()
        raw.close()

def perform(settings, result):
    need(type(settings) is dict and set(settings) == {'address','identity','token_sha256','ca_sha256'})
    ca = read('/config/ca.crt',65536,settings['ca_sha256'])
    token = read('/config/classifier.token',4097,settings['token_sha256']).decode('ascii').removesuffix('\n')
    need(32 <= len(token) <= 4096 and all(33 <= ord(c) <= 126 for c in token))
    previous = None
    for scenario in SCENARIOS:
        result['failed_scenario'] = scenario
        started = time.monotonic()
        before = library['perform'](settings)
        need(previous is None or (before['epoch'],before['attempts']) == previous)
        if scenario == 'ready':
            observed = {'outcome':'ready','http_status':before['status'],'tls':before['tls'],'verify_code':0}
        else:
            observed = request(settings,scenario,ca,token)
        after = library['perform'](settings)
        need(before['epoch'] == after['epoch'] and before['attempts'] == after['attempts'])
        elapsed = (time.monotonic()-started)*1000
        need(0 <= elapsed <= 12000)
        result['rows'].append({'scenario':scenario,**observed,'elapsed_ms':round(elapsed,6),
            'epoch':before['epoch'],'before_attempts':before['attempts'],'after_attempts':after['attempts']})
        previous = (after['epoch'],after['attempts'])
    result.update(status='passed',failed_scenario=None)

if __name__ == '__main__':
    result = {'schema_version':'evolution-classifier-tls-probe-v1','status':'failed','failed_scenario':'setup','rows':[]}
    def expired(*unused): raise TimeoutError()
    signal.signal(signal.SIGALRM,expired)
    signal.setitimer(signal.ITIMER_REAL,45)
    try:
        need(len(sys.argv) == 2 and len(sys.argv[1]) <= 4096)
        perform(load(sys.argv[1]),result)
    except BaseException:
        pass
    finally:
        signal.setitimer(signal.ITIMER_REAL,0)
    print(json.dumps(result,sort_keys=True,separators=(',',':'),allow_nan=False))
    raise SystemExit(0 if result['status'] == 'passed' else 1)
'''


class TlsObservation:
    def __init__(self, cluster, runtime):
        self.observer = ReadinessObserver(cluster, runtime=runtime)
        self.value = {"schema_version": evidence.SCHEMA, "runtime": runtime, "status": "failed",
            "binding_sha256": None, "identity_sha256": hashlib.sha256(canonical(identity())).hexdigest(),
            "elapsed_ms": 0, "probe": None}

    def bind_locked(self):
        return self.observer.bind_locked()

    def run_locked(self):
        observer = self.observer
        require(observer.bound is not None)
        started = time.monotonic()
        observer.deadline = started + 90
        try:
            before, _, _ = observer._capture()
            require(before == observer.bound)
            self.value["binding_sha256"] = hashlib.sha256(canonical(before)).hexdigest()
            settings = {"address": before["service"]["address"], "identity": identity(),
                "token_sha256": before["files"]["assembly/private/classifier.token"],
                "ca_sha256": before["files"]["assembly/tls/gr-platform/classifier/ca.crt"]}
            d = observer.deployment
            for name, digest in observer._api_pins.items():
                raw = private_bytes(d.state / name, deadline=observer.deadline,
                    modes=(0o600,) if name in observer._api_private else (0o600, 0o644))
                require(hashlib.sha256(raw).hexdigest() == digest)
            require(observer._native(observer._api_owner, observer._api_images) == before["api_server"])
            # Exact immutable static config via stdin; only this source-bound
            # literal probe is allowed, on the previously verified runtime Pod.
            observer._routing()
            code, raw = d.run([d.bin / "kubectl", "--kubeconfig", "/dev/stdin",
                "--context", "guardrails-evolution", "--namespace", NAMESPACE, "--request-timeout=50s",
                "exec", before["pods"][observer.runtime]["pod_name"], "-c", "runtime", "--", "python", "-B", "-c",
                TLS_SCRIPT, canonical(settings).decode()], timeout=observer._remaining(50), check=False, input_text=observer._api_input)
            require(type(raw) is str and 0 < len(raw.encode()) <= evidence.MAX_BYTES)
            self.value["probe"] = evidence.validate_probe(parse(raw.encode(), limit=evidence.MAX_BYTES))
            require(code == 0)
            after, _, _ = observer._capture()
            require(after == before)
            evidence.validate_probe(self.value["probe"], complete=True)
            self.value["status"] = "passed"
            return self.value
        finally:
            self.value["elapsed_ms"] = round((time.monotonic() - started) * 1000, 6)


def run_tls_case(cluster_factory, *, root, runtime):
    """Run one original case, preserve failure and always attempt private evidence."""
    case_id = "SEC-CLASSIFIER-DEPLOYED-TLS-AUTH-" + runtime.upper()
    sources = evidence.gate_sources(root, case_id)
    cluster = None
    observation = None
    primary = None
    try:
        cluster = cluster_factory()
        observation = TlsObservation(cluster, runtime)
        with cluster.deployment.lock():
            observation.bind_locked()
            value = observation.run_locked()
            evidence.validate(value, case_id, root=root, sources=sources, complete=True)
    except BaseException as error:
        primary = error
        raise
    finally:
        failure = None
        try:
            if cluster is not None:
                cluster.close()
        except BaseException as error:  # noqa: BLE001 - preserve original failure and record cleanup failure
            failure = error
        try:
            if observation is not None:
                if primary is not None or failure is not None:
                    observation.value["status"] = "failed"
                evidence.record(observation.value, case_id, root=root, sources=sources)
        except BaseException as error:  # noqa: BLE001 - evidence failure cannot hide an earlier test failure
            failure = failure or error
        if failure is not None:
            if primary is None:
                raise failure
            primary.add_note("Classifier operational cleanup/evidence also failed: " + type(failure).__name__)
