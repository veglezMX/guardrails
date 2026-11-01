"""Probe mode imports and real local TCP/TLS failures preserve fail-closed meaning."""
from __future__ import annotations

import http.server
import json
import os
import socket
import ssl
import subprocess
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

ROOT = Path(__file__).resolve().parents[4]
PROBE = ROOT / "deploy/evolution/charts/probes/scripts/probe.py"

# A fresh interpreter fails on any attempted heavy import, even if it was cached.
LIGHTWEIGHT = '''import builtins,json,runpy,signal,socket,sys
original=builtins.__import__
def guarded(name,*args,**kwargs):
    if name in ('argparse','ssl','http.server','threading'):
        raise RuntimeError('forbidden_mode_import')
    return original(name,*args,**kwargs)
builtins.__import__=guarded
alarms=[]
original_alarm=signal.alarm
def alarm(seconds):
    alarms.append(seconds)
    return original_alarm(seconds)
signal.alarm=alarm
attempts=[]
if sys.argv[2]=='deny':
    def timed_out(address,timeout):
        attempts.append({'address':address,'timeout':timeout})
        raise TimeoutError()
    socket.create_connection=timed_out
script=sys.argv[1]
sys.argv=sys.argv[1:]
runpy.run_path(script,run_name='__main__')
print(json.dumps({'alarms':alarms,'attempts':attempts,'heavy_modules_present':
    [name for name in ('argparse','ssl','http.server','threading') if name in sys.modules]}))
'''


def child(mode, *, port=8443, ca=None, payload=None, wrapper=None, extra=()):
    command = [sys.executable, "-B"]
    command += ["-c", wrapper, str(PROBE)] if wrapper else [str(PROBE)]
    command += [mode, "--host", "localhost", "--port", str(port), *extra]
    if ca is not None:
        command += ["--ca", str(ca)]
    if payload is not None:
        command += ["--payload-stdin"]
    return subprocess.run(command, input=payload, capture_output=True, timeout=10, check=False)


@pytest.mark.parametrize("mode", ["deny", "dns"])
def test_probe_dns_and_deny_do_not_import_server_or_tls_modules(mode):
    result = child(mode, payload=b"synthetic-sensitive-canary" if mode == "deny" else None,
                   wrapper=LIGHTWEIGHT)
    assert result.returncode == 0
    observation, metadata = [json.loads(line) for line in result.stdout.splitlines()]
    assert metadata["heavy_modules_present"] == []
    assert metadata["alarms"] == [10]
    if mode == "deny":
        assert observation["result"] == "denied_timeout"
        assert observation["attempts"] == len(metadata["attempts"]) >= 1
        assert observation["payload_bytes"] == len(b"synthetic-sensitive-canary")
        assert all(row["timeout"] == 2 for row in metadata["attempts"])
    else:
        assert observation["result"] == "resolved"
        assert "127.0.0.1" in observation["addresses"]
        assert metadata["attempts"] == []
    assert b"synthetic-sensitive-canary" not in result.stdout


@pytest.mark.parametrize("payload,reason", [(b"", "probe_payload_missing"), (b"x" * 4097, "probe_payload_too_large")],
                         ids=["missing", "oversized"])
def test_probe_deny_rejects_missing_or_oversized_payload_without_success(payload, reason):
    result = child("deny", payload=payload, wrapper=LIGHTWEIGHT)
    assert result.returncode != 0
    assert reason.encode() in result.stderr
    assert result.stdout == b""
    assert b"forbidden_mode_import" not in result.stderr


def test_probe_deny_refuses_connected_and_refused_tcp_as_denial_evidence():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(2)
        result = child("deny", port=listener.getsockname()[1], payload=b"synthetic-canary")
        accepted, _ = listener.accept()
        with accepted:
            accepted.settimeout(2)
            assert accepted.recv(4096) == b"synthetic-canary"
        assert result.returncode != 0
        assert b"unexpected_allowed_connection" in result.stderr
        assert result.stdout == b""
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        result = child("deny", port=reserved.getsockname()[1])
        assert result.returncode != 0
        assert b"ConnectionRefusedError" in result.stderr
        assert result.stdout == b""


def test_probe_dns_failure_is_not_a_denial_or_certificate_success():
    wrapper = '''import runpy,socket,sys
def unavailable(*args,**kwargs):
    raise socket.gaierror(-2,'synthetic_dns_failure')
socket.getaddrinfo=unavailable
sys.argv=sys.argv[1:]
runpy.run_path(sys.argv[0],run_name='__main__')
'''
    for mode in ("deny", "wrong-ca"):
        result = child(mode, wrapper=wrapper)
        assert result.returncode != 0
        assert b"gaierror" in result.stderr
        assert result.stdout == b""


ARGUMENTS = '''import json,runpy,sys
namespace=runpy.run_path(sys.argv[1],run_name='probe_arguments')
args=namespace['parse_args'](sys.argv[2:])
print(json.dumps(vars(args),sort_keys=True))
'''


def argument_child(argv, *, posix=False, wrapper=True):
    env = dict(os.environ)
    env.pop("POSIXLY_CORRECT", None)
    if posix:
        env["POSIXLY_CORRECT"] = "1"
    command = [sys.executable, "-B"]
    command += ["-c", ARGUMENTS, str(PROBE)] if wrapper else [str(PROBE)]
    return subprocess.run([*command, *argv], env=env, capture_output=True, timeout=10, check=False)


def test_probe_cli_all_modes_preserve_exact_defaults():
    for mode in ("server", "query", "count", "deny", "wrong-ca", "wrong-name", "expired", "dns"):
        result = argument_child([mode])
        assert result.returncode == 0
        assert result.stderr == b""
        assert json.loads(result.stdout) == {
            "mode": mode, "host": "probe.gr-platform.svc.cluster.local",
            "port": 8443, "ca": "/secrets/ca.crt", "payload_stdin": False,
        }


@pytest.mark.parametrize("posix", [False, True], ids=["gnu-environment", "posix-environment"])
def test_probe_cli_interspersed_equals_and_duplicate_options_preserve_last_value(posix):
    options = ["--host", "first.invalid", "--port=9000", "--ca=/first.pem", "--payload-stdin",
               "--host=localhost", "--port", "+8443", "--ca", "/final.pem", "--payload-stdin"]
    for index in (0, 5, len(options)):
        result = argument_child([*options[:index], "deny", *options[index:]], posix=posix)
        assert result.returncode == 0
        assert result.stderr == b""
        assert json.loads(result.stdout) == {
            "mode": "deny", "host": "localhost", "port": 8443,
            "ca": "/final.pem", "payload_stdin": True,
        }


@pytest.mark.parametrize("help_option", ["-h", "--help"])
def test_probe_cli_help_succeeds_without_mode_or_dns(help_option):
    result = argument_child([help_option], wrapper=False)
    assert result.returncode == 0
    assert result.stderr == b""
    assert result.stdout.startswith(b"usage: probe.py MODE")
    assert b"--payload-stdin" in result.stdout
    assert b"wrong-ca" in result.stdout and b"dns" in result.stdout


def test_probe_cli_invalid_missing_and_extra_arguments_fail_before_network():
    canary = "synthetic-private-argument"
    invalid = [[], [canary], ["dns", "deny"], ["dns", canary], ["dns", "--unknown=" + canary],
               ["dns", "--host"], ["dns", "--port"], ["dns", "--ca"],
               ["dns", "--host", "--payload-stdin"], ["dns", "--ca", "-x"],
               ["dns", "--port=" + canary], ["dns", "--port=1.5"], ["dns", "--port="],
               ["dns", "--payload-stdin=yes"], ["dns", "--p"], ["dns", "-x"],
               ["dns", "--", "--host=localhost"], [canary, "--help"],
               ["dns", "extra", "--help"], ["--port=invalid", "--help"],
               ["--help", "--unknown"]]
    for argv in invalid:
        result = argument_child(argv, wrapper=False)
        assert result.returncode == 2
        assert result.stdout == b""
        assert result.stderr.endswith(b"probe.py: invalid arguments\n")
        assert canary.encode() not in result.stderr
        assert b"Traceback" not in result.stderr


def test_probe_cli_terminator_abbreviations_and_negative_integer_are_preserved():
    for argv in (["--hos=localhost", "--por=-1", "--c=/ca.pem", "--payload-s", "--", "dns"],
                 ["dns", "--host", "localhost", "--port", "-1", "--ca=/ca.pem", "--payload-stdin"]):
        result = argument_child(argv)
        assert result.returncode == 0
        assert result.stderr == b""
        assert json.loads(result.stdout) == {
            "mode": "dns", "host": "localhost", "port": -1,
            "ca": "/ca.pem", "payload_stdin": True,
        }


def certificates(directory, expired):
    now = datetime.now(UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Synthetic probe CA")])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - timedelta(days=2)).not_valid_after(now + timedelta(days=1))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
          .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
          .sign(ca_key, hashes.SHA256()))
    key = ec.generate_private_key(ec.SECP256R1())
    cert = (x509.CertificateBuilder().subject_name(x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])).issuer_name(ca_name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=2))
        .not_valid_after(now - timedelta(days=1) if expired else now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(ca_key, hashes.SHA256()))
    for name, value in (("ca.pem", ca), ("server.pem", cert)):
        (directory / name).write_bytes(value.public_bytes(serialization.Encoding.PEM))
    (directory / "key.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    (directory / "key.pem").chmod(0o600)
    return directory / "ca.pem"


@pytest.mark.parametrize("mode,expected", [("query", "tls_verified"), ("count", "tls_verified"),
                                           ("wrong-name", "certificate_rejected"),
                                           ("expired", "certificate_rejected")])
def test_probe_tls_modes_preserve_verified_transport_and_certificate_negatives(tmp_path, mode, expected):
    ca = certificates(tmp_path, mode == "expired")
    dispatches = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            dispatches.append(1)
            body = b'{"transport":"tls","counts":{"8443":1}}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(tmp_path / "server.pem", tmp_path / "key.pem")
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        result = child(mode, port=server.server_port, ca=ca)
        assert result.returncode == 0
        value = json.loads(result.stdout)
        assert value["result"] == expected
        if expected == "certificate_rejected":
            assert value["verify_code"] == (10 if mode == "expired" else 62)
            assert dispatches == []
        else:
            assert value["protocol"] in ("TLSv1.2", "TLSv1.3")
            assert len(dispatches) == 1
            if mode == "count":
                assert value["counts"] == {"8443": 1}
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()
    assert not thread.is_alive()
